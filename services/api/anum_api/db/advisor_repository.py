"""PostgreSQL store for the Money advisor (migration ``0143_finance_money_advisor``, docs/finance.md#money-advisor).

The session carries the tenant, workspace and user RLS context (``open_advisor_store`` opens it with
``user_scoped=True``); both tables also have a restrictive ``owner_user_id = anum.user_id`` policy, and every
query here repeats the scope and the owner.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, SmallInteger, String, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.advisor_store import AdvisorReport, AdvisorSettings
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)


class FinanceAdvisorReportRecord(Base):
    __tablename__ = "finance_advisor_reports"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    week_start: Mapped[date] = mapped_column(Date)
    trigger: Mapped[str] = mapped_column(String(10))
    generated_at: Mapped[datetime] = mapped_column(TS)
    delivered_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    note: Mapped[dict[str, Any]] = mapped_column(JSONB)


class FinanceAdvisorSettingsRecord(Base):
    __tablename__ = "finance_advisor_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    updated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


RECORDS = (FinanceAdvisorReportRecord, FinanceAdvisorSettingsRecord)


def _mine(record: Any, context: TenantContext) -> list[Any]:
    return [record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == context.user_id]


def _report(row: FinanceAdvisorReportRecord) -> AdvisorReport:
    return AdvisorReport.model_validate({c.name: getattr(row, c.name) for c in FinanceAdvisorReportRecord.__table__.columns})


class SqlAlchemyAdvisorStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_settings(self, context: TenantContext) -> AdvisorSettings | None:
        row = self.session.scalar(select(FinanceAdvisorSettingsRecord).where(*_mine(FinanceAdvisorSettingsRecord, context)))
        return AdvisorSettings(enabled=row.enabled, weekday=row.weekday, updated_at=row.updated_at) if row is not None else None

    def save_settings(self, context: TenantContext, settings: AdvisorSettings) -> AdvisorSettings:
        values = {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "owner_user_id": context.user_id,
                  "enabled": settings.enabled, "weekday": settings.weekday, "updated_at": settings.updated_at}
        self.session.execute(insert(FinanceAdvisorSettingsRecord).values(**values).on_conflict_do_update(
            index_elements=["tenant_id", "workspace_id", "owner_user_id"],
            set_={"enabled": settings.enabled, "weekday": settings.weekday, "updated_at": settings.updated_at}))
        return settings

    def for_week(self, context: TenantContext, week_start: date) -> AdvisorReport | None:
        record = FinanceAdvisorReportRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.week_start == week_start))
        return _report(row) if row is not None else None

    def save_report(self, report: AdvisorReport) -> AdvisorReport:
        """Insert, or replace the note of the same person's same week (keeping its id and delivery)."""
        values = report.model_dump(mode="json")
        values["week_start"], values["generated_at"] = report.week_start, report.generated_at
        values["delivered_at"] = report.delivered_at
        record = FinanceAdvisorReportRecord
        row = self.session.execute(insert(record).values(**values).on_conflict_do_update(
            constraint="uq_finance_advisor_reports_week",
            set_={"note": values["note"], "trigger": values["trigger"], "generated_at": report.generated_at},
        ).returning(record.id, record.delivered_at)).one()
        return report.model_copy(update={"id": row[0], "delivered_at": row[1]})

    def claim_delivery(self, context: TenantContext, report_id: str, now: datetime) -> bool:
        record = FinanceAdvisorReportRecord
        result = self.session.execute(update(record).where(*_mine(record, context), record.id == report_id, record.delivered_at.is_(None))
                                      .values(delivered_at=now))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    def release_delivery(self, context: TenantContext, report_id: str) -> None:
        record = FinanceAdvisorReportRecord
        self.session.execute(update(record).where(*_mine(record, context), record.id == report_id).values(delivered_at=None))

    def get_report(self, context: TenantContext, report_id: str) -> AdvisorReport | None:
        record = FinanceAdvisorReportRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == report_id))
        return _report(row) if row is not None else None

    def list_reports(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[AdvisorReport], int]:
        record = FinanceAdvisorReportRecord
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*_mine(record, context))) or 0)
        rows = self.session.scalars(select(record).where(*_mine(record, context)).order_by(record.week_start.desc()).limit(limit).offset(offset))
        return [_report(row) for row in rows], total

    def prune(self, context: TenantContext, before: date) -> int:
        record = FinanceAdvisorReportRecord
        return int(self.session.execute(delete(record).where(*_mine(record, context), record.week_start < before)).rowcount or 0)  # type: ignore[attr-defined]

    def count_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.scalar(select(func.count()).select_from(r).where(*_mine(r, context))) or 0) for r in RECORDS)

    def delete_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.execute(delete(r).where(*_mine(r, context))).rowcount or 0) for r in RECORDS)  # type: ignore[attr-defined]


__all__ = ["FinanceAdvisorReportRecord", "FinanceAdvisorSettingsRecord", "RECORDS", "SqlAlchemyAdvisorStore"]
