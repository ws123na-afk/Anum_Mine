"""The event stream's reads and the event history's retention on PostgreSQL (migration 0162, pytest -m database).

Reliability audit, November 2026 (docs/realtime.md#event-history). ``domain_events`` was never purged and the
stream's poll had no index for its ``(created_at, id)`` order. Covers the new index and that the stream's reads use
it, the limited cursor read with tenant-wide events mixed in, the newest event a new stream starts from, and the
purge: discovery as ``anum_maintenance`` (scope of events older than 7 days only), deletion as the non-owner app
role inside each scope's RLS context, in chunks, with the audit trail untouched.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from sqlalchemy import Engine, text
from sqlalchemy import event as sa_event
from sqlalchemy.exc import DBAPIError
from test_postgres_live_agent_meeting_room import ctx, factory  # noqa: F401

from anum_api import voice_retention
from anum_api.db.repository import SqlAlchemyRepository
from anum_api.event_retention import purge, purge_events
from anum_api.schemas import utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database


def sql(engine: Engine, statement: str, **params):  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        result = connection.execute(text(statement), params)
        return result.all() if result.returns_rows else result.rowcount


def add_event(engine: Engine, tenant: str, workspace: str | None, days_ago: float, *, published: bool = True, name: str = "") -> str:
    event_id = f"event_{name or uuid4().hex[:12]}"
    at = utc_now() - timedelta(days=days_ago)
    sql(engine, """insert into domain_events (id, tenant_id, workspace_id, type, subject, correlation_id, payload,
        created_at, published_at) values (:i, :t, :w, 'task.created', 'task_example', 'task_example',
        '{"note": "payload example"}', :at, :pub)""", i=event_id, t=tenant, w=workspace, at=at, pub=at if published else None)
    return event_id


def event_ids(engine: Engine) -> set[str]:
    return {row[0] for row in sql(engine, "select id from domain_events")}


def test_stream_index_exists_and_maintenance_sees_only_the_scope_of_old_events(factory, database_engine: Engine) -> None:  # noqa: F811
    assert sql(database_engine, "select indexdef from pg_indexes where indexname = 'ix_domain_events_scope_created'") == [
        ("CREATE INDEX ix_domain_events_scope_created ON public.domain_events USING btree (tenant_id, workspace_id, created_at, id)",)
    ]
    assert sql(database_engine, "select cmd, roles from pg_policies where policyname = 'maintenance_event_retention'") == [
        ("SELECT", ["anum_maintenance"])
    ]
    add_event(database_engine, TENANT_A, WORKSPACE_A, 30)
    add_event(database_engine, TENANT_A, WORKSPACE_A2, 3)
    add_event(database_engine, TENANT_B, None, 8)
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            seen = connection.execute(text("select tenant_id, workspace_id from domain_events order by 1")).all()
        assert seen == [(TENANT_A, WORKSPACE_A), (TENANT_B, None)]  # not the event of three days ago
        for statement in ("select id from domain_events", "select type from domain_events", "select payload from domain_events",
                          "select subject from domain_events", "delete from domain_events",
                          "update domain_events set type = 'x'"):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


def test_stream_reads_are_limited_merge_tenant_wide_events_and_use_the_index(
    factory, database_engine: Engine, repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],  # noqa: F811
) -> None:
    context = ctx()
    # Oldest first: w0, wide1 (tenant-wide), w2, w3, wide4, w5; another workspace's and another tenant's in between.
    order = [add_event(database_engine, TENANT_A, WORKSPACE_A if index not in (1, 4) else None, 10 - index, name=f"{index}")
             for index in range(6)]
    hidden = add_event(database_engine, TENANT_A, WORKSPACE_A2, 0.5, name="hidden")
    add_event(database_engine, TENANT_B, WORKSPACE_B, 0.1, name="foreign")

    with repository_factory(context) as repository:
        assert [e.id for e in repository.list_events(context)] == order
        assert [e.id for e in repository.list_events(context, limit=4)] == order[:4]
        assert [e.id for e in repository.list_events_after(context, order[0], limit=3)] == order[1:4]
        assert [e.id for e in repository.list_events_after(context, order[3], limit=10)] == order[4:]
        assert repository.list_events_after(context, order[-1], limit=10) == []
        # A cursor this workspace does not have reads from the start, limited.
        assert [e.id for e in repository.list_events_after(context, hidden, limit=2)] == order[:2]
        assert repository.latest_event_id(context) == order[-1]
        # GET /events: the newest rows, oldest first.
        assert [e.id for e in repository.list_recent_events(context, 3)] == order[-3:]
        assert [e.id for e in repository.list_recent_events(context, 50)] == order
    with repository_factory(ctx(TENANT_A, WORKSPACE_A2)) as repository:
        # The other workspace's newest is its own event; the tenant-wide ones are older.
        assert repository.latest_event_id(ctx(TENANT_A, WORKSPACE_A2)) == hidden
    sql(database_engine, "delete from domain_events")
    with repository_factory(context) as repository:
        assert repository.latest_event_id(context) is None and repository.list_events(context, limit=5) == []

    # The plan: both branches walk the new index in order instead of sorting the history. The table is tiny
    # here, so the planner is told not to prefer a sequential scan; what matters is that the index can serve it.
    with repository_factory(context) as repository:
        repository.session.execute(text("set local enable_seqscan = off"))
        repository.session.execute(text("set local enable_bitmapscan = off"))
        executed: list[tuple[str, object]] = []

        def capture(conn, cursor, statement, parameters, ctx_, executemany):  # type: ignore[no-untyped-def]
            executed.append((statement, parameters))

        connection = repository.session.connection()
        sa_event.listen(connection, "before_cursor_execute", capture)
        repository.list_events_after(context, "event_missing", limit=50)
        sa_event.remove(connection, "before_cursor_execute", capture)
        statement, parameters = next(item for item in executed if "UNION ALL" in item[0].upper())
        plan = "\n".join(row[0] for row in connection.exec_driver_sql("explain " + statement, parameters))
    assert plan.count("ix_domain_events_scope_created") == 2, plan


def test_purge_deletes_old_events_per_scope_and_keeps_the_audit_trail(factory, database_engine: Engine) -> None:  # noqa: F811
    old_a = [add_event(database_engine, TENANT_A, WORKSPACE_A, 100 + index) for index in range(5)]
    old_wide = add_event(database_engine, TENANT_A, None, 120)
    kept_a = add_event(database_engine, TENANT_A, WORKSPACE_A, 89)
    kept_a2 = add_event(database_engine, TENANT_A, WORKSPACE_A2, 10)
    old_b = add_event(database_engine, TENANT_B, WORKSPACE_B, 91)
    kept_b = add_event(database_engine, TENANT_B, WORKSPACE_B, 1)
    sql(database_engine, """insert into audit_records (id, tenant_id, workspace_id, actor, action, target, outcome,
        correlation_id, metadata, created_at) values ('audit_old_example', :t, :w, 'owner', 'task.create', 'task:1', 'success',
        'corr_example', '{}', now() - interval '900 days')""", t=TENANT_A, w=WORKSPACE_A)
    before = event_ids(database_engine)

    dry = purge_events(factory, retention_days=90, dry_run=True, batch_size=1)
    assert (dry.scopes_scanned, dry.scopes_purged, dry.events, dry.dry_run) == (4, 3, 7, True)
    assert event_ids(database_engine) == before
    assert sql(database_engine, "select count(*) from audit_records where action = 'event_retention.purge'") == [(0,)]

    done = purge_events(factory, retention_days=90, chunk=2)  # three chunks for workspace A
    assert (done.scopes_purged, done.events) == (3, 7)
    assert event_ids(database_engine) == {kept_a, kept_a2, kept_b}
    assert not {*old_a, old_wide, old_b} & event_ids(database_engine)
    # One audit record per purged workspace, with the count only; the old audit record is still there.
    audits = sql(database_engine, "select tenant_id, workspace_id, actor, metadata from audit_records "
                                  "where action = 'event_retention.purge' order by tenant_id")
    assert audits == [(TENANT_A, WORKSPACE_A, "system:retention", {"retention_days": 90, "events": 5}),
                      (TENANT_B, WORKSPACE_B, "system:retention", {"retention_days": 90, "events": 1})]
    assert sql(database_engine, "select count(*) from audit_records where id = 'audit_old_example'") == [(1,)]

    again = purge_events(factory, retention_days=90)
    assert (again.scopes_purged, again.events) == (0, 0)
    assert event_ids(database_engine) == {kept_a, kept_a2, kept_b}


def test_purge_keeps_everything_at_zero_refuses_under_a_week_and_keeps_unpublished_events_for_the_relay(
    factory, database_engine: Engine, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    published = add_event(database_engine, TENANT_A, WORKSPACE_A, 40)
    waiting = add_event(database_engine, TENANT_A, WORKSPACE_A, 40, published=False)
    recent = add_event(database_engine, TENANT_A, WORKSPACE_A, 5)

    assert purge_events(factory, retention_days=0).events == 0
    with pytest.raises(ValueError, match="at least 7"):
        purge_events(factory, retention_days=3)
    assert event_ids(database_engine) == {published, waiting, recent}

    # With NATS the outbox relay still has to publish the waiting event: it is kept.
    monkeypatch.setattr(settings, "event_bus", "nats")
    monkeypatch.setattr(settings, "event_retention_days", 30)
    assert purge(session_factory=factory)["events"] == 1
    assert event_ids(database_engine) == {waiting, recent}
    # Without a bus nothing publishes events, so age alone decides.
    monkeypatch.setattr(settings, "event_bus", "memory")
    assert purge(session_factory=factory, dry_run=True) == {"retention_days": 30, "dry_run": True, "scopes_scanned": 1,
                                                           "scopes_purged": 1, "events": 1}
    assert purge(session_factory=factory)["events"] == 1
    assert event_ids(database_engine) == {recent}


def test_the_daily_job_runs_the_event_purge_and_reports_it(factory, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    gone = add_event(database_engine, TENANT_A, WORKSPACE_A, 200)
    stays = add_event(database_engine, TENANT_A, WORKSPACE_A, 2)
    assert ("events", ".event_retention", "purge", True) in voice_retention.STEPS
    only_events = [step for step in voice_retention.STEPS if step[0] == "events"]

    # Off by default: the job runs, reports the step and deletes nothing.
    assert settings.event_retention_days == 0
    report, failed = voice_retention.run_steps(only_events, dry_run=False, batch_size=500)
    assert failed == [] and report["events"]["events"] == 0 and report["events"]["retention_days"] == 0  # type: ignore[index]
    assert {gone, stays} <= event_ids(database_engine)

    monkeypatch.setattr(settings, "event_retention_days", 90)
    report, failed = voice_retention.run_steps(only_events, dry_run=False, batch_size=500)

    assert failed == [] and report["events"]["events"] == 1  # type: ignore[index]
    assert gone not in event_ids(database_engine) and stays in event_ids(database_engine)
