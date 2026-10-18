"""Read-only routes for the trading intelligence dashboard under ``/api/v1/finance/intel``
(docs/finance-trading-intel.md#dashboard).

Everything here is SIMULATED or recorded research. These routes only read: they never create
an order, change a strategy, loosen a rule or touch a kill switch (the existing routes in
``trading_api`` do that, through the gateway and the Guardian). ``finance:read`` on every route,
and every record is the caller's own (the store reads in the owner's scope, under forced RLS in
PostgreSQL mode).

Strategies, their backtest and walk-forward runs, and the Supervisor's and specialist agents'
decisions come from the records of migration ``0065_finance_intel`` (``intel/records.py``). The
changes (lifecycle moves, runs, Supervisor cycles) live in ``intel_control_api``, not here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..dependencies import tenant_context
from ..schemas import TenantContext, utc_now
from .api import _not_found, _read
from .config import finance_config
from .execution import ExecMode
from .guardian import GuardianRules
from .intel.markets import VENUES, session
from .intel.records import Agent, AgentDecision, StrategyConfig, StrategyRun, StrategyState
from .policy import current_rules
from .store import MAX_PAGE, FinanceStore, open_finance_store
from .trading import SIMULATED_NOTE, MoneyRow, PortfolioMode, PositionView
from .trading_api import execution_status, portfolio_view

router = APIRouter(prefix="/api/v1/finance/intel", tags=["finance"])

SIMULATION_ONLY = (
    "Simulation only, no real orders. Paper and shadow trades use recorded prices and stated costs; "
    "live trading is off and cannot be switched on from ANUM."
)
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


class LifecycleCount(BaseModel):
    state: StrategyState
    count: int


class IntelOverview(BaseModel):
    simulation: SimulationView
    switches: SwitchState
    venues: list[VenueView]
    portfolios: list[PortfolioIntel]
    risk: RiskView
    strategies: list[LifecycleCount]
    pending_requests: int
    agent_requests: int
    note: str
    generated_at: datetime


def _lifecycle_counts(store: FinanceStore, context: TenantContext) -> list[LifecycleCount]:
    return [LifecycleCount(state=state, count=store.list_strategies(context, state=state.value, limit=1, offset=0)[1]) for state in StrategyState]


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
        lifecycle = _lifecycle_counts(store, context)
        agent_total = _agent_requests(store, context)
    return IntelOverview(
        simulation=SimulationView(simulation_only=True, live_enabled=finance_config.live_enabled,
                                  wider_live_enabled=finance_config.wider_live_enabled, note=SIMULATION_ONLY),
        switches=SwitchState(person_engaged=status.person_switch.engaged, global_engaged=status.global_switch.engaged,
                             can_change_global=status.global_switch.can_change),
        venues=_venues(now), portfolios=portfolios, risk=risk, strategies=lifecycle, pending_requests=status.pending, agent_requests=agent_total,
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
    lineage_id: str
    key: str
    code_version: str
    name: str
    version: int
    state: StrategyState
    symbols: list[str]
    params: dict[str, Any]
    reason: str
    portfolio_id: str | None
    parent_id: str | None
    evidence_run_id: str | None
    created_by: str
    state_changed_by: str
    state_changed_at: datetime
    created_at: datetime
    updated_at: datetime
    simulated: bool = True


class StrategyPage(BaseModel):
    items: list[StrategyView]
    total: int
    limit: int
    offset: int


class FoldView(BaseModel):
    index: int
    train_start: date | None
    train_end: date | None
    test_start: date | None
    test_end: date | None
    metrics: dict[str, float | None]


class RunView(BaseModel):
    id: str
    strategy_id: str
    kind: str
    created_at: datetime
    data_source: str
    data_hash: str
    code_version: str
    symbols: list[str]
    costs: dict[str, str]
    metrics: dict[str, float | None]
    benchmark: dict[str, float | None]
    verdict: str
    reasons: list[str]
    warnings: list[str]
    folds: list[FoldView]
    reproduced: bool
    requested_by: str
    # The reproducible evaluation report of a walk-forward run (intel/report.py); empty for a backtest.
    report: dict[str, Any] = Field(default_factory=dict)
    simulated: bool = True


class RunPage(BaseModel):
    items: list[RunView]
    total: int
    limit: int
    offset: int


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
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _span(fold: object, name: str) -> tuple[date | None, date | None]:
    """A fold's train or test window: ``{"train": [start, end]}`` or ``train_start`` / ``train_end``."""
    pair = _get(fold, name)
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        return _date(pair[0]), _date(pair[1])
    return _date(_get(fold, f"{name}_start")), _date(_get(fold, f"{name}_end"))


def strategy_view(item: StrategyConfig) -> StrategyView:
    return StrategyView(
        id=item.id, lineage_id=item.lineage_id, key=item.key[:60], code_version=item.code_version[:40], name=item.name[:120], version=item.version,
        state=item.state, symbols=[s[:20] for s in item.symbols[:20]], params=_params(item.params), reason=item.reason[:MAX_TEXT],
        portfolio_id=item.portfolio_id, parent_id=item.parent_id, evidence_run_id=item.evidence_run_id, created_by=item.created_by[:40],
        state_changed_by=item.state_changed_by[:40], state_changed_at=item.state_changed_at, created_at=item.created_at, updated_at=item.updated_at,
    )


def fold_view(index: int, fold: object) -> FoldView:
    train, test = _span(fold, "train"), _span(fold, "test")
    metrics = {k: v for k, v in _numbers(fold).items() if k != "index"}
    metrics.update(_numbers(_get(fold, "metrics")))
    raw = _get(fold, "index", index)
    return FoldView(index=raw if isinstance(raw, int) and not isinstance(raw, bool) else index, train_start=train[0], train_end=train[1],
                    test_start=test[0], test_end=test[1], metrics=metrics)


def run_view(item: StrategyRun) -> RunView:
    return RunView(
        id=item.id, strategy_id=item.strategy_id, kind=item.kind, created_at=item.created_at, data_source=item.data_source[:200],
        data_hash=item.data_hash[:80], code_version=item.code_version[:40], symbols=[s[:20] for s in item.symbols[:20]],
        costs={str(k)[:40]: str(v)[:40] for k, v in list(item.costs.items())[:20]}, metrics=_numbers(item.metrics), benchmark=_numbers(item.benchmark),
        verdict=item.verdict, reasons=_texts(item.reasons), warnings=_texts(item.warnings),
        folds=[fold_view(i, f) for i, f in enumerate(item.folds[:50])], reproduced=item.reproduced, requested_by=item.requested_by[:40],
        report=item.report if item.kind == "walk_forward" else {},
    )


@router.get("/strategies", response_model=StrategyPage)
def list_strategies(
    state: StrategyState | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> StrategyPage:
    """Your strategy versions with their lifecycle state, newest change first. SIMULATED."""
    _read(context)
    with open_finance_store(context) as store:
        items, total = store.list_strategies(context, state=state.value if state else None, limit=limit, offset=offset)
    return StrategyPage(items=[strategy_view(i) for i in items], total=total, limit=limit, offset=offset)


@router.get("/strategies/{strategy_id}/runs", response_model=RunPage)
def list_runs(
    strategy_id: str, limit: int = Query(default=10, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> RunPage:
    """Backtest and walk-forward runs of one of your strategy versions, newest first. SIMULATED."""
    _read(context)
    with open_finance_store(context) as store:
        if store.get_strategy(context, strategy_id[:80]) is None:
            raise _not_found("Strategy")
        items, total = store.list_strategy_runs(context, strategy_id[:80], limit=limit, offset=offset)
    return RunPage(items=[run_view(i) for i in items], total=total, limit=limit, offset=offset)


# -- Supervisor and agent decisions ---------------------------------------------------------------------


class DecisionView(BaseModel):
    id: str
    cycle_id: str | None
    agent: str
    action: str
    outcome: str
    reasons: list[str]
    strategy_id: str | None
    intent_id: str | None
    created_at: datetime
    simulated: bool = True


class DecisionPage(BaseModel):
    items: list[DecisionView]
    total: int
    limit: int
    offset: int


def decision_view(item: AgentDecision) -> DecisionView:
    return DecisionView(id=item.id, cycle_id=item.cycle_id, agent=item.agent.value, action=item.action[:60], outcome=item.outcome[:30],
                        reasons=_texts(item.reasons), strategy_id=item.strategy_id, intent_id=item.intent_id, created_at=item.created_at)


@router.get("/decisions", response_model=DecisionPage)
def list_decisions(
    agent: Agent | None = Query(default=None), strategy_id: str | None = Query(default=None, max_length=80),
    cycle_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0, le=100_000),
    context: TenantContext = Depends(tenant_context),
) -> DecisionPage:
    """What the Trading Supervisor, the specialist agents and you decided, with the reasons, newest first.
    Recorded text is shown as data and never followed."""
    _read(context)
    with open_finance_store(context) as store:
        items, total = store.list_agent_decisions(context, agent=agent.value if agent else None, strategy_id=strategy_id, cycle_id=cycle_id,
                                                  limit=limit, offset=offset)
    return DecisionPage(items=[decision_view(i) for i in items], total=total, limit=limit, offset=offset)
