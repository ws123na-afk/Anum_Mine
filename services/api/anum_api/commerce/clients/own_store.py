"""The owner's own store: one generic connector, four ways in (docs/commerce.md#own-store).

``anum360`` (the default)
    The owner's ANUM 360 store (FastAPI + MongoDB). ANUM signs in to the store's admin
    API as a dedicated staff account (``POST /api/admin/login``) and reads
    ``/api/admin/inventory`` (products, SKU, stock, prices), ``/api/admin/orders`` and
    ``/api/returns/admin/all``. Give that staff account only ``orders.view``,
    ``products.view``, ``inventory.view`` and ``returns.view`` (plus ``inventory.adjust``
    to let approved stock changes through): without ``orders.contact`` the store masks
    customer contact details, so none reach ANUM. The store requires two-step
    verification for money keys (``products.price``), so price changes for this store
    stay in the store's own admin unless the owner gives the account that key.
``rest``
    Any store that implements ANUM's small own-store REST contract (bearer API key):
    ``GET /products``, ``GET /orders?updated_since=``, ``GET /returns?updated_since=``,
    ``PUT /products/{sku}/price`` and ``PUT /products/{sku}/stock``.
``supabase``
    Read-only pull from a Supabase project through PostgREST with a key that can only
    read (a dedicated role or RLS read policies). Table and column names are mapped in
    the connector's settings. Writes are refused: a read-only connection never changes
    the store.
``webhook``
    The store pushes signed events (``order``, ``product``, ``return``) to
    ``POST /api/v1/commerce/webhooks/{tenant}/{workspace}/{connector}``; nothing is pulled.

Every owner-supplied URL passes the SSRF guard (``commerce.http.check_owner_url``).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from ..http import ChannelAuthError, ChannelError, check_owner_url, request_json
from ..models import OWN_STORE_MARKETPLACE, OrderStatus, OwnStoreMode
from .base import (
    PageResult,
    RemoteCompetition,
    RemoteLine,
    RemoteListing,
    RemoteOrder,
    RemoteReturn,
    UnsupportedOperation,
    integer,
    iso,
    number,
    parse_time,
    text,
)

RATE = (5.0, 10)
PAGE = 100

ANUM360_PATHS = {
    "login": "/api/admin/login",
    "inventory": "/api/admin/inventory",
    "orders": "/api/admin/orders",
    "returns": "/api/returns/admin/all",
    "adjust": "/api/admin/inventory/adjust",
    "product": "/api/products/{id}",
}

# ANUM 360's order lifecycle (utils/order_lifecycle.py in the store) -> the unified status.
ANUM360_STATUS = {
    "pending": OrderStatus.PENDING,
    "confirmed": OrderStatus.UNSHIPPED,
    "processing": OrderStatus.UNSHIPPED,
    "shipped": OrderStatus.SHIPPED,
    "out_for_delivery": OrderStatus.SHIPPED,
    "delivered": OrderStatus.DELIVERED,
    "completed": OrderStatus.DELIVERED,
    "cancelled": OrderStatus.CANCELLED,
    "refunded": OrderStatus.RETURNED,
}

CONTRACT_STATUS = {status.value: status for status in OrderStatus} | {
    "paid": OrderStatus.UNSHIPPED,
    "confirmed": OrderStatus.UNSHIPPED,
    "processing": OrderStatus.UNSHIPPED,
    "fulfilled": OrderStatus.SHIPPED,
    "completed": OrderStatus.DELIVERED,
    "canceled": OrderStatus.CANCELLED,
    "refunded": OrderStatus.RETURNED,
}

SUPABASE_DEFAULTS: dict[str, Any] = {
    "tables": {"orders": "orders", "order_items": "order_items", "products": "products", "returns": "returns"},
    "columns": {
        "orders": {
            "id": "id", "status": "status", "created_at": "created_at", "updated_at": "updated_at",
            "total": "total", "currency": "currency", "city": "city", "country": "country", "ship_by": "ship_by",
        },
        "order_items": {"order_id": "order_id", "sku": "sku", "title": "title", "quantity": "quantity", "unit_price": "unit_price"},
        "products": {
            "sku": "sku", "id": "id", "title": "title", "title_ar": "title_ar", "brand": "brand",
            "price": "price", "currency": "currency", "quantity": "stock", "active": "is_active", "updated_at": "updated_at",
        },
        "returns": {
            "id": "id", "order_id": "order_id", "sku": "sku", "quantity": "quantity", "reason": "reason",
            "status": "status", "amount": "amount", "currency": "currency", "created_at": "created_at",
        },
    },
}

# connector key -> (store admin token, monotonic expiry)
_store_tokens: dict[str, tuple[str, float]] = {}


def reset_tokens() -> None:
    _store_tokens.clear()


# -- the own-store contract (rest, webhook and normalized supabase rows) ------------------


def contract_listing(row: dict[str, Any], default_currency: str | None) -> RemoteListing | None:
    sku = str(row.get("sku") or row.get("id") or "").strip()
    if not sku:
        return None
    active = row.get("active", row.get("is_active", True))
    quantity = integer(row.get("quantity", row.get("stock")))
    return RemoteListing(
        marketplace_id=OWN_STORE_MARKETPLACE,
        sku=sku[:120],
        external_id=str(row.get("id")) if row.get("id") is not None else None,
        title=text(row.get("title") or row.get("name")),
        title_ar=text(row.get("title_ar")) or None,
        brand=text(row.get("brand"), 120) or None,
        price=number(row.get("price")),
        currency=row.get("currency") or default_currency,
        quantity=quantity,
        status="inactive" if active is False else ("out_of_stock" if quantity == 0 else "active"),
        fulfillment="merchant",
    )


def contract_order(row: dict[str, Any], default_currency: str | None, fallback_time: datetime) -> RemoteOrder | None:
    order_id = str(row.get("number") or row.get("id") or "").strip()
    if not order_id:
        return None
    lines = []
    for item in row.get("items") or []:
        if not isinstance(item, dict):
            continue
        quantity = integer(item.get("quantity")) or 1
        unit = number(item.get("unit_price", item.get("price")))
        total = number(item.get("total"))
        lines.append(
            RemoteLine(
                sku=str(item.get("sku") or item.get("product_id") or "unknown")[:120],
                external_line_id=str(item["id"]) if item.get("id") is not None else None,
                title=text(item.get("title") or item.get("name")),
                quantity=quantity,
                unit_price=unit,
                total=total if total is not None else (round(unit * quantity, 2) if unit is not None else None),
            )
        )
    return RemoteOrder(
        marketplace_id=OWN_STORE_MARKETPLACE,
        external_id=order_id[:120],
        status=CONTRACT_STATUS.get(str(row.get("status") or "").lower(), OrderStatus.PENDING),
        purchased_at=parse_time(row.get("created_at")) or fallback_time,
        updated_at=parse_time(row.get("updated_at")),
        ship_by=parse_time(row.get("ship_by")),
        total_amount=number(row.get("total")) or round(sum(line.total or 0 for line in lines), 2),
        currency=row.get("currency") or default_currency,
        item_count=sum(line.quantity for line in lines),
        fulfillment="merchant",
        ship_city=text(row.get("city"), 120) or None,
        ship_country=(text(row.get("country"), 2).upper() or None),
        lines=lines,
    )


def contract_return(row: dict[str, Any], fallback_time: datetime) -> RemoteReturn | None:
    order_id = str(row.get("order_number") or row.get("order_id") or "").strip()
    if not order_id:
        return None
    return RemoteReturn(
        marketplace_id=OWN_STORE_MARKETPLACE,
        external_id=str(row.get("id") or f"{order_id}:{row.get('sku')}"),
        order_external_id=order_id,
        sku=str(row["sku"]) if row.get("sku") else None,
        quantity=integer(row.get("quantity")) or 1,
        reason=text(row.get("reason"), 200) or None,
        status=str(row.get("status") or "requested").lower()[:40],
        amount=number(row.get("amount", row.get("refund_amount"))),
        currency=row.get("currency"),
        returned_at=parse_time(row.get("created_at")) or fallback_time,
    )


class OwnStoreClient:
    channel_name = "Own store"

    def __init__(self, connector_key: str, credentials: dict[str, str], settings: dict[str, Any]) -> None:
        self.key = connector_key
        self.mode = OwnStoreMode(settings.get("mode") or OwnStoreMode.ANUM360)
        self.credentials = credentials
        self.settings = settings
        self.currency = str(settings.get("currency") or ("AED" if self.mode == OwnStoreMode.ANUM360 else "SAR"))[:3]
        self.base = check_owner_url(str(settings.get("base_url") or "")) if self.mode != OwnStoreMode.WEBHOOK else ""
        self.status_window = timedelta(days=int(settings.get("status_window_days") or 14))
        self.paths = {**ANUM360_PATHS, **{k: v for k, v in (settings.get("paths") or {}).items() if k in ANUM360_PATHS and isinstance(v, str) and v.startswith("/")}}
        self._sku_by_id: dict[str, str] = {}
        self._products: dict[str, dict[str, Any]] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        if self.mode == OwnStoreMode.WEBHOOK:
            return frozenset({"webhook"})
        if self.mode == OwnStoreMode.SUPABASE:
            return frozenset({"listings", "orders", "returns"})
        return frozenset({"listings", "orders", "returns", "update_price", "update_stock"})

    # -- plumbing ------------------------------------------------------------------

    async def _call(self, method: str, path: str, operation: str, *, params: dict[str, Any] | None = None, json_body: Any = None, auth: bool = True) -> Any:
        headers = {"accept": "application/json"}
        if self.mode == OwnStoreMode.ANUM360 and auth:
            headers["authorization"] = f"Bearer {await self._anum360_token()}"
        elif self.mode == OwnStoreMode.REST:
            headers["authorization"] = f"Bearer {self.credentials.get('api_key', '')}"
        elif self.mode == OwnStoreMode.SUPABASE:
            key = self.credentials.get("api_key", "")
            headers.update({"apikey": key, "authorization": f"Bearer {key}"})
        try:
            return await request_json(
                method,
                f"{self.base}{path}",
                channel_name="Your store",
                operation=operation,
                connector_key=self.key,
                rate=RATE[0],
                burst=RATE[1],
                headers=headers,
                params=params,
                json_body=json_body,
                owner_url=True,
            )
        except ChannelAuthError:
            _store_tokens.pop(self.key, None)
            raise

    async def _anum360_token(self) -> str:
        cached = _store_tokens.get(self.key)
        if cached and cached[1] > time.monotonic() + 60:
            return cached[0]
        try:
            body = await self._call(
                "POST",
                self.paths["login"],
                "staff sign-in",
                json_body={"email": self.credentials.get("email", ""), "password": self.credentials.get("password", "")},
                auth=False,
            )
        except ChannelAuthError as exc:
            if "two-factor" in exc.message.lower():
                raise ChannelAuthError(
                    "The store asks this staff account for a two-step code. Connect ANUM with a staff account that "
                    "holds only view keys (orders.view, products.view, inventory.view, returns.view), which the store "
                    "does not require two-step verification for.",
                    status=exc.status,
                    code="two_factor_required",
                ) from None
            raise
        if body.get("twofa_setup_required") or body.get("scope"):
            raise ChannelAuthError(
                "The store requires two-step verification for this staff account. Use a staff account with view keys only.",
                code="two_factor_required",
            )
        token = str(body.get("token") or "")
        if not token:
            raise ChannelAuthError("The store's sign-in returned no session.", code="no_token")
        _store_tokens[self.key] = (token, time.monotonic() + float(body.get("expires_in") or 3600))
        return token

    # -- health --------------------------------------------------------------------

    async def check(self) -> str:
        if self.mode == OwnStoreMode.WEBHOOK:
            return "Waiting for your store to send events to the webhook URL."
        if self.mode == OwnStoreMode.ANUM360:
            body = await self._call("GET", self.paths["inventory"], "inventory")
            return f"Connected to your store: {integer(body.get('total_products')) or len(body.get('products') or [])} products."
        if self.mode == OwnStoreMode.SUPABASE:
            table = self._table("products")
            await self._call("GET", f"/rest/v1/{quote(table, safe='')}", "read products", params={"select": "*", "limit": 1})
            return "Connected to your Supabase project (read-only)."
        await self._call("GET", "/products", "list products", params={"limit": 1})
        return "Connected to your store's API."

    # -- listings ------------------------------------------------------------------

    async def fetch_listings(self, marketplace_id: str, page_token: str | None) -> PageResult:
        if self.mode == OwnStoreMode.WEBHOOK:
            return PageResult(items=[])
        if self.mode == OwnStoreMode.ANUM360:
            body = await self._call("GET", self.paths["inventory"], "inventory")
            items = []
            for row in body.get("products") or []:
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                pricing = row.get("pricing") or {}
                sale, retail = number(pricing.get("sale")), number(pricing.get("retail"))
                sku = str(row.get("sku") or row["id"])[:120]
                self._sku_by_id[str(row["id"])] = sku
                self._products[sku] = row
                name = row.get("name") or {}
                quantity = integer(row.get("stock"))
                items.append(
                    RemoteListing(
                        marketplace_id=OWN_STORE_MARKETPLACE,
                        sku=sku,
                        external_id=str(row["id"]),
                        title=text(name.get("en") if isinstance(name, dict) else name),
                        title_ar=(text(name.get("ar")) or None) if isinstance(name, dict) else None,
                        brand=text(row.get("brand"), 120) or None,
                        price=sale if sale else retail,
                        currency=self.currency,
                        quantity=quantity,
                        status="out_of_stock" if quantity == 0 else "active",
                        fulfillment="merchant",
                    )
                )
            # The store returns its catalogue in one response (capped, lowest stock first).
            return PageResult(items=items)
        if self.mode == OwnStoreMode.SUPABASE:
            offset = int(page_token or 0)
            cols = self._columns("products")
            rows = await self._call(
                "GET",
                f"/rest/v1/{quote(self._table('products'), safe='')}",
                "read products",
                params={"select": "*", "order": f"{cols['sku']}.asc", "limit": PAGE, "offset": offset},
            )
            items = [contract_listing(self._normalize(row, cols), self.currency) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            return PageResult(items=[i for i in items if i], next_token=str(offset + PAGE) if isinstance(rows, list) and len(rows) == PAGE else None)
        body = await self._call("GET", "/products", "list products", params={"cursor": page_token, "limit": PAGE})
        listings = [contract_listing(row, self.currency) for row in body.get("products") or [] if isinstance(row, dict)]
        return PageResult(items=[i for i in listings if i], next_token=body.get("next_cursor"))

    # -- orders ------------------------------------------------------------------------

    async def fetch_orders(self, since: datetime, page_token: str | None) -> PageResult:
        if self.mode == OwnStoreMode.WEBHOOK:
            return PageResult(items=[])
        if self.mode == OwnStoreMode.ANUM360:
            return await self._anum360_orders(since, page_token)
        if self.mode == OwnStoreMode.SUPABASE:
            return await self._supabase_orders(since, page_token)
        body = await self._call("GET", "/orders", "list orders", params={"updated_since": iso(since), "cursor": page_token, "limit": PAGE})
        orders = [contract_order(row, self.currency, since) for row in body.get("orders") or [] if isinstance(row, dict)]
        return PageResult(items=[o for o in orders if o], next_token=body.get("next_cursor"))

    async def _anum360_orders(self, since: datetime, page_token: str | None) -> PageResult:
        """Newest first by ``created_at``; reads back through the status window so later
        status changes (shipped, delivered, refunded) of recent orders are picked up."""
        skip = int(page_token or 0)
        rows = await self._call("GET", self.paths["orders"], "list orders", params={"limit": PAGE, "skip": skip})
        rows = rows if isinstance(rows, list) else rows.get("orders") or []
        window_start = since - self.status_window
        orders: list[RemoteOrder] = []
        reached_end = len(rows) < PAGE
        for row in rows:
            if not isinstance(row, dict):
                continue
            created = parse_time(row.get("created_at")) or since
            if created < window_start:
                reached_end = True
                continue
            delivery = row.get("delivery_info") or {}
            address = delivery.get("address") or {}
            lines = []
            for item in row.get("items") or []:
                if not isinstance(item, dict):
                    continue
                product_id = str(item.get("product_id") or "")
                quantity = integer(item.get("quantity")) or 1
                lines.append(
                    RemoteLine(
                        sku=self._sku_by_id.get(product_id, product_id or "unknown"),
                        external_line_id=product_id or None,
                        title=text(item.get("name")),
                        quantity=quantity,
                        unit_price=number(item.get("price")),
                        total=number(item.get("total")),
                    )
                )
            country = str(delivery.get("country") or address.get("country") or "AE")
            orders.append(
                RemoteOrder(
                    marketplace_id=OWN_STORE_MARKETPLACE,
                    external_id=str(row.get("order_number") or row.get("id")),
                    status=ANUM360_STATUS.get(str(row.get("order_status") or "pending"), OrderStatus.PENDING),
                    purchased_at=created,
                    updated_at=parse_time(row.get("updated_at")),
                    total_amount=number(row.get("total")) or 0.0,
                    currency=self.currency,
                    item_count=sum(line.quantity for line in lines),
                    fulfillment="merchant",
                    ship_city=text(address.get("city"), 120) or None,
                    ship_country="AE" if country.upper() in ("UAE", "AE", "") else country.upper()[:2],
                    lines=lines,
                )
            )
        return PageResult(items=orders, next_token=None if reached_end else str(skip + PAGE))

    async def _supabase_orders(self, since: datetime, page_token: str | None) -> PageResult:
        offset = int(page_token or 0)
        cols = self._columns("orders")
        rows = await self._call(
            "GET",
            f"/rest/v1/{quote(self._table('orders'), safe='')}",
            "read orders",
            params={
                "select": "*",
                cols["updated_at"]: f"gt.{iso(since)}",
                "order": f"{cols['updated_at']}.asc",
                "limit": PAGE,
                "offset": offset,
            },
        )
        rows = rows if isinstance(rows, list) else []
        normalized = [self._normalize(row, cols) for row in rows if isinstance(row, dict)]
        ids = [str(row["id"]) for row in normalized if row.get("id") is not None]
        if ids:
            item_cols = self._columns("order_items")
            items = await self._call(
                "GET",
                f"/rest/v1/{quote(self._table('order_items'), safe='')}",
                "read order items",
                params={"select": "*", item_cols["order_id"]: "in.(" + ",".join(f'"{i}"' for i in ids) + ")"},
            )
            by_order: dict[str, list[dict[str, Any]]] = {}
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict):
                    flat = self._normalize(item, item_cols)
                    by_order.setdefault(str(flat.get("order_id")), []).append(flat)
            for row in normalized:
                row["items"] = by_order.get(str(row.get("id")), [])
        orders = [contract_order(row, self.currency, since) for row in normalized]
        return PageResult(items=[o for o in orders if o], next_token=str(offset + PAGE) if len(rows) == PAGE else None)

    # -- returns ------------------------------------------------------------------------

    async def fetch_returns(self, since: datetime, page_token: str | None) -> PageResult:
        if self.mode == OwnStoreMode.WEBHOOK:
            return PageResult(items=[])
        if self.mode == OwnStoreMode.ANUM360:
            skip = int(page_token or 0)
            body = await self._call(
                "GET", self.paths["returns"], "list returns", params={"date_from": iso(since - self.status_window), "limit": PAGE, "skip": skip}
            )
            items: list[RemoteReturn] = []
            for row in body.get("returns") or []:
                if not isinstance(row, dict) or not row.get("order_number"):
                    continue
                returned = parse_time(row.get("created_at")) or since
                for index, item in enumerate(row.get("items") or [{}]):
                    product_id = str((item or {}).get("product_id") or "")
                    items.append(
                        RemoteReturn(
                            marketplace_id=OWN_STORE_MARKETPLACE,
                            external_id=f"{row.get('id') or row['order_number']}:{index}",
                            order_external_id=str(row["order_number"]),
                            sku=self._sku_by_id.get(product_id, product_id or None),
                            quantity=integer((item or {}).get("quantity")) or 1,
                            reason=text(row.get("reason"), 200) or None,
                            status=str(row.get("status") or "pending")[:40],
                            amount=number(row.get("refund_amount")) if index == 0 else None,
                            currency=self.currency,
                            returned_at=returned,
                        )
                    )
            total = integer(body.get("total")) or 0
            return PageResult(items=items, next_token=str(skip + PAGE) if skip + PAGE < total else None)
        if self.mode == OwnStoreMode.SUPABASE:
            cols = self._columns("returns")
            offset = int(page_token or 0)
            rows = await self._call(
                "GET",
                f"/rest/v1/{quote(self._table('returns'), safe='')}",
                "read returns",
                params={"select": "*", cols["created_at"]: f"gt.{iso(since)}", "order": f"{cols['created_at']}.asc", "limit": PAGE, "offset": offset},
            )
            rows = rows if isinstance(rows, list) else []
            found = [contract_return(self._normalize(row, cols), since) for row in rows if isinstance(row, dict)]
            return PageResult(items=[r for r in found if r], next_token=str(offset + PAGE) if len(rows) == PAGE else None)
        body = await self._call("GET", "/returns", "list returns", params={"updated_since": iso(since), "cursor": page_token, "limit": PAGE})
        found = [contract_return(row, since) for row in body.get("returns") or [] if isinstance(row, dict)]
        return PageResult(items=[r for r in found if r], next_token=body.get("next_cursor"))

    async def fetch_fees(self, since: datetime, page_token: str | None) -> PageResult:
        return PageResult(items=[])

    async def fetch_competition(self, marketplace_id: str, external_id: str) -> RemoteCompetition | None:
        return None

    # -- changes (executed only by the approved tools) ------------------------------------

    def _refuse_read_only(self) -> None:
        if self.mode == OwnStoreMode.SUPABASE:
            raise UnsupportedOperation("This own-store connection is read-only (Supabase). Change it in your store, or connect the store's API.")
        if self.mode == OwnStoreMode.WEBHOOK:
            raise UnsupportedOperation("This own-store connection only receives events. Change it in your store, or connect the store's API.")

    async def update_price(self, marketplace_id: str, sku: str, external_id: str | None, product_type: str | None, price: float, currency: str) -> str:
        self._refuse_read_only()
        if self.mode == OwnStoreMode.ANUM360:
            if not external_id:
                raise ChannelError("The store's product id is unknown; sync the store first.", code="unknown_product")
            inventory = await self._call("GET", self.paths["inventory"], "inventory")
            product = next((row for row in inventory.get("products") or [] if str(row.get("id")) == external_id), None)
            if product is None:
                raise ChannelError("The product is no longer in the store.", code="unknown_product")
            pricing = dict(product.get("pricing") or {})
            pricing["retail"] = price
            pricing.pop("sale", None)
            try:
                await self._call("PUT", self.paths["product"].replace("{id}", quote(external_id, safe="")), "set price", json_body={"pricing": pricing})
            except ChannelAuthError as exc:
                raise ChannelError(
                    "The store did not let ANUM's staff account set prices (products.price needs two-step verification "
                    "in the store). Change the price in the store's admin.",
                    status=exc.status,
                    code="price_owner_only",
                ) from None
            return "Your store saved the new price."
        await self._call("PUT", f"/products/{quote(sku, safe='')}/price", "set price", json_body={"price": price, "currency": currency})
        return "Your store saved the new price."

    async def update_stock(self, marketplace_id: str, sku: str, external_id: str | None, product_type: str | None, quantity: int) -> str:
        self._refuse_read_only()
        if self.mode == OwnStoreMode.ANUM360:
            if not external_id:
                raise ChannelError("The store's product id is unknown; sync the store first.", code="unknown_product")
            inventory = await self._call("GET", self.paths["inventory"], "inventory")
            product = next((row for row in inventory.get("products") or [] if str(row.get("id")) == external_id), None)
            if product is None:
                raise ChannelError("The product is no longer in the store.", code="unknown_product")
            # The store adjusts stock by a difference; it is read right before the change.
            delta = quantity - (integer(product.get("stock")) or 0)
            if delta == 0:
                return "The store already has that stock."
            await self._call(
                "POST", self.paths["adjust"], "adjust stock", params={"product_id": external_id, "adjustment": delta, "reason": "anum"}
            )
            return f"Your store adjusted the stock by {delta:+d} to {quantity}."
        await self._call("PUT", f"/products/{quote(sku, safe='')}/stock", "set stock", json_body={"quantity": quantity})
        return "Your store saved the new stock."

    # -- supabase mapping -------------------------------------------------------------------

    def _table(self, name: str) -> str:
        tables = {**SUPABASE_DEFAULTS["tables"], **(self.settings.get("tables") or {})}
        return str(tables[name])

    def _columns(self, name: str) -> dict[str, str]:
        custom = (self.settings.get("columns") or {}).get(name) or {}
        return {**SUPABASE_DEFAULTS["columns"][name], **{k: str(v) for k, v in custom.items() if isinstance(v, str)}}

    @staticmethod
    def _normalize(row: dict[str, Any], columns: dict[str, str]) -> dict[str, Any]:
        """A store row under ANUM's contract names (``columns`` maps contract -> store column)."""
        return {contract: row.get(column) for contract, column in columns.items()}
