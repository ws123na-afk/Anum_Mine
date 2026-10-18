"""Delete call recordings past their retention (docs/calls.md#recordings).

A recording is kept for the workspace's ``recording_retention_days`` (1 to 365, set by an
owner under Calls > Provider); its expiry is written on the call when it is stored. The call
itself, its notes, transcript and summary stay until someone deletes the call.

``python -m anum_api.voice_retention`` (the daily retention job) runs this purge and reports it
under ``call_recordings``, so a deployment needs no extra schedule.

Calls holding an expired recording are discovered as ``anum_maintenance``: id, scope and
expiry only (migration ``0071_calls``). Each one is then cleared as the application role
inside its tenant and workspace RLS context, and the file is deleted from the workspace
files. Output is counts only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from ..maintenance import SessionFactory, default_session_factory, discover
from ..schemas import utc_now
from ..settings import settings
from .service import system_context
from .store import memory_data, open_calls_store

_DISCOVER = text(
    """
    select id, tenant_id, workspace_id
    from call_log
    where recording_expires_at <= :now and id > :after
    order by id
    limit :limit
    """
)


@dataclass
class RecordingPurge:
    dry_run: bool
    scanned: int = 0
    deleted: int = 0
    failed: int = 0


def _expired_memory(now: datetime) -> list[tuple[str, str, str]]:
    with memory_data.lock:
        return sorted(
            (call.id, call.tenant_id, call.workspace_id)
            for call in memory_data.calls.values()
            if call.recording_file_id and call.recording_expires_at is not None and call.recording_expires_at <= now
        )


def purge_recordings(
    *, session_factory: SessionFactory = default_session_factory, now: datetime | None = None, dry_run: bool = False, batch_size: int = 500,
) -> RecordingPurge:
    from ..files import remove_workspace_file

    now = now or utc_now()
    result = RecordingPurge(dry_run=dry_run)
    after = ""
    while True:
        if settings.repository_backend == "memory":
            rows: list[Any] = [row for row in _expired_memory(now) if row[0] > after][:batch_size]
        else:
            rows = [(row.id, row.tenant_id, row.workspace_id) for row in discover(session_factory, _DISCOVER, {"now": now, "after": after, "limit": batch_size})]
        for call_id, tenant_id, workspace_id in rows:
            result.scanned += 1
            context = system_context(tenant_id, workspace_id)
            with open_calls_store(context) as store:
                entry = store.get_call(call_id, lock=True)
                if entry is None or not entry.recording_file_id or entry.recording_expires_at is None or entry.recording_expires_at > now:
                    continue
                file_id = entry.recording_file_id
                if not dry_run:
                    store.save_call(entry.model_copy(update={"recording_file_id": None, "recording_expires_at": None, "updated_at": now}))
            if dry_run:
                result.deleted += 1
                continue
            try:
                remove_workspace_file(context, file_id)
                result.deleted += 1
            except Exception:  # the call no longer points at it; the file is counted and left for the operator
                result.failed += 1
        if len(rows) < batch_size:
            return result
        after = rows[-1][0]


def purge(*, dry_run: bool = False, batch_size: int = 500) -> dict[str, Any]:
    return asdict(purge_recordings(dry_run=dry_run, batch_size=batch_size))
