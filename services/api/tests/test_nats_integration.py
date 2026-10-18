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
from contextlib import aclosing
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from anum_api.app_call_wake import CallWakeHub
from anum_api.app_calls import CallSignal, SignalKind, Snapshot, call_event_source
from anum_api.event_bus import (
    EventBusRuntime,
    NatsJetStreamBus,
    encode_event,
    event_subject,
)
from anum_api.events import CanonicalEventName, create_event
from anum_api.schemas import DomainEvent, TenantContext
from anum_api.settings import settings

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


@pytest.mark.nats
@requires_nats
def test_call_events_wake_call_streams_through_nats_only_in_their_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-app call signaling over NATS (docs/in-app-calls.md#signaling): a committed call
    event goes outbox, JetStream, realtime hub, call wake hub, and the call stream re-reads
    at once although its fallback poll is a minute away. Other tenants and workspaces are
    not woken, and the in-process feed stays quiet while the bus is live."""
    monkeypatch.setattr(settings, "calls_bus_fallback_poll_seconds", 60.0)
    tenant_a = f"tenant_{uuid4().hex[:8]}"
    tenant_b = f"tenant_{uuid4().hex[:8]}"
    call_id = f"call_{uuid4().hex[:12]}"
    device = "device-bob-nats-0001"
    bob = TenantContext(tenant_id=tenant_a, workspace_id="w1", user_id="bob", roles=["member"])
    alice = bob.model_copy(update={"user_id": "alice"})
    stored: list[CallSignal] = []

    def read(context: TenantContext, client_id: str) -> Snapshot:
        return Snapshot(calls=[], signals=list(stored))

    async def scenario() -> tuple[float, list[str], dict[str, bool], int, int]:
        runtime = EventBusRuntime("nats", NatsJetStreamBus(NATS_URL))
        hub = CallWakeHub()
        hub.attach(runtime)
        await runtime.start()
        try:
            await _wait_until(lambda: runtime.hub.live)
            loop = asyncio.get_running_loop()
            frames: list[str] = []
            woke_elsewhere: dict[str, bool] = {}
            other_tenant_context = TenantContext(tenant_id=tenant_b, workspace_id="w1", user_id="bob", roles=["member"])
            other_workspace_context = TenantContext(tenant_id=tenant_a, workspace_id="w2", user_id="bob", roles=["member"])
            async with hub.listen(other_tenant_context) as other_tenant, hub.listen(other_workspace_context) as other_workspace:

                async def never() -> bool:
                    return False

                async def consume() -> None:
                    async with aclosing(call_event_source(bob, device, never, read=read, wakes=hub)) as source:
                        async for frame in source:
                            frames.append(frame)
                            if "event: signal" in frame:
                                return

                task = asyncio.create_task(consume())
                await _wait_until(lambda: hub.listener_count == 3)
                await asyncio.sleep(0.2)  # the stream now waits out its slow poll
                started = loop.time()
                stored.append(
                    CallSignal(
                        id=f"sig_{uuid4().hex[:8]}",
                        call_id=call_id,
                        sender_user_id="alice",
                        recipient_user_id="bob",
                        recipient_client_id=device,
                        kind=SignalKind.OFFER,
                        payload={"type": "offer", "sdp": "v=0"},
                        created_at=datetime.now(UTC),
                    )
                )
                # A ring wakes every call stream of the workspace (the callee learns the id).
                ring = create_event(
                    CanonicalEventName.APP_CALL_RINGING, alice, call_id, {"call_id": call_id, "recipient": "bob"}
                ).event
                await asyncio.to_thread(runtime.after_commit, [ring])  # like a sync route's commit
                await asyncio.wait_for(task, 5)
                elapsed = loop.time() - started
                await asyncio.sleep(0.2)  # allow any stray delivery to arrive
                woke_elsewhere["tenant"] = await other_tenant.wait(0.05) is not None
                woke_elsewhere["workspace"] = await other_workspace.wait(0.05) is not None
            return elapsed, frames, woke_elsewhere, hub.bus_wakes, hub.local_wakes
        finally:
            await runtime.stop()

    elapsed, frames, woke_elsewhere, bus_wakes, local_wakes = asyncio.run(scenario())
    assert elapsed < 3.0, f"the stream waited for its poll ({elapsed:.2f}s)"
    assert any("event: signal" in frame and call_id in frame for frame in frames)
    assert woke_elsewhere == {"tenant": False, "workspace": False}
    assert bus_wakes >= 1 and local_wakes == 0


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
