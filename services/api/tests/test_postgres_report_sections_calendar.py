"""Typed report sections and the calendar in PostgreSQL (migration 0045_reports_calendar).

The API runs as the non-owner ``anum_test_app`` role. Rows are scoped to their tenant and
workspace by forced RLS; the in-memory backends are cleared so everything is read back
from PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_report_sections import tyre_report

from anum_api.calendar_api import calendar_backend
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.reports import report_store
from anum_api.schemas import utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user_id: str = "owner_test", role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


A = headers()
A2 = headers(workspace_id=WORKSPACE_A2)
B = headers(TENANT_B, WORKSPACE_B)


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    report_store.clear()
    calendar_backend.clear()
    yield TestClient(app)
    report_store.clear()
    calendar_backend.clear()


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return int(connection.execute(text(f"select count(*) from {table}")).scalar_one())


def test_report_sections_persist_and_stay_in_their_workspace(client: TestClient, database_engine: Engine) -> None:
    created = client.post("/api/v1/reports", headers=A, json=tyre_report())
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    report_store.clear()  # read back from PostgreSQL
    opened = client.get(f"/api/v1/reports/{report_id}", headers=A).json()
    assert [s["type"] for s in opened["sections"]] == ["metrics", "series", "breakdown", "ranked", "text"]
    assert opened["tabs"] == ["Overview", "High cost", "Vehicles", "Sites"]
    assert client.get("/api/v1/reports", headers=A).json()["items"][0]["section_count"] == 5
    renamed = client.patch(f"/api/v1/reports/{report_id}", headers=A, json={"title": "PMV Tyre Report"})
    assert renamed.status_code == 200 and len(renamed.json()["sections"]) == 5
    for other in (A2, B):
        assert client.get(f"/api/v1/reports/{report_id}", headers=other).status_code == 404
        assert client.post(f"/api/v1/reports/{report_id}/ask", headers=other, json={"question": "x"}).status_code == 404
    assert client.post(f"/api/v1/reports/{report_id}/ask", headers=A, json={"question": "Why?"}).status_code == 201
    with database_engine.connect() as connection:
        stored = connection.execute(text("select jsonb_array_length(sections), tabs from reports")).one()
    assert stored[0] == 5 and stored[1][0] == "Overview"


def test_calendar_events_persist_by_range_and_stay_in_their_workspace(client: TestClient, database_engine: Engine) -> None:
    start = utc_now().replace(microsecond=0) + timedelta(hours=2)
    body = {
        "title": "PMV Tyre Cost Review",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
        "attendees": [{"name": "Sajid Kamboh", "email": "sajid@example.com"}],
    }
    created = client.post("/api/v1/calendar/events", headers=A, json=body)
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    attendee = created.json()["attendees"][0]["id"]
    assert _count(database_engine, "calendar_events") == 1

    listed = client.get("/api/v1/calendar/events", headers=A).json()["items"]
    assert [e["id"] for e in listed] == [event_id]
    replied = client.put(
        f"/api/v1/calendar/events/{event_id}/attendees/{attendee}/response", headers=A, json={"response": "accepted"}
    )
    assert replied.status_code == 200
    assert client.get(f"/api/v1/calendar/events/{event_id}", headers=A).json()["attendees"][0]["response"] == "accepted"
    cancelled = client.patch(f"/api/v1/calendar/events/{event_id}", headers=A, json={"status": "cancelled"})
    assert cancelled.status_code == 200
    assert client.get("/api/v1/calendar/events", headers=A).json()["items"] == []

    for other in (A2, B):
        assert client.get(f"/api/v1/calendar/events/{event_id}", headers=other).status_code == 404
        assert client.get("/api/v1/calendar/events", headers=other, params={"include_cancelled": "true"}).json()["items"] == []
    assert client.get("/api/v1/calendar/next-steps", headers=A).status_code == 200
    assert client.delete(f"/api/v1/calendar/events/{event_id}", headers=A).status_code == 204
    assert _count(database_engine, "calendar_events") == 0


def test_rls_refuses_rows_written_for_another_workspace(database_engine: Engine, seed_scopes: None) -> None:
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_A})
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "insert into calendar_events (id, tenant_id, workspace_id, title, kind, starts_at, ends_at, created_by) "
                    "values ('event_x', :t, :w, 'x', 'meeting', now(), now() + interval '1 hour', 'u')"
                ),
                {"t": TENANT_B, "w": WORKSPACE_B},
            )
        transaction.rollback()
