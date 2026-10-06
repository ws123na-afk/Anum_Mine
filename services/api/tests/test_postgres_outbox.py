"""Durable PostgreSQL outbox relay: restart safety, RLS-narrow relay role, SKIP LOCKED."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Iterator
from datetime import timedelta
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from anum_api.event_bus import InMemoryEventBus
from anum_api.events import CanonicalEventName, create_event
from anum_api.outbox_relay import PostgresOutboxRelay
from anum_api.schemas import DomainEvent

from conftest import (
    FIXED_NOW,
    TENANT_A,
    TENANT_B,
    WORKSPACE_A,
    WORKSPACE_B,
    grant_app_role,
    run_migration,
    tenant_context,
)


pytestmark = pytest.mark.database


class SlowBus(InMemoryEventBus):
    async def publish(self, subject: str, data: bytes, *, msg_id: str) -> None:
        await asyncio.sleep(0.002)
        await super().publish(subject, data, msg_id=msg_id)


def connected(bus: InMemoryEventBus) -> InMemoryEventBus:
    asyncio.run(bus.connect())
    return bus


def relay_for(engine: Engine, bus: InMemoryEventBus, **kwargs) -> PostgresOutboxRelay:
    # The relay connects with a privileged login (the test superuser) on purpose: every
    # relay transaction drops to anum_outbox_relay, which is what these tests exercise.
    return PostgresOutboxRelay(bus, lambda: Session(bind=engine), **kwargs)


def record(repository_factory, context, subject: str, *, offset: int = 0, commit: bool = True,
           event_type: str = CanonicalEventName.TASK_CREATED.value) -> DomainEvent:
    event = create_event(
        CanonicalEventName.TASK_CREATED, context, subject, {"n": offset},
        created_at=FIXED_NOW + timedelta(milliseconds=offset),
    ).event.model_copy(update={"type": event_type})
    with repository_factory(context, commit=commit) as repository:
        repository.record_event(event)
    return event


def outbox_state(engine: Engine) -> dict[str, tuple]:
    with engine.connect() as connection:
        return {
            row.id: (row.published_at, row.publish_attempts, row.publish_last_error)
            for row in connection.execute(
                text("select id, published_at, publish_attempts, publish_last_error from domain_events")
            )
        }


@pytest.fixture
def scopes(seed_scopes: None) -> Iterator[tuple]:
    yield tenant_context(TENANT_A, WORKSPACE_A), tenant_context(TENANT_B, WORKSPACE_B)


def test_committed_events_survive_a_restart_and_rolled_back_ones_are_never_published(
    database_engine: Engine, repository_factory, scopes
) -> None:
    scope_a, scope_b = scopes
    kept_a = record(repository_factory, scope_a, "task_a", offset=1)
    kept_b = record(repository_factory, scope_b, "task_b", offset=2)
    record(repository_factory, scope_a, "task_rolled_back", offset=3, commit=False)

    # The process that committed the events "crashed" before publishing anything; a
    # fresh relay (a new process) finds them in PostgreSQL.
    bus = connected(InMemoryEventBus())
    published = asyncio.run(relay_for(database_engine, bus).drain())

    assert published == 2
    assert [message.msg_id for message in bus.messages] == [kept_a.id, kept_b.id]
    assert [message.subject for message in bus.messages] == [
        f"anum.{TENANT_A}.{WORKSPACE_A}.task.created",
        f"anum.{TENANT_B}.{WORKSPACE_B}.task.created",
    ]
    assert DomainEvent.model_validate_json(bus.messages[0].data) == kept_a
    state = outbox_state(database_engine)
    assert set(state) == {kept_a.id, kept_b.id}
    assert all(published_at is not None for published_at, _, _ in state.values())

    # Nothing is published twice by a second relay.
    again = connected(InMemoryEventBus())
    assert asyncio.run(relay_for(database_engine, again).drain()) == 0
    assert again.messages == []


def test_relay_role_is_limited_to_unpublished_events_and_publication_columns(
    database_engine: Engine, repository_factory, scopes
) -> None:
    scope_a, _ = scopes
    pending = record(repository_factory, scope_a, "task_pending", offset=1)
    done = record(repository_factory, scope_a, "task_done", offset=2)
    with database_engine.begin() as connection:
        connection.execute(
            text("update domain_events set published_at = now() - interval '1 second' where id = :id"),
            {"id": done.id},
        )

    def as_relay(statement: str, params: dict | None = None):
        with database_engine.connect() as connection:
            with connection.begin():
                connection.execute(text("set local role anum_outbox_relay"))
                return connection.execute(text(statement), params or {}).all()

    visible = as_relay("select id from domain_events")
    assert [row.id for row in visible] == [pending.id]  # published rows are invisible

    for statement in (
        "select id from tasks",
        "select user_id from workspace_memberships",
        "select id from workspace_invitations",
        "select id from audit_records",
        "update domain_events set payload = '{}'::jsonb",
        "update domain_events set tenant_id = 'x'",
        "delete from domain_events",
        "insert into domain_events (id, tenant_id, type, subject, correlation_id)"
        " values ('evil', 'tenant_x', 't', 's', 'c')",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            as_relay(statement)

    marked = as_relay(
        "update domain_events set publish_attempts = publish_attempts + 1 returning id"
    )
    assert [row.id for row in marked] == [pending.id]


def test_app_role_still_sees_only_its_own_tenant(
    database_engine: Engine, repository_factory, scopes
) -> None:
    scope_a, scope_b = scopes
    record(repository_factory, scope_a, "task_a", offset=1)
    record(repository_factory, scope_b, "task_b", offset=2)

    with repository_factory(scope_a) as repository:
        assert [event.subject for event in repository.list_events(scope_a)] == ["task_a"]
        # The relay policy is scoped to anum_outbox_relay, not to the app role.
        rows = repository.session.execute(text("select id from domain_events")).all()
        assert len(rows) == 1


def test_failed_publish_is_retried_with_backoff(
    database_engine: Engine, repository_factory, scopes
) -> None:
    scope_a, _ = scopes
    first = record(repository_factory, scope_a, "task_1", offset=1)
    second = record(repository_factory, scope_a, "task_2", offset=2)
    bus = connected(InMemoryEventBus())
    bus.fail_publishes = 1
    relay = relay_for(database_engine, bus, base_backoff=60, max_backoff=60)

    outcome = asyncio.run(relay.relay_once())

    assert (outcome.claimed, outcome.published, outcome.failed) == (2, 0, 1)
    state = outbox_state(database_engine)
    assert state[first.id][0] is None and state[first.id][1] == 1
    assert state[first.id][2] == "injected publish failure"
    assert state[second.id] == (None, 0, None)  # released untouched
    with database_engine.connect() as connection:
        delay = connection.execute(
            text("select publish_next_attempt_at - now() from domain_events where id = :id"),
            {"id": first.id},
        ).scalar_one()
    assert timedelta(seconds=50) < delay <= timedelta(seconds=60)

    # The failed row waits out its backoff; the next row is not blocked behind it.
    asyncio.run(relay.drain())
    assert [message.msg_id for message in bus.messages] == [second.id]

    with database_engine.begin() as connection:
        connection.execute(text("update domain_events set publish_next_attempt_at = now()"))
    asyncio.run(relay.drain())
    assert [message.msg_id for message in bus.messages] == [second.id, first.id]
    assert outbox_state(database_engine)[first.id][2] is None


def test_unpublishable_event_is_parked_without_blocking_others(
    database_engine: Engine, repository_factory, scopes
) -> None:
    scope_a, _ = scopes
    bad = record(repository_factory, scope_a, "task_bad", offset=1, event_type="Not A Subject!")
    good = record(repository_factory, scope_a, "task_good", offset=2)
    bus = connected(InMemoryEventBus())
    relay = relay_for(database_engine, bus)

    asyncio.run(relay.drain())
    asyncio.run(relay.drain())

    assert [message.msg_id for message in bus.messages] == [good.id]
    assert relay.rejected_count == 1
    with database_engine.connect() as connection:
        parked = connection.execute(
            text("select publish_next_attempt_at = 'infinity' from domain_events where id = :id"),
            {"id": bad.id},
        ).scalar_one()
    assert parked is True


def test_skip_locked_lets_concurrent_relays_split_the_backlog(
    database_engine: Engine, repository_factory, scopes
) -> None:
    scope_a, scope_b = scopes
    events = [
        record(repository_factory, scope_a if index % 2 else scope_b, f"task_{index}", offset=index)
        for index in range(40)
    ]

    # Deterministic part: rows claimed (locked) by one relay are skipped by another.
    holder_bus = connected(InMemoryEventBus())
    holder = relay_for(database_engine, holder_bus, batch_size=10)
    held_session = Session(bind=database_engine)
    try:
        held = holder._claim(held_session)
        other_bus = connected(InMemoryEventBus())
        other = relay_for(database_engine, other_bus, batch_size=100)
        asyncio.run(other.relay_once())
        held_ids = {entry.event.id for entry in held}
        assert len(held_ids) == 10
        assert {message.msg_id for message in other_bus.messages} == {
            event.id for event in events
        } - held_ids
    finally:
        held_session.rollback()
        held_session.close()

    # Concurrent part: two relays racing over the rest publish each row exactly once.
    with database_engine.begin() as connection:
        connection.execute(
            text("update domain_events set published_at = null, publish_attempts = 0")
        )
    bus_1, bus_2 = connected(SlowBus()), connected(SlowBus())
    relay_1 = relay_for(database_engine, bus_1, batch_size=5)
    relay_2 = relay_for(database_engine, bus_2, batch_size=5)

    async def race() -> None:
        for _ in range(40):
            outcomes = await asyncio.gather(relay_1.relay_once(), relay_2.relay_once())
            if all(outcome.claimed == 0 for outcome in outcomes):
                return

    asyncio.run(race())
    ids_1 = [message.msg_id for message in bus_1.messages]
    ids_2 = [message.msg_id for message in bus_2.messages]
    assert sorted(ids_1 + ids_2) == sorted(event.id for event in events)
    assert not set(ids_1) & set(ids_2)


def test_runtime_relay_publishes_api_events_from_postgresql(
    database_engine: Engine, scopes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: API request -> committed row -> relay -> bus, with no in-process queue."""
    from fastapi.testclient import TestClient

    import anum_api.db.session as db_session
    from anum_api import dependencies, main
    from anum_api.event_bus import EventBusRuntime
    from anum_api.settings import settings

    from conftest import APP_ROLE

    def app_role_session() -> Session:
        session = Session(bind=database_engine)
        session.execute(text(f"set local role {APP_ROLE}"))
        return session

    bus = InMemoryEventBus()
    relay = relay_for(database_engine, bus, poll_interval=0.05)
    runtime = EventBusRuntime("nats", bus, relay=relay, reconnect_backoff=0.01)
    assert runtime.outbox is None
    monkeypatch.setattr(db_session, "SessionLocal", app_role_session)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    monkeypatch.setattr(dependencies, "event_runtime", runtime)
    monkeypatch.setattr(main, "event_runtime", runtime)
    headers = {
        "x-tenant-id": TENANT_A,
        "x-workspace-id": WORKSPACE_A,
        "x-user-id": "user_relay",
        "x-user-roles": "owner",
    }

    with TestClient(main.app) as client:
        created = client.post("/api/v1/tasks", headers=headers, json={"title": "T", "prompt": "P"})
        assert created.status_code == 201
        import time

        deadline = time.monotonic() + 5
        while not bus.messages and time.monotonic() < deadline:
            time.sleep(0.02)

    assert [message.subject for message in bus.messages] == [
        f"anum.{TENANT_A}.{WORKSPACE_A}.task.created"
    ]
    assert DomainEvent.model_validate_json(bus.messages[0].data).subject == created.json()["id"]
    assert all(published_at for published_at, _, _ in outbox_state(database_engine).values())


def test_outbox_migration_backfills_history_as_published(
    database_engine: Engine, test_database_url: str, seed_scopes: None
) -> None:
    try:
        run_migration(test_database_url, "downgrade", "0006_workspace_invitations")
        assert "published_at" not in {
            column["name"] for column in inspect(database_engine).get_columns("domain_events")
        }
        with database_engine.begin() as connection:
            connection.execute(
                text(
                    "insert into domain_events (id, tenant_id, workspace_id, type, subject, correlation_id)"
                    " values ('event_history', :tenant, :workspace, 'task.created', 's', 'c')"
                ),
                {"tenant": TENANT_A, "workspace": WORKSPACE_A},
            )
    finally:
        run_migration(test_database_url, "upgrade", "head")
        grant_app_role(database_engine)

    state = outbox_state(database_engine)
    assert state["event_history"][0] is not None
    bus = connected(InMemoryEventBus())
    assert asyncio.run(relay_for(database_engine, bus).drain()) == 0


NATS_URL = os.getenv("ANUM_TEST_NATS_URL", "nats://127.0.0.1:4222")


def _nats_reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 4222), 0.5):
            return True
    except OSError:
        return False


@pytest.mark.nats
@pytest.mark.skipif(not _nats_reachable(NATS_URL), reason=f"NATS is not reachable at {NATS_URL}")
def test_relay_publishes_to_jetstream_once_even_after_a_crash_before_marking(
    database_engine: Engine, repository_factory, scopes
) -> None:
    from anum_api.event_bus import NatsJetStreamBus

    scope_a, scope_b = scopes
    run = uuid4().hex[:8]
    for index, scope in enumerate((scope_a, scope_b, scope_a)):
        record(repository_factory, scope, f"task_{run}_{index}", offset=index)

    def relay(bus: NatsJetStreamBus) -> PostgresOutboxRelay:
        return PostgresOutboxRelay(bus, lambda: Session(bind=database_engine))

    async def scenario() -> tuple[int, int, int, int]:
        bus = NatsJetStreamBus(NATS_URL)
        await bus.connect()
        try:
            async def stored() -> int:
                return (await bus._js.stream_info(bus.stream_name)).state.messages

            before = await stored()
            first = await relay(bus).drain()
            after_first = await stored()
            # A relay that crashed after JetStream acknowledged but before its commit
            # leaves the rows unpublished; the next relay re-sends them.
            with database_engine.begin() as connection:
                connection.execute(text("update domain_events set published_at = null"))
            resent = await relay(bus).drain()
            after_resend = await stored()
            return first, after_first - before, resent, after_resend - after_first
        finally:
            await bus.close()

    first, stored_first, resent, stored_again = asyncio.run(scenario())
    assert (first, stored_first) == (3, 3)
    assert (resent, stored_again) == (3, 0)  # Nats-Msg-Id dedupe drops the copies


def test_backlog_metrics_count_due_and_parked_events_as_the_relay_role(
    database_engine: Engine, repository_factory, scopes
) -> None:
    """The anum.outbox.* gauges (docs/observability.md) read through the narrow relay role."""
    scope_a, scope_b = scopes
    bad = record(repository_factory, scope_a, "task_bad", offset=1, event_type="Not A Subject!")
    record(repository_factory, scope_a, "task_a", offset=2)
    record(repository_factory, scope_b, "task_b", offset=3)
    bus = InMemoryEventBus()  # not connected: the backlog builds up
    relay = relay_for(database_engine, bus)

    before = asyncio.run(relay.refresh_backlog())
    assert before is not None
    assert (before.backlog, before.parked) == (3, 0)
    # FIXED_NOW is in the past, so the oldest event is (much) older than a minute.
    assert before.oldest_age_seconds > 60

    asyncio.run(bus.connect())
    asyncio.run(relay.drain())
    after = asyncio.run(relay.refresh_backlog())
    assert after is not None
    assert (after.backlog, after.parked, after.oldest_age_seconds) == (0, 1, 0)
    assert relay.backlog_snapshot() == after
    assert outbox_state(database_engine)[bad.id][0] is None  # parked, still unpublished
