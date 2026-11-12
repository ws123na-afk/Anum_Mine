"""The server continues a command on its own (docs/command.md#the-server-continues-a-command).

A command step that waits for earlier steps used to start only when a client called
``POST /commands/{id}/start`` again. Now, when a step's run completes, the server starts every
step of that command whose earlier steps have all completed, wherever the run finished: inline
in this process, after an approval, or on the durable worker.

How it works:

- **Feeds.** ``agent_run.completed`` events. With ``ANUM_EVENT_BUS=nats`` the process's
  realtime hub hands every validated event to :meth:`CommandContinuer.observe`, so a run the
  durable worker (or another replica) finished reaches every API replica. Without the bus (or
  while it is down) committed events reach :meth:`CommandContinuer.submit` in the committing
  process. A private task's event never goes on the bus, so it is always taken in process.
  Each event id is handled once per process.
- **Who acts.** The finished task is read in its tenant and workspace; the command is read as
  the task's creator, who must still be an active owner or member of the workspace (with their
  current role). A step is started exactly as ``POST /tasks/{id}/run`` starts it
  (``main.start_task_run``): task permission, run lock, monthly model budget and emergency
  stop, the agent's profile, tool policy and approvals.
- **Exactly once.** Reads run in their own short units of work in a worker thread; nothing is
  held across a run. Starting a step claims its task in a short transaction that locks the row
  and checks it is still ``created`` (or queues it for the durable worker under the same lock
  and check), so when two replicas, or two finishing steps, try to start the same waiting step,
  one starts it and the other gets ``409``, which is expected here and only counted.
- **What is left alone.** A cancelled, failed or archived step, a step whose earlier step
  failed, and a step refused by the budget or the emergency stop stay as they are; the person
  continues them with "Start the remaining steps" (``POST /commands/{id}/start``).

The queue is in process and bounded: a continuation lost to a restart between a step
completing and the next one starting is recovered by "Start the remaining steps".
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from .schemas import DomainEvent, TenantContext

logger = logging.getLogger("anum.commands")

CONTINUE_EVENTS = frozenset({"agent_run.completed"})
SYSTEM_ACTOR = "system:command-continuation"
MAX_PENDING = 2_000
SEEN_EVENTS = 4_096
MAX_CONCURRENT = 8

# Starts one step as the given person, like POST /tasks/{id}/run (configured by main.py).
StepRunner = Callable[[TenantContext, str], Awaitable[Any]]


@dataclass(frozen=True)
class Finished:
    """A completed run of an agent task (from its ``agent_run.completed`` event)."""

    tenant_id: str
    workspace_id: str
    task_id: str
    private_to: str | None = None


def finished_of(event: DomainEvent) -> Finished | None:
    if event.type not in CONTINUE_EVENTS or not event.workspace_id:
        return None
    task_id = event.payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    return Finished(event.tenant_id, event.workspace_id, task_id, event.private_to)


def _creator_context(item: Finished) -> TenantContext | None:
    """The finished task's creator with their current role, or ``None`` when the task is gone or
    its creator may no longer run tasks in the workspace."""
    from .automation import StepFailed
    from .automation_dispatch import actor_with_role
    from .dependencies import _unit_of_work

    # A private task is visible only to its person, so it is read as them.
    reader = TenantContext(
        tenant_id=item.tenant_id, workspace_id=item.workspace_id, user_id=item.private_to or SYSTEM_ACTOR, roles=[]
    )
    with _unit_of_work(reader, require_membership=False) as repository:
        task = repository.get_task(item.task_id, reader)
    if task is None or not task.created_by:
        return None
    creator = reader.model_copy(update={"user_id": task.created_by})
    try:
        return actor_with_role(creator)
    except StepFailed:
        logger.info("command_continuation_skipped reason=creator_inactive")
        return None


def steps_after(item: Finished) -> tuple[TenantContext, str, list[str]] | None:
    """(creator, command id, task ids that can start now) after ``item``'s task completed, or
    ``None`` when it is not a step of a command or nothing can continue. Blocking: call it in a
    worker thread."""
    from .commands import steps_to_continue
    from .dependencies import _unit_of_work

    creator = _creator_context(item)
    if creator is None:
        return None
    with _unit_of_work(creator, require_membership=True) as repository:
        found = steps_to_continue(repository, creator, item.task_id)
    if found is None:
        return None
    command_id, ready = found
    return creator, command_id, ready


class CommandContinuer:
    """Thread-safe queue of finished steps, drained on the event loop in the background.

    ``run_pending`` handles everything queued and waits for the steps it starts (tests call it
    directly); the background loop handles each item in its own task, at most
    ``MAX_CONCURRENT`` at a time, so a long run never holds up another command."""

    def __init__(self, *, max_concurrent: int = MAX_CONCURRENT) -> None:
        self._queue: deque[Finished] = deque()
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._inflight: set[asyncio.Task[None]] = set()
        self._limit = asyncio.Semaphore(max_concurrent)
        self._stopping = False
        self._bus_live: Callable[[], bool] = lambda: False
        self.runner: StepRunner | None = None
        # Counters for logs and tests.
        self.started = 0
        self.already_started = 0
        self.refused = 0
        self.dropped = 0

    def configure(self, *, runner: StepRunner) -> None:
        self.runner = runner

    def attach(self, runtime: Any) -> None:
        """Feed from an ``event_bus.EventBusRuntime``: its realtime hub (NATS, every replica)
        and its committed-event listeners (this process)."""
        self._bus_live = lambda: bool(runtime.hub.live)
        runtime.hub.add_observer(self.observe)
        runtime.add_listener(self.submit)

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
            self._seen.clear()

    def _live(self) -> bool:
        try:
            return bool(self._bus_live())
        except Exception:  # pragma: no cover - a broken probe falls back to in-process events
            return False

    def _offer(self, event: DomainEvent) -> None:
        item = finished_of(event)
        if item is None:
            return
        with self._lock:
            if event.id in self._seen:
                return
            self._seen[event.id] = None
            while len(self._seen) > SEEN_EVENTS:
                self._seen.popitem(last=False)
            self._queue.append(item)
            while len(self._queue) > MAX_PENDING:
                self._queue.popleft()
                self.dropped += 1
                logger.warning("command_continuation_queue_full; dropped the oldest item")
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # pragma: no cover - loop shutting down
                pass

    def submit(self, events: Sequence[DomainEvent]) -> None:
        """Committed-event listener (the committing thread): only queues, never raises. While the
        bus is live the same events arrive through :meth:`observe` on every replica, except a
        private task's, which never goes on the bus."""
        live = self._live()
        for event in events:
            if live and event.private_to is None:
                continue
            self._offer(event)

    def observe(self, event: DomainEvent) -> None:
        """From the realtime hub (NATS), on the event loop."""
        self._offer(event)

    def _take(self) -> Finished | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    async def handle(self, item: Finished) -> list[str]:
        """Start every step of ``item``'s command that can start now; returns the ones started."""
        if self.runner is None:
            return []
        found = await asyncio.to_thread(steps_after, item)
        if found is None:
            return []
        creator, command_id, ready = found
        if not ready:
            return []
        runner = self.runner
        results = await asyncio.gather(*(runner(creator, task_id) for task_id in ready), return_exceptions=True)
        started: list[str] = []
        for task_id, result in zip(ready, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
            if isinstance(result, HTTPException) and result.status_code == status.HTTP_409_CONFLICT:
                self.already_started += 1  # another replica, tab or finishing step started it first
            elif isinstance(result, Exception):
                self.refused += 1
                logger.info(
                    "command_step_not_continued",
                    extra={"anum_command_continuation": {"command_id": command_id, "task_id": task_id,
                                                          "reason": type(result).__name__}},
                )
            else:
                self.started += 1
                started.append(task_id)
        return started

    async def _guarded(self, item: Finished) -> None:
        async with self._limit:
            try:
                await self.handle(item)
            except Exception:  # one item must never stop the others
                logger.exception("command_continuation_failed")

    async def run_pending(self) -> int:
        done = 0
        while (item := self._take()) is not None:
            await self._guarded(item)
            done += 1
        return done

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._limit = asyncio.Semaphore(MAX_CONCURRENT)
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="anum-command-continuation")
        if self.pending:
            self._wake.set()

    async def stop(self, *, timeout: float = 10.0) -> None:
        task, self._task = self._task, None
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        pending = [item for item in (task, *self._inflight) if item is not None]
        if pending:
            # Database work runs in worker threads and finishes its own unit; only the waiting
            # coroutines are cancelled after the timeout.
            _, still = await asyncio.wait(pending, timeout=timeout)
            for item in still:
                item.cancel()
            if still:
                await asyncio.gather(*still, return_exceptions=True)
        self._inflight.clear()
        self._loop = None
        self._wake = None

    async def _run(self) -> None:
        wake = self._wake
        if wake is None:
            raise RuntimeError("Command continuation started without a wake event")
        while not self._stopping:
            await wake.wait()
            wake.clear()
            while not self._stopping and (item := self._take()) is not None:
                job = asyncio.create_task(self._guarded(item), name="anum-command-continue")
                self._inflight.add(job)
                job.add_done_callback(self._inflight.discard)


command_continuer = CommandContinuer()

__all__ = ["CONTINUE_EVENTS", "CommandContinuer", "Finished", "command_continuer", "finished_of", "steps_after"]
