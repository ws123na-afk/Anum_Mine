"""Temporal activities that do the actual state mutation for a task run.

Each activity opens its own repository access the same way a normal HTTP
request would (see `_repository_for` below, which mirrors
`anum_api.dependencies.db_session_context` + `repository_context`): for the
in-memory backend it reuses the same process-wide singleton the API's own
request handlers use (this worker runs in-process, in the same event loop
as the FastAPI app - see workflows/worker.py), and for the PostgreSQL
backend it opens a short-lived, tenant-scoped session per activity call.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..events import CanonicalEventName, create_event
from ..model_gateway import MockModelGateway
from ..repository import AnumRepository
from ..runtime import AgentRuntime
from ..schemas import ApprovalStatus, TaskStatus, TenantContext


@dataclass(frozen=True, slots=True)
class WorkflowTenantContext:
    """Plain-dataclass mirror of TenantContext - Temporal's default data
    converter handles dataclasses of primitives without extra setup, so
    workflow/activity inputs use this instead of the Pydantic model."""

    tenant_id: str
    workspace_id: str
    user_id: str
    roles: list[str] = field(default_factory=list)

    def to_tenant_context(self) -> TenantContext:
        return TenantContext(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            roles=list(self.roles),
        )

    @classmethod
    def from_tenant_context(cls, context: TenantContext) -> "WorkflowTenantContext":
        return cls(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            roles=list(context.roles),
        )


@dataclass(frozen=True, slots=True)
class RunAgentInput:
    task_id: str
    context: WorkflowTenantContext


@dataclass(frozen=True, slots=True)
class RunAgentOutput:
    status: str  # "completed" | "waiting_approval" | "failed"
    approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResumeAfterApprovalInput:
    task_id: str
    approval_id: str
    decision: str  # "approved" | "rejected"
    context: WorkflowTenantContext


@dataclass(frozen=True, slots=True)
class ResumeAfterApprovalOutput:
    status: str  # "completed" | "failed"


@dataclass(frozen=True, slots=True)
class CancelTaskInput:
    task_id: str
    context: WorkflowTenantContext


@contextmanager
def _repository_for(context: TenantContext) -> Iterator[AnumRepository]:
    from ..settings import settings

    if settings.repository_backend == "memory":
        from ..dependencies import memory_repository

        yield memory_repository
        return

    from ..db.repository import SqlAlchemyRepository
    from ..db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        session.info["user_id"] = context.user_id
        yield SqlAlchemyRepository(session, created_by_user_id=context.user_id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@activity.defn
async def run_agent_activity(input: RunAgentInput) -> RunAgentOutput:
    context = input.context.to_tenant_context()
    with _repository_for(context) as repository:
        task = repository.get_task_for_update(input.task_id, context)
        if task is None:
            raise ApplicationError(f"Task {input.task_id} not found", non_retryable=True)
        if task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
            raise ApplicationError(
                "Task cannot be run from current state", non_retryable=True
            )

        runtime = AgentRuntime(MockModelGateway(), repository)
        run, approval = await runtime.run_task(task, context)
        repository.save_task(task)
        repository.save_run(run)

        if approval is not None:
            return RunAgentOutput(status="waiting_approval", approval_id=approval.id)
        return RunAgentOutput(status="completed")


@activity.defn
async def resume_after_approval_activity(
    input: ResumeAfterApprovalInput,
) -> ResumeAfterApprovalOutput:
    context = input.context.to_tenant_context()
    decision = ApprovalStatus.APPROVED if input.decision == "approved" else ApprovalStatus.REJECTED

    with _repository_for(context) as repository:
        task = repository.get_task_for_update(input.task_id, context)
        if task is None:
            raise ApplicationError(f"Task {input.task_id} not found", non_retryable=True)
        approval = repository.get_approval_for_update(input.approval_id, context)
        if approval is None:
            raise ApplicationError(
                f"Approval {input.approval_id} not found", non_retryable=True
            )
        if approval.status != ApprovalStatus.PENDING:
            # Already decided (e.g. a duplicate signal) - nothing to do,
            # report the task's current terminal status instead of failing.
            return ResumeAfterApprovalOutput(
                status="completed" if task.status == TaskStatus.COMPLETED else "failed"
            )

        approval.status = decision
        approval.decided_at = datetime.now(timezone.utc)
        repository.save_approval(approval)

        run = repository.find_run_for_task(task.id, context)
        runtime = AgentRuntime(MockModelGateway(), repository)
        resumed_run = await runtime.resume_after_approval(task, run, approval, context) if run else None
        repository.save_task(task)
        if resumed_run:
            repository.save_run(resumed_run)

        return ResumeAfterApprovalOutput(
            status="completed" if task.status == TaskStatus.COMPLETED else "failed"
        )


@activity.defn
async def cancel_task_activity(input: CancelTaskInput) -> None:
    context = input.context.to_tenant_context()
    with _repository_for(context) as repository:
        task = repository.get_task_for_update(input.task_id, context)
        if task is None:
            raise ApplicationError(f"Task {input.task_id} not found", non_retryable=True)
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return

        now = datetime.now(timezone.utc)
        task.status = TaskStatus.CANCELLED
        task.updated_at = now
        repository.save_task(task)

        run = repository.find_run_for_task(task.id, context)
        if run and run.status == TaskStatus.WAITING_APPROVAL:
            run.status = TaskStatus.CANCELLED
            run.updated_at = now
            repository.save_run(run)

        for approval in repository.list_approvals_for_update(context):
            if approval.task_id == task.id and approval.status == ApprovalStatus.PENDING:
                approval.status = ApprovalStatus.EXPIRED
                approval.decided_at = now
                repository.save_approval(approval)

        repository.record_event(
            create_event(
                CanonicalEventName.TASK_CANCELLED,
                context,
                task.id,
                {"task_id": task.id},
                correlation_id=task.id,
                created_at=now,
            ).event
        )
