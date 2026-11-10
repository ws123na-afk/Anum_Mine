"""Checks every repository backend must pass for private tasks (docs/tasks.md#private-tasks).

``test_private_tasks.py`` runs them against the in-memory repository and
``test_postgres_private_tasks.py`` against PostgreSQL, so the two backends cannot drift apart:
an event is stamped ``private_to`` when it is recorded, the stamp follows the task when its marker
changes, and the activity list, the event stream cursor reads, automation triggers
(``events_since``) and the event-bus filter all read that one stamp.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import Any

from anum_api.schemas import DomainEvent, Task, TaskStatus, TenantContext, utc_now

AsUser = Callable[[str], AbstractContextManager[Any]]
EventsSince = Callable[[str, str], list[DomainEvent]]


def _event(task: Task, event_id: str, event_type: str = "task.created") -> DomainEvent:
    return DomainEvent(
        id=event_id, type=event_type, tenant_id=task.tenant_id, workspace_id=task.workspace_id, subject=task.id,
        correlation_id=task.id, created_at=utc_now(), payload={"title": task.title},
    )


def check_events_follow_the_task(
    as_user: AsUser,
    events_since: EventsSince,
    context_for: Callable[[str], TenantContext],
    task_id: str,
) -> None:
    """``as_user(user)`` yields a repository whose session (if any) runs as ``user``;
    ``events_since(user, event_type)`` reads through the automation effects of that backend."""
    alice, omar = context_for("alice"), context_for("omar")
    now = utc_now()
    with as_user("alice") as repository:
        task = repository.create_task(Task(
            id=task_id, title="Balance question", prompt="What is my balance?", status=TaskStatus.CREATED,
            tenant_id=alice.tenant_id, workspace_id=alice.workspace_id, created_at=now, updated_at=now, created_by="alice",
        ))
        first = repository.record_event(_event(task, f"evt_{task_id}_1"))
        assert first.private_to is None  # a workspace task so far: on the bus, visible to everyone
    with as_user("omar") as repository:
        assert [event.id for event in repository.list_events(omar)] == [first.id]
        assert repository.latest_event_id(omar) == first.id
    assert [event.id for event in events_since("omar", "task.created")] == [first.id]

    # The task turns private: its earlier event follows it, and the next one is stamped at record time.
    with as_user("alice") as repository:
        task = repository.get_task(task_id, alice)
        assert task is not None
        task.private_to, task.private_kind = "alice", "data"
        repository.save_task(task)
        second = repository.record_event(_event(task, f"evt_{task_id}_2", "agent_run.completed"))
        assert second.private_to == "alice"
    with as_user("omar") as repository:
        assert repository.list_events(omar) == [] and repository.list_events_after(omar, first.id) == []
        assert repository.latest_event_id(omar) is None and repository.list_recent_events(omar, 10) == []
    # A private task's events never start a workspace automation, not even for its person.
    assert events_since("omar", "task.created") == [] and events_since("alice", "task.created") == []
    assert events_since("alice", "agent_run.completed") == []
    with as_user("alice") as repository:
        events = repository.list_events(alice)
        assert [event.id for event in events] == [first.id, second.id]
        assert {event.private_to for event in events} == {"alice"}  # the stamp the bus filter reads
        assert [event.id for event in repository.list_events_after(alice, first.id)] == [second.id]


def since(after_days: int = 1) -> Any:
    """The ``after`` cursor ``events_since`` callers pass: a little while ago."""
    return utc_now() - timedelta(days=after_days)
