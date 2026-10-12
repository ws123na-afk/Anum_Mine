"""Background refresh of developer requests still in progress (docs/notifications.md#developer-mode).

Developer requests learn their state from GitHub only when someone looks (the list or
the detail view refreshes them). So that an owner hears "Change ready for review" while
the app is closed, each API process refreshes the configured tenant's requests that are
still in progress every ``ANUM_DEVELOPER_WATCH_SECONDS`` (default 120). A refresh that
changes a request's status records ``developer_request.ready_for_review`` or
``developer_request.failed`` (``developer._refresh``), and the push dispatcher delivers it.

It runs only when phone push is on (``ANUM_FCM_SERVICE_ACCOUNT_JSON``), developer mode is
configured and the interval is above zero. Like the other cross-tenant jobs
(``anum_api/maintenance.py``) it never bypasses RLS: with PostgreSQL it discovers the
in-progress requests' ids and workspaces as ``anum_maintenance`` (migration
``0027_push_devices``: four columns of in-progress rows only) and refreshes each one as
the application role inside its tenant and workspace.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text

from .schemas import TenantContext
from .settings import settings

logger = logging.getLogger("anum.developer")

WATCH_ACTOR = "system:developer-watch"
BATCH = 50
_IN_PROGRESS = text(
    """
    select id, workspace_id
    from developer_requests
    where tenant_id = :tenant_id
      and status in ('queued', 'coding', 'ci_running', 'ci_failed', 'waiting_approval')
    order by id
    limit :limit
    """
)


def discover_in_progress(tenant_id: str, workspace_id: str = "", *, limit: int = BATCH) -> list[tuple[str, str]]:
    """``(request id, workspace id)`` of the tenant's requests that are not finished."""
    from .developer import TERMINAL, developer_store

    if settings.repository_backend == "memory":
        found = [
            (item.id, item.workspace_id)
            for item in developer_store._items.values()
            if item.tenant_id == tenant_id and item.status not in TERMINAL
        ]
    else:
        from .maintenance import default_session_factory, discover

        rows = discover(default_session_factory, _IN_PROGRESS, {"tenant_id": tenant_id, "limit": limit})
        found = [(str(row.id), str(row.workspace_id)) for row in rows]
    if workspace_id:
        found = [item for item in found if item[1] == workspace_id]
    return sorted(found)[:limit]


async def refresh_in_progress() -> int:
    """Refresh every in-progress request of the configured tenant once. Returns how many changed."""
    from .dependencies import _unit_of_work
    from .developer import TERMINAL, _refresh, developer_settings, open_developer_store, setup_problems

    if setup_problems():
        return 0
    tenant_id = developer_settings.tenant_id.strip()
    scopes = await asyncio.to_thread(discover_in_progress, tenant_id, developer_settings.workspace_id.strip())
    changed = 0
    for request_id, workspace_id in scopes:
        context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=WATCH_ACTOR, roles=[])
        try:
            with open_developer_store(context) as store:
                request = store.get(request_id, context)
            if request is None or request.status in TERMINAL:
                continue
            previous = request.status
            with _unit_of_work(context, require_membership=False) as repository:
                refreshed = await _refresh(request, repository, context)
            changed += int(refreshed.status != previous)
        except Exception:  # one request must not stop the others
            logger.exception(
                "developer_watch_failed",
                extra={"anum_developer": {"event": "watch_failed", "request_id": request_id}},
            )
    return changed


class DeveloperWatcher:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="anum-developer-watch")

    async def stop(self, *, timeout: float = 10.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._stop = None

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Developer watcher started without a stop event")
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
                break
            except TimeoutError:
                pass
            try:
                await refresh_in_progress()
            except Exception:
                logger.exception("Developer watch pass failed")


def build_developer_watcher(config: Any = None) -> DeveloperWatcher | None:
    from .developer import developer_settings, setup_problems
    from .push import configured_account

    interval = (config or developer_settings).watch_seconds
    if interval <= 0 or configured_account() is None or setup_problems():
        return None
    return DeveloperWatcher(interval)
