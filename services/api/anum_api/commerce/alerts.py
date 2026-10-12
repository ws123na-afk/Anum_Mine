"""Commerce alerts (docs/commerce.md#alerts).

After every sync the workspace's data is checked against its rules
(``CommerceSettings``): low stock per listing, merchant-fulfilled orders not shipped in
time, return spikes per channel, a lost Amazon buy box, and a competitor below our price.
An alert is raised once per subject (``dedupe_key``) and resolved automatically when its
condition clears; acknowledging it hides it from the open list without resolving it.

A newly raised alert is published as a ``commerce.alert.raised`` event: it reaches the web
and desktop apps on the workspace event stream and phones as a push
(``anum_api/push.py``, owners and members), each gated by the person's
``commerce_alerts`` notification preference (and ``push_enabled`` for phones).

Reviews are not covered: neither SP-API nor noon's API exposes product reviews or
seller feedback, so "negative review" alerts cannot be built on official APIs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..schemas import TenantContext, new_id, utc_now
from .models import (
    CHANNEL_LABELS,
    OPEN_ORDER_STATUSES,
    Alert,
    AlertKind,
    AlertSeverity,
    Channel,
    CommerceSettings,
    Connector,
    marketplace_name,
)
from .store import CommerceStore

logger = logging.getLogger("anum.commerce")

ALERT_EVENT = "commerce.alert.raised"


def _alert(
    context: TenantContext,
    kind: AlertKind,
    severity: AlertSeverity,
    key: str,
    title: str,
    detail: str,
    *,
    subject: str | None = None,
    channel: Channel | None = None,
    connector_id: str | None = None,
    now: datetime,
) -> Alert:
    return Alert(
        id=new_id("calert"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        kind=kind,
        severity=severity,
        title=title[:200],
        detail=detail[:1000],
        subject=subject,
        channel=channel,
        connector_id=connector_id,
        dedupe_key=key[:200],
        created_at=now,
        updated_at=now,
    )


def evaluate(context: TenantContext, store: CommerceStore, settings: CommerceSettings, *, now: datetime | None = None) -> list[Alert]:
    """Raise and resolve rule-based alerts; returns the alerts that are new."""
    now = now or utc_now()
    raised: list[Alert] = []

    def keep(kind: AlertKind, alerts: list[Alert]) -> None:
        keys = set()
        for alert in alerts:
            saved, created = store.raise_alert(alert)
            keys.add(alert.dedupe_key)
            if created:
                raised.append(saved)
        store.resolve_alerts(context, kind.value, keys)

    listings = store.all_listings(context)
    thresholds: dict[str, int | None] = {}

    def threshold(sku: str) -> int:
        if sku not in thresholds:
            product = store.get_product_by_sku(context, sku)
            thresholds[sku] = product.low_stock_threshold if product else None
        value = thresholds[sku]
        return settings.low_stock_threshold if value is None else value

    low: list[Alert] = []
    buy_box: list[Alert] = []
    competitor: list[Alert] = []
    for listing in listings:
        where = marketplace_name(listing.channel, listing.marketplace_id)
        name = listing.title or listing.sku
        if listing.status != "inactive" and listing.quantity is not None and listing.quantity <= threshold(listing.sku):
            out = listing.quantity <= 0
            low.append(
                _alert(
                    context,
                    AlertKind.LOW_STOCK,
                    AlertSeverity.CRITICAL if out else AlertSeverity.WARNING,
                    f"low_stock:{listing.id}",
                    f"{'Out of stock' if out else 'Low stock'}: {listing.sku} on {where}",
                    f"{name}: {listing.quantity} left (alert at {threshold(listing.sku)} or fewer).",
                    subject=listing.sku,
                    channel=listing.channel,
                    connector_id=listing.connector_id,
                    now=now,
                )
            )
        if listing.buy_box_winner is False and listing.status == "active":
            price = f" at {listing.buy_box_price:.2f} {listing.currency or ''}".rstrip() if listing.buy_box_price else ""
            buy_box.append(
                _alert(
                    context,
                    AlertKind.BUY_BOX_LOST,
                    AlertSeverity.WARNING,
                    f"buy_box:{listing.id}",
                    f"Buy box lost: {listing.sku} on {where}",
                    f"Another seller holds the buy box{price}; your price is {listing.price or 0:.2f}.",
                    subject=listing.sku,
                    channel=listing.channel,
                    connector_id=listing.connector_id,
                    now=now,
                )
            )
        if (
            listing.lowest_competitor_price is not None
            and listing.price
            and listing.lowest_competitor_price < listing.price * (1 - settings.price_alert_percent / 100)
        ):
            competitor.append(
                _alert(
                    context,
                    AlertKind.COMPETITOR_PRICE,
                    AlertSeverity.INFO,
                    f"competitor:{listing.id}",
                    f"Competitor below your price: {listing.sku} on {where}",
                    f"Lowest other offer {listing.lowest_competitor_price:.2f} vs your {listing.price:.2f} {listing.currency or ''}.".strip(),
                    subject=listing.sku,
                    channel=listing.channel,
                    connector_id=listing.connector_id,
                    now=now,
                )
            )
    keep(AlertKind.LOW_STOCK, low)
    keep(AlertKind.BUY_BOX_LOST, buy_box)
    keep(AlertKind.COMPETITOR_PRICE, competitor)

    late: list[Alert] = []
    open_orders, _ = store.list_orders(context, statuses=list(OPEN_ORDER_STATUSES), limit=1000, offset=0)
    for order in open_orders:
        if order.fulfillment not in (None, "merchant"):
            continue
        due = order.ship_by or order.purchased_at + timedelta(hours=settings.handling_hours)
        if due < now:
            hours = int((now - due).total_seconds() // 3600)
            late.append(
                _alert(
                    context,
                    AlertKind.LATE_SHIPMENT,
                    AlertSeverity.CRITICAL if hours >= 24 else AlertSeverity.WARNING,
                    f"late:{order.id}",
                    f"Not shipped in time: order {order.external_id} ({CHANNEL_LABELS[order.channel]})",
                    f"Ship-by passed {hours} h ago; {order.item_count} item(s), {order.total_amount:.2f} {order.currency or ''}.".strip(),
                    subject=order.id,
                    channel=order.channel,
                    connector_id=order.connector_id,
                    now=now,
                )
            )
    keep(AlertKind.LATE_SHIPMENT, late)

    week = now - timedelta(days=7)
    recent_returns, _ = store.list_returns(context, since=week, limit=5000, offset=0)
    recent_orders = store.orders_between(context, week, now + timedelta(minutes=1), limit=50_000)
    spikes: list[Alert] = []
    for channel in Channel:
        returned = sum(r.quantity for r in recent_returns if r.channel == channel)
        ordered = sum(o.item_count or 1 for o in recent_orders if o.channel == channel)
        ratio = returned / ordered if ordered else (1.0 if returned else 0.0)
        if returned >= settings.return_spike_count and ratio >= settings.return_spike_ratio:
            spikes.append(
                _alert(
                    context,
                    AlertKind.RETURN_SPIKE,
                    AlertSeverity.WARNING,
                    f"return_spike:{channel.value}",
                    f"Return spike on {CHANNEL_LABELS[channel]}",
                    f"{returned} unit(s) returned in 7 days against {ordered} sold ({ratio:.0%}).",
                    channel=channel,
                    now=now,
                )
            )
    keep(AlertKind.RETURN_SPIKE, spikes)
    return raised


def sync_failed_alert(context: TenantContext, connector: Connector, message: str, *, auth: bool, now: datetime) -> Alert:
    return _alert(
        context,
        AlertKind.SYNC_FAILED,
        AlertSeverity.CRITICAL if auth else AlertSeverity.WARNING,
        f"sync_failed:{connector.id}",
        f"{'Reconnect' if auth else 'Sync failed'}: {connector.name}",
        message,
        subject=connector.id,
        channel=connector.channel,
        connector_id=connector.id,
        now=now,
    )


def publish(context: TenantContext, alerts: list[Alert]) -> None:
    """Publish new alerts on the workspace event stream (after their rows committed)."""
    if not alerts:
        return
    from ..dependencies import _unit_of_work
    from ..events import CanonicalEventName, create_event

    try:
        with _unit_of_work(context, require_membership=False) as repository:
            for alert in alerts[:50]:
                repository.record_event(
                    create_event(
                        CanonicalEventName.COMMERCE_ALERT_RAISED,
                        context,
                        alert.id,
                        {
                            "alert_id": alert.id,
                            "kind": alert.kind.value,
                            "severity": alert.severity.value,
                            "title": alert.title,
                            "channel": alert.channel.value if alert.channel else None,
                        },
                        created_at=alert.created_at,
                    ).event
                )
    except Exception:  # the alert rows are saved; only the live notification is lost
        logger.warning("commerce_alert_publish_failed", extra={"anum_commerce": {"count": len(alerts)}})
