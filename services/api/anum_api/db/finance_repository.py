"""PostgreSQL finance store (migration ``0056_finance_ledger``, docs/finance.md).

The session carries the tenant, workspace and user RLS context (``open_finance_store``
opens it with ``user_scoped=True``). Every finance table also has a restrictive policy on
``owner_user_id = anum.user_id``, and every query here repeats the scope and the owner,
so one person's ledger is never read or written for another, even if a policy were
missing. Money columns are ``numeric``: values come back as ``Decimal``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.finance.connectors import Connection
from anum_api.finance.execution import ExecSettings, GlobalSwitch, Intent, IntentStatus
from anum_api.finance.guardian import GuardianChange, GuardianPolicy
from anum_api.finance.models import (
    Account,
    Bill,
    Debt,
    FinanceSettings,
    FxRate,
    Goal,
    Holding,
    ImportRecord,
    PlanSettings,
    Transaction,
)
from anum_api.finance.money import quantize
from anum_api.finance.store import MAX_ORDERS, MAX_SUMMARY_ROWS, ItemKind, TransactionFilter
from anum_api.finance.trading import PaperOrder, Portfolio, PriceRecord
from anum_api.finance.wider import Incident, Widening
from anum_api.schemas import TenantContext

from .models import Base
from .scope import require_workspace

TS = DateTime(timezone=True)
MONEY = Numeric(20, 4)


class _Owned:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)


class FinanceSettingsRecord(Base):
    __tablename__ = "finance_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64))
    category_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceAccountRecord(Base, _Owned):
    __tablename__ = "finance_accounts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    institution: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    opening_balance: Mapped[Decimal] = mapped_column(MONEY)
    archived: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceImportRecord(Base, _Owned):
    __tablename__ = "finance_imports"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80))
    filename: Mapped[str] = mapped_column(String(200))
    preset: Mapped[str] = mapped_column(String(40))
    date_format: Mapped[str] = mapped_column(String(20))
    row_count: Mapped[int] = mapped_column(Integer)
    imported_count: Mapped[int] = mapped_column(Integer)
    duplicate_count: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    file_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS)


class FinanceTransactionRecord(Base, _Owned):
    __tablename__ = "finance_transactions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80))
    posted_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(30))
    category_source: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    import_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dedupe_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceBillRecord(Base, _Owned):
    __tablename__ = "finance_bills"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    cadence: Mapped[str] = mapped_column(String(20))
    next_due_on: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(30))
    account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceDebtRecord(Base, _Owned):
    __tablename__ = "finance_debts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    balance: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    apr_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    minimum_payment: Mapped[Decimal] = mapped_column(MONEY)
    due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceGoalRecord(Base, _Owned):
    __tablename__ = "finance_goals"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    target_amount: Mapped[Decimal] = mapped_column(MONEY)
    saved_amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    target_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    monthly_contribution: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceHoldingRecord(Base, _Owned):
    __tablename__ = "finance_holdings"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(80))
    symbol: Mapped[str] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 8))
    cost_basis: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinancePlanRecord(Base):
    __tablename__ = "finance_plans"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    buffers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    combine_currencies: Mapped[bool] = mapped_column(Boolean)
    fx_max_age_days: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceGuardianVersionRecord(Base, _Owned):
    __tablename__ = "finance_guardian_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    change_id: Mapped[str] = mapped_column(String(80))
    confirmed_by: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(TS)


class FinanceGuardianChangeRecord(Base, _Owned):
    __tablename__ = "finance_guardian_changes"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    base_version: Mapped[int] = mapped_column(Integer)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    proposed_by: Mapped[str] = mapped_column(String(10))
    proposer: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    loosens: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    decided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class FinanceConnectionRecord(Base, _Owned):
    __tablename__ = "finance_connections"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20))
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_hint: Mapped[str | None] = mapped_column(String(40), nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean)
    next_sync_at: Mapped[datetime] = mapped_column(TS)
    last_sync_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceFxRateRecord(Base, _Owned):
    __tablename__ = "finance_fx_rates"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    base: Mapped[str] = mapped_column(String(3))
    quote: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    connection_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(TS)


class FinancePriceRecord(Base, _Owned):
    __tablename__ = "finance_prices"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    price: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    as_of: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(120))
    connection_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(TS)


class FinancePaperPortfolioRecord(Base, _Owned):
    __tablename__ = "finance_paper_portfolios"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(10))
    starting_cash: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    fee_bps: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    fee_min: Mapped[Decimal] = mapped_column(MONEY)
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    max_price_age_days: Mapped[int] = mapped_column(Integer)
    epoch: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)
    reset_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class FinancePaperOrderRecord(Base, _Owned):
    __tablename__ = "finance_paper_orders"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String(80))
    intent_id: Mapped[str] = mapped_column(String(80))
    epoch: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(10))
    instrument_kind: Mapped[str] = mapped_column(String(10))
    symbol: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 8))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    status: Mapped[str] = mapped_column(String(10))
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(10))
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    price_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    price_source: Mapped[str | None] = mapped_column(String(240), nullable=True)
    notional: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    fee: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    slippage: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    guardian_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    guardian_codes: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS)
    filled_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceExecIntentRecord(Base, _Owned):
    __tablename__ = "finance_exec_intents"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(20))
    portfolio_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    instrument_kind: Mapped[str | None] = mapped_column(String(10), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    side: Mapped[str | None] = mapped_column(String(4), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 8), nullable=True)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    live_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(10))
    proposer: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    decision: Mapped[dict[str, Any]] = mapped_column(JSONB)
    guardian_version: Mapped[int] = mapped_column(Integer)
    estimate: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    refusal_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    refusal: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    approved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceExecSettingsRecord(Base):
    __tablename__ = "finance_exec_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    kill_switch: Mapped[bool] = mapped_column(Boolean)
    kill_switch_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    kill_switch_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    live_opt_in: Mapped[bool] = mapped_column(Boolean)
    live_opt_in_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    live_limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    live_version: Mapped[int] = mapped_column(Integer)
    wider: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(TS)


class FinanceLiveWideningRecord(Base, _Owned):
    __tablename__ = "finance_live_widenings"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    base_tier: Mapped[int] = mapped_column(Integer)
    tier: Mapped[int] = mapped_column(Integer)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    kinds: Mapped[list[str]] = mapped_column(JSONB)
    proposed_by: Mapped[str] = mapped_column(String(10))
    proposer: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    decided_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class FinanceLiveIncidentRecord(Base, _Owned):
    __tablename__ = "finance_live_incidents"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    code: Mapped[str] = mapped_column(String(30))
    tier_before: Mapped[int] = mapped_column(Integer)
    tier_after: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    reviewed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class FinanceExecGlobalRecord(Base):
    __tablename__ = "finance_exec_global"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    engaged: Mapped[bool] = mapped_column(Boolean)
    changed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    changed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


ITEM_RECORDS: dict[str, tuple[type[Any], type[BaseModel]]] = {
    "bill": (FinanceBillRecord, Bill),
    "debt": (FinanceDebtRecord, Debt),
    "goal": (FinanceGoalRecord, Goal),
    "holding": (FinanceHoldingRecord, Holding),
}


def _fields(model: BaseModel) -> dict[str, Any]:
    """A model's values as the record's columns (enums as their values)."""
    return {key: getattr(value, "value", value) for key, value in model.__dict__.items()}


MONEY_FIELDS = frozenset({
    "amount", "opening_balance", "balance", "minimum_payment", "target_amount", "saved_amount", "cost_basis", "monthly_contribution",
    "notional", "fee", "slippage",
})


def _model(record: Any, model: type[BaseModel]) -> Any:
    """The record as its model; money read back from ``numeric(20, 4)`` gets its currency's
    minor unit again (the value is unchanged: it was rounded to it on the way in)."""
    values = {name: getattr(record, name) for name in model.model_fields}
    currency = values.get("currency")
    if currency:
        for name in MONEY_FIELDS & values.keys():
            if values[name] is not None:
                values[name] = quantize(Decimal(values[name]), currency)
    return model.model_validate(values)


class SqlAlchemyFinanceStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _mine(self, record: type[Any], context: TenantContext) -> tuple[Any, ...]:
        return (
            record.tenant_id == context.tenant_id,
            record.workspace_id == context.workspace_id,
            record.owner_user_id == context.user_id,
        )

    def _require(self, item: Any) -> None:
        if (item.tenant_id, item.workspace_id, item.owner_user_id) != (
            self.context.tenant_id, self.context.workspace_id, self.context.user_id
        ):
            raise PermissionError("finance rows belong to the caller only")
        require_workspace(self.session, item.tenant_id, item.workspace_id)

    def _upsert(self, record: type[Any], item: BaseModel) -> None:
        self._require(item)
        values = _fields(item)
        existing = self.session.get(record, values["id"])
        if existing is None:
            self.session.add(record(**values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        self.session.flush()

    # settings -----------------------------------------------------------------------------

    def get_settings(self, context: TenantContext) -> FinanceSettings | None:
        found = self.session.scalar(select(FinanceSettingsRecord).where(*self._mine(FinanceSettingsRecord, context)))
        if found is None:
            return None
        return FinanceSettings.model_validate(
            {"base_currency": found.base_currency, "timezone": found.timezone, "category_rules": found.category_rules,
             "updated_at": found.updated_at}
        )

    def save_settings(self, context: TenantContext, settings: FinanceSettings) -> FinanceSettings:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        values = {
            "base_currency": settings.base_currency,
            "timezone": settings.timezone,
            "category_rules": [rule.model_dump(mode="json") for rule in settings.category_rules],
            "updated_at": settings.updated_at or datetime.now().astimezone(),
        }
        statement = insert(FinanceSettingsRecord).values(
            tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, **values
        )
        self.session.execute(
            statement.on_conflict_do_update(index_elements=["tenant_id", "workspace_id", "owner_user_id"], set_=values)
        )
        return settings

    # accounts -----------------------------------------------------------------------------

    def list_accounts(self, context: TenantContext) -> list[Account]:
        rows = self.session.scalars(
            select(FinanceAccountRecord)
            .where(*self._mine(FinanceAccountRecord, context))
            .order_by(FinanceAccountRecord.archived, func.lower(FinanceAccountRecord.name), FinanceAccountRecord.created_at)
        )
        return [_model(row, Account) for row in rows]

    def get_account(self, context: TenantContext, account_id: str) -> Account | None:
        row = self.session.scalar(
            select(FinanceAccountRecord).where(*self._mine(FinanceAccountRecord, context), FinanceAccountRecord.id == account_id)
        )
        return _model(row, Account) if row else None

    def save_account(self, account: Account) -> Account:
        self._upsert(FinanceAccountRecord, account)
        return account

    def delete_account(self, context: TenantContext, account_id: str) -> bool:
        # Transactions, imports and holdings cascade; bills, debts and goals keep their row.
        result = self.session.execute(
            delete(FinanceAccountRecord).where(*self._mine(FinanceAccountRecord, context), FinanceAccountRecord.id == account_id)
        )
        return bool(result.rowcount)

    def account_sums(self, context: TenantContext) -> dict[str, Decimal]:
        rows = self.session.execute(
            select(FinanceTransactionRecord.account_id, func.sum(FinanceTransactionRecord.amount))
            .where(*self._mine(FinanceTransactionRecord, context))
            .group_by(FinanceTransactionRecord.account_id)
        )
        return {account_id: Decimal(total or 0) for account_id, total in rows}  # exact; rounded when shown

    # transactions -------------------------------------------------------------------------

    def _filtered(self, context: TenantContext, flt: TransactionFilter) -> list[Any]:
        record = FinanceTransactionRecord
        clauses: list[Any] = list(self._mine(record, context))
        if flt.account_id:
            clauses.append(record.account_id == flt.account_id)
        if flt.category:
            clauses.append(record.category == flt.category.value)
        if flt.start:
            clauses.append(record.posted_on >= flt.start)
        if flt.end:
            clauses.append(record.posted_on <= flt.end)
        if flt.direction == "in":
            clauses.append(record.amount > 0)
        elif flt.direction == "out":
            clauses.append(record.amount < 0)
        if flt.query and flt.query.strip():
            needle = "%" + flt.query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append(or_(record.description.ilike(needle, escape="\\"), record.notes.ilike(needle, escape="\\")))
        return clauses

    def list_transactions(self, context: TenantContext, flt: TransactionFilter, *, limit: int, offset: int) -> tuple[list[Transaction], int]:
        record = FinanceTransactionRecord
        clauses = self._filtered(context, flt)
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*clauses)) or 0)
        rows = self.session.scalars(
            select(record).where(*clauses).order_by(record.posted_on.desc(), record.created_at.desc(), record.id.desc()).limit(limit).offset(offset)
        )
        return [_model(row, Transaction) for row in rows], total

    def get_transaction(self, context: TenantContext, transaction_id: str) -> Transaction | None:
        row = self.session.scalar(
            select(FinanceTransactionRecord).where(*self._mine(FinanceTransactionRecord, context), FinanceTransactionRecord.id == transaction_id)
        )
        return _model(row, Transaction) if row else None

    def save_transaction(self, transaction: Transaction) -> Transaction:
        self._upsert(FinanceTransactionRecord, transaction)
        return transaction

    def delete_transaction(self, context: TenantContext, transaction_id: str) -> bool:
        result = self.session.execute(
            delete(FinanceTransactionRecord).where(*self._mine(FinanceTransactionRecord, context), FinanceTransactionRecord.id == transaction_id)
        )
        return bool(result.rowcount)

    def existing_hashes(self, context: TenantContext, account_id: str, hashes: list[str]) -> set[str]:
        found: set[str] = set()
        for start in range(0, len(hashes), 1000):
            chunk = hashes[start : start + 1000]
            found |= set(
                self.session.scalars(
                    select(FinanceTransactionRecord.dedupe_hash).where(
                        *self._mine(FinanceTransactionRecord, context),
                        FinanceTransactionRecord.account_id == account_id,
                        FinanceTransactionRecord.dedupe_hash.in_(chunk),
                    )
                )
            )
        return found

    def add_transactions(self, transactions: list[Transaction]) -> int:
        added = 0
        for start in range(0, len(transactions), 500):
            chunk = transactions[start : start + 500]
            for item in chunk:
                self._require(item)
            statement = insert(FinanceTransactionRecord).values([_fields(item) for item in chunk])
            inserted = self.session.execute(
                statement.on_conflict_do_nothing(index_elements=["account_id", "dedupe_hash"]).returning(FinanceTransactionRecord.id)
            )
            added += len(inserted.all())
        return added

    def transactions_between(self, context: TenantContext, start: date, end: date) -> list[Transaction]:
        rows = self.session.scalars(
            select(FinanceTransactionRecord)
            .where(*self._mine(FinanceTransactionRecord, context), FinanceTransactionRecord.posted_on >= start, FinanceTransactionRecord.posted_on <= end)
            .order_by(FinanceTransactionRecord.posted_on)
            .limit(MAX_SUMMARY_ROWS)
        )
        return [_model(row, Transaction) for row in rows]

    # imports ------------------------------------------------------------------------------

    def list_imports(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[ImportRecord], int]:
        clauses = self._mine(FinanceImportRecord, context)
        total = int(self.session.scalar(select(func.count()).select_from(FinanceImportRecord).where(*clauses)) or 0)
        rows = self.session.scalars(
            select(FinanceImportRecord).where(*clauses).order_by(FinanceImportRecord.created_at.desc(), FinanceImportRecord.id.desc()).limit(limit).offset(offset)
        )
        return [_model(row, ImportRecord) for row in rows], total

    def get_import(self, context: TenantContext, import_id: str) -> ImportRecord | None:
        row = self.session.scalar(select(FinanceImportRecord).where(*self._mine(FinanceImportRecord, context), FinanceImportRecord.id == import_id))
        return _model(row, ImportRecord) if row else None

    def save_import(self, record: ImportRecord) -> ImportRecord:
        self._upsert(FinanceImportRecord, record)
        return record

    def delete_import(self, context: TenantContext, import_id: str) -> int | None:
        if self.get_import(context, import_id) is None:
            return None
        removed = self.session.execute(
            delete(FinanceTransactionRecord).where(*self._mine(FinanceTransactionRecord, context), FinanceTransactionRecord.import_id == import_id)
        ).rowcount
        self.session.execute(delete(FinanceImportRecord).where(*self._mine(FinanceImportRecord, context), FinanceImportRecord.id == import_id))
        return int(removed or 0)

    # bills, debts, goals, holdings ---------------------------------------------------------

    def list_items(self, context: TenantContext, kind: ItemKind) -> list[BaseModel]:
        record, model = ITEM_RECORDS[kind]
        order = func.lower(record.symbol) if kind == "holding" else func.lower(record.name)
        rows = self.session.scalars(select(record).where(*self._mine(record, context)).order_by(order, record.created_at))
        return [_model(row, model) for row in rows]

    def get_item(self, context: TenantContext, kind: ItemKind, item_id: str) -> BaseModel | None:
        record, model = ITEM_RECORDS[kind]
        row = self.session.scalar(select(record).where(*self._mine(record, context), record.id == item_id))
        return _model(row, model) if row else None

    def save_item(self, kind: ItemKind, item: BaseModel) -> BaseModel:
        self._upsert(ITEM_RECORDS[kind][0], item)
        return item

    def delete_item(self, context: TenantContext, kind: ItemKind, item_id: str) -> bool:
        record = ITEM_RECORDS[kind][0]
        return bool(self.session.execute(delete(record).where(*self._mine(record, context), record.id == item_id)).rowcount)

    # Stage 3: safe-to-spend settings ----------------------------------------------------

    def get_plan(self, context: TenantContext) -> PlanSettings | None:
        found = self.session.scalar(select(FinancePlanRecord).where(*self._mine(FinancePlanRecord, context)))
        if found is None:
            return None
        return PlanSettings.model_validate({
            "buffers": found.buffers, "combine_currencies": found.combine_currencies, "fx_max_age_days": found.fx_max_age_days,
            "updated_at": found.updated_at,
        })

    def save_plan(self, context: TenantContext, plan: PlanSettings) -> PlanSettings:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        values = {
            "buffers": [b.model_dump(mode="json") for b in plan.buffers], "combine_currencies": plan.combine_currencies,
            "fx_max_age_days": plan.fx_max_age_days, "updated_at": plan.updated_at or datetime.now().astimezone(),
        }
        statement = insert(FinancePlanRecord).values(
            tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, **values
        )
        self.session.execute(statement.on_conflict_do_update(index_elements=["tenant_id", "workspace_id", "owner_user_id"], set_=values))
        return plan

    # Stage 4: Guardian --------------------------------------------------------------------

    @staticmethod
    def _policy(row: FinanceGuardianVersionRecord) -> GuardianPolicy:
        return GuardianPolicy.model_validate({name: getattr(row, name) for name in GuardianPolicy.model_fields})

    @staticmethod
    def _change(row: FinanceGuardianChangeRecord) -> GuardianChange:
        return GuardianChange.model_validate({name: getattr(row, name) for name in GuardianChange.model_fields})

    def get_policy(self, context: TenantContext) -> GuardianPolicy | None:
        row = self.session.scalar(
            select(FinanceGuardianVersionRecord).where(*self._mine(FinanceGuardianVersionRecord, context))
            .order_by(FinanceGuardianVersionRecord.version.desc()).limit(1)
        )
        return self._policy(row) if row else None

    def list_policies(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[GuardianPolicy], int]:
        clauses = self._mine(FinanceGuardianVersionRecord, context)
        total = int(self.session.scalar(select(func.count()).select_from(FinanceGuardianVersionRecord).where(*clauses)) or 0)
        rows = self.session.scalars(
            select(FinanceGuardianVersionRecord).where(*clauses).order_by(FinanceGuardianVersionRecord.version.desc()).limit(limit).offset(offset)
        )
        return [self._policy(row) for row in rows], total

    def add_policy(self, policy: GuardianPolicy) -> bool:
        self._require(policy)
        values = {**_fields(policy), "rules": policy.rules.canonical()}
        inserted = self.session.execute(
            insert(FinanceGuardianVersionRecord).values(**values)
            .on_conflict_do_nothing(constraint="uq_finance_guardian_versions_version")
            .returning(FinanceGuardianVersionRecord.id)
        )
        return inserted.first() is not None

    def list_changes(self, context: TenantContext, *, status: str | None, limit: int) -> list[GuardianChange]:
        clauses = list(self._mine(FinanceGuardianChangeRecord, context))
        if status is not None:
            clauses.append(FinanceGuardianChangeRecord.status == status)
        rows = self.session.scalars(
            select(FinanceGuardianChangeRecord).where(*clauses)
            .order_by(FinanceGuardianChangeRecord.created_at.desc(), FinanceGuardianChangeRecord.id.desc()).limit(limit)
        )
        return [self._change(row) for row in rows]

    def get_change(self, context: TenantContext, change_id: str) -> GuardianChange | None:
        row = self.session.scalar(
            select(FinanceGuardianChangeRecord).where(*self._mine(FinanceGuardianChangeRecord, context), FinanceGuardianChangeRecord.id == change_id)
        )
        return self._change(row) if row else None

    def save_change(self, change: GuardianChange) -> GuardianChange:
        self._require(change)
        values = {**_fields(change), "rules": change.rules.canonical(), "loosens": list(change.loosens)}
        existing = self.session.get(FinanceGuardianChangeRecord, change.id)
        if existing is None:
            self.session.add(FinanceGuardianChangeRecord(**values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        self.session.flush()
        return change

    # Stage 5: connections and rates -------------------------------------------------------

    @staticmethod
    def _connection(row: FinanceConnectionRecord) -> Connection:
        return Connection.model_validate({name: getattr(row, name) for name in Connection.model_fields})

    def list_connections(self, context: TenantContext) -> list[Connection]:
        rows = self.session.scalars(
            select(FinanceConnectionRecord).where(*self._mine(FinanceConnectionRecord, context))
            .order_by(FinanceConnectionRecord.created_at, FinanceConnectionRecord.id)
        )
        return [self._connection(row) for row in rows]

    def get_connection(self, context: TenantContext, connection_id: str) -> Connection | None:
        row = self.session.scalar(
            select(FinanceConnectionRecord).where(*self._mine(FinanceConnectionRecord, context), FinanceConnectionRecord.id == connection_id)
        )
        return self._connection(row) if row else None

    def save_connection(self, connection: Connection) -> Connection:
        self._upsert(FinanceConnectionRecord, connection)
        return connection

    def delete_connection(self, context: TenantContext, connection_id: str) -> bool:
        # The rates it fetched cascade; transactions it imported stay in the ledger.
        result = self.session.execute(
            delete(FinanceConnectionRecord).where(*self._mine(FinanceConnectionRecord, context), FinanceConnectionRecord.id == connection_id)
        )
        return bool(result.rowcount)

    def claim_connection(self, context: TenantContext, connection_id: str, now: datetime, until: datetime) -> bool:
        record = FinanceConnectionRecord
        result = self.session.execute(
            update(record)
            .where(*self._mine(record, context), record.id == connection_id, record.enabled.is_(True), record.next_sync_at <= now)
            .values(next_sync_at=until)
        )
        return bool(result.rowcount)

    def save_fx_rates(self, rates: list[FxRate]) -> int:
        added = 0
        for start in range(0, len(rates), 500):
            chunk = rates[start : start + 500]
            for item in chunk:
                self._require(item)
            inserted = self.session.execute(
                insert(FinanceFxRateRecord).values([_fields(item) for item in chunk])
                .on_conflict_do_nothing(constraint="uq_finance_fx_rates_day").returning(FinanceFxRateRecord.id)
            )
            added += len(inserted.all())
        return added

    def list_fx_rates(self, context: TenantContext, *, limit: int) -> list[FxRate]:
        rows = self.session.scalars(
            select(FinanceFxRateRecord).where(*self._mine(FinanceFxRateRecord, context))
            .order_by(FinanceFxRateRecord.as_of.desc(), FinanceFxRateRecord.fetched_at.desc(), FinanceFxRateRecord.id.desc()).limit(limit)
        )
        return [FxRate.model_validate({name: getattr(row, name) for name in FxRate.model_fields}) for row in rows]

    def delete_fx_rate(self, context: TenantContext, rate_id: str) -> bool:
        result = self.session.execute(
            delete(FinanceFxRateRecord).where(*self._mine(FinanceFxRateRecord, context), FinanceFxRateRecord.id == rate_id)
        )
        return bool(result.rowcount)

    # Stage 6: prices, portfolios, orders --------------------------------------------------

    @staticmethod
    def _plain(row: Any, model: type[BaseModel]) -> Any:
        return model.model_validate({name: getattr(row, name) for name in model.model_fields})

    def list_prices(self, context: TenantContext, *, symbol: str | None, limit: int) -> list[PriceRecord]:
        record = FinancePriceRecord
        clauses = list(self._mine(record, context))
        if symbol is not None:
            clauses.append(record.symbol == symbol)
        rows = self.session.scalars(select(record).where(*clauses).order_by(record.as_of.desc(), record.recorded_at.desc(), record.id.desc()).limit(limit))
        return [self._plain(row, PriceRecord) for row in rows]

    def save_price(self, price: PriceRecord) -> bool:
        self._require(price)
        inserted = self.session.execute(
            insert(FinancePriceRecord).values(**_fields(price)).on_conflict_do_nothing(constraint="uq_finance_prices_day").returning(FinancePriceRecord.id)
        )
        return inserted.first() is not None

    def delete_price(self, context: TenantContext, price_id: str) -> bool:
        record = FinancePriceRecord
        return bool(self.session.execute(delete(record).where(*self._mine(record, context), record.id == price_id)).rowcount)

    def _portfolio(self, row: FinancePaperPortfolioRecord) -> Portfolio:
        values = {name: getattr(row, name) for name in Portfolio.model_fields}
        values["fee_min"] = Decimal(values["fee_min"]).quantize(Decimal("0.01"))
        return Portfolio.model_validate(values)

    def list_portfolios(self, context: TenantContext) -> list[Portfolio]:
        record = FinancePaperPortfolioRecord
        rows = self.session.scalars(select(record).where(*self._mine(record, context)).order_by(record.created_at, record.id))
        return [self._portfolio(row) for row in rows]

    def get_portfolio(self, context: TenantContext, portfolio_id: str) -> Portfolio | None:
        record = FinancePaperPortfolioRecord
        row = self.session.scalar(select(record).where(*self._mine(record, context), record.id == portfolio_id))
        return self._portfolio(row) if row else None

    def save_portfolio(self, portfolio: Portfolio) -> Portfolio:
        self._require(portfolio)
        values = {**_fields(portfolio), "starting_cash": [line.model_dump(mode="json") for line in portfolio.starting_cash]}
        existing = self.session.get(FinancePaperPortfolioRecord, portfolio.id)
        if existing is None:
            self.session.add(FinancePaperPortfolioRecord(**values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        self.session.flush()
        return portfolio

    def delete_portfolio(self, context: TenantContext, portfolio_id: str) -> bool:
        # Its orders cascade; the execution requests made for it stay as history.
        record = FinancePaperPortfolioRecord
        return bool(self.session.execute(delete(record).where(*self._mine(record, context), record.id == portfolio_id)).rowcount)

    def _order(self, row: FinancePaperOrderRecord) -> PaperOrder:
        return _model(row, PaperOrder)

    def list_orders(self, context: TenantContext, portfolio_id: str, *, epoch: int | None, status: str | None, limit: int, offset: int) -> tuple[list[PaperOrder], int]:
        record = FinancePaperOrderRecord
        clauses = [*self._mine(record, context), record.portfolio_id == portfolio_id]
        if epoch is not None:
            clauses.append(record.epoch == epoch)
        if status is not None:
            clauses.append(record.status == status)
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*clauses)) or 0)
        rows = self.session.scalars(select(record).where(*clauses).order_by(record.created_at.desc(), record.id.desc()).limit(limit).offset(offset))
        return [self._order(row) for row in rows], total

    def portfolio_orders(self, context: TenantContext, portfolio_id: str, epoch: int) -> list[PaperOrder]:
        record = FinancePaperOrderRecord
        rows = self.session.scalars(
            select(record).where(*self._mine(record, context), record.portfolio_id == portfolio_id, record.epoch == epoch)
            .order_by(record.created_at, record.id).limit(MAX_ORDERS)
        )
        return [self._order(row) for row in rows]

    def get_order(self, context: TenantContext, order_id: str) -> PaperOrder | None:
        record = FinancePaperOrderRecord
        row = self.session.scalar(select(record).where(*self._mine(record, context), record.id == order_id))
        return self._order(row) if row else None

    def order_for_intent(self, context: TenantContext, intent_id: str) -> PaperOrder | None:
        record = FinancePaperOrderRecord
        row = self.session.scalar(select(record).where(*self._mine(record, context), record.intent_id == intent_id))
        return self._order(row) if row else None

    def add_order(self, order: PaperOrder) -> bool:
        self._require(order)
        inserted = self.session.execute(
            insert(FinancePaperOrderRecord).values(**_fields(order))
            .on_conflict_do_nothing(constraint="uq_finance_paper_orders_intent").returning(FinancePaperOrderRecord.id)
        )
        return inserted.first() is not None

    def save_order(self, order: PaperOrder) -> PaperOrder:
        self._upsert(FinancePaperOrderRecord, order)
        return order

    # Stage 7 and 8: intents, kill switches, live settings ------------------------------

    def _intent(self, row: FinanceExecIntentRecord) -> Intent:
        values = {name: getattr(row, name) for name in Intent.model_fields}
        if values.get("amount") is not None and values.get("currency"):
            values["amount"] = quantize(Decimal(values["amount"]), values["currency"])
        return Intent.model_validate(values)

    def list_intents(self, context: TenantContext, *, status: str | None, mode: str | None, limit: int, offset: int) -> tuple[list[Intent], int]:
        record = FinanceExecIntentRecord
        clauses = list(self._mine(record, context))
        if status is not None:
            clauses.append(record.status == status)
        if mode is not None:
            clauses.append(record.mode == mode)
        total = int(self.session.scalar(select(func.count()).select_from(record).where(*clauses)) or 0)
        rows = self.session.scalars(select(record).where(*clauses).order_by(record.created_at.desc(), record.id.desc()).limit(limit).offset(offset))
        return [self._intent(row) for row in rows], total

    def get_intent(self, context: TenantContext, intent_id: str) -> Intent | None:
        record = FinanceExecIntentRecord
        row = self.session.scalar(select(record).where(*self._mine(record, context), record.id == intent_id))
        return self._intent(row) if row else None

    def intent_by_key(self, context: TenantContext, key: str) -> Intent | None:
        record = FinanceExecIntentRecord
        row = self.session.scalar(select(record).where(*self._mine(record, context), record.idempotency_key == key))
        return self._intent(row) if row else None

    def save_intent(self, intent: Intent) -> bool:
        self._require(intent)
        values = _fields(intent)
        existing = self.session.get(FinanceExecIntentRecord, intent.id)
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            self.session.flush()
            return True
        inserted = self.session.execute(
            insert(FinanceExecIntentRecord).values(**values)
            .on_conflict_do_nothing(constraint="uq_finance_exec_intents_key").returning(FinanceExecIntentRecord.id)
        )
        return inserted.first() is not None

    def claim_intent(self, context: TenantContext, intent_id: str, now: datetime, approver: str) -> bool:
        """Pending to executing, once: the approval is used by exactly one caller."""
        record = FinanceExecIntentRecord
        result = self.session.execute(
            update(record)
            .where(*self._mine(record, context), record.id == intent_id, record.status == IntentStatus.PENDING.value,
                   record.expires_at > now, record.owner_user_id == approver)
            .values(status=IntentStatus.EXECUTING.value, approved_at=now, approved_by=approver, updated_at=now)
        )
        return bool(result.rowcount)

    def get_exec_settings(self, context: TenantContext) -> ExecSettings | None:
        row = self.session.scalar(select(FinanceExecSettingsRecord).where(*self._mine(FinanceExecSettingsRecord, context)))
        if row is None:
            return None
        return ExecSettings.model_validate({name: getattr(row, name) for name in ExecSettings.model_fields})

    def save_exec_settings(self, context: TenantContext, settings: ExecSettings) -> ExecSettings:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        values = {**settings.model_dump(mode="json", exclude={"live_limits", "updated_at", "kill_switch_at", "live_opt_in_at"}),
                  "kill_switch_at": settings.kill_switch_at, "live_opt_in_at": settings.live_opt_in_at, "wider": settings.wider.model_dump(mode="json"),
                  "live_limits": settings.live_limits.model_dump(mode="json"), "updated_at": settings.updated_at or datetime.now().astimezone()}
        statement = insert(FinanceExecSettingsRecord).values(
            tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, **values
        )
        self.session.execute(statement.on_conflict_do_update(index_elements=["tenant_id", "workspace_id", "owner_user_id"], set_=values))
        return settings

    def get_global_switch(self, context: TenantContext) -> GlobalSwitch | None:
        row = self.session.scalar(select(FinanceExecGlobalRecord).where(FinanceExecGlobalRecord.tenant_id == context.tenant_id))
        if row is None:
            return None
        return GlobalSwitch(engaged=row.engaged, changed_by=row.changed_by, changed_at=row.changed_at, reason=row.reason)

    def save_global_switch(self, context: TenantContext, switch: GlobalSwitch) -> GlobalSwitch:
        values = switch.model_dump()
        statement = insert(FinanceExecGlobalRecord).values(tenant_id=context.tenant_id, **values)
        self.session.execute(statement.on_conflict_do_update(index_elements=["tenant_id"], set_=values))
        return switch

    # Stage 9: widenings and incidents -----------------------------------------------------

    def list_widenings(self, context: TenantContext, *, status: str | None, limit: int) -> list[Widening]:
        record = FinanceLiveWideningRecord
        clauses = list(self._mine(record, context))
        if status is not None:
            clauses.append(record.status == status)
        rows = self.session.scalars(select(record).where(*clauses).order_by(record.created_at.desc(), record.id.desc()).limit(limit))
        return [self._plain(row, Widening) for row in rows]

    def get_widening(self, context: TenantContext, widening_id: str) -> Widening | None:
        record = FinanceLiveWideningRecord
        row = self.session.scalar(select(record).where(*self._mine(record, context), record.id == widening_id))
        return self._plain(row, Widening) if row else None

    def save_widening(self, widening: Widening) -> Widening:
        self._require(widening)
        values = {**_fields(widening), "limits": widening.limits.model_dump(mode="json"), "kinds": list(widening.kinds)}
        existing = self.session.get(FinanceLiveWideningRecord, widening.id)
        if existing is None:
            self.session.add(FinanceLiveWideningRecord(**values))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        self.session.flush()
        return widening

    def claim_widening(self, context: TenantContext, widening_id: str, now: datetime, approver: str, effective_at: datetime) -> bool:
        record = FinanceLiveWideningRecord
        result = self.session.execute(
            update(record)
            .where(*self._mine(record, context), record.id == widening_id, record.status == "pending", record.expires_at > now,
                   record.owner_user_id == approver)
            .values(status="approved", decided_at=now, decided_by=approver, effective_at=effective_at)
        )
        return bool(result.rowcount)

    def list_incidents(self, context: TenantContext, *, limit: int) -> list[Incident]:
        record = FinanceLiveIncidentRecord
        rows = self.session.scalars(select(record).where(*self._mine(record, context)).order_by(record.created_at.desc(), record.id.desc()).limit(limit))
        return [self._plain(row, Incident) for row in rows]

    def save_incident(self, incident: Incident) -> Incident:
        self._upsert(FinanceLiveIncidentRecord, incident)
        return incident

    # privacy ------------------------------------------------------------------------------

    _ALL = (
        FinanceLiveIncidentRecord, FinanceLiveWideningRecord, FinancePaperOrderRecord, FinancePaperPortfolioRecord, FinancePriceRecord, FinanceExecIntentRecord, FinanceExecSettingsRecord,
        FinanceTransactionRecord, FinanceImportRecord, FinanceHoldingRecord, FinanceBillRecord, FinanceDebtRecord,
        FinanceGoalRecord, FinanceFxRateRecord, FinanceConnectionRecord, FinanceGuardianChangeRecord,
        FinanceGuardianVersionRecord, FinancePlanRecord, FinanceAccountRecord, FinanceSettingsRecord,
    )

    def count_everything(self, context: TenantContext) -> int:
        return sum(
            int(self.session.scalar(select(func.count()).select_from(record).where(*self._mine(record, context))) or 0)
            for record in self._ALL
        )

    def delete_everything(self, context: TenantContext) -> int:
        removed = 0
        for record in self._ALL:
            removed += int(self.session.execute(delete(record).where(*self._mine(record, context))).rowcount or 0)
        return removed


__all__ = ["SqlAlchemyFinanceStore"]
