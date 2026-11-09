"""Storage for reconciliation sessions and the owner's resolutions in them (docs/finance.md#reconciliation-sessions).

With PostgreSQL the store is ``anum_api.db.reconciliation_repository.SqlAlchemyReconciliationStore`` in the same
owner-scoped session as the finance and journal stores (``open_recon_stores``): both tables (migration
``0153_finance_reconciliation``) are under forced RLS with the tenant policy and a restrictive owner-only policy, and
every query repeats the scope and the owner. Partial unique indexes keep a statement row and a ledger record in one
live resolution per session. The in-memory store serves local development and tests with the same rules. Counted,
exported and deleted with the rest of Finance.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from ..schemas import TenantContext

Source = Literal["import", "typed"]
Status = Literal["open", "completed"]
ItemKind = Literal["match", "attach", "pending", "reversal", "correction"]
MAX_SESSIONS = 2000  # per person


class Reconciliation(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    account_id: str
    period_start: date
    period_end: date
    currency: str
    statement_opening: Decimal | None = None
    statement_closing: Decimal
    source: Source
    import_id: str | None = None
    amount_due: Decimal | None = None  # a card statement's amount due (positive)
    minimum_due: Decimal | None = None
    due_on: date | None = None
    status: Status = "open"
    completed_at: datetime | None = None
    completed_by: str | None = None
    # At completion: the figures and what was matched (docs/finance.md#reconciliation-sessions).
    snapshot: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    version: int = 0
    created_by: str
    created_at: datetime
    updated_at: datetime


class ReconItem(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    reconciliation_id: str
    kind: ItemKind
    statement_ref: str | None = None
    transaction_id: str | None = None
    other_transaction_id: str | None = None
    payload_hash: str | None = None
    note: str | None = None
    status: Literal["active", "undone"] = "active"
    created_by: str
    created_at: datetime
    undone_at: datetime | None = None
    undone_by: str | None = None


class PeriodTaken(Exception):
    """Another session of this account already covers exactly this period."""


class ReconciliationStore(Protocol):
    def list(self, context: TenantContext, *, account_id: str | None = None) -> list[Reconciliation]: ...
    def get(self, context: TenantContext, reconciliation_id: str) -> Reconciliation | None: ...
    def add(self, item: Reconciliation) -> None: ...
    def save(self, item: Reconciliation, *, expected_version: int) -> bool: ...
    def delete(self, context: TenantContext, reconciliation_id: str) -> bool: ...
    def delete_for_account(self, context: TenantContext, account_id: str) -> int: ...
    def items(self, context: TenantContext, reconciliation_id: str | None = None) -> list[ReconItem]: ...
    def add_item(self, item: ReconItem) -> bool: ...
    def undo_item(self, context: TenantContext, item_id: str, *, by: str, at: datetime) -> bool: ...
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def _mine(item: object, context: TenantContext) -> bool:
    return (item.tenant_id, item.workspace_id, item.owner_user_id) == (context.tenant_id, context.workspace_id, context.user_id)  # type: ignore[attr-defined]


def sort_key(item: Reconciliation) -> tuple:
    return (item.period_end, item.period_start, item.created_at)


class InMemoryReconciliationStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.clear()

    def clear(self) -> None:
        self.sessions: dict[str, Reconciliation] = {}
        self.rows: dict[str, ReconItem] = {}

    def list(self, context: TenantContext, *, account_id: str | None = None) -> list[Reconciliation]:
        with self._lock:
            found = [s.model_copy(deep=True) for s in self.sessions.values() if _mine(s, context) and (account_id is None or s.account_id == account_id)]
        return sorted(found, key=sort_key, reverse=True)

    def get(self, context: TenantContext, reconciliation_id: str) -> Reconciliation | None:
        with self._lock:
            found = self.sessions.get(reconciliation_id)
            return found.model_copy(deep=True) if found is not None and _mine(found, context) else None

    def add(self, item: Reconciliation) -> None:
        with self._lock:
            for other in self.sessions.values():
                if ((other.tenant_id, other.workspace_id, other.owner_user_id, other.account_id, other.period_start, other.period_end)
                        == (item.tenant_id, item.workspace_id, item.owner_user_id, item.account_id, item.period_start, item.period_end)):
                    raise PeriodTaken
            self.sessions[item.id] = item.model_copy(deep=True)

    def save(self, item: Reconciliation, *, expected_version: int) -> bool:
        """Compare and set on ``version``: a stale write (another tab completed or reopened it) changes nothing."""
        with self._lock:
            current = self.sessions.get(item.id)
            if current is None or not _mine(current, TenantContext(tenant_id=item.tenant_id, workspace_id=item.workspace_id, user_id=item.owner_user_id)):
                return False
            if current.version != expected_version:
                return False
            stored = item.model_copy(deep=True)
            stored.version = expected_version + 1
            self.sessions[item.id] = stored
            item.version = stored.version
            return True

    def delete(self, context: TenantContext, reconciliation_id: str) -> bool:
        with self._lock:
            found = self.sessions.get(reconciliation_id)
            if found is None or not _mine(found, context):
                return False
            del self.sessions[reconciliation_id]
            for key in [k for k, v in self.rows.items() if v.reconciliation_id == reconciliation_id]:
                del self.rows[key]
            return True

    def delete_for_account(self, context: TenantContext, account_id: str) -> int:
        with self._lock:
            ids = [s.id for s in self.sessions.values() if _mine(s, context) and s.account_id == account_id]
            for session_id in ids:
                self.delete(context, session_id)
            return len(ids)

    def items(self, context: TenantContext, reconciliation_id: str | None = None) -> list[ReconItem]:
        with self._lock:
            found = [r.model_copy(deep=True) for r in self.rows.values()
                     if _mine(r, context) and (reconciliation_id is None or r.reconciliation_id == reconciliation_id)]
        return sorted(found, key=lambda r: (r.created_at, r.id))

    def add_item(self, item: ReconItem) -> bool:
        """False when the statement row or a ledger record already has a live resolution in the session (the
        partial unique indexes of migration ``0152`` in PostgreSQL)."""
        with self._lock:
            if item.reconciliation_id not in self.sessions:
                return False
            for other in self.rows.values():
                if other.reconciliation_id != item.reconciliation_id or other.status != "active" or not _mine(other, TenantContext(
                        tenant_id=item.tenant_id, workspace_id=item.workspace_id, user_id=item.owner_user_id)):
                    continue
                if item.statement_ref is not None and other.statement_ref == item.statement_ref:
                    return False
                if item.transaction_id is not None and other.transaction_id == item.transaction_id:
                    return False
                if item.other_transaction_id is not None and other.other_transaction_id == item.other_transaction_id:
                    return False
            self.rows[item.id] = item.model_copy(deep=True)
            return True

    def undo_item(self, context: TenantContext, item_id: str, *, by: str, at: datetime) -> bool:
        with self._lock:
            found = self.rows.get(item_id)
            if found is None or not _mine(found, context) or found.status != "active":
                return False
            found.status, found.undone_at, found.undone_by = "undone", at, by
            return True

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            return sum(1 for s in self.sessions.values() if _mine(s, context)) + sum(1 for r in self.rows.values() if _mine(r, context))

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = 0
            for table in (self.rows, self.sessions):
                for key in [k for k, v in table.items() if _mine(v, context)]:
                    del table[key]
                    removed += 1
            return removed


recon_memory_store = InMemoryReconciliationStore()


def recon_store_for(sms: Any) -> ReconciliationStore:
    """The reconciliation store in the same unit of work as an SMS and journal store."""
    session = getattr(sms, "session", None)
    if session is None:
        return recon_memory_store
    from ..db.reconciliation_repository import SqlAlchemyReconciliationStore

    return SqlAlchemyReconciliationStore(session)


@contextmanager
def open_recon_stores(context: TenantContext) -> Iterator[tuple[Any, Any, Any, ReconciliationStore]]:
    """The finance, journal, link and reconciliation stores in one owner-scoped unit of work."""
    from .links_store import open_link_stores

    with open_link_stores(context) as (finance, sms, links):
        yield finance, sms, links, recon_store_for(sms)


__all__ = ["InMemoryReconciliationStore", "ItemKind", "MAX_SESSIONS", "PeriodTaken", "ReconItem", "Reconciliation", "ReconciliationStore",
           "open_recon_stores", "recon_memory_store", "recon_store_for"]
