"""Real, end-to-end tests against a local Temporal dev server.

Spins up `temporal server start-dev` (the CLI's embedded, sqlite-backed dev
server) as a subprocess, runs an in-process Worker against it, and drives
real TaskWorkflow executions through start -> activity -> signal ->
resume, exactly the way main.py's endpoints will (see workflows/client.py).
No mocking of Temporal itself - this proves the durable pause/resume/cancel
mechanism actually works, not just that the code compiles.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from temporalio.client import Client

import anum_api.workflows.client as workflow_client
from anum_api.dependencies import memory_repository
from anum_api.schemas import Task, TaskStatus, TenantContext, new_id, utc_now
from anum_api.settings import settings
from anum_api.workflows.client import (
    signal_approval_decision,
    signal_cancel,
    start_task_workflow,
    wait_for_task_change,
)
from anum_api.workflows.worker import build_worker

pytestmark = pytest.mark.temporal

TEMPORAL_PORT = 17233
TEMPORAL_ADDRESS = f"localhost:{TEMPORAL_PORT}"

TENANT_CONTEXT = TenantContext(
    tenant_id="tenant_temporal_test",
    workspace_id="workspace_temporal_test",
    user_id="user_temporal_test",
    roles=["owner"],
)


def _temporal_binary() -> str:
    binary = shutil.which("temporal")
    if not binary:
        pytest.skip("temporal CLI is not on PATH")
    return binary


@pytest.fixture(scope="module")
def temporal_dev_server() -> Iterator[None]:
    binary = _temporal_binary()
    # A fresh, uniquely-named sqlite file every run - reusing one across
    # runs risks resuming stale/half-written shard state from a prior
    # killed process, which manifests as confusing "shard status unknown"
    # errors on the *next* run rather than a clean failure on this one.
    work_dir = Path(tempfile.mkdtemp(prefix="anum-temporal-test-"))
    db_path = work_dir / "temporal.db"
    log_file = open(work_dir / "server.log", "wb")
    process = subprocess.Popen(
        [
            binary,
            "server",
            "start-dev",
            "--port",
            str(TEMPORAL_PORT),
            "--ui-port",
            "0",
            "--headless",
            "--db-filename",
            str(db_path),
            "--namespace",
            "default",
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 20
        connected = False
        while time.monotonic() < deadline:
            try:
                asyncio.run(Client.connect(TEMPORAL_ADDRESS))
                connected = True
                break
            except Exception:
                time.sleep(0.5)
        if not connected:
            process.terminate()
            process.wait(timeout=5)
            pytest.skip("temporal dev server did not become ready in time")
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        log_file.close()
        shutil.rmtree(work_dir, ignore_errors=True)


@pytest.fixture
def temporal_settings(temporal_dev_server: None, request: pytest.FixtureRequest) -> Iterator[None]:
    # Each test gets its OWN task queue (derived from the test name) rather
    # than sharing TASK_QUEUE across the whole module. The dev server
    # process is shared (module-scoped, expensive to start), but reusing
    # one queue meant test N+1's fresh Worker could start polling the same
    # queue while test N's Worker was still mid-shutdown, occasionally
    # letting a workflow task get delivered to the worker that's on its way
    # out - which then never completes it. Distinct queues per test make
    # that race impossible.
    original_address = settings.temporal_address
    original_queue = settings.temporal_task_queue
    settings.temporal_address = TEMPORAL_ADDRESS
    settings.temporal_task_queue = f"anum-tasks-test-{request.node.name}"
    workflow_client._client = None
    try:
        yield
    finally:
        settings.temporal_address = original_address
        settings.temporal_task_queue = original_queue
        workflow_client._client = None


@pytest_asyncio.fixture
async def running_worker(temporal_settings: None) -> AsyncIterator[None]:
    worker = await build_worker()
    assert worker is not None
    task = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        await worker.shutdown()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _seed_task(prompt: str) -> Task:
    now = utc_now()
    task = Task(
        id=new_id("task"),
        title="Temporal test task",
        prompt=prompt,
        status=TaskStatus.CREATED,
        tenant_id=TENANT_CONTEXT.tenant_id,
        workspace_id=TENANT_CONTEXT.workspace_id,
        created_at=now,
        updated_at=now,
    )
    memory_repository.save_task(task)
    return task


@pytest.mark.asyncio
async def test_low_risk_task_runs_to_completion_via_temporal(running_worker: None) -> None:
    task = _seed_task("Summarize last week's notes")

    await start_task_workflow(task.id, TENANT_CONTEXT)

    completed = await wait_for_task_change(
        memory_repository, task.id, TENANT_CONTEXT, from_status=TaskStatus.CREATED, timeout_seconds=10
    )
    assert completed.status == TaskStatus.COMPLETED

    run = memory_repository.find_run_for_task(task.id, TENANT_CONTEXT)
    assert run is not None
    assert run.status == TaskStatus.COMPLETED
    assert run.result is not None


@pytest.mark.asyncio
async def test_high_risk_task_pauses_then_resumes_on_approval_signal(running_worker: None) -> None:
    task = _seed_task("Delete all archived customer records")

    await start_task_workflow(task.id, TENANT_CONTEXT)

    waiting = await wait_for_task_change(
        memory_repository, task.id, TENANT_CONTEXT, from_status=TaskStatus.CREATED, timeout_seconds=10
    )
    assert waiting.status == TaskStatus.WAITING_APPROVAL

    approvals = [
        approval
        for approval in memory_repository.list_approvals(TENANT_CONTEXT)
        if approval.task_id == task.id
    ]
    assert len(approvals) == 1
    assert approvals[0].status.value == "pending"

    await signal_approval_decision(task.id, "approved")

    completed = await wait_for_task_change(
        memory_repository,
        task.id,
        TENANT_CONTEXT,
        from_status=TaskStatus.WAITING_APPROVAL,
        timeout_seconds=10,
    )
    assert completed.status == TaskStatus.COMPLETED

    approvals_after = [
        approval
        for approval in memory_repository.list_approvals(TENANT_CONTEXT)
        if approval.task_id == task.id
    ]
    assert approvals_after[0].status.value == "approved"


@pytest.mark.asyncio
async def test_high_risk_task_rejection_marks_task_failed(running_worker: None) -> None:
    task = _seed_task("Send a payment to an external vendor")

    await start_task_workflow(task.id, TENANT_CONTEXT)
    await wait_for_task_change(
        memory_repository, task.id, TENANT_CONTEXT, from_status=TaskStatus.CREATED, timeout_seconds=10
    )

    await signal_approval_decision(task.id, "rejected")

    resolved = await wait_for_task_change(
        memory_repository,
        task.id,
        TENANT_CONTEXT,
        from_status=TaskStatus.WAITING_APPROVAL,
        timeout_seconds=10,
    )
    assert resolved.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_cancel_signal_during_approval_wait_cancels_task(running_worker: None) -> None:
    task = _seed_task("Publish the announcement externally")

    await start_task_workflow(task.id, TENANT_CONTEXT)
    await wait_for_task_change(
        memory_repository, task.id, TENANT_CONTEXT, from_status=TaskStatus.CREATED, timeout_seconds=10
    )

    await signal_cancel(task.id)

    resolved = await wait_for_task_change(
        memory_repository,
        task.id,
        TENANT_CONTEXT,
        from_status=TaskStatus.WAITING_APPROVAL,
        timeout_seconds=10,
    )
    assert resolved.status == TaskStatus.CANCELLED

    approvals_after = [
        approval
        for approval in memory_repository.list_approvals(TENANT_CONTEXT)
        if approval.task_id == task.id
    ]
    assert approvals_after[0].status.value == "expired"
