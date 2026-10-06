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
from typing import Any

from opentelemetry import trace
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .repository import AnumRepository
from .runtime import AgentRuntime
from .schemas import AgentRun, ApprovalStatus, RunPhase, TaskStatus, TenantContext, utc_now
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


def state_of(run: AgentRun) -> AgentRunState:
    return AgentRunState(
        run_id=run.id,
        phase=run.checkpoint.phase.value,
        status=run.status.value,
        approval_id=run.checkpoint.approval_id,
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
        set_tenant_context(session, context.tenant_id, context.workspace_id)
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
            approval_id: str | None = None
            call = None
            if phase == RunPhase.PLANNING:
                await runtime.plan_run(task, run, context)
            elif phase == RunPhase.TOOL_READY:
                call = runtime.begin_execution(task, run, context)
            elif phase == RunPhase.WAITING_APPROVAL:
                approval = (
                    repository.get_approval_for_update(run.checkpoint.approval_id, context)
                    if run.checkpoint.approval_id
                    else None
                )
                if approval is None or approval.status == ApprovalStatus.PENDING:
                    return state_of(run)
                approval_id = approval.id
                call = runtime.begin_execution(task, run, context, approval=approval)
            elif phase == RunPhase.EXECUTING:
                await runtime.recover_interrupted_execution(task, run, context)
            repository.save_task(task)
            repository.save_run(run)
            if call is None:
                return state_of(run)

        # Second transaction: the `executing` checkpoint above is committed first.
        with self.unit_of_work(context) as repository:
            task, run = self._load(repository, context, request)
            if run.checkpoint.phase != RunPhase.EXECUTING:
                return state_of(run)  # cancelled or settled in between
            runtime = self.runtime_factory(context, repository)
            await runtime.finish_execution(task, run, context, call, approval_id)
            repository.save_task(task)
            repository.save_run(run)
            return state_of(run)


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

    async def start(self, request: AgentRunInput) -> str:
        """Start the run's workflow; an already running workflow for the task is reused."""
        from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

        workflow_id = workflow_id_for(request.tenant_id, request.workspace_id, request.task_id)
        client = await self.client()
        await client.start_workflow(
            WORKFLOW_NAME,
            request,
            id=workflow_id,
            task_queue=self.task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
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
