from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from anum_api import dependencies, main
from anum_api.event_bus import (
    TENANT_WIDE_SEGMENT,
    BusMessage,
    EventBusRuntime,
    EventCollectingRepository,
    EventOutbox,
    InMemoryEventBus,
    encode_event,
    encode_subject_token,
    event_subject,
    scope_filter_subjects,
    subject_matches,
)
from anum_api.events import CanonicalEventName, create_event
from anum_api.realtime import RealtimeHub, live_event_source
from anum_api.repository import InMemoryRepository
from anum_api.schemas import DomainEvent, TenantContext
from anum_api.store import InMemoryStore

NOW = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def ctx(tenant: str = "tenant_a", workspace: str = "workspace_a") -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="user_a", roles=["owner"])


def make_event(
    event_id: str,
    tenant: str = "tenant_a",
    workspace: str | None = "workspace_a",
    *,
    subject: str = "task_1",
    payload: dict | None = None,
) -> DomainEvent:
    event = create_event(
        CanonicalEventName.TASK_CREATED,
        ctx(tenant, workspace or "unused"),
        subject,
        payload or {"task_id": subject},
        event_id=event_id,
        created_at=NOW,
    ).event
    if workspace is None:
        event = event.model_copy(update={"workspace_id": None})
    return event


# --------------------------------------------------------------------------- subjects


def test_subject_tokens_escape_nats_syntax_and_stay_distinct() -> None:
    for raw in ["a.b", "a*", "a>", "a b", "~", "a~2eb", "tenant/é"]:
        token = encode_subject_token(raw)
        assert not set(token) & {".", "*", ">", " "}
        assert token != TENANT_WIDE_SEGMENT
    assert encode_subject_token("tenant_a-1") == "tenant_a-1"
    # Injective: an id that looks like an escape sequence does not collide.
    assert encode_subject_token("a.b") != encode_subject_token("a~2eb")
    with pytest.raises(ValueError):
        encode_subject_token("")


def test_event_subject_carries_tenant_and_workspace() -> None:
    assert event_subject(make_event("e1")) == "anum.tenant_a.workspace_a.task.created"
    assert event_subject(make_event("e2", workspace=None)) == "anum.tenant_a.~.task.created"
    assert event_subject(make_event("e3", tenant="t.x")) == "anum.t~2ex.workspace_a.task.created"


def test_scope_filters_never_match_other_scopes() -> None:
    own, tenant_wide = scope_filter_subjects("tenant_a", "workspace_a")
    assert subject_matches(own, "anum.tenant_a.workspace_a.task.created")
    assert subject_matches(tenant_wide, "anum.tenant_a.~.task.created")
    assert not subject_matches(own, "anum.tenant_a.workspace_b.task.created")
    assert not subject_matches(own, "anum.tenant_b.workspace_a.task.created")
    assert not subject_matches(tenant_wide, "anum.tenant_b.~.task.created")
    # A hostile workspace id cannot become a wildcard.
    hostile, _ = scope_filter_subjects("tenant_a", ">")
    assert not subject_matches(hostile, "anum.tenant_a.workspace_a.task.created")


# --------------------------------------------------------------------------- outbox


def test_outbox_publishes_with_event_id_as_dedupe_key() -> None:
    bus = InMemoryEventBus()
    outbox = EventOutbox(bus, clock=FakeClock())

    async def scenario() -> None:
        await bus.connect()
        event = make_event("event_1")
        outbox.enqueue([event, event])
        assert await outbox.publish_due() == 1
        # A redelivery of the same event is deduplicated by the bus.
        outbox.enqueue([event])
        assert await outbox.publish_due() == 1

    asyncio.run(scenario())
    assert [message.msg_id for message in bus.messages] == ["event_1"]
    assert bus.messages[0].subject == "anum.tenant_a.workspace_a.task.created"
    assert DomainEvent.model_validate_json(bus.messages[0].data).id == "event_1"
    assert outbox.pending == ()


def test_outbox_retries_with_backoff_until_bus_recovers() -> None:
    clock = FakeClock()
    bus = InMemoryEventBus()
    outbox = EventOutbox(bus, clock=clock, base_backoff=1.0, max_backoff=4.0)

    async def scenario() -> None:
        await bus.connect()
        bus.available = False
        outbox.enqueue([make_event("e1"), make_event("e2")])

        assert await outbox.publish_due() == 0
        first, second = outbox.pending
        assert first.attempts == 1 and first.last_error
        assert first.available_at == NOW + timedelta(seconds=1)
        assert second.attempts == 0  # the pass stops at the first failure

        assert await outbox.publish_due() == 0  # not due yet: no new attempt
        assert outbox.pending[0].attempts == 1

        for expected_delay in (2.0, 4.0, 4.0):
            clock.advance(10)
            await outbox.publish_due()
            entry = outbox.pending[0]
            assert entry.available_at == clock.now + timedelta(seconds=expected_delay)

        bus.available = True
        clock.advance(10)
        assert await outbox.publish_due() == 2

    asyncio.run(scenario())
    assert [message.msg_id for message in bus.messages] == ["e1", "e2"]
    assert outbox.pending == ()
    assert outbox.published_count == 2


def test_outbox_drops_unpublishable_events_without_blocking_the_queue() -> None:
    bus = InMemoryEventBus()
    outbox = EventOutbox(bus, clock=FakeClock())
    poison = make_event("poison").model_copy(update={"type": "Not A Subject"})

    async def scenario() -> int:
        await bus.connect()
        outbox.enqueue([poison, make_event("ok")])
        return await outbox.publish_due()

    assert asyncio.run(scenario()) == 1
    assert [message.msg_id for message in bus.messages] == ["ok"]
    assert outbox.rejected_count == 1
    assert outbox.pending == ()


def test_outbox_retry_now_skips_remaining_backoff() -> None:
    clock = FakeClock()
    bus = InMemoryEventBus()
    outbox = EventOutbox(bus, clock=clock, base_backoff=30.0)

    async def scenario() -> int:
        await bus.connect()
        bus.fail_publishes = 1
        outbox.enqueue([make_event("e1")])
        assert await outbox.publish_due() == 0
        assert await outbox.publish_due() == 0  # still backing off
        outbox.retry_now()
        return await outbox.publish_due()

    assert asyncio.run(scenario()) == 1


def test_outbox_is_bounded_and_counts_dropped_events() -> None:
    outbox = EventOutbox(InMemoryEventBus(), clock=FakeClock(), max_pending=2)
    outbox.enqueue([make_event("e1"), make_event("e2"), make_event("e3")])
    assert [entry.event.id for entry in outbox.pending] == ["e2", "e3"]
    assert outbox.dropped_count == 1


def test_runtime_never_raises_from_after_commit() -> None:
    runtime = EventBusRuntime("nats", InMemoryEventBus())

    class BrokenOutbox:
        def enqueue(self, events: object) -> None:
            raise RuntimeError("boom")

    runtime.outbox = BrokenOutbox()  # type: ignore[assignment]
    runtime.after_commit([make_event("e1")])


def test_memory_mode_runtime_is_inert() -> None:
    runtime = EventBusRuntime("memory")
    assert not runtime.live
    runtime.after_commit([make_event("e1")])

    async def scenario() -> None:
        await runtime.start()
        await runtime.stop()

    asyncio.run(scenario())
    with pytest.raises(ValueError):
        EventBusRuntime("kafka")


class FakeRelay:
    def __init__(self) -> None:
        self.notified = 0
        self.started = False
        self.stopped = False

    def notify(self) -> None:
        self.notified += 1

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def test_runtime_with_durable_relay_has_no_in_process_outbox() -> None:
    bus = InMemoryEventBus()
    relay = FakeRelay()
    runtime = EventBusRuntime("nats", bus, relay=relay, reconnect_backoff=0.01)
    assert runtime.outbox is None

    runtime.after_commit([])
    runtime.after_commit([make_event("e1")])
    assert relay.notified == 1  # committed rows are already durable; only wake the relay

    async def scenario() -> None:
        await runtime.start()
        for _ in range(100):
            if runtime.hub.live:
                break
            await asyncio.sleep(0.01)
        await runtime.stop()

    asyncio.run(scenario())
    assert relay.started and relay.stopped
    assert relay.notified >= 2  # connecting flushes the backlog through the relay
    with pytest.raises(ValueError):
        EventBusRuntime("memory", relay=FakeRelay())


def test_build_event_runtime_picks_relay_only_for_postgresql() -> None:
    from types import SimpleNamespace

    from anum_api.event_bus import build_event_runtime
    from anum_api.outbox_relay import PostgresOutboxRelay

    def config(backend: str) -> SimpleNamespace:
        return SimpleNamespace(
            event_bus="nats", nats_url="nats://127.0.0.1:1", nats_stream="ANUM_EVENTS",
            repository_backend=backend, outbox_database_url=None,
            outbox_batch_size=50, outbox_poll_seconds=0.5,
        )

    durable = build_event_runtime(config("postgresql"))
    in_process = build_event_runtime(config("memory"))

    assert isinstance(durable.relay, PostgresOutboxRelay) and durable.outbox is None
    assert durable.relay.batch_size == 50 and durable.relay.poll_interval == 0.5
    assert in_process.relay is None and isinstance(in_process.outbox, EventOutbox)


def test_relay_backoff_is_exponential_and_capped() -> None:
    from anum_api.outbox_relay import PostgresOutboxRelay

    relay = PostgresOutboxRelay(InMemoryEventBus(), lambda: None, base_backoff=0.5, max_backoff=4)  # type: ignore[arg-type,return-value]
    assert [relay.backoff_for(n) for n in (1, 2, 3, 4, 5)] == [0.5, 1, 2, 4, 4]
    with pytest.raises(ValueError):
        PostgresOutboxRelay(InMemoryEventBus(), lambda: None, batch_size=0)  # type: ignore[arg-type,return-value]


def test_runtime_reconnects_and_flushes_backlog_when_bus_returns() -> None:
    bus = InMemoryEventBus()
    bus.available = False
    runtime = EventBusRuntime("nats", bus, reconnect_backoff=0.01, max_reconnect_backoff=0.02)
    runtime.outbox.base_backoff = 0.01  # type: ignore[union-attr]

    async def scenario() -> None:
        await runtime.start()
        runtime.after_commit([make_event("e1")])
        await asyncio.sleep(0.05)
        assert bus.messages == []
        assert not runtime.hub.live
        bus.available = True
        for _ in range(100):
            if bus.messages and runtime.hub.live:
                break
            await asyncio.sleep(0.01)
        await runtime.stop()

    asyncio.run(scenario())
    assert [message.msg_id for message in bus.messages] == ["e1"]


def test_collecting_repository_delegates_and_records() -> None:
    inner = InMemoryRepository(InMemoryStore())
    repository = EventCollectingRepository(inner)
    event = make_event("e1")
    repository.record_event(event)
    assert repository.recorded_events == [event]
    assert repository.list_events(ctx()) == [event]


# --------------------------------------------------------------------------- isolation


def test_hub_routes_events_only_to_matching_tenant_and_workspace() -> None:
    bus = InMemoryEventBus()
    hub = RealtimeHub()
    received: dict[str, list[str]] = {}

    async def scenario() -> None:
        await bus.connect()
        await bus.subscribe("anum.>", hub.dispatch)
        scopes = {
            "a_w1": ctx("tenant_a", "w1"),
            "a_w2": ctx("tenant_a", "w2"),
            "b_w1": ctx("tenant_b", "w1"),
            "dotted": ctx("tenant_a.w1", "x"),
        }
        async with (
            hub.listen(scopes["a_w1"]) as a_w1,
            hub.listen(scopes["a_w2"]) as a_w2,
            hub.listen(scopes["b_w1"]) as b_w1,
            hub.listen(scopes["dotted"]) as dotted,
        ):
            events = [
                make_event("a_w1_event", "tenant_a", "w1"),
                make_event("a_w2_event", "tenant_a", "w2"),
                make_event("b_w1_event", "tenant_b", "w1"),
                make_event("a_tenant_wide", "tenant_a", None),
                make_event("b_tenant_wide", "tenant_b", None),
                make_event("dotted_event", "tenant_a.w1", "x"),
            ]
            for event in events:
                await bus.publish(event_subject(event), encode_event(event), msg_id=event.id)
            for name, listener in {
                "a_w1": a_w1,
                "a_w2": a_w2,
                "b_w1": b_w1,
                "dotted": dotted,
            }.items():
                received[name] = sorted(event.id for event in listener.drain())

    asyncio.run(scenario())
    assert received == {
        "a_w1": ["a_tenant_wide", "a_w1_event"],
        "a_w2": ["a_tenant_wide", "a_w2_event"],
        "b_w1": ["b_tenant_wide", "b_w1_event"],
        "dotted": ["dotted_event"],
    }


def test_hub_drops_messages_whose_subject_disagrees_with_the_event() -> None:
    bus = InMemoryEventBus()
    hub = RealtimeHub()

    async def scenario() -> list[DomainEvent]:
        await bus.connect()
        await bus.subscribe("anum.>", hub.dispatch)
        async with hub.listen(ctx("tenant_a", "w1")) as listener:
            foreign = make_event("foreign", "tenant_b", "w1")
            # Forged routing: tenant A's subject carrying tenant B's event.
            await bus.deliver(
                BusMessage("anum.tenant_a.w1.task.created", encode_event(foreign), "foreign")
            )
            # Mismatched dedupe id and garbage payloads are dropped too.
            own = make_event("own", "tenant_a", "w1")
            await bus.deliver(BusMessage(event_subject(own), encode_event(own), "other"))
            await bus.deliver(BusMessage("anum.tenant_a.w1.task.created", b"not json", None))
            await hub.dispatch(BusMessage("other.subject", encode_event(own), None))
            return listener.drain()

    assert asyncio.run(scenario()) == []
    assert hub.dropped_messages == 4


# --------------------------------------------------------------------------- live stream


async def _collect(source, count: int) -> list[str]:
    chunks: list[str] = []
    async for chunk in source:
        chunks.append(chunk)
        if len(chunks) >= count:
            break
    return chunks


def _ids(chunks: list[str]) -> list[str]:
    return [chunk.split("\n", 1)[0].removeprefix("id: ") for chunk in chunks if chunk.startswith("id:")]


def test_live_stream_replays_history_then_streams_only_own_scope() -> None:
    bus = InMemoryEventBus()
    hub = RealtimeHub()
    hub.set_live(True)
    context = ctx("tenant_a", "w1")
    history = [make_event("old_1", "tenant_a", "w1"), make_event("old_2", "tenant_a", "w1")]

    async def scenario() -> list[str]:
        await bus.connect()
        await bus.subscribe("anum.>", hub.dispatch)
        source = live_event_source(
            hub=hub,
            context=context,
            list_events=lambda: list(history),
            is_disconnected=_never,
            last_event_id="old_1",
        )
        collector = asyncio.create_task(_collect(source, 3))
        await asyncio.sleep(0.01)
        for event in [
            make_event("other_tenant", "tenant_b", "w1"),
            make_event("other_workspace", "tenant_a", "w2"),
            make_event("old_2", "tenant_a", "w1"),  # duplicate of replayed history
            make_event("live_1", "tenant_a", "w1"),
            make_event("tenant_wide", "tenant_a", None),
        ]:
            await bus.deliver(BusMessage(event_subject(event), encode_event(event), event.id))
        return _ids(await asyncio.wait_for(collector, timeout=2))

    assert asyncio.run(scenario()) == ["old_2", "live_1", "tenant_wide"]


def test_live_stream_filters_by_task_and_catches_up_when_bus_is_down() -> None:
    hub = RealtimeHub()  # not live: NATS unreachable
    context = ctx("tenant_a", "w1")
    history: list[DomainEvent] = [make_event("t1_a", "tenant_a", "w1", subject="task_1")]

    async def scenario() -> list[str]:
        source = live_event_source(
            hub=hub,
            context=context,
            list_events=lambda: list(history),
            is_disconnected=_never,
            task_id="task_1",
            fallback_poll_seconds=0.01,
        )
        collector = asyncio.create_task(_collect(source, 2))
        await asyncio.sleep(0.02)
        history.append(make_event("t2_a", "tenant_a", "w1", subject="task_2"))
        history.append(make_event("t1_b", "tenant_a", "w1", subject="task_1"))
        return _ids(await asyncio.wait_for(collector, timeout=2))

    assert asyncio.run(scenario()) == ["t1_a", "t1_b"]


def test_live_stream_without_follow_only_replays() -> None:
    hub = RealtimeHub()
    hub.set_live(True)

    async def scenario() -> list[str]:
        source = live_event_source(
            hub=hub,
            context=ctx("tenant_a", "w1"),
            list_events=lambda: [
                make_event("mine", "tenant_a", "w1"),
                make_event("leak", "tenant_b", "w1"),
            ],
            is_disconnected=_never,
            follow=False,
        )
        return _ids([chunk async for chunk in source])

    assert asyncio.run(scenario()) == ["mine"]


async def _never() -> bool:
    return False


# --------------------------------------------------------------------------- API wiring


HEADERS = {
    "x-tenant-id": "tenant_bus",
    "x-workspace-id": "workspace_bus",
    "x-user-id": "user_bus",
    "x-user-roles": "owner,member",
}


@pytest.fixture
def nats_mode_runtime(monkeypatch: pytest.MonkeyPatch):
    bus = InMemoryEventBus()
    runtime = EventBusRuntime("nats", bus, reconnect_backoff=0.01, max_reconnect_backoff=0.05)
    runtime.outbox.base_backoff = 0.01  # type: ignore[union-attr]
    runtime.outbox.max_backoff = 0.05  # type: ignore[union-attr]
    monkeypatch.setattr(dependencies, "event_runtime", runtime)
    monkeypatch.setattr(main, "event_runtime", runtime)
    yield runtime, bus


def _wait_for(predicate, timeout: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def test_api_publishes_committed_events_to_tenant_subject(nats_mode_runtime) -> None:
    _, bus = nats_mode_runtime
    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/tasks", headers=HEADERS, json={"title": "Bus task", "prompt": "Say hi"}
        )
        assert created.status_code == 201
        _wait_for(lambda: len(bus.messages) >= 1)
        stream = client.get(
            "/api/v1/events/stream",
            headers=HEADERS,
            params={"follow": "false", "task_id": created.json()["id"]},
        )
    message = bus.messages[0]
    event = DomainEvent.model_validate_json(message.data)
    assert message.subject == "anum.tenant_bus.workspace_bus.task.created"
    assert message.msg_id == event.id
    assert event.tenant_id == "tenant_bus" and event.workspace_id == "workspace_bus"
    assert stream.status_code == 200
    assert f"id: {event.id}" in stream.text


def test_api_keeps_working_when_bus_is_down(nats_mode_runtime) -> None:
    runtime, bus = nats_mode_runtime
    bus.available = False
    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/tasks", headers=HEADERS, json={"title": "Offline", "prompt": "Say hi"}
        )
        assert created.status_code == 201
        listed = client.get("/api/v1/events", headers=HEADERS)
        assert any(event["subject"] == created.json()["id"] for event in listed.json())
        assert [entry.event.subject for entry in runtime.outbox.pending] == [created.json()["id"]]
        bus.available = True
        _wait_for(lambda: len(bus.messages) == 1, timeout=5)
    assert runtime.outbox.pending == ()
