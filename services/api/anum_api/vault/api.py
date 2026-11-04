"""HTTP routes of the document vault (``/api/v1/documents``, docs/documents.md#api).

Role permissions first (``document:read`` / ``document:write`` / ``document:manage``; viewers have
none), then the document's own rules in ``vault/service.py``. Bytes are read with the Files upload
checks (size, type and content signature, the malware scan, the plan and the workspace quota). All
database work runs in worker threads; the OCR call is awaited with no transaction open.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from .. import file_policy
from ..authorization import Permission
from ..dependencies import has_permission, tenant_context
from ..files import check_upload_content, read_upload_body, resolve_upload_type, valid_file_name
from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from . import service
from .devices import SCOPE, DeviceRefused, check_device, mint, parse_token, upload_cap
from .models import (
    BUILT_IN_CATEGORIES,
    IDENTITY_CATEGORIES,
    LINK_TYPES,
    AccessEvent,
    AccessGrant,
    DeviceRecord,
    DocumentQuery,
    DocumentRecord,
    ExtractionRecord,
    LinkRecord,
    VaultSettings,
    extraction_kind,
    masked_number,
)
from .ocr_client import OcrUnavailable, ocr_connected, read_text
from .ocr_parse import read_dates, read_receipt
from .store import Summary, open_vault_unit

logger = logging.getLogger("anum_api.vault")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

READ_DENIED = "The document vault needs a member or owner role in this workspace. Viewers do not see documents. Ask a workspace owner."
WRITE_DENIED = "Adding and changing documents needs a member or owner role in this workspace. Ask a workspace owner."
MANAGE_DENIED = "Only a workspace owner changes the vault's settings and categories."


def _require(context: TenantContext, permission: Permission, message: str) -> None:
    if not has_permission(context, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, message)


# --------------------------------------------------------------------------- views


def expiry_state(expiry: date | None, today: date, window: int) -> Literal["none", "valid", "soon", "expired"]:
    if expiry is None:
        return "none"
    if expiry < today:
        return "expired"
    return "soon" if (expiry - today).days <= window else "valid"


class FileSummary(BaseModel):
    name: str
    content_type: str
    size_bytes: int
    version: int
    kind: str | None
    previewable: bool
    scan_status: str | None


class DocumentView(BaseModel):
    id: str
    title: str
    category: str
    access_class: Literal["private", "workspace"]
    status: Literal["active", "deleted"]
    issue_date: date | None
    expiry_date: date | None
    expiry_state: Literal["none", "valid", "soon", "expired"]
    days_left: int | None
    issuer: str | None
    country: str | None
    tags: list[str]
    description: str
    number_masked: str | None = Field(description="The last four characters only; the full number is never listed")
    assistant_may_read: bool
    reminder_days: int | None
    retention_until: date | None
    receipt: dict[str, Any] | None
    source: str
    owner_user_id: str
    is_owner: bool
    can_edit: bool
    row_version: int
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    deleted_at: datetime | None
    purge_after: datetime | None
    file: FileSummary | None = None


class ExtractionView(BaseModel):
    id: str
    document_id: str
    kind: str
    status: str
    engine: str
    languages: str
    fields: dict[str, Any]
    items: list[dict[str, Any]]
    warnings: list[str]
    error: str | None
    requested_by: str
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


class DocumentDetailView(DocumentView):
    shared_with: list[AccessGrant]
    links: list[LinkRecord]
    extractions: list[ExtractionView]
    activity: list[AccessEvent]
    can_share: bool
    can_extract: bool


class DocumentPage(BaseModel):
    items: list[DocumentView]
    next_cursor: str | None


class CategoryView(BaseModel):
    id: str
    label: str | None = Field(description="Set for categories the workspace added; built-in ones are translated by the clients")
    built_in: bool
    reads: Literal["receipt", "dates"] | None
    identity: bool


class VaultMeta(BaseModel):
    summary: dict[str, int]
    categories: list[CategoryView]
    settings: VaultSettings
    ocr_connected: bool
    can_write: bool
    can_manage: bool
    max_upload_bytes: int
    accepted: str
    accepted_extensions: list[str]
    link_types: list[str]
    reminder_window_days: int


def _view(context: TenantContext, record: DocumentRecord, today: date, window: int, found: Any = None) -> DocumentView:
    file_summary = None
    if found is not None:
        kind = file_policy.kind_of(found.content_type, found.name)
        file_summary = FileSummary(name=found.name, content_type=found.content_type, size_bytes=found.size_bytes, version=found.version,
                                   kind=kind.value if kind is not None else None,
                                   previewable=kind in file_policy.PREVIEWABLE_KINDS and found.scan_status != "infected", scan_status=found.scan_status)
    return DocumentView(
        id=record.id, title=record.title, category=record.category, access_class=record.access_class, status=record.status,
        issue_date=record.issue_date, expiry_date=record.expiry_date,
        expiry_state=expiry_state(record.expiry_date, today, record.reminder_days or window),
        days_left=(record.expiry_date - today).days if record.expiry_date else None,
        issuer=record.issuer, country=record.country, tags=record.tags, description=record.description,
        number_masked=masked_number(record.number_last4), assistant_may_read=record.assistant_may_read, reminder_days=record.reminder_days,
        retention_until=record.retention_until, receipt=record.receipt, source=record.source, owner_user_id=record.owner_user_id,
        is_owner=record.owner_user_id == context.user_id, can_edit=service.can_edit(context, record), row_version=record.row_version,
        created_by=record.created_by, created_at=record.created_at, updated_by=record.updated_by, updated_at=record.updated_at,
        deleted_at=record.deleted_at, purge_after=record.purge_after, file=file_summary,
    )


def _extraction_view(record: ExtractionRecord) -> ExtractionView:
    return ExtractionView(**record.model_dump(exclude={"file_sha256", "updated_at"}))


def _window(context: TenantContext) -> int:
    settings_value, _ = service.read_settings(context)
    return settings_value.reminder_days


# --------------------------------------------------------------------------- overview


@router.get("/meta", response_model=VaultMeta)
def vault_meta(context: TenantContext = Depends(tenant_context)) -> VaultMeta:
    """Counts, categories, settings and what this person may do: the vault's first read."""
    _require(context, Permission.DOCUMENT_READ, READ_DENIED)
    with open_vault_unit(context) as unit:
        value = unit.store.get_settings(context)
        custom = unit.store.categories(context)
        summary: Summary = unit.store.summary(context, service.today(), value.reminder_days)
    categories = [CategoryView(id=key, label=None, built_in=True, reads=extraction_kind(key), identity=key in IDENTITY_CATEGORIES)
                  for key in BUILT_IN_CATEGORIES]
    categories += [CategoryView(id=item.id, label=item.label, built_in=False, reads=None, identity=False) for item in custom]
    return VaultMeta(
        summary={"active": summary.active, "private": summary.private, "expiring": summary.expiring, "expired": summary.expired,
                 "review": summary.review, "trash": summary.trash},
        categories=categories, settings=value, ocr_connected=ocr_connected(),
        can_write=has_permission(context, Permission.DOCUMENT_WRITE), can_manage=has_permission(context, Permission.DOCUMENT_MANAGE),
        max_upload_bytes=settings.max_upload_body_bytes, accepted=file_policy.ACCEPTED_SUMMARY,
        accepted_extensions=sorted(ext for allowed in file_policy.ALLOWED_TYPES for ext in allowed.extensions),
        link_types=[kind for kind in LINK_TYPES if kind != "finance_transaction"], reminder_window_days=value.reminder_days,
    )


class SettingsBody(BaseModel):
    reminder_days: int = Field(ge=1, le=365)
    trash_days: int = Field(ge=1, le=365)
    ocr_receipts: bool
    ocr_identity_dates: bool


@router.put("/settings", response_model=VaultSettings)
def put_settings(body: SettingsBody, context: TenantContext = Depends(tenant_context)) -> VaultSettings:
    _require(context, Permission.DOCUMENT_MANAGE, MANAGE_DENIED)
    return service.save_settings(context, VaultSettings(**body.model_dump()))


class CategoryBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)


@router.post("/categories", response_model=CategoryView, status_code=status.HTTP_201_CREATED)
def post_category(body: CategoryBody, context: TenantContext = Depends(tenant_context)) -> CategoryView:
    _require(context, Permission.DOCUMENT_MANAGE, MANAGE_DENIED)
    item = service.add_category(context, body.label)
    return CategoryView(id=item.id, label=item.label, built_in=False, reads=None, identity=False)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_category(category_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.DOCUMENT_MANAGE, MANAGE_DENIED)
    service.delete_category(context, category_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- list, review queue


@router.get("", response_model=DocumentPage)
def list_documents(
    q: str | None = Query(default=None, max_length=120, description="Part of the title, issuer, description, a tag or the last four of the number"),
    category: str | None = Query(default=None, max_length=80),
    access_class: Literal["private", "workspace"] | None = None,
    expiry: Literal["soon", "expired"] | None = Query(default=None, description="soon: within the reminder window; expired: past"),
    tag: str | None = Query(default=None, max_length=40),
    state: Literal["active", "deleted"] = Query(default="active", alias="status"),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    context: TenantContext = Depends(tenant_context),
) -> DocumentPage:
    _require(context, Permission.DOCUMENT_READ, READ_DENIED)
    window = _window(context)
    query = DocumentQuery(
        status=state, search=(q or "").strip() or None, category=category or None, access_class=access_class, tag=(tag or "").strip() or None,
        expiring_within_days=window if expiry == "soon" else None, expired=True if expiry == "expired" else None, limit=limit + 1,
        after=service.decode_cursor(cursor) if cursor else None,
    )
    rows = service.list_documents(context, query)
    more = len(rows) > limit
    rows = rows[:limit]
    today = service.today()
    return DocumentPage(items=[_view(context, row, today, window) for row in rows], next_cursor=service.encode_cursor(rows[-1]) if more and rows else None)


class ReviewItem(BaseModel):
    extraction: ExtractionView
    document: DocumentView


@router.get("/review", response_model=list[ReviewItem])
def review_queue(context: TenantContext = Depends(tenant_context)) -> list[ReviewItem]:
    """Scan results waiting for a person to confirm them (documents the caller can see)."""
    _require(context, Permission.DOCUMENT_READ, READ_DENIED)
    window = _window(context)
    today = service.today()
    return [ReviewItem(extraction=_extraction_view(e), document=_view(context, d, today, window)) for e, d in service.review_queue(context)]


# --------------------------------------------------------------------------- adding


async def _read_new(request: Request, name_param: str | None, x_file_name: str | None, x_content_sha256: str | None) -> tuple[str, Any, bytes, str]:
    raw_name = name_param or x_file_name
    if raw_name is None:
        raise HTTPException(422, "Name the file: send ?name= (or the X-File-Name header for ASCII names).")
    if Path(raw_name).name != raw_name:
        raise HTTPException(422, "Invalid file name: send the name only, without a folder.")
    name = valid_file_name(raw_name)
    allowed = resolve_upload_type(name, request.headers.get("content-type"))
    content = await read_upload_body(request, settings.max_upload_body_bytes)
    digest = hashlib.sha256(content).hexdigest()
    if x_content_sha256 is not None and x_content_sha256.lower() != digest:
        raise HTTPException(422, "Content checksum mismatch: the upload was corrupted in transit. Try again.")
    return name, allowed, content, digest


async def _add(context: TenantContext, background: BackgroundTasks, *, name: str, allowed: Any, content: bytes, digest: str, title: str | None,
               category: str, access_class: str, source: str, allow_duplicate: bool) -> DocumentView:
    if not allow_duplicate:
        await asyncio.to_thread(service.check_duplicate, context, digest)
    # Blocking network I/O (clamd) and the plan read: never on the event loop.
    scanned = await asyncio.to_thread(check_upload_content, name, allowed, content)
    from ..billing.enforce import require_capacity

    await asyncio.to_thread(require_capacity, context, "storage", len(content))
    new = service.NewDocument(
        name=name, content_type=allowed.content_type, content=content, sha256=digest, scanned=scanned,
        title=service.safe_value(title or Path(name).stem, 200) or name, category=category, access_class=access_class, source=source,
        allow_duplicate=allow_duplicate,
    )
    record, found, extract = await asyncio.to_thread(service.create_document, context, new)
    if extract and (found.content_type == "application/pdf" or ocr_connected()):
        queued = await asyncio.to_thread(service.queue_extraction, context, record.id, check_edit=False)
        if queued is not None:
            background.add_task(run_extraction, context, queued[0], content, found.content_type)
    window = await asyncio.to_thread(_window, context)
    return _view(context, record, service.today(), window, found)


@router.post("", response_model=DocumentView, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    background: BackgroundTasks,
    context: TenantContext = Depends(tenant_context),
    name_param: str | None = Query(default=None, alias="name", min_length=1, max_length=255),
    title: str | None = Query(default=None, max_length=200),
    category: str = Query(default="other", max_length=80),
    access_class: Literal["private", "workspace"] = Query(default="private"),
    allow_duplicate: bool = Query(default=False, description="Add it even when the same file is already in the vault"),
    x_file_name: str | None = Header(default=None, min_length=1, max_length=255),
    x_content_sha256: str | None = Header(default=None),
) -> DocumentView:
    """Add a document: the raw file as the body. New documents are private unless ``access_class=workspace``."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    name, allowed, content, digest = await _read_new(request, name_param, x_file_name, x_content_sha256)
    return await _add(context, background, name=name, allowed=allowed, content=content, digest=digest, title=title, category=category,
                      access_class=access_class, source="web", allow_duplicate=allow_duplicate)


# --------------------------------------------------------------------------- iPhone Shortcut


class DeviceView(BaseModel):
    id: str
    name: str
    hint: str
    scopes: list[str]
    status: str
    last_seen_at: datetime | None
    last_error: str | None
    upload_count: int
    created_at: datetime


class DeviceCreated(BaseModel):
    device: DeviceView
    token: str = Field(description="Shown once: paste it into the Shortcut. ANUM keeps only its hash.")
    upload_url: str


class DeviceBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _device_view(record: DeviceRecord) -> DeviceView:
    return DeviceView(**record.model_dump(exclude={"token_hash", "owner_user_id", "revoked_at"}))


@router.get("/devices", response_model=list[DeviceView])
def list_devices(context: TenantContext = Depends(tenant_context)) -> list[DeviceView]:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    with open_vault_unit(context) as unit:
        return [_device_view(item) for item in unit.store.devices(context, context.user_id)]


@router.post("/devices", response_model=DeviceCreated, status_code=status.HTTP_201_CREATED)
def create_device(body: DeviceBody, context: TenantContext = Depends(tenant_context)) -> DeviceCreated:
    """A token for the iPhone Shortcut, with the ``documents`` scope only. Shown once."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    device_id = "vdev_" + new_id("x").split("_", 1)[1]
    token, digest, hint = mint(context, device_id)
    record = DeviceRecord(id=device_id, owner_user_id=context.user_id, name=service.safe_value(body.name, 80) or "iPhone", token_hash=digest,
                          hint=hint, scopes=[SCOPE], created_at=utc_now())
    with open_vault_unit(context) as unit:
        if sum(1 for d in unit.store.devices(context, context.user_id) if d.status == "active") >= 10:
            raise HTTPException(409, "You have 10 connected devices. Disconnect one you no longer use first.")
        unit.store.save_device(context, record)
    return DeviceCreated(device=_device_view(record), token=token, upload_url=f"{router.prefix}/device/upload")


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(device_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    with open_vault_unit(context) as unit:
        record = unit.store.device(context, device_id)
        if record is None or record.owner_user_id != context.user_id:
            raise HTTPException(404, "Device not found.")
        if record.status != "revoked":
            unit.store.save_device(context, record.model_copy(update={"status": "revoked", "revoked_at": utc_now()}))
    return Response(status_code=204)


def _device_context(authorization: str | None) -> tuple[TenantContext, DeviceRecord]:
    from ..dependencies import lookup_membership, workspace_archived

    if not authorization or not authorization.lower().startswith("bearer "):
        raise DeviceRefused(401, "Send the device token as Authorization: Bearer <token>.", "missing_token")
    parsed = parse_token(authorization.split(" ", 1)[1])
    if parsed is None:
        raise DeviceRefused(401, "This device token is not valid.", "invalid_token")
    context, device_id, secret = parsed
    membership = lookup_membership(context)
    if membership is None and settings.auth_mode == "oidc" or membership is not None and not membership.active:
        raise DeviceRefused(401, "The person this device belongs to is no longer a member of the workspace.", "not_member")
    context = context.model_copy(update={"roles": [membership.role.lower()] if membership is not None else ["member"]})
    with open_vault_unit(context) as unit:
        device = check_device(unit.store.device(context, device_id), secret)
    if device.owner_user_id != context.user_id:
        raise DeviceRefused(401, "This device token is not valid.", "invalid_token")
    if not has_permission(context, Permission.DOCUMENT_WRITE):
        raise DeviceRefused(403, "The person this device belongs to can no longer add documents (viewer role).", "not_allowed")
    if workspace_archived(context):
        raise DeviceRefused(409, "This workspace is archived, so it accepts no new documents.", "archived")
    return context, device


def _device_seen(context: TenantContext, device_id: str, *, error: str | None, uploaded: bool) -> None:
    with open_vault_unit(context) as unit:
        record = unit.store.device(context, device_id)
        if record is not None:
            unit.store.save_device(context, record.model_copy(update={
                "last_seen_at": utc_now(), "last_error": error, "upload_count": record.upload_count + (1 if uploaded else 0)}))


@router.post("/device/upload", response_model=DocumentView, status_code=status.HTTP_201_CREATED)
async def device_upload(
    request: Request,
    background: BackgroundTasks,
    authorization: str | None = Header(default=None),
    name_param: str | None = Query(default=None, alias="name", min_length=1, max_length=255),
    title: str | None = Query(default=None, max_length=200),
    category: str = Query(default="receipt", max_length=80),
    x_file_name: str | None = Header(default=None, min_length=1, max_length=255),
) -> DocumentView:
    """The iPhone Shortcut's upload ("Scan a document", Share Sheet, Files): device token, ``documents`` scope.
    The document is private to the token's owner; a receipt is read when the owner turned scanning on."""
    try:
        context, device = await asyncio.to_thread(_device_context, authorization)
    except DeviceRefused as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(exc.status_code, exc.message, headers=headers) from exc
    wait = upload_cap.take(f"{context.tenant_id}:{context.workspace_id}:{device.id}", settings.vault_device_uploads_per_hour)
    if wait is not None:
        raise HTTPException(429, "This device sent too many documents in the last hour. Try again later.", headers={"Retry-After": str(wait)})
    try:
        name, allowed, content, digest = await _read_new(request, name_param, x_file_name, None)
        view = await _add(context, background, name=name, allowed=allowed, content=content, digest=digest, title=title, category=category,
                          access_class="private", source="device", allow_duplicate=False)
    except HTTPException as exc:
        await asyncio.to_thread(_device_seen, context, device.id, error=f"{exc.status_code}", uploaded=False)
        raise
    except Exception as exc:
        await asyncio.to_thread(_device_seen, context, device.id, error=type(exc).__name__[:80], uploaded=False)
        raise
    await asyncio.to_thread(_device_seen, context, device.id, error=None, uploaded=True)
    return view


# --------------------------------------------------------------------------- signed links (before /{id})


@router.get("/links/{token}")
def serve_link(token: str) -> Response:
    """The bytes behind a short-lived link (the signed link is the credential, as for Files)."""
    found, content, inline = service.serve_token(token)
    kind = file_policy.kind_of(found.content_type, found.name)
    inline = inline and kind in file_policy.PREVIEWABLE_KINDS
    media = found.content_type if inline else "application/octet-stream"
    if inline and kind in file_policy.TEXT_PREVIEW_KINDS:
        media = "text/plain; charset=utf-8"
    return Response(content, media_type=media, headers={
        "Content-Disposition": file_policy.content_disposition("inline" if inline else "attachment", found.name),
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox",
    })


class ExtractionDecision(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] | None = None


@router.post("/extractions/{extraction_id}/confirm", response_model=DocumentView)
def confirm_extraction(extraction_id: str, body: ExtractionDecision, context: TenantContext = Depends(tenant_context)) -> DocumentView:
    """Confirm proposed values (with the person's corrections). They fill the document only: nothing is posted to finance."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    _, record = service.decide_extraction(context, extraction_id, True, body.fields, body.items)
    return _view(context, record, service.today(), _window(context))


@router.post("/extractions/{extraction_id}/reject", response_model=ExtractionView)
def reject_extraction(extraction_id: str, context: TenantContext = Depends(tenant_context)) -> ExtractionView:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    decided, _ = service.decide_extraction(context, extraction_id, False, {}, None)
    return _extraction_view(decided)


# --------------------------------------------------------------------------- one document


def _detail_view(context: TenantContext, detail: service.Detail) -> DocumentDetailView:
    window = _window(context)
    base = _view(context, detail.record, service.today(), window, detail.file)
    return DocumentDetailView(
        **base.model_dump(), shared_with=detail.access, links=detail.links, extractions=[_extraction_view(e) for e in detail.extractions],
        activity=detail.events, can_share=detail.record.owner_user_id == context.user_id and detail.record.access_class == "private",
        can_extract=base.can_edit and extraction_kind(detail.record.category) is not None,
    )


@router.get("/{document_id}", response_model=DocumentDetailView)
def get_document(document_id: str, context: TenantContext = Depends(tenant_context)) -> DocumentDetailView:
    _require(context, Permission.DOCUMENT_READ, READ_DENIED)
    return _detail_view(context, service.detail(context, document_id))


class DocumentPatch(BaseModel):
    row_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=80)
    access_class: Literal["private", "workspace"] | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    issuer: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=2)
    tags: list[str] | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=2000)
    number: str | None = Field(default=None, max_length=60, description="Identity or account number; stored encrypted, listed as the last four only. Empty clears it.")
    reminder_days: int | None = Field(default=None, ge=1, le=365)
    retention_until: date | None = None
    assistant_may_read: bool | None = None


@router.patch("/{document_id}", response_model=DocumentView)
async def patch_document(document_id: str, body: DocumentPatch, context: TenantContext = Depends(tenant_context)) -> DocumentView:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    values = body.model_dump(exclude_unset=True)
    values.pop("row_version", None)
    if not values:
        raise HTTPException(422, "Nothing to change.")
    record, change = await asyncio.to_thread(service.update_document, context, document_id, body.row_version, values)
    if change == "index":
        # Opt-in only (owner decision): the assistant reads this document's text from now on.
        await _index_for_assistant(context, record)
    elif change == "forget":
        from ..retrieval_api import forget_file

        await asyncio.to_thread(forget_file, context, record.file_id)
    window = await asyncio.to_thread(_window, context)
    return _view(context, record, service.today(), window)


async def _index_for_assistant(context: TenantContext, record: DocumentRecord) -> None:
    from ..files import file_store, open_file_metadata_store, vault_files_visible
    from ..retrieval_api import index_uploaded_file

    def load() -> tuple[Any, bytes]:
        with open_file_metadata_store(context) as store:
            found = store.get(context, record.file_id)
        if found is None:
            raise HTTPException(404, service.NOT_FOUND)
        return found, file_store.storage.get(found.storage_key)

    with vault_files_visible():
        found, content = await asyncio.to_thread(load)
        await index_uploaded_file(context, found, content)


class NumberView(BaseModel):
    number: str


@router.post("/{document_id}/number", response_model=NumberView)
def reveal_number(document_id: str, context: TenantContext = Depends(tenant_context)) -> NumberView:
    """The full number, for the document's owner only; every reveal is logged."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    with open_vault_unit(context) as unit:
        record = service.get_visible(unit, context, document_id)
        if record.owner_user_id != context.user_id:
            raise HTTPException(403, "Only the person who added this document sees its full number.")
        number = service.reveal_number(record)
        if number is None:
            raise HTTPException(404, "This document has no number.")
        service.log_event(unit, context, document_id, "number_revealed")
    return NumberView(number=number)


@router.get("/{document_id}/content")
def download(document_id: str, version_id: str | None = Query(default=None, max_length=80),
             context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.DOCUMENT_READ, READ_DENIED)
    _, found, content, number = service.read_bytes(context, document_id, "download", version_id)
    name = found.name if version_id is None else f"{Path(found.name).stem} (version {number}){Path(found.name).suffix}"
    return Response(content, media_type="application/octet-stream", headers={
        "Content-Disposition": file_policy.content_disposition("attachment", name), "Cache-Control": "private, no-store"})


class LinkRequest(BaseModel):
    disposition: Literal["attachment", "inline"] = "attachment"


class SignedLink(BaseModel):
    url: str
    expires_at: datetime


@router.post("/{document_id}/link", response_model=SignedLink, status_code=status.HTTP_201_CREATED)
def create_link(document_id: str, body: LinkRequest | None = None, context: TenantContext = Depends(tenant_context)) -> SignedLink:
    _require(context, Permission.DOCUMENT_READ, READ_DENIED)
    token, expires_at = service.link_for(context, document_id, (body or LinkRequest()).disposition == "inline")
    return SignedLink(url=f"{router.prefix}/links/{token}", expires_at=expires_at)


@router.delete("/{document_id}", response_model=DocumentView)
def delete_document(document_id: str, permanent: bool = Query(default=False), context: TenantContext = Depends(tenant_context)) -> Response | DocumentView:
    """Move to the trash (restorable for the workspace's trash days), or, from the trash, delete for good."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    if permanent:
        service.delete_forever(context, document_id)
        return Response(status_code=204)
    return _view(context, service.trash(context, document_id), service.today(), _window(context))


@router.post("/{document_id}/restore", response_model=DocumentView)
def restore_document(document_id: str, context: TenantContext = Depends(tenant_context)) -> DocumentView:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    return _view(context, service.restore(context, document_id), service.today(), _window(context))


class VersionView(BaseModel):
    id: str
    version: int
    content_type: str
    size_bytes: int
    created_by: str
    created_at: datetime
    replaced_by: str
    replaced_at: datetime
    downloadable: bool


class VersionPage(BaseModel):
    current_version: int
    current_size_bytes: int
    items: list[VersionView]
    total: int


@router.get("/{document_id}/versions", response_model=VersionPage)
def list_versions(document_id: str, limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100_000),
                  context: TenantContext = Depends(tenant_context)) -> VersionPage:
    _require(context, Permission.DOCUMENT_READ, READ_DENIED)
    _, found, items, total = service.versions(context, document_id, limit, offset)
    return VersionPage(current_version=found.version, current_size_bytes=found.size_bytes, total=total, items=[
        VersionView(id=v.id, version=v.version, content_type=v.content_type, size_bytes=v.size_bytes, created_by=v.created_by,
                    created_at=v.created_at, replaced_by=v.replaced_by, replaced_at=v.replaced_at, downloadable=v.scan_status != "infected")
        for v in items])


@router.post("/{document_id}/versions", response_model=DocumentView, status_code=status.HTTP_201_CREATED)
async def upload_version(document_id: str, request: Request, context: TenantContext = Depends(tenant_context)) -> DocumentView:
    """A renewed passport, a corrected contract: the new file becomes current, the old one an older version."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)

    def current_name() -> str:
        with open_vault_unit(context) as unit:
            record = service.editable(unit, context, document_id)
            return service.file_of(unit, context, record).name

    name = await asyncio.to_thread(current_name)
    allowed = resolve_upload_type(name, request.headers.get("content-type"))
    content = await read_upload_body(request, settings.max_upload_body_bytes)
    digest = hashlib.sha256(content).hexdigest()
    scanned = await asyncio.to_thread(check_upload_content, name, allowed, content)
    record = await asyncio.to_thread(service.add_version, context, document_id, content, digest, allowed.content_type, scanned)
    return _view(context, record, service.today(), await asyncio.to_thread(_window, context))


@router.post("/{document_id}/versions/{version_id}/restore", response_model=DocumentView)
def restore_version(document_id: str, version_id: str, context: TenantContext = Depends(tenant_context)) -> DocumentView:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    return _view(context, service.restore_version(context, document_id, version_id), service.today(), _window(context))


@router.delete("/{document_id}/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_version(document_id: str, version_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    service.delete_version(context, document_id, version_id)
    return Response(status_code=204)


class ShareBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=160)


def _readers(context: TenantContext) -> set[str]:
    from ..authorization import ROLE_PERMISSIONS
    from ..team_work import open_team_unit

    with open_team_unit(context) as unit:
        members = unit.repository.list_memberships(context)
    return {m.user_id for m in members if m.active and Permission.DOCUMENT_READ in ROLE_PERMISSIONS.get(m.role, frozenset())}


@router.post("/{document_id}/access", response_model=list[AccessGrant], status_code=status.HTTP_201_CREATED)
def share_document(document_id: str, body: ShareBody, context: TenantContext = Depends(tenant_context)) -> list[AccessGrant]:
    """Let one more member see a private document (read only)."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    return service.share(context, document_id, body.user_id.strip(), _readers(context))


@router.delete("/{document_id}/access/{user_id}", response_model=list[AccessGrant])
def unshare_document(document_id: str, user_id: str, context: TenantContext = Depends(tenant_context)) -> list[AccessGrant]:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    return service.unshare(context, document_id, user_id)


class LinkBody(BaseModel):
    target_type: Literal["vehicle", "person", "business", "purchase", "finance_transaction"]
    target_id: str = Field(min_length=1, max_length=160)
    label: str = Field(default="", max_length=200)


@router.post("/{document_id}/links", response_model=LinkRecord, status_code=status.HTTP_201_CREATED)
def link_document(document_id: str, body: LinkBody, context: TenantContext = Depends(tenant_context)) -> LinkRecord:
    """Say what the document belongs to. A finance transaction link waits for the finance ledger links (409 until then)."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    return service.add_link(context, document_id, body.target_type, body.target_id, body.label)


@router.delete("/{document_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_document(document_id: str, link_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    service.remove_link(context, document_id, link_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- scanning


async def run_extraction(context: TenantContext, extraction: ExtractionRecord, content: bytes, content_type: str) -> ExtractionRecord:
    """Read the text (OCR service or PDF text layer) with no transaction open, then store the proposed values."""
    try:
        text = await read_text(content, content_type)
    except OcrUnavailable as exc:
        return await asyncio.to_thread(service.finish_extraction, context, extraction, error=str(exc)[:300])
    except Exception as exc:  # noqa: BLE001 - an outcome for the person, never a stack trace
        logger.warning("vault_extraction_failed error_class=%s", type(exc).__name__)
        return await asyncio.to_thread(service.finish_extraction, context, extraction, error="Reading the document failed. Try again.")
    parsed = read_receipt(text.lines) if extraction.kind == "receipt" else read_dates(text.lines)
    return await asyncio.to_thread(service.finish_extraction, context, extraction, fields=parsed.fields, items=parsed.items,
                                   warnings=[*text.warnings, *parsed.warnings], engine=text.engine, languages=text.languages)


@router.post("/{document_id}/extract", response_model=ExtractionView, status_code=status.HTTP_201_CREATED)
async def extract(document_id: str, context: TenantContext = Depends(tenant_context)) -> ExtractionView:
    """Read a receipt's fields, or an identity or insurance paper's dates, and wait for the person to confirm them."""
    _require(context, Permission.DOCUMENT_WRITE, WRITE_DENIED)
    queued = await asyncio.to_thread(service.queue_extraction, context, document_id)
    if queued is None:
        raise HTTPException(422, "This document has nothing to read.")
    extraction, found = queued
    from ..files import file_store

    try:
        content = await asyncio.to_thread(file_store.storage.get, found.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "File content not found") from exc
    return _extraction_view(await run_extraction(context, extraction, content, found.content_type))


__all__ = ["router", "run_extraction"]
