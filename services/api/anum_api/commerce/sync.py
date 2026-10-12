"""Incremental sync of one connector (docs/commerce.md#sync).

Each collection keeps a cursor on the connector: ``orders_since`` (the channel's
"last updated after" time, with a five-minute overlap), the channel's page token while a
pass is unfinished, and the same for returns and money. Listings are a full pass that
may span several syncs (``listings:<marketplace>`` holds the page token).

A sync never holds a database transaction across a channel call: each page is written in
its own unit of work, so a rate limit or a crash keeps everything already fetched and the
next sync continues from the cursor. A sync stops after
``ANUM_COMMERCE_SYNC_TIME_BUDGET_SECONDS`` and when the channel's rate limit asks for a
longer wait than ``ANUM_COMMERCE_MAX_RETRY_WAIT_SECONDS``; the connector reports
``throttled`` and its next sync waits for the limit.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from ..schemas import TenantContext, new_id, utc_now
from ..secret_box import SecretDecryptionError, secret_cipher
from . import alerts as commerce_alerts
from .clients.base import ChannelClient, RemoteBuyer, RemoteFee, RemoteListing, RemoteOrder, RemoteReturn, iso, parse_time
from .config import commerce_config
from .connectors import build_client, decrypt_credentials
from .http import ChannelAuthError, ChannelError, RateLimited
from .models import (
    Channel,
    CommerceSettings,
    Connector,
    ConnectorHealth,
    Customer,
    FeeKind,
    FeeRecord,
    Listing,
    Order,
    Product,
    ReturnRecord,
)
from .store import CommerceStore, open_commerce_store
from .tyre import parse_tyre

logger = logging.getLogger("anum.commerce")

OVERLAP = timedelta(minutes=5)
AUTH_RETRY = timedelta(hours=6)


class SyncReport(BaseModel):
    connector_id: str
    status: str  # ok, partial, throttled, auth_failed, error, skipped
    message: str
    counts: dict[str, int] = Field(default_factory=dict)
    alerts_raised: int = 0
    finished_at: Any = None


class _Budget:
    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + seconds

    @property
    def spent(self) -> bool:
        return time.monotonic() >= self.deadline


def workspace_settings(context: TenantContext, store: CommerceStore) -> CommerceSettings:
    return store.get_settings(context) or CommerceSettings()


def _bump(counts: dict[str, int], key: str, value: int = 1) -> None:
    counts[key] = counts.get(key, 0) + value


def ensure_product(store: CommerceStore, context: TenantContext, remote: RemoteListing, now: datetime) -> Product:
    product = store.get_product_by_sku(context, remote.sku)
    if product is None:
        product = Product(
            id=new_id("cprod"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            sku=remote.sku,
            title=remote.title,
            title_ar=remote.title_ar,
            brand=remote.brand,
            tyre=parse_tyre(remote.title, remote.title_ar),
            currency=remote.currency,
            created_at=now,
            updated_at=now,
        )
        return store.upsert_product(product)
    changed = False
    if not product.title and remote.title:
        product.title, changed = remote.title, True
    if not product.title_ar and remote.title_ar:
        product.title_ar, changed = remote.title_ar, True
    if not product.brand and remote.brand:
        product.brand, changed = remote.brand, True
    if product.tyre.size is None and remote.title:
        parsed = parse_tyre(remote.title, remote.title_ar)
        if parsed.size:
            product.tyre, changed = parsed, True
    if changed:
        product.updated_at = now
        store.upsert_product(product)
    return product


def save_listing(store: CommerceStore, context: TenantContext, connector: Connector, remote: RemoteListing, now: datetime) -> Listing:
    ensure_product(store, context, remote, now)
    existing, _ = store.list_listings(context, sku=remote.sku, connector_id=connector.id, limit=50)
    current = next((item for item in existing if item.marketplace_id == remote.marketplace_id), None)
    listing = Listing(
        id=current.id if current else new_id("clist"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        connector_id=connector.id,
        channel=connector.channel,
        marketplace_id=remote.marketplace_id,
        sku=remote.sku,
        external_id=remote.external_id,
        title=remote.title,
        product_type=remote.product_type or (current.product_type if current else None),
        price=remote.price,
        currency=remote.currency,
        quantity=remote.quantity if remote.quantity is not None or current is None else current.quantity,
        status=remote.status,
        fulfillment=remote.fulfillment,
        buy_box_price=current.buy_box_price if current else None,
        buy_box_winner=current.buy_box_winner if current else None,
        lowest_competitor_price=current.lowest_competitor_price if current else None,
        competition_checked_at=current.competition_checked_at if current else None,
        updated_at=now,
    )
    return store.upsert_listing(listing)


def save_customer(store: CommerceStore, context: TenantContext, connector: Connector, buyer: RemoteBuyer, order: RemoteOrder, now: datetime) -> str:
    cipher = secret_cipher()
    ref_hash = hashlib.sha256(f"{connector.channel.value}:{buyer.reference}".encode()).hexdigest()

    def enc(value: str | None) -> str | None:
        return cipher.encrypt(value) if value else None

    customer = store.upsert_customer(
        Customer(
            id=new_id("ccust"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            connector_id=connector.id,
            ref_hash=ref_hash,
            name_ciphertext=enc(buyer.name),
            email_ciphertext=enc(buyer.email),
            phone_ciphertext=enc(buyer.phone),
            address_ciphertext=enc(buyer.address),
            city=order.ship_city,
            country=order.ship_country,
            orders_count=1,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    return customer.id


def save_order(store: CommerceStore, context: TenantContext, connector: Connector, remote: RemoteOrder, now: datetime) -> Order:
    current = store.find_order(context, remote.external_id)
    if current is not None and current.connector_id != connector.id:
        current = None
    customer_id = current.customer_id if current else None
    if remote.buyer is not None:
        customer_id = save_customer(store, context, connector, remote.buyer, remote, now)
    lines = remote.lines or (current.lines if current else [])
    order = Order(
        id=current.id if current else new_id("cord"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        connector_id=connector.id,
        channel=connector.channel,
        marketplace_id=remote.marketplace_id,
        external_id=remote.external_id,
        status=remote.status,
        purchased_at=remote.purchased_at,
        ship_by=remote.ship_by,
        total_amount=remote.total_amount,
        currency=remote.currency,
        item_count=remote.item_count or sum(line.quantity for line in lines),
        fulfillment=remote.fulfillment,
        customer_id=customer_id,
        ship_city=remote.ship_city,
        ship_country=remote.ship_country,
        lines=[line.model_dump() for line in lines],  # type: ignore[misc]
        remote_updated_at=remote.updated_at,
        synced_at=now,
    )
    return store.upsert_order(order)


def save_return(store: CommerceStore, context: TenantContext, connector: Connector, remote: RemoteReturn) -> ReturnRecord:
    return store.upsert_return(
        ReturnRecord(
            id=new_id("cret"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            connector_id=connector.id,
            channel=connector.channel,
            external_id=remote.external_id,
            marketplace_id=remote.marketplace_id,
            order_external_id=remote.order_external_id,
            sku=remote.sku,
            quantity=remote.quantity,
            reason=remote.reason,
            status=remote.status,
            amount=remote.amount,
            currency=remote.currency,
            returned_at=remote.returned_at,
        )
    )


def save_fee(store: CommerceStore, context: TenantContext, connector: Connector, remote: RemoteFee) -> FeeRecord:
    return store.upsert_fee(
        FeeRecord(
            id=new_id("cfee"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            connector_id=connector.id,
            channel=connector.channel,
            external_id=remote.external_id,
            order_external_id=remote.order_external_id,
            kind=FeeKind(remote.kind) if remote.kind in FeeKind.__members__.values() else FeeKind.ADJUSTMENT,
            description=remote.description,
            amount=remote.amount,
            currency=remote.currency,
            posted_at=remote.posted_at,
        )
    )


def ingest(context: TenantContext, connector: Connector, items: list[Any], now: datetime, counts: dict[str, int]) -> None:
    """Write one page of channel data in one unit of work."""
    with open_commerce_store(context) as store:
        for item in items:
            if isinstance(item, RemoteListing):
                save_listing(store, context, connector, item, now)
                _bump(counts, "listings")
            elif isinstance(item, RemoteOrder):
                save_order(store, context, connector, item, now)
                _bump(counts, "orders")
            elif isinstance(item, RemoteReturn):
                save_return(store, context, connector, item)
                _bump(counts, "returns")
            elif isinstance(item, RemoteFee):
                save_fee(store, context, connector, item)
                _bump(counts, "fees")


class _Stop(Exception):
    """The time budget ran out: the rest continues in the next sync."""


async def _pages(
    context: TenantContext,
    connector: Connector,
    budget: _Budget,
    counts: dict[str, int],
    cursor_key: str,
    fetch: Any,
    now: datetime,
) -> bool:
    """Fetch pages until done; True when the pass finished. The page token is saved as it goes."""
    token = connector.cursors.get(cursor_key) or None
    for _ in range(commerce_config.sync_max_pages):
        if budget.spent:
            raise _Stop()
        page = await fetch(token)
        ingest(context, connector, page.items, now, counts)
        token = page.next_token
        if token:
            connector.cursors[cursor_key] = token
        else:
            connector.cursors.pop(cursor_key, None)
            return True
    return False


async def _run(client: ChannelClient, context: TenantContext, connector: Connector, budget: _Budget, counts: dict[str, int], started: datetime) -> bool:
    """Every collection the channel offers; True when every pass finished."""
    complete = True
    caps = client.capabilities
    lookback = started - timedelta(days=commerce_config.initial_lookback_days)
    if "listings" in caps:
        for marketplace in connector.marketplaces:
            done = await _pages(context, connector, budget, counts, f"listings:{marketplace}", lambda t, m=marketplace: client.fetch_listings(m, t), started)
            complete &= done
    if "fba_inventory" in caps:
        for marketplace in connector.marketplaces:
            token: str | None = None
            for _ in range(commerce_config.sync_max_pages):
                if budget.spent:
                    raise _Stop()
                page = await client.fetch_inventory(marketplace, token)  # type: ignore[attr-defined]
                with open_commerce_store(context) as store:
                    for sku, quantity in page.items:
                        found, _ = store.list_listings(context, sku=sku, connector_id=connector.id, limit=10)
                        for listing in found:
                            if listing.marketplace_id == marketplace and listing.fulfillment == "amazon":
                                listing.quantity = quantity
                                listing.updated_at = started
                                store.upsert_listing(listing)
                                _bump(counts, "fba_stock")
                token = page.next_token
                if not token:
                    break
    for collection, fetch_name in (("orders", "fetch_orders"), ("returns", "fetch_returns"), ("fees", "fetch_fees")):
        if collection == "fees" and "finances" not in caps:
            continue
        if collection != "fees" and collection not in caps:
            continue
        since = parse_time(connector.cursors.get(f"{collection}_since")) or lookback
        fetch = getattr(client, fetch_name)
        done = await _pages(context, connector, budget, counts, f"{collection}_token", lambda t, f=fetch, s=since: f(s, t), started)
        if done:
            connector.cursors[f"{collection}_since"] = iso(started - OVERLAP)
        complete &= done
    if "competition" in caps and commerce_config.pricing_checks_per_sync:
        with open_commerce_store(context) as store:
            candidates = [
                item
                for item in store.all_listings(context)
                if item.connector_id == connector.id and item.external_id and item.status == "active"
            ]
        # Never-checked listings first, then the longest unchecked.
        candidates.sort(key=lambda item: (item.competition_checked_at is not None, item.competition_checked_at or started))
        for listing in candidates[: commerce_config.pricing_checks_per_sync]:
            if budget.spent:
                raise _Stop()
            result = await client.fetch_competition(listing.marketplace_id, listing.external_id or "")
            if result is None:
                continue
            with open_commerce_store(context) as store:
                fresh = store.get_listing(context, listing.id)
                if fresh is not None:
                    fresh.buy_box_price = result.buy_box_price
                    fresh.buy_box_winner = result.buy_box_winner
                    fresh.lowest_competitor_price = result.lowest_competitor_price
                    fresh.competition_checked_at = started
                    store.upsert_listing(fresh)
                    _bump(counts, "price_checks")
    return complete


def _save_connector(context: TenantContext, connector: Connector) -> None:
    with open_commerce_store(context) as store:
        current = store.get_connector(context, connector.id)
        if current is None:  # deleted while syncing
            return
        current.health, current.last_error = connector.health, connector.last_error
        current.last_sync_at, current.last_success_at = connector.last_sync_at, connector.last_success_at
        current.next_sync_at, current.sync_counts, current.cursors = connector.next_sync_at, connector.sync_counts, connector.cursors
        current.updated_at = utc_now()
        store.save_connector(current)


async def sync_connector(context: TenantContext, connector_id: str) -> SyncReport:
    """Sync one connector now and evaluate alerts. Raises KeyError for an unknown connector."""
    with open_commerce_store(context) as store:
        connector = store.get_connector(context, connector_id)
        if connector is None:
            raise KeyError(connector_id)
        ciphertext = store.credentials_ciphertext(context, connector_id)
        settings = workspace_settings(context, store)
    started = utc_now()
    if not connector.enabled:
        return SyncReport(connector_id=connector.id, status="skipped", message="This connection is turned off.")
    counts: dict[str, int] = {}
    connector.last_sync_at = started
    status, message, auth = "ok", "", False
    try:
        credentials = decrypt_credentials(ciphertext)
        if not credentials and connector.credentials_configured:
            raise ChannelAuthError("The saved credentials are missing. Enter them again.")
        client = build_client(connector, credentials)
        if "webhook" in client.capabilities:
            status, message = "ok", "This connection receives events from your store; nothing to pull."
        else:
            complete = await _run(client, context, connector, _Budget(commerce_config.sync_time_budget_seconds), counts, started)
            status = "ok" if complete else "partial"
            message = "Up to date." if complete else "Synced the first pages; the rest continues in the next sync."
        connector.health, connector.last_error = ConnectorHealth.OK, None
        connector.last_success_at = utc_now()
        connector.next_sync_at = started + timedelta(minutes=settings.sync_interval_minutes if complete_or_webhook(status) else 5)
    except _Stop:
        status, message = "partial", "Stopped at the time limit; the next sync continues where this one stopped."
        connector.health, connector.last_error = ConnectorHealth.OK, None
        connector.last_success_at = utc_now()
        connector.next_sync_at = started + timedelta(minutes=5)
    except RateLimited as exc:
        status, message = "throttled", f"{exc.message} Continuing in {int(exc.retry_after)} s."
        connector.health, connector.last_error = ConnectorHealth.THROTTLED, exc.message
        connector.next_sync_at = utc_now() + timedelta(seconds=max(60.0, exc.retry_after))
    except (ChannelAuthError, SecretDecryptionError) as exc:
        auth = True
        status = "auth_failed"
        message = exc.message if isinstance(exc, ChannelError) else "The saved credentials cannot be decrypted with this server's key. Enter them again."
        connector.health, connector.last_error = ConnectorHealth.AUTH_FAILED, message
        connector.next_sync_at = utc_now() + AUTH_RETRY
    except ChannelError as exc:
        status, message = "error", exc.message
        connector.health, connector.last_error = ConnectorHealth.ERROR, exc.message
        connector.next_sync_at = utc_now() + timedelta(minutes=settings.sync_interval_minutes)
    except Exception as exc:  # a bug or an unexpected response: reported without details
        logger.exception("commerce_sync_failed", extra={"anum_commerce": {"connector_id": connector.id, "error_class": type(exc).__name__}})
        status, message = "error", "The sync stopped unexpectedly. Try again; if it keeps failing, check the API logs."
        connector.health, connector.last_error = ConnectorHealth.ERROR, message
        connector.next_sync_at = utc_now() + timedelta(minutes=settings.sync_interval_minutes)
    for key, value in counts.items():
        connector.sync_counts[key] = connector.sync_counts.get(key, 0) + value
    _save_connector(context, connector)

    now = utc_now()
    raised = []
    with open_commerce_store(context) as store:
        if status in ("auth_failed", "error"):
            alert, created = store.raise_alert(commerce_alerts.sync_failed_alert(context, connector, message, auth=auth, now=now))
            if created:
                raised.append(alert)
        else:
            store.resolve_alerts(context, "sync_failed", set(), connector_id=connector.id)
        raised += commerce_alerts.evaluate(context, store, settings, now=now)
    commerce_alerts.publish(context, raised)
    logger.info(
        "commerce_sync",
        extra={"anum_commerce": {"connector_id": connector.id, "channel": connector.channel.value, "status": status, **counts}},
    )
    return SyncReport(connector_id=connector.id, status=status, message=message, counts=counts, alerts_raised=len(raised), finished_at=now)


def complete_or_webhook(status: str) -> bool:
    return status == "ok"


async def ingest_webhook(context: TenantContext, connector: Connector, events: list[dict[str, Any]]) -> dict[str, int]:
    """Store events pushed by the owner's store (own-store ``webhook`` mode)."""
    from .clients.own_store import contract_listing, contract_order, contract_return

    now = utc_now()
    currency = str(connector.settings.get("currency") or "SAR")
    items: list[Any] = []
    for event in events:
        kind, data = event.get("type"), event.get("data")
        if not isinstance(data, dict):
            continue
        parsed = (
            contract_order(data, currency, now) if kind == "order"
            else contract_listing(data, currency) if kind == "product"
            else contract_return(data, now) if kind == "return"
            else None
        )
        if parsed is not None:
            items.append(parsed)
    counts: dict[str, int] = {}
    ingest(context, connector, items, now, counts)
    with open_commerce_store(context) as store:
        current = store.get_connector(context, connector.id)
        if current is not None:
            current.health, current.last_error = ConnectorHealth.OK, None
            current.last_sync_at = current.last_success_at = now
            for key, value in counts.items():
                current.sync_counts[key] = current.sync_counts.get(key, 0) + value
            current.updated_at = now
            store.save_connector(current)
        raised = commerce_alerts.evaluate(context, store, workspace_settings(context, store), now=now)
    commerce_alerts.publish(context, raised)
    return counts


def channel_of(value: str | None) -> Channel | None:
    try:
        return Channel(value) if value else None
    except ValueError:
        return None
