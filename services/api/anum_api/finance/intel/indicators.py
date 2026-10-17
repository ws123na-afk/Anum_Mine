"""Deterministic technical indicators (docs/finance-trading-intel.md#technical-analysis).

Pure functions over a list of bars, oldest first. Each returns a list as long as its input
with ``None`` where there is not enough history yet ("warm-up"): a value is never invented
for a bar that cannot have one. The value at index ``t`` uses bars ``0..t`` only, so no
indicator looks ahead (``tests/test_finance_intel_indicators.py`` checks that cutting the
future off never changes a past value).

Indicators are research figures, not money: they are computed in binary floating point
from the recorded closes. Money (fills, cash, fees) stays ``Decimal`` in ``backtest.py``.

Definitions follow the common textbook forms: Wilder's smoothing for RSI, ATR and ADX
(J. Welles Wilder, 1978), MACD 12/26/9 on EMAs (Gerald Appel), annualised volatility as the
sample standard deviation of log returns times the square root of the periods per year.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

Series = list[float | None]


@dataclass(frozen=True)
class Bar:
    """One bar's prices as floats, for the indicators. ``volume`` may be unknown."""

    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


def _check_period(period: int) -> None:
    if not isinstance(period, int) or period < 1:
        raise ValueError("A period is a whole number of bars, 1 or more.")


def sma(values: Sequence[float], period: int) -> Series:
    _check_period(period)
    out: Series = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """Exponential moving average seeded with the simple average of the first ``period`` values."""
    _check_period(period)
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = values[i] * k + current * (1 - k)
        out[i] = current
    return out


def _wilder(values: Sequence[float], period: int, start: int) -> Series:
    """Wilder's smoothing of ``values`` from index ``start`` (seeded with a simple average)."""
    out: Series = [None] * len(values)
    if len(values) - start < period:
        return out
    current = sum(values[start:start + period]) / period
    out[start + period - 1] = current
    for i in range(start + period, len(values)):
        current = (current * (period - 1) + values[i]) / period
        out[i] = current
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Series:
    """Relative strength index, 0 to 100. Flat prices (no gain and no loss) give 50."""
    _check_period(period)
    n = len(closes)
    out: Series = [None] * n
    if n <= period:
        return out
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]
    avg_gain = _wilder(gains, period, 1)
    avg_loss = _wilder(losses, period, 1)
    for i in range(n):
        g, loss = avg_gain[i], avg_loss[i]
        if g is None or loss is None:
            continue
        if g == 0 and loss == 0:
            out[i] = 50.0
        elif loss == 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + g / loss)
    return out


@dataclass(frozen=True)
class Macd:
    macd: Series
    signal: Series
    histogram: Series


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Macd:
    if fast >= slow:
        raise ValueError("The fast period must be shorter than the slow one.")
    fast_ema, slow_ema = ema(closes, fast), ema(closes, slow)
    line: Series = [f - s if f is not None and s is not None else None for f, s in zip(fast_ema, slow_ema)]
    start = next((i for i, v in enumerate(line) if v is not None), None)
    sig: Series = [None] * len(closes)
    if start is not None:
        tail = ema([v for v in line[start:] if v is not None], signal)
        for offset, value in enumerate(tail):
            sig[start + offset] = value
    hist: Series = [m - s if m is not None and s is not None else None for m, s in zip(line, sig)]
    return Macd(line, sig, hist)


def true_range(bars: Sequence[Bar]) -> list[float]:
    out: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            out.append(bar.high - bar.low)
        else:
            prev = bars[i - 1].close
            out.append(max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev)))
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> Series:
    """Average true range (Wilder), from the second bar on so every range has a previous close."""
    _check_period(period)
    return _wilder(true_range(bars), period, 1)


def adx(bars: Sequence[Bar], period: int = 14) -> Series:
    """Average directional index (Wilder), 0 to 100: trend strength, not direction."""
    _check_period(period)
    n = len(bars)
    out: Series = [None] * n
    if n < 2 * period + 1:
        return out
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, n):
        up = bars[i].high - bars[i - 1].high
        down = bars[i - 1].low - bars[i].low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    tr = _wilder(true_range(bars), period, 1)
    pdm, mdm = _wilder(plus_dm, period, 1), _wilder(minus_dm, period, 1)
    dx: list[float] = []
    first_dx = None
    for i in range(n):
        t, p, m = tr[i], pdm[i], mdm[i]
        if t is None or p is None or m is None:
            continue
        if first_dx is None:
            first_dx = i
        pdi, mdi = (100 * p / t, 100 * m / t) if t > 0 else (0.0, 0.0)
        dx.append(0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi))
    if first_dx is None:
        return out
    smoothed = _wilder(dx, period, 0)
    for offset, value in enumerate(smoothed):
        out[first_dx + offset] = value
    return out


def log_returns(closes: Sequence[float]) -> list[float]:
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]


def volatility(closes: Sequence[float], period: int = 20, periods_per_year: int = 252) -> Series:
    """Annualised volatility: sample standard deviation of ``period`` log returns."""
    if period < 2:
        raise ValueError("Volatility needs at least 2 returns.")
    out: Series = [None] * len(closes)
    rets = log_returns(closes)
    for i in range(period, len(closes)):
        window = rets[i - period:i]
        mean = sum(window) / period
        var = sum((r - mean) ** 2 for r in window) / (period - 1)
        out[i] = math.sqrt(var) * math.sqrt(periods_per_year)
    return out


def momentum(closes: Sequence[float], period: int = 20) -> Series:
    """Rate of change over ``period`` bars, as a fraction (0.05 is 5 percent)."""
    _check_period(period)
    out: Series = [None] * len(closes)
    for i in range(period, len(closes)):
        out[i] = closes[i] / closes[i - period] - 1.0
    return out


@dataclass(frozen=True)
class Snapshot:
    """The indicators at the last bar, the regime labels and any signals that disagree."""

    bars: int
    close: float
    sma_50: float | None
    sma_200: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    atr_14: float | None
    adx_14: float | None
    volatility_20: float | None
    momentum_20: float | None
    trend: str  # up, down, sideways, unknown
    volatility_regime: str  # low, normal, high, unknown
    contradictions: tuple[str, ...]


def snapshot(bars: Sequence[Bar], periods_per_year: int = 252) -> Snapshot:
    """Every indicator at the last bar. Labels say ``unknown`` when history is too short."""
    if not bars:
        raise ValueError("No bars: nothing to analyse.")
    closes = [b.close for b in bars]
    last = len(bars) - 1

    def at(series: Series) -> float | None:
        return series[last]

    s50, s200 = at(sma(closes, 50)), at(sma(closes, 200))
    r = at(rsi(closes, 14))
    m = macd(closes)
    a = at(atr(bars, 14))
    d = at(adx(bars, 14))
    vol_series = volatility(closes, 20, periods_per_year)
    v = at(vol_series)
    mom = at(momentum(closes, 20))
    trend = "unknown"
    if s50 is not None and s200 is not None:
        if d is not None and d < 20:
            trend = "sideways"
        elif closes[last] > s50 > s200:
            trend = "up"
        elif closes[last] < s50 < s200:
            trend = "down"
        else:
            trend = "sideways"
    regime = "unknown"
    history = [x for x in vol_series if x is not None]
    if v is not None and len(history) >= 60:
        ordered = sorted(history)
        low, high = ordered[len(ordered) // 4], ordered[(3 * len(ordered)) // 4]
        regime = "low" if v < low else "high" if v > high else "normal"
    contradictions: list[str] = []
    macd_now, signal_now = at(m.macd), at(m.signal)
    if trend == "up" and r is not None and r > 70:
        contradictions.append("uptrend_but_overbought")
    if trend == "down" and r is not None and r < 30:
        contradictions.append("downtrend_but_oversold")
    if trend == "up" and macd_now is not None and signal_now is not None and macd_now < signal_now:
        contradictions.append("uptrend_but_macd_below_signal")
    if trend == "down" and macd_now is not None and signal_now is not None and macd_now > signal_now:
        contradictions.append("downtrend_but_macd_above_signal")
    if mom is not None and s50 is not None and ((mom > 0) != (closes[last] > s50)):
        contradictions.append("momentum_disagrees_with_average")
    return Snapshot(
        bars=len(bars), close=closes[last], sma_50=s50, sma_200=s200, rsi_14=r, macd=macd_now, macd_signal=signal_now,
        atr_14=a, adx_14=d, volatility_20=v, momentum_20=mom, trend=trend, volatility_regime=regime,
        contradictions=tuple(contradictions),
    )
