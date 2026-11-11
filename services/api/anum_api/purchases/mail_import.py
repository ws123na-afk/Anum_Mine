"""Save as purchase from the ANUM mailbox (docs/purchases.md#save-as-purchase-from-the-email-center).

A person opens an order confirmation in the Email Center and presses **Save as purchase**. ANUM reads that one
message from the mailbox with their own access (it never reads the mailbox by itself), reads it with the same fixed
patterns as a shared message (``parser.py``: the text is data, never instructions; no model reads it and no link in
it is opened), and saves a purchase draft with **needs review** on. When the person picks one of the message's
attachments (the receipt or invoice PDF), it is stored privately in their document vault, with the same type,
content and malware checks as an upload, and attached as the purchase's receipt.

* **Permissions.** Purchases ``purchase:write``, the Email Center ``email:read`` and read access to that mailbox
  (their own, or one shared with them); the attachment also needs ``document:write``. A refusal says which.
* **Once per message.** The purchase's idempotency key is derived from the person, the mailbox and the message's
  Message-ID (else its folder and number), so pressing it twice, or on two devices, answers the first purchase
  (``200 replayed``). A message that names a purchase already saved (its order or tracking number) updates that
  purchase, like a shared message, and keeps its receipt if it has one.
* Nothing is sent and the message is not changed (not even marked read).
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import UnitsOfWork, has_permission, run_in_unit, tenant_context, units_of_work
from ..schemas import TenantContext, new_id, utc_now
from . import parser as P
from . import service as S
from .store import DuplicateError, open_purchase_store

router = APIRouter(tags=["purchases"])

MAIL_DENIED = "Saving a purchase from an email needs the Email Center (owners and members) and read access to that mailbox."
VAULT_DENIED = "Keeping the attachment needs the document vault (owners and members). Save it without the attachment, or ask a workspace owner."
FROM_MAIL_NOTE = "Saved from an email in the Email Center"


class FromMail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    folder: str = Field(min_length=1, max_length=500)
    uid: int = Field(ge=1)
    # The message attachment to keep as the receipt (its index in the message), or none.
    attachment_index: int | None = Field(default=None, ge=0, le=500)
    ownership: Literal["personal", "business"] = "personal"


def _refuse(status: int, detail: str, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code, **extra})


def _sender_name(sender: Any) -> str:
    """The shop's name when the text names none: the sender's display name, else its domain."""
    name = (getattr(sender, "name", None) or "").strip()
    if name:
        return name[:120]
    address = (getattr(sender, "address", None) or getattr(sender, "email", None) or "").strip()
    domain = address.rpartition("@")[2]
    return (domain or "Email order")[:120]


def _seed(account_id: str, message_id: str | None, folder: str, uid: int) -> str:
    stable = (message_id or "").strip() or f"{folder}\x1f{uid}"
    return hashlib.sha256(f"mail\x1f{account_id}\x1f{stable}".encode()).hexdigest()


def _store_attachment(context: TenantContext, raw: bytes, index: int, subject: str) -> str:
    """The attachment as a private vault document (or the person's own copy already in the vault). Returns its id."""
    from ..billing.enforce import require_capacity
    from ..files import check_upload_content, resolve_upload_type, valid_file_name
    from ..mailbox.config import mail_settings
    from ..mailbox.mime import attachment_bytes
    from ..vault import service as V
    from ..vault.store import open_vault_unit

    found = attachment_bytes(raw, index)
    if found is None:
        raise S.PurchaseError(404, "That attachment is not in this message.", "attachment_not_found")
    data, info = found
    if len(data) > mail_settings.attachment_max_bytes:
        raise S.PurchaseError(413, "This attachment is too large to keep in ANUM. Save the purchase without it and add the receipt from Files.", "attachment_too_large")
    try:
        name = valid_file_name(Path(info.filename or "attachment").name or "attachment")
        allowed = resolve_upload_type(name, info.content_type)
        scanned = check_upload_content(name, allowed, data)
        require_capacity(context, "storage", len(data))
    except HTTPException as exc:
        raise S.PurchaseError(exc.status_code, str(exc.detail), "attachment_refused") from None
    digest = hashlib.sha256(data).hexdigest()
    with open_vault_unit(context) as unit:
        same = [record for record in unit.store.same_content(context, digest) if record.owner_user_id == context.user_id]
    if same:
        return same[0].id
    title = V.safe_value(subject or Path(name).stem, 200) or name
    try:
        record, _, _ = V.create_document(context, V.NewDocument(name=name, content_type=allowed.content_type, content=data, sha256=digest, scanned=scanned,
                                                                title=title, category="receipt", access_class="private", source="web"))
    except HTTPException as exc:
        raise S.PurchaseError(exc.status_code, str(exc.detail), "attachment_refused") from None
    return record.id


def save(context: TenantContext, body: FromMail, parsed: Any, raw: bytes) -> dict[str, Any]:
    """The purchase draft (or the purchase the message is about) and its receipt. Runs in a worker thread."""
    from .api import _note_document
    from .devices import ShareEvent, handle

    now = utc_now()
    subject = (parsed.subject or "").strip()
    text = f"{subject}\n{parsed.text or ''}".strip()
    seed = _seed(body.account_id, parsed.message_id, body.folder, body.uid)
    key = S.record_key(context, "share", seed)  # the same key the shared-message path derives from this seed
    with open_purchase_store(context) as store:
        replay = store.purchase_by_idem(context, key)
    if replay is not None:
        return {"status": "replayed", "purchase_id": replay.id, "message": "This email was already saved as a purchase."}
    document_id = _store_attachment(context, raw, body.attachment_index, subject) if body.attachment_index is not None else None
    sent_at = parsed.date if parsed.date is not None and parsed.date.tzinfo is not None and parsed.date <= now else None
    found = P.parse(text, today=now.date()) if text else None
    if found is not None and found.recognised:
        result = handle(context, None, ShareEvent(text=text[:P.MAX_TEXT], occurred_at=sent_at,
                                                  idempotency_key=seed, ownership=body.ownership), source="web")
        purchase_id = result["purchase_id"]
        if result["status"] in {"created", "updated"}:
            with open_purchase_store(context) as store:
                S.add_event(store, context, S.get_purchase(store, context, purchase_id), "share", note=FROM_MAIL_NOTE, source="web", now=now)
    else:
        ordered_on: date = sent_at.date() if sent_at is not None else now.date()
        with open_purchase_store(context) as store:
            purchase = S.new_purchase(context, {"merchant": _sender_name(parsed.sender), "ordered_on": ordered_on, "ownership": body.ownership,
                                                "notes": subject[:500] if subject else None},
                                      now=now, idem_key=key, currency=S.preferences(store, context).default_currency, source="web", needs_review=True)
            try:
                store.add_purchase(purchase)
            except DuplicateError:
                existing = store.purchase_by_idem(context, key)
                return {"status": "replayed", "purchase_id": existing.id if existing else None, "message": "This email was already saved as a purchase."}
            S.add_event(store, context, purchase, "share", to_status="ordered", note=FROM_MAIL_NOTE, source="web", now=now)
        purchase_id = purchase.id
        result = {"status": "created", "purchase_id": purchase_id, "message": f"Saved {purchase.merchant} under Purchases as a draft. Check its details."}
    attached = False
    if document_id is not None and result["status"] in {"created", "updated"}:
        with open_purchase_store(context) as store:
            current = S.get_purchase(store, context, purchase_id, lock=True)
            if current.document_id is None and S.can_edit_purchase(context, current):
                updated = S.apply_changes(current, {"document_id": document_id}, now)
                if store.save_purchase(updated, current.version):
                    S.add_event(store, context, updated, "note", note="Receipt kept from the email", source="web", now=now)
                    attached = True
        if attached:
            _note_document(context, document_id, updated)
    return {**result, "document_id": document_id if attached else None, "attachment_kept": attached}


@router.post("/api/v1/purchases/from-mail", response_model=None)
async def from_mail(body: FromMail, context: TenantContext = Depends(tenant_context), units: UnitsOfWork = Depends(units_of_work)) -> Any:
    """Save the message as a purchase draft, with the chosen attachment as its receipt."""
    from ..mailbox.api import _available, load_access, on_server
    from ..mailbox.config import mail_settings
    from ..mailbox.mime import parse_message
    from ..mailbox.service import MailError

    if not has_permission(context, Permission.PURCHASE_WRITE):
        return _refuse(403, "Purchases are for workspace owners and members. Ask a workspace owner to change your role from viewer to member.", "forbidden")
    if not has_permission(context, Permission.EMAIL_READ):
        return _refuse(403, MAIL_DENIED, "mail_forbidden")
    if body.attachment_index is not None and not has_permission(context, Permission.DOCUMENT_WRITE):
        return _refuse(403, VAULT_DENIED, "vault_forbidden")
    try:
        await asyncio.to_thread(_available, context)
        account, _ = await asyncio.to_thread(load_access, context, body.account_id, "read")
    except HTTPException as exc:
        code = "mailbox_not_found" if exc.status_code == 404 else "mail_forbidden" if exc.status_code == 403 else "mail_unavailable"
        return _refuse(exc.status_code, str(exc.detail), code)
    try:
        raw: bytes = await on_server(context, account, lambda server: server.fetch(body.folder, body.uid, mark_seen=False, max_bytes=mail_settings.message_max_bytes))
    except MailError as exc:
        return _refuse(exc.status, exc.message, "mail_unavailable")
    parsed = parse_message(raw)
    try:
        result = await asyncio.to_thread(save, context, body, parsed, raw)
    except S.PurchaseError as exc:
        return _refuse(exc.status_code, exc.message, exc.code, **exc.extra)
    if result["status"] != "replayed":
        await run_in_unit(units, context, _audit, context, result["purchase_id"], result)
    return JSONResponse(status_code=200 if result["status"] == "replayed" else 201, content=result)


def _audit(repository: Any, context: TenantContext, purchase_id: str, result: dict[str, Any]) -> None:
    """Ids and outcomes only: never the shop, the subject, the sender or the attachment's name."""
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id, action="purchase.from_mail",
        target=purchase_id, outcome="success", correlation_id=purchase_id, created_at=utc_now(),
        metadata={"status": result["status"], "attachment_kept": bool(result.get("attachment_kept")), "document_id": result.get("document_id")},
    ))


__all__ = ["FromMail", "router", "save"]
