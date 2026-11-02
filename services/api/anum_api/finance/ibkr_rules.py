"""IBKR live trading: records and the pure rules (docs/finance-live-trading.md).

Everything here is pure and deterministic: no database, no network. ``ibkr_service.py`` reads
the store and IBKR, then asks these functions whether an order may be created, approved or
sent. The same checks run at approval **and** immediately before the order is sent, each time
from fresh IBKR data.

Owner decisions (2 Nov 2026): IBKR, US-listed ETFs only, every order approved by the owner,
total capital at most USD 50 to start, about USD 10 per order, a daily loss stop of USD 5 that
the owner can edit, and only honest figures from IBKR's own data (no projections).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .intel.markets import venue

NEW_YORK = ZoneInfo("America/New_York")
USD = "USD"

# Hard ceilings no owner edit can pass (the database repeats the per-order one for live orders).
CEILING_CAPITAL = Decimal(10000)
CEILING_ORDER = Decimal(1000)
CEILING_DAILY_LOSS = Decimal(1000)
CEILING_ORDERS_PER_DAY = 20
QUOTE_MAX_AGE_SECONDS = 120
FRACTION_DIGITS = 4

# US-listed exchange-traded funds the owner may put on the allowed list: broad, plain (no
# leverage, no inverse, no options), all listed on a US exchange. The conid is looked up at IBKR
# when the owner adds one and must be a US stock-type contract (ETFs are stock-type at IBKR).
US_ETFS: dict[str, str] = {
    "SPY": "SPDR S&P 500 ETF Trust", "VOO": "Vanguard S&P 500 ETF", "IVV": "iShares Core S&P 500 ETF",
    "SPLG": "SPDR Portfolio S&P 500 ETF", "VTI": "Vanguard Total Stock Market ETF", "ITOT": "iShares Core S&P Total U.S. Stock Market ETF",
    "SCHB": "Schwab U.S. Broad Market ETF", "SPTM": "SPDR Portfolio S&P 1500 Composite Stock Market ETF",
    "QQQ": "Invesco QQQ Trust", "QQQM": "Invesco NASDAQ 100 ETF", "VT": "Vanguard Total World Stock ETF",
    "VXUS": "Vanguard Total International Stock ETF", "IXUS": "iShares Core MSCI Total International Stock ETF",
    "VEA": "Vanguard FTSE Developed Markets ETF", "VWO": "Vanguard FTSE Emerging Markets ETF",
    "BND": "Vanguard Total Bond Market ETF", "AGG": "iShares Core U.S. Aggregate Bond ETF",
    "SCHZ": "Schwab U.S. Aggregate Bond ETF", "SGOV": "iShares 0-3 Month Treasury Bond ETF",
    "BIL": "SPDR Bloomberg 1-3 Month T-Bill ETF", "SHV": "iShares Short Treasury Bond ETF",
    "VIG": "Vanguard Dividend Appreciation ETF", "SCHD": "Schwab U.S. Dividend Equity ETF",
    "VYM": "Vanguard High Dividend Yield ETF", "GLD": "SPDR Gold Shares", "IAU": "iShares Gold Trust",
    # Shariah-screened by the fund's issuer (owner decision, 2 Nov 2026: halal investing first).
    # ANUM shows the issuer's own label; it does not certify compliance.
    "SPUS": "SP Funds S&P 500 Sharia Industry Exclusions ETF", "HLAL": "Wahed FTSE USA Shariah ETF",
    "UMMA": "Wahed Dow Jones Islamic World ETF", "SPRE": "SP Funds S&P Global REIT Sharia ETF",
    "SPSK": "SP Funds Dow Jones Global Sukuk ETF",
}
SHARIAH_ETFS = frozenset({"SPUS", "HLAL", "UMMA", "SPRE", "SPSK"})
US_EXCHANGES = frozenset({"ARCA", "NYSE", "NASDAQ", "BATS", "AMEX", "NYSEARCA", "CBOE"})

# Early closes (13:00 New York) on the exchanges' published calendars for 2026 and 2027.
EARLY_CLOSES = frozenset({date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)})
EARLY_CLOSE = time(13, 0)


class Environment(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class OrderState(StrEnum):
    SUBMITTING = "submitting"  # claimed and about to be sent (or the API stopped while sending)
    SUBMITTED = "submitted"  # IBKR holds it, nothing filled yet
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"  # IBKR refused it, or ANUM declined a question IBKR asked
    UNKNOWN = "unknown"  # sent, but IBKR did not answer: looked up by its client order id, never resent blind


OPEN_STATES = frozenset({OrderState.SUBMITTING, OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED, OrderState.CANCEL_REQUESTED, OrderState.UNKNOWN})
DONE_STATES = frozenset({OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED})


class _Owned(BaseModel):
    tenant_id: str
    workspace_id: str
    owner_user_id: str


# -- settings ---------------------------------------------------------------------------------------


class BrokerLimits(BaseModel):
    """The owner's hard limits, all in USD. Checked at approval and again right before sending."""

    model_config = ConfigDict(extra="forbid")

    max_capital: Decimal = Field(default=Decimal(50), gt=0, le=CEILING_CAPITAL)
    max_order: Decimal = Field(default=Decimal(10), gt=0, le=CEILING_ORDER)
    max_daily_loss: Decimal = Field(default=Decimal(5), gt=0, le=CEILING_DAILY_LOSS)
    max_orders_per_day: int = Field(default=3, ge=1, le=CEILING_ORDERS_PER_DAY)
    price_band_percent: Decimal = Field(default=Decimal(2), ge=Decimal("0.1"), le=Decimal(10))
    session_buffer_minutes: int = Field(default=15, ge=0, le=60)
    fee_buffer: Decimal = Field(default=Decimal(1), ge=0, le=Decimal(50))

    @model_validator(mode="after")
    def _order_within_capital(self) -> BrokerLimits:
        if self.max_order > self.max_capital:
            raise ValueError("The most per order cannot be more than the total capital.")
        return self

    def loosened(self, before: BrokerLimits) -> list[str]:
        """Which limits this change makes less strict than ``before``."""
        names = []
        for field in ("max_capital", "max_order", "max_daily_loss", "max_orders_per_day", "price_band_percent"):
            if getattr(self, field) > getattr(before, field):
                names.append(field)
        if self.session_buffer_minutes < before.session_buffer_minutes:
            names.append("session_buffer_minutes")
        if self.fee_buffer < before.fee_buffer:
            names.append("fee_buffer")
        return names


class AllowedSymbol(BaseModel):
    symbol: str
    conid: int = Field(gt=0)
    name: str = ""
    exchange: str = ""
    shariah_screened: bool = False  # by the fund issuer's own Shariah board; ANUM does not certify it


class LiveSwitch(BaseModel):
    """The owner's own switch for live orders: a typed code, then a cooling-off period."""

    on: bool = False
    code_hash: str | None = None
    code_expires_at: datetime | None = None
    requested_at: datetime | None = None
    switched_on_at: datetime | None = None
    effective_at: datetime | None = None
    switched_off_at: datetime | None = None


class Reconciliation(BaseModel):
    """IBKR's records against ANUM's, per environment. Any open mismatch blocks new orders."""

    state: Literal["not_checked", "ok", "mismatch"] = "not_checked"
    checked_at: datetime | None = None
    mismatches: list[dict[str, str]] = Field(default_factory=list)
    # Positions IBKR held when the owner last accepted IBKR's state (conid -> quantity text).
    baseline: dict[str, str] = Field(default_factory=dict)
    baseline_at: datetime | None = None
    # How much of each ANUM order was already filled when the baseline was taken (order id -> quantity text).
    filled_at_baseline: dict[str, str] = Field(default_factory=dict)
    episodes: int = 0  # times the records went from matching to not matching
    explained: int = 0  # of those, how many the owner explained with a note


class BuyPlan(BaseModel):
    """A regular buy the owner sets: on ``day_of_month`` ANUM PROPOSES one buy for approval. It never sends."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=1, max_length=10)
    amount: Decimal = Field(gt=0, le=CEILING_ORDER, decimal_places=2)
    day_of_month: int = Field(ge=1, le=28)
    active: bool = True
    last_proposed: str | None = None  # the New York month ("2026-11") a proposal was last made

    def due(self, now: datetime) -> bool:
        local = now.astimezone(NEW_YORK)
        return self.active and local.day >= self.day_of_month and self.last_proposed != f"{local:%Y-%m}"


class BrokerSettings(BaseModel):
    """One owner's IBKR settings in one workspace."""

    limits: BrokerLimits = Field(default_factory=BrokerLimits)
    allowed: list[AllowedSymbol] = Field(default_factory=list, max_length=25)
    buy_plans: list[BuyPlan] = Field(default_factory=list, max_length=10)
    fractional_ok: bool = False
    cooling_off_hours: int = Field(default=24, ge=1, le=168)
    live: LiveSwitch = Field(default_factory=LiveSwitch)
    kill_switch: bool = False
    kill_switch_at: datetime | None = None
    reconciliation: dict[str, Reconciliation] = Field(default_factory=dict)
    version: int = 0
    updated_at: datetime | None = None

    def mode(self, now: datetime) -> Environment:
        return Environment.LIVE if self.live.on else Environment.PAPER

    def recon(self, environment: Environment) -> Reconciliation:
        return self.reconciliation.get(environment.value) or Reconciliation()

    def allowed_symbol(self, symbol: str) -> AllowedSymbol | None:
        return next((a for a in self.allowed if a.symbol == symbol), None)


# -- records ----------------------------------------------------------------------------------------


class BrokerConnection(_Owned):
    """One IBKR connection (paper or live). The secret is Fernet ciphertext and never leaves the API."""

    id: str
    environment: Environment
    gateway_host: str
    secret: str
    hint: str
    status: Literal["active", "revoked"] = "active"
    session: Literal["unknown", "authenticated", "expired", "unreachable"] = "unknown"
    session_message: str | None = None
    session_checked_at: datetime | None = None
    verified_at: datetime | None = None
    trading_type: str | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)
    statement: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class BrokerOrder(_Owned):
    """One order ANUM sent (or is sending) to IBKR. ``id`` is the intent id and IBKR's ``cOID``."""

    id: str
    connection_id: str
    environment: Environment
    symbol: str
    conid: int
    side: Literal["buy", "sell"]
    quantity: Decimal
    limit_price: Decimal
    state: OrderState
    broker_order_id: str | None = None
    filled_quantity: Decimal = Decimal(0)
    average_price: Decimal | None = None
    message: str | None = None
    attempts: int = 0
    submitted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BrokerEvent(_Owned):
    """An audit line and, when ``alert`` is set, an in-app alert. No secrets, no account id."""

    id: str
    environment: Environment | None = None
    kind: str
    severity: Literal["info", "warning", "critical"] = "info"
    message: str
    ref: str | None = None
    alert: bool = False
    created_at: datetime
    read_at: datetime | None = None


# -- confirm code -----------------------------------------------------------------------------------

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def code_hash(code: str, owner: str) -> str:
    return hashlib.sha256(f"anum/ibkr/live-code/v1:{owner}:{code.strip().upper()}".encode()).hexdigest()


def code_matches(code: str, owner: str, stored: str | None) -> bool:
    return bool(stored) and hmac.compare_digest(code_hash(code, owner), stored or "")


# -- checks -----------------------------------------------------------------------------------------


class Check(BaseModel):
    code: str
    ok: bool
    message: str


class MarketState(BaseModel):
    open: bool
    message: str
    closes_at: datetime | None = None


def us_session(now: datetime, buffer_minutes: int) -> MarketState:
    """Is the US regular session open, outside the first and last ``buffer_minutes``?"""
    v = venue("XNYS")
    local = now.astimezone(NEW_YORK)
    day = local.date()
    if not v.holidays_known(day):
        return MarketState(open=False, message="ANUM does not know this year's US market holidays, so it does not trade.")
    if day.weekday() not in v.weekdays or day in v.holidays:
        return MarketState(open=False, message="The US market is closed today.")
    opens = datetime.combine(day, v.opens, NEW_YORK)
    closes = datetime.combine(day, EARLY_CLOSE if day in EARLY_CLOSES else v.closes, NEW_YORK)
    start, end = opens + timedelta(minutes=buffer_minutes), closes - timedelta(minutes=buffer_minutes)
    if not start <= local < end:
        return MarketState(open=False, message=f"Orders are allowed from {start:%H:%M} to {end:%H:%M} New York time on market days.",
                           closes_at=closes)
    return MarketState(open=True, message="The US regular session is open.", closes_at=closes)


def new_york_day(now: datetime) -> date:
    return now.astimezone(NEW_YORK).date()


def check_quantity(quantity: Decimal, fractional_ok: bool) -> str | None:
    if quantity <= 0:
        return "The quantity is above zero."
    if quantity != quantity.quantize(Decimal(1).scaleb(-FRACTION_DIGITS), rounding=ROUND_DOWN):
        return f"Use at most {FRACTION_DIGITS} decimal places."
    if not fractional_ok and quantity != quantity.to_integral_value():
        return "Whole shares only. Fractional orders are off until IBKR confirms they work through the API for your account."
    return None


class OrderFacts(BaseModel):
    """Fresh IBKR facts an order is checked against (read just before the check)."""

    session_ok: bool
    session_message: str = ""
    quote_last: Decimal | None = None
    quote_age_seconds: float | None = None
    quote_is_close: bool = False
    quote_delayed: bool = False  # free delayed data (about 15 minutes old): same band, labelled, never widened
    cash: Decimal | None = None  # settled USD cash at IBKR
    positions_value: Decimal = Decimal(0)  # market value of all positions
    position_quantity: Decimal = Decimal(0)  # this contract
    open_buy_value: Decimal = Decimal(0)  # remaining value of open buy orders
    open_sell_quantity: Decimal = Decimal(0)  # this contract, open sells
    daily_pnl: Decimal | None = None  # IBKR's "dpl"
    orders_today: int = 0
    reconciliation_ok: bool = False
    kill_global: bool = False
    kill_person: bool = False
    kill_broker: bool = False


def order_checks(*, side: str, quantity: Decimal, limit_price: Decimal, symbol: str, conid: int, settings: BrokerSettings,
                 facts: OrderFacts, now: datetime) -> list[Check]:
    """Every hard limit for one order, in a fixed order. All must be ``ok``."""
    limits = settings.limits
    allowed = settings.allowed_symbol(symbol)
    notional = (quantity * limit_price).quantize(Decimal("0.01"))
    checks: list[Check] = []

    def add(code: str, ok: bool, message: str) -> None:
        checks.append(Check(code=code, ok=ok, message=message))

    add("kill_switch", not (facts.kill_global or facts.kill_person or facts.kill_broker),
        "A kill switch is on: no order is sent until you turn it off." if (facts.kill_global or facts.kill_person or facts.kill_broker) else "No kill switch is on.")
    add("session", facts.session_ok, facts.session_message or ("Your IBKR session is active." if facts.session_ok else "Log in to IBKR again."))
    add("reconciliation", facts.reconciliation_ok,
        "IBKR's records match ANUM's." if facts.reconciliation_ok else "IBKR's records and ANUM's do not match yet. Check and explain the difference first.")
    add("symbol", allowed is not None and allowed.conid == conid,
        f"{symbol} is on your allowed list." if allowed is not None and allowed.conid == conid else f"{symbol} is not on your allowed list of US ETFs.")
    add("order_type", side in {"buy", "sell"} and limit_price > 0, "A buy or sell limit order." if side in {"buy", "sell"} and limit_price > 0
        else "Only buy or sell limit orders are allowed.")
    problem = check_quantity(quantity, settings.fractional_ok)
    add("quantity", problem is None, problem or "The quantity is allowed.")
    add("max_order", notional <= limits.max_order,
        f"The order is USD {notional}, within your USD {limits.max_order} per order." if notional <= limits.max_order
        else f"The order is USD {notional}, over your USD {limits.max_order} per order.")
    if side == "buy":
        deployed = facts.positions_value + facts.open_buy_value + notional
        add("max_capital", deployed <= limits.max_capital,
            f"Capital in use would be USD {deployed.quantize(Decimal('0.01'))} of your USD {limits.max_capital}." if deployed <= limits.max_capital
            else f"Capital in use would be USD {deployed.quantize(Decimal('0.01'))}, over your USD {limits.max_capital}.")
        needed = notional + limits.fee_buffer
        enough = facts.cash is not None and facts.cash >= needed
        add("cash", enough, f"Settled cash covers USD {needed} with fees." if enough else
            f"Settled cash at IBKR does not cover USD {needed} with fees. ANUM never borrows (no margin).")
    else:
        free = facts.position_quantity - facts.open_sell_quantity
        add("no_short", quantity <= free, "You hold enough to sell." if quantity <= free else
            "You can only sell what you hold. ANUM never sells short.")
    loss_hit = facts.daily_pnl is not None and facts.daily_pnl <= -limits.max_daily_loss
    add("daily_loss", facts.daily_pnl is not None and not loss_hit,
        "Today's loss limit is not reached." if facts.daily_pnl is not None and not loss_hit else
        (f"Today's loss reached your USD {limits.max_daily_loss} limit: no new orders until tomorrow." if loss_hit
         else "IBKR did not report today's profit or loss, so the loss limit cannot be checked."))
    add("orders_per_day", facts.orders_today < limits.max_orders_per_day,
        f"{facts.orders_today} of {limits.max_orders_per_day} orders today." if facts.orders_today < limits.max_orders_per_day
        else f"You reached {limits.max_orders_per_day} orders today.")
    market = us_session(now, limits.session_buffer_minutes)
    add("market_hours", market.open, market.message)
    band_ok, band_message = price_band(limit_price, facts, limits.price_band_percent)
    add("price_band", band_ok, band_message)
    return checks


def price_band(limit_price: Decimal, facts: OrderFacts, band_percent: Decimal) -> tuple[bool, str]:
    last, age = facts.quote_last, facts.quote_age_seconds
    if last is None or last <= 0 or age is None or facts.quote_is_close:
        return False, "IBKR has no current price for this ETF, so the price band cannot be checked."
    if age > QUOTE_MAX_AGE_SECONDS:
        return False, "IBKR's price is too old to check the price band."
    kind = "delayed price (about 15 minutes old)" if facts.quote_delayed else "last price"
    gap = abs(limit_price - last) / last * 100
    if gap > band_percent:
        return False, f"Your limit is {gap.quantize(Decimal('0.01'))} percent from IBKR's {kind} {last}, outside your {band_percent} percent band."
    return True, f"Your limit is within {band_percent} percent of IBKR's {kind} {last}."


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok]


class ProofFacts(BaseModel):
    paper_orders: int
    first_paper_at: datetime | None
    unexplained_mismatches: int


def paper_proof(facts: ProofFacts, *, min_orders: int, min_days: int, now: datetime) -> list[Check]:
    days = (now - facts.first_paper_at).days if facts.first_paper_at else 0
    return [
        Check(code="paper_orders", ok=facts.paper_orders >= min_orders,
              message=f"{facts.paper_orders} of {min_orders} paper orders reached IBKR."),
        Check(code="paper_days", ok=days >= min_days, message=f"{days} of {min_days} days since the first paper order."),
        Check(code="paper_mismatches", ok=facts.unexplained_mismatches == 0,
              message="No unexplained mismatches." if facts.unexplained_mismatches == 0
              else f"{facts.unexplained_mismatches} mismatches are not explained yet."),
    ]


# -- reconciliation ---------------------------------------------------------------------------------


def reconcile(*, orders: list[BrokerOrder], broker_orders: list[dict[str, Any]], positions: dict[str, Decimal], recon: Reconciliation,
              allowed_conids: set[int], now: datetime) -> Reconciliation:
    """IBKR's orders and positions against ANUM's. Pure: returns the new reconciliation.

    * Every open IBKR order on an allowed ETF must be one ANUM sent (its ``order_ref`` is an ANUM id).
    * Every ANUM order IBKR reports must show the same filled quantity in ANUM after the update.
    * Positions in allowed ETFs must equal the accepted baseline plus every ANUM fill since then.
    """
    mine = {o.id: o for o in orders}
    mismatches: list[dict[str, str]] = []
    for item in broker_orders:
        ref = item.get("order_ref")
        if ref and ref in mine:
            order = mine[ref]
            filled = Decimal(str(item.get("filled_quantity") or 0))
            if filled != order.filled_quantity:
                mismatches.append({"code": "fill_differs", "ref": order.id,
                                   "message": f"IBKR shows {filled} filled for an order ANUM records as {order.filled_quantity}."})
        elif item.get("conid") in allowed_conids and item.get("open"):
            mismatches.append({"code": "unknown_order", "ref": str(item.get("order_id") or ""),
                               "message": "IBKR has an open order on an allowed ETF that ANUM did not send."})
    expected: dict[str, Decimal] = {k: Decimal(v) for k, v in recon.baseline.items()}
    for order in orders:
        already = Decimal(recon.filled_at_baseline.get(order.id, "0"))
        delta = order.filled_quantity - already
        if delta:
            key = str(order.conid)
            expected[key] = expected.get(key, Decimal(0)) + (delta if order.side == "buy" else -delta)
    keys = {k for k in expected if int(k) in allowed_conids} | {k for k in positions if int(k) in allowed_conids}
    for conid in sorted(keys):
        want, have = expected.get(conid, Decimal(0)), positions.get(conid, Decimal(0))
        if want != have:
            mismatches.append({"code": "position_differs", "ref": conid,
                               "message": f"IBKR holds {have} of contract {conid}; ANUM expects {want}."})
    return recon.model_copy(update={"state": "mismatch" if mismatches else "ok", "checked_at": now, "mismatches": mismatches[:50]})


def new_baseline(recon: Reconciliation, positions: dict[str, Decimal], orders: list[BrokerOrder], now: datetime, *, explained: bool) -> Reconciliation:
    """Accept IBKR's current positions as the starting point (on connect, or after the owner explains a difference)."""
    return Reconciliation(state="ok", checked_at=now, mismatches=[], baseline={k: str(v) for k, v in positions.items()}, baseline_at=now,
                          filled_at_baseline={o.id: str(o.filled_quantity) for o in orders if o.filled_quantity},
                          episodes=recon.episodes, explained=recon.explained + (1 if explained else 0))
