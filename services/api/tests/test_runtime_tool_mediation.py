"""AgentRuntime now routes skill selection and the high-risk action through
the real skills registry / tools mediation (see runtime.py) instead of
hand-rolling an Approval and a hardcoded "did this need approval" check in
isolation. These tests exercise that wiring specifically - test_api.py's
existing low/high-risk tests only assert on task/run status, not on the
skill-selection step or the mediated approval's shape.
"""

from fastapi.testclient import TestClient

from anum_api.dependencies import memory_note_repository
from anum_api.main import app, store

client = TestClient(app)
headers = {
    "x-tenant-id": "tenant_a",
    "x-workspace-id": "workspace_a",
    "x-user-id": "user_a",
    "x-user-roles": "owner,member",
}


def setup_function() -> None:
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.events.clear()
    memory_note_repository._notes.clear()


def _create_and_run(prompt: str) -> dict:
    created = client.post(
        "/api/v1/tasks", headers=headers, json={"title": "t", "prompt": prompt}
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    run = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers)
    assert run.status_code == 200
    return run.json()


def test_matching_prompt_records_skill_selection_step() -> None:
    payload = _create_and_run("Make a plan for the migration")

    step_types = [step["type"] for step in payload["run"]["steps"]]
    assert step_types[0] == "tool_proposal"
    assert "Task Planning" in payload["run"]["steps"][0]["summary"]
    assert payload["run"]["steps"][0]["metadata"]["skill_id"] == "task_planning"
    # model_call still follows immediately after, unchanged
    assert step_types[1] == "model_call"


def test_non_matching_prompt_has_no_skill_selection_step() -> None:
    payload = _create_and_run("Summarize the quarterly numbers")

    step_types = [step["type"] for step in payload["run"]["steps"]]
    assert "tool_proposal" not in step_types
    assert step_types[0] == "model_call"


def test_high_risk_prompt_creates_approval_via_tool_mediation() -> None:
    payload = _create_and_run("Delete the archived customer records")

    assert payload["task"]["status"] == "waiting_approval"
    approval = payload["approval"]
    assert approval is not None
    assert approval["action"] == "tool:agent_high_risk_action"
    assert "high risk" in approval["reason"]
    assert approval["status"] == "pending"

    # And it's really persisted, not just returned in the response.
    listed = client.get("/api/v1/approvals", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == approval["id"] for item in listed.json())


def test_approving_runs_the_mediated_tool_and_completes() -> None:
    payload = _create_and_run("Delete the archived customer records")
    approval_id = payload["approval"]["id"]

    decision = client.post(f"/api/v1/approvals/{approval_id}/approve", headers=headers)
    assert decision.status_code == 200
    body = decision.json()

    assert body["task"]["status"] == "completed"
    assert body["run"]["status"] == "completed"
    tool_result_steps = [step for step in body["run"]["steps"] if step["type"] == "tool_result"]
    assert any("Executed approved high-risk mock action" in step["summary"] for step in tool_result_steps)
    assert body["run"]["steps"][-1]["type"] == "final"


def test_rejecting_fails_the_task_without_running_the_tool() -> None:
    payload = _create_and_run("Delete the archived customer records")
    approval_id = payload["approval"]["id"]

    decision = client.post(f"/api/v1/approvals/{approval_id}/reject", headers=headers)
    assert decision.status_code == 200
    body = decision.json()

    assert body["task"]["status"] == "failed"
    assert body["run"]["status"] == "failed"
