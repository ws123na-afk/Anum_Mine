"""Task-level SSE realtime endpoint (docs/realtime.md "Now" scope).

`GET /api/v1/events/stream` streams this tenant/workspace's domain events as
Server-Sent Events. It always replays persisted events newer than the
client's cursor first (so a reconnecting client recovers recent events
instead of losing context - see docs/realtime.md), then keeps the connection
open and forwards new events as they happen:

- If `settings.nats_url` is configured, new events are delivered live by
  subscribing to this tenant's JetStream subjects
  (`anum.events.<tenant_id>.>`, see anum_api/events_nats.py).
- Otherwise (NATS not configured - the default), the endpoint falls back to
  polling `repository.list_events(context)` on a short interval. This keeps
  the "Now" scope's promise that realtime works with zero external
  dependencies; NATS only improves latency/efficiency once configured.

Authorization mirrors the existing `GET /api/v1/events` endpoint exactly:
the same `tenant_context` dependency and `Permission.EVENT_READ` gate, so a
client can only ever stream its own tenant+workspace's events - see
`_visible_to_context` below and its dedicated cross-tenant test.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Iterator

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from .authorization import Permission
from .dependencies import repository_context, require_permission, tenant_context
from .events_nats import event_subject
from .repository import AnumRepository
from .schemas import DomainEvent, TenantContext
from .settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# How often the polling fallback re-checks the repository's persisted event
# log for new events, and how often the NATS path re-checks for client
# disconnect between messages. Deliberately short for a snappy "Now"-scope
# implementation; see the module docstring for the tradeoffs.
POLL_INTERVAL_SECONDS = 1.0
NATS_NEXT_MSG_TIMEOUT_SECONDS = 1.0

# When NATS is configured, new events still also arrive via this slower
# reconciliation poll (see _merge_streams / event_stream below) - AgentRuntime
# and the Temporal activities only ever write to the repository, not NATS
# (anum_api/runtime.py's _record_event, workflows/activities.py's
# repository.record_event), so a NATS-only live path would silently miss
# every run step / approval-requested event until the client's next
# reconnect replays the backlog. This keeps "live" actually live for those
# too, at a coarser interval than the no-NATS fallback above.
RECONCILE_INTERVAL_SECONDS = 5.0


@contextlib.contextmanager
def _repository_for_read(context: TenantContext, fallback: AnumRepository) -> Iterator[AnumRepository]:
    """A repository to read with, scoped to exactly this one call.

    On the PostgreSQL backend, `fallback` (the request's own
    dependency-injected repository) is backed by a session that FastAPI
    closes - committing it, which clears the `SET LOCAL anum.tenant_id`/
    `anum.workspace_id` settings `set_tenant_context` established - as soon
    as the route handler returns its `StreamingResponse` object. That
    happens *before* Starlette ever starts iterating this module's
    generators to send the response body, so by the time any of them would
    actually run a query, `fallback`'s session is already committed and
    closed: queries through it still succeed (SQLAlchemy opens a fresh
    transaction) but run with no tenant context, and the `domain_events` RLS
    policy then filters out every row.  Opening a short-lived session here
    instead - used for exactly one read and then closed - keeps each query's
    tenant context correct regardless of the request's own session
    lifecycle. The in-memory backend has no sessions/RLS to worry about, so
    `fallback` (the same singleton every caller shares) is used as-is there.
    """

    if settings.repository_backend != "postgresql":
        yield fallback
        return

    from .db.repository import SqlAlchemyRepository
    from .db.session import SessionLocal, set_tenant_context

    session = SessionLocal()
    try:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        yield SqlAlchemyRepository(session, created_by_user_id=context.user_id)
    finally:
        session.close()


def _visible_to_context(event: DomainEvent, context: TenantContext) -> bool:
    """Same tenant/workspace scoping rule as InMemoryRepository.list_events."""

    return event.tenant_id == context.tenant_id and (
        event.workspace_id is None or event.workspace_id == context.workspace_id
    )


def _events_after_cursor(
    events: list[DomainEvent], cursor: str | None
) -> list[DomainEvent]:
    """Return events strictly after `cursor` (by event id).

    No cursor means "nothing consumed yet" - replay everything currently
    persisted, matching realtime.md's "recover recent events... rather than
    losing context" for a client's very first connection. If a cursor is
    given but doesn't match any known event (e.g. stale/pruned), we also
    fall back to replaying everything rather than silently dropping events -
    losing a few duplicates client-side is preferable to losing history.
    """

    if cursor is None:
        return events
    for index, event in enumerate(events):
        if event.id == cursor:
            return events[index + 1 :]
    return events


def format_sse_event(event: DomainEvent) -> str:
    """Render one domain event as a single SSE frame (id/event/data + blank line)."""

    data = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n"


async def _replay_backlog(
    repository: AnumRepository, context: TenantContext, cursor: str | None
) -> tuple[list[DomainEvent], str | None]:
    """Return (backlog events to emit, id of the last event now seen)."""

    with _repository_for_read(context, repository) as repo:
        persisted = repo.list_events(context)
    backlog = _events_after_cursor(persisted, cursor)
    last_seen = backlog[-1].id if backlog else cursor
    return backlog, last_seen


async def _poll_for_new_events(
    repository: AnumRepository,
    context: TenantContext,
    request: Request,
    last_seen: str | None,
    *,
    interval_seconds: float | None = None,
) -> AsyncIterator[DomainEvent]:
    """Live path: poll the persisted event log for anything new.

    Used as the only live path when no NATS is configured (at
    POLL_INTERVAL_SECONDS), and as a slower reconciliation poll alongside
    the NATS live path otherwise (at RECONCILE_INTERVAL_SECONDS, via
    `interval_seconds` - see event_stream).
    """

    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(interval_seconds if interval_seconds is not None else POLL_INTERVAL_SECONDS)
        if await request.is_disconnected():
            return
        with _repository_for_read(context, repository) as repo:
            persisted = repo.list_events(context)
        new_events = _events_after_cursor(persisted, last_seen)
        for event in new_events:
            last_seen = event.id
            yield event


async def _merge_streams(*sources: AsyncIterator[DomainEvent]) -> AsyncIterator[DomainEvent]:
    """Interleave events from several sources as they arrive, until all are
    exhausted (or this generator itself is closed early).

    Used to combine the NATS live path with the slower reconciliation poll
    (see RECONCILE_INTERVAL_SECONDS) without either one waiting on the
    other. Closing this generator (e.g. the client disconnected) cancels
    whichever source is still mid-`__anext__` and closes every source in
    turn, so each one's own cleanup (e.g. the NATS path's unsubscribe/drain)
    still runs.
    """

    pending: dict[asyncio.Task[DomainEvent], AsyncIterator[DomainEvent]] = {
        asyncio.ensure_future(anext(source)): source for source in sources
    }
    try:
        while pending:
            done, _ = await asyncio.wait(pending.keys(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                source = pending.pop(task)
                try:
                    result = task.result()
                except StopAsyncIteration:
                    continue
                pending[asyncio.ensure_future(anext(source))] = source
                yield result
    finally:
        for task in pending:
            task.cancel()
        for source in {*pending.values()}:
            with contextlib.suppress(Exception):
                await source.aclose()


async def _subscribe_nats_for_new_events(
    context: TenantContext,
    request: Request,
) -> AsyncIterator[DomainEvent]:
    """Live path: subscribe to this tenant's JetStream subjects for new events.

    Only events newer than "now" are delivered here (DeliverPolicy.NEW) -
    everything older was already replayed from the repository's persisted
    log by the caller, so there is no double-delivery of history.
    """

    import nats
    from nats.js.api import ConsumerConfig, DeliverPolicy

    from .events_nats import ensure_stream

    assert settings.nats_url is not None
    client = await nats.connect(settings.nats_url)
    try:
        jetstream = client.jetstream()
        # A client can open this SSE endpoint before any event has ever been
        # published on a fresh deployment - ensure the stream exists rather
        # than assuming NatsEventPublisher.connect() already ran elsewhere.
        await ensure_stream(jetstream, settings.nats_stream_name)
        tenant_subject = f"anum.events.{context.tenant_id}.>"
        subscription = await jetstream.subscribe(
            tenant_subject,
            stream=settings.nats_stream_name,
            config=ConsumerConfig(deliver_policy=DeliverPolicy.NEW),
        )
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    message = await subscription.next_msg(timeout=NATS_NEXT_MSG_TIMEOUT_SECONDS)
                except TimeoutError:
                    # nats.errors.TimeoutError subclasses the builtin - this
                    # is the expected "nothing new yet" case, not a failure.
                    continue
                try:
                    event = DomainEvent.model_validate_json(message.data)
                except Exception:  # pragma: no cover - defensive against malformed payloads
                    logger.warning("Discarding malformed event on subject %s", message.subject)
                    continue
                if _visible_to_context(event, context):
                    yield event
        finally:
            await subscription.unsubscribe()
    finally:
        await client.drain()


async def event_stream(
    request: Request,
    context: TenantContext,
    repository: AnumRepository,
    cursor: str | None,
) -> AsyncIterator[str]:
    """The SSE generator itself - kept free of FastAPI/StreamingResponse glue
    so tests can drive it directly without depending on TestClient's
    streaming support.
    """

    backlog, last_seen = await _replay_backlog(repository, context, cursor)
    for event in backlog:
        yield format_sse_event(event)

    if await request.is_disconnected():
        return

    live_source = (
        _merge_streams(
            _subscribe_nats_for_new_events(context, request),
            # Reconciliation poll: AgentRuntime/Temporal activities only
            # ever write to the repository, not NATS, so the NATS path
            # alone would miss their events until the next reconnect
            # replays the backlog - see RECONCILE_INTERVAL_SECONDS above.
            _poll_for_new_events(
                repository, context, request, last_seen, interval_seconds=RECONCILE_INTERVAL_SECONDS
            ),
        )
        if settings.nats_url
        else _poll_for_new_events(repository, context, request, last_seen)
    )
    seen_ids = {event.id for event in backlog}
    try:
        async for event in live_source:
            # The two merged sources above can both independently observe
            # the same persisted event; only emit it once.
            if event.id in seen_ids:
                continue
            seen_ids.add(event.id)
            yield format_sse_event(event)
    finally:
        # If our consumer (StreamingResponse on a real client disconnect, or
        # a test) stops iterating early, closing here propagates GeneratorExit
        # into `live_source` so its own try/finally (NATS unsubscribe/drain)
        # actually runs instead of leaking a connection. `async for` alone
        # would not do this automatically - it only owns the outer frame.
        await live_source.aclose()


@router.get("/api/v1/events/stream")
async def stream_events(
    request: Request,
    cursor: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> StreamingResponse:
    require_permission(context, Permission.EVENT_READ)
    resolved_cursor = cursor or last_event_id
    return StreamingResponse(
        event_stream(request, context, repository, resolved_cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disable nginx-style response buffering so events reach the
            # client as they're yielded, not batched at the end.
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router", "event_stream", "format_sse_event", "event_subject"]
