"""The command sweep (docs/command.md#the-sweep).

The server continues a command when a step's run completes (``command_continuation``), but only
for completions it hears about: a run the durable worker finished reaches the API only through
NATS (``ANUM_EVENT_BUS=nats``), and the in-process queue is lost on a restart. The Oracle VM runs
no NATS, so without this sweep a waiting step could stay unstarted until someone pressed "Start the
remaining steps".

Every ``ANUM_COMMAND_SWEEP_SECONDS`` (default 120; on with ``ANUM_AUTOMATION_SCHEDULER_ENABLED``)
one pass:

1. **Discovers**, with PostgreSQL as ``anum_maintenance`` (``maintenance.discover``, never
   BYPASSRLS; migration ``0182_command_sweep``), steps of commands created in the last
   ``LOOKBACK_DAYS`` days that wait for an earlier step, were never started, are not archived and
   were never tried by the server (``command_step_continuations``). The role reads ids, status and
   times only, never a step's text.
2. **Acts** per command in its creator's own tenant, workspace and user scope, as the application
   role: the creator must still be an active owner or member; the steps that can start now (every
   step they wait for completed) are started exactly as ``POST /tasks/{id}/run`` starts them, with
   the same permission, budget, emergency stop and approvals. Starting claims the task row in a
   short transaction, so a sweep racing the in-process continuer (or another replica) starts a
   step once and the other gets ``409``.
3. **Records** each step it tried, whatever the outcome, so a refused step is not tried again; the
   person continues it with "Start the remaining steps".

A step whose earlier step is still running is left for a later pass; one whose earlier step failed
or was cancelled never becomes ready and drops out of the window after ``LOOKBACK_DAYS`` days.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from .command_continuation import SYSTEM_ACTOR, command_continuer, memory_attempts
from .schemas import TaskStatus, TenantContext, utc_now
from .settings import settings

logger = logging.getLogger("anum.commands")

LOOKBACK_DAYS = 7
BATCH = 200

# (tenant, workspace, creator, command id) -> the waiting task ids found for it
CommandKey = tuple[str, str, str, str]


def _discover_sql(since: datetime, batch: int) -> list[Any]:
    from .maintenance import default_session_factory, discover

    return discover(
        default_session_factory,
        text(
            """
            select c.tenant_id, c.workspace_id, c.created_by, c.id as command_id, t.id as task_id
            from commands c
            join tasks t on t.tenant_id = c.tenant_id and t.workspace_id = c.workspace_id
                        and c.waiting_task_ids @> jsonb_build_array(t.id)
            where c.created_at >= :since
              and t.status = 'created' and t.archived_at is null
              and not exists (
                select 1 from command_step_continuations s
                where s.tenant_id = t.tenant_id and s.workspace_id = t.workspace_id and s.task_id = t.id
              )
            order by c.created_at, c.id
            limit :batch
            """
        ),
        {"since": since, "batch": batch},
    )


def _discover_memory(since: datetime, batch: int) -> list[tuple[str, str, str, str, str]]:
    from .commands import memory_command_store
    from .dependencies import memory_repository

    rows: list[tuple[str, str, str, str, str]] = []
    commands = sorted(memory_command_store.commands.values(), key=lambda c: (c.created_at, c.id))
    for command in commands:
        if command.created_at < since:
            continue
        reader = TenantContext(tenant_id=command.tenant_id, workspace_id=command.workspace_id, user_id=command.created_by, roles=[])
        for step in command.steps:
            if not step.depends_on or memory_attempts.tried(command.tenant_id, command.workspace_id, step.task_id):
                continue
            task = memory_repository.get_task(step.task_id, reader)
            if task is None or task.status != TaskStatus.CREATED or task.archived_at is not None:
                continue
            rows.append((command.tenant_id, command.workspace_id, command.created_by, command.id, step.task_id))
    return rows[:batch]


def discover_waiting(now: datetime | None = None, batch: int = BATCH) -> OrderedDict[CommandKey, list[str]]:
    """Waiting, never-started, never-tried steps, grouped by command. Blocking."""
    since = (now or utc_now()) - timedelta(days=LOOKBACK_DAYS)
    if settings.repository_backend == "postgresql":
        rows = [(r.tenant_id, r.workspace_id, r.created_by, r.command_id, r.task_id) for r in _discover_sql(since, batch)]
    else:
        rows = _discover_memory(since, batch)
    grouped: OrderedDict[CommandKey, list[str]] = OrderedDict()
    for tenant_id, workspace_id, created_by, command_id, task_id in rows:
        grouped.setdefault((tenant_id, workspace_id, created_by, command_id), []).append(task_id)
    return grouped


def ready_for(key: CommandKey, waiting: list[str]) -> tuple[TenantContext, list[str]] | None:
    """(the creator with their current role, the found steps that can start now), or ``None`` when the
    creator may no longer run tasks here. Blocking: its own short units of work, as the creator."""
    from .automation import StepFailed
    from .automation_dispatch import actor_with_role
    from .commands import ready_steps
    from .dependencies import _unit_of_work

    tenant_id, workspace_id, created_by, command_id = key
    if not created_by or created_by == SYSTEM_ACTOR:
        return None
    creator = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=created_by, roles=[])
    try:
        creator = actor_with_role(creator)
    except StepFailed:
        logger.info("command_sweep_skipped reason=creator_inactive")
        return None
    with _unit_of_work(creator, require_membership=True) as repository:
        try:
            ready = ready_steps(repository, creator, command_id)
        except Exception as exc:  # the command was deleted since discovery (404)
            if getattr(exc, "status_code", None) == 404:
                return None
            raise
    wanted = set(waiting)
    return creator, [task_id for task_id in ready if task_id in wanted]


async def run_sweep_pass(now: datetime | None = None, batch: int = BATCH) -> dict[str, int]:
    """One sweep over every tenant; returns counts for logs and tests."""
    counts = {"commands": 0, "started": 0, "waiting": 0}
    if command_continuer.runner is None:
        return counts
    groups = await asyncio.to_thread(discover_waiting, now, batch)
    for key, waiting in groups.items():
        try:
            found = await asyncio.to_thread(ready_for, key, waiting)
        except Exception:
            logger.exception("command_sweep_read_failed")
            continue
        if found is None:
            continue
        creator, ready = found
        counts["waiting"] += len(waiting) - len(ready)
        if not ready:
            continue
        counts["commands"] += 1
        try:
            started = await command_continuer.start_steps(creator, key[3], ready, source="sweep")
        except Exception:
            logger.exception("command_sweep_failed")
            continue
        counts["started"] += len(started)
    if counts["started"]:
        logger.info("command_sweep_pass", extra={"anum_command_sweep": counts})
    return counts


class CommandSweeper:
    """Background loop for :func:`run_sweep_pass` on the event loop (the steps it starts are
    coroutines; discovery and reads run in worker threads). ``stop`` lets a pass finish."""

    def __init__(self, poll_interval: float) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self.last_pass_at: datetime | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-command-sweep")

    async def stop(self, *, timeout: float = 30.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except Exception:
                logger.exception("Command sweep stopped with an error")

    async def _run(self) -> None:
        from .system_health import heartbeat

        stop = self._stop
        if stop is None:
            raise RuntimeError("Command sweep started without a stop event")
        while not stop.is_set():
            try:
                await run_sweep_pass()
                self.last_pass_at = utc_now()
                heartbeat("command_sweep")
            except Exception:
                logger.exception("Command sweep pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_command_sweeper(config: Any) -> CommandSweeper | None:
    if not config.automation_scheduler_enabled:
        return None
    return CommandSweeper(config.command_sweep_seconds)


__all__ = ["BATCH", "LOOKBACK_DAYS", "CommandSweeper", "build_command_sweeper", "discover_waiting", "ready_for", "run_sweep_pass"]
