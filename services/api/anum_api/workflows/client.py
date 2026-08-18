"""Thin helpers the HTTP layer uses to start/signal task workflows.

Kept deliberately small: main.py's endpoints stay in charge of the HTTP
contract (same response shapes as the non-Temporal path) and just call
through here for the Temporal-specific parts (start/signal), then read
the resulting state back from the repository - see `wait_for_task_change`.
"""

from __future__ import annotations

import asyncio
import time

from temporalio.client import Client, WorkflowHandle

from ..repository import AnumRepository
from ..schemas import Task, TaskStatus, TenantContext
from ..settings import settings
from .activities import WorkflowTenantContext
from .task_workflow import TaskWorkflow, TaskWorkflowInput

_client: Client | None = None
_client_lock = asyncio.Lock()


async def get_temporal_client() -> Client | None:
    """Return a cached, connected Temporal client, or None if unconfigured."""

    global _client
    if not settings.temporal_address:
        return None
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            _client = await Client.connect(
                settings.temporal_address, namespace=settings.temporal_namespace
            )
    return _client


def workflow_id_for_task(task_id: str) -> str:
    return f"anum-task-{task_id}"


async def start_task_workflow(task_id: str, context: TenantContext) -> WorkflowHandle:
    client = await get_temporal_client()
    assert client is not None, "start_task_workflow called without settings.temporal_address set"
    return await client.start_workflow(
        TaskWorkflow.run,
        TaskWorkflowInput(
            task_id=task_id, context=WorkflowTenantContext.from_tenant_context(context)
        ),
        id=workflow_id_for_task(task_id),
        task_queue=settings.temporal_task_queue,
    )


async def signal_approval_decision(task_id: str, decision: str) -> None:
    client = await get_temporal_client()
    assert client is not None
    handle = client.get_workflow_handle(workflow_id_for_task(task_id))
    await handle.signal(TaskWorkflow.decide_approval, decision)


async def signal_cancel(task_id: str) -> None:
    client = await get_temporal_client()
    assert client is not None
    handle = client.get_workflow_handle(workflow_id_for_task(task_id))
    await handle.signal(TaskWorkflow.cancel)


async def wait_for_task_change(
    repository: AnumRepository,
    task_id: str,
    context: TenantContext,
    *,
    from_status: TaskStatus,
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.05,
) -> Task:
    """Poll the repository until the task's status differs from `from_status`.

    The Temporal workflow mutates state via activities running in this same
    process (see workflows/activities.py); by the time the first activity
    completes the repository already reflects it, so a short poll bridges
    the gap between "signalled/started a workflow" (async, returns
    immediately) and "an HTTP endpoint that returns the resulting state"
    (the existing response contract every non-Temporal endpoint already
    has). Raises TimeoutError if the status hasn't moved in time - callers
    should surface that as a 202/504-ish "still processing" response rather
    than silently returning stale state.
    """

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        task = repository.get_task(task_id, context)
        if task is not None and task.status != from_status:
            return task
        await asyncio.sleep(poll_interval_seconds)
    raise TimeoutError(f"Task {task_id} did not change status within {timeout_seconds}s")
