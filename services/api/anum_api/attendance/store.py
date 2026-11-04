"""Attendance storage: the protocol, the in-memory store and ``open_attendance_store``.

With ``ANUM_REPOSITORY_BACKEND=postgresql`` the store is
``anum_api.db.attendance_repository.SqlAttendanceStore`` in a session whose tenant, workspace
**and user** RLS context is set. Every attendance table is under forced RLS: the tenant policy
plus restrictive owner policies (migration ``0133_work_attendance``). A person reads and writes
only their own rows; another member may *read* them only while an active share names them
(``attendance_shares``), and never writes them. The in-memory store applies the same rules for
local development and tests.

``owner`` arguments name whose records are read: the caller's own, or a person who shared
theirs with the caller.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from threading import RLock
from typing import Any, Protocol

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import AttendanceEvent, Correction, DailyReport, DayTotal, EventStatus, Profile, Share, Site

MAX_SITES = 100
MAX_EVENTS_READ = 20_000
MAX_SHARES = 50


class DuplicateError(ValueError):
    """A unique key (site name, report day, share) is already taken."""


class AttendanceStore(Protocol):
    def get_profile(self, context: TenantContext, owner: str) -> Profile | None: ...
    def save_profile(self, profile: Profile, expected_version: int) -> bool: ...
    def list_sites(self, context: TenantContext, owner: str) -> list[Site]: ...
    def get_site(self, context: TenantContext, owner: str, site_id: str) -> Site | None: ...
    def save_site(self, site: Site) -> Site: ...
    def delete_site(self, context: TenantContext, site_id: str) -> bool: ...
    def add_event(self, event: AttendanceEvent) -> tuple[AttendanceEvent, bool]: ...
    def get_event(self, context: TenantContext, owner: str, event_id: str) -> AttendanceEvent | None: ...
    def list_events(self, context: TenantContext, owner: str, start: datetime, end: datetime, *, active_only: bool = True) -> list[AttendanceEvent]: ...
    def set_event_status(self, context: TenantContext, event_id: str, status: EventStatus) -> bool: ...
    def add_correction(self, correction: Correction) -> Correction: ...
    def list_corrections(self, context: TenantContext, owner: str, *, limit: int, offset: int) -> tuple[list[Correction], int]: ...
    def list_day_totals(self, context: TenantContext, owner: str, start: date, end: date) -> list[DayTotal]: ...
    def save_day_totals(self, totals: list[DayTotal]) -> int: ...
    def delete_events_before(self, context: TenantContext, cutoff: datetime) -> int: ...
    def delete_corrections_before(self, context: TenantContext, cutoff: datetime) -> int: ...
    def list_reports(self, context: TenantContext, owner: str, start: date | None, end: date | None, query: str | None, *,
                     limit: int, offset: int) -> tuple[list[DailyReport], int]: ...
    def get_report(self, context: TenantContext, owner: str, day: date) -> DailyReport | None: ...
    def save_report(self, report: DailyReport, expected_version: int | None) -> bool: ...
    def delete_report(self, context: TenantContext, day: date) -> bool: ...
    def list_shares(self, context: TenantContext) -> list[Share]: ...
    def save_share(self, share: Share) -> Share: ...
    def revoke_share(self, context: TenantContext, share_id: str, now: datetime) -> bool: ...
    def share_for(self, context: TenantContext, owner: str) -> Share | None: ...
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...
    def export(self, context: TenantContext) -> dict[str, Any]: ...


def _scope(item: Any, context: TenantContext) -> bool:
    return item.tenant_id == context.tenant_id and item.workspace_id == context.workspace_id


def _mine(item: Any, context: TenantContext) -> bool:
    return _scope(item, context) and item.owner_user_id == context.user_id


def report_matches(report: DailyReport, query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    texts = [report.project or "", report.issues or "", report.notes or "", *report.tasks_done, *report.tasks_pending,
             *(visit.name for visit in report.site_visits)]
    return any(needle in text.casefold() for text in texts)


class InMemoryAttendanceStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self.clear()

    def clear(self) -> None:
        with getattr(self, "_lock", RLock()):
            self.profiles: dict[tuple[str, str, str], Profile] = {}
            self.sites: dict[str, Site] = {}
            self.events: dict[str, AttendanceEvent] = {}
            self.corrections: dict[str, Correction] = {}
            self.totals: dict[tuple[str, str, str, date], DayTotal] = {}
            self.reports: dict[str, DailyReport] = {}
            self.shares: dict[str, Share] = {}

    # what the caller may read: their own rows, or a sharer's while the share is active
    def _readable(self, item: Any, context: TenantContext, owner: str, *, reports: bool = False) -> bool:
        if not _scope(item, context) or item.owner_user_id != owner:
            return False
        if owner == context.user_id:
            return True
        share = self.share_for(context, owner)
        return share is not None and (share.include_reports or not reports)

    def get_profile(self, context: TenantContext, owner: str) -> Profile | None:
        with self._lock:
            found = self.profiles.get((context.tenant_id, context.workspace_id, owner))
            return found.model_copy(deep=True) if found and self._readable(found, context, owner) else None

    def save_profile(self, profile: Profile, expected_version: int) -> bool:
        with self._lock:
            key = (profile.tenant_id, profile.workspace_id, profile.owner_user_id)
            current = self.profiles.get(key)
            if (current.version if current else 0) != expected_version:
                return False
            self.profiles[key] = profile.model_copy(deep=True, update={"version": expected_version + 1})
            return True

    def list_sites(self, context: TenantContext, owner: str) -> list[Site]:
        with self._lock:
            found = [s for s in self.sites.values() if self._readable(s, context, owner)]
            return [s.model_copy() for s in sorted(found, key=lambda s: (s.archived, s.name.casefold(), s.id))]

    def get_site(self, context: TenantContext, owner: str, site_id: str) -> Site | None:
        with self._lock:
            found = self.sites.get(site_id)
            return found.model_copy() if found and self._readable(found, context, owner) else None

    def save_site(self, site: Site) -> Site:
        with self._lock:
            for other in self.sites.values():
                if (other.id != site.id and other.tenant_id == site.tenant_id and other.workspace_id == site.workspace_id
                        and other.owner_user_id == site.owner_user_id and other.name.casefold() == site.name.casefold()):
                    raise DuplicateError("site_name")
            self.sites[site.id] = site.model_copy()
            return site

    def delete_site(self, context: TenantContext, site_id: str) -> bool:
        with self._lock:
            found = self.sites.get(site_id)
            if found is None or not _mine(found, context):
                return False
            del self.sites[site_id]
            return True

    def add_event(self, event: AttendanceEvent) -> tuple[AttendanceEvent, bool]:
        with self._lock:
            for other in self.events.values():
                if (other.tenant_id, other.workspace_id, other.owner_user_id, other.idem_key) == (
                        event.tenant_id, event.workspace_id, event.owner_user_id, event.idem_key):
                    return other.model_copy(), False
            self.events[event.id] = event.model_copy()
            return event, True

    def get_event(self, context: TenantContext, owner: str, event_id: str) -> AttendanceEvent | None:
        with self._lock:
            found = self.events.get(event_id)
            return found.model_copy() if found and self._readable(found, context, owner) else None

    def list_events(self, context: TenantContext, owner: str, start: datetime, end: datetime, *, active_only: bool = True) -> list[AttendanceEvent]:
        with self._lock:
            found = [e for e in self.events.values() if self._readable(e, context, owner) and start <= e.occurred_at < end
                     and (not active_only or e.status == EventStatus.ACTIVE)]
            found.sort(key=lambda e: (e.occurred_at, e.received_at, e.id))
            return [e.model_copy() for e in found[:MAX_EVENTS_READ]]

    def set_event_status(self, context: TenantContext, event_id: str, status: EventStatus) -> bool:
        with self._lock:
            found = self.events.get(event_id)
            if found is None or not _mine(found, context) or found.status != EventStatus.ACTIVE:
                return False
            self.events[event_id] = found.model_copy(update={"status": status})
            return True

    def add_correction(self, correction: Correction) -> Correction:
        with self._lock:
            self.corrections[correction.id] = correction.model_copy(deep=True)
            return correction

    def list_corrections(self, context: TenantContext, owner: str, *, limit: int, offset: int) -> tuple[list[Correction], int]:
        with self._lock:
            found = sorted((c for c in self.corrections.values() if self._readable(c, context, owner)),
                           key=lambda c: (c.created_at, c.id), reverse=True)
            return [c.model_copy(deep=True) for c in found[offset:offset + limit]], len(found)

    def list_day_totals(self, context: TenantContext, owner: str, start: date, end: date) -> list[DayTotal]:
        with self._lock:
            found = [t for t in self.totals.values() if self._readable(t, context, owner) and start <= t.day <= end]
            return [t.model_copy(deep=True) for t in sorted(found, key=lambda t: t.day)]

    def save_day_totals(self, totals: list[DayTotal]) -> int:
        with self._lock:
            for total in totals:
                self.totals[(total.tenant_id, total.workspace_id, total.owner_user_id, total.day)] = total.model_copy(deep=True)
            return len(totals)

    def delete_events_before(self, context: TenantContext, cutoff: datetime) -> int:
        with self._lock:
            gone = [k for k, e in self.events.items() if _mine(e, context) and e.occurred_at < cutoff]
            for key in gone:
                del self.events[key]
            return len(gone)

    def delete_corrections_before(self, context: TenantContext, cutoff: datetime) -> int:
        with self._lock:
            gone = [k for k, c in self.corrections.items() if _mine(c, context) and c.created_at < cutoff]
            for key in gone:
                del self.corrections[key]
            return len(gone)

    def list_reports(self, context: TenantContext, owner: str, start: date | None, end: date | None, query: str | None, *,
                     limit: int, offset: int) -> tuple[list[DailyReport], int]:
        with self._lock:
            found = [r for r in self.reports.values() if self._readable(r, context, owner, reports=True)
                     and (start is None or r.day >= start) and (end is None or r.day <= end) and report_matches(r, query)]
            found.sort(key=lambda r: (r.day, r.id), reverse=True)
            return [r.model_copy(deep=True) for r in found[offset:offset + limit]], len(found)

    def get_report(self, context: TenantContext, owner: str, day: date) -> DailyReport | None:
        with self._lock:
            for report in self.reports.values():
                if report.day == day and self._readable(report, context, owner, reports=True):
                    return report.model_copy(deep=True)
            return None

    def save_report(self, report: DailyReport, expected_version: int | None) -> bool:
        with self._lock:
            ctx = TenantContext(tenant_id=report.tenant_id, workspace_id=report.workspace_id, user_id=report.owner_user_id)
            current = self.get_report(ctx, report.owner_user_id, report.day)
            if expected_version is None and current is not None:
                raise DuplicateError("report_day")
            if expected_version is not None and (current is None or current.version != expected_version):
                return False
            if current is not None:
                del self.reports[current.id]
            self.reports[report.id] = report.model_copy(deep=True)
            return True

    def delete_report(self, context: TenantContext, day: date) -> bool:
        with self._lock:
            for key, report in list(self.reports.items()):
                if _mine(report, context) and report.day == day:
                    del self.reports[key]
                    return True
            return False

    def list_shares(self, context: TenantContext) -> list[Share]:
        with self._lock:
            found = [s for s in self.shares.values() if _scope(s, context) and s.revoked_at is None
                     and context.user_id in (s.owner_user_id, s.viewer_user_id)]
            return [s.model_copy() for s in sorted(found, key=lambda s: (s.created_at, s.id))]

    def save_share(self, share: Share) -> Share:
        with self._lock:
            for other in self.shares.values():
                if (other.revoked_at is None and other.tenant_id == share.tenant_id and other.workspace_id == share.workspace_id
                        and other.owner_user_id == share.owner_user_id and other.viewer_user_id == share.viewer_user_id
                        and other.id != share.id):
                    raise DuplicateError("share")
            self.shares[share.id] = share.model_copy()
            return share

    def revoke_share(self, context: TenantContext, share_id: str, now: datetime) -> bool:
        with self._lock:
            found = self.shares.get(share_id)
            if found is None or not _mine(found, context) or found.revoked_at is not None:
                return False
            self.shares[share_id] = found.model_copy(update={"revoked_at": now})
            return True

    def share_for(self, context: TenantContext, owner: str) -> Share | None:
        with self._lock:
            for share in self.shares.values():
                if (_scope(share, context) and share.revoked_at is None and share.owner_user_id == owner
                        and share.viewer_user_id == context.user_id):
                    return share.model_copy()
            return None

    # privacy -------------------------------------------------------------------------------------

    def _tables(self) -> tuple[dict[Any, Any], ...]:
        return (self.sites, self.events, self.corrections, self.totals, self.reports, self.profiles)

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            owned = sum(1 for table in self._tables() for item in table.values() if _mine(item, context))
            return owned + sum(1 for s in self.shares.values() if _mine(s, context))

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = 0
            for table in (*self._tables(), self.shares):
                for key in [k for k, item in table.items() if _mine(item, context)]:
                    del table[key]
                    removed += 1
            return removed

    def export(self, context: TenantContext) -> dict[str, Any]:
        return export_records(self, context)


def export_records(store: AttendanceStore, context: TenantContext) -> dict[str, Any]:
    """The caller's own attendance for the privacy export (docs/attendance.md#privacy)."""
    from datetime import UTC
    from datetime import datetime as dt

    me = context.user_id
    hidden = {"tenant_id", "workspace_id", "owner_user_id"}
    profile = store.get_profile(context, me)
    first, last = dt(1970, 1, 1, tzinfo=UTC), dt(9999, 1, 1, tzinfo=UTC)
    corrections, total = store.list_corrections(context, me, limit=10_000, offset=0)
    reports, _ = store.list_reports(context, me, None, None, None, limit=10_000, offset=0)
    return {
        "profile": profile.model_dump(mode="json", exclude=hidden) if profile else None,
        "sites": [s.model_dump(mode="json", exclude=hidden) for s in store.list_sites(context, me)],
        "events": [e.model_dump(mode="json", exclude=hidden) for e in store.list_events(context, me, first, last, active_only=False)],
        "corrections": [c.model_dump(mode="json", exclude=hidden) for c in corrections],
        "day_totals": [t.model_dump(mode="json", exclude=hidden) for t in store.list_day_totals(context, me, date.min, date.max)],
        "daily_reports": [r.model_dump(mode="json", exclude=hidden) for r in reports],
        "shares": [s.model_dump(mode="json", exclude={"tenant_id", "workspace_id"}) for s in store.list_shares(context)
                   if s.owner_user_id == me],
        "corrections_total": total,
    }


attendance_store = InMemoryAttendanceStore()


@contextmanager
def open_attendance_store(context: TenantContext) -> Iterator[AttendanceStore]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.attendance_repository import SqlAttendanceStore

        return SqlAttendanceStore(session, context)

    with open_scoped_store(context, attendance_store, sql_store, user_scoped=True) as store:
        yield store
