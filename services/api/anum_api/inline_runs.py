"""Inline task runs that never hold a database transaction while the model or a tool works.

``ANUM_RUNTIME_BACKEND=inline`` (the Oracle VM) runs a task inside the API process.
It used to run in one request transaction that took the task's row lock
(``SELECT ... FOR UPDATE``) and kept it while the model answered. Anything else that
locked the task on the event loop meanwhile (Cancel, Archive, a rename) then waited
for that lock on the loop, the run could never resume to release it, and the whole API
stopped answering: one "small task" froze every tab (docs/realtime.md#event-loop).

A run now goes in short units of work, each in a worker thread:

1. **Claim**: lock the task, check it may run (the caller's rules), set it ``running``
   and commit. The claim is the task's status plus the id of its latest run at that
   moment; the API process also holds the task's run lock (``_task_lock``).
2. **Plan**: retrieval and the model call, with no transaction open.
3. **Apply**: lock the task again and check the claim still holds (still ``running``,
   no other run started, not cancelled or archived). Then record the plan, apply
   tool policy (approvals and their payload hash exactly as before) and, when policy
   allows the call, commit the run as ``executing``.
4. **Execute**: the tool runs with no transaction open.
5. **Settle**: lock again, check the run is still the ``executing`` checkpoint that was
   committed, then complete or fail it.

A cancel during the run is therefore answered at once; the run notices it at the next
step and stops with :class:`RunClaimLost` (409) without overwriting it. If the run
fails before step 3 commits, the claim is released (the task gets its earlier status
back). A claim left behind by a process that died mid-run expires after
``ANUM_INLINE_RUN_LEASE_SECONDS`` (:func:`claim_expired`); Cancel also clears it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .agent_policy import AgentRunProfile
from .agent_tools import ToolCall, ToolResult
from .repository import AnumRepository
from .runtime import AgentRuntime
from .schemas import AgentRun, Approval, RunPhase, Task, TaskStatus, TenantContext, utc_now

logger = logging.getLogger(__name__)

UnitOfWork = Callable[[TenantContext], AbstractContextManager[AnumRepository]]
RuntimeFactory = Callable[[AnumRepository], AgentRuntime]
ClaimCheck = Callable[[AnumRepository, Task | None], AgentRunProfile | None]
Present = Callable[[Approval | None, AnumRepository], Approval | None]

TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class RunClaimLost(Exception):
    """The task changed while the run worked (cancelled, archived or run elsewhere)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _NoRepository:
    """The runtime's repository while no transaction is open: any use is a bug."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"repository.{name} used while no unit of work is open")


@dataclass
class _Claim:
    task: Task
    agent: AgentRunProfile | None
    previous_status: TaskStatus
    previous_run_id: str | None


@dataclass
class InlineRunResult:
    task: Task
    run: AgentRun
    approval: Approval | None


def claim_expired(task: Task, latest_run: AgentRun | None, lease_seconds: float, now: datetime | None = None) -> bool:
    """A ``running`` task whose claim was never applied (no run, or only finished runs)
    and has not moved for ``lease_seconds``: its process stopped mid-run."""
    if task.status != TaskStatus.RUNNING:
        return False
    if latest_run is not None and latest_run.status not in TERMINAL:
        return False
    return (now or utc_now()) - task.updated_at >= timedelta(seconds=lease_seconds)


async def run_inline(
    context: TenantContext,
    task_id: str,
    *,
    unit_of_work: UnitOfWork,
    runtime_factory: RuntimeFactory,
    claim: ClaimCheck,
    on_start: Callable[[AnumRepository, Task, str | None], None] | None = None,
    present: Present | None = None,
    timeout_seconds: float | None = None,
) -> InlineRunResult:
    """Run ``task_id`` inline in short units of work (see the module docstring).

    ``claim`` checks the locked task (raise to refuse; return the agent snapshot or
    None). ``on_start`` runs in the transaction that records the plan (a retry's event).
    ``present`` turns the approval into its response shape inside the last transaction.
    """
    claimed = await asyncio.to_thread(_claim, context, task_id, unit_of_work, claim)
    planner = runtime_factory(_NoRepository())  # type: ignore[arg-type]
    task = claimed.task
    run = planner.new_run(task, agent=claimed.agent)
    applied = False
    try:
        async with asyncio.timeout(timeout_seconds):
            planner.start_planning(task, run)
            planned, learned = await planner.plan_with_model(task, run, context, offload=True)
            task, run, approval, call = await asyncio.to_thread(
                _apply, context, claimed, run, planned, learned, unit_of_work, runtime_factory, on_start, present
            )
            applied = True
            if call is None:
                return InlineRunResult(task, run, approval)
            try:
                result = await planner.tools.execute(call, context)
            except Exception as exc:
                await asyncio.to_thread(
                    _settle_failure, context, task.id, run, unit_of_work, runtime_factory,
                    f"The action stopped with an error ({type(exc).__name__}); the task did not finish.",
                )
                raise
            task, run = await asyncio.to_thread(
                _settle, context, task.id, run, call, result, unit_of_work, runtime_factory
            )
            return InlineRunResult(task, run, None)
    except (asyncio.CancelledError, GeneratorExit):
        _in_background(
            _release if not applied else _settle_failure_quietly,
            context, claimed, run, unit_of_work, runtime_factory,
        )
        raise
    except BaseException:
        if not applied:
            await asyncio.to_thread(_release, context, claimed, run, unit_of_work, runtime_factory)
        raise


def _in_background(function: Callable[..., None], *args: Any) -> None:
    """Run a cleanup step in a worker thread without waiting (a cancelled request)."""
    try:
        asyncio.get_running_loop().run_in_executor(None, lambda: function(*args))
    except RuntimeError:
        function(*args)


def _claim(context: TenantContext, task_id: str, unit_of_work: UnitOfWork, check: ClaimCheck) -> _Claim:
    with unit_of_work(context) as repository:
        task = repository.get_task_for_update(task_id, context)
        agent = check(repository, task)
        if task is None:  # ``check`` refuses a missing task; this is only a guard
            raise RunClaimLost("The task was not found.")
        latest = repository.find_run_for_task(task.id, context)
        previous_status = task.status
        task.status = TaskStatus.RUNNING
        task.updated_at = utc_now()
        repository.save_task(task)
        return _Claim(task.model_copy(deep=True), agent, previous_status, latest.id if latest else None)


def _locked_claimed_task(repository: AnumRepository, context: TenantContext, claimed: _Claim) -> Task:
    """The task, locked, if the claim still holds; :class:`RunClaimLost` otherwise."""
    task = repository.get_task_for_update(claimed.task.id, context)
    if task is None:
        raise RunClaimLost("The task was deleted while it was running.")
    if task.status == TaskStatus.CANCELLED:
        raise RunClaimLost("The task was cancelled while it was running.")
    if task.archived_at is not None:
        raise RunClaimLost("The task was archived while it was running.")
    latest = repository.find_run_for_task(task.id, context)
    if task.status != TaskStatus.RUNNING or (latest.id if latest else None) != claimed.previous_run_id:
        raise RunClaimLost("The task was started again elsewhere while this run was planning.")
    return task


def _apply(
    context: TenantContext,
    claimed: _Claim,
    run: AgentRun,
    planned: Any,
    learned: str | None,
    unit_of_work: UnitOfWork,
    runtime_factory: RuntimeFactory,
    on_start: Callable[[AnumRepository, Task, str | None], None] | None,
    present: Present | None,
) -> tuple[Task, AgentRun, Approval | None, ToolCall | None]:
    with unit_of_work(context) as repository:
        task = _locked_claimed_task(repository, context, claimed)
        runtime = runtime_factory(repository)
        if on_start is not None:
            on_start(repository, task, claimed.previous_run_id)
        approval = runtime.apply_plan(task, run, context, planned, learned)
        call: ToolCall | None = None
        if run.checkpoint.phase == RunPhase.TOOL_READY and run.checkpoint.tool_call:
            call = ToolCall.model_validate(run.checkpoint.tool_call)
            # Committed before the tool runs, like the durable worker: a crash mid-tool
            # is visible as ``executing`` instead of silently rolled back.
            runtime._mark_executing(task, run)
        # The final state of this step (a pause for approval or a policy failure only
        # changes the objects; the caller saves them, as the request route always did).
        repository.save_task(task)
        repository.save_run(run)
        shown =present(approval, repository) if present is not None else approval
        return task, run, shown, call


def _locked_executing_run(
    repository: AnumRepository, context: TenantContext, task_id: str, run: AgentRun
) -> tuple[Task, AgentRun]:
    task = repository.get_task_for_update(task_id, context)
    stored = repository.get_run(run.id, context)
    if task is None or stored is None:
        raise RunClaimLost("The task was deleted while its action ran.")
    if stored.checkpoint.phase != RunPhase.EXECUTING or stored.checkpoint.version != run.checkpoint.version:
        raise RunClaimLost(
            "The task was cancelled while its action ran; the action may already have taken effect."
        )
    return task, run


def _settle(
    context: TenantContext,
    task_id: str,
    run: AgentRun,
    call: ToolCall,
    result: ToolResult,
    unit_of_work: UnitOfWork,
    runtime_factory: RuntimeFactory,
) -> tuple[Task, AgentRun]:
    with unit_of_work(context) as repository:
        runtime = runtime_factory(repository)
        if result.audit_metadata:
            # Recorded even when the run was cancelled meanwhile: the call did happen.
            runtime._audit(
                context,
                "tool.call",
                call.name,
                result.status,
                task_id,
                {**result.audit_metadata, "tool": call.name, "task_id": task_id, "run_id": run.id},
            )
        try:
            task, run = _locked_executing_run(repository, context, task_id, run)
        except RunClaimLost:
            # Commit the audit record; the cancel stays as it is.
            lost = True
        else:
            lost = False
            runtime._settle(task, run, context, result)
            repository.save_task(task)
            repository.save_run(run)
    if lost:
        raise RunClaimLost(
            "The task was cancelled while its action ran; the action may already have taken effect."
        )
    return task, run


def _settle_failure(
    context: TenantContext,
    task_id: str,
    run: AgentRun,
    unit_of_work: UnitOfWork,
    runtime_factory: RuntimeFactory,
    reason: str,
) -> None:
    with unit_of_work(context) as repository:
        try:
            task, run = _locked_executing_run(repository, context, task_id, run)
        except RunClaimLost:
            return
        runtime_factory(repository)._fail(task, run, context, reason, run.checkpoint.approval_id)
        repository.save_task(task)
        repository.save_run(run)


def _settle_failure_quietly(
    context: TenantContext, claimed: _Claim, run: AgentRun, unit_of_work: UnitOfWork, runtime_factory: RuntimeFactory
) -> None:
    try:
        _settle_failure(
            context, claimed.task.id, run, unit_of_work, runtime_factory,
            "The run was stopped while its action ran; its outcome is unknown. Check the target and run the task again if needed.",
        )
    except Exception as exc:  # the request is gone; Tasks shows the executing run
        logger.warning("inline_run_stop_failed error_class=%s", type(exc).__name__)


def _release(
    context: TenantContext, claimed: _Claim, run: AgentRun, unit_of_work: UnitOfWork, runtime_factory: RuntimeFactory
) -> None:
    """Undo a claim whose plan was never applied: the task gets its earlier status back."""
    try:
        with unit_of_work(context) as repository:
            task = repository.get_task_for_update(claimed.task.id, context)
            if task is None or task.status != TaskStatus.RUNNING:
                return
            latest = repository.find_run_for_task(task.id, context)
            if (latest.id if latest else None) != claimed.previous_run_id:
                return
            task.status = claimed.previous_status
            task.updated_at = utc_now()
            repository.save_task(task)
    except Exception as exc:  # the claim then expires (claim_expired) or is cancelled
        logger.warning("inline_run_release_failed error_class=%s", type(exc).__name__)


__all__ = ["InlineRunResult", "RunClaimLost", "claim_expired", "run_inline"]
