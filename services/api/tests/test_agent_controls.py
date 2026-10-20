"""Owner agent controls (docs/agent-controls.md), memory backend: the emergency stop and
per-agent monthly cost caps."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anum_api import agent_controls
from anum_api.agent_controls import (
    AgentCapExceededError,
    AgentsStoppedError,
    EmergencyStop,
    memory_agent_controls_store,
    record_agent_usage,
)
from anum_api.agent_profiles import agent_store
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from anum_api.automation import (
    LocalAutomationEngine,
    ScheduleCreate,
    ScheduleOutcome,
    WorkflowCreate,
    WorkflowStep,
    trigger_decision,
)
from anum_api.automation_triggers import TriggerOutcome
from anum_api.main import app
from anum_api.model_budget import BudgetedModelGateway, ModelBudgetExceededError, check_model_budget, memory_model_budget_store
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.schemas import RiskLevel, Task, TaskStatus, TenantContext, new_id, utc_now
from anum_api.store import store

client = TestClient(app)
TENANT_A = "tenant_ctl_a"
TENANT_B = "tenant_ctl_b"
WORKSPACE_A = "workspace_ctl_a"
WORKSPACE_B = "workspace_ctl_b"
OCTOBER = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def _headers(role: str = "owner", tenant: str = TENANT_A, workspace: str = WORKSPACE_A, user: str = "owner_ctl") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = _headers("owner")
ORG_ADMIN = _headers("owner,org_admin", user="admin_ctl")
MEMBER = _headers("member", user="member_ctl")
VIEWER = _headers("viewer", user="viewer_ctl")
B_ADMIN = _headers("owner,org_admin", tenant=TENANT_B, workspace=WORKSPACE_B, user="admin_b")


def _context(tenant: str = TENANT_A, workspace: str = WORKSPACE_A, role: str = "owner") -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="owner_ctl", roles=[role])


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    memory_model_budget_store.clear()
    agent_store.clear()
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.audit_records.clear()
    yield
    agent_store.clear()
    memory_model_budget_store.clear()


def _engage(headers: dict[str, str] = OWNER, reason: str = "Runaway outreach agent") -> object:
    return client.put("/api/v1/agent-controls/emergency-stop", headers=headers, json={"engaged": True, "reason": reason})


def _release(headers: dict[str, str] = ORG_ADMIN, reason: str = "Fixed the agent") -> object:
    return client.put("/api/v1/agent-controls/emergency-stop", headers=headers, json={"engaged": False, "reason": reason})


class FixedGateway:
    provider = "openai-compatible"
    model = "gpt-4.1-mini"

    def __init__(self, cost: float = 0.5) -> None:
        self.cost = cost
        self.calls = 0

    async def generate_text(self, prompt: str) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text="ok",
            usage=ModelUsage(input_tokens=10, output_tokens=5, provider=self.provider, model=self.model, estimated_cost_usd=self.cost),
        )


# --------------------------------------------------------------------------- the switch


def test_every_member_reads_the_switch_and_learns_what_they_may_do() -> None:
    member = client.get("/api/v1/agent-controls", headers=MEMBER)
    owner = client.get("/api/v1/agent-controls", headers=OWNER)
    admin = client.get("/api/v1/agent-controls", headers=ORG_ADMIN)

    assert member.status_code == 200, member.text
    assert member.json()["emergency_stop"]["engaged"] is False
    assert (member.json()["can_engage"], member.json()["can_release"]) == (False, False)
    assert (owner.json()["can_engage"], owner.json()["can_release"]) == (True, False)
    assert (admin.json()["can_engage"], admin.json()["can_release"]) == (True, True)


def test_an_owner_turns_it_on_with_a_reason_and_it_is_audited() -> None:
    response = _engage()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["emergency_stop"]["engaged"] is True
    assert body["emergency_stop"]["reason"] == "Runaway outreach agent"
    assert body["emergency_stop"]["changed_by"] == "owner_ctl" and body["emergency_stop"]["changed_by_you"] is True
    [record] = [r for r in store.audit_records if r.action == "agent_controls.emergency_stop.engaged"]
    assert record.actor == "owner_ctl" and record.tenant_id == TENANT_A
    assert record.metadata["reason"] == "Runaway outreach agent"
    # Turning it on twice is a conflict, not a silent second audit record.
    assert _engage().status_code == 409


def test_members_and_viewers_cannot_turn_it_on_and_a_reason_is_required() -> None:
    assert _engage(MEMBER).status_code == 403
    assert _engage(VIEWER).status_code == 403
    missing = client.put("/api/v1/agent-controls/emergency-stop", headers=OWNER, json={"engaged": True})
    blank = client.put("/api/v1/agent-controls/emergency-stop", headers=OWNER, json={"engaged": True, "reason": "   "})
    assert missing.status_code == 422 and blank.status_code == 422
    assert agent_controls.emergency_stop(_context()).engaged is False


def test_only_the_organization_owner_turns_it_off_and_that_is_audited() -> None:
    assert _engage().status_code == 200

    by_owner = _release(OWNER)
    assert by_owner.status_code == 403
    assert by_owner.json()["error"]["message"] == agent_controls.RELEASE_DENIED
    assert agent_controls.emergency_stop(_context()).engaged is True

    released = _release(ORG_ADMIN)
    assert released.status_code == 200, released.text
    assert released.json()["emergency_stop"]["engaged"] is False
    [record] = [r for r in store.audit_records if r.action == "agent_controls.emergency_stop.released"]
    assert record.actor == "admin_ctl" and record.metadata["reason"] == "Fixed the agent"
    assert _release(ORG_ADMIN).status_code == 409


def test_tenant_a_cannot_flip_tenant_b() -> None:
    assert _engage().status_code == 200

    # Tenant B is untouched and its owner cannot release tenant A's stop from B.
    assert client.get("/api/v1/agent-controls", headers=B_ADMIN).json()["emergency_stop"]["engaged"] is False
    assert _release(B_ADMIN).status_code == 409  # B's own switch is already off
    assert agent_controls.emergency_stop(_context()).engaged is True
    check_model_budget(_context(TENANT_B, WORKSPACE_B))  # tenant B still runs
    with pytest.raises(AgentsStoppedError):
        check_model_budget(_context())

    # And B turning its own stop on and off never changes A's.
    assert _engage(B_ADMIN).status_code == 200
    assert _release(B_ADMIN).status_code == 200
    assert agent_controls.emergency_stop(_context()).engaged is True


# --------------------------------------------------------------------------- what the stop refuses


def test_model_calls_are_refused_before_the_provider_while_on() -> None:
    inner = FixedGateway()
    gateway = BudgetedModelGateway(inner, _context(), clock=lambda: OCTOBER)
    memory_agent_controls_store.save_stop(_context(), EmergencyStop(engaged=True, changed_by="owner_ctl"))

    with pytest.raises(AgentsStoppedError) as refused:
        asyncio.run(gateway.generate_text("hello"))

    assert inner.calls == 0
    assert isinstance(refused.value, ModelBudgetExceededError) and refused.value.code == "agents_stopped"
    assert "emergency stop" in refused.value.message
    assert "emergency stop" in refused.value.spoken(False)


def test_task_runs_answer_423_while_the_stop_is_on() -> None:
    assert _engage().status_code == 200
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Plan", "prompt": "Plan the week"}).json()

    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=OWNER)

    assert run.status_code == 423, run.text
    assert run.json()["error"]["code"] == "agents_stopped"
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=OWNER).json()["status"] == "created"


def test_every_tool_call_is_refused_whichever_path_reaches_it() -> None:
    calls: list[str] = []

    async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
        calls.append(call.name)
        return ToolResult(status="succeeded", summary="sent")

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="email.send", description="Send", risk_level=RiskLevel.HIGH), handler)
    memory_agent_controls_store.save_stop(_context(), EmergencyStop(engaged=True, changed_by="owner_ctl"))

    stopped = asyncio.run(registry.execute(ToolCall(name="email.send", arguments={"to": "a@example.com"}), _context()))
    other_tenant = asyncio.run(registry.execute(ToolCall(name="email.send", arguments={}), _context(TENANT_B, WORKSPACE_B)))

    assert stopped.status == "blocked" and "emergency stop" in stopped.summary
    assert stopped.audit_metadata == {"outcome": "emergency_stop"}
    assert other_tenant.status == "succeeded"
    assert calls == ["email.send"]  # only tenant B's call reached the connector


def test_a_held_send_stays_pending_until_the_stop_is_off() -> None:
    task = client.post("/api/v1/tasks", headers=ORG_ADMIN, json={"title": "Publish", "prompt": "Send and publish the final update"}).json()
    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=ORG_ADMIN).json()
    approval = run["approval"]
    assert approval["status"] == "pending"
    assert _engage(ORG_ADMIN).status_code == 200

    refused = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=ORG_ADMIN, json={"payload_hash": approval["payload_hash"]})

    assert refused.status_code == 423 and refused.json()["error"]["code"] == "agents_stopped"
    assert store.approvals[approval["id"]].status.value == "pending"
    assert _release(ORG_ADMIN).status_code == 200
    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=ORG_ADMIN, json={"payload_hash": approval["payload_hash"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["task"]["status"] == "completed"


def test_scheduled_mail_and_marketing_posts_are_held(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.mailbox import outgoing
    from anum_api.marketing import publisher

    def must_not_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a held send must not be claimed")

    monkeypatch.setattr(outgoing, "open_mail_store", must_not_open)
    monkeypatch.setattr(publisher, "open_marketing_store", must_not_open)
    memory_agent_controls_store.save_stop(_context(), EmergencyStop(engaged=True, changed_by="owner_ctl"))

    assert asyncio.run(outgoing.handle_due(TENANT_A, WORKSPACE_A, "owner_ctl", "draft_1")) == "held"
    assert asyncio.run(publisher.publish(_context(), "post_1")) is None


def test_discovery_of_due_sends_skips_stopped_tenants() -> None:
    from anum_api.mailbox.outgoing import _DUE
    from anum_api.marketing.publisher import _DISCOVER

    for statement in (_DUE, _DISCOVER):
        assert "agent_emergency_stops" in str(statement) and "stop.engaged" in str(statement)


def test_scheduled_automations_do_not_fire_and_triggers_do_not_start(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    workflow = engine.create_workflow(
        _context(), WorkflowCreate(name="Daily", steps=[WorkflowStep(id="n", name="Notify", action="notify")])
    )
    schedule = engine.create_schedule(_context(), ScheduleCreate(workflow_id=workflow.id, name="Daily", cron="0 8 * * *"))
    fire_at = engine.get_schedule(_context(), schedule.id).next_run_at
    assert fire_at is not None
    memory_agent_controls_store.save_stop(_context(), EmergencyStop(engaged=True, changed_by="owner_ctl"))

    assert engine.run_due_schedules(now=fire_at) == []
    after = engine.get_schedule(_context(), schedule.id)
    assert after.last_outcome == ScheduleOutcome.EMERGENCY_STOP.value
    assert after.next_run_at is not None and after.next_run_at > fire_at  # advanced, no catch-up later
    assert engine.list_runs(_context()) == []

    decision = trigger_decision(
        type("T", (), {"enabled": True, "rate_limit_per_minute": 10})(),  # type: ignore[arg-type]
        workflow, archived=False, actor_ok=True, recent=0, active=0, looped=False, stopped=True,
    )
    assert decision == TriggerOutcome.EMERGENCY_STOP


def test_turning_it_on_cancels_running_and_queued_tasks() -> None:
    context = _context()
    now = utc_now()
    ids = []
    for state in (TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.COMPLETED):
        task = Task(
            id=new_id("task"), tenant_id=TENANT_A, workspace_id=WORKSPACE_A, title=state.value, prompt="p",
            status=state, created_at=now, updated_at=now, created_by=context.user_id,
        )
        store.tasks[task.id] = task
        ids.append(task.id)

    response = _engage()

    assert response.status_code == 200, response.text
    assert response.json()["cancelled_tasks"] == 2
    assert [store.tasks[i].status for i in ids] == [TaskStatus.CANCELLED, TaskStatus.CANCELLED, TaskStatus.COMPLETED]
    assert any(r.action == "agent_controls.emergency_stop.cancelled_tasks" for r in store.audit_records)


# --------------------------------------------------------------------------- per-agent caps


def _agent(name: str = "Outreach", headers: dict[str, str] = OWNER) -> dict:  # type: ignore[type-arg]
    created = client.post("/api/v1/agents", headers=headers, json={"name": name, "sharing": "workspace"})
    assert created.status_code == 201, created.text
    return created.json()


def test_owners_set_caps_and_see_usage_against_them() -> None:
    agent = _agent()
    other = _agent("Research")

    saved = client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=OWNER, json={"monthly_cost_limit_usd": 2})
    record_agent_usage(_context(), agent["id"], ModelUsage(input_tokens=1, output_tokens=1, provider="p", model="m", estimated_cost_usd=0.5))
    overview = client.get("/api/v1/agent-controls/agent-caps", headers=OWNER)

    assert saved.status_code == 200, saved.text
    assert saved.json()["monthly_cost_limit_usd"] == 2
    body = overview.json()
    assert body["total"] == 3 and body["can_manage"] is True
    by_id = {item["agent_id"]: item for item in body["items"]}
    assert by_id[agent["id"]]["usage"]["estimated_cost_usd"] == pytest.approx(0.5)
    assert by_id[agent["id"]]["percent_used"] == pytest.approx(25)
    assert by_id[agent["id"]]["exceeded"] is False
    assert by_id[other["id"]]["monthly_cost_limit_usd"] is None and by_id[other["id"]]["percent_used"] is None
    assert any(r.action == "agent_controls.agent_cap.update" and r.target == f"agent:{agent['id']}" for r in store.audit_records)
    # Search and paging.
    found = client.get("/api/v1/agent-controls/agent-caps", headers=OWNER, params={"q": "resea"}).json()
    assert [item["name"] for item in found["items"]] == ["Research"] and found["total"] == 1
    paged = client.get("/api/v1/agent-controls/agent-caps", headers=OWNER, params={"limit": 1, "offset": 1}).json()
    assert len(paged["items"]) == 1 and paged["total"] == 3
    # Clearing the cap.
    cleared = client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=OWNER, json={"monthly_cost_limit_usd": None})
    assert cleared.json()["monthly_cost_limit_usd"] is None


def test_caps_are_owner_only_validated_and_scoped() -> None:
    agent = _agent()
    assert client.get("/api/v1/agent-controls/agent-caps", headers=MEMBER).status_code == 403
    member_set = client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=MEMBER, json={"monthly_cost_limit_usd": 1})
    assert member_set.status_code == 403
    assert client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=OWNER, json={"monthly_cost_limit_usd": -1}).status_code == 422
    assert client.put("/api/v1/agent-controls/agent-caps/agent_missing", headers=OWNER, json={"monthly_cost_limit_usd": 1}).status_code == 404
    # Another tenant's owner never sees or caps this agent.
    other = _headers("owner", tenant=TENANT_B, workspace=WORKSPACE_B, user="owner_b")
    assert client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=other, json={"monthly_cost_limit_usd": 1}).status_code == 404
    assert client.get("/api/v1/agent-controls/agent-caps", headers=other).json()["total"] == 1


def test_an_agent_over_its_cap_is_refused_with_a_clear_message() -> None:
    agent = _agent()
    assert client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=OWNER, json={"monthly_cost_limit_usd": 1}).status_code == 200
    inner = FixedGateway(cost=0.6)
    gateway = BudgetedModelGateway(inner, _context(), clock=utc_now).for_agent(agent["id"])

    asyncio.run(gateway.generate_text("one"))
    asyncio.run(gateway.generate_text("two"))  # admitted at 0.6, ends at 1.2: the check does not reserve
    with pytest.raises(AgentCapExceededError) as refused:
        asyncio.run(gateway.generate_text("three"))

    assert inner.calls == 2
    assert refused.value.code == "model_budget_exceeded"
    assert "Outreach" in refused.value.message and "monthly cost cap" in refused.value.message
    # The workspace is not capped: the same workspace without the agent still runs.
    asyncio.run(BudgetedModelGateway(inner, _context(), clock=utc_now).generate_text("plain"))
    assert inner.calls == 3
    overview = client.get("/api/v1/agent-controls/agent-caps", headers=OWNER).json()["items"][1]
    assert overview["exceeded"] is True and overview["usage"]["calls"] == 2


def test_routed_and_bound_gateways_keep_the_agent() -> None:
    gateway = BudgetedModelGateway(FixedGateway(), _context()).for_agent("agent_x")
    assert gateway.agent_id == "agent_x"
    assert gateway.for_agent("agent_x") is gateway
    assert gateway.for_agent(None).agent_id is None


def test_task_runs_as_an_agent_count_against_its_cap() -> None:
    agent = _agent()
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Plan", "prompt": "Plan the week", "agent_id": agent["id"]})
    assert task.status_code in {200, 201}, task.text
    assert client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=OWNER).status_code == 200

    item = client.get("/api/v1/agent-controls/agent-caps", headers=OWNER).json()["items"][1]
    assert item["usage"]["calls"] == 1

    assert client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=OWNER, json={"monthly_cost_limit_usd": 0}).status_code == 200
    again = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Again", "prompt": "Plan the week", "agent_id": agent["id"]}).json()
    refused = client.post(f"/api/v1/tasks/{again['id']}/run", headers=OWNER)
    assert refused.status_code == 402 and "monthly cost cap" in refused.json()["error"]["message"]


def test_every_state_changing_route_is_audited_without_content() -> None:
    from audit_probe import AuditProbe

    agent = _agent()
    probe = AuditProbe(client, "agent_controls")
    probe.call("PUT", "/api/v1/agent-controls/emergency-stop", ORG_ADMIN, json={"engaged": True, "reason": "Audit walk"})
    probe.call("PUT", f"/api/v1/agent-controls/agent-caps/{agent['id']}", OWNER, json={"monthly_cost_limit_usd": 3})
    probe.assert_covered()


# --------------------------------------------------------------------------- attribution, tokens, held, notices


def test_chat_and_voice_count_against_the_default_assistant() -> None:
    from anum_api.onboarding import budgeted_model_gateway

    gateway = budgeted_model_gateway(_context(), FixedGateway(cost=0.25))
    assert gateway.agent_id == agent_controls.DEFAULT_ASSISTANT_ID
    asyncio.run(gateway.generate_text("hello"))

    assistant = client.get("/api/v1/agent-controls/agent-caps", headers=OWNER).json()["items"][0]
    assert assistant["agent_id"] == "assistant" and assistant["is_default"] is True
    assert assistant["usage"]["calls"] == 1 and assistant["usage"]["estimated_cost_usd"] == pytest.approx(0.25)
    # The default assistant can be capped like any agent.
    capped = client.put("/api/v1/agent-controls/agent-caps/assistant", headers=OWNER, json={"monthly_cost_limit_usd": 0.2})
    assert capped.status_code == 200 and capped.json()["exceeded"] is True
    with pytest.raises(AgentCapExceededError) as refused:
        asyncio.run(gateway.generate_text("again"))
    assert "ANUM assistant" in refused.value.message


def test_a_chat_thread_with_an_agent_is_attributed_to_it() -> None:
    agent = _agent()
    thread = client.post("/api/v1/conversations", headers=OWNER, json={"title": "Outreach", "agent_id": agent["id"]})
    assert thread.status_code in {200, 201}, thread.text
    asked = client.post(f"/api/v1/conversations/{thread.json()['id']}/ask", headers=OWNER, json={"content": "Draft a follow-up"})
    assert asked.status_code == 200
    by_id = {item["agent_id"]: item for item in client.get("/api/v1/agent-controls/agent-caps", headers=OWNER).json()["items"]}
    assert by_id[agent["id"]]["usage"]["calls"] >= 1


def test_token_caps_refuse_an_agent_over_its_tokens() -> None:
    agent = _agent()
    saved = client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=OWNER, json={"monthly_token_limit": 20})
    assert saved.status_code == 200, saved.text
    assert saved.json()["monthly_token_limit"] == 20 and saved.json()["monthly_cost_limit_usd"] is None
    inner = FixedGateway(cost=0.0)
    gateway = BudgetedModelGateway(inner, _context(), clock=utc_now).for_agent(agent["id"])
    asyncio.run(gateway.generate_text("one"))
    asyncio.run(gateway.generate_text("two"))  # 15 tokens, then 30
    with pytest.raises(AgentCapExceededError) as refused:
        asyncio.run(gateway.generate_text("three"))
    assert "monthly token cap (20 tokens)" in refused.value.message
    items = client.get("/api/v1/agent-controls/agent-caps", headers=OWNER).json()["items"]
    item = {i["agent_id"]: i for i in items}[agent["id"]]
    assert item["total_tokens"] == 30 and item["token_percent_used"] == pytest.approx(150) and item["exceeded"] is True
    bad = client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=OWNER, json={"monthly_token_limit": -1})
    assert bad.status_code == 422


def test_the_held_list_pages_pending_sends_for_owners() -> None:
    for index in range(3):
        task = client.post(
            "/api/v1/tasks", headers=ORG_ADMIN, json={"title": f"Publish {index}", "prompt": "Send and publish the final update"}
        ).json()
        assert client.post(f"/api/v1/tasks/{task['id']}/run", headers=ORG_ADMIN).json()["approval"]["status"] == "pending"
    assert _engage(ORG_ADMIN).status_code == 200

    first = client.get("/api/v1/agent-controls/held", headers=ORG_ADMIN, params={"limit": 2})
    second = client.get("/api/v1/agent-controls/held", headers=ORG_ADMIN, params={"limit": 2, "offset": 2})

    assert first.status_code == 200, first.text
    assert first.json()["engaged"] is True and first.json()["total"] == 3 and len(first.json()["items"]) == 2
    assert first.json()["items"][0]["kind"] == "approval" and first.json()["items"][0]["title"] == "external.action"
    assert len(second.json()["items"]) == 1
    assert client.get("/api/v1/agent-controls/held", headers=MEMBER).status_code == 403
    other = _headers("owner", tenant=TENANT_B, workspace=WORKSPACE_B, user="owner_b")
    assert client.get("/api/v1/agent-controls/held", headers=other).json()["total"] == 0


def test_owners_get_an_in_app_notice_and_a_push_event_when_the_stop_changes() -> None:
    from anum_api.push import PushKind, intent_for
    from anum_api.schemas import WorkspaceMembership

    now = utc_now()
    users = (("owner_ctl", "owner"), ("owner_two", "owner"), ("member_ctl", "member"))
    for user, role in users:
        store.memberships[(TENANT_A, WORKSPACE_A, user)] = WorkspaceMembership(
            tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user, role=role, active=True, created_at=now, updated_at=now
        )
    before = len(store.events)
    try:
        assert _engage().status_code == 200
        events = [e for e in store.events[before:] if e.type == "notification.created"]
        assert [e.payload["recipient"] for e in events] == ["owner_two"]
        assert events[0].payload["kind"] == agent_controls.NOTICE_KIND
        intent = intent_for(events[0])
        assert intent is not None and intent.kind == PushKind.TEAM_WORK and intent.recipient == "owner_two"
        title, body = agent_controls.notice_text(agent_controls.emergency_stop(_context()))
        assert title == "Emergency stop turned on" and "Runaway outreach agent" in body
    finally:
        for user, _ in users:
            store.memberships.pop((TENANT_A, WORKSPACE_A, user), None)


def test_turning_it_on_cancels_every_active_task_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    # A small batch size stands in for "more active tasks than one batch": all must be cancelled.
    monkeypatch.setattr(agent_controls, "CANCEL_LIMIT", 2)
    context = _context()
    now = utc_now()
    ids = []
    for index in range(7):
        task = Task(
            id=new_id("task"), tenant_id=TENANT_A, workspace_id=WORKSPACE_A, title=f"t{index}", prompt="p",
            status=TaskStatus.RUNNING if index % 2 else TaskStatus.QUEUED, created_at=now, updated_at=now, created_by=context.user_id,
        )
        store.tasks[task.id] = task
        ids.append(task.id)
    done = Task(
        id=new_id("task"), tenant_id=TENANT_A, workspace_id=WORKSPACE_A, title="done", prompt="p",
        status=TaskStatus.COMPLETED, created_at=now, updated_at=now, created_by=context.user_id,
    )
    store.tasks[done.id] = done
    batches: list[int] = []
    original = agent_controls._cancel_batch

    def counting(scoped: TenantContext) -> tuple[int, list[str]]:
        listed, cancelled = original(scoped)
        batches.append(len(cancelled))
        return listed, cancelled

    monkeypatch.setattr(agent_controls, "_cancel_batch", counting)

    response = _engage()

    assert response.status_code == 200, response.text
    assert response.json()["cancelled_tasks"] == 7
    assert [store.tasks[i].status for i in ids] == [TaskStatus.CANCELLED] * 7
    assert store.tasks[done.id].status == TaskStatus.COMPLETED
    assert batches == [2, 2, 2, 1]  # four short units of work, none holding more than a batch


def test_a_batch_that_cancels_nothing_ends_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def stuck(scoped: TenantContext) -> tuple[int, list[str]]:
        calls.append(1)
        return agent_controls.CANCEL_LIMIT, []  # a full page of tasks someone else settled meanwhile

    monkeypatch.setattr(agent_controls, "_cancel_batch", stuck)
    assert agent_controls.cancel_in_flight(_context(), [WORKSPACE_A]) == []
    assert calls == [1]
