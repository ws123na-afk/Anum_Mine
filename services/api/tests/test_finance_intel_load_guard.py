"""The load guard before heavy evaluations (docs/finance-trading-intel.md#load-guard). In memory.

A person's evaluation is refused with 503 and Retry-After while the machine is busy, and
nothing is stored; a scheduled revalidation is deferred (not failed, nothing suspended) and
retried by the next post-close cycle.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_finance_intel_api import API, CSV, MEMBER, _instrument, _move, _strategy
from test_finance_intel_supervisor import ME, NOW, _active, _store_with_data

from anum_api import main
from anum_api.finance import config as finance_cfg
from anum_api.finance.intel import load_guard
from anum_api.finance.intel.records import Agent, StrategyState
from anum_api.finance.intel.supervisor import Supervisor
from anum_api.finance.store import finance_store

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    yield
    finance_store.clear()


def _backtesting() -> dict:
    instrument = _instrument()
    assert client.post(f"{API}/instruments/{instrument['id']}/bars", json={"payload": CSV}, headers=MEMBER).status_code == 200
    strategy = _strategy(instrument["id"])
    assert _move(strategy["id"], "BACKTESTING").status_code == 200
    return strategy


def test_readings_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_guard.check().load_per_cpu is None  # unknown readings never count as busy
    monkeypatch.setattr(load_guard, "read_load", lambda: 0.5)
    monkeypatch.setattr(load_guard, "read_free_memory", lambda: 4096)
    assert load_guard.check().load_per_cpu == 0.5
    monkeypatch.setattr(load_guard, "read_load", lambda: 3.0)
    with pytest.raises(load_guard.ServerBusy) as busy:
        load_guard.check()
    assert busy.value.reason == "load" and "Try again in a minute" in str(busy.value)
    monkeypatch.setattr(load_guard, "read_load", lambda: 0.2)
    monkeypatch.setattr(load_guard, "read_free_memory", lambda: 100)
    with pytest.raises(load_guard.ServerBusy) as short:
        load_guard.check()
    assert short.value.reason == "memory"
    monkeypatch.setattr(load_guard, "read_free_memory", lambda: 4096)
    monkeypatch.setattr(finance_cfg.finance_config, "eval_max_concurrent", 1)
    with load_guard.heavy():
        with pytest.raises(load_guard.ServerBusy) as full:
            with load_guard.heavy():
                pass
        assert full.value.reason == "concurrent"
    with load_guard.heavy() as reading:  # the slot is free again
        assert reading.running == 0


def test_the_real_readers_work_on_this_machine() -> None:
    load, memory = load_guard._load_per_cpu(), load_guard._free_memory_mb()
    assert load is None or load >= 0
    assert memory is None or memory >= 0
    assert load_guard._cpus() > 0


def test_a_busy_server_refuses_an_evaluation_and_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = _backtesting()
    monkeypatch.setattr(load_guard, "read_load", lambda: 2.5)
    refused = client.post(f"{API}/strategies/{strategy['id']}/evaluate", json={"kind": "walk_forward"}, headers=MEMBER)
    assert refused.status_code == 503 and refused.headers["retry-after"] == "60"
    assert "server is busy" in refused.text
    assert client.get(f"{API}/strategies/{strategy['id']}/runs", headers=MEMBER).json()["total"] == 0
    monkeypatch.setattr(load_guard, "read_load", lambda: 0.3)
    ok = client.post(f"{API}/strategies/{strategy['id']}/evaluate", json={"kind": "backtest"}, headers=MEMBER)
    assert ok.status_code == 201


def test_a_scheduled_revalidation_is_deferred_and_retried_after_the_close(monkeypatch: pytest.MonkeyPatch) -> None:
    store, _, _ = _store_with_data()
    strategy = _active(store)
    monkeypatch.setattr(load_guard, "read_load", lambda: 5.0)
    Supervisor(store, ME, NOW).run("monthly")
    decisions = store.list_agent_decisions(ME, agent=Agent.STRATEGY_RESEARCH.value, limit=20, offset=0)[0]
    deferred = next(d for d in decisions if d.action == "revalidate")
    assert deferred.outcome == "deferred" and deferred.reasons == ["server_busy", "load"]
    assert store.get_strategy(ME, strategy.id).state == StrategyState.PAPER_ACTIVE  # not suspended
    monkeypatch.setattr(load_guard, "read_load", lambda: 0.2)
    Supervisor(store, ME, NOW + timedelta(minutes=5)).run("post_close")
    newest = next(d for d in store.list_agent_decisions(ME, agent=Agent.STRATEGY_RESEARCH.value, limit=20, offset=0)[0]
                  if d.action == "revalidate")
    assert newest.outcome != "deferred"
    report = next(d for d in store.list_agent_decisions(ME, limit=200, offset=0)[0] if d.action == "daily_report")
    assert report.details["report"]["summary"]["problems"] >= 0
