"""Repeating events and reminders in PostgreSQL (migration 0167_calendar_recurrence,
docs/calendar.md#recurrence): series columns round-trip under forced RLS as the non-owner app
role, a changed occurrence is a child row deleted with its series, and reminders are discovered
as ``anum_maintenance`` (never a title) and sent once."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from anum_api.calendar_reminders import run_calendar_reminder_pass
from anum_api.main import app
from anum_api.schemas import utc_now

from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)
from test_postgres_team_work import _member, _rows, _scoped

pytestmark = pytest.mark.database


def h(user: str = "owner_a", roles: str = "owner", tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


@pytest.fixture
def client(postgres_backend: None, database_engine: Engine) -> TestClient:  # noqa: F811
    _member(database_engine, TENANT_A, WORKSPACE_A, "owner_a", "owner")
    _member(database_engine, TENANT_A, WORKSPACE_A, "member_a", "member")
    _member(database_engine, TENANT_B, WORKSPACE_B, "owner_b", "owner")
    return TestClient(app)


def _count(engine: Engine, sql: str, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> int:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            _scoped(connection, tenant, workspace)
            return int(connection.execute(text(sql)).scalar_one())
        finally:
            transaction.rollback()


def _series(hours_from_now: float = 24) -> dict[str, object]:
    start = utc_now().replace(microsecond=0) + timedelta(hours=hours_from_now)
    return {
        "title": "Weekly fleet review", "kind": "review", "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
        "timezone": "Asia/Dubai", "recurrence": {"freq": "weekly", "count": 4}, "reminders": [10],
        "attendees": [{"name": "Sara", "email": "sara@example.com"}],
    }


def test_a_series_and_its_changed_occurrence_live_in_their_workspace(client: TestClient, database_engine: Engine) -> None:
    created = client.post("/api/v1/calendar/events", headers=h(), json=_series())
    assert created.status_code == 201, created.text
    series = created.json()
    assert series["rrule"] == "FREQ=WEEKLY;COUNT=4" and series["recurrence_until"] is not None
    window = {"start": series["starts_at"], "end": (utc_now() + timedelta(days=60)).isoformat()}
    items = client.get("/api/v1/calendar/events", headers=h(), params=window).json()["items"]
    assert len(items) == 4 and _count(database_engine, "select count(*) from calendar_events") == 1
    # One occurrence changed on its own becomes a child row; one deleted on its own is skipped.
    changed = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=h(),
                           json={"scope": "this", "occurrence_start": items[1]["occurrence_start"], "title": "Moved review", "reminders": [5, 30]})
    assert changed.status_code == 200, changed.text
    assert client.delete(f"/api/v1/calendar/events/{series['id']}", headers=h(), params={"scope": "this", "occurrence_start": items[2]["occurrence_start"]}).status_code == 204
    assert _count(database_engine, "select count(*) from calendar_events") == 2
    assert _count(database_engine, "select count(*) from calendar_events where series_id = '" + series["id"] + "'") == 1
    assert _count(database_engine, "select jsonb_array_length(skipped) from calendar_events where id = '" + series["id"] + "'") == 1
    again = client.get("/api/v1/calendar/events", headers=h(), params=window).json()["items"]
    assert [i["title"] for i in again] == ["Weekly fleet review", "Moved review", "Weekly fleet review"]
    assert again[1]["reminders"] == [5, 30] and again[1]["overridden"] is True and again[0]["reminders"] == [10]
    reopened = client.get(f"/api/v1/calendar/events/{series['id']}", headers=h(), params={"occurrence_start": items[1]["occurrence_start"]}).json()
    assert reopened["title"] == "Moved review" and reopened["timezone"] == "Asia/Dubai"
    # Other workspaces and tenants see nothing of it.
    for other in (h(workspace=WORKSPACE_A2), h("owner_b", tenant=TENANT_B, workspace=WORKSPACE_B)):
        assert client.get("/api/v1/calendar/events", headers=other, params=window).json()["items"] == []
        assert client.get(f"/api/v1/calendar/events/{series['id']}", headers=other).status_code == 404
    # Deleting the series deletes the child row with it.
    assert client.delete(f"/api/v1/calendar/events/{series['id']}", headers=h()).status_code == 204
    assert _count(database_engine, "select count(*) from calendar_events") == 0


def test_all_day_events_persist_their_days(client: TestClient, database_engine: Engine) -> None:
    body = {"title": "Trade fair", "kind": "meeting", "all_day": True, "timezone": "Asia/Dubai", "start_date": "2031-02-03", "end_date": "2031-02-04"}
    created = client.post("/api/v1/calendar/events", headers=h(), json=body)
    assert created.status_code == 201, created.text
    event = created.json()
    assert (event["start_date"], event["end_date"], event["starts_at"]) == ("2031-02-03", "2031-02-04", "2031-02-02T20:00:00Z")
    assert _count(database_engine, "select count(*) from calendar_events where all_day") == 1
    opened = client.get(f"/api/v1/calendar/events/{event['id']}", headers=h()).json()
    assert opened["all_day"] is True and opened["end_date"] == "2031-02-04"


def test_reminders_are_discovered_as_maintenance_and_sent_once(client: TestClient, database_engine: Engine) -> None:
    now = utc_now().replace(microsecond=0)
    start = now + timedelta(minutes=20)
    body = {"title": "Board call", "kind": "call", "starts_at": start.isoformat(), "ends_at": (start + timedelta(minutes=30)).isoformat(), "reminders": [30],
            "phone": "+971 50 000 0000"}
    created = client.post("/api/v1/calendar/events", headers=h("member_a", "member"), json=body)
    assert created.status_code == 201, created.text
    quiet = client.post("/api/v1/calendar/events", headers=h(), json={**body, "title": "No reminder", "reminders": []})
    assert quiet.status_code == 201
    due = _rows(database_engine, "anum_maintenance", "select tenant_id, id, reminders from calendar_events")
    assert [r.id for r in due] == [created.json()["id"]]
    assert run_calendar_reminder_pass(now=now) == 1
    assert run_calendar_reminder_pass(now=now) == 0
    assert run_calendar_reminder_pass(now=now + timedelta(minutes=5)) == 0
    assert _count(database_engine, "select count(*) from calendar_reminder_sends") == 1
    assert _count(database_engine, "select count(*) from calendar_reminder_sends", TENANT_B, WORKSPACE_B) == 0
    items = client.get("/api/v1/notifications", headers=h("member_a", "member")).json()["items"]
    assert [n["kind"] for n in items] == ["calendar_reminder"] and items[0]["title"] == "Board call starts in 20 minutes"
    assert client.get("/api/v1/notifications", headers=h()).json()["items"] == []
    # The maintenance role reads no content of the event, and the send rows go with the event.
    with pytest.raises(DBAPIError):
        _rows(database_engine, "anum_maintenance", "select title from calendar_events")
    assert client.delete(f"/api/v1/calendar/events/{created.json()['id']}", headers=h("member_a", "member")).status_code == 204
    assert _count(database_engine, "select count(*) from calendar_reminder_sends") == 0
