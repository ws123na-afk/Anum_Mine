"""Trading intelligence write routes (``finance/intel_control_api.py``). In memory.

Synthetic bars are generated test data, imported through the same CSV path a person uses.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance.intel.marketdata import synthetic_bars
from anum_api.finance.store import finance_store

TENANT, WORKSPACE = "tenant_intel", "workspace_intel"
MEMBER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "member_intel", "x-user-roles": "member"}
OTHER = {**MEMBER, "x-user-id": "other_intel"}
VIEWER = {**MEMBER, "x-user-id": "viewer_intel", "x-user-roles": "viewer"}
API = "/api/v1/finance/intel"
client = TestClient(main.app)
BARS = synthetic_bars(12, date(2023, 1, 2), 900, drift=0.0008)  # ends 12 June 2026, before today
CSV = "Date,Open,High,Low,Close,Volume\n" + "".join(f"{c.day},{c.open},{c.high},{c.low},{c.close},{c.volume}\n" for c in BARS)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    finance_store.clear()


def _instrument(headers: dict[str, str] = MEMBER) -> dict:
    response = client.post(f"{API}/instruments", json={"symbol": "exmpl", "asset_class": "etf", "venue": "XNYS", "currency": "usd",
                                                        "listed_on": "2000-01-03"}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _strategy(instrument_id: str, key: str = "buy_and_hold", headers: dict[str, str] = MEMBER) -> dict:
    response = client.post(f"{API}/strategies", json={"key": key, "instrument_ids": [instrument_id], "reason": "benchmark research"},
                           headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _move(strategy_id: str, state: str, headers: dict[str, str] = MEMBER):  # type: ignore[no-untyped-def]
    return client.post(f"{API}/strategies/{strategy_id}/transition", json={"state": state, "reason": f"move to {state}"}, headers=headers)


def test_full_research_flow_to_paper_and_a_supervisor_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from anum_api.finance import intel_control_api

    tuesday = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)  # a New York trading day, whatever day the test runs
    monkeypatch.setattr(intel_control_api, "utc_now", lambda: tuesday)
    instrument = _instrument()
    assert instrument["symbol"] == "EXMPL" and instrument["currency"] == "USD"
    listed = client.get(f"{API}/instruments", headers=MEMBER).json()
    assert listed[0]["bars"] == 0 and listed[0]["last_bar"] is None
    imported = client.post(f"{API}/instruments/{instrument['id']}/bars", json={"payload": CSV, "source": "Test file"}, headers=MEMBER)
    assert imported.status_code == 200 and imported.json()["imported"] == 900 and imported.json()["changed"] == 900
    again = client.post(f"{API}/instruments/{instrument['id']}/bars", json={"payload": CSV, "source": "Test file"}, headers=MEMBER)
    assert again.json()["changed"] == 0  # idempotent import
    listed = client.get(f"{API}/instruments", headers=MEMBER).json()
    assert listed[0]["bars"] == 900 and listed[0]["last_bar"] == BARS[-1].day.isoformat() and listed[0]["last_source"] == "Test file"
    assert client.get(f"{API}/instruments", headers=OTHER).json() == []
    strategy = _strategy(instrument["id"])
    assert strategy["state"] == "DRAFT" and strategy["symbols"] == ["EXMPL"] and strategy["simulated"] is True and "owner_user_id" not in strategy
    assert _move(strategy["id"], "VALIDATION").status_code == 409  # DRAFT cannot skip ahead
    assert _move(strategy["id"], "BACKTESTING").status_code == 200
    assert _move(strategy["id"], "VALIDATION").status_code == 409  # needs a backtest first
    backtest = client.post(f"{API}/strategies/{strategy['id']}/evaluate", json={"kind": "backtest"}, headers=MEMBER)
    assert backtest.status_code == 201 and backtest.json()["verdict"] == "completed" and len(backtest.json()["data_hash"]) == 64
    assert _move(strategy["id"], "VALIDATION").status_code == 200
    assert _move(strategy["id"], "PAPER_ACTIVE").status_code == 409  # no validation run, no portfolio
    portfolio = client.post("/api/v1/finance/paper/portfolios", json={"name": "Research", "mode": "paper",
                                                                       "starting_cash": [{"currency": "USD", "amount": "10000"}]}, headers=MEMBER).json()
    assert client.post(f"{API}/strategies/{strategy['id']}/portfolio", json={"portfolio_id": portfolio["id"]}, headers=MEMBER).status_code == 200
    validation = client.post(f"{API}/strategies/{strategy['id']}/evaluate", json={"kind": "walk_forward"}, headers=MEMBER).json()
    assert validation["verdict"] == "passed" and validation["reproduced"] is True and validation["report"]["label"] == "SIMULATED"
    active = _move(strategy["id"], "PAPER_ACTIVE")
    assert active.status_code == 200 and active.json()["state_changed_by"] == "person"
    # The stored bars end in June: the Risk Guardian suspends instead of trading on old prices.
    cycle = client.post(f"{API}/supervisor/run", json={"phase": "intraday"}, headers=MEMBER).json()
    assert cycle["suspended"] == 1 and cycle["proposed_orders"] == 0
    assert client.post(f"{API}/supervisor/run", json={"phase": "intraday"}, headers=MEMBER).json()["skipped"] is True
    actions = {r.action for r in memory_repository.store.audit_records}
    assert {"finance.intel.bars.import", "finance.intel.strategy.evaluate", "finance.intel.supervisor.run"} <= actions
    for record in memory_repository.store.audit_records:
        assert "EXMPL" not in str(record.metadata)


def test_bad_imports_and_instruments_are_refused() -> None:
    instrument = _instrument()
    duplicate = CSV + f"{BARS[-1].day},{BARS[-1].open},{BARS[-1].high},{BARS[-1].low},{BARS[-1].close},1\n"
    refused = client.post(f"{API}/instruments/{instrument['id']}/bars", json={"payload": duplicate}, headers=MEMBER)
    assert refused.status_code == 422 and "Two bars for the same day" in refused.json()["error"]["message"]
    future = "Date,Open,High,Low,Close\n2099-01-02,1,1,1,1\n"
    assert client.post(f"{API}/instruments/{instrument['id']}/bars", json={"payload": future}, headers=MEMBER).status_code == 422
    assert client.post(f"{API}/instruments", json={"symbol": "EXMPL", "asset_class": "etf", "venue": "XNYS", "currency": "USD"},
                       headers=MEMBER).status_code == 409
    assert client.post(f"{API}/instruments", json={"symbol": "BTC/USDT", "asset_class": "crypto", "venue": "CRYPTO", "currency": "USDT"},
                       headers=MEMBER).status_code == 422
    assert client.post(f"{API}/instruments", json={"symbol": "X", "asset_class": "etf", "venue": "MOON", "currency": "USD"},
                       headers=MEMBER).status_code == 422
    unbuilt = client.post(f"{API}/instruments/{instrument['id']}/bars", json={"payload": "x", "provider": "alpha_vantage"}, headers=MEMBER)
    assert unbuilt.status_code == 422


def test_versions_live_candidate_and_permissions() -> None:
    instrument = _instrument()
    strategy = _strategy(instrument["id"], key="trend_sma")
    version = client.post(f"{API}/strategies/{strategy['id']}/versions", json={"params": {"fast": 20}, "reason": "faster"}, headers=MEMBER)
    assert version.status_code == 201 and version.json()["version"] == 2 and version.json()["parent_id"] == strategy["id"]
    same = client.post(f"{API}/strategies/{strategy['id']}/versions", json={"reason": "nothing"}, headers=MEMBER)
    assert same.status_code == 409
    bad = client.post(f"{API}/strategies/{strategy['id']}/versions", json={"params": {"fast": 500}, "reason": "too slow"}, headers=MEMBER)
    assert bad.status_code == 422
    live = _move(strategy["id"], "LIVE_CANDIDATE")
    assert live.status_code == 409 and "grants nothing" in live.json()["error"]["message"]
    assert client.post(f"{API}/strategies", json={"key": "martingale", "instrument_ids": [instrument["id"]], "reason": "no"},
                       headers=MEMBER).status_code == 422
    # Another person and a viewer cannot touch it.
    assert _move(strategy["id"], "BACKTESTING", headers=OTHER).status_code == 404
    assert client.post(f"{API}/strategies/{strategy['id']}/evaluate", json={}, headers=OTHER).status_code == 404
    assert _move(strategy["id"], "BACKTESTING", headers=VIEWER).status_code == 403
    assert client.post(f"{API}/pause-all", json={}, headers=VIEWER).status_code == 403
    assert client.post(f"{API}/supervisor/run", json={"phase": "midnight"}, headers=MEMBER).status_code == 422
    assert client.post(f"{API}/pause-all", json={}, headers=MEMBER).json() == {"paused": [], "simulated": True}
    in_use = client.delete(f"{API}/instruments/{instrument['id']}", headers=MEMBER)
    assert in_use.status_code == 204  # only DRAFT versions use it


def test_scheduled_cycles_run_through_automation_once_per_day(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime, timezone

    from anum_api import automation
    from anum_api.finance.intel.supervisor import PHASES
    from anum_api.schemas import WorkspaceMembership

    assert automation.FINANCE_INTEL_PHASES == PHASES
    monkeypatch.setattr(automation, "engine", automation.LocalAutomationEngine(str(tmp_path / "automation.db")))
    now = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
    memory_repository.save_membership(WorkspaceMembership(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_intel", role="member", created_at=now, updated_at=now))
    assert client.post(f"{API}/schedules", json={"phase": "post_close"}, headers=VIEWER).status_code == 403
    assert client.post(f"{API}/schedules", json={"phase": "midnight"}, headers=MEMBER).status_code == 422
    assert client.post(f"{API}/schedules", json={"phase": "weekly", "timezone": "Mars/Base"}, headers=MEMBER).status_code == 422
    created = client.post(f"{API}/schedules", json={"phase": "weekly", "weekday": 5, "time": "18:15", "timezone": "Asia/Dubai"}, headers=MEMBER)
    assert created.status_code == 201, created.text
    schedule = created.json()
    assert schedule["cron"] == "15 18 * * 5" and schedule["description"] == "Every Friday at 18:15" and schedule["next_run_at"]
    assert client.post(f"{API}/schedules", json={"phase": "weekly"}, headers=MEMBER).status_code == 409
    assert client.get(f"{API}/schedules", headers=OTHER).json() == []  # another person's finance schedule is not shown

    run = client.post(f"/api/v1/automation/workflows/{schedule['workflow_id']}/runs", headers=MEMBER)
    assert run.status_code == 201, run.text
    decisions = finance_store.list_agent_decisions(_me(), agent="trading_supervisor", limit=10, offset=0)[0]
    assert len(decisions) == 1 and decisions[0].action == "cycle" and decisions[0].details["phase"] == "weekly"
    # A second fire the same day is skipped by the cycle itself.
    client.post(f"/api/v1/automation/workflows/{schedule['workflow_id']}/runs", headers=MEMBER)
    assert len(finance_store.list_agent_decisions(_me(), agent="trading_supervisor", limit=10, offset=0)[0]) == 1

    assert client.delete(f"{API}/schedules/{schedule['id']}", headers=OTHER).status_code == 404
    assert client.delete(f"{API}/schedules/{schedule['id']}", headers=MEMBER).status_code == 204
    assert client.get(f"{API}/schedules", headers=MEMBER).json() == []


def test_a_scheduled_post_close_cycle_announces_its_report(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime, timezone

    from anum_api import automation
    from anum_api.finance.intel.report_notice import KIND
    from anum_api.schemas import WorkspaceMembership
    from anum_api.team_store import memory_team_store

    monkeypatch.setattr(automation, "engine", automation.LocalAutomationEngine(str(tmp_path / "automation.db")))
    now = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
    memory_repository.save_membership(WorkspaceMembership(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_intel", role="member", created_at=now, updated_at=now))
    schedule = client.post(f"{API}/schedules", json={"phase": "post_close"}, headers=MEMBER).json()
    before = [n for n in memory_team_store.list_notifications(_me(), limit=100, before=None, unread_only=False) if n.kind == KIND]
    assert client.post(f"/api/v1/automation/workflows/{schedule['workflow_id']}/runs", headers=MEMBER).status_code == 201
    after = [n for n in memory_team_store.list_notifications(_me(), limit=100, before=None, unread_only=False) if n.kind == KIND]
    assert len(after) == len(before) + 1 and after[0].user_id == "member_intel" and "Daily reports" in after[0].body
    reports = client.get(f"{API}/daily-reports", headers=MEMBER).json()
    assert reports["total"] >= 1
    client.delete(f"{API}/schedules/{schedule['id']}", headers=MEMBER)


def _me():  # type: ignore[no-untyped-def]
    from anum_api.schemas import TenantContext

    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_intel", roles=["member"])


def test_rules_list_the_six_candidates_and_the_benchmark() -> None:
    rules = client.get(f"{API}/rules", headers=VIEWER)
    assert rules.status_code == 403
    items = client.get(f"{API}/rules", headers=MEMBER).json()
    assert items[0]["key"] == "buy_and_hold" and sorted({r["candidate"] for r in items}) == [0, 1, 2, 3, 4, 5, 6]
    assert all(r["hypothesis"] and r["entry"] and r["exit"] for r in items)


def test_an_instrument_keeps_a_bounded_number_of_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.finance import intel_control_api

    monkeypatch.setattr(intel_control_api, "MAX_BARS", 500)
    instrument = _instrument()
    refused = client.post(f"{API}/instruments/{instrument['id']}/bars", json={"payload": CSV}, headers=MEMBER)
    assert refused.status_code == 409 and "at most 500" in refused.json()["error"]["message"]
    assert client.get(f"{API}/instruments", headers=MEMBER).json()[0]["bars"] == 0
