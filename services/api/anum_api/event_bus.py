"""Durable event publication for canonical ANUM events.

Events recorded through the repository are handed to an outbox once the
owning transaction commits. The outbox publishes them to the configured bus
(NATS JetStream in production) with the event id as the dedupe key, and keeps
retrying with backoff while the bus is unreachable. Publication never fails
the request that recorded the event: the repository remains the source of
truth and clients can always recover history from the REST API.

Subjects carry tenant context explicitly::

    anum.<tenant>.<workspace>.<event type>      workspace-scoped events
    anum.<tenant>.~.<event type>                tenant-wide events (no workspace)

Identifier tokens are escaped so that ids containing NATS subject syntax can
never widen a subscription or collide with another scope.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from .schemas import DomainEvent, utc_now

logger = logging.getLogger(__name__)

SUBJECT_ROOT = "anum"
TENANT_WIDE_TOKEN = "~"
DEFAULT_STREAM_NAME = "ANUM_EVENTS"
MSG_ID_HEADER = "Nats-Msg-Id"

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_\-]")
_EVENT_TYPE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")


# --------------------------------------------------------------------------- subjects


def encode_subject_token(value: str) -> str:
    """Encode an identifier as one NATS subject token, injectively.

    Characters outside ``[A-Za-z0-9_-]`` (including ``.``, ``*``, ``>``,
    whitespace and ``~``) are escaped as ``~xx`` hex bytes, so the result never
    contains subject separators or wildcards and never equals the bare
    tenant-wide marker ``~``.
    """

    if not value:
        raise ValueError("Subject tokens must not be empty")
    encoded: list[str] = []
    for character in value:
        if _SAFE_TOKEN.fullmatch(character):
            encoded.append(character)
        else:
            encoded.extend(f"~{byte:02x}" for byte in character.encode("utf-8"))
    return "".join(encoded)


def scope_tokens(tenant_id: str, workspace_id: str | None) -> tuple[str, str]:
    tenant_token = encode_subject_token(tenant_id)
    workspace_token = (
        encode_subject_token(workspace_id) if workspace_id is not None else TENANT_WIDE_TOKEN
    )
    return tenant_token, workspace_token


def event_subject(event: DomainEvent) -> str:
    if not _EVENT_TYPE.fullmatch(event.type):
        raise ValueError(f"Event type is not a valid subject suffix: {event.type!r}")
    tenant_token, workspace_token = scope_tokens(event.tenant_id, event.workspace_id)
    return f"{SUBJECT_ROOT}.{tenant_token}.{workspace_token}.{event.type}"


def scope_filter_subjects(tenant_id: str, workspace_id: str) -> tuple[str, str]:
    """Subjects a workspace member may observe: their workspace plus tenant-wide events."""

    tenant_token, workspace_token = scope_tokens(tenant_id, workspace_id)
    return (
        f"{SUBJECT_ROOT}.{tenant_token}.{workspace_token}.>",
        f"{SUBJECT_ROOT}.{tenant_token}.{TENANT_WIDE_TOKEN}.>",
    )


@dataclass(frozen=True)
class SubjectScope:
    tenant_token: str
    workspace_token: str
    event_type: str


def parse_subject(subject: str) -> SubjectScope | None:
    parts = subject.split(".")
    if len(parts) < 4 or parts[0] != SUBJECT_ROOT:
        return None
    return SubjectScope(parts[1], parts[2], ".".join(parts[3:]))


def subject_matches(pattern: str, subject: str) -> bool:
    """NATS wildcard matching (``*`` one token, ``>`` one or more trailing tokens)."""

    pattern_tokens = pattern.split(".")
    subject_tokens = subject.split(".")
    for index, token in enumerate(pattern_tokens):
        if token == ">":
            return len(subject_tokens) > index
        if index >= len(subject_tokens):
            return False
        if token != "*" and token != subject_tokens[index]:
            return False
    return len(pattern_tokens) == len(subject_tokens)


def encode_event(event: DomainEvent) -> bytes:
    return event.model_dump_json().encode("utf-8")


def decode_event(data: bytes) -> DomainEvent:
    return DomainEvent.model_validate_json(data)


# --------------------------------------------------------------------------- buses


@dataclass(frozen=True)
class BusMessage:
    subject: str
    data: bytes
    msg_id: str | None = None


MessageHandler = Callable[[BusMessage], Awaitable[None]]
Unsubscribe = Callable[[], Awaitable[None]]


class BusUnavailableError(RuntimeError):
    """Raised when the bus cannot accept a publish right now."""


class EventBus(Protocol):
    @property
    def connected(self) -> bool: ...

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def publish(self, subject: str, data: bytes, *, msg_id: str) -> None: ...

    async def subscribe(self, subject: str, handler: MessageHandler) -> Unsubscribe: ...


class InMemoryEventBus:
    """Deterministic in-process bus with JetStream-like dedupe, for tests and local use."""

    def __init__(self) -> None:
        self.available = True
        self.fail_publishes = 0
        self.messages: list[BusMessage] = []
        self.publish_attempts = 0
        self._seen_ids: set[str] = set()
        self._subscriptions: dict[int, tuple[str, MessageHandler]] = {}
        self._next_subscription = 0
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self.available

    async def connect(self) -> None:
        if not self.available:
            raise BusUnavailableError("in-memory bus is unavailable")
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def publish(self, subject: str, data: bytes, *, msg_id: str) -> None:
        self.publish_attempts += 1
        if not self.connected:
            raise BusUnavailableError("in-memory bus is unavailable")
        if self.fail_publishes > 0:
            self.fail_publishes -= 1
            raise BusUnavailableError("injected publish failure")
        if msg_id in self._seen_ids:
            return
        self._seen_ids.add(msg_id)
        message = BusMessage(subject=subject, data=data, msg_id=msg_id)
        self.messages.append(message)
        await self.deliver(message)

    async def deliver(self, message: BusMessage) -> None:
        """Deliver a raw message to matching subscribers (bypasses dedupe; for tests)."""

        for pattern, handler in list(self._subscriptions.values()):
            if subject_matches(pattern, message.subject):
                await handler(message)

    async def subscribe(self, subject: str, handler: MessageHandler) -> Unsubscribe:
        key = self._next_subscription
        self._next_subscription += 1
        self._subscriptions[key] = (subject, handler)

        async def unsubscribe() -> None:
            self._subscriptions.pop(key, None)

        return unsubscribe


class NatsJetStreamBus:
    """NATS JetStream adapter (``nats-py``)."""

    def __init__(
        self,
        url: str,
        *,
        stream_name: str = DEFAULT_STREAM_NAME,
        publish_timeout: float = 2.0,
        connect_timeout: float = 2.0,
        duplicate_window_seconds: float = 600.0,
        max_age_seconds: float = 7 * 24 * 3600,
    ) -> None:
        self.url = url
        self.stream_name = stream_name
        self.publish_timeout = publish_timeout
        self.connect_timeout = connect_timeout
        self.duplicate_window_seconds = duplicate_window_seconds
        self.max_age_seconds = max_age_seconds
        self._nc: Any = None
        self._js: Any = None

    @property
    def connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    async def connect(self) -> None:
        import nats
        from nats.js.api import RetentionPolicy, StorageType, StreamConfig
        from nats.js.errors import NotFoundError

        if self._nc is not None and not self._nc.is_closed:
            return
        nc = await nats.connect(
            self.url,
            connect_timeout=self.connect_timeout,
            allow_reconnect=True,
            max_reconnect_attempts=-1,
            reconnect_time_wait=1,
            name="anum-api",
            error_cb=self._on_error,
            disconnected_cb=self._on_disconnected,
            reconnected_cb=self._on_reconnected,
        )
        try:
            js = nc.jetstream(timeout=self.publish_timeout)
            config = StreamConfig(
                name=self.stream_name,
                subjects=[f"{SUBJECT_ROOT}.>"],
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.FILE,
                duplicate_window=self.duplicate_window_seconds,
                max_age=self.max_age_seconds,
            )
            try:
                await js.stream_info(self.stream_name)
                await js.update_stream(config)
            except NotFoundError:
                await js.add_stream(config)
        except Exception:
            await nc.close()
            raise
        self._nc = nc
        self._js = js

    async def _on_error(self, exc: Exception) -> None:
        logger.warning("NATS connection error: %s", exc or type(exc).__name__)

    async def _on_disconnected(self) -> None:
        logger.warning("NATS disconnected; events will queue until it reconnects")

    async def _on_reconnected(self) -> None:
        logger.info("NATS reconnected")

    async def close(self) -> None:
        nc, self._nc, self._js = self._nc, None, None
        if nc is not None and not nc.is_closed:
            try:
                await nc.drain()
            except Exception:  # pragma: no cover - best effort on shutdown
                await nc.close()

    async def publish(self, subject: str, data: bytes, *, msg_id: str) -> None:
        if self._js is None or not self.connected:
            raise BusUnavailableError("NATS is not connected")
        await self._js.publish(
            subject,
            data,
            timeout=self.publish_timeout,
            stream=self.stream_name,
            headers={MSG_ID_HEADER: msg_id},
        )

    async def subscribe(self, subject: str, handler: MessageHandler) -> Unsubscribe:
        from nats.js.api import DeliverPolicy

        if self._js is None:
            raise BusUnavailableError("NATS is not connected")

        async def callback(msg: Any) -> None:
            headers = msg.headers or {}
            await handler(
                BusMessage(subject=msg.subject, data=msg.data, msg_id=headers.get(MSG_ID_HEADER))
            )

        subscription = await self._js.subscribe(
            subject,
            cb=callback,
            stream=self.stream_name,
            ordered_consumer=True,
            deliver_policy=DeliverPolicy.NEW,
        )

        async def unsubscribe() -> None:
            try:
                await subscription.unsubscribe()
            except Exception:  # pragma: no cover - connection already gone
                logger.debug("Ignoring unsubscribe failure", exc_info=True)

        return unsubscribe


# --------------------------------------------------------------------------- outbox


@dataclass
class OutboxEntry:
    event: DomainEvent
    attempts: int = 0
    available_at: datetime = field(default_factory=utc_now)
    last_error: str | None = None


class EventOutbox:
    """In-process outbox delivering committed events at least once.

    Entries stay queued until the bus acknowledges them. A failed publish
    schedules the entry again with exponential backoff; the remaining entries
    wait for the next pass so a down bus is not hammered. The queue is bounded:
    beyond ``max_pending`` the oldest entries are dropped and counted, and
    clients recover them from the persisted event history.
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        clock: Callable[[], datetime] = utc_now,
        base_backoff: float = 0.5,
        max_backoff: float = 30.0,
        max_pending: int = 10_000,
    ) -> None:
        self.bus = bus
        self._clock = clock
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.max_pending = max_pending
        self._entries: OrderedDict[str, OutboxEntry] = OrderedDict()
        self._lock = threading.Lock()
        self.published_count = 0
        self.dropped_count = 0
        self.rejected_count = 0
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def pending(self) -> tuple[OutboxEntry, ...]:
        with self._lock:
            return tuple(self._entries.values())

    def enqueue(self, events: Iterable[DomainEvent]) -> None:
        now = self._clock()
        with self._lock:
            for event in events:
                if event.id in self._entries:
                    continue
                self._entries[event.id] = OutboxEntry(event=event, available_at=now)
            while len(self._entries) > self.max_pending:
                dropped_id, _ = self._entries.popitem(last=False)
                self.dropped_count += 1
                logger.warning("Event outbox full; dropped event %s from publication", dropped_id)
        self._notify()

    def backoff_for(self, attempts: int) -> float:
        return min(self.max_backoff, self.base_backoff * (2 ** max(0, attempts - 1)))

    async def publish_due(self) -> int:
        """Publish every due entry once, in order. Returns how many were acknowledged."""

        published = 0
        now = self._clock()
        for entry in self.pending:
            if entry.available_at > now:
                continue
            try:
                subject, data = event_subject(entry.event), encode_event(entry.event)
            except ValueError:
                # Not publishable at all; retrying cannot help and would block the queue.
                with self._lock:
                    self._entries.pop(entry.event.id, None)
                self.rejected_count += 1
                logger.exception("Event %s cannot be published; dropped", entry.event.id)
                continue
            try:
                await self.bus.publish(subject, data, msg_id=entry.event.id)
            except Exception as exc:  # publication must never escape to callers
                self._reschedule(entry, exc)
                break
            with self._lock:
                self._entries.pop(entry.event.id, None)
            self.published_count += 1
            published += 1
        return published

    def retry_now(self) -> None:
        """Make every queued entry due immediately (e.g. after the bus reconnects)."""

        now = self._clock()
        with self._lock:
            for entry in self._entries.values():
                entry.available_at = min(entry.available_at, now)
        self._notify()

    def next_due_in(self) -> float | None:
        entries = self.pending
        if not entries:
            return None
        earliest = min(entry.available_at for entry in entries)
        return max(0.0, (earliest - self._clock()).total_seconds())

    def _reschedule(self, entry: OutboxEntry, exc: Exception) -> None:
        attempts = entry.attempts + 1
        delay = self.backoff_for(attempts)
        message = (str(exc) or type(exc).__name__)[:1000]
        with self._lock:
            current = self._entries.get(entry.event.id)
            if current is None:
                return
            current.attempts = attempts
            current.last_error = message
            current.available_at = self._clock() + timedelta(seconds=delay)
        logger.warning(
            "Event %s publish failed (attempt %s); retrying in %.1fs: %s",
            entry.event.id,
            attempts,
            delay,
            message,
        )

    # -- background delivery -------------------------------------------------

    def _notify(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:  # pragma: no cover - loop shutting down
            logger.debug("Outbox loop closed before wake-up", exc_info=True)

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="anum-event-outbox")
        if self.pending:
            self._wake.set()

    async def stop(self, *, flush_timeout: float = 2.0) -> None:
        task, self._task = self._task, None
        self._stopping = True
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.pending and self.bus.connected:
            try:
                await asyncio.wait_for(self.publish_due(), timeout=flush_timeout)
            except Exception:  # pragma: no cover - best effort on shutdown
                logger.warning("Final outbox flush failed", exc_info=True)
        if self.pending:
            logger.warning("Event outbox stopped with %s unpublished events", len(self.pending))
        self._loop = None
        self._wake = None

    async def _run(self) -> None:
        assert self._wake is not None
        while not self._stopping:
            delay = self.next_due_in()
            if delay is None or delay > 0:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except TimeoutError:
                    pass
            self._wake.clear()
            try:
                await self.publish_due()
            except Exception:  # pragma: no cover - defensive; publish_due swallows errors
                logger.exception("Event outbox pass failed")
                await asyncio.sleep(self.base_backoff)


# --------------------------------------------------------------------------- repository hook


class EventCollectingRepository:
    """Repository proxy that remembers events recorded during one unit of work."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.recorded_events: list[DomainEvent] = []

    def record_event(self, event: DomainEvent) -> DomainEvent:
        stored = self._inner.record_event(event)
        self.recorded_events.append(stored)
        return stored

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# --------------------------------------------------------------------------- runtime


class EventBusRuntime:
    """Process-wide wiring for the selected bus, its outbox and the realtime hub."""

    def __init__(
        self,
        mode: str,
        bus: EventBus | None = None,
        *,
        reconnect_backoff: float = 1.0,
        max_reconnect_backoff: float = 30.0,
    ) -> None:
        from .realtime import RealtimeHub

        if mode not in {"memory", "nats"}:
            raise ValueError(f"Unsupported event bus: {mode}")
        if mode == "nats" and bus is None:
            raise ValueError("NATS event bus requires a bus adapter")
        self.mode = mode
        self.bus = bus
        self.outbox = EventOutbox(bus) if bus is not None else None
        self.hub = RealtimeHub()
        self.reconnect_backoff = reconnect_backoff
        self.max_reconnect_backoff = max_reconnect_backoff
        self._supervisor: asyncio.Task[None] | None = None
        self._hub_unsubscribe: Unsubscribe | None = None

    @property
    def live(self) -> bool:
        """Whether realtime streams are fed from the bus instead of repository polling."""

        return self.mode == "nats"

    def after_commit(self, events: Sequence[DomainEvent]) -> None:
        if self.outbox is None or not events:
            return
        try:
            self.outbox.enqueue(events)
        except Exception:  # publication must never fail the request
            logger.exception("Could not enqueue committed events for publication")

    async def start(self) -> None:
        if self.bus is None or self.outbox is None:
            return
        self.outbox.start()
        self._supervisor = asyncio.create_task(self._connect_loop(), name="anum-event-bus")

    async def stop(self) -> None:
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is not None:
            supervisor.cancel()
            try:
                await supervisor
            except asyncio.CancelledError:
                pass
        if self.outbox is not None:
            await self.outbox.stop()
        if self._hub_unsubscribe is not None:
            await self._hub_unsubscribe()
            self._hub_unsubscribe = None
        self.hub.set_live(False)
        if self.bus is not None:
            await self.bus.close()

    async def _connect_loop(self) -> None:
        assert self.bus is not None and self.outbox is not None
        delay = self.reconnect_backoff
        while True:
            try:
                await self.bus.connect()
                if self._hub_unsubscribe is None:
                    self._hub_unsubscribe = await self.bus.subscribe(
                        f"{SUBJECT_ROOT}.>", self.hub.dispatch
                    )
                self.hub.set_live(True)
                self.outbox.retry_now()  # flush the backlog without waiting out backoff
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Event bus unavailable (%s); retrying in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(self.max_reconnect_backoff, delay * 2)
        # nats-py reconnects on its own after the first connection; mirror its state.
        while True:
            connected = self.bus.connected
            if connected and not self.hub.live:
                self.outbox.retry_now()
            self.hub.set_live(connected)
            await asyncio.sleep(1.0)


def build_event_runtime(settings: Any) -> EventBusRuntime:
    mode = (settings.event_bus or "memory").strip().lower()
    if mode == "nats":
        return EventBusRuntime(
            "nats", NatsJetStreamBus(settings.nats_url, stream_name=settings.nats_stream)
        )
    return EventBusRuntime(mode)

