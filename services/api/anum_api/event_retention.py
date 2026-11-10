"""Delete workspace events older than their retention (docs/realtime.md#event-history).

    python -m anum_api.event_retention --dry-run   # count only
    python -m anum_api.event_retention             # delete

``domain_events`` holds every event a workspace ever produced (the outbox the event stream
and NATS are fed from). Nothing deleted its rows, so the table, and every read of a
workspace's history, grew without end. An event older than ``ANUM_EVENT_RETENTION_DAYS``
is deleted; the setting is off by default (0 keeps every event) and takes 7 to 3650 when the
owner turns it on. What stays:

* ``audit_records``, the audit trail, is another table and is never touched here;
* tasks, their runs with every step, approvals and results are their own tables;
* with ``ANUM_EVENT_BUS=nats``, an event the outbox relay has not published yet.

What goes with an old event is its line in a task's Activity list and run log export
(``GET /tasks/{id}/events``, ``/export``) and its place in a stream resumed with a
``Last-Event-ID`` that old.

``python -m anum_api.voice_retention`` (the daily retention job) runs this purge and
reports it under ``events``, so a deployment needs no extra schedule.

Scopes are discovered as ``anum_maintenance``: tenant and workspace of events older than
7 days, nothing else (migration ``0162_event_stream_retention``). Each scope is then purged
as the application role inside its tenant and workspace RLS context, in chunks, each in
its own short transaction, and a workspace that lost events gets one audit record with
the count only (never a type, a subject or a payload). A run is idempotent, and an
interrupted run is finished by the next one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import text

from .maintenance import SessionFactory, default_session_factory, discover, scoped_unit
from .schemas import new_id, utc_now
from .settings import settings

# The shortest period the setting accepts (the migration's discovery policy holds the same number).
MIN_RETENTION_DAYS = 7
ACTOR = "system:retention"
# Rows deleted per transaction: short enough that no lock or WAL burst is noticed by the API.
DELETE_CHUNK = 5000

# As anum_maintenance: the scopes that hold an event older than 7 days ('' = tenant-wide events).
_DISCOVER = text(
    """
    select tenant_id, workspace_id from (
      select distinct tenant_id, coalesce(workspace_id, '') as workspace_id from domain_events
    ) found
    where (tenant_id, workspace_id) > (:after_tenant, :after_workspace)
    order by tenant_id, workspace_id
    limit :limit
    """
)
# As the application role inside the scope's RLS context; ix_domain_events_scope_created serves all four.
# The scope is written out twice (one workspace's events, the tenant-wide ones) so each is an index range,
# and as literal statements: no SQL text is built at run time.
_COUNT = {
    True: text(
        """
        select count(*) from domain_events
        where tenant_id = :t and workspace_id = :w and created_at < :cutoff
          and (not :published_only or published_at is not null)
        """
    ),
    False: text(
        """
        select count(*) from domain_events
        where tenant_id = :t and workspace_id is null and created_at < :cutoff
          and (not :published_only or published_at is not null)
        """
    ),
}
_DELETE = {
    True: text(
        """
        delete from domain_events where id in (
          select id from domain_events
          where tenant_id = :t and workspace_id = :w and created_at < :cutoff
            and (not :published_only or published_at is not null)
          order by created_at, id limit :chunk)
        """
    ),
    False: text(
        """
        delete from domain_events where id in (
          select id from domain_events
          where tenant_id = :t and workspace_id is null and created_at < :cutoff
            and (not :published_only or published_at is not null)
          order by created_at, id limit :chunk)
        """
    ),
}


@dataclass
class EventPurgeResult:
    retention_days: int
    dry_run: bool
    scopes_scanned: int = 0
    scopes_purged: int = 0
    events: int = 0


def purge_events(
    session_factory: SessionFactory = default_session_factory,
    *,
    retention_days: int,
    maintenance_session_factory: SessionFactory | None = None,
    published_only: bool = False,
    now: datetime | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
    chunk: int = DELETE_CHUNK,
) -> EventPurgeResult:
    """Delete events older than ``retention_days`` (0 keeps every event).

    ``published_only`` keeps events the outbox relay has not published yet (NATS mode).
    """
    result = EventPurgeResult(retention_days=retention_days, dry_run=dry_run)
    if retention_days == 0:
        return result
    if retention_days < MIN_RETENTION_DAYS:
        raise ValueError(f"retention_days must be 0 (keep) or at least {MIN_RETENTION_DAYS} days")
    now = now or utc_now()
    cutoff = now - timedelta(days=retention_days)
    after = ("", "")
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _DISCOVER,
            {"after_tenant": after[0], "after_workspace": after[1], "limit": batch_size},
        )
        for row in rows:
            result.scopes_scanned += 1
            deleted = _purge_scope(
                session_factory, row.tenant_id, row.workspace_id or None,
                cutoff=cutoff, published_only=published_only, now=now, retention_days=retention_days,
                dry_run=dry_run, chunk=chunk,
            )
            if deleted:
                result.scopes_purged += 1
                result.events += deleted
        if len(rows) < batch_size:
            return result
        after = (rows[-1].tenant_id, rows[-1].workspace_id)


def _purge_scope(
    session_factory: SessionFactory,
    tenant_id: str,
    workspace_id: str | None,
    *,
    cutoff: datetime,
    published_only: bool,
    now: datetime,
    retention_days: int,
    dry_run: bool,
    chunk: int,
) -> int:
    params: dict[str, object] = {"t": tenant_id, "cutoff": cutoff, "published_only": published_only}
    if workspace_id:
        params["w"] = workspace_id
    if dry_run:
        with scoped_unit(session_factory, tenant_id, workspace_id or "") as session:
            return int(session.execute(_COUNT[bool(workspace_id)], params).scalar_one())
    deleted = 0
    while True:
        with scoped_unit(session_factory, tenant_id, workspace_id or "") as session:
            removed = int(session.execute(_DELETE[bool(workspace_id)], {**params, "chunk": chunk}).rowcount or 0)
        deleted += removed
        if removed < chunk:
            break
    if deleted and workspace_id:
        with scoped_unit(session_factory, tenant_id, workspace_id) as session:
            _audit(session, tenant_id, workspace_id, now, retention_days, deleted)
    return deleted


def _audit(session: object, tenant_id: str, workspace_id: str, now: datetime, retention_days: int, events: int) -> None:
    """One record per purged workspace: the period and the count, never what was deleted."""
    from .audit import AuditRecord
    from .db.repository import SqlAlchemyRepository

    SqlAlchemyRepository(session).record_audit(AuditRecord(  # type: ignore[arg-type]
        id=new_id("audit"), tenant_id=tenant_id, workspace_id=workspace_id, actor=ACTOR,
        action="event_retention.purge", target=f"event_retention:{workspace_id}", outcome="success",
        correlation_id=new_id("correlation"), created_at=now,
        metadata={"retention_days": retention_days, "events": events},
    ))


def purge(
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    retention_days: int | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict[str, object]:
    return asdict(purge_events(
        session_factory,
        retention_days=settings.event_retention_days if retention_days is None else retention_days,
        maintenance_session_factory=maintenance_session_factory,
        published_only=settings.event_bus == "nats",
        dry_run=dry_run,
        batch_size=batch_size,
    ))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.event_retention",
        description="Delete workspace events older than ANUM_EVENT_RETENTION_DAYS.",
    )
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted")
    parser.add_argument("--batch-size", type=int, default=500, help="scopes discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if settings.repository_backend != "postgresql":
        print(json.dumps({"error": "event_retention needs ANUM_REPOSITORY_BACKEND=postgresql"}), file=sys.stderr)
        return 2
    print(json.dumps(purge(dry_run=args.dry_run, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
