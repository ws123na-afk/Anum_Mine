"""Standard retention for meeting transcripts and reception visitor records (owner decision O5, 28 October 2026).

    python -m anum_api.record_retention --dry-run   # count only
    python -m anum_api.record_retention             # delete

The owner asked to "keep what is standard". Each workspace keeps:

* **Meeting transcripts** for ``transcript_days`` after the meeting ended (standard 90 days, an owner may choose 7 to
  1825). The purge removes exactly what the owner's manual ``DELETE /transcript`` removes (the messages, turn leases
  and event log) through the same helper, ``live_agent_meeting_room.erase_transcript``. The meeting, its usage
  totals, the summary, confirmed outputs, their links and tasks stay.
* **Visitor records** (``office_visitors``) for ``visitor_days`` after the visit (standard 365 days, 30 to 1825).
  A visit's time is its latest of expected, checked in, checked out and cancelled: a checked-out visit counts from
  the check-out, a cancelled one from the cancellation and a visitor who never came from the planned time.

The periods are stored in ``workspace_record_retention`` (migration ``0120_record_retention``); no row, or a null
column, means the standard period. No time-indexed office snapshot is stored (owner decision O8 stays "not stored"),
so there is nothing else to purge.

``python -m anum_api.voice_retention`` (the daily retention job) runs this purge last and reports it under
``record_retention``, so a deployment needs no extra schedule.

Workspaces are discovered as ``anum_maintenance``: tenant and workspace of ended meetings that still hold a
transcript older than 7 days and of visits older than 30 days, nothing else. Each workspace is then purged in one
transaction as the application role inside its tenant and workspace RLS context, where its periods are read, and
leaves one audit record with counts only (never a title, message, name, company or purpose). A run is idempotent:
a second run finds nothing. On SIGTERM the workspace in progress finishes and commits, then the run stops.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from .maintenance import SessionFactory, default_session_factory, discover, scoped_unit
from .schemas import new_id, utc_now
from .settings import settings

# Standard periods and the range an owner may choose (the migration's check constraints hold the same numbers).
TRANSCRIPT_DEFAULT_DAYS = 90
TRANSCRIPT_MIN_DAYS = 7
VISITOR_DEFAULT_DAYS = 365
VISITOR_MIN_DAYS = 30
MAX_DAYS = 1825
ACTOR = "system:retention"
TERMINAL = ("finished", "cancelled", "failed")

# As anum_maintenance: workspaces with an ended meeting that still holds a transcript, or an old visit.
_DISCOVER = text(
    """
    select tenant_id, workspace_id from (
      select tenant_id, workspace_id from agent_meeting_sessions
      union
      select tenant_id, workspace_id from office_visitors
    ) found
    where (tenant_id, workspace_id) > (:after_tenant, :after_workspace)
    order by tenant_id, workspace_id
    limit :limit
    """
)
# As the application role inside the workspace's RLS context.
_SETTING = text(
    "select transcript_days, visitor_days from workspace_record_retention where tenant_id = :t and workspace_id = :w"
)
_OLD_TRANSCRIPTS = text(
    """
    select id from agent_meeting_sessions
    where tenant_id = :t and workspace_id = :w
      and status in ('finished', 'cancelled', 'failed') and transcript_deleted_at is null
      and coalesce(ended_at, updated_at) < :cutoff
    order by id
    for update skip locked
    """
)
_DELETE_VISITORS = text(
    """
    delete from office_visitors
    where tenant_id = :t and workspace_id = :w
      and greatest(expected_at, checked_in_at, checked_out_at, cancelled_at) < :cutoff
    """
)


@dataclass(frozen=True)
class RetentionSetting:
    transcript_days: int | None = None
    visitor_days: int | None = None

    @property
    def transcripts(self) -> int:
        return TRANSCRIPT_DEFAULT_DAYS if self.transcript_days is None else self.transcript_days

    @property
    def visitors(self) -> int:
        return VISITOR_DEFAULT_DAYS if self.visitor_days is None else self.visitor_days


def read_setting(session: Any, tenant_id: str, workspace_id: str) -> RetentionSetting:
    row = session.execute(_SETTING, {"t": tenant_id, "w": workspace_id}).one_or_none()
    return RetentionSetting() if row is None else RetentionSetting(row.transcript_days, row.visitor_days)


@dataclass
class RecordPurgeResult:
    dry_run: bool
    workspaces_scanned: int = 0
    workspaces_purged: int = 0
    meeting_transcripts: int = 0
    transcript_messages: int = 0
    transcript_events: int = 0
    visitor_records: int = 0
    stopped: bool = False


def purge_workspace(session: Any, tenant_id: str, workspace_id: str, now: datetime) -> dict[str, int]:
    """Purge one workspace inside its RLS context; the caller commits (or rolls back for a dry run)."""
    from .live_agent_meeting_room import erase_transcript

    setting = read_setting(session, tenant_id, workspace_id)
    scope = {"t": tenant_id, "w": workspace_id}
    counts = {"meetings": 0, "messages": 0, "events": 0, "visitors": 0}
    transcript_cutoff = now - timedelta(days=setting.transcripts)
    for meeting_id in session.execute(_OLD_TRANSCRIPTS, {**scope, "cutoff": transcript_cutoff}).scalars().all():
        erased = erase_transcript(session, tenant_id, workspace_id, meeting_id)
        counts["meetings"] += 1
        counts["messages"] += erased["turns"]
        counts["events"] += erased["events"]
    visitor_cutoff = now - timedelta(days=setting.visitors)
    counts["visitors"] = int(session.execute(_DELETE_VISITORS, {**scope, "cutoff": visitor_cutoff}).rowcount or 0)
    if counts["meetings"] or counts["visitors"]:
        _audit(session, tenant_id, workspace_id, now, setting, counts)
    return counts


def _audit(session: Any, tenant_id: str, workspace_id: str, now: datetime, setting: RetentionSetting,
           counts: dict[str, int]) -> None:
    """One record per purged workspace: the periods and counts, never what was deleted."""
    from .audit import AuditRecord
    from .db.repository import SqlAlchemyRepository

    SqlAlchemyRepository(session).record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=tenant_id, workspace_id=workspace_id, actor=ACTOR,
        action="record_retention.purge", target=f"record_retention:{workspace_id}", outcome="success",
        correlation_id=new_id("correlation"), created_at=now,
        metadata={"transcript_days": setting.transcripts, "visitor_days": setting.visitors,
                  "meeting_transcripts": counts["meetings"], "transcript_messages": counts["messages"],
                  "transcript_events": counts["events"], "visitor_records": counts["visitors"]},
    ))


@contextmanager
def finish_on_sigterm() -> Iterator[threading.Event]:
    """While the purge runs, SIGTERM only asks it to stop after the workspace in progress has committed."""
    stop = threading.Event()
    try:
        previous = signal.signal(signal.SIGTERM, lambda *_: stop.set())
    except ValueError:  # not the main thread (tests, an embedding server): nothing to install
        yield stop
        return
    try:
        yield stop
    finally:
        signal.signal(signal.SIGTERM, previous)


def purge_records(
    session_factory: SessionFactory = default_session_factory,
    *,
    maintenance_session_factory: SessionFactory | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
    should_stop: Callable[[], bool] = lambda: False,
) -> RecordPurgeResult:
    now = now or utc_now()
    result = RecordPurgeResult(dry_run=dry_run)
    after = ("", "")
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _DISCOVER,
            {"after_tenant": after[0], "after_workspace": after[1], "limit": batch_size},
        )
        for row in rows:
            if should_stop():
                result.stopped = True
                return result
            result.workspaces_scanned += 1
            with scoped_unit(session_factory, row.tenant_id, row.workspace_id) as session:
                counts = purge_workspace(session, row.tenant_id, row.workspace_id, now)
                if dry_run:
                    session.rollback()
            if counts["meetings"] or counts["visitors"]:
                result.workspaces_purged += 1
            result.meeting_transcripts += counts["meetings"]
            result.transcript_messages += counts["messages"]
            result.transcript_events += counts["events"]
            result.visitor_records += counts["visitors"]
        if len(rows) < batch_size:
            return result
        after = (rows[-1].tenant_id, rows[-1].workspace_id)


def purge(
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict[str, object]:
    with finish_on_sigterm() as stop:
        return asdict(purge_records(
            session_factory,
            maintenance_session_factory=maintenance_session_factory,
            dry_run=dry_run,
            batch_size=batch_size,
            should_stop=stop.is_set,
        ))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.record_retention",
        description="Delete meeting transcripts and visitor records older than each workspace's retention.",
    )
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted")
    parser.add_argument("--batch-size", type=int, default=500, help="workspaces discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if settings.repository_backend != "postgresql":
        print(json.dumps({"error": "record_retention needs ANUM_REPOSITORY_BACKEND=postgresql"}), file=sys.stderr)
        return 2
    print(json.dumps(purge(dry_run=args.dry_run, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
