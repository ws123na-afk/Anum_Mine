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


REGISTRY: dict[str, Spec] = {
    s.key: s
    for s in (
        Spec("buy_and_hold", BuyAndHold.version, "Buy and hold", "benchmark", {}, {}, needs_trades=False, warmup=False,
             description="Equal weights on the first bar, held to the end.", factory=BuyAndHold),
        Spec("trend_sma", TrendSma.version, "Trend following (moving averages)", "trend", {"fast": 50, "slow": 200},
             {"fast": (20, 50), "slow": (100, 200)}, description="Hold while the fast average is above the slow one.", factory=TrendSma),
        Spec("rsi_reversion", RsiReversion.version, "Mean reversion (RSI)", "mean_reversion", {"period": 14, "low": 30, "exit": 55},
             {"low": (25, 30, 35), "exit": (50, 60)}, description="Buy oversold, sell once it recovers.", factory=RsiReversion),
        Spec("momentum", Momentum.version, "Momentum", "momentum", {"lookback": 120, "threshold": 0.0, "top": 1, "every": 21},
             {"lookback": (60, 120, 250)}, description="Hold the strongest symbols with a positive return over the lookback.",
             factory=Momentum),
        Spec("periodic", Periodic.version, "Periodic investing", "periodic", {"tranches": 12, "every": 21}, {}, needs_trades=False, warmup=False,
             description="Invest the cash in equal steps at a fixed interval.", factory=Periodic),
        Spec("rebalance", Rebalance.version, "Fixed-weight rebalancing", "rebalancing", {"every": 63, "weights": {}},
             {"every": (21, 63)}, min_symbols=2, needs_trades=False, warmup=False, description="Back to fixed weights at a fixed interval.",
             factory=Rebalance),
        Spec("pair_ratio", PairRatio.version, "Pair trading (long only)", "pairs", {"lookback": 60, "entry_z": 2.0, "exit_z": 0.5},
             {"lookback": (40, 60), "entry_z": (1.5, 2.0)}, min_symbols=2, max_symbols=2,
             description="Hold the cheaper of two related symbols when their price ratio stretches.", factory=PairRatio),
    )
}


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
    for name in ("fast", "slow", "period", "lookback", "every", "tranches", "top"):
        if name in merged and int(merged[name]) < 1:
            raise StrategyError(f"{name} must be 1 or more.")
    if key == "trend_sma" and merged["fast"] >= merged["slow"]:
        raise StrategyError("The fast average must be shorter than the slow one.")
    if key == "rsi_reversion" and not 0 < merged["low"] < merged["exit"] < 100:
        raise StrategyError("RSI levels need 0 < low < exit < 100.")
    if key == "pair_ratio" and not 0 <= merged["exit_z"] < merged["entry_z"]:
        raise StrategyError("The exit level must be below the entry level.")
    return merged


def build(key: str, symbols: tuple[str, ...], params: dict[str, Any] | None = None) -> Any:
    return spec(key).factory(symbols, check_params(key, params, symbols))
