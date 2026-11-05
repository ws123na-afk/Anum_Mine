"""PostgreSQL store for finance links, business allocations and device events (migration ``0134_finance_links``).

The session is the owner-scoped unit of work of ``open_sms_stores`` (tenant, workspace and user
RLS context), and every owner query repeats the scope and the owner. Allocation rows live in the
business workspace: :meth:`SqlAlchemyLinkStore._in_workspace` switches the transaction-local
workspace setting for those statements and always switches it back, so the deferred link and
journal checks run in the owner's scope when the transaction commits.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Integer, Numeric, String, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.links_store import BusinessAllocation, DeviceEvent, EventSummary, FinanceLink, LinkStatus
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)


class FinanceLinkRecord(Base):
    __tablename__ = "finance_links"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    workspace_id: Mapped[str] = mapped_column(String(80))
    owner_user_id: Mapped[str] = mapped_column(String(160))
    canonical_id: Mapped[str] = mapped_column(String(80))
    dimension: Mapped[str] = mapped_column(String(20))
    role: Mapped[str] = mapped_column(String(12))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(80))
    target_workspace_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(14))
    status_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_event_ids: Mapped[list[str]] = mapped_column(JSONB)
    document_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idem_key: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)
    voided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    version: Mapped[int] = mapped_column(Integer)


class BusinessLedgerAllocationRecord(Base):
    __tablename__ = "business_ledger_allocations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    workspace_id: Mapped[str] = mapped_column(String(80))
    source_link_id: Mapped[str] = mapped_column(String(80))
    source_workspace_id: Mapped[str] = mapped_column(String(80))
    allocated_by: Mapped[str] = mapped_column(String(160))
    source_ref: Mapped[str] = mapped_column(String(12))
    posted_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))
    category: Mapped[str] = mapped_column(String(40))
    project_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    memo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    treatment: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(14))
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceDeviceEventRecord(Base):
    __tablename__ = "finance_device_events"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80))
    workspace_id: Mapped[str] = mapped_column(String(80))
    owner_user_id: Mapped[str] = mapped_column(String(160))
    device_id: Mapped[str] = mapped_column(String(80))
    integration: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(10))
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)


RECORDS = (FinanceLinkRecord, FinanceDeviceEventRecord)


def _mine(record: Any, context: TenantContext) -> tuple[Any, ...]:
    return (record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == context.user_id)


def _load(record: Any, model: Any) -> Any:
    return model.model_validate({name: getattr(record, name) for name in model.model_fields if hasattr(record, name)})


def _values(model: Any) -> dict[str, Any]:
    return {key: getattr(value, "value", value) for key, value in model.__dict__.items()}


class SqlAlchemyLinkStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @contextmanager
    def _in_workspace(self, context: TenantContext, workspace_id: str) -> Iterator[None]:
        """Statements inside run in ``workspace_id`` of the caller's tenant; the owner's scope comes back after."""
        self.session.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": workspace_id})
        try:
            yield
        finally:
            self.session.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": context.workspace_id})

    # links
    def add_link(self, link: FinanceLink) -> None:
        self.session.execute(insert(FinanceLinkRecord).values(**_values(link)))

    def save_link(self, link: FinanceLink, *, expected_version: int) -> bool:
        record = FinanceLinkRecord
        values = {k: v for k, v in _values(link).items() if k not in ("id", "tenant_id", "workspace_id", "owner_user_id")}
        result = self.session.execute(update(record).where(*_mine(record, _ctx(link)), record.id == link.id, record.version == expected_version)
                                      .values(**values))
        return bool(result.rowcount)

    def get_link(self, context: TenantContext, link_id: str) -> FinanceLink | None:
        record = FinanceLinkRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == link_id))
        return _load(row, FinanceLink) if row else None

    def link_by_idem(self, context: TenantContext, idem_key: str) -> FinanceLink | None:
        record = FinanceLinkRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.idem_key == idem_key, record.status != LinkStatus.VOID.value))
        return _load(row, FinanceLink) if row else None

    def live_links(self, context: TenantContext, canonical_id: str) -> list[FinanceLink]:
        record = FinanceLinkRecord
        rows = self.session.scalars(select(record).where(*_mine(record, context), record.canonical_id == canonical_id,
                                                         record.status != LinkStatus.VOID.value).order_by(record.created_at, record.id).limit(500))
        return [_load(r, FinanceLink) for r in rows]

    def list_links(self, context: TenantContext, *, canonical_id: str | None, dimension: str | None, target_type: str | None,
                   target_id: str | None, status: str | None, limit: int, offset: int) -> tuple[list[FinanceLink], int]:
        record = FinanceLinkRecord
        where = [*_mine(record, context)]
        for column, value in ((record.canonical_id, canonical_id), (record.dimension, dimension), (record.target_type, target_type),
                              (record.target_id, target_id), (record.status, status)):
            if value is not None:
                where.append(column == value)
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*where)) or 0)
        rows = self.session.scalars(select(record).where(*where).order_by(record.created_at.desc(), record.id.desc()).offset(offset).limit(limit))
        return [_load(r, FinanceLink) for r in rows], total

    # allocations (in the business workspace)
    def put_allocation(self, context: TenantContext, allocation: BusinessAllocation) -> None:
        with self._in_workspace(context, allocation.workspace_id):
            self.session.execute(insert(BusinessLedgerAllocationRecord).values(**_values(allocation)))

    def set_allocation(self, context: TenantContext, workspace_id: str, link_id: str, *, status: str, posted_on: date, now: datetime) -> None:
        record = BusinessLedgerAllocationRecord
        with self._in_workspace(context, workspace_id):
            self.session.execute(update(record).where(record.tenant_id == context.tenant_id, record.workspace_id == workspace_id,
                                                      record.source_link_id == link_id).values(status=status, posted_on=posted_on, updated_at=now))

    def retarget_allocation(self, context: TenantContext, workspace_id: str, link_id: str, *, source_ref: str, now: datetime) -> None:
        record = BusinessLedgerAllocationRecord
        with self._in_workspace(context, workspace_id):
            self.session.execute(update(record).where(record.tenant_id == context.tenant_id, record.workspace_id == workspace_id,
                                                      record.source_link_id == link_id).values(source_ref=source_ref, updated_at=now))

    def delete_allocation(self, context: TenantContext, workspace_id: str, link_id: str) -> None:
        record = BusinessLedgerAllocationRecord
        with self._in_workspace(context, workspace_id):
            self.session.execute(delete(record).where(record.tenant_id == context.tenant_id, record.workspace_id == workspace_id,
                                                      record.source_link_id == link_id))

    def list_allocations(self, context: TenantContext, *, currency: str | None, limit: int, offset: int) -> tuple[list[BusinessAllocation], int]:
        record = BusinessLedgerAllocationRecord
        where = [record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id]
        if currency is not None:
            where.append(record.currency == currency)
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*where)) or 0)
        rows = self.session.scalars(select(record).where(*where).order_by(record.posted_on.desc(), record.created_at.desc(), record.id.desc())
                                    .offset(offset).limit(limit))
        return [_load(r, BusinessAllocation) for r in rows], total

    def allocation_totals(self, context: TenantContext, *, currency: str | None) -> dict[str, Decimal]:
        record = BusinessLedgerAllocationRecord
        where = [record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id]
        if currency is not None:
            where.append(record.currency == currency)
        return {str(c): Decimal(v) for c, v in self.session.execute(select(record.currency, func.sum(record.amount)).where(*where).group_by(record.currency))}

    # device events
    def add_event(self, event: DeviceEvent) -> None:
        self.session.execute(insert(FinanceDeviceEventRecord).values(**_values(event)))

    def count_events(self, context: TenantContext, device_id: str, scope: str, since: datetime) -> int:
        record = FinanceDeviceEventRecord
        return int(self.session.scalar(select(func.count()).select_from(record).where(
            *_mine(record, context), record.device_id == device_id, record.scope == scope, record.outcome.in_(("ok", "test")),
            record.created_at >= since)) or 0)

    def purge_events(self, context: TenantContext, before: datetime) -> int:
        record = FinanceDeviceEventRecord
        return int(self.session.execute(delete(record).where(*_mine(record, context), record.created_at < before)).rowcount or 0)

    def event_summary(self, context: TenantContext, since: datetime) -> list[EventSummary]:
        record = FinanceDeviceEventRecord
        ok = record.outcome.in_(("ok", "test"))
        problem = record.outcome.in_(("failed", "denied"))
        rows = self.session.execute(
            select(record.integration, func.max(record.created_at),
                   func.max(record.created_at).filter(ok), func.count().filter(problem, record.created_at >= since))
            .where(*_mine(record, context)).group_by(record.integration))
        return [EventSummary(integration=str(i), last_at=last, last_ok_at=last_ok, failed_7d=int(failed or 0)) for i, last, last_ok, failed in rows]

    def list_events(self, context: TenantContext, *, integration: str, outcome: str | None, limit: int, offset: int) -> tuple[list[DeviceEvent], int]:
        record = FinanceDeviceEventRecord
        where = [*_mine(record, context), record.integration == integration]
        if outcome == "problem":
            where.append(record.outcome.in_(("failed", "denied")))
        elif outcome is not None:
            where.append(record.outcome == outcome)
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*where)) or 0)
        rows = self.session.scalars(select(record).where(*where).order_by(record.created_at.desc(), record.id.desc()).offset(offset).limit(limit))
        return [_load(r, DeviceEvent) for r in rows], total

    # privacy
    def _allocation_workspaces(self, context: TenantContext) -> list[str]:
        record = FinanceLinkRecord
        return sorted({str(w) for w in self.session.scalars(select(record.target_workspace_id).distinct().where(
            *_mine(record, context), record.target_workspace_id.is_not(None)))})

    def _made_by_me(self, context: TenantContext) -> Any:
        record = BusinessLedgerAllocationRecord
        return (record.tenant_id == context.tenant_id, record.source_workspace_id == context.workspace_id, record.allocated_by == context.user_id)

    def export_everything(self, context: TenantContext) -> dict[str, list[dict[str, Any]]]:
        made: list[dict[str, Any]] = []
        for workspace_id in self._allocation_workspaces(context):
            with self._in_workspace(context, workspace_id):
                made.extend(_load(r, BusinessAllocation).model_dump(mode="json")
                            for r in self.session.scalars(select(BusinessLedgerAllocationRecord).where(*self._made_by_me(context))))
        return {
            "finance_links": [_load(r, FinanceLink).model_dump(mode="json", exclude={"idem_key"})
                              for r in self.session.scalars(select(FinanceLinkRecord).where(*_mine(FinanceLinkRecord, context)))],
            "business_allocations_made": made,
            "device_events": [_load(r, DeviceEvent).model_dump(mode="json")
                              for r in self.session.scalars(select(FinanceDeviceEventRecord).where(*_mine(FinanceDeviceEventRecord, context)))],
        }

    def count_everything(self, context: TenantContext) -> int:
        made = 0
        for workspace_id in self._allocation_workspaces(context):
            with self._in_workspace(context, workspace_id):
                made += int(self.session.scalar(select(func.count()).select_from(BusinessLedgerAllocationRecord).where(*self._made_by_me(context))) or 0)
        return made + sum(int(self.session.scalar(select(func.count()).select_from(r).where(*_mine(r, context))) or 0) for r in RECORDS)

    def delete_everything(self, context: TenantContext) -> int:
        """The person's links and events, and the allocation rows they made in business workspaces."""
        removed = 0
        for workspace_id in self._allocation_workspaces(context):
            with self._in_workspace(context, workspace_id):
                removed += int(self.session.execute(delete(BusinessLedgerAllocationRecord).where(*self._made_by_me(context))).rowcount or 0)
        for record in RECORDS:
            removed += int(self.session.execute(delete(record).where(*_mine(record, context))).rowcount or 0)
        return removed


def _ctx(link: FinanceLink) -> TenantContext:
    return TenantContext(tenant_id=link.tenant_id, workspace_id=link.workspace_id, user_id=link.owner_user_id)


__all__ = ["RECORDS", "SqlAlchemyLinkStore"]
