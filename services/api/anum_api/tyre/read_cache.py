"""A short, tenant-scoped cache of the loaded tyre dataset and its forecasts.

Every read-only tyre tool (fleet summary, tyres due, purchase plan, vehicles) started by
loading the whole synced snapshot from the store and recomputing every tyre's forecast. A
spoken turn that asked three of them did that three times, in parallel threads that share
one interpreter lock, which is why the lookups timed out (docs/voice.md#latency).

* **Scope.** One entry per ``(tenant_id, workspace_id)``; a different tenant or workspace
  never sees it. Roles are checked by the tools before they reach this cache.
* **Lifetime.** :data:`TTL_SECONDS`, and never past midnight UTC (forecasts depend on the
  date). A sync, a saved or deleted connection, and clearing the store drop the entry, so
  the next read is fresh. Another API replica learns of a sync within the TTL at most.
* **One computation at a time.** Callers that arrive while the entry is being built wait
  for it instead of building it again; a caller that gave up (a lookup timeout) leaves the
  build running, so the next question finds it ready.
* **Derived results** (the fleet summary, a purchase plan) are memoised on the entry, so a
  follow-up question costs no computation either.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..schemas import TenantContext, utc_now

TTL_SECONDS = 30.0
MAX_ENTRIES = 64


@dataclass
class _Entry:
    value: Any
    day: date
    built_at: float
    derived: dict[Any, Any] = field(default_factory=dict)


_lock = threading.Lock()
_entries: dict[tuple[str, str], _Entry] = {}
_building: dict[tuple[str, str], threading.Lock] = {}
_generation = 0
_stats = {"hits": 0, "builds": 0}


def _key(tenant_id: str, workspace_id: str) -> tuple[str, str]:
    return tenant_id, workspace_id


def invalidate(tenant_id: str, workspace_id: str) -> None:
    """Drop the scope's entry; a build that started before this is not stored."""
    global _generation
    with _lock:
        _generation += 1
        _entries.pop(_key(tenant_id, workspace_id), None)


def clear() -> None:
    global _generation
    with _lock:
        _generation += 1
        _entries.clear()


def stats() -> dict[str, int]:
    return dict(_stats)


def _fresh(entry: _Entry | None) -> bool:
    return entry is not None and time.monotonic() - entry.built_at < TTL_SECONDS and entry.day == utc_now().date()


def get(context: TenantContext, build: Callable[[], Any]) -> Any:
    """The scope's cached value, building it (once, at a time) when missing or stale.

    ``build`` raises when the data is not connected or not synced; nothing is cached then.
    """
    key = _key(context.tenant_id, context.workspace_id)
    with _lock:
        entry = _entries.get(key)
        if _fresh(entry):
            _stats["hits"] += 1
            return entry.value  # type: ignore[union-attr]
        gate = _building.setdefault(key, threading.Lock())
    with gate:
        with _lock:
            entry = _entries.get(key)
            if _fresh(entry):
                _stats["hits"] += 1
                return entry.value  # type: ignore[union-attr]
            started_at = _generation
        value = build()
        with _lock:
            _stats["builds"] += 1
            if started_at == _generation:
                if len(_entries) >= MAX_ENTRIES:
                    _entries.pop(next(iter(_entries)))
                _entries[key] = _Entry(value=value, day=utc_now().date(), built_at=time.monotonic())
        return value


def memo(context: TenantContext, source: Any, name: Any, compute: Callable[[], Any]) -> Any:
    """A result derived from ``source`` (the first item of the scope's cached value), kept
    with the entry while that entry still holds the same ``source``."""
    key = _key(context.tenant_id, context.workspace_id)
    with _lock:
        entry = _entries.get(key)
        if entry is not None and entry.value[0] is source and name in entry.derived:
            return entry.derived[name]
    value = compute()
    with _lock:
        current = _entries.get(key)
        if current is not None and current.value[0] is source:
            current.derived[name] = value
    return value
