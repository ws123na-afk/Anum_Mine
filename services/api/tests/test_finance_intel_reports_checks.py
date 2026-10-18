"""Loop 8, the written post-close report, and loop 9, the performance guards
(docs/finance-trading-intel.md#daily-report and #checks). In memory.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from test_finance_intel_supervisor import BARS, ME, NOW, OTHER, _active, _store_with_data

from anum_api import main
from anum_api.agent_tools import ToolCall, default_tool_registry
from anum_api.finance.execution import ExecSettings
from anum_api.finance.intel import backtest as bt
from anum_api.finance.intel import research
from anum_api.finance.intel.marketdata import StoredCandle, synthetic_bars
from anum_api.finance.intel.records import Agent
from anum_api.finance.intel.strategies import REGISTRY
from anum_api.finance.intel.supervisor import Supervisor
from anum_api.finance.intel.validation import walk_forward
from anum_api.finance.store import finance_store
from anum_api.finance.tools import register_finance_tools


def _report(store, context=ME):  # type: ignore[no-untyped-def]
    found = [d for d in store.list_agent_decisions(context, limit=200, offset=0)[0] if d.action == "daily_report"]
    return found[0] if found else None


# -- loop 8: the daily report -----------------------------------------------------------------------


def test_post_close_writes_a_report_from_the_cycle_decisions() -> None:
    store, _, _ = _store_with_data()
    _active(store)
    result = Supervisor(store, ME, NOW).run("post_close")
    report = _report(store)
    assert report is not None and result["report_id"] == report.id and report.agent is Agent.SUPERVISOR
    data, text = report.details["report"], report.details["markdown"]
    assert data["label"] == "SIMULATED" and data["summary"]["proposed_orders"] == 1 and data["summary"]["waiting_for_approval"] == 1
    assert data["orders"][0]["intent_id"] and data["data_health"][0]["status"] == "ok"
    for heading in ("## Data health", "## Orders proposed", "## Refused and suspended", "## Paper portfolio", "## Against buy and hold",
                    "## Problems", "## Next steps"):
        assert heading in text
    assert "waiting for your approval" in text and "no real order was placed" in text
    assert chr(0x2014) not in text and chr(0x2013) not in text
    for word in ("profitable", "guarantee", "recommend"):
        assert word not in text.lower()


def test_other_phases_write_no_report_and_a_halted_close_still_reports() -> None:
    store, _, _ = _store_with_data()
    _active(store)
    Supervisor(store, ME, NOW).run("intraday")
    assert _report(store) is None
    store.save_exec_settings(ME, ExecSettings(kill_switch=True))
    Supervisor(store, ME, NOW).run("post_close")
    report = _report(store)
    assert report is not None and "kill_switch_person" in report.details["markdown"]


def test_suspensions_and_stale_data_reach_the_next_steps() -> None:
    store, _, _ = _store_with_data()
    _active(store)
    Supervisor(store, ME, NOW + timedelta(days=6)).run("post_close")  # four sessions behind: suspended
    data = _report(store).details["report"]  # type: ignore[union-attr]
    assert data["summary"]["suspended"] == 1 and data["suspended"][0]["agent"] == "risk_guardian"
    assert any("suspended strategy" in step for step in data["next_steps"])
    assert any("fresh daily bars" in step for step in data["next_steps"])


def test_report_routes_and_tool() -> None:
    from anum_api.finance.intel import supervisor as sup

    finance_store.clear()
    store = finance_store
    ctx = ME
    _store_with_data_into(store)
    _active(store)
    sup.Supervisor(store, ctx, NOW).run("post_close")
    client = TestClient(main.app)
    headers = {"x-tenant-id": ctx.tenant_id, "x-workspace-id": ctx.workspace_id, "x-user-id": ctx.user_id, "x-user-roles": "member"}
    page = client.get("/api/v1/finance/intel/daily-reports", headers=headers).json()
    assert page["total"] == 1 and page["items"][0]["summary"]["proposed_orders"] == 1
    one = client.get(f"/api/v1/finance/intel/daily-reports/{page['items'][0]['id']}", headers=headers).json()
    assert one["markdown"].startswith("# Trading report") and one["report"]["label"] == "SIMULATED"
    other = {**headers, "x-user-id": OTHER.user_id}
    assert client.get("/api/v1/finance/intel/daily-reports", headers=other).json()["total"] == 0
    assert client.get(f"/api/v1/finance/intel/daily-reports/{page['items'][0]['id']}", headers=other).status_code == 404
    assert client.get("/api/v1/finance/intel/daily-reports", headers={**headers, "x-user-roles": "viewer"}).status_code == 403
    registry = register_finance_tools(default_tool_registry())
    result = asyncio.run(registry.execute(ToolCall(name="finance.intel.daily_report", arguments={}), ctx))
    assert result.status == "succeeded" and "orders proposed for your approval" in result.summary and result.output["markdown"]
    none = asyncio.run(registry.execute(ToolCall(name="finance.intel.daily_report", arguments={"day": "2020-01-01"}), ctx))
    assert "No daily trading report" in none.summary
    finance_store.clear()


def _store_with_data_into(store) -> None:  # type: ignore[no-untyped-def]
    built, _, _ = _store_with_data()
    for i in built.instruments.values():
        store.save_instrument(i)
    store.save_bars(list(built.bars.values()))
    for p in built.portfolios.values():
        store.save_portfolio(p)


# -- loop 9: performance guards ---------------------------------------------------------------------


def test_shared_indicator_cache_changes_no_result() -> None:
    a, b = synthetic_bars(1, date(2018, 1, 1), 900, regime_every=150), synthetic_bars(2, date(2018, 1, 1), 900, regime_every=150)
    original = bt.History.__init__

    def fresh(self, data, shared=None):  # type: ignore[no-untyped-def]
        original(self, data, None)

    for key, item in REGISTRY.items():
        data = {"A": a} if item.min_symbols == 1 else {"A": a, "B": b}
        cached = walk_forward(data, key).as_dict()
        bt.History.__init__ = fresh  # type: ignore[method-assign]
        try:
            uncached = walk_forward(data, key).as_dict()
        finally:
            bt.History.__init__ = original  # type: ignore[method-assign]
        assert cached == uncached, key


def test_walk_forward_stays_fast_on_a_long_series() -> None:
    bars = {"A": synthetic_bars(4, date(2010, 1, 1), 2500)}
    started = time.perf_counter()
    walk_forward(bars, "trend_vol")
    assert time.perf_counter() - started < 20  # about 1 s locally; 26 s before the shared cache


def test_evaluations_use_recent_bars_only_and_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    store, instrument, _ = _store_with_data()
    strategy = _active(store)
    monkeypatch.setattr(research, "MAX_EVAL_BARS", 300)
    run = research.evaluate(store, ME, strategy, kind="backtest", actor=Agent.PERSON, now=NOW)
    assert "trimmed_to_recent_bars" in run.warnings and run.metrics["bars"] == 300
    with research._one_at_a_time(ME):
        with pytest.raises(research.ResearchError) as busy:
            research.evaluate(store, ME, strategy, kind="backtest", actor=Agent.PERSON, now=NOW)
        assert busy.value.code == "busy"
    assert research.evaluate(store, ME, strategy, kind="backtest", actor=Agent.PERSON, now=NOW).verdict == "completed"
    _ = BARS, StoredCandle, instrument
