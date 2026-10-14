"""Finance stage 9, wider live use (docs/finance.md#wider-live): the framework only, shipped OFF.

Graduation is computed from the stage 7 and 8 records; widening is a one-tier proposal under
hard ceilings that only the owner approves with its code, once, before a cooling-off; an
incident steps the tier down and freezes widening until the owner reviews it. The gateway
still refuses every live intent whatever the flags or tier say.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anum_api import main
from anum_api.agent_tools import ToolCall
from anum_api.dependencies import memory_repository
from anum_api.finance import wider_api
from anum_api.finance.config import FinanceConfig, finance_config
from anum_api.finance.live import LiveLimits
from anum_api.finance.store import finance_store, open_finance_store
from anum_api.finance.wider import Criterion, LiveRecord, ceiling_breaches, graduation, incident_codes
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_wide", "workspace_wide"
MEMBER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "member_wide", "x-user-roles": "member"}
VIEWER = {**MEMBER, "x-user-id": "viewer_wide", "x-user-roles": "viewer"}
CONTEXT = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_wide", roles=["member"])
API = "/api/v1/finance"
client = TestClient(main.app)
BASE = {"max_per_action": [{"currency": "AED", "amount": "100"}], "max_per_day": [{"currency": "AED", "amount": "200"}],
        "max_per_month": [{"currency": "AED", "amount": "1000"}], "currencies": ["AED"], "instruments": ["EXMPL"], "cooling_off_hours": 24}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    memory_repository.store.audit_records.clear()
    yield
    finance_store.clear()


def _limits(factor: str = "1") -> dict:
    f = Decimal(factor)
    return {**BASE, **{k: [{"currency": "AED", "amount": str(Decimal(BASE[k][0]["amount"]) * f)}] for k in ("max_per_action", "max_per_day", "max_per_month")}}


@pytest.fixture
def switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both flags forced on (startup refuses them) and the proof forced met, to test the machinery."""
    monkeypatch.setattr(finance_config, "live_enabled", True)
    monkeypatch.setattr(finance_config, "wider_live_enabled", True)
    monkeypatch.setattr(wider_api, "criteria", lambda *a, **k: [Criterion(code="forced", met=True, value="1", needed="1")])
    change = client.post(f"{API}/execution/live/changes", json={"limits": BASE}, headers=MEMBER).json()
    assert client.post(f"{API}/execution/intents/{change['id']}/approve", json={"payload_hash": change["payload_hash"]}, headers=MEMBER).status_code == 200


def test_the_wider_flag_needs_the_live_flag_and_both_are_refused() -> None:
    assert FinanceConfig().wider_live_enabled is False
    with pytest.raises(ValidationError, match="requires ANUM_FINANCE_LIVE_ENABLED"):
        FinanceConfig(wider_live_enabled=True)
    with pytest.raises(ValidationError):
        FinanceConfig(live_enabled=True, wider_live_enabled=True)


def test_graduation_is_measured_from_the_records_and_never_met_today() -> None:
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    rec = lambda **v: LiveRecord(**{"mode": "live", "status": "executed", "outcome": "allow", "created_at": now - timedelta(days=1), **v})  # noqa: E731
    good = graduation(now=now, opted_in_at=now - timedelta(days=40), records=[rec() for _ in range(20)], kill_switch_at=[], frozen=False,
                      preconditions_met=True, window_days=30, min_days=30, min_actions=20)
    assert all(c.met for c in good)
    codes = lambda **k: [c.code for c in graduation(**{"now": now, "opted_in_at": now - timedelta(days=40), "records": [rec() for _ in range(20)],  # noqa: E731
                                                       "kill_switch_at": [], "frozen": False, "preconditions_met": True, "window_days": 30,
                                                       "min_days": 30, "min_actions": 20, **k}) if not c.met]
    assert codes(opted_in_at=now - timedelta(days=5)) == ["days_live"]
    assert codes(records=[rec() for _ in range(19)]) == ["executed_actions"]
    assert codes(records=[rec() for _ in range(20)] + [rec(outcome="deny", mode="paper")]) == ["no_denials_overridden"]
    assert codes(records=[rec() for _ in range(20)] + [rec(status="failed")]) == ["no_failed_executions"]
    assert codes(kill_switch_at=[now - timedelta(days=2)]) == ["no_kill_switch_trips"]
    assert codes(kill_switch_at=[now - timedelta(days=40)]) == []  # outside the window
    assert codes(frozen=True) == ["not_frozen"]
    assert codes(records=[rec(created_at=now - timedelta(days=31)) for _ in range(20)]) == ["executed_actions"]
    view = client.get(f"{API}/execution/wider", headers=MEMBER).json()
    assert view["enabled"] is False and view["live_enabled"] is False and view["graduated"] is False and view["tier"] == 0
    met = {c["code"]: c["met"] for c in view["criteria"]}
    assert met["stage8_ready"] is False and met["executed_actions"] is False and met["days_live"] is False


def test_ceilings_allow_one_tier_and_one_step_of_limits() -> None:
    base = LiveLimits.model_validate(BASE)
    assert ceiling_breaches(base, LiveLimits.model_validate(_limits("1.5")), 1, max_tier=3, step_percent=50) == []
    assert ceiling_breaches(base, LiveLimits.model_validate(_limits("1.51")), 1, max_tier=3, step_percent=50) == ["max_per_action", "max_per_day", "max_per_month"]
    assert ceiling_breaches(base, LiveLimits.model_validate(_limits("2")), 2, max_tier=3, step_percent=50) == []
    assert ceiling_breaches(base, LiveLimits.model_validate(_limits("1")), 4, max_tier=3, step_percent=50) == ["tier_out_of_range"]
    assert ceiling_breaches(base, LiveLimits.model_validate({**BASE, "currencies": ["AED", "USD"]}), 1, max_tier=3, step_percent=50) == ["currencies"]
    assert ceiling_breaches(base, LiveLimits.model_validate({**BASE, "cooling_off_hours": 1}), 1, max_tier=3, step_percent=50) == ["cooling_off_hours"]


def test_proposals_are_refused_while_wider_live_is_off() -> None:
    refused = client.post(f"{API}/execution/wider/widenings", json={"limits": _limits("1.2")}, headers=MEMBER)
    assert refused.status_code == 409 and "Wider live use is not enabled" in refused.json()["error"]["message"]
    result = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.live.widening.propose", arguments={"percent": 20}), CONTEXT))
    assert result.status == "blocked" and not finance_store.widenings
    assert client.get(f"{API}/execution/wider", headers=VIEWER).status_code == 403
    assert [r.outcome for r in memory_repository.store.audit_records if r.action == "finance.wider.propose.refused"] == ["success"]


def test_a_step_up_needs_the_owners_code_once_and_waits_out_the_cooling_off(switched_on: None) -> None:
    over = client.post(f"{API}/execution/wider/widenings", json={"limits": _limits("2")}, headers=MEMBER)
    assert over.status_code == 422 and "ceiling" in over.json()["error"]["message"]
    two_kinds = client.post(f"{API}/execution/wider/widenings", json={"limits": _limits("1.2"), "kinds": ["trade", "transfer", "spend"]}, headers=MEMBER)
    assert two_kinds.status_code == 422
    step = client.post(f"{API}/execution/wider/widenings", json={"limits": _limits("1.2"), "kinds": ["trade", "transfer"]}, headers=MEMBER).json()
    assert step["status"] == "pending" and step["tier"] == 1 and step["base_tier"] == 0
    assert client.post(f"{API}/execution/wider/widenings", json={"limits": _limits("1.1")}, headers=MEMBER).status_code == 409
    wrong = client.post(f"{API}/execution/wider/widenings/{step['id']}/approve", json={"payload_hash": "e" * 64}, headers=MEMBER)
    assert wrong.status_code == 409
    other = {**MEMBER, "x-user-id": "someone_else"}
    assert client.post(f"{API}/execution/wider/widenings/{step['id']}/approve", json={"payload_hash": step["payload_hash"]}, headers=other).status_code == 404
    done = client.post(f"{API}/execution/wider/widenings/{step['id']}/approve", json={"payload_hash": step["payload_hash"]}, headers=MEMBER).json()
    assert done["status"] == "approved"
    assert datetime.fromisoformat(done["effective_at"]) - datetime.fromisoformat(done["decided_at"]) == timedelta(hours=finance_config.wider_cooling_hours)
    again = client.post(f"{API}/execution/wider/widenings/{step['id']}/approve", json={"payload_hash": step["payload_hash"]}, headers=MEMBER)
    assert again.status_code == 409
    assert client.get(f"{API}/execution/wider", headers=MEMBER).json()["tier"] == 0  # still cooling off
    later = datetime.now(timezone.utc) + timedelta(hours=finance_config.wider_cooling_hours + 1)
    with open_finance_store(CONTEXT) as store:
        view = wider_api.wider_view(store, CONTEXT, later)
    assert view.tier == 1 and view.kinds == ["trade", "transfer"] and view.limits.max_per_action[0].amount == Decimal("120.00")
    # Live execution is still refused whatever the tier says.
    refused = client.post(f"{API}/execution/intents", json={"mode": "live", "kind": "transfer", "amount": "10"}, headers=MEMBER)
    assert refused.status_code == 409 and "Live execution is not enabled" in refused.json()["error"]["message"]


def test_an_assistant_can_propose_but_never_approve(switched_on: None) -> None:
    result = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.live.widening.propose", arguments={"percent": 20, "add_kind": "transfer"}), CONTEXT))
    assert result.status == "succeeded" and "approve it with its code" in result.summary
    widening = finance_store.widenings[result.output["widening_id"]]
    assert widening.proposed_by == "agent" and widening.status == "pending" and widening.kinds == ["trade", "transfer"]
    assert not [n for n in main.tool_registry.names if "widening" in n and "propose" not in n]
    too_far = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.live.widening.propose", arguments={"percent": 90}), CONTEXT))
    assert too_far.status in {"blocked", "failed"}


def test_an_incident_steps_down_cancels_a_cooling_step_and_freezes_until_reviewed(switched_on: None) -> None:
    step = client.post(f"{API}/execution/wider/widenings", json={"limits": _limits("1.2")}, headers=MEMBER).json()
    client.post(f"{API}/execution/wider/widenings/{step['id']}/approve", json={"payload_hash": step["payload_hash"]}, headers=MEMBER)
    with open_finance_store(CONTEXT) as store:
        settings = store.get_exec_settings(CONTEXT)
        settings.wider.tier = 1
        store.save_exec_settings(CONTEXT, settings)
    client.put(f"{API}/execution/kill-switch", json={"scope": "person", "engaged": True}, headers=MEMBER)
    view = client.get(f"{API}/execution/wider", headers=MEMBER).json()
    assert view["frozen"] and view["frozen_reason"] == "kill_switch_person" and view["tier"] == 0
    assert view["incidents"][0]["tier_before"] == 1 and view["incidents"][0]["tier_after"] == 0
    assert view["widenings"][0]["status"] == "cancelled"
    assert any(r.action == "finance.wider.step_down" for r in memory_repository.store.audit_records)
    frozen = client.post(f"{API}/execution/wider/widenings", json={"limits": _limits("1.2")}, headers=MEMBER)
    assert frozen.status_code == 409 and "frozen" in frozen.json()["error"]["message"]
    reviewed = client.post(f"{API}/execution/wider/review", headers=MEMBER).json()
    assert reviewed["frozen"] is False and reviewed["incidents"][0]["reviewed_at"] is not None and reviewed["tier"] == 0


def test_incidents_are_failed_executions_kill_switches_and_repeated_denials() -> None:
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    since = now - timedelta(days=1)
    rec = lambda status, hours: LiveRecord(mode="paper", status=status, outcome=None, created_at=now - timedelta(hours=hours))  # noqa: E731
    found = lambda **k: incident_codes(**{"since": since, "records": [], "person_switch_at": None, "person_switch_on": False,  # noqa: E731
                                          "global_switch_at": None, "global_switch_on": False, **k})
    assert found() == []
    assert found(records=[rec("failed", 1)]) == ["failed_execution"]
    assert found(records=[rec("denied", h) for h in (1, 2, 3)]) == ["repeated_denials"]
    assert found(records=[rec("denied", h) for h in (1, 2)]) == []
    assert found(person_switch_at=now - timedelta(hours=1), person_switch_on=True) == ["kill_switch_person"]
    assert found(global_switch_at=now - timedelta(hours=1), global_switch_on=True) == ["kill_switch_global"]
    assert found(person_switch_at=now - timedelta(days=2), person_switch_on=True) == []  # seen before
