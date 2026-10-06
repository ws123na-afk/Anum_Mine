"""AgentRunWorkflow against a real Temporal server, including a worker restart mid-run.

Server, in order of preference:

* ``ANUM_TEST_TEMPORAL_TARGET`` (for example ``localhost:7233`` from
  ``docker compose -f infra/docker/compose.yaml up temporal``), or
* a Temporal dev server started by the SDK (``WorkflowEnvironment.start_local``), which
  downloads the Temporal CLI on first use or uses ``ANUM_TEST_TEMPORAL_CLI``.

When neither is available every test here is skipped with the reason.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from temporalio.client import Client
from temporalio.worker import Worker

from anum_api import main
from anum_api.agent_tools import ToolCall, ToolRegistry, ToolResult, default_tool_registry
from anum_api.durable_runs import AgentRunActivities, RunDispatcher
from anum_api.repository import AnumRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import ApprovalStatus, RunPhase, TenantContext
from anum_api.store import store
from anum_api.temporal_workflow import AgentRunInput, AgentRunState, AgentRunWorkflow, workflow_id_for

from test_durable_runs import HEADERS, CONTEXT, CountingGateway, RecordingDispatcher

pytestmark = pytest.mark.temporal

client = TestClient(main.app)


@asynccontextmanager
async def temporal_client() -> AsyncIterator[Client]:
    target = os.getenv("ANUM_TEST_TEMPORAL_TARGET")
    if target:
        try:
            yield await Client.connect(target)
        except RuntimeError as exc:
            pytest.skip(f"Temporal is not reachable at {target}: {exc}")
        return
    from temporalio.testing import WorkflowEnvironment

    # A real Temporal dev server (the SDK downloads the CLI on first use, or uses
    # ANUM_TEST_TEMPORAL_CLI). The time-skipping test server is a separate, simplified
    # implementation that does not reproduce worker shutdown mid-activity faithfully.
    try:
        env = await WorkflowEnvironment.start_local(
            dev_server_existing_path=os.getenv("ANUM_TEST_TEMPORAL_CLI") or None,
        )
    except Exception as exc:
        pytest.skip(f"Temporal dev server unavailable (set ANUM_TEST_TEMPORAL_TARGET): {exc}")
    try:
        yield env.client
    finally:
        await env.shutdown()


def _activities(gateway: CountingGateway, tools: ToolRegistry | None = None) -> AgentRunActivities:
    def factory(context: TenantContext, repository: AnumRepository) -> AgentRuntime:
        return AgentRuntime(gateway, repository, tools=tools)

    return AgentRunActivities(factory)


def _worker(temporal: Client, queue: str, activities: AgentRunActivities) -> Worker:
    return Worker(temporal, task_queue=queue, workflows=[AgentRunWorkflow], activities=[activities.advance_run])


def _queued_run(prompt: str, monkeypatch: pytest.MonkeyPatch) -> AgentRunInput:
    """Create and queue a run through the API; the test starts the workflow itself."""
    for collection in (store.tasks, store.runs, store.approvals, store.events):
        collection.clear()
    recording = RecordingDispatcher()
    monkeypatch.setattr(main, "run_dispatcher", recording)
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": "Durable", "prompt": prompt}).json()
    assert client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS).status_code == 200
    request = recording.started[-1]
    request.activity_timeout_seconds = 3
    request.approval_poll_seconds = 30
    return request


async def _eventually(predicate, timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_workflow_runs_a_task_to_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _queued_run("Summarize the project notes", monkeypatch)

    async def scenario() -> AgentRunState:
        async with temporal_client() as temporal:
            queue = f"anum-test-{uuid4().hex}"
            gateway = CountingGateway()
            async with _worker(temporal, queue, _activities(gateway)):
                dispatcher = RunDispatcher(target="", namespace="default", task_queue=queue, client=temporal)
                workflow_id = await dispatcher.start(request)
                assert workflow_id == workflow_id_for(request.tenant_id, request.workspace_id, request.task_id)
                assert await dispatcher.start(request) == workflow_id  # duplicate start reuses it
                result = await temporal.get_workflow_handle(workflow_id, result_type=AgentRunState).result()
            assert gateway.calls == 1
            return result

    result = asyncio.run(scenario())
    assert (result.phase, result.status) == ("completed", "completed")
    assert store.runs[request.run_id].result


def test_workflow_waits_for_the_approval_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _queued_run("Publish the final update", monkeypatch)

    async def scenario() -> AgentRunState:
        async with temporal_client() as temporal:
            queue = f"anum-test-{uuid4().hex}"
            async with _worker(temporal, queue, _activities(CountingGateway())):
                dispatcher = RunDispatcher(target="", namespace="default", task_queue=queue, client=temporal)
                workflow_id = await dispatcher.start(request)
                run = store.runs[request.run_id]
                await _eventually(lambda: run.checkpoint.phase == RunPhase.WAITING_APPROVAL)
                approval = store.approvals[run.checkpoint.approval_id]
                approval.status = ApprovalStatus.APPROVED  # what the API commits
                assert await dispatcher.approval_decided(CONTEXT, request.task_id, approval.id)
                return await temporal.get_workflow_handle(workflow_id, result_type=AgentRunState).result()

    result = asyncio.run(scenario())
    assert (result.phase, result.status) == ("completed", "completed")


def test_run_survives_a_worker_restart_mid_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker 1 dies while the tool is running; worker 2 resumes from the checkpoint."""
    request = _queued_run("Summarize the project notes", monkeypatch)
    started: list[str] = []
    base = default_tool_registry()

    def registry(hang: bool) -> ToolRegistry:
        tools = ToolRegistry()
        for name in sorted(base.names):
            definition = base.definition(name)
            assert definition is not None

            async def handler(call: ToolCall, context: TenantContext, _name: str = name) -> ToolResult:
                started.append(_name)
                if hang:
                    await asyncio.Event().wait()  # until the worker is stopped
                return ToolResult(status="succeeded", summary=f"{_name} finished", output={})

            tools.register(definition, handler)
        return tools

    async def scenario() -> tuple[AgentRunState, CountingGateway, CountingGateway]:
        async with temporal_client() as temporal:
            queue = f"anum-test-{uuid4().hex}"
            dispatcher = RunDispatcher(target="", namespace="default", task_queue=queue, client=temporal)
            first_gateway, second_gateway = CountingGateway(), CountingGateway()

            first = _worker(temporal, queue, _activities(first_gateway, registry(hang=True)))
            async with first:
                workflow_id = await dispatcher.start(request)
                await _eventually(lambda: started == ["anum.respond"])
                assert store.runs[request.run_id].checkpoint.phase == RunPhase.EXECUTING
            # Leaving the block shuts worker 1 down and cancels its in-flight activity.

            handle = temporal.get_workflow_handle(workflow_id, result_type=AgentRunState)
            async with _worker(temporal, queue, _activities(second_gateway, registry(hang=False))):
                result = await asyncio.wait_for(handle.result(), timeout=60)
            history = await handle.fetch_history()
            assert history.events
            return result, first_gateway, second_gateway

    result, first_gateway, second_gateway = asyncio.run(scenario())
    assert (result.phase, result.status) == ("completed", "completed")
    assert first_gateway.calls == 1 and second_gateway.calls == 0
    assert started == ["anum.respond", "anum.respond"]
    assert store.runs[request.run_id].result == "anum.respond finished"
