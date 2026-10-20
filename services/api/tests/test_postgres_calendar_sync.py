"""Calendar sync in PostgreSQL (migration 0092_calendar_sync, docs/calendar.md#sync).

The API runs as the non-owner ``anum_test_app`` role against fake Google and Microsoft
calendars. Connections, pending connections and the links on events are scoped to their
tenant and workspace by forced RLS; "restart" clears the in-memory stores so everything is
read back from PostgreSQL.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_calendar_sync import FakeCalendars, stamp

from anum_api import calendar_sync
from anum_api.calendar_api import calendar_backend
from anum_api.calendar_sync import CalendarSyncScheduler, calendar_settings, sync_backend
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.schemas import utc_now
from anum_api.secret_box import secret_cipher
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user_id: str = "owner_test", role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


A = headers()
A2 = headers(workspace_id=WORKSPACE_A2)
B = headers(TENANT_B, WORKSPACE_B)


@pytest.fixture
def fake(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCalendars]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    cal = FakeCalendars()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(calendar_sync, "calendar_transport", httpx.MockTransport(cal))
    monkeypatch.setattr(calendar_settings, "google_client_id", "example-google-client")
    monkeypatch.setattr(calendar_settings, "google_client_secret", SecretStr("example"))
    monkeypatch.setattr(calendar_settings, "microsoft_client_id", "example-azure-client")
    monkeypatch.setattr(calendar_settings, "microsoft_client_secret", SecretStr("example"))
    monkeypatch.setattr(calendar_settings, "public_api_url", "https://api.shop.example")
    monkeypatch.setattr(calendar_settings, "web_url", "https://app.shop.example")
    calendar_backend.clear()
    sync_backend.clear()
    yield cal
    calendar_backend.clear()
    sync_backend.clear()


@pytest.fixture
def client(fake: FakeCalendars) -> TestClient:
    return TestClient(app)


def _connect(client: TestClient, provider: str = "google", who: dict[str, str] = A) -> dict:
    started = client.post(f"/api/v1/calendar/sync/oauth/{provider}/start", headers=who)
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    back = client.get("/api/v1/calendar/sync/oauth/callback", params={"state": state, "code": "example-code"}, follow_redirects=False)
    nonce = parse_qs(urlsplit(back.headers["location"]).query)["calendar_connect"][0]
    done = client.post("/api/v1/calendar/sync/oauth/complete", headers=who, json={"connection": nonce})
    assert done.status_code == 201, done.text
    return done.json()


def _events(client: TestClient, who: dict[str, str] = A) -> list[dict]:
    response = client.get("/api/v1/calendar/events", headers=who, params={"start": stamp(-24 * 40), "end": stamp(24 * 50), "include_cancelled": "true"})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _count(database_engine: Engine, sql: str) -> int:
    with database_engine.connect() as connection:
        return int(connection.execute(text(sql)).scalar_one())


def test_connection_events_and_links_persist_encrypted_and_scoped(client: TestClient, database_engine: Engine, fake: FakeCalendars) -> None:
    connection = _connect(client)
    assert connection["events_synced"] == 2
    with database_engine.connect() as raw:
        row = raw.execute(text("select access_token_enc, refresh_token_enc, sync_cursor, status from calendar_connections")).one()
    assert "google-" not in row.access_token_enc and secret_cipher().decrypt(row.refresh_token_enc) == "google-refresh-1"
    assert row.sync_cursor.startswith("sync:S1|") and row.status == "connected"
    assert _count(database_engine, "select count(*) from calendar_events where connection_id is not null and external_id is not null") == 2
    assert _count(database_engine, "select count(*) from calendar_oauth_requests") == 0  # single use
    calendar_backend.clear()
    sync_backend.clear()  # restart: read back from PostgreSQL
    assert [e["external_id"] for e in _events(client)] == ["g1", "g2"]
    # Another workspace and another tenant see nothing; they cannot sync or disconnect it.
    for other in (A2, B):
        assert _events(client, other) == []
        assert client.get("/api/v1/calendar/sync", headers=other).json()["connections"] == []
        assert client.post(f"/api/v1/calendar/sync/connections/{connection['id']}/sync", headers=other).status_code == 404
        assert client.delete(f"/api/v1/calendar/sync/connections/{connection['id']}", headers=other).status_code == 404
    assert client.get("/api/v1/calendar/sync", headers=A).json()["connections"][0]["email"] == "owner@example.com"


def test_two_way_sync_round_trip_in_postgresql(client: TestClient, database_engine: Engine, fake: FakeCalendars) -> None:
    connection = _connect(client)
    start = utc_now().replace(microsecond=0) + timedelta(hours=40)
    created = client.post(
        "/api/v1/calendar/events", headers=A,
        json={"title": "Tyre cost review", "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
              "attendees": [{"name": "Sara", "email": "sara@client.example"}]},
    )
    assert created.status_code == 201 and created.json()["external_id"] == "g_new"
    assert "attendees" not in fake.google_writes[0]
    fake.google_changes = [{"id": "g2", "status": "cancelled"}]
    result = client.post(f"/api/v1/calendar/sync/connections/{connection['id']}/sync", headers=A).json()
    assert result["summary"]["deleted"] == 1
    assert sorted(e["external_id"] for e in _events(client)) == ["g1", "g_new"]
    # The same provider event cannot be linked twice to a connection.
    with database_engine.connect() as raw:
        transaction = raw.begin()
        with pytest.raises(DBAPIError):
            raw.execute(
                text(
                    "insert into calendar_events (id, tenant_id, workspace_id, title, kind, starts_at, ends_at, created_by, connection_id, external_id) "
                    "select 'event_dup', tenant_id, workspace_id, 'dup', 'meeting', now(), now() + interval '1 hour', 'u', connection_id, external_id "
                    "from calendar_events where external_id = 'g1'"
                )
            )
        transaction.rollback()
    disconnected = client.delete(f"/api/v1/calendar/sync/connections/{connection['id']}", headers=A)
    assert disconnected.json()["events_kept"] == 2
    assert _count(database_engine, "select count(*) from calendar_events where connection_id is not null or external_id is not null") == 0
    assert _count(database_engine, "select count(*) from calendar_connections") == 0


def test_invitations_through_the_approval_flow_in_postgresql(client: TestClient, fake: FakeCalendars) -> None:
    _connect(client, "microsoft")
    start = utc_now().replace(microsecond=0) + timedelta(hours=40)
    created = client.post(
        "/api/v1/calendar/events", headers=A,
        json={"title": "Client kickoff", "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
              "attendees": [{"name": "Sara", "email": "sara@client.example"}]},
    ).json()
    asked = client.post(f"/api/v1/calendar/events/{created['id']}/invitations", headers=A)
    assert asked.status_code == 202, asked.text
    approval = asked.json()["approval"]
    assert not [w for w in fake.graph_writes if "attendees" in w]
    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    assert approved.status_code == 200, approved.text
    assert fake.graph_writes[-1]["attendees"][0]["emailAddress"]["address"] == "sara@client.example"
    calendar_backend.clear()
    assert client.get(f"/api/v1/calendar/events/{created['id']}", headers=A).json()["invitations_pending"] is False


def test_background_sync_and_key_rotation_discover_connections_as_anum_maintenance_only(
    client: TestClient, database_engine: Engine, fake: FakeCalendars
) -> None:
    connection = _connect(client)
    with database_engine.begin() as raw:
        raw.execute(text("grant anum_maintenance to current_user"))
        raw.execute(text("update calendar_connections set last_sync_at = now() - interval '1 hour'"))
    with database_engine.connect() as raw:
        transaction = raw.begin()
        raw.execute(text("set local role anum_maintenance"))
        visible = raw.execute(text("select id, tenant_id, workspace_id from calendar_connections")).all()
        assert [row.id for row in visible] == [connection["id"]]
        with pytest.raises(DBAPIError):
            raw.execute(text("select refresh_token_enc from calendar_connections"))
        transaction.rollback()
    fake.google_changes = [{"id": "g1", "status": "cancelled"}]
    done = asyncio.run(CalendarSyncScheduler(60).run_once())
    assert done == 1
    assert _count(database_engine, "select count(*) from calendar_connections where last_sync_at > now() - interval '1 minute'") == 1
    assert [e["external_id"] for e in _events(client)] == ["g2"]
    # A connection that needs reconnecting is not synced again by the background job.
    with database_engine.begin() as raw:
        raw.execute(text("update calendar_connections set status = 'reauth_required', last_sync_at = now() - interval '1 hour'"))
    assert asyncio.run(CalendarSyncScheduler(60).run_once()) == 0


def test_rls_refuses_rows_written_for_another_workspace(database_engine: Engine, seed_scopes: None) -> None:
    statements = (
        (
            "insert into calendar_connections (id, tenant_id, workspace_id, provider, email, connected_by) "
            "values ('cal_x', :t, :w, 'google', 'x@example.com', 'u')"
        ),
        (
            "insert into calendar_oauth_requests (nonce, tenant_id, workspace_id, user_id, provider, expires_at) "
            "values ('coc_x', :t, :w, 'u', 'google', now())"
        ),
    )
    for statement in statements:
        with database_engine.connect() as raw:
            transaction = raw.begin()
            raw.execute(text(f"set local role {APP_ROLE}"))
            raw.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
            raw.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_A})
            with pytest.raises(DBAPIError):
                raw.execute(text(statement), {"t": TENANT_B, "w": WORKSPACE_B})
            transaction.rollback()
