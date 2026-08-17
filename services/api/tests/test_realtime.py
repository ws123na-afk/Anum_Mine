"""Tests for anum_api/realtime.py's SSE endpoint.

Unmarked tests exercise the polling fallback (no NATS configured - the
default) and need no external services, so they run in the default
`pytest -m "not database"` suite.

`@pytest.mark.nats` tests spin up a real local `nats-server -js` process
(see the `nats_server` fixture below) and exercise the live JetStream path
end-to-end: a real `NatsEventPublisher.publish()` call followed by a real
`event_stream()` consumption that receives it over an actual NATS
connection. Skip these with `pytest -m "not nats"` (or select them with
`pytest -m nats`) if no nats-server binary is available.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from anum_api.events import CanonicalEventName, create_event
from anum_api.events_nats import NatsEventPublisher
from anum_api.realtime import _events_after_cursor, _visible_to_context, event_stream, format_sse_event
from anum_api.repository import InMemoryRepository
from anum_api.schemas import DomainEvent, TenantContext
from anum_api.settings import settings
from anum_api.store import InMemoryStore


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def tenant_context(tenant_id: str = "tenant_a", workspace_id: str = "workspace_a") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id="user_a", roles=["owner"])


def make_repository() -> InMemoryRepository:
    return InMemoryRepository(InMemoryStore())


def record(repository: InMemoryRepository, context: TenantContext, subject: str, *, event_id: str | None = None) -> DomainEvent:
    envelope = create_event(
        CanonicalEventName.TASK_CREATED,
        context,
        subject,
        {"title": subject},
        event_id=event_id,
        created_at=NOW,
    )
    return repository.record_event(envelope.event)


class FakeRequest:
    """Duck-typed stand-in for fastapi.Request - event_stream() only calls
    `is_disconnected()` on its request argument, so that's all this needs to
    provide, which keeps these tests independent of TestClient's streaming
    support (per the task's "your call" on how to exercise the endpoint)."""

    def __init__(self, disconnect_after: int | None = None) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after
        self.force_disconnected = False

    async def is_disconnected(self) -> bool:
        self._calls += 1
        if self.force_disconnected:
            return True
        if self._disconnect_after is not None and self._calls > self._disconnect_after:
            return True
        return False


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


def test_events_after_cursor_with_no_cursor_replays_everything() -> None:
    events = [
        DomainEvent(id="e1", type="task.created", tenant_id="t", subject="s", correlation_id="c", created_at=NOW),
        DomainEvent(id="e2", type="task.created", tenant_id="t", subject="s", correlation_id="c", created_at=NOW),
    ]
    assert _events_after_cursor(events, None) == events


def test_events_after_cursor_returns_only_events_after_the_match() -> None:
    events = [
        DomainEvent(id="e1", type="task.created", tenant_id="t", subject="s", correlation_id="c", created_at=NOW),
        DomainEvent(id="e2", type="task.created", tenant_id="t", subject="s", correlation_id="c", created_at=NOW),
        DomainEvent(id="e3", type="task.created", tenant_id="t", subject="s", correlation_id="c", created_at=NOW),
    ]
    assert [e.id for e in _events_after_cursor(events, "e1")] == ["e2", "e3"]


def test_events_after_cursor_with_unknown_cursor_falls_back_to_everything() -> None:
    events = [DomainEvent(id="e1", type="task.created", tenant_id="t", subject="s", correlation_id="c", created_at=NOW)]
    assert _events_after_cursor(events, "does_not_exist") == events


def test_visible_to_context_scopes_by_tenant_and_workspace() -> None:
    context = tenant_context()
    same = DomainEvent(id="e1", type="t", tenant_id="tenant_a", workspace_id="workspace_a", subject="s", correlation_id="c", created_at=NOW)
    other_tenant = DomainEvent(id="e2", type="t", tenant_id="tenant_b", workspace_id="workspace_a", subject="s", correlation_id="c", created_at=NOW)
    other_workspace = DomainEvent(id="e3", type="t", tenant_id="tenant_a", workspace_id="workspace_b", subject="s", correlation_id="c", created_at=NOW)
    no_workspace = DomainEvent(id="e4", type="t", tenant_id="tenant_a", workspace_id=None, subject="s", correlation_id="c", created_at=NOW)

    assert _visible_to_context(same, context) is True
    assert _visible_to_context(other_tenant, context) is False
    assert _visible_to_context(other_workspace, context) is False
    assert _visible_to_context(no_workspace, context) is True


def test_format_sse_event_shape() -> None:
    event = DomainEvent(id="event_1", type="task.created", tenant_id="t", subject="s", correlation_id="c", created_at=NOW)
    frame = format_sse_event(event)
    assert frame.startswith("id: event_1\nevent: task.created\ndata: ")
    assert frame.endswith("\n\n")


# ---------------------------------------------------------------------------
# Polling fallback (no NATS) - default "Now" scope path
# ---------------------------------------------------------------------------


def test_no_cursor_replays_full_persisted_backlog() -> None:
    async def scenario() -> list[str]:
        repository = make_repository()
        context = tenant_context()
        record(repository, context, "task_1", event_id="event_1")
        record(repository, context, "task_2", event_id="event_2")
        request = FakeRequest(disconnect_after=0)

        frames = [frame async for frame in event_stream(request, context, repository, None)]
        return frames

    frames = asyncio.run(scenario())
    assert len(frames) == 2
    assert "event_1" in frames[0]
    assert "event_2" in frames[1]


def test_cursor_only_replays_events_after_it() -> None:
    async def scenario() -> list[str]:
        repository = make_repository()
        context = tenant_context()
        record(repository, context, "task_1", event_id="event_1")
        record(repository, context, "task_2", event_id="event_2")
        record(repository, context, "task_3", event_id="event_3")
        request = FakeRequest(disconnect_after=0)

        return [frame async for frame in event_stream(request, context, repository, "event_1")]

    frames = asyncio.run(scenario())
    assert len(frames) == 2
    assert "event_2" in frames[0]
    assert "event_3" in frames[1]


def test_unknown_cursor_falls_back_to_full_replay_end_to_end() -> None:
    async def scenario() -> list[str]:
        repository = make_repository()
        context = tenant_context()
        record(repository, context, "task_1", event_id="event_1")
        request = FakeRequest(disconnect_after=0)

        return [frame async for frame in event_stream(request, context, repository, "event_never_seen")]

    frames = asyncio.run(scenario())
    assert len(frames) == 1
    assert "event_1" in frames[0]


def test_polling_fallback_picks_up_events_recorded_after_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anum_api.realtime.POLL_INTERVAL_SECONDS", 0.01)

    async def scenario() -> list[str]:
        repository = make_repository()
        context = tenant_context()
        record(repository, context, "task_1", event_id="event_1")
        request = FakeRequest()

        frames: list[str] = []

        async def consume() -> None:
            async for frame in event_stream(request, context, repository, None):
                frames.append(frame)
                if len(frames) >= 2:
                    request.force_disconnected = True
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        record(repository, context, "task_2", event_id="event_2")
        await asyncio.wait_for(consumer, timeout=5)
        return frames

    frames = asyncio.run(scenario())
    assert len(frames) == 2
    assert "event_1" in frames[0]
    assert "event_2" in frames[1]


def test_polling_fallback_is_tenant_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anum_api.realtime.POLL_INTERVAL_SECONDS", 0.01)

    async def scenario() -> list[str]:
        repository = make_repository()
        tenant_a = tenant_context("tenant_a", "workspace_a")
        record(repository, tenant_a, "task_a", event_id="event_a")
        request = FakeRequest(disconnect_after=3)

        return [frame async for frame in event_stream(request, tenant_a, repository, None)]

    frames = asyncio.run(scenario())
    assert len(frames) == 1
    assert "event_a" in frames[0]

    async def other_tenant_scenario() -> list[str]:
        repository = make_repository()
        tenant_a = tenant_context("tenant_a", "workspace_a")
        tenant_b = tenant_context("tenant_b", "workspace_b")
        record(repository, tenant_a, "task_a", event_id="event_a")
        record(repository, tenant_b, "task_b", event_id="event_b")
        request = FakeRequest(disconnect_after=0)

        return [frame async for frame in event_stream(request, tenant_b, repository, None)]

    frames_b = asyncio.run(other_tenant_scenario())
    assert len(frames_b) == 1
    assert "event_b" in frames_b[0]
    assert "event_a" not in frames_b[0]


def test_disconnect_before_any_replay_yields_nothing() -> None:
    async def scenario() -> list[str]:
        repository = make_repository()
        context = tenant_context()
        record(repository, context, "task_1", event_id="event_1")
        request = FakeRequest()
        request.force_disconnected = True

        return [frame async for frame in event_stream(request, context, repository, None)]

    # Backlog replay happens unconditionally before the first disconnect
    # check (a reconnecting client should still get its catch-up backlog
    # even if it disconnects immediately after), but the live loop must not
    # start once disconnected.
    frames = asyncio.run(scenario())
    assert len(frames) == 1


# ---------------------------------------------------------------------------
# Live NATS path (@pytest.mark.nats)
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"nats-server did not become ready on {host}:{port}") from last_error


@pytest.fixture
def nats_server(tmp_path: Path):
    if not shutil.which("nats-server"):
        pytest.skip("nats-server is not on PATH")
    port = _find_free_port()
    store_dir = tmp_path / "nats-store"
    store_dir.mkdir()
    process = subprocess.Popen(
        ["nats-server", "-js", "-p", str(port), "-sd", str(store_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port("127.0.0.1", port, timeout=10)
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def nats_url(nats_server: int, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"nats://127.0.0.1:{nats_server}"
    monkeypatch.setattr(settings, "nats_url", url)
    monkeypatch.setattr(settings, "nats_stream_name", "ANUM_EVENTS_TEST")
    return url


@pytest.mark.nats
def test_nats_publisher_connect_is_idempotent_and_publishes(nats_url: str) -> None:
    async def scenario() -> None:
        publisher = NatsEventPublisher(nats_url, settings.nats_stream_name)
        await publisher.connect()
        await publisher.connect()  # must not raise / must not duplicate the stream

        context = tenant_context()
        envelope = create_event(
            CanonicalEventName.TASK_CREATED, context, "task_1", {"title": "hi"}, event_id="event_1", created_at=NOW
        )
        await publisher.publish(envelope.event)
        await publisher.close()

    asyncio.run(scenario())


@pytest.mark.nats
def test_nats_live_path_delivers_a_published_event_end_to_end(nats_url: str) -> None:
    async def scenario() -> list[str]:
        repository = make_repository()  # empty: nothing to replay, isolates the live path
        context = tenant_context()
        request = FakeRequest()
        frames: list[str] = []

        async def consume() -> None:
            async for frame in event_stream(request, context, repository, None):
                frames.append(frame)
                request.force_disconnected = True
                return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(1.0)  # let the JetStream consumer finish subscribing

        publisher = NatsEventPublisher(nats_url, settings.nats_stream_name)
        await publisher.connect()
        envelope = create_event(
            CanonicalEventName.TASK_CREATED,
            context,
            "task_live",
            {"title": "live event"},
            event_id="event_live",
            created_at=NOW,
        )
        await publisher.publish(envelope.event)
        await publisher.close()

        await asyncio.wait_for(consumer, timeout=10)
        return frames

    frames = asyncio.run(scenario())
    assert len(frames) == 1
    assert "event_live" in frames[0]
    assert "task.created" in frames[0]


@pytest.mark.nats
def test_nats_live_path_is_tenant_isolated(nats_url: str) -> None:
    async def scenario() -> list[str]:
        repository = make_repository()
        tenant_a = tenant_context("tenant_a", "workspace_a")
        tenant_b = tenant_context("tenant_b", "workspace_b")
        request = FakeRequest()
        frames: list[str] = []

        async def consume() -> None:
            async for frame in event_stream(request, tenant_a, repository, None):
                frames.append(frame)
                request.force_disconnected = True
                return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(1.0)  # let the JetStream consumer finish subscribing

        publisher = NatsEventPublisher(nats_url, settings.nats_stream_name)
        await publisher.connect()
        # Published for tenant B - tenant A's stream must never see it.
        other_envelope = create_event(
            CanonicalEventName.TASK_CREATED, tenant_b, "task_b", event_id="event_b", created_at=NOW
        )
        await publisher.publish(other_envelope.event)

        # Give the (incorrect) delivery a real chance to arrive before we
        # conclude it didn't - tenant A's consumer must still be waiting.
        await asyncio.sleep(2.0)
        assert not consumer.done(), "tenant A's stream received another tenant's event"

        own_envelope = create_event(
            CanonicalEventName.TASK_CREATED, tenant_a, "task_a", event_id="event_a", created_at=NOW
        )
        await publisher.publish(own_envelope.event)
        await publisher.close()

        await asyncio.wait_for(consumer, timeout=10)
        return frames

    frames = asyncio.run(scenario())
    assert len(frames) == 1
    assert "event_a" in frames[0]
    assert "event_b" not in frames[0]
