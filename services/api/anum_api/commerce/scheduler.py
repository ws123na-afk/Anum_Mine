"""The background commerce sync (``ANUM_COMMERCE_SYNC_ENABLED``, docs/commerce.md#sync).

Discovery follows the cross-tenant maintenance pattern (``anum_api.maintenance``): with
PostgreSQL the due connectors are found as ``anum_maintenance``, which may read only the
``id``, ``tenant_id``, ``workspace_id``, ``enabled`` and ``next_sync_at`` of enabled
connectors whose sync is due (migration ``0038_commerce``), never credentials or data.
Each sync then runs as the application role inside that connector's tenant and
workspace. A connector is claimed first (its ``next_sync_at`` moves ahead in a
compare-and-set update), so replicas running the loop never sync the same connector at once.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import text

from ..schemas import TenantContext, utc_now
from ..settings import settings
from .config import commerce_config
from .store import commerce_store, open_commerce_store
from .sync import sync_connector

logger = logging.getLogger("anum.commerce")
SYSTEM_USER = "system:commerce-sync"
CLAIM_FOR = timedelta(minutes=15)

_DUE = text(
    "select tenant_id, workspace_id, id from commerce_connectors "
    "where enabled and next_sync_at <= now() order by next_sync_at limit :limit"
)


def due_connectors(limit: int) -> list[tuple[str, str, str]]:
    if settings.repository_backend == "memory":
        return commerce_store.due_connectors(utc_now(), limit)
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1], row[2]) for row in discover(default_session_factory, _DUE, {"limit": limit})]


async def run_once(limit: int | None = None) -> int:
    """Sync every due connector once; returns how many were synced."""
    synced = 0
    for tenant_id, workspace_id, connector_id in await asyncio.to_thread(due_connectors, limit or commerce_config.sync_batch_size):
        context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_USER, roles=[])
        now = utc_now()

        def claim(context: TenantContext = context, connector_id: str = connector_id) -> bool:
            with open_commerce_store(context) as store:
                return store.claim_due(context, connector_id, now, now + CLAIM_FOR)

        try:
            if not await asyncio.to_thread(claim):
                continue
            await sync_connector(context, connector_id)
            synced += 1
        except Exception:
            logger.exception("commerce_scheduled_sync_failed", extra={"anum_commerce": {"connector_id": connector_id}})
    return synced


class CommerceSyncScheduler:
    def __init__(self, poll_interval: float) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-commerce-sync")

    async def stop(self, timeout: float = 30.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                # A sync in progress finishes its current page (each page is its own transaction).
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except Exception:
                logger.exception("Commerce sync scheduler stopped with an error")

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Commerce sync scheduler started without a stop event")
        while not stop.is_set():
            try:
                await run_once()
            except Exception:
                logger.exception("Commerce sync pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_commerce_scheduler() -> CommerceSyncScheduler | None:
    if not commerce_config.sync_enabled:
        return None
    return CommerceSyncScheduler(commerce_config.sync_poll_seconds)
