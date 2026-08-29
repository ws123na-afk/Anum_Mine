from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import anum_api.automation as automation_module
from anum_api.automation import (
    LocalAutomationEngine,
    RunStatus,
    ScheduleCreate,
    WorkflowCreate,
    WorkflowStep,
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
