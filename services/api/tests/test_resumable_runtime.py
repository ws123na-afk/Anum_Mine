from fastapi.testclient import TestClient

from anum_api.main import app, store
from anum_api.schemas import AgentRun, RunCheckpoint, RunPhase, TaskStatus, new_id, utc_now


client = TestClient(app)
HEADERS = {
    "x-tenant-id": "tenant_resume",
    "x-workspace-id": "workspace_resume",
    "x-user-id": "user_resume",
    "x-user-roles": "owner",
}


def setup_function() -> None:
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.events.clear()
    store.tenants.clear()
    store.workspaces.clear()
    store.memberships.clear()


def _stranded_run() -> tuple[str, str]:
    task = client.post(
        "/api/v1/tasks",
        headers=HEADERS,
        json={"title": "Recover work", "prompt": "Summarize recovery notes"},
    ).json()
    stored_task = store.tasks[task["id"]]
    stored_task.status = TaskStatus.RUNNING
    now = utc_now()
    run = AgentRun(
        id=new_id("run"),
        task_id=stored_task.id,
        status=TaskStatus.RUNNING,
        checkpoint=RunCheckpoint(
            phase=RunPhase.TOOL_READY,
            version=2,
            selected_skills=["anum.task-planning"],
            tool_call={"name": "anum.respond", "arguments": {"content": "Recovered"}},
        ),
        created_at=now,
        updated_at=now,
    )
    store.runs[run.id] = run
    return stored_task.id, run.id


def test_resume_executes_persisted_checkpoint_without_replanning() -> None:
    task_id, run_id = _stranded_run()

    response = client.post(f"/api/v1/agent-runs/{run_id}/resume", headers=HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["id"] == task_id
    assert payload["run"]["status"] == "completed"
    assert payload["run"]["checkpoint"]["phase"] == "completed"
    assert payload["run"]["checkpoint"]["version"] == 4
    assert [step["type"] for step in payload["run"]["steps"]] == ["tool_result", "final"]
    assert [event.type for event in store.events] == [
        "task.created",
        "agent_run.resumed",
        "agent_run.completed",
    ]


def test_cancel_terminalizes_checkpoint_and_blocks_late_resume() -> None:
    _, run_id = _stranded_run()

    cancelled = client.post(
        f"/api/v1/tasks/{store.runs[run_id].task_id}/cancel", headers=HEADERS
    )
    resumed = client.post(f"/api/v1/agent-runs/{run_id}/resume", headers=HEADERS)

    assert cancelled.status_code == 200
    assert store.runs[run_id].checkpoint.phase == RunPhase.CANCELLED
    assert resumed.status_code == 409
    assert resumed.json()["error"]["message"] == "Cancelled runs cannot be resumed"


def test_resume_is_tenant_scoped() -> None:
    _, run_id = _stranded_run()
    other = {**HEADERS, "x-tenant-id": "tenant_other", "x-workspace-id": "workspace_other"}

    response = client.post(f"/api/v1/agent-runs/{run_id}/resume", headers=other)

    assert response.status_code == 404
