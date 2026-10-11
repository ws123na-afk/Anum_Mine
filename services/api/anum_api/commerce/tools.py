"""Commerce agent tools and request parsing (docs/commerce.md#assistant-and-voice).

Read tools (low risk, run without approval for anyone with ``commerce:read``):
``commerce.sales_summary``, ``commerce.list_orders``, ``commerce.get_order``,
``commerce.low_stock``.

Change tools (high risk: the runtime always pauses them for an approval bound to the
payload hash the approver saw; owners and members only, re-checked at execution), one
per channel so governance can treat channels differently:
``commerce.amazon.update_price``, ``commerce.noon.update_price``,
``commerce.own_store.update_price`` and the matching ``update_stock`` tools.

``plan_call`` turns the user's own words (never retrieved text or model output, threat
model G5) into one of these calls: "how were Amazon sales today?",
"set the price of SKU MX-2055516 on Amazon.sa to 450", "orders not shipped".
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..authorization import Permission
from ..schemas import RiskLevel, TenantContext, utc_now
from .clients.base import UnsupportedOperation
from .config import commerce_config
from .connectors import build_client, decrypt_credentials
from .http import ChannelError
from .insights import low_stock, sales_summary
from .models import (
    AMAZON_MARKETPLACES,
    CHANNEL_LABELS,
    NOON_MARKETPLACES,
    OPEN_ORDER_STATUSES,
    Channel,
    Listing,
    OrderStatus,
    marketplace_name,
)
from .store import open_commerce_store
from .sync import workspace_settings

READ_TOOLS = ("commerce.sales_summary", "commerce.list_orders", "commerce.get_order", "commerce.low_stock")
MAX_PRICE = 1_000_000.0
MAX_QUANTITY = 1_000_000


def change_tool(channel: Channel, kind: str) -> str:
    return f"commerce.{channel.value}.update_{kind}"


def _has(context: TenantContext, permission: Permission) -> bool:
    from ..dependencies import has_permission

    return has_permission(context, permission)


def _denied(what: str, who: str) -> ToolResult:
    return ToolResult(status="blocked", summary=f"You cannot {what}: {who}.")


def _channel(value: Any) -> Channel | None:
    try:
        return Channel(str(value)) if value else None
    except ValueError:
        return None


# -- read tools ---------------------------------------------------------------------------


async def _sales_summary(call: ToolCall, context: TenantContext) -> ToolResult:
    if not _has(context, Permission.COMMERCE_READ):
        return _denied("read sales", "ask a workspace member to add you")
    args = call.arguments
    with open_commerce_store(context) as store:
        text, data = sales_summary(
            store,
            context,
            channel=_channel(args.get("channel")),
            period=str(args.get("period") or "today"),
            tz_name=args.get("tz"),
            arabic=str(args.get("language") or "").startswith("ar"),
        )
    return ToolResult(status="succeeded", summary=text, output=data, audit_metadata={"orders": data["orders"]})


async def _list_orders(call: ToolCall, context: TenantContext) -> ToolResult:
    if not _has(context, Permission.COMMERCE_READ):
        return _denied("read orders", "ask a workspace member to add you")
    args = call.arguments
    channel = _channel(args.get("channel"))
    status = str(args.get("status") or "open")
    statuses = list(OPEN_ORDER_STATUSES) if status == "open" else [s for s in OrderStatus if s.value == status] or None
    limit = max(1, min(20, int(args.get("limit") or 10)))
    with open_commerce_store(context) as store:
        orders, total = store.list_orders(context, channel=channel.value if channel else None, statuses=statuses, limit=limit, offset=0)
    where = CHANNEL_LABELS[channel] if channel else "all channels"
    if not orders:
        return ToolResult(status="succeeded", summary=f"No {status} orders on {where}.", output={"total": 0, "orders": []})
    lines = [
        f"{o.external_id} · {marketplace_name(o.channel, o.marketplace_id)} · {o.status.value} · {o.item_count} item(s) · {o.total_amount:.2f} {o.currency or ''}".rstrip()
        for o in orders
    ]
    summary = f"{total} {status} order(s) on {where}" + (f"; the {len(orders)} newest:" if total > len(orders) else ":") + "\n" + "\n".join(lines)
    return ToolResult(
        status="succeeded",
        summary=summary,
        output={"total": total, "orders": [o.model_dump(mode="json", exclude={"customer_id"}) for o in orders]},
        audit_metadata={"count": len(orders)},
    )


async def _get_order(call: ToolCall, context: TenantContext) -> ToolResult:
    if not _has(context, Permission.COMMERCE_READ):
        return _denied("read orders", "ask a workspace member to add you")
    ref = str(call.arguments.get("order") or "").strip()
    if not ref:
        return ToolResult(status="failed", summary="Say which order: its number, for example 402-1234567-1234567.")
    with open_commerce_store(context) as store:
        order = store.get_order(context, ref) or store.find_order(context, ref)
    if order is None:
        return ToolResult(status="failed", summary=f"No order {ref} in the synced orders. Sync the channel, then ask again.")
    items = "; ".join(f"{line.quantity} × {line.sku} {line.title}".strip() for line in order.lines) or "no lines synced"
    ship = f", ship by {order.ship_by:%Y-%m-%d %H:%M} UTC" if order.ship_by else ""
    summary = (
        f"Order {order.external_id} on {marketplace_name(order.channel, order.marketplace_id)}: {order.status.value}, "
        f"{order.total_amount:.2f} {order.currency or ''}, placed {order.purchased_at:%Y-%m-%d %H:%M} UTC{ship}. Items: {items}."
    )
    return ToolResult(status="succeeded", summary=summary, output=order.model_dump(mode="json", exclude={"customer_id"}))


async def _low_stock(call: ToolCall, context: TenantContext) -> ToolResult:
    if not _has(context, Permission.COMMERCE_READ):
        return _denied("read stock", "ask a workspace member to add you")
    channel = _channel(call.arguments.get("channel"))
    with open_commerce_store(context) as store:
        rows = low_stock(store, context, workspace_settings(context, store), limit=100)
    if channel is not None:
        rows = [row for row in rows if row.channel == channel]
    if not rows:
        return ToolResult(status="succeeded", summary="Nothing is low on stock.", output={"items": []})
    lines = [f"{row.sku} on {row.marketplace}: {row.quantity} left" for row in rows[:15]]
    more = f" (and {len(rows) - 15} more)" if len(rows) > 15 else ""
    return ToolResult(
        status="succeeded",
        summary=f"{len(rows)} listing(s) low on stock{more}:\n" + "\n".join(lines),
        output={"items": [row.model_dump(mode="json") for row in rows]},
    )


# -- change tools (approved) ------------------------------------------------------------


def _resolve_listing(store: Any, context: TenantContext, channel: Channel, args: dict[str, Any]) -> Listing | str:
    listing_id = args.get("listing_id")
    if listing_id:
        listing = store.get_listing(context, str(listing_id))
        if listing is None or listing.channel != channel:
            return "The listing is no longer synced for this channel."
        if args.get("sku") and listing.sku != args["sku"]:
            return "The approved SKU does not match the listing."
        return listing
    sku = str(args.get("sku") or "").strip()
    if not sku:
        return "Say which SKU to change."
    found, _ = store.list_listings(context, sku=sku, limit=20)
    found = [item for item in found if item.channel == channel]
    marketplace = args.get("marketplace_id")
    if marketplace:
        found = [item for item in found if item.marketplace_id == marketplace]
    if not found:
        return f"No {CHANNEL_LABELS[channel]} listing with SKU {sku}. Sync the channel or check the SKU."
    if len(found) > 1:
        names = ", ".join(marketplace_name(item.channel, item.marketplace_id) for item in found)
        return f"SKU {sku} is listed on {names}; say which marketplace."
    return found[0]


def _change_handler(channel: Channel, kind: str):  # type: ignore[no-untyped-def]
    async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
        if not _has(context, Permission.COMMERCE_OPERATE):
            return _denied("change prices or stock", "owners and members can, after approval")
        args = call.arguments
        try:
            value = float(args["price"]) if kind == "price" else int(args["quantity"])
        except (KeyError, TypeError, ValueError):
            return ToolResult(status="failed", summary=f"The {'price' if kind == 'price' else 'quantity'} is missing or not a number; nothing was changed.")
        if kind == "price" and not (0 < value <= MAX_PRICE):
            return ToolResult(status="failed", summary="The price must be above 0; nothing was changed.")
        if kind == "stock" and not (0 <= value <= MAX_QUANTITY):
            return ToolResult(status="failed", summary="The quantity must be 0 or more; nothing was changed.")
        with open_commerce_store(context) as store:
            resolved = _resolve_listing(store, context, channel, args)
            if isinstance(resolved, str):
                return ToolResult(status="failed", summary=f"{resolved} Nothing was changed.")
            listing = resolved
            connector = store.get_connector(context, listing.connector_id)
            ciphertext = store.credentials_ciphertext(context, listing.connector_id)
        if connector is None or not connector.enabled:
            return ToolResult(status="failed", summary="That channel connection is missing or turned off; nothing was changed.")
        if args.get("connector_id") and args["connector_id"] != connector.id:
            return ToolResult(status="failed", summary="The approved connection does not match the listing; nothing was changed.")
        currency = str(args.get("currency") or listing.currency or "SAR")
        try:
            client = build_client(connector, decrypt_credentials(ciphertext))
            if kind == "price":
                message = await client.update_price(listing.marketplace_id, listing.sku, listing.external_id, listing.product_type, float(value), currency)
            else:
                message = await client.update_stock(listing.marketplace_id, listing.sku, listing.external_id, listing.product_type, int(value))
        except (ChannelError, UnsupportedOperation) as exc:
            reason = exc.message if isinstance(exc, ChannelError) else str(exc)
            return ToolResult(
                status="failed",
                summary=f"Not changed: {reason}",
                audit_metadata={"channel": channel.value, "change": kind, "outcome": "refused"},
            )
        previous = listing.price if kind == "price" else listing.quantity
        with open_commerce_store(context) as store:
            fresh = store.get_listing(context, listing.id)
            if fresh is not None:
                if kind == "price":
                    fresh.price = float(value)
                else:
                    fresh.quantity = int(value)
                fresh.updated_at = utc_now()
                store.upsert_listing(fresh)
        where = marketplace_name(listing.channel, listing.marketplace_id)
        shown = f"{float(value):.2f} {currency}" if kind == "price" else str(int(value))
        return ToolResult(
            status="succeeded",
            summary=f"{'Price' if kind == 'price' else 'Stock'} of {listing.sku} on {where} set to {shown}. {message}",
            output={"listing_id": listing.id, "sku": listing.sku, "marketplace": where, kind: value, "previous": previous},
            audit_metadata={"channel": channel.value, "change": kind, "outcome": "applied"},
        )

    return handler


def _target(channel: Channel) -> str | None:
    if channel == Channel.AMAZON:
        return urlsplit(commerce_config.amazon_eu_endpoint).hostname
    if channel == Channel.NOON:
        return urlsplit(commerce_config.noon_base_url).hostname
    return None  # each own store has its own host; the approval shows it in target_host


def register_commerce_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the commerce tools to a runtime's registry (the API's and the Temporal worker's)."""
    if "commerce.sales_summary" in registry.names:
        return registry
    reads = {
        "commerce.sales_summary": ("Sales totals for a channel and period (orders, units, revenue per currency).", _sales_summary),
        "commerce.list_orders": ("Recent orders by channel and status, newest first.", _list_orders),
        "commerce.get_order": ("One order with its lines, by order number.", _get_order),
        "commerce.low_stock": ("Listings at or below their low-stock threshold.", _low_stock),
    }
    for name, (description, handler) in reads.items():
        registry.register(
            ToolDefinition(
                name=name, description=description, risk_level=RiskLevel.LOW, idempotent=True, timeout_seconds=30,
                category="commerce", action_kind="read",
            ),
            handler,
        )
    for channel in Channel:
        for kind in ("price", "stock"):
            registry.register(
                ToolDefinition(
                    name=change_tool(channel, kind),
                    description=f"Change a listing's {kind} on {CHANNEL_LABELS[channel]} after approval.",
                    risk_level=RiskLevel.HIGH,
                    required_roles=frozenset({"owner", "member"}),
                    timeout_seconds=60,
                    target=_target(channel),
                    # Autonomy (docs/autonomy.md): price and stock changes always ask.
                    category="commerce",
                    action_kind="price_change" if kind == "price" else "stock_change",
                ),
                _change_handler(channel, kind),
            )
    return registry


# -- parsing the user's own request ------------------------------------------------------

_AMAZON = re.compile(r"\bamazon\b|أمازون|امازون", re.IGNORECASE)
_NOON = re.compile(r"\bnoon\b|(?<!\w)نون(?!\w)", re.IGNORECASE)
_OWN = re.compile(r"\b(own|my|our)\s+(store|shop|website|site|app)\b|\bwebsite\b|متجري|موقعي|متجرنا", re.IGNORECASE)
_SA = re.compile(r"amazon\.sa|\bksa\b|\bsaudi|السعودي", re.IGNORECASE)
_AE = re.compile(r"amazon\.ae|\buae\b|emirates|الإمارات|الامارات", re.IGNORECASE)
_PRICE_CHANGE = re.compile(r"\b(set|change|update|make|lower|raise|drop|reduce|increase|put)\b[^.?!]*\bprice\b|\bprice\b[^.?!]*\bto\s*(?:SAR|AED|SR)?\s*\d|(غيّر|غير|عدّل|عدل|اجعل)\s*(ال)?سعر", re.IGNORECASE)
_STOCK_CHANGE = re.compile(r"\b(set|change|update|make|put)\b[^.?!]*\b(stock|quantity|inventory)\b|\b(stock|quantity|inventory)\b[^.?!]*\bto\b\s*\d|(غيّر|غير|عدّل|عدل|اجعل)\s*(ال)?(مخزون|كمية)", re.IGNORECASE)
_SKU = re.compile(r"\bsku\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,119})", re.IGNORECASE)
_SKU_LIKE = re.compile(r"\b(?=[A-Za-z0-9._/-]*[A-Za-z])(?=[A-Za-z0-9._/-]*\d)[A-Za-z0-9][A-Za-z0-9._/-]{3,119}\b")
_TO_NUMBER = re.compile(r"\b(?:to|=|الى|إلى)\s*(?:SAR|AED|SR|ريال|درهم)?\s*(\d{1,7}(?:[.,]\d{1,2})?)", re.IGNORECASE)
_ORDER_ID = re.compile(r"\b(\d{3}-\d{7}-\d{7}|[A-Z]{1,4}-?\d{5,}|N\d{8,})\b")
_ORDER_WORD = re.compile(r"\border\b|طلب", re.IGNORECASE)
_ORDERS = re.compile(r"\borders\b|\bunshipped\b|\bnot shipped\b|\bpending\b|\bto ship\b|طلبات|الطلبات", re.IGNORECASE)
_LOW_STOCK = re.compile(r"\blow[- ]stock\b|\blow (on|in) stock\b|\bout of stock\b|\brunning low\b|\brunning out\b|\brestock\b|نفد|ينفد|المخزون المنخفض|مخزون منخفض", re.IGNORECASE)
_SALES = re.compile(r"\bsales\b|\bsold\b|\brevenue\b|\bhow (did|were|was|are)\b.*\b(do|doing|sell|selling|sales)\b|\bturnover\b|مبيعات|المبيعات|بعنا|إيرادات", re.IGNORECASE)
_PERIOD = [
    ("yesterday", re.compile(r"\byesterday\b|أمس|امس", re.IGNORECASE)),
    ("last_7_days", re.compile(r"\b(this|last|past)\s+week\b|\b7 days\b|الأسبوع|الاسبوع", re.IGNORECASE)),
    ("this_month", re.compile(r"\bthis month\b|\bmonth to date\b|هذا الشهر", re.IGNORECASE)),
    ("last_30_days", re.compile(r"\b(last|past)\s+(month|30 days)\b|آخر شهر|الشهر الماضي", re.IGNORECASE)),
    ("today", re.compile(r"\btoday\b|اليوم", re.IGNORECASE)),
]
CHANGE_HELP = (
    "To change a price or stock, name the channel, the SKU and the new value, for example "
    "\"set the price of SKU MX-2055516 on Amazon.sa to 450\" or \"set noon stock of SKU MX-2055516 to 12\". "
    "The change then waits for approval before anything is sent."
)
TRIGGERS = frozenset(
    {
        "sales", "sold", "revenue", "orders", "order", "amazon", "noon", "stock", "inventory", "listing", "listings",
        "price", "prices", "returns", "unshipped", "sku", "store", "shop", "selling", "restock",
    }
)


def detect_channel(text: str) -> Channel | None:
    if _AMAZON.search(text):
        return Channel.AMAZON
    if _NOON.search(text):
        return Channel.NOON
    if _OWN.search(text):
        return Channel.OWN_STORE
    return None


def detect_marketplace(text: str, channel: Channel | None) -> str | None:
    saudi, emirates = bool(_SA.search(text)), bool(_AE.search(text))
    if saudi == emirates:
        return None
    if channel == Channel.AMAZON:
        return next(m for m, info in AMAZON_MARKETPLACES.items() if info["country"] == ("SA" if saudi else "AE"))
    if channel == Channel.NOON:
        return "SA" if saudi else "AE"
    return None


def detect_period(text: str) -> str:
    for period, pattern in _PERIOD:
        if pattern.search(text):
            return period
    return "today"


def _sku(text: str, exclude: set[str]) -> str | None:
    match = _SKU.search(text)
    if match:
        return match.group(1).rstrip(".,")
    for candidate in _SKU_LIKE.findall(text):
        if candidate.lower() not in exclude and not candidate.lower().startswith(("amazon.", "http")):
            return candidate.rstrip(".,")
    return None


def is_change_request(text: str) -> bool:
    return bool(_PRICE_CHANGE.search(text) or _STOCK_CHANGE.search(text))


def is_commerce_question(text: str) -> bool:
    return bool(_SALES.search(text) or _LOW_STOCK.search(text) or (_ORDERS.search(text) and detect_channel(text)) or (_ORDER_WORD.search(text) and _ORDER_ID.search(text)))


def plan_call(prompt: str, language: str | None = None) -> ToolCall | None:
    """The commerce tool call the user's own words ask for, or None."""
    text = " ".join(prompt.split())
    channel = detect_channel(text)
    marketplace = detect_marketplace(text, channel)
    if is_change_request(text):
        number = _TO_NUMBER.search(text)
        sku = _sku(text, {"amazon", "noon"})
        price = bool(_PRICE_CHANGE.search(text))
        if channel is None:
            # Not about a sales channel ("send the price list to the buyer"): not ours.
            return None
        if number is None or sku is None:
            # Nothing is proposed for approval until the request names all three.
            return ToolCall(name="anum.respond", arguments={"text": CHANGE_HELP})
        raw = number.group(1).replace(",", ".")
        arguments: dict[str, Any] = {"sku": sku}
        if marketplace:
            arguments["marketplace_id"] = marketplace
        arguments["price" if price else "quantity"] = round(float(raw), 2) if price else int(float(raw))
        return ToolCall(name=change_tool(channel, "price" if price else "stock"), arguments=arguments)
    order = _ORDER_ID.search(text)
    if order and _ORDER_WORD.search(text):
        return ToolCall(name="commerce.get_order", arguments={"order": order.group(1)})
    if _LOW_STOCK.search(text):
        return ToolCall(name="commerce.low_stock", arguments={"channel": channel.value if channel else None})
    if _SALES.search(text):
        args: dict[str, Any] = {"channel": channel.value if channel else None, "period": detect_period(text)}
        if language:
            args["language"] = language
        return ToolCall(name="commerce.sales_summary", arguments=args)
    if _ORDERS.search(text):
        status = "unshipped" if re.search(r"unshipped|not shipped|to ship", text, re.IGNORECASE) else "open"
        return ToolCall(name="commerce.list_orders", arguments={"channel": channel.value if channel else None, "status": status})
    return None

