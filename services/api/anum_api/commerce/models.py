"""The unified commerce model (docs/commerce.md#data-model).

Every record belongs to one tenant and workspace. Amounts are decimals stored as
``float`` with two decimals of meaning; currencies are ISO 4217 codes as the channel
reports them (SAR for Amazon.sa and noon Saudi, AED for Amazon.ae and noon UAE).

Customers are minimized: an order keeps only the ship-to city and country. A customer
row exists only when the connector was set to fetch buyer details (off by default); its
name, email, phone and address are encrypted at rest and returned only to callers with
``commerce:pii`` (workspace owners).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .tyre import TyreSpec


class Channel(StrEnum):
    AMAZON = "amazon"
    NOON = "noon"
    OWN_STORE = "own_store"


CHANNEL_LABELS = {Channel.AMAZON: "Amazon", Channel.NOON: "noon", Channel.OWN_STORE: "Own store"}

# Amazon marketplaces this deployment supports (both on the Europe SP-API endpoint).
AMAZON_MARKETPLACES = {
    "A17E79C6D8DWNP": {"name": "Amazon.sa", "country": "SA", "currency": "SAR"},
    "A2VIGQ35RCS4UG": {"name": "Amazon.ae", "country": "AE", "currency": "AED"},
}
NOON_MARKETPLACES = {
    "SA": {"name": "noon Saudi", "country": "SA", "currency": "SAR"},
    "AE": {"name": "noon UAE", "country": "AE", "currency": "AED"},
}
OWN_STORE_MARKETPLACE = "own"


def marketplace_name(channel: Channel | str, marketplace_id: str) -> str:
    if channel == Channel.AMAZON:
        return AMAZON_MARKETPLACES.get(marketplace_id, {}).get("name", marketplace_id)
    if channel == Channel.NOON:
        return NOON_MARKETPLACES.get(marketplace_id, {}).get("name", marketplace_id)
    return "Own store"


class ConnectorHealth(StrEnum):
    PENDING = "pending"  # saved, never synced
    OK = "ok"
    THROTTLED = "throttled"  # the channel's rate limit; the next sync waits for it
    AUTH_FAILED = "auth_failed"  # credentials refused: the owner must reconnect
    ERROR = "error"
    DISABLED = "disabled"


class OwnStoreMode(StrEnum):
    ANUM360 = "anum360"  # the owner's ANUM 360 store: its admin API with a view-only staff account
    REST = "rest"  # ANUM pulls from the owner's API (docs/commerce.md#own-store-rest-contract)
    SUPABASE = "supabase"  # read-only pull from Supabase (PostgREST)
    WEBHOOK = "webhook"  # the owner's app pushes signed events to ANUM


class Connector(BaseModel):
    """A connected sales channel. Credentials are never part of this model."""

    id: str
    tenant_id: str
    workspace_id: str
    channel: Channel
    name: str = Field(min_length=1, max_length=120)
    marketplaces: list[str] = Field(default_factory=list)
    # Non-secret settings (seller id, region, base URL, own-store mode and column mapping).
    settings: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    credentials_configured: bool = False
    credential_hint: str | None = None
    health: ConnectorHealth = ConnectorHealth.PENDING
    last_error: str | None = None
    last_sync_at: datetime | None = None
    last_success_at: datetime | None = None
    next_sync_at: datetime | None = None
    sync_counts: dict[str, int] = Field(default_factory=dict)
    cursors: dict[str, str] = Field(default_factory=dict)
    created_by: str
    created_at: datetime
    updated_at: datetime


class Product(BaseModel):
    """One sellable item, identified by the owner's SKU across every channel."""

    id: str
    tenant_id: str
    workspace_id: str
    sku: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=500)
    title_ar: str | None = Field(default=None, max_length=500)
    brand: str | None = Field(default=None, max_length=120)
    tyre: TyreSpec = Field(default_factory=TyreSpec)
    cost_price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    low_stock_threshold: int | None = Field(default=None, ge=0, le=100000)
    created_at: datetime
    updated_at: datetime


class Listing(BaseModel):
    """The product on one channel and marketplace: price and stock per channel."""

    id: str
    tenant_id: str
    workspace_id: str
    connector_id: str
    channel: Channel
    marketplace_id: str
    sku: str
    external_id: str | None = None  # ASIN, noon SKU (Z...), own-store id
    title: str = ""
    product_type: str | None = None  # Amazon product type, needed to patch the listing
    price: float | None = None
    currency: str | None = None
    quantity: int | None = None
    status: str = "active"  # active, inactive, out_of_stock
    fulfillment: str | None = None  # merchant, amazon (FBA), noon (FBN)
    buy_box_price: float | None = None
    buy_box_winner: bool | None = None
    lowest_competitor_price: float | None = None
    competition_checked_at: datetime | None = None
    updated_at: datetime


class OrderStatus(StrEnum):
    PENDING = "pending"  # placed, payment or confirmation pending
    UNSHIPPED = "unshipped"  # ready to ship
    PARTIALLY_SHIPPED = "partially_shipped"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


OPEN_ORDER_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.UNSHIPPED, OrderStatus.PARTIALLY_SHIPPED})
COUNTED_ORDER_STATUSES = frozenset(set(OrderStatus) - {OrderStatus.CANCELLED})


class OrderLine(BaseModel):
    sku: str
    external_line_id: str | None = None
    title: str = ""
    quantity: int = Field(default=1, ge=0)
    unit_price: float | None = None
    total: float | None = None


class Order(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    connector_id: str
    channel: Channel
    marketplace_id: str
    external_id: str
    status: OrderStatus
    purchased_at: datetime
    ship_by: datetime | None = None
    total_amount: float = 0.0
    currency: str | None = None
    item_count: int = 0
    fulfillment: str | None = None
    customer_id: str | None = None
    ship_city: str | None = None
    ship_country: str | None = None
    lines: list[OrderLine] = Field(default_factory=list)
    remote_updated_at: datetime | None = None
    synced_at: datetime


class Customer(BaseModel):
    """A buyer, stored only when the connector fetches buyer details. PII is ciphertext here."""

    id: str
    tenant_id: str
    workspace_id: str
    connector_id: str
    ref_hash: str  # sha256 of channel + the channel's buyer reference: never the raw reference
    name_ciphertext: str | None = None
    email_ciphertext: str | None = None
    phone_ciphertext: str | None = None
    address_ciphertext: str | None = None
    city: str | None = None
    country: str | None = None
    orders_count: int = 0
    first_seen_at: datetime
    last_seen_at: datetime


class CustomerView(BaseModel):
    id: str
    city: str | None = None
    country: str | None = None
    orders_count: int = 0
    first_seen_at: datetime
    last_seen_at: datetime
    # Present only for commerce:pii (owners); null otherwise.
    pii_visible: bool = False
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None


class ReturnRecord(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    connector_id: str
    channel: Channel
    external_id: str
    marketplace_id: str | None = None
    order_external_id: str
    sku: str | None = None
    quantity: int = 1
    reason: str | None = None
    status: str = "refunded"
    amount: float | None = None
    currency: str | None = None
    returned_at: datetime


class FeeKind(StrEnum):
    FEE = "fee"
    REFUND = "refund"
    SETTLEMENT = "settlement"
    ADJUSTMENT = "adjustment"


class FeeRecord(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    connector_id: str
    channel: Channel
    external_id: str
    order_external_id: str | None = None
    kind: FeeKind
    description: str = ""
    amount: float
    currency: str | None = None
    posted_at: datetime


class AlertKind(StrEnum):
    LOW_STOCK = "low_stock"
    LATE_SHIPMENT = "late_shipment"
    RETURN_SPIKE = "return_spike"
    BUY_BOX_LOST = "buy_box_lost"
    COMPETITOR_PRICE = "competitor_price"
    SYNC_FAILED = "sync_failed"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class Alert(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    kind: AlertKind
    severity: AlertSeverity
    title: str
    detail: str = ""
    subject: str | None = None  # SKU, order id or connector id
    channel: Channel | None = None
    connector_id: str | None = None
    dedupe_key: str
    status: AlertStatus = AlertStatus.OPEN
    acknowledged_by: str | None = None
    created_at: datetime
    updated_at: datetime


class CommerceSettings(BaseModel):
    """Per-workspace alert rules and sync interval."""

    low_stock_threshold: int = Field(default=4, ge=0, le=100000, description="Units per listing; tyres sell in sets of four")
    handling_hours: int = Field(default=48, ge=1, le=720, description="When an order has no ship-by date: hours to ship")
    return_spike_count: int = Field(default=3, ge=1, le=1000, description="Returns in 7 days that count as a spike")
    return_spike_ratio: float = Field(default=0.1, ge=0, le=1, description="Returns / orders in 7 days that count as a spike")
    price_alert_percent: float = Field(default=2.0, ge=0, le=100, description="Competitor below our price by this much")
    sync_interval_minutes: int = Field(default=30, ge=15, le=1440)
    updated_at: datetime | None = None


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


def page(items: list[Any], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}
