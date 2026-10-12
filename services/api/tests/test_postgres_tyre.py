"""Tyre planning in PostgreSQL (migration 0037_tyre_planning).

The API runs as the non-owner ``anum_test_app`` role. Every tyre table is under forced
tenant and workspace RLS; credentials are stored only as Fernet ciphertext; key rotation
covers them; and the Supabase PostgreSQL connector is exercised against a real
PostgreSQL schema shaped like TyrePulse's, through a SELECT-only login.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import secret_box
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.rotate_secrets import rotate_tyre_credentials
from anum_api.secret_box import SecretCipher, reset_secret_cipher
from anum_api.settings import settings
from anum_api.tyre import api as tyre_api
from anum_api.tyre import service, sources, tools
from anum_api.tyre.store import memory_tyre_store
from tyre_fixtures import AS_OF, FakeSupabase, build_rows

pytestmark = pytest.mark.database

API = "/api/v1/tyre"
NOW = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 12, tzinfo=timezone.utc)
TABLES = ("tyre_connections", "tyre_snapshots", "tyre_alerts", "tyre_reports")
READER = "anum_test_tyre_reader"
FIXTURE_SCHEMA = "tyrepulse_fixture"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": f"user_{role}", "x-user-roles": role}


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    fake = FakeSupabase()
    monkeypatch.setattr(sources, "rest_client_factory", fake.client_factory())
    for module in (tyre_api, service, tools):
        monkeypatch.setattr(module, "utc_now", lambda: NOW)
    memory_tyre_store.clear()
    yield factory


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def connect_and_sync(client: TestClient, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> None:
    owner = headers(tenant_id, workspace_id)
    assert client.put(f"{API}/connection", headers=owner, json={
        "kind": "supabase_rest", "endpoint": "https://example.supabase.co", "secret": "example-key"}).status_code == 200
    suggestion = client.post(f"{API}/discover", headers=owner).json()["suggestion"]
    assert client.put(f"{API}/mapping", headers=owner, json=suggestion).status_code == 200
    synced = client.post(f"{API}/sync", headers=owner)
    assert synced.status_code == 200, synced.text


def _count(app_session: Callable[..., Iterator[Session]], table: str, tenant_id: str, workspace_id: str) -> int:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        # No predicate: only RLS decides what is visible.
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608 - test constant


def test_the_whole_flow_persists_and_secrets_are_ciphertext(client: TestClient, database_engine: Engine) -> None:
    connect_and_sync(client)
    report = client.post(f"{API}/reports", headers=headers(role="member"), json={"kind": "fleet_health"})
    assert report.status_code == 201
    memory_tyre_store.clear()  # nothing comes from process memory
    dashboard = client.get(f"{API}/dashboard", headers=headers(role="viewer")).json()
    assert dashboard["summary"]["tyres_fitted"] == 24 and dashboard["alert_counts"] == {"critical": 2, "warning": 4}
    assert client.get(f"{API}/reports", headers=headers(role="viewer")).json()["total"] == 1
    with database_engine.connect() as connection:
        ciphertext = connection.execute(text("select secret_ciphertext from tyre_connections")).scalar_one()
        dataset = connection.execute(text("select dataset from tyre_snapshots")).scalar_one()
    assert "example-key" not in ciphertext and secret_box.secret_cipher().decrypt(ciphertext) == "example-key"
    assert len(dataset["tyres"]) == 32
    # Acknowledge and re-sync keeps one active row per key.
    alert = client.get(f"{API}/alerts", headers=headers()).json()["items"][0]
    assert client.post(f"{API}/alerts/{alert['id']}/acknowledge", headers=headers(role="member")).status_code == 200
    client.post(f"{API}/sync", headers=headers())
    assert client.get(f"{API}/alerts", headers=headers()).json()["total"] == 6
    assert client.delete(f"{API}/connection", headers=headers()).status_code == 204
    with database_engine.connect() as connection:
        remaining = {table: connection.execute(text(f"select count(*) from {table}")).scalar_one() for table in TABLES}  # nosec B608
    assert remaining == {"tyre_connections": 0, "tyre_snapshots": 0, "tyre_alerts": 0, "tyre_reports": 1}


def test_tyre_tables_are_isolated_by_tenant_and_workspace(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    connect_and_sync(client)
    client.post(f"{API}/reports", headers=headers(), json={"kind": "scrap_reasons"})
    for table in TABLES:
        assert _count(app_session, table, TENANT_A, WORKSPACE_A) >= 1, table
        assert _count(app_session, table, TENANT_B, WORKSPACE_B) == 0, table
        assert _count(app_session, table, TENANT_A, "workspace_test_a2") == 0, table
    other = headers(TENANT_B, WORKSPACE_B)
    assert client.get(f"{API}/setup", headers=other).json()["connection"] is None
    assert client.get(f"{API}/alerts", headers=other).json()["total"] == 0
    report_id = client.get(f"{API}/reports", headers=headers()).json()["items"][0]["id"]
    assert client.get(f"{API}/reports/{report_id}", headers=other).status_code == 404

    # Writing a row into another tenant's scope is refused by WITH CHECK.
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text(
                "insert into tyre_reports (id, tenant_id, workspace_id, kind, title, created_by, created_at, source, content) "
                "values ('r1', :t, :w, 'fleet_health', 'x', 'u', now(), 'manual', '{}')"), {"t": TENANT_B, "w": WORKSPACE_B})
    # Another tenant's statement changes nothing.
    with app_session(tenant_context(TENANT_B, WORKSPACE_B), commit=True) as session:
        assert session.execute(text("update tyre_connections set label = 'stolen'")).rowcount == 0
        assert session.execute(text("delete from tyre_alerts")).rowcount == 0


def test_a_resolved_alert_can_be_raised_again(client: TestClient, database_engine: Engine) -> None:
    connect_and_sync(client)
    setup = client.get(f"{API}/setup", headers=headers()).json()
    lenient = {**setup["assumptions"], "pressure_tolerance_pct": 50}
    client.put(f"{API}/assumptions", headers=headers(), json=lenient)
    client.put(f"{API}/assumptions", headers=headers(), json=setup["assumptions"])
    with database_engine.connect() as connection:
        rows = connection.execute(text(
            "select status, count(*) from tyre_alerts where kind = 'under_inflation' group by status order by status")).all()
    assert [tuple(row) for row in rows] == [("open", 1), ("resolved", 1)]


def test_key_rotation_covers_tyre_credentials(
    client: TestClient, app_factory: sessionmaker[Session], database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    old, new = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setattr(settings, "secrets_key", SecretStr(old.decode()))
    reset_secret_cipher()
    try:
        connect_and_sync(client)
        with database_engine.connect() as connection:
            before = connection.execute(text("select secret_ciphertext from tyre_connections")).scalar_one()
        report = rotate_tyre_credentials(SecretCipher([new, old]), session_factory=app_factory)
        assert report.rotated == 1 and report.failed == 0
        with database_engine.connect() as connection:
            after = connection.execute(text("select secret_ciphertext from tyre_connections")).scalar_one()
            audits = connection.execute(text(
                "select target from audit_records where action = 'secrets.rotated'")).scalars().all()
        assert after != before and SecretCipher([new]).decrypt(after) == "example-key"
        assert audits == [f"tyre_connection:{WORKSPACE_A}"]
        assert rotate_tyre_credentials(SecretCipher([new, old]), session_factory=app_factory).already_current == 1
    finally:
        reset_secret_cipher()


def _value(column: str, value: object) -> object:
    if column.endswith("date") and isinstance(value, str):
        return date.fromisoformat(value)
    return value


@pytest.fixture
def tyrepulse_database(database_engine: Engine) -> Iterator[str]:
    """A TyrePulse-shaped schema and a SELECT-only login; returns its connection string."""
    rows = build_rows()
    with database_engine.begin() as connection:
        connection.execute(text(f"drop schema if exists {FIXTURE_SCHEMA} cascade"))
        connection.execute(text(f"create schema {FIXTURE_SCHEMA}"))
        connection.execute(text(
            f"create table {FIXTURE_SCHEMA}.vehicle_fleet (asset_no text primary key, registration_no text, "
            "fleet_number text, vehicle_type text, site text, current_km numeric, status text)"))
        connection.execute(text(
            f"create table {FIXTURE_SCHEMA}.tyre_records (id text primary key, serial_no text, brand text, size text, "
            "asset_no text, tyre_position text, status text, issue_date date, km_at_fitment numeric, removal_date date, "
            "km_at_removal numeric, removal_reason text, total_km numeric, cost_per_tyre numeric, supplier text)"))
        connection.execute(text(
            f"create table {FIXTURE_SCHEMA}.tyre_service_events (id text primary key, tyre_serial text, asset_no text, "
            "position text, event_type text, event_date date, tread_depth numeric, pressure numeric, cost numeric)"))
        for table, columns in (
            ("vehicle_fleet", ("asset_no", "registration_no", "fleet_number", "vehicle_type", "site", "current_km", "status")),
            ("tyre_records", ("id", "serial_no", "brand", "size", "asset_no", "tyre_position", "status", "issue_date",
                              "km_at_fitment", "removal_date", "km_at_removal", "removal_reason", "total_km",
                              "cost_per_tyre", "supplier")),
            ("tyre_service_events", ("id", "tyre_serial", "asset_no", "position", "event_type", "event_date",
                                     "tread_depth", "pressure", "cost")),
        ):
            statement = text(
                f"insert into {FIXTURE_SCHEMA}.{table} ({', '.join(columns)}) "  # nosec B608 - test constants
                f"values ({', '.join(':' + c for c in columns)})")
            connection.execute(statement, [{c: _value(c, row.get(c)) for c in columns} for row in rows[table]])
        connection.execute(text(f"""
            do $$ begin
              if not exists (select 1 from pg_roles where rolname = '{READER}') then
                create role {READER} login password 'example';
              end if;
            end $$"""))
        connection.execute(text(f"grant usage on schema {FIXTURE_SCHEMA} to {READER}"))
        connection.execute(text(f"grant select on all tables in schema {FIXTURE_SCHEMA} to {READER}"))
    url = make_url(str(database_engine.url))
    yield f"postgresql://{READER}:example@{url.host}:{url.port}/{url.database}?sslmode=prefer"
    with database_engine.begin() as connection:
        connection.execute(text(f"drop schema if exists {FIXTURE_SCHEMA} cascade"))
        connection.execute(text(f"drop owned by {READER}"))
        connection.execute(text(f"drop role if exists {READER}"))


def test_supabase_postgres_connector_reads_a_real_database_read_only(
    client: TestClient, tyrepulse_database: str, database_engine: Engine
) -> None:
    import asyncio

    source = sources.SupabasePostgresSource(tyrepulse_database, FIXTURE_SCHEMA)
    assert "read-only session: on" in asyncio.run(source.test())
    tables = {table.name: table for table in asyncio.run(source.discover())}
    assert set(tables) == {f"{FIXTURE_SCHEMA}.{name}" for name in ("vehicle_fleet", "tyre_records", "tyre_service_events")}
    assert not any(table.writable for table in tables.values())
    fetched = asyncio.run(source.fetch(f"{FIXTURE_SCHEMA}.tyre_records", ["serial_no", "brand"]))
    assert len(fetched.rows) == 32 and fetched.rows[0]["serial_no"].startswith("fake-sn-")

    def write(connection):  # type: ignore[no-untyped-def]
        connection.execute(f"delete from {FIXTURE_SCHEMA}.tyre_records")  # nosec B608 - test constant

    with pytest.raises(sources.TyreSourceError):
        asyncio.run(source._run(write))
    # Even a login that may write cannot: every session is read-only.
    admin = database_engine.url
    writer = sources.SupabasePostgresSource(
        f"postgresql://{admin.username}:{admin.password}@{admin.host}:{admin.port}/{admin.database}?sslmode=prefer",
        FIXTURE_SCHEMA,
    )
    with pytest.raises(sources.TyreSourceError) as refused:
        asyncio.run(writer._run(write))
    assert "read-only" in refused.value.message
    assert len(asyncio.run(source.fetch(f"{FIXTURE_SCHEMA}.tyre_records", ["id"])).rows) == 32

    owner = headers()
    saved = client.put(f"{API}/connection", headers=owner, json={
        "kind": "supabase_postgres", "label": "TyrePulse", "schema_name": FIXTURE_SCHEMA, "secret": tyrepulse_database})
    assert saved.status_code == 200 and saved.json()["connection"]["secret_hint"] == f"{READER}@{make_url(tyrepulse_database).host}"
    assert "example" not in saved.json()["connection"]["endpoint"]
    suggestion = client.post(f"{API}/discover", headers=owner).json()["suggestion"]
    assert suggestion["preset"] == "tyrepulse" and suggestion["entities"]["tyres"]["source"] == f"{FIXTURE_SCHEMA}.tyre_records"
    client.put(f"{API}/mapping", headers=owner, json=suggestion)
    synced = client.post(f"{API}/sync", headers=owner)
    assert synced.status_code == 200, synced.text
    assert synced.json()["setup"]["sync"]["counts"]["tyres"] == 32
    assert client.get(f"{API}/dashboard", headers=owner).json()["summary"]["tyres_fitted"] == 24
