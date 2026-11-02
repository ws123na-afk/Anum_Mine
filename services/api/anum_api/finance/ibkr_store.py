"""Storage for IBKR live trading (docs/finance-live-trading.md#storage).

Four owner-only tables (migration ``0122_finance_ibkr_live``): settings, connections, orders and
events. ``open_ibkr_stores`` yields the finance store (for intents and kill switches) and this
store in **one** unit of work, so a claim and its order row commit together. Nothing here is
ever held across an IBKR call: the service opens a store, works, closes it, then calls IBKR.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Protocol

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .ibkr_rules import BrokerConnection, BrokerEvent, BrokerOrder, BrokerSettings, Environment, OrderState
from .store import FinanceStore, finance_store

MAX_EVENTS_PAGE = 100


class IbkrStore(Protocol):
    def get_settings(self, context: TenantContext) -> BrokerSettings | None: ...
    def save_settings(self, context: TenantContext, settings: BrokerSettings, *, expected_version: int | None) -> bool: ...

    def list_connections(self, context: TenantContext) -> list[BrokerConnection]: ...
    def get_connection(self, context: TenantContext, connection_id: str) -> BrokerConnection | None: ...
    def active_connection(self, context: TenantContext, environment: Environment) -> BrokerConnection | None: ...
    def save_connection(self, connection: BrokerConnection) -> bool: ...

    def get_order(self, context: TenantContext, order_id: str) -> BrokerOrder | None: ...
    def list_orders(self, context: TenantContext, *, environment: str | None, limit: int, offset: int) -> tuple[list[BrokerOrder], int]: ...
    def orders_for(self, context: TenantContext, environment: Environment) -> list[BrokerOrder]: ...
    def add_order(self, order: BrokerOrder) -> bool: ...
    def save_order(self, order: BrokerOrder) -> None: ...

    def add_event(self, event: BrokerEvent) -> None: ...
    def list_events(self, context: TenantContext, *, alerts_only: bool, limit: int, offset: int) -> tuple[list[BrokerEvent], int]: ...
    def unread_alerts(self, context: TenantContext) -> int: ...
    def mark_read(self, context: TenantContext, now: datetime) -> int: ...

    def count_everything(self, context: TenantContext) -> int: ...
    def delete_everything(self, context: TenantContext) -> int: ...


def _mine(item: object, context: TenantContext) -> bool:
    return (getattr(item, "tenant_id", None), getattr(item, "workspace_id", None), getattr(item, "owner_user_id", None)) == (
        context.tenant_id, context.workspace_id, context.user_id)


class InMemoryIbkrStore:
    """Development and unit-test store. Same contract as the PostgreSQL one."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.clear()

    def clear(self) -> None:
        self.settings: dict[tuple[str, str, str], BrokerSettings] = {}
        self.connections: dict[str, BrokerConnection] = {}
        self.orders: dict[str, BrokerOrder] = {}
        self.events: list[BrokerEvent] = []

    @staticmethod
    def _key(context: TenantContext) -> tuple[str, str, str]:
        return (context.tenant_id, context.workspace_id, context.user_id)

    def get_settings(self, context: TenantContext) -> BrokerSettings | None:
        found = self.settings.get(self._key(context))
        return found.model_copy(deep=True) if found else None

    def save_settings(self, context: TenantContext, settings: BrokerSettings, *, expected_version: int | None) -> bool:
        with self._lock:
            current = self.settings.get(self._key(context))
            if expected_version is not None and (current.version if current else 0) != expected_version:
                return False
            self.settings[self._key(context)] = settings.model_copy(deep=True)
            return True

    def list_connections(self, context: TenantContext) -> list[BrokerConnection]:
        return sorted((c.model_copy(deep=True) for c in self.connections.values() if _mine(c, context)), key=lambda c: c.created_at, reverse=True)

    def get_connection(self, context: TenantContext, connection_id: str) -> BrokerConnection | None:
        found = self.connections.get(connection_id)
        return found.model_copy(deep=True) if found and _mine(found, context) else None

    def active_connection(self, context: TenantContext, environment: Environment) -> BrokerConnection | None:
        return next((c for c in self.list_connections(context) if c.environment == environment and c.status == "active"), None)

    def save_connection(self, connection: BrokerConnection) -> bool:
        with self._lock:
            ctx = TenantContext(tenant_id=connection.tenant_id, workspace_id=connection.workspace_id, user_id=connection.owner_user_id)
            other = self.active_connection(ctx, connection.environment)
            if connection.status == "active" and other is not None and other.id != connection.id:
                return False
            self.connections[connection.id] = connection.model_copy(deep=True)
            return True

    def get_order(self, context: TenantContext, order_id: str) -> BrokerOrder | None:
        found = self.orders.get(order_id)
        return found.model_copy(deep=True) if found and _mine(found, context) else None

    def list_orders(self, context: TenantContext, *, environment: str | None, limit: int, offset: int) -> tuple[list[BrokerOrder], int]:
        rows = sorted((o for o in self.orders.values() if _mine(o, context) and (environment is None or o.environment.value == environment)),
                      key=lambda o: o.created_at, reverse=True)
        return [o.model_copy(deep=True) for o in rows[offset:offset + limit]], len(rows)

    def orders_for(self, context: TenantContext, environment: Environment) -> list[BrokerOrder]:
        return [o.model_copy(deep=True) for o in self.orders.values() if _mine(o, context) and o.environment == environment]

    def add_order(self, order: BrokerOrder) -> bool:
        with self._lock:
            if order.id in self.orders:
                return False
            self.orders[order.id] = order.model_copy(deep=True)
            return True

    def save_order(self, order: BrokerOrder) -> None:
        with self._lock:
            self.orders[order.id] = order.model_copy(deep=True)

    def add_event(self, event: BrokerEvent) -> None:
        with self._lock:
            self.events.append(event.model_copy(deep=True))

    def list_events(self, context: TenantContext, *, alerts_only: bool, limit: int, offset: int) -> tuple[list[BrokerEvent], int]:
        rows = sorted((e for e in self.events if _mine(e, context) and (e.alert or not alerts_only)), key=lambda e: e.created_at, reverse=True)
        return [e.model_copy(deep=True) for e in rows[offset:offset + limit]], len(rows)

    def unread_alerts(self, context: TenantContext) -> int:
        return sum(1 for e in self.events if _mine(e, context) and e.alert and e.read_at is None)

    def mark_read(self, context: TenantContext, now: datetime) -> int:
        count = 0
        with self._lock:
            for event in self.events:
                if _mine(event, context) and event.alert and event.read_at is None:
                    event.read_at = now
                    count += 1
        return count

    def count_everything(self, context: TenantContext) -> int:
        return (int(self._key(context) in self.settings) + sum(1 for c in self.connections.values() if _mine(c, context))
                + sum(1 for o in self.orders.values() if _mine(o, context)) + sum(1 for e in self.events if _mine(e, context)))

    def delete_everything(self, context: TenantContext) -> int:
        with self._lock:
            removed = self.count_everything(context)
            self.settings.pop(self._key(context), None)
            self.connections = {k: v for k, v in self.connections.items() if not _mine(v, context)}
            self.orders = {k: v for k, v in self.orders.items() if not _mine(v, context)}
            self.events = [e for e in self.events if not _mine(e, context)]
            return removed


ibkr_memory_store = InMemoryIbkrStore()


@contextmanager
def open_ibkr_stores(context: TenantContext) -> Iterator[tuple[FinanceStore, IbkrStore]]:
    """The finance store and the IBKR store in one owner-scoped unit of work."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.finance_repository import SqlAlchemyFinanceStore
        from ..db.ibkr_repository import SqlAlchemyIbkrStore

        return (SqlAlchemyFinanceStore(session, context), SqlAlchemyIbkrStore(session))

    with open_scoped_store(context, (finance_store, ibkr_memory_store), sql_store, user_scoped=True) as pair:
        yield pair


OPEN_ORDER_STATES = [s.value for s in (OrderState.SUBMITTING, OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED, OrderState.CANCEL_REQUESTED,
                                       OrderState.UNKNOWN)]
