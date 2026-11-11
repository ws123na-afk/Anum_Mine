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


def test_no_row_lock_or_transaction_is_held_while_the_model_plans_or_the_tool_runs(
    repository_factory, app_session, seed_scopes: None
) -> None:
    """CLAUDE.md: never keep a transaction or a FOR UPDATE lock open across a model or tool
    call. While the model answers and while the tool works, another connection must be able
    to lock the task row at once (NOWAIT) and see the committed checkpoint."""
    from sqlalchemy import text

    from anum_api.agent_tools import ToolRegistry, ToolResult, default_tool_registry

    context, request = _queued(repository_factory, "Summarize the project notes")
    seen: list[tuple[str, str]] = []

    def probe(moment: str) -> None:
        with app_session(context) as session:
            session.execute(text("set local lock_timeout = '1ms'"))
            locked = session.execute(
                text("select id from tasks where id = :id for update nowait"), {"id": request.task_id}
            ).scalar_one()
            assert locked == request.task_id
            phase = session.execute(
                text("select checkpoint->>'phase' from agent_runs where id = :id"), {"id": request.run_id}
            ).scalar_one()
            seen.append((moment, phase))

    class ProbingGateway(CountingGateway):
        async def generate_text(self, prompt: str):  # type: ignore[override]
            probe("model")
            return await super().generate_text(prompt)

    base = default_tool_registry()
    tools = ToolRegistry()
    for name in sorted(base.names):
        definition = base.definition(name)
        assert definition is not None

        async def handler(call, ctx, _name: str = name) -> ToolResult:
            probe("tool")
            return ToolResult(status="succeeded", summary=f"{_name} done", output={})

        tools.register(definition, handler)

    activities = _activities(repository_factory, ProbingGateway(), tools)
    assert asyncio.run(activities.advance(context, request)).phase == "tool_ready"
    done = asyncio.run(activities.advance(context, request))
    assert (done.phase, done.status) == ("completed", "completed")
    # The model ran while the run was still at its committed planning checkpoint; the tool ran
    # after `executing` was committed, with the task row free both times.
    assert seen == [("model", "planning"), ("tool", "executing")]


def test_a_cancel_while_the_tool_runs_is_not_overwritten(repository_factory, app_session, seed_scopes: None) -> None:
    """The settle step re-checks the claim: a cancel committed while the tool ran wins, and the
    tool's outcome does not complete the run over it."""
    from sqlalchemy import text

    from anum_api.agent_tools import ToolRegistry, ToolResult, default_tool_registry

    context, request = _queued(repository_factory, "Summarize the project notes")

    def cancel() -> None:
        with repository_factory(context, commit=True) as repository:
            # Fails at once (instead of waiting forever) if the worker still holds the row lock.
            repository.session.execute(text("set local lock_timeout = '1s'"))
            task = repository.get_task_for_update(request.task_id, context)
            run = repository.get_run(request.run_id, context)
            assert task is not None and run is not None
            task.status = run.status = TaskStatus.CANCELLED
            run.checkpoint.phase = RunPhase.CANCELLED
            run.checkpoint.version += 1
            repository.save_task(task)
            repository.save_run(run)

    base = default_tool_registry()
    tools = ToolRegistry()
    for name in sorted(base.names):
        definition = base.definition(name)
        assert definition is not None

        async def handler(call, ctx, _name: str = name) -> ToolResult:
            cancel()  # would block on the row lock if the worker still held it
            return ToolResult(status="succeeded", summary=f"{_name} done", output={})

        tools.register(definition, handler)

    activities = _activities(repository_factory, CountingGateway(), tools)
    assert asyncio.run(activities.advance(context, request)).phase == "tool_ready"
    state = asyncio.run(activities.advance(context, request))
    assert (state.phase, state.status) == ("cancelled", "cancelled")
