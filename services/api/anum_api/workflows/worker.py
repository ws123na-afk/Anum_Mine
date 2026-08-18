"""Temporal worker: polls `settings.temporal_task_queue` and executes
TaskWorkflow (+ its activities).

Two ways to run it:

1. In-process (the default for this "Now"-scoped integration): FastAPI's
   lifespan starts `run_worker_in_background()` as a background asyncio
   task when `settings.temporal_address` is set, so a single `uvicorn`
   process both serves HTTP and executes workflows. This keeps the
   in-memory repository backend working unmodified (its store is
   process-local - a separate worker process couldn't see it) and is a
   reasonable default for a single-instance deployment.
2. Standalone process: `python -m anum_api.workflows.worker`, for a
   PostgreSQL-backed deployment that wants to scale the worker separately
   from the API. Left here for that "Later" step; not exercised by the
   in-process path above.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.worker import Worker

from ..settings import settings
from .activities import cancel_task_activity, resume_after_approval_activity, run_agent_activity
from .client import get_temporal_client
from .task_workflow import TaskWorkflow

logger = logging.getLogger(__name__)


async def build_worker() -> Worker | None:
    client = await get_temporal_client()
    if client is None:
        return None
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[TaskWorkflow],
        activities=[run_agent_activity, resume_after_approval_activity, cancel_task_activity],
    )


async def run_worker_in_background() -> asyncio.Task | None:
    """Start the worker as a background task; returns None if Temporal isn't configured.

    The caller (main.py's lifespan) is responsible for cancelling the
    returned task on shutdown.
    """

    worker = await build_worker()
    if worker is None:
        return None

    async def _run() -> None:
        try:
            await worker.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Temporal worker stopped unexpectedly")

    return asyncio.create_task(_run(), name="anum-temporal-worker")


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    worker = await build_worker()
    if worker is None:
        raise SystemExit(
            "ANUM_TEMPORAL_ADDRESS is not set - nothing for a standalone worker to connect to."
        )
    logger.info(
        "Starting ANUM Temporal worker on task queue %r", settings.temporal_task_queue
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(_main())
