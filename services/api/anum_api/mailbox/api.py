"""HTTP routes of the Email Center (docs/email.md#email-center), under ``/api/v1/mail``.

Permissions: reading needs ``email:read``; connecting one's own mailbox, flags, moves,
drafts and asking to send need ``email:act`` (owners and members; viewers have no mail).
A mailbox is private to the person who connected it (row policy and every query). Sending
and deleting for good pause for an approval bound to the exact call (payload hash).
Every state-changing route writes an audit record with ids and counts, never content.
"""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ..agent_tools import ToolCall
from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import has_permission, repository_context, require_permission, tenant_context
from ..email_api import _pause_for_approval, _retire
from ..file_policy import content_disposition
from ..repository import AnumRepository
from ..schemas import Approval, ApprovalStatus, TenantContext, new_id, utc_now
from .config import mail_settings
from .mime import (
    Person,
    forward_subject,
    new_message_id,
    parse_message,
    attachment_bytes,
    quoted,
    references_for,
    reply_subject,
)
from .server import Folder, MailServer, MailServerError, Summary
from .service import (
    DELETE_TOOL,
    SEND_TOOL,
    MailError,
    check_ready_to_send,
    clean_addresses,
    domain_allowed,
    encrypt_password,
    folder_by_role,
    mail_tool_registry,
    on_server,
    require_available,
    send_arguments,
    take_connect_attempt,
    tenant_allowed,
)
from . import monitor, service
from .store import EDITABLE, AccountStatus, DraftKind, DraftStatus, MailAccount, MailDraft, open_mail_store

router = APIRouter(prefix="/api/v1/mail", tags=["mail"])

READ_DENIED = "Reading email needs the owner or member role in this workspace."
ACT_DENIED = "Using a mailbox needs the owner or member role in this workspace."
MAX_BODY = 100_000
MAX_LIMIT = 50
KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,119}$"
# Attachment types served as themselves; anything else downloads as application/octet-stream.
SAFE_TYPES = frozenset({
    "application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain", "text/csv", "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword", "application/vnd.ms-excel", "text/calendar",
})


def _require(context: TenantContext, permission: Permission, detail: str) -> None:
    try:
        require_permission(context, permission)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=detail) from None


def _http(exc: MailError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, outcome: str = "success", metadata: dict[str, Any] | None = None) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action=action, target=target, outcome=outcome, correlation_id=target, created_at=utc_now(), metadata=metadata or {},
        )
    )


def _available(context: TenantContext) -> None:
    try:
        require_available(context)
    except MailError as exc:
        raise _http(exc) from None


# -- schemas ------------------------------------------------------------------------------------


class AccountView(BaseModel):
    id: str
    address: str
    display_name: str | None
    status: AccountStatus
    last_error: str | None
    last_checked_at: str | None
    created_at: str


class MailSetup(BaseModel):
    enabled: bool
    configured: bool
    tenant_allowed: bool
    imap_host: str | None
    imap_port: int
    smtp_host: str | None
    smtp_port: int
    smtp_security: str
    allowed_domains: list[str]
    can_read: bool
    can_act: bool
    max_recipients: int
    send_per_hour: int
    accounts: list[AccountView]


class ConnectBody(BaseModel):
    address: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=500)
    display_name: str | None = Field(default=None, max_length=200)


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=500)


class FolderView(BaseModel):
    id: str
    name: str
    role: str | None
    total: int
    unread: int


class PersonView(BaseModel):
    name: str
    address: str


class MessageView(BaseModel):
    uid: int
    folder: str
    subject: str
    sender: PersonView | None
    to: list[PersonView]
    cc: list[PersonView]
    date: str
    message_id: str | None
    seen: bool
    flagged: bool
    answered: bool
    size: int
    maybe_attachments: bool


class MessagePage(BaseModel):
    items: list[MessageView]
    total: int
    next_before: int | None


class AttachmentView(BaseModel):
    index: int
    filename: str
    content_type: str
    size: int
    downloadable: bool


class MessageDetail(BaseModel):
    message: MessageView
    reply_to: list[PersonView]
    text: str
    text_truncated: bool
    html: str | None
    remote_images_blocked: int
    inline_images_hidden: int
    attachments: list[AttachmentView]


class FlagBody(BaseModel):
    folder: str = Field(min_length=1, max_length=500)
    uids: list[int] = Field(min_length=1, max_length=200)
    flag: Literal["seen", "flagged"]
    value: bool


class MoveBody(BaseModel):
    folder: str = Field(min_length=1, max_length=500)
    uids: list[int] = Field(min_length=1, max_length=200)
    # A role (inbox, archive, junk, trash) or a folder id from the folder list.
    to: str = Field(min_length=1, max_length=500)


class DeleteBody(BaseModel):
    folder: str = Field(min_length=1, max_length=500)
    uids: list[int] = Field(min_length=1, max_length=200)


class ActionResult(BaseModel):
    status: Literal["done", "waiting_approval"]
    summary: str
    approval: Approval | None = None


class DraftView(BaseModel):
    id: str
    account_id: str
    kind: DraftKind
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    in_reply_to: str | None
    message_id: str
    status: DraftStatus
    error: str | None
    approval: Approval | None
    approval_note: str | None
    saved_to_sent: bool | None
    sent_at: str | None
    created_at: str
    updated_at: str


class DraftCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=80)
    kind: DraftKind = "new"
    folder: str | None = Field(default=None, max_length=500)
    uid: int | None = Field(default=None, ge=1)


class DraftUpdate(BaseModel):
    to: list[str] | None = Field(default=None, max_length=100)
    cc: list[str] | None = Field(default=None, max_length=100)
    bcc: list[str] | None = Field(default=None, max_length=100)
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=MAX_BODY)


class SendBody(BaseModel):
    idempotency_key: str = Field(pattern=KEY_PATTERN)


class SendResponse(BaseModel):
    draft: DraftView
    approval: Approval | None


# -- views --------------------------------------------------------------------------------------


def _account_view(account: MailAccount) -> AccountView:
    return AccountView(
        id=account.id, address=account.address, display_name=account.display_name, status=account.status,
        last_error=account.last_error, last_checked_at=account.last_checked_at.isoformat() if account.last_checked_at else None,
        created_at=account.created_at.isoformat(),
    )


def _person(person: Person) -> PersonView:
    return PersonView(name=person.name, address=person.address)


def _message_view(folder: str, item: Summary) -> MessageView:
    return MessageView(
        uid=item.uid, folder=folder, subject=item.subject, sender=_person(item.sender) if item.sender else None,
        to=[_person(p) for p in item.to], cc=[_person(p) for p in item.cc], date=item.date.isoformat(),
        message_id=item.message_id, seen=item.seen, flagged=item.flagged, answered=item.answered, size=item.size,
        maybe_attachments=item.maybe_attachments,
    )


def _folder_view(folder: Folder) -> FolderView:
    return FolderView(id=folder.id, name=folder.name, role=folder.role, total=folder.total, unread=folder.unread)


def _draft_view(draft: MailDraft, repository: AnumRepository, context: TenantContext) -> DraftView:
    approval = None
    note = None
    shown = draft.status
    if draft.approval_id and draft.status == DraftStatus.PENDING_APPROVAL:
        found = repository.get_approval(draft.approval_id, context)
        if found is not None and found.status == ApprovalStatus.PENDING and (found.expires_at is None or found.expires_at > utc_now()):
            approval = found
        elif found is None or found.status in {ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}:
            shown = DraftStatus.DRAFT
            note = (f"The send approval was {found.status.value}." if found is not None else "The send approval is gone.") + " Check the message and send it again."
    return DraftView(
        id=draft.id, account_id=draft.account_id, kind=draft.kind, to=draft.to, cc=draft.cc, bcc=draft.bcc,
        subject=draft.subject, body=draft.body, in_reply_to=draft.in_reply_to, message_id=draft.message_id, status=shown,
        error=draft.error, approval=approval, approval_note=note, saved_to_sent=draft.saved_to_sent,
        sent_at=draft.sent_at.isoformat() if draft.sent_at else None, created_at=draft.created_at.isoformat(), updated_at=draft.updated_at.isoformat(),
    )


def _load_account(context: TenantContext, account_id: str) -> MailAccount:
    with open_mail_store(context) as store:
        account = store.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Mailbox not found.")
    return account


def _load_draft(context: TenantContext, draft_id: str) -> tuple[MailDraft, MailAccount]:
    with open_mail_store(context) as store:
        draft = store.get_draft(draft_id)
        account = store.get_account(draft.account_id) if draft else None
    if draft is None or account is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return draft, account


async def _server(context: TenantContext, account: MailAccount, job: Any) -> Any:
    try:
        return await on_server(context, account, job)
    except MailError as exc:
        raise _http(exc) from None


# -- setup and mailboxes ------------------------------------------------------------------------


@router.get("/setup", response_model=MailSetup)
async def get_setup(context: TenantContext = Depends(tenant_context)) -> MailSetup:
    """What the Email Center can do here: switched on, which server, and the caller's mailboxes."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    cfg = mail_settings
    allowed = cfg.enabled and tenant_allowed(context, cfg)
    accounts: list[AccountView] = []
    if allowed:
        with open_mail_store(context) as store:
            accounts = [_account_view(a) for a in store.list_accounts()]
    return MailSetup(
        enabled=cfg.enabled, configured=cfg.configured, tenant_allowed=allowed,
        imap_host=cfg.imap_host or None, imap_port=cfg.imap_port, smtp_host=cfg.smtp_host or None, smtp_port=cfg.smtp_port,
        smtp_security=cfg.smtp_security, allowed_domains=sorted(cfg.domains), can_read=True,
        can_act=has_permission(context, Permission.EMAIL_ACT), max_recipients=cfg.max_recipients, send_per_hour=cfg.send_per_hour,
        accounts=accounts,
    )


@router.post("/accounts", response_model=AccountView, status_code=status.HTTP_201_CREATED)
async def connect_account(
    body: ConnectBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> AccountView:
    """Check the address and password against the mail server, then keep the password encrypted."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    address = body.address.strip()
    try:
        clean_addresses([address])
    except MailError as exc:
        raise _http(exc) from None
    if not domain_allowed(address):
        raise HTTPException(status_code=422, detail=f"Only mailboxes on {', '.join(sorted(mail_settings.domains))} can be connected here.")
    with open_mail_store(context) as store:
        if any(a.address.lower() == address.lower() for a in store.list_accounts()):
            raise HTTPException(status_code=409, detail="This mailbox is already connected.")
    if not take_connect_attempt(context):
        _audit(repository, context, "mail.account.connect", address.rsplit("@", 1)[-1], "denied", {"reason": "rate_limited"})
        monitor.bump(context, "connect_rate_limited")
        raise HTTPException(status_code=429, detail="Too many connection attempts. Wait an hour and try again.")
    server: MailServer = service.server_factory(mail_settings, address, body.password)
    try:
        await asyncio.to_thread(server.check_login)
    except MailServerError as exc:
        _audit(repository, context, "mail.account.connect", address.rsplit("@", 1)[-1], "failure", {"reason": exc.kind})
        message = "The mail server refused this address and password. Use the mailbox's app password." if exc.kind == "auth" else exc.message
        raise HTTPException(status_code=422 if exc.kind == "auth" else service.HTTP_STATUS.get(exc.kind, 502), detail=message) from None
    now = utc_now()
    account = MailAccount(
        id=new_id("mailbox"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        address=address, display_name=(body.display_name or "").strip() or None, password_enc=encrypt_password(body.password),
        status=AccountStatus.CONNECTED, last_checked_at=now, created_at=now, updated_at=now,
    )
    try:
        with open_mail_store(context) as store:
            store.save_account(account)
    except ValueError:
        raise HTTPException(status_code=409, detail="This mailbox is already connected in this workspace.") from None
    _audit(repository, context, "mail.account.connect", account.id)
    return _account_view(account)


@router.put("/accounts/{account_id}/password", response_model=AccountView)
async def update_password(
    account_id: str, body: PasswordBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> AccountView:
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    if not take_connect_attempt(context):
        monitor.bump(context, "connect_rate_limited")
        raise HTTPException(status_code=429, detail="Too many connection attempts. Wait an hour and try again.")
    server: MailServer = service.server_factory(mail_settings, account.address, body.password)
    try:
        await asyncio.to_thread(server.check_login)
    except MailServerError as exc:
        _audit(repository, context, "mail.account.password", account.id, "failure", {"reason": exc.kind})
        message = "The mail server refused this password." if exc.kind == "auth" else exc.message
        raise HTTPException(status_code=422 if exc.kind == "auth" else service.HTTP_STATUS.get(exc.kind, 502), detail=message) from None
    now = utc_now()
    with open_mail_store(context) as store:
        stored = store.get_account(account.id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Mailbox not found.")
        stored.password_enc = encrypt_password(body.password)
        stored.status, stored.last_error, stored.last_checked_at, stored.updated_at = AccountStatus.CONNECTED, None, now, now
        store.save_account(stored)
    _audit(repository, context, "mail.account.password", account.id)
    return _account_view(stored)


@router.post("/accounts/{account_id}/check", response_model=AccountView)
async def check_account(
    account_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> AccountView:
    """Sign in to the mail server now and record the mailbox's health."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    try:
        await on_server(context, account, lambda server: server.check_login())
    except MailError as exc:
        _audit(repository, context, "mail.account.check", account.id, "failure", {"reason": exc.kind})
        return _account_view(_load_account(context, account_id))
    _audit(repository, context, "mail.account.check", account.id)
    return _account_view(_load_account(context, account_id))


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(
    account_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> Response:
    """Forget the mailbox: its password and every draft written in ANUM. Mail on the server stays."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    account = _load_account(context, account_id)
    with open_mail_store(context) as store:
        for draft in store.list_drafts(account.id, 1000):
            if draft.status == DraftStatus.PENDING_APPROVAL:
                _retire(repository, context, draft)  # type: ignore[arg-type]
        store.delete_account(account.id)
    _audit(repository, context, "mail.account.disconnect", account.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- reading ------------------------------------------------------------------------------------


@router.get("/accounts/{account_id}/folders", response_model=list[FolderView])
async def list_folders(account_id: str, context: TenantContext = Depends(tenant_context)) -> list[FolderView]:
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    folders: list[Folder] = await _server(context, account, lambda server: server.folders())
    order = {"inbox": 0, "drafts": 1, "sent": 2, "archive": 3, "junk": 4, "trash": 5}
    return [_folder_view(f) for f in sorted((f for f in folders if f.selectable), key=lambda f: (order.get(f.role or "", 9), f.name.lower()))]


@router.get("/accounts/{account_id}/messages", response_model=MessagePage)
async def list_messages(
    account_id: str,
    folder: str = Query(default="INBOX", min_length=1, max_length=500),
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=25, ge=1, le=MAX_LIMIT),
    q: str | None = Query(default=None, max_length=200),
    unread: bool = False,
    flagged: bool = False,
    context: TenantContext = Depends(tenant_context),
) -> MessagePage:
    """Newest first, ``limit`` at a time; ``before`` is the ``next_before`` of the previous page."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    query = re.sub(r"[\x00-\x1f\x7f]", " ", q).strip() if q else None
    page = await _server(context, account, lambda server: server.messages(folder, before=before, limit=limit, query=query or None, unread=unread, flagged=flagged))
    return MessagePage(items=[_message_view(folder, item) for item in page.items], total=page.total, next_before=page.next_before)


@router.get("/accounts/{account_id}/message", response_model=MessageDetail)
async def read_message(
    account_id: str,
    folder: str = Query(min_length=1, max_length=500),
    uid: int = Query(ge=1),
    context: TenantContext = Depends(tenant_context),
) -> MessageDetail:
    """One message: text, sanitized HTML (remote images blocked) and attachments. Marks it read."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    mark = has_permission(context, Permission.EMAIL_ACT)
    raw: bytes = await _server(context, account, lambda server: server.fetch(folder, uid, mark_seen=mark, max_bytes=mail_settings.message_max_bytes))
    parsed = parse_message(raw)
    summary = Summary(
        uid=uid, subject=parsed.subject, sender=parsed.sender, to=parsed.to, cc=parsed.cc, date=parsed.date or utc_now(),
        message_id=parsed.message_id, seen=True, flagged=False, answered=False, size=len(raw), maybe_attachments=bool(parsed.attachments),
    )
    return MessageDetail(
        message=_message_view(folder, summary), reply_to=[_person(p) for p in parsed.reply_to], text=parsed.text,
        text_truncated=parsed.text_truncated, html=parsed.html.html if parsed.html else None,
        remote_images_blocked=parsed.html.remote_images_blocked if parsed.html else 0,
        inline_images_hidden=parsed.html.inline_images_hidden if parsed.html else 0,
        attachments=[AttachmentView(index=a.index, filename=a.filename, content_type=a.content_type, size=a.size, downloadable=a.size <= mail_settings.attachment_max_bytes) for a in parsed.attachments],
    )


@router.get("/accounts/{account_id}/attachment")
async def download_attachment(
    account_id: str,
    folder: str = Query(min_length=1, max_length=500),
    uid: int = Query(ge=1),
    index: int = Query(ge=0, le=500),
    context: TenantContext = Depends(tenant_context),
) -> Response:
    """Download only: never shown inline, never sniffed, and capped in size."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    raw: bytes = await _server(context, account, lambda server: server.fetch(folder, uid, mark_seen=False, max_bytes=mail_settings.message_max_bytes))
    found = attachment_bytes(raw, index)
    if found is None:
        raise HTTPException(status_code=404, detail="That attachment is not in this message.")
    data, info = found
    if len(data) > mail_settings.attachment_max_bytes:
        raise HTTPException(status_code=413, detail="This attachment is too large to download through ANUM. Open it in webmail.")
    media = info.content_type if info.content_type in SAFE_TYPES else "application/octet-stream"
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": content_disposition("attachment", info.filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, no-store",
        },
    )


# -- acting on messages -------------------------------------------------------------------------


async def _resolve_target(context: TenantContext, account: MailAccount, target: str) -> str:
    if target in {"inbox", "archive", "junk", "trash", "sent", "drafts"}:
        folders: list[Folder] = await _server(context, account, lambda server: server.folders())
        found = folder_by_role(folders, target)
        if found is None:
            raise HTTPException(status_code=409, detail=f"This mailbox has no {target} folder.")
        return found.id
    return target


@router.post("/accounts/{account_id}/flags", response_model=ActionResult)
async def set_flags(
    account_id: str, body: FlagBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> ActionResult:
    """Read or unread, starred or not, on the mail server."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    await _server(context, account, lambda server: server.set_flag(body.folder, body.uids, body.flag, body.value))
    _audit(repository, context, f"mail.message.{body.flag}", account.id, metadata={"count": len(body.uids), "value": body.value})
    return ActionResult(status="done", summary=f"Updated {len(body.uids)} message(s).")


@router.post("/accounts/{account_id}/move", response_model=ActionResult)
async def move_messages(
    account_id: str, body: MoveBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> ActionResult:
    """Archive, mark as junk, move to Trash, or back to the inbox (all reversible)."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    target = await _resolve_target(context, account, body.to)
    if target == body.folder:
        raise HTTPException(status_code=409, detail="The messages are already in that folder.")
    await _server(context, account, lambda server: server.move(body.folder, body.uids, target))
    _audit(repository, context, "mail.message.move", account.id, metadata={"count": len(body.uids), "to_role": body.to if body.to in {"inbox", "archive", "junk", "trash"} else "folder"})
    return ActionResult(status="done", summary=f"Moved {len(body.uids)} message(s).")


@router.post("/accounts/{account_id}/delete", response_model=ActionResult)
async def delete_messages(
    account_id: str, body: DeleteBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> ActionResult:
    """Delete for good (Trash or Junk only): waits for an approval of exactly these messages."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    account = _load_account(context, account_id)
    folders: list[Folder] = await _server(context, account, lambda server: server.folders())
    found = next((f for f in folders if f.id == body.folder), None)
    if found is None or found.role not in {"trash", "junk"}:
        raise HTTPException(status_code=409, detail="Move messages to Trash first. Only Trash and Junk can be emptied for good.")
    uids = sorted(set(body.uids))
    call = ToolCall(name=DELETE_TOOL, arguments={"account_id": account.id, "owner_user_id": account.owner_user_id, "mailbox": account.address, "folder": body.folder, "uids": uids})
    _, approval = _pause_for_approval(
        repository, context, call, f"Delete {len(uids)} message(s) for good from {found.name}",
        f"Delete {len(uids)} message(s) for good from {found.name} in {account.address}.", registry=mail_tool_registry(),
    )
    _audit(repository, context, "mail.message.delete.requested", account.id, metadata={"count": len(uids), "approval_id": approval.id, "payload_hash": approval.payload_hash})
    return ActionResult(status="waiting_approval", summary="Waiting for approval to delete for good.", approval=approval)


# -- drafts and sending -------------------------------------------------------------------------


@router.get("/drafts", response_model=list[DraftView])
async def list_drafts(
    account_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[DraftView]:
    """Messages written in ANUM (drafts, waiting for approval, sent, failed), newest first."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    with open_mail_store(context) as store:
        drafts = store.list_drafts(account_id, limit)
    return [_draft_view(d, repository, context) for d in drafts]


@router.get("/drafts/{draft_id}", response_model=DraftView)
async def get_draft(draft_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> DraftView:
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    draft, _ = _load_draft(context, draft_id)
    return _draft_view(draft, repository, context)


@router.post("/drafts", response_model=DraftView, status_code=status.HTTP_201_CREATED)
async def create_draft(
    body: DraftCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> DraftView:
    """A new message, or a reply, reply to all or forward of a message (quoted, threaded)."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    account = _load_account(context, body.account_id)
    now = utc_now()
    draft = MailDraft(
        id=new_id("maildraft"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        account_id=account.id, kind=body.kind, message_id=new_message_id(account.address), created_at=now, updated_at=now,
    )
    if body.kind != "new":
        if not body.folder or not body.uid:
            raise HTTPException(status_code=422, detail="Pick the message to answer or forward.")
        folder, uid = body.folder, body.uid
        raw: bytes = await _server(context, account, lambda server: server.fetch(folder, uid, mark_seen=False, max_bytes=mail_settings.message_max_bytes))
        original = parse_message(raw)
        own = account.address.lower()
        if body.kind == "forward":
            draft.subject = forward_subject(original.subject)
        else:
            draft.subject = reply_subject(original.subject)
            first = original.reply_to or ([original.sender] if original.sender else [])
            to = [p.address for p in first]
            cc: list[str] = []
            if body.kind == "reply_all":
                to += [p.address for p in original.to if p.address.lower() != own]
                cc = [p.address for p in original.cc if p.address.lower() != own]
            draft.to = _valid_only([a for a in to if a.lower() != own] or to)
            draft.cc = [a for a in _valid_only(cc) if a.lower() not in {x.lower() for x in draft.to}]
            draft.in_reply_to = original.message_id
            draft.references = references_for(original)
        draft.body = quoted(original, forward=body.kind == "forward")[:MAX_BODY]
        draft.source_folder, draft.source_uid = folder, uid
    with open_mail_store(context) as store:
        store.save_draft(draft)
    _audit(repository, context, "mail.draft.create", draft.id, metadata={"kind": draft.kind})
    return _draft_view(draft, repository, context)


def _valid_only(values: list[str]) -> list[str]:
    try:
        return clean_addresses(values)
    except MailError:
        return [value for value in values if _is_address(value)]


def _is_address(value: str) -> bool:
    try:
        clean_addresses([value])
    except MailError:
        return False
    return True


@router.patch("/drafts/{draft_id}", response_model=DraftView)
async def update_draft(
    draft_id: str, body: DraftUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> DraftView:
    """Edit a draft. Editing one waiting for approval withdraws that approval."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    draft, _ = _load_draft(context, draft_id)
    if draft.status not in EDITABLE:
        raise HTTPException(status_code=409, detail=f"This message is {draft.status.value} and can no longer be edited.")
    try:
        if body.to is not None:
            draft.to = clean_addresses(body.to)
        if body.cc is not None:
            draft.cc = clean_addresses(body.cc)
        if body.bcc is not None:
            draft.bcc = clean_addresses(body.bcc)
    except MailError as exc:
        raise _http(exc) from None
    if body.subject is not None:
        draft.subject = re.sub(r"[\r\n\t]+", " ", body.subject).strip()
    if body.body is not None:
        draft.body = body.body
    if draft.status == DraftStatus.PENDING_APPROVAL:
        _retire(repository, context, draft)  # type: ignore[arg-type]
    draft.status, draft.approval_id, draft.task_id, draft.idempotency_key, draft.error = DraftStatus.DRAFT, None, None, None, None
    draft.updated_at = utc_now()
    with open_mail_store(context) as store:
        store.save_draft(draft)
    _audit(repository, context, "mail.draft.update", draft.id, metadata={"recipients": len(draft.to) + len(draft.cc) + len(draft.bcc)})
    return _draft_view(draft, repository, context)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(
    draft_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> Response:
    """Discard a draft, or remove a sent or failed message from ANUM's list (mail on the server stays)."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    draft, _ = _load_draft(context, draft_id)
    if draft.status == DraftStatus.SENDING:
        raise HTTPException(status_code=409, detail="This message is being sent right now.")
    if draft.status == DraftStatus.PENDING_APPROVAL:
        _retire(repository, context, draft)  # type: ignore[arg-type]
    with open_mail_store(context) as store:
        store.delete_draft(draft.id)
    _audit(repository, context, "mail.draft.delete", draft.id, metadata={"status": draft.status.value})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/drafts/{draft_id}/send", response_model=SendResponse)
async def send_draft(
    draft_id: str, body: SendBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> SendResponse:
    """Ask to send: a paused ``send_mail`` call with exactly this message.

    The same ``idempotency_key`` always answers with the same message and approval; approving
    goes through ``POST /api/v1/approvals/{id}/approve`` with the payload hash shown.
    """
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    draft, account = _load_draft(context, draft_id)
    if draft.idempotency_key == body.idempotency_key and draft.status != DraftStatus.DRAFT:
        view = _draft_view(draft, repository, context)
        return SendResponse(draft=view, approval=view.approval)
    with open_mail_store(context) as store:
        other = store.find_by_key(account.id, body.idempotency_key)
    if other is not None and other.id != draft.id:
        raise HTTPException(status_code=409, detail="This send key was already used for another message.")
    if draft.status not in {DraftStatus.DRAFT, DraftStatus.FAILED, DraftStatus.PENDING_APPROVAL}:
        raise HTTPException(status_code=409, detail=f"This message is {draft.status.value}.")
    if account.status == AccountStatus.AUTH_FAILED:
        raise HTTPException(status_code=409, detail="The mail server refused the saved password. Enter it again in Mailbox settings before sending.")
    try:
        check_ready_to_send(draft)
    except MailError as exc:
        raise _http(exc) from None
    with open_mail_store(context) as store:
        if store.count_sends_since(account.id, utc_now() - timedelta(hours=1)) >= mail_settings.send_per_hour:
            monitor.bump(context, "send_rate_limited")
            raise HTTPException(status_code=429, detail=f"This mailbox sent {mail_settings.send_per_hour} messages in the last hour. Try again later.")
    arguments = send_arguments(draft, account)
    if draft.status == DraftStatus.PENDING_APPROVAL and draft.approval_id:
        existing = repository.get_approval(draft.approval_id, context)
        if existing is not None and existing.status == ApprovalStatus.PENDING and (existing.expires_at is None or existing.expires_at > utc_now()) and existing.arguments.get("body") == arguments["body"]:
            draft.idempotency_key = body.idempotency_key
            with open_mail_store(context) as store:
                store.save_draft(draft)
            return SendResponse(draft=_draft_view(draft, repository, context), approval=existing)
        _retire(repository, context, draft)  # type: ignore[arg-type]
    recipients = ", ".join(draft.to)
    task, approval = _pause_for_approval(
        repository, context, ToolCall(name=SEND_TOOL, arguments=arguments), f"Send email to {recipients}"[:160],
        f"Send '{draft.subject or '(no subject)'}' from {account.address} to {recipients}.", registry=mail_tool_registry(),
    )
    draft.status, draft.approval_id, draft.task_id, draft.error = DraftStatus.PENDING_APPROVAL, approval.id, task.id, None
    draft.idempotency_key = body.idempotency_key
    draft.updated_at = utc_now()
    try:
        with open_mail_store(context) as store:
            store.save_draft(draft)
    except ValueError:
        raise HTTPException(status_code=409, detail="This send key was already used for another message.") from None
    _audit(
        repository, context, "mail.send.requested", draft.id,
        metadata={"approval_id": approval.id, "task_id": task.id, "recipients": len(draft.to) + len(draft.cc) + len(draft.bcc), "payload_hash": approval.payload_hash},
    )
    return SendResponse(draft=_draft_view(draft, repository, context), approval=approval)
