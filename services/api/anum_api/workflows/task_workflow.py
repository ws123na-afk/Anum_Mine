"""The durable task-run workflow (see docs/automation.md's "Temporal Role").

One TaskWorkflow execution corresponds to one ANUM task run, using the
task's own id as the Temporal workflow id (Temporal enforces workflow-id
uniqueness per namespace by default, which gives "run" idempotency for
free: starting a second workflow with the same id fails instead of
double-running the task).

Runtime.run_task's optional approval pause becomes a durable
`workflow.wait_condition` on this workflow's `decide_approval`/`cancel`
signals - both survive a worker restart, unlike the plain in-process
`await` the non-Temporal code path uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from .activities import (
        CancelTaskInput,
        ResumeAfterApprovalInput,
        RunAgentInput,
        WorkflowTenantContext,
        cancel_task_activity,
        resume_after_approval_activity,
        run_agent_activity,
    )


_ACTIVITY_RETRY_POLICY = RetryPolicy(maximum_attempts=3)
_ACTIVITY_TIMEOUT = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class TaskWorkflowInput:
    task_id: str
    context: WorkflowTenantContext


@dataclass(frozen=True, slots=True)
class TaskWorkflowResult:
    status: str  # "completed" | "failed" | "cancelled"
    approval_id: str | None = None


@workflow.defn
class TaskWorkflow:
    def __init__(self) -> None:
        self._approval_decision: str | None = None
        self._cancel_requested = False

    @workflow.run
    async def run(self, input: TaskWorkflowInput) -> TaskWorkflowResult:
        run_result = await workflow.execute_activity(
            run_agent_activity,
            RunAgentInput(task_id=input.task_id, context=input.context),
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_ACTIVITY_RETRY_POLICY,
        )

        if run_result.status != "waiting_approval":
            return TaskWorkflowResult(status=run_result.status)

        # Durable wait: survives a worker restart, unlike an in-process
        # await. Resolves on either signal - approval decision or cancel.
        await workflow.wait_condition(
            lambda: self._approval_decision is not None or self._cancel_requested
        )

        if self._cancel_requested:
            await workflow.execute_activity(
                cancel_task_activity,
                CancelTaskInput(task_id=input.task_id, context=input.context),
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_ACTIVITY_RETRY_POLICY,
            )
            return TaskWorkflowResult(status="cancelled", approval_id=run_result.approval_id)

        assert run_result.approval_id is not None
        resume_result = await workflow.execute_activity(
            resume_after_approval_activity,
            ResumeAfterApprovalInput(
                task_id=input.task_id,
                approval_id=run_result.approval_id,
                decision=self._approval_decision,
                context=input.context,
            ),
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_ACTIVITY_RETRY_POLICY,
        )
        return TaskWorkflowResult(status=resume_result.status, approval_id=run_result.approval_id)

    @workflow.signal
    async def decide_approval(self, decision: str) -> None:
        if decision not in {"approved", "rejected"}:
            raise ValueError(f"Invalid approval decision: {decision!r}")
        if self._approval_decision is None and not self._cancel_requested:
            self._approval_decision = decision

    @workflow.signal
    async def cancel(self) -> None:
        if self._approval_decision is None:
            self._cancel_requested = True

    @workflow.query
    def current_state(self) -> str:
        if self._cancel_requested:
            return "cancel_requested"
        if self._approval_decision is not None:
            return f"approval_{self._approval_decision}"
        return "running"
