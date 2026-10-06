"""Durable agent runs (ANUM_RUNTIME_BACKEND=temporal) without a Temporal server.

These drive the ``anum.advance_run`` activity body directly, which is everything
that touches ANUM state; the workflow only sequences it. "Worker restarts" are
simulated by a tool that crashes mid-execution and a fresh activities instance
(new runtime, new gateway) taking over from the persisted checkpoint. The same
scenarios against a real Temporal server live in ``test_temporal_worker.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from anum_api import main
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult, default_tool_registry
from anum_api.durable_runs import AgentRunActivities, RunDispatcher, build_run_dispatcher, run_input_for
from anum_api.model_gateway import MockModelGateway
from anum_api.repository import AnumRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import RiskLevel, RunPhase, TaskStatus, TenantContext
from anum_api.settings import Settings
from anum_api.store import store
from anum_api.temporal_workflow import AgentRunInput, workflow_id_for

HEADERS = {
    "x-tenant-id": "tenant_durable",
    "x-workspace-id": "workspace_durable",
    "x-user-id": "user_durable",
    "x-user-roles": "owner",
}
CONTEXT = TenantContext(
    tenant_id="tenant_durable", workspace_id="workspace_durable", user_id="user_durable", roles=["owner"]
)


class RecordingDispatcher(RunDispatcher):
    def __init__(self) -> None:
        super().__init__(target="unused:7233", namespace="default", task_queue="test")
        self.started: list[AgentRunInput] = []
        self.signals: list[tuple[str, str, tuple[str, ...]]] = []

    async def start(self, request: AgentRunInput) -> str:
        self.started.append(request)
        return workflow_id_for(request.tenant_id, request.workspace_id, request.task_id)

    async def _signal(self, context: TenantContext, task_id: str, signal: str, *args: str) -> bool:
        self.signals.append((task_id, signal, args))
        return True


class CountingGateway(MockModelGateway):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def generate_text(self, prompt: str):  # type: ignore[override]
        self.calls += 1
        return await super().generate_text(prompt)


class WorkerCrashed(BaseException):
    """Stands in for the process dying: not an Exception, so nothing handles it."""


def crashing_registry(crashes: list[str], executed: list[str]) -> ToolRegistry:
    """Default tools, but each tool crashes the first time it runs."""
    base = default_tool_registry()
    registry = ToolRegistry()
    for name in sorted(base.names):
        definition = base.definition(name)
        assert definition is not None

        async def handler(call: ToolCall, context: TenantContext, _name: str = name) -> ToolResult:
            if _name not in crashes:
                crashes.append(_name)
                raise WorkerCrashed(_name)
            executed.append(_name)
            return ToolResult(status="succeeded", summary=f"{_name} done", output={})

        registry.register(definition, handler)
    return registry


def activities_with(gateway: CountingGateway, tools: ToolRegistry | None = None) -> AgentRunActivities:
    def factory(context: TenantContext, repository: AnumRepository) -> AgentRuntime:
        return AgentRuntime(gateway, repository, tools=tools)

    return AgentRunActivities(factory)


@pytest.fixture
def dispatcher(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingDispatcher]:
    for collection in (store.tasks, store.runs, store.approvals, store.events):
        collection.clear()
    recording = RecordingDispatcher()
    monkeypatch.setattr(main, "run_dispatcher", recording)
    yield recording


client = TestClient(main.app)


def _queue(prompt: str, dispatcher: RecordingDispatcher) -> AgentRunInput:
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": "Durable", "prompt": prompt}).json()
    response = client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["status"] == "queued"
    assert payload["run"]["status"] == "queued"
    assert payload["run"]["checkpoint"]["phase"] == "planning"
    assert payload["run"]["steps"][0]["metadata"]["workflow_id"] == (
        f"anum-run/tenant_durable/workspace_durable/{task['id']}"
    )
    request = dispatcher.started[-1]
    assert (request.tenant_id, request.workspace_id, request.task_id) == (
        "tenant_durable", "workspace_durable", task["id"],
    )
    assert request.run_id == payload["run"]["id"]
    return request


def _shown(approval_id: str) -> dict[str, str]:
    """The decision body a client sends: the payload hash it displayed."""
    approval = client.get(f"/api/v1/approvals/{approval_id}", headers=HEADERS).json()
    return {"payload_hash": approval["payload_hash"]}


def _advance(activities: AgentRunActivities, request: AgentRunInput):
    return asyncio.run(activities.advance(CONTEXT, request))


def test_settings_select_the_runtime_backend() -> None:
    assert build_run_dispatcher(Settings()) is None
    dispatcher = build_run_dispatcher(Settings(runtime_backend="temporal", temporal_task_queue="q"))
    assert isinstance(dispatcher, RunDispatcher) and dispatcher.task_queue == "q"
    with pytest.raises(ValueError):
        Settings(runtime_backend="celery")


def test_queued_run_is_planned_then_executed_by_activity_steps(dispatcher: RecordingDispatcher) -> None:
    request = _queue("Summarize the project notes", dispatcher)
    gateway = CountingGateway()
    activities = activities_with(gateway)

    planned = _advance(activities, request)
    assert planned.phase == "tool_ready"
    done = _advance(activities, request)
    assert (done.phase, done.status) == ("completed", "completed")
    again = _advance(activities, request)  # retries of a settled run change nothing
    assert again.phase == "completed"

    assert gateway.calls == 1
    run = store.runs[request.run_id]
    assert [step.type for step in run.steps] == ["queued", "model_call", "tool_proposal", "tool_result", "final"]
    assert store.tasks[request.task_id].status == TaskStatus.COMPLETED
    event_types = [event.type for event in store.events if event.payload.get("task_id") == request.task_id]
    assert "agent_run.completed" in event_types
    assert "agent_run.resumed" not in event_types


def test_second_run_request_for_a_queued_task_is_rejected(dispatcher: RecordingDispatcher) -> None:
    request = _queue("Summarize the project notes", dispatcher)
    again = client.post(f"/api/v1/tasks/{request.task_id}/run", headers=HEADERS)
    assert again.status_code == 409
    assert len(dispatcher.started) == 1


def test_worker_crash_mid_tool_resumes_from_checkpoint_without_replanning(
    dispatcher: RecordingDispatcher,
) -> None:
    request = _queue("Summarize the project notes", dispatcher)
    crashes: list[str] = []
    executed: list[str] = []
    first_gateway = CountingGateway()
    first_worker = activities_with(first_gateway, crashing_registry(crashes, executed))

    assert _advance(first_worker, request).phase == "tool_ready"
    with pytest.raises(WorkerCrashed):
        _advance(first_worker, request)
    # The `executing` checkpoint was committed before the tool ran.
    assert store.runs[request.run_id].checkpoint.phase == RunPhase.EXECUTING

    second_gateway = CountingGateway()
    second_worker = activities_with(second_gateway, crashing_registry(crashes, executed))
    resumed = _advance(second_worker, request)

    assert (resumed.phase, resumed.status) == ("completed", "completed")
    assert first_gateway.calls == 1 and second_gateway.calls == 0  # never re-planned
    assert executed == ["anum.respond"]  # idempotent low-risk tool re-executed once
    event_types = [event.type for event in store.events if event.payload.get("task_id") == request.task_id]
    assert event_types.count("agent_run.resumed") == 1


def test_approval_waits_for_decision_and_is_applied_by_the_worker(dispatcher: RecordingDispatcher) -> None:
    request = _queue("Publish the final update", dispatcher)
    activities = activities_with(CountingGateway())

    waiting = _advance(activities, request)
    assert waiting.phase == "waiting_approval" and waiting.approval_id
    assert _advance(activities, request).phase == "waiting_approval"  # still pending: no change

    decided = client.post(
        f"/api/v1/approvals/{waiting.approval_id}/approve", headers=HEADERS, json=_shown(waiting.approval_id)
    )
    assert decided.status_code == 200
    body = decided.json()
    assert body["approval"]["status"] == "approved"
    assert body["run"]["status"] == "waiting_approval"  # the worker, not the API, executes it
    assert dispatcher.signals == [(request.task_id, "approval_decided", (waiting.approval_id,))]

    done = _advance(activities, request)
    assert (done.phase, done.status) == ("completed", "completed")
    assert store.runs[request.run_id].result == "Executed the approved external action through the mock adapter."


def test_rejected_approval_fails_the_run(dispatcher: RecordingDispatcher) -> None:
    request = _queue("Publish the final update", dispatcher)
    activities = activities_with(CountingGateway())
    waiting = _advance(activities, request)
    assert client.post(f"/api/v1/approvals/{waiting.approval_id}/reject", headers=HEADERS).status_code == 200
    failed = _advance(activities, request)
    assert (failed.phase, failed.status) == ("failed", "failed")


def test_an_approval_chain_signals_the_workflow_only_when_complete(dispatcher: RecordingDispatcher) -> None:
    """Temporal path: partial approvals leave the workflow waiting; the last one signals it."""
    from anum_api.governance import governance_store

    governance_store.clear()
    rule = client.post(
        "/api/v1/organization/approval-rules",
        headers=HEADERS,
        json={"name": "Three approvers", "action_pattern": "external.*", "minimum_approvers": 3},
    )
    assert rule.status_code == 201, rule.text
    try:
        request = _queue("Publish the final update", dispatcher)
        activities = activities_with(CountingGateway())
        waiting = _advance(activities, request)
        assert waiting.phase == "waiting_approval" and waiting.approval_id
        body = _shown(waiting.approval_id)

        for number, approver in enumerate(("approver_b", "approver_c"), start=1):
            partial = client.post(
                f"/api/v1/approvals/{waiting.approval_id}/approve",
                headers={**HEADERS, "x-user-id": approver},
                json=body,
            )
            assert partial.status_code == 200, partial.text
            assert partial.json()["approval"]["status"] == "pending"
            assert len(partial.json()["approval"]["approvers"]) == number
            assert dispatcher.signals == []
            # The worker sees a still-pending approval and keeps waiting.
            assert _advance(activities, request).phase == "waiting_approval"

        final = client.post(
            f"/api/v1/approvals/{waiting.approval_id}/approve",
            headers={**HEADERS, "x-user-id": "approver_d"},
            json=body,
        )
        assert final.status_code == 200, final.text
        assert final.json()["approval"]["status"] == "approved"
        assert dispatcher.signals == [(request.task_id, "approval_decided", (waiting.approval_id,))]
        done = _advance(activities, request)
        assert (done.phase, done.status) == ("completed", "completed")
    finally:
        governance_store.clear()


def test_crash_during_an_approved_high_risk_action_is_never_repeated(dispatcher: RecordingDispatcher) -> None:
    request = _queue("Publish the final update", dispatcher)
    crashes: list[str] = []
    executed: list[str] = []
    worker = activities_with(CountingGateway(), crashing_registry(crashes, executed))
    waiting = _advance(worker, request)
    client.post(f"/api/v1/approvals/{waiting.approval_id}/approve", headers=HEADERS, json=_shown(waiting.approval_id))
    with pytest.raises(WorkerCrashed):
        _advance(worker, request)

    settled = _advance(activities_with(CountingGateway(), crashing_registry(crashes, executed)), request)

    assert (settled.phase, settled.status) == ("failed", "failed")
    assert executed == []
    assert "outcome is unknown" in store.runs[request.run_id].steps[-1].summary


def test_cancelled_task_settles_the_workflow(dispatcher: RecordingDispatcher) -> None:
    request = _queue("Publish the final update", dispatcher)
    activities = activities_with(CountingGateway())
    assert _advance(activities, request).phase == "waiting_approval"
    assert client.post(f"/api/v1/tasks/{request.task_id}/cancel", headers=HEADERS).status_code == 200
    assert (request.task_id, "cancel", ()) in dispatcher.signals
    assert _advance(activities, request).phase == "cancelled"


def test_resume_endpoint_restarts_the_workflow_for_a_stranded_run(dispatcher: RecordingDispatcher) -> None:
    request = _queue("Summarize the project notes", dispatcher)
    assert _advance(activities_with(CountingGateway()), request).phase == "tool_ready"

    response = client.post(f"/api/v1/agent-runs/{request.run_id}/resume", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["run"]["checkpoint"]["phase"] == "tool_ready"
    assert len(dispatcher.started) == 2 and dispatcher.started[-1].run_id == request.run_id


def test_activity_rejects_runs_outside_the_callers_workspace(dispatcher: RecordingDispatcher) -> None:
    request = _queue("Summarize the project notes", dispatcher)
    other = TenantContext(tenant_id="tenant_other", workspace_id="workspace_durable", user_id="u", roles=["owner"])
    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities_with(CountingGateway()).advance(other, request))
    assert raised.value.type == "RunNotFound" and raised.value.non_retryable


def test_run_input_carries_explicit_tenant_context() -> None:
    request = run_input_for(CONTEXT, "task_1", "run_1")
    assert (request.tenant_id, request.workspace_id, request.user_id, request.roles) == (
        "tenant_durable", "workspace_durable", "user_durable", ["owner"],
    )


def test_unavailable_temporal_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    class DownDispatcher(RecordingDispatcher):
        async def start(self, request: AgentRunInput) -> str:
            raise RuntimeError("connection refused")

    monkeypatch.setattr(main, "run_dispatcher", DownDispatcher())
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": "Down", "prompt": "Summarize"}).json()
    assert client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS).status_code == 503


def test_unknown_tool_definition_is_not_retried_after_a_crash() -> None:
    """A tool missing from the registry at recovery time is treated as non-idempotent."""
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="anum.respond", description="x", risk_level=RiskLevel.LOW, idempotent=False),
        lambda call, context: None,  # type: ignore[arg-type,return-value]
    )
    from anum_api.repository import InMemoryRepository
    from anum_api.store import InMemoryStore
    from anum_api.schemas import Task, new_id, utc_now

    repository = InMemoryRepository(InMemoryStore())
    runtime = AgentRuntime(CountingGateway(), repository, tools=registry)
    now = utc_now()
    task = Task(
        id=new_id("task"), title="t", prompt="p", status=TaskStatus.RUNNING,
        tenant_id=CONTEXT.tenant_id, workspace_id=CONTEXT.workspace_id, created_at=now, updated_at=now,
    )
    run = runtime.new_run(task)
    run.checkpoint.phase = RunPhase.EXECUTING
    run.checkpoint.tool_call = {"name": "anum.respond", "arguments": {}}
    asyncio.run(runtime.recover_interrupted_execution(task, run, CONTEXT))
    assert run.status == TaskStatus.FAILED


def test_a_run_not_yet_committed_is_retried_before_it_is_declared_missing(
    dispatcher: RecordingDispatcher,
) -> None:
    """The workflow can start before the API's transaction commits the queued run."""
    from dataclasses import replace

    from anum_api.durable_runs import MISSING_RUN_RETRY_ATTEMPTS

    request = _queue("Summarize the project notes", dispatcher)
    unseen = replace(request, run_id="run_not_committed_yet")
    activities = activities_with(CountingGateway())

    def attempt(number: int) -> ApplicationError:
        environment = ActivityEnvironment()
        environment.info = replace(environment.info, attempt=number)
        with pytest.raises(ApplicationError) as raised:
            asyncio.run(environment.run(activities.advance_run, unseen))
        return raised.value

    early = attempt(1)
    assert early.type == "RunNotVisibleYet" and not early.non_retryable
    final = attempt(MISSING_RUN_RETRY_ATTEMPTS + 1)
    assert final.type == "RunNotFound" and final.non_retryable
