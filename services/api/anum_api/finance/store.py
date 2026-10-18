"""Finance storage: the protocol, the in-memory store and ``open_finance_store``.

With ``ANUM_REPOSITORY_BACKEND=postgresql`` the store is
``anum_api.db.finance_repository.SqlAlchemyFinanceStore`` in a session whose tenant,
workspace **and user** RLS context is set (``open_scoped_store(..., user_scoped=True)``):
every finance table has forced RLS with the tenant policy and a restrictive owner policy
(migration ``0056_finance_ledger``), and every statement repeats the scope and the owner.
The in-memory store serves local development and tests with the same rules.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from threading import RLock
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .connectors import Connection, ConnectionStatus
from .execution import ExecSettings, GlobalSwitch, Intent, IntentStatus
from .guardian import GuardianChange, GuardianPolicy
from .intel.marketdata import Instrument, StoredCandle
from .intel.records import AgentDecision, StrategyConfig, StrategyRun
from .models import (
    Account,
    Bill,
    Category,
    Debt,
    FinanceSettings,
    FxRate,
    Goal,
    Holding,
    ImportRecord,
    PlanSettings,
    Transaction,
)
from .trading import PaperOrder, Portfolio, PriceRecord
from .wider import Incident, Widening

MAX_PAGE = 200
MAX_ACCOUNTS = 100
MAX_ITEMS = 500  # bills, debts, goals and holdings, each
MAX_SUMMARY_ROWS = 50_000
MAX_CONNECTIONS = 20
MAX_FX_RATES = 5_000
# Stages 6 to 8: recorded prices, paper and shadow portfolios, orders and execution intents.
MAX_PRICES = 5_000
MAX_PORTFOLIOS = 20
MAX_ORDERS = 5_000  # per portfolio, every run together

ItemKind = Literal["bill", "debt", "goal", "holding"]
ITEM_TYPES: dict[str, type[BaseModel]] = {"bill": Bill, "debt": Debt, "goal": Goal, "holding": Holding}
ItemT = TypeVar("ItemT", Bill, Debt, Goal, Holding)


class TransactionFilter(BaseModel):
    account_id: str | None = None
    category: Category | None = None
    query: str | None = None
    start: date | None = None  # inclusive
    end: date | None = None  # inclusive
    direction: Literal["in", "out"] | None = None


class FinanceStore(Protocol):
    def get_settings(self, context: TenantContext) -> FinanceSettings | None: ...
    def save_settings(self, context: TenantContext, settings: FinanceSettings) -> FinanceSettings: ...
    # accounts
    def list_accounts(self, context: TenantContext) -> list[Account]: ...
    def get_account(self, context: TenantContext, account_id: str) -> Account | None: ...
    def save_account(self, account: Account) -> Account: ...
    def delete_account(self, context: TenantContext, account_id: str) -> bool: ...
    def account_sums(self, context: TenantContext) -> dict[str, Decimal]: ...
    # transactions
    def list_transactions(self, context: TenantContext, flt: TransactionFilter, *, limit: int, offset: int) -> tuple[list[Transaction], int]: ...
    def get_transaction(self, context: TenantContext, transaction_id: str) -> Transaction | None: ...
    def save_transaction(self, transaction: Transaction) -> Transaction: ...
    def delete_transaction(self, context: TenantContext, transaction_id: str) -> bool: ...
    def existing_hashes(self, context: TenantContext, account_id: str, hashes: list[str]) -> set[str]: ...
    def add_transactions(self, transactions: list[Transaction]) -> int: ...
    def transactions_between(self, context: TenantContext, start: date, end: date) -> list[Transaction]: ...
    # imports
    def list_imports(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[ImportRecord], int]: ...
    def get_import(self, context: TenantContext, import_id: str) -> ImportRecord | None: ...
    def save_import(self, record: ImportRecord) -> ImportRecord: ...
    def delete_import(self, context: TenantContext, import_id: str) -> int | None: ...
    # bills, debts, goals and holdings
    def list_items(self, context: TenantContext, kind: ItemKind) -> list[BaseModel]: ...
    def get_item(self, context: TenantContext, kind: ItemKind, item_id: str) -> BaseModel | None: ...
    def save_item(self, kind: ItemKind, item: BaseModel) -> BaseModel: ...
    def delete_item(self, context: TenantContext, kind: ItemKind, item_id: str) -> bool: ...
    # Stage 3: safe-to-spend settings
    def get_plan(self, context: TenantContext) -> PlanSettings | None: ...
    def save_plan(self, context: TenantContext, plan: PlanSettings) -> PlanSettings: ...
    # Stage 4: Guardian rule versions and proposed changes
    def get_policy(self, context: TenantContext) -> GuardianPolicy | None: ...
    def list_policies(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[GuardianPolicy], int]: ...
    def add_policy(self, policy: GuardianPolicy) -> bool: ...
    def list_changes(self, context: TenantContext, *, status: str | None, limit: int) -> list[GuardianChange]: ...
    def get_change(self, context: TenantContext, change_id: str) -> GuardianChange | None: ...
    def save_change(self, change: GuardianChange) -> GuardianChange: ...
    # Stage 5: connections and exchange rates
    def list_connections(self, context: TenantContext) -> list[Connection]: ...
    def get_connection(self, context: TenantContext, connection_id: str) -> Connection | None: ...
    def save_connection(self, connection: Connection) -> Connection: ...
    def delete_connection(self, context: TenantContext, connection_id: str) -> bool: ...
    def claim_connection(self, context: TenantContext, connection_id: str, now: datetime, until: datetime) -> bool: ...
    def save_fx_rates(self, rates: list[FxRate]) -> int: ...
    def list_fx_rates(self, context: TenantContext, *, limit: int) -> list[FxRate]: ...
    def delete_fx_rate(self, context: TenantContext, rate_id: str) -> bool: ...
    # Stage 6: recorded prices, paper and shadow portfolios and their orders
    def list_prices(self, context: TenantContext, *, symbol: str | None, limit: int) -> list[PriceRecord]: ...
    def save_price(self, price: PriceRecord) -> bool: ...
    def delete_price(self, context: TenantContext, price_id: str) -> bool: ...
    def list_portfolios(self, context: TenantContext) -> list[Portfolio]: ...
    def get_portfolio(self, context: TenantContext, portfolio_id: str) -> Portfolio | None: ...
    def save_portfolio(self, portfolio: Portfolio) -> Portfolio: ...
    def delete_portfolio(self, context: TenantContext, portfolio_id: str) -> bool: ...
    def list_orders(self, context: TenantContext, portfolio_id: str, *, epoch: int | None, status: str | None, limit: int, offset: int) -> tuple[list[PaperOrder], int]: ...
    def portfolio_orders(self, context: TenantContext, portfolio_id: str, epoch: int) -> list[PaperOrder]: ...
    def get_order(self, context: TenantContext, order_id: str) -> PaperOrder | None: ...
    def order_for_intent(self, context: TenantContext, intent_id: str) -> PaperOrder | None: ...
    def add_order(self, order: PaperOrder) -> bool: ...
    def save_order(self, order: PaperOrder) -> PaperOrder: ...
    # Stage 7: execution intents (one approval each), kill switches; stage 8: live settings
    def list_intents(self, context: TenantContext, *, status: str | None, mode: str | None, limit: int, offset: int) -> tuple[list[Intent], int]: ...
    def get_intent(self, context: TenantContext, intent_id: str) -> Intent | None: ...
    def intent_by_key(self, context: TenantContext, key: str) -> Intent | None: ...
    def save_intent(self, intent: Intent) -> bool: ...
    def claim_intent(self, context: TenantContext, intent_id: str, now: datetime, approver: str) -> bool: ...
    def get_exec_settings(self, context: TenantContext) -> ExecSettings | None: ...
    def save_exec_settings(self, context: TenantContext, settings: ExecSettings) -> ExecSettings: ...
    def get_global_switch(self, context: TenantContext) -> GlobalSwitch | None: ...
    def save_global_switch(self, context: TenantContext, switch: GlobalSwitch) -> GlobalSwitch: ...
    # Stage 9: widening proposals and incidents
    def list_widenings(self, context: TenantContext, *, status: str | None, limit: int) -> list[Widening]: ...
    def get_widening(self, context: TenantContext, widening_id: str) -> Widening | None: ...
    def save_widening(self, widening: Widening) -> Widening: ...
    def claim_widening(self, context: TenantContext, widening_id: str, now: datetime, approver: str, effective_at: datetime) -> bool: ...
    def list_incidents(self, context: TenantContext, *, limit: int) -> list[Incident]: ...
    def save_incident(self, incident: Incident) -> Incident: ...
    # Trading intelligence (migration 0060_finance_intel): instruments, daily bars, strategy
    # versions, their evaluation runs and the agents' decisions. Lists are newest first except bars.
    def list_instruments(self, context: TenantContext) -> list[Instrument]: ...
    def get_instrument(self, context: TenantContext, instrument_id: str) -> Instrument | None: ...
    def save_instrument(self, instrument: Instrument) -> Instrument: ...
    def delete_instrument(self, context: TenantContext, instrument_id: str) -> bool: ...
    def save_bars(self, bars: list[StoredCandle]) -> int: ...
    def list_bars(self, context: TenantContext, instrument_id: str, *, start: date | None = None, end: date | None = None,
                  limit: int = 10_000) -> list[StoredCandle]: ...
    def list_strategies(self, context: TenantContext, *, state: str | None = None, lineage_id: str | None = None, limit: int,
                        offset: int) -> tuple[list[StrategyConfig], int]: ...
    def get_strategy(self, context: TenantContext, strategy_id: str) -> StrategyConfig | None: ...
    def latest_version(self, context: TenantContext, lineage_id: str) -> int: ...
    def save_strategy(self, strategy: StrategyConfig) -> StrategyConfig: ...
    def list_strategy_runs(self, context: TenantContext, strategy_id: str, *, limit: int, offset: int) -> tuple[list[StrategyRun], int]: ...
    def get_strategy_run(self, context: TenantContext, run_id: str) -> StrategyRun | None: ...
    def save_strategy_run(self, run: StrategyRun) -> StrategyRun: ...
    def list_agent_decisions(self, context: TenantContext, *, agent: str | None = None, strategy_id: str | None = None,
                             cycle_id: str | None = None, limit: int, offset: int) -> tuple[list[AgentDecision], int]: ...
    def save_agent_decision(self, decision: AgentDecision) -> AgentDecision: ...
    # privacy: every finance row of the caller
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def mine(item: object, context: TenantContext) -> bool:
    return (
        item.tenant_id == context.tenant_id
        and item.workspace_id == context.workspace_id
        and item.owner_user_id == context.user_id
    )


def _ctx(item: object) -> TenantContext:
    return TenantContext(tenant_id=item.tenant_id, workspace_id=item.workspace_id, user_id=item.owner_user_id)  # type: ignore[attr-defined]


def transaction_matches(item: Transaction, flt: TransactionFilter) -> bool:
    if flt.account_id and item.account_id != flt.account_id:
        return False
    if flt.category and item.category != flt.category:
        return False
    if flt.start and item.posted_on < flt.start:
        return False
    if flt.end and item.posted_on > flt.end:
        return False
    if flt.direction == "in" and item.amount <= 0:
        return False
    if flt.direction == "out" and item.amount >= 0:
        return False
    if flt.query:
        needle = flt.query.strip().lower()
        if needle and needle not in item.description.lower() and needle not in (item.notes or "").lower():
            return False
    return True


class InMemoryFinanceStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.clear()

    def clear(self) -> None:
        self.settings: dict[tuple[str, str, str], FinanceSettings] = {}
        self.accounts: dict[str, Account] = {}
        self.transactions: dict[str, Transaction] = {}
        self.imports: dict[str, ImportRecord] = {}
        self.items: dict[str, dict[str, BaseModel]] = {kind: {} for kind in ITEM_TYPES}
        self.plans: dict[tuple[str, str, str], PlanSettings] = {}
        self.policies: dict[str, GuardianPolicy] = {}
        self.changes: dict[str, GuardianChange] = {}
        self.connections: dict[str, Connection] = {}
        self.fx_rates: dict[str, FxRate] = {}
        self.prices: dict[str, PriceRecord] = {}
        self.portfolios: dict[str, Portfolio] = {}
        self.orders: dict[str, PaperOrder] = {}
        self.intents: dict[str, Intent] = {}
        self.exec_settings: dict[tuple[str, str, str], ExecSettings] = {}
        self.global_switches: dict[str, GlobalSwitch] = {}
        self.widenings: dict[str, Widening] = {}
        self.incidents: dict[str, Incident] = {}
        self.instruments: dict[str, Instrument] = {}
        self.bars: dict[str, StoredCandle] = {}
        self.strategies: dict[str, StrategyConfig] = {}
        self.strategy_runs: dict[str, StrategyRun] = {}
        self.decisions: dict[str, AgentDecision] = {}

    @staticmethod
    def _key(context: TenantContext) -> tuple[str, str, str]:
        return context.tenant_id, context.workspace_id, context.user_id

    def get_settings(self, context: TenantContext) -> FinanceSettings | None:
        with self._lock:
            found = self.settings.get(self._key(context))
            return found.model_copy(deep=True) if found else None

    def save_settings(self, context: TenantContext, settings: FinanceSettings) -> FinanceSettings:
        with self._lock:
            self.settings[self._key(context)] = settings.model_copy(deep=True)
            return settings

    # accounts -----------------------------------------------------------------------------

    def list_accounts(self, context: TenantContext) -> list[Account]:
        with self._lock:
            found = [a.model_copy() for a in self.accounts.values() if mine(a, context)]
            return sorted(found, key=lambda a: (a.archived, a.name.lower(), a.created_at))

    def get_account(self, context: TenantContext, account_id: str) -> Account | None:
        with self._lock:
            found = self.accounts.get(account_id)
            return found.model_copy() if found and mine(found, context) else None

    def save_account(self, account: Account) -> Account:
        with self._lock:
            self.accounts[account.id] = account.model_copy()
            return account

    def delete_account(self, context: TenantContext, account_id: str) -> bool:
        with self._lock:
            found = self.accounts.get(account_id)
            if found is None or not mine(found, context):
                return False
            del self.accounts[account_id]
            for table in (self.transactions, self.imports, self.items["holding"]):
                for key in [k for k, v in table.items() if getattr(v, "account_id", None) == account_id]:
                    del table[key]
            for kind in ("bill", "debt", "goal"):
                for item in self.items[kind].values():
                    if getattr(item, "account_id", None) == account_id:
                        item.account_id = None
            return True

    def account_sums(self, context: TenantContext) -> dict[str, Decimal]:
        with self._lock:
            sums: dict[str, Decimal] = {}
            for item in self.transactions.values():
                if mine(item, context):
                    sums[item.account_id] = sums.get(item.account_id, Decimal(0)) + item.amount
            return sums

    # transactions -------------------------------------------------------------------------

    def list_transactions(self, context: TenantContext, flt: TransactionFilter, *, limit: int, offset: int) -> tuple[list[Transaction], int]:
        with self._lock:
            found = [t for t in self.transactions.values() if mine(t, context) and transaction_matches(t, flt)]
            found.sort(key=lambda t: (t.posted_on, t.created_at, t.id), reverse=True)
            return [t.model_copy() for t in found[offset : offset + limit]], len(found)

    def get_transaction(self, context: TenantContext, transaction_id: str) -> Transaction | None:
        with self._lock:
            found = self.transactions.get(transaction_id)
            return found.model_copy() if found and mine(found, context) else None

    def save_transaction(self, transaction: Transaction) -> Transaction:
        with self._lock:
            self.transactions[transaction.id] = transaction.model_copy()
            return transaction

    def delete_transaction(self, context: TenantContext, transaction_id: str) -> bool:
        with self._lock:
            found = self.transactions.get(transaction_id)
            if found is None or not mine(found, context):
                return False
            del self.transactions[transaction_id]
            return True

    def existing_hashes(self, context: TenantContext, account_id: str, hashes: list[str]) -> set[str]:
        wanted = set(hashes)
        with self._lock:
            return {
                t.dedupe_hash
                for t in self.transactions.values()
                if t.account_id == account_id and t.dedupe_hash in wanted and mine(t, context)
            }

    def add_transactions(self, transactions: list[Transaction]) -> int:
        with self._lock:
            taken = {(t.account_id, t.dedupe_hash) for t in self.transactions.values()}
            added = 0
            for item in transactions:
                if (item.account_id, item.dedupe_hash) in taken:
                    continue
                taken.add((item.account_id, item.dedupe_hash))
                self.transactions[item.id] = item.model_copy()
                added += 1
            return added

    def transactions_between(self, context: TenantContext, start: date, end: date) -> list[Transaction]:
        with self._lock:
            found = [t.model_copy() for t in self.transactions.values() if mine(t, context) and start <= t.posted_on <= end]
            return found[:MAX_SUMMARY_ROWS]

    # imports ------------------------------------------------------------------------------

    def list_imports(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[ImportRecord], int]:
        with self._lock:
            found = sorted((i for i in self.imports.values() if mine(i, context)), key=lambda i: (i.created_at, i.id), reverse=True)
            return [i.model_copy() for i in found[offset : offset + limit]], len(found)

    def get_import(self, context: TenantContext, import_id: str) -> ImportRecord | None:
        with self._lock:
            found = self.imports.get(import_id)
            return found.model_copy() if found and mine(found, context) else None

    def save_import(self, record: ImportRecord) -> ImportRecord:
        with self._lock:
            self.imports[record.id] = record.model_copy()
            return record

    def delete_import(self, context: TenantContext, import_id: str) -> int | None:
        with self._lock:
            found = self.imports.get(import_id)
            if found is None or not mine(found, context):
                return None
            del self.imports[import_id]
            keys = [k for k, t in self.transactions.items() if t.import_id == import_id and mine(t, context)]
            for key in keys:
                del self.transactions[key]
            return len(keys)

    # bills, debts, goals, holdings ---------------------------------------------------------

    def list_items(self, context: TenantContext, kind: ItemKind) -> list[BaseModel]:
        with self._lock:
            found = [i.model_copy() for i in self.items[kind].values() if mine(i, context)]
            return sorted(found, key=lambda i: (str(getattr(i, "name", "") or getattr(i, "symbol", "")).lower(), i.created_at))

    def get_item(self, context: TenantContext, kind: ItemKind, item_id: str) -> BaseModel | None:
        with self._lock:
            found = self.items[kind].get(item_id)
            return found.model_copy() if found and mine(found, context) else None

    def save_item(self, kind: ItemKind, item: BaseModel) -> BaseModel:
        with self._lock:
            self.items[kind][item.id] = item.model_copy()
            return item

    def delete_item(self, context: TenantContext, kind: ItemKind, item_id: str) -> bool:
        with self._lock:
            found = self.items[kind].get(item_id)
            if found is None or not mine(found, context):
                return False
            del self.items[kind][item_id]
            return True

    # Stage 3: safe-to-spend settings ---------------------------------------------------

    def get_plan(self, context: TenantContext) -> PlanSettings | None:
        with self._lock:
            found = self.plans.get(self._key(context))
            return found.model_copy(deep=True) if found else None

    def save_plan(self, context: TenantContext, plan: PlanSettings) -> PlanSettings:
        with self._lock:
            self.plans[self._key(context)] = plan.model_copy(deep=True)
            return plan

    # Stage 4: Guardian -------------------------------------------------------------------

    def get_policy(self, context: TenantContext) -> GuardianPolicy | None:
        with self._lock:
            found = [p for p in self.policies.values() if mine(p, context)]
            return max(found, key=lambda p: p.version).model_copy(deep=True) if found else None

    def list_policies(self, context: TenantContext, *, limit: int, offset: int) -> tuple[list[GuardianPolicy], int]:
        with self._lock:
            found = sorted((p for p in self.policies.values() if mine(p, context)), key=lambda p: p.version, reverse=True)
            return [p.model_copy(deep=True) for p in found[offset : offset + limit]], len(found)

    def add_policy(self, policy: GuardianPolicy) -> bool:
        with self._lock:
            if any(mine(p, _ctx(policy)) and p.version == policy.version for p in self.policies.values()):
                return False
            self.policies[policy.id] = policy.model_copy(deep=True)
            return True

    def list_changes(self, context: TenantContext, *, status: str | None, limit: int) -> list[GuardianChange]:
        with self._lock:
            found = [c for c in self.changes.values() if mine(c, context) and (status is None or c.status == status)]
            found.sort(key=lambda c: (c.created_at, c.id), reverse=True)
            return [c.model_copy(deep=True) for c in found[:limit]]

    def get_change(self, context: TenantContext, change_id: str) -> GuardianChange | None:
        with self._lock:
            found = self.changes.get(change_id)
            return found.model_copy(deep=True) if found and mine(found, context) else None

    def save_change(self, change: GuardianChange) -> GuardianChange:
        with self._lock:
            self.changes[change.id] = change.model_copy(deep=True)
            return change

    # Stage 5: connections and rates ------------------------------------------------------

    def list_connections(self, context: TenantContext) -> list[Connection]:
        with self._lock:
            found = [c.model_copy(deep=True) for c in self.connections.values() if mine(c, context)]
            return sorted(found, key=lambda c: (c.created_at, c.id))

    def get_connection(self, context: TenantContext, connection_id: str) -> Connection | None:
        with self._lock:
            found = self.connections.get(connection_id)
            return found.model_copy(deep=True) if found and mine(found, context) else None

    def save_connection(self, connection: Connection) -> Connection:
        with self._lock:
            self.connections[connection.id] = connection.model_copy(deep=True)
            return connection

    def delete_connection(self, context: TenantContext, connection_id: str) -> bool:
        with self._lock:
            found = self.connections.get(connection_id)
            if found is None or not mine(found, context):
                return False
            del self.connections[connection_id]
            for key in [k for k, r in self.fx_rates.items() if r.connection_id == connection_id]:
                del self.fx_rates[key]
            return True

    def claim_connection(self, context: TenantContext, connection_id: str, now: datetime, until: datetime) -> bool:
        with self._lock:
            found = self.connections.get(connection_id)
            if found is None or not mine(found, context) or not found.enabled or found.next_sync_at > now:
                return False
            found.next_sync_at = until
            return True

    def due_connections(self, now: datetime, limit: int) -> list[tuple[str, str, str, str]]:
        with self._lock:
            due = sorted(
                (c for c in self.connections.values()
                 if c.enabled and c.status != ConnectionStatus.DISCONNECTED and c.next_sync_at <= now),
                key=lambda c: c.next_sync_at,
            )
            return [(c.tenant_id, c.workspace_id, c.owner_user_id, c.id) for c in due[:limit]]

    def save_fx_rates(self, rates: list[FxRate]) -> int:
        with self._lock:
            taken = {(r.tenant_id, r.workspace_id, r.owner_user_id, r.base, r.quote, r.as_of, r.source) for r in self.fx_rates.values()}
            added = 0
            for rate in rates:
                key = (rate.tenant_id, rate.workspace_id, rate.owner_user_id, rate.base, rate.quote, rate.as_of, rate.source)
                if key in taken:
                    continue
                taken.add(key)
                self.fx_rates[rate.id] = rate.model_copy()
                added += 1
            return added

    def list_fx_rates(self, context: TenantContext, *, limit: int) -> list[FxRate]:
        with self._lock:
            found = sorted((r for r in self.fx_rates.values() if mine(r, context)), key=lambda r: (r.as_of, r.fetched_at, r.id), reverse=True)
            return [r.model_copy() for r in found[:limit]]

    def delete_fx_rate(self, context: TenantContext, rate_id: str) -> bool:
        with self._lock:
            found = self.fx_rates.get(rate_id)
            if found is None or not mine(found, context):
                return False
            del self.fx_rates[rate_id]
            return True

    # Stage 6: prices, portfolios, orders ---------------------------------------------------

    def list_prices(self, context: TenantContext, *, symbol: str | None, limit: int) -> list[PriceRecord]:
        with self._lock:
            found = [p for p in self.prices.values() if mine(p, context) and (symbol is None or p.symbol == symbol)]
            found.sort(key=lambda p: (p.as_of, p.recorded_at, p.id), reverse=True)
            return [p.model_copy() for p in found[:limit]]

    def save_price(self, price: PriceRecord) -> bool:
        with self._lock:
            key = (price.tenant_id, price.workspace_id, price.owner_user_id, price.symbol, price.currency, price.as_of, price.source)
            if any((p.tenant_id, p.workspace_id, p.owner_user_id, p.symbol, p.currency, p.as_of, p.source) == key for p in self.prices.values()):
                return False
            self.prices[price.id] = price.model_copy()
            return True

    def delete_price(self, context: TenantContext, price_id: str) -> bool:
        with self._lock:
            found = self.prices.get(price_id)
            if found is None or not mine(found, context):
                return False
            del self.prices[price_id]
            return True

    def list_portfolios(self, context: TenantContext) -> list[Portfolio]:
        with self._lock:
            found = [p.model_copy(deep=True) for p in self.portfolios.values() if mine(p, context)]
            return sorted(found, key=lambda p: (p.created_at, p.id))

    def get_portfolio(self, context: TenantContext, portfolio_id: str) -> Portfolio | None:
        with self._lock:
            found = self.portfolios.get(portfolio_id)
            return found.model_copy(deep=True) if found and mine(found, context) else None

    def save_portfolio(self, portfolio: Portfolio) -> Portfolio:
        with self._lock:
            self.portfolios[portfolio.id] = portfolio.model_copy(deep=True)
            return portfolio

    def delete_portfolio(self, context: TenantContext, portfolio_id: str) -> bool:
        with self._lock:
            found = self.portfolios.get(portfolio_id)
            if found is None or not mine(found, context):
                return False
            del self.portfolios[portfolio_id]
            for key in [k for k, o in self.orders.items() if o.portfolio_id == portfolio_id]:
                del self.orders[key]
            return True

    def list_orders(self, context: TenantContext, portfolio_id: str, *, epoch: int | None, status: str | None, limit: int, offset: int) -> tuple[list[PaperOrder], int]:
        with self._lock:
            found = [o for o in self.orders.values() if mine(o, context) and o.portfolio_id == portfolio_id
                     and (epoch is None or o.epoch == epoch) and (status is None or o.status == status)]
            found.sort(key=lambda o: (o.created_at, o.id), reverse=True)
            return [o.model_copy() for o in found[offset : offset + limit]], len(found)

    def portfolio_orders(self, context: TenantContext, portfolio_id: str, epoch: int) -> list[PaperOrder]:
        with self._lock:
            found = [o.model_copy() for o in self.orders.values() if mine(o, context) and o.portfolio_id == portfolio_id and o.epoch == epoch]
            return sorted(found, key=lambda o: (o.created_at, o.id))[:MAX_ORDERS]

    def get_order(self, context: TenantContext, order_id: str) -> PaperOrder | None:
        with self._lock:
            found = self.orders.get(order_id)
            return found.model_copy() if found and mine(found, context) else None

    def order_for_intent(self, context: TenantContext, intent_id: str) -> PaperOrder | None:
        with self._lock:
            found = next((o for o in self.orders.values() if o.intent_id == intent_id and mine(o, context)), None)
            return found.model_copy() if found else None

    def add_order(self, order: PaperOrder) -> bool:
        with self._lock:
            if any(o.intent_id == order.intent_id for o in self.orders.values()):
                return False
            self.orders[order.id] = order.model_copy()
            return True

    def save_order(self, order: PaperOrder) -> PaperOrder:
        with self._lock:
            self.orders[order.id] = order.model_copy()
            return order

    # Stage 7 and 8: intents, kill switches, live settings ----------------------------------

    def list_intents(self, context: TenantContext, *, status: str | None, mode: str | None, limit: int, offset: int) -> tuple[list[Intent], int]:
        with self._lock:
            found = [i for i in self.intents.values() if mine(i, context) and (status is None or i.status == status)
                     and (mode is None or i.mode == mode)]
            found.sort(key=lambda i: (i.created_at, i.id), reverse=True)
            return [i.model_copy(deep=True) for i in found[offset : offset + limit]], len(found)

    def get_intent(self, context: TenantContext, intent_id: str) -> Intent | None:
        with self._lock:
            found = self.intents.get(intent_id)
            return found.model_copy(deep=True) if found and mine(found, context) else None

    def intent_by_key(self, context: TenantContext, key: str) -> Intent | None:
        with self._lock:
            found = next((i for i in self.intents.values() if mine(i, context) and i.idempotency_key == key), None)
            return found.model_copy(deep=True) if found else None

    def save_intent(self, intent: Intent) -> bool:
        with self._lock:
            if intent.id not in self.intents and any(
                mine(i, _ctx(intent)) and i.idempotency_key == intent.idempotency_key for i in self.intents.values()
            ):
                return False
            self.intents[intent.id] = intent.model_copy(deep=True)
            return True

    def claim_intent(self, context: TenantContext, intent_id: str, now: datetime, approver: str) -> bool:
        with self._lock:
            found = self.intents.get(intent_id)
            if found is None or not mine(found, context) or found.status != IntentStatus.PENDING or now >= found.expires_at:
                return False
            if approver != found.owner_user_id:
                return False
            found.status, found.approved_at, found.approved_by, found.updated_at = IntentStatus.EXECUTING, now, approver, now
            return True

    def get_exec_settings(self, context: TenantContext) -> ExecSettings | None:
        with self._lock:
            found = self.exec_settings.get(self._key(context))
            return found.model_copy(deep=True) if found else None

    def save_exec_settings(self, context: TenantContext, settings: ExecSettings) -> ExecSettings:
        with self._lock:
            self.exec_settings[self._key(context)] = settings.model_copy(deep=True)
            return settings

    def get_global_switch(self, context: TenantContext) -> GlobalSwitch | None:
        with self._lock:
            found = self.global_switches.get(context.tenant_id)
            return found.model_copy() if found else None

    def save_global_switch(self, context: TenantContext, switch: GlobalSwitch) -> GlobalSwitch:
        with self._lock:
            self.global_switches[context.tenant_id] = switch.model_copy()
            return switch

    # Stage 9: widenings and incidents -------------------------------------------------------

    def list_widenings(self, context: TenantContext, *, status: str | None, limit: int) -> list[Widening]:
        with self._lock:
            found = [w for w in self.widenings.values() if mine(w, context) and (status is None or w.status == status)]
            found.sort(key=lambda w: (w.created_at, w.id), reverse=True)
            return [w.model_copy(deep=True) for w in found[:limit]]

    def get_widening(self, context: TenantContext, widening_id: str) -> Widening | None:
        with self._lock:
            found = self.widenings.get(widening_id)
            return found.model_copy(deep=True) if found and mine(found, context) else None

    def save_widening(self, widening: Widening) -> Widening:
        with self._lock:
            self.widenings[widening.id] = widening.model_copy(deep=True)
            return widening

    def claim_widening(self, context: TenantContext, widening_id: str, now: datetime, approver: str, effective_at: datetime) -> bool:
        with self._lock:
            found = self.widenings.get(widening_id)
            if found is None or not mine(found, context) or found.status != "pending" or now >= found.expires_at or approver != found.owner_user_id:
                return False
            found.status, found.decided_at, found.decided_by, found.effective_at = "approved", now, approver, effective_at
            return True

    def list_incidents(self, context: TenantContext, *, limit: int) -> list[Incident]:
        with self._lock:
            found = sorted((i for i in self.incidents.values() if mine(i, context)), key=lambda i: (i.created_at, i.id), reverse=True)
            return [i.model_copy() for i in found[:limit]]

    def save_incident(self, incident: Incident) -> Incident:
        with self._lock:
            self.incidents[incident.id] = incident.model_copy()
            return incident

    # Trading intelligence ---------------------------------------------------------------------

    def list_instruments(self, context: TenantContext) -> list[Instrument]:
        with self._lock:
            return [i.model_copy() for i in sorted((i for i in self.instruments.values() if mine(i, context)), key=lambda i: (i.symbol, i.id))]

    def get_instrument(self, context: TenantContext, instrument_id: str) -> Instrument | None:
        with self._lock:
            found = self.instruments.get(instrument_id)
            return found.model_copy() if found and mine(found, context) else None

    def save_instrument(self, instrument: Instrument) -> Instrument:
        with self._lock:
            for other in self.instruments.values():
                if other.id != instrument.id and mine(other, _ctx(instrument)) and (other.symbol, other.venue) == (instrument.symbol, instrument.venue):
                    raise ValueError("duplicate_instrument")
            self.instruments[instrument.id] = instrument.model_copy()
            return instrument

    def delete_instrument(self, context: TenantContext, instrument_id: str) -> bool:
        with self._lock:
            found = self.instruments.get(instrument_id)
            if found is None or not mine(found, context):
                return False
            del self.instruments[instrument_id]
            for key in [k for k, b in self.bars.items() if b.instrument_id == instrument_id and mine(b, context)]:
                del self.bars[key]
            return True

    @staticmethod
    def _bar_key(bar: StoredCandle) -> str:
        return f"{bar.tenant_id}|{bar.workspace_id}|{bar.owner_user_id}|{bar.instrument_id}|{bar.day.isoformat()}"

    def save_bars(self, bars: list[StoredCandle]) -> int:
        """Insert or replace by (instrument, day); returns how many rows were new or changed."""
        changed = 0
        with self._lock:
            for bar in bars:
                key = self._bar_key(bar)
                old = self.bars.get(key)
                if old is None or old.candle() != bar.candle() or old.source != bar.source:
                    changed += 1
                    self.bars[key] = bar.model_copy(update={"id": old.id if old else bar.id})
            return changed

    def list_bars(self, context: TenantContext, instrument_id: str, *, start: date | None = None, end: date | None = None,
                  limit: int = 10_000) -> list[StoredCandle]:
        with self._lock:
            found = [b for b in self.bars.values() if mine(b, context) and b.instrument_id == instrument_id
                     and (start is None or b.day >= start) and (end is None or b.day <= end)]
            found.sort(key=lambda b: b.day)
            return [b.model_copy() for b in found[-limit:]]

    def list_strategies(self, context: TenantContext, *, state: str | None = None, lineage_id: str | None = None, limit: int,
                        offset: int) -> tuple[list[StrategyConfig], int]:
        with self._lock:
            found = [s for s in self.strategies.values() if mine(s, context) and (state is None or s.state.value == state)
                     and (lineage_id is None or s.lineage_id == lineage_id)]
            found.sort(key=lambda s: (s.updated_at, s.id), reverse=True)
            return [s.model_copy() for s in found[offset:offset + limit]], len(found)

    def get_strategy(self, context: TenantContext, strategy_id: str) -> StrategyConfig | None:
        with self._lock:
            found = self.strategies.get(strategy_id)
            return found.model_copy() if found and mine(found, context) else None

    def latest_version(self, context: TenantContext, lineage_id: str) -> int:
        with self._lock:
            return max((s.version for s in self.strategies.values() if mine(s, context) and s.lineage_id == lineage_id), default=0)

    def save_strategy(self, strategy: StrategyConfig) -> StrategyConfig:
        with self._lock:
            for other in self.strategies.values():
                if other.id != strategy.id and other.lineage_id == strategy.lineage_id and other.version == strategy.version:
                    raise ValueError("duplicate_version")
            self.strategies[strategy.id] = strategy.model_copy()
            return strategy

    def list_strategy_runs(self, context: TenantContext, strategy_id: str, *, limit: int, offset: int) -> tuple[list[StrategyRun], int]:
        with self._lock:
            found = sorted((r for r in self.strategy_runs.values() if mine(r, context) and r.strategy_id == strategy_id),
                           key=lambda r: (r.created_at, r.id), reverse=True)
            return [r.model_copy() for r in found[offset:offset + limit]], len(found)

    def get_strategy_run(self, context: TenantContext, run_id: str) -> StrategyRun | None:
        with self._lock:
            found = self.strategy_runs.get(run_id)
            return found.model_copy() if found and mine(found, context) else None

    def save_strategy_run(self, run: StrategyRun) -> StrategyRun:
        with self._lock:
            self.strategy_runs[run.id] = run.model_copy()
            return run

    def list_agent_decisions(self, context: TenantContext, *, agent: str | None = None, strategy_id: str | None = None,
                             cycle_id: str | None = None, limit: int, offset: int) -> tuple[list[AgentDecision], int]:
        with self._lock:
            found = [d for d in self.decisions.values() if mine(d, context) and (agent is None or d.agent.value == agent)
                     and (strategy_id is None or d.strategy_id == strategy_id) and (cycle_id is None or d.cycle_id == cycle_id)]
            found.sort(key=lambda d: (d.created_at, d.id), reverse=True)
            return [d.model_copy() for d in found[offset:offset + limit]], len(found)

    def save_agent_decision(self, decision: AgentDecision) -> AgentDecision:
        with self._lock:
            self.decisions[decision.id] = decision.model_copy()
            return decision

    # privacy ------------------------------------------------------------------------------

    def _tables(self) -> tuple[dict[str, Any], ...]:
        return (
            self.transactions, self.imports, self.accounts, *self.items.values(), self.policies, self.changes,
            self.fx_rates, self.connections, self.prices, self.orders, self.portfolios, self.intents, self.widenings, self.incidents,
            self.bars, self.instruments, self.strategy_runs, self.strategies, self.decisions,
        )

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            rows = sum(1 for table in self._tables() for v in table.values() if mine(v, context))
            keyed = (self.settings, self.plans, self.exec_settings)
            return rows + sum(int(self._key(context) in table) for table in keyed)

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = 0
            for table in self._tables():
                for key in [k for k, v in table.items() if mine(v, context)]:
                    del table[key]
                    removed += 1
            for keyed in (self.settings, self.plans, self.exec_settings):
                if keyed.pop(self._key(context), None) is not None:
                    removed += 1
            return removed


finance_store = InMemoryFinanceStore()


@contextmanager
def open_finance_store(context: TenantContext) -> Iterator[FinanceStore]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.finance_repository import SqlAlchemyFinanceStore

        return SqlAlchemyFinanceStore(session, context)

    with open_scoped_store(context, finance_store, sql_store, user_scoped=True) as store:
        yield store
