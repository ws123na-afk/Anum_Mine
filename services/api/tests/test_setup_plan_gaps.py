"""The setup plan's remaining gaps, built (docs/setup-plan.md): budget alerts, the approval-ageing
reminder, automations that run as an agent, per-agent loop limits, Home priorities, consent records
for campaigns, the plan's agents in each business workspace, office seats and the live checklist.
Memory backend; the tables are checked in PostgreSQL by test_postgres_setup_plan_gaps.py."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import model_budget
from anum_api.agent_controls import memory_agent_controls_store, record_agent_usage
from anum_api.agent_loop_limits import memory_loop_limit_store
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolPolicyOutcome, ToolRegistry, ToolResult
from anum_api.approval_reminders import memory_reminder_store, run_approval_reminder_pass
from anum_api.budget_alerts import crossed
from anum_api.contact_consent import consent_refusals_for_call, consent_store
from anum_api.dependencies import memory_repository
from anum_api.model_budget import BudgetScope, ModelBudgetLimits, memory_model_budget_store, record_model_usage
from anum_api.model_gateway import MockModelGateway, ModelUsage
from anum_api.repository import InMemoryRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import Approval, ApprovalStatus, RiskLevel, Task, TaskStatus, TenantContext, new_id, utc_now
from anum_api.store import InMemoryStore
from anum_api.suppression import suppression_store
from anum_api.team_store import memory_team_store

from test_setup_plans import ADMIN, MEMBER, OWNER, PLAN, TENANT, WORKSPACE, _member, agents, apply, client, h, items, preview  # noqa: F401

CTX = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_1", roles=["owner", "org_admin"])


@pytest.fixture(autouse=True)
def _clean() -> Any:
    for cleared in (memory_agent_controls_store, memory_loop_limit_store, memory_reminder_store, consent_store, suppression_store):
        cleared.clear()
    yield
    for cleared in (memory_agent_controls_store, memory_loop_limit_store, memory_reminder_store, consent_store, suppression_store):
        cleared.clear()


def notices(user: str, kind: str, workspace: str = WORKSPACE, tenant: str = TENANT) -> list[Any]:
    ctx = TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=["owner"])
    return [n for n in memory_team_store.list_notifications(ctx, limit=200, before=None, unread_only=False) if n.kind == kind]


def usage(cost: float, tokens: int = 10) -> ModelUsage:
    return ModelUsage(input_tokens=tokens, output_tokens=0, provider="mock", model="mock", estimated_cost_usd=cost)


# ---------------------------------------------------------------------------- budget alerts


def test_crossings_are_exact_and_once() -> None:
    assert crossed(0, 5, 10) == [50]
    assert crossed(4, 9.5, 10) == [50, 75, 90]
    assert crossed(9.5, 9.6, 10) == []
    assert crossed(0, 10, 10) == [50, 75, 90, 100]
    assert crossed(0, 10, None) == [] and crossed(0, 10, 0) == []


def test_workspace_budget_alerts_reach_owners_in_the_app_only(client: TestClient) -> None:
    memory_model_budget_store.set_budget(CTX, BudgetScope.WORKSPACE, ModelBudgetLimits(monthly_cost_limit_usd=10), utc_now())
    record_model_usage(CTX, usage(5.0))
    first = notices("owner_1", "budget_alert")
    assert len(first) == 1 and "50%" in first[0].title and first[0].email_status.value == "not_requested"
    assert notices("owner_2", "budget_alert") and not notices("member_1", "budget_alert")
    record_model_usage(CTX, usage(0.1))
    assert len(notices("owner_1", "budget_alert")) == 1
    record_model_usage(CTX, usage(4.5))
    titles = sorted(n.title for n in notices("owner_1", "budget_alert"))
    assert any("75%" in t for t in titles) and any("90%" in t for t in titles)


def test_agent_cap_alerts_name_the_agent(client: TestClient) -> None:
    agent = client.post("/api/v1/agents", headers=ADMIN, json={"name": "Cap tester", "category": "work"}).json()
    assert client.put(f"/api/v1/agent-controls/agent-caps/{agent['id']}", headers=ADMIN,
                      json={"monthly_cost_limit_usd": 2, "monthly_token_limit": None}).status_code == 200
    record_agent_usage(CTX, agent["id"], usage(1.6))
    found = notices("owner_1", "budget_alert")
    assert {("Cap tester" in n.title) for n in found} == {True} and len(found) == 2  # 50 and 75


# ---------------------------------------------------------------------------- approval reminder


def _pending_approval(hours_old: float, *, workspace: str = WORKSPACE, tenant: str = TENANT, status: ApprovalStatus = ApprovalStatus.PENDING) -> Approval:
    now = utc_now()
    task = Task(id=new_id("task"), title="Refund", prompt="Refund", status=TaskStatus.WAITING_APPROVAL if hasattr(TaskStatus, "WAITING_APPROVAL") else TaskStatus.CREATED,
                tenant_id=tenant, workspace_id=workspace, created_at=now, updated_at=now, created_by="owner_1")
    memory_repository.create_task(task)
    approval = Approval(id=new_id("approval"), task_id=task.id, action="commerce.refund", risk_level=RiskLevel.HIGH, status=status,
                        reason="Refund", created_at=now - timedelta(hours=hours_old), expires_at=now - timedelta(hours=hours_old) + timedelta(hours=24))
    return memory_repository.save_approval(approval)


def test_reminder_settings_need_an_owner_and_valid_hours(client: TestClient) -> None:
    url = "/api/v1/approval-reminders/settings"
    assert client.get(url, headers=MEMBER).json()["reminder_hours"] is None
    assert client.put(url, headers=MEMBER, json={"reminder_hours": 4}).status_code == 403
    assert client.put(url, headers=ADMIN, json={"reminder_hours": 24}).status_code == 422
    assert client.put(url, headers=ADMIN, json={"reminder_hours": 0}).status_code == 422
    saved = client.put(url, headers=ADMIN, json={"reminder_hours": 4})
    assert saved.status_code == 200 and saved.json()["reminder_hours"] == 4
    assert client.put(url, headers=ADMIN, json={"reminder_hours": None}).json()["reminder_hours"] is None


def test_an_old_pending_approval_reminds_owners_once(client: TestClient) -> None:
    assert client.put("/api/v1/approval-reminders/settings", headers=ADMIN, json={"reminder_hours": 4}).status_code == 200
    old = _pending_approval(5)
    _pending_approval(1)  # too young
    _pending_approval(6, status=ApprovalStatus.APPROVED)  # decided
    _pending_approval(30)  # expired
    assert run_approval_reminder_pass() == 1
    sent = notices("owner_1", "approval_reminder")
    assert len(sent) == 1 and "commerce.refund" in sent[0].title and sent[0].email_status.value == "not_requested"
    assert notices("owner_2", "approval_reminder") and not notices("member_1", "approval_reminder")
    assert run_approval_reminder_pass() == 0
    assert len(notices("owner_1", "approval_reminder")) == 1
    assert (TENANT, WORKSPACE, old.id) in memory_reminder_store.sent


def test_no_reminder_without_the_setting_or_in_another_tenant(client: TestClient) -> None:
    _pending_approval(10)
    _pending_approval(10, tenant="tenant_other", workspace="workspace_other")
    assert run_approval_reminder_pass() == 0
    assert client.put("/api/v1/approval-reminders/settings", headers=ADMIN, json={"reminder_hours": 2}).status_code == 200
    assert run_approval_reminder_pass() == 1
    assert not notices("owner_1", "approval_reminder", workspace="workspace_other", tenant="tenant_other")


# ---------------------------------------------------------------------------- loop limits and run as an agent


def _agent(client: TestClient, name: str = "Loop agent") -> dict:
    response = client.post("/api/v1/agents", headers=ADMIN, json={"name": name, "category": "work", "sharing": "workspace"})
    assert response.status_code in {200, 201}, response.text
    return response.json()


def test_loop_limits_are_owner_only_validated_and_listed(client: TestClient) -> None:
    agent = _agent(client)
    url = f"/api/v1/agent-controls/loop-limits/{agent['id']}"
    assert client.put(url, headers=MEMBER, json={"max_runs_per_task": 5}).status_code == 403
    assert client.get("/api/v1/agent-controls/loop-limits", headers=MEMBER).status_code == 403
    assert client.put(url, headers=ADMIN, json={"max_runs_per_task": 21}).status_code == 422
    assert client.put("/api/v1/agent-controls/loop-limits/agent_missing", headers=ADMIN, json={"max_runs_per_task": 2}).status_code == 404
    saved = client.put(url, headers=ADMIN, json={"max_runs_per_task": 2, "max_step_attempts": 1}).json()
    assert (saved["max_runs_per_task"], saved["max_step_attempts"]) == (2, 1)
    listed = client.get("/api/v1/agent-controls/loop-limits?q=loop", headers=ADMIN).json()
    assert listed["total"] == 1 and listed["items"][0]["max_runs_per_task"] == 2 and listed["can_manage"]
    cleared = client.put(url, headers=ADMIN, json={"max_runs_per_task": None, "max_step_attempts": None}).json()
    assert cleared["max_runs_per_task"] is None


def test_a_task_as_the_agent_stops_retrying_at_its_loop_limit(client: TestClient) -> None:
    from fastapi import HTTPException

    from anum_api.agent_loop_limits import check_retry_allowed

    agent = _agent(client)
    task = client.post("/api/v1/tasks", headers=ADMIN, json={"title": "Check stock", "prompt": "Check stock", "agent_id": agent["id"]}).json()
    assert client.post(f"/api/v1/tasks/{task['id']}/run", headers=ADMIN).status_code == 200
    stored = memory_repository.get_task(task["id"], CTX)
    check_retry_allowed(CTX, memory_repository, stored)  # no limit yet
    assert client.put(f"/api/v1/agent-controls/loop-limits/{agent['id']}", headers=ADMIN, json={"max_runs_per_task": 2}).status_code == 200
    check_retry_allowed(CTX, memory_repository, stored)  # one run of two
    assert client.put(f"/api/v1/agent-controls/loop-limits/{agent['id']}", headers=ADMIN, json={"max_runs_per_task": 1}).status_code == 200
    with pytest.raises(HTTPException) as refused:
        check_retry_allowed(CTX, memory_repository, stored)
    assert refused.value.status_code == 409 and "loop limit" in str(refused.value.detail) and "Loop agent" in str(refused.value.detail)
    audits = [r for r in memory_repository.store.audit_records if r.action == "agent_controls.loop_limit.update"]
    assert audits and audits[-1].metadata["limits"].startswith("runs_per_task=1")


def error(response: Any) -> str:
    body = response.json()
    return str(body.get("detail") or body.get("error", {}).get("message"))


def test_an_automation_step_runs_as_a_chosen_agent(client: TestClient) -> None:
    from anum_api import automation as auto

    agent = _agent(client, "Automation agent")
    bad = client.post("/api/v1/automation/workflows", headers=ADMIN, json={
        "name": "As someone", "steps": [{"id": "t", "name": "T", "action": "task.create", "input": {"title": "T", "prompt": "P", "agent_id": "agent_nope"}}]})
    assert bad.status_code == 422 and "Choose an agent you can see" in error(bad)
    created = client.post("/api/v1/automation/workflows", headers=ADMIN, json={
        "name": "As the agent", "steps": [{"id": "t", "name": "T", "action": "task.create",
                                           "input": {"title": "T", "prompt": "P", "agent_id": agent["id"], "run": False}}]})
    assert created.status_code == 201, created.text
    assert client.put(f"/api/v1/agent-controls/loop-limits/{agent['id']}", headers=ADMIN, json={"max_step_attempts": 1}).status_code == 200
    run = client.post(f"/api/v1/automation/workflows/{created.json()['id']}/runs", headers=ADMIN, json={})
    assert run.status_code in {200, 201, 202}, run.text
    tasks = [t for t in memory_repository.list_tasks(CTX) if t.title == "T"] if hasattr(memory_repository, "list_tasks") else []
    found = auto.current_engine().get_run(CTX, run.json()["id"]) if hasattr(auto.current_engine(), "get_run") else None
    step = (found.steps if found else run.json()["steps"])[0]
    output = step.output if hasattr(step, "output") else step["output"]
    assert output.get("agent_id") == agent["id"]
    attempts = step.max_attempts if hasattr(step, "max_attempts") else step["max_attempts"]
    assert attempts == 1
    if tasks:
        assert tasks[0].agent_id == agent["id"]
    # A paused agent fails the step with the reason.
    assert client.patch(f"/api/v1/agents/{agent['id']}", headers=ADMIN, json={"status": "paused", "version": agent["version"]}).status_code == 200
    second = client.post(f"/api/v1/automation/workflows/{created.json()['id']}/runs", headers=ADMIN, json={}).json()
    assert second["status"] == "failed" and "paused" in (second["steps"][0]["error"] or "")


# ---------------------------------------------------------------------------- Home priorities


def test_home_layout_is_chosen_ordered_and_per_person(client: TestClient) -> None:
    default = client.get("/api/v1/home/layout", headers=ADMIN).json()
    assert default["customized"] is False and default["sections"][0] == "glance" and default["hidden"] == []
    assert client.put("/api/v1/home/layout", headers=ADMIN, json={"sections": ["focus", "nope"]}).status_code == 422
    assert client.put("/api/v1/home/layout", headers=ADMIN, json={"sections": ["focus", "focus"]}).status_code == 422
    saved = client.put("/api/v1/home/layout", headers=ADMIN, json={"sections": ["decisions", "focus"]}).json()
    assert saved["sections"] == ["decisions", "focus"] and "glance" in saved["hidden"] and saved["customized"]
    # Changing the greeting name keeps the order; another person keeps the default.
    assert client.put("/api/v1/home/profile", headers=ADMIN, json={"display_name": "Shahzeb"}).status_code == 200
    assert client.get("/api/v1/home/layout", headers=ADMIN).json()["sections"] == ["decisions", "focus"]
    assert client.get("/api/v1/home/layout", headers=OWNER).json()["customized"] is False
    reset = client.delete("/api/v1/home/layout", headers=ADMIN).json()
    assert reset["customized"] is False and len(reset["sections"]) == len(default["sections"])


# ---------------------------------------------------------------------------- consent records


WA = ToolDefinition(name="send_whatsapp_template", description="", risk_level=RiskLevel.HIGH, category="whatsapp", action_kind="send")
CAMPAIGN = ToolDefinition(name="marketing.send_campaign", description="", risk_level=RiskLevel.HIGH, category="marketing", action_kind="send")


def _consent(client: TestClient, recipient: str = "+971501234567", channel: str = "whatsapp", who: dict[str, str] = ADMIN, **extra: Any) -> Any:
    return client.post("/api/v1/contact-consents", headers=who, json={
        "recipient": recipient, "channel": channel, "lawful_basis": "consent", "source": "Website form",
        "consented_at": (utc_now() - timedelta(days=3)).isoformat(), **extra})


def test_consents_are_recorded_withdrawn_and_owner_only(client: TestClient) -> None:
    assert _consent(client, who=MEMBER).status_code == 403
    assert _consent(client, recipient="ahmed@example.com", channel="whatsapp").status_code == 422
    assert _consent(client, consented_at=(utc_now() + timedelta(days=2)).isoformat()).status_code == 422
    first = _consent(client)
    assert first.status_code == 201 and first.json()["masked"] == "+971•••4567" and first.json()["status"] == "active"
    assert "501234567" not in str(first.json())
    again = _consent(client, source="Shop counter sign-up")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"] and again.json()["source"] == "Shop counter sign-up"
    record_id = first.json()["id"]
    withdrawn = client.post(f"/api/v1/contact-consents/{record_id}/withdraw", headers=ADMIN, json={"reason": "Asked by phone"})
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"
    assert client.post(f"/api/v1/contact-consents/{record_id}/withdraw", headers=ADMIN, json={"reason": "Again"}).status_code == 409
    page = client.get("/api/v1/contact-consents?q=%2B971501234567", headers=ADMIN).json()
    assert page["total"] == 1 and page["withdrawn"] == 1
    assert client.get("/api/v1/contact-consents?state=active", headers=ADMIN).json()["total"] == 0
    other = h("owner_b", "owner", workspace="workspace_b", tenant="tenant_b")
    assert client.get("/api/v1/contact-consents", headers=other).json()["total"] == 0
    assert client.put("/api/v1/contact-consents/settings", headers=MEMBER, json={"require_for_marketing": True}).status_code == 403


def test_marketing_sends_check_consent_as_well_as_suppression(client: TestClient) -> None:
    marketing = {"to": "971501234567", "purpose": "marketing"}
    assert consent_refusals_for_call(CTX, WA.name, marketing, WA) == []
    record = _consent(client).json()
    client.post(f"/api/v1/contact-consents/{record['id']}/withdraw", headers=ADMIN, json={"reason": "Asked by phone"})
    refusals = consent_refusals_for_call(CTX, WA.name, marketing, WA)
    assert len(refusals) == 1 and refusals[0].why == "withdrawn"
    # A service message (not marketing) is never checked here.
    assert consent_refusals_for_call(CTX, WA.name, {"to": "971501234567"}, WA) == []
    # With the rule on, a recipient without any record is refused too; one with consent is not.
    assert client.put("/api/v1/contact-consents/settings", headers=ADMIN, json={"require_for_marketing": True}).status_code == 200
    assert consent_refusals_for_call(CTX, CAMPAIGN.name, {"recipients": ["ahmed@example.com"]}, CAMPAIGN)[0].why == "missing"
    _consent(client, recipient="ahmed@example.com", channel="email")
    assert consent_refusals_for_call(CTX, CAMPAIGN.name, {"recipients": ["ahmed@example.com"]}, CAMPAIGN) == []


def test_runtime_and_registry_refuse_a_marketing_send_without_consent(client: TestClient) -> None:
    sent: list[ToolCall] = []
    registry = ToolRegistry()

    async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
        sent.append(call)
        return ToolResult(status="succeeded", summary="sent")

    registry.register(WA, handler)
    runtime = AgentRuntime(MockModelGateway(), InMemoryRepository(InMemoryStore()), tools=registry)
    call = ToolCall(name=WA.name, arguments={"to": "971501234567", "purpose": "marketing"})
    assert client.put("/api/v1/contact-consents/settings", headers=ADMIN, json={"require_for_marketing": True}).status_code == 200
    decision = runtime.evaluate(call, CTX)
    assert decision.outcome == ToolPolicyOutcome.BLOCK and "contact_consent" in decision.governance_rules and "no consent record" in decision.reason
    result = asyncio.run(registry.execute(call, CTX))
    assert result.status == "blocked" and sent == []
    audits = [r for r in memory_repository.store.audit_records if r.action == "consent.send_refused"]
    assert {r.metadata["stage"] for r in audits} >= {"policy", "execute"}
    assert all("971501234567" not in str(r.metadata) for r in audits)


# ---------------------------------------------------------------------------- the setup plan


def test_plan_sets_caps_loop_limits_reminder_home_and_consent_once(client: TestClient) -> None:
    result = apply(client)
    controls = {i["key"]: i for i in items(result, "controls")}
    assert controls["a1:cap"]["outcome"] == "created" and controls["a1:loop"]["outcome"] == "created"
    caps = {row["name"]: row for row in client.get("/api/v1/agent-controls/agent-caps?limit=100", headers=ADMIN).json()["items"]}
    assert caps["Group: Chief of Staff"]["monthly_cost_limit_usd"] == pytest.approx(round(60 / 3.6725, 2))
    loops = {row["name"]: row for row in client.get("/api/v1/agent-controls/loop-limits?limit=100", headers=ADMIN).json()["items"]}
    assert (loops["Group: Chief of Staff"]["max_runs_per_task"], loops["Group: Chief of Staff"]["max_step_attempts"]) == (5, 3)
    reminder = next(i for i in result["items"] if i["key"] == "auto04")
    assert reminder["outcome"] == "created"
    assert client.get("/api/v1/approval-reminders/settings", headers=ADMIN).json()["reminder_hours"] == 4
    assert client.get("/api/v1/home/layout", headers=ADMIN).json()["sections"][0] == "decisions"
    assert client.get("/api/v1/contact-consents/settings", headers=ADMIN).json()["require_for_marketing"] is True
    assert next(i for i in result["items"] if i["key"] == "budget_alerts")["action"] == "builtin"
    # Applying again changes nothing.
    again = apply(client)
    for key in ("a1:cap", "a1:loop", "auto04", "home_layout", "consent_marketing"):
        assert next(i for i in again["items"] if i["key"] == key)["action"] == "present", key
    # An owner's own cap is kept.
    agent_id = caps["Group: Chief of Staff"]["agent_id"]
    client.put(f"/api/v1/agent-controls/agent-caps/{agent_id}", headers=ADMIN, json={"monthly_cost_limit_usd": 99, "monthly_token_limit": None})
    assert next(i for i in preview(client)["items"] if i["key"] == "a1:cap")["action"] == "kept"
    assert next(i for i in preview(client, overwrite_edited="true")["items"] if i["key"] == "a1:cap")["action"] == "update"


def test_owner_without_rights_is_skipped_with_reasons(client: TestClient) -> None:
    viewer = h("viewer_1", "viewer")
    _member("viewer_1", "viewer")
    assert client.get(f"/api/v1/setup-plans/{PLAN}/preview", headers=viewer).status_code == 403
    assert client.get(f"/api/v1/setup-plans/{PLAN}/checklist", headers=MEMBER).status_code == 403
    member_admin = h("admin_m", "member,org_admin")
    _member("admin_m", "member")
    result = preview(client, headers=member_admin)
    cap = next(i for i in result["items"] if i["key"] == "a1:cap")
    assert cap["action"] == "skipped" and cap["reason_code"] in {"needs_owner", "missing_dependency"}


def test_automations_run_as_their_agent_when_asked(client: TestClient) -> None:
    from anum_api import automation as auto

    apply(client)
    result = apply(client, run_automations_as_agents=True)
    updated = [i for i in items(result, "automations") if i["reason_code"] == "will_run_as_agent"]
    assert updated and all(i["outcome"] == "updated" for i in updated)
    brief = next(w for w in auto.current_engine().list_workflows(CTX) if w.name == "Group: Morning Brief")
    chief = agents(client)["Group: Chief of Staff"]
    assert brief.steps[0].input["agent_id"] == chief["id"] and brief.status.value != "active"
    assert all(i["reason_code"] != "will_run_as_agent" for i in apply(client, run_automations_as_agents=True)["items"])


def test_business_workspaces_get_their_agents_with_per_workspace_checks(client: TestClient) -> None:
    pilot = preview(client, business_workspaces="true")
    assert {i["action"] for i in items(pilot, "business_agents")} == {"later"}
    missing = preview(client, phase="all", business_workspaces="true")
    assert {i["reason_code"] for i in items(missing, "business_agents")} == {"workspace_missing"}
    result = apply(client, phase="all")
    created = {i["key"]: i for i in items(result, "workspaces")}
    assert created["w_fleet"]["outcome"] == "created"
    placed = apply(client, phase="all", business_workspaces=True)
    rows = items(placed, "business_agents")
    assert {i["outcome"] for i in rows} == {"created"}, [(i["key"], i.get("error")) for i in rows]
    fleet_id = created["w_fleet"]["link"]["id"]
    in_fleet = client.get("/api/v1/agents?limit=100", headers=h(workspace=fleet_id)).json()["items"]
    assert {a["name"] for a in in_fleet} == {"Fleet: Sales Pipeline", "Fleet: Proposal Consultant"}
    again = apply(client, phase="all", business_workspaces=True)
    assert {i["action"] for i in items(again, "business_agents")} == {"present"}
    # Another owner who is not a member of the business workspaces is told why.
    other = preview(client, phase="all", headers=OWNER, business_workspaces="true")
    assert {i["reason_code"] for i in items(other, "business_agents")} == {"not_a_member"}


def test_offices_need_the_headquarters(client: TestClient) -> None:
    result = preview(client)
    office = items(result, "offices")
    assert len(office) == 1 and office[0]["action"] == "manual" and office[0]["reason_code"] == "no_offices"
    assert items(preview(client, seat_in_offices="false"), "offices") == []


def test_checklist_reads_real_state(client: TestClient) -> None:
    checklist = client.get(f"/api/v1/setup-plans/{PLAN}/checklist", headers=ADMIN)
    assert checklist.status_code == 200, checklist.text
    rows = {i["check"]: i for i in checklist.json()["items"] if i["check"]}
    assert rows["two_factor"]["state"] == "cannot_check"
    assert rows["two_person"]["state"] == "todo"
    assert rows["people"]["state"] in {"todo", "partial"} and "members" in rows["people"]["summary"]
    assert rows["documents"]["state"] == "todo"
    assert rows["automations_on"]["state"] == "todo"
    assert rows["ai_model"]["state"] in {"todo", "done"}
    assert rows["backups"]["state"] == "cannot_check"
    apply(client)
    after = {i["check"]: i for i in client.get(f"/api/v1/setup-plans/{PLAN}/checklist", headers=ADMIN).json()["items"] if i["check"]}
    assert after["documents"]["summary"].startswith("0 of 9")
    assert after["webhooks"]["summary"].startswith("0 of")
    assert after["approvers"]["state"] == "todo" and len(after["approvers"]["details"]) == 3
    assert client.put("/api/v1/approval-policy", headers=ADMIN, json={"two_person_rule": True, "medium_risk_requires_approval": False}).status_code == 200
    final = {i["check"]: i for i in client.get(f"/api/v1/setup-plans/{PLAN}/checklist", headers=ADMIN).json()["items"] if i["check"]}
    assert final["two_person"]["state"] == "done"


# ---------------------------------------------------------------------------- audit walks


def test_consent_and_reminder_routes_are_audited_without_content(client: TestClient) -> None:
    from audit_probe import AuditProbe

    probe = AuditProbe(client, "contact_consents")
    recorded = probe.call("POST", "/api/v1/contact-consents", ADMIN, json={
        "recipient": "+971501234567", "channel": "whatsapp", "lawful_basis": "consent", "source": "Website form",
        "consented_at": (utc_now() - timedelta(days=1)).isoformat()})
    probe.call("POST", f"/api/v1/contact-consents/{recorded.json()['id']}/withdraw", ADMIN, json={"reason": "Asked by phone"})
    probe.call("PUT", "/api/v1/contact-consents/settings", ADMIN, json={"require_for_marketing": True})
    probe.assert_covered()
    probe = AuditProbe(client, "approval_reminders")
    probe.call("PUT", "/api/v1/approval-reminders/settings", ADMIN, json={"reminder_hours": 4})
    probe.assert_covered()
