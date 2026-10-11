"""Task list filters, summary, edit, archive, bulk actions, run history, retry, task
events and the run-log export (docs/tasks.md), against the in-memory backend."""

import json
from datetime import timedelta

from fastapi.testclient import TestClient

from anum_api.main import app, store
from anum_api.schemas import utc_now
from anum_api.tasks import TaskFilters, export_markdown, like_pattern

client = TestClient(app)
owner = {
    "x-tenant-id": "tenant_a",
    "x-workspace-id": "workspace_a",
    "x-user-id": "user_owner",
    "x-user-roles": "owner,member",
}
member = {**owner, "x-user-id": "user_member", "x-user-roles": "member"}
other_member = {**owner, "x-user-id": "user_other", "x-user-roles": "member"}
viewer = {**owner, "x-user-id": "user_viewer", "x-user-roles": "viewer"}
other_tenant = {**owner, "x-tenant-id": "tenant_b"}

HIGH_RISK = "Publish the final update"


def setup_function() -> None:
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.events.clear()
    store.audit_records.clear()


def _create(title: str, prompt: str = "Summarize notes", headers: dict[str, str] = owner) -> dict:
    response = client.post("/api/v1/tasks", headers=headers, json={"title": title, "prompt": prompt})
    assert response.status_code == 201, response.text
    return response.json()


def _failed_task(headers: dict[str, str] = owner) -> tuple[dict, dict]:
    """A task whose run was rejected at approval (failed), and that run response."""
    task = _create("Publish", HIGH_RISK, headers)
    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers).json()
    rejected = client.post(f"/api/v1/approvals/{started['approval']['id']}/reject", headers=owner)
    assert rejected.json()["task"]["status"] == "failed"
    return task, started


def _ids(response) -> list[str]:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    return [task["id"] for task in response.json()]


# --- list filters -------------------------------------------------------------------


def test_list_filters_by_status_search_creator_and_date_range() -> None:
    done = _create("Quarterly report", "Summarize the quarter")
    client.post(f"/api/v1/tasks/{done['id']}/run", headers=owner)
    draft = _create("Draft 100% plan_b", "Plan the launch", member)
    old = _create("Old notes")
    store.tasks[old["id"]].created_at = utc_now() - timedelta(days=10)

    assert _ids(client.get("/api/v1/tasks", headers=owner, params={"status": "completed"})) == [done["id"]]
    both = client.get("/api/v1/tasks", headers=owner, params=[("status", "completed"), ("status", "created")])
    assert set(_ids(both)) == {done["id"], draft["id"], old["id"]}
    assert _ids(client.get("/api/v1/tasks", headers=owner, params={"q": "QUARTER"})) == [done["id"]]
    # LIKE wildcards in the search are literal text.
    assert _ids(client.get("/api/v1/tasks", headers=owner, params={"q": "100%"})) == [draft["id"]]
    assert _ids(client.get("/api/v1/tasks", headers=owner, params={"q": "n_b"})) == [draft["id"]]
    assert _ids(client.get("/api/v1/tasks", headers=owner, params={"q": "%"})) == [draft["id"]]
    assert _ids(client.get("/api/v1/tasks", headers=member, params={"created_by": "me"})) == [draft["id"]]
    assert _ids(client.get("/api/v1/tasks", headers=owner, params={"created_by": "user_member"})) == [draft["id"]]
    since = (utc_now() - timedelta(days=1)).isoformat()
    assert old["id"] not in _ids(client.get("/api/v1/tasks", headers=owner, params={"created_after": since}))
    assert _ids(client.get("/api/v1/tasks", headers=owner, params={"created_before": since})) == [old["id"]]


def test_list_rejects_bad_filters() -> None:
    now = utc_now()
    inverted = client.get(
        "/api/v1/tasks",
        headers=owner,
        params={"created_after": now.isoformat(), "created_before": (now - timedelta(hours=1)).isoformat()},
    )
    assert inverted.status_code == 400
    assert client.get("/api/v1/tasks", headers=owner, params={"status": "bogus"}).status_code == 422
    assert client.get("/api/v1/tasks", headers=owner, params={"q": "x" * 201}).status_code == 422


def test_filtered_list_pages_by_keyset() -> None:
    created = [_create(f"Report {index}") for index in range(5)]
    _create("Unrelated")
    first = client.get("/api/v1/tasks", headers=owner, params={"q": "report", "limit": 2}).json()
    second = client.get(
        "/api/v1/tasks", headers=owner, params={"q": "report", "limit": 2, "cursor": first[-1]["id"]}
    ).json()
    third = client.get(
        "/api/v1/tasks", headers=owner, params={"q": "report", "limit": 2, "cursor": second[-1]["id"]}
    ).json()
    seen = [task["id"] for task in first + second + third]
    assert len(seen) == 5 and set(seen) == {task["id"] for task in created}


def test_summary_counts_statuses_archived_and_creators() -> None:
    done = _create("Done")
    client.post(f"/api/v1/tasks/{done['id']}/run", headers=owner)
    _create("Draft", headers=member)
    archived = _create("Archived")
    client.post(f"/api/v1/tasks/{archived['id']}/archive", headers=owner)

    summary = client.get("/api/v1/tasks/summary", headers=owner, params={"status": "completed"})
    assert summary.status_code == 200
    body = summary.json()
    # The status filter does not narrow the per-status counts.
    assert body["by_status"] == {"completed": 1, "created": 1}
    assert body["total"] == 2
    assert body["archived"] == 1
    assert body["creators"] == ["user_member", "user_owner"]
    assert client.get("/api/v1/tasks/summary", headers=other_tenant).json()["total"] == 0


# --- edit ---------------------------------------------------------------------------


def test_creator_edits_title_and_instructions_before_the_task_runs() -> None:
    task = _create("Draft", "First instructions", member)
    updated = client.put(
        f"/api/v1/tasks/{task['id']}", headers=member, json={"title": "  Renamed ", "prompt": "Second"}
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Renamed"
    assert updated.json()["prompt"] == "Second"
    events = client.get(f"/api/v1/tasks/{task['id']}/events", headers=owner).json()
    assert events[0]["type"] == "task.updated"
    assert events[0]["payload"] == {"task_id": task["id"], "fields": ["prompt", "title"]}
    assert "Second" not in json.dumps(events)  # instructions never travel in events
    assert any(record.action == "task.updated" for record in store.audit_records)


def test_instructions_are_fixed_once_the_task_ran_but_the_title_is_not() -> None:
    task = _create("Ran")
    client.post(f"/api/v1/tasks/{task['id']}/run", headers=owner)
    refused = client.put(f"/api/v1/tasks/{task['id']}", headers=owner, json={"prompt": "Different"})
    assert refused.status_code == 409
    assert "cannot change after the task has run" in refused.json()["error"]["message"]
    assert client.put(f"/api/v1/tasks/{task['id']}", headers=owner, json={"title": "Renamed"}).status_code == 200


def test_edit_permissions_creator_or_owner_only() -> None:
    task = _create("Mine", headers=member)
    denied = client.put(f"/api/v1/tasks/{task['id']}", headers=other_member, json={"title": "Taken"})
    assert denied.status_code == 403
    assert "creator or a workspace owner" in denied.json()["error"]["message"]
    assert client.put(f"/api/v1/tasks/{task['id']}", headers=viewer, json={"title": "Taken"}).status_code == 403
    assert client.put(f"/api/v1/tasks/{task['id']}", headers=owner, json={"title": "By owner"}).status_code == 200
    assert client.put(f"/api/v1/tasks/{task['id']}", headers=other_tenant, json={"title": "x"}).status_code == 404
    assert client.put(f"/api/v1/tasks/{task['id']}", headers=owner, json={}).status_code == 422
    assert client.put(f"/api/v1/tasks/{task['id']}", headers=owner, json={"title": "   "}).status_code == 422


# --- archive ------------------------------------------------------------------------


def test_archive_hides_the_task_and_unarchive_restores_it() -> None:
    task = _create("Finished")
    client.post(f"/api/v1/tasks/{task['id']}/run", headers=owner)
    archived = client.post(f"/api/v1/tasks/{task['id']}/archive", headers=owner)
    assert archived.status_code == 200
    assert archived.json()["archived_by"] == "user_owner"
    assert archived.json()["archived_at"] is not None
    assert task["id"] not in _ids(client.get("/api/v1/tasks", headers=owner))
    assert _ids(client.get("/api/v1/tasks", headers=owner, params={"archived": "true"})) == [task["id"]]
    # Still readable, with its history; but not editable, runnable or retryable.
    assert client.get(f"/api/v1/tasks/{task['id']}/runs", headers=owner).status_code == 200
    assert client.put(f"/api/v1/tasks/{task['id']}", headers=owner, json={"title": "x"}).status_code == 409
    assert client.post(f"/api/v1/tasks/{task['id']}/archive", headers=owner).status_code == 409

    restored = client.post(f"/api/v1/tasks/{task['id']}/unarchive", headers=owner)
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert task["id"] in _ids(client.get("/api/v1/tasks", headers=owner))
    assert client.post(f"/api/v1/tasks/{task['id']}/unarchive", headers=owner).status_code == 409
    types = [event["type"] for event in client.get(f"/api/v1/tasks/{task['id']}/events", headers=owner).json()]
    assert types[:2] == ["task.unarchived", "task.archived"]


def test_an_active_task_must_be_cancelled_before_archiving() -> None:
    task = _create("Waiting", HIGH_RISK)
    client.post(f"/api/v1/tasks/{task['id']}/run", headers=owner)
    refused = client.post(f"/api/v1/tasks/{task['id']}/archive", headers=owner)
    assert refused.status_code == 409
    assert "Cancel the task" in refused.json()["error"]["message"]
    # A task that never ran can be archived straight away.
    assert client.post(f"/api/v1/tasks/{_create('Draft')['id']}/archive", headers=owner).status_code == 200


def test_archive_permissions_and_archived_tasks_cannot_run() -> None:
    task = _create("Members", headers=member)
    assert client.post(f"/api/v1/tasks/{task['id']}/archive", headers=other_member).status_code == 403
    assert client.post(f"/api/v1/tasks/{task['id']}/archive", headers=viewer).status_code == 403
    assert client.post(f"/api/v1/tasks/{task['id']}/archive", headers=member).status_code == 200
    assert client.post(f"/api/v1/tasks/{task['id']}/run", headers=member).status_code == 409
    assert client.post(f"/api/v1/tasks/{task['id']}/cancel", headers=owner).status_code == 409


# --- bulk ---------------------------------------------------------------------------


def test_bulk_archive_reports_each_task() -> None:
    done = _create("Done")
    client.post(f"/api/v1/tasks/{done['id']}/run", headers=owner)
    draft = _create("Draft")
    waiting = _create("Waiting", HIGH_RISK)
    client.post(f"/api/v1/tasks/{waiting['id']}/run", headers=owner)

    response = client.post(
        "/api/v1/tasks/bulk",
        headers=owner,
        json={"action": "archive", "task_ids": [done["id"], draft["id"], waiting["id"], "task_missing"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["succeeded"], body["failed"]) == (2, 2)
    codes = {result["task_id"]: result["status_code"] for result in body["results"]}
    assert codes == {done["id"]: 200, draft["id"]: 200, waiting["id"]: 409, "task_missing": 404}
    assert body["results"][0]["task"]["archived_at"] is not None


def test_bulk_cancel_needs_the_cancel_permission_per_task() -> None:
    first, second = _create("One"), _create("Two")
    denied = client.post(
        "/api/v1/tasks/bulk", headers=member, json={"action": "cancel", "task_ids": [first["id"], second["id"]]}
    ).json()
    assert denied["failed"] == 2 and {result["status_code"] for result in denied["results"]} == {403}
    allowed = client.post(
        "/api/v1/tasks/bulk", headers=owner, json={"action": "cancel", "task_ids": [first["id"], second["id"]]}
    ).json()
    assert allowed["succeeded"] == 2
    assert {result["task"]["status"] for result in allowed["results"]} == {"cancelled"}


def test_bulk_request_validation() -> None:
    assert client.post("/api/v1/tasks/bulk", headers=owner, json={"action": "archive", "task_ids": []}).status_code == 422
    assert client.post(
        "/api/v1/tasks/bulk", headers=owner, json={"action": "archive", "task_ids": ["a", "a"]}
    ).status_code == 422
    assert client.post(
        "/api/v1/tasks/bulk", headers=owner, json={"action": "archive", "task_ids": [f"t{i}" for i in range(101)]}
    ).status_code == 422
    assert client.post("/api/v1/tasks/bulk", headers=owner, json={"action": "delete", "task_ids": ["a"]}).status_code == 422


# --- retry and run history ----------------------------------------------------------


def test_retry_starts_a_new_run_and_keeps_the_history() -> None:
    task, first = _failed_task()
    retried = client.post(f"/api/v1/tasks/{task['id']}/retry", headers=owner)
    assert retried.status_code == 200
    body = retried.json()
    assert body["run"]["id"] != first["run"]["id"]
    assert body["task"]["status"] == "waiting_approval"

    runs = client.get(f"/api/v1/tasks/{task['id']}/runs", headers=owner).json()
    assert [run["id"] for run in runs] == [body["run"]["id"], first["run"]["id"]]
    assert [run["status"] for run in runs] == ["waiting_approval", "failed"]
    latest = client.get(f"/api/v1/tasks/{task['id']}/latest-run", headers=owner).json()
    assert latest["id"] == body["run"]["id"]

    # The new approval resumes the new run, not the failed one.
    approved = client.post(
        f"/api/v1/approvals/{body['approval']['id']}/approve",
        headers=owner,
        json={"payload_hash": body["approval"]["payload_hash"]},
    )
    assert approved.status_code == 200
    assert approved.json()["run"]["id"] == body["run"]["id"]
    assert approved.json()["task"]["status"] == "completed"
    events = client.get(f"/api/v1/tasks/{task['id']}/events", headers=owner).json()
    retried_event = next(event for event in events if event["type"] == "task.retried")
    assert retried_event["payload"]["previous_run_id"] == first["run"]["id"]


def test_retry_refuses_tasks_that_did_not_fail_and_needs_run_permission() -> None:
    done = _create("Done")
    client.post(f"/api/v1/tasks/{done['id']}/run", headers=owner)
    refused = client.post(f"/api/v1/tasks/{done['id']}/retry", headers=owner)
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == "Only a failed or cancelled task can be retried"
    cancelled = _create("Cancelled")
    client.post(f"/api/v1/tasks/{cancelled['id']}/cancel", headers=owner)
    assert client.post(f"/api/v1/tasks/{cancelled['id']}/retry", headers=viewer).status_code == 403
    assert client.post(f"/api/v1/tasks/{cancelled['id']}/retry", headers=other_tenant).status_code == 404
    assert client.post(f"/api/v1/tasks/{cancelled['id']}/retry", headers=member).json()["task"]["status"] == "completed"


def test_runs_page_by_keyset_and_reject_unknown_cursors() -> None:
    task, _ = _failed_task()
    for _ in range(2):
        body = client.post(f"/api/v1/tasks/{task['id']}/retry", headers=owner).json()
        client.post(f"/api/v1/approvals/{body['approval']['id']}/reject", headers=owner)
    first = client.get(f"/api/v1/tasks/{task['id']}/runs", headers=owner, params={"limit": 2}).json()
    rest = client.get(
        f"/api/v1/tasks/{task['id']}/runs", headers=owner, params={"limit": 2, "cursor": first[-1]["id"]}
    ).json()
    assert len(first) == 2 and len(rest) == 1
    assert len({run["id"] for run in first + rest}) == 3
    bad = client.get(f"/api/v1/tasks/{task['id']}/runs", headers=owner, params={"cursor": "run_missing"})
    assert bad.status_code == 400
    assert client.get(f"/api/v1/tasks/{task['id']}/runs", headers=other_tenant).status_code == 404
    assert client.get(f"/api/v1/tasks/{task['id']}/runs", headers=viewer).status_code == 200


def test_task_events_page_newest_first() -> None:
    task, _ = _failed_task()
    everything = client.get(f"/api/v1/tasks/{task['id']}/events", headers=owner).json()
    assert everything[-1]["type"] == "task.created"
    assert all(event["correlation_id"] == task["id"] for event in everything)
    page = client.get(f"/api/v1/tasks/{task['id']}/events", headers=owner, params={"limit": 2}).json()
    after = client.get(
        f"/api/v1/tasks/{task['id']}/events", headers=owner, params={"limit": 50, "cursor": page[-1]["id"]}
    ).json()
    assert [event["id"] for event in page + after] == [event["id"] for event in everything]
    assert client.get(f"/api/v1/tasks/{task['id']}/events", headers=owner, params={"cursor": "evt_x"}).status_code == 400


# --- export -------------------------------------------------------------------------


def test_export_json_holds_every_run_step_approval_and_event() -> None:
    task, first = _failed_task()
    client.post(f"/api/v1/tasks/{task['id']}/retry", headers=owner)
    response = client.get(f"/api/v1/tasks/{task['id']}/export", headers=viewer)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == f'attachment; filename="{task["id"]}-run-log.json"'
    assert response.headers["cache-control"] == "no-store"
    document = response.json()
    assert document["format"] == "anum.task-run-log/v1"
    assert document["exported_by"] == "user_viewer"
    assert document["task"]["id"] == task["id"]
    assert [run["id"] for run in document["runs"]][0] == first["run"]["id"]  # oldest first
    assert len(document["runs"]) == 2 and all(run["steps"] for run in document["runs"])
    assert len(document["approvals"]) == 2
    assert document["events"][0]["type"] == "task.created"
    assert document["truncated"] == {"runs": False, "events": False}
    assert any(record.action == "task.exported" for record in store.audit_records)


def test_export_markdown_and_scoping() -> None:
    task = _create("Report `x`", "Line one\n```\n# not a heading")
    client.post(f"/api/v1/tasks/{task['id']}/run", headers=owner)
    response = client.get(f"/api/v1/tasks/{task['id']}/export", headers=owner, params={"format": "markdown"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"].endswith('-run-log.md"')
    text = response.text
    assert text.startswith('# Task run log: "Report `x`"')
    assert "````text\nLine one\n```\n# not a heading\n````" in text  # fenced longer than the content
    assert "## Runs (1)" in text and "**final**" in text
    assert client.get(f"/api/v1/tasks/{task['id']}/export", headers=other_tenant).status_code == 404
    assert client.get(f"/api/v1/tasks/{task['id']}/export", headers=owner, params={"format": "pdf"}).status_code == 422


def test_export_redacts_secret_arguments_in_the_checkpoint() -> None:
    task = _create("Publish", HIGH_RISK)
    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=owner).json()
    run = store.runs[started["run"]["id"]]
    assert run.checkpoint.tool_call is not None
    run.checkpoint.tool_call = {**run.checkpoint.tool_call, "arguments": {"api_key": "plainly-fake-value"}}
    document = client.get(f"/api/v1/tasks/{task['id']}/export", headers=owner).json()
    assert document["runs"][0]["checkpoint"]["tool_call"]["arguments"] == {"api_key": "[REDACTED]"}
    assert "plainly-fake-value" not in json.dumps(document)


# --- pure helpers -------------------------------------------------------------------


def test_like_pattern_escapes_wildcards() -> None:
    assert like_pattern("50%_a\\b") == "%50\\%\\_a\\\\b%"


def test_filters_match_archived_and_ranges() -> None:
    task = _create("Plain")
    model = store.tasks[task["id"]]
    assert TaskFilters().matches(model)
    assert not TaskFilters(archived=True).matches(model)
    assert not TaskFilters(created_after=model.created_at + timedelta(seconds=1)).matches(model)
    assert TaskFilters(created_after=model.created_at).matches(model)
    assert not TaskFilters(created_before=model.created_at).matches(model)


def test_markdown_of_a_task_that_never_ran() -> None:
    now = utc_now().isoformat()
    text = export_markdown(
        {
            "exported_at": now,
            "exported_by": "user_a",
            "task": {
                "id": "task_1", "title": "T", "prompt": "P", "status": "created",
                "created_at": now, "updated_at": now, "created_by": "user_a",
            },
            "runs": [], "approvals": [], "events": [],
            "truncated": {"runs": False, "events": False},
        }
    )
    assert "This task never ran." in text and "No approvals were requested." in text
