"""The versioned strategy registry (docs/finance-trading-intel.md#strategy-lab).

Each strategy is a small, readable rule with a key, a version, default parameters and a
small parameter grid for walk-forward validation. A change to a rule's logic is a new
version; stored runs keep the version they used. Long only, no leverage: these are
benchmark strategies for research and SIMULATED trading, not recommendations.

* ``buy_and_hold``: equal weights on the first bar, never traded again. The benchmark.
* ``trend_sma``: hold a symbol while its fast average is above its slow one (trend following).
* ``rsi_reversion``: buy when RSI falls below ``low``, sell when it rises above ``exit`` (mean reversion).
* ``momentum``: hold the ``top`` symbols whose return over ``lookback`` bars is above ``threshold``.
* ``periodic``: invest the starting cash in ``tranches`` equal steps every ``every`` bars (periodic investing).
* ``rebalance``: fixed weights, back to them every ``every`` bars.
* ``pair_ratio``: limited pair trading on two symbols: when the log price ratio is ``entry_z``
  standard deviations from its mean, hold the cheaper leg; flat again within ``exit_z``. No short side.
* ``trend_vol``: trend following with volatility controls: the SMA trend rule, sized to a target
  volatility and closed by an ATR trailing stop.
* ``breakout``: hold after a close above the highest close of the previous ``entry`` bars; leave
  after a close below the lowest close of the previous ``exit`` bars (Donchian channels).
* ``rsi_regime``: mean reversion only in a calm uptrend: buy an RSI dip while the close is above
  its long average and ADX says the market is ranging; leave on recovery or a trend break.
* ``crypto_trend``: crypto spot, daily bars every day of the week: hold while the fast EMA is
  above the slow one and RSI is not overbought, sized to a target volatility.

``CANDIDATES`` groups them into the six research candidates of the brief; ``buy_and_hold`` is
the benchmark every candidate is compared with.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .backtest import History


class StrategyError(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    key: str
    version: str
    name: str
    kind: str  # benchmark, trend, mean_reversion, momentum, periodic, rebalancing, pairs
    defaults: dict[str, Any]
    grid: dict[str, tuple[Any, ...]]
    min_symbols: int = 1
    max_symbols: int = 10
    needs_trades: bool = True  # validation asks for enough closed trades
    warmup: bool = True  # reads indicators, so a test window starts after warm-up bars; calendar rules start on the test bar
    description: str = ""
    candidate: int = 0  # the research candidate (1 to 6) it belongs to; 0 is the benchmark
    hypothesis: str = ""
    instruments: str = ""
    timeframe: str = "Daily bars"
    entry: str = ""
    exit: str = ""
    factory: Callable[[tuple[str, ...], dict[str, Any]], Any] = field(repr=False, default=None)  # type: ignore[assignment]


class _Base:
    key = ""
    version = ""

    def __init__(self, symbols: tuple[str, ...], params: dict[str, Any]) -> None:
        self.symbols = symbols
        self.p = params
        self.state: dict[str, Any] = {}


class BuyAndHold(_Base):
    key, version = "buy_and_hold", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        if self.state.get("done"):
            return None
        self.state["done"] = True
        return {s: 1.0 / len(self.symbols) for s in self.symbols}


class TrendSma(_Base):
    key, version = "trend_sma", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        out: dict[str, float] = {}
        for s in self.symbols:
            fast, slow = h.value(s, "sma", int(self.p["fast"])), h.value(s, "sma", int(self.p["slow"]))
            out[s] = 1.0 / len(self.symbols) if fast is not None and slow is not None and fast > slow else 0.0
        return out


class RsiReversion(_Base):
    key, version = "rsi_reversion", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        held: set[str] = self.state.setdefault("held", set())
        for s in self.symbols:
            r = h.value(s, "rsi", int(self.p["period"]))
            if r is None:
                continue
            if s not in held and r < float(self.p["low"]):
                held.add(s)
            elif s in held and r > float(self.p["exit"]):
                held.discard(s)
        return {s: (1.0 / len(self.symbols) if s in held else 0.0) for s in self.symbols}


class Momentum(_Base):
    key, version = "momentum", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        every = int(self.p.get("every", 21))
        if h.t % every:
            return None
        scored = [(h.value(s, "momentum", int(self.p["lookback"])), s) for s in self.symbols]
        ranked = sorted(((m, s) for m, s in scored if m is not None and m > float(self.p["threshold"])), reverse=True)
        top = [s for _, s in ranked[: int(self.p["top"])]]
        slots = max(1, min(int(self.p["top"]), len(self.symbols)))
        return {s: (1.0 / slots if s in top else 0.0) for s in self.symbols}


class Periodic(_Base):
    key, version = "periodic", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        start = self.state.setdefault("start", h.t)
        tranches, every = int(self.p["tranches"]), int(self.p["every"])
        step = (h.t - start) // every + 1
        if (h.t - start) % every or step > tranches:
            return None
        share = min(1.0, step / tranches)
        return {s: share / len(self.symbols) for s in self.symbols}


class Rebalance(_Base):
    key, version = "rebalance", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        start = self.state.setdefault("start", h.t)
        if (h.t - start) % int(self.p["every"]):
            return None
        weights = self.p.get("weights") or {}
        if weights:
            return {s: float(weights.get(s, 0.0)) for s in self.symbols}
        return {s: 1.0 / len(self.symbols) for s in self.symbols}


class PairRatio(_Base):
    key, version = "pair_ratio", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        a, b = self.symbols[0], self.symbols[1]
        n = int(self.p["lookback"])
        ca, cb = h.closes(a, n), h.closes(b, n)
        if len(ca) < n:
            return None
        ratios = [math.log(x / y) for x, y in zip(ca, cb)]
        mean = sum(ratios) / n
        std = math.sqrt(sum((r - mean) ** 2 for r in ratios) / (n - 1))
        if std == 0:
            return None
        z = (ratios[-1] - mean) / std
        side = self.state.get("side")
        if side is None and z > float(self.p["entry_z"]):
            side = b  # a is rich against b: hold b
        elif side is None and z < -float(self.p["entry_z"]):
            side = a
        elif side is not None and abs(z) < float(self.p["exit_z"]):
            side = None
        self.state["side"] = side
        return {a: 1.0 if side == a else 0.0, b: 1.0 if side == b else 0.0}


class TrendVol(_Base):
    key, version = "trend_vol", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        out: dict[str, float] = {}
        peaks: dict[str, float] = self.state.setdefault("peaks", {})
        n = len(self.symbols)
        for s in self.symbols:
            fast, slow = h.value(s, "sma", int(self.p["fast"])), h.value(s, "sma", int(self.p["slow"]))
            atr, vol, close = h.value(s, "atr", 14), h.value(s, "volatility", 20), h.close(s)
            if fast is None or slow is None or atr is None or vol is None or close is None or vol <= 0:
                out[s] = 0.0
                continue
            trending = fast > slow
            if s in peaks:
                peaks[s] = max(peaks[s], close)
                if close < peaks[s] - float(self.p["atr_stop"]) * atr or not trending:
                    del peaks[s]
            elif trending:
                peaks[s] = close
            out[s] = min(1.0, float(self.p["target_vol"]) / vol) / n if s in peaks else 0.0
        return out


class Breakout(_Base):
    key, version = "breakout", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        held: set[str] = self.state.setdefault("held", set())
        for s in self.symbols:
            close, high, low = h.close(s), h.value(s, "prior_high", int(self.p["entry"])), h.value(s, "prior_low", int(self.p["exit"]))
            if close is None or high is None or low is None:
                continue
            if s not in held and close > high:
                held.add(s)
            elif s in held and close < low:
                held.discard(s)
        return {s: (1.0 / len(self.symbols) if s in held else 0.0) for s in self.symbols}


class RsiRegime(_Base):
    key, version = "rsi_regime", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        held: set[str] = self.state.setdefault("held", set())
        for s in self.symbols:
            r, long_avg, adx, close = h.value(s, "rsi", 14), h.value(s, "sma", int(self.p["trend"])), h.value(s, "adx", 14), h.close(s)
            if r is None or long_avg is None or adx is None or close is None:
                continue
            calm_uptrend = close > long_avg and adx < float(self.p["max_adx"])
            if s not in held and calm_uptrend and r < float(self.p["low"]):
                held.add(s)
            elif s in held and (r > float(self.p["exit"]) or close < long_avg):
                held.discard(s)
        return {s: (1.0 / len(self.symbols) if s in held else 0.0) for s in self.symbols}


class CryptoTrend(_Base):
    key, version = "crypto_trend", "1.0.0"

    def targets(self, h: History) -> dict[str, float] | None:
        out: dict[str, float] = {}
        for s in self.symbols:
            fast, slow = h.value(s, "ema", int(self.p["fast"])), h.value(s, "ema", int(self.p["slow"]))
            r, vol = h.value(s, "rsi", 14), h.value(s, "volatility", 20, 365)
            if fast is None or slow is None or r is None or vol is None or vol <= 0:
                out[s] = 0.0
                continue
            hold = fast > slow and r < float(self.p["max_rsi"])
            out[s] = min(1.0, float(self.p["target_vol"]) / vol) / len(self.symbols) if hold else 0.0
        return out


_EQUITY_ETF = "Equities and ETFs on any supported venue; also currencies and commodities with daily bars"

REGISTRY: dict[str, Spec] = {
    s.key: s
    for s in (
        Spec("buy_and_hold", BuyAndHold.version, "Buy and hold", "benchmark", {}, {}, needs_trades=False, warmup=False,
             description="Equal weights on the first bar, held to the end.", candidate=0,
             hypothesis="The comparison point: owning the same symbols with no timing at all.", instruments="Any",
             entry="Buy equal weights at the first open.", exit="None: held to the end of the test window.", factory=BuyAndHold),
        Spec("periodic", Periodic.version, "Periodic investing", "periodic", {"tranches": 12, "every": 21}, {}, needs_trades=False, warmup=False,
             description="Invest the cash in equal steps at a fixed interval.", candidate=1,
             hypothesis="Investing in equal steps spreads the entry price over time and lowers the risk of one badly timed purchase; it does not raise the expected return.",
             instruments=_EQUITY_ETF, entry="Every `every` bars, raise the invested share by 1/`tranches`, equal weights across symbols.",
             exit="None: a long-term holding.", factory=Periodic),
        Spec("rebalance", Rebalance.version, "Fixed-weight rebalancing", "rebalancing", {"every": 63, "weights": {}},
             {"every": (21, 63)}, min_symbols=2, needs_trades=False, warmup=False, description="Back to fixed weights at a fixed interval.",
             candidate=1, hypothesis="Diversifying across symbols that do not move together lowers volatility; rebalancing keeps the risk mix from drifting.",
             instruments=_EQUITY_ETF, entry="Every `every` bars, trade back to the fixed weights (equal unless given).",
             exit="None: weights are restored, never abandoned.", factory=Rebalance),
        Spec("trend_sma", TrendSma.version, "Trend following (moving averages)", "trend", {"fast": 50, "slow": 200},
             {"fast": (20, 50), "slow": (100, 200)}, description="Hold while the fast average is above the slow one.", candidate=2,
             hypothesis="Prices that have risen tend to keep rising for a while (time-series momentum); staying out in a downtrend avoids the deepest drawdowns.",
             instruments=_EQUITY_ETF, entry="Close SMA(`fast`) above SMA(`slow`): hold an equal share.",
             exit="SMA(`fast`) at or below SMA(`slow`): sell.", factory=TrendSma),
        Spec("trend_vol", TrendVol.version, "Trend following with volatility controls", "trend",
             {"fast": 50, "slow": 200, "target_vol": 0.15, "atr_stop": 3.0},
             {"fast": (20, 50), "atr_stop": (2.0, 3.0), "target_vol": (0.10, 0.15)}, candidate=2,
             description="The trend rule, sized to a target volatility, with an ATR trailing stop.",
             hypothesis="As trend_sma, with steadier risk: a smaller position when the market is wild and a stop that limits the give-back after a peak.",
             instruments=_EQUITY_ETF,
             entry="SMA(`fast`) above SMA(`slow`): hold min(1, `target_vol` / 20-bar annualised volatility) of an equal share.",
             exit="The close falls more than `atr_stop` x ATR(14) below its highest close since entry, or the averages cross down.", factory=TrendVol),
        Spec("momentum", Momentum.version, "Momentum", "momentum", {"lookback": 120, "threshold": 0.0, "top": 1, "every": 21},
             {"lookback": (60, 120, 250)}, description="Hold the strongest symbols with a positive return over the lookback.", candidate=3,
             hypothesis="Across symbols, recent winners tend to keep outperforming recent losers over the following months (cross-sectional momentum).",
             instruments=_EQUITY_ETF + "; needs several symbols to rank",
             entry="Every `every` bars, rank by return over `lookback` bars; hold the `top` with a return above `threshold`.",
             exit="Dropped at the next ranking when no longer in the top or no longer positive.", factory=Momentum),
        Spec("breakout", Breakout.version, "Breakout (Donchian channels)", "momentum", {"entry": 55, "exit": 20},
             {"entry": (20, 55), "exit": (10, 20)}, candidate=3, description="Buy a new high, sell a new low.",
             hypothesis="A close above the recent range often starts a move that continues; the classic turtle-style channel rule.",
             instruments=_EQUITY_ETF + "; crypto with daily bars",
             entry="The close is above the highest close of the previous `entry` bars.",
             exit="The close is below the lowest close of the previous `exit` bars.", factory=Breakout),
        Spec("rsi_reversion", RsiReversion.version, "Mean reversion (RSI)", "mean_reversion", {"period": 14, "low": 30, "exit": 55},
             {"low": (25, 30, 35), "exit": (50, 60)}, description="Buy oversold, sell once it recovers.", candidate=4,
             hypothesis="Short, sharp falls in liquid instruments partly reverse within days.",
             instruments=_EQUITY_ETF, entry="RSI(`period`) below `low`.", exit="RSI(`period`) above `exit`.", factory=RsiReversion),
        Spec("rsi_regime", RsiRegime.version, "Mean reversion with a regime filter", "mean_reversion",
             {"low": 30, "exit": 55, "trend": 200, "max_adx": 25},
             {"low": (25, 30, 35), "max_adx": (20, 25)}, candidate=4, description="Buy dips only in a calm uptrend.",
             hypothesis="Dips revert more reliably in a long-term uptrend that is ranging than in a falling or strongly trending market.",
             instruments=_EQUITY_ETF,
             entry="Close above SMA(`trend`), ADX(14) below `max_adx`, and RSI(14) below `low`.",
             exit="RSI(14) above `exit`, or the close falls below SMA(`trend`).", factory=RsiRegime),
        Spec("pair_ratio", PairRatio.version, "Pair trading (long only)", "pairs", {"lookback": 60, "entry_z": 2.0, "exit_z": 0.5},
             {"lookback": (40, 60), "entry_z": (1.5, 2.0)}, min_symbols=2, max_symbols=2, candidate=5,
             description="Hold the cheaper of two related symbols when their price ratio stretches.",
             hypothesis="Two closely related instruments (for example two share classes, or two ETFs on one index) drift apart and back; the cheap leg tends to catch up. Long only: there is no short leg, so this is a limited form of pairs trading.",
             instruments="Two related equities or ETFs on the same venue and currency",
             entry="The log price ratio is more than `entry_z` standard deviations from its `lookback` mean: hold the cheaper leg.",
             exit="The ratio is back within `exit_z` standard deviations: sell.", factory=PairRatio),
        Spec("crypto_trend", CryptoTrend.version, "Crypto spot trend (EMA and RSI)", "crypto",
             {"fast": 20, "slow": 50, "max_rsi": 75, "target_vol": 0.5},
             {"fast": (10, 20), "slow": (50, 100)}, candidate=6,
             description="Spot only: hold while the fast EMA is above the slow one and RSI is not overbought.",
             hypothesis="Crypto assets trend strongly and crash hard; a trend filter with volatility sizing tries to keep part of the trend and miss part of the crash. Spot only: no leverage, no futures, no shorting.",
             instruments="Crypto spot pairs with daily bars (365 a year, every day)", timeframe="Daily bars, 24/7 (a bar closes at 00:00 UTC)",
             entry="EMA(`fast`) above EMA(`slow`) and RSI(14) below `max_rsi`: hold min(1, `target_vol` / annualised volatility) of an equal share.",
             exit="EMA(`fast`) at or below EMA(`slow`), or RSI(14) at or above `max_rsi`.", factory=CryptoTrend),
    )
}

CANDIDATES: dict[int, str] = {
    1: "Diversified periodic investing and rebalancing",
    2: "Trend following with volatility-based controls",
    3: "Momentum and breakout",
    4: "Mean reversion with regime filters",
    5: "Statistical pairs trading (long only)",
    6: "Crypto spot strategies (native rule; Freqtrade not adopted)",
}


def by_candidate() -> dict[int, list[str]]:
    out: dict[int, list[str]] = {n: [] for n in CANDIDATES}
    for key, item in REGISTRY.items():
        if item.candidate:
            out[item.candidate].append(key)
    return out


def spec(key: str) -> Spec:
    try:
        return REGISTRY[key]
    except KeyError:
        raise StrategyError(f"Unknown strategy {key!r}: use one of {', '.join(REGISTRY)}.") from None


def check_params(key: str, params: dict[str, Any] | None, symbols: tuple[str, ...]) -> dict[str, Any]:
    """The defaults overlaid with ``params``; unknown keys and bad values are refused."""
    s = spec(key)
    if not s.min_symbols <= len(symbols) <= s.max_symbols:
        raise StrategyError(f"{s.name} needs {s.min_symbols} to {s.max_symbols} symbols.")
    merged = dict(s.defaults)
    for name, value in (params or {}).items():
        if name not in s.defaults:
            raise StrategyError(f"{s.name} has no parameter {name!r}.")
        if name == "weights":
            if not isinstance(value, dict) or any(k not in symbols for k in value):
                raise StrategyError("Weights name the strategy's own symbols.")
            weights = {k: float(v) for k, v in value.items()}
            if any(w < 0 for w in weights.values()) or sum(weights.values()) > 1.0 + 1e-9:
                raise StrategyError("Weights are 0 or more and add up to at most 1.")
            merged[name] = weights
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise StrategyError(f"{name} must be a number.") from None
        if not math.isfinite(number) or number < 0 or number > 10_000:
            raise StrategyError(f"{name} is out of range.")
        merged[name] = type(s.defaults[name])(number)
    for name in ("fast", "slow", "period", "lookback", "every", "tranches", "top", "entry", "trend"):
        if name in merged and int(merged[name]) < 1:
            raise StrategyError(f"{name} must be 1 or more.")
    if key in {"trend_sma", "trend_vol", "crypto_trend"} and merged["fast"] >= merged["slow"]:
        raise StrategyError("The fast average must be shorter than the slow one.")
    if key in {"rsi_reversion", "rsi_regime"} and not 0 < merged["low"] < merged["exit"] < 100:
        raise StrategyError("RSI levels need 0 < low < exit < 100.")
    if key == "pair_ratio" and not 0 <= merged["exit_z"] < merged["entry_z"]:
        raise StrategyError("The exit level must be below the entry level.")
    if key == "breakout" and (int(merged["entry"]) < 1 or int(merged["exit"]) < 1):
        raise StrategyError("Channel lengths must be 1 or more.")
    if "target_vol" in merged and not 0 < merged["target_vol"] <= 2:
        raise StrategyError("The target volatility is a yearly fraction above 0 and at most 2 (200 percent).")
    if key == "crypto_trend" and not 50 < merged["max_rsi"] < 100:
        raise StrategyError("The RSI ceiling must be between 50 and 100.")
    return merged


def build(key: str, symbols: tuple[str, ...], params: dict[str, Any] | None = None) -> Any:
    return spec(key).factory(symbols, check_params(key, params, symbols))
