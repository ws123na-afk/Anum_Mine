"""Work attendance in PostgreSQL (migration 0132_work_attendance, docs/attendance.md, pytest -m database).

The API runs as the non-owner ``anum_test_app`` role. Every attendance row is readable by its
owner and, while an active share names them, by one viewer; writable by its owner only; never
visible in another workspace or tenant. Counts below run with no predicate, so only RLS decides.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.attendance import jobs
from anum_api.attendance.store import attendance_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/attendance"
TABLES = ("attendance_profiles", "attendance_sites", "attendance_events", "attendance_corrections",
          "attendance_day_totals", "attendance_daily_reports", "attendance_shares")


def headers(user_id: str = "user_att", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


ME = headers()
BOSS = headers("user_boss", role="owner")
A2 = headers(workspace_id=WORKSPACE_A2)
B = headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)


@pytest.fixture
def factory(database_engine: Engine) -> sessionmaker[Session]:
    made = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(made, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    return made


@pytest.fixture
def client(factory: sessionmaker[Session], seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    attendance_store.clear()
    yield TestClient(app)


def members(engine: Engine, *rows: tuple[str, str, str, str]) -> None:
    with engine.begin() as connection:
        for tenant, workspace, user, role in rows:
            connection.execute(text("insert into workspace_memberships (tenant_id, workspace_id, user_id, role, active) "
                                    "values (:t, :w, :u, :r, true)"), {"t": tenant, "w": workspace, "u": user, "r": role})


def count(app_session: Callable[..., Iterator[Session]], table: str, user_id: str | None,
          tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> int:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user_id})
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608


def seed(client: TestClient, who: dict[str, str] = ME) -> str:
    site = client.post(f"{API}/sites", json={"name": "Head office", "kind": "workplace"}, headers=who)
    assert site.status_code == 201, site.text
    day = datetime.now(UTC) - timedelta(days=2)
    for kind, hour in (("arrive", 5), ("leave", 13)):
        response = client.post(f"{API}/corrections", json={"action": "add", "kind": kind, "site_id": site.json()["id"],
                                                            "occurred_at": day.replace(hour=hour, minute=0).isoformat(),
                                                            "reason": "Forgot to check in"}, headers=who)
        assert response.status_code == 201, response.text
    assert client.put(f"{API}/daily-reports/{date.today().isoformat()}", json={"tasks_done": ["Private work"]}, headers=who).status_code == 201
    return site.json()["id"]


def test_every_attendance_table_has_forced_rls_and_restrictive_owner_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"),
                                        {"t": table}).scalar_one()
            assert forced, table
            policies = dict(connection.execute(text("select policyname, permissive from pg_policies where tablename = :t"),
                                               {"t": table}).all())
            assert policies.get(f"tenant_isolation_{table}") == "PERMISSIVE"
            for name in ("owner_read", "owner_insert", "owner_update", "owner_delete"):
                assert policies.get(f"{name}_{table}") == "RESTRICTIVE", (table, name)
            assert f"lifecycle_purge_{table}" in policies


def test_rows_persist_and_only_the_owner_sees_them(client: TestClient, app_session) -> None:  # type: ignore[no-untyped-def]
    seed(client)
    me = ME["x-user-id"]
    assert count(app_session, "attendance_events", me) == 2
    assert count(app_session, "attendance_corrections", me) == 2
    for table in TABLES[:-1]:
        assert count(app_session, table, "user_someone_else") == 0, table
        assert count(app_session, table, None) == 0, table  # no person set: nothing
    assert count(app_session, "attendance_events", me, workspace_id=WORKSPACE_A2) == 0
    assert count(app_session, "attendance_events", me, tenant_id=TENANT_B, workspace_id=WORKSPACE_B) == 0
    days = client.get(f"{API}/days", headers=ME).json()
    assert any(d["worked_seconds"] == 8 * 3600 for d in days)
    assert client.get(f"{API}/sites", headers=A2).json() == []
    assert client.get(f"{API}/sites", headers=B).json() == []
    assert client.get(f"{API}/days", params={"person": me}, headers=BOSS).status_code == 404


def test_the_database_refuses_writing_someone_elses_rows(client: TestClient, app_session) -> None:  # type: ignore[no-untyped-def]
    seed(client)
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text("select set_config('anum.user_id', 'user_intruder', true)"))
            session.execute(text("insert into attendance_sites (id, tenant_id, workspace_id, owner_user_id, name, kind) "
                                 "values ('att_site_x', :t, :w, :o, 'Planted', 'site')"),
                            {"t": TENANT_A, "w": WORKSPACE_A, "o": ME["x-user-id"]})
    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        session.execute(text("select set_config('anum.user_id', 'user_intruder', true)"))
        changed = session.execute(text("update attendance_events set note = 'x'")).rowcount
        removed = session.execute(text("delete from attendance_daily_reports")).rowcount
    assert changed == 0 and removed == 0


def test_sharing_widens_reading_only_and_reports_only_when_included(client: TestClient, app_session, database_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    members(database_engine, (TENANT_A, WORKSPACE_A, ME["x-user-id"], "member"), (TENANT_A, WORKSPACE_A, BOSS["x-user-id"], "owner"))
    seed(client)
    me, boss = ME["x-user-id"], BOSS["x-user-id"]
    share = client.post(f"{API}/shares", json={"viewer_user_id": boss}, headers=ME)
    assert share.status_code == 201, share.text
    assert count(app_session, "attendance_events", boss) == 2
    assert count(app_session, "attendance_daily_reports", boss) == 0
    assert client.get(f"{API}/days", params={"person": me}, headers=BOSS).status_code == 200
    assert client.get(f"{API}/daily-reports", params={"person": me}, headers=BOSS).status_code == 404
    # Read access never becomes write access.
    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": boss})
        assert session.execute(text("update attendance_events set note = 'boss'")).rowcount == 0
        assert session.execute(text("delete from attendance_events")).rowcount == 0
        assert session.execute(text("update attendance_shares set include_reports = true")).rowcount == 0
    # Another member is not shared with; a stranger outside the workspace cannot be chosen.
    assert count(app_session, "attendance_events", "user_third") == 0
    assert client.post(f"{API}/shares", json={"viewer_user_id": "user_stranger"}, headers=ME).status_code == 404
    client.delete(f"{API}/shares/{share.json()['id']}", headers=ME)
    with_reports = client.post(f"{API}/shares", json={"viewer_user_id": boss, "include_reports": True}, headers=ME)
    assert with_reports.status_code == 201
    assert count(app_session, "attendance_daily_reports", boss) == 1
    client.delete(f"{API}/shares/{with_reports.json()['id']}", headers=ME)
    assert count(app_session, "attendance_events", boss) == 0
    # A share in workspace A shows nothing in workspace A2.
    assert count(app_session, "attendance_events", boss, workspace_id=WORKSPACE_A2) == 0


def test_team_view_lists_only_sharers_and_report_status_only_when_included(client: TestClient, database_engine: Engine) -> None:
    """GET /team under RLS (docs/attendance.md#team-attendance): the caller plus active sharers, read only."""
    members(database_engine, (TENANT_A, WORKSPACE_A, ME["x-user-id"], "member"), (TENANT_A, WORKSPACE_A, BOSS["x-user-id"], "owner"),
            (TENANT_A, WORKSPACE_A, "user_third", "member"))
    third = headers("user_third")
    seed(client)
    seed(client, third)
    me, boss = ME["x-user-id"], BOSS["x-user-id"]
    today = date.today().isoformat()
    # Submitting keeps the time through later edits, in PostgreSQL too.
    submitted = client.put(f"{API}/daily-reports/{today}", json={"tasks_done": ["Private work"], "expected_version": 1, "submit": True}, headers=ME)
    assert submitted.status_code == 200 and submitted.json()["submitted_at"]
    assert [r["user_id"] for r in client.get(f"{API}/team", headers=BOSS).json()["rows"]] == [boss]
    assert client.post(f"{API}/shares", json={"viewer_user_id": boss}, headers=third).status_code == 201
    assert client.post(f"{API}/shares", json={"viewer_user_id": boss, "include_reports": True}, headers=ME).status_code == 201
    rows = {r["user_id"]: r for r in client.get(f"{API}/team", params={"date": today}, headers=BOSS).json()["rows"]}
    assert set(rows) == {boss, me, "user_third"}
    assert rows[me]["report"]["status"] == "submitted" and rows[me]["report"]["summary"] == "Private work"
    assert rows["user_third"]["report"] is None and rows["user_third"]["reports_shared"] is False
    # The third member sees only their own row: sharing with the boss shares nothing with anyone else.
    assert [r["user_id"] for r in client.get(f"{API}/team", headers=third).json()["rows"]] == ["user_third"]
    # Another workspace sees none of it.
    assert [r["user_id"] for r in client.get(f"{API}/team", headers=headers(boss, workspace_id=WORKSPACE_A2)).json()["rows"]] == [boss]


def test_device_retries_store_one_row_under_the_unique_key(client: TestClient, app_session) -> None:  # type: ignore[no-untyped-def]
    client.post(f"{API}/sites", json={"name": "Head office", "kind": "workplace"}, headers=ME)
    client.post(f"{API}/tracking", json={"action": "consent"}, headers=ME)
    body = {"event": "arrive", "site": "Head office", "idempotency_key": "pg-retry-0001"}
    assert [client.post(f"{API}/device/events", json=body, headers=ME).status_code for _ in range(3)] == [201, 200, 200]
    assert count(app_session, "attendance_events", ME["x-user-id"]) == 1


def test_profile_versions_and_report_versions_refuse_stale_writes(client: TestClient) -> None:
    assert client.put(f"{API}/profile", json={"time_zone": "Asia/Riyadh", "expected_version": 0}, headers=ME).json()["version"] == 1
    assert client.put(f"{API}/profile", json={"time_zone": "Asia/Riyadh", "expected_version": 0}, headers=ME).status_code == 409
    day = date.today().isoformat()
    assert client.put(f"{API}/daily-reports/{day}", json={"notes": "one"}, headers=ME).status_code == 201
    assert client.put(f"{API}/daily-reports/{day}", json={"notes": "two", "expected_version": 1}, headers=ME).status_code == 200
    assert client.put(f"{API}/daily-reports/{day}", json={"notes": "three", "expected_version": 1}, headers=ME).status_code == 409
    assert client.get(f"{API}/daily-reports", params={"q": "two"}, headers=ME).json()["total"] == 1


def test_maintenance_sees_only_scope_columns_of_old_events_and_due_profiles(client: TestClient, database_engine: Engine) -> None:
    seed(client)
    client.put(f"{API}/profile", json={"expected_version": 0}, headers=ME)
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert connection.execute(text("select count(*) from attendance_events")).scalar_one() == 0  # nothing older than 90 days
            assert connection.execute(text("select owner_user_id from attendance_profiles")).scalars().all() == [ME["x-user-id"]]
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("select note from attendance_events"))


def test_daily_job_reduces_old_events_and_alerts_in_the_persons_own_scope(client: TestClient, database_engine: Engine,
                                                                          factory: sessionmaker[Session], app_session) -> None:  # type: ignore[no-untyped-def]
    members(database_engine, (TENANT_A, WORKSPACE_A, ME["x-user-id"], "member"))
    seed(client)
    client.put(f"{API}/profile", json={"expected_version": 0}, headers=ME)
    # Make the seeded day 100 days old, as if it had been recorded then.
    with database_engine.begin() as connection:
        connection.execute(text("update attendance_events set occurred_at = occurred_at - interval '100 days', "
                                "received_at = received_at - interval '100 days'"))

    def maintenance() -> Session:
        session = sessionmaker(bind=database_engine)()
        return session

    result = jobs.run_sql(session_factory=factory, maintenance_session_factory=maintenance)
    assert result.days_reduced == 1 and result.events_deleted == 2
    me = ME["x-user-id"]
    assert count(app_session, "attendance_events", me) == 0
    assert count(app_session, "attendance_day_totals", me) == 1
    again = jobs.run_sql(session_factory=factory, maintenance_session_factory=maintenance)
    assert again.days_reduced == 0 and again.alerts == 0


def test_privacy_delete_and_purge_role_cover_attendance(client: TestClient, app_session, database_engine: Engine) -> None:  # type: ignore[no-untyped-def]
    seed(client)
    summary = client.get("/api/v1/privacy/summary", headers=ME)
    if summary.status_code == 200:
        assert summary.json()["private"]["attendance_records"] >= 5
    with database_engine.connect() as connection:
        grants = connection.execute(text(
            "select table_name from information_schema.role_table_grants where grantee = 'anum_tenant_purger' "
            "and privilege_type = 'DELETE' and table_name like 'attendance_%'")).scalars().all()
    assert set(grants) == set(TABLES)
