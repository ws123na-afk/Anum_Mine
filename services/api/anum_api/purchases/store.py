"""Purchase storage: the protocol, the in-memory store and ``open_purchase_store``.

With ``ANUM_REPOSITORY_BACKEND=postgresql`` the store is
``anum_api.db.purchases_repository.SqlPurchaseStore`` in a session whose tenant, workspace **and
user** RLS context is set (migration ``0136_purchases``). A business purchase and its rows are
visible to the workspace; a personal purchase and its rows only to its owner. The in-memory store
applies the same rule for local development and tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from threading import RLock
from typing import Any, Protocol

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import (
    AWAITING_DELIVERY,
    CLOSED,
    OPEN_RETURN,
    Event,
    Item,
    Ownership,
    PaymentRef,
    Preferences,
    Purchase,
    ReminderSent,
    Return,
    Shipment,
)

MAX_PURCHASES = 5_000  # read at once for the overview and the reminder job (the newest ones)
MAX_ITEMS = 200
MAX_SHIPMENTS = 20
MAX_RETURNS = 20
RETURN_STATES = frozenset({"return_requested", "return_approved", "pickup_scheduled", "returned", "refund_pending", "partially_refunded", "disputed"})
# Filters the list understands besides one status.
GROUPS: dict[str, frozenset[str]] = {
    "awaiting": frozenset(AWAITING_DELIVERY),
    "returns": RETURN_STATES,
    "closed": frozenset(CLOSED),
    "open": frozenset({"ordered", "confirmed", "shipped", "out_for_delivery", "partially_delivered", "delivered", *RETURN_STATES}),
}


class DuplicateError(ValueError):
    """A unique key (idempotency key, tracking number) is already taken."""


class PurchaseStore(Protocol):
    def list_purchases(self, context: TenantContext, *, query: str | None, status: str | None, archived: bool, limit: int,
                       offset: int) -> tuple[list[Purchase], int]: ...
    def all_purchases(self, context: TenantContext) -> list[Purchase]: ...
    def get_purchase(self, context: TenantContext, purchase_id: str) -> Purchase | None: ...
    def purchase_by_idem(self, context: TenantContext, idem_key: str) -> Purchase | None: ...
    def find_by_order_ref(self, context: TenantContext, order_ref: str) -> list[Purchase]: ...
    def add_purchase(self, purchase: Purchase) -> None: ...
    def save_purchase(self, purchase: Purchase, expected_version: int) -> bool: ...
    def delete_purchase(self, context: TenantContext, purchase_id: str) -> bool: ...
    def set_payment(self, context: TenantContext, purchase_id: str, payment: PaymentRef | None) -> bool: ...

    def items(self, context: TenantContext, purchase_ids: list[str]) -> list[Item]: ...
    def replace_items(self, context: TenantContext, purchase_id: str, items: list[Item]) -> None: ...

    def events(self, context: TenantContext, purchase_id: str, *, limit: int, offset: int) -> tuple[list[Event], int]: ...
    def add_event(self, event: Event) -> None: ...

    def shipments(self, context: TenantContext, purchase_ids: list[str]) -> list[Shipment]: ...
    def get_shipment(self, context: TenantContext, shipment_id: str) -> Shipment | None: ...
    def find_by_tracking(self, context: TenantContext, tracking_number: str) -> list[Shipment]: ...
    def add_shipment(self, shipment: Shipment) -> None: ...
    def save_shipment(self, shipment: Shipment, expected_version: int) -> bool: ...
    def delete_shipment(self, context: TenantContext, shipment_id: str) -> bool: ...

    def returns(self, context: TenantContext, purchase_ids: list[str]) -> list[Return]: ...
    def list_returns(self, context: TenantContext, *, open_only: bool, limit: int, offset: int) -> tuple[list[Return], int]: ...
    def get_return(self, context: TenantContext, return_id: str) -> Return | None: ...
    def return_by_idem(self, context: TenantContext, idem_key: str) -> Return | None: ...
    def add_return(self, item: Return) -> None: ...
    def save_return(self, item: Return, expected_version: int) -> bool: ...
    def delete_return(self, context: TenantContext, return_id: str) -> bool: ...
    def set_refunds(self, context: TenantContext, return_id: str, refunds: list[PaymentRef]) -> bool: ...

    def reminder_sent(self, reminder: ReminderSent) -> bool: ...
    def add_reminder(self, reminder: ReminderSent) -> bool: ...

    def get_preferences(self, context: TenantContext) -> Preferences | None: ...
    def save_preferences(self, preferences: Preferences, expected_version: int) -> bool: ...

    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...
    def export(self, context: TenantContext) -> dict[str, Any]: ...


def _scope(item: Any, context: Any) -> bool:
    return item.tenant_id == context.tenant_id and item.workspace_id == context.workspace_id


def purchase_visible(purchase: Purchase, context: TenantContext) -> bool:
    return _scope(purchase, context) and (purchase.ownership == Ownership.BUSINESS or purchase.owner_user_id == context.user_id)


def child_visible(item: Any, context: TenantContext) -> bool:
    return _scope(item, context) and (item.visibility == Ownership.BUSINESS or item.purchase_owner_id == context.user_id)


def purchase_matches(purchase: Purchase, query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return any(needle in (text or "").casefold() for text in (purchase.merchant, purchase.order_ref, purchase.notes, purchase.address_ref))


def status_matches(purchase: Purchase, status: str | None) -> bool:
    if not status:
        return True
    group = GROUPS.get(status)
    return purchase.status in group if group is not None else purchase.status == status


def newest_first(purchase: Purchase) -> tuple[Any, ...]:
    return (purchase.ordered_on, purchase.created_at, purchase.id)


class InMemoryPurchaseStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.clear()

    def clear(self) -> None:
        with getattr(self, "_lock", RLock()):
            self.purchases: dict[str, Purchase] = {}
            self.item_rows: dict[str, Item] = {}
            self.event_rows: dict[str, Event] = {}
            self.shipment_rows: dict[str, Shipment] = {}
            self.return_rows: dict[str, Return] = {}
            self.reminders: dict[tuple[str, str, str, date, int], ReminderSent] = {}
            self.preferences: dict[tuple[str, str, str], Preferences] = {}

    # purchases ---------------------------------------------------------------------------------

    def list_purchases(self, context: TenantContext, *, query: str | None, status: str | None, archived: bool, limit: int,
                       offset: int) -> tuple[list[Purchase], int]:
        with self._lock:
            rows = [p for p in self.purchases.values() if purchase_visible(p, context) and (p.archived_at is not None) == archived
                    and purchase_matches(p, query) and status_matches(p, status)]
        rows.sort(key=newest_first, reverse=True)
        return [p.model_copy(deep=True) for p in rows[offset:offset + limit]], len(rows)

    def all_purchases(self, context: TenantContext) -> list[Purchase]:
        rows, _ = self.list_purchases(context, query=None, status=None, archived=False, limit=MAX_PURCHASES, offset=0)
        return rows

    def get_purchase(self, context: TenantContext, purchase_id: str) -> Purchase | None:
        found = self.purchases.get(purchase_id)
        return found.model_copy(deep=True) if found is not None and purchase_visible(found, context) else None

    def purchase_by_idem(self, context: TenantContext, idem_key: str) -> Purchase | None:
        with self._lock:
            found = next((p for p in self.purchases.values() if _scope(p, context) and p.owner_user_id == context.user_id
                          and p.idem_key == idem_key), None)
        return found.model_copy(deep=True) if found is not None else None

    def find_by_order_ref(self, context: TenantContext, order_ref: str) -> list[Purchase]:
        wanted = order_ref.upper()
        with self._lock:
            rows = [p for p in self.purchases.values() if purchase_visible(p, context) and (p.order_ref or "").upper() == wanted]
        return [p.model_copy(deep=True) for p in sorted(rows, key=newest_first, reverse=True)]

    def add_purchase(self, purchase: Purchase) -> None:
        with self._lock:
            for other in self.purchases.values():
                if _scope(other, purchase) and other.owner_user_id == purchase.owner_user_id and other.idem_key == purchase.idem_key:
                    raise DuplicateError("idem_key")
            self.purchases[purchase.id] = purchase.model_copy(deep=True)

    def save_purchase(self, purchase: Purchase, expected_version: int) -> bool:
        with self._lock:
            current = self.purchases.get(purchase.id)
            if current is None or current.version != expected_version:
                return False
            if current.ownership != purchase.ownership or current.owner_user_id != purchase.owner_user_id:
                raise ValueError("a purchase keeps its ownership and owner")
            # The payment's read copy is the link's to keep: an edit never writes it back.
            self.purchases[purchase.id] = purchase.model_copy(deep=True, update={"version": expected_version + 1, "payment": current.payment})
            return True

    def delete_purchase(self, context: TenantContext, purchase_id: str) -> bool:
        with self._lock:
            found = self.purchases.get(purchase_id)
            if found is None or not purchase_visible(found, context):
                return False
            del self.purchases[purchase_id]
            for table in (self.item_rows, self.event_rows, self.shipment_rows, self.return_rows):
                for key in [k for k, r in table.items() if r.purchase_id == purchase_id]:
                    del table[key]
            for key in [k for k in self.reminders if k[0] == purchase_id]:
                del self.reminders[key]
            return True

    def set_payment(self, context: TenantContext, purchase_id: str, payment: PaymentRef | None) -> bool:
        with self._lock:
            found = self.purchases.get(purchase_id)
            if found is None or not purchase_visible(found, context):
                return False
            found.payment = payment.model_copy() if payment else None
            return True

    # items -------------------------------------------------------------------------------------

    def items(self, context: TenantContext, purchase_ids: list[str]) -> list[Item]:
        wanted = set(purchase_ids)
        rows = [r for r in list(self.item_rows.values()) if r.purchase_id in wanted and child_visible(r, context)]
        return [r.model_copy() for r in sorted(rows, key=lambda r: (r.purchase_id, r.position, r.id))]

    def replace_items(self, context: TenantContext, purchase_id: str, items: list[Item]) -> None:
        with self._lock:
            for key in [k for k, r in self.item_rows.items() if r.purchase_id == purchase_id and child_visible(r, context)]:
                del self.item_rows[key]
            for item in items:
                self.item_rows[item.id] = item.model_copy()

    # events ------------------------------------------------------------------------------------

    def events(self, context: TenantContext, purchase_id: str, *, limit: int, offset: int) -> tuple[list[Event], int]:
        rows = [r for r in list(self.event_rows.values()) if r.purchase_id == purchase_id and child_visible(r, context)]
        rows.sort(key=lambda r: (r.occurred_at, r.created_at, r.id), reverse=True)
        return [r.model_copy() for r in rows[offset:offset + limit]], len(rows)

    def add_event(self, event: Event) -> None:
        with self._lock:
            if event.id in self.event_rows:
                raise ValueError("purchase history is append-only")
            self.event_rows[event.id] = event.model_copy()

    # shipments ---------------------------------------------------------------------------------

    def shipments(self, context: TenantContext, purchase_ids: list[str]) -> list[Shipment]:
        wanted = set(purchase_ids)
        rows = [r for r in list(self.shipment_rows.values()) if r.purchase_id in wanted and child_visible(r, context)]
        return [r.model_copy() for r in sorted(rows, key=lambda r: (r.purchase_id, r.created_at, r.id))]

    def get_shipment(self, context: TenantContext, shipment_id: str) -> Shipment | None:
        found = self.shipment_rows.get(shipment_id)
        return found.model_copy() if found is not None and child_visible(found, context) else None

    def find_by_tracking(self, context: TenantContext, tracking_number: str) -> list[Shipment]:
        wanted = tracking_number.upper()
        return [r.model_copy() for r in list(self.shipment_rows.values()) if child_visible(r, context) and (r.tracking_number or "").upper() == wanted]

    def _unique_tracking(self, shipment: Shipment) -> None:
        if not shipment.tracking_number:
            return
        for other in self.shipment_rows.values():
            if other.id != shipment.id and other.purchase_id == shipment.purchase_id and (other.tracking_number or "").upper() == shipment.tracking_number.upper():
                raise DuplicateError("tracking_number")

    def add_shipment(self, shipment: Shipment) -> None:
        with self._lock:
            self._unique_tracking(shipment)
            self.shipment_rows[shipment.id] = shipment.model_copy()

    def save_shipment(self, shipment: Shipment, expected_version: int) -> bool:
        with self._lock:
            current = self.shipment_rows.get(shipment.id)
            if current is None or current.version != expected_version:
                return False
            self._unique_tracking(shipment)
            self.shipment_rows[shipment.id] = shipment.model_copy(update={"version": expected_version + 1})
            return True

    def delete_shipment(self, context: TenantContext, shipment_id: str) -> bool:
        with self._lock:
            found = self.shipment_rows.get(shipment_id)
            if found is None or not child_visible(found, context):
                return False
            del self.shipment_rows[shipment_id]
            return True

    # returns -----------------------------------------------------------------------------------

    def returns(self, context: TenantContext, purchase_ids: list[str]) -> list[Return]:
        wanted = set(purchase_ids)
        rows = [r for r in list(self.return_rows.values()) if r.purchase_id in wanted and child_visible(r, context)]
        return [r.model_copy(deep=True) for r in sorted(rows, key=lambda r: (r.purchase_id, r.created_at, r.id))]

    def list_returns(self, context: TenantContext, *, open_only: bool, limit: int, offset: int) -> tuple[list[Return], int]:
        rows = [r for r in list(self.return_rows.values()) if child_visible(r, context) and (not open_only or r.status in OPEN_RETURN)]
        rows.sort(key=lambda r: (r.requested_on, r.created_at, r.id), reverse=True)
        return [r.model_copy(deep=True) for r in rows[offset:offset + limit]], len(rows)

    def get_return(self, context: TenantContext, return_id: str) -> Return | None:
        found = self.return_rows.get(return_id)
        return found.model_copy(deep=True) if found is not None and child_visible(found, context) else None

    def return_by_idem(self, context: TenantContext, idem_key: str) -> Return | None:
        found = next((r for r in list(self.return_rows.values()) if _scope(r, context) and r.owner_user_id == context.user_id
                      and r.idem_key == idem_key), None)
        return found.model_copy(deep=True) if found is not None else None

    def add_return(self, item: Return) -> None:
        with self._lock:
            for other in self.return_rows.values():
                if _scope(other, item) and other.owner_user_id == item.owner_user_id and other.idem_key == item.idem_key:
                    raise DuplicateError("idem_key")
            self.return_rows[item.id] = item.model_copy(deep=True)

    def save_return(self, item: Return, expected_version: int) -> bool:
        with self._lock:
            current = self.return_rows.get(item.id)
            if current is None or current.version != expected_version:
                return False
            # The refunds' read copy is the links' to keep: an edit never writes it back.
            self.return_rows[item.id] = item.model_copy(deep=True, update={"version": expected_version + 1, "refunds": current.refunds})
            return True

    def delete_return(self, context: TenantContext, return_id: str) -> bool:
        with self._lock:
            found = self.return_rows.get(return_id)
            if found is None or not child_visible(found, context):
                return False
            del self.return_rows[return_id]
            return True

    def set_refunds(self, context: TenantContext, return_id: str, refunds: list[PaymentRef]) -> bool:
        with self._lock:
            found = self.return_rows.get(return_id)
            if found is None or not child_visible(found, context):
                return False
            found.refunds = [r.model_copy() for r in refunds]
            return True

    # reminders and preferences -----------------------------------------------------------------

    @staticmethod
    def _reminder_key(reminder: ReminderSent) -> tuple[str, str, str, date, int]:
        return (reminder.purchase_id, reminder.kind, reminder.source_id, reminder.due_on, reminder.step)

    def reminder_sent(self, reminder: ReminderSent) -> bool:
        return self._reminder_key(reminder) in self.reminders

    def add_reminder(self, reminder: ReminderSent) -> bool:
        with self._lock:
            key = self._reminder_key(reminder)
            if key in self.reminders:
                return False
            self.reminders[key] = reminder.model_copy()
            return True

    def get_preferences(self, context: TenantContext) -> Preferences | None:
        found = self.preferences.get((context.tenant_id, context.workspace_id, context.user_id))
        return found.model_copy() if found else None

    def save_preferences(self, preferences: Preferences, expected_version: int) -> bool:
        with self._lock:
            key = (preferences.tenant_id, preferences.workspace_id, preferences.owner_user_id)
            current = self.preferences.get(key)
            if (current.version if current else 0) != expected_version:
                return False
            self.preferences[key] = preferences.model_copy(update={"version": expected_version + 1})
            return True

    # privacy -----------------------------------------------------------------------------------

    def _mine(self, context: TenantContext) -> dict[str, list[Any]]:
        """The person's own data: their personal purchases (with every row), their rows on business purchases, their settings."""
        mine = [p for p in self.purchases.values() if _scope(p, context) and p.owner_user_id == context.user_id]
        personal = {p.id for p in mine if p.ownership == Ownership.PERSONAL}
        out: dict[str, list[Any]] = {"purchases": mine}
        for name, table in (("items", self.item_rows), ("events", self.event_rows), ("shipments", self.shipment_rows),
                            ("returns", self.return_rows), ("reminders", self.reminders)):
            # Items are part of a purchase: a business purchase keeps them.
            theirs = (lambda r: r.purchase_id in personal) if name == "items" else (lambda r: r.owner_user_id == context.user_id or r.purchase_id in personal)
            out[name] = [r for r in table.values() if _scope(r, context) and theirs(r)]
        out["preferences"] = [p for p in self.preferences.values() if _scope(p, context) and p.owner_user_id == context.user_id]
        return out

    def count_everything(self, context: TenantContext) -> int:
        with self._lock:
            return sum(len(rows) for rows in self._mine(context).values())

    def delete_everything(self, context: TenantContext) -> int:
        """Personal purchases go with every row; on business purchases the person's own rows go and the purchase stays."""
        with self._lock:
            mine = self._mine(context)
            removed = sum(len(rows) for name, rows in mine.items() if name != "purchases")
            removed += sum(1 for p in mine["purchases"] if p.ownership == Ownership.PERSONAL)
            ids = {id(r) for rows in mine.values() for r in rows}
            for table in (self.item_rows, self.event_rows, self.shipment_rows, self.return_rows, self.reminders, self.preferences):
                for key in [k for k, r in table.items() if id(r) in ids]:
                    del table[key]
            for purchase in mine["purchases"]:
                if purchase.ownership == Ownership.PERSONAL:
                    self.purchases.pop(purchase.id, None)
            return removed

    def export(self, context: TenantContext) -> dict[str, Any]:
        with self._lock:
            return export_records(self._mine(context))


def export_records(mine: dict[str, list[Any]]) -> dict[str, Any]:
    hidden = {"tenant_id", "workspace_id", "idem_key"}
    return {name: [r.model_dump(mode="json", exclude=hidden) for r in rows] for name, rows in mine.items()}


purchase_memory_store = InMemoryPurchaseStore()


def store_for_session(session: Any, context: TenantContext) -> PurchaseStore:
    """The purchase store on an existing unit of work (a finance link's session), or the in-memory store."""
    if session is None:
        return purchase_memory_store
    from ..db.purchases_repository import SqlPurchaseStore

    return SqlPurchaseStore(session, context)


@contextmanager
def open_purchase_store(context: TenantContext) -> Iterator[PurchaseStore]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.purchases_repository import SqlPurchaseStore

        return SqlPurchaseStore(session, context)

    with open_scoped_store(context, purchase_memory_store, sql_store, user_scoped=True) as store:
        yield store


__all__ = [
    "DuplicateError",
    "GROUPS",
    "InMemoryPurchaseStore",
    "MAX_ITEMS",
    "MAX_PURCHASES",
    "MAX_RETURNS",
    "MAX_SHIPMENTS",
    "PurchaseStore",
    "child_visible",
    "export_records",
    "open_purchase_store",
    "purchase_memory_store",
    "purchase_visible",
    "store_for_session",
]
