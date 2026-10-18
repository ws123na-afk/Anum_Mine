"""The governed strategy lifecycle (docs/finance-trading-intel.md#lifecycle).

    DRAFT -> BACKTESTING -> VALIDATION -> PAPER_ACTIVE <-> PAPER_SUSPENDED -> RETIRED

Rules, enforced here and nowhere else:

* **Who.** Agents may start research (DRAFT to BACKTESTING, BACKTESTING to VALIDATION),
  send a version back a step, and **suspend** a PAPER_ACTIVE version (tightening is always
  allowed). Only the person promotes to PAPER_ACTIVE, resumes a suspended version and
  retires a version. No agent can make a strategy trade.
* **Promotion.** PAPER_ACTIVE needs the configured ``Criteria`` on this exact version: the
  newest walk-forward run passed, it was reproduced (a second run on the same bars gave the
  same data hash and metrics), enough folds, a drawdown inside the limit, and a paper
  portfolio to trade in. A positive backtest return alone never promotes.
* **Versions.** A version's rule, symbols and parameters never change after DRAFT. A change
  is ``new_version``: a new DRAFT in the same lineage with a reason, and for an agent the
  evaluation run that justifies it; the old version keeps its state and history. So a loss
  can never be "fixed" by silently rewriting the strategy that made it.
* **LIVE_CANDIDATE** is a documented future state only. It is not a ``StrategyState``, and
  nothing here grants any trading permission beyond SIMULATED paper orders through the
  existing Guardian gate and execution gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ...schemas import TenantContext, new_id
from .records import MAX_REASON, Agent, StrategyConfig, StrategyRun, StrategyState
from .strategies import check_params, spec

S = StrategyState

TRANSITIONS: dict[StrategyState, frozenset[StrategyState]] = {
    S.DRAFT: frozenset({S.BACKTESTING, S.RETIRED}),
    S.BACKTESTING: frozenset({S.VALIDATION, S.DRAFT, S.RETIRED}),
    S.VALIDATION: frozenset({S.PAPER_ACTIVE, S.BACKTESTING, S.RETIRED}),
    S.PAPER_ACTIVE: frozenset({S.PAPER_SUSPENDED, S.RETIRED}),
    S.PAPER_SUSPENDED: frozenset({S.PAPER_ACTIVE, S.RETIRED}),
    S.RETIRED: frozenset(),
}
# Moves an agent may make by itself. Everything else needs the person.
AGENT_MOVES: frozenset[tuple[StrategyState, StrategyState]] = frozenset({
    (S.DRAFT, S.BACKTESTING), (S.BACKTESTING, S.VALIDATION), (S.BACKTESTING, S.DRAFT), (S.VALIDATION, S.BACKTESTING),
    (S.PAPER_ACTIVE, S.PAPER_SUSPENDED),
})
PERSON_ONLY = frozenset({S.PAPER_ACTIVE, S.RETIRED})


class LifecycleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Criteria:
    """What a version must show before it may trade on paper. Stricter values are allowed."""

    min_folds: int = 3
    max_drawdown: float = -0.35  # the out-of-sample drawdown may not be worse than this
    require_benchmark_comparison: bool = True

    def describe(self) -> dict[str, Any]:
        return dict(self.__dict__)


DEFAULT_CRITERIA = Criteria()


def _reason(text: str | None) -> str:
    value = " ".join((text or "").split())
    if len(value) < 3:
        raise LifecycleError("reason_required", "Say why, in a few words.")
    return value[:MAX_REASON]


def _actor_value(actor: Agent | str) -> Agent:
    try:
        return Agent(actor)
    except ValueError:
        raise LifecycleError("unknown_actor", f"Unknown actor {actor!r}.") from None


def create(context: TenantContext, *, key: str, symbols: list[str], params: dict[str, Any] | None, name: str | None,
           reason: str, actor: Agent | str, now: datetime, instrument_ids: list[str] | None = None) -> StrategyConfig:
    """Version 1 of a new strategy, in DRAFT. Parameters are checked against the rule."""
    who = _actor_value(actor)
    rule = spec(key)
    merged = check_params(key, params, tuple(symbols))
    lineage = new_id("fin_strl")
    return StrategyConfig(
        id=new_id("fin_str"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        lineage_id=lineage, version=1, key=key, code_version=rule.version, name=(name or rule.name)[:120], state=S.DRAFT,
        symbols=list(symbols), params=merged, instrument_ids=list(instrument_ids or []), reason=_reason(reason),
        created_by=who.value, created_at=now, updated_at=now, state_changed_at=now, state_changed_by=who.value,
    )


def new_version(current: StrategyConfig, *, latest_version: int, reason: str, actor: Agent | str, now: datetime,
                params: dict[str, Any] | None = None, symbols: list[str] | None = None,
                evidence: StrategyRun | None = None) -> StrategyConfig:
    """A changed copy as the next version in the lineage, in DRAFT. ``current`` is not touched.

    An agent's proposal must name the evaluation run that justifies it (on ``current``).
    """
    who = _actor_value(actor)
    if who != Agent.PERSON:
        if evidence is None:
            raise LifecycleError("evidence_required", "An agent's change needs the evaluation run that justifies it.")
        if evidence.strategy_id != current.id:
            raise LifecycleError("evidence_mismatch", "The evidence must be a run of the version being changed.")
    new_symbols = list(symbols) if symbols is not None else list(current.symbols)
    merged = check_params(current.key, {**current.params, **(params or {})}, tuple(new_symbols))
    if merged == current.params and new_symbols == current.symbols and spec(current.key).version == current.code_version:
        raise LifecycleError("no_change", "Nothing changed: a new version needs different parameters, symbols or rule code.")
    return current.model_copy(update={
        "id": new_id("fin_str"), "version": latest_version + 1, "code_version": spec(current.key).version, "state": S.DRAFT,
        "symbols": new_symbols, "params": merged, "reason": _reason(reason), "parent_id": current.id,
        "evidence_run_id": evidence.id if evidence else None, "created_by": who.value, "created_at": now, "updated_at": now,
        "state_changed_at": now, "state_changed_by": who.value, "portfolio_id": None,
    })


def promotion_problems(strategy: StrategyConfig, runs: list[StrategyRun], criteria: Criteria = DEFAULT_CRITERIA) -> list[str]:
    """Why this version may not trade on paper yet (empty when it may). ``runs`` are its own runs."""
    problems: list[str] = []
    own = sorted((r for r in runs if r.strategy_id == strategy.id and r.kind == "walk_forward"), key=lambda r: (r.created_at, r.id))
    if strategy.portfolio_id is None:
        problems.append("no_portfolio")
    if not own:
        return [*problems, "no_validation_run"]
    latest = own[-1]
    if latest.code_version != strategy.code_version or latest.params != strategy.params or latest.symbols != strategy.symbols:
        problems.append("run_is_for_other_settings")
    if latest.verdict != "passed":
        problems.append(f"validation_{latest.verdict}")
    if not latest.reproduced:
        problems.append("not_reproduced")
    if len(latest.folds) < criteria.min_folds:
        problems.append("too_few_folds")
    drawdown = latest.metrics.get("max_drawdown")
    if not isinstance(drawdown, (int, float)) or drawdown < criteria.max_drawdown:
        problems.append("drawdown_over_limit")
    if criteria.require_benchmark_comparison and not latest.benchmark:
        problems.append("no_benchmark")
    return problems


def transition(strategy: StrategyConfig, target: StrategyState | str, *, actor: Agent | str, reason: str, now: datetime,
               runs: list[StrategyRun] | None = None, criteria: Criteria = DEFAULT_CRITERIA) -> StrategyConfig:
    """The strategy in ``target``, or ``LifecycleError`` saying why not. Returns a new object."""
    who = _actor_value(actor)
    try:
        goal = StrategyState(target)
    except ValueError:
        raise LifecycleError("unknown_state", f"Unknown state {target!r}. LIVE_CANDIDATE is documented only and grants nothing.") from None
    if goal not in TRANSITIONS[strategy.state]:
        raise LifecycleError("not_allowed", f"A {strategy.state.value} strategy cannot move to {goal.value}.")
    if who != Agent.PERSON and (strategy.state, goal) not in AGENT_MOVES:
        raise LifecycleError("person_only", f"Only you can move a strategy from {strategy.state.value} to {goal.value}.")
    if goal == S.VALIDATION and not any(r.strategy_id == strategy.id for r in runs or []):
        raise LifecycleError("no_backtest", "Run a backtest of this version before validation.")
    if goal == S.PAPER_ACTIVE:
        problems = promotion_problems(strategy, runs or [], criteria)
        if problems:
            raise LifecycleError("criteria_not_met", "Not ready for paper trading: " + ", ".join(problems) + ".")
    return strategy.model_copy(update={"state": goal, "reason": _reason(reason), "updated_at": now, "state_changed_at": now,
                                       "state_changed_by": who.value})


def can_trade(strategy: StrategyConfig) -> bool:
    """Only a PAPER_ACTIVE version may propose SIMULATED orders, and only to its paper portfolio."""
    return strategy.state == S.PAPER_ACTIVE and strategy.portfolio_id is not None


def attach_portfolio(strategy: StrategyConfig, portfolio_id: str, *, now: datetime) -> StrategyConfig:
    if strategy.state not in {S.DRAFT, S.BACKTESTING, S.VALIDATION, S.PAPER_SUSPENDED}:
        raise LifecycleError("not_allowed", "Choose the paper portfolio before the strategy is active.")
    return strategy.model_copy(update={"portfolio_id": portfolio_id, "updated_at": now})
