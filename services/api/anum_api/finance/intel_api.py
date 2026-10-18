"""Read-only routes for the trading intelligence dashboard under ``/api/v1/finance/intel``
(docs/finance-trading-intel.md#dashboard).

Everything here is SIMULATED or recorded research. These routes only read: they never create
an order, change a strategy, loosen a rule or touch a kill switch (the existing routes in
``trading_api`` do that, through the gateway and the Guardian). ``finance:read`` on every route,
and every record is the caller's own (the store reads in the owner's scope, under forced RLS in
PostgreSQL mode).

Strategies, their backtest and walk-forward runs, and the Supervisor's and specialist agents'
decisions are read through optional store methods (``list_strategies``, ``list_strategy_runs``,
``list_agent_decisions``). A store without them answers an empty page with ``recording: false``
so the dashboard can say plainly that nothing is recorded yet, instead of inventing rows.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..dependencies import tenant_context
from ..schemas import TenantContext, utc_now
from .api import _read
from .config import finance_config
from .execution import ExecMode
from .guardian import GuardianRules
from .intel.markets import VENUES, session
from .policy import current_rules
from .store import MAX_PAGE, FinanceStore, open_finance_store
from .trading import SIMULATED_NOTE, MoneyRow, PortfolioMode, PositionView
from .trading_api import execution_status, portfolio_view

router = APIRouter(prefix="/api/v1/finance/intel", tags=["finance"])

LIFECYCLE = ("DRAFT", "BACKTESTING", "VALIDATION", "PAPER_ACTIVE", "PAPER_SUSPENDED", "RETIRED")
SIMULATION_ONLY = (
    "Simulation only, no real orders. Paper and shadow trades use recorded prices and stated costs; "
    "live trading is off and cannot be switched on from ANUM."
)
NOT_RECORDED = "Nothing is recorded yet: the trading agents and the strategy lab have not stored any {what} for you."
MAX_HOLDINGS = 50
MAX_TEXT = 500


def _get(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _text(value: object, limit: int = MAX_TEXT) -> str | None:
    """Recorded text is shown as data, never followed: trimmed and bounded."""
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text[:limit] or None


def _texts(value: object, limit: int = 20) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items[:limit]:
        if isinstance(item, (Mapping, BaseModel)):
            item = _get(item, "message") or _get(item, "text") or _get(item, "code")
        text = _text(item)
        if text:
            out.append(text)
    return out


def _numbers(value: object) -> dict[str, float | None]:
    """A metrics record as plain numbers (anything else is left out)."""
    if value is None:
        return {}
    raw = value.model_dump() if isinstance(value, BaseModel) else value if isinstance(value, Mapping) else getattr(value, "__dict__", {})
    out: dict[str, float | None] = {}
    for key, item in list(raw.items())[:40]:
        if isinstance(item, bool):
            continue
        if item is None:
            out[str(key)[:40]] = None
        elif isinstance(item, (int, float, Decimal)):
            number = float(item)
            out[str(key)[:40]] = number if math.isfinite(number) else None
    return out


# -- overview ---------------------------------------------------------------------------------------------


class SimulationView(BaseModel):
    simulation_only: bool
    live_enabled: bool
    wider_live_enabled: bool
    note: str


class SwitchState(BaseModel):
    person_engaged: bool
    global_engaged: bool
    can_change_global: bool


class VenueView(BaseModel):
    code: str
    name: str
    timezone: str
    state: str
    local_time: datetime
    holidays_known: bool
    reason: str
    note: str


class PortfolioIntel(BaseModel):
    id: str
    name: str
    mode: PortfolioMode
    cash: list[MoneyRow]
    realised: list[MoneyRow]
    unrealised: list[MoneyRow]
    fees: list[MoneyRow]
    slippage: list[MoneyRow]
    holdings: list[PositionView]
    holdings_total: int
    unpriced: list[str]
    waiting: int
    simulated: bool = True


class RiskView(BaseModel):
    version: int
    rules: GuardianRules
    paper_always_needs_approval: bool
    read_only: bool
    note: str


class RecordingView(BaseModel):
    strategies: bool
    runs: bool
    decisions: bool


class IntelOverview(BaseModel):
    simulation: SimulationView
    switches: SwitchState
    venues: list[VenueView]
    portfolios: list[PortfolioIntel]
    risk: RiskView
    recording: RecordingView
    pending_requests: int
    agent_requests: int
    note: str
    generated_at: datetime


def _recording(store: FinanceStore) -> RecordingView:
    return RecordingView(strategies=callable(getattr(store, "list_strategies", None)), runs=callable(getattr(store, "list_strategy_runs", None)),
                         decisions=callable(getattr(store, "list_agent_decisions", None)))


def _venues(now: datetime) -> list[VenueView]:
    out = []
    for code, v in VENUES.items():
        s = session(code, now)
        out.append(VenueView(code=code, name=v.name, timezone=v.timezone, state=s.state.value, local_time=s.local_time,
                             holidays_known=s.holidays_known, reason=s.reason, note=v.note))
    return out


def _risk(store: FinanceStore, context: TenantContext) -> RiskView:
    rules, version = current_rules(store, context)
    return RiskView(
        version=version, rules=rules, paper_always_needs_approval=True, read_only=True,
        note="Your Guardian rules, read only here. Change them on the Guardian tab: a change that loosens a rule needs your confirmation, and no agent can change them.",
    )


@router.get("/overview", response_model=IntelOverview)
def get_overview(context: TenantContext = Depends(tenant_context)) -> IntelOverview:
    """The dashboard's first read: simulation status, kill switches, venue sessions, SIMULATED
    portfolios with P&L, your Guardian limits (read only) and what is recorded so far."""
    _read(context)
    now = utc_now()
    with open_finance_store(context) as store:
        status = execution_status(store, context)
        portfolios = []
        for p in store.list_portfolios(context):
            view = portfolio_view(store, context, p, now)
            portfolios.append(PortfolioIntel(
                id=view.id, name=view.name, mode=view.mode, cash=view.cash, realised=view.realised, unrealised=view.unrealised, fees=view.fees,
                slippage=view.slippage, holdings=view.holdings[:MAX_HOLDINGS], holdings_total=len(view.holdings), unpriced=view.unpriced[:50],
                waiting=view.waiting,
            ))
        risk = _risk(store, context)
        recording = _recording(store)
        agent_total = _agent_requests(store, context)
    return IntelOverview(
        simulation=SimulationView(simulation_only=True, live_enabled=finance_config.live_enabled,
                                  wider_live_enabled=finance_config.wider_live_enabled, note=SIMULATION_ONLY),
        switches=SwitchState(person_engaged=status.person_switch.engaged, global_engaged=status.global_switch.engaged,
                             can_change_global=status.global_switch.can_change),
        venues=_venues(now), portfolios=portfolios, risk=risk, recording=recording, pending_requests=status.pending, agent_requests=agent_total,
        note=SIMULATED_NOTE, generated_at=now,
    )


def _agent_requests(store: FinanceStore, context: TenantContext) -> int:
    """Paper and shadow requests an agent proposed (recent 500), for the overview count."""
    total = 0
    for mode in (ExecMode.PAPER.value, ExecMode.SHADOW.value):
        items, _ = store.list_intents(context, status=None, mode=mode, limit=500, offset=0)
        total += sum(1 for i in items if i.requested_by == "agent")
    return total


# -- strategies and runs ---------------------------------------------------------------------------------


class StrategyView(BaseModel):
    id: str
    key: str | None
    name: str
    version: int | None
    state: str
    symbols: list[str]
    params: dict[str, Any]
    reason: str | None
    updated_at: datetime | None
    simulated: bool = True


class StrategyPage(BaseModel):
    items: list[StrategyView]
    total: int
    limit: int
    offset: int
    recording: bool
    note: str | None


class FoldView(BaseModel):
    index: int
    train_start: date | None
    train_end: date | None
    test_start: date | None
    test_end: date | None
    metrics: dict[str, float | None]


class RunView(BaseModel):
    id: str
    kind: str
    created_at: datetime | None
    data_source: str | None
    data_hash: str | None
    metrics: dict[str, float | None]
    benchmark: dict[str, float | None]
    verdict: str | None
    reasons: list[str]
    folds: list[FoldView]
    simulated: bool = True


class RunPage(BaseModel):
    items: list[RunView]
    total: int
    limit: int
    offset: int
    recording: bool
    note: str | None


def _params(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, item in list(value.items())[:30]:
        if isinstance(item, (bool, int, float, str)) or item is None:
            out[str(key)[:40]] = item if not isinstance(item, str) else item[:80]
        elif isinstance(item, Decimal):
            out[str(key)[:40]] = str(item)
    return out


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def strategy_view(item: object) -> StrategyView:
    state = (_text(_get(item, "state") or _get(item, "lifecycle")) or "DRAFT").upper()
    version = _get(item, "version")
    symbols = _get(item, "symbols") or ()
    return StrategyView(
        id=str(_get(item, "id")), key=_text(_get(item, "key"), 60), name=_text(_get(item, "name"), 120) or _text(_get(item, "key"), 60) or "Strategy",
        version=int(version) if isinstance(version, int) and not isinstance(version, bool) else None,
        state=state if state in LIFECYCLE else "DRAFT", symbols=[s for s in (_text(x, 20) for x in list(symbols)[:20]) if s],
        params=_params(_get(item, "params")), reason=_text(_get(item, "reason")), updated_at=_get(item, "updated_at") or _get(item, "created_at"),
    )


def run_view(item: object) -> RunView:
    folds = []
    for index, fold in enumerate(list(_get(item, "folds") or [])[:50]):
        folds.append(FoldView(index=int(_get(fold, "index", index)), train_start=_date(_get(fold, "train_start")), train_end=_date(_get(fold, "train_end")),
                              test_start=_date(_get(fold, "test_start")), test_end=_date(_get(fold, "test_end")), metrics=_numbers(_get(fold, "metrics"))))
    return RunView(
        id=str(_get(item, "id")), kind=_text(_get(item, "kind"), 30) or "backtest", created_at=_get(item, "created_at"),
        data_source=_text(_get(item, "data_source"), 200), data_hash=_text(_get(item, "data_hash"), 80), metrics=_numbers(_get(item, "metrics")),
        benchmark=_numbers(_get(item, "benchmark")), verdict=_text(_get(item, "verdict"), 40), reasons=_texts(_get(item, "reasons")), folds=folds,
    )


@router.get("/strategies", response_model=StrategyPage)
def list_strategies(
    state: Literal["DRAFT", "BACKTESTING", "VALIDATION", "PAPER_ACTIVE", "PAPER_SUSPENDED", "RETIRED"] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> StrategyPage:
    """Your strategies with their lifecycle state, newest change first. SIMULATED."""
    _read(context)
    with open_finance_store(context) as store:
        reader = getattr(store, "list_strategies", None)
        if not callable(reader):
            return StrategyPage(items=[], total=0, limit=limit, offset=offset, recording=False, note=NOT_RECORDED.format(what="strategies"))
        items, total = reader(context, state=state, limit=limit, offset=offset)
    return StrategyPage(items=[strategy_view(i) for i in items], total=int(total), limit=limit, offset=offset, recording=True, note=None)


@router.get("/strategies/{strategy_id}/runs", response_model=RunPage)
def list_runs(
    strategy_id: str, limit: int = Query(default=10, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> RunPage:
    """Backtest and walk-forward runs of one of your strategies, newest first. SIMULATED."""
    _read(context)
    with open_finance_store(context) as store:
        reader = getattr(store, "list_strategy_runs", None)
        if not callable(reader):
            return RunPage(items=[], total=0, limit=limit, offset=offset, recording=False, note=NOT_RECORDED.format(what="backtest or walk-forward runs"))
        items, total = reader(context, strategy_id[:80], limit=limit, offset=offset)
    return RunPage(items=[run_view(i) for i in items], total=int(total), limit=limit, offset=offset, recording=True, note=None)


# -- Supervisor and agent decisions ---------------------------------------------------------------------


class DecisionView(BaseModel):
    id: str
    agent: str
    action: str
    outcome: str | None
    reasons: list[str]
    strategy_id: str | None
    intent_id: str | None
    created_at: datetime | None
    simulated: bool = True


class DecisionPage(BaseModel):
    items: list[DecisionView]
    total: int
    limit: int
    offset: int
    recording: bool
    note: str | None


def decision_view(item: object) -> DecisionView:
    return DecisionView(
        id=str(_get(item, "id")), agent=_text(_get(item, "agent"), 60) or "supervisor", action=_text(_get(item, "action"), 80) or "decision",
        outcome=_text(_get(item, "outcome"), 40), reasons=_texts(_get(item, "reasons")), strategy_id=_text(_get(item, "strategy_id"), 80),
        intent_id=_text(_get(item, "intent_id"), 80), created_at=_get(item, "created_at"),
    )


@router.get("/decisions", response_model=DecisionPage)
def list_decisions(
    agent: str | None = Query(default=None, max_length=60, pattern=r"^[a-z_]+$"),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> DecisionPage:
    """What the Trading Supervisor and the specialist agents decided, with their reasons, newest first."""
    _read(context)
    with open_finance_store(context) as store:
        reader = getattr(store, "list_agent_decisions", None)
        if not callable(reader):
            return DecisionPage(items=[], total=0, limit=limit, offset=offset, recording=False, note=NOT_RECORDED.format(what="agent decisions"))
        items, total = reader(context, agent=agent, limit=limit, offset=offset)
    return DecisionPage(items=[decision_view(i) for i in items], total=int(total), limit=limit, offset=offset, recording=True, note=None)
