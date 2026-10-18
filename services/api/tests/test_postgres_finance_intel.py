"""Trading intelligence storage in PostgreSQL (migration 0065_finance_intel).

Runs as the non-owner ``anum_test_app`` role: forced RLS and the restrictive owner-only
policy decide every count. The database also refuses an agent promoting a strategy, a
PAPER_ACTIVE version without a paper portfolio, and inconsistent bars.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from anum_api.db.finance_repository import SqlAlchemyFinanceStore
from anum_api.finance.intel import lifecycle
from anum_api.finance.intel.marketdata import AssetClass, Instrument, StoredCandle, synthetic_bars
from anum_api.finance.intel.records import Agent, AgentDecision, StrategyRun, StrategyState
from anum_api.schemas import TenantContext

pytestmark = pytest.mark.database
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
TABLES = ("finance_instruments", "finance_market_bars", "finance_strategies", "finance_strategy_runs", "finance_agent_decisions")
ME = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test", roles=["member"])
OTHER = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_owner", roles=["owner"])
FOREIGN = TenantContext(tenant_id=TENANT_B, workspace_id=WORKSPACE_B, user_id="user_test", roles=["member"])


@pytest.fixture
def store(app_session: Callable[..., Iterator[Session]], seed_scopes: None) -> Callable[..., Iterator[SqlAlchemyFinanceStore]]:
    @contextmanager
    def factory(context: TenantContext = ME) -> Iterator[SqlAlchemyFinanceStore]:
        with app_session(context, commit=True) as session:
            session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": context.user_id})
            yield SqlAlchemyFinanceStore(session, context)

    return factory


def _instrument(context: TenantContext = ME, symbol: str = "AAA") -> Instrument:
    return Instrument(id=f"fin_ins_{symbol.lower()}_{context.user_id}", tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                      owner_user_id=context.user_id, symbol=symbol, asset_class=AssetClass.EQUITY, venue="XNYS", currency="USD", created_at=NOW)


def _bars(instrument: Instrument, count: int = 30) -> list[StoredCandle]:
    return [
        StoredCandle(id=f"bar_{instrument.id}_{c.day.isoformat()}", tenant_id=instrument.tenant_id, workspace_id=instrument.workspace_id,
                     owner_user_id=instrument.owner_user_id, instrument_id=instrument.id, day=c.day, open=c.open, high=c.high, low=c.low,
                     close=c.close, volume=c.volume, source="synthetic: generated test data, not market data", provider="synthetic",
                     fetched_at=NOW)
        for c in synthetic_bars(1, date(2026, 1, 1), count)
    ]


def test_new_tables_have_forced_rls_and_owner_only_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            assert forced, table
            policies = dict(connection.execute(text("select policyname, permissive from pg_policies where tablename = :t"), {"t": table}).all())
            assert policies.get(f"tenant_isolation_{table}") == "PERMISSIVE", table
            assert policies.get(f"owner_only_{table}") == "RESTRICTIVE", table


def test_bars_upsert_is_idempotent_and_private(store: Callable[..., Iterator[SqlAlchemyFinanceStore]]) -> None:
    with store() as s:
        instrument = s.save_instrument(_instrument())
        bars = _bars(instrument)
        assert s.save_bars(bars) == 30
    with store() as s:
        assert s.save_bars(bars) == 0  # same bars again: nothing changes
        changed = bars[5].model_copy(update={"close": bars[5].close + Decimal("0.0001"), "high": bars[5].high + Decimal("1")})
        assert s.save_bars([changed]) == 1
        stored = s.list_bars(ME, instrument.id)
        assert len(stored) == 30 and stored[0].day < stored[-1].day and stored[5].close == changed.close
        assert s.list_bars(ME, instrument.id, limit=10)[-1].day == stored[-1].day
        with pytest.raises(ValueError, match="duplicate_instrument"):
            s.save_instrument(_instrument().model_copy(update={"id": "fin_ins_other"}))
    with store(OTHER) as s:
        assert s.list_instruments(OTHER) == [] and s.list_bars(OTHER, instrument.id) == []
    with store(FOREIGN) as s:
        assert s.get_instrument(FOREIGN, instrument.id) is None


def test_bad_bars_are_refused_by_the_database(store: Callable[..., Iterator[SqlAlchemyFinanceStore]]) -> None:
    with store() as s:
        instrument = s.save_instrument(_instrument(symbol="BBB"))
    bad = _bars(instrument, 1)[0].model_copy(update={"high": Decimal("1"), "low": Decimal("2")})
    with pytest.raises(DBAPIError), store() as s:
        s.save_bars([bad])


def test_strategy_versions_runs_and_decisions_round_trip(store: Callable[..., Iterator[SqlAlchemyFinanceStore]]) -> None:
    draft = lifecycle.create(ME, key="trend_sma", symbols=["AAA"], params={"fast": 20, "slow": 100}, name=None, reason="first idea",
                             actor=Agent.PERSON, now=NOW)
    with store() as s:
        s.save_strategy(draft)
        run = StrategyRun(id="fin_run_1", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, owner_user_id="user_test", strategy_id=draft.id,
                          kind="backtest", created_at=NOW, data_source="synthetic", data_hash="a" * 64, code_version=draft.code_version,
                          symbols=["AAA"], params=draft.params, metrics={"sharpe": 0.4, "max_drawdown": -0.2}, verdict="completed",
                          requested_by=Agent.STRATEGY_RESEARCH.value)
        s.save_strategy_run(run)
        s.save_agent_decision(AgentDecision(id="fin_dec_1", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, owner_user_id="user_test",
                                            agent=Agent.STRATEGY_RESEARCH, action="backtest", outcome="ok", reasons=["completed"],
                                            strategy_id=draft.id, created_at=NOW))
    with store() as s:
        items, total = s.list_strategies(ME, limit=10, offset=0)
        assert total == 1 and items[0].params == {"fast": 20, "slow": 100} and items[0].state is StrategyState.DRAFT
        runs, count = s.list_strategy_runs(ME, draft.id, limit=10, offset=0)
        assert count == 1 and runs[0].metrics["sharpe"] == 0.4 and runs[0].created_at == NOW
        decisions, n = s.list_agent_decisions(ME, agent="strategy_research", limit=10, offset=0)
        assert n == 1 and decisions[0].agent is Agent.STRATEGY_RESEARCH
        moved = lifecycle.transition(draft, StrategyState.BACKTESTING, actor=Agent.STRATEGY_RESEARCH, reason="start research", now=NOW)
        s.save_strategy(moved)
        assert s.get_strategy(ME, draft.id).state is StrategyState.BACKTESTING  # type: ignore[union-attr]
        assert s.latest_version(ME, draft.lineage_id) == 1
        with pytest.raises(ValueError, match="duplicate_version"):
            s.save_strategy(draft.model_copy(update={"id": "fin_str_dup"}))
    with store(OTHER) as s:
        assert s.list_strategies(OTHER, limit=10, offset=0) == ([], 0)
        assert s.list_agent_decisions(OTHER, limit=10, offset=0) == ([], 0)
    with store() as s:
        assert s.count_everything(ME) >= 3
        assert s.delete_everything(ME) >= 3
        assert s.list_strategies(ME, limit=10, offset=0) == ([], 0)


@pytest.mark.parametrize(
    ("update", "why"),
    [
        ({"state": StrategyState.PAPER_ACTIVE, "portfolio_id": "fin_pf_1", "state_changed_by": "risk_guardian"}, "agent promotes"),
        ({"state": StrategyState.RETIRED, "state_changed_by": "performance_reviewer"}, "agent retires"),
        ({"state": StrategyState.PAPER_ACTIVE, "portfolio_id": None, "state_changed_by": "person"}, "no portfolio"),
        ({"version": 2, "created_by": "strategy_research", "evidence_run_id": None}, "agent version without evidence"),
    ],
)
def test_database_refuses_what_the_lifecycle_forbids(store: Callable[..., Iterator[SqlAlchemyFinanceStore]], update: dict, why: str) -> None:
    draft = lifecycle.create(ME, key="buy_and_hold", symbols=["AAA"], params=None, name=None, reason="benchmark", actor=Agent.PERSON, now=NOW)
    with pytest.raises(DBAPIError), store() as s:
        s.save_strategy(draft.model_copy(update=update))
