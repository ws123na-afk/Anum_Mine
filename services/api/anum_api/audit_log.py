"""The workspace audit log: filtered, keyset-paged reads and a streamed export.

``audit_records`` is append-only (RLS allows select and insert only) and every read here
runs inside the caller's tenant and workspace scope, so a workspace's log never shows
another workspace's records. Owners (``audit:read`` / ``audit:export``) and organization
administrators with a membership in the workspace may read and export it.

See docs/governance-and-scale.md#audit-log.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .audit import AuditCursor, AuditFilter, AuditRecord
from .authorization import Permission
from .dependencies import (
    open_read_only_repository,
    repository_context,
    require_owner_or_org_admin,
    tenant_context,
)
from .repository import AnumRepository
from .schemas import TenantContext, new_id, utc_now

router = APIRouter(prefix="/api/v1", tags=["audit"])

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
EXPORT_BATCH = 500
# Shown with the log and documented: nothing purges audit records.
RETENTION = (
    "Audit records are kept for as long as the workspace exists, including after it is "
    "archived. They are append-only: nobody can edit or delete them, owners included."
)
AUDIT_ACCESS_REQUIRED = (
    "Audit log access requires the owner role in this workspace or the organization "
    "administrator role. Ask a workspace owner to show you the log or change your role."
)
EXPORT_FIELDS = (
    "id",
    "created_at",
    "actor",
    "action",
    "target",
    "outcome",
    "correlation_id",
    "tenant_id",
    "workspace_id",
    "metadata",
)


class AuditRecordView(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    actor: str
    action: str
    target: str
    outcome: str
    correlation_id: str
    created_at: datetime
    metadata: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditRecordView]
    # Pass as ``cursor`` for the next (older) page; null on the last page.
    next_cursor: str | None
    retention: str = RETENTION


class AuditActions(BaseModel):
    actions: list[str]


# --------------------------------------------------------------------------- helpers


def _plain(value: Any) -> Any:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=repr)
    return value


def record_view(record: AuditRecord) -> AuditRecordView:
    return AuditRecordView(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        actor=record.actor,
        action=record.action,
        target=record.target,
        outcome=record.outcome,
        correlation_id=record.correlation_id,
        created_at=record.created_at,
        metadata=_plain(record.metadata),
    )


def encode_cursor(record: AuditRecord) -> str:
    raw = f"{record.created_at.astimezone(timezone.utc).isoformat()}|{record.id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> AuditCursor:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, record_id = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8").split("|", 1)
        created_at = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeError, binascii.Error):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown or invalid cursor") from None
    if created_at.tzinfo is None or not record_id or len(record_id) > 80:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown or invalid cursor")
    return created_at, record_id


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def audit_filter(
    actor: str | None = Query(default=None, max_length=160),
    action: str | None = Query(default=None, max_length=160),
    target: str | None = Query(default=None, max_length=200),
    outcome: str | None = Query(default=None, max_length=40),
    correlation_id: str | None = Query(default=None, max_length=160),
    since: datetime | None = Query(default=None, description="Inclusive; ISO 8601, UTC if no offset"),
    until: datetime | None = Query(default=None, description="Exclusive; ISO 8601, UTC if no offset"),
    q: str | None = Query(default=None, max_length=200, description="Text in actor, action or target"),
) -> AuditFilter:
    result = AuditFilter(
        actor=_blank_to_none(actor),
        action=_blank_to_none(action),
        target=_blank_to_none(target),
        outcome=_blank_to_none(outcome),
        correlation_id=_blank_to_none(correlation_id),
        since=_aware(since),
        until=_aware(until),
        text=_blank_to_none(q),
    )
    if result.since is not None and result.until is not None and result.since >= result.until:
        raise HTTPException(
            status_code=422,
            detail="The start of the date range must be before its end",
        )
    return result


def _require_reader(context: TenantContext, permission: Permission) -> None:
    require_owner_or_org_admin(context, permission, AUDIT_ACCESS_REQUIRED)


# --------------------------------------------------------------------------- routes


@router.get("/audit-records", response_model=AuditPage)
async def list_audit_records(
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=400),
    filters: AuditFilter = Depends(audit_filter),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AuditPage:
    """The workspace's audit records, newest first, one keyset page at a time."""
    _require_reader(context, Permission.AUDIT_READ)
    after = decode_cursor(cursor) if cursor else None
    records = repository.list_audit_page(context, filters, limit=limit + 1, after=after)
    page = records[:limit]
    next_cursor = encode_cursor(page[-1]) if len(records) > limit and page else None
    return AuditPage(items=[record_view(record) for record in page], next_cursor=next_cursor)


@router.get("/audit-records/actions", response_model=AuditActions)
async def list_audit_actions(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AuditActions:
    """The distinct actions recorded in this workspace, for the action filter."""
    _require_reader(context, Permission.AUDIT_READ)
    return AuditActions(actions=repository.list_audit_actions(context))


@router.get("/audit-records/{record_id}", response_model=AuditRecordView)
async def get_audit_record(
    record_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AuditRecordView:
    _require_reader(context, Permission.AUDIT_READ)
    record = repository.get_audit_record(record_id, context) if len(record_id) <= 80 else None
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit record not found")
    return record_view(record)


def _export_row(record: AuditRecord) -> dict[str, Any]:
    view = record_view(record)
    return {
        "id": view.id,
        "created_at": view.created_at.isoformat(),
        "actor": view.actor,
        "action": view.action,
        "target": view.target,
        "outcome": view.outcome,
        "correlation_id": view.correlation_id,
        "tenant_id": view.tenant_id,
        "workspace_id": view.workspace_id,
        "metadata": view.metadata,
    }


def _spreadsheet_safe(value: str) -> str:
    """Keep a spreadsheet from running a cell as a formula (CSV injection)."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _records(context: TenantContext, filters: AuditFilter) -> Iterator[AuditRecord]:
    """Every matching record, newest first, one batch per short read-only transaction."""
    after: AuditCursor | None = None
    while True:
        with open_read_only_repository(context) as reader:
            batch = reader.list_audit_page(context, filters, limit=EXPORT_BATCH, after=after)
        yield from batch
        if len(batch) < EXPORT_BATCH:
            return
        after = (batch[-1].created_at, batch[-1].id)


def _json_body(context: TenantContext, filters: AuditFilter) -> Iterator[str]:
    yield "["
    first = True
    for record in _records(context, filters):
        yield ("" if first else ",") + json.dumps(_export_row(record), sort_keys=True, default=str)
        first = False
    yield "]"


def _csv_body(context: TenantContext, filters: AuditFilter) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_FIELDS)
    for record in _records(context, filters):
        row = _export_row(record)
        row["metadata"] = json.dumps(row["metadata"], sort_keys=True, default=str)
        writer.writerow([_spreadsheet_safe(str(row[field])) for field in EXPORT_FIELDS])
        if buffer.tell() > 64_000:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue()


@router.get("/audit/export")
async def export_audit(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    filters: AuditFilter = Depends(audit_filter),
    context: TenantContext = Depends(tenant_context),
    # Released before the body streams (see stream_events in main.py).
    repository: AnumRepository = Depends(repository_context, scope="function"),
) -> StreamingResponse:
    """Download the filtered audit log as JSON (an array) or CSV, newest first.

    The export is itself audited (``audit.export``, with the filters) before the body is
    streamed. The body reads in batches through its own short transactions, never the
    request's session, so a large log is not held in memory. Records written after the
    request started are not included.
    """
    _require_reader(context, Permission.AUDIT_EXPORT)
    now = utc_now()
    pinned = filters if filters.until is not None and filters.until <= now else AuditFilter(
        actor=filters.actor,
        action=filters.action,
        target=filters.target,
        outcome=filters.outcome,
        correlation_id=filters.correlation_id,
        since=filters.since,
        until=now,
        text=filters.text,
    )
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action="audit.export",
            target="audit_records",
            outcome="success",
            correlation_id=new_id("correlation"),
            created_at=now,
            metadata={"format": format, "filters": filters.as_metadata()},
        )
    )
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    filename = f"audit-{context.workspace_id}-{stamp}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"}
    if format == "json":
        return StreamingResponse(_json_body(context, pinned), media_type="application/json", headers=headers)
    return StreamingResponse(_csv_body(context, pinned), media_type="text/csv; charset=utf-8", headers=headers)
