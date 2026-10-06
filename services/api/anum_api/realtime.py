"""Realtime fan-out of bus events to tenant- and workspace-scoped listeners.

One bus subscription per API process feeds the :class:`RealtimeHub`. The hub
routes each message only to listeners whose tenant and workspace match both
the subject tokens and the decoded event, so a forged or mis-routed message
can never reach another tenant's or workspace's stream.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager

from .event_bus import TENANT_WIDE_TOKEN, BusMessage, decode_event, parse_subject, scope_tokens
from .schemas import DomainEvent, TenantContext

logger = logging.getLogger(__name__)


def event_visible_to(event: DomainEvent, context: TenantContext) -> bool:
    return event.tenant_id == context.tenant_id and (
        event.workspace_id is None or event.workspace_id == context.workspace_id
    )


class RealtimeListener:
    def __init__(self, context: TenantContext, *, max_queue: int = 1000) -> None:
        self.context = context
        self.tenant_token, self.workspace_token = scope_tokens(
            context.tenant_id, context.workspace_id
        )
        self._queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=max_queue)
        self.lagged = False

    def offer(self, event: DomainEvent) -> None:
        if not event_visible_to(event, self.context):
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # The stream catches up from the persisted history instead.
            self.lagged = True

    async def next(self, timeout: float) -> DomainEvent | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    def drain(self) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        while not self._queue.empty():
            events.append(self._queue.get_nowait())
        return events


class RealtimeHub:
    def __init__(self) -> None:
        self._listeners: dict[tuple[str, str], set[RealtimeListener]] = {}
        self.live = False
        self.dropped_messages = 0

    def set_live(self, live: bool) -> None:
        self.live = live

    @property
    def listener_count(self) -> int:
        return sum(len(listeners) for listeners in self._listeners.values())

    @asynccontextmanager
    async def listen(
        self, context: TenantContext, *, max_queue: int = 1000
    ) -> AsyncIterator[RealtimeListener]:
        listener = RealtimeListener(context, max_queue=max_queue)
        key = (listener.tenant_token, listener.workspace_token)
        self._listeners.setdefault(key, set()).add(listener)
        try:
            yield listener
        finally:
            listeners = self._listeners.get(key)
            if listeners is not None:
                listeners.discard(listener)
                if not listeners:
                    self._listeners.pop(key, None)

    async def dispatch(self, message: BusMessage) -> None:
        scope = parse_subject(message.subject)
        if scope is None:
            self._drop(message, "unrecognised subject")
            return
        try:
            event = decode_event(message.data)
        except ValueError:
            self._drop(message, "undecodable payload")
            return
        tenant_token, workspace_token = scope_tokens(event.tenant_id, event.workspace_id)
        if (
            scope.tenant_token != tenant_token
            or scope.workspace_token != workspace_token
            or scope.event_type != event.type
            or (message.msg_id is not None and message.msg_id != event.id)
        ):
            self._drop(message, "subject does not match event scope")
            return

        if workspace_token == TENANT_WIDE_TOKEN:
            targets: Iterable[RealtimeListener] = [
                listener
                for (tenant, _), listeners in self._listeners.items()
                if tenant == tenant_token
                for listener in listeners
            ]
        else:
            targets = list(self._listeners.get((tenant_token, workspace_token), ()))
        for listener in targets:
            listener.offer(event)

    def _drop(self, message: BusMessage, reason: str) -> None:
        self.dropped_messages += 1
        logger.warning("Dropped realtime message on %s: %s", message.subject, reason)


class _RecentIds:
    def __init__(self, limit: int = 10_000) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._limit = limit

    def add(self, event_id: str) -> None:
        self._ids[event_id] = None
        self._ids.move_to_end(event_id)
        while len(self._ids) > self._limit:
            self._ids.popitem(last=False)

    def __contains__(self, event_id: object) -> bool:
        return event_id in self._ids


def format_sse(event: DomainEvent) -> str:
    return f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


async def live_event_source(
    *,
    hub: RealtimeHub,
    context: TenantContext,
    list_events: Callable[[], list[DomainEvent]],
    is_disconnected: Callable[[], Awaitable[bool]],
    task_id: str | None = None,
    follow: bool = True,
    last_event_id: str | None = None,
    keepalive_seconds: float = 15.0,
    fallback_poll_seconds: float = 1.0,
) -> AsyncIterator[str]:
    """SSE stream fed by the bus, with persisted-history replay and catch-up.

    The listener is registered before the history replay so no event can fall
    between the two; duplicates (at-least-once delivery, replay overlap) are
    suppressed by event id. When the bus is unavailable or this listener
    overflowed, the stream catches up from the repository instead.
    """

    def matches(event: DomainEvent) -> bool:
        if not event_visible_to(event, context):
            return False
        return task_id is None or event.subject == task_id or event.payload.get("task_id") == task_id

    seen = _RecentIds()
    repository_cursor = last_event_id

    def catch_up() -> list[DomainEvent]:
        nonlocal repository_cursor
        events = list_events()
        if repository_cursor:
            index = next(
                (i for i, event in enumerate(events) if event.id == repository_cursor), None
            )
            if index is not None:
                events = events[index + 1 :]
        if events:
            repository_cursor = events[-1].id
        fresh = [event for event in events if matches(event) and event.id not in seen]
        for event in fresh:
            seen.add(event.id)
        return fresh

    async with hub.listen(context) as listener:
        for event in catch_up():
            yield format_sse(event)
        if not follow:
            return

        idle = 0.0
        while not await is_disconnected():
            if listener.lagged or not hub.live:
                listener.lagged = False
                listener.drain()
                caught_up = catch_up()
                for event in caught_up:
                    yield format_sse(event)
                if caught_up:
                    idle = 0.0
            wait = keepalive_seconds - idle if hub.live else fallback_poll_seconds
            event = await listener.next(timeout=max(0.05, min(wait, keepalive_seconds)))
            if event is None:
                idle += wait if hub.live else fallback_poll_seconds
                if idle >= keepalive_seconds:
                    idle = 0.0
                    yield ": keep-alive\n\n"
                continue
            if matches(event) and event.id not in seen:
                seen.add(event.id)
                idle = 0.0
                yield format_sse(event)


__all__ = [
    "RealtimeHub",
    "RealtimeListener",
    "event_visible_to",
    "format_sse",
    "live_event_source",
]
