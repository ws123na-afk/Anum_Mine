import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import anum_api.automation as automation_module
from anum_api.automation import (
    AutomationScheduler,
    LocalAutomationEngine,
    RunStatus,
    ScheduleCreate,
    ScheduleUpdate,
    WorkflowCreate,
    WorkflowStep,
    build_automation_scheduler,
)
from anum_api.schemas import TenantContext
from anum_api.main import app


def context(workspace: str = "workspace_a") -> TenantContext:
    return TenantContext(tenant_id="tenant_a", workspace_id=workspace, user_id="user_a", roles=["owner"])


def workflow(action: str = "notify") -> WorkflowCreate:
    return WorkflowCreate(
        name="Morning operations",
        description="Execute the daily operating checks",
        steps=[WorkflowStep(id="check", name="Check queue", action=action)],
    )


def test_workflows_and_schedules_are_durable_and_scoped(tmp_path: Path) -> None:
    path = tmp_path / "automation.db"
    first = LocalAutomationEngine(str(path))
    created = first.create_workflow(context(), workflow())
    schedule = first.create_schedule(context(), ScheduleCreate(workflow_id=created.id, name="Daily", cron="0 8 * * *"))

    restarted = LocalAutomationEngine(str(path))
    assert restarted.list_workflows(context())[0].id == created.id
    assert restarted.list_schedules(context())[0].id == schedule.id
    assert restarted.list_workflows(context("workspace_b")) == []


def test_idempotent_start_returns_same_completed_run(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow())

    first = engine.start(context(), created.id, "daily-2026-08-29")
    second = engine.start(context(), created.id, "daily-2026-08-29")

    assert first.id == second.id
    assert first.status == RunStatus.COMPLETED
    assert first.steps[0].attempt == 1


def test_paused_run_can_resume_after_external_signal(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow("pause"))
    run = engine.start(context(), created.id)

    assert run.status == RunStatus.PAUSED
    resumed = engine.resume(context(), run.id)
    assert resumed.status == RunStatus.COMPLETED
    assert resumed.steps[0].output == {"resumed": True}


def test_cancel_and_retry_preserve_lineage(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow("pause"))
    cancelled = engine.cancel(context(), engine.start(context(), created.id).id)
    retried = engine.start(context(), created.id, retry_of=cancelled.id)

    assert cancelled.status == RunStatus.CANCELLED
    assert retried.retry_of == cancelled.id
    assert retried.id != cancelled.id


def test_invalid_transitions_are_rejected(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow())
    run = engine.start(context(), created.id)

    with pytest.raises(ValueError, match="cancelled"):
        engine.cancel(context(), run.id)
    with pytest.raises(ValueError, match="paused"):
        engine.resume(context(), run.id)


def test_automation_api_supports_create_start_and_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(automation_module, "engine", LocalAutomationEngine(str(tmp_path / "api.db")))
    client = TestClient(app)
    headers = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a", "x-user-id": "owner_a", "x-user-roles": "owner"}
    created = client.post("/api/v1/automation/workflows", headers=headers, json={"name": "Approval wait", "steps": [{"id": "wait", "name": "Wait", "action": "pause"}]})
    assert created.status_code == 201
    run = client.post(f"/api/v1/automation/workflows/{created.json()['id']}/runs", headers={**headers, "Idempotency-Key": "approval-wait-1"})
    assert run.status_code == 201 and run.json()["status"] == "paused"
    resumed = client.post(f"/api/v1/automation/runs/{run.json()['id']}/resume", headers=headers)
    assert resumed.status_code == 200 and resumed.json()["status"] == "completed"


def test_viewer_cannot_manage_automation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(automation_module, "engine", LocalAutomationEngine(str(tmp_path / "api.db")))
    headers = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a", "x-user-id": "viewer_a", "x-user-roles": "viewer"}
    response = TestClient(app).post("/api/v1/automation/workflows", headers=headers, json={"name": "Blocked", "steps": [{"id": "one", "name": "One", "action": "notify"}]})
    assert response.status_code == 403


# Schedules and the scheduler (local engine) ---------------------------------------------


def _due(engine: LocalAutomationEngine, schedule_id: str) -> datetime:
    schedule = engine.get_schedule(context(), schedule_id)
    assert schedule.next_run_at is not None
    return schedule.next_run_at


def test_schedule_records_its_next_fire_time_and_clears_it_when_disabled(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow())
    schedule = engine.create_schedule(context(), ScheduleCreate(workflow_id=created.id, name="Daily", cron="0 8 * * *"))

    assert schedule.next_run_at is not None and schedule.next_run_at > schedule.created_at
    assert (schedule.next_run_at.hour, schedule.next_run_at.minute) == (8, 0)
    assert schedule.created_by == "user_a"
    disabled = engine.update_schedule(context(), schedule.id, ScheduleUpdate(enabled=False))
    assert disabled.next_run_at is None
    enabled = engine.update_schedule(context(), schedule.id, ScheduleUpdate(enabled=True))
    assert enabled.next_run_at is not None


def test_due_schedule_fires_once_per_fire_time_and_advances(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow())
    schedule = engine.create_schedule(context(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    fire_at = _due(engine, schedule.id)

    assert engine.run_due_schedules(now=fire_at - timedelta(minutes=1)) == []
    [run] = engine.run_due_schedules(now=fire_at)
    assert engine.run_due_schedules(now=fire_at) == []  # the same fire time never runs twice

    assert run.schedule_id == schedule.id and run.status == RunStatus.COMPLETED
    assert run.idempotency_key == f"schedule:{schedule.id}:{fire_at.isoformat()}"
    assert run.created_by == "user_a"  # the schedule's creator is the run's actor
    assert _due(engine, schedule.id) == fire_at + timedelta(hours=1)
    assert [item.id for item in engine.list_runs(context())] == [run.id]
    assert engine.list_runs(context("workspace_b")) == []


def test_missed_fires_run_once_without_catching_up(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow())
    schedule = engine.create_schedule(context(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    fire_at = _due(engine, schedule.id)
    late = fire_at + timedelta(hours=5, minutes=10)

    assert len(engine.run_due_schedules(now=late)) == 1
    assert _due(engine, schedule.id) == fire_at + timedelta(hours=6)


def test_disabled_schedules_do_not_fire(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow())
    schedule = engine.create_schedule(context(), ScheduleCreate(workflow_id=created.id, name="Off", cron="* * * * *", enabled=False))

    assert schedule.next_run_at is None
    assert engine.run_due_schedules(now=schedule.created_at + timedelta(days=1)) == []


def test_invalid_cron_or_time_zone_is_a_validation_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(automation_module, "engine", LocalAutomationEngine(str(tmp_path / "api.db")))
    client = TestClient(app)
    headers = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a", "x-user-id": "owner_a", "x-user-roles": "owner"}
    created = client.post("/api/v1/automation/workflows", headers=headers, json={"name": "W", "steps": [{"id": "s", "name": "S", "action": "notify"}]}).json()

    for body in ({"cron": "61 * * * *"}, {"cron": "0 8 * * *", "timezone": "Nowhere/City"}, {"cron": "0 0 30 2 *"}):
        response = client.post("/api/v1/automation/schedules", headers=headers, json={"workflow_id": created["id"], "name": "S", **body})
        assert response.status_code == 422, body
    ok = client.post("/api/v1/automation/schedules", headers=headers, json={"workflow_id": created["id"], "name": "S", "cron": "0 8 * * MON", "timezone": "Asia/Riyadh"})
    assert ok.status_code == 201 and ok.json()["next_run_at"]


def test_scheduler_loop_fires_and_stops_cleanly(tmp_path: Path) -> None:
    engine = LocalAutomationEngine(str(tmp_path / "automation.db"))
    created = engine.create_workflow(context(), workflow())
    schedule = engine.create_schedule(context(), ScheduleCreate(workflow_id=created.id, name="Hourly", cron="0 * * * *"))
    fire_at = _due(engine, schedule.id)

    class Clocked:
        def run_due_schedules(self, *, limit: int = 100) -> list:
            return engine.run_due_schedules(limit=limit, now=fire_at)

    async def scenario() -> int:
        scheduler = AutomationScheduler(Clocked, poll_interval=0.01)  # type: ignore[arg-type]
        scheduler.start()
        for _ in range(200):
            if scheduler.fired:
                break
            await asyncio.sleep(0.01)
        await scheduler.stop(timeout=5)
        assert scheduler._task is None and scheduler._pass is None
        return scheduler.fired

    assert asyncio.run(scenario()) == 1
    assert len(engine.list_runs(context())) == 1


def test_scheduler_is_off_unless_enabled() -> None:
    from anum_api.settings import Settings

    assert build_automation_scheduler(Settings(_env_file=None)) is None
    enabled = build_automation_scheduler(
        Settings(_env_file=None, automation_scheduler_enabled=True, automation_scheduler_poll_seconds=5)
    )
    assert enabled is not None and enabled.poll_interval == 5
