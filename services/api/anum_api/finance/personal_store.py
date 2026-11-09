"""Storage for money lent and borrowed with people, payslips and remittances (docs/finance.md#lent-and-borrowed).

With PostgreSQL the store is ``anum_api.db.personal_finance_repository.SqlAlchemyPersonalStore`` in a session whose
tenant, workspace and user RLS context is set (``open_scoped_store(..., user_scoped=True)``): the three tables
(migration ``0147_finance_personal_money``) are under forced RLS with the tenant policy and a restrictive owner-only
policy, and every query repeats the scope and the owner. The in-memory store serves local development and tests with
the same rules. Counted, exported and deleted with the rest of Finance.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Literal, Protocol, TypeVar

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .personal import Payslip, PersonLoan, Remittance

Kind = Literal["person_loan", "payslip", "remittance"]
MODELS: dict[str, type] = {"person_loan": PersonLoan, "payslip": Payslip, "remittance": Remittance}
MAX_ROWS = 2000  # per person and kind
Item = TypeVar("Item", PersonLoan, Payslip, Remittance)


class PersonalStore(Protocol):
    def list(self, context: TenantContext, kind: Kind) -> list: ...
    def get(self, context: TenantContext, kind: Kind, item_id: str) -> PersonLoan | Payslip | Remittance | None: ...
    def save(self, kind: Kind, item: PersonLoan | Payslip | Remittance) -> None: ...
    def delete(self, context: TenantContext, kind: Kind, item_id: str) -> bool: ...
    def count(self, context: TenantContext, kind: Kind) -> int: ...
    def claim_reminder(self, context: TenantContext, loan_id: str, due_on: date, overdue: bool) -> bool: ...
    def release_reminder(self, context: TenantContext, loan_id: str, overdue: bool) -> None: ...
    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def _mine(item: object, context: TenantContext) -> bool:
    return (item.tenant_id, item.workspace_id, item.owner_user_id) == (context.tenant_id, context.workspace_id, context.user_id)  # type: ignore[attr-defined]


def sort_key(kind: Kind, item: object) -> tuple:
    """Newest first: by the date the money moved (or the pay month), then when it was saved."""
    if kind == "person_loan":
        return (item.lent_on, item.created_at)  # type: ignore[attr-defined]
    if kind == "payslip":
        return (item.month, item.created_at)  # type: ignore[attr-defined]
    return (item.sent_on, item.created_at)  # type: ignore[attr-defined]


class InMemoryPersonalStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.clear()

    def clear(self) -> None:
        self.rows: dict[str, dict[str, PersonLoan | Payslip | Remittance]] = {kind: {} for kind in MODELS}

    def list(self, context: TenantContext, kind: Kind) -> list:
        with self._lock:
            found = [v.model_copy(deep=True) for v in self.rows[kind].values() if _mine(v, context)]
        return sorted(found, key=lambda v: sort_key(kind, v), reverse=True)

    def get(self, context: TenantContext, kind: Kind, item_id: str) -> PersonLoan | Payslip | Remittance | None:
        with self._lock:
            found = self.rows[kind].get(item_id)
            return found.model_copy(deep=True) if found is not None and _mine(found, context) else None

    def save(self, kind: Kind, item: PersonLoan | Payslip | Remittance) -> None:
        with self._lock:
            existing = self.rows[kind].get(item.id)
            if existing is not None and not _mine(existing, TenantContext(tenant_id=item.tenant_id, workspace_id=item.workspace_id,
                                                                        user_id=item.owner_user_id)):
                raise PermissionError("personal finance rows belong to the caller only")
            self.rows[kind][item.id] = item.model_copy(deep=True)

    def delete(self, context: TenantContext, kind: Kind, item_id: str) -> bool:
        with self._lock:
            found = self.rows[kind].get(item_id)
            if found is None or not _mine(found, context):
                return False
            del self.rows[kind][item_id]
            return True

    def count(self, context: TenantContext, kind: Kind) -> int:
        with self._lock:
            return sum(1 for v in self.rows[kind].values() if _mine(v, context))

    def claim_reminder(self, context: TenantContext, loan_id: str, due_on: date, overdue: bool) -> bool:
        """Mark the reminder for this due date as sent, unless another run already did (compare and set)."""
        with self._lock:
            loan = self.rows["person_loan"].get(loan_id)
            if not isinstance(loan, PersonLoan) or not _mine(loan, context) or loan.due_on != due_on:
                return False
            field = "reminded_overdue_for" if overdue else "reminded_for"
            if getattr(loan, field) == due_on:
                return False
            setattr(loan, field, due_on)
            return True

    def release_reminder(self, context: TenantContext, loan_id: str, overdue: bool) -> None:
        with self._lock:
            loan = self.rows["person_loan"].get(loan_id)
            if isinstance(loan, PersonLoan) and _mine(loan, context):
                setattr(loan, "reminded_overdue_for" if overdue else "reminded_for", None)

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            return sum(1 for table in self.rows.values() for v in table.values() if _mine(v, context))

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = 0
            for table in self.rows.values():
                for key in [k for k, v in table.items() if _mine(v, context)]:
                    del table[key]
                    removed += 1
            return removed


personal_memory_store = InMemoryPersonalStore()


@contextmanager
def open_personal_store(context: TenantContext) -> Iterator[PersonalStore]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.personal_finance_repository import SqlAlchemyPersonalStore

        return SqlAlchemyPersonalStore(session)

    with open_scoped_store(context, personal_memory_store, sql_store, user_scoped=True) as store:
        yield store


__all__ = ["InMemoryPersonalStore", "Kind", "MAX_ROWS", "MODELS", "PersonalStore", "open_personal_store", "personal_memory_store", "sort_key"]
