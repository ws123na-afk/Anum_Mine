"""Integration tests against a real NATS JetStream server.

Run a server with JetStream enabled (``docker compose -f infra/docker/compose.yaml
up nats`` or ``nats-server -js``) and point ``ANUM_TEST_NATS_URL`` at it
(default ``nats://127.0.0.1:4222``). Tests marked ``nats`` are skipped when the
server is not reachable.
"""

from __future__ import annotations

import asyncio
import os
import socket
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from anum_api.event_bus import (
    EventBusRuntime,
    NatsJetStreamBus,
    encode_event,
    event_subject,
)
from anum_api.events import CanonicalEventName, create_event
from anum_api.schemas import DomainEvent, TenantContext

NATS_URL = os.getenv("ANUM_TEST_NATS_URL", "nats://127.0.0.1:4222")


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 4222), 0.5):
            return True
    except OSError:
        return False


requires_nats = pytest.mark.skipif(
    not _reachable(NATS_URL), reason=f"NATS is not reachable at {NATS_URL}"
)


def _event(tenant: str, workspace: str, event_id: str | None = None) -> DomainEvent:
    context = TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="user", roles=["owner"])
    return create_event(
        CanonicalEventName.TASK_CREATED,
        context,
        "task_1",
        {"task_id": "task_1"},
        event_id=event_id,
    ).event


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.mark.nats
@requires_nats
def test_jetstream_deduplicates_by_event_id() -> None:
    async def scenario() -> int:
        bus = NatsJetStreamBus(NATS_URL)
        await bus.connect()
        try:
            event = _event(f"tenant_{uuid4().hex[:8]}", "workspace_a")
            before = (await bus._js.stream_info(bus.stream_name)).state.messages
            for _ in range(3):
                await bus.publish(event_subject(event), encode_event(event), msg_id=event.id)
            after = (await bus._js.stream_info(bus.stream_name)).state.messages
            return after - before
        finally:
            await bus.close()

    assert asyncio.run(scenario()) == 1


@pytest.mark.nats
@requires_nats
def test_runtime_streams_only_own_tenant_and_workspace() -> None:
    tenant_a = f"tenant_{uuid4().hex[:8]}"
    tenant_b = f"tenant_{uuid4().hex[:8]}"
    run = uuid4().hex[:8]

    async def scenario() -> dict[str, list[str]]:
        runtime = EventBusRuntime("nats", NatsJetStreamBus(NATS_URL))
        await runtime.start()
        try:
            await _wait_until(lambda: runtime.hub.live)
            context_a = TenantContext(
                tenant_id=tenant_a, workspace_id="w1", user_id="u", roles=["owner"]
            )
            context_b = TenantContext(
                tenant_id=tenant_b, workspace_id="w1", user_id="u", roles=["owner"]
            )
            context_a2 = TenantContext(
                tenant_id=tenant_a, workspace_id="w2", user_id="u", roles=["owner"]
            )
            async with (
                runtime.hub.listen(context_a) as listener_a,
                runtime.hub.listen(context_b) as listener_b,
                runtime.hub.listen(context_a2) as listener_a2,
            ):
                # Event ids are the JetStream dedupe key, so keep them unique per run.
                events = [
                    _event(tenant_a, "w1", f"{run}_a_w1"),
                    _event(tenant_b, "w1", f"{run}_b_w1"),
                    _event(tenant_a, "w2", f"{run}_a_w2"),
                ]
                runtime.after_commit(events)
                runtime.after_commit(events[:1])  # duplicate delivery attempt
                received: dict[str, list[str]] = {"a": [], "b": [], "a2": []}

                async def pump() -> None:
                    for key, listener in (("a", listener_a), ("b", listener_b), ("a2", listener_a2)):
                        received[key].extend(event.id for event in listener.drain())

                async def done() -> bool:
                    await pump()
                    return all(received.values())

                loop = asyncio.get_running_loop()
                deadline = loop.time() + 5
                while not await done() and loop.time() < deadline:
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.2)  # allow any stray or duplicate delivery to arrive
                await pump()
                return received
        finally:
            await runtime.stop()

    assert asyncio.run(scenario()) == {
        "a": [f"{run}_a_w1"],
        "b": [f"{run}_b_w1"],
        "a2": [f"{run}_a_w2"],
    }


def test_runtime_with_unreachable_nats_keeps_events_pending() -> None:
    async def scenario() -> tuple[int, bool]:
        runtime = EventBusRuntime(
            "nats",
            NatsJetStreamBus("nats://127.0.0.1:1", connect_timeout=0.2),
            reconnect_backoff=0.05,
        )
        await runtime.start()
        try:
            runtime.after_commit([_event("tenant_x", "w1", "pending_1")])
            await asyncio.sleep(0.3)
            return len(runtime.outbox.pending), runtime.hub.live  # type: ignore[union-attr]
        finally:
            await runtime.stop()

    assert asyncio.run(scenario()) == (1, False)
