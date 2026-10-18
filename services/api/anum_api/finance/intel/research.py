"""Evaluating a stored strategy version on stored bars (docs/finance-trading-intel.md#evaluation-reports).

Used by the Strategy Research agent and by the person's own "evaluate" action. Bars come
from ``finance_market_bars`` (never invented); a symbol without bars stops the run. A
walk-forward run is executed twice on the same bars and marked ``reproduced`` only when the
data hash, metrics and folds are identical, which promotion requires.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from ...schemas import TenantContext, new_id
from ..store import FinanceStore
from . import backtest as bt
from . import load_guard
from .marketdata import Candle, Instrument, check_series
from .markets import periods_per_year
from .records import Agent, StrategyConfig, StrategyRun
from .report import evaluation_report
from .strategies import build
from .validation import data_hash, walk_forward

MIN_BARS = 60
# Evaluations run inside the API process on a shared VM (docs/finance-trading-intel.md#checks):
# only the newest bars are used, and one evaluation per person runs at a time.
MAX_EVAL_BARS = 3_000
_running: set[tuple[str, str, str]] = set()
_running_lock = threading.Lock()


@contextmanager
def _one_at_a_time(context: TenantContext):  # type: ignore[no-untyped-def]
    key = (context.tenant_id, context.workspace_id, context.user_id)
    with _running_lock:
        if key in _running:
            raise ResearchError("busy", "An evaluation of yours is already running. Wait for it to finish.")
        _running.add(key)
    try:
        yield
    finally:
        with _running_lock:
            _running.discard(key)


class ResearchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Dataset:
    bars: dict[str, list[Candle]]
    instruments: list[Instrument]
    source: str
    blocking: int
    listing_known: bool
    periods_per_year: int
    last_day: Any
    trimmed: bool = False


def load(store: FinanceStore, context: TenantContext, strategy: StrategyConfig, today: Any) -> Dataset:
    """The strategy's instruments and their stored bars, checked. Raises when a symbol has none."""
    if len(strategy.instrument_ids) != len(strategy.symbols):
        raise ResearchError("no_instruments", "Link each symbol to a stored instrument with bars first.")
    bars: dict[str, list[Candle]] = {}
    instruments: list[Instrument] = []
    providers: set[str] = set()
    blocking = 0
    trimmed = False
    for symbol, instrument_id in zip(strategy.symbols, strategy.instrument_ids):
        instrument = store.get_instrument(context, instrument_id)
        if instrument is None or instrument.symbol != symbol:
            raise ResearchError("no_instruments", f"The instrument for {symbol} is missing. It may have been deleted.")
        stored = store.list_bars(context, instrument_id, limit=50_000)
        trimmed = trimmed or len(stored) > MAX_EVAL_BARS
        if len(stored) < MIN_BARS:
            raise ResearchError("not_enough_bars", f"{symbol} has {len(stored)} stored bars; at least {MIN_BARS} are needed.")
        candles = [b.candle() for b in stored][-MAX_EVAL_BARS:]
        blocking += sum(1 for i in check_series(candles, instrument.asset_class, instrument.venue, today) if i.blocking)
        providers.update(f"{b.provider}: {b.source}" for b in stored[-1:])
        bars[symbol] = candles
        instruments.append(instrument)
    listing_known = all(i.listed_on is not None for i in instruments)
    return Dataset(bars=bars, instruments=instruments, source="; ".join(sorted(providers))[:120], blocking=blocking,
                   listing_known=listing_known, periods_per_year=periods_per_year(instruments[0].asset_class),
                   last_day=min(b[-1].day for b in bars.values()), trimmed=trimmed)


def _plain(value: Any) -> Any:
    return value.as_dict() if hasattr(value, "as_dict") else value


def evaluate(store: FinanceStore, context: TenantContext, strategy: StrategyConfig, *, kind: Literal["backtest", "walk_forward"],
             actor: Agent | str, now: datetime, costs: bt.Costs | None = None, today: Any = None) -> StrategyRun:
    """Run, store and return one evaluation of this version. SIMULATED."""
    who = Agent(actor)
    # One per person, then the machine-wide load guard (load_guard.ServerBusy when busy).
    with _one_at_a_time(context), load_guard.heavy():
        return _evaluate(store, context, strategy, kind=kind, who=who, now=now, costs=costs, today=today)


def _evaluate(store: FinanceStore, context: TenantContext, strategy: StrategyConfig, *, kind: str, who: Agent, now: datetime,
              costs: bt.Costs | None, today: Any) -> StrategyRun:
    data = load(store, context, strategy, today or now.date())
    extra = ["trimmed_to_recent_bars"] if data.trimmed else []
    costs = costs or bt.Costs()
    base: dict[str, Any] = dict(
        id=new_id("fin_run"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        strategy_id=strategy.id, kind=kind, created_at=now, data_source=data.source or "stored bars", code_version=strategy.code_version,
        symbols=list(strategy.symbols), params=dict(strategy.params), costs=costs.describe(), requested_by=who.value,
    )
    if kind == "backtest":
        aligned = bt.align(data.bars)
        if len(next(iter(aligned.values()))) < MIN_BARS:
            raise ResearchError("not_enough_bars", "The symbols share too few days with bars.")
        result = bt.run(aligned, build(strategy.key, tuple(strategy.symbols), strategy.params), costs=costs)
        bench = bt.run(aligned, build("buy_and_hold", tuple(strategy.symbols)), costs=costs)
        m, b = bt.metrics(result, data.periods_per_year), bt.metrics(bench, data.periods_per_year)
        reasons = ["data_issues"] if data.blocking else []
        run = StrategyRun(**base, data_hash=data_hash(aligned), metrics=m.as_dict(), benchmark=b.as_dict(), verdict="completed",
                          reasons=reasons, warnings=([] if data.listing_known else ["survivorship_unknown"]) + extra, reproduced=False,
                          report={"label": "SIMULATED", "kind": "backtest", "bars": m.bars})
    else:
        def once() -> Any:
            return walk_forward(data.bars, strategy.key, costs=costs, periods_per_year=data.periods_per_year,
                                blocking_issues=data.blocking, listing_known=data.listing_known, params=dict(strategy.params))

        first, second = once(), once()
        same = first.as_dict() == second.as_dict()
        report = evaluation_report(first, data_source=data.source, params=dict(strategy.params))
        run = StrategyRun(
            **base, data_hash=first.data_hash, metrics=_plain(first.out_of_sample) or {}, benchmark=_plain(first.benchmark),
            verdict=first.verdict, reasons=list(first.reasons), warnings=list(first.warnings) + extra, folds=first.as_dict()["folds"],
            reproduced=same, report=report,
        )
    store.save_strategy_run(run)
    return run
