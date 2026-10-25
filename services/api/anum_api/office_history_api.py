"""F09: evidence-only work history and historical office reconstruction.

This module deliberately reads existing append-only audit records. It does not
invent a historical agent status from a task title or an avatar animation.
Reads are scoped through the existing repository and permission gates.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .audit import AuditFilter, AuditRecord
from .audit_log import _aware, _blank_to_none, decode_cursor, encode_cursor
from .authorization import Permission
from .dependencies import repository_context, require_owner_or_org_admin, tenant_context
from .repository import AnumRepository
from .schemas import TenantContext

router = APIRouter(prefix="/api/v1/office-history", tags=["office history"])
MAX_PAGE = 200
DEFAULT_PAGE = 60
HISTORY_ACCESS = (
    "Work-history replay is restricted to workspace owners and authorized "
    "organization administrators. Select the correct workspace before reading."
)
EVIDENCE_NOTICE = (
    "This view includes recorded workspace audit evidence only. A missing record "
    "does not prove that an activity did not happen; it cannot reconstruct "
    "unrecorded agent presence, exact room occupancy, or past screen positions."
)

# Only categorize actions, never infer a state that was not recorded.
PREFIXES: tuple[tuple[str, str], ...] = (
    ("agent_meeting.", "meeting"),
    ("office.", "office"),
    ("work_item.", "team_work"),
    ("task.", "task"),
    ("agent_run.", "agent_run"),
    ("approval.", "approval"),
    ("workspace.", "workspace"),
    ("workspace_member.", "membership"),
    ("automation.", "automation"),
    ("autonomy.", "autonomy"),
)


class HistoryCategory(StrEnum):
    ALL = "all"
    TASK = "task"
    AGENT_RUN = "agent_run"
    APPROVAL = "approval"
    MEETING = "meeting"
    OFFICE = "office"
    TEAM_WORK = "team_work"
    WORKSPACE = "workspace"
    MEMBERSHIP = "membership"
    AUTOMATION = "automation"
    AUTONOMY = "autonomy"
    OTHER = "other"


def classify_action(action: str) -> HistoryCategory:
    for prefix, category in PREFIXES:
        if action.startswith(prefix):
            return HistoryCategory(category)
    return HistoryCategory.OTHER


def normalized_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Historical timestamps must include a timezone")
    return value.astimezone(timezone.utc)


class HistoryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    timestamp: datetime
    actor: str
    action: str
    target: str
    category: HistoryCategory
    outcome: str
    correlation_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    # The record id and timestamp are source-of-truth references, not replayed activity.
    source: str = "workspace_audit"
    verified: bool = True


class HistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[HistoryEvidence]
    next_cursor: str | None
    workspace_id: str
    category: HistoryCategory
    evidence_notice: str = EVIDENCE_NOTICE
    # A page only establishes the collected records, never completeness of history.
    historical_coverage_complete: bool = False


class HistoricalOfficeState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_at: datetime
    workspace_id: str
    known_events: list[HistoryEvidence]
    action_counts: dict[str, int]
    evidence_limited: bool = True
    historical_room_occupancy: str = "unknown"
    historical_agent_presence: str = "unknown"
    historical_layout: str = "unknown"
    evidence_notice: str = EVIDENCE_NOTICE
    next_cursor: str | None = None


def evidence(record: AuditRecord) -> HistoryEvidence:
    # AuditRecord already sanitized nested secret fields on construction.
    # Force detached plain objects for serialization, preserving original keys.
    from .audit_log import record_view
    item = record_view(record)
    return HistoryEvidence(
        id=item.id,
        timestamp=normalized_time(item.created_at),
        actor=item.actor,
        action=item.action,
        target=item.target,
        category=classify_action(item.action),
        outcome=item.outcome,
        correlation_id=item.correlation_id,
        metadata=item.metadata,
    )


def authorize(context: TenantContext) -> None:
    require_owner_or_org_admin(context, Permission.AUDIT_READ, HISTORY_ACCESS)


def read_page(
    repository: AnumRepository,
    context: TenantContext,
    *,
    limit: int,
    cursor: str | None,
    category: HistoryCategory,
    actor: str | None = None,
    target: str | None = None,
    correlation_id: str | None = None,
    until: datetime | None = None,
    since: datetime | None = None,
    text: str | None = None,
) -> HistoryPage:
    """Page underlying records, not filtered results, so cursors never skip evidence.

    A category-specific page may be empty while next_cursor is present: the
    client must follow the cursor to discover additional matching records.
    This avoids unbounded server-side scanning of sparse audit categories.
    """
    if not 1 <= limit <= MAX_PAGE:
        raise ValueError("History page size out of bounds")
    audit_filter = AuditFilter(
        actor=actor, target=target, correlation_id=correlation_id,
        since=since, until=until, text=text,
    )
    after = decode_cursor(cursor) if cursor else None
    rows = repository.list_audit_page(context, audit_filter, limit=limit + 1, after=after)
    page = rows[:limit]
    matching = [
        evidence(row) for row in page
        if category is HistoryCategory.ALL or classify_action(row.action) is category
    ]
    next_cursor = encode_cursor(page[-1]) if len(rows) > limit and page else None
    return HistoryPage(
        items=matching,
        next_cursor=next_cursor,
        workspace_id=context.workspace_id,
        category=category,
    )


@router.get("", response_model=HistoryPage)
def list_office_history(
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    cursor: str | None = Query(default=None, max_length=400),
    category: HistoryCategory = Query(default=HistoryCategory.ALL),
    actor: str | None = Query(default=None, max_length=160),
    target: str | None = Query(default=None, max_length=200),
    correlation_id: str | None = Query(default=None, max_length=160),
    since: datetime | None = Query(default=None, description="Inclusive; ISO 8601, UTC if no offset"),
    until: datetime | None = Query(default=None, description="Exclusive; ISO 8601, UTC if no offset"),
    q: str | None = Query(default=None, max_length=200, description="Text in actor, action or target"),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> HistoryPage:
    authorize(context)
    since, until = _aware(since), _aware(until)
    if since is not None and until is not None and since >= until:
        raise HTTPException(422, detail="The start of the date range must be before its end")
    return read_page(
        repository, context, limit=limit, cursor=cursor, category=category,
        actor=actor, target=target, correlation_id=correlation_id,
        since=since, until=until, text=_blank_to_none(q),
    )


@router.get("/at", response_model=HistoricalOfficeState)
def office_history_at(
    at: datetime = Query(description="Historical UTC instant, with timezone offset"),
    limit: int = Query(default=100, ge=1, le=MAX_PAGE),
    cursor: str | None = Query(default=None, max_length=400),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> HistoricalOfficeState:
    authorize(context)
    try:
        instant = normalized_time(at)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    if instant > datetime.now(timezone.utc):
        raise HTTPException(422, detail="Cannot replay an instant in the future")
    # Audits are newest first. Exclude the instant itself because AuditFilter.until
    # is exclusive. Avoid presenting a partial audit stream as full scene state.
    page = read_page(
        repository, context, limit=limit, cursor=cursor,
        category=HistoryCategory.ALL, until=instant,
    )
    counts = dict(Counter(row.category.value for row in page.items))
    return HistoricalOfficeState(
        requested_at=instant, workspace_id=context.workspace_id,
        known_events=page.items, action_counts=counts,
        next_cursor=page.next_cursor,
    )


@router.get("/correlations/{correlation_id}", response_model=HistoryPage)
def correlation_history(
    correlation_id: Annotated[str, Path(min_length=1, max_length=160)],
    cursor: str | None = Query(default=None, max_length=400),
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> HistoryPage:
    authorize(context)
    return read_page(
        repository, context, limit=limit, cursor=cursor,
        category=HistoryCategory.ALL, correlation_id=correlation_id,
    )
