"""Storage of the business books: the protocol, the in-memory store and ``open_books_store``.

With ``ANUM_REPOSITORY_BACKEND=postgresql`` the store is
``anum_api.db.business_books_repository.SqlBooksStore`` in a session whose tenant and workspace
RLS context is set (migration ``0137_business_books``). The in-memory store keeps the same scope
and unique rules for local development and tests. Every read filters on the caller's tenant and
workspace, so a guessed id of another business reads as missing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal
from enum import Enum
from threading import RLock
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .books_models import KINDS, BookSettings

ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_PAGE = 200
MAX_SCAN = 5_000  # rows of one kind read at once for a report (the newest); more is reported as truncated


class DuplicateError(ValueError):
    """A unique key (an idempotency key, a vendor name, a cost centre code, a rate's day, an allocation already paying a bill)."""


class BooksStore(Protocol):
    def get(self, kind: str, item_id: str, *, for_update: bool = False) -> Any: ...
    def add(self, kind: str, item: ModelT) -> ModelT: ...
    def save(self, kind: str, item: ModelT, *, expected_version: int | None = None) -> bool: ...
    def delete(self, kind: str, item_id: str) -> bool: ...
    def first(self, kind: str, **where: Any) -> Any: ...
    def find(self, kind: str, *, where: dict[str, Any] | None = None, text: str | None = None, text_fields: tuple[str, ...] = (),
             order_by: str = "created_at", descending: bool = True, limit: int = 50, offset: int = 0) -> tuple[list[Any], int]: ...
    def get_settings(self) -> BookSettings | None: ...
    def save_settings(self, value: BookSettings, *, expected_version: int | None) -> bool: ...
    # Intercompany entries are written in both businesses (the caller manages both; checked first).
    def add_elsewhere(self, kind: str, item: ModelT) -> ModelT: ...
    def get_elsewhere(self, kind: str, workspace_id: str, **where: Any) -> Any: ...
    def save_elsewhere(self, kind: str, item: ModelT) -> None: ...
    # The journal (migration 0148): a lock that serialises posting runs and closes per business, an entry written with its
    # lines in one step, and sums of lines grouped by columns (debit, credit and the signed original amount).
    def lock_books(self) -> None: ...
    def add_entry(self, entry: Any, lines: list[Any]) -> None: ...
    def ledger_totals(self, where: dict[str, Any], group_by: tuple[str, ...]) -> list[dict[str, Any]]: ...


class PeriodLocked(ValueError):  # noqa: N818 - the database's refusal, named for what it means
    """A journal line or a source document dated in a closed period (the database trigger ``business_book_period_open``)."""


class Unbalanced(ValueError):  # noqa: N818
    """A journal entry whose debits and credits differ, or with fewer than two lines (``business_book_journal_balanced``)."""


def plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def matches(item: BaseModel, where: dict[str, Any]) -> bool:
    for name, wanted in where.items():
        actual = plain(getattr(item, name))
        if isinstance(wanted, AtLeast):
            if actual is None or actual < wanted.value:
                return False
        elif isinstance(wanted, AtMost):
            if actual is None or actual > wanted.value:
                return False
        elif isinstance(wanted, After):
            if actual is None or actual <= wanted.value:
                return False
        elif isinstance(wanted, Between):
            if actual is None or not (wanted.low <= actual <= wanted.high):
                return False
        elif isinstance(wanted, Not):
            if actual == plain(wanted.value):
                return False
        elif isinstance(wanted, (set, frozenset, list, tuple)):
            if actual not in {plain(value) for value in wanted}:
                return False
        elif actual != plain(wanted):
            return False
    return True


class AtLeast:
    """A ``where`` value: the column is on or after ``value`` (``find(..., where={"as_of": AtLeast(day)})``)."""

    def __init__(self, value: Any) -> None:
        self.value = value


class AtMost:
    """A ``where`` value: the column is on or before ``value``."""

    def __init__(self, value: Any) -> None:
        self.value = value


class After:
    """A ``where`` value: the column is strictly after ``value`` (keyset paging by id)."""

    def __init__(self, value: Any) -> None:
        self.value = value


class Between:
    """A ``where`` value: ``low <= column <= high``."""

    def __init__(self, low: Any, high: Any) -> None:
        self.low = low
        self.high = high


class Not:
    """A ``where`` value: the column is not ``value`` (``None`` means "is not null")."""

    def __init__(self, value: Any) -> None:
        self.value = value


# The unique keys the database enforces (migrations 0137 and 0138), repeated for the in-memory store.
_UNIQUE: dict[str, Callable[[Any], tuple[Any, ...] | None]] = {
    "cost_centres": lambda r: ("code", r.code.lower()),
    "vendors": lambda r: ("name", r.name_key),
    "bills": lambda r: ("idem", r.idem_key) if r.idem_key else None,
    "bill_payments": lambda r: ("allocation", r.allocation_id) if r.allocation_id and r.voided_at is None else None,
    "allocation_tags": lambda r: ("allocation", r.allocation_id),
    "rates": lambda r: ("day", r.base, r.quote, r.as_of),
    "adjustments": lambda r: ("idem", r.idem_key) if r.idem_key else None,
    "intercompany": lambda r: ("pair", r.pair_id),
    "rate_feeds": lambda r: ("feed",),
    "ledger_accounts": lambda r: ("code", r.code),
    "journal_entries": lambda r: ("event", r.event_key) if r.status == "posted" and r.reverses_id is None else None,
    "posting_issues": lambda r: ("event", r.event_key),
    "closes": lambda r: ("period", r.period_end) if r.status == "closed" else None,
    "accountants": lambda r: ("user", r.user_id),
}
_SECOND_UNIQUE: dict[str, Callable[[Any], tuple[Any, ...] | None]] = {
    "bill_payments": lambda r: ("idem", r.bill_id, r.idem_key) if r.idem_key else None,
    "bills": lambda r: ("period", r.template_id, r.period_date) if r.template_id else None,
    "ledger_accounts": lambda r: ("key", r.system_key) if r.system_key else None,
    "journal_entries": lambda r: ("reverses", r.reverses_id) if r.reverses_id else None,
}
_THIRD_UNIQUE: dict[str, Callable[[Any], tuple[Any, ...] | None]] = {
    "ledger_accounts": lambda r: ("category", r.category) if r.category else None,
}


_BILL_FACTS = ("bill_date", "due_date", "currency", "subtotal", "tax", "total", "category", "section", "status", "useful_life_months", "project_id",
               "cost_centre_id", "vendor_id")


def _bill_touched(new: Any, old: Any, locked: Any) -> bool:
    if old is None:
        return new.bill_date <= locked and plain(new.status) != "void"
    if new.bill_date > locked and old.bill_date > locked:
        return False
    return any(plain(getattr(new, name)) != plain(getattr(old, name)) for name in _BILL_FACTS)


def _adjustment_touched(new: Any, old: Any, locked: Any) -> bool:
    if new.entry_date <= locked:
        return True
    return old is not None and ((old.start_month is not None and old.start_month <= locked) or (old.reverse_on is not None and old.reverse_on <= locked))


_DATED: dict[str, Callable[[Any, Any, Any], bool]] = {
    "bills": _bill_touched,
    "bill_payments": lambda new, old, locked: new.paid_on <= locked or (old is not None and old.paid_on <= locked),
    "adjustments": _adjustment_touched,
    "intercompany": lambda new, old, locked: new.entry_date <= locked,
}


class InMemoryBooksStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._books_locks: dict[tuple[str, str], RLock] = {}
        self.rows: dict[str, dict[str, BaseModel]] = {kind: {} for kind in KINDS}
        self.settings: dict[tuple[str, str], BookSettings] = {}

    def clear(self) -> None:
        with self._lock:
            for table in self.rows.values():
                table.clear()
            self.settings.clear()

    def scoped(self, context: TenantContext) -> _ScopedMemoryBooks:
        return _ScopedMemoryBooks(self, context)


class _ScopedMemoryBooks:
    def __init__(self, root: InMemoryBooksStore, context: TenantContext) -> None:
        self.root = root
        self.context = context

    def _mine(self, item: Any, workspace_id: str | None = None) -> bool:
        return item.tenant_id == self.context.tenant_id and item.workspace_id == (workspace_id or self.context.workspace_id)

    def _check_unique(self, kind: str, item: Any) -> None:
        for rule in (_UNIQUE.get(kind), _SECOND_UNIQUE.get(kind), _THIRD_UNIQUE.get(kind)):
            if rule is None or (key := rule(item)) is None:
                continue
            for other in self.root.rows[kind].values():
                if other.id != item.id and self._mine(other, item.workspace_id) and rule(other) == key:
                    raise DuplicateError(f"{kind}: {key[0]}")

    def get(self, kind: str, item_id: str, *, for_update: bool = False) -> Any:
        found = self.root.rows[kind].get(item_id)
        return found.model_copy(deep=True) if found is not None and self._mine(found) else None

    def _period_open(self, kind: str, new: Any, old: Any = None) -> None:
        """The database trigger ``business_book_period_open``, repeated: nothing dated in a closed period is written or changed."""
        if kind not in _DATED:
            return
        locked = self.locked_through(new.workspace_id)
        if locked is None:
            return
        if _DATED[kind](new, old, locked):
            raise PeriodLocked(f"closed through {locked}")

    def add(self, kind: str, item: ModelT) -> ModelT:
        with self.root._lock:
            if not self._mine(item):
                raise ValueError("a record is written in the caller's scope only")
            self._period_open(kind, item)
            self._check_unique(kind, item)
            self.root.rows[kind][item.id] = item.model_copy(deep=True)
        return item

    def save(self, kind: str, item: ModelT, *, expected_version: int | None = None) -> bool:
        with self.root._lock:
            current = self.root.rows[kind].get(item.id)
            if current is None or not self._mine(current):
                return False
            if expected_version is not None and getattr(current, "version", None) != expected_version:
                return False
            self._period_open(kind, item, current)
            self._check_unique(kind, item)
            self.root.rows[kind][item.id] = item.model_copy(deep=True)
            return True

    def delete(self, kind: str, item_id: str) -> bool:
        with self.root._lock:
            current = self.root.rows[kind].get(item_id)
            if current is None or not self._mine(current):
                return False
            del self.root.rows[kind][item_id]
            return True

    def first(self, kind: str, **where: Any) -> Any:
        for item in list(self.root.rows[kind].values()):
            if self._mine(item) and matches(item, where):
                return item.model_copy(deep=True)
        return None

    def find(self, kind: str, *, where: dict[str, Any] | None = None, text: str | None = None, text_fields: tuple[str, ...] = (),
             order_by: str = "created_at", descending: bool = True, limit: int = 50, offset: int = 0) -> tuple[list[Any], int]:
        needle = text.casefold() if text else None
        rows = [item for item in list(self.root.rows[kind].values())
                if self._mine(item) and matches(item, where or {})
                and (needle is None or any(needle in str(getattr(item, name) or "").casefold() for name in text_fields))]
        rows.sort(key=lambda item: (getattr(item, order_by), item.created_at, item.id), reverse=descending)
        return [item.model_copy(deep=True) for item in rows[offset:offset + limit]], len(rows)

    def get_settings(self) -> BookSettings | None:
        found = self.root.settings.get((self.context.tenant_id, self.context.workspace_id))
        return found.model_copy(deep=True) if found is not None else None

    def save_settings(self, value: BookSettings, *, expected_version: int | None) -> bool:
        with self.root._lock:
            key = (self.context.tenant_id, self.context.workspace_id)
            current = self.root.settings.get(key)
            if (current.version if current is not None else None) != expected_version:
                return False
            self.root.settings[key] = value.model_copy(deep=True)
            return True

    def add_elsewhere(self, kind: str, item: ModelT) -> ModelT:
        with self.root._lock:
            if item.tenant_id != self.context.tenant_id:
                raise ValueError("never another tenant")
            self._period_open(kind, item)
            self._check_unique(kind, item)
            self.root.rows[kind][item.id] = item.model_copy(deep=True)
        return item

    def lock_books(self) -> None:
        """Held until the request's thread is done with the store (the in-memory store has no transaction to end)."""
        with self.root._lock:
            lock = self.root._books_locks.setdefault((self.context.tenant_id, self.context.workspace_id), RLock())
        lock.acquire()
        self._held = lock

    def release_books(self) -> None:
        held = getattr(self, "_held", None)
        if held is not None:
            self._held = None
            held.release()

    def locked_through(self, workspace_id: str | None = None) -> Any:
        days = [row.period_end for row in self.root.rows["closes"].values() if self._mine(row, workspace_id) and row.status == "closed"]
        return max(days) if days else None

    def add_entry(self, entry: Any, lines: list[Any]) -> None:
        """The database's rules, repeated: at least two lines, debits equal credits, nothing dated in a closed period."""
        with self.root._lock:
            if len(lines) < 2 or sum((line.debit - line.credit for line in lines), Decimal(0)) != 0:
                raise Unbalanced(f"business journal entry {entry.id} does not balance")
            locked = self.locked_through()
            if locked is not None and any(line.entry_date <= locked for line in lines):
                raise PeriodLocked(f"closed through {locked}")
            self.add("journal_entries", entry)
            for line in lines:
                self.root.rows["journal_lines"][line.id] = line.model_copy(deep=True)

    def ledger_totals(self, where: dict[str, Any], group_by: tuple[str, ...]) -> list[dict[str, Any]]:
        sums: dict[tuple[Any, ...], dict[str, Decimal]] = {}
        for line in list(self.root.rows["journal_lines"].values()):
            if not self._mine(line) or not matches(line, where):
                continue
            key = tuple(plain(getattr(line, name)) for name in group_by)
            found = sums.setdefault(key, {"debit": Decimal(0), "credit": Decimal(0), "amount": Decimal(0)})
            found["debit"] += line.debit
            found["credit"] += line.credit
            found["amount"] += line.amount
        return [{**dict(zip(group_by, key, strict=True)), **values} for key, values in sums.items()]

    def get_elsewhere(self, kind: str, workspace_id: str, **where: Any) -> Any:
        for item in list(self.root.rows[kind].values()):
            if self._mine(item, workspace_id) and matches(item, where):
                return item.model_copy(deep=True)
        return None

    def save_elsewhere(self, kind: str, item: ModelT) -> None:
        with self.root._lock:
            current = self.root.rows[kind].get(item.id)
            if current is not None and current.tenant_id == self.context.tenant_id and current.workspace_id == item.workspace_id:
                self._period_open(kind, item, current)
                self.root.rows[kind][item.id] = item.model_copy(deep=True)


books_memory_store = InMemoryBooksStore()


@contextmanager
def open_books_store(context: TenantContext) -> Iterator[BooksStore]:
    """One unit of work in the caller's tenant and workspace scope (RLS in PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.business_books_repository import SqlBooksStore

        return SqlBooksStore(session, context)

    memory = books_memory_store.scoped(context)
    try:
        with open_scoped_store(context, memory, sql_store) as store:
            yield store
    finally:
        memory.release_books()


__all__ = [
    "MAX_PAGE",
    "MAX_SCAN",
    "After",
    "AtLeast",
    "AtMost",
    "Between",
    "BooksStore",
    "Not",
    "PeriodLocked",
    "Unbalanced",
    "DuplicateError",
    "InMemoryBooksStore",
    "books_memory_store",
    "matches",
    "open_books_store",
    "plain",
]
