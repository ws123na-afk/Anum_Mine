"""Automation work that happens after a transaction commits (docs/automation.md#execution).

``AutomationDispatcher`` runs in every API process, off the request path, in one
background task:

- **Deferred steps.** ``task.create`` runs its agent task through the same runtime, budget,
  ToolPolicy and approvals as ``POST /api/v1/tasks/{id}/run`` (or queues it for the
  durable worker), as the run's actor with their current workspace role. ``email.send``
  stores the email as a draft of the chosen mailbox and asks to send it: sending is high
  risk, so a person always approves the exact text first. Each outcome is reported back to
  ``AutomationEngine.advance``.
- **Committed events.** An event an event trigger listens to starts the matching
  automations at once (``AutomationEngine.fire_event``); an event about an agent task wakes
  the runs whose current step waits on that task.

Both are idempotent and also covered by the scheduler pass (``run_due_wakeups`` and
``catch_up_triggers``), so work this process could not finish (it stopped, or another
process committed the event) is picked up there.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass

from .automation import (
    SCHEDULER_ACTOR,
    DeferredStep,
    WorkflowRun,
    dispatch_step_actions,
    StepFailed,
    StepReport,
    current_engine,
    scheduler_context,
)
from .automation_triggers import TASK_PROGRESS_EVENTS, TRIGGER_EVENTS, event_task_id
from .schemas import DomainEvent, TenantContext, utc_now
from .settings import settings

logger = logging.getLogger(__name__)

MAX_PENDING = 10_000

TaskRunner = Callable[[TenantContext, str], Awaitable[None]]


@dataclass(frozen=True)
class _EventJob:
    event: DomainEvent


def actor_with_role(context: TenantContext) -> TenantContext:
    """The run's actor with their current workspace role (``StepFailed`` if they have none)."""
    from .durable_runs import tenant_unit_of_work

    if context.user_id == SCHEDULER_ACTOR:
        raise StepFailed(
            "This run has no owner to act as. A workspace owner can take over its schedule or trigger, then retry the run."
        )
    with tenant_unit_of_work(context) as repository:
        membership = repository.get_membership(context)
    if membership is not None and membership.active and membership.role.lower() in {"owner", "member"}:
        return context.model_copy(update={"roles": [membership.role.lower()]})
    if settings.auth_mode == "oidc" or membership is not None:
        raise StepFailed(
            f"The run's owner ({context.user_id}) is no longer an active owner or member of this workspace. "
            "A workspace owner can take over the schedule or trigger, then retry the run."
        )
    # Local header sign-in without a stored membership: the least role that may run tasks.
    return context.model_copy(update={"roles": ["member"]})


async def request_email(context: TenantContext, job: DeferredStep) -> StepReport:
    """Store the email as a draft and ask to send it (always approved by a person first)."""
    from fastapi import HTTPException

    from .agent_tools import ToolCall
    from .durable_runs import tenant_unit_of_work
    from .email_api import _audit, _pause_for_approval
    from .email_inbox import (
        SEND_TOOL,
        AccountStatus,
        DraftStatus,
        EmailDraft,
        EmailError,
        can_see,
        open_email_store,
        send_arguments,
        valid_addresses,
    )

    args = job.arguments
    seed = f"{job.run_id}:{job.step_id}:{job.attempt}".encode()
    draft_id = "draft_auto_" + hashlib.sha256(seed).hexdigest()[:24]
    with open_email_store(context) as store:
        account = store.get_account(str(args.get("account_id") or ""))
        if account is None or not can_see(account, context):
            raise StepFailed("The mailbox to send from is not connected or not visible to the run's owner. Choose another in the step.")
        if account.status == AccountStatus.REAUTH_REQUIRED:
            raise StepFailed(f"Reconnect the mailbox {account.address} under Email, then retry.", retryable=True)
        existing = store.get_draft(draft_id)
        if existing is not None and existing.task_id and existing.approval_id:
            # Asked already (the report was lost): pick the same approval up again.
            return StepReport(
                step_id=job.step_id,
                attempt=job.attempt,
                output={"draft_id": existing.id, "approval_id": existing.approval_id},
                task_id=existing.task_id,
            )
        try:
            to = valid_addresses([str(item) for item in args.get("to") or []])
        except EmailError as exc:
            raise StepFailed(f"The email was not sent: {exc.message}") from None
        now = utc_now()
        draft = existing or EmailDraft(
            id=draft_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            account_id=account.id,
            to=to,
            subject=str(args.get("subject") or ""),
            body=str(args.get("body") or ""),
            source="person",
            created_by=context.user_id,
            created_at=now,
            updated_at=now,
        )
        if existing is None:
            store.add_draft(draft)
    recipients = ", ".join(draft.to)
    try:
        with tenant_unit_of_work(context) as repository:
            task, approval = _pause_for_approval(
                repository,
                context,
                ToolCall(name=SEND_TOOL, arguments=send_arguments(draft, account, None)),
                f"Send email to {recipients}"[:160],
                f"An automation asks to send '{draft.subject}' from {account.address} to {recipients}.",
            )
            _audit(
                repository,
                context,
                "email.send.requested",
                draft.id,
                metadata={
                    "approval_id": approval.id,
                    "task_id": task.id,
                    "recipients": len(draft.to),
                    "payload_hash": approval.payload_hash,
                    "automation_run_id": job.run_id,
                },
            )
    except HTTPException as exc:
        raise StepFailed(f"The email was not sent: {exc.detail}") from None
    with open_email_store(context) as store:
        stored = store.get_draft(draft.id) or draft
        stored.status = DraftStatus.PENDING_APPROVAL
        stored.approval_id, stored.task_id = approval.id, task.id
        stored.updated_at = utc_now()
        store.save_draft(stored)
    return StepReport(
        step_id=job.step_id,
        attempt=job.attempt,
        output={"draft_id": draft.id, "approval_id": approval.id},
        task_id=task.id,
    )


class AutomationDispatcher:
    """Thread-safe queue of deferred steps and committed events, drained in the background.

    ``run_pending`` processes everything queued (tests call it directly). Work queued when
    the process stops is not lost: the scheduler pass finds it again.
    """

    def __init__(self) -> None:
        self._queue: deque[DeferredStep | _EventJob] = deque()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.task_runner: TaskRunner | None = None
        self.email_requester: Callable[[TenantContext, DeferredStep], Awaitable[StepReport]] = request_email
        self.dropped = 0

    def configure(self, *, task_runner: TaskRunner) -> None:
        self.task_runner = task_runner

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()

    def _put(self, items: Iterable[DeferredStep | _EventJob]) -> None:
        with self._lock:
            self._queue.extend(items)
            while len(self._queue) > MAX_PENDING:
                self._queue.popleft()
                self.dropped += 1
                logger.warning("automation_dispatch_queue_full; dropped the oldest item (the scheduler pass recovers it)")
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # pragma: no cover - loop shutting down
                pass

    def submit_deferred(self, jobs: Sequence[DeferredStep]) -> None:
        self._put(jobs)

    def submit_events(self, events: Sequence[DomainEvent]) -> None:
        """Event-bus listener: only queues; never raises."""
        relevant = [
            _EventJob(event)
            for event in events
            if event.workspace_id and (event.type in TRIGGER_EVENTS or event.type in TASK_PROGRESS_EVENTS)
        ]
        if relevant:
            self._put(relevant)

    def _take(self) -> DeferredStep | _EventJob | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    async def run_pending(self) -> int:
        done = 0
        while (item := self._take()) is not None:
            try:
                if isinstance(item, _EventJob):
                    await self._handle_event(item.event)
                else:
                    await self._handle_step(item)
            except Exception:  # one item must never stop the others
                logger.exception("automation_dispatch_failed")
            done += 1
        return done

    async def _handle_event(self, event: DomainEvent) -> None:
        context = scheduler_context(event.tenant_id, event.workspace_id or "", None)
        engine = current_engine()
        since = utc_now()
        runs: list[WorkflowRun] = []
        if event.type in TRIGGER_EVENTS:
            runs.extend(await asyncio.to_thread(engine.fire_event, context, event))
        task_id = event_task_id(event)
        if task_id and event.type in TASK_PROGRESS_EVENTS:
            runs.extend(await asyncio.to_thread(engine.wake_for_task, context, task_id))
        # Feature step actions (tyre reports) of steps this event started, after commit.
        if runs:
            await asyncio.to_thread(dispatch_step_actions, runs, None, since=since)

    async def _handle_step(self, job: DeferredStep) -> None:
        context = scheduler_context(job.tenant_id, job.workspace_id, job.actor)
        since = utc_now()
        report: StepReport | None = None
        try:
            actor = await asyncio.to_thread(actor_with_role, context)
            if job.action == "task.create" and job.task_id:
                if self.task_runner is None:
                    raise StepFailed("Agent tasks cannot run in this process.", retryable=True)
                await self.task_runner(actor, job.task_id)
            elif job.action == "email.send":
                report = await self.email_requester(actor, job)
        except StepFailed as exc:
            report = StepReport(step_id=job.step_id, attempt=job.attempt, error=str(exc), retryable=exc.retryable)
        except Exception as exc:
            logger.exception("automation_step_failed action=%s", job.action)
            report = StepReport(
                step_id=job.step_id,
                attempt=job.attempt,
                error=f"The step could not run ({type(exc).__name__}); it is tried again.",
                retryable=True,
            )
        run = await asyncio.to_thread(current_engine().advance, context, job.run_id, report)
        await asyncio.to_thread(dispatch_step_actions, [run], None, since=since)

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="anum-automation-dispatcher")
        if self.pending:
            self._wake.set()

    async def stop(self, *, timeout: float = 10.0) -> None:
        task, self._task = self._task, None
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._loop = None
        self._wake = None

    async def _run(self) -> None:
        wake = self._wake
        if wake is None:
            raise RuntimeError("Automation dispatcher started without a wake event")
        while not self._stopping:
            await wake.wait()
            wake.clear()
            if self._stopping:
                break
            await self.run_pending()


automation_dispatcher = AutomationDispatcher()


__all__ = ["AutomationDispatcher", "actor_with_role", "automation_dispatcher", "request_email"]
