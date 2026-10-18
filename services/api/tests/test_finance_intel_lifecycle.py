"""The governed strategy lifecycle and the in-memory store for it (docs/finance-trading-intel.md#lifecycle)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anum_api.finance.intel import lifecycle
from anum_api.finance.intel.lifecycle import Criteria, LifecycleError
from anum_api.finance.intel.records import Agent, AgentDecision, StrategyRun, StrategyState
from anum_api.finance.store import InMemoryFinanceStore
from anum_api.schemas import TenantContext

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
ME = TenantContext(tenant_id="t1", workspace_id="w1", user_id="u1", roles=["member"])
OTHER = TenantContext(tenant_id="t1", workspace_id="w1", user_id="u2", roles=["owner"])
S = StrategyState


def _draft(key: str = "trend_sma", **kw: object):  # type: ignore[no-untyped-def]
    return lifecycle.create(ME, key=key, symbols=["AAA"], params={"fast": 20, "slow": 100}, name=None, reason="first idea",
                            actor=Agent.PERSON, now=NOW, **kw)  # type: ignore[arg-type]


def _run(strategy, *, kind: str = "walk_forward", verdict: str = "passed", reproduced: bool = True, folds: int = 4,  # type: ignore[no-untyped-def]
         drawdown: float = -0.2, at: datetime = NOW, benchmark: dict | None = None) -> StrategyRun:
    return StrategyRun(
        id=f"run_{at.timestamp()}_{kind}", tenant_id="t1", workspace_id="w1", owner_user_id="u1", strategy_id=strategy.id, kind=kind,  # type: ignore[arg-type]
        created_at=at, data_source="synthetic", data_hash="b" * 64, code_version=strategy.code_version, symbols=strategy.symbols,
        params=strategy.params, metrics={"sharpe": 0.5, "max_drawdown": drawdown}, verdict=verdict, folds=[{}] * folds,  # type: ignore[arg-type]
        reproduced=reproduced, benchmark=benchmark if benchmark is not None else {"sharpe": 0.3},
    )


def _to_validation(strategy):  # type: ignore[no-untyped-def]
    s = lifecycle.transition(strategy, S.BACKTESTING, actor=Agent.STRATEGY_RESEARCH, reason="start", now=NOW)
    return lifecycle.transition(s, S.VALIDATION, actor=Agent.STRATEGY_RESEARCH, reason="backtest done", now=NOW,
                                runs=[_run(s, kind="backtest", verdict="completed")])


def test_create_checks_the_rule_and_its_parameters() -> None:
    draft = _draft()
    assert draft.state is S.DRAFT and draft.version == 1 and draft.code_version == "1.0.0" and draft.params == {"fast": 20, "slow": 100}
    with pytest.raises(ValueError):
        lifecycle.create(ME, key="trend_sma", symbols=["AAA"], params={"fast": 300}, name=None, reason="bad", actor="person", now=NOW)
    with pytest.raises(LifecycleError, match="Say why"):
        lifecycle.create(ME, key="trend_sma", symbols=["AAA"], params=None, name=None, reason=" ", actor="person", now=NOW)
    with pytest.raises(LifecycleError, match="Unknown actor"):
        lifecycle.create(ME, key="trend_sma", symbols=["AAA"], params=None, name=None, reason="x y z", actor="broker_bot", now=NOW)


def test_agents_research_and_suspend_but_never_promote_resume_or_retire() -> None:
    v = _to_validation(_draft())
    assert v.state is S.VALIDATION and v.state_changed_by == Agent.STRATEGY_RESEARCH.value
    ready = lifecycle.attach_portfolio(v, "pf1", now=NOW)
    runs = [_run(ready)]
    with pytest.raises(LifecycleError) as refused:
        lifecycle.transition(ready, S.PAPER_ACTIVE, actor=Agent.SUPERVISOR, reason="looks good", now=NOW, runs=runs)
    assert refused.value.code == "person_only"
    active = lifecycle.transition(ready, S.PAPER_ACTIVE, actor=Agent.PERSON, reason="approved by me", now=NOW, runs=runs)
    assert lifecycle.can_trade(active) and active.state_changed_by == "person"
    suspended = lifecycle.transition(active, S.PAPER_SUSPENDED, actor=Agent.RISK_GUARDIAN, reason="stale data", now=NOW)
    assert not lifecycle.can_trade(suspended)
    for target in (S.PAPER_ACTIVE, S.RETIRED):
        with pytest.raises(LifecycleError, match="Only you"):
            lifecycle.transition(suspended, target, actor=Agent.PERFORMANCE_REVIEWER, reason="try", now=NOW, runs=runs)
    retired = lifecycle.transition(suspended, S.RETIRED, actor=Agent.PERSON, reason="done", now=NOW)
    with pytest.raises(LifecycleError, match="cannot move"):
        lifecycle.transition(retired, S.DRAFT, actor=Agent.PERSON, reason="again", now=NOW)


def test_live_candidate_is_not_a_state() -> None:
    with pytest.raises(LifecycleError) as refused:
        lifecycle.transition(_draft(), "LIVE_CANDIDATE", actor=Agent.PERSON, reason="go live", now=NOW)
    assert refused.value.code == "unknown_state" and "grants nothing" in str(refused.value)
    assert "LIVE" not in {s.value for s in S}


def test_validation_needs_a_backtest_first() -> None:
    s = lifecycle.transition(_draft(), S.BACKTESTING, actor=Agent.STRATEGY_RESEARCH, reason="start", now=NOW)
    with pytest.raises(LifecycleError, match="backtest"):
        lifecycle.transition(s, S.VALIDATION, actor=Agent.STRATEGY_RESEARCH, reason="skip", now=NOW, runs=[])


@pytest.mark.parametrize(
    ("run_kw", "portfolio", "problem"),
    [
        ({}, None, "no_portfolio"),
        ({"verdict": "rejected"}, "pf1", "validation_rejected"),
        ({"reproduced": False}, "pf1", "not_reproduced"),
        ({"folds": 2}, "pf1", "too_few_folds"),
        ({"drawdown": -0.5}, "pf1", "drawdown_over_limit"),
        ({"benchmark": {}}, "pf1", "no_benchmark"),
    ],
)
def test_promotion_criteria(run_kw: dict, portfolio: str | None, problem: str) -> None:
    v = _to_validation(_draft())
    if portfolio:
        v = lifecycle.attach_portfolio(v, portfolio, now=NOW)
    problems = lifecycle.promotion_problems(v, [_run(v, **run_kw)])
    assert problem in problems
    with pytest.raises(LifecycleError, match="Not ready"):
        lifecycle.transition(v, S.PAPER_ACTIVE, actor=Agent.PERSON, reason="try", now=NOW, runs=[_run(v, **run_kw)])


def test_positive_backtest_alone_never_promotes() -> None:
    v = lifecycle.attach_portfolio(_to_validation(_draft()), "pf1", now=NOW)
    only_backtest = [_run(v, kind="backtest", verdict="completed")]
    assert "no_validation_run" in lifecycle.promotion_problems(v, only_backtest)
    # The newest walk-forward run decides: an older pass does not outweigh a newer rejection.
    runs = [_run(v, at=NOW), _run(v, verdict="rejected", at=NOW + timedelta(hours=1))]
    assert "validation_rejected" in lifecycle.promotion_problems(v, runs)
    stricter = Criteria(min_folds=10)
    assert "too_few_folds" in lifecycle.promotion_problems(v, [_run(v)], stricter)


def test_changes_are_new_versions_with_reason_and_evidence() -> None:
    active = _draft()
    run = _run(active)
    with pytest.raises(LifecycleError, match="evaluation run that justifies"):
        lifecycle.new_version(active, latest_version=1, reason="tune after loss", actor=Agent.STRATEGY_RESEARCH, now=NOW, params={"fast": 30})
    other = _draft()
    with pytest.raises(LifecycleError, match="version being changed"):
        lifecycle.new_version(active, latest_version=1, reason="tune", actor=Agent.STRATEGY_RESEARCH, now=NOW, params={"fast": 30},
                              evidence=_run(other))
    with pytest.raises(LifecycleError, match="Nothing changed"):
        lifecycle.new_version(active, latest_version=1, reason="same", actor=Agent.PERSON, now=NOW)
    v2 = lifecycle.new_version(active, latest_version=1, reason="faster average", actor=Agent.STRATEGY_RESEARCH, now=NOW,
                               params={"fast": 30}, evidence=run)
    assert (v2.version, v2.state, v2.parent_id, v2.lineage_id, v2.evidence_run_id) == (2, S.DRAFT, active.id, active.lineage_id, run.id)
    assert v2.params == {"fast": 30, "slow": 100} and active.params == {"fast": 20, "slow": 100}  # the old version is untouched
    assert v2.portfolio_id is None


def test_memory_store_is_owner_only_and_pages() -> None:
    store = InMemoryFinanceStore()
    first = _draft()
    store.save_strategy(first)
    second = lifecycle.new_version(first, latest_version=1, reason="person change", actor="person", now=NOW + timedelta(minutes=1),
                                   params={"fast": 25})
    store.save_strategy(second)
    with pytest.raises(ValueError, match="duplicate_version"):
        store.save_strategy(second.model_copy(update={"id": "dup"}))
    items, total = store.list_strategies(ME, limit=1, offset=0)
    assert total == 2 and items[0].id == second.id
    assert store.latest_version(ME, first.lineage_id) == 2
    assert store.list_strategies(ME, state="DRAFT", limit=10, offset=0)[1] == 2
    assert store.list_strategies(OTHER, limit=10, offset=0) == ([], 0) and store.get_strategy(OTHER, first.id) is None
    store.save_strategy_run(_run(first))
    assert store.list_strategy_runs(ME, first.id, limit=10, offset=0)[1] == 1
    assert store.list_strategy_runs(OTHER, first.id, limit=10, offset=0)[1] == 0
    store.save_agent_decision(AgentDecision(id="d1", tenant_id="t1", workspace_id="w1", owner_user_id="u1", agent=Agent.RISK_GUARDIAN,
                                            action="check", outcome="refused", reasons=["stale_price"], created_at=NOW))
    assert store.list_agent_decisions(ME, agent="risk_guardian", limit=5, offset=0)[1] == 1
    assert store.list_agent_decisions(ME, agent="portfolio", limit=5, offset=0)[1] == 0
    assert store.count_everything(ME) == 4 and store.delete_everything(ME) == 4 and store.count_everything(ME) == 0
