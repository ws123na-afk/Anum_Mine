"""PostgreSQL attendance store (migration ``0132_work_attendance``, docs/attendance.md).

The session carries the tenant, workspace and user RLS context (``open_attendance_store`` opens
it with ``user_scoped=True``). The restrictive policies admit a row to its owner, and for
reading only, to a viewer named in the owner's active share. Every query here repeats the scope
and the owner as well, so a missing policy would still not mix people up.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, SmallInteger, String, Text, delete, func, or_, select, union, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.attendance.models import AttendanceEvent, Correction, DailyReport, DayTotal, EventStatus, Profile, Share, Site, WorkspaceSettings
from anum_api.attendance.store import MAX_EVENTS_READ, DuplicateError, export_records
from anum_api.schemas import TenantContext

from .models import Base
from .scope import require_workspace

TS = DateTime(timezone=True)


class _Owned:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class AttendanceProfileRecord(Base):
    __tablename__ = "attendance_profiles"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    organisation: Mapped[str | None] = mapped_column(String(160), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workplace_site_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    time_zone: Mapped[str] = mapped_column(String(64))
    week_starts_on: Mapped[int] = mapped_column(SmallInteger)
    schedule: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    daily_overtime_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekly_overtime_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_consent_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    tracking_paused: Mapped[bool] = mapped_column(Boolean)
    paused_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    ai_drafts_enabled: Mapped[bool] = mapped_column(Boolean)
    missed_check_alerts: Mapped[bool] = mapped_column(Boolean)
    last_alert_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TS)


class AttendanceSiteRecord(Base, _Owned):
    __tablename__ = "attendance_sites"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    address_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    radius_m: Mapped[int] = mapped_column(Integer)
    archived: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class AttendanceEventRecord(Base, _Owned):
    __tablename__ = "attendance_events"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(TS)
    received_at: Mapped[datetime] = mapped_column(TS)
    time_zone: Mapped[str] = mapped_column(String(64))
    site_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(10))
    quality: Mapped[str] = mapped_column(String(10))
    device_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idem_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class AttendanceCorrectionRecord(Base, _Owned):
    __tablename__ = "attendance_corrections"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    action: Mapped[str] = mapped_column(String(10))
    event_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    replacement_event_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(TS)


class AttendanceDayTotalRecord(Base):
    __tablename__ = "attendance_day_totals"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    time_zone: Mapped[str] = mapped_column(String(64))
    presence_seconds: Mapped[int] = mapped_column(Integer)
    remote_seconds: Mapped[int] = mapped_column(Integer)
    break_seconds: Mapped[int] = mapped_column(Integer)
    worked_seconds: Mapped[int] = mapped_column(Integer)
    verified_seconds: Mapped[int] = mapped_column(Integer)
    estimated_seconds: Mapped[int] = mapped_column(Integer)
    scheduled_seconds: Mapped[int] = mapped_column(Integer)
    event_count: Mapped[int] = mapped_column(Integer)
    overtime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_arrival: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_departure: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    site_ids: Mapped[list[str]] = mapped_column(JSONB)
    flags: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS)


class AttendanceDailyReportRecord(Base, _Owned):
    __tablename__ = "attendance_daily_reports"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    project: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tasks_done: Mapped[list[str]] = mapped_column(JSONB)
    tasks_pending: Mapped[list[str]] = mapped_column(JSONB)
    site_visits: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    issues: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_spent_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[str] = mapped_column(String(10))
    follow_up_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_ids: Mapped[list[str]] = mapped_column(JSONB)
    drafted_with_ai: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)
    submitted_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)  # 0141_attendance_report_submit


class AttendanceShareRecord(Base, _Owned):
    __tablename__ = "attendance_shares"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    viewer_user_id: Mapped[str] = mapped_column(String(160))
    include_reports: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


def _fields(record: Any, model: type) -> dict[str, Any]:
    return {name: getattr(record, name) for name in model.model_fields if hasattr(record, name)}


class AttendanceWorkspaceSettingsRecord(Base):
    """Migration ``0181_attendance_manager_view``: one row per workspace, written only in a manager unit."""

    __tablename__ = "attendance_workspace_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    manager_view: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[str] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(TS)


class SqlAttendanceStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self, record: Any, context: TenantContext, owner: str) -> list[Any]:
        return [record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == owner]

    def _ready(self, context: TenantContext) -> None:
        require_workspace(self.session, context.tenant_id, context.workspace_id)

    # profile -------------------------------------------------------------------------------------

    def get_profile(self, context: TenantContext, owner: str) -> Profile | None:
        row = self.session.scalar(select(AttendanceProfileRecord).where(*self._scope(AttendanceProfileRecord, context, owner)))
        return Profile(**_fields(row, Profile)) if row else None

    def save_profile(self, profile: Profile, expected_version: int) -> bool:
        ctx = TenantContext(tenant_id=profile.tenant_id, workspace_id=profile.workspace_id, user_id=profile.owner_user_id)
        self._ready(ctx)
        values = profile.model_dump(mode="python", exclude={"version"})
        values["schedule"] = [s.model_dump() for s in profile.schedule]
        values["updated_at"] = values.get("updated_at") or func.now()
        if expected_version == 0:
            result = self.session.execute(insert(AttendanceProfileRecord).values(**values, version=1)
                                          .on_conflict_do_nothing().returning(AttendanceProfileRecord.version))
            return result.first() is not None
        key = {k: values.pop(k) for k in ("tenant_id", "workspace_id", "owner_user_id")}
        result = self.session.execute(
            update(AttendanceProfileRecord)
            .where(*self._scope(AttendanceProfileRecord, ctx, key["owner_user_id"]), AttendanceProfileRecord.version == expected_version)
            .values(**values, version=expected_version + 1)
        )
        return result.rowcount == 1  # type: ignore[attr-defined]

    # sites ---------------------------------------------------------------------------------------

    def list_sites(self, context: TenantContext, owner: str) -> list[Site]:
        rows = self.session.scalars(select(AttendanceSiteRecord).where(*self._scope(AttendanceSiteRecord, context, owner))
                                    .order_by(AttendanceSiteRecord.archived, func.lower(AttendanceSiteRecord.name), AttendanceSiteRecord.id))
        return [Site(**_fields(r, Site)) for r in rows]

    def get_site(self, context: TenantContext, owner: str, site_id: str) -> Site | None:
        row = self.session.scalar(select(AttendanceSiteRecord).where(*self._scope(AttendanceSiteRecord, context, owner),
                                                                     AttendanceSiteRecord.id == site_id))
        return Site(**_fields(row, Site)) if row else None

    def save_site(self, site: Site) -> Site:
        ctx = TenantContext(tenant_id=site.tenant_id, workspace_id=site.workspace_id, user_id=site.owner_user_id)
        self._ready(ctx)
        values = site.model_dump(mode="python")
        values["kind"] = site.kind.value
        nested = self.session.begin_nested()
        try:
            self.session.execute(insert(AttendanceSiteRecord).values(**values).on_conflict_do_update(
                index_elements=[AttendanceSiteRecord.id],
                set_={k: values[k] for k in ("name", "kind", "address_label", "radius_m", "archived", "updated_at")},
                where=AttendanceSiteRecord.owner_user_id == site.owner_user_id,
            ))
            nested.commit()
        except IntegrityError:
            nested.rollback()
            raise DuplicateError("site_name") from None
        return site

    def delete_site(self, context: TenantContext, site_id: str) -> bool:
        result = self.session.execute(delete(AttendanceSiteRecord).where(*self._scope(AttendanceSiteRecord, context, context.user_id),
                                                                         AttendanceSiteRecord.id == site_id))
        return result.rowcount == 1  # type: ignore[attr-defined]

    # events --------------------------------------------------------------------------------------

    def _event(self, row: AttendanceEventRecord) -> AttendanceEvent:
        return AttendanceEvent(**_fields(row, AttendanceEvent))

    def add_event(self, event: AttendanceEvent) -> tuple[AttendanceEvent, bool]:
        ctx = TenantContext(tenant_id=event.tenant_id, workspace_id=event.workspace_id, user_id=event.owner_user_id)
        self._ready(ctx)
        values = event.model_dump(mode="python")
        for key in ("kind", "source", "status"):
            values[key] = getattr(event, key).value
        inserted = self.session.execute(insert(AttendanceEventRecord).values(**values).on_conflict_do_nothing(
            constraint="uq_attendance_events_idem").returning(AttendanceEventRecord.id)).first()
        if inserted is not None:
            return event, True
        existing = self.session.scalar(select(AttendanceEventRecord).where(
            *self._scope(AttendanceEventRecord, ctx, event.owner_user_id), AttendanceEventRecord.idem_key == event.idem_key))
        assert existing is not None  # nosec B101 - the conflict says the row exists in this scope
        return self._event(existing), False

    def get_event(self, context: TenantContext, owner: str, event_id: str) -> AttendanceEvent | None:
        row = self.session.scalar(select(AttendanceEventRecord).where(*self._scope(AttendanceEventRecord, context, owner),
                                                                      AttendanceEventRecord.id == event_id))
        return self._event(row) if row else None

    def list_events(self, context: TenantContext, owner: str, start: datetime, end: datetime, *, active_only: bool = True) -> list[AttendanceEvent]:
        query = select(AttendanceEventRecord).where(*self._scope(AttendanceEventRecord, context, owner),
                                                    AttendanceEventRecord.occurred_at >= start, AttendanceEventRecord.occurred_at < end)
        if active_only:
            query = query.where(AttendanceEventRecord.status == EventStatus.ACTIVE.value)
        rows = self.session.scalars(query.order_by(AttendanceEventRecord.occurred_at, AttendanceEventRecord.received_at,
                                                   AttendanceEventRecord.id).limit(MAX_EVENTS_READ))
        return [self._event(r) for r in rows]

    def set_event_status(self, context: TenantContext, event_id: str, status: EventStatus) -> bool:
        result = self.session.execute(update(AttendanceEventRecord).where(
            *self._scope(AttendanceEventRecord, context, context.user_id), AttendanceEventRecord.id == event_id,
            AttendanceEventRecord.status == EventStatus.ACTIVE.value).values(status=status.value))
        return result.rowcount == 1  # type: ignore[attr-defined]

    def delete_events_before(self, context: TenantContext, cutoff: datetime) -> int:
        result = self.session.execute(delete(AttendanceEventRecord).where(
            *self._scope(AttendanceEventRecord, context, context.user_id), AttendanceEventRecord.occurred_at < cutoff))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def delete_events(self, context: TenantContext, event_ids: list[str]) -> int:
        removed = 0
        for start in range(0, len(event_ids), 500):
            result = self.session.execute(delete(AttendanceEventRecord).where(
                *self._scope(AttendanceEventRecord, context, context.user_id), AttendanceEventRecord.id.in_(event_ids[start:start + 500])))
            removed += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return removed

    # corrections ---------------------------------------------------------------------------------

    def add_correction(self, correction: Correction) -> Correction:
        values = correction.model_dump(mode="json")
        values["created_at"] = correction.created_at
        self.session.execute(insert(AttendanceCorrectionRecord).values(**values))
        return correction

    def list_corrections(self, context: TenantContext, owner: str, *, limit: int, offset: int) -> tuple[list[Correction], int]:
        where = self._scope(AttendanceCorrectionRecord, context, owner)
        total = int(self.session.scalar(select(func.count()).select_from(AttendanceCorrectionRecord).where(*where)) or 0)
        rows = self.session.scalars(select(AttendanceCorrectionRecord).where(*where).order_by(
            AttendanceCorrectionRecord.created_at.desc(), AttendanceCorrectionRecord.id.desc()).limit(limit).offset(offset))
        return [Correction(**_fields(r, Correction)) for r in rows], total

    def delete_corrections_before(self, context: TenantContext, cutoff: datetime) -> int:
        result = self.session.execute(delete(AttendanceCorrectionRecord).where(
            *self._scope(AttendanceCorrectionRecord, context, context.user_id), AttendanceCorrectionRecord.created_at < cutoff))
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    # day totals ----------------------------------------------------------------------------------

    def list_day_totals(self, context: TenantContext, owner: str, start: date, end: date) -> list[DayTotal]:
        rows = self.session.scalars(select(AttendanceDayTotalRecord).where(
            *self._scope(AttendanceDayTotalRecord, context, owner), AttendanceDayTotalRecord.day >= start,
            AttendanceDayTotalRecord.day <= end).order_by(AttendanceDayTotalRecord.day))
        return [DayTotal(**_fields(r, DayTotal)) for r in rows]

    def save_day_totals(self, totals: list[DayTotal]) -> int:
        for total in totals:
            values = total.model_dump(mode="python")
            self.session.execute(insert(AttendanceDayTotalRecord).values(**values).on_conflict_do_nothing())
        return len(totals)

    # daily reports -------------------------------------------------------------------------------

    def _report(self, row: AttendanceDailyReportRecord) -> DailyReport:
        return DailyReport(**_fields(row, DailyReport))

    def list_reports(self, context: TenantContext, owner: str, start: date | None, end: date | None, query: str | None, *,
                     limit: int, offset: int) -> tuple[list[DailyReport], int]:
        where = self._scope(AttendanceDailyReportRecord, context, owner)
        if start is not None:
            where.append(AttendanceDailyReportRecord.day >= start)
        if end is not None:
            where.append(AttendanceDailyReportRecord.day <= end)
        if query:
            needle = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append(or_(*(column.ilike(needle, escape="\\") for column in (
                AttendanceDailyReportRecord.project, AttendanceDailyReportRecord.issues, AttendanceDailyReportRecord.notes,
                func.cast(AttendanceDailyReportRecord.tasks_done, Text), func.cast(AttendanceDailyReportRecord.tasks_pending, Text),
                func.cast(AttendanceDailyReportRecord.site_visits, Text)))))
        total = int(self.session.scalar(select(func.count()).select_from(AttendanceDailyReportRecord).where(*where)) or 0)
        rows = self.session.scalars(select(AttendanceDailyReportRecord).where(*where).order_by(
            AttendanceDailyReportRecord.day.desc(), AttendanceDailyReportRecord.id).limit(limit).offset(offset))
        return [self._report(r) for r in rows], total

    def get_report(self, context: TenantContext, owner: str, day: date) -> DailyReport | None:
        row = self.session.scalar(select(AttendanceDailyReportRecord).where(
            *self._scope(AttendanceDailyReportRecord, context, owner), AttendanceDailyReportRecord.day == day))
        return self._report(row) if row else None

    def save_report(self, report: DailyReport, expected_version: int | None) -> bool:
        ctx = TenantContext(tenant_id=report.tenant_id, workspace_id=report.workspace_id, user_id=report.owner_user_id)
        self._ready(ctx)
        values = report.model_dump(mode="python")
        values["priority"] = report.priority.value
        values["site_visits"] = [v.model_dump() for v in report.site_visits]
        if expected_version is None:
            inserted = self.session.execute(insert(AttendanceDailyReportRecord).values(**values).on_conflict_do_nothing(
                constraint="uq_attendance_daily_reports_day").returning(AttendanceDailyReportRecord.id)).first()
            if inserted is None:
                raise DuplicateError("report_day")
            return True
        changes = {k: v for k, v in values.items() if k not in ("id", "tenant_id", "workspace_id", "owner_user_id", "day", "created_at")}
        result = self.session.execute(update(AttendanceDailyReportRecord).where(
            *self._scope(AttendanceDailyReportRecord, ctx, report.owner_user_id), AttendanceDailyReportRecord.day == report.day,
            AttendanceDailyReportRecord.version == expected_version).values(**changes))
        return result.rowcount == 1  # type: ignore[attr-defined]

    def delete_report(self, context: TenantContext, day: date) -> bool:
        result = self.session.execute(delete(AttendanceDailyReportRecord).where(
            *self._scope(AttendanceDailyReportRecord, context, context.user_id), AttendanceDailyReportRecord.day == day))
        return result.rowcount == 1  # type: ignore[attr-defined]

    # shares --------------------------------------------------------------------------------------

    def list_shares(self, context: TenantContext) -> list[Share]:
        rows = self.session.scalars(select(AttendanceShareRecord).where(
            AttendanceShareRecord.tenant_id == context.tenant_id, AttendanceShareRecord.workspace_id == context.workspace_id,
            AttendanceShareRecord.revoked_at.is_(None),
            or_(AttendanceShareRecord.owner_user_id == context.user_id, AttendanceShareRecord.viewer_user_id == context.user_id),
        ).order_by(AttendanceShareRecord.created_at, AttendanceShareRecord.id))
        return [Share(**_fields(r, Share)) for r in rows]

    def save_share(self, share: Share) -> Share:
        ctx = TenantContext(tenant_id=share.tenant_id, workspace_id=share.workspace_id, user_id=share.owner_user_id)
        self._ready(ctx)
        inserted = self.session.execute(insert(AttendanceShareRecord).values(**share.model_dump(mode="python")).on_conflict_do_nothing()
                                        .returning(AttendanceShareRecord.id)).first()
        if inserted is None:
            raise DuplicateError("share")
        return share

    def revoke_share(self, context: TenantContext, share_id: str, now: datetime) -> bool:
        result = self.session.execute(update(AttendanceShareRecord).where(
            *self._scope(AttendanceShareRecord, context, context.user_id), AttendanceShareRecord.id == share_id,
            AttendanceShareRecord.revoked_at.is_(None)).values(revoked_at=now))
        return result.rowcount == 1  # type: ignore[attr-defined]

    def share_for(self, context: TenantContext, owner: str) -> Share | None:
        row = self.session.scalar(select(AttendanceShareRecord).where(
            *self._scope(AttendanceShareRecord, context, owner), AttendanceShareRecord.viewer_user_id == context.user_id,
            AttendanceShareRecord.revoked_at.is_(None)))
        return Share(**_fields(row, Share)) if row else None

    # privacy -------------------------------------------------------------------------------------

    _RECORDS = (AttendanceShareRecord, AttendanceDailyReportRecord, AttendanceDayTotalRecord, AttendanceCorrectionRecord,
                AttendanceEventRecord, AttendanceSiteRecord, AttendanceProfileRecord)

    # workspace settings and the manager view (migration 0181_attendance_manager_view) -----------------

    def get_workspace_settings(self, context: TenantContext) -> WorkspaceSettings:
        row = self.session.scalar(select(AttendanceWorkspaceSettingsRecord).where(
            AttendanceWorkspaceSettingsRecord.tenant_id == context.tenant_id,
            AttendanceWorkspaceSettingsRecord.workspace_id == context.workspace_id))
        if row is None:
            return WorkspaceSettings(tenant_id=context.tenant_id, workspace_id=context.workspace_id)
        return WorkspaceSettings(tenant_id=row.tenant_id, workspace_id=row.workspace_id, manager_view=row.manager_view, version=row.version,
                                 updated_by=row.updated_by, updated_at=row.updated_at)

    def save_workspace_settings(self, settings: WorkspaceSettings, expected_version: int) -> bool:
        ctx = TenantContext(tenant_id=settings.tenant_id, workspace_id=settings.workspace_id, user_id=settings.updated_by or "")
        self._ready(ctx)
        values = {"manager_view": settings.manager_view, "updated_by": settings.updated_by, "updated_at": settings.updated_at or func.now()}
        if expected_version == 0:
            result = self.session.execute(insert(AttendanceWorkspaceSettingsRecord)
                                          .values(tenant_id=settings.tenant_id, workspace_id=settings.workspace_id, version=1, **values)
                                          .on_conflict_do_nothing().returning(AttendanceWorkspaceSettingsRecord.version))
            return result.first() is not None
        result = self.session.execute(
            update(AttendanceWorkspaceSettingsRecord)
            .where(AttendanceWorkspaceSettingsRecord.tenant_id == settings.tenant_id,
                   AttendanceWorkspaceSettingsRecord.workspace_id == settings.workspace_id,
                   AttendanceWorkspaceSettingsRecord.version == expected_version)
            .values(**values, version=expected_version + 1)
        )
        return result.rowcount == 1  # type: ignore[attr-defined]

    def list_people(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[str], int]:
        """Everyone keeping attendance here whose rows the policies let this unit read (own, or the manager view)."""
        people = union(
            select(AttendanceProfileRecord.owner_user_id.label("person")).where(
                AttendanceProfileRecord.tenant_id == context.tenant_id, AttendanceProfileRecord.workspace_id == context.workspace_id),
            select(AttendanceEventRecord.owner_user_id.label("person")).where(
                AttendanceEventRecord.tenant_id == context.tenant_id, AttendanceEventRecord.workspace_id == context.workspace_id),
        ).subquery()
        total = int(self.session.scalar(select(func.count()).select_from(people)) or 0)
        rows = self.session.scalars(select(people.c.person).order_by(people.c.person).limit(limit).offset(offset))
        return list(rows), total

    def count_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.scalar(select(func.count()).select_from(record).where(
            *self._scope(record, context, context.user_id))) or 0) for record in self._RECORDS)

    def delete_everything(self, context: TenantContext) -> int:
        removed = 0
        for record in self._RECORDS:
            result = self.session.execute(delete(record).where(*self._scope(record, context, context.user_id)))
            removed += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return removed

    def export(self, context: TenantContext) -> dict[str, Any]:
        return export_records(self, context)
