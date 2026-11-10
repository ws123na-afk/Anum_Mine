"""The workspace event stream sends what happens from now on, in bounded reads.

Reliability audit, November 2026. Before it:

* a stream opened without ``Last-Event-ID`` (every page load of the web app) first sent
  the workspace's whole event history, which is never purged: 20,000 events were 10.8 MB
  and 9 s;
* a task stream whose task had no new event never moved its cursor, so it read the whole
  history again every second;
* a read had no limit, so one poll could load every row after the cursor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from anum_api import dependencies
from anum_api import main as api_main
from anum_api.events import CanonicalEventName, create_event
from anum_api.main import store
from anum_api.realtime import RealtimeHub, live_event_source
from anum_api.schemas import DomainEvent, TenantContext

CONTEXT = TenantContext(tenant_id="tenant_s", workspace_id="workspace_s", user_id="user_s", roles=["owner"])
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def event(event_id: str, *, subject: str = "task_1", workspace: str = "workspace_s", seconds: int = 0) -> DomainEvent:
    context = CONTEXT.model_copy(update={"workspace_id": workspace})
    return create_event(
        CanonicalEventName.TASK_CREATED, context, subject, {"task_id": subject},
        event_id=event_id, created_at=NOW + timedelta(seconds=seconds),
    ).event


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[DomainEvent]]:
    monkeypatch.setattr(dependencies.settings, "repository_backend", "memory")
    saved = list(store.events)
    store.events.clear()
    yield store.events
    store.events[:] = saved


class FakeRequest:
    """Stays connected for a number of polls, then reports that the client left."""

    def __init__(self, polls: int) -> None:
        self.polls = polls

    async def is_disconnected(self) -> bool:
        self.polls -= 1
        return self.polls < 0


def _stream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    polls: int,
    between_polls: list = (),  # type: ignore[assignment]
    reads: list | None = None,
    page_size: int | None = None,
    **route_arguments: object,
) -> list[str]:
    """Run the route's memory-bus stream body and return the event ids it sent, in order."""
    pending = list(between_polls)
    real_read = dependencies.list_events_for_stream

    def read(context: TenantContext, after: str | None = None) -> list[DomainEvent]:
        if reads is not None:
            reads.append(after)
        if page_size is None:
            return real_read(context, after)
        return real_read(context, after, limit=page_size)

    async def no_sleep(_seconds: float) -> None:
        if pending:
            pending.pop(0)()

    monkeypatch.setattr(api_main, "list_events_for_stream", read)
    if page_size is not None:
        monkeypatch.setattr(api_main, "EVENT_STREAM_PAGE_SIZE", page_size)
    monkeypatch.setattr(api_main.asyncio, "sleep", no_sleep)

    async def collect() -> list[str]:
        arguments = {"task_id": None, "follow": True, "last_event_id": None, **route_arguments}
        response = await api_main.stream_events(FakeRequest(polls), context=CONTEXT, repository=None, **arguments)  # type: ignore[arg-type]
        frames = [chunk async for chunk in response.body_iterator]
        return [line[4:] for frame in frames for line in str(frame).split("\n") if line.startswith("id: ")]

    return asyncio.run(collect())


def test_a_stream_without_a_cursor_sends_nothing_old_and_then_what_is_new(
    events: list[DomainEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    events.extend(event(f"old_{index}", seconds=index) for index in range(5))
    reads: list[str | None] = []

    sent = _stream(monkeypatch, polls=2, reads=reads, between_polls=[lambda: events.append(event("new_1", seconds=60))])

    assert sent == ["new_1"]
    # Both polls read after the newest event that existed at connect, never from the start.
    assert reads == ["old_4", "old_4"]


def test_a_stream_opened_in_an_empty_workspace_sends_the_first_event(
    events: list[DomainEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _stream(monkeypatch, polls=2, between_polls=[lambda: events.append(event("first"))])

    assert sent == ["first"]


def test_last_event_id_still_resumes_and_a_one_shot_replay_still_replays(
    events: list[DomainEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    events.extend(event(f"e_{index}", seconds=index) for index in range(4))

    assert _stream(monkeypatch, polls=1, last_event_id="e_1") == ["e_2", "e_3"]
    assert _stream(monkeypatch, polls=5, follow=False) == ["e_0", "e_1", "e_2", "e_3"]
    assert _stream(monkeypatch, polls=5, follow=False, last_event_id="e_2") == ["e_3"]


def test_a_task_stream_moves_its_cursor_past_events_of_other_tasks(
    events: list[DomainEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    events.append(event("mine_0", subject="task_mine"))
    reads: list[str | None] = []
    arrivals = [
        lambda: events.extend(event(f"other_{index}", subject="task_other", seconds=10 + index) for index in range(3)),
        lambda: None,
        lambda: events.append(event("mine_1", subject="task_mine", seconds=30)),
    ]

    sent = _stream(monkeypatch, polls=4, reads=reads, between_polls=arrivals, task_id="task_mine", last_event_id="mine_0")

    assert sent == ["mine_1"]
    # After the other task's events were read once, the next polls start after them.
    assert reads == ["mine_0", "mine_0", "other_2", "other_2"]


def test_a_stream_that_is_far_behind_reads_page_by_page_without_skipping(
    events: list[DomainEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    events.extend(event(f"e_{index}", seconds=index) for index in range(7))
    reads: list[str | None] = []

    sent = _stream(monkeypatch, polls=10, reads=reads, page_size=3, follow=False)

    assert sent == [f"e_{index}" for index in range(7)]
    assert reads == [None, "e_2", "e_5"]  # three reads of at most three rows each


def test_stream_reads_are_limited_and_start_after_the_cursor(events: list[DomainEvent]) -> None:
    events.extend(event(f"e_{index}", seconds=index) for index in range(5))
    events.append(event("elsewhere", workspace="workspace_other", seconds=9))

    assert dependencies.EVENT_STREAM_PAGE_SIZE == 500
    assert [e.id for e in dependencies.list_events_for_stream(CONTEXT, limit=2)] == ["e_0", "e_1"]
    assert [e.id for e in dependencies.list_events_for_stream(CONTEXT, "e_1", limit=2)] == ["e_2", "e_3"]
    assert [e.id for e in dependencies.list_events_for_stream(CONTEXT, "e_3")] == ["e_4"]
    # Another workspace's event is not this workspace's newest, and not a cursor it has.
    assert dependencies.latest_event_id_for_stream(CONTEXT) == "e_4"
    assert [e.id for e in dependencies.list_events_for_stream(CONTEXT, "elsewhere", limit=2)] == ["e_0", "e_1"]
    other = CONTEXT.model_copy(update={"workspace_id": "workspace_empty"})
    assert dependencies.latest_event_id_for_stream(other) is None


# ----------------------------------------------------------------- the NATS-fed stream


async def _never() -> bool:
    return False


async def _take(source, count: int) -> list[str]:  # type: ignore[no-untyped-def]
    ids: list[str] = []
    async for chunk in source:
        ids.extend(line[4:] for line in chunk.split("\n") if line.startswith("id: "))
        if len(ids) >= count:
            break
    await source.aclose()
    return ids


def test_live_stream_without_a_cursor_starts_at_the_newest_event_and_reads_pages() -> None:
    hub = RealtimeHub()  # not live: the stream polls the repository
    history = [event(f"old_{index}", seconds=index) for index in range(5)]
    reads: list[str | None] = []

    def after(cursor: str | None) -> list[DomainEvent]:
        reads.append(cursor)
        index = next((i for i, item in enumerate(history) if item.id == cursor), -1)
        return history[index + 1 : index + 3]  # a page of two

    async def scenario() -> list[str]:
        source = live_event_source(
            hub=hub, context=CONTEXT, list_events=after, is_disconnected=_never, fallback_poll_seconds=0.01,
            latest_event_id=lambda: history[-1].id, page_size=2,
        )
        collector = asyncio.create_task(_take(source, 3))
        await asyncio.sleep(0.03)
        history.extend(event(f"new_{index}", seconds=60 + index) for index in range(3))
        return await asyncio.wait_for(collector, timeout=2)

    assert asyncio.run(scenario()) == ["new_0", "new_1", "new_2"]
    assert reads[0] == "old_4" and None not in reads  # never from the start
    assert "new_1" in reads  # the full first page was followed by another read


def test_live_one_shot_replay_ignores_the_newest_event_shortcut() -> None:
    hub = RealtimeHub()
    hub.set_live(True)
    history = [event("a"), event("b", seconds=1)]

    async def scenario() -> list[str]:
        source = live_event_source(
            hub=hub, context=CONTEXT, list_events=lambda _after: list(history), is_disconnected=_never,
            follow=False, latest_event_id=lambda: "b",
        )
        return [line[4:] async for chunk in source for line in chunk.split("\n") if line.startswith("id: ")]

    assert asyncio.run(scenario()) == ["a", "b"]
