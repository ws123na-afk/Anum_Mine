"""noon seller API client (docs/commerce.md#noon).

noon's API Platform reference is issued to approved sellers (ask noon's integration team,
automation@noon.com) and is not public, so the request paths are data, not code:
``DEFAULT_PATHS`` holds the paths this adapter was written against and a connector may
override any of them in its ``paths`` setting without a release. The credential is the
API key noon issues for the seller's integration, sent as a bearer token, with the
partner (project) id in ``X-Partner-Id`` when set.

The responses are read defensively: each field is looked up under the names noon's
documentation and integrators use (``partner_sku``/``sku``, ``order_nr``/``id`` ...), so a
renamed field degrades to an empty value instead of a failed sync.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

from ..config import commerce_config
from ..http import check_owner_url, request_json
from ..models import NOON_MARKETPLACES, OrderStatus
from .base import PageResult, RemoteCompetition, RemoteLine, RemoteListing, RemoteOrder, RemoteReturn, integer, iso, number, parse_time, text

DEFAULT_PATHS = {
    "check": "/partner/v1/whoami",
    "offers": "/offer/v1/offers",
    "offer": "/offer/v1/offers/{sku}",
    "orders": "/order/v1/orders",
    "returns": "/return/v1/returns",
}
# noon throttles per seller; these keep well under its limits.
RATE = (2.0, 5)

STATUS = {
    "created": OrderStatus.PENDING,
    "pending": OrderStatus.PENDING,
    "confirmed": OrderStatus.UNSHIPPED,
    "processing": OrderStatus.UNSHIPPED,
    "ready_for_pickup": OrderStatus.UNSHIPPED,
    "packed": OrderStatus.UNSHIPPED,
    "shipped": OrderStatus.SHIPPED,
    "picked_up": OrderStatus.SHIPPED,
    "in_transit": OrderStatus.SHIPPED,
    "delivered": OrderStatus.DELIVERED,
    "cancelled": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
    "returned": OrderStatus.RETURNED,
}


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


class NoonClient:
    channel_name = "noon"
    capabilities = frozenset({"listings", "orders", "returns", "update_price", "update_stock"})

    def __init__(self, connector_key: str, credentials: dict[str, str], settings: dict[str, Any], marketplaces: list[str]) -> None:
        self.key = connector_key
        self.api_key = credentials.get("api_key", "")
        self.partner_id = str(settings.get("partner_id") or "")
        base = str(settings.get("base_url") or commerce_config.noon_base_url)
        self.base = check_owner_url(base) if settings.get("base_url") else base.rstrip("/")
        self.owner_url = bool(settings.get("base_url"))
        self.paths = {**DEFAULT_PATHS, **{k: v for k, v in (settings.get("paths") or {}).items() if k in DEFAULT_PATHS and isinstance(v, str) and v.startswith("/")}}
        self.marketplaces = [m for m in marketplaces if m in NOON_MARKETPLACES] or list(NOON_MARKETPLACES)

    async def _call(self, method: str, path: str, operation: str, *, params: dict[str, Any] | None = None, json_body: Any = None) -> Any:
        headers = {"authorization": f"Bearer {self.api_key}", "accept": "application/json"}
        if self.partner_id:
            headers["x-partner-id"] = self.partner_id
        return await request_json(
            method,
            f"{self.base}{path}",
            channel_name="noon",
            operation=operation,
            connector_key=self.key,
            rate=RATE[0],
            burst=RATE[1],
            headers=headers,
            params=params,
            json_body=json_body,
            owner_url=self.owner_url,
        )

    async def check(self) -> str:
        body = await self._call("GET", self.paths["check"], "check")
        name = _first(body if isinstance(body, dict) else {}, "partner_name", "name", "store_name")
        return f"Connected to noon{f' as {name}' if name else ''}"

    async def fetch_listings(self, marketplace_id: str, page_token: str | None) -> PageResult:
        body = await self._call(
            "GET", self.paths["offers"], "list offers", params={"country_code": marketplace_id, "cursor": page_token, "limit": 100}
        )
        currency = NOON_MARKETPLACES.get(marketplace_id, {}).get("currency")
        items: list[RemoteListing] = []
        for row in _rows(body, "offers", "items", "data"):
            sku = str(_first(row, "partner_sku", "sku", "seller_sku") or "").strip()
            if not sku:
                continue
            sale = number(_first(row, "sale_price", "offer_price"))
            price = number(_first(row, "price", "base_price"))
            active = _first(row, "is_active", "active")
            items.append(
                RemoteListing(
                    marketplace_id=marketplace_id,
                    sku=sku,
                    external_id=_first(row, "noon_sku", "zsku", "catalog_sku"),
                    title=text(_first(row, "title", "name", "title_en")),
                    title_ar=text(_first(row, "title_ar", "name_ar")) or None,
                    brand=_first(row, "brand"),
                    price=sale if sale else price,
                    currency=_first(row, "currency", "currency_code") or currency,
                    quantity=integer(_first(row, "stock", "quantity", "stock_net_gross")),
                    status="inactive" if active is False else "active",
                    fulfillment="noon" if str(_first(row, "fulfillment", "fulfillment_type") or "").lower() in ("fbn", "noon") else "merchant",
                )
            )
        return PageResult(items=items, next_token=_next(body))

    async def fetch_orders(self, since: datetime, page_token: str | None) -> PageResult:
        body = await self._call(
            "GET", self.paths["orders"], "list orders", params={"updated_after": iso(since), "cursor": page_token, "limit": 100}
        )
        orders: list[RemoteOrder] = []
        for row in _rows(body, "orders", "items", "data"):
            order_id = str(_first(row, "order_nr", "order_number", "id") or "")
            if not order_id:
                continue
            country = str(_first(row, "country_code", "country") or "").upper()[:2] or None
            marketplace = country if country in NOON_MARKETPLACES else self.marketplaces[0]
            lines = [
                RemoteLine(
                    sku=str(_first(item, "partner_sku", "sku") or "unknown"),
                    external_line_id=_first(item, "item_nr", "id"),
                    title=text(_first(item, "title", "name")),
                    quantity=integer(_first(item, "quantity", "qty")) or 1,
                    unit_price=number(_first(item, "price", "offer_price")),
                    total=number(_first(item, "total", "line_total")),
                )
                for item in (row.get("items") or row.get("lines") or [])
                if isinstance(item, dict)
            ]
            for line in lines:
                if line.total is None and line.unit_price is not None:
                    line.total = round(line.unit_price * line.quantity, 2)
            status_text = str(_first(row, "status", "order_status") or "").lower()
            orders.append(
                RemoteOrder(
                    marketplace_id=marketplace,
                    external_id=order_id,
                    status=STATUS.get(status_text, OrderStatus.PENDING),
                    purchased_at=parse_time(_first(row, "created_at", "order_created_at", "ordered_at")) or since,
                    updated_at=parse_time(_first(row, "updated_at", "last_updated_at")),
                    ship_by=parse_time(_first(row, "ship_by", "shipment_due_at", "sla_at")),
                    total_amount=number(_first(row, "total", "order_total", "grand_total")) or sum(line.total or 0 for line in lines),
                    currency=_first(row, "currency", "currency_code") or NOON_MARKETPLACES[marketplace]["currency"],
                    item_count=sum(line.quantity for line in lines),
                    fulfillment="noon" if str(_first(row, "fulfillment", "fulfillment_type") or "").lower() in ("fbn", "noon") else "merchant",
                    ship_city=_first(row, "city", "ship_city"),
                    ship_country=country,
                    lines=lines,
                )
            )
        return PageResult(items=orders, next_token=_next(body))

    async def fetch_returns(self, since: datetime, page_token: str | None) -> PageResult:
        body = await self._call(
            "GET", self.paths["returns"], "list returns", params={"updated_after": iso(since), "cursor": page_token, "limit": 100}
        )
        items: list[RemoteReturn] = []
        for row in _rows(body, "returns", "items", "data"):
            order_id = str(_first(row, "order_nr", "order_number", "order_id") or "")
            if not order_id:
                continue
            sku = _first(row, "partner_sku", "sku")
            items.append(
                RemoteReturn(
                    external_id=str(_first(row, "return_nr", "id") or f"{order_id}:{sku}"),
                    order_external_id=order_id,
                    sku=sku,
                    quantity=integer(_first(row, "quantity", "qty")) or 1,
                    reason=text(_first(row, "reason", "return_reason"), 200) or None,
                    status=str(_first(row, "status") or "requested").lower(),
                    amount=number(_first(row, "amount", "refund_amount")),
                    currency=_first(row, "currency", "currency_code"),
                    returned_at=parse_time(_first(row, "created_at", "returned_at")) or since,
                )
            )
        return PageResult(items=items, next_token=_next(body))

    async def fetch_fees(self, since: datetime, page_token: str | None) -> PageResult:
        return PageResult(items=[])

    async def fetch_competition(self, marketplace_id: str, external_id: str) -> RemoteCompetition | None:
        return None

    async def update_price(self, marketplace_id: str, sku: str, external_id: str | None, product_type: str | None, price: float, currency: str) -> str:
        await self._call(
            "PATCH",
            self.paths["offer"].replace("{sku}", quote(sku, safe="")),
            "update price",
            json_body={"country_code": marketplace_id, "price": price, "currency": currency},
        )
        return "noon accepted the new price."

    async def update_stock(self, marketplace_id: str, sku: str, external_id: str | None, product_type: str | None, quantity: int) -> str:
        await self._call(
            "PATCH",
            self.paths["offer"].replace("{sku}", quote(sku, safe="")),
            "update stock",
            json_body={"country_code": marketplace_id, "stock": quantity},
        )
        return "noon accepted the new stock."


def _rows(body: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        for key in keys:
            value = body.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _next(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    for key in ("next_cursor", "nextCursor", "next_page_token", "cursor"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    pagination = body.get("pagination")
    if isinstance(pagination, dict):
        value = pagination.get("next_cursor") or pagination.get("nextToken")
        return str(value) if value else None
    return None
