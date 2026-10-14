"""Paper and shadow trading, stage 6 (docs/finance.md#paper-and-shadow-trading). SIMULATED.

Nothing here buys, sells, pays or sends anything, and no real account is touched. A paper
portfolio holds simulated cash per currency; a shadow portfolio holds no cash and records
what an order would have done. Both fill only against prices the person has **recorded**:

* a **security** (a symbol such as ``AAPL`` priced in a currency) uses the newest price in
  ``finance_prices`` (typed by the person, source ``manual``) within the portfolio's age
  limit;
* an **FX** instrument (a currency code such as ``USD``) uses the newest recorded exchange
  rate from stage 5 (``finance_fx_rates``: the ECB reference rates or a typed rate), direct,
  inverse or across a shared base, through ``fx.find_rate``.

ANUM never invents a price. With no recorded price a market order is refused with the
reason, and a limit order waits until a price that meets its limit is recorded.

The fee and slippage model is the portfolio's own, shown with every result: slippage moves
the fill price against the order (a buy pays more, a sell gets less) by ``slippage_bps``
basis points, and the fee is ``fee_bps`` of the order's value with a ``fee_min`` floor, in
the order's currency. Results are a simulation with those assumptions, never a promise of
what a real order would do.

Everything below is pure and deterministic: the same orders and prices always give the
same book. Positions and cash are replayed from the filled orders of the current run
(``epoch``), so a reset starts a new run and keeps the earlier history.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from . import fx
from .models import FxRate, _Owned
from .money import quantize

SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")
PRICE_PLACES = Decimal("0.0000000001")  # numeric(24, 10)
QTY_PLACES = Decimal("0.00000001")  # numeric(28, 8)
MAX_QTY = Decimal("1000000000")
MIN_PRICE = Decimal("0.0000000001")
MAX_PRICE = Decimal("100000000")
BPS = Decimal(10000)
SIMULATED_NOTE = (
    "SIMULATED. Paper and shadow results use prices you recorded and the fee and slippage assumptions shown. "
    "They are not real trades and say nothing certain about what a real order would earn or lose."
)


class InstrumentKind(StrEnum):
    SECURITY = "security"
    FX = "fx"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class PortfolioMode(StrEnum):
    PAPER = "paper"  # simulated cash, positions and P&L
    SHADOW = "shadow"  # no cash: what an order would have done, compared over time


class OrderStatus(StrEnum):
    WAITING = "waiting"  # a limit order whose limit no recorded price has met yet
    FILLED = "filled"
    REFUSED = "refused"  # with a reason: no price, not enough simulated cash or units
    CANCELLED = "cancelled"


class TradingError(ValueError):
    """An instrument, quantity or price ANUM refuses (the message is safe to show)."""


def normalize_symbol(value: str) -> str:
    symbol = (value or "").strip().upper()
    if not SYMBOL.match(symbol):
        raise TradingError("A symbol is 1 to 20 letters, digits, dots or hyphens, such as AAPL or BRK.B.")
    return symbol


def check_quantity(value: object) -> Decimal:
    try:
        quantity = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001 - any unreadable value is the same answer
        raise TradingError("A quantity is a number above zero, for example 10 or 0.5.") from None
    if not quantity.is_finite() or quantity <= 0 or quantity >= MAX_QTY:
        raise TradingError("A quantity is a number above zero and below 1,000,000,000.")
    if quantity != quantity.quantize(QTY_PLACES):
        raise TradingError("A quantity has at most 8 decimals.")
    return quantity.quantize(QTY_PLACES)


def check_price(value: object) -> Decimal:
    try:
        price = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001
        raise TradingError("A price is a number above zero, for example 187.25.") from None
    if not price.is_finite() or price < MIN_PRICE or price > MAX_PRICE:
        raise TradingError("A price must be above zero and below 100,000,000.")
    return price.quantize(PRICE_PLACES)


# -- records --------------------------------------------------------------------------------------


class PriceRecord(_Owned):
    """One recorded price: 1 ``symbol`` cost ``price`` ``currency`` on ``as_of``, from ``source``."""

    id: str
    symbol: str
    currency: str
    price: Decimal
    as_of: date
    source: str = "manual"
    connection_id: str | None = None
    recorded_at: datetime


class CashLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal


class Portfolio(_Owned):
    id: str
    name: str
    mode: PortfolioMode
    starting_cash: list[CashLine] = Field(default_factory=list)
    fee_bps: Decimal = Decimal("10")  # 0.10 percent of the order's value
    fee_min: Decimal = Decimal(0)  # in the order's currency
    slippage_bps: Decimal = Decimal("5")  # 0.05 percent against the order
    max_price_age_days: int = Field(default=3, ge=1, le=30)
    epoch: int = 1
    created_at: datetime
    updated_at: datetime
    reset_at: datetime | None = None


class PaperOrder(_Owned):
    """A simulated order and what it did. Made only by the paper or shadow executor."""

    id: str
    portfolio_id: str
    intent_id: str
    epoch: int
    mode: PortfolioMode
    instrument_kind: InstrumentKind
    symbol: str
    currency: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    status: OrderStatus
    reason_code: str | None = None
    reason: str | None = None
    requested_by: str = "person"
    market_price: Decimal | None = None  # the recorded price used
    fill_price: Decimal | None = None  # after slippage
    price_as_of: date | None = None
    price_source: str | None = None
    notional: Decimal | None = None
    fee: Decimal | None = None
    slippage: Decimal | None = None
    guardian_outcome: str | None = None
    guardian_codes: list[str] = Field(default_factory=list)
    created_at: datetime
    filled_at: datetime | None = None
    updated_at: datetime


# -- prices ---------------------------------------------------------------------------------------


class PricePoint(BaseModel):
    price: Decimal
    as_of: date
    source: str
    via: str | None = None


def price_for(
    kind: InstrumentKind, symbol: str, currency: str, prices: Iterable[PriceRecord], rates: Iterable[FxRate],
    today: date, max_age_days: int,
) -> PricePoint | None:
    """The newest recorded price of one unit of ``symbol`` in ``currency``, or ``None``. Never invented."""
    oldest = today - timedelta(days=max_age_days)
    if kind == InstrumentKind.FX:
        if symbol == currency:
            return None
        path = fx.find_rate(list(rates), symbol, currency, today, max_age_days)
        if path is None or path.as_of > today:
            return None
        return PricePoint(price=path.rate, as_of=path.as_of, source=path.source, via=path.via)
    best: PriceRecord | None = None
    for item in prices:
        if item.symbol != symbol or item.currency != currency or item.as_of < oldest or item.as_of > today:
            continue
        if best is None or (item.as_of, item.recorded_at, item.id) > (best.as_of, best.recorded_at, best.id):
            best = item
    if best is None:
        return None
    return PricePoint(price=best.price, as_of=best.as_of, source=best.source)


# -- fills ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeeModel:
    fee_bps: Decimal
    fee_min: Decimal
    slippage_bps: Decimal

    @classmethod
    def of(cls, portfolio: Portfolio) -> FeeModel:
        return cls(portfolio.fee_bps, portfolio.fee_min, portfolio.slippage_bps)


@dataclass(frozen=True)
class Fill:
    market_price: Decimal
    fill_price: Decimal
    notional: Decimal  # quantity x fill price, rounded once to the currency
    fee: Decimal
    slippage: Decimal  # what slippage cost, rounded once
    as_of: date
    source: str


def adjusted_price(price: Decimal, side: Side, slippage_bps: Decimal) -> Decimal:
    """The fill price after slippage: against the order (a buy pays more, a sell gets less)."""
    factor = slippage_bps / BPS
    moved = price * (Decimal(1) + factor) if side == Side.BUY else price * (Decimal(1) - factor)
    return moved.quantize(PRICE_PLACES)


def fill_at(point: PricePoint, side: Side, quantity: Decimal, currency: str, model: FeeModel) -> Fill:
    price = adjusted_price(point.price, side, model.slippage_bps)
    notional = quantize(quantity * price, currency)
    fee = max(quantize(notional * model.fee_bps / BPS, currency), quantize(model.fee_min, currency))
    slippage = quantize(quantity * abs(price - point.price), currency)
    return Fill(market_price=point.price, fill_price=price, notional=notional, fee=fee, slippage=slippage, as_of=point.as_of, source=point.source)


def limit_met(fill: Fill, side: Side, limit: Decimal | None) -> bool:
    if limit is None:
        return True
    return fill.fill_price <= limit if side == Side.BUY else fill.fill_price >= limit


# -- the book -------------------------------------------------------------------------------------


@dataclass
class Position:
    kind: InstrumentKind
    symbol: str
    currency: str
    quantity: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)  # what the units still held cost, fees included


@dataclass
class Book:
    cash: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[tuple[str, str, str], Position] = field(default_factory=dict)
    realised: dict[str, Decimal] = field(default_factory=dict)
    fees: dict[str, Decimal] = field(default_factory=dict)
    slippage: dict[str, Decimal] = field(default_factory=dict)

    def position(self, kind: InstrumentKind, symbol: str, currency: str) -> Position:
        key = (kind.value, symbol, currency)
        if key not in self.positions:
            self.positions[key] = Position(kind=kind, symbol=symbol, currency=currency)
        return self.positions[key]

    def held(self, kind: InstrumentKind, symbol: str, currency: str) -> Decimal:
        found = self.positions.get((kind.value, symbol, currency))
        return found.quantity if found else Decimal(0)


def _add(table: dict[str, Decimal], currency: str, amount: Decimal) -> None:
    table[currency] = table.get(currency, Decimal(0)) + amount


def apply_fill(book: Book, order: PaperOrder, *, with_cash: bool) -> None:
    """One filled order into the book (average cost). Amounts were rounded when filled."""
    if order.status != OrderStatus.FILLED or order.notional is None or order.fee is None:
        return
    position = book.position(order.instrument_kind, order.symbol, order.currency)
    _add(book.fees, order.currency, order.fee)
    _add(book.slippage, order.currency, order.slippage or Decimal(0))
    if order.side == Side.BUY:
        position.quantity += order.quantity
        position.cost += order.notional + order.fee
        if with_cash:
            _add(book.cash, order.currency, -(order.notional + order.fee))
        return
    held = position.quantity
    if held <= 0:
        return
    sold = min(order.quantity, held)
    removed = position.cost if sold == held else quantize(position.cost * sold / held, order.currency)
    proceeds = order.notional - order.fee
    _add(book.realised, order.currency, proceeds - removed)
    position.quantity -= sold
    position.cost -= removed
    if position.quantity == 0:
        position.cost = Decimal(0)
    if with_cash:
        _add(book.cash, order.currency, proceeds)


def replay(portfolio: Portfolio, orders: Iterable[PaperOrder]) -> Book:
    """The current run's book: starting cash plus every filled order, oldest first."""
    book = Book()
    paper = portfolio.mode == PortfolioMode.PAPER
    if paper:
        for line in portfolio.starting_cash:
            _add(book.cash, line.currency, line.amount)
    filled = sorted((o for o in orders if o.epoch == portfolio.epoch and o.status == OrderStatus.FILLED),
                    key=lambda o: (o.filled_at or o.created_at, o.id))
    for order in filled:
        apply_fill(book, order, with_cash=paper)
    return book


class Refusal(BaseModel):
    code: str
    message: str


def check_paper_fill(book: Book, order: PaperOrder, fill: Fill) -> Refusal | None:
    """Can a paper portfolio afford this fill? Shadow portfolios have no cash and are never refused here."""
    if order.side == Side.BUY:
        cash = book.cash.get(order.currency, Decimal(0))
        need = fill.notional + fill.fee
        if need > cash:
            return Refusal(code="insufficient_cash", message=f"Not enough simulated {order.currency} cash: this needs {order.currency} {need}, you have {order.currency} {cash}.")
        return None
    held = book.held(order.instrument_kind, order.symbol, order.currency)
    if order.quantity > held:
        return Refusal(code="insufficient_units", message=f"You hold {held} {order.symbol} in this portfolio: you cannot sell more (no short selling).")
    return None


# -- valuation ------------------------------------------------------------------------------------


class PositionView(BaseModel):
    instrument_kind: InstrumentKind
    symbol: str
    currency: str
    quantity: Decimal
    cost: Decimal
    average_price: Decimal | None
    price: Decimal | None
    price_as_of: date | None
    price_source: str | None
    market_value: Decimal | None
    unrealised: Decimal | None


class MoneyRow(BaseModel):
    currency: str
    amount: Decimal


def _rows(table: dict[str, Decimal]) -> list[MoneyRow]:
    return [MoneyRow(currency=c, amount=quantize(v, c)) for c, v in sorted(table.items())]


class Valuation(BaseModel):
    cash: list[MoneyRow]
    positions: list[PositionView]
    realised: list[MoneyRow]
    unrealised: list[MoneyRow]
    fees: list[MoneyRow]
    slippage: list[MoneyRow]
    unpriced: list[str]  # symbols held with no recent recorded price: not valued


def value_book(book: Book, prices: list[PriceRecord], rates: list[FxRate], today: date, max_age_days: int) -> Valuation:
    views: list[PositionView] = []
    unrealised: dict[str, Decimal] = {}
    unpriced: list[str] = []
    for key in sorted(book.positions):
        position = book.positions[key]
        if position.quantity <= 0:
            continue
        point = price_for(position.kind, position.symbol, position.currency, prices, rates, today, max_age_days)
        market = quantize(position.quantity * point.price, position.currency) if point else None
        gain = market - position.cost if market is not None else None
        if gain is not None:
            _add(unrealised, position.currency, gain)
        else:
            unpriced.append(position.symbol)
        views.append(PositionView(
            instrument_kind=position.kind, symbol=position.symbol, currency=position.currency, quantity=position.quantity,
            cost=quantize(position.cost, position.currency),
            average_price=(position.cost / position.quantity).quantize(PRICE_PLACES) if position.quantity else None,
            price=point.price if point else None, price_as_of=point.as_of if point else None, price_source=point.source if point else None,
            market_value=market, unrealised=quantize(gain, position.currency) if gain is not None else None,
        ))
    return Valuation(
        cash=_rows(book.cash), positions=views, realised=_rows(book.realised), unrealised=_rows(unrealised),
        fees=_rows(book.fees), slippage=_rows(book.slippage), unpriced=sorted(set(unpriced)),
    )


class ShadowEntry(BaseModel):
    """One shadow order compared with the newest recorded price: what it would have done so far."""

    order_id: str
    symbol: str
    currency: str
    side: Side
    quantity: Decimal
    fill_price: Decimal
    filled_at: datetime
    fee: Decimal
    price: Decimal | None
    price_as_of: date | None
    result: Decimal | None  # a buy gains when the price rose, a sell when it fell; fees taken off
    days: int


def compare_shadow(orders: Iterable[PaperOrder], prices: list[PriceRecord], rates: list[FxRate], today: date, max_age_days: int) -> tuple[list[ShadowEntry], list[MoneyRow]]:
    entries: list[ShadowEntry] = []
    totals: dict[str, Decimal] = {}
    for order in sorted((o for o in orders if o.status == OrderStatus.FILLED and o.fill_price is not None),
                        key=lambda o: (o.filled_at or o.created_at, o.id), reverse=True):
        point = price_for(order.instrument_kind, order.symbol, order.currency, prices, rates, today, max_age_days)
        result: Decimal | None = None
        if point is not None:
            move = point.price - order.fill_price if order.side == Side.BUY else order.fill_price - point.price
            result = quantize(order.quantity * move, order.currency) - (order.fee or Decimal(0))
            _add(totals, order.currency, result)
        filled = order.filled_at or order.created_at
        entries.append(ShadowEntry(
            order_id=order.id, symbol=order.symbol, currency=order.currency, side=order.side, quantity=order.quantity,
            fill_price=order.fill_price, filled_at=filled, fee=order.fee or Decimal(0), price=point.price if point else None,
            price_as_of=point.as_of if point else None, result=result, days=max((today - filled.date()).days, 0),
        ))
    return entries, _rows(totals)
