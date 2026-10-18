"""The Trading Supervisor, its agents, evaluation reports and the six candidates
(docs/finance-trading-intel.md#trading-supervisor). In memory; orders go through the real
execution gateway and Guardian, and every one waits for the person's approval.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from anum_api.finance.execution import ExecSettings, IntentStatus
from anum_api.finance.intel import lifecycle, research
from anum_api.finance.intel.marketdata import AssetClass, Instrument, StoredCandle, synthetic_bars
from anum_api.finance.intel.records import Agent, StrategyState
from anum_api.finance.intel.report import DISCLAIMER, evaluation_report, report_markdown
from anum_api.finance.intel.strategies import CANDIDATES, REGISTRY, by_candidate, check_params
from anum_api.finance.intel.supervisor import RiskLimits, Supervisor, cycle_key, pause_all
from anum_api.finance.intel.validation import walk_forward
from anum_api.finance.store import InMemoryFinanceStore
from anum_api.finance.trading import CashLine, Portfolio, PortfolioMode
from anum_api.schemas import TenantContext

ME = TenantContext(tenant_id="t1", workspace_id="w1", user_id="u1", roles=["member"])
OTHER = TenantContext(tenant_id="t1", workspace_id="w1", user_id="u2", roles=["member"])
BARS = synthetic_bars(12, date(2023, 1, 2), 900, drift=0.0008)  # a seed on which buy and hold passes validation
LAST = BARS[-1].day
NOW = datetime.combine(LAST, time(22, 0), UTC)  # after the New York close of the last bar


def _store_with_data(context: TenantContext = ME, bars: list = BARS) -> tuple[InMemoryFinanceStore, Instrument, Portfolio]:
    store = InMemoryFinanceStore()
    instrument = Instrument(id="ins_aaa", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                            symbol="AAA", asset_class=AssetClass.ETF, venue="XNYS", currency="USD", listed_on=date(2000, 1, 1), created_at=NOW)
    store.save_instrument(instrument)
    store.save_bars([
        StoredCandle(id=f"bar_{c.day}", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                     instrument_id=instrument.id, day=c.day, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
                     source="synthetic: generated test data, not market data", provider="csv_import", fetched_at=NOW)
        for c in bars
    ])
    portfolio = Portfolio(id="pf1", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                          name="Research", mode=PortfolioMode.PAPER, starting_cash=[CashLine(currency="USD", amount=Decimal("10000"))],
                          created_at=NOW, updated_at=NOW)
    store.save_portfolio(portfolio)
    return store, instrument, portfolio


def _active(store: InMemoryFinanceStore, key: str = "buy_and_hold", params: dict | None = None):  # type: ignore[no-untyped-def]
    s = lifecycle.create(ME, key=key, symbols=["AAA"], params=params, name=None, reason="research", actor=Agent.PERSON, now=NOW,
                         instrument_ids=["ins_aaa"])
    s = lifecycle.transition(s, StrategyState.BACKTESTING, actor=Agent.STRATEGY_RESEARCH, reason="start", now=NOW)
    store.save_strategy(s)
    backtest = research.evaluate(store, ME, s, kind="backtest", actor=Agent.STRATEGY_RESEARCH, now=NOW)
    s = lifecycle.transition(s, StrategyState.VALIDATION, actor=Agent.STRATEGY_RESEARCH, reason="backtest done", now=NOW, runs=[backtest])
    s = lifecycle.attach_portfolio(s, "pf1", now=NOW)
    store.save_strategy(s)
    wf = research.evaluate(store, ME, s, kind="walk_forward", actor=Agent.STRATEGY_RESEARCH, now=NOW)
    assert wf.reproduced and wf.data_hash == backtest.data_hash
    runs = store.list_strategy_runs(ME, s.id, limit=10, offset=0)[0]
    s = lifecycle.transition(s, StrategyState.PAPER_ACTIVE, actor=Agent.PERSON, reason="I approve paper trading",
                             now=NOW - timedelta(days=30), runs=runs)
    store.save_strategy(s)
    return s


# -- the six candidates and reports ---------------------------------------------------------------


def test_six_research_candidates_each_have_rules_and_a_hypothesis() -> None:
    groups = by_candidate()
    assert sorted(groups) == [1, 2, 3, 4, 5, 6] and all(groups[n] for n in groups) and len(CANDIDATES) == 6
    for key, item in REGISTRY.items():
        assert item.hypothesis and item.entry and item.exit and item.instruments, key
        assert "profit" not in (item.hypothesis + item.description).lower(), key
    assert REGISTRY["crypto_trend"].candidate == 6 and "Freqtrade not adopted" in CANDIDATES[6]
    with pytest.raises(ValueError):
        check_params("trend_vol", {"target_vol": 5}, ("A",))
    with pytest.raises(ValueError):
        check_params("crypto_trend", {"max_rsi": 40}, ("A",))


def test_report_is_reproducible_and_never_says_profitable() -> None:
    v1 = walk_forward({"AAA": BARS}, "trend_vol")
    v2 = walk_forward({"AAA": BARS}, "trend_vol")
    r1, r2 = evaluation_report(v1, data_source="csv"), evaluation_report(v2, data_source="csv")
    assert r1 == r2 and r1["label"] == "SIMULATED" and r1["disclaimer"] == DISCLAIMER
    assert r1["worst_window"] is not None and r1["worst_drawdown"] == r1["out_of_sample"]["max_drawdown"]
    text = report_markdown(r1)
    for word in ("profitable", "approved", "recommended", "guarantee"):
        assert word not in text.lower().replace("not a forecast, a recommendation", "")
    assert chr(0x2014) not in text and chr(0x2013) not in text and r1["data"]["sha256"] in text


def test_fixed_params_walk_forward_evaluates_the_version_itself() -> None:
    v = walk_forward({"AAA": BARS}, "trend_sma", params={"fast": 20, "slow": 100})
    assert all(f.params == {"fast": 20, "slow": 100} for f in v.folds) and "unstable" not in v.reasons


# -- the Supervisor ------------------------------------------------------------------------------


def test_a_cycle_proposes_simulated_orders_that_wait_for_the_person() -> None:
    store, _, _ = _store_with_data()
    strategy = _active(store)
    result = Supervisor(store, ME, NOW).run("post_close")
    assert result["proposed_orders"] == 1 and result["simulated"] is True
    intents, _ = store.list_intents(ME, status=None, mode=None, limit=10, offset=0)
    (intent,) = intents
    assert intent.requested_by == "agent" and intent.status == IntentStatus.PENDING and intent.mode.value == "paper"
    assert intent.idempotency_key == f"intel:{strategy.id}:{LAST.isoformat()}:AAA:buy"
    assert intent.estimate and intent.estimate["simulated"] is True and intent.estimate["price_source"].startswith("intel: ")
    decisions, _ = store.list_agent_decisions(ME, cycle_id=result["cycle_id"], limit=50, offset=0)
    agents = {d.agent for d in decisions}
    assert {Agent.MARKET_RESEARCH, Agent.EXECUTION_COORDINATOR, Agent.PORTFOLIO, Agent.PERFORMANCE_REVIEWER, Agent.SUPERVISOR} <= agents
    proposal = next(d for d in decisions if d.outcome == "proposed")
    assert proposal.intent_id == intent.id and proposal.details["strategy_version"] == 1 and proposal.details["capped"] is True
    assert proposal.details["data_source"] and proposal.details["payload_hash"] == intent.payload_hash
    # 25 percent cap: buy and hold wanted 100 percent of one symbol.
    assert Decimal(proposal.details["quantity"]) * BARS[-1].close <= Decimal("2500.01")


def test_cycles_are_idempotent_and_orders_are_never_doubled() -> None:
    store, _, _ = _store_with_data()
    _active(store)
    first = Supervisor(store, ME, NOW).run("intraday")
    again = Supervisor(store, ME, NOW + timedelta(minutes=5)).run("intraday")
    assert again["skipped"] is True and again["cycle_id"] == first["cycle_id"]
    Supervisor(store, ME, NOW).run("post_close")  # another phase: same bar, same idempotency key
    assert store.list_intents(ME, status=None, mode=None, limit=10, offset=0)[1] == 1
    assert cycle_key(ME, "intraday", NOW.date()) != cycle_key(OTHER, "intraday", NOW.date())


def test_kill_switch_halts_the_cycle() -> None:
    store, _, _ = _store_with_data()
    _active(store)
    store.save_exec_settings(ME, ExecSettings(kill_switch=True))
    result = Supervisor(store, ME, NOW).run("intraday")
    assert result["halted"] == 1 and store.list_intents(ME, status=None, mode=None, limit=10, offset=0)[1] == 0


def test_stale_or_missing_data_suspends_and_one_session_only_skips() -> None:
    store, _, _ = _store_with_data()
    strategy = _active(store)
    later = NOW + timedelta(days=7)
    Supervisor(store, ME, later).run("intraday")
    suspended = store.get_strategy(ME, strategy.id)
    assert suspended is not None and suspended.state is StrategyState.PAPER_SUSPENDED and suspended.state_changed_by == "risk_guardian"
    assert store.list_intents(ME, status=None, mode=None, limit=10, offset=0)[1] == 0
    # An agent cannot resume it.
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.transition(suspended, StrategyState.PAPER_ACTIVE, actor=Agent.SUPERVISOR, reason="data back", now=later)

    store2, _, _ = _store_with_data()
    _active(store2)
    assert LAST.weekday() == 4  # a Friday: the next session is Monday
    next_day = NOW + timedelta(days=3)  # Monday after the close: one session behind
    Supervisor(store2, ME, next_day).run("intraday")
    refused = store2.list_agent_decisions(ME, agent="risk_guardian", limit=10, offset=0)[0]
    assert refused and refused[0].reasons == ["stale_price"]


def test_abnormal_move_trips_the_circuit_breaker() -> None:
    jumped = list(BARS)
    last = jumped[-1]
    jumped[-1] = last.model_copy(update={"close": last.close * 2, "high": last.close * 2})
    store, _, _ = _store_with_data(bars=jumped)
    _active(store)
    Supervisor(store, ME, NOW).run("intraday")
    reasons = [r for d in store.list_agent_decisions(ME, agent="risk_guardian", limit=10, offset=0)[0] for r in d.reasons]
    assert "abnormal_move" in reasons and store.list_intents(ME, status=None, mode=None, limit=10, offset=0)[1] == 0


def test_risk_limits_can_only_be_tightened() -> None:
    store, _, _ = _store_with_data()
    with pytest.raises(ValueError, match="stricter"):
        Supervisor(store, ME, NOW, RiskLimits(max_position_pct=0.9))
    Supervisor(store, ME, NOW, RiskLimits(max_position_pct=0.1))


def test_pause_all_and_tenant_isolation() -> None:
    store, _, _ = _store_with_data()
    strategy = _active(store)
    assert Supervisor(store, OTHER, NOW).run("intraday")["active_strategies"] == 0  # another person sees nothing
    paused = pause_all(store, ME, now=NOW, reason="emergency pause")
    assert [p.id for p in paused] == [strategy.id] and store.get_strategy(ME, strategy.id).state is StrategyState.PAPER_SUSPENDED  # type: ignore[union-attr]
    assert Supervisor(store, ME, NOW).run("intraday")["proposed_orders"] == 0


def test_monthly_revalidation_and_weekly_review_record_decisions() -> None:
    store, _, _ = _store_with_data()
    strategy = _active(store)
    Supervisor(store, ME, NOW).run("monthly")
    revalidations = store.list_agent_decisions(ME, agent="strategy_research", limit=10, offset=0)[0]
    assert revalidations and revalidations[0].action in {"revalidate", "suspend_strategy"}
    Supervisor(store, ME, NOW).run("weekly")
    weekly = [d for d in store.list_agent_decisions(ME, agent="performance_reviewer", limit=20, offset=0)[0] if d.action == "weekly_review"]
    current = store.get_strategy(ME, strategy.id)
    assert weekly or (current is not None and current.state is StrategyState.PAPER_SUSPENDED)


def test_research_refuses_without_stored_bars() -> None:
    store = InMemoryFinanceStore()
    s = lifecycle.create(ME, key="trend_sma", symbols=["AAA"], params=None, name=None, reason="idea", actor=Agent.PERSON, now=NOW)
    with pytest.raises(research.ResearchError) as refused:
        research.evaluate(store, ME, s, kind="backtest", actor=Agent.PERSON, now=NOW)
    assert refused.value.code == "no_instruments"
