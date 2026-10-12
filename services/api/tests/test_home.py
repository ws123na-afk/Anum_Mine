"""Home (docs/home.md): summary, pins, quick commands, insights and the source registry.

In-memory backend; PostgreSQL and RLS are in ``test_postgres_home.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import anum_api.automation as automation_module
from anum_api import home
from anum_api.audit import AuditRecord
from anum_api.automation import LocalAutomationEngine, WorkflowCreate, WorkflowStep
from anum_api.autonomy import ActionKind, AutonomyProposal, ProposalStatus, ToolCategory, autonomy_memory_store
from anum_api.home import FocusItem, FocusResult, FocusSource, Insight, InsightAction, InsightSource, Target, home_store
from anum_api.integrations import IntegrationCheck, IntegrationStatus
from anum_api.schemas import (
    AgentRun,
    Approval,
    ApprovalStatus,
    DomainEvent,
    RiskLevel,
    RunCheckpoint,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from anum_api.store import store

TENANT, WORKSPACE = "tenant_home", "workspace_home"


def headers(user: str = "owner_home", role: str = "owner", workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = headers()
MEMBER = headers("member_home", "member")
VIEWER = headers("viewer_home", "viewer")


def ctx(user: str = "owner_home", role: str = "owner", workspace: str = WORKSPACE) -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=workspace, user_id=user, roles=[role])


class _Effects:
    def create_task(self, context, title, prompt):  # type: ignore[no-untyped-def]
        raise AssertionError("not used")

    def record_event(self, event: DomainEvent) -> None:
        pass

    def record_audit(self, record: AuditRecord) -> None:
        pass

    def wants_failure_notice(self, context, user_id):  # type: ignore[no-untyped-def]
        return False

    def actor_may_run(self, context, user_id):  # type: ignore[no-untyped-def]
        return True

    def flush(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for collection in (store.tasks, store.runs, store.approvals, store.audit_records, store.memberships, store.workspaces):
        collection.clear()
    autonomy_memory_store.clear()
    home_store.clear()
    monkeypatch.setattr(
        automation_module, "engine", LocalAutomationEngine(str(tmp_path / "automation.db"), effects_factory=_Effects)
    )
    from anum_api import main

    main.integration_registry.memory_configurations.clear()
    yield
    home_store.clear()
    for name in ("custom_focus", "custom", "broken", "learning"):
        home.unregister_source(name)


@pytest.fixture
def client() -> TestClient:
    from anum_api.main import app

    return TestClient(app)


def _task(task_id: str, status: TaskStatus, *, title: str = "Tyre report", age: timedelta = timedelta(minutes=5)) -> Task:
    when = utc_now() - age
    task = Task(
        id=task_id, title=title, prompt="prepare it", status=status, tenant_id=TENANT, workspace_id=WORKSPACE,
        created_at=when, updated_at=when, created_by="owner_home",
    )
    store.tasks[task_id] = task
    return task


def _approval(approval_id: str, *, risk: RiskLevel = RiskLevel.HIGH, age: timedelta = timedelta(minutes=10)) -> Approval:
    _task(f"task_for_{approval_id}", TaskStatus.WAITING_APPROVAL)
    approval = Approval(
        id=approval_id, task_id=f"task_for_{approval_id}", action="email.send", risk_level=risk,
        status=ApprovalStatus.PENDING, reason="Sends mail", created_at=utc_now() - age,
    )
    store.approvals[approval_id] = approval
    return approval


def _audits(action: str) -> list[AuditRecord]:
    return [r for r in store.audit_records if r.action == action]


# --------------------------------------------------------------------------- summary


def test_summary_aggregates_focus_progress_and_activity(client: TestClient) -> None:
    _approval("appr_one")
    _task("task_running", TaskStatus.RUNNING, title="PMV tyre report review")
    store.runs["run_one"] = AgentRun(
        id="run_one", task_id="task_running", status=TaskStatus.RUNNING,
        checkpoint=RunCheckpoint(phase=RunPhase.EXECUTING), created_at=utc_now(), updated_at=utc_now(),
    )
    _task("task_done", TaskStatus.COMPLETED, title="Supplier comparison", age=timedelta(seconds=30))

    response = client.get("/api/v1/home/summary", headers=OWNER)
    assert response.status_code == 200
    body = response.json()
    kinds = [item["kind"] for item in body["focus"]]
    assert kinds[0] == "approvals_pending"  # high severity first
    approvals = body["focus"][0]
    assert approvals["count"] == 1 and approvals["target"] == {"feature": "approvals", "id": "appr_one"}
    running = next(item for item in body["focus"] if item["id"] == "task:task_running")
    assert running["progress"] == 0.75 and running["target"] == {"feature": "tasks", "id": "task_running"}
    assert body["counts"]["approvals"] == 1 and body["counts"]["tasks"] == 2  # running + waiting approval
    assert body["progress"]["completed"] >= 1 and body["progress"]["open"] == 2
    assert body["activity"][0]["title"] == "Supplier comparison"
    statuses = {s["name"]: s["status"] for s in body["sources"]}
    assert statuses["email"] == "not_connected" and statuses["meetings"] == "not_installed"
    assert statuses["approvals"] == statuses["tasks"] == statuses["automations"] == "ok"


def test_summary_leaves_out_sources_the_caller_may_not_read(client: TestClient) -> None:
    body = client.get("/api/v1/home/summary", headers=VIEWER).json()
    statuses = {s["name"]: s["status"] for s in body["sources"]}
    assert statuses["email"] == "not_permitted"
    assert statuses["tasks"] == "ok"


def test_a_failing_source_is_reported_and_the_rest_still_answers(client: TestClient) -> None:
    def broken(scope: home.HomeScope) -> FocusResult:
        raise RuntimeError("database password: secret")

    home.register_focus_source(FocusSource("broken", broken))
    _task("task_running", TaskStatus.RUNNING)
    response = client.get("/api/v1/home/summary", headers=OWNER)
    assert response.status_code == 200
    broken_status = next(s for s in response.json()["sources"] if s["name"] == "broken")
    assert broken_status["status"] == "unavailable" and "secret" not in (broken_status["detail"] or "")
    assert any(item["id"] == "task:task_running" for item in response.json()["focus"])


def test_registered_focus_source_appears_and_meetings_stop_reading_as_missing(client: TestClient) -> None:
    async def meetings(scope: home.HomeScope) -> FocusResult:
        item = FocusItem(
            id="meeting:1", source="meetings", kind="meeting_today", title="Team meeting", severity="medium",
            at=scope.now, target=Target(feature="calendar", id="1"),
        )
        return FocusResult(items=[item], count=1)

    home.register_focus_source(FocusSource("custom_focus", meetings))
    home.registry.expected_focus = ("custom_focus",)
    try:
        body = client.get("/api/v1/home/summary", headers=OWNER).json()
    finally:
        home.registry.expected_focus = ("meetings",)
    assert any(item["kind"] == "meeting_today" for item in body["focus"])
    assert body["counts"]["custom_focus"] == 1
    assert [s["status"] for s in body["sources"] if s["name"] == "custom_focus"] == ["ok"]


def test_profile_name_is_the_callers_own_and_audited(client: TestClient) -> None:
    saved = client.put("/api/v1/home/profile", headers=OWNER, json={"display_name": "  Shahzeb  "})
    assert saved.status_code == 200 and saved.json()["display_name"] == "Shahzeb"
    assert client.get("/api/v1/home/summary", headers=OWNER).json()["profile"]["display_name"] == "Shahzeb"
    assert client.get("/api/v1/home/summary", headers=MEMBER).json()["profile"]["display_name"] is None
    cleared = client.put("/api/v1/home/profile", headers=OWNER, json={"display_name": "   "})
    assert cleared.json()["display_name"] is None
    assert len(_audits("home.profile_updated")) == 2
    assert client.put("/api/v1/home/profile", headers=OWNER, json={"user_id": "x"}).status_code == 422


# --------------------------------------------------------------------------- pins


def test_pins_add_list_update_reorder_and_remove(client: TestClient) -> None:
    first = client.post(
        "/api/v1/home/pins", headers=OWNER,
        json={"kind": "link", "label": "TyrePulse", "target": "https://tyrepulse.example/app", "icon": "tyre"},
    )
    assert first.status_code == 201, first.text
    second = client.post("/api/v1/home/pins", headers=OWNER, json={"kind": "feature", "label": "Reports", "target": "reports"})
    assert second.status_code == 201
    ids = [p["id"] for p in client.get("/api/v1/home/pins", headers=OWNER).json()]
    assert ids == [first.json()["id"], second.json()["id"]]

    reordered = client.put("/api/v1/home/pins/order", headers=OWNER, json={"ids": list(reversed(ids))})
    assert [p["id"] for p in reordered.json()] == list(reversed(ids))
    assert client.put("/api/v1/home/pins/order", headers=OWNER, json={"ids": ids[:1]}).status_code == 422

    renamed = client.patch(f"/api/v1/home/pins/{ids[0]}", headers=OWNER, json={"label": "Tyre Pulse"})
    assert renamed.json()["label"] == "Tyre Pulse"
    assert client.patch(f"/api/v1/home/pins/{ids[0]}", headers=OWNER, json={"target": "http://insecure.example"}).status_code == 422

    assert client.delete(f"/api/v1/home/pins/{ids[0]}", headers=OWNER).status_code == 204
    assert client.delete(f"/api/v1/home/pins/{ids[0]}", headers=OWNER).status_code == 404
    assert [p["id"] for p in client.get("/api/v1/home/pins", headers=OWNER).json()] == [ids[1]]
    for action in ("home.pin_added", "home.pin_updated", "home.pins_reordered", "home.pin_removed"):
        assert _audits(action), action
    assert all(r.actor == "owner_home" for r in store.audit_records)


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "link", "label": "Plain http", "target": "http://example.org"},
        {"kind": "link", "label": "Credentials", "target": "https://user:pass@example.org"},
        {"kind": "link", "label": "Script", "target": "javascript:alert(1)"},
        {"kind": "feature", "label": "Nope", "target": "billing"},
        {"kind": "feature", "label": "  ", "target": "tasks"},
        {"kind": "feature", "label": "Icon", "target": "tasks", "icon": "Bad Icon"},
        {"kind": "feature", "label": "Extra", "target": "tasks", "user_id": "someone_else"},
    ],
)
def test_pin_validation(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/api/v1/home/pins", headers=OWNER, json=body).status_code == 422


def test_pins_refuse_duplicates_limits_and_foreign_workspaces(client: TestClient) -> None:
    body = {"kind": "feature", "label": "Tasks", "target": "tasks"}
    assert client.post("/api/v1/home/pins", headers=OWNER, json=body).status_code == 201
    assert client.post("/api/v1/home/pins", headers=OWNER, json=body).status_code == 409

    foreign = client.post("/api/v1/home/pins", headers=OWNER, json={"kind": "workspace", "label": "PMV", "target": "workspace_pmv"})
    assert foreign.status_code == 422
    store.workspaces["workspace_pmv"] = Workspace(
        id="workspace_pmv", tenant_id=TENANT, name="PMV Operations", created_at=utc_now(), updated_at=utc_now()
    )
    store.memberships[(TENANT, "workspace_pmv", "owner_home")] = WorkspaceMembership(
        tenant_id=TENANT, workspace_id="workspace_pmv", user_id="owner_home", role="owner", created_at=utc_now(), updated_at=utc_now()
    )
    mine = client.post("/api/v1/home/pins", headers=OWNER, json={"kind": "workspace", "label": "PMV", "target": "workspace_pmv"})
    assert mine.status_code == 201

    for index in range(home.MAX_PINS - 2):
        assert client.post(
            "/api/v1/home/pins", headers=OWNER, json={"kind": "link", "label": f"App {index}", "target": f"https://app{index}.example"}
        ).status_code == 201
    over = client.post("/api/v1/home/pins", headers=OWNER, json={"kind": "link", "label": "One more", "target": "https://more.example"})
    assert over.status_code == 409 and "Remove one" in over.json()["error"]["message"]


def test_pins_are_private_to_each_user(client: TestClient) -> None:
    created = client.post("/api/v1/home/pins", headers=OWNER, json={"kind": "feature", "label": "Tasks", "target": "tasks"}).json()
    assert client.get("/api/v1/home/pins", headers=MEMBER).json() == []
    assert client.delete(f"/api/v1/home/pins/{created['id']}", headers=MEMBER).status_code == 404
    assert client.patch(f"/api/v1/home/pins/{created['id']}", headers=MEMBER, json={"label": "x"}).status_code == 404
    other_workspace = headers(workspace="workspace_other")
    assert client.get("/api/v1/home/pins", headers=other_workspace).json() == []


# --------------------------------------------------------------------------- quick commands


def test_quick_commands_built_in_plus_own_and_viewers_cannot_edit(client: TestClient) -> None:
    listed = client.get("/api/v1/home/commands", headers=MEMBER).json()
    keys = {item["key"] for item in listed["items"] if item["built_in"]}
    assert {"check_email", "summarize_day", "plan_day"} <= keys and listed["can_edit"] is True
    assert {item["mode"] for item in listed["items"]} == {"work", "personal"}

    created = client.post(
        "/api/v1/home/commands", headers=MEMBER, json={"mode": "custom", "label": "Tyre stock", "prompt": "Check tyre stock on Noon"}
    )
    assert created.status_code == 201
    command_id = created.json()["id"]
    updated = client.patch(f"/api/v1/home/commands/{command_id}", headers=MEMBER, json={"mode": "work"})
    assert updated.json()["mode"] == "work" and updated.json()["label"] == "Tyre stock"
    own = [i for i in client.get("/api/v1/home/commands", headers=MEMBER).json()["items"] if not i["built_in"]]
    assert [i["id"] for i in own] == [command_id]
    assert client.get("/api/v1/home/commands", headers=OWNER).json()["items"][-1]["built_in"] is True  # not shared
    assert client.delete(f"/api/v1/home/commands/{command_id}", headers=MEMBER).status_code == 204
    assert client.delete("/api/v1/home/commands/builtin:check_email", headers=MEMBER).status_code == 404

    viewer = client.get("/api/v1/home/commands", headers=VIEWER).json()
    assert viewer == {"items": [], "can_edit": False}
    denied = client.post("/api/v1/home/commands", headers=VIEWER, json={"label": "x", "prompt": "y"})
    assert denied.status_code == 403 and "Viewers" in denied.json()["error"]["message"]
    assert client.post("/api/v1/home/commands", headers=MEMBER, json={"label": " ", "prompt": "y"}).status_code == 422
    assert _audits("home.command_added") and _audits("home.command_updated") and _audits("home.command_removed")


# --------------------------------------------------------------------------- insights


def test_insights_cover_tasks_approvals_autonomy_automations_and_integrations(client: TestClient) -> None:
    _task("task_failed", TaskStatus.FAILED, title="Supplier quotation")
    _task("task_stuck", TaskStatus.RUNNING, title="Stock sync", age=timedelta(hours=3))
    _approval("appr_old", age=timedelta(hours=30))
    _approval("appr_new", age=timedelta(minutes=5))
    autonomy_memory_store.save_proposal(
        AutonomyProposal(
            id="prop_one", tenant_id=TENANT, workspace_id=WORKSPACE, pattern_key="k", tool="email.archive",
            category=ToolCategory.EMAIL, action_kind=ActionKind.ARCHIVE, approvals=3, status=ProposalStatus.PENDING, created_at=utc_now(),
        )
    )
    engine = automation_module.engine
    workflow = engine.create_workflow(
        ctx(), WorkflowCreate(name="Daily tyre report", steps=[WorkflowStep(id="s0", name="Step 0", action="fail", input={"message": "Disk full"})])
    )
    engine.start(ctx(), workflow.id)
    from anum_api import main

    main.integration_registry.memory_configurations.save_check(
        ctx(), "external-webhook",
        IntegrationCheck(status=IntegrationStatus.DEGRADED, detail="timeout", checked_by="system", checked_at=utc_now()),
    )

    body = client.get("/api/v1/home/insights", headers=OWNER).json()
    by_kind = {item["kind"]: item for item in body["items"]}
    assert set(by_kind) >= {"task_failed", "task_stalled", "approval_waiting", "autonomy_proposal", "automation_failing"}
    assert by_kind["task_failed"]["action"]["type"] == "command" and "Supplier quotation" in by_kind["task_failed"]["action"]["command"]
    assert by_kind["task_stalled"]["action"] == {
        "type": "open", "verb": "check_now", "target": {"feature": "tasks", "id": "task_stuck"}, "command": None
    }
    assert by_kind["approval_waiting"]["key"] == "approvals:appr_old"
    assert all(item["key"] != "approvals:appr_new" for item in body["items"])
    assert by_kind["autonomy_proposal"]["action"]["target"] == {"feature": "autonomy", "id": "prop_one"}
    assert by_kind["automation_failing"]["params"]["name"] == "Daily tyre report"
    assert by_kind["integration_degraded"]["action"]["target"] == {"feature": "integrations", "id": "external-webhook"}
    assert body["items"][0]["severity"] == "high"

    member = client.get("/api/v1/home/insights", headers=VIEWER).json()
    statuses = {s["name"]: s["status"] for s in member["sources"]}
    assert statuses["email"] == "not_permitted"


def test_dismiss_snooze_and_restore_are_per_user(client: TestClient) -> None:
    _task("task_failed", TaskStatus.FAILED)
    key = "tasks:failed:task_failed"
    dismissed = client.post(f"/api/v1/home/insights/{key}/dismiss", headers=OWNER)
    assert dismissed.status_code == 200 and dismissed.json()["state"] == "dismissed"
    visible = client.get("/api/v1/home/insights", headers=OWNER).json()
    assert all(item["key"] != key for item in visible["items"]) and visible["hidden"] == 1
    hidden = client.get("/api/v1/home/insights?include_hidden=true", headers=OWNER).json()
    assert [item["state"] for item in hidden["items"] if item["key"] == key] == ["dismissed"]
    # Another user still sees it.
    assert any(item["key"] == key for item in client.get("/api/v1/home/insights", headers=MEMBER).json()["items"])

    assert client.delete(f"/api/v1/home/insights/{key}/state", headers=OWNER).status_code == 204
    assert client.delete(f"/api/v1/home/insights/{key}/state", headers=OWNER).status_code == 404
    snoozed = client.post(f"/api/v1/home/insights/{key}/snooze", headers=OWNER, json={"hours": 4})
    assert snoozed.json()["state"] == "snoozed" and snoozed.json()["snoozed_until"]
    later = client.post(f"/api/v1/home/insights/{key}/snooze", headers=OWNER)
    assert later.status_code == 200
    assert client.post(f"/api/v1/home/insights/{key}/snooze", headers=OWNER, json={"hours": 500}).status_code == 422
    for action in ("home.insight_dismissed", "home.insight_restored", "home.insight_snoozed"):
        assert _audits(action), action

    # The snooze runs out: the insight is new again.
    state = home_store.list_insight_states(ctx())[key]
    home_store.save_insight_state(ctx(), state.model_copy(update={"until": utc_now() - timedelta(minutes=1)}))
    assert any(item["key"] == key and item["state"] == "new" for item in client.get("/api/v1/home/insights", headers=OWNER).json()["items"])


def test_dismissing_an_unknown_or_malformed_key_is_refused(client: TestClient) -> None:
    assert client.post("/api/v1/home/insights/tasks:failed:nope/dismiss", headers=OWNER).status_code == 404
    assert client.post("/api/v1/home/insights/Bad Key/dismiss", headers=OWNER).status_code == 404
    assert home_store.list_insight_states(ctx()) == {}


def test_registered_insight_sources_are_validated_and_permission_checked(client: TestClient) -> None:
    def learning(scope: home.HomeScope) -> list[Insight]:
        good = Insight(
            key="learning:tyre_cost", source="learning", kind="pattern_cost_trend", title="Tyre cost trend",
            finding="Tyre cost rose this month.", question="Shall I prepare a detailed analysis?",
            action=InsightAction(type="command", verb="analyze", command="Analyze this month's tyre cost increase."),
        )
        wrong_prefix = good.model_copy(update={"key": "tasks:spoofed"})
        return [good, wrong_prefix, good]

    from anum_api.authorization import Permission

    home.register_insight_source(InsightSource("learning", learning, Permission.TASK_READ))
    home.register_insight_source(InsightSource("custom", learning, Permission.POLICY_MANAGE))
    body = client.get("/api/v1/home/insights", headers=MEMBER).json()
    keys = [item["key"] for item in body["items"]]
    assert keys.count("learning:tyre_cost") == 1 and "tasks:spoofed" not in keys
    assert {s["name"]: s["status"] for s in body["sources"]}["custom"] == "not_permitted"
    assert client.post("/api/v1/home/insights/learning:tyre_cost/dismiss", headers=MEMBER).status_code == 200


def test_every_home_route_needs_an_identity(client: TestClient) -> None:
    for method, path in (("GET", "/api/v1/home/summary"), ("GET", "/api/v1/home/pins"), ("GET", "/api/v1/home/insights")):
        assert client.request(method, path).status_code in (401, 403)
