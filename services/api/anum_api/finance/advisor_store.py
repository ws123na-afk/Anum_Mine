"""Money advisor storage: the person's weekly notes and their switches (docs/finance.md#money-advisor).

With PostgreSQL the store is ``anum_api.db.advisor_repository.SqlAlchemyAdvisorStore`` in a session whose
tenant, workspace and user RLS context is set (``open_scoped_store(..., user_scoped=True)``): both tables
(migration ``0143_finance_money_advisor``) are under forced RLS with the tenant policy and a restrictive
owner-only policy, and every query repeats the scope and the owner. The in-memory store serves local
development and tests with the same rules.

One note per person per advice week (``week_start``, unique): the daily job and "Generate now" both write
that week's row, and ``delivered_at`` records that the weekly notification went out, so it goes out once.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .advisor import AdvisorNote

DEFAULT_WEEKDAY = 6  # Sunday (Monday is 0)
KEEP_WEEKS = 104  # notes older than two years are deleted when a new one is written

Trigger = Literal["schedule", "manual"]


class AdvisorSettings(BaseModel):
    """The weekly note's switches. On by default, delivered on Sunday in the person's Finance time zone."""

    enabled: bool = True
    weekday: int = Field(default=DEFAULT_WEEKDAY, ge=0, le=6)
    updated_at: datetime | None = None


class AdvisorReport(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    week_start: date
    trigger: Trigger
    generated_at: datetime
    delivered_at: datetime | None = None
    note: AdvisorNote


class AdvisorStore(Protocol):
    def get_settings(self, context: TenantContext) -> AdvisorSettings | None: ...
    def save_settings(self, context: TenantContext, settings: AdvisorSettings) -> AdvisorSettings: ...
    def for_week(self, context: TenantContext, week_start: date) -> AdvisorReport | None: ...
    def save_report(self, report: AdvisorReport) -> AdvisorReport: ...
    def claim_delivery(self, context: TenantContext, report_id: str, now: datetime) -> bool: ...
    def release_delivery(self, context: TenantContext, report_id: str) -> None: ...
    def get_report(self, context: TenantContext, report_id: str) -> AdvisorReport | None: ...
    def list_reports(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[AdvisorReport], int]: ...
    def prune(self, context: TenantContext, before: date) -> int: ...
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def _mine(item: AdvisorReport, context: TenantContext) -> bool:
    return (item.tenant_id, item.workspace_id, item.owner_user_id) == (context.tenant_id, context.workspace_id, context.user_id)


def _key(context: TenantContext) -> tuple[str, str, str]:
    return (context.tenant_id, context.workspace_id, context.user_id)


class InMemoryAdvisorStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.clear()

    def clear(self) -> None:
        self.settings: dict[tuple[str, str, str], AdvisorSettings] = {}
        self.reports: dict[str, AdvisorReport] = {}

    def get_settings(self, context: TenantContext) -> AdvisorSettings | None:
        with self._lock:
            found = self.settings.get(_key(context))
            return found.model_copy() if found else None

    def save_settings(self, context: TenantContext, settings: AdvisorSettings) -> AdvisorSettings:
        with self._lock:
            self.settings[_key(context)] = settings.model_copy()
        return settings

    def for_week(self, context: TenantContext, week_start: date) -> AdvisorReport | None:
        with self._lock:
            return next((r.model_copy(deep=True) for r in self.reports.values() if _mine(r, context) and r.week_start == week_start), None)

    def save_report(self, report: AdvisorReport) -> AdvisorReport:
        """Insert, or replace the note of the same person's same week (keeping its id and delivery)."""
        with self._lock:
            same = next((r for r in self.reports.values() if (r.tenant_id, r.workspace_id, r.owner_user_id, r.week_start)
                         == (report.tenant_id, report.workspace_id, report.owner_user_id, report.week_start)), None)
            if same is not None:
                report = report.model_copy(update={"id": same.id, "delivered_at": same.delivered_at})
            self.reports[report.id] = report.model_copy(deep=True)
        return report

    def claim_delivery(self, context: TenantContext, report_id: str, now: datetime) -> bool:
        with self._lock:
            found = self.reports.get(report_id)
            if found is None or not _mine(found, context) or found.delivered_at is not None:
                return False
            found.delivered_at = now
            return True

    def release_delivery(self, context: TenantContext, report_id: str) -> None:
        with self._lock:
            found = self.reports.get(report_id)
            if found is not None and _mine(found, context):
                found.delivered_at = None

    def get_report(self, context: TenantContext, report_id: str) -> AdvisorReport | None:
        with self._lock:
            found = self.reports.get(report_id)
            return found.model_copy(deep=True) if found and _mine(found, context) else None

    def list_reports(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[AdvisorReport], int]:
        with self._lock:
            mine = sorted((r for r in self.reports.values() if _mine(r, context)), key=lambda r: r.week_start, reverse=True)
            return [r.model_copy(deep=True) for r in mine[offset:offset + limit]], len(mine)

    def prune(self, context: TenantContext, before: date) -> int:
        with self._lock:
            old = [k for k, r in self.reports.items() if _mine(r, context) and r.week_start < before]
            for key in old:
                del self.reports[key]
            return len(old)

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            return sum(1 for r in self.reports.values() if _mine(r, context)) + int(_key(context) in self.settings)

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = self.prune(context, date.max)
            if self.settings.pop(_key(context), None) is not None:
                removed += 1
            return removed


advisor_memory_store = InMemoryAdvisorStore()


@contextmanager
def open_advisor_store(context: TenantContext) -> Iterator[AdvisorStore]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.advisor_repository import SqlAlchemyAdvisorStore

        return SqlAlchemyAdvisorStore(session)

    with open_scoped_store(context, advisor_memory_store, sql_store, user_scoped=True) as store:
        yield store


__all__ = ["AdvisorReport", "AdvisorSettings", "AdvisorStore", "DEFAULT_WEEKDAY", "InMemoryAdvisorStore", "KEEP_WEEKS", "Trigger",
           "advisor_memory_store", "open_advisor_store"]
