"""HTTP routes under ``/api/v1/commerce`` (docs/commerce.md#api).

Who can do what (checked here, on the server, for every route):

* ``commerce:read`` (owners, members, viewers): dashboard, summaries, orders, catalogue,
  listings, returns, alerts, connection health.
* ``commerce:operate`` (owners, members): edit the catalogue, sync, acknowledge alerts,
  propose price and stock changes. A proposal is a paused high-risk tool call; nothing
  reaches the channel until someone approves it with the payload hash shown
  (``POST /api/v1/approvals/{id}/approve``).
* ``commerce:manage`` (owners): connect, change and remove channels; alert rules.
* ``commerce:pii`` (owners): buyer name, e-mail, phone and address on an order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..agent_tools import ToolCall, ToolRegistry
from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import has_permission, repository_context, require_permission, tenant_context
from ..events import CanonicalEventName, create_event
from ..model_gateway import MockModelGateway
from ..repository import AnumRepository
from ..runtime import AgentRuntime
from ..schemas import AgentRunStep, Approval, RunPhase, Task, TaskStatus, TenantContext, new_id, utc_now
from ..secret_box import SecretDecryptionError, secret_cipher
from . import insights
from .clients.base import UnsupportedOperation
from .config import commerce_config
from .connectors import (
    ConnectorCreate,
    ConnectorInput,
    ConnectorSetupError,
    build_client,
    channel_target,
    credential_hint,
    decrypt_credentials,
    encrypt_credentials,
    new_webhook_secret,
    validate_credentials,
    validate_marketplaces,
    validate_settings,
)
from .http import ChannelAuthError, ChannelError, RateLimited
from .models import (
    AMAZON_MARKETPLACES,
    NOON_MARKETPLACES,
    Alert,
    AlertStatus,
    Channel,
    CommerceSettings,
    Connector,
    ConnectorHealth,
    CustomerView,
    Listing,
    Order,
    OrderStatus,
    OwnStoreMode,
    Product,
    ReturnRecord,
    marketplace_name,
)
from .store import MAX_PAGE, open_commerce_store
from .sync import SyncReport, ingest_webhook, sync_connector, workspace_settings
from .tools import change_tool, register_commerce_tools
from .tyre import TyreSpec, parse_tyre

router = APIRouter(prefix="/api/v1/commerce", tags=["commerce"])
WEBHOOK_TOLERANCE_SECONDS = 300
MAX_CONNECTORS = 20

OPERATE_DENIED = "Only workspace owners and members can do this. Ask an owner to change your role."
MANAGE_DENIED = "Only workspace owners can connect or change sales channels."


def _require(context: TenantContext, permission: Permission, message: str | None = None) -> None:
    try:
        require_permission(context, permission)
    except HTTPException as exc:
        if message and exc.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message) from None
        raise


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, outcome: str = "success", metadata: dict[str, Any] | None = None) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome=outcome,
            correlation_id=target,
            created_at=utc_now(),
            metadata=metadata or {},
        )
    )


# -- views --------------------------------------------------------------------------------


class ConnectorView(BaseModel):
    id: str
    channel: Channel
    name: str
    marketplaces: list[str]
    marketplace_names: list[str]
    settings: dict[str, Any]
    enabled: bool
    credentials_configured: bool
    credential_hint: str | None
    health: ConnectorHealth
    last_error: str | None
    last_sync_at: datetime | None
    last_success_at: datetime | None
    next_sync_at: datetime | None
    sync_counts: dict[str, int]
    capabilities: list[str]
    webhook_path: str | None = None
    created_at: datetime
    updated_at: datetime


class ConnectorCreated(BaseModel):
    connector: ConnectorView
    # Shown once (own-store webhook mode): the store signs its events with it.
    webhook_secret: str | None = None


def _capabilities(connector: Connector) -> list[str]:
    try:
        client = build_client(connector, {})
    except ChannelError:
        return []
    return sorted(client.capabilities)


def connector_view(connector: Connector) -> ConnectorView:
    webhook = (
        f"/api/v1/commerce/webhooks/{connector.tenant_id}/{connector.workspace_id}/{connector.id}"
        if connector.channel == Channel.OWN_STORE and connector.settings.get("mode") == OwnStoreMode.WEBHOOK.value
        else None
    )
    return ConnectorView(
        **connector.model_dump(exclude={"tenant_id", "workspace_id", "cursors", "created_by"}),
        marketplace_names=[marketplace_name(connector.channel, m) for m in connector.marketplaces],
        capabilities=_capabilities(connector),
        webhook_path=webhook,
    )


class ListingView(Listing):
    marketplace: str


def listing_view(listing: Listing) -> ListingView:
    return ListingView(**listing.model_dump(), marketplace=marketplace_name(listing.channel, listing.marketplace_id))


class ProductView(Product):
    listings: list[ListingView] = Field(default_factory=list)
    total_stock: int | None = None


def product_view(product: Product, listings: list[Listing]) -> ProductView:
    quantities = [item.quantity for item in listings if item.quantity is not None]
    return ProductView(**product.model_dump(), listings=[listing_view(item) for item in listings], total_stock=sum(quantities) if quantities else None)


class OrderView(Order):
    marketplace: str
    late: bool = False


class OrderDetail(OrderView):
    customer: CustomerView | None = None
    returns: list[ReturnRecord] = Field(default_factory=list)


def order_view(order: Order, settings: CommerceSettings, now: datetime) -> OrderView:
    due = order.ship_by or order.purchased_at + timedelta(hours=settings.handling_hours)
    late = order.status.value in ("pending", "unshipped", "partially_shipped") and order.fulfillment in (None, "merchant") and due < now
    return OrderView(**order.model_dump(), marketplace=marketplace_name(order.channel, order.marketplace_id), late=late)


class PageOf(BaseModel):
    total: int
    limit: int
    offset: int


class ProductPage(PageOf):
    items: list[ProductView]


class ListingPage(PageOf):
    items: list[ListingView]


class OrderPage(PageOf):
    items: list[OrderView]


class ReturnPage(PageOf):
    items: list[ReturnRecord]


class AlertPage(PageOf):
    items: list[Alert]


class Permissions(BaseModel):
    read: bool
    operate: bool
    manage: bool
    pii: bool


class CommerceSetup(BaseModel):
    permissions: Permissions
    connectors: int
    channels: list[dict[str, Any]]
    own_store_modes: list[str]
    sync_scheduler: bool


class ProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=500)
    title_ar: str | None = Field(default=None, max_length=500)
    brand: str | None = Field(default=None, max_length=120)
    tyre: TyreSpec | None = None
    cost_price: float | None = Field(default=None, ge=0, le=1_000_000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    low_stock_threshold: int | None = Field(default=None, ge=0, le=100000)


class ProductCreate(ProductInput):
    sku: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/ -]*$")


class PriceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: float = Field(gt=0, le=1_000_000)


class StockChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int = Field(ge=0, le=1_000_000)


class ChangeProposal(BaseModel):
    listing: ListingView
    approval: Approval
    task_id: str


class SummaryView(BaseModel):
    text: str
    data: dict[str, Any]


class ConnectorTest(BaseModel):
    ok: bool
    message: str
    connector: ConnectorView


# -- setup and dashboards ---------------------------------------------------------------


def _permissions(context: TenantContext) -> Permissions:
    return Permissions(
        read=has_permission(context, Permission.COMMERCE_READ),
        operate=has_permission(context, Permission.COMMERCE_OPERATE),
        manage=has_permission(context, Permission.COMMERCE_MANAGE),
        pii=has_permission(context, Permission.COMMERCE_PII),
    )


@router.get("/setup", response_model=CommerceSetup)
async def get_setup(context: TenantContext = Depends(tenant_context)) -> CommerceSetup:
    """What the caller may do, which channels exist and how many are connected."""
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        count = len(store.list_connectors(context))
    return CommerceSetup(
        permissions=_permissions(context),
        connectors=count,
        channels=[
            {"channel": "amazon", "marketplaces": [{"id": k, **v} for k, v in AMAZON_MARKETPLACES.items()]},
            {"channel": "noon", "marketplaces": [{"id": k, **v} for k, v in NOON_MARKETPLACES.items()]},
            {"channel": "own_store", "marketplaces": [{"id": "own", "name": "Own store"}]},
        ],
        own_store_modes=[mode.value for mode in OwnStoreMode],
        sync_scheduler=commerce_config.sync_enabled,
    )


def _date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Dates are YYYY-MM-DD; got {value!r}.") from None


@router.get("/dashboard", response_model=insights.Dashboard)
async def get_dashboard(
    start: str | None = Query(default=None, alias="from", max_length=10),
    end: str | None = Query(default=None, alias="to", max_length=10),
    tz: str | None = Query(default=None, max_length=64),
    currency: str | None = Query(default=None, pattern=r"^[A-Z]{3}$"),
    channel: Channel | None = None,
    context: TenantContext = Depends(tenant_context),
) -> insights.Dashboard:
    """Sales by channel and day, top products, pending orders, low stock, returns, price alerts."""
    _require(context, Permission.COMMERCE_READ)
    zone = insights.zone(tz)
    today = utc_now().astimezone(zone).date()
    last = _date(end, today)
    first = _date(start, last - timedelta(days=29))
    if first > last:
        raise HTTPException(status_code=422, detail="The start date is after the end date.")
    if (last - first).days >= commerce_config.dashboard_max_days:
        raise HTTPException(status_code=422, detail=f"Choose at most {commerce_config.dashboard_max_days} days.")
    with open_commerce_store(context) as store:
        return insights.dashboard(
            store, context, workspace_settings(context, store), first=first, last=last, tz_name=tz, currency=currency, channel=channel
        )


@router.get("/summary", response_model=SummaryView)
async def get_summary(
    period: str = Query(default="today", pattern="^(today|yesterday|last_7_days|last_30_days|this_month)$"),
    channel: Channel | None = None,
    tz: str | None = Query(default=None, max_length=64),
    lang: str = Query(default="en", pattern="^(en|ar)$"),
    context: TenantContext = Depends(tenant_context),
) -> SummaryView:
    """The sentence the assistant says for "how were sales today?" and its numbers."""
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        text, data = insights.sales_summary(store, context, channel=channel, period=period, tz_name=tz, arabic=lang == "ar")
    return SummaryView(text=text, data=data)


# -- connectors ----------------------------------------------------------------------------


def _connector_or_404(store: Any, context: TenantContext, connector_id: str) -> Connector:
    connector = store.get_connector(context, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return connector


@router.get("/connectors", response_model=list[ConnectorView])
async def list_connectors(context: TenantContext = Depends(tenant_context)) -> list[ConnectorView]:
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        return [connector_view(c) for c in store.list_connectors(context)]


@router.post("/connectors", response_model=ConnectorCreated, status_code=status.HTTP_201_CREATED)
async def create_connector(
    payload: ConnectorCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectorCreated:
    _require(context, Permission.COMMERCE_MANAGE, MANAGE_DENIED)
    try:
        settings = validate_settings(payload.channel, payload.settings or {})
        marketplaces = validate_marketplaces(payload.channel, payload.marketplaces)
        webhook_secret = None
        if payload.channel == Channel.OWN_STORE and settings["mode"] == OwnStoreMode.WEBHOOK.value:
            webhook_secret = new_webhook_secret()
            credentials = {"webhook_secret": webhook_secret}
        else:
            credentials = validate_credentials(payload.channel, settings, payload.credentials or {})
    except ConnectorSetupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    now = utc_now()
    connector = Connector(
        id=new_id("conn"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        channel=payload.channel,
        name=(payload.name or _default_name(payload.channel, marketplaces)).strip(),
        marketplaces=marketplaces,
        settings=settings,
        enabled=payload.enabled if payload.enabled is not None else True,
        credentials_configured=True,
        credential_hint=credential_hint(payload.channel, settings, credentials),
        created_by=context.user_id,
        created_at=now,
        updated_at=now,
        next_sync_at=now,
    )
    with open_commerce_store(context) as store:
        if len(store.list_connectors(context)) >= MAX_CONNECTORS:
            raise HTTPException(status_code=409, detail=f"A workspace can have at most {MAX_CONNECTORS} sales channel connections.")
        store.save_connector(connector, credentials_ciphertext=encrypt_credentials(credentials), replace_credentials=True)
    _audit(repository, context, "commerce.connector.created", connector.id, metadata={"channel": connector.channel.value, "marketplaces": marketplaces})
    return ConnectorCreated(connector=connector_view(connector), webhook_secret=webhook_secret)


def _default_name(channel: Channel, marketplaces: list[str]) -> str:
    if channel == Channel.OWN_STORE:
        return "Own store"
    return " + ".join(marketplace_name(channel, m) for m in marketplaces)


@router.get("/connectors/{connector_id}", response_model=ConnectorView)
async def get_connector(connector_id: str, context: TenantContext = Depends(tenant_context)) -> ConnectorView:
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        return connector_view(_connector_or_404(store, context, connector_id))


@router.patch("/connectors/{connector_id}", response_model=ConnectorView)
async def update_connector(
    connector_id: str,
    payload: ConnectorInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectorView:
    """Rename, switch on or off, change settings or marketplaces, or replace the credentials."""
    _require(context, Permission.COMMERCE_MANAGE, MANAGE_DENIED)
    with open_commerce_store(context) as store:
        connector = _connector_or_404(store, context, connector_id)
        ciphertext = store.credentials_ciphertext(context, connector_id)
        try:
            if payload.settings is not None:
                merged = {**connector.settings, **payload.settings}
                if connector.channel == Channel.OWN_STORE and merged.get("mode") != connector.settings.get("mode"):
                    raise ConnectorSetupError("The own-store mode cannot change; add a new connection instead.")
                connector.settings = validate_settings(connector.channel, merged)
            if payload.marketplaces is not None:
                connector.marketplaces = validate_marketplaces(connector.channel, payload.marketplaces)
            replace = False
            new_ciphertext = None
            if payload.credentials:
                try:
                    current = decrypt_credentials(ciphertext)
                except SecretDecryptionError:
                    current = {}
                credentials = validate_credentials(connector.channel, connector.settings, {**current, **payload.credentials})
                new_ciphertext, replace = encrypt_credentials(credentials), True
                connector.credential_hint = credential_hint(connector.channel, connector.settings, credentials)
                connector.credentials_configured = True
                connector.health, connector.last_error = ConnectorHealth.PENDING, None
                connector.next_sync_at = utc_now()
        except ConnectorSetupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if payload.name is not None:
            connector.name = payload.name.strip()
        if payload.enabled is not None:
            connector.enabled = payload.enabled
            connector.health = ConnectorHealth.DISABLED if not payload.enabled else (
                ConnectorHealth.PENDING if connector.health == ConnectorHealth.DISABLED else connector.health
            )
            if payload.enabled:
                connector.next_sync_at = utc_now()
        connector.updated_at = utc_now()
        store.save_connector(connector, credentials_ciphertext=new_ciphertext, replace_credentials=replace)
    _audit(
        repository,
        context,
        "commerce.connector.updated",
        connector.id,
        metadata={"fields": sorted(k for k, v in payload.model_dump(exclude_none=True).items() if k != "credentials") + (["credentials"] if payload.credentials else [])},
    )
    return connector_view(connector)


@router.delete("/connectors/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    connector_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> None:
    """Remove the connection, its credentials and everything synced through it."""
    _require(context, Permission.COMMERCE_MANAGE, MANAGE_DENIED)
    with open_commerce_store(context) as store:
        connector = _connector_or_404(store, context, connector_id)
        store.delete_connector(context, connector_id)
    _audit(repository, context, "commerce.connector.deleted", connector_id, metadata={"channel": connector.channel.value})


@router.post("/connectors/{connector_id}/test", response_model=ConnectorTest)
async def test_connector(connector_id: str, context: TenantContext = Depends(tenant_context)) -> ConnectorTest:
    """Call the channel once with the saved credentials and record the result as the health."""
    _require(context, Permission.COMMERCE_MANAGE, MANAGE_DENIED)
    with open_commerce_store(context) as store:
        connector = _connector_or_404(store, context, connector_id)
        ciphertext = store.credentials_ciphertext(context, connector_id)
    ok, message = False, ""
    try:
        client = build_client(connector, decrypt_credentials(ciphertext))
        message = await client.check()
        ok = True
        health = ConnectorHealth.OK if connector.enabled else ConnectorHealth.DISABLED
    except RateLimited as exc:
        message, health = exc.message, ConnectorHealth.THROTTLED
    except ChannelAuthError as exc:
        message, health = exc.message, ConnectorHealth.AUTH_FAILED
    except ChannelError as exc:
        message, health = exc.message, ConnectorHealth.ERROR
    except SecretDecryptionError:
        message, health = "The saved credentials cannot be decrypted with this server's key. Enter them again.", ConnectorHealth.AUTH_FAILED
    with open_commerce_store(context) as store:
        connector = _connector_or_404(store, context, connector_id)
        connector.health, connector.last_error = health, None if ok else message
        connector.updated_at = utc_now()
        store.save_connector(connector)
    return ConnectorTest(ok=ok, message=message, connector=connector_view(connector))


@router.post("/connectors/{connector_id}/sync", response_model=SyncReport)
async def sync_now(connector_id: str, context: TenantContext = Depends(tenant_context)) -> SyncReport:
    """Sync now (bounded by ANUM_COMMERCE_SYNC_TIME_BUDGET_SECONDS); the next sync continues."""
    _require(context, Permission.COMMERCE_OPERATE, OPERATE_DENIED)
    try:
        return await sync_connector(context, connector_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Connection not found") from None


@router.post("/connectors/{connector_id}/webhook-secret", response_model=ConnectorCreated)
async def rotate_webhook_secret(
    connector_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectorCreated:
    """A new signing secret for an own-store webhook connection (shown once; the old one stops working)."""
    _require(context, Permission.COMMERCE_MANAGE, MANAGE_DENIED)
    with open_commerce_store(context) as store:
        connector = _connector_or_404(store, context, connector_id)
        if connector.settings.get("mode") != OwnStoreMode.WEBHOOK.value:
            raise HTTPException(status_code=409, detail="Only webhook connections have a signing secret.")
        secret = new_webhook_secret()
        connector.updated_at = utc_now()
        store.save_connector(connector, credentials_ciphertext=encrypt_credentials({"webhook_secret": secret}), replace_credentials=True)
    _audit(repository, context, "commerce.connector.webhook_secret_rotated", connector_id)
    return ConnectorCreated(connector=connector_view(connector), webhook_secret=secret)


@router.post("/webhooks/{tenant_id}/{workspace_id}/{connector_id}", include_in_schema=True)
async def receive_webhook(
    tenant_id: str,
    workspace_id: str,
    connector_id: str,
    request: Request,
    x_anum_timestamp: str = Header(default="", max_length=20),
    x_anum_signature: str = Header(default="", max_length=200),
) -> dict[str, Any]:
    """Events pushed by the owner's store, signed with the connection's secret.

    ``X-Anum-Timestamp`` is the Unix time of sending; ``X-Anum-Signature`` is
    ``sha256=`` and the hex HMAC-SHA256 of ``"<timestamp>.<raw body>"``. The body is
    ``{"events": [{"type": "order" | "product" | "return", "data": {...}}]}`` in the
    own-store contract's shapes. Events older than five minutes are refused.
    """
    context = TenantContext(tenant_id=tenant_id[:80], workspace_id=workspace_id[:80], user_id=f"webhook:{connector_id[:60]}", roles=[])
    refused = HTTPException(status_code=401, detail="Signature missing, expired or invalid.")
    try:
        sent_at = int(x_anum_timestamp)
    except ValueError:
        raise refused from None
    if abs(time.time() - sent_at) > WEBHOOK_TOLERANCE_SECONDS:
        raise refused
    body = await request.body()
    with open_commerce_store(context) as store:
        connector = store.get_connector(context, connector_id)
        ciphertext = store.credentials_ciphertext(context, connector_id) if connector else None
    if connector is None or connector.settings.get("mode") != OwnStoreMode.WEBHOOK.value or not connector.enabled:
        raise HTTPException(status_code=404, detail="Connection not found")
    try:
        secret = decrypt_credentials(ciphertext).get("webhook_secret", "")
    except SecretDecryptionError:
        raise refused from None
    expected = "sha256=" + hmac.new(secret.encode(), f"{sent_at}.".encode() + body, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(expected, x_anum_signature):
        raise refused
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=422, detail="The body is not JSON.") from None
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list) or len(events) > 500:
        raise HTTPException(status_code=422, detail='Send {"events": [...]} with at most 500 events.')
    counts = await ingest_webhook(context, connector, [e for e in events if isinstance(e, dict)])
    return {"accepted": sum(counts.values()), "counts": counts}


# -- catalogue -----------------------------------------------------------------------------


@router.get("/products", response_model=ProductPage)
async def list_products(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> ProductPage:
    """The catalogue with each product's listings (price and stock per channel)."""
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        items, total = store.list_products(context, query=(q or "").strip() or None, limit=limit, offset=offset)
        views = [product_view(p, store.list_listings(context, sku=p.sku, limit=50)[0]) for p in items]
    return ProductPage(items=views, total=total, limit=limit, offset=offset)


@router.post("/products", response_model=ProductView, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ProductView:
    _require(context, Permission.COMMERCE_OPERATE, OPERATE_DENIED)
    now = utc_now()
    sku = payload.sku.strip()
    with open_commerce_store(context) as store:
        if store.get_product_by_sku(context, sku) is not None:
            raise HTTPException(status_code=409, detail=f"SKU {sku} is already in the catalogue.")
        title = (payload.title or "").strip()
        product = store.upsert_product(
            Product(
                id=new_id("cprod"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                sku=sku,
                title=title,
                title_ar=(payload.title_ar or "").strip() or None,
                brand=(payload.brand or "").strip() or None,
                tyre=payload.tyre or parse_tyre(title, payload.title_ar),
                cost_price=payload.cost_price,
                currency=payload.currency,
                low_stock_threshold=payload.low_stock_threshold,
                created_at=now,
                updated_at=now,
            )
        )
    _audit(repository, context, "commerce.product.created", product.id, metadata={"sku": sku})
    return product_view(product, [])


@router.get("/products/{product_id}", response_model=ProductView)
async def get_product(product_id: str, context: TenantContext = Depends(tenant_context)) -> ProductView:
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        product = store.get_product(context, product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product_view(product, store.list_listings(context, sku=product.sku, limit=50)[0])


@router.patch("/products/{product_id}", response_model=ProductView)
async def update_product(
    product_id: str,
    payload: ProductInput,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ProductView:
    """Edit catalogue details in ANUM (titles, Arabic title, brand, tyre attributes, cost, threshold).

    These stay in ANUM: they never change a channel's listing.
    """
    _require(context, Permission.COMMERCE_OPERATE, OPERATE_DENIED)
    changes = payload.model_dump(exclude_unset=True)
    with open_commerce_store(context) as store:
        product = store.get_product(context, product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        for key, value in changes.items():
            if key == "tyre":
                product.tyre = TyreSpec(**value) if value else TyreSpec()
            elif isinstance(value, str):
                setattr(product, key, value.strip() or (None if key != "title" else ""))
            else:
                setattr(product, key, value)
        product.updated_at = utc_now()
        store.upsert_product(product)
        listings = store.list_listings(context, sku=product.sku, limit=50)[0]
    _audit(repository, context, "commerce.product.updated", product.id, metadata={"fields": sorted(changes)})
    return product_view(product, listings)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> None:
    _require(context, Permission.COMMERCE_OPERATE, OPERATE_DENIED)
    with open_commerce_store(context) as store:
        product = store.get_product(context, product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        if store.list_listings(context, sku=product.sku, limit=1)[1]:
            raise HTTPException(
                status_code=409,
                detail="This SKU is still listed on a connected channel; it would come back with the next sync. Remove the listing on the channel first.",
            )
        store.delete_product(context, product_id)
    _audit(repository, context, "commerce.product.deleted", product_id, metadata={"sku": product.sku})


@router.get("/listings", response_model=ListingPage)
async def list_listings(
    q: str | None = Query(default=None, max_length=200),
    connector_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> ListingPage:
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        items, total = store.list_listings(context, connector_id=connector_id, query=(q or "").strip() or None, limit=limit, offset=offset)
    return ListingPage(items=[listing_view(i) for i in items], total=total, limit=limit, offset=offset)


def propose(repository: AnumRepository, context: TenantContext, call: ToolCall, title: str, prompt: str) -> tuple[Task, Approval]:
    """A task whose run is paused at the high-risk tool call, waiting for approval."""
    now = utc_now()
    task = Task(
        id=new_id("task"),
        title=title[:160],
        prompt=prompt[:4000],
        status=TaskStatus.CREATED,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_at=now,
        updated_at=now,
        created_by=context.user_id,
    )
    repository.create_task(task)
    repository.record_event(
        create_event(CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now).event
    )
    runtime = AgentRuntime(MockModelGateway(), repository, tools=register_commerce_tools(ToolRegistry()))
    run = runtime.new_run(task)
    decision = runtime.evaluate(call, context)
    step = AgentRunStep(
        id=new_id("step"),
        type="tool_proposal",
        summary=f"Proposed tool call: {call.name}",
        created_at=utc_now(),
        metadata={
            "tool": call.name,
            "risk_level": decision.risk_level.value,
            "policy_outcome": decision.outcome.value,
            "governance_rules": list(decision.governance_rules),
        },
    )
    run.steps.append(step)
    run.checkpoint.phase = RunPhase.TOOL_READY
    run.checkpoint.version += 1
    run.checkpoint.tool_call = call.model_dump(mode="json")
    run.checkpoint.last_step_id = step.id
    task.status = run.status = TaskStatus.RUNNING
    repository.save_task(task)
    repository.save_run(run)
    runtime.begin_execution(task, run, context)
    repository.save_task(task)
    repository.save_run(run)
    if run.checkpoint.phase != RunPhase.WAITING_APPROVAL or not run.checkpoint.approval_id:
        reason = run.steps[-1].summary if run.steps else "Blocked by policy."
        raise HTTPException(status_code=403, detail=f"The change is blocked: {reason}")
    approval = repository.get_approval(run.checkpoint.approval_id, context)
    if approval is None:  # pragma: no cover - saved just above
        raise HTTPException(status_code=500, detail="Approval was not stored")
    return task, approval


def _propose_listing_change(repository: AnumRepository, context: TenantContext, listing_id: str, kind: str, value: float | int) -> ChangeProposal:
    _require(context, Permission.COMMERCE_OPERATE, OPERATE_DENIED)
    with open_commerce_store(context) as store:
        listing = store.get_listing(context, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="Listing not found")
        connector = store.get_connector(context, listing.connector_id)
    if connector is None or not connector.enabled:
        raise HTTPException(status_code=409, detail="The listing's channel connection is missing or turned off.")
    caps = _capabilities(connector)
    if f"update_{kind}" not in caps:
        raise HTTPException(status_code=409, detail="This connection is read-only; change it in your store.")
    where = marketplace_name(listing.channel, listing.marketplace_id)
    arguments: dict[str, Any] = {
        "connector_id": connector.id,
        "listing_id": listing.id,
        "sku": listing.sku,
        "marketplace_id": listing.marketplace_id,
        "marketplace": where,
        "target_host": channel_target(connector),
    }
    if kind == "price":
        arguments.update(price=round(float(value), 2), currency=listing.currency or "SAR", previous_price=listing.price)
        shown = f"{float(value):.2f} {listing.currency or ''}".strip()
    else:
        arguments.update(quantity=int(value), previous_quantity=listing.quantity)
        shown = str(int(value))
    call = ToolCall(name=change_tool(listing.channel, kind), arguments=arguments)
    task, approval = propose(
        repository,
        context,
        call,
        f"Set {kind} of {listing.sku} on {where} to {shown}",
        f"Change the {kind} of SKU {listing.sku} on {where} to {shown}.",
    )
    _audit(
        repository,
        context,
        f"commerce.{kind}_change.requested",
        listing.id,
        metadata={"approval_id": approval.id, "task_id": task.id, "channel": listing.channel.value, "payload_hash": approval.payload_hash},
    )
    return ChangeProposal(listing=listing_view(listing), approval=approval, task_id=task.id)


@router.post("/listings/{listing_id}/price-change", response_model=ChangeProposal)
async def request_price_change(
    listing_id: str,
    payload: PriceChange,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ChangeProposal:
    """Propose a new price: returns the pending approval (tool, arguments, payload hash)."""
    return _propose_listing_change(repository, context, listing_id, "price", payload.price)


@router.post("/listings/{listing_id}/stock-change", response_model=ChangeProposal)
async def request_stock_change(
    listing_id: str,
    payload: StockChange,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ChangeProposal:
    return _propose_listing_change(repository, context, listing_id, "stock", payload.quantity)


# -- orders, returns, alerts, settings ----------------------------------------------------------


@router.get("/orders", response_model=OrderPage)
async def list_orders(
    channel: Channel | None = None,
    order_status: str | None = Query(default=None, alias="status", pattern="^(open|pending|unshipped|partially_shipped|shipped|delivered|cancelled|returned)$"),
    start: str | None = Query(default=None, alias="from", max_length=10),
    end: str | None = Query(default=None, alias="to", max_length=10),
    tz: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> OrderPage:
    """Orders newest first. ``status=open`` means pending, unshipped or partially shipped."""
    _require(context, Permission.COMMERCE_READ)
    statuses = None
    if order_status == "open":
        statuses = [OrderStatus.PENDING, OrderStatus.UNSHIPPED, OrderStatus.PARTIALLY_SHIPPED]
    elif order_status:
        statuses = [OrderStatus(order_status)]
    zone = insights.zone(tz)
    since = until = None
    if start or end:
        today = utc_now().astimezone(zone).date()
        since, until = insights._bounds(_date(start, date(2000, 1, 1)), _date(end, today), zone)
    with open_commerce_store(context) as store:
        items, total = store.list_orders(
            context,
            channel=channel.value if channel else None,
            statuses=statuses,
            since=since,
            until=until,
            query=(q or "").strip() or None,
            limit=limit,
            offset=offset,
        )
        settings = workspace_settings(context, store)
    now = utc_now()
    return OrderPage(items=[order_view(o, settings, now) for o in items], total=total, limit=limit, offset=offset)


def _customer_view(context: TenantContext, customer: Any) -> CustomerView:
    view = CustomerView(
        id=customer.id,
        city=customer.city,
        country=customer.country,
        orders_count=customer.orders_count,
        first_seen_at=customer.first_seen_at,
        last_seen_at=customer.last_seen_at,
    )
    if has_permission(context, Permission.COMMERCE_PII):
        cipher = secret_cipher()

        def dec(value: str | None) -> str | None:
            try:
                return cipher.decrypt(value) if value else None
            except SecretDecryptionError:
                return None

        view.pii_visible = True
        view.name, view.email = dec(customer.name_ciphertext), dec(customer.email_ciphertext)
        view.phone, view.address = dec(customer.phone_ciphertext), dec(customer.address_ciphertext)
    return view


@router.get("/orders/{order_id}", response_model=OrderDetail)
async def get_order(
    order_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> OrderDetail:
    """One order with its lines and returns; buyer details only for owners (``commerce:pii``)."""
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        order = store.get_order(context, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        customer = store.get_customer(context, order.customer_id) if order.customer_id else None
        returns, _ = store.list_returns(context, since=None, limit=500, offset=0)
        settings = workspace_settings(context, store)
    view = order_view(order, settings, utc_now())
    customer_view = _customer_view(context, customer) if customer else None
    if customer_view is not None and customer_view.pii_visible:
        _audit(repository, context, "commerce.customer.viewed", customer.id, metadata={"order_id": order.id})
    return OrderDetail(
        **view.model_dump(),
        customer=customer_view,
        returns=[r for r in returns if r.order_external_id == order.external_id and r.connector_id == order.connector_id],
    )


@router.get("/returns", response_model=ReturnPage)
async def list_returns(
    days: int = Query(default=30, ge=1, le=366),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> ReturnPage:
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        items, total = store.list_returns(context, since=utc_now() - timedelta(days=days), limit=limit, offset=offset)
    return ReturnPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/alerts", response_model=AlertPage)
async def list_alerts(
    alert_status: AlertStatus | None = Query(default=AlertStatus.OPEN, alias="status"),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    context: TenantContext = Depends(tenant_context),
) -> AlertPage:
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        items, total = store.list_alerts(context, status=alert_status, limit=limit, offset=offset)
    return AlertPage(items=items, total=total, limit=limit, offset=offset)


@router.post("/alerts/{alert_id}/acknowledge", response_model=Alert)
async def acknowledge_alert(alert_id: str, context: TenantContext = Depends(tenant_context)) -> Alert:
    """Hide an open alert from the open list; it resolves by itself when its cause clears."""
    _require(context, Permission.COMMERCE_OPERATE, OPERATE_DENIED)
    with open_commerce_store(context) as store:
        alert = store.get_alert(context, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        if alert.status == AlertStatus.OPEN:
            alert.status, alert.acknowledged_by, alert.updated_at = AlertStatus.ACKNOWLEDGED, context.user_id, utc_now()
            store.save_alert(alert)
        return alert


@router.get("/settings", response_model=CommerceSettings)
async def get_settings(context: TenantContext = Depends(tenant_context)) -> CommerceSettings:
    _require(context, Permission.COMMERCE_READ)
    with open_commerce_store(context) as store:
        return workspace_settings(context, store)


@router.put("/settings", response_model=CommerceSettings)
async def put_settings(
    payload: CommerceSettings,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> CommerceSettings:
    _require(context, Permission.COMMERCE_MANAGE, "Only workspace owners can change alert rules.")
    payload.updated_at = utc_now()
    with open_commerce_store(context) as store:
        saved = store.save_settings(context, payload)
    _audit(repository, context, "commerce.settings.updated", context.workspace_id, metadata=payload.model_dump(mode="json", exclude={"updated_at"}))
    return saved


__all__ = ["router", "UnsupportedOperation"]
