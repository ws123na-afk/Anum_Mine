"""Dashboards and summaries over the stored commerce data (docs/commerce.md#dashboard).

Days are calendar days in the workspace's time zone (``tz``, default ``Asia/Riyadh``).
Money is never added across currencies: Amazon.sa and noon Saudi report SAR, Amazon.ae
and noon UAE report AED. The dashboard charts one currency at a time (the one with the
most revenue unless the caller picks one) and lists every currency's totals.
Cancelled orders count nowhere; everything else counts as a sale on its purchase day.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from ..schemas import TenantContext, utc_now
from .config import commerce_config
from .models import (
    CHANNEL_LABELS,
    COUNTED_ORDER_STATUSES,
    OPEN_ORDER_STATUSES,
    AlertStatus,
    Channel,
    CommerceSettings,
    Listing,
    Order,
    OrderStatus,
    marketplace_name,
)
from .store import CommerceStore

DEFAULT_TZ = "Asia/Riyadh"
PERIODS = ("today", "yesterday", "last_7_days", "last_30_days", "this_month")


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def period_range(period: str, tz: ZoneInfo, now: datetime | None = None) -> tuple[datetime, datetime, date, date]:
    """``[start, end)`` in UTC and the first and last local day for a named period."""
    now = now or utc_now()
    today = now.astimezone(tz).date()
    if period == "yesterday":
        first = last = today - timedelta(days=1)
    elif period == "last_7_days":
        first, last = today - timedelta(days=6), today
    elif period == "last_30_days":
        first, last = today - timedelta(days=29), today
    elif period == "this_month":
        first, last = today.replace(day=1), today
    else:
        first = last = today
    return _bounds(first, last, tz) + (first, last)


def _bounds(first: date, last: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(first, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end


class MoneyTotal(BaseModel):
    currency: str
    revenue: float = 0.0
    orders: int = 0
    units: int = 0


class ChannelRow(BaseModel):
    channel: Channel
    label: str
    marketplace_id: str
    marketplace: str
    currency: str
    revenue: float = 0.0
    orders: int = 0
    units: int = 0


class DayRow(BaseModel):
    date: str
    channel: Channel
    revenue: float = 0.0
    orders: int = 0
    units: int = 0


class TopProduct(BaseModel):
    sku: str
    title: str
    title_ar: str | None = None
    tyre_size: str | None = None
    units: int = 0
    revenue: float = 0.0
    currency: str


class LowStockRow(BaseModel):
    listing_id: str
    sku: str
    title: str
    channel: Channel
    marketplace: str
    quantity: int
    threshold: int


class PendingOrderRow(BaseModel):
    id: str
    external_id: str
    channel: Channel
    marketplace: str
    status: OrderStatus
    purchased_at: datetime
    ship_by: datetime | None = None
    late: bool = False
    total_amount: float
    currency: str | None = None
    item_count: int


class ReturnsSummary(BaseModel):
    count: int = 0
    units: int = 0
    amount: dict[str, float] = Field(default_factory=dict)
    rate: float = 0.0  # returned units / sold units over the range


class PriceAlertRow(BaseModel):
    listing_id: str
    sku: str
    marketplace: str
    price: float | None = None
    buy_box_price: float | None = None
    buy_box_winner: bool | None = None
    lowest_competitor_price: float | None = None
    currency: str | None = None


class Dashboard(BaseModel):
    start: str
    end: str
    tz: str
    currency: str | None
    currencies: list[MoneyTotal]
    by_channel: list[ChannelRow]
    by_day: list[DayRow]
    top_products: list[TopProduct]
    pending_orders: list[PendingOrderRow]
    pending_total: int
    late_total: int
    low_stock: list[LowStockRow]
    returns: ReturnsSummary
    price_alerts: list[PriceAlertRow]
    open_alerts: int
    fees: dict[str, float]
    truncated: bool = False
    connectors: int = 0


def _counted(order: Order) -> bool:
    return order.status in COUNTED_ORDER_STATUSES


def _units(order: Order) -> int:
    return sum(line.quantity for line in order.lines) or order.item_count


def low_stock(store: CommerceStore, context: TenantContext, settings: CommerceSettings, limit: int = 50) -> list[LowStockRow]:
    rows: list[LowStockRow] = []
    thresholds: dict[str, int] = {}
    for listing in store.all_listings(context):
        if listing.status == "inactive" or listing.quantity is None:
            continue
        if listing.sku not in thresholds:
            product = store.get_product_by_sku(context, listing.sku)
            thresholds[listing.sku] = (
                product.low_stock_threshold if product and product.low_stock_threshold is not None else settings.low_stock_threshold
            )
        if listing.quantity <= thresholds[listing.sku]:
            rows.append(
                LowStockRow(
                    listing_id=listing.id,
                    sku=listing.sku,
                    title=listing.title,
                    channel=listing.channel,
                    marketplace=marketplace_name(listing.channel, listing.marketplace_id),
                    quantity=listing.quantity,
                    threshold=thresholds[listing.sku],
                )
            )
    rows.sort(key=lambda row: (row.quantity, row.sku))
    return rows[:limit]


def pending(store: CommerceStore, context: TenantContext, settings: CommerceSettings, now: datetime, limit: int = 20) -> tuple[list[PendingOrderRow], int, int]:
    orders, total = store.list_orders(context, statuses=list(OPEN_ORDER_STATUSES), limit=1000, offset=0)
    rows = []
    late_total = 0
    for order in orders:
        due = order.ship_by or order.purchased_at + timedelta(hours=settings.handling_hours)
        late = order.fulfillment in (None, "merchant") and due < now
        late_total += late
        rows.append(
            PendingOrderRow(
                id=order.id,
                external_id=order.external_id,
                channel=order.channel,
                marketplace=marketplace_name(order.channel, order.marketplace_id),
                status=order.status,
                purchased_at=order.purchased_at,
                ship_by=order.ship_by,
                late=late,
                total_amount=order.total_amount,
                currency=order.currency,
                item_count=order.item_count,
            )
        )
    rows.sort(key=lambda row: (not row.late, row.ship_by or row.purchased_at))
    return rows[:limit], total, late_total


def price_alerts(listings: list[Listing], settings: CommerceSettings) -> list[PriceAlertRow]:
    rows = []
    for listing in listings:
        lost = listing.buy_box_winner is False
        undercut = (
            listing.lowest_competitor_price is not None
            and listing.price
            and listing.lowest_competitor_price < listing.price * (1 - settings.price_alert_percent / 100)
        )
        if lost or undercut:
            rows.append(
                PriceAlertRow(
                    listing_id=listing.id,
                    sku=listing.sku,
                    marketplace=marketplace_name(listing.channel, listing.marketplace_id),
                    price=listing.price,
                    buy_box_price=listing.buy_box_price,
                    buy_box_winner=listing.buy_box_winner,
                    lowest_competitor_price=listing.lowest_competitor_price,
                    currency=listing.currency,
                )
            )
    return rows[:50]


def dashboard(
    store: CommerceStore,
    context: TenantContext,
    settings: CommerceSettings,
    *,
    first: date,
    last: date,
    tz_name: str | None,
    currency: str | None,
    channel: Channel | None = None,
    now: datetime | None = None,
) -> Dashboard:
    now = now or utc_now()
    tz = zone(tz_name)
    start, end = _bounds(first, last, tz)
    cap = commerce_config.dashboard_max_orders
    orders = [o for o in store.orders_between(context, start, end, limit=cap + 1) if _counted(o) and (channel is None or o.channel == channel)]
    truncated = len(orders) > cap
    orders = orders[:cap]

    totals: dict[str, MoneyTotal] = {}
    for order in orders:
        key = order.currency or "-"
        total = totals.setdefault(key, MoneyTotal(currency=key))
        total.revenue += order.total_amount
        total.orders += 1
        total.units += _units(order)
    currencies = sorted(totals.values(), key=lambda t: t.revenue, reverse=True)
    for total in currencies:
        total.revenue = round(total.revenue, 2)
    chosen = currency if currency in totals else (currencies[0].currency if currencies else None)

    by_channel: dict[tuple[str, str, str], ChannelRow] = {}
    days: dict[tuple[str, str], DayRow] = {}
    top: dict[str, TopProduct] = {}
    for order in orders:
        key = (order.channel.value, order.marketplace_id, order.currency or "-")
        row = by_channel.setdefault(
            key,
            ChannelRow(
                channel=order.channel,
                label=CHANNEL_LABELS[order.channel],
                marketplace_id=order.marketplace_id,
                marketplace=marketplace_name(order.channel, order.marketplace_id),
                currency=order.currency or "-",
            ),
        )
        row.revenue = round(row.revenue + order.total_amount, 2)
        row.orders += 1
        row.units += _units(order)
        if (order.currency or "-") != chosen:
            continue
        day = order.purchased_at.astimezone(tz).date().isoformat()
        day_row = days.setdefault((day, order.channel.value), DayRow(date=day, channel=order.channel))
        day_row.revenue = round(day_row.revenue + order.total_amount, 2)
        day_row.orders += 1
        day_row.units += _units(order)
        for line in order.lines:
            item = top.setdefault(line.sku, TopProduct(sku=line.sku, title=line.title, currency=chosen or "-"))
            item.units += line.quantity
            item.revenue = round(item.revenue + (line.total if line.total is not None else (line.unit_price or 0) * line.quantity), 2)
    top_rows = sorted(top.values(), key=lambda p: (p.units, p.revenue), reverse=True)[:10]
    for row in top_rows:
        product = store.get_product_by_sku(context, row.sku)
        if product is not None:
            row.title = product.title or row.title
            row.title_ar = product.title_ar
            row.tyre_size = product.tyre.size

    returns, _ = store.list_returns(context, since=start, limit=10_000, offset=0)
    returns = [r for r in returns if r.returned_at < end and (channel is None or r.channel == channel)]
    amounts: dict[str, float] = defaultdict(float)
    for record in returns:
        if record.amount is not None:
            amounts[record.currency or "-"] += record.amount
    sold_units = sum(_units(o) for o in orders)
    returned_units = sum(r.quantity for r in returns)

    fees: dict[str, float] = defaultdict(float)
    for fee in store.list_fees(context, since=start, until=end):
        if channel is None or fee.channel == channel:
            if fee.kind.value == "fee":
                fees[fee.currency or "-"] += fee.amount

    pending_rows, pending_total, late_total = pending(store, context, settings, now)
    if channel is not None:
        pending_rows = [row for row in pending_rows if row.channel == channel]
    listings = store.all_listings(context)
    _, open_alerts = store.list_alerts(context, status=AlertStatus.OPEN, limit=1, offset=0)
    return Dashboard(
        start=first.isoformat(),
        end=last.isoformat(),
        tz=str(tz.key),
        currency=chosen,
        currencies=currencies,
        by_channel=sorted(by_channel.values(), key=lambda r: r.revenue, reverse=True),
        by_day=sorted(days.values(), key=lambda r: (r.date, r.channel.value)),
        top_products=top_rows,
        pending_orders=pending_rows,
        pending_total=pending_total,
        late_total=late_total,
        low_stock=[row for row in low_stock(store, context, settings) if channel is None or row.channel == channel],
        returns=ReturnsSummary(
            count=len(returns),
            units=returned_units,
            amount={k: round(v, 2) for k, v in amounts.items()},
            rate=round(returned_units / sold_units, 4) if sold_units else 0.0,
        ),
        price_alerts=price_alerts([l for l in listings if channel is None or l.channel == channel], settings),
        open_alerts=open_alerts,
        fees={k: round(v, 2) for k, v in fees.items()},
        truncated=truncated,
        connectors=len(store.list_connectors(context)),
    )


# -- spoken and written summaries (agent tools, voice) --------------------------------------

PERIOD_WORDS = {
    "today": ("today", "اليوم"),
    "yesterday": ("yesterday", "أمس"),
    "last_7_days": ("in the last 7 days", "في آخر 7 أيام"),
    "last_30_days": ("in the last 30 days", "في آخر 30 يومًا"),
    "this_month": ("this month", "هذا الشهر"),
}
CHANNEL_WORDS_AR = {Channel.AMAZON: "أمازون", Channel.NOON: "نون", Channel.OWN_STORE: "متجرك"}


def _money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def sales_summary(
    store: CommerceStore,
    context: TenantContext,
    *,
    channel: Channel | None,
    period: str,
    tz_name: str | None = None,
    arabic: bool = False,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """A short sentence (English or Arabic) and the structured numbers behind it."""
    period = period if period in PERIODS else "today"
    tz = zone(tz_name)
    start, end, first, last = period_range(period, tz, now)
    orders = [o for o in store.orders_between(context, start, end, limit=commerce_config.dashboard_max_orders) if _counted(o)]
    if channel is not None:
        orders = [o for o in orders if o.channel == channel]
    totals: dict[str, float] = defaultdict(float)
    units = 0
    skus: dict[str, int] = defaultdict(int)
    for order in orders:
        totals[order.currency or "-"] += order.total_amount
        units += _units(order)
        for line in order.lines:
            skus[line.sku] += line.quantity
    data = {
        "period": period,
        "channel": channel.value if channel else None,
        "start": first.isoformat(),
        "end": last.isoformat(),
        "orders": len(orders),
        "units": units,
        "revenue": {k: round(v, 2) for k, v in totals.items()},
        "top_skus": sorted(skus.items(), key=lambda kv: kv[1], reverse=True)[:3],
    }
    when_en, when_ar = PERIOD_WORDS[period]
    if arabic:
        who = f"مبيعات {CHANNEL_WORDS_AR[channel]}" if channel else "مبيعاتك"
        if not orders:
            return f"{who} {when_ar}: لا توجد طلبات بعد.", data
        money = " و".join(_money(v, k) for k, v in sorted(totals.items(), key=lambda kv: -kv[1]))
        text = f"{who} {when_ar}: {len(orders)} طلب، {units} قطعة، بقيمة {money}."
        if data["top_skus"]:
            text += f" الأكثر مبيعًا: {data['top_skus'][0][0]} ({data['top_skus'][0][1]} قطعة)."
        return text, data
    who = f"{CHANNEL_LABELS[channel]} sales" if channel else "Sales across all channels"
    if not orders:
        return f"{who} {when_en}: no orders yet.", data
    money = " and ".join(_money(v, k) for k, v in sorted(totals.items(), key=lambda kv: -kv[1]))
    order_word = "order" if len(orders) == 1 else "orders"
    unit_word = "unit" if units == 1 else "units"
    text = f"{who} {when_en}: {len(orders)} {order_word}, {units} {unit_word}, {money}."
    if data["top_skus"]:
        sku, count = data["top_skus"][0]
        text += f" Best seller: {sku} ({count} sold)."
    return text, data
