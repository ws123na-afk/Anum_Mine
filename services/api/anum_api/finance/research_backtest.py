"""Backtests of simple long-term rules on past monthly prices (docs/finance-research.md#backtests).

Pure and deterministic: the same prices, dividends and settings always give the same result, to the
cent. Nothing here fetches data or stores anything; ``research_service`` passes the gathered price
history in. Every trade happens at a month's last close, which a real limit order may not get.

Rules:

* ``monthly``: put in a fixed amount each month (dollar-cost averaging) and buy whole shares.
* ``buy_hold``: put the whole amount in at the start and keep it.
* ``rebalance``: two funds, monthly money goes to the one below its target share, and every few
  months the mix is set back to the target (this sells some of the fund that grew).
* ``dip``: the monthly buying, plus one extra buy when the price is a set percent below its
  highest close so far (once per fall; armed again at a new high).

Rules the owner set apply: **whole shares only** (IBKR's API has no confirmed fractional orders), at
most the per-order limit per order, never more money in than the total limit, an IBKR-like
commission (a configurable estimate: per share, a minimum and a cap as a percent of the order),
and dividends, when the data has them, paid in cash with the US withholding tax taken off
(30 percent for a Saudi resident: there is no US income tax treaty with Saudi Arabia).

Results are reported honestly: final value, money put in, total return, the worst fall
(time-weighted, so new money does not hide it), the months the account was worth less than the
money put in, and the same money simply held in the first fund from the start.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .research_data import PricePoint, money, pct

Rule = Literal["monthly", "buy_hold", "rebalance", "dip"]
RULES: tuple[Rule, ...] = ("monthly", "buy_hold", "rebalance", "dip")
WARNING = ("Past results do not predict the future. This replays a simple rule on past monthly prices; real prices, fills, "
           "fees and taxes differ. It is not financial advice and promises no return.")
MIN_MONTHS = 12
CENT = Decimal("0.01")


class Commission(BaseModel):
    """An estimate of IBKR's fixed pricing for US stocks and ETFs; the owner can change it."""

    model_config = ConfigDict(extra="forbid")

    per_share: Decimal = Field(default=Decimal("0.005"), ge=0, le=Decimal("0.1"))
    minimum: Decimal = Field(default=Decimal("1.00"), ge=0, le=Decimal(20))
    max_percent: Decimal = Field(default=Decimal(1), ge=0, le=Decimal(5))  # of the order's value

    def fee(self, shares: int, price: Decimal) -> Decimal:
        if shares <= 0:
            return Decimal(0)
        value = price * shares
        raw = max(self.per_share * shares, self.minimum)
        cap = value * self.max_percent / 100
        return (min(raw, cap) if self.max_percent > 0 else raw).quantize(CENT, rounding=ROUND_HALF_UP)


class BacktestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: Rule
    symbols: list[str] = Field(min_length=1, max_length=2)
    monthly_amount: Decimal = Field(default=Decimal(10), gt=0, le=Decimal(10_000), decimal_places=2)
    total_limit: Decimal = Field(default=Decimal(50), gt=0, le=Decimal(1_000_000), decimal_places=2)
    per_order_limit: Decimal = Field(default=Decimal(10), gt=0, le=Decimal(100_000), decimal_places=2)
    years: int = Field(default=10, ge=1, le=10)
    weight_percent: int = Field(default=60, ge=10, le=90)  # rebalance: the first fund's target share
    rebalance_months: Literal[3, 6, 12] = 12
    dip_percent: int = Field(default=10, ge=2, le=60)
    dip_amount: Decimal = Field(default=Decimal(10), gt=0, le=Decimal(10_000), decimal_places=2)
    withholding_percent: Decimal = Field(default=Decimal(30), ge=0, le=Decimal(50))
    commission: Commission = Field(default_factory=Commission)

    @model_validator(mode="after")
    def _shape(self) -> BacktestInput:
        self.symbols = [s.strip().upper() for s in self.symbols]
        if self.rule == "rebalance" and (len(self.symbols) != 2 or self.symbols[0] == self.symbols[1]):
            raise ValueError("A rebalance needs two different funds.")
        if self.rule != "rebalance" and len(self.symbols) != 1:
            raise ValueError("This rule uses one fund.")
        return self


@dataclass
class _Book:
    cash: Decimal = Decimal(0)
    put_in: Decimal = Decimal(0)
    shares: dict[str, int] = field(default_factory=dict)
    fees: Decimal = Decimal(0)
    dividends_net: Decimal = Decimal(0)
    withheld: Decimal = Decimal(0)
    orders: int = 0
    skipped: int = 0

    def value(self, prices: dict[str, Decimal]) -> Decimal:
        return self.cash + sum((prices[s] * n for s, n in self.shares.items()), Decimal(0))


def _deposit(book: _Book, amount: Decimal, limit: Decimal) -> Decimal:
    room = max(Decimal(0), limit - book.put_in)
    added = min(amount, room)
    book.cash += added
    book.put_in += added
    return added


def _buy(book: _Book, symbol: str, price: Decimal, budget: Decimal, per_order: Decimal, fees: Commission) -> int:
    """Buy as many whole shares as ``budget`` (at most the per-order limit and the cash) pays for, fee included."""
    cap = min(budget, per_order, book.cash)
    shares = int((cap / price).to_integral_value(rounding=ROUND_DOWN)) if cap > 0 else 0
    while shares > 0 and price * shares + fees.fee(shares, price) > cap:
        shares -= 1
    if shares <= 0:
        return 0
    cost = price * shares + fees.fee(shares, price)
    book.cash -= cost
    book.fees += fees.fee(shares, price)
    book.shares[symbol] = book.shares.get(symbol, 0) + shares
    book.orders += 1
    return shares


def _sell(book: _Book, symbol: str, price: Decimal, shares: int, fees: Commission) -> int:
    shares = min(shares, book.shares.get(symbol, 0))
    if shares <= 0:
        return 0
    fee = fees.fee(shares, price)
    book.cash += price * shares - fee
    book.fees += fee
    book.shares[symbol] -= shares
    book.orders += 1
    return shares


def align(prices: dict[str, Sequence[PricePoint]], years: int) -> list[tuple[date, dict[str, Decimal]]]:
    """Months every fund has a close for, the newest ``years`` years (plus the start month)."""
    by_symbol = {s: {(p.day.year, p.day.month): p for p in pts} for s, pts in prices.items()}
    common = sorted(set.intersection(*(set(m) for m in by_symbol.values()))) if by_symbol else []
    common = common[-(years * 12 + 1):]
    return [(max(by_symbol[s][m].day for s in by_symbol), {s: by_symbol[s][m].close for s in by_symbol}) for m in common]


def _dividends_between(dividends: dict[str, Sequence[tuple[date, Decimal]]], symbol: str, after: date, until: date) -> Decimal:
    return sum((amount for day, amount in dividends.get(symbol, ()) if after < day <= until), Decimal(0))


def simulate(body: BacktestInput, months: list[tuple[date, dict[str, Decimal]]],
             dividends: dict[str, Sequence[tuple[date, Decimal]]] | None = None) -> dict[str, Any]:
    """Replay one rule. ``months``: aligned month-end closes (``align``). Deterministic."""
    dividends = dividends or {}
    fees, book = body.commission, _Book()
    first = body.symbols[0]
    timeline: list[dict[str, str]] = []
    index, index_peak, worst = Decimal(1), Decimal(1), Decimal(0)
    worst_at: tuple[str | None, str | None] = (None, None)
    peak_date = months[0][0].isoformat() if months else None
    previous_value: Decimal | None = None
    underwater = 0
    high: dict[str, Decimal] = {}
    armed = True
    keep = Decimal(1) - body.withholding_percent / 100
    for step, (day, price) in enumerate(months):
        # Dividends paid since last month, on the shares held before this month's trades.
        if step > 0:
            before = months[step - 1][0]
            for symbol, held in book.shares.items():
                per_share = _dividends_between(dividends, symbol, before, day)
                if per_share and held:
                    gross = (per_share * held).quantize(CENT, rounding=ROUND_HALF_UP)
                    net = (gross * keep).quantize(CENT, rounding=ROUND_HALF_UP)
                    book.cash += net
                    book.dividends_net += net
                    book.withheld += gross - net
        flow = Decimal(0)
        if body.rule == "buy_hold":
            if step == 0:
                flow = _deposit(book, body.total_limit, body.total_limit)
                while book.cash > 0 and _buy(book, first, price[first], book.cash, body.per_order_limit, fees):
                    pass
        else:
            flow = _deposit(book, body.monthly_amount, body.total_limit)
            if body.rule == "rebalance":
                second = body.symbols[1]
                total = book.value(price)
                target_first = total * body.weight_percent / 100
                held_first = price[first] * book.shares.get(first, 0)
                if step > 0 and step % body.rebalance_months == 0:
                    if held_first > target_first:
                        _sell(book, first, price[first], int(min(held_first - target_first, body.per_order_limit) / price[first]), fees)
                    else:
                        held_second = price[second] * book.shares.get(second, 0)
                        excess = held_second - (total - target_first)
                        if excess > 0:
                            _sell(book, second, price[second], int(min(excess, body.per_order_limit) / price[second]), fees)
                pick = first if price[first] * book.shares.get(first, 0) < target_first else second
                if book.cash > 0 and not _buy(book, pick, price[pick], book.cash, body.per_order_limit, fees):
                    other = second if pick == first else first
                    if not _buy(book, other, price[other], book.cash, body.per_order_limit, fees) and flow > 0:
                        book.skipped += 1
            else:
                bought = _buy(book, first, price[first], book.cash, body.per_order_limit, fees) if book.cash > 0 else 0
                if not bought and flow > 0:
                    book.skipped += 1
                if body.rule == "dip":
                    high[first] = max(high.get(first, price[first]), price[first])
                    if price[first] >= high[first]:
                        armed = True
                    elif armed and price[first] <= high[first] * (1 - Decimal(body.dip_percent) / 100):
                        extra = _deposit(book, body.dip_amount, body.total_limit)
                        if extra > 0:
                            flow += extra
                            armed = False
                            if not _buy(book, first, price[first], book.cash, body.per_order_limit, fees):
                                book.skipped += 1
        value = book.value(price)
        if previous_value is not None and previous_value > 0:
            index *= (value - flow) / previous_value
        if index >= index_peak:
            index_peak, peak_date = index, day.isoformat()
        elif index_peak > 0 and index / index_peak - 1 < worst:
            worst = index / index_peak - 1
            worst_at = (peak_date, day.isoformat())
        previous_value = value
        if book.put_in > 0 and value < book.put_in:
            underwater += 1
        timeline.append({"month": day.strftime("%Y-%m"), "value": money(value), "put_in": money(book.put_in)})
    final_prices = months[-1][1] if months else {}
    final = book.value(final_prices) if months else Decimal(0)
    return {
        "final_value": money(final), "put_in": money(book.put_in), "cash_left": money(book.cash),
        "gain": money(final - book.put_in),
        "total_return_percent": pct(final / book.put_in - 1) if book.put_in > 0 else None,
        "worst_fall_percent": pct(worst), "worst_fall_from": worst_at[0], "worst_fall_to": worst_at[1],
        "months": len(months), "months_below_put_in": underwater,
        "fees": money(book.fees), "dividends_net": money(book.dividends_net), "tax_withheld": money(book.withheld),
        "orders": book.orders, "skipped_buys": book.skipped, "shares": dict(sorted(book.shares.items())),
        "timeline": timeline,
    }


def run(body: BacktestInput, prices: dict[str, Sequence[PricePoint]], dividends: dict[str, Sequence[tuple[date, Decimal]]] | None,
        dividends_included: bool) -> dict[str, Any]:
    """The rule, and the same money simply held in the first fund from the start."""
    months = align(prices, body.years)
    if len(months) < MIN_MONTHS:
        return {"ok": False, "reason": f"Only {len(months)} months of prices the funds share; a backtest needs at least {MIN_MONTHS}.",
                "warning": WARNING}
    result = simulate(body, months, dividends)
    hold_body = body.model_copy(update={"rule": "buy_hold", "symbols": [body.symbols[0]], "total_limit": Decimal(result["put_in"]) or body.total_limit})
    hold = simulate(hold_body, months, dividends) if Decimal(result["put_in"]) > 0 else None
    return {
        "ok": True, "rule": body.rule, "symbols": body.symbols, "from": months[0][0].isoformat(), "to": months[-1][0].isoformat(),
        "result": result, "simply_holding": None if hold is None else {k: hold[k] for k in ("final_value", "put_in", "total_return_percent", "worst_fall_percent", "months_below_put_in", "fees")},
        "dividends_included": dividends_included,
        "assumptions": {
            "whole_shares_only": True, "price": "Each trade at the month's last close.",
            "commission": {k: str(v) for k, v in body.commission.model_dump().items()},
            "withholding_percent": str(body.withholding_percent), "per_order_limit": str(body.per_order_limit), "total_limit": str(body.total_limit),
        },
        "warning": WARNING,
    }
