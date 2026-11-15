"""The monthly fleet report's saved draft per fleet and month (docs/vehicles.md#monthly-fleet-report).

A client fleet lives in its own business workspace, so "per fleet and month" is one row per workspace and month
(``vehicle_fleet_reports``, migration ``0191_gap_fleet``): the company name and the owner's notes kept between
visits, the AI summary paragraph per language (``fleet_summary.py``) and the report's deliveries. Each delivery keeps
the frozen content it attaches (the figures, the company name, the notes, the paragraph and the date prepared), so
the file rendered when the approved call runs is byte for byte the one whose hash was approved.

With ``ANUM_REPOSITORY_BACKEND=postgresql`` the store is ``db.vehicles_repository.SqlFleetReportStore`` in a session
with the tenant, workspace and user RLS context (the restrictive policies let a person write only rows that name
them as ``updated_by``). The in-memory store is for local development and tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from threading import RLock
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store

MAX_DELIVERIES = 10


class FleetReportDraft(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    month: date  # the first day of the month
    company_name: str | None = None
    notes: str = ""
    # language -> {"text", "fingerprint", "model", "created_at", "created_by"}
    summaries: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # newest last; each {"id", "channel", "to", "format", "language", "status", "approval_id", "task_id", "requested_at",
    # "requested_by", "snapshot": {...}}
    deliveries: list[dict[str, Any]] = Field(default_factory=list)
    version: int = 0
    updated_by: str
    created_at: datetime
    updated_at: datetime


class FleetReportStore(Protocol):
    def get(self, context: TenantContext, month: date) -> FleetReportDraft | None: ...
    def get_by_id(self, context: TenantContext, draft_id: str) -> FleetReportDraft | None: ...
    def latest_before(self, context: TenantContext, month: date) -> FleetReportDraft | None: ...
    def save(self, draft: FleetReportDraft, expected_version: int) -> bool: ...


class InMemoryFleetReportStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self.rows: dict[tuple[str, str, date], FleetReportDraft] = {}

    def clear(self) -> None:
        with self._lock:
            self.rows.clear()

    def get(self, context: TenantContext, month: date) -> FleetReportDraft | None:
        row = self.rows.get((context.tenant_id, context.workspace_id, month))
        return row.model_copy(deep=True) if row is not None else None

    def get_by_id(self, context: TenantContext, draft_id: str) -> FleetReportDraft | None:
        found = next((r for r in self.rows.values() if r.id == draft_id and r.tenant_id == context.tenant_id
                      and r.workspace_id == context.workspace_id), None)
        return found.model_copy(deep=True) if found is not None else None

    def latest_before(self, context: TenantContext, month: date) -> FleetReportDraft | None:
        earlier = [r for (t, w, m), r in self.rows.items() if t == context.tenant_id and w == context.workspace_id and m < month and r.company_name]
        return max(earlier, key=lambda r: r.month).model_copy(deep=True) if earlier else None

    def save(self, draft: FleetReportDraft, expected_version: int) -> bool:
        key = (draft.tenant_id, draft.workspace_id, draft.month)
        with self._lock:
            current = self.rows.get(key)
            if (current.version if current is not None else 0) != expected_version:
                return False
            self.rows[key] = draft.model_copy(update={"version": expected_version + 1}, deep=True)
            return True


fleet_report_memory_store = InMemoryFleetReportStore()


@contextmanager
def open_fleet_report_store(context: TenantContext) -> Iterator[FleetReportStore]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.vehicles_repository import SqlFleetReportStore

        return SqlFleetReportStore(session, context)

    with open_scoped_store(context, fleet_report_memory_store, sql_store, user_scoped=True) as store:
        yield store


__all__ = ["MAX_DELIVERIES", "FleetReportDraft", "FleetReportStore", "InMemoryFleetReportStore", "fleet_report_memory_store",
           "open_fleet_report_store"]
