"""Walk-forward validation and the rules that reject unreliable results (docs/finance-trading-intel.md#validation).

Time-ordered splits only: each fold picks parameters on a training window and is then
measured on the **next** window it never saw. Bars before a window only warm indicators up;
no fold trains on data after its test window. Out-of-sample results are joined fold by
fold into one equity line.

A strategy **passes** only when all of these hold; otherwise it is **rejected** with every
reason (or ``insufficient_data``):

* ``data_issues``: the bars have a blocking problem (duplicates, future dates, bad order);
* ``look_ahead``: the signals change when future bars are cut off (``probe_look_ahead``);
* ``too_few_folds``: fewer than 2 test windows;
* ``too_few_trades``: fewer than ``MIN_TRADES`` closed trades out of sample (rules that trade
  rarely by design, such as buy and hold, periodic investing and rebalancing, are exempt);
* ``overfit``: in-sample Sharpe above 0.5 and out-of-sample Sharpe below half of it;
* ``no_edge``: out-of-sample Sharpe at or below 0;
* ``unstable``: the chosen parameters change in more than half of the folds while the grid
  has more than one choice.

Survivorship: when an instrument's listing dates are unknown the result carries a
``survivorship_unknown`` warning (a universe picked from today's listings overstates past
results); an instrument delisted inside the period is kept to its last bar, never dropped.

Passing says the rule held up on these past bars with these costs; it says nothing certain
about the future.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from .backtest import Costs, Metrics, Result, align, metrics, run
from .marketdata import Candle
from .strategies import build, spec

MIN_TRADES = 5
MAX_GRID = 24


@dataclass
class Fold:
    train: tuple[date, date]
    test: tuple[date, date]
    params: dict[str, Any]
    in_sample: Metrics
    out_of_sample: Metrics


@dataclass
class Validation:
    strategy: str
    version: str
    symbols: tuple[str, ...]
    verdict: str  # passed, rejected, insufficient_data
    reasons: list[str]
    warnings: list[str]
    folds: list[Fold]
    out_of_sample: Metrics | None
    benchmark: Metrics | None
    beats_benchmark: bool | None
    chosen_params: dict[str, Any] | None
    data_hash: str
    costs: dict[str, str]
    equity: list[tuple[str, float]] = field(default_factory=list)
    benchmark_equity: list[tuple[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy, "version": self.version, "symbols": list(self.symbols), "verdict": self.verdict,
            "reasons": self.reasons, "warnings": self.warnings, "chosen_params": self.chosen_params,
            "out_of_sample": self.out_of_sample.as_dict() if self.out_of_sample else None,
            "benchmark": self.benchmark.as_dict() if self.benchmark else None, "beats_benchmark": self.beats_benchmark,
            "folds": [
                {"train": [f.train[0].isoformat(), f.train[1].isoformat()], "test": [f.test[0].isoformat(), f.test[1].isoformat()],
                 "params": f.params, "in_sample_sharpe": _r(f.in_sample.sharpe), "out_of_sample_sharpe": _r(f.out_of_sample.sharpe),
                 "out_of_sample_return": _r(f.out_of_sample.total_return)}
                for f in self.folds
            ],
            "data_hash": self.data_hash, "costs": self.costs,
            "equity": self.equity[-400:], "benchmark_equity": self.benchmark_equity[-400:],
        }


def _r(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def data_hash(data: Mapping[str, Sequence[Candle]]) -> str:
    """SHA-256 over every bar used, so a stored run names exactly the data it saw."""
    digest = hashlib.sha256()
    for symbol in sorted(data):
        for c in data[symbol]:
            digest.update(json.dumps([symbol, c.day.isoformat(), str(c.open), str(c.high), str(c.low), str(c.close),
                                      str(c.volume)]).encode())
    return digest.hexdigest()


def grid(key: str) -> list[dict[str, Any]]:
    g = spec(key).grid
    if not g:
        return [{}]
    names = sorted(g)
    combos = [dict(zip(names, values)) for values in itertools.product(*(g[n] for n in names))]
    return combos[:MAX_GRID]


def probe_look_ahead(data: Mapping[str, Sequence[Candle]], key: str, params: dict[str, Any], points: int = 8) -> bool:
    """True when every signal up to and including bar ``k`` is the same with the bars after ``k`` removed.

    The signal at the cut bar itself is compared too (``final_signal``): a rule that peeks one
    bar ahead only differs there, because every earlier bar still has its next bar in the cut.
    """
    bars = align(data)
    symbols = tuple(bars)
    n = len(bars[symbols[0]])
    full = run(bars, build(key, symbols, params), final_signal=True).targets
    for k in sorted({max(2, n * (i + 1) // (points + 1)) for i in range(points)}):
        cut = {s: v[: k + 1] for s, v in bars.items()}
        partial = run(cut, build(key, symbols, params), final_signal=True).targets
        if partial != full[: len(partial)]:
            return False
    return True


def _score(m: Metrics) -> float:
    return m.sharpe if m.sharpe is not None else -1e9


def walk_forward(data: Mapping[str, Sequence[Candle]], key: str, *, train_bars: int = 500, test_bars: int = 125,
                 costs: Costs | None = None, cash: Decimal = Decimal("100000"), periods_per_year: int = 252,
                 blocking_issues: int = 0, listing_known: bool = True, warmup: int = 250) -> Validation:
    s = spec(key)
    costs = costs or Costs()
    bars = align(data)
    symbols = tuple(bars)
    n = len(bars[symbols[0]])
    days = [c.day for c in bars[symbols[0]]]
    reasons: list[str] = []
    warnings: list[str] = []
    digest = data_hash(bars)
    if blocking_issues:
        reasons.append("data_issues")
    if not listing_known:
        warnings.append("survivorship_unknown")
    folds: list[Fold] = []
    start = train_bars
    oos_equity: list[tuple[str, float]] = []
    level = float(cash)
    while start + test_bars <= n:
        train_from = max(0, start - train_bars)
        best: tuple[float, dict[str, Any], Metrics] | None = None
        for params in grid(key):
            merged = {**s.defaults, **params}
            try:
                res = run(bars, build(key, symbols, merged), cash=cash, costs=costs, start_index=train_from, end_index=start)
            except ValueError:
                continue
            m = metrics(res, periods_per_year)
            if best is None or _score(m) > best[0]:
                best = (_score(m), merged, m)
        if best is None:
            break
        _, chosen, in_m = best
        test_from = max(0, start - warmup) if s.warmup else start
        res = run(bars, build(key, symbols, chosen), cash=cash, costs=costs, start_index=test_from, end_index=start + test_bars)
        # Only the test window counts: re-base the run at the first test bar.
        offset = start - test_from
        window = Result(days=res.days[offset:], equity=res.equity[offset:], trades=[t for t in res.trades if t.day >= days[start]],
                        targets=res.targets[offset:], costs=costs, fees=sum((t.fee for t in res.trades if t.day >= days[start]), Decimal("0")))
        out_m = metrics(window, periods_per_year)
        folds.append(Fold((days[train_from], days[start - 1]), (days[start], days[start + test_bars - 1]), chosen, in_m, out_m))
        base = window.equity[0]
        for d, e in zip(window.days, window.equity):
            oos_equity.append((d.isoformat(), level * e / base))
        level = oos_equity[-1][1]
        start += test_bars
    if len(folds) < 2:
        return Validation(key, s.version, symbols, "insufficient_data", reasons or ["too_few_folds"], warnings, folds, None, None, None,
                          None, digest, costs.describe())
    joined = Result(days=[date.fromisoformat(d) for d, _ in oos_equity], equity=[e for _, e in oos_equity],
                    trades=[], targets=[], costs=costs)
    oos = metrics(joined, periods_per_year)
    closed = sum(f.out_of_sample.closed_trades for f in folds)
    oos = Metrics(**{**oos.__dict__, "trades": sum(f.out_of_sample.trades for f in folds), "closed_trades": closed,
                     "fees": sum(f.out_of_sample.fees for f in folds), "exposure": sum(f.out_of_sample.exposure for f in folds) / len(folds),
                     "turnover": sum(f.out_of_sample.turnover for f in folds) / len(folds)})
    if not probe_look_ahead(bars, key, folds[-1].params):
        reasons.append("look_ahead")
    if s.needs_trades and closed < MIN_TRADES:
        reasons.append("too_few_trades")
    in_sharpe = sum(_score(f.in_sample) for f in folds) / len(folds)
    if in_sharpe > 0.5 and (oos.sharpe or 0.0) < in_sharpe / 2:
        reasons.append("overfit")
    if oos.sharpe is None or oos.sharpe <= 0:
        reasons.append("no_edge")
    if len(grid(key)) > 1:
        changes = sum(1 for a, b in zip(folds, folds[1:]) if a.params != b.params)
        if changes > len(folds) / 2:
            reasons.append("unstable")
    # The benchmark holds from the first test bar to the last one, the same span as the joined line.
    bench_res = run(bars, build("buy_and_hold", symbols), cash=cash, costs=costs, start_index=train_bars,
                    end_index=train_bars + len(folds) * test_bars)
    bench = metrics(bench_res, periods_per_year)
    b0 = bench_res.equity[0]
    bench_line = [(d.isoformat(), float(cash) * e / b0) for d, e in zip(bench_res.days, bench_res.equity)]
    beats = (oos.sharpe or 0.0) > (bench.sharpe or 0.0) and oos.total_return > bench.total_return
    verdict = "rejected" if reasons else "passed"
    return Validation(key, s.version, symbols, verdict, reasons, warnings, folds, oos, bench, beats, folds[-1].params, digest,
                      costs.describe(), oos_equity, bench_line)
