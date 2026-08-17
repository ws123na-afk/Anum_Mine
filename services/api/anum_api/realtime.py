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
import json
import logging
from collections.abc import AsyncIterator

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

    persisted = repository.list_events(context)
    backlog = _events_after_cursor(persisted, cursor)
    last_seen = backlog[-1].id if backlog else cursor
    return backlog, last_seen


async def _poll_for_new_events(
    repository: AnumRepository,
    context: TenantContext,
    request: Request,
    last_seen: str | None,
) -> AsyncIterator[DomainEvent]:
    """Fallback live path: no NATS configured, poll the persisted event log."""

    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        if await request.is_disconnected():
            return
        persisted = repository.list_events(context)
        new_events = _events_after_cursor(persisted, last_seen)
        for event in new_events:
            last_seen = event.id
            yield event


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
        _subscribe_nats_for_new_events(context, request)
        if settings.nats_url
        else _poll_for_new_events(repository, context, request, last_seen)
    )
    try:
        async for event in live_source:
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
