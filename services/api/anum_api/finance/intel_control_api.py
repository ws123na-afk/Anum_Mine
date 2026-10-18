"""Write routes for trading intelligence under ``/api/v1/finance/intel`` (docs/finance-trading-intel.md#api).

The person's own actions: instruments and bar imports, strategy versions, lifecycle moves,
evaluations, a Supervisor cycle on demand and the emergency pause. The read routes are in
``intel_api.py``. ``finance:manage`` for every route here; every record is the caller's own.
Audit records carry ids, states, phases and codes only: never a symbol, price or amount.

Everything is SIMULATED research. Nothing here can approve an order or trade live: orders a
cycle proposes wait in the execution gateway for the person's hash-bound approval.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .. import automation
from ..scoped_store import ScopeNotProvisionedError
from .api import _audit, _clean, _invalid, _manage, _not_found, _read
from .intel import lifecycle, research
from .intel import schedules as intel_schedules
from .intel.marketdata import (
    PROVIDERS,
    SYMBOL,
    AssetClass,
    Instrument,
    MarketDataError,
    StoredCandle,
    check_series,
    normalize_symbol,
    parse_provider,
)
from .intel.markets import VENUES
from .intel.records import MAX_INSTRUMENTS, MAX_STRATEGIES, Agent, StrategyConfig, StrategyRun, StrategyState
from .intel.strategies import StrategyError
from .intel.supervisor import PHASES, Supervisor, pause_all
from .store import FinanceStore, open_finance_store
from .trading import PortfolioMode

router = APIRouter(prefix="/api/v1/finance/intel", tags=["finance"])


def _lifecycle_error(exc: lifecycle.LifecycleError) -> HTTPException:
    code = status.HTTP_403_FORBIDDEN if exc.code == "person_only" else status.HTTP_409_CONFLICT
    return HTTPException(code, detail=str(exc))


def _own(store: FinanceStore, context: TenantContext, strategy_id: str) -> StrategyConfig:
    found = store.get_strategy(context, strategy_id)
    if found is None:
        raise _not_found("Strategy")
    return found


# -- instruments and bars ----------------------------------------------------------------------


class InstrumentInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    asset_class: AssetClass
    venue: str
    currency: str = Field(min_length=3, max_length=10)
    name: str | None = Field(default=None, max_length=120)
    listed_on: date | None = None
    delisted_on: date | None = None


@router.post("/instruments", response_model=Instrument, status_code=status.HTTP_201_CREATED)
def create_instrument(body: InstrumentInput, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> Instrument:
    _manage(context)
    try:
        symbol = normalize_symbol(body.symbol)
    except MarketDataError as exc:
        raise _invalid(str(exc)) from None
    if "/" in symbol:
        raise _invalid("Use the base asset as the symbol and the quote asset as the currency (BTC with USDT, not BTC/USDT).")
    if body.venue not in VENUES:
        raise _invalid(f"Unknown venue: use one of {', '.join(sorted(VENUES))}.")
    currency = body.currency.strip().upper()
    if not SYMBOL.match(currency):
        raise _invalid("The currency is a code such as USD, AED or USDT.")
    if body.listed_on and body.delisted_on and body.delisted_on < body.listed_on:
        raise _invalid("The delisting date is before the listing date.")
    instrument = Instrument(
        id=new_id("fin_ins"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        symbol=symbol, name=_clean(body.name, 120), asset_class=body.asset_class, venue=body.venue, currency=currency,
        listed_on=body.listed_on, delisted_on=body.delisted_on, created_at=utc_now(),
    )
    with open_finance_store(context) as store:
        if len(store.list_instruments(context)) >= MAX_INSTRUMENTS:
            raise HTTPException(status.HTTP_409_CONFLICT, f"You can keep up to {MAX_INSTRUMENTS} instruments. Delete one first.")
        try:
            store.save_instrument(instrument)
        except ValueError:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already have this symbol on this venue.") from None
    _audit(repository, context, "finance.intel.instrument.create", instrument.id, {"asset_class": instrument.asset_class.value})
    return instrument


@router.delete("/instruments/{instrument_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instrument(instrument_id: str, context: TenantContext = Depends(tenant_context),
                      repository: AnumRepository = Depends(repository_context)) -> None:
    _manage(context)
    with open_finance_store(context) as store:
        in_use = [s.id for s in store.list_strategies(context, limit=MAX_STRATEGIES, offset=0)[0]
                  if instrument_id in s.instrument_ids and s.state not in {StrategyState.RETIRED, StrategyState.DRAFT}]
        if in_use:
            raise HTTPException(status.HTTP_409_CONFLICT, "A strategy uses this instrument. Retire it first.")
        if not store.delete_instrument(context, instrument_id):
            raise _not_found("Instrument")
    _audit(repository, context, "finance.intel.instrument.delete", instrument_id)


class BarsInput(BaseModel):
    provider: Literal["csv_import", "stooq", "binance_public"] = "csv_import"
    payload: str = Field(min_length=1, max_length=2_000_000)
    source: str | None = Field(default=None, max_length=100)  # where the file came from, shown with every bar


class BarsResult(BaseModel):
    imported: int
    changed: int
    first: date | None
    last: date | None
    issues: list[dict[str, Any]]


@router.post("/instruments/{instrument_id}/bars", response_model=BarsResult)
def import_bars(instrument_id: str, body: BarsInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> BarsResult:
    """Daily bars from a file or a recorded provider response. Corrupt or future bars refuse the import."""
    _manage(context)
    now = utc_now()
    try:
        candles = parse_provider(body.provider, body.payload)
    except MarketDataError as exc:
        raise _invalid(str(exc)) from None
    with open_finance_store(context) as store:
        instrument = store.get_instrument(context, instrument_id)
        if instrument is None:
            raise _not_found("Instrument")
        issues = check_series(candles, instrument.asset_class, instrument.venue, now.date())
        blocking = [i for i in issues if i.blocking]
        if blocking:
            raise _invalid(f"{len(blocking)} bar(s) cannot be imported: " + "; ".join(f"{i.day}: {i.message}" for i in blocking[:5]))
        info = PROVIDERS[body.provider]
        label = (_clean(body.source, 100) or info.name)[:120]
        rows = [
            StoredCandle(id=new_id("fin_bar"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                         instrument_id=instrument.id, day=c.day, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
                         source=label, provider=body.provider, delayed=info.delayed, fetched_at=now)
            for c in candles
        ]
        changed = store.save_bars(rows)
    _audit(repository, context, "finance.intel.bars.import", instrument_id, {"provider": body.provider, "bars": len(rows), "changed": changed})
    return BarsResult(imported=len(rows), changed=changed, first=candles[0].day if candles else None, last=candles[-1].day if candles else None,
                      issues=[{"code": i.code, "day": i.day.isoformat(), "message": i.message} for i in issues])


# -- strategies --------------------------------------------------------------------------------


class StrategyInput(BaseModel):
    key: str
    instrument_ids: list[str] = Field(min_length=1, max_length=10)
    params: dict[str, Any] | None = None
    name: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=3, max_length=300)


def _symbols(store: FinanceStore, context: TenantContext, instrument_ids: list[str]) -> list[str]:
    symbols: list[str] = []
    for instrument_id in instrument_ids:
        instrument = store.get_instrument(context, instrument_id)
        if instrument is None:
            raise _not_found("Instrument")
        symbols.append(instrument.symbol)
    if len(set(symbols)) != len(symbols):
        raise _invalid("Each symbol once.")
    return symbols


@router.post("/strategies", response_model=StrategyConfig, status_code=status.HTTP_201_CREATED)
def create_strategy(body: StrategyInput, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> StrategyConfig:
    _manage(context)
    with open_finance_store(context) as store:
        if store.list_strategies(context, limit=1, offset=0)[1] >= MAX_STRATEGIES:
            raise HTTPException(status.HTTP_409_CONFLICT, f"You can keep up to {MAX_STRATEGIES} strategy versions. Retire and delete some first.")
        symbols = _symbols(store, context, body.instrument_ids)
        try:
            strategy = lifecycle.create(context, key=body.key, symbols=symbols, params=body.params, name=_clean(body.name, 120),
                                        reason=body.reason, actor=Agent.PERSON, now=utc_now(), instrument_ids=body.instrument_ids)
        except (StrategyError, lifecycle.LifecycleError) as exc:
            raise _invalid(str(exc)) from None
        store.save_strategy(strategy)
    _audit(repository, context, "finance.intel.strategy.create", strategy.id, {"key": strategy.key, "version": 1})
    return strategy


class VersionInput(BaseModel):
    params: dict[str, Any] | None = None
    instrument_ids: list[str] | None = Field(default=None, min_length=1, max_length=10)
    reason: str = Field(min_length=3, max_length=300)


@router.post("/strategies/{strategy_id}/versions", response_model=StrategyConfig, status_code=status.HTTP_201_CREATED)
def new_version(strategy_id: str, body: VersionInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> StrategyConfig:
    """A changed copy as the next version (DRAFT). The current version is never edited."""
    _manage(context)
    with open_finance_store(context) as store:
        current = _own(store, context, strategy_id)
        symbols = _symbols(store, context, body.instrument_ids) if body.instrument_ids else None
        try:
            version = lifecycle.new_version(current, latest_version=store.latest_version(context, current.lineage_id), reason=body.reason,
                                            actor=Agent.PERSON, now=utc_now(), params=body.params, symbols=symbols)
        except StrategyError as exc:
            raise _invalid(str(exc)) from None
        except lifecycle.LifecycleError as exc:
            raise _lifecycle_error(exc) from None
        if body.instrument_ids:
            version = version.model_copy(update={"instrument_ids": body.instrument_ids})
        store.save_strategy(version)
    _audit(repository, context, "finance.intel.strategy.version", version.id, {"parent": current.id, "version": version.version})
    return version


class TransitionInput(BaseModel):
    state: str
    reason: str = Field(min_length=3, max_length=300)


@router.post("/strategies/{strategy_id}/transition", response_model=StrategyConfig)
def move_strategy(strategy_id: str, body: TransitionInput, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> StrategyConfig:
    _manage(context)
    with open_finance_store(context) as store:
        current = _own(store, context, strategy_id)
        runs = store.list_strategy_runs(context, strategy_id, limit=200, offset=0)[0]
        try:
            moved = lifecycle.transition(current, body.state, actor=Agent.PERSON, reason=body.reason, now=utc_now(), runs=runs)
        except lifecycle.LifecycleError as exc:
            raise _lifecycle_error(exc) from None
        store.save_strategy(moved)
        store.save_agent_decision(_person_decision(context, "transition", moved, {"from": current.state.value, "to": moved.state.value}))
    _audit(repository, context, "finance.intel.strategy.transition", strategy_id, {"from": current.state.value, "to": moved.state.value})
    return moved


def _person_decision(context: TenantContext, action: str, strategy: StrategyConfig | None, details: dict[str, Any]) -> Any:
    from .intel.records import AgentDecision

    return AgentDecision(id=new_id("fin_dec"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                         agent=Agent.PERSON, action=action, outcome="ok", strategy_id=strategy.id if strategy else None,
                         details={"simulated": True, **details}, created_at=utc_now())


class PortfolioInput(BaseModel):
    portfolio_id: str


@router.post("/strategies/{strategy_id}/portfolio", response_model=StrategyConfig)
def choose_portfolio(strategy_id: str, body: PortfolioInput, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context)) -> StrategyConfig:
    _manage(context)
    with open_finance_store(context) as store:
        current = _own(store, context, strategy_id)
        portfolio = store.get_portfolio(context, body.portfolio_id)
        if portfolio is None:
            raise _not_found("Portfolio")
        if portfolio.mode != PortfolioMode.PAPER:
            raise _invalid("Choose a paper portfolio: strategies trade only with simulated cash.")
        try:
            updated = lifecycle.attach_portfolio(current, portfolio.id, now=utc_now())
        except lifecycle.LifecycleError as exc:
            raise _lifecycle_error(exc) from None
        store.save_strategy(updated)
    _audit(repository, context, "finance.intel.strategy.portfolio", strategy_id, {"portfolio": portfolio.id})
    return updated


class EvaluateInput(BaseModel):
    kind: Literal["backtest", "walk_forward"] = "walk_forward"


@router.post("/strategies/{strategy_id}/evaluate", response_model=StrategyRun, status_code=status.HTTP_201_CREATED)
def evaluate(strategy_id: str, body: EvaluateInput, context: TenantContext = Depends(tenant_context),
             repository: AnumRepository = Depends(repository_context)) -> StrategyRun:
    """A SIMULATED backtest or walk-forward run of this version on its stored bars."""
    _manage(context)
    with open_finance_store(context) as store:
        strategy = _own(store, context, strategy_id)
        if store.list_strategy_runs(context, strategy_id, limit=1, offset=0)[1] >= 200:
            raise HTTPException(status.HTTP_409_CONFLICT, "This version has 200 runs. Make a new version to keep researching.")
        try:
            run = research.evaluate(store, context, strategy, kind=body.kind, actor=Agent.PERSON, now=utc_now())
        except research.ResearchError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    _audit(repository, context, "finance.intel.strategy.evaluate", strategy_id, {"kind": run.kind, "verdict": run.verdict, "run": run.id})
    return run


# -- the Supervisor -----------------------------------------------------------------------------


class CycleInput(BaseModel):
    phase: str


@router.post("/supervisor/run")
def run_cycle(body: CycleInput, context: TenantContext = Depends(tenant_context),
              repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """One Supervisor cycle now (the scheduler runs the same thing). Runs once per phase and day."""
    _manage(context)
    if body.phase not in PHASES:
        raise _invalid(f"Unknown phase: use one of {', '.join(PHASES)}.")
    with open_finance_store(context) as store:
        result = Supervisor(store, context, utc_now()).run(body.phase)
    _audit(repository, context, "finance.intel.supervisor.run", result["cycle_id"], {
        "phase": body.phase, "skipped": result["skipped"], "proposed": result.get("proposed_orders", 0), "suspended": result.get("suspended", 0),
    })
    return result


class PauseInput(BaseModel):
    reason: str = Field(default="Emergency pause", min_length=3, max_length=300)


@router.post("/pause-all")
def pause_everything(body: PauseInput, context: TenantContext = Depends(tenant_context),
                     repository: AnumRepository = Depends(repository_context)) -> dict[str, Any]:
    """Suspend every active paper strategy at once. Resume each one yourself when ready."""
    _manage(context)
    with open_finance_store(context) as store:
        paused = pause_all(store, context, now=utc_now(), reason=body.reason)
    _audit(repository, context, "finance.intel.pause_all", context.user_id, {"paused": len(paused)})
    return {"paused": [s.id for s in paused], "simulated": True}


# -- scheduled operations -----------------------------------------------------------------------

WEEKDAYS = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")


class ScheduleInput(BaseModel):
    phase: str
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Asia/Dubai", min_length=1, max_length=80)
    weekday: int = Field(default=5, ge=0, le=6, description="0 = Sunday; weekly only")

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        from .. import cron

        try:
            cron.zone(value)
        except cron.CronError as exc:
            raise ValueError(str(exc)) from None
        return value

    def at(self) -> str:
        return self.time or intel_schedules.DEFAULT_TIMES.get(self.phase, "08:30")

    def cron_expression(self) -> str:
        hour, minute = (int(part) for part in self.at().split(":"))
        if self.phase == "weekly":
            return f"{minute} {hour} * * {self.weekday}"
        if self.phase == "monthly":
            return f"{minute} {hour} 1 * *"
        return f"{minute} {hour} * * *"

    def describe(self) -> str:
        if self.phase == "weekly":
            return f"Every {WEEKDAYS[self.weekday].capitalize()} at {self.at()}"
        if self.phase == "monthly":
            return f"Monthly on the 1st at {self.at()}"
        return f"Daily at {self.at()} (skipped on days the venue does not trade)"


class ScheduleView(BaseModel):
    id: str
    workflow_id: str
    phase: str
    description: str
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None


def _schedules(context: TenantContext) -> list[ScheduleView]:
    engine = automation.current_engine()
    workflows = {w.id: w for w in engine.list_workflows(context)}
    views: list[ScheduleView] = []
    for schedule in engine.list_schedules(context):
        workflow = workflows.get(schedule.workflow_id)
        step = next((x for x in workflow.steps if x.action == intel_schedules.STEP_ACTION), None) if workflow else None
        if step is None or schedule.created_by != context.user_id:  # finance is personal: only your own schedules
            continue
        views.append(ScheduleView(
            id=schedule.id, workflow_id=schedule.workflow_id, phase=str(step.input.get("phase")),
            description=str(step.input.get("description", "")), cron=schedule.cron, timezone=schedule.timezone,
            enabled=schedule.enabled, next_run_at=schedule.next_run_at, last_run_at=schedule.last_run_at,
        ))
    return views


@router.get("/schedules", response_model=list[ScheduleView])
def list_schedules(context: TenantContext = Depends(tenant_context)) -> list[ScheduleView]:
    """Your scheduled Supervisor cycles."""
    _read(context)
    return _schedules(context)


@router.post("/schedules", response_model=ScheduleView, status_code=status.HTTP_201_CREATED)
def create_schedule(body: ScheduleInput, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> ScheduleView:
    """Run one Supervisor phase on a schedule, as you. One schedule per phase."""
    _manage(context)
    problem = intel_schedules.check_step_input({"phase": body.phase})
    if problem:
        raise _invalid(problem[0].upper() + problem[1:])
    if any(view.phase == body.phase for view in _schedules(context)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"You already schedule the {body.phase} cycle. Delete that schedule first.")
    engine = automation.current_engine()
    try:
        workflow = engine.create_workflow(context, automation.WorkflowCreate(
            name=f"Trading intelligence: {body.phase} cycle (SIMULATED)",
            description="Created from Finance > Trading intelligence. Runs one SIMULATED Supervisor cycle; orders still wait for your approval.",
            steps=[automation.WorkflowStep(id="cycle", name=f"{body.phase} cycle", action=intel_schedules.STEP_ACTION,
                                           input={"phase": body.phase, "description": body.describe()}, max_attempts=1)],
        ))
        schedule = engine.create_schedule(context, automation.ScheduleCreate(
            workflow_id=workflow.id, name=f"Trading intelligence {body.phase} - {body.describe()}"[:160], cron=body.cron_expression(),
            timezone=body.timezone,
        ))
    except ScopeNotProvisionedError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Complete onboarding for this workspace first.") from None
    except (KeyError, ValueError) as exc:
        raise _invalid(str(exc)) from None
    _audit(repository, context, "finance.intel.schedule.create", schedule.id, {"phase": body.phase})
    return next(view for view in _schedules(context) if view.id == schedule.id)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> Response:
    _manage(context)
    view = next((v for v in _schedules(context) if v.id == schedule_id), None)
    if view is None:
        raise _not_found("Schedule")
    engine = automation.current_engine()
    engine.delete_schedule(context, schedule_id)
    try:
        engine.delete_workflow(context, view.workflow_id)
    except (KeyError, ValueError, AttributeError):
        pass  # the schedule is gone; an orphaned workflow without a schedule never fires
    _audit(repository, context, "finance.intel.schedule.delete", schedule_id, {"phase": view.phase})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
