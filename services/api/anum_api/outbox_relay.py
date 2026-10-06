"""Restart-durable PostgreSQL outbox relay for canonical events.

With ``ANUM_REPOSITORY_BACKEND=postgresql`` every event row in ``domain_events`` is
its own outbox entry: ``published_at`` stays null until the relay has had the event
acknowledged by JetStream. The row and its unpublished state are written by the same
insert, in the request's transaction, so a committed event cannot be lost by a crash
and a rolled-back event is never published.

The relay reads across tenants without weakening RLS for the application role. Each
relay transaction starts with ``SET LOCAL ROLE anum_outbox_relay`` (migration 0007).
That role has no access to any table except ``domain_events``, where it can read only
unpublished rows (a dedicated RLS policy) and update only the four publication
columns (column-level grants). Claims use ``FOR UPDATE SKIP LOCKED`` so any number of
API instances can relay concurrently without publishing the same row twice from the
database side; JetStream's ``Nats-Msg-Id`` dedupe covers a crash between the publish
acknowledgement and the commit that marks the row.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .event_bus import EventBus, encode_event, event_subject
from .schemas import DomainEvent
from .telemetry import OutboxSnapshot, register_outbox_source, telemetry

logger = logging.getLogger(__name__)

RELAY_ROLE = "anum_outbox_relay"

_SET_ROLE = text("set local role anum_outbox_relay")
_CLAIM = text(
    """
    select id, tenant_id, workspace_id, type, version, subject, correlation_id, payload,
           created_at, publish_attempts
    from domain_events
    where published_at is null
      and publish_next_attempt_at <= now()
    order by created_at, id
    limit :limit
    for update skip locked
    """
)
# Depth for the anum.outbox.* gauges, read as the relay role (unpublished rows only).
# Parked rows (next attempt 'infinity') are counted apart so they do not hold the
# oldest-age alert open forever; they need an operator, see docs/runbooks.md.
_BACKLOG = text(
    """
    select
      count(*) filter (where publish_next_attempt_at <> 'infinity') as backlog,
      count(*) filter (where publish_next_attempt_at = 'infinity') as parked,
      coalesce(
        extract(epoch from now() - min(created_at)
          filter (where publish_next_attempt_at <> 'infinity')),
        0
      ) as oldest_age_seconds
    from domain_events
    where published_at is null
    """
)
_POSTGRES_OUTBOX = {"anum.outbox": "postgresql"}
_MARK_PUBLISHED = text(
    """
    update domain_events
    set published_at = now(), publish_last_error = null
    where id in :ids and published_at is null
    """
).bindparams(bindparam("ids", expanding=True))
_MARK_FAILED = text(
    """
    update domain_events
    set publish_attempts = publish_attempts + 1,
        publish_next_attempt_at = now() + make_interval(secs => :delay),
        publish_last_error = :error
    where id = :id and published_at is null
    """
)
_MARK_REJECTED = text(
    """
    update domain_events
    set publish_attempts = publish_attempts + 1,
        publish_next_attempt_at = 'infinity',
        publish_last_error = :error
    where id = :id and published_at is null
    """
)


@dataclass(frozen=True)
class ClaimedEvent:
    event: DomainEvent
    attempts: int


@dataclass
class RelayPass:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    rejected: int = 0


class PostgresOutboxRelay:
    """Publishes unpublished ``domain_events`` rows to the bus and marks them."""

    def __init__(
        self,
        bus: EventBus,
        session_factory: Callable[[], Session],
        *,
        batch_size: int = 100,
        poll_interval: float = 1.0,
        base_backoff: float = 0.5,
        max_backoff: float = 30.0,
        metrics_interval: float = 15.0,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.bus = bus
        self.session_factory = session_factory
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.published_count = 0
        self.rejected_count = 0
        # Backlog gauges: refreshed from the relay loop at most every metrics_interval
        # seconds (the metric export thread never queries the database).
        self.metrics_interval = metrics_interval
        self._snapshot: OutboxSnapshot | None = None
        self._snapshot_at = 0.0
        self._unregister_metrics: Callable[[], None] | None = None
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def backoff_for(self, attempts: int) -> float:
        return min(self.max_backoff, self.base_backoff * (2 ** max(0, attempts - 1)))

    # -- backlog metrics ---------------------------------------------------------

    def _read_backlog(self) -> OutboxSnapshot:
        session = self.session_factory()
        try:
            session.execute(_SET_ROLE)
            row = session.execute(_BACKLOG).mappings().one()
            session.rollback()  # read-only; never hold the transaction open
            return OutboxSnapshot(
                backlog=int(row["backlog"] or 0),
                oldest_age_seconds=float(row["oldest_age_seconds"] or 0),
                parked=int(row["parked"] or 0),
            )
        finally:
            session.close()

    async def refresh_backlog(self) -> OutboxSnapshot | None:
        """Re-read the backlog for the gauges. Failures leave the last value stale."""
        try:
            self._snapshot = await asyncio.to_thread(self._read_backlog)
        except Exception:
            logger.warning("Could not read the outbox backlog", exc_info=True)
        self._snapshot_at = time.monotonic()
        return self._snapshot

    def backlog_snapshot(self) -> OutboxSnapshot | None:
        return self._snapshot

    async def _maybe_refresh_backlog(self) -> None:
        if time.monotonic() - self._snapshot_at >= self.metrics_interval:
            await self.refresh_backlog()

    # -- one pass ---------------------------------------------------------------

    def _claim(self, session: Session) -> list[ClaimedEvent]:
        session.execute(_SET_ROLE)
        rows = session.execute(_CLAIM, {"limit": self.batch_size}).mappings().all()
        return [self._claimed_from_row(row) for row in rows]

    @staticmethod
    def _claimed_from_row(row: Any) -> ClaimedEvent:
        event = DomainEvent(
            id=row["id"],
            type=row["type"],
            version=row["version"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            subject=row["subject"],
            correlation_id=row["correlation_id"],
            created_at=row["created_at"],
            payload=dict(row["payload"] or {}),
        )
        return ClaimedEvent(event=event, attempts=int(row["publish_attempts"]))

    def _finish(
        self,
        session: Session,
        published: list[str],
        failed: tuple[ClaimedEvent, str] | None,
        rejected: list[tuple[ClaimedEvent, str]],
    ) -> None:
        if published:
            session.execute(_MARK_PUBLISHED, {"ids": published})
        if failed is not None:
            entry, error = failed
            session.execute(
                _MARK_FAILED,
                {
                    "id": entry.event.id,
                    "delay": self.backoff_for(entry.attempts + 1),
                    "error": error,
                },
            )
        for entry, error in rejected:
            session.execute(_MARK_REJECTED, {"id": entry.event.id, "error": error})
        session.commit()

    async def relay_once(self) -> RelayPass:
        """Claim one batch, publish it in order, and record the outcome.

        The claimed rows stay locked until the outcome commits, so a concurrent relay
        skips them. A failed publish reschedules that row with exponential backoff and
        ends the pass; the remaining rows are released untouched for the next pass.
        """
        result = RelayPass()
        session = self.session_factory()
        try:
            claimed = await asyncio.to_thread(self._claim, session)
            result.claimed = len(claimed)
            published: list[str] = []
            failed: tuple[ClaimedEvent, str] | None = None
            rejected: list[tuple[ClaimedEvent, str]] = []
            for entry in claimed:
                try:
                    subject, data = event_subject(entry.event), encode_event(entry.event)
                except ValueError as exc:
                    # Never publishable; park it instead of blocking the queue forever.
                    rejected.append((entry, (str(exc) or "unpublishable event")[:1000]))
                    telemetry.outbox_rejected.add(1, _POSTGRES_OUTBOX)
                    logger.error("Event %s cannot be published; parked: %s", entry.event.id, exc)
                    continue
                try:
                    await self.bus.publish(subject, data, msg_id=entry.event.id)
                except Exception as exc:  # publication never escapes the relay
                    failed = (entry, (str(exc) or type(exc).__name__)[:1000])
                    telemetry.outbox_publish_failures.add(1, _POSTGRES_OUTBOX)
                    logger.warning(
                        "Event %s publish failed (attempt %s): %s",
                        entry.event.id,
                        entry.attempts + 1,
                        failed[1],
                    )
                    break
                published.append(entry.event.id)
            await asyncio.to_thread(self._finish, session, published, failed, rejected)
            result.published = len(published)
            result.failed = 1 if failed else 0
            result.rejected = len(rejected)
            self.published_count += result.published
            if result.published:
                telemetry.outbox_published.add(result.published, _POSTGRES_OUTBOX)
            self.rejected_count += result.rejected
            return result
        except BaseException:
            await asyncio.to_thread(session.rollback)
            raise
        finally:
            await asyncio.to_thread(session.close)

    async def drain(self, *, max_passes: int = 1000) -> int:
        """Relay until nothing due is left (or a publish fails). Returns events published."""
        total = 0
        for _ in range(max_passes):
            outcome = await self.relay_once()
            total += outcome.published
            if outcome.claimed < self.batch_size or outcome.failed:
                break
        return total

    # -- background loop --------------------------------------------------------

    def notify(self) -> None:
        """Wake the relay (called after a request commits events). Thread-safe."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:  # pragma: no cover - loop shutting down
            logger.debug("Outbox relay loop closed before wake-up", exc_info=True)

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="anum-outbox-relay")
        self._unregister_metrics = register_outbox_source("postgresql", self.backlog_snapshot)
        self._wake.set()  # relay whatever a previous process left behind

    async def stop(self) -> None:
        task, self._task = self._task, None
        self._stopping = True
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Nothing is lost: unpublished rows stay in PostgreSQL for the next relay.
        if self._unregister_metrics is not None:
            self._unregister_metrics()
            self._unregister_metrics = None
        self._loop = None
        self._wake = None

    async def _run(self) -> None:
        if self._wake is None:
            raise RuntimeError("Outbox relay started without a wake event")
        while not self._stopping:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass
            self._wake.clear()
            # Measured even while the bus is down: that is when the backlog grows.
            await self._maybe_refresh_backlog()
            if not self.bus.connected:
                continue  # do not burn retry attempts while the bus is down
            try:
                await self.drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Outbox relay pass failed")
                await asyncio.sleep(self.base_backoff)


def build_outbox_session_factory(settings: Any) -> Callable[[], Session]:
    """Session factory for the relay: ``ANUM_OUTBOX_DATABASE_URL`` or the API database."""
    if settings.outbox_database_url:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(settings.outbox_database_url, pool_pre_ping=True)
        return sessionmaker(bind=engine, autoflush=False, autocommit=False)
    from .db.session import SessionLocal

    return SessionLocal
