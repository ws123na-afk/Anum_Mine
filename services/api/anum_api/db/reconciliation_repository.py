"""PostgreSQL store for reconciliation sessions and their resolutions (migration ``0153_finance_reconciliation``,
docs/finance.md#reconciliation-sessions).

The session carries the tenant, workspace and user RLS context (``open_recon_stores`` opens it with the finance and
journal stores, ``user_scoped=True``); the tables also have a restrictive ``owner_user_id = anum.user_id`` policy, and
every query here repeats the scope and the owner. Writes that may collide (a second session for the same period, a
second live resolution of the same statement row or ledger record) run in a savepoint, so the unit of work stays usable.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Integer, Numeric, String, delete, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.money import quantize
from anum_api.finance.reconciliation_store import PeriodTaken, ReconItem, Reconciliation
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)
MONEY = Numeric(20, 4)


class _Owned:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class FinanceReconciliationRecord(Base, _Owned):
    __tablename__ = "finance_reconciliations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    statement_opening: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    statement_closing: Mapped[Decimal] = mapped_column(MONEY)
    source: Mapped[str] = mapped_column(String(10))
    import_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    amount_due: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    minimum_due: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(12))
    completed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    completed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceReconciliationItemRecord(Base, _Owned):
    __tablename__ = "finance_reconciliation_items"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    reconciliation_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(12))
    statement_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    other_transaction_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(10))
    created_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(TS)
    undone_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    undone_by: Mapped[str | None] = mapped_column(String(160), nullable=True)


# Children first: privacy deletion removes the resolutions before their sessions.
RECORDS = (FinanceReconciliationItemRecord, FinanceReconciliationRecord)


def _mine(record: Any, context: TenantContext) -> list[Any]:
    return [record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == context.user_id]


def _plain(record: Any, model: Any) -> Any:
    item = model.model_validate({c.name: getattr(record, c.name) for c in record.__table__.columns})
    if isinstance(item, Reconciliation):
        # numeric(20, 4) reads back with four places: amounts carry their currency's own (docs/finance.md#money-rules).
        for name in ("statement_opening", "statement_closing", "amount_due", "minimum_due"):
            value = getattr(item, name)
            if value is not None:
                setattr(item, name, quantize(value, item.currency))
    return item


def _values(item: Reconciliation | ReconItem) -> dict[str, Any]:
    values = item.model_dump(mode="python")
    if isinstance(item, Reconciliation):
        values["snapshot"] = item.model_dump(mode="json")["snapshot"]
        values["history"] = item.model_dump(mode="json")["history"]
    return values


class SqlAlchemyReconciliationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, context: TenantContext, *, account_id: str | None = None) -> list[Reconciliation]:
        record = FinanceReconciliationRecord
        where = _mine(record, context) + ([record.account_id == account_id] if account_id else [])
        rows = self.session.scalars(select(record).where(*where).order_by(record.period_end.desc(), record.period_start.desc(), record.created_at.desc()))
        return [_plain(row, Reconciliation) for row in rows]

    def get(self, context: TenantContext, reconciliation_id: str) -> Reconciliation | None:
        record = FinanceReconciliationRecord
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == reconciliation_id))
        return _plain(row, Reconciliation) if row is not None else None

    def add(self, item: Reconciliation) -> None:
        try:
            with self.session.begin_nested():
                self.session.add(FinanceReconciliationRecord(**_values(item)))
                self.session.flush()
        except IntegrityError as exc:
            if "uq_finance_reconciliations_period" in str(exc.orig):
                raise PeriodTaken from None
            raise

    def save(self, item: Reconciliation, *, expected_version: int) -> bool:
        record = FinanceReconciliationRecord
        values = _values(item)
        values.pop("id")
        values["version"] = expected_version + 1
        context = (item.tenant_id, item.workspace_id, item.owner_user_id)
        result = self.session.execute(update(record).where(
            record.tenant_id == context[0], record.workspace_id == context[1], record.owner_user_id == context[2], record.id == item.id,
            record.version == expected_version).values(**values))
        if not result.rowcount:
            return False
        item.version = expected_version + 1
        return True

    def delete(self, context: TenantContext, reconciliation_id: str) -> bool:
        item = FinanceReconciliationItemRecord
        self.session.execute(delete(item).where(*_mine(item, context), item.reconciliation_id == reconciliation_id))
        record = FinanceReconciliationRecord
        return bool(self.session.execute(delete(record).where(*_mine(record, context), record.id == reconciliation_id)).rowcount)

    def delete_for_account(self, context: TenantContext, account_id: str) -> int:
        ids = [s.id for s in self.list(context, account_id=account_id)]
        return sum(1 for session_id in ids if self.delete(context, session_id))

    def items(self, context: TenantContext, reconciliation_id: str | None = None) -> list[ReconItem]:
        record = FinanceReconciliationItemRecord
        where = _mine(record, context) + ([record.reconciliation_id == reconciliation_id] if reconciliation_id else [])
        return [_plain(row, ReconItem) for row in self.session.scalars(select(record).where(*where).order_by(record.created_at, record.id))]

    def add_item(self, item: ReconItem) -> bool:
        """False when the statement row or a ledger record already has a live resolution in the session."""
        try:
            with self.session.begin_nested():
                self.session.add(FinanceReconciliationItemRecord(**_values(item)))
                self.session.flush()
        except IntegrityError as exc:
            if "uq_finance_reconciliation_items" in str(exc.orig):
                return False
            raise
        return True

    def undo_item(self, context: TenantContext, item_id: str, *, by: str, at: datetime) -> bool:
        record = FinanceReconciliationItemRecord
        result = self.session.execute(update(record).where(*_mine(record, context), record.id == item_id, record.status == "active")
                                      .values(status="undone", undone_at=at, undone_by=by))
        return bool(result.rowcount)

    def count_everything(self, context: TenantContext) -> int:
        from sqlalchemy import func

        return sum(int(self.session.scalar(select(func.count()).select_from(r).where(*_mine(r, context))) or 0) for r in RECORDS)

    def delete_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.execute(delete(r).where(*_mine(r, context))).rowcount or 0) for r in RECORDS)


__all__ = ["FinanceReconciliationItemRecord", "FinanceReconciliationRecord", "RECORDS", "SqlAlchemyReconciliationStore"]
