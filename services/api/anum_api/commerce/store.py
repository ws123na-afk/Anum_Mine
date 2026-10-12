"""Commerce storage: the protocol, the in-memory store and ``open_commerce_store``.

With ``ANUM_REPOSITORY_BACKEND=postgresql`` the store is
``anum_api.db.commerce_repository.SqlAlchemyCommerceStore``: every table is under forced
RLS on ``anum.tenant_id`` and ``anum.workspace_id`` (migration ``0038_commerce``) and every
statement repeats the scope. The in-memory store serves local development and tests.

Searching: product and listing search matches the SKU, the titles (English and Arabic),
the brand, the ASIN / channel id, and a tyre size typed in any common form
(``2055516``, ``205/55R16``, ``205 55 16``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Protocol

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store
from .models import (
    Alert,
    AlertStatus,
    CommerceSettings,
    Connector,
    Customer,
    FeeRecord,
    Listing,
    Order,
    OrderStatus,
    Product,
    ReturnRecord,
)
from .tyre import size_query

MAX_PAGE = 200


class CommerceStore(Protocol):
    # connectors
    def list_connectors(self, context: TenantContext) -> list[Connector]: ...
    def get_connector(self, context: TenantContext, connector_id: str) -> Connector | None: ...
    def save_connector(self, connector: Connector, *, credentials_ciphertext: str | None = None, replace_credentials: bool = False) -> Connector: ...
    def credentials_ciphertext(self, context: TenantContext, connector_id: str) -> str | None: ...
    def delete_connector(self, context: TenantContext, connector_id: str) -> bool: ...
    def claim_due(self, context: TenantContext, connector_id: str, now: datetime, until: datetime) -> bool: ...
    # products
    def upsert_product(self, product: Product) -> Product: ...
    def get_product(self, context: TenantContext, product_id: str) -> Product | None: ...
    def get_product_by_sku(self, context: TenantContext, sku: str) -> Product | None: ...
    def list_products(self, context: TenantContext, *, query: str | None, limit: int, offset: int) -> tuple[list[Product], int]: ...
    def delete_product(self, context: TenantContext, product_id: str) -> bool: ...
    # listings
    def upsert_listing(self, listing: Listing) -> Listing: ...
    def get_listing(self, context: TenantContext, listing_id: str) -> Listing | None: ...
    def list_listings(
        self, context: TenantContext, *, sku: str | None = None, connector_id: str | None = None, query: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Listing], int]: ...
    def all_listings(self, context: TenantContext) -> list[Listing]: ...
    # orders
    def upsert_order(self, order: Order) -> Order: ...
    def get_order(self, context: TenantContext, order_id: str) -> Order | None: ...
    def find_order(self, context: TenantContext, external_id: str) -> Order | None: ...
    def list_orders(
        self,
        context: TenantContext,
        *,
        channel: str | None = None,
        statuses: list[OrderStatus] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]: ...
    def orders_between(self, context: TenantContext, start: datetime, end: datetime, *, limit: int) -> list[Order]: ...
    # customers
    def upsert_customer(self, customer: Customer) -> Customer: ...
    def get_customer(self, context: TenantContext, customer_id: str) -> Customer | None: ...
    # returns and money
    def upsert_return(self, record: ReturnRecord) -> ReturnRecord: ...
    def list_returns(self, context: TenantContext, *, since: datetime | None, limit: int, offset: int) -> tuple[list[ReturnRecord], int]: ...
    def upsert_fee(self, record: FeeRecord) -> FeeRecord: ...
    def list_fees(self, context: TenantContext, *, since: datetime, until: datetime) -> list[FeeRecord]: ...
    # alerts
    def raise_alert(self, alert: Alert) -> tuple[Alert, bool]: ...
    def resolve_alerts(self, context: TenantContext, kind: str, keep_keys: set[str], *, connector_id: str | None = None) -> int: ...
    def list_alerts(self, context: TenantContext, *, status: AlertStatus | None, limit: int, offset: int) -> tuple[list[Alert], int]: ...
    def get_alert(self, context: TenantContext, alert_id: str) -> Alert | None: ...
    def save_alert(self, alert: Alert) -> Alert: ...
    # settings
    def get_settings(self, context: TenantContext) -> CommerceSettings | None: ...
    def save_settings(self, context: TenantContext, settings: CommerceSettings) -> CommerceSettings: ...


def _scope(context: TenantContext) -> tuple[str, str]:
    return context.tenant_id, context.workspace_id


def _in(item: object, context: TenantContext) -> bool:
    return getattr(item, "tenant_id") == context.tenant_id and getattr(item, "workspace_id") == context.workspace_id


def _norm_size(value: str | None) -> str:
    return (value or "").upper().replace("ZR", "R")


def product_matches(product: Product, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    size = size_query(query)
    if size and _norm_size(product.tyre.size) == _norm_size(size):
        return True
    haystack = " ".join(
        part for part in (product.sku, product.title, product.title_ar or "", product.brand or "", product.tyre.size or "") if part
    ).lower()
    return needle in haystack


def listing_matches(listing: Listing, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    size = size_query(query)
    if size and _norm_size(size) in _norm_size(listing.title).replace(" ", ""):
        return True
    return needle in " ".join((listing.sku, listing.title, listing.external_id or "")).lower()


def order_matches(order: Order, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    return needle in order.external_id.lower() or any(needle in line.sku.lower() or needle in line.title.lower() for line in order.lines)


class InMemoryCommerceStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.clear()

    def clear(self) -> None:
        self.connectors: dict[str, Connector] = {}
        self.ciphertexts: dict[str, str] = {}
        self.products: dict[str, Product] = {}
        self.listings: dict[str, Listing] = {}
        self.orders: dict[str, Order] = {}
        self.customers: dict[str, Customer] = {}
        self.returns: dict[str, ReturnRecord] = {}
        self.fees: dict[str, FeeRecord] = {}
        self.alerts: dict[str, Alert] = {}
        self.settings: dict[tuple[str, str], CommerceSettings] = {}

    # connectors ---------------------------------------------------------------------

    def list_connectors(self, context: TenantContext) -> list[Connector]:
        with self._lock:
            return sorted((c.model_copy(deep=True) for c in self.connectors.values() if _in(c, context)), key=lambda c: c.created_at)

    def get_connector(self, context: TenantContext, connector_id: str) -> Connector | None:
        with self._lock:
            found = self.connectors.get(connector_id)
            return found.model_copy(deep=True) if found and _in(found, context) else None

    def save_connector(self, connector: Connector, *, credentials_ciphertext: str | None = None, replace_credentials: bool = False) -> Connector:
        with self._lock:
            self.connectors[connector.id] = connector.model_copy(deep=True)
            if replace_credentials:
                if credentials_ciphertext:
                    self.ciphertexts[connector.id] = credentials_ciphertext
                else:
                    self.ciphertexts.pop(connector.id, None)
        return connector

    def credentials_ciphertext(self, context: TenantContext, connector_id: str) -> str | None:
        with self._lock:
            found = self.connectors.get(connector_id)
            return self.ciphertexts.get(connector_id) if found and _in(found, context) else None

    def delete_connector(self, context: TenantContext, connector_id: str) -> bool:
        with self._lock:
            found = self.connectors.get(connector_id)
            if found is None or not _in(found, context):
                return False
            del self.connectors[connector_id]
            self.ciphertexts.pop(connector_id, None)
            for table in (self.listings, self.orders, self.customers, self.returns, self.fees, self.alerts):
                for key in [k for k, v in table.items() if getattr(v, "connector_id", None) == connector_id]:
                    del table[key]
            return True

    def claim_due(self, context: TenantContext, connector_id: str, now: datetime, until: datetime) -> bool:
        """Take a due connector for one scheduled sync (pushes ``next_sync_at`` to ``until``)."""
        with self._lock:
            found = self.connectors.get(connector_id)
            if found is None or not _in(found, context) or not found.enabled:
                return False
            if found.next_sync_at is not None and found.next_sync_at > now:
                return False
            found.next_sync_at = until
            return True

    # products -----------------------------------------------------------------------

    def upsert_product(self, product: Product) -> Product:
        with self._lock:
            existing = next(
                (p for p in self.products.values() if p.tenant_id == product.tenant_id and p.workspace_id == product.workspace_id and p.sku == product.sku),
                None,
            )
            if existing is not None and existing.id != product.id:
                product = product.model_copy(update={"id": existing.id, "created_at": existing.created_at})
            self.products[product.id] = product.model_copy(deep=True)
            return product

    def get_product(self, context: TenantContext, product_id: str) -> Product | None:
        with self._lock:
            found = self.products.get(product_id)
            return found.model_copy(deep=True) if found and _in(found, context) else None

    def get_product_by_sku(self, context: TenantContext, sku: str) -> Product | None:
        with self._lock:
            found = next((p for p in self.products.values() if _in(p, context) and p.sku == sku), None)
            return found.model_copy(deep=True) if found else None

    def list_products(self, context: TenantContext, *, query: str | None, limit: int, offset: int) -> tuple[list[Product], int]:
        with self._lock:
            items = sorted((p for p in self.products.values() if _in(p, context) and product_matches(p, query or "")), key=lambda p: (p.title.lower(), p.sku))
            return [p.model_copy(deep=True) for p in items[offset : offset + limit]], len(items)

    def delete_product(self, context: TenantContext, product_id: str) -> bool:
        with self._lock:
            found = self.products.get(product_id)
            if found is None or not _in(found, context):
                return False
            del self.products[product_id]
            return True

    # listings -----------------------------------------------------------------------

    def upsert_listing(self, listing: Listing) -> Listing:
        with self._lock:
            existing = next(
                (
                    item
                    for item in self.listings.values()
                    if item.connector_id == listing.connector_id and item.marketplace_id == listing.marketplace_id and item.sku == listing.sku
                    and item.tenant_id == listing.tenant_id and item.workspace_id == listing.workspace_id
                ),
                None,
            )
            if existing is not None and existing.id != listing.id:
                listing = listing.model_copy(update={"id": existing.id})
            self.listings[listing.id] = listing.model_copy(deep=True)
            return listing

    def get_listing(self, context: TenantContext, listing_id: str) -> Listing | None:
        with self._lock:
            found = self.listings.get(listing_id)
            return found.model_copy(deep=True) if found and _in(found, context) else None

    def list_listings(self, context, *, sku=None, connector_id=None, query=None, limit=50, offset=0):  # type: ignore[no-untyped-def]
        with self._lock:
            items = [
                item
                for item in self.listings.values()
                if _in(item, context)
                and (sku is None or item.sku == sku)
                and (connector_id is None or item.connector_id == connector_id)
                and listing_matches(item, query or "")
            ]
            items.sort(key=lambda item: (item.sku, item.channel.value, item.marketplace_id))
            return [item.model_copy(deep=True) for item in items[offset : offset + limit]], len(items)

    def all_listings(self, context: TenantContext) -> list[Listing]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self.listings.values() if _in(item, context)]

    # orders --------------------------------------------------------------------------

    def upsert_order(self, order: Order) -> Order:
        with self._lock:
            existing = next(
                (
                    item
                    for item in self.orders.values()
                    if item.connector_id == order.connector_id and item.external_id == order.external_id
                    and item.tenant_id == order.tenant_id and item.workspace_id == order.workspace_id
                ),
                None,
            )
            if existing is not None and existing.id != order.id:
                order = order.model_copy(update={"id": existing.id})
            self.orders[order.id] = order.model_copy(deep=True)
            return order

    def get_order(self, context: TenantContext, order_id: str) -> Order | None:
        with self._lock:
            found = self.orders.get(order_id)
            return found.model_copy(deep=True) if found and _in(found, context) else None

    def find_order(self, context: TenantContext, external_id: str) -> Order | None:
        with self._lock:
            found = next((o for o in self.orders.values() if _in(o, context) and o.external_id == external_id), None)
            return found.model_copy(deep=True) if found else None

    def list_orders(self, context, *, channel=None, statuses=None, since=None, until=None, query=None, limit=50, offset=0):  # type: ignore[no-untyped-def]
        with self._lock:
            items = [
                order
                for order in self.orders.values()
                if _in(order, context)
                and (channel is None or order.channel == channel)
                and (not statuses or order.status in statuses)
                and (since is None or order.purchased_at >= since)
                and (until is None or order.purchased_at < until)
                and order_matches(order, query or "")
            ]
            items.sort(key=lambda order: (order.purchased_at, order.id), reverse=True)
            return [order.model_copy(deep=True) for order in items[offset : offset + limit]], len(items)

    def orders_between(self, context: TenantContext, start: datetime, end: datetime, *, limit: int) -> list[Order]:
        items, _ = self.list_orders(context, since=start, until=end, limit=limit, offset=0)
        return items

    # customers ------------------------------------------------------------------------

    def upsert_customer(self, customer: Customer) -> Customer:
        with self._lock:
            existing = next(
                (
                    item
                    for item in self.customers.values()
                    if item.connector_id == customer.connector_id and item.ref_hash == customer.ref_hash
                    and item.tenant_id == customer.tenant_id and item.workspace_id == customer.workspace_id
                ),
                None,
            )
            if existing is not None:
                customer = customer.model_copy(update={"id": existing.id, "first_seen_at": existing.first_seen_at})
            self.customers[customer.id] = customer.model_copy(deep=True)
            return customer

    def get_customer(self, context: TenantContext, customer_id: str) -> Customer | None:
        with self._lock:
            found = self.customers.get(customer_id)
            return found.model_copy(deep=True) if found and _in(found, context) else None

    # returns and money --------------------------------------------------------------------

    def upsert_return(self, record: ReturnRecord) -> ReturnRecord:
        with self._lock:
            existing = next(
                (r for r in self.returns.values() if r.connector_id == record.connector_id and r.external_id == record.external_id and _in(r, _ctx(record))),
                None,
            )
            if existing is not None:
                record = record.model_copy(update={"id": existing.id})
            self.returns[record.id] = record.model_copy(deep=True)
            return record

    def list_returns(self, context: TenantContext, *, since: datetime | None, limit: int, offset: int) -> tuple[list[ReturnRecord], int]:
        with self._lock:
            items = [r for r in self.returns.values() if _in(r, context) and (since is None or r.returned_at >= since)]
            items.sort(key=lambda r: (r.returned_at, r.id), reverse=True)
            return [r.model_copy(deep=True) for r in items[offset : offset + limit]], len(items)

    def upsert_fee(self, record: FeeRecord) -> FeeRecord:
        with self._lock:
            existing = next(
                (f for f in self.fees.values() if f.connector_id == record.connector_id and f.external_id == record.external_id and _in(f, _ctx(record))),
                None,
            )
            if existing is not None:
                record = record.model_copy(update={"id": existing.id})
            self.fees[record.id] = record.model_copy(deep=True)
            return record

    def list_fees(self, context: TenantContext, *, since: datetime, until: datetime) -> list[FeeRecord]:
        with self._lock:
            return [f.model_copy(deep=True) for f in self.fees.values() if _in(f, context) and since <= f.posted_at < until]

    # alerts -------------------------------------------------------------------------------

    def raise_alert(self, alert: Alert) -> tuple[Alert, bool]:
        with self._lock:
            existing = next(
                (a for a in self.alerts.values() if _in(a, _ctx(alert)) and a.dedupe_key == alert.dedupe_key and a.status != AlertStatus.RESOLVED),
                None,
            )
            if existing is not None:
                existing.title, existing.detail, existing.severity, existing.updated_at = alert.title, alert.detail, alert.severity, alert.updated_at
                return existing.model_copy(deep=True), False
            self.alerts[alert.id] = alert.model_copy(deep=True)
            return alert, True

    def resolve_alerts(self, context: TenantContext, kind: str, keep_keys: set[str], *, connector_id: str | None = None) -> int:
        with self._lock:
            count = 0
            for alert in self.alerts.values():
                if (
                    _in(alert, context)
                    and alert.kind == kind
                    and alert.status != AlertStatus.RESOLVED
                    and alert.dedupe_key not in keep_keys
                    and (connector_id is None or alert.connector_id == connector_id)
                ):
                    alert.status = AlertStatus.RESOLVED
                    count += 1
            return count

    def list_alerts(self, context: TenantContext, *, status: AlertStatus | None, limit: int, offset: int) -> tuple[list[Alert], int]:
        with self._lock:
            items = [a for a in self.alerts.values() if _in(a, context) and (status is None or a.status == status)]
            items.sort(key=lambda a: (a.updated_at, a.id), reverse=True)
            return [a.model_copy(deep=True) for a in items[offset : offset + limit]], len(items)

    def get_alert(self, context: TenantContext, alert_id: str) -> Alert | None:
        with self._lock:
            found = self.alerts.get(alert_id)
            return found.model_copy(deep=True) if found and _in(found, context) else None

    def save_alert(self, alert: Alert) -> Alert:
        with self._lock:
            self.alerts[alert.id] = alert.model_copy(deep=True)
            return alert

    # settings -----------------------------------------------------------------------------

    def get_settings(self, context: TenantContext) -> CommerceSettings | None:
        with self._lock:
            found = self.settings.get(_scope(context))
            return found.model_copy() if found else None

    def save_settings(self, context: TenantContext, settings: CommerceSettings) -> CommerceSettings:
        with self._lock:
            self.settings[_scope(context)] = settings.model_copy()
            return settings

    # scheduler (memory backend only; PostgreSQL discovers through anum_maintenance) ---------

    def due_connectors(self, now: datetime, limit: int) -> list[tuple[str, str, str]]:
        with self._lock:
            due = [
                c
                for c in self.connectors.values()
                if c.enabled and c.credentials_configured and (c.next_sync_at is None or c.next_sync_at <= now)
            ]
            due.sort(key=lambda c: c.next_sync_at or c.created_at)
            return [(c.tenant_id, c.workspace_id, c.id) for c in due[:limit]]


def _ctx(item: object) -> TenantContext:
    return TenantContext(tenant_id=getattr(item, "tenant_id"), workspace_id=getattr(item, "workspace_id"), user_id="system")


commerce_store = InMemoryCommerceStore()


@contextmanager
def open_commerce_store(context: TenantContext) -> Iterator[CommerceStore]:
    """One unit of work in the caller's tenant and workspace scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.commerce_repository import SqlAlchemyCommerceStore

        return SqlAlchemyCommerceStore(session, context)

    with open_scoped_store(context, commerce_store, sql_store) as store:
        yield store
