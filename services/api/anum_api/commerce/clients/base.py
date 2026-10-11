"""What every channel client returns: the channel's data before it becomes store rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..models import OrderStatus


class RemoteListing(BaseModel):
    marketplace_id: str
    sku: str
    external_id: str | None = None
    title: str = ""
    title_ar: str | None = None
    brand: str | None = None
    product_type: str | None = None
    price: float | None = None
    currency: str | None = None
    quantity: int | None = None
    status: str = "active"
    fulfillment: str | None = None


class RemoteLine(BaseModel):
    sku: str
    external_line_id: str | None = None
    title: str = ""
    quantity: int = 1
    unit_price: float | None = None
    total: float | None = None


class RemoteBuyer(BaseModel):
    """Buyer details; only produced when the connector is set to fetch them."""

    reference: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None


class RemoteOrder(BaseModel):
    marketplace_id: str
    external_id: str
    status: OrderStatus
    purchased_at: datetime
    updated_at: datetime | None = None
    ship_by: datetime | None = None
    total_amount: float = 0.0
    currency: str | None = None
    item_count: int = 0
    fulfillment: str | None = None
    ship_city: str | None = None
    ship_country: str | None = None
    lines: list[RemoteLine] = Field(default_factory=list)
    buyer: RemoteBuyer | None = None


class RemoteReturn(BaseModel):
    marketplace_id: str | None = None
    external_id: str  # stable id for upserts
    order_external_id: str
    sku: str | None = None
    quantity: int = 1
    reason: str | None = None
    status: str = "refunded"
    amount: float | None = None
    currency: str | None = None
    returned_at: datetime


class RemoteFee(BaseModel):
    external_id: str
    order_external_id: str | None = None
    kind: str
    description: str = ""
    amount: float
    currency: str | None = None
    posted_at: datetime


class RemoteCompetition(BaseModel):
    buy_box_price: float | None = None
    buy_box_winner: bool | None = None
    lowest_competitor_price: float | None = None


class PageResult(BaseModel):
    items: list[Any]
    next_token: str | None = None


class ChannelClient(Protocol):
    channel_name: str
    capabilities: frozenset[str]

    async def check(self) -> str: ...
    async def fetch_listings(self, marketplace_id: str, page_token: str | None) -> PageResult: ...
    async def fetch_orders(self, since: datetime, page_token: str | None) -> PageResult: ...
    async def fetch_returns(self, since: datetime, page_token: str | None) -> PageResult: ...
    async def fetch_fees(self, since: datetime, page_token: str | None) -> PageResult: ...
    async def fetch_competition(self, marketplace_id: str, external_id: str) -> RemoteCompetition | None: ...
    async def update_price(self, marketplace_id: str, sku: str, external_id: str | None, product_type: str | None, price: float, currency: str) -> str: ...
    async def update_stock(self, marketplace_id: str, sku: str, external_id: str | None, product_type: str | None, quantity: int) -> str: ...


def parse_time(value: Any) -> datetime | None:
    """ISO 8601 (``Z`` or offset) or epoch seconds -> aware UTC datetime; None when absent or invalid."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("Amount", value.get("amount", value.get("CurrencyAmount")))
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def text(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("en") or next((v for v in value.values() if isinstance(v, str)), "")
    return " ".join(str(value).split())[:limit]


class UnsupportedOperation(Exception):
    """The channel (or this connection mode) does not offer the operation; the message says why."""
