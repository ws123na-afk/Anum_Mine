"""Approval integrity (docs/approvals-and-risk.md, threat model A1 to A3).

A1: the approval carries the exact tool name and arguments (secrets redacted).
A2: the decision is bound to a SHA-256 hash of the canonical tool call; the runtime
    re-computes it before executing, inline and in the Temporal activity.
A3: pending approvals expire; the decider and decision time are recorded.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_tools import ToolCall
from anum_api.approval_integrity import (
    REDACTED,
    as_viewed,
    canonical_tool_call,
    display_arguments,
    payload_hash,
)
from anum_api.durable_runs import state_of
from anum_api.repository import AnumRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import ApprovalStatus, RunPhase, TaskStatus, TenantContext, utc_now
from anum_api.settings import Settings
from anum_api.store import store

from test_durable_runs import (
    CONTEXT as DURABLE_CONTEXT,
    HEADERS as DURABLE_HEADERS,
    CountingGateway,
    RecordingDispatcher,
    _advance,
    _queue,
    activities_with,
)

HEADERS = {
    "x-tenant-id": "tenant_integrity",
    "x-workspace-id": "workspace_integrity",
    "x-user-id": "owner_integrity",
    "x-user-roles": "owner",
}
CONTEXT = TenantContext(
    tenant_id="tenant_integrity", workspace_id="workspace_integrity", user_id="owner_integrity", roles=["owner"]
)
RISKY_PROMPT = "Publish the final update"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_store() -> Iterator[None]:
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield


def _waiting(prompt: str = RISKY_PROMPT) -> dict:
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": "Risky", "prompt": prompt}).json()
    started = client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS)
    assert started.status_code == 200
    body = started.json()
    assert body["task"]["status"] == "waiting_approval"
    return body


def _approve(approval_id: str, payload_hash_value: str | None):
    body = None if payload_hash_value is None else {"payload_hash": payload_hash_value}
    return client.post(f"/api/v1/approvals/{approval_id}/approve", headers=HEADERS, json=body)


# A1: the exact action ---------------------------------------------------------------


def test_approval_carries_the_exact_tool_and_arguments() -> None:
    body = _waiting()
    approval, run = body["approval"], body["run"]

    assert approval["action"] == "external.action"
    checkpoint_call = run["checkpoint"]["tool_call"]
    assert checkpoint_call["name"] == "external.action"
    # Every argument the tool will receive is on the approval, verbatim (nothing secret here).
    assert approval["arguments"] == checkpoint_call["arguments"]
    assert approval["arguments"]["action"] == RISKY_PROMPT
    assert "planned_response" in approval["arguments"]
    assert approval["run_id"] == run["id"]
    assert approval["step_id"] == run["checkpoint"]["last_step_id"]
    assert approval["decided_by"] is None


def test_display_arguments_redact_secret_looking_fields_only() -> None:
    shown = display_arguments(
        {
            "to": "team@example.com",
            "api_key": "abc123",
            "headers": {"Authorization": "Bearer abc.def", "Accept": "application/json"},
            "items": [{"password": "hunter2"}, "Bearer sk-live-xyz"],
            "note": "sk-abcdefghijklmnop",
            "count": 3,
        }
    )
    assert shown == {
        "to": "team@example.com",
        "api_key": REDACTED,
        "headers": {"Authorization": REDACTED, "Accept": "application/json"},
        "items": [{"password": REDACTED}, REDACTED],
        "note": REDACTED,
        "count": 3,
    }


def test_secret_arguments_are_redacted_on_the_approval_but_bound_by_the_hash() -> None:
    gateway = CountingGateway()
    from anum_api.repository import InMemoryRepository
    from anum_api.store import InMemoryStore
    from anum_api.schemas import Task, new_id

    repository = InMemoryRepository(InMemoryStore())
    runtime = AgentRuntime(gateway, repository)
    now = utc_now()
    task = Task(
        id=new_id("task"), title="t", prompt="p", status=TaskStatus.RUNNING,
        tenant_id=CONTEXT.tenant_id, workspace_id=CONTEXT.workspace_id, created_at=now, updated_at=now,
    )
    repository.create_task(task)
    run = runtime.new_run(task)
    call = ToolCall(name="external.action", arguments={"action": "send", "api_token": "s3cret"})
    run.checkpoint.tool_call = call.model_dump(mode="json")
    run.checkpoint.last_step_id = "step_proposal"
    _, approval = runtime._pause_for_approval(task, run, CONTEXT, call, "Needs approval.")

    assert approval.arguments == {"action": "send", "api_token": REDACTED}
    assert approval.payload_hash == payload_hash(call, task_id=task.id, run_id=run.id, step_id="step_proposal")
    redacted = ToolCall(name=call.name, arguments=approval.arguments)
    assert approval.payload_hash != payload_hash(redacted, task_id=task.id, run_id=run.id, step_id="step_proposal")


# A2: hash binding --------------------------------------------------------------------


def test_payload_hash_is_sha256_of_canonical_json() -> None:
    call = ToolCall(name="external.action", arguments={"b": 1, "a": "é"})
    canonical = canonical_tool_call(call, task_id="task_1", run_id="run_1", step_id="step_1")
    assert canonical == (
        '{"arguments":{"a":"é","b":1},"run_id":"run_1","step_id":"step_1","task_id":"task_1",'
        '"tool":"external.action"}'
    ).encode("utf-8")
    assert payload_hash(call, task_id="task_1", run_id="run_1", step_id="step_1") == hashlib.sha256(
        canonical
    ).hexdigest()
    # Any change to the tool, arguments, task, run or step changes the hash.
    base = payload_hash(call, task_id="task_1", run_id="run_1", step_id="step_1")
    variants = [
        payload_hash(ToolCall(name="anum.respond", arguments=call.arguments), task_id="task_1", run_id="run_1", step_id="step_1"),
        payload_hash(ToolCall(name=call.name, arguments={"b": 2, "a": "é"}), task_id="task_1", run_id="run_1", step_id="step_1"),
        payload_hash(call, task_id="task_2", run_id="run_1", step_id="step_1"),
        payload_hash(call, task_id="task_1", run_id="run_2", step_id="step_1"),
        payload_hash(call, task_id="task_1", run_id="run_1", step_id="step_2"),
    ]
    assert base not in variants and len(set(variants)) == len(variants)


def test_approval_hash_matches_the_checkpointed_call() -> None:
    body = _waiting()
    approval, run = body["approval"], body["run"]
    call = ToolCall.model_validate(run["checkpoint"]["tool_call"])
    expected = payload_hash(call, task_id=body["task"]["id"], run_id=run["id"], step_id=run["checkpoint"]["last_step_id"])
    assert approval["payload_hash"] == expected


def test_approve_requires_the_displayed_hash() -> None:
    body = _waiting()
    approval = body["approval"]

    missing = _approve(approval["id"], None)
    assert missing.status_code == 422
    malformed = client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=HEADERS, json={"payload_hash": "abc"}
    )
    assert malformed.status_code == 422
    stale = _approve(approval["id"], "0" * 64)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"
    assert store.approvals[approval["id"]].status == ApprovalStatus.PENDING
    assert store.runs[body["run"]["id"]].status == TaskStatus.WAITING_APPROVAL

    approved = _approve(approval["id"], approval["payload_hash"])
    assert approved.status_code == 200
    assert approved.json()["run"]["status"] == "completed"


def test_reject_with_a_stale_hash_is_refused_but_without_a_hash_is_allowed() -> None:
    first = _waiting()
    stale = client.post(
        f"/api/v1/approvals/{first['approval']['id']}/reject", headers=HEADERS, json={"payload_hash": "f" * 64}
    )
    assert stale.status_code == 409
    rejected = client.post(f"/api/v1/approvals/{first['approval']['id']}/reject", headers=HEADERS)
    assert rejected.status_code == 200
    assert rejected.json()["approval"]["decided_by"] == "owner_integrity"


def test_tampered_arguments_are_never_executed_inline() -> None:
    body = _waiting()
    approval, run_id = body["approval"], body["run"]["id"]
    # The checkpoint is changed after the approver saw the action (for example through
    # direct database access): the approver's hash still matches the approval row.
    store.runs[run_id].checkpoint.tool_call["arguments"]["action"] = "Wire $1,000,000 to account 42"

    decided = _approve(approval["id"], approval["payload_hash"])

    assert decided.status_code == 200
    payload = decided.json()
    assert payload["approval"]["status"] == "approved"
    assert payload["run"]["status"] == "failed"
    assert payload["task"]["status"] == "failed"
    assert "payload hash mismatch" in payload["run"]["steps"][-1]["summary"]
    assert payload["run"]["result"] is None  # the tool never ran
    events = [event.type for event in store.events if event.payload.get("task_id") == body["task"]["id"]]
    assert events[-1] == "agent_run.failed" and "agent_run.completed" not in events
    mismatches = [record for record in store.audit_records if record.action == "approval.payload_mismatch"]
    assert len(mismatches) == 1
    record = mismatches[0]
    assert (record.target, record.outcome, record.tenant_id) == (approval["id"], "denied", "tenant_integrity")
    assert record.metadata["approved_hash"] == approval["payload_hash"]
    assert record.metadata["checkpoint_hash"] != approval["payload_hash"]
    assert record.metadata["decided_by"] == "owner_integrity"


def test_tampered_tool_name_is_never_executed_inline() -> None:
    body = _waiting()
    store.runs[body["run"]["id"]].checkpoint.tool_call["name"] = "anum.respond"
    decided = _approve(body["approval"]["id"], body["approval"]["payload_hash"])
    assert decided.json()["run"]["status"] == "failed"


def test_tampered_arguments_are_never_executed_by_the_temporal_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    for collection in (store.tasks, store.runs, store.approvals, store.events):
        collection.clear()
    dispatcher = RecordingDispatcher()
    monkeypatch.setattr(main, "run_dispatcher", dispatcher)
    request = _queue(RISKY_PROMPT, dispatcher)
    executed: list[str] = []
    activities = activities_with(CountingGateway())
    original_execute = activities.runtime_factory

    def factory(context: TenantContext, repository: AnumRepository) -> AgentRuntime:
        runtime = original_execute(context, repository)
        execute = runtime.tools.execute

        async def recording(call: ToolCall, ctx: TenantContext):
            executed.append(call.name)
            return await execute(call, ctx)

        runtime.tools.execute = recording  # type: ignore[method-assign]
        return runtime

    activities.runtime_factory = factory

    waiting = _advance(activities, request)
    assert waiting.phase == "waiting_approval"
    approval = store.approvals[waiting.approval_id]
    decided = client.post(
        f"/api/v1/approvals/{approval.id}/approve",
        headers=DURABLE_HEADERS,
        json={"payload_hash": approval.payload_hash},
    )
    assert decided.status_code == 200
    # Tampered between the decision and the worker picking it up.
    store.runs[request.run_id].checkpoint.tool_call["arguments"]["planned_response"] = "something else"

    settled = _advance(activities, request)

    assert (settled.phase, settled.status) == ("failed", "failed")
    assert executed == []
    assert "payload hash mismatch" in store.runs[request.run_id].steps[-1].summary
    assert any(
        record.action == "approval.payload_mismatch" and record.tenant_id == DURABLE_CONTEXT.tenant_id
        for record in store.audit_records
    )


def test_untampered_approval_executes_in_the_temporal_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    for collection in (store.tasks, store.runs, store.approvals, store.events):
        collection.clear()
    dispatcher = RecordingDispatcher()
    monkeypatch.setattr(main, "run_dispatcher", dispatcher)
    request = _queue(RISKY_PROMPT, dispatcher)
    activities = activities_with(CountingGateway())
    waiting = _advance(activities, request)
    approval = store.approvals[waiting.approval_id]
    client.post(
        f"/api/v1/approvals/{approval.id}/approve",
        headers=DURABLE_HEADERS,
        json={"payload_hash": approval.payload_hash},
    )
    assert _advance(activities, request).phase == "completed"


# A3: expiry and decider -----------------------------------------------------------------


def test_settings_default_ttl_is_24_hours_and_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().approval_ttl_seconds == 86_400
    monkeypatch.setenv("ANUM_APPROVAL_TTL_SECONDS", "600")
    assert Settings().approval_ttl_seconds == 600


def test_approval_expires_after_the_configured_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.settings, "approval_ttl_seconds", 600)
    body = _waiting()
    approval = store.approvals[body["approval"]["id"]]
    assert approval.expires_at is not None
    assert approval.expires_at - approval.created_at == timedelta(seconds=600)


def test_expired_approval_cannot_be_approved_and_fails_the_run() -> None:
    body = _waiting()
    approval_id, run_id, task_id = body["approval"]["id"], body["run"]["id"], body["task"]["id"]
    store.approvals[approval_id].expires_at = utc_now() - timedelta(seconds=1)

    # Reads show the lapse before anything is stored.
    assert client.get(f"/api/v1/approvals/{approval_id}", headers=HEADERS).json()["status"] == "expired"
    assert store.approvals[approval_id].status == ApprovalStatus.PENDING

    late = _approve(approval_id, body["approval"]["payload_hash"])

    assert late.status_code == 410
    assert late.json()["error"]["code"] == "gone"
    stored = store.approvals[approval_id]
    assert stored.status == ApprovalStatus.EXPIRED and stored.decided_by is None
    assert store.runs[run_id].status == TaskStatus.FAILED
    assert store.tasks[task_id].status == TaskStatus.FAILED
    assert "expired" in store.runs[run_id].steps[-1].summary
    events = [event.type for event in store.events if event.payload.get("task_id") == task_id]
    assert events[-2:] == ["approval.expired", "agent_run.failed"]
    assert any(record.action == "approval.expired" for record in store.audit_records)

    again = _approve(approval_id, body["approval"]["payload_hash"])
    assert again.status_code == 410
    rejected = client.post(f"/api/v1/approvals/{approval_id}/reject", headers=HEADERS)
    assert rejected.status_code == 410


def test_as_viewed_only_changes_lapsed_pending_approvals() -> None:
    body = _waiting()
    approval = store.approvals[body["approval"]["id"]]
    now = utc_now()
    assert as_viewed(approval, now).status == ApprovalStatus.PENDING
    assert as_viewed(approval, approval.expires_at).status == ApprovalStatus.EXPIRED
    approval.status = ApprovalStatus.APPROVED
    assert as_viewed(approval, approval.expires_at + timedelta(days=1)).status == ApprovalStatus.APPROVED


def test_decider_and_decision_time_are_recorded_and_audited() -> None:
    body = _waiting()
    decided = _approve(body["approval"]["id"], body["approval"]["payload_hash"])
    approval = decided.json()["approval"]
    assert approval["decided_by"] == "owner_integrity"
    assert approval["decided_at"] is not None
    listed = client.get("/api/v1/approvals", headers=HEADERS).json()
    assert listed[0]["decided_by"] == "owner_integrity"
    audit = [record for record in store.audit_records if record.action == "approval.approved"]
    assert len(audit) == 1
    assert audit[0].actor == "owner_integrity"
    assert audit[0].metadata["payload_hash"] == body["approval"]["payload_hash"]


def test_temporal_activity_expires_a_lapsed_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    for collection in (store.tasks, store.runs, store.approvals, store.events):
        collection.clear()
    dispatcher = RecordingDispatcher()
    monkeypatch.setattr(main, "run_dispatcher", dispatcher)
    request = _queue(RISKY_PROMPT, dispatcher)
    activities = activities_with(CountingGateway())

    waiting = _advance(activities, request)
    approval = store.approvals[waiting.approval_id]
    # The workflow learns when to re-read: its wait is capped at this instant.
    assert waiting.approval_expires_at == pytest.approx(approval.expires_at.timestamp())

    approval.expires_at = utc_now() - timedelta(seconds=1)
    settled = _advance(activities, request)

    assert (settled.phase, settled.status) == ("failed", "failed")
    assert approval.status == ApprovalStatus.EXPIRED
    assert "expired" in store.runs[request.run_id].steps[-1].summary
    event_types = [event.type for event in store.events if event.payload.get("task_id") == request.task_id]
    assert "approval.expired" in event_types and "agent_run.completed" not in event_types


def test_expired_decision_in_temporal_mode_signals_the_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    for collection in (store.tasks, store.runs, store.approvals, store.events):
        collection.clear()
    dispatcher = RecordingDispatcher()
    monkeypatch.setattr(main, "run_dispatcher", dispatcher)
    request = _queue(RISKY_PROMPT, dispatcher)
    activities = activities_with(CountingGateway())
    waiting = _advance(activities, request)
    approval = store.approvals[waiting.approval_id]
    approval.expires_at = utc_now() - timedelta(seconds=1)

    late = client.post(
        f"/api/v1/approvals/{approval.id}/approve",
        headers=DURABLE_HEADERS,
        json={"payload_hash": approval.payload_hash},
    )

    assert late.status_code == 410
    assert approval.status == ApprovalStatus.EXPIRED
    assert dispatcher.signals[-1] == (request.task_id, "approval_decided", (approval.id,))
    assert _advance(activities, request).phase == "failed"


def test_state_of_reports_expiry_only_for_a_pending_approval() -> None:
    body = _waiting()
    run = store.runs[body["run"]["id"]]
    approval = store.approvals[body["approval"]["id"]]
    assert run.checkpoint.phase == RunPhase.WAITING_APPROVAL
    assert state_of(run, approval).approval_expires_at == approval.expires_at.timestamp()
    assert state_of(run).approval_expires_at is None
    approval.status = ApprovalStatus.REJECTED
    assert state_of(run, approval).approval_expires_at is None


def test_approval_response_schema_is_json_serialisable() -> None:
    body = _waiting()
    assert json.loads(json.dumps(body["approval"]))["payload_hash"] == body["approval"]["payload_hash"]


def test_runtime_refuses_an_approval_without_a_hash() -> None:
    """Rows from before payload binding (no hash) can never execute."""
    body = _waiting()
    approval = store.approvals[body["approval"]["id"]]
    task = store.tasks[body["task"]["id"]]
    run = store.runs[body["run"]["id"]]
    approval.payload_hash = None
    approval.status = ApprovalStatus.APPROVED
    approval.decided_at = utc_now()
    from anum_api.repository import InMemoryRepository

    runtime = AgentRuntime(CountingGateway(), InMemoryRepository(store))
    asyncio.run(runtime.resume_after_approval(task, run, approval, CONTEXT))
    assert run.status == TaskStatus.FAILED
