"""Wake-ups for the in-app call stream (docs/in-app-calls.md#signaling).

The call stream's source of truth stays the database: every frame it sends comes from a
read of the participant's calls and signals, in its own short session in a worker thread.
What the event bus adds is *when* to read. Every change a participant's stream must show
records a canonical event in the same transaction as the change (so with PostgreSQL it is
a durable outbox row, published by the relay):

- ``app_call.ringing``: a call was placed (the call id and the person rung).
- ``app_call.updated``: a call was answered or ended (the call id only).
- ``app_call.signaled``: an offer, answer or ICE batch was stored (the call id only).

The events carry ids only, never a session description, a candidate, a device id or the
caller's id. :class:`CallWakeHub` hands them to the call streams of the event's tenant and
workspace, which then re-read at once instead of waiting for their next poll. A wake-up
that reaches a stream it does not concern costs one read and shows nothing: the read runs
under the participant's row policies.

Two feeds, one per bus mode:

- ``ANUM_EVENT_BUS=nats``: the process's realtime hub (one JetStream consumer per API
  process, ``realtime.RealtimeHub``) passes every validated event to :meth:`observe`, so a
  change committed on any replica wakes the streams on every replica.
- ``ANUM_EVENT_BUS=memory``, or while NATS is down: committed events reach
  :meth:`submit` in the committing process (an in-process bus). The stream keeps its
  normal poll as well, so a change committed by another replica still shows on the next
  poll.

The poll is the fallback either way: with NATS live it slows to
``ANUM_CALLS_BUS_FALLBACK_POLL_SECONDS``, and a lost wake-up only delays a frame until then.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from .schemas import DomainEvent, TenantContext

logger = logging.getLogger("anum.app_calls")

RINGING = "app_call.ringing"
UPDATED = "app_call.updated"
SIGNALED = "app_call.signaled"
CALL_WAKE_EVENTS = frozenset({RINGING, UPDATED, SIGNALED})
# Wake-ups only: the workspace event stream and list leave these out, so open tabs do not
# reload their tasks for every ICE batch (``app_call.ringing`` stays: it is a push trigger).
WORKSPACE_HIDDEN_EVENTS = frozenset({UPDATED, SIGNALED})
MAX_PENDING = 256


def visible_on_workspace_stream(event: DomainEvent) -> bool:
    return event.type not in WORKSPACE_HIDDEN_EVENTS


@dataclass
class CallWake:
    """What woke a stream: the calls that changed, whether a call rang, or an overflow."""

    call_ids: set[str] = field(default_factory=set)
    ringing: bool = False
    overflowed: bool = False

    def concerns(self, known_call_ids: Iterable[str]) -> bool:
        return self.overflowed or self.ringing or not self.call_ids.isdisjoint(known_call_ids)


class CallWakeListener:
    """One call stream's wake-ups. ``offer`` and ``wait`` run on the stream's event loop."""

    def __init__(self, context: TenantContext, loop: asyncio.AbstractEventLoop, *, max_pending: int = MAX_PENDING) -> None:
        self.context = context
        self.loop = loop
        self.max_pending = max_pending
        self._wake = CallWake()
        self._flag = asyncio.Event()

    def accepts(self, event: DomainEvent) -> bool:
        return (
            event.type in CALL_WAKE_EVENTS
            and event.tenant_id == self.context.tenant_id
            and event.workspace_id == self.context.workspace_id
        )

    def offer(self, event: DomainEvent) -> None:
        if not self.accepts(event):
            return
        if event.type == RINGING:
            self._wake.ringing = True
        call_id = event.payload.get("call_id") or event.subject
        if isinstance(call_id, str) and call_id:
            if len(self._wake.call_ids) >= self.max_pending:
                self._wake.overflowed = True  # read anyway; nothing is lost
            else:
                self._wake.call_ids.add(call_id)
        self._flag.set()

    async def wait(self, timeout: float) -> CallWake | None:
        """The wake-ups since the last call, or ``None`` when ``timeout`` passes first."""
        if not self._flag.is_set():
            try:
                await asyncio.wait_for(self._flag.wait(), timeout=max(0.0, timeout))
            except TimeoutError:
                return None
        self._flag.clear()
        wake, self._wake = self._wake, CallWake()
        return wake


class CallWakeHub:
    """Per-process registry of call streams, keyed by tenant and workspace."""

    def __init__(self, bus_live: Callable[[], bool] | None = None) -> None:
        self._bus_live = bus_live or (lambda: False)
        self._listeners: dict[tuple[str, str], set[CallWakeListener]] = {}
        self._lock = threading.Lock()
        self.local_wakes = 0
        self.bus_wakes = 0

    @property
    def live(self) -> bool:
        """Whether wake-ups arrive from the shared bus (NATS), so the poll can slow down."""
        try:
            return bool(self._bus_live())
        except Exception:  # pragma: no cover - a broken probe must never end a stream
            return False

    @property
    def listener_count(self) -> int:
        with self._lock:
            return sum(len(listeners) for listeners in self._listeners.values())

    def attach(self, runtime: Any) -> None:
        """Feed this hub from an ``event_bus.EventBusRuntime``: its realtime hub (NATS) and
        its committed-event listeners (in-process)."""
        self._bus_live = lambda: bool(runtime.hub.live)
        runtime.hub.add_observer(self.observe)
        runtime.add_listener(self.submit)

    @asynccontextmanager
    async def listen(self, context: TenantContext) -> AsyncIterator[CallWakeListener]:
        if context.workspace_id is None:
            raise ValueError("A call stream needs a workspace")
        listener = CallWakeListener(context, asyncio.get_running_loop())
        key = (context.tenant_id, context.workspace_id)
        with self._lock:
            self._listeners.setdefault(key, set()).add(listener)
        try:
            yield listener
        finally:
            with self._lock:
                listeners = self._listeners.get(key)
                if listeners is not None:
                    listeners.discard(listener)
                    if not listeners:
                        self._listeners.pop(key, None)

    def _targets(self, event: DomainEvent) -> list[CallWakeListener]:
        if event.type not in CALL_WAKE_EVENTS or event.workspace_id is None:
            return []
        with self._lock:
            return list(self._listeners.get((event.tenant_id, event.workspace_id), ()))

    def observe(self, event: DomainEvent) -> None:
        """From the realtime hub, on the event loop, for an event it has validated (NATS)."""
        for listener in self._targets(event):
            self.bus_wakes += 1
            listener.offer(event)

    def submit(self, events: Sequence[DomainEvent]) -> None:
        """Committed-event listener: runs in the committing thread, only schedules wake-ups.

        Skipped while the shared bus is live: the same events then arrive through
        :meth:`observe` on every replica, and waking twice would read twice.
        """
        if self.live:
            return
        for event in events:
            for listener in self._targets(event):
                try:
                    listener.loop.call_soon_threadsafe(listener.offer, event)
                    self.local_wakes += 1
                except RuntimeError:  # the stream's loop has closed; its poll is gone too
                    logger.debug("app_call_wake_loop_closed")


call_wake_hub = CallWakeHub()

__all__ = [
    "CALL_WAKE_EVENTS",
    "CallWake",
    "CallWakeHub",
    "CallWakeListener",
    "WORKSPACE_HIDDEN_EVENTS",
    "call_wake_hub",
    "visible_on_workspace_stream",
]
