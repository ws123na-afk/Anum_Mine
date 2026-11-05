"""Record retention on PostgreSQL (owner decision O5, migration 0120, pytest -m database).

Discovery runs as ``anum_maintenance`` (tenant and workspace of old ended meetings and old visits only); each workspace
is purged as the non-owner app role inside its RLS context, where its own periods are read. Covers the standard
periods (transcripts 90 days, visitors 365 days), an owner's own periods, what stays after a transcript goes (the
meeting, totals, summary and confirmed outputs), the audit record with counts only, idempotence, the daily job's
report, the owner setting route (read, save, stale revision, reset, refusals, Arabic) and tenant isolation.
"""
from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_live_agent_meeting_room import FakeGateway, ctx, factory, make_meeting, read, run_turn  # noqa: F401
from test_postgres_office_hq import EXEC, OWNER_A, headers, hq  # noqa: F401
from test_postgres_control_plane import postgres_backend  # noqa: F401

from anum_api import live_agent_meeting_room as room
from anum_api.record_retention import purge_records
from anum_api.schemas import utc_now

pytestmark = pytest.mark.database
URL = "/api/v1/workspace/record-retention"
NAME = "Visitor Example"
PURPOSE = "Supplier meeting example"


def sql(engine: Engine, statement: str, **params):  # type: ignore[no-untyped-def]
    with engine.begin() as connection:
        result = connection.execute(text(statement), params)
        return result.all() if result.returns_rows else result.rowcount


def ended_meeting(factory, engine: Engine, context, days_ago: int, *, words: str = "Ship on Tuesday."):  # type: ignore[no-untyped-def]  # noqa: F811
    """A meeting with one message and one decision, ended ``days_ago`` days ago."""
    mid = make_meeting(factory, context)
    run_turn(context, mid, "ret-" + uuid4().hex[:10], FakeGateway([words]))
    read(factory, context, lambda s: room.output_create(s, context, mid, kind="decision", body="Ship Tuesday",
                                                              owner_label=None, source_sequences=[1]))
    read(factory, context, lambda s: s.transition(context, mid, s.get(context, mid)["revision"], "finished", reason="limit_reached"))
    sql(engine, "update agent_meeting_sessions set ended_at = now() - make_interval(days => :d) where id = :i", d=days_ago, i=mid)
    return mid


def visit(engine: Engine, tenant: str, workspace: str, status: str, days_ago: int) -> str:
    vid = "visit-" + uuid4().hex[:10]
    at = utc_now() - timedelta(days=days_ago)
    times = {"expected": (None, None, None), "checked_in": (at, None, None), "checked_out": (at - timedelta(hours=2), at, None),
             "cancelled": (None, None, at)}[status]
    sql(engine, """insert into office_visitors (tenant_id, workspace_id, id, visitor_name, company, purpose, expected_at,
        checked_in_at, checked_out_at, cancelled_at, status, created_by, updated_by)
        values (:t, :w, :i, :n, 'Example Trading', :p, :e, :ci, :co, :ca, :s, 'owner', 'owner')""",
        t=tenant, w=workspace, i=vid, n=NAME, p=PURPOSE, e=at - timedelta(hours=3), ci=times[0], co=times[1], ca=times[2], s=status)
    return vid


def ids(engine: Engine, table: str) -> set[str]:
    return {r[0] for r in sql(engine, f"select id from {table}")}  # nosec B608: fixed table names


def transcript_left(engine: Engine, mid: str) -> int:
    return sql(engine, "select count(*) from agent_meeting_turns where session_id = :i", i=mid)[0][0]


# --------------------------------------------------------------------------- schema and the maintenance role


def test_setting_table_forces_rls_and_maintenance_sees_only_the_scope_of_old_rows(factory, database_engine: Engine) -> None:  # noqa: F811
    assert sql(database_engine, "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'workspace_record_retention'") == [(True, True)]
    names = {r[0] for r in sql(database_engine, "select policyname from pg_policies where tablename in "
                                                "('workspace_record_retention', 'agent_meeting_sessions', 'office_visitors')")}
    assert {"tenant_isolation_workspace_record_retention", "lifecycle_purge_workspace_record_retention",
            "maintenance_transcript_retention", "maintenance_visitor_retention"} <= names
    old, recent = ended_meeting(factory, database_engine, ctx(), 20), ended_meeting(factory, database_engine, ctx(), 2)
    live = make_meeting(factory, ctx())
    visit(database_engine, TENANT_A, WORKSPACE_A, "checked_out", 40)
    visit(database_engine, TENANT_A, WORKSPACE_A, "checked_out", 5)
    del old, recent, live
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            meetings = connection.execute(text("select tenant_id, workspace_id from agent_meeting_sessions")).all()
            visitors = connection.execute(text("select tenant_id, workspace_id from office_visitors")).all()
        assert meetings == [(TENANT_A, WORKSPACE_A)]  # only the meeting that ended 20 days ago
        assert visitors == [(TENANT_A, WORKSPACE_A)]  # only the visit 40 days ago
        for statement in ("select id from agent_meeting_sessions", "select title from agent_meeting_sessions",
                          "select body from agent_meeting_turns", "select visitor_name from office_visitors",
                          "select purpose from office_visitors", "delete from office_visitors",
                          "update agent_meeting_sessions set title = 'x'", "select transcript_days from workspace_record_retention"):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


# --------------------------------------------------------------------------- the purge


def test_standard_periods_purge_old_transcripts_and_visits_and_keep_the_record(factory, database_engine: Engine) -> None:  # noqa: F811
    a, b = ctx(), ctx(TENANT_B, WORKSPACE_B)
    old = ended_meeting(factory, database_engine, a, 100)
    young = ended_meeting(factory, database_engine, a, 60)
    live = make_meeting(factory, a)
    run_turn(a, live, "ret-live-1", FakeGateway(["Still talking."]))
    sql(database_engine, "update agent_meeting_sessions set created_at = now() - interval '400 days', "
                         "updated_at = now() - interval '400 days' where id = :i", i=live)
    gone_out = visit(database_engine, TENANT_A, WORKSPACE_A, "checked_out", 400)
    gone_cancel = visit(database_engine, TENANT_A, WORKSPACE_A, "cancelled", 366)
    gone_noshow = visit(database_engine, TENANT_A, WORKSPACE_A, "expected", 370)
    kept_out = visit(database_engine, TENANT_A, WORKSPACE_A, "checked_out", 300)
    # Tenant B chose shorter periods: 7 days for transcripts, 30 for visitors.
    b_old = ended_meeting(factory, database_engine, b, 10)
    b_visit = visit(database_engine, TENANT_B, WORKSPACE_B, "checked_out", 31)
    sql(database_engine, "insert into workspace_record_retention (tenant_id, workspace_id, transcript_days, visitor_days, updated_by) "
                         "values (:t, :w, 7, 30, 'owner')", t=TENANT_B, w=WORKSPACE_B)
    before = (ids(database_engine, "office_visitors"), transcript_left(database_engine, old))

    dry = purge_records(factory, dry_run=True, batch_size=1)
    assert (dry.workspaces_scanned, dry.meeting_transcripts, dry.visitor_records) == (2, 2, 4)
    assert (ids(database_engine, "office_visitors"), transcript_left(database_engine, old)) == before
    assert sql(database_engine, "select count(*) from audit_records where action = 'record_retention.purge'") == [(0,)]

    done = purge_records(factory)
    assert (done.workspaces_purged, done.meeting_transcripts, done.visitor_records) == (2, 2, 4)
    assert done.transcript_messages == 2 and done.transcript_events > 0 and done.stopped is False
    assert ids(database_engine, "office_visitors") == {kept_out}
    assert not {gone_out, gone_cancel, gone_noshow, b_visit} & ids(database_engine, "office_visitors")
    assert transcript_left(database_engine, old) == 0 and transcript_left(database_engine, b_old) == 0
    assert transcript_left(database_engine, young) == 1 and transcript_left(database_engine, live) == 1
    # What stays, as with the owner's manual delete: the meeting, its totals and its outputs.
    bundle = read(factory, a, lambda s: room.room(s, a, old))
    assert bundle["transcript_deleted"] is True and bundle["meeting"]["total_cost_usd"] > 0
    assert [o["body"] for o in bundle["outputs"]] == ["Ship Tuesday"] and bundle["outputs"][0]["source_sequences"] == []
    assert read(factory, a, lambda s: room.room(s, a, young))["transcript_deleted"] is False

    audit = sql(database_engine, "select tenant_id, actor, target, metadata from audit_records "
                                 "where action = 'record_retention.purge' order by tenant_id")
    assert [(r[0], r[1], r[2]) for r in audit] == [(TENANT_A, "system:retention", f"record_retention:{WORKSPACE_A}"),
                                                    (TENANT_B, "system:retention", f"record_retention:{WORKSPACE_B}")]
    meta_a = audit[0][3]
    assert meta_a["transcript_days"] == 90 and meta_a["visitor_days"] == 365
    assert meta_a["meeting_transcripts"] == 1 and meta_a["visitor_records"] == 3
    assert audit[1][3]["transcript_days"] == 7 and audit[1][3]["visitor_days"] == 30
    dumped = json.dumps([r[3] for r in audit])
    for secret in (NAME, PURPOSE, "Example Trading", "Ship on Tuesday", "Weekly plan"):
        assert secret not in dumped

    again = purge_records(factory)
    assert (again.meeting_transcripts, again.visitor_records, again.workspaces_purged) == (0, 0, 0)


def test_daily_retention_job_reports_record_retention(factory, database_engine: Engine, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: F811
    from anum_api.voice_retention import main as voice_retention_main

    visit(database_engine, TENANT_A, WORKSPACE_A, "checked_out", 400)
    assert voice_retention_main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)["record_retention"]
    assert report["dry_run"] is True and report["visitor_records"] == 1 and NAME not in output
    assert voice_retention_main([]) == 0
    assert json.loads(capsys.readouterr().out)["record_retention"]["visitor_records"] == 1
    assert ids(database_engine, "office_visitors") == set()


def test_another_tenants_context_never_reads_or_purges_this_workspace(factory, database_engine: Engine) -> None:  # noqa: F811
    from anum_api.record_retention import purge_workspace

    vid = visit(database_engine, TENANT_A, WORKSPACE_A, "checked_out", 400)
    mid = ended_meeting(factory, database_engine, ctx(), 200)
    session = factory()
    try:
        from anum_api.db.session import set_tenant_context

        set_tenant_context(session, TENANT_B, WORKSPACE_B)
        # Asking tenant B's context to purge tenant A's workspace finds and deletes nothing under RLS.
        counts = purge_workspace(session, TENANT_A, WORKSPACE_A, utc_now())
        session.commit()
    finally:
        session.close()
    assert counts == {"meetings": 0, "messages": 0, "events": 0, "visitors": 0}
    assert vid in ids(database_engine, "office_visitors") and transcript_left(database_engine, mid) == 1


# --------------------------------------------------------------------------- the owner setting


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def test_owner_reads_saves_and_resets_the_periods(hq: TestClient, database_engine: Engine) -> None:  # noqa: F811
    first = hq.get(URL, headers=OWNER()).json()
    assert first["transcripts"] == {"days": 90, "is_default": True, "default_days": 90, "min_days": 7, "max_days": 1825}
    assert first["visitors"]["days"] == 365 and first["visitors"]["min_days"] == 30
    assert first["revision"] == 0 and first["can_manage"] is True and first["snapshots_stored"] is False
    viewer = hq.get(URL, headers=headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")).json()
    assert viewer["can_manage"] is False and viewer["transcripts"]["days"] == 90

    saved = hq.put(URL, headers=OWNER(), json={"transcript_days": 30, "visitor_days": None, "expected_revision": 0, "input_mode": "click"})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["transcripts"]["days"] == 30 and body["transcripts"]["is_default"] is False
    assert body["visitors"]["is_default"] is True and body["revision"] == 1 and body["updated_by"] == OWNER_A
    stale = hq.put(URL, headers=OWNER(), json={"transcript_days": 45, "expected_revision": 0, "input_mode": "click"})
    assert stale.status_code == 409
    assert hq.put(URL, headers={**OWNER(), "accept-language": "ar"},
                  json={"transcript_days": 45, "expected_revision": 0, "input_mode": "click"}).json()["error"]["message"] == \
        "تغيّر إعداد مدة الاحتفاظ؛ أعد التحميل وحاول مرة أخرى."
    reset = hq.put(URL, headers=OWNER(), json={"transcript_days": None, "visitor_days": 730, "expected_revision": 1, "input_mode": "click"}).json()
    assert reset["transcripts"]["days"] == 90 and reset["transcripts"]["is_default"] and reset["visitors"]["days"] == 730
    assert reset["revision"] == 2

    audit = sql(database_engine, "select actor, metadata from audit_records where action = 'record_retention.update' order by created_at")
    assert [r[0] for r in audit] == [OWNER_A, OWNER_A]
    assert audit[0][1]["previous_transcript_days"] == 90 and audit[0][1]["new_transcript_days"] == 30
    assert audit[1][1]["new_visitor_days"] == 730 and audit[1][1]["transcripts_standard"] is True

    # Another tenant still has the standard periods and cannot change this workspace's row.
    other = hq.get(URL, headers=headers(TENANT_B, WORKSPACE_B, EXEC, "owner")).json()
    assert other["revision"] == 0 and other["transcripts"]["days"] == 90
    assert sql(database_engine, "select count(*) from workspace_record_retention") == [(1,)]


def test_refusals_on_postgres(hq: TestClient) -> None:  # noqa: F811
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert hq.put(URL, headers=viewer, json={"transcript_days": 30, "expected_revision": 0, "input_mode": "click"}).status_code == 403
    assert hq.put(URL, headers=OWNER(), json={"transcript_days": 30, "expected_revision": 0, "input_mode": "voice"}).status_code == 403
    assert hq.put(URL, headers=OWNER(), json={"transcript_days": 3, "expected_revision": 0, "input_mode": "click"}).status_code == 422
    assert hq.get(URL, headers=OWNER()).json()["revision"] == 0


def test_app_role_cannot_write_another_workspaces_setting(database_engine: Engine, factory) -> None:  # noqa: F811
    with database_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text(f"set local role {APP_ROLE}"))
                connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"),
                                   {"t": TENANT_B, "w": WORKSPACE_B})
                connection.execute(text("insert into workspace_record_retention (tenant_id, workspace_id, transcript_days, updated_by) "
                                        "values (:t, :w, 30, 'x')"), {"t": TENANT_A, "w": WORKSPACE_A})
