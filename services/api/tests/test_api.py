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


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_and_run_low_risk_task() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Summarize notes", "prompt": "Summarize the project notes"},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    run = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers)

    assert run.status_code == 200
    payload = run.json()
    assert payload["task"]["status"] == "completed"
    assert payload["run"]["status"] == "completed"
    assert payload["approval"] is None


def test_cancel_created_task() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Cancel me", "prompt": "Do not run yet"},
    )
    task_id = created.json()["id"]

    cancelled = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    events = client.get("/api/v1/events", headers=headers).json()
    assert any(event["type"] == "task.cancelled" for event in events)


def test_completed_task_cannot_be_cancelled() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Run me", "prompt": "Summarize the project notes"},
    )
    task_id = created.json()["id"]
    client.post(f"/api/v1/tasks/{task_id}/run", headers=headers)

    cancelled = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)

    assert cancelled.status_code == 409


def test_high_risk_task_waits_for_approval_then_completes() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Publish update", "prompt": "Send and publish the final update"},
    )
    task_id = created.json()["id"]

    run = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers)
    payload = run.json()

    assert payload["task"]["status"] == "waiting_approval"
    approval_id = payload["approval"]["id"]

    approved = client.post(f"/api/v1/approvals/{approval_id}/approve", headers=headers)

    assert approved.status_code == 200
    approved_payload = approved.json()
    assert approved_payload["approval"]["status"] == "approved"
    assert approved_payload["task"]["status"] == "completed"
    assert approved_payload["run"]["status"] == "completed"


def test_rejected_high_risk_task_fails_and_records_events() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Publish update", "prompt": "Publish the final update"},
    )
    task_id = created.json()["id"]
    started = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers).json()
    approval_id = started["approval"]["id"]

    rejected = client.post(f"/api/v1/approvals/{approval_id}/reject", headers=headers)

    assert rejected.status_code == 200
    assert rejected.json()["approval"]["status"] == "rejected"
    assert rejected.json()["task"]["status"] == "failed"
    assert rejected.json()["run"]["status"] == "failed"
    event_types = [event["type"] for event in client.get("/api/v1/events", headers=headers).json()]
    assert event_types == [
        "task.created",
        "approval.requested",
        "approval.rejected",
        "agent_run.failed",
    ]


def test_duplicate_approval_decision_returns_conflict_without_duplicate_events() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Publish update", "prompt": "Publish the final update"},
    )
    task_id = created.json()["id"]
    started = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers).json()
    approval_id = started["approval"]["id"]

    assert client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=headers
    ).status_code == 200
    duplicate = client.post(
        f"/api/v1/approvals/{approval_id}/reject", headers=headers
    )

    assert duplicate.status_code == 409
    event_types = [event["type"] for event in client.get("/api/v1/events", headers=headers).json()]
    assert event_types.count("approval.approved") == 1
    assert event_types.count("approval.rejected") == 0
    assert event_types.count("agent_run.completed") == 1


def test_cancel_waiting_task_expires_approval_and_blocks_late_decision() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Publish update", "prompt": "Publish the final update"},
    )
    task_id = created.json()["id"]
    started = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers).json()
    run_id = started["run"]["id"]
    approval_id = started["approval"]["id"]

    cancelled = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)
    late_decision = client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=headers
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert late_decision.status_code == 409
    assert client.get(f"/api/v1/agent-runs/{run_id}", headers=headers).json()["status"] == "cancelled"
    approvals = client.get("/api/v1/approvals", headers=headers).json()
    assert approvals[0]["status"] == "expired"


def test_tenant_isolation_hides_task() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Private", "prompt": "Keep scoped"},
    )
    task_id = created.json()["id"]

    other_headers = dict(headers)
    other_headers["x-tenant-id"] = "tenant_b"

    response = client.get(f"/api/v1/tasks/{task_id}", headers=other_headers)

    assert response.status_code == 404


def test_other_tenant_cannot_cancel_task() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Scoped", "prompt": "Keep scoped"},
    )
    task_id = created.json()["id"]

    other_headers = dict(headers)
    other_headers["x-tenant-id"] = "tenant_b"
    response = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=other_headers)

    assert response.status_code == 404


def test_create_list_and_delete_task_memory() -> None:
    created = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Remember", "prompt": "Keep a task note"},
    )
    task_id = created.json()["id"]

    memory = client.post(
        "/api/v1/memories",
        headers=headers,
        json={
            "task_id": task_id,
            "content": "The launch decision is Friday.",
            "source_type": "user_note",
        },
    )
    assert memory.status_code == 201
    memory_id = memory.json()["id"]

    listed = client.get(
        "/api/v1/memories",
        headers=headers,
        params={"task_id": task_id, "query": "launch Friday"},
    )
    assert listed.status_code == 200
    assert [note["id"] for note in listed.json()] == [memory_id]

    deleted = client.delete(f"/api/v1/memories/{memory_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get("/api/v1/memories", headers=headers).json() == []


def test_viewer_cannot_create_memory() -> None:
    viewer_headers = dict(headers)
    viewer_headers["x-user-roles"] = "viewer"
    response = client.post(
        "/api/v1/memories",
        headers=viewer_headers,
        json={
            "task_id": "task_missing",
            "content": "No write access",
            "source_type": "user_note",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_invalid_memory_retention_uses_validation_error_contract() -> None:
    task = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"title": "Retention task", "prompt": "Remember briefly"},
    )

    response = client.post(
        "/api/v1/memories",
        headers={**headers, "X-Correlation-ID": "memory-request-1"},
        json={
            "task_id": task.json()["id"],
            "content": "This expiry is invalid.",
            "source_type": "user_note",
            "retention": {
                "kind": "expires_at",
                "expires_at": "2000-01-01T00:00:00Z",
            },
        },
    )

    assert response.status_code == 422
    assert response.headers["X-Correlation-ID"] == "memory-request-1"
    assert response.json()["error"] == {
        "code": "validation_error",
        "message": "memory expiry must be in the future",
        "correlation_id": "memory-request-1",
        "details": [],
    }


def test_unmatched_route_uses_error_envelope() -> None:
    response = client.get("/api/v1/does-not-exist", headers=headers)

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["correlation_id"]


def test_wrong_method_uses_error_envelope() -> None:
    response = client.delete("/api/v1/tasks", headers=headers)

    assert response.status_code == 405
    body = response.json()
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["correlation_id"]
