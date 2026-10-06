"""The durable-run activity against PostgreSQL with RLS (the worker's real storage).

Each activity step runs in its own committed, tenant-scoped transaction as the
non-owner application role, so these tests check that the ``executing`` checkpoint
is durable before a tool runs and that a crashed step resumes from what was
committed, without re-planning.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import pytest
from temporalio.exceptions import ApplicationError

from anum_api.db.repository import SqlAlchemyRepository
from anum_api.durable_runs import AgentRunActivities, run_input_for
from anum_api.repository import AnumRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import ApprovalStatus, RunPhase, Task, TaskStatus, TenantContext, new_id, utc_now

from conftest import TENANT_B, WORKSPACE_B, tenant_context
from test_durable_runs import CountingGateway, WorkerCrashed, crashing_registry

pytestmark = pytest.mark.database


def _activities(
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
    gateway: CountingGateway,
    tools=None,
) -> AgentRunActivities:
    def factory(context: TenantContext, repository: AnumRepository) -> AgentRuntime:
        return AgentRuntime(gateway, repository, tools=tools)

    return AgentRunActivities(factory, unit_of_work=lambda context: repository_factory(context, commit=True))


def _queued(repository_factory, prompt: str):
    context = tenant_context()
    now = utc_now()
    task = Task(
        id=new_id("task"), title="Durable", prompt=prompt, status=TaskStatus.QUEUED,
        tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, updated_at=now,
    )
    with repository_factory(context, commit=True) as repository:
        repository.create_task(task)
        run = AgentRuntime(CountingGateway(), repository).new_run(task, status=TaskStatus.QUEUED)
        repository.save_run(run)
    return context, run_input_for(context, task.id, run.id)


def _phase(repository_factory, context: TenantContext, run_id: str) -> RunPhase:
    with repository_factory(context) as repository:
        run = repository.get_run(run_id, context)
        assert run is not None
        return run.checkpoint.phase


def test_worker_restart_resumes_committed_checkpoint(repository_factory, seed_scopes: None) -> None:
    context, request = _queued(repository_factory, "Summarize the project notes")
    crashes: list[str] = []
    executed: list[str] = []
    first_gateway, second_gateway = CountingGateway(), CountingGateway()
    first = _activities(repository_factory, first_gateway, crashing_registry(crashes, executed))

    assert asyncio.run(first.advance(context, request)).phase == "tool_ready"
    with pytest.raises(WorkerCrashed):
        asyncio.run(first.advance(context, request))
    assert _phase(repository_factory, context, request.run_id) == RunPhase.EXECUTING

    second = _activities(repository_factory, second_gateway, crashing_registry(crashes, executed))
    state = asyncio.run(second.advance(context, request))

    assert (state.phase, state.status) == ("completed", "completed")
    assert first_gateway.calls == 1 and second_gateway.calls == 0
    assert executed == ["anum.respond"]
    with repository_factory(context) as repository:
        task = repository.get_task(request.task_id, context)
        assert task is not None and task.status == TaskStatus.COMPLETED
        types = [event.type for event in repository.list_events(context)]
        assert "agent_run.resumed" in types and "agent_run.completed" in types


def test_approval_flow_across_transactions(repository_factory, seed_scopes: None) -> None:
    context, request = _queued(repository_factory, "Publish the final update")
    activities = _activities(repository_factory, CountingGateway())
    waiting = asyncio.run(activities.advance(context, request))
    assert waiting.phase == "waiting_approval"

    with repository_factory(context, commit=True) as repository:
        approval = repository.get_approval_for_update(waiting.approval_id, context)
        assert approval is not None
        approval.status = ApprovalStatus.APPROVED
        approval.decided_at = utc_now()
        repository.save_approval(approval)

    done = asyncio.run(activities.advance(context, request))
    assert (done.phase, done.status) == ("completed", "completed")


def test_activity_cannot_reach_another_tenants_run(repository_factory, seed_scopes: None) -> None:
    _, request = _queued(repository_factory, "Summarize the project notes")
    other = tenant_context(TENANT_B, WORKSPACE_B)
    with pytest.raises(ApplicationError) as raised:
        asyncio.run(_activities(repository_factory, CountingGateway()).advance(other, request))
    assert raised.value.type == "RunNotFound"
