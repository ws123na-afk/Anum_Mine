"""PostgreSQL store for money lent and borrowed with people, payslips and remittances
(migration ``0147_finance_personal_money``, docs/finance.md#lent-and-borrowed).

The session carries the tenant, workspace and user RLS context (``open_personal_store`` opens it with
``user_scoped=True``); the tables also have a restrictive ``owner_user_id = anum.user_id`` policy, and every
query here repeats the scope and the owner.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Numeric, SmallInteger, String, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.personal import Payslip, PersonLoan, Remittance, SalaryProfile
from anum_api.schemas import TenantContext

from .models import Base

TS = DateTime(timezone=True)
MONEY = Numeric(20, 4)


class _Owned:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class FinancePersonLoanRecord(Base, _Owned):
    __tablename__ = "finance_person_loans"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    direction: Mapped[str] = mapped_column(String(10))
    person: Mapped[str] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    lent_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    remind: Mapped[bool] = mapped_column(Boolean)
    remind_days_before: Mapped[int] = mapped_column(SmallInteger)
    reminded_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    reminded_overdue_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinancePayslipRecord(Base, _Owned):
    __tablename__ = "finance_payslips"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    employer: Mapped[str] = mapped_column(String(120))
    month: Mapped[str] = mapped_column(String(7))
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    gross: Mapped[Decimal] = mapped_column(MONEY)
    deductions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceRemittanceRecord(Base, _Owned):
    __tablename__ = "finance_remittances"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sent_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    fee: Mapped[Decimal] = mapped_column(MONEY)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    received_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    received_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    recipient: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(120))
    regular: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceSalaryProfileRecord(Base, _Owned):
    """One per person (migration ``0150_finance_salary_profile``): the rules and the confirmed or rejected months as JSON."""

    __tablename__ = "finance_salary_profiles"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    employer: Mapped[str] = mapped_column(String(120))
    joined_on: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    expected_gross: Mapped[Decimal] = mapped_column(MONEY)
    pay_day: Mapped[int] = mapped_column(SmallInteger)
    account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


RECORDS = (FinancePersonLoanRecord, FinancePayslipRecord, FinanceRemittanceRecord, FinanceSalaryProfileRecord)
_BY_KIND: dict[str, tuple[type[Any], type[Any]]] = {
    "person_loan": (FinancePersonLoanRecord, PersonLoan),
    "payslip": (FinancePayslipRecord, Payslip),
    "remittance": (FinanceRemittanceRecord, Remittance),
    "salary_profile": (FinanceSalaryProfileRecord, SalaryProfile),
}
_ORDER = {"person_loan": "lent_on", "payslip": "month", "remittance": "sent_on", "salary_profile": "joined_on"}


def _mine(record: Any, context: TenantContext) -> list[Any]:
    return [record.tenant_id == context.tenant_id, record.workspace_id == context.workspace_id, record.owner_user_id == context.user_id]


class SqlAlchemyPersonalStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _plain(self, kind: str, row: Any) -> Any:
        record, model = _BY_KIND[kind]
        return model.model_validate({c.name: getattr(row, c.name) for c in record.__table__.columns})

    def list(self, context: TenantContext, kind: str) -> list:
        record, _ = _BY_KIND[kind]
        order = getattr(record, _ORDER[kind])
        rows = self.session.scalars(select(record).where(*_mine(record, context)).order_by(order.desc(), record.created_at.desc(), record.id))
        return [self._plain(kind, row) for row in rows]

    def get(self, context: TenantContext, kind: str, item_id: str) -> Any:
        record, _ = _BY_KIND[kind]
        row = self.session.scalar(select(record).where(*_mine(record, context), record.id == item_id))
        return self._plain(kind, row) if row is not None else None

    def save(self, kind: str, item: Any) -> None:
        record, _ = _BY_KIND[kind]
        values = item.model_dump(mode="python")
        if kind == "payslip":
            values["deductions"] = [d.model_dump(mode="json") for d in item.deductions]
        if kind == "salary_profile":
            values["rules"] = [r.model_dump(mode="json") for r in item.rules]
            values["decisions"] = [d.model_dump(mode="json") for d in item.decisions]
        for name in ("direction",):
            if name in values:
                values[name] = getattr(values[name], "value", values[name])
        existing = self.session.get(record, item.id)
        if existing is None:
            self.session.add(record(**values))
        else:
            if (existing.tenant_id, existing.workspace_id, existing.owner_user_id) != (item.tenant_id, item.workspace_id, item.owner_user_id):
                raise PermissionError("personal finance rows belong to the caller only")
            for key, value in values.items():
                setattr(existing, key, value)
        self.session.flush()

    def delete(self, context: TenantContext, kind: str, item_id: str) -> bool:
        record, _ = _BY_KIND[kind]
        return bool(self.session.execute(delete(record).where(*_mine(record, context), record.id == item_id)).rowcount)

    def count(self, context: TenantContext, kind: str) -> int:
        record, _ = _BY_KIND[kind]
        return int(self.session.scalar(select(func.count()).select_from(record).where(*_mine(record, context))) or 0)

    def claim_reminder(self, context: TenantContext, loan_id: str, due_on: date, overdue: bool) -> bool:
        record = FinancePersonLoanRecord
        column = record.reminded_overdue_for if overdue else record.reminded_for
        result = self.session.execute(update(record).where(*_mine(record, context), record.id == loan_id, record.due_on == due_on,
                                                           column.is_distinct_from(due_on))
                                      .values({column.key: due_on}))
        return bool(result.rowcount)

    def release_reminder(self, context: TenantContext, loan_id: str, overdue: bool) -> None:
        record = FinancePersonLoanRecord
        column = record.reminded_overdue_for if overdue else record.reminded_for
        self.session.execute(update(record).where(*_mine(record, context), record.id == loan_id).values({column.key: None}))

    def count_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.scalar(select(func.count()).select_from(r).where(*_mine(r, context))) or 0) for r in RECORDS)

    def delete_everything(self, context: TenantContext) -> int:
        return sum(int(self.session.execute(delete(r).where(*_mine(r, context))).rowcount or 0) for r in RECORDS)


__all__ = ["RECORDS", "SqlAlchemyPersonalStore"]
