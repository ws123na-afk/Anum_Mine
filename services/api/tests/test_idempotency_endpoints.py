from fastapi.testclient import TestClient

from anum_api.dependencies import idempotency_repository, memory_note_repository
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
    idempotency_repository._records.clear()


def test_repeated_create_task_with_same_key_returns_original_task() -> None:
    request_headers = {**headers, "Idempotency-Key": "create-task-1"}
    body = {"title": "Summarize notes", "prompt": "Summarize the project notes"}

    first = client.post("/api/v1/tasks", headers=request_headers, json=body)
    second = client.post("/api/v1/tasks", headers=request_headers, json=body)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert len(store.tasks) == 1


def test_same_key_with_different_body_is_a_conflict() -> None:
    request_headers = {**headers, "Idempotency-Key": "create-task-2"}

    first = client.post(
        "/api/v1/tasks",
        headers=request_headers,
        json={"title": "First", "prompt": "First prompt"},
    )
    second = client.post(
        "/api/v1/tasks",
        headers=request_headers,
        json={"title": "Second", "prompt": "Second prompt"},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert len(store.tasks) == 1


def test_repeated_delete_memory_with_same_key_replays_success() -> None:
    task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Remember", "prompt": "Keep a task note"},
    )
    task_id = task.json()["id"]
    memory = client.post(
        "/api/v1/memories",
        headers=headers,
        json={
            "task_id": task_id,
            "content": "The launch decision is Friday.",
            "source_type": "user_note",
        },
    )
    memory_id = memory.json()["id"]

    delete_headers = {**headers, "Idempotency-Key": "delete-memory-1"}
    first = client.delete(f"/api/v1/memories/{memory_id}", headers=delete_headers)
    second = client.delete(f"/api/v1/memories/{memory_id}", headers=delete_headers)

    assert first.status_code == 204
    assert second.status_code == 204


def test_delete_memory_without_key_is_not_idempotent() -> None:
    task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Remember", "prompt": "Keep a task note"},
    )
    task_id = task.json()["id"]
    memory = client.post(
        "/api/v1/memories",
        headers=headers,
        json={
            "task_id": task_id,
            "content": "The launch decision is Friday.",
            "source_type": "user_note",
        },
    )
    memory_id = memory.json()["id"]

    first = client.delete(f"/api/v1/memories/{memory_id}", headers=headers)
    second = client.delete(f"/api/v1/memories/{memory_id}", headers=headers)

    assert first.status_code == 204
    assert second.status_code == 404


def test_invalid_idempotency_key_is_rejected() -> None:
    response = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "has a space"},
        json={"title": "Bad key", "prompt": "Should be rejected"},
    )

    assert response.status_code == 400


def test_repeated_approval_decision_with_same_key_replays_response() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Publish update", "prompt": "Publish the final update"},
    )
    task_id = created.json()["id"]
    run = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers)
    approval_id = run.json()["approval"]["id"]

    decide_headers = {**headers, "Idempotency-Key": "approve-1"}
    first = client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=decide_headers
    )
    second = client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=decide_headers
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
