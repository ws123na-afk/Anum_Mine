"""NATS JetStream publisher for ANUM domain events.

Phase 2 "Now" scope (see docs/automation.md): "NATS JetStream should carry
domain events that notify clients, workers, and integrations." This module
wraps nats-py's JetStream client in a small async-native publisher.

This is deliberately NOT wired into the synchronous `EventPublisher` Protocol
in anum_api/events.py - that Protocol/InMemoryEventPublisher pair isn't used
in any live request path today (see that module's docstring context). The
request handlers in main.py are already `async def`, so the rest of this
session can simply `await NatsEventPublisher.publish(event)` directly after
`repository.record_event(event)`, with no adapter layer needed.

Subject naming: `anum.events.<tenant_id>.<event_type>`, e.g.
`anum.events.tenant_a.task.created`. This lets subscribers filter by tenant
(`anum.events.tenant_a.>`), by event type across tenants (`anum.events.*.task.created`),
or take everything (`anum.events.>`). The JetStream stream itself is
configured with the wildcard `anum.events.>` so it captures every subject
under this publisher's control.
"""

from __future__ import annotations

import asyncio
import logging

import nats
from nats.aio.client import Client as NatsClient
from nats.js.api import StreamConfig
from nats.js.client import JetStreamContext

from .schemas import DomainEvent
from .settings import settings

logger = logging.getLogger(__name__)

STREAM_SUBJECT_WILDCARD_SUFFIX = ">"


def event_subject(event: DomainEvent) -> str:
    """Build the JetStream subject a given domain event is published to."""

    return f"anum.events.{event.tenant_id}.{event.type}"


def stream_subject_wildcard(stream_name: str | None = None) -> str:
    """The wildcard subject the stream is configured to capture.

    `stream_name` is accepted (but unused beyond documentation intent) so
    callers reading this alongside `settings.nats_stream_name` see the
    relationship explicitly; the wildcard itself is a fixed subject prefix,
    not derived from the stream's name.
    """

    return f"anum.events.{STREAM_SUBJECT_WILDCARD_SUFFIX}"


async def ensure_stream(jetstream: JetStreamContext, stream_name: str) -> None:
    """Create the ANUM events stream if it doesn't already exist.

    Idempotent (falls back to `update_stream` if it does). Shared by both
    `NatsEventPublisher.connect()` and `realtime.py`'s SSE endpoint, since a
    client can open an SSE connection before any event has ever been
    published on a fresh deployment - the subscriber must not depend on the
    publisher having run first.
    """

    subjects = [stream_subject_wildcard()]
    try:
        await jetstream.add_stream(StreamConfig(name=stream_name, subjects=subjects))
    except Exception:  # pragma: no cover - exercised via "already exists" path
        # add_stream raises if the stream already exists (e.g. a concurrent
        # connect()/ensure_stream() call, or another process created it
        # first). JetStream doesn't expose a clean "already exists"
        # exception type via nats-py, so fall back to update_stream, which
        # is idempotent.
        await jetstream.update_stream(StreamConfig(name=stream_name, subjects=subjects))


class NatsEventPublisher:
    """Async, connect-once, reuse-everywhere publisher for domain events.

    Constructed once at app startup (see `build_nats_publisher`) and reused
    across requests. `connect()` is idempotent - safe to call from multiple
    request paths without risking duplicate connections.
    """

    def __init__(self, nats_url: str, stream_name: str) -> None:
        self._nats_url = nats_url
        self._stream_name = stream_name
        self._client: NatsClient | None = None
        self._jetstream: JetStreamContext | None = None
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connect to NATS and ensure the configured stream exists.

        Idempotent: a second call while already connected is a no-op.
        """

        if self._client is not None and self._client.is_connected:
            return

        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return

            client = await nats.connect(self._nats_url)
            jetstream = client.jetstream()
            await ensure_stream(jetstream, self._stream_name)

            self._client = client
            self._jetstream = jetstream

    async def publish(self, event: DomainEvent) -> None:
        """Publish one domain event to JetStream, connecting first if needed."""

        await self.connect()
        assert self._jetstream is not None  # connect() guarantees this
        subject = event_subject(event)
        data = event.model_dump_json().encode("utf-8")
        await self._jetstream.publish(subject, data)

    async def close(self) -> None:
        """Disconnect cleanly. Safe to call even if never connected."""

        if self._client is not None:
            await self._client.drain()
            self._client = None
            self._jetstream = None


def build_nats_publisher() -> "NatsEventPublisher | None":
    """Factory used by app startup to (maybe) construct the NATS publisher.

    Returns None when `settings.nats_url` is falsy, so callers can treat a
    missing NATS configuration as "no publisher" without special-casing it -
    domain events then remain durable-only via `repository.record_event`,
    exactly as they behave today.
    """

    if not settings.nats_url:
        return None
    return NatsEventPublisher(settings.nats_url, settings.nats_stream_name)
