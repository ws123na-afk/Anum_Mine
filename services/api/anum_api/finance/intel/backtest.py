"""A small, deterministic daily-bar backtester (docs/finance-trading-intel.md#backtesting).

How a run works, bar by bar:

1. At the **close** of bar ``t`` the strategy sees bars ``0..t`` only (``History``) and may
   return target weights per symbol (long only, each 0 to 1, together at most 1).
2. Orders to reach those weights execute at the **open of bar t+1**: never at a price the
   strategy has already seen, so a signal cannot trade on its own bar (no look-ahead).
3. Each fill pays half the spread and the slippage against it (a buy pays more, a sell gets
   less), a fee (basis points with a minimum), and is capped at a share of that bar's volume
   (``max_volume_pct``). A bar without volume cannot fill unless ``allow_unknown_volume``.
4. Sells go first, then buys within the cash left. No leverage, no short selling.

Money (cash, fills, fees) is ``Decimal``; indicators and metrics are floats. Every result is
SIMULATED: past bars and stated cost assumptions, no statement about future returns.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Any, Protocol

from . import indicators as ind
from .marketdata import Candle

QTY = Decimal("0.00000001")
CENT = Decimal("0.0001")
BPS = Decimal(10000)
MAX_BARS = 20_000
MAX_SYMBOLS = 10


class BacktestError(ValueError):
    pass


@dataclass(frozen=True)
class Costs:
    fee_bps: Decimal = Decimal("10")
    fee_min: Decimal = Decimal("0")
    spread_bps: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("5")
    max_volume_pct: Decimal = Decimal("0.05")
    allow_unknown_volume: bool = False
    rebalance_band: Decimal = Decimal("0.02")  # skip a trade smaller than this share of equity

    def describe(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.__dict__.items()}


class History:
    """What a strategy may see at the close of bar ``t``: bars ``0..t`` and nothing later.

    Indicator series are computed once on the series given to the run and read only at
    index ``t`` or earlier; every indicator is causal (tested), and ``probe_look_ahead``
    re-runs strategies on cut series to prove the signals do not change.
    """

    def __init__(self, data: Mapping[str, Sequence[Candle]]) -> None:
        self._data = data
        self._closes = {s: [float(c.close) for c in bars] for s, bars in data.items()}
        self._bars = {s: [c.bar() for c in bars] for s, bars in data.items()}
        self._cache: dict[tuple[Any, ...], Any] = {}
        self.t = 0
        self.weights: dict[str, float] = {}
        self.symbols = tuple(data)

    @property
    def day(self) -> date:
        return self._data[self.symbols[0]][self.t].day

    def close(self, symbol: str, back: int = 0) -> float | None:
        i = self.t - back
        return self._closes[symbol][i] if 0 <= i <= self.t else None

    def closes(self, symbol: str, window: int) -> list[float]:
        start = max(0, self.t - window + 1)
        return self._closes[symbol][start:self.t + 1]

    def value(self, symbol: str, name: str, *args: Any) -> float | None:
        key = (symbol, name, args)
        if key not in self._cache:
            closes, bars = self._closes[symbol], self._bars[symbol]
            fn: dict[str, Callable[[], Any]] = {
                "sma": lambda: ind.sma(closes, *args), "ema": lambda: ind.ema(closes, *args),
                "rsi": lambda: ind.rsi(closes, *args), "momentum": lambda: ind.momentum(closes, *args),
                "atr": lambda: ind.atr(bars, *args), "adx": lambda: ind.adx(bars, *args),
                "volatility": lambda: ind.volatility(closes, *args),
                "prior_high": lambda: ind.prior_high(closes, *args), "prior_low": lambda: ind.prior_low(closes, *args),
            }
            if name not in fn:
                raise BacktestError(f"Unknown indicator {name!r}.")
            self._cache[key] = fn[name]()
        return self._cache[key][self.t]


class Strategy(Protocol):
    key: str
    version: str

    def targets(self, h: History) -> dict[str, float] | None: ...


@dataclass(frozen=True)
class Trade:
    day: date
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    notional: Decimal
    fee: Decimal
    pnl: Decimal | None = None  # realised on a sale, after fees


@dataclass
class Result:
    days: list[date]
    equity: list[float]
    trades: list[Trade]
    targets: list[dict[str, float] | None]
    skipped: list[tuple[date, str, str]] = field(default_factory=list)  # day, symbol, reason
    start_index: int = 0
    starting_cash: Decimal = Decimal("0")
    costs: Costs = field(default_factory=Costs)
    fees: Decimal = Decimal("0")


def align(data: Mapping[str, Sequence[Candle]]) -> dict[str, list[Candle]]:
    """Keep only the days every symbol has a bar for (no bar is invented to fill a gap)."""
    if not data:
        raise BacktestError("No symbols.")
    if len(data) > MAX_SYMBOLS:
        raise BacktestError(f"At most {MAX_SYMBOLS} symbols per run.")
    common = set.intersection(*({c.day for c in bars} for bars in data.values()))
    out = {s: sorted((c for c in bars if c.day in common), key=lambda c: c.day) for s, bars in data.items()}
    if len(common) > MAX_BARS:
        raise BacktestError(f"At most {MAX_BARS} bars per run.")
    return out


def _round_qty(value: Decimal) -> Decimal:
    return value.quantize(QTY, rounding=ROUND_DOWN) if value > 0 else Decimal("0")


def run(data: Mapping[str, Sequence[Candle]], strategy: Strategy, *, cash: Decimal = Decimal("100000"),
        costs: Costs | None = None, start_index: int = 0, end_index: int | None = None, final_signal: bool = False) -> Result:
    """Run ``strategy`` over aligned bars. Trading starts at ``start_index``; earlier bars only warm up indicators.

    The strategy is not asked on the last bar (there is no next open to fill at) unless
    ``final_signal``: the look-ahead probe needs that signal to compare it with a longer run.
    """
    costs = costs or Costs()
    bars = align(data)
    symbols = list(bars)
    n = len(bars[symbols[0]])
    end = n if end_index is None else min(end_index, n)
    if end - start_index < 2:
        raise BacktestError("Not enough bars to trade: at least two after the warm-up.")
    history = History(bars)
    holdings = {s: Decimal("0") for s in symbols}
    cost_basis = {s: Decimal("0") for s in symbols}
    money = cash
    result = Result(days=[], equity=[], trades=[], targets=[], start_index=start_index, starting_cash=cash, costs=costs)
    pending: dict[str, float] | None = None
    edge = (costs.spread_bps / 2 + costs.slippage_bps) / BPS

    def fee_for(notional: Decimal) -> Decimal:
        return max((notional * costs.fee_bps / BPS).quantize(CENT), costs.fee_min)

    for t in range(start_index, end):
        day = bars[symbols[0]][t].day
        if pending is not None:
            opens = {s: bars[s][t].open for s in symbols}
            equity_open = money + sum(holdings[s] * opens[s] for s in symbols)
            desired = {s: Decimal(str(pending.get(s, 0.0))) * equity_open for s in symbols}
            order = sorted(symbols, key=lambda s: desired[s] - holdings[s] * opens[s])  # sells first
            for s in order:
                gap = desired[s] - holdings[s] * opens[s]
                if equity_open <= 0 or abs(gap) < costs.rebalance_band * equity_open:
                    continue
                volume = bars[s][t].volume
                if volume is None and not costs.allow_unknown_volume:
                    result.skipped.append((day, s, "no_volume"))
                    continue
                cap = volume * costs.max_volume_pct if volume is not None else None
                if gap < 0:
                    price = (opens[s] * (1 - edge)).quantize(CENT)
                    qty = _round_qty(min(holdings[s], -gap / opens[s]))
                    if cap is not None and qty > cap:
                        result.skipped.append((day, s, "volume_cap"))
                        qty = _round_qty(cap)
                    if qty <= 0:
                        continue
                    notional = (qty * price).quantize(CENT)
                    fee = fee_for(notional)
                    avg = cost_basis[s] / holdings[s] if holdings[s] > 0 else Decimal("0")
                    pnl = (notional - fee - avg * qty).quantize(CENT)
                    cost_basis[s] -= avg * qty
                    holdings[s] -= qty
                    if holdings[s] <= 0:
                        holdings[s], cost_basis[s] = Decimal("0"), Decimal("0")
                    money += notional - fee
                    result.fees += fee
                    result.trades.append(Trade(day, s, "sell", qty, price, notional, fee, pnl))
                else:
                    price = (opens[s] * (1 + edge)).quantize(CENT)
                    budget = min(gap, money)
                    qty = _round_qty(budget / (price * (1 + costs.fee_bps / BPS)))
                    if cap is not None and qty > cap:
                        result.skipped.append((day, s, "volume_cap"))
                        qty = _round_qty(cap)
                    if qty <= 0:
                        continue
                    notional = (qty * price).quantize(CENT)
                    fee = fee_for(notional)
                    if notional + fee > money:
                        result.skipped.append((day, s, "cash"))
                        continue
                    holdings[s] += qty
                    cost_basis[s] += notional + fee
                    money -= notional + fee
                    result.fees += fee
                    result.trades.append(Trade(day, s, "buy", qty, price, notional, fee))
            pending = None
        closes = {s: bars[s][t].close for s in symbols}
        equity = money + sum(holdings[s] * closes[s] for s in symbols)
        result.days.append(day)
        result.equity.append(float(equity))
        history.t = t
        history.weights = {s: float(holdings[s] * closes[s] / equity) if equity > 0 else 0.0 for s in symbols}
        wanted = strategy.targets(history) if t < end - 1 or final_signal else None
        if wanted is not None and t < end - 1:
            clean = {s: max(0.0, min(1.0, float(w))) for s, w in wanted.items() if s in holdings and math.isfinite(float(w))}
            total = sum(clean.values())
            if total > 1.0:
                clean = {s: w / total for s, w in clean.items()}
            pending = clean
        result.targets.append(wanted)
    return result


# -- metrics -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Metrics:
    bars: int
    total_return: float
    cagr: float
    volatility: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    turnover: float  # traded value per year, as a multiple of average equity
    trades: int
    closed_trades: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None  # average realised P&L per closed trade, in the run's currency
    exposure: float  # share of bars with money in the market
    fees: float

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def returns(equity: Sequence[float]) -> list[float]:
    return [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1] > 0]


def max_drawdown(equity: Sequence[float]) -> float:
    peak, worst = -math.inf, 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def metrics(result: Result, periods_per_year: int = 252) -> Metrics:
    eq = result.equity
    rets = returns(eq)
    n = len(rets)
    total = eq[-1] / eq[0] - 1.0 if eq and eq[0] > 0 else 0.0
    years = n / periods_per_year if n else 0.0
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1.0 if years > 0 and eq[0] > 0 and eq[-1] > 0 else 0.0
    mean = sum(rets) / n if n else 0.0
    std = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in rets) / n) if n else 0.0
    sharpe = mean / std * math.sqrt(periods_per_year) if std > 0 else None
    sortino = mean / downside * math.sqrt(periods_per_year) if downside > 0 else None
    traded = sum(float(t.notional) for t in result.trades)
    avg_equity = sum(eq) / len(eq) if eq else 0.0
    turnover = traded / avg_equity / years if avg_equity > 0 and years > 0 else 0.0
    closed = [float(t.pnl) for t in result.trades if t.pnl is not None]
    wins, losses = [p for p in closed if p > 0], [p for p in closed if p < 0]
    profit_factor = sum(wins) / -sum(losses) if losses else None
    exposure_bars = sum(1 for tg in _held(result))
    return Metrics(
        bars=len(eq), total_return=total, cagr=cagr, volatility=std * math.sqrt(periods_per_year), sharpe=sharpe,
        sortino=sortino, max_drawdown=max_drawdown(eq), turnover=turnover, trades=len(result.trades),
        closed_trades=len(closed), win_rate=len(wins) / len(closed) if closed else None, profit_factor=profit_factor,
        expectancy=sum(closed) / len(closed) if closed else None, exposure=exposure_bars / len(eq) if eq else 0.0,
        fees=float(result.fees),
    )


def _held(result: Result) -> list[bool]:
    """Whether money was in the market at each close (replayed from the trades)."""
    held: dict[str, Decimal] = {}
    by_day: dict[date, list[Trade]] = {}
    for trade in result.trades:
        by_day.setdefault(trade.day, []).append(trade)
    out: list[bool] = []
    for day in result.days:
        for trade in by_day.get(day, []):
            held[trade.symbol] = held.get(trade.symbol, Decimal("0")) + (trade.quantity if trade.side == "buy" else -trade.quantity)
        if any(q > 0 for q in held.values()):
            out.append(True)
    return out
