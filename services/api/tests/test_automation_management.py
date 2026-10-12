"""Managing automations end to end (docs/automation.md): edit, pause, delete, overlap
policy, real step effects, failure notifications, paging, search and permissions."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import anum_api.automation as automation_module
from anum_api.audit import AuditRecord
from anum_api.automation import (
    AutomationConflict,
    DeferredStep,
    TaskOutcome,
    EditDenied,
    LocalAutomationEngine,
    OverlapPolicy,
    RepositoryEffects,
    RunStatus,
    ScheduleCreate,
    WorkflowCreate,
    WorkflowDefinition,
    WorkflowStatus,
    WorkflowStep,
    WorkflowUpdate,
)
from anum_api.automation_dispatch import automation_dispatcher
from anum_api.dependencies import memory_repository
from anum_api.main import app
from anum_api.onboarding import NotificationPreferences, _notifications
from anum_api.schemas import DomainEvent, Task, TaskStatus, TenantContext, WorkspaceMembership, utc_now
from anum_api.settings import settings


class RecordingEffects:
    """Effects that keep everything in lists, so engine tests do not touch shared state."""

    def __init__(self, opted_out: set[str] | None = None, inactive: set[str] | None = None) -> None:
        self.tasks: list[Task] = []
        self.events: list[DomainEvent] = []
        self.audits: list[AuditRecord] = []
        self.opted_out = opted_out or set()
        self.inactive = inactive or set()
        self.deferred: list[DeferredStep] = []
        self.outcomes: dict[str, dict[str, Any]] = {}

    def create_task(self, context: TenantContext, title: str, prompt: str) -> Task:
        now = utc_now()
        task = Task(id=f"task_{len(self.tasks) + 1}", title=title, prompt=prompt, status="created", tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id, created_at=now, updated_at=now, created_by=context.user_id)
        self.tasks.append(task)
        return task

    def record_event(self, event: DomainEvent) -> None:
        self.events.append(event)

    def record_audit(self, record: AuditRecord) -> None:
        self.audits.append(record)

    def wants_failure_notice(self, context: TenantContext, user_id: str) -> bool:
        return user_id not in self.opted_out

    def actor_may_run(self, context: TenantContext, user_id: str) -> bool:
        return user_id not in self.inactive

    def task_outcome(self, context: TenantContext, task_id: str) -> TaskOutcome | None:
        task = next((task for task in self.tasks if task.id == task_id), None)
        if task is None:
            return None
        extra = self.outcomes.get(task_id, {})
        return TaskOutcome(status=task.status, updated_at=task.updated_at, **extra)

    def events_since(self, context: TenantContext, event_type: str, after: Any, limit: int) -> list[DomainEvent]:
        return [e for e in self.events if e.type == event_type and e.created_at >= after][:limit]

    def defer(self, job: DeferredStep) -> None:
        self.deferred.append(job)

    def flush(self) -> None:
        pass

    def types(self) -> list[str]:
        return [event.type for event in self.events]


def ctx(user: str = "user_a", role: str = "member", workspace: str = "workspace_a") -> TenantContext:
    return TenantContext(tenant_id="tenant_a", workspace_id=workspace, user_id=user, roles=[role])


def steps(*actions: str, **inputs: Any) -> list[WorkflowStep]:
    return [WorkflowStep(id=f"s{i}", name=f"Step {i}", action=action, input=inputs.get(action, {})) for i, action in enumerate(actions)]


def flow(*actions: str, policy: OverlapPolicy = OverlapPolicy.ALLOW, name: str = "Ops", **inputs: Any) -> WorkflowCreate:
    return WorkflowCreate(name=name, steps=steps(*actions, **inputs), overlap_policy=policy)


@pytest.fixture
def effects() -> RecordingEffects:
    return RecordingEffects()


@pytest.fixture
def engine(tmp_path: Path, effects: RecordingEffects) -> LocalAutomationEngine:
    return LocalAutomationEngine(str(tmp_path / "automation.db"), effects_factory=lambda: effects)


# Validation ------------------------------------------------------------------------------


def test_new_workflows_must_use_known_actions_with_their_inputs() -> None:
    with pytest.raises(ValueError, match=r"Step 1 \(\"Step 0\"\): action 'task.execute' is not available. Choose one of: task.create"):
        flow("task.execute")
    with pytest.raises(ValueError, match="needs a title and a prompt"):
        flow("task.create", **{"task.create": {"title": "Report"}})
    with pytest.raises(ValueError, match="message must be text"):
        flow("notify", notify={"message": 3})
    assert flow("task.create", **{"task.create": {"title": "Report", "prompt": "Summarise"}}).steps[0].action == "task.create"


def test_stored_legacy_actions_still_load_and_run(engine: LocalAutomationEngine, effects: RecordingEffects) -> None:
    created = engine.create_workflow(ctx(), flow("notify"))
    legacy = WorkflowDefinition.model_validate({**created.model_dump(), "steps": [{"id": "x", "name": "Old", "action": "task.execute"}]})
    with engine._tx() as (connection, _):
        engine._put(connection, "automation_workflows", legacy)

    run = engine.start(ctx(), created.id)
    assert run.status == RunStatus.COMPLETED
    assert run.steps[0].output == {"accepted": True, "action": "task.execute"}


# Step effects and events -----------------------------------------------------------------


def test_task_create_step_creates_a_real_task_and_links_it(engine: LocalAutomationEngine, effects: RecordingEffects) -> None:
    created = engine.create_workflow(ctx(), flow("task.create", **{"task.create": {"title": "Weekly report", "prompt": "Summarise the week"}}))
    run = engine.start(ctx(), created.id)

    # The task is created in the run's transaction and handed to the dispatcher to run.
    assert run.status == RunStatus.RUNNING and run.workflow_version == 1 and run.trigger == "manual"
    [task] = effects.tasks
    assert (task.title, task.prompt, task.created_by) == ("Weekly report", "Summarise the week", "user_a")
    assert run.steps[0].output == {"task_id": task.id, "title": "Weekly report"}
    assert run.steps[0].input == {"title": "Weekly report", "prompt": "Summarise the week"}
    assert run.waiting_for == "task" and run.waiting_task_id == task.id
    [job] = effects.deferred
    assert (job.action, job.task_id, job.actor, job.run_id) == ("task.create", task.id, "user_a", run.id)

    # The agent run finishes: the step completes with the task's result.
    task.status = TaskStatus.COMPLETED
    effects.outcomes[task.id] = {"result": "Sales up 4%"}
    [done] = engine.wake_for_task(ctx(), task.id)
    assert done.status == RunStatus.COMPLETED and done.steps[0].output["result"] == "Sales up 4%"
    assert effects.types()[-1] == "automation_run.completed"


def test_notify_step_notifies_the_run_owner(engine: LocalAutomationEngine, effects: RecordingEffects) -> None:
    created = engine.create_workflow(ctx(), flow("notify", notify={"message": "Backups verified"}))
    run = engine.start(ctx("user_b"), created.id)

    notice = next(event for event in effects.events if event.type == "automation.notification")
    assert notice.payload["message"] == "Backups verified" and notice.payload["notify_user_ids"] == ["user_b"]
    assert notice.payload["run_id"] == run.id and run.steps[0].output["notified"] == ["user_b"]


def test_failure_notifies_run_owner_and_creator_who_want_it(tmp_path: Path) -> None:
    effects = RecordingEffects(opted_out={"user_c"})
    engine = LocalAutomationEngine(str(tmp_path / "a.db"), effects_factory=lambda: effects)
    created = engine.create_workflow(ctx("user_a"), flow("fail", fail={"message": "Disk full"}))

    run = engine.start(ctx("user_b"), created.id)
    assert run.status == RunStatus.FAILED and run.error == "Disk full"
    failed = next(event for event in effects.events if event.type == "automation_run.failed")
    assert failed.payload["notify_user_ids"] == ["user_b", "user_a"]
    assert failed.payload["error"] == "Disk full" and failed.payload["failed_step"] == "Step 0"

    engine.start(ctx("user_c"), created.id)
    second = [event for event in effects.events if event.type == "automation_run.failed"][-1]
    assert second.payload["notify_user_ids"] == ["user_a"]  # user_c turned automation_failed off


def test_repository_effects_read_the_automation_failed_preference() -> None:
    context = ctx("pref_user", workspace="workspace_prefs")
    effects = RepositoryEffects(memory_repository, _notifications.get)
    assert effects.wants_failure_notice(context, "pref_user") is True  # no saved preferences: default on
    _notifications.save(context, NotificationPreferences(automation_failed=False))
    try:
        assert effects.wants_failure_notice(context, "pref_user") is False
    finally:
        _notifications.save(context, NotificationPreferences())


def test_cancel_and_pause_publish_events(engine: LocalAutomationEngine, effects: RecordingEffects) -> None:
    created = engine.create_workflow(ctx(), flow("pause", "notify"))
    run = engine.start(ctx(), created.id)
    assert effects.types()[-1] == "automation_run.paused"
    engine.cancel(ctx(), run.id)
    assert effects.types()[-1] == "automation_run.cancelled"
    with pytest.raises(AutomationConflict, match="already cancelled"):
        engine.cancel(ctx(), run.id)


# Editing, pausing, deleting ---------------------------------------------------------------


def test_edit_bumps_version_and_paused_runs_keep_their_snapshot(engine: LocalAutomationEngine, effects: RecordingEffects) -> None:
    created = engine.create_workflow(ctx(), flow("pause", "notify", notify={"message": "old"}))
    paused = engine.start(ctx(), created.id)

    updated = engine.update_workflow(ctx(), created.id, WorkflowUpdate(name="Ops v2", steps=steps("notify", notify={"message": "new"})))
    assert (updated.version, updated.name, updated.updated_by) == (2, "Ops v2", "user_a")
    resumed = engine.resume(ctx(), paused.id)
    assert resumed.status == RunStatus.COMPLETED and resumed.steps[1].output["message"] == "old"
    assert [a.action for a in effects.audits] == ["automation.workflow.created", "automation.workflow.updated"]


def test_members_change_only_their_own_automations_owners_change_any(engine: LocalAutomationEngine) -> None:
    created = engine.create_workflow(ctx("user_a"), flow("notify"))
    with pytest.raises(EditDenied, match=r"Only the automation's creator \(user_a\) or a workspace owner"):
        engine.update_workflow(ctx("user_b"), created.id, WorkflowUpdate(name="Hijack", steps=steps("notify")))
    with pytest.raises(EditDenied):
        engine.delete_workflow(ctx("user_b"), created.id)
    schedule = engine.create_schedule(ctx("user_a"), ScheduleCreate(workflow_id=created.id, name="Daily", cron="0 8 * * *"))
    with pytest.raises(EditDenied, match="schedule's creator"):
        engine.delete_schedule(ctx("user_b"), schedule.id)

    paused = engine.set_workflow_status(ctx("boss", role="owner"), created.id, WorkflowStatus.DISABLED)
    assert paused.status == WorkflowStatus.DISABLED and paused.next_run_at is None


def test_paused_automation_refuses_runs_and_skips_fires(engine: LocalAutomationEngine) -> None:
    created = engine.create_workflow(ctx(), flow("notify"))
    schedule = engine.create_schedule(ctx(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    engine.set_workflow_status(ctx(), created.id, WorkflowStatus.DISABLED)
    with pytest.raises(AutomationConflict, match="is paused. Resume the automation"):
        engine.start(ctx(), created.id)

    fire_at = engine.get_schedule(ctx(), schedule.id).next_run_at
    assert fire_at is not None
    assert engine.run_due_schedules(now=fire_at) == []
    assert engine.get_schedule(ctx(), schedule.id).last_outcome == "workflow_paused"

    resumed = engine.set_workflow_status(ctx(), created.id, WorkflowStatus.ACTIVE)
    assert resumed.status == WorkflowStatus.ACTIVE and resumed.next_run_at is not None


def test_delete_refuses_active_runs_then_removes_schedules_and_history(engine: LocalAutomationEngine) -> None:
    created = engine.create_workflow(ctx(), flow("pause"))
    engine.create_schedule(ctx(), ScheduleCreate(workflow_id=created.id, name="Daily", cron="0 8 * * *"))
    run = engine.start(ctx(), created.id)
    with pytest.raises(AutomationConflict, match="1 active run"):
        engine.delete_workflow(ctx(), created.id)

    engine.cancel(ctx(), run.id)
    engine.delete_workflow(ctx(), created.id)
    assert engine.list_workflows(ctx()) == [] and engine.list_schedules(ctx()) == [] and engine.list_runs(ctx()) == []


# Overlap policy --------------------------------------------------------------------------


def test_skip_policy_refuses_a_second_manual_run(engine: LocalAutomationEngine) -> None:
    created = engine.create_workflow(ctx(), flow("pause", policy=OverlapPolicy.SKIP))
    first = engine.start(ctx(), created.id)
    with pytest.raises(AutomationConflict, match="overlap policy is skip"):
        engine.start(ctx(), created.id)
    engine.cancel(ctx(), first.id)
    assert engine.start(ctx(), created.id).status == RunStatus.PAUSED


def test_skip_policy_skips_a_scheduled_fire_while_a_run_is_active(engine: LocalAutomationEngine) -> None:
    created = engine.create_workflow(ctx(), flow("pause", policy=OverlapPolicy.SKIP))
    schedule = engine.create_schedule(ctx(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    engine.start(ctx(), created.id)
    fire_at = engine.get_schedule(ctx(), schedule.id).next_run_at
    assert fire_at is not None

    assert engine.run_due_schedules(now=fire_at) == []
    after = engine.get_schedule(ctx(), schedule.id)
    assert after.last_outcome == "skipped_overlap" and after.next_run_at == fire_at + timedelta(hours=1)


def test_cancel_previous_policy_cancels_active_runs_first(engine: LocalAutomationEngine) -> None:
    created = engine.create_workflow(ctx(), flow("pause", policy=OverlapPolicy.CANCEL_PREVIOUS))
    first = engine.start(ctx(), created.id)
    second = engine.start(ctx(), created.id)
    assert engine.get_run(ctx(), first.id).status == RunStatus.CANCELLED
    assert second.status == RunStatus.PAUSED


def test_scheduled_fire_by_an_inactive_owner_records_a_failed_run(tmp_path: Path) -> None:
    effects = RecordingEffects(inactive={"user_a"})
    engine = LocalAutomationEngine(str(tmp_path / "a.db"), effects_factory=lambda: effects)
    created = engine.create_workflow(ctx(), flow("task.create", **{"task.create": {"title": "T", "prompt": "P"}}))
    schedule = engine.create_schedule(ctx(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    fire_at = engine.get_schedule(ctx(), schedule.id).next_run_at
    assert fire_at is not None

    [run] = engine.run_due_schedules(now=fire_at)
    assert run.status == RunStatus.FAILED and "no longer an active owner or member" in (run.error or "")
    assert effects.tasks == []  # nothing ran as the removed user
    assert engine.get_schedule(ctx(), schedule.id).last_outcome == "owner_inactive"


def test_actor_check_uses_persisted_membership_with_oidc(monkeypatch: pytest.MonkeyPatch) -> None:
    effects = RepositoryEffects(memory_repository, _notifications.get)
    context = ctx("member_x", workspace="workspace_actor")
    assert effects.actor_may_run(context, "member_x") is True  # header auth: roles are not persisted
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    assert effects.actor_may_run(context, "member_x") is False
    now = utc_now()
    memory_repository.save_membership(WorkspaceMembership(tenant_id="tenant_a", workspace_id="workspace_actor", user_id="member_x",
                                                          role="member", created_at=now, updated_at=now))
    assert effects.actor_may_run(context, "member_x") is True


# Summaries, paging and search --------------------------------------------------------------


def test_workflow_summary_reports_last_and_next_runs(engine: LocalAutomationEngine) -> None:
    created = engine.create_workflow(ctx(), flow("pause"))
    schedule = engine.create_schedule(ctx(), ScheduleCreate(workflow_id=created.id, name="Daily", cron="0 8 * * *"))
    run = engine.start(ctx(), created.id)

    summary = engine.get_workflow(ctx(), created.id)
    assert (summary.run_count, summary.active_runs, summary.schedule_count) == (1, 1, 1)
    assert summary.last_run_at == run.created_at and summary.last_run_status == RunStatus.PAUSED
    assert summary.next_run_at == schedule.next_run_at


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(automation_module, "engine", LocalAutomationEngine(str(tmp_path / "api.db")))
    automation_dispatcher.clear()
    return TestClient(app)


def headers(user: str = "owner_a", role: str = "owner", workspace: str = "workspace_api") -> dict[str, str]:
    return {"x-tenant-id": "tenant_api", "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


def body(name: str, *actions: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "steps": [{"id": f"s{i}", "name": f"S{i}", "action": a, "input": extra.get(a, {})} for i, a in enumerate(actions)]}


def test_api_pages_and_searches_workflows_and_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    ids = [client.post("/api/v1/automation/workflows", headers=headers(), json=body(f"Report {i}", "notify")).json()["id"] for i in range(5)]
    client.post("/api/v1/automation/workflows", headers=headers(), json=body("Invoice sweep", "notify"))

    page = client.get("/api/v1/automation/workflows?limit=2&offset=2", headers=headers())
    assert page.status_code == 200 and page.headers["X-Total-Count"] == "6" and len(page.json()) == 2
    found = client.get("/api/v1/automation/workflows?query=invoice", headers=headers())
    assert [w["name"] for w in found.json()] == ["Invoice sweep"] and found.headers["X-Total-Count"] == "1"

    for _ in range(3):
        client.post(f"/api/v1/automation/workflows/{ids[0]}/runs", headers=headers())
    client.post(f"/api/v1/automation/workflows/{ids[1]}/runs", headers=headers())
    history = client.get(f"/api/v1/automation/runs?workflow_id={ids[0]}&limit=2", headers=headers())
    assert history.headers["X-Total-Count"] == "3" and len(history.json()) == 2
    assert {run["workflow_name"] for run in history.json()} == {"Report 0"}
    one = client.get(f"/api/v1/automation/runs/{history.json()[0]['id']}", headers=headers())
    assert one.status_code == 200 and one.json()["trigger"] == "manual"
    assert client.get("/api/v1/automation/runs?status=completed", headers=headers()).headers["X-Total-Count"] == "4"
    assert client.get("/api/v1/automation/runs/automation_run_missing", headers=headers()).status_code == 404


def test_api_edit_pause_resume_and_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    created = client.post("/api/v1/automation/workflows", headers=headers(), json={**body("Nightly", "notify"), "overlap_policy": "skip"}).json()
    assert created["overlap_policy"] == "skip" and created["created_by"] == "owner_a"

    edited = client.put(f"/api/v1/automation/workflows/{created['id']}", headers=headers(), json=body("Nightly v2", "notify", "pause"))
    assert edited.status_code == 200 and edited.json()["version"] == 2 and edited.json()["overlap_policy"] == "allow"
    paused = client.post(f"/api/v1/automation/workflows/{created['id']}/disable", headers=headers())
    assert paused.json()["status"] == "disabled"
    refused = client.post(f"/api/v1/automation/workflows/{created['id']}/runs", headers=headers())
    assert refused.status_code == 409 and "Resume the automation" in refused.json()["error"]["message"]
    assert client.post(f"/api/v1/automation/workflows/{created['id']}/enable", headers=headers()).json()["status"] == "active"
    assert client.get(f"/api/v1/automation/workflows/{created['id']}", headers=headers()).json()["name"] == "Nightly v2"

    assert client.delete(f"/api/v1/automation/workflows/{created['id']}", headers=headers()).status_code == 204
    missing = client.get(f"/api/v1/automation/workflows/{created['id']}", headers=headers())
    assert missing.status_code == 404 and "refresh the list" in missing.json()["error"]["message"]


def test_api_permission_refusals_say_who_can(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    created = client.post("/api/v1/automation/workflows", headers=headers("member_a", "member"), json=body("Mine", "notify")).json()

    viewer = client.post("/api/v1/automation/workflows", headers=headers("viewer_a", "viewer"), json=body("X", "notify"))
    assert viewer.status_code == 403 and "owner or member role" in viewer.json()["error"]["message"]
    assert client.get("/api/v1/automation/workflows", headers=headers("viewer_a", "viewer")).status_code == 200
    other = client.put(f"/api/v1/automation/workflows/{created['id']}", headers=headers("member_b", "member"), json=body("Theirs", "notify"))
    assert other.status_code == 403 and "creator (member_a) or a workspace owner" in other.json()["error"]["message"]
    owner = client.put(f"/api/v1/automation/workflows/{created['id']}", headers=headers("owner_a", "owner"), json=body("Owned", "notify"))
    assert owner.status_code == 200
    # Running someone else's automation is allowed for members.
    assert client.post(f"/api/v1/automation/workflows/{created['id']}/runs", headers=headers("member_b", "member")).status_code == 201


def test_api_validation_errors_explain_the_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    bad = client.post("/api/v1/automation/workflows", headers=headers(), json=body("X", "task.execute"))
    assert bad.status_code == 422
    assert "Choose one of: task.create, email.send, notify, pause, fail" in bad.json()["error"]["details"][0]["message"]
    wf = client.post("/api/v1/automation/workflows", headers=headers(), json=body("X", "notify")).json()
    cron_bad = client.post("/api/v1/automation/schedules", headers=headers(), json={"workflow_id": wf["id"], "name": "S", "cron": "0 25 * * *"})
    assert cron_bad.status_code == 422 and "Hour field: 25 is outside 0-23" in cron_bad.json()["error"]["details"][0]["message"]
    zone_bad = client.put(
        f"/api/v1/automation/schedules/{client.post('/api/v1/automation/schedules', headers=headers(), json={'workflow_id': wf['id'], 'name': 'S', 'cron': '0 8 * * *'}).json()['id']}",
        headers=headers(),
        json={"timezone": "Mars/Base"},
    )
    assert zone_bad.status_code == 422 and "IANA time zone" in zone_bad.json()["error"]["details"][0]["message"]


def test_api_schedule_preview_describes_and_lists_next_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    preview = client.post("/api/v1/automation/schedules/preview", headers=headers("viewer_a", "viewer"),
                          json={"cron": "0 9 * * 1-5", "timezone": "Asia/Riyadh", "count": 3})
    assert preview.status_code == 200
    data = preview.json()
    assert data["description"] == "At 09:00, Monday through Friday" and len(data["next_runs"]) == 3
    assert all(run.endswith("06:00:00Z") for run in data["next_runs"])  # 09:00 in Riyadh is 06:00 UTC
    invalid = client.post("/api/v1/automation/schedules/preview", headers=headers(), json={"cron": "0 9 * *"})
    assert invalid.status_code == 422 and "five fields" in invalid.json()["error"]["details"][0]["message"]
    schedules = client.get("/api/v1/automation/schedules", headers=headers())
    assert schedules.status_code == 200 and schedules.headers["X-Total-Count"] == "0"


def test_api_task_create_step_creates_a_task_in_the_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    scope = headers(workspace="workspace_tasks")
    wf = client.post("/api/v1/automation/workflows", headers=scope,
                     json=body("Digest", "task.create", **{"task.create": {"title": "Daily digest", "prompt": "Summarise yesterday"}})).json()
    run = client.post(f"/api/v1/automation/workflows/{wf['id']}/runs", headers=scope).json()
    task_id = run["steps"][0]["output"]["task_id"]

    assert run["status"] == "running" and run["waiting_for"] == "task"
    # After the run's transaction commits, the dispatcher runs the task through the runtime.
    asyncio.run(automation_dispatcher.run_pending())
    task = client.get(f"/api/v1/tasks/{task_id}", headers=scope)
    assert task.status_code == 200 and task.json()["title"] == "Daily digest" and task.json()["created_by"] == "owner_a"
    assert task.json()["status"] == "completed"
    finished = client.get(f"/api/v1/automation/runs/{run['id']}", headers=scope).json()
    assert finished["status"] == "completed" and finished["steps"][0]["output"]["status"] == "completed"
    assert finished["steps"][0]["output"]["result"]
    events = client.get("/api/v1/events", headers=scope).json()
    assert {"task.created", "automation_run.completed"} <= {event["type"] for event in events}


def test_every_state_changing_automation_route_is_audited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from audit_probe import AuditProbe

    from anum_api.store import store

    store.audit_records.clear()
    probe = AuditProbe(_client(tmp_path, monkeypatch), "automation")
    owner, base = headers(), "/api/v1/automation"
    workflow = probe.call("POST", f"{base}/workflows", owner, json=body("Nightly", "notify", "pause")).json()
    probe.call("PUT", f"{base}/workflows/{workflow['id']}", owner, json=body("Nightly v2", "notify", "pause"))
    probe.call("POST", f"{base}/workflows/{workflow['id']}/disable", owner)
    probe.call("POST", f"{base}/workflows/{workflow['id']}/enable", owner)
    schedule = probe.call(
        "POST", f"{base}/schedules", owner, json={"workflow_id": workflow["id"], "name": "Every morning", "cron": "0 8 * * *"}
    ).json()
    probe.call("PUT", f"{base}/schedules/{schedule['id']}", owner, json={"cron": "0 9 * * *"})
    probe.call("POST", f"{base}/schedules/{schedule['id']}/disable", owner)
    probe.call("DELETE", f"{base}/schedules/{schedule['id']}", owner)
    paused = probe.call("POST", f"{base}/workflows/{workflow['id']}/runs", owner).json()
    assert paused["status"] == "paused"
    probe.call("POST", f"{base}/runs/{paused['id']}/resume", owner)
    second = probe.call("POST", f"{base}/workflows/{workflow['id']}/runs", owner).json()
    probe.call("POST", f"{base}/runs/{second['id']}/cancel", owner)
    retried = probe.call("POST", f"{base}/runs/{second['id']}/retry", owner).json()
    probe.call("POST", f"{base}/runs/{retried['id']}/cancel", owner)
    probe.call("DELETE", f"{base}/workflows/{workflow['id']}", owner)
    probe.assert_covered()
