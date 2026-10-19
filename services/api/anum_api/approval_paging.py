"""Paged approvals: filter, search and keyset cursor (docs/approvals-and-risk.md#paging-filters-and-search).

Order is pending first, then newest first: ``(group, created_at desc, id desc)`` where
``group`` is 0 for an approval that is pending now (stored ``pending`` and not past
``expires_at``) and 1 for everything else. A cursor names the last row of a page as
``group|created_at|id``; the next page starts strictly after it. The memory repository and
the PostgreSQL repository share these helpers so both page identically.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .schemas import Approval, ApprovalStatus

APPROVAL_PAGE_DEFAULT_LIMIT = 25
APPROVAL_PAGE_MAX_LIMIT = 100
APPROVAL_QUERY_MAX_CHARS = 200

ApprovalCursor = tuple[int, datetime, str]


class ApprovalFilter(StrEnum):
    PENDING = "pending"
    DECIDED = "decided"
    ALL = "all"


class ApprovalPage(BaseModel):
    """One page of approvals; pass ``next_cursor`` back as ``cursor`` for the next one."""

    items: list[Approval]
    next_cursor: str | None = Field(default=None, description="Pass as ``cursor`` for the next page")


class ApprovalSummary(BaseModel):
    """How many of the workspace's approvals are in each state now (expiry applied)."""

    pending: int = 0
    approved: int = 0
    rejected: int = 0
    expired: int = 0


def is_pending_now(approval: Approval, now: datetime) -> bool:
    return approval.status == ApprovalStatus.PENDING and (
        approval.expires_at is None or approval.expires_at > now
    )


def group_of(approval: Approval, now: datetime) -> int:
    return 0 if is_pending_now(approval, now) else 1


def encode_cursor(group: int, created_at: datetime, approval_id: str) -> str:
    raw = f"{group}|{created_at.isoformat()}|{approval_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> ApprovalCursor:
    """The (group, created_at, id) a cursor names; ``ValueError`` for anything this API did not issue."""
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        group_text, stamp, approval_id = raw.split("|", 2)
        group = int(group_text)
        when = datetime.fromisoformat(stamp)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid cursor") from exc
    if group not in (0, 1) or when.tzinfo is None or not approval_id or len(approval_id) > 80:
        raise ValueError("Invalid cursor")
    return group, when, approval_id


def normalize_query(query: str | None) -> str | None:
    text = (query or "").strip()
    return text[:APPROVAL_QUERY_MAX_CHARS] if text else None


def matches(approval: Approval, query: str, task_title: str | None) -> bool:
    needle = query.casefold()
    fields = (approval.id, approval.action, approval.reason, approval.target or "", task_title or "")
    return any(needle in field.casefold() for field in fields)


def page_in_memory(
    approvals: Iterable[Approval],
    *,
    now: datetime,
    status: ApprovalFilter,
    query: str | None,
    task_id: str | None,
    cursor: ApprovalCursor | None,
    limit: int,
    task_title: Callable[[str], str | None],
) -> tuple[list[Approval], bool]:
    """The memory repository's page: the same order and filters as the SQL query."""
    rows = []
    for approval in approvals:
        group = group_of(approval, now)
        if status == ApprovalFilter.PENDING and group != 0:
            continue
        if status == ApprovalFilter.DECIDED and group != 1:
            continue
        if task_id is not None and approval.task_id != task_id:
            continue
        if query is not None and not matches(approval, query, task_title(approval.task_id)):
            continue
        rows.append(approval)
    # Newest first within a group, pending group first.
    rows.sort(key=lambda a: (a.created_at, a.id), reverse=True)
    rows.sort(key=lambda a: group_of(a, now))
    if cursor is not None:
        rows = [a for a in rows if _after(a, now, cursor)]
    return rows[:limit], len(rows) > limit


def _after(approval: Approval, now: datetime, cursor: ApprovalCursor) -> bool:
    group, created_at, approval_id = cursor
    mine = group_of(approval, now)
    if mine != group:
        return mine > group
    return (approval.created_at, approval.id) < (created_at, approval_id)


def summary_in_memory(approvals: Iterable[Approval], now: datetime) -> ApprovalSummary:
    counts = {state: 0 for state in ("pending", "approved", "rejected", "expired")}
    for approval in approvals:
        if is_pending_now(approval, now):
            counts["pending"] += 1
        elif approval.status == ApprovalStatus.PENDING:
            counts["expired"] += 1
        else:
            counts[approval.status.value] += 1
    return ApprovalSummary(**counts)
