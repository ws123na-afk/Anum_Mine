"""SIMULATED P&L and drawdown over time for one paper or shadow portfolio
(docs/finance-trading-intel.md#pl-and-drawdown-over-time).

Pure functions: no store, no clock. The curve is rebuilt from what is stored, never from a guess:

* the portfolio's current run (its epoch), its starting cash and its filled orders, applied in the
  same order and with the same average-cost rules as ``trading.replay``;
* recorded prices and exchange rates, matched by the date they are for: on day D a holding is
  valued at the newest recorded price with ``as_of`` on or before D and no older than the
  portfolio's ``max_price_age_days`` (the same rule as ``trading.price_for``).

A day on which something held has no price that fresh is a gap (``complete`` false, no value),
never a value with that holding left out. Currencies are never mixed or converted: each currency
has its own series. A paper portfolio's drawdown is measured on its value (cash plus priced
holdings); a shadow portfolio has no cash, so its drawdown is measured on its P&L.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import fx
from .models import FxRate
from .money import quantize
from .trading import (
    Book,
    InstrumentKind,
    OrderStatus,
    PaperOrder,
    Portfolio,
    PortfolioMode,
    PriceRecord,
    _add,
    apply_fill,
)

MAX_DAYS = 730
DEFAULT_DAYS = 180


@dataclass(frozen=True)
class CurvePoint:
    day: date
    value: Decimal | None  # paper: cash plus priced holdings; shadow: None (no cash)
    pnl: Decimal | None  # realised plus unrealised, fees and slippage included
    drawdown: Decimal | None  # how far below the running peak, zero or negative
    drawdown_pct: float | None  # paper only, of the running peak value
    complete: bool  # False when something held had no fresh enough price


@dataclass(frozen=True)
class CurrencyCurve:
    currency: str
    points: list[CurvePoint]
    max_drawdown: Decimal | None
    max_drawdown_pct: float | None
    peak_day: date | None  # the peak before the deepest drawdown
    trough_day: date | None
    gaps: int
    unpriced: list[str]


class _PriceIndex:
    """Recorded prices per (symbol, currency), sorted the way ``price_for`` breaks ties."""

    def __init__(self, prices: Iterable[PriceRecord]) -> None:
        grouped: dict[tuple[str, str], list[PriceRecord]] = {}
        for item in prices:
            grouped.setdefault((item.symbol, item.currency), []).append(item)
        self._items = {k: sorted(v, key=lambda p: (p.as_of, p.recorded_at, p.id)) for k, v in grouped.items()}
        self._days = {k: [p.as_of for p in v] for k, v in self._items.items()}

    def at(self, symbol: str, currency: str, day: date, max_age_days: int) -> Decimal | None:
        days = self._days.get((symbol, currency))
        if not days:
            return None
        index = bisect_right(days, day) - 1
        if index < 0:
            return None
        found = self._items[(symbol, currency)][index]
        return found.price if found.as_of >= day - timedelta(days=max_age_days) else None


def _fill_day(order: PaperOrder, tz: ZoneInfo) -> date:
    return (order.filled_at or order.created_at).astimezone(tz).date()


def _run_start(portfolio: Portfolio, tz: ZoneInfo) -> date:
    return (portfolio.reset_at or portfolio.created_at).astimezone(tz).date()


def curve_window(portfolio: Portfolio, orders: Iterable[PaperOrder], today: date, days: int, tz: ZoneInfo) -> tuple[date, date]:
    """From the run's start or first fill (whichever is earlier), at most ``days`` back, to today."""
    days = max(1, min(days, MAX_DAYS))
    filled = [_fill_day(o, tz) for o in orders if o.epoch == portfolio.epoch and o.status == OrderStatus.FILLED]
    start = min([_run_start(portfolio, tz), *filled])
    return max(start, today - timedelta(days=days - 1)), today


def equity_curves(
    portfolio: Portfolio, orders: Iterable[PaperOrder], prices: Iterable[PriceRecord], rates: Iterable[FxRate],
    start: date, end: date, tz: ZoneInfo,
) -> list[CurrencyCurve]:
    """One series per currency, one point per day from ``start`` to ``end`` (both included)."""
    paper = portfolio.mode == PortfolioMode.PAPER
    filled = sorted((o for o in orders if o.epoch == portfolio.epoch and o.status == OrderStatus.FILLED),
                    key=lambda o: (o.filled_at or o.created_at, o.id))
    book = Book()
    currencies: set[str] = {o.currency for o in filled}
    if paper:
        for line in portfolio.starting_cash:
            _add(book.cash, line.currency, line.amount)
            currencies.add(line.currency)
    index, rate_list = _PriceIndex(prices), sorted(rates, key=lambda r: r.as_of)
    rate_days = [r.as_of for r in rate_list]
    age = portfolio.max_price_age_days
    points: dict[str, list[CurvePoint]] = {c: [] for c in currencies}
    unpriced: dict[str, set[str]] = {c: set() for c in currencies}
    position = 0
    day = start
    while day <= end:
        while position < len(filled) and _fill_day(filled[position], tz) <= day:
            apply_fill(book, filled[position], with_cash=paper)
            position += 1
        market: dict[str, Decimal] = {}
        unrealised: dict[str, Decimal] = {}
        missing: set[str] = set()
        for held in book.positions.values():
            if held.quantity <= 0:
                continue
            if held.kind == InstrumentKind.FX:
                known = rate_list[: bisect_right(rate_days, day)]
                path = fx.find_rate(known, held.symbol, held.currency, day, age) if held.symbol != held.currency else None
                price = path.rate if path is not None else None
            else:
                price = index.at(held.symbol, held.currency, day, age)
            if price is None:
                missing.add(held.currency)
                unpriced.setdefault(held.currency, set()).add(held.symbol)
                continue
            worth = quantize(held.quantity * price, held.currency)
            _add(market, held.currency, worth)
            _add(unrealised, held.currency, worth - held.cost)
        for currency in currencies:
            if currency in missing:
                points[currency].append(CurvePoint(day, None, None, None, None, False))
                continue
            pnl = quantize(book.realised.get(currency, Decimal(0)) + unrealised.get(currency, Decimal(0)), currency)
            value = quantize(book.cash.get(currency, Decimal(0)) + market.get(currency, Decimal(0)), currency) if paper else None
            points[currency].append(CurvePoint(day, value, pnl, None, None, True))
        day += timedelta(days=1)
    return [_with_drawdown(c, points[c], paper, sorted(unpriced.get(c, set()))) for c in sorted(currencies)]


def _with_drawdown(currency: str, raw: list[CurvePoint], paper: bool, unpriced: list[str]) -> CurrencyCurve:
    out: list[CurvePoint] = []
    peak: Decimal | None = None
    peak_day: date | None = None
    worst: Decimal | None = None
    worst_pct: float | None = None
    worst_peak: date | None = None
    worst_day: date | None = None
    for point in raw:
        basis = point.value if paper else point.pnl
        if basis is None:
            out.append(point)
            continue
        if peak is None or basis > peak:
            peak, peak_day = basis, point.day
        drawdown = basis - peak
        pct = float(drawdown / peak) if paper and peak > 0 else None
        if worst is None or drawdown < worst:
            worst, worst_pct, worst_peak, worst_day = drawdown, pct, peak_day, point.day
        out.append(CurvePoint(point.day, point.value, point.pnl, drawdown, pct, True))
    deep = worst is not None and worst < 0
    return CurrencyCurve(
        currency=currency, points=out, max_drawdown=worst, max_drawdown_pct=worst_pct if deep else (0.0 if paper and worst is not None else None),
        peak_day=worst_peak if deep else None, trough_day=worst_day if deep else None, gaps=sum(1 for p in out if not p.complete), unpriced=unpriced,
    )
