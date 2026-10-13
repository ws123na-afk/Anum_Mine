"""The TyrePulse reader SQL and ANUM's Supabase sign-in source, on real PostgreSQL.

``infra/tyrepulse/supabase-anum-reader.sql`` is run, as written, against a database shaped
like TyrePulse's Supabase project (``supabase_emulation``: Supabase's roles, ``auth.uid()``
from the JWT claims, TyrePulse's ``app_current_org()`` and RESTRICTIVE org and country
policies). The tests prove what the docs promise the owner:

* the old permanent ``request.jwt.claims`` role setting is refused (42501, as on Supabase)
  and the file removes that half-made ``anum_reader`` login;
* the reader user sees only its own organisation's rows of the allowed tables, even when
  its profile is later made a super admin, and nothing else;
* every write is refused: by policy for INSERT / UPDATE / DELETE, by the pre-request guard
  for any non-GET request, /rpc, GraphQL and other tables, and GET runs read-only so even a
  SECURITY DEFINER function cannot write;
* TyrePulse's own users are unaffected;
* ANUM signs in, discovers, previews and syncs through a PostgREST stand-in that runs each
  request against this database, and gets organisation A's data only;
* the extend file (``supabase-anum-reader-extend.sql``) adds the operational tables
  (inspections, work orders, checklists...) under the same pin: own organisation only, GET
  only, no writes; it skips a table without ``organisation_id`` and never adds personal data
  (``drivers``); its rollback takes them off again;
* the rollback file removes everything, and the file can be applied again.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import make_url

from anum_api.tyre import sources
from supabase_emulation import (
    OPERATION_TABLES,
    ORG,
    ORG_B,
    PUBLISHABLE_KEY,
    USERS,
    FakePostgrest,
    SupabaseProject,
    as_user,
    create_project,
    drop_project_objects,
    run_file,
)
from test_postgres_tyre import API, app_factory, client, headers  # noqa: F401 - fixtures reused

pytestmark = pytest.mark.database

ROOT = Path(__file__).resolve().parents[3]
READER_SQL = str(ROOT / "infra" / "tyrepulse" / "supabase-anum-reader.sql")
ROLLBACK_SQL = str(ROOT / "infra" / "tyrepulse" / "supabase-anum-reader-rollback.sql")
EXTEND_SQL = str(ROOT / "infra" / "tyrepulse" / "supabase-anum-reader-extend.sql")
EXTEND_ROLLBACK_SQL = str(ROOT / "infra" / "tyrepulse" / "supabase-anum-reader-extend-rollback.sql")
DATABASE = "anum_test_tyrepulse"
READER = USERS["reader"]
URL = "https://tyrepulse.example.supabase.co"


def _superuser_conninfo() -> str:
    url = make_url(os.environ["ANUM_TEST_DATABASE_URL"])
    return psycopg.conninfo.make_conninfo(host=url.host or "127.0.0.1", port=url.port or 5432,
                                          user=url.username, password=url.password, dbname=url.database)


@pytest.fixture(scope="module")
def project() -> Iterator[tuple[SupabaseProject, str, list[str], list[str]]]:
    superuser = _superuser_conninfo()
    with psycopg.connect(superuser, autocommit=True) as conn:
        if not conn.execute("select rolsuper from pg_roles where rolname = current_user").fetchone()[0]:  # type: ignore[index]
            pytest.fail("ANUM_TEST_DATABASE_URL must be a superuser to build the Supabase stand-in")
    built, old_state = create_project(superuser, DATABASE)
    notices = run_file(built, READER_SQL)
    run_file(built, READER_SQL)  # idempotent
    extend_notices = run_file(built, EXTEND_SQL)
    run_file(built, EXTEND_SQL)  # idempotent
    with built.connect() as conn:
        conn.execute("select * from anum_integration.register_reader(%s)", (READER.email,))
    yield built, old_state, notices, extend_notices
    with psycopg.connect(superuser, autocommit=True) as conn:
        drop_project_objects(conn, DATABASE)


@pytest.fixture(autouse=True)
def fresh_sessions() -> Iterator[None]:
    sources.reset_auth_sessions()
    yield
    sources.reset_auth_sessions()


def _rows(project: SupabaseProject, user_key: str, table: str) -> list[tuple[str | None]]:
    state, rows = as_user(project, USERS[user_key], "GET", f"/{table}",
                          f"select organisation_id::text from public.{table}")  # nosec B608 - test constants
    assert state == "ok", rows
    return rows  # type: ignore[no-any-return]


def test_the_permanent_jwt_claims_setting_is_refused_and_the_old_login_is_removed(project) -> None:  # type: ignore[no-untyped-def]
    built, old_state, notices, _ = project
    # What the old instructions asked for fails exactly as on Supabase.
    assert old_state == "42501"
    assert "anum_reader: grants revoked and role dropped" in notices
    assert "pre-request guard: installed" in notices
    with built.connect() as conn:
        assert conn.execute("select 1 from pg_roles where rolname = 'anum_reader'").fetchone() is None
        # Nothing was disabled or bypassed, and nothing executes as definer.
        assert conn.execute("select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                            "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity").fetchone() == (0,)
        assert conn.execute("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                            "where n.nspname = 'anum_integration' and p.prosecdef").fetchone() == (0,)
        assert conn.execute("select rolbypassrls from pg_roles where rolname = 'authenticated'").fetchone() == (False,)
        policies = conn.execute("select count(*) from pg_policy where polname like 'anum_reader_%' "
                                "and polpermissive").fetchone()
        assert policies == (0,)  # every reader policy is restrictive: it can only narrow


def test_register_reader_refuses_administrators_and_other_organisations(project) -> None:  # type: ignore[no-untyped-def]
    built, _, _, _ = project
    with built.connect() as conn:
        for email, organisation, message in (
            (USERS["owner"].email, None, "administrator"),
            (READER.email, ORG_B, "belongs to organisation"),
            ("nobody@example.com", None, "No Supabase user"),
        ):
            with pytest.raises(psycopg.errors.RaiseException, match=message):
                conn.execute("select * from anum_integration.register_reader(%s, %s)", (email, organisation))
        assert conn.execute("select user_id::text, organisation_id::text from anum_integration.readers").fetchall() == [
            (READER.id, ORG)]


def test_verify_reader_passes_every_check(project) -> None:  # type: ignore[no-untyped-def]
    built, _, _, _ = project
    with built.connect() as conn:
        checks = conn.execute("select check_name, status, detail from anum_integration.verify_reader(%s)",
                              (READER.email,)).fetchall()
    failing = [check for check in checks if check[1] != "pass"]
    assert not failing, failing
    names = {check[0] for check in checks}
    assert {"read tyre_records", "insert tyre_records", "update tyre_records", "delete tyre_records",
            "api POST /rpc/anything", "api guard installed", "old anum_reader login removed"} <= names


def test_the_reader_sees_only_its_organisation(project) -> None:  # type: ignore[no-untyped-def]
    built, _, _, _ = project
    for table in ("vehicle_fleet", "tyre_records", "tyre_service_events", "tpms_readings", "suppliers", *OPERATION_TABLES):
        assert {row[0] for row in _rows(built, "reader", table)} == {ORG}
    # TyrePulse's own users keep their view: staff of A also see A's unassigned suppliers,
    # B sees only B.
    assert {row[0] for row in _rows(built, "staff", "suppliers")} == {ORG, None}
    assert {row[0] for row in _rows(built, "other", "tyre_records")} == {ORG_B}
    # A table outside the allow-list shows nothing, even through TyrePulse's own policies.
    state, rows = as_user(built, READER, "GET", "/tyre_records", "select count(*) from public.audit_events")
    assert (state, rows) == ("ok", [(0,)])
    # Even if the reader's profile is made a super admin later, the pin holds.
    with built.connect() as conn:
        conn.execute("update public.profiles set is_super_admin = true where id = %s", (READER.id,))
    try:
        assert {row[0] for row in _rows(built, "reader", "tyre_records")} == {ORG}
        assert {row[0] for row in _rows(built, "owner", "tyre_records")} == {ORG, ORG_B, None}
    finally:
        with built.connect() as conn:
            conn.execute("update public.profiles set is_super_admin = false where id = %s", (READER.id,))


def test_every_write_by_the_reader_is_refused(project) -> None:  # type: ignore[no-untyped-def]
    built, _, _, _ = project
    insert = "insert into public.tyre_records (id, serial_no) values ('fake-new', 'fake-sn-new')"
    update = "update public.tyre_records set brand = 'changed'"
    delete = "delete from public.tyre_records"
    # The API guard refuses every non-GET request, functions, GraphQL and other tables.
    for method, path in (("POST", "/tyre_records"), ("PATCH", "/tyre_records"), ("DELETE", "/tyre_records"),
                         ("POST", "/rpc/purge_tyre_records"), ("GET", "/rpc/purge_tyre_records"),
                         ("POST", "/rpc/graphql"), ("GET", "/audit_events"), ("GET", "/")):
        assert as_user(built, READER, method, path, "select 1") == ("error", "PT403"), (method, path)
    # Behind the guard, row level security refuses the writes on its own.
    assert as_user(built, READER, "POST", "/tyre_records", insert, guard=False) == ("error", "42501")
    assert as_user(built, READER, "PATCH", "/tyre_records", update, guard=False) == ("ok", 0)
    assert as_user(built, READER, "DELETE", "/tyre_records", delete, guard=False) == ("ok", 0)
    # A GET runs read-only: not even a SECURITY DEFINER function can write.
    assert as_user(built, READER, "GET", "/tyre_records", "select public.purge_tyre_records()") == ("error", "25006")
    # TyrePulse's own staff still write as before (rolled back by as_user).
    assert as_user(built, USERS["staff"], "POST", "/tyre_records", insert) == ("ok", 1)
    assert as_user(built, USERS["staff"], "PATCH", "/tyre_records", update)[1] == 32
    # Nothing changed.
    with built.connect() as conn:
        assert conn.execute("select count(*) from public.tyre_records").fetchone() == (65,)
        assert conn.execute("select count(*) from public.tyre_records where brand = 'changed'").fetchone() == (0,)


def test_anum_signs_in_and_reads_only_organisation_a(project, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    built, _, _, _ = project
    fake = FakePostgrest(built)
    monkeypatch.setattr(sources, "rest_client_factory", fake.client_factory())
    login = f'{{"api_key": "{PUBLISHABLE_KEY}", "email": "{READER.email}", "password": "example"}}'
    source = sources.SupabaseAuthSource(URL, login)
    assert "Signed in as anum-reader@example.com" in asyncio.run(source.test())
    tables = {table.name: table for table in asyncio.run(source.discover())}
    assert {"vehicle_fleet", "tyre_records", "tyre_service_events", "tpms_readings", "suppliers",
            *OPERATION_TABLES} <= set(tables)
    fetched = asyncio.run(source.fetch("tyre_records", ["id", "organisation_id"]))
    assert len(fetched.rows) == 32 and {row["organisation_id"] for row in fetched.rows} == {ORG}
    # ANUM sent only GETs to tables, plus the sign-in; the openapi root was refused by the guard.
    assert {method for method, path in fake.requests if path.startswith("/rest/v1")} == {"GET"}
    assert all("/rpc" not in path for _, path in fake.requests)
    assert {(m, p) for m, p in fake.requests if p.startswith("/auth")} <= {("POST", "/auth/v1/token"), ("GET", "/auth/v1/user")}
    # A wrong password says so.
    sources.reset_auth_sessions()
    wrong = sources.SupabaseAuthSource(URL, login.replace('"example"}', '"wrong"}'))
    with pytest.raises(sources.TyreSourceError, match="refused the reader's e-mail or password"):
        asyncio.run(wrong.test())


def test_anum_syncs_tyrepulse_through_the_reader(  # type: ignore[no-untyped-def]
    project, client: TestClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811 - fixture
) -> None:
    built, _, _, _ = project
    fake = FakePostgrest(built)
    monkeypatch.setattr(sources, "rest_client_factory", fake.client_factory())
    owner = headers()
    saved = client.put(f"{API}/connection", headers=owner, json={
        "kind": "supabase_auth", "label": "TyrePulse", "endpoint": URL,
        "secret": f'{{"api_key": "{PUBLISHABLE_KEY}", "email": "{READER.email}", "password": "example"}}'})
    assert saved.status_code == 200, saved.text
    connection = saved.json()["connection"]
    assert connection["kind"] == "supabase_auth" and connection["secret_hint"] == f"{READER.email} · key …mple"
    assert "example\"" not in saved.text and "password" not in connection["secret_hint"]
    tested = client.post(f"{API}/connection/test", headers=owner).json()
    assert tested["ok"] is True and "tyre_records 1" in tested["message"]
    found = client.post(f"{API}/discover", headers=owner).json()
    assert found["suggestion"]["preset"] == "tyrepulse"
    preview = client.post(f"{API}/mapping/preview", headers=owner, json=found["suggestion"])
    assert preview.status_code == 200, preview.text
    assert client.put(f"{API}/mapping", headers=owner, json=found["suggestion"]).status_code == 200
    synced = client.post(f"{API}/sync", headers=owner)
    assert synced.status_code == 200, synced.text
    # Organisation A only: B's copy and the unassigned rows never reach ANUM.
    assert synced.json()["setup"]["sync"]["counts"]["tyres"] == 32
    assert client.get(f"{API}/dashboard", headers=owner).json()["summary"]["tyres_fitted"] == 24
    counts = synced.json()["setup"]["sync"]["counts"]
    # Readings from TyrePulse's inspections; work orders within the 90-day window; A only.
    assert counts["inspection_visits"] == 24 and counts["inspections"] == 96 and counts["work_orders"] == 3
    assert counts["checklists"] == 3 and counts["inspection_schedules"] == 3 and counts["sites"] == 1
    window = [path for method, path in fake.requests if path == "/rest/v1/work_orders"]
    assert window and fake.sign_ins == 1  # one sign-in, reused for every read


def test_the_extend_file_adds_operational_tables_read_only_and_org_scoped(project) -> None:  # type: ignore[no-untyped-def]
    built, _, _, notices = project
    assert "inspections: added: own organisation only, GET only, no writes" in notices
    assert any(n.startswith("ksa_kms: SKIPPED: no uuid organisation_id") for n in notices)
    assert "uae_kms: SKIPPED: no such table in public" in notices
    with built.connect() as conn:
        listed = {row[0] for row in conn.execute("select table_name from anum_integration.reader_tables")}
        # Personal data is never listed, even when added by hand and the file is run again.
        conn.execute("insert into anum_integration.reader_tables values ('drivers')")
    assert set(OPERATION_TABLES) <= listed and not {"drivers", "ksa_kms", "profiles"} & listed
    pruned = run_file(built, EXTEND_SQL)
    assert "drivers: removed from the reader tables (accounts, security or personal data)" in pruned
    # Reads: organisation A only; the excluded tables show nothing and the API guard refuses them.
    assert {row[0] for row in _rows(built, "reader", "work_orders")} == {ORG}
    assert {row[0] for row in _rows(built, "reader", "inspections")} == {ORG}
    assert as_user(built, READER, "GET", "/work_orders", "select count(*) from public.drivers") == ("ok", [(0,)])
    assert as_user(built, READER, "GET", "/work_orders", "select count(*) from public.ksa_kms") == ("ok", [(0,)])
    for path in ("/drivers", "/ksa_kms", "/profiles"):
        assert as_user(built, READER, "GET", path, "select 1") == ("error", "PT403"), path
    # Writes on a new table: refused by the guard and, behind it, by row level security.
    assert as_user(built, READER, "PATCH", "/work_orders", "update public.work_orders set status = 'x'") == ("error", "PT403")
    assert as_user(built, READER, "POST", "/inspections", "insert into public.inspections (id) values ('fake-new')",
                   guard=False) == ("error", "42501")
    assert as_user(built, READER, "PATCH", "/work_orders", "update public.work_orders set status = 'x'", guard=False) == ("ok", 0)
    assert as_user(built, READER, "DELETE", "/inspections", "delete from public.inspections", guard=False) == ("ok", 0)
    # TyrePulse's own staff are unaffected.
    assert {row[0] for row in _rows(built, "staff", "work_orders")} == {ORG}
    # The extend rollback takes the operational tables off again; the first file's tables stay.
    run_file(built, EXTEND_ROLLBACK_SQL)
    assert _rows(built, "reader", "vehicle_fleet")
    assert as_user(built, READER, "GET", "/work_orders", "select 1") == ("error", "PT403")
    assert as_user(built, READER, "GET", "/vehicle_fleet", "select count(*) from public.work_orders") == ("ok", [(0,)])
    run_file(built, EXTEND_SQL)
    assert {row[0] for row in _rows(built, "reader", "work_orders")} == {ORG}


def test_rollback_removes_everything_and_the_file_applies_again(project) -> None:  # type: ignore[no-untyped-def]
    built, _, _, _ = project
    run_file(built, ROLLBACK_SQL)
    run_file(built, ROLLBACK_SQL)  # idempotent
    with built.connect() as conn:
        assert conn.execute("select count(*) from pg_policy where polname like 'anum_reader_%'").fetchone() == (0,)
        assert conn.execute("select 1 from pg_namespace where nspname = 'anum_integration'").fetchone() is None
        setting = conn.execute("select count(*) from pg_db_role_setting s join pg_roles r on r.oid = s.setrole, "
                               "unnest(s.setconfig) c where r.rolname = 'authenticator' "
                               "and c like 'pgrst.db_pre_request=%'").fetchone()
        assert setting == (0,)
    # Without the pin the user is an ordinary TyrePulse user again.
    assert {row[0] for row in _rows_unguarded(built)} == {ORG}
    run_file(built, READER_SQL)
    with built.connect() as conn:
        conn.execute("select * from anum_integration.register_reader(%s)", (READER.email,))
        statuses = {row[0] for row in conn.execute("select status from anum_integration.verify_reader(%s)",
                                                    (READER.email,)).fetchall()}
    assert statuses == {"pass"}


def _rows_unguarded(project: SupabaseProject) -> list[tuple[str | None]]:
    state, rows = as_user(project, READER, "GET", "/tyre_records",
                          "select organisation_id::text from public.tyre_records", guard=False)
    assert state == "ok", rows
    return rows  # type: ignore[no-any-return]
