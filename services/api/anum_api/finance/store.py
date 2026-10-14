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
from datetime import date
from decimal import Decimal
from threading import RLock
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import (
    Account,
    Bill,
    Category,
    Debt,
    FinanceSettings,
    Goal,
    Holding,
    ImportRecord,
    Transaction,
)

MAX_PAGE = 200
MAX_ACCOUNTS = 100
MAX_ITEMS = 500  # bills, debts, goals and holdings, each
MAX_SUMMARY_ROWS = 50_000

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
    # privacy: every finance row of the caller
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def mine(item: object, context: TenantContext) -> bool:
    return (
        item.tenant_id == context.tenant_id
        and item.workspace_id == context.workspace_id
        and item.owner_user_id == context.user_id
    )


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

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            tables = (self.transactions, self.imports, self.accounts, *self.items.values())
            return sum(1 for table in tables for v in table.values() if mine(v, context)) + int(self._key(context) in self.settings)

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = 0
            for table in (self.transactions, self.imports, self.accounts, *self.items.values()):
                for key in [k for k, v in table.items() if mine(v, context)]:
                    del table[key]
                    removed += 1
            if self.settings.pop(self._key(context), None) is not None:
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
