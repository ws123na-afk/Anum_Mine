"""Call stream wake-ups (anum_api/app_call_wake.py, docs/in-app-calls.md#signaling).

Publish: committed call events reach the streams of their own tenant and workspace only,
in process (``submit``) or from the bus through the realtime hub (``observe``). Subscribe:
a listener collects the call ids that changed and wakes its stream. The NATS round trip is
in test_nats_integration.py; the routes that record the events are in test_app_calls.py.
"""

from __future__ import annotations

import asyncio
import threading

from anum_api.app_call_wake import (
    CALL_WAKE_EVENTS,
    WORKSPACE_HIDDEN_EVENTS,
    CallWake,
    CallWakeHub,
    visible_on_workspace_stream,
)
from anum_api.event_bus import BusMessage, EventBusRuntime, InMemoryEventBus, encode_event, event_subject
from anum_api.events import CanonicalEventName, create_event
from anum_api.realtime import RealtimeHub
from anum_api.schemas import DomainEvent, TenantContext


def ctx(tenant: str = "tenant_w", workspace: str = "workspace_w", user: str = "alice") -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=["member"])


def call_event(name: CanonicalEventName, call_id: str, context: TenantContext | None = None) -> DomainEvent:
    return create_event(name, context or ctx(), call_id, {"call_id": call_id}).event


def test_the_wake_events_are_the_call_events_and_two_stay_off_the_workspace_stream() -> None:
    assert CALL_WAKE_EVENTS == {"app_call.ringing", "app_call.updated", "app_call.signaled"}
    assert WORKSPACE_HIDDEN_EVENTS == {"app_call.updated", "app_call.signaled"}
    assert visible_on_workspace_stream(call_event(CanonicalEventName.APP_CALL_RINGING, "call_1"))
    assert not visible_on_workspace_stream(call_event(CanonicalEventName.APP_CALL_SIGNALED, "call_1"))
    assert not visible_on_workspace_stream(call_event(CanonicalEventName.APP_CALL_UPDATED, "call_1"))


def test_a_wake_concerns_a_stream_that_knows_the_call_or_any_ring() -> None:
    assert CallWake(call_ids={"call_1"}).concerns({"call_1", "call_2"})
    assert not CallWake(call_ids={"call_9"}).concerns({"call_1"})
    assert CallWake(call_ids={"call_9"}, ringing=True).concerns(set())
    # A state change may be for a call the stream never saw (busy, Delete my data).
    assert CallWake(call_ids={"call_9"}, updated=True).concerns(set())
    assert CallWake(overflowed=True).concerns(set())


def test_submit_from_a_worker_thread_wakes_only_the_same_tenant_and_workspace() -> None:
    hub = CallWakeHub()

    async def scenario() -> tuple[CallWake | None, CallWake | None, CallWake | None, int]:
        async with (
            hub.listen(ctx()) as mine,
            hub.listen(ctx(workspace="workspace_other")) as other_workspace,
            hub.listen(ctx(tenant="tenant_other")) as other_tenant,
        ):
            events = [
                call_event(CanonicalEventName.APP_CALL_SIGNALED, "call_1"),
                call_event(CanonicalEventName.APP_CALL_UPDATED, "call_2"),
                create_event(CanonicalEventName.TASK_CREATED, ctx(), "task_1", {"task_id": "task_1"}).event,
            ]
            # The committing thread is never the stream's loop (sync routes, worker threads).
            worker = threading.Thread(target=hub.submit, args=(events,))
            worker.start()
            worker.join()
            woke = await mine.wait(1.0)
            count = hub.listener_count
            return woke, await other_workspace.wait(0.1), await other_tenant.wait(0.1), count

    woke, other_workspace, other_tenant, count = asyncio.run(scenario())
    assert woke is not None and woke.call_ids == {"call_1", "call_2"} and not woke.ringing
    assert other_workspace is None and other_tenant is None
    assert count == 3 and hub.listener_count == 0 and hub.local_wakes == 2


def test_a_ring_wakes_every_stream_of_the_workspace_and_a_flood_overflows() -> None:
    hub = CallWakeHub()

    async def scenario() -> tuple[CallWake | None, CallWake | None]:
        async with hub.listen(ctx(user="bob")) as listener:
            hub.submit([call_event(CanonicalEventName.APP_CALL_RINGING, "call_1")])
            ring = await listener.wait(1.0)
            hub.submit([call_event(CanonicalEventName.APP_CALL_SIGNALED, f"call_{n}") for n in range(400)])
            return ring, await listener.wait(1.0)

    ring, flood = asyncio.run(scenario())
    assert ring is not None and ring.ringing and ring.call_ids == {"call_1"}
    assert flood is not None and flood.overflowed and len(flood.call_ids) == 256


def test_submit_is_skipped_while_the_bus_delivers_and_observe_routes_by_scope() -> None:
    live = {"on": True}
    hub = CallWakeHub(lambda: live["on"])

    async def scenario() -> tuple[CallWake | None, CallWake | None, CallWake | None]:
        async with hub.listen(ctx()) as listener, hub.listen(ctx(tenant="tenant_other")) as other:
            hub.submit([call_event(CanonicalEventName.APP_CALL_SIGNALED, "call_local")])
            skipped = await listener.wait(0.1)
            hub.observe(call_event(CanonicalEventName.APP_CALL_SIGNALED, "call_bus"))
            return skipped, await listener.wait(1.0), await other.wait(0.1)

    skipped, from_bus, other = asyncio.run(scenario())
    assert skipped is None and hub.local_wakes == 0
    assert from_bus is not None and from_bus.call_ids == {"call_bus"} and hub.bus_wakes == 1
    assert other is None


def test_the_realtime_hub_hands_observers_validated_events_only() -> None:
    realtime = RealtimeHub()
    seen: list[str] = []
    realtime.add_observer(lambda event: seen.append(event.id))
    event = call_event(CanonicalEventName.APP_CALL_SIGNALED, "call_1")

    async def scenario() -> None:
        await realtime.dispatch(BusMessage(event_subject(event), encode_event(event), msg_id=event.id))
        # A message whose subject names another tenant than its body: dropped, not observed.
        forged = call_event(CanonicalEventName.APP_CALL_SIGNALED, "call_2", ctx(tenant="tenant_other"))
        await realtime.dispatch(BusMessage(event_subject(event), encode_event(forged), msg_id=forged.id))

    asyncio.run(scenario())
    assert seen == [event.id] and realtime.dropped_messages == 1


def test_a_committed_call_event_wakes_the_stream_through_the_in_memory_bus() -> None:
    """NATS mode end to end with the in-memory bus: outbox, bus, realtime hub, wake hub."""
    bus = InMemoryEventBus()
    runtime = EventBusRuntime("nats", bus, reconnect_backoff=0.01)
    hub = CallWakeHub()
    hub.attach(runtime)

    async def scenario() -> CallWake | None:
        await runtime.start()
        try:
            for _ in range(200):
                if runtime.hub.live:
                    break
                await asyncio.sleep(0.01)
            assert hub.live
            async with hub.listen(ctx(user="bob")) as listener:
                await asyncio.to_thread(runtime.after_commit, [call_event(CanonicalEventName.APP_CALL_UPDATED, "call_7")])
                return await listener.wait(2.0)
        finally:
            await runtime.stop()

    woke = asyncio.run(scenario())
    assert woke is not None and woke.call_ids == {"call_7"}
    assert hub.bus_wakes == 1 and hub.local_wakes == 0
    assert [message.subject.rsplit(".", 2)[-2:] for message in bus.messages] == [["app_call", "updated"]]
