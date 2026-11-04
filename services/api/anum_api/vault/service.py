"""The document vault's rules (docs/documents.md).

Every function here is synchronous and runs in a worker thread (``asyncio.to_thread``): one short
unit of work each, never on the event loop and never across an OCR call
(docs/realtime.md#event-loop). The HTTP layer (``vault/api.py``) checks the role permission first;
these functions check what depends on the document: a private document exists only for its owner
and the people it is shared with (the row policy says the same), and only the owner, or a
workspace owner for a workspace document, changes it.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException

from .. import file_policy
from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import has_permission
from ..errors import ApplicationError, ErrorCode, ErrorDetail
from ..files import (
    FileContent,
    FileContentChanged,
    FileRecord,
    StorageQuotaExceeded,
    file_store,
    human_bytes,
    workspace_object_key,
)
from ..schemas import TenantContext, new_id, utc_now
from ..secret_box import SecretDecryptionError, secret_cipher
from ..settings import settings
from .models import (
    COUNTRY,
    MAX_TAGS,
    TAG,
    AccessEvent,
    AccessGrant,
    CategoryRecord,
    DocumentQuery,
    DocumentRecord,
    ExtractionRecord,
    LinkRecord,
    VaultSettings,
    extraction_kind,
    is_category,
    last_four,
    normalize_number,
)
from .ocr_parse import CURRENCY_CODES, safe_value
from .store import CategoryInUse, CategoryTaken, StaleDocument, VaultUnit, open_vault_unit

logger = logging.getLogger("anum_api.vault")

NOT_FOUND = "Document not found. It may have been deleted, or it is private to someone else."
EDIT_DENIED = "Only the person who added this document can change it (a workspace owner can also change workspace documents)."
SHARE_DENIED = "Only the person who added a private document can share it."
STALE = "This document changed while you were editing it. Reload it and try again."
JOB_USER = "system:vault"


# --------------------------------------------------------------------------- helpers


def today() -> date:
    return utc_now().date()


def log_event(unit: VaultUnit, context: TenantContext, document_id: str, action: str, detail: dict[str, Any] | None = None) -> None:
    """The document's access log (ids and codes only), and the workspace audit log for changes."""
    now = utc_now()
    unit.store.log(context, AccessEvent(id=new_id("vlog"), document_id=document_id, actor=context.user_id, action=action,
                                        detail=detail or {}, created_at=now))
    if action not in {"view", "download", "link", "link_opened", "preview"}:
        unit.store.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action=f"document.{action}", target=f"vault_document:{document_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=now, metadata={k: v for k, v in (detail or {}).items() if isinstance(v, (str, int, bool))},
        ))


def can_edit(context: TenantContext, record: DocumentRecord) -> bool:
    if record.owner_user_id == context.user_id and has_permission(context, Permission.DOCUMENT_WRITE):
        return True
    return record.access_class == "workspace" and has_permission(context, Permission.DOCUMENT_MANAGE)


def get_visible(unit: VaultUnit, context: TenantContext, document_id: str, *, for_update: bool = False) -> DocumentRecord:
    record = unit.store.get_document(context, document_id, for_update=for_update)
    if record is None:
        raise HTTPException(404, NOT_FOUND)
    return record


def editable(unit: VaultUnit, context: TenantContext, document_id: str) -> DocumentRecord:
    record = get_visible(unit, context, document_id, for_update=True)
    if not can_edit(context, record):
        raise HTTPException(403, EDIT_DENIED)
    return record


def file_of(unit: VaultUnit, context: TenantContext, record: DocumentRecord) -> FileRecord:
    found = unit.files.get(context, record.file_id)
    if found is None:
        raise HTTPException(404, "The document's file is missing. Delete the document and add it again.")
    return found


def quota_error(used: int, size: int) -> ApplicationError:
    quota = settings.files_workspace_quota_bytes
    return ApplicationError(
        ErrorCode.PAYLOAD_TOO_LARGE,
        f"This workspace has used {human_bytes(used)} of its {human_bytes(quota)} storage, so a {human_bytes(size)} document does not fit. "
        "Delete documents, files or older versions you no longer need, or ask the operator to raise ANUM_FILES_WORKSPACE_QUOTA_BYTES.",
        status_code=413,
        details=[ErrorDetail(type="storage_quota", message=f"used={used} quota={quota}")],
    )


def encode_cursor(record: DocumentRecord) -> str:
    raw = f"{record.created_at.isoformat()}|{record.id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        stamp, document_id = raw.split("|", 1)
        return datetime.fromisoformat(stamp), document_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Unknown or invalid cursor. Reload the list.") from exc


def duplicate_conflict(existing: DocumentRecord) -> ApplicationError:
    where = "in the trash (restore it from there)" if existing.status == "deleted" else f"as {existing.title}"
    return ApplicationError(
        ErrorCode.CONFLICT,
        f"This exact file is already in the vault {where}. Open it, or add it anyway as a separate document.",
        status_code=409,
        details=[ErrorDetail(type="duplicate", message=existing.id), ErrorDetail(type="duplicate_status", message=existing.status)],
    )


# --------------------------------------------------------------------------- values


@dataclass
class DocumentFields:
    title: str | None = None
    category: str | None = None
    access_class: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    issuer: str | None = None
    country: str | None = None
    tags: list[str] | None = None
    description: str | None = None
    number: str | None = None
    reminder_days: int | None = None
    retention_until: date | None = None
    assistant_may_read: bool | None = None


def _clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for raw in tags:
        tag = safe_value(raw, 40)
        if not tag:
            continue
        if not TAG.fullmatch(tag):
            raise HTTPException(422, "Tags are short words without control characters.")
        if tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise HTTPException(422, f"A document can have at most {MAX_TAGS} tags.")
    return out


def apply_fields(record: DocumentRecord, values: dict[str, Any], custom: set[str]) -> DocumentRecord:
    """The record with the sent fields applied and checked (only keys present in ``values``)."""
    update: dict[str, Any] = {}
    if "title" in values:
        title = safe_value(str(values["title"] or ""), 200)
        if not title:
            raise HTTPException(422, "Give the document a title.")
        update["title"] = title
    if "category" in values:
        category = str(values["category"] or "")
        if not is_category(category, custom):
            raise HTTPException(422, "Choose one of the vault's categories.")
        update["category"] = category
    for key in ("issue_date", "expiry_date", "retention_until"):
        if key in values:
            update[key] = values[key]
    if "issuer" in values:
        update["issuer"] = safe_value(str(values["issuer"] or ""), 200) or None
    if "country" in values:
        country = str(values["country"] or "").strip().upper() or None
        if country is not None and not COUNTRY.fullmatch(country):
            raise HTTPException(422, "The country is a two-letter code such as SA or AE.")
        update["country"] = country
    if "tags" in values:
        update["tags"] = _clean_tags(list(values["tags"] or []))
    if "description" in values:
        text = str(values["description"] or "").replace("\x00", "").strip()
        if len(text) > 2000:
            raise HTTPException(422, "The description can be at most 2,000 characters.")
        update["description"] = text
    if "reminder_days" in values:
        days = values["reminder_days"]
        if days is not None and not (1 <= int(days) <= 365):
            raise HTTPException(422, "Remind between 1 and 365 days before the expiry date.")
        update["reminder_days"] = days
    if "access_class" in values:
        if values["access_class"] not in ("private", "workspace"):
            raise HTTPException(422, "Access is private or workspace.")
        update["access_class"] = values["access_class"]
    if "number" in values:
        number = normalize_number(str(values["number"] or ""))
        if number and not 4 <= len(number) <= 40:
            raise HTTPException(422, "A document number has 4 to 40 letters and digits.")
        if number:
            update["number_ciphertext"] = secret_cipher().encrypt(number)
            update["number_last4"] = last_four(number)
        else:
            update["number_ciphertext"] = None
            update["number_last4"] = None
    if "assistant_may_read" in values:
        update["assistant_may_read"] = bool(values["assistant_may_read"])
    result = record.model_copy(update=update)
    if result.issue_date and result.expiry_date and result.issue_date > result.expiry_date:
        raise HTTPException(422, "The issue date must be on or before the expiry date.")
    if result.access_class == "private" and result.assistant_may_read:
        if values.get("assistant_may_read"):
            raise HTTPException(422, "The assistant may read workspace documents only. Make the document visible to the workspace first.")
        result = result.model_copy(update={"assistant_may_read": False})
    if result.expiry_date != record.expiry_date:
        # A new expiry date starts its reminders afresh.
        result = result.model_copy(update={"reminders_sent": []})
    return result


def reveal_number(record: DocumentRecord) -> str | None:
    if not record.number_ciphertext:
        return None
    try:
        return secret_cipher().decrypt(record.number_ciphertext)
    except SecretDecryptionError as exc:
        raise HTTPException(500, "The stored number could not be decrypted. Ask the operator to check ANUM_SECRETS_KEY.") from exc


# --------------------------------------------------------------------------- settings and categories


def read_settings(context: TenantContext) -> tuple[VaultSettings, list[CategoryRecord]]:
    with open_vault_unit(context) as unit:
        return unit.store.get_settings(context), unit.store.categories(context)


def save_settings(context: TenantContext, value: VaultSettings) -> VaultSettings:
    saved = value.model_copy(update={"updated_by": context.user_id, "updated_at": utc_now()})
    with open_vault_unit(context) as unit:
        unit.store.save_settings(context, saved)
        unit.store.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="document.settings_changed", target=f"vault_settings:{context.workspace_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata={"reminder_days": saved.reminder_days, "trash_days": saved.trash_days, "ocr_receipts": saved.ocr_receipts,
                      "ocr_identity_dates": saved.ocr_identity_dates},
        ))
    return saved


def add_category(context: TenantContext, label: str) -> CategoryRecord:
    clean = safe_value(label, 80)
    if not clean:
        raise HTTPException(422, "Name the category.")
    record = CategoryRecord(id=new_id("vcat"), label=clean, created_by=context.user_id, created_at=utc_now())
    with open_vault_unit(context) as unit:
        try:
            return unit.store.add_category(context, record)
        except CategoryTaken as exc:
            raise HTTPException(409, f"There is already a category called {clean}.") from exc


def delete_category(context: TenantContext, category_id: str) -> None:
    with open_vault_unit(context) as unit:
        try:
            if not unit.store.delete_category(context, category_id):
                raise HTTPException(404, "Category not found.")
        except CategoryInUse as exc:
            raise HTTPException(409, "Documents still use this category. Move them to another category first.") from exc


def custom_ids(unit: VaultUnit, context: TenantContext) -> set[str]:
    return {category.id for category in unit.store.categories(context)}


# --------------------------------------------------------------------------- adding documents


@dataclass
class NewDocument:
    name: str
    content_type: str
    content: bytes
    sha256: str
    scanned: bool
    title: str
    category: str
    access_class: str
    source: str
    allow_duplicate: bool = False


def check_duplicate(context: TenantContext, sha256: str) -> None:
    with open_vault_unit(context) as unit:
        found = unit.store.same_content(context, sha256)
    if found:
        raise duplicate_conflict(found[0])


def create_document(context: TenantContext, new: NewDocument) -> tuple[DocumentRecord, FileRecord, bool]:
    """Store the bytes, the hidden file row and the document in one unit; return whether to extract."""
    quota = settings.files_workspace_quota_bytes
    file_id = new_id("file")
    try:
        key = workspace_object_key(context.tenant_id, context.workspace_id, "files", file_id, new.sha256)
    except ValueError as exc:
        raise HTTPException(422, "Invalid tenant or workspace identifier") from exc
    now = utc_now()
    file_record = FileRecord(
        id=file_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=new.name, content_type=new.content_type,
        size_bytes=len(new.content), sha256=new.sha256, storage_key=key, created_by=context.user_id, created_at=now,
        scan_status="clean" if new.scanned else None, scanned_at=now if new.scanned else None, vault="hidden",
    )
    file_store.storage.put(key, new.content, new.content_type)
    try:
        with open_vault_unit(context) as unit:
            if not new.allow_duplicate:
                found = unit.store.same_content(context, new.sha256)
                if found:
                    raise duplicate_conflict(found[0])
            if not is_category(new.category, custom_ids(unit, context)):
                raise HTTPException(422, "Choose one of the vault's categories.")
            usage = unit.files.usage(context)
            if usage.used_bytes + len(new.content) > quota:
                raise quota_error(usage.used_bytes, len(new.content))
            try:
                unit.files.add(file_record, quota)
            except StorageQuotaExceeded as exc:
                raise quota_error(exc.usage.used_bytes, len(new.content)) from exc
            record = DocumentRecord(
                id=new_id("vdoc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, file_id=file_id,
                owner_user_id=context.user_id, title=new.title, category=new.category, access_class=new.access_class,  # type: ignore[arg-type]
                content_sha256=new.sha256, source=new.source,  # type: ignore[arg-type]
                created_by=context.user_id, created_at=now, updated_by=context.user_id, updated_at=now,
            )
            unit.store.create_document(context, record)
            log_event(unit, context, record.id, "upload", {"source": new.source, "category": new.category, "size": len(new.content)})
            vault_settings = unit.store.get_settings(context)
            kind = extraction_kind(new.category)
            extract = (kind == "receipt" and vault_settings.ocr_receipts) or (
                kind == "dates" and vault_settings.ocr_identity_dates
            )
    except BaseException:
        # No row, no bytes: never leave unreachable bytes behind.
        file_store.storage.delete(key)
        raise
    return record, file_record, extract


# --------------------------------------------------------------------------- reading


def list_documents(context: TenantContext, query: DocumentQuery) -> list[DocumentRecord]:
    with open_vault_unit(context) as unit:
        return unit.store.list_documents(context, query, today())


@dataclass
class Detail:
    record: DocumentRecord
    file: FileRecord
    access: list[AccessGrant]
    links: list[LinkRecord]
    extractions: list[ExtractionRecord]
    events: list[AccessEvent]


def detail(context: TenantContext, document_id: str, *, log: bool = True) -> Detail:
    with open_vault_unit(context) as unit:
        record = get_visible(unit, context, document_id)
        found = file_of(unit, context, record)
        result = Detail(
            record=record, file=found, access=unit.store.access(context, document_id), links=unit.store.links(context, document_id),
            extractions=unit.store.extractions(context, document_id),
            events=unit.store.events(context, document_id, 50) if can_edit(context, record) else [],
        )
        if log:
            log_event(unit, context, document_id, "view")
    return result


def read_bytes(context: TenantContext, document_id: str, action: str, version_id: str | None = None) -> tuple[DocumentRecord, FileRecord, bytes, int]:
    """The bytes of the current version (or ``version_id``), quarantine refused, the read logged."""
    with open_vault_unit(context) as unit:
        record = get_visible(unit, context, document_id)
        found = file_of(unit, context, record)
        key, sha, number, status = found.storage_key, found.sha256, found.version, found.scan_status
        if version_id is not None:
            version = unit.files.get_version(context, found.id, version_id)
            if version is None:
                raise HTTPException(404, "Version not found: it may have been deleted. Reload the versions.")
            key, sha, number, status = version.storage_key, version.sha256, version.version, version.scan_status
        if status == "infected":
            raise HTTPException(409, "This file is quarantined: the malware scanner found something in it. Delete it.")
        log_event(unit, context, document_id, action, {"version": number})
    try:
        content = file_store.storage.get(key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    del sha
    return record, found, content, number


def link_for(context: TenantContext, document_id: str, inline: bool) -> tuple[str, datetime]:
    with open_vault_unit(context) as unit:
        record = get_visible(unit, context, document_id)
        found = file_of(unit, context, record)
        if found.scan_status == "infected":
            raise HTTPException(409, "This file is quarantined: the malware scanner found something in it. Delete it.")
        if inline and file_policy.kind_of(found.content_type, found.name) not in file_policy.PREVIEWABLE_KINDS:
            raise HTTPException(422, "This kind of file has no preview; download it instead.")
        log_event(unit, context, document_id, "link", {"inline": inline})
    expires_at = utc_now().replace(microsecond=0) + timedelta(seconds=settings.files_link_ttl_seconds)
    token = file_policy.sign_link(file_policy.LinkClaims(
        tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id,
        file_id=f"vault:{record.id}", sha256=found.sha256, inline=inline, expires_at=expires_at))
    return token, expires_at


def serve_token(token: str) -> tuple[FileRecord, bytes, bool]:
    try:
        claims = file_policy.verify_link(token)
    except file_policy.InvalidLink as exc:
        raise HTTPException(410, "This document link has expired or is invalid. Open the document again for a new link.") from exc
    if not claims.file_id.startswith("vault:"):
        raise HTTPException(404, NOT_FOUND)
    context = TenantContext(tenant_id=claims.tenant_id, workspace_id=claims.workspace_id, user_id=claims.user_id)
    if settings.auth_mode == "oidc":
        from ..dependencies import lookup_membership

        membership = lookup_membership(context)
        if membership is None or not membership.active:
            raise HTTPException(403, "The person who opened this link is no longer a member of the workspace.")
    with open_vault_unit(context) as unit:
        record = unit.store.get_document(context, claims.file_id.removeprefix("vault:"))
        found = None if record is None or record.status != "active" else unit.files.get(context, record.file_id)
        if found is None or found.sha256 != claims.sha256:
            raise HTTPException(404, NOT_FOUND)
        if found.scan_status == "infected":
            raise HTTPException(409, "This file is quarantined: the malware scanner found something in it.")
        log_event(unit, context, record.id, "link_opened", {"inline": claims.inline})  # type: ignore[union-attr]
    try:
        content = file_store.storage.get(found.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    return found, content, claims.inline


# --------------------------------------------------------------------------- changing


def update_document(context: TenantContext, document_id: str, expected_version: int, values: dict[str, Any]) -> tuple[DocumentRecord, str | None]:
    """Apply the fields; returns the record and the assistant change (``index``/``forget``/None) to run after the commit."""
    with open_vault_unit(context) as unit:
        record = editable(unit, context, document_id)
        if record.status != "active":
            raise HTTPException(409, "Restore the document from the trash before changing it.")
        if record.row_version != expected_version:
            raise HTTPException(409, STALE)
        if "access_class" in values and values["access_class"] != record.access_class and record.owner_user_id != context.user_id:
            raise HTTPException(403, "Only the person who added a document changes who can see it.")
        if "number" in values and record.owner_user_id != context.user_id:
            raise HTTPException(403, "Only the person who added a document changes its number.")
        updated = apply_fields(record, values, custom_ids(unit, context))
        updated = updated.model_copy(update={"updated_by": context.user_id, "updated_at": utc_now()})
        try:
            saved = unit.store.save_document(context, updated, expected_version)
        except StaleDocument as exc:
            raise HTTPException(409, STALE) from exc
        change = None
        if saved.assistant_may_read != record.assistant_may_read:
            unit.store.set_file_vault(context, saved.file_id, "assistant" if saved.assistant_may_read else "hidden")
            change = "index" if saved.assistant_may_read else "forget"
        if saved.access_class == "workspace" and record.access_class == "private":
            pass
        changed = sorted(k for k in values if k != "number") + (["number"] if "number" in values else [])
        log_event(unit, context, document_id, "update", {"fields": ",".join(changed)[:200]})
    return saved, change


def trash(context: TenantContext, document_id: str) -> DocumentRecord:
    with open_vault_unit(context) as unit:
        record = editable(unit, context, document_id)
        if record.status == "deleted":
            return record
        days = unit.store.get_settings(context).trash_days
        now = utc_now()
        updated = record.model_copy(update={"status": "deleted", "deleted_at": now, "deleted_by": context.user_id,
                                            "purge_after": now + timedelta(days=days), "assistant_may_read": False,
                                            "updated_by": context.user_id, "updated_at": now})
        saved = unit.store.save_document(context, updated, record.row_version)
        if record.assistant_may_read:
            unit.store.set_file_vault(context, record.file_id, "hidden")
        log_event(unit, context, document_id, "trash", {"purge_days": days})
    if record.assistant_may_read:
        from ..retrieval_api import forget_file

        forget_file(context, record.file_id)
    return saved


def restore(context: TenantContext, document_id: str) -> DocumentRecord:
    with open_vault_unit(context) as unit:
        record = editable(unit, context, document_id)
        if record.status == "active":
            return record
        now = utc_now()
        updated = record.model_copy(update={"status": "active", "deleted_at": None, "deleted_by": None, "purge_after": None,
                                            "updated_by": context.user_id, "updated_at": now})
        saved = unit.store.save_document(context, updated, record.row_version)
        log_event(unit, context, document_id, "restore")
    return saved


def purge_document(unit: VaultUnit, context: TenantContext, record: DocumentRecord) -> list[str]:
    """Delete the document, its children and its file rows; returns the object keys to delete after the commit."""
    keys = unit.files.storage_keys(context, record.file_id)
    unit.store.remove_document(context, record.id)
    unit.files.delete(context, record.file_id)
    unit.store.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action="document.purged", target=f"vault_document:{record.id}", outcome="success", correlation_id=new_id("correlation"),
        created_at=utc_now(), metadata={"file_id": record.file_id},
    ))
    return keys


def delete_objects(context: TenantContext, file_id: str, keys: list[str]) -> None:
    for key in keys:
        try:
            file_store.storage.delete(key)
        except Exception as exc:  # noqa: BLE001 - the rows are gone, so the bytes are unreachable either way.
            logger.warning("vault_object_delete_failed error_class=%s", type(exc).__name__)
    from ..retrieval_api import forget_file

    forget_file(context, file_id)


def delete_forever(context: TenantContext, document_id: str) -> None:
    with open_vault_unit(context) as unit:
        record = editable(unit, context, document_id)
        if record.status != "deleted":
            raise HTTPException(409, "Move the document to the trash first; it is deleted for good from there.")
        keys = purge_document(unit, context, record)
    delete_objects(context, record.file_id, keys)


# --------------------------------------------------------------------------- versions


def versions(context: TenantContext, document_id: str, limit: int, offset: int) -> tuple[DocumentRecord, FileRecord, list[Any], int]:
    with open_vault_unit(context) as unit:
        record = get_visible(unit, context, document_id)
        found = file_of(unit, context, record)
        items, total = unit.files.list_versions(context, found.id, limit, offset)
    return record, found, items, total


def add_version(context: TenantContext, document_id: str, content: bytes, sha256: str, content_type: str, scanned: bool) -> DocumentRecord:
    quota = settings.files_workspace_quota_bytes
    with open_vault_unit(context) as unit:
        record = editable(unit, context, document_id)
        if record.status != "active":
            raise HTTPException(409, "Restore the document from the trash before adding a version.")
        found = file_of(unit, context, record)
        if found.sha256 == sha256:
            raise HTTPException(409, "This is the same content as the current version, so nothing changed.")
        usage = unit.files.usage(context)
        added = unit.files.added_bytes(context, found.id, sha256, len(content))
        if usage.used_bytes + added > quota:
            raise quota_error(usage.used_bytes, len(content))
        key = workspace_object_key(context.tenant_id, context.workspace_id, "files", found.id, sha256)
        now = utc_now()
        new = FileContent(content_type=content_type, size_bytes=len(content), sha256=sha256, storage_key=key, created_by=context.user_id,
                          created_at=now, scan_status="clean" if scanned else None, scanned_at=now if scanned else None)
        if added:
            file_store.storage.put(key, content, content_type)
        try:
            unit.files.replace_content(context, found.id, found.sha256, new, quota)
        except StorageQuotaExceeded as exc:
            if added:
                file_store.storage.delete(key)
            raise quota_error(exc.usage.used_bytes, len(content)) from exc
        except FileContentChanged as exc:
            if added:
                file_store.storage.delete(key)
            raise HTTPException(409, STALE) from exc
        saved = unit.store.save_document(context, record.model_copy(update={"content_sha256": sha256, "updated_by": context.user_id,
                                                                            "updated_at": now}), record.row_version)
        log_event(unit, context, document_id, "version_added", {"version": found.version + 1})
    return saved


def restore_version(context: TenantContext, document_id: str, version_id: str) -> DocumentRecord:
    with open_vault_unit(context) as unit:
        record = editable(unit, context, document_id)
        found = file_of(unit, context, record)
        version = unit.files.get_version(context, found.id, version_id)
        if version is None:
            raise HTTPException(404, "Version not found: it may have been deleted. Reload the versions.")
        if version.scan_status == "infected":
            raise HTTPException(409, "That version is quarantined: the malware scanner found something in it.")
        if version.sha256 == found.sha256:
            raise HTTPException(409, "This is the same content as the current version, so nothing changed.")
        new = FileContent(content_type=version.content_type, size_bytes=version.size_bytes, sha256=version.sha256, storage_key=version.storage_key,
                          created_by=context.user_id, created_at=utc_now(), scan_status=version.scan_status,
                          scan_signature=version.scan_signature, scanned_at=version.scanned_at)
        try:
            unit.files.replace_content(context, found.id, found.sha256, new, settings.files_workspace_quota_bytes, version.id)
        except (FileContentChanged, StorageQuotaExceeded) as exc:
            raise HTTPException(409, STALE) from exc
        saved = unit.store.save_document(context, record.model_copy(update={"content_sha256": version.sha256, "updated_by": context.user_id,
                                                                            "updated_at": utc_now()}), record.row_version)
        log_event(unit, context, document_id, "version_restored", {"version": version.version})
    return saved


def delete_version(context: TenantContext, document_id: str, version_id: str) -> None:
    with open_vault_unit(context) as unit:
        record = editable(unit, context, document_id)
        found = file_of(unit, context, record)
        version = unit.files.delete_version(context, found.id, version_id)
        if version is None:
            raise HTTPException(404, "Version not found: it may have been deleted. Reload the versions.")
        in_use = unit.files.key_in_use(context, found.id, version.storage_key)
        log_event(unit, context, document_id, "version_deleted", {"version": version.version})
    if not in_use:
        try:
            file_store.storage.delete(version.storage_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vault_object_delete_failed error_class=%s", type(exc).__name__)


# --------------------------------------------------------------------------- sharing and links


def share(context: TenantContext, document_id: str, user_id: str, members: set[str]) -> list[AccessGrant]:
    with open_vault_unit(context) as unit:
        record = get_visible(unit, context, document_id, for_update=True)
        if record.owner_user_id != context.user_id:
            raise HTTPException(403, SHARE_DENIED)
        if record.access_class != "private":
            raise HTTPException(409, "Everyone in the workspace who may read documents already sees this one. Make it private to share it with chosen people.")
        if user_id == context.user_id:
            raise HTTPException(422, "You already see your own document.")
        if user_id not in members:
            raise HTTPException(422, "Share with an active member of this workspace who may read documents.")
        unit.store.grant(context, AccessGrant(document_id=document_id, user_id=user_id, granted_by=context.user_id, granted_at=utc_now()))
        log_event(unit, context, document_id, "shared", {"with": user_id})
        return unit.store.access(context, document_id)


def unshare(context: TenantContext, document_id: str, user_id: str) -> list[AccessGrant]:
    with open_vault_unit(context) as unit:
        record = get_visible(unit, context, document_id, for_update=True)
        if record.owner_user_id != context.user_id:
            raise HTTPException(403, SHARE_DENIED)
        if not unit.store.revoke(context, document_id, user_id):
            raise HTTPException(404, "That person does not have access to this document.")
        log_event(unit, context, document_id, "unshared", {"with": user_id})
        return unit.store.access(context, document_id)


def add_link(context: TenantContext, document_id: str, target_type: str, target_id: str, label: str) -> LinkRecord:
    from .links import finance_links

    target = safe_value(target_id, 160)
    if not target:
        raise HTTPException(422, "Name what the document belongs to.")
    status = "confirmed"
    if target_type == "finance_transaction":
        # The finance ledger owns transaction links (PR "finance_links"); until it is connected this answers 409.
        status = finance_links().propose(context, document_id, target)
    with open_vault_unit(context) as unit:
        editable(unit, context, document_id)
        record = unit.store.add_link(context, LinkRecord(id=new_id("vlnk"), document_id=document_id, target_type=target_type,  # type: ignore[arg-type]
                                                         target_id=target, label=safe_value(label, 200), status=status,  # type: ignore[arg-type]
                                                         created_by=context.user_id, created_at=utc_now()))
        log_event(unit, context, document_id, "linked", {"target_type": target_type})
    return record


def remove_link(context: TenantContext, document_id: str, link_id: str) -> None:
    with open_vault_unit(context) as unit:
        editable(unit, context, document_id)
        if not unit.store.delete_link(context, document_id, link_id):
            raise HTTPException(404, "Link not found.")
        log_event(unit, context, document_id, "unlinked")


# --------------------------------------------------------------------------- extraction


def queue_extraction(context: TenantContext, document_id: str, *, check_edit: bool = True) -> tuple[ExtractionRecord, FileRecord] | None:
    """A queued extraction for the document's current bytes (None when the category has no reader)."""
    with open_vault_unit(context) as unit:
        record = editable(unit, context, document_id) if check_edit else get_visible(unit, context, document_id)
        kind = extraction_kind(record.category)
        if kind is None:
            raise HTTPException(422, "Scanning reads receipts, invoices and the dates of identity, insurance and registration papers only.")
        vault_settings = unit.store.get_settings(context)
        if kind == "dates" and not vault_settings.ocr_identity_dates:
            raise HTTPException(409, "Reading dates from identity and insurance papers is off. A workspace owner turns it on in the vault's settings.")
        found = file_of(unit, context, record)
        if found.scan_status == "infected":
            raise HTTPException(409, "This file is quarantined: the malware scanner found something in it.")
        now = utc_now()
        extraction = ExtractionRecord(id=new_id("vext"), document_id=document_id, file_sha256=found.sha256, kind=kind, status="queued",
                                      requested_by=context.user_id, created_at=now, updated_at=now)
        unit.store.add_extraction(context, extraction)
        log_event(unit, context, document_id, "extraction_requested", {"kind": kind})
    return extraction, found


def finish_extraction(context: TenantContext, extraction: ExtractionRecord, *, fields: dict[str, Any] | None = None, items: list[dict[str, Any]] | None = None,
                      warnings: list[str] | None = None, engine: str = "", languages: str = "", error: str | None = None) -> ExtractionRecord:
    with open_vault_unit(context) as unit:
        current = unit.store.extraction(context, extraction.id)
        if current is None:
            return extraction
        done = current.model_copy(update={
            "status": "failed" if error else "pending", "fields": fields or {}, "items": items or [], "warnings": warnings or [],
            "engine": engine, "languages": languages, "error": error, "updated_at": utc_now(),
        })
        return unit.store.save_extraction(context, done)


def review_queue(context: TenantContext, limit: int = 100) -> list[tuple[ExtractionRecord, DocumentRecord]]:
    with open_vault_unit(context) as unit:
        rows = unit.store.open_extractions(context, limit)
        out = []
        for row in rows:
            record = unit.store.get_document(context, row.document_id)
            if record is not None:
                out.append((row, record))
        return out


def _money(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise HTTPException(422, f"The {field} must be a number such as 150.00.") from exc
    if amount < 0 or amount >= Decimal(100000000) or amount.as_tuple().exponent < -3:  # type: ignore[operator]
        raise HTTPException(422, f"The {field} must be between 0 and 99,999,999.999.")
    return str(amount)


def _date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(422, f"The {field} must be a date (YYYY-MM-DD).") from exc


def decide_extraction(context: TenantContext, extraction_id: str, accept: bool, values: dict[str, Any], items: list[dict[str, Any]] | None) -> tuple[ExtractionRecord, DocumentRecord]:
    """Confirm (with the person's corrections) or reject proposed values. Confirming fills the document; nothing is posted anywhere."""
    with open_vault_unit(context) as unit:
        extraction = unit.store.extraction(context, extraction_id)
        if extraction is None:
            raise HTTPException(404, "Nothing to review: the scan result is gone or belongs to a document you cannot see.")
        record = editable(unit, context, extraction.document_id)
        if extraction.status != "pending":
            raise HTTPException(409, "This scan result was already decided, or it is still being read.")
        now = utc_now()
        updated = record
        if accept:
            if extraction.kind == "receipt":
                currency = (str(values.get("currency") or "").strip().upper() or None)
                if currency is not None and currency not in CURRENCY_CODES:
                    raise HTTPException(422, "Choose the currency from the list.")
                receipt_date = _date(values.get("date"), "date")
                clean_items = []
                for item in (items or [])[:100]:
                    description = safe_value(str(item.get("description") or ""), 80)
                    amount = _money(item.get("amount"), "item amount")
                    if description and amount is not None:
                        clean_items.append({"description": description, "quantity": safe_value(str(item.get("quantity") or "1"), 10), "amount": amount})
                receipt = {
                    "merchant": safe_value(str(values.get("merchant") or ""), 120) or None,
                    "date": receipt_date.isoformat() if receipt_date else None,
                    "total": _money(values.get("total"), "total"),
                    "currency": currency,
                    "tax": _money(values.get("tax"), "tax"),
                    "reference": safe_value(str(values.get("reference") or ""), 40) or None,
                    "items": clean_items,
                    "confirmed_by": context.user_id,
                    "confirmed_at": now.isoformat(),
                    "extraction_id": extraction.id,
                }
                if receipt["total"] is not None and receipt["currency"] is None:
                    raise HTTPException(422, "Choose the currency of the total.")
                updated = record.model_copy(update={"receipt": receipt, "issue_date": record.issue_date or receipt_date})
            else:
                issue = _date(values.get("issue_date"), "issue date")
                expiry = _date(values.get("expiry_date"), "expiry date")
                if issue and expiry and issue > expiry:
                    raise HTTPException(422, "The issue date must be on or before the expiry date.")
                changes: dict[str, Any] = {}
                if issue is not None:
                    changes["issue_date"] = issue
                if expiry is not None:
                    changes["expiry_date"] = expiry
                    if expiry != record.expiry_date:
                        changes["reminders_sent"] = []
                updated = record.model_copy(update=changes)
            updated = updated.model_copy(update={"updated_by": context.user_id, "updated_at": now})
            if updated.issue_date and updated.expiry_date and updated.issue_date > updated.expiry_date:
                raise HTTPException(422, "The issue date must be on or before the expiry date.")
            try:
                updated = unit.store.save_document(context, updated, record.row_version)
            except StaleDocument as exc:
                raise HTTPException(409, STALE) from exc
        decided = unit.store.save_extraction(context, extraction.model_copy(update={
            "status": "confirmed" if accept else "rejected", "decided_by": context.user_id, "decided_at": now, "updated_at": now}))
        log_event(unit, context, record.id, "extraction_confirmed" if accept else "extraction_rejected", {"kind": extraction.kind})
    return decided, updated


__all__ = [
    "DocumentFields", "NewDocument", "add_category", "add_link", "add_version", "check_duplicate", "create_document", "decide_extraction",
    "delete_category", "delete_forever", "delete_version", "detail", "finish_extraction", "link_for", "list_documents", "queue_extraction",
    "read_bytes", "read_settings", "remove_link", "restore", "restore_version", "reveal_number", "review_queue", "save_settings", "serve_token",
    "share", "trash", "unshare", "update_document", "versions",
]
