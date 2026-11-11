"""Durable agent runs on Temporal: the activity, the API-side dispatcher, and wiring.

``ANUM_RUNTIME_BACKEND=temporal`` makes ``POST /api/v1/tasks/{id}/run`` persist a
queued run and start :class:`~anum_api.temporal_workflow.AgentRunWorkflow`; the
worker (``python -m anum_api.worker``) executes it. Every activity call opens a
tenant-scoped unit of work (RLS context set, as in API requests), loads the run's
persisted checkpoint, advances it by one step, and commits. See
``docs/agent-runtime.md`` for the delivery guarantees.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .agent_tools import ToolCall, ToolResult, running_in
from .model_budget import ModelBudgetExceededError
from .repository import AnumRepository
from .runtime import AgentRuntime
from .schemas import AgentRun, Approval, ApprovalStatus, RunPhase, Task, TaskStatus, TenantContext, utc_now
from .temporal_workflow import (
    ADVANCE_ACTIVITY,
    APPROVAL_SIGNAL,
    CANCEL_SIGNAL,
    WORKFLOW_NAME,
    AgentRunInput,
    AgentRunState,
    AgentRunWorkflow,
    workflow_id_for,
)
from .telemetry import set_tenant_attributes, telemetry, temporal_interceptors
from .valkey import CoordinationUnavailable, LockNotAcquired, RunLockManager

logger = logging.getLogger(__name__)

RuntimeFactory = Callable[[TenantContext, AnumRepository], AgentRuntime]
UnitOfWork = Callable[[TenantContext], AbstractContextManager[AnumRepository]]


def context_for(request: AgentRunInput) -> TenantContext:
    return TenantContext(
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        user_id=request.user_id,
        roles=list(request.roles),
    )


def run_input_for(context: TenantContext, task_id: str, run_id: str) -> AgentRunInput:
    return AgentRunInput(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        roles=list(context.roles),
        task_id=task_id,
        run_id=run_id,
    )


def state_of(run: AgentRun, approval: Approval | None = None) -> AgentRunState:
    """The workflow's view of a run; a pending approval adds its expiry (epoch seconds)."""
    expires_at = (
        approval.expires_at.timestamp()
        if approval is not None
        and approval.status == ApprovalStatus.PENDING
        and approval.expires_at is not None
        and run.checkpoint.phase == RunPhase.WAITING_APPROVAL
        else None
    )
    return AgentRunState(
        run_id=run.id,
        phase=run.checkpoint.phase.value,
        status=run.status.value,
        approval_id=run.checkpoint.approval_id,
        approval_expires_at=expires_at,
    )


@contextmanager
def tenant_unit_of_work(context: TenantContext) -> Iterator[AnumRepository]:
    """One committed, tenant-scoped repository transaction outside an HTTP request.

    Mirrors ``dependencies.repository_context``: PostgreSQL sessions set the RLS
    tenant and workspace first, and recorded events are handed to the event bus
    only after commit.
    """
    from .dependencies import event_runtime, memory_repository
    from .event_bus import EventCollectingRepository
    from .settings import settings

    if settings.repository_backend == "memory":
        collecting = EventCollectingRepository(memory_repository)
        yield collecting  # type: ignore[misc]
        event_runtime.after_commit(collecting.recorded_events)
        return
    if settings.repository_backend != "postgresql":
        raise RuntimeError(f"Unsupported repository backend: {settings.repository_backend}")

    from .db.repository import SqlAlchemyRepository
    from .db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        # The user too: the run's task can be private to the person it runs for (0163_private_tasks).
        set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
        session.info["user_id"] = context.user_id
        collecting = EventCollectingRepository(
            SqlAlchemyRepository(session, created_by_user_id=context.user_id)
        )
        yield collecting  # type: ignore[misc]
        session.commit()
        event_runtime.after_commit(collecting.recorded_events)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# The API starts the workflow before its request transaction commits, so the first
# attempts can run before the queued task and run are visible. Retry those (Temporal
# backs off 1s, 2s, 4s, ...) and only give up once the run has clearly never existed.
MISSING_RUN_RETRY_ATTEMPTS = 5


def _missing_run_error(request: "AgentRunInput") -> ApplicationError:
    message = f"Run {request.run_id} for task {request.task_id} not found in this workspace"
    attempt = activity.info().attempt if activity.in_activity() else MISSING_RUN_RETRY_ATTEMPTS + 1
    if attempt <= MISSING_RUN_RETRY_ATTEMPTS:
        return ApplicationError(f"{message} yet; retrying", type="RunNotVisibleYet")
    return ApplicationError(message, type="RunNotFound", non_retryable=True)


# anum.temporal.activity.outcomes values for the activity's own ApplicationErrors.
ACTIVITY_ERROR_OUTCOMES = {"RunNotVisibleYet": "not_visible_yet", "RunNotFound": "not_found"}


class AgentRunActivities:
    """The ``anum.advance_run`` activity. Each call moves a run forward one step.

    Steps, chosen from the persisted checkpoint phase:

    * ``planning`` -> plan (model call, policy) and commit ``tool_ready``,
      ``waiting_approval`` or ``failed``.
    * ``tool_ready`` or a decided ``waiting_approval`` -> re-check policy and commit
      ``executing``; then, in a second transaction, run the tool and commit
      ``completed``. Committing ``executing`` first is what lets a retry tell that a
      previous worker died mid-tool.
    * ``executing`` (found at the start of a call) -> the previous attempt stopped
      mid-tool: :meth:`AgentRuntime.recover_interrupted_execution`.
    * a pending approval past its ``expires_at`` -> mark it ``expired`` and fail the
      run (the workflow's wait is capped at the expiry, so this is observed on time).
    * a decided approval whose checkpointed call no longer hashes to the approved
      ``payload_hash`` -> fail the run with an audit record; nothing executes.
    * terminal or still-pending approval -> no change.
    """

    def __init__(
        self,
        runtime_factory: RuntimeFactory,
        *,
        unit_of_work: UnitOfWork = tenant_unit_of_work,
        locks: RunLockManager | None = None,
    ) -> None:
        self.runtime_factory = runtime_factory
        self.unit_of_work = unit_of_work
        self.locks = locks or RunLockManager(None)

    @activity.defn(name=ADVANCE_ACTIVITY)
    async def advance_run(self, request: AgentRunInput) -> AgentRunState:
        context = context_for(request)
        set_tenant_attributes(trace.get_current_span(), request.tenant_id, request.workspace_id)
        started = time.perf_counter()
        outcome = "error"
        try:
            async with self.locks.hold(context, request.task_id):
                state = await self.advance(context, request)
            outcome = "advanced"
            return state
        except LockNotAcquired as exc:
            # Retryable: another worker or API replica is handling this task right now.
            outcome = "locked"
            raise ApplicationError("Run is locked by another worker", type="RunLocked") from exc
        except CoordinationUnavailable:
            outcome = "coordination_unavailable"
            raise
        except ApplicationError as exc:
            outcome = ACTIVITY_ERROR_OUTCOMES.get(exc.type or "", "application_error")
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            telemetry.record_activity(ADVANCE_ACTIVITY, outcome, time.perf_counter() - started)

    def _load(self, repository: AnumRepository, context: TenantContext, request: AgentRunInput):
        task = repository.get_task_for_update(request.task_id, context)
        run = repository.get_run(request.run_id, context)
        if task is None or run is None or run.task_id != task.id:
            raise _missing_run_error(request)
        return task, run

    async def advance(self, context: TenantContext, request: AgentRunInput) -> AgentRunState:
        """One step, in short units of work (each in a worker thread, docs/realtime.md#event-loop).

        No transaction and no row lock is open while the model plans or a tool works
        (the ``inline_runs.py`` rule): a unit reads and decides, the slow call runs with
        none open, and a new unit re-checks that the run is still where the first one left
        it before writing the outcome. A cancel or another worker meanwhile wins.
        """
        step = await asyncio.to_thread(self._begin, context, request)
        if isinstance(step, AgentRunState):
            return step
        if isinstance(step, _Planning):
            return await self._plan(context, request, step)
        return await self._execute(context, request, step)

    def _begin(self, context: TenantContext, request: AgentRunInput) -> AgentRunState | _Planning | _Executing:
        with self.unit_of_work(context) as repository:
            task, run = self._load(repository, context, request)
            if task.status == TaskStatus.CANCELLED or run.checkpoint.phase == RunPhase.CANCELLED:
                if run.status != TaskStatus.CANCELLED:
                    run.status = TaskStatus.CANCELLED
                    run.checkpoint.phase = RunPhase.CANCELLED
                    run.checkpoint.version += 1
                    run.updated_at = utc_now()
                    repository.save_run(run)
                return state_of(run)
            if run.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                return state_of(run)

            runtime = self.runtime_factory(context, repository)
            phase = run.checkpoint.phase
            if phase == RunPhase.PLANNING:
                # Nothing is written: the model plans with no transaction open, then
                # ``_apply_plan`` re-checks this checkpoint version under the lock.
                return _Planning(task.model_copy(deep=True), run.model_copy(deep=True), run.checkpoint.version)
            approval_id: str | None = None
            call = None
            if phase == RunPhase.TOOL_READY:
                call = runtime.begin_execution(task, run, context)
            elif phase == RunPhase.WAITING_APPROVAL:
                approval = (
                    repository.get_approval_for_update(run.checkpoint.approval_id, context)
                    if run.checkpoint.approval_id
                    else None
                )
                if approval is None:
                    return state_of(run)
                runtime.expire_if_due(task, approval, context)
                if approval.status == ApprovalStatus.PENDING:
                    return state_of(run, approval)
                approval_id = approval.id
                call = runtime.begin_execution(task, run, context, approval=approval)
            elif phase == RunPhase.EXECUTING:
                # The previous attempt stopped mid-tool: decide here, repeat (if allowed) below.
                call = runtime.prepare_recovery(task, run, context)
            repository.save_task(task)
            repository.save_run(run)
            if call is None:
                pending = (
                    repository.get_approval(run.checkpoint.approval_id, context)
                    if run.checkpoint.phase == RunPhase.WAITING_APPROVAL and run.checkpoint.approval_id
                    else None
                )
                return state_of(run, pending)
            # Committed as ``executing`` before the tool runs, so a crash mid-tool is visible.
            return _Executing(task.model_copy(deep=True), run.model_copy(deep=True), call, approval_id)

    async def _plan(self, context: TenantContext, request: AgentRunInput, step: _Planning) -> AgentRunState:
        planner = self.runtime_factory(context, _NoUnitOpen())  # type: ignore[arg-type]
        planner.start_planning(step.task, step.run)
        try:
            planned, learned = await planner.plan_with_model(step.task, step.run, context, offload=True)
        except ModelBudgetExceededError as exc:
            # Retrying cannot help until the budget resets or is raised: fail the run with
            # the budget message instead of retrying the activity.
            return await asyncio.to_thread(self._fail_planning, context, request, step, exc.message)
        return await asyncio.to_thread(self._apply_plan, context, request, step, planned, learned)

    def _claimed_planning(self, repository: AnumRepository, context: TenantContext, request: AgentRunInput,
                          step: _Planning) -> tuple[Task, AgentRun] | AgentRunState:
        """The locked task and run if the run is still the ``planning`` checkpoint this step read."""
        task, run = self._load(repository, context, request)
        if (
            task.status == TaskStatus.CANCELLED
            or run.checkpoint.phase != RunPhase.PLANNING
            or run.checkpoint.version != step.version
        ):
            return state_of(run)  # cancelled or planned by another attempt meanwhile
        return task, run

    def _apply_plan(self, context: TenantContext, request: AgentRunInput, step: _Planning,
                    planned: Any, learned: str | None) -> AgentRunState:
        with self.unit_of_work(context) as repository:
            claimed = self._claimed_planning(repository, context, request, step)
            if isinstance(claimed, AgentRunState):
                return claimed
            task, run = claimed
            runtime = self.runtime_factory(context, repository)
            runtime.start_planning(task, run)
            approval = runtime.apply_plan(task, run, context, planned, learned)
            repository.save_task(task)
            repository.save_run(run)
            return state_of(run, approval if run.checkpoint.phase == RunPhase.WAITING_APPROVAL else None)

    def _fail_planning(self, context: TenantContext, request: AgentRunInput, step: _Planning, reason: str) -> AgentRunState:
        with self.unit_of_work(context) as repository:
            claimed = self._claimed_planning(repository, context, request, step)
            if isinstance(claimed, AgentRunState):
                return claimed
            task, run = claimed
            runtime = self.runtime_factory(context, repository)
            runtime.start_planning(task, run)
            runtime._fail(task, run, context, reason)
            repository.save_task(task)
            repository.save_run(run)
            return state_of(run)

    async def _execute(self, context: TenantContext, request: AgentRunInput, step: _Executing) -> AgentRunState:
        tools = self.runtime_factory(context, _NoUnitOpen()).tools  # type: ignore[arg-type]
        # No transaction is open here. A tool that raises leaves the committed ``executing``
        # checkpoint, which the next attempt recovers (recover_interrupted_execution).
        with running_in(step.task.id, step.run.id):
            result = await tools.execute(step.call, context)
        return await asyncio.to_thread(self._settle, context, request, step, result)

    def _settle(self, context: TenantContext, request: AgentRunInput, step: _Executing, result: ToolResult) -> AgentRunState:
        with self.unit_of_work(context) as repository:
            runtime = self.runtime_factory(context, repository)
            if result.audit_metadata:
                # Recorded even when the run moved meanwhile: the call did happen.
                runtime._audit(
                    context, "tool.call", step.call.name, result.status, step.task.id,
                    {**result.audit_metadata, "tool": step.call.name, "task_id": step.task.id, "run_id": step.run.id},
                )
            task, run = self._load(repository, context, request)
            if run.checkpoint.phase != RunPhase.EXECUTING or run.checkpoint.version != step.run.checkpoint.version:
                return state_of(run)  # cancelled or settled in between; the audit record is kept
            runtime._settle(task, run, context, result, step.approval_id)
            repository.save_task(task)
            repository.save_run(run)
            return state_of(run)


class _NoUnitOpen:
    """The runtime's repository while no unit of work is open: any use is a bug."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"repository.{name} used while no unit of work is open")


@dataclass(frozen=True)
class _Planning:
    task: Task
    run: AgentRun
    version: int


@dataclass(frozen=True)
class _Executing:
    task: Task
    run: AgentRun
    call: ToolCall
    approval_id: str | None


class RunDispatcher:
    """API-side handle on Temporal: start run workflows and deliver signals."""

    def __init__(
        self,
        *,
        target: str,
        namespace: str,
        task_queue: str,
        client: Any | None = None,
    ) -> None:
        self.target = target
        self.namespace = namespace
        self.task_queue = task_queue
        self._client = client

    async def client(self) -> Any:
        if self._client is None:
            from temporalio.client import Client

            self._client = await Client.connect(
                self.target, namespace=self.namespace, interceptors=temporal_interceptors()
            )
        return self._client

    async def start(self, request: AgentRunInput, *, replace_existing: bool = False) -> str:
        """Start the run's workflow; an already running workflow for the task is reused.

        ``replace_existing`` (a retry, docs/tasks.md#retry) terminates a workflow still
        open for the task instead: it can only be finishing the failed or cancelled run,
        and reusing it would leave the new run queued forever.
        """
        from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

        workflow_id = workflow_id_for(request.tenant_id, request.workspace_id, request.task_id)
        client = await self.client()
        await client.start_workflow(
            WORKFLOW_NAME,
            request,
            id=workflow_id,
            task_queue=self.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            id_conflict_policy=(
                WorkflowIDConflictPolicy.TERMINATE_EXISTING
                if replace_existing
                else WorkflowIDConflictPolicy.USE_EXISTING
            ),
            memo={"tenant_id": request.tenant_id, "workspace_id": request.workspace_id},
        )
        return workflow_id

    async def _signal(self, context: TenantContext, task_id: str, signal: str, *args: Any) -> bool:
        workflow_id = workflow_id_for(context.tenant_id, context.workspace_id, task_id)
        try:
            client = await self.client()
            await client.get_workflow_handle(workflow_id).signal(signal, *args)
            return True
        except Exception:
            # The decision is already committed; the workflow re-reads it on its next poll.
            safe_id = workflow_id.replace("\r", "").replace("\n", "")  # task ids come from the URL
            logger.warning("Could not signal %s to workflow %s", signal, safe_id, exc_info=True)
            return False

    async def approval_decided(self, context: TenantContext, task_id: str, approval_id: str) -> bool:
        return await self._signal(context, task_id, APPROVAL_SIGNAL, approval_id)

    async def cancelled(self, context: TenantContext, task_id: str) -> bool:
        return await self._signal(context, task_id, CANCEL_SIGNAL)


def build_run_dispatcher(config: Any) -> RunDispatcher | None:
    if config.runtime_backend == "inline":
        return None
    if config.runtime_backend != "temporal":
        raise RuntimeError(f"Unsupported runtime backend: {config.runtime_backend}")
    return RunDispatcher(
        target=config.temporal_target,
        namespace=config.temporal_namespace,
        task_queue=config.temporal_task_queue,
    )


__all__ = [
    "AgentRunActivities",
    "AgentRunInput",
    "AgentRunState",
    "AgentRunWorkflow",
    "RunDispatcher",
    "build_run_dispatcher",
    "context_for",
    "run_input_for",
    "state_of",
    "tenant_unit_of_work",
    "workflow_id_for",
]
