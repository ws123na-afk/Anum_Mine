"""Calendar sync depth in PostgreSQL (migration 0171_calendar_sync_depth, docs/calendar.md):
a series goes to the provider and its failed sends are kept on the row and stop after five; a
Google series is read back as one series without the instances an older sync flattened; replies
to one occurrence persist; email reminders are claimed once per person under forced RLS.

The API runs as the non-owner ``anum_test_app`` role against the fake calendars of ``test_calendar_sync``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_calendar_sync import FakeCalendars, google_item
from test_postgres_calendar_sync import A, _connect, client, fake  # noqa: F401 (fixtures)
from test_postgres_team_work import _scoped

from anum_api import notifications
from anum_api.calendar_api import MAX_PUSH_ATTEMPTS
from anum_api.calendar_reminders import run_calendar_reminder_pass
from anum_api.schemas import utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database


def _scalar(engine: Engine, sql: str, params: dict[str, object] | None = None, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> object:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            _scoped(connection, tenant, workspace)
            return connection.execute(text(sql), params or {}).scalar_one()
        finally:
            transaction.rollback()


def _series(**extra: object) -> dict[str, object]:
    start = (utc_now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    return {
        "title": "Weekly depot review", "kind": "review", "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
        "timezone": "Asia/Dubai", "recurrence": {"freq": "weekly", "count": 4},
        "attendees": [{"name": "Sara", "email": "sara@example.com"}], **extra,
    }


def test_a_series_push_its_failures_and_a_retry_persist(client: TestClient, database_engine: Engine, fake: FakeCalendars) -> None:  # noqa: F811
    connection = _connect(client)
    fake.write_status = 503
    created = client.post("/api/v1/calendar/events", headers=A, json=_series())
    assert created.status_code == 201, created.text
    series = created.json()
    assert series["sync_state"] == "pending_push" and series["sync_error"]
    assert _scalar(database_engine, "select sync_attempts from calendar_events where id = :id", {"id": series["id"]}) == 1
    for _ in range(MAX_PUSH_ATTEMPTS):
        assert client.post(f"/api/v1/calendar/sync/connections/{connection['id']}/sync", headers=A).status_code == 200
    assert _scalar(database_engine, "select sync_attempts from calendar_events where id = :id", {"id": series["id"]}) == MAX_PUSH_ATTEMPTS
    writes = len(fake.google_writes)
    client.post(f"/api/v1/calendar/sync/connections/{connection['id']}/sync", headers=A)
    assert len(fake.google_writes) == writes  # no longer retried on its own
    fake.write_status = 200
    retried = client.post(f"/api/v1/calendar/events/{series['id']}/sync/retry", headers=A)
    assert retried.status_code == 200, retried.text
    assert retried.json()["sync_state"] == "synced" and retried.json()["external_id"] == "g_new"
    assert fake.google_writes[-1]["recurrence"][0].startswith("RRULE:FREQ=WEEKLY;COUNT=4")
    assert _scalar(database_engine, "select sync_attempts from calendar_events where id = :id", {"id": series["id"]}) == 0
    # Another tenant can neither see nor retry it.
    other = {**A, "x-tenant-id": TENANT_B, "x-workspace-id": WORKSPACE_B}
    assert client.post(f"/api/v1/calendar/events/{series['id']}/sync/retry", headers=other).status_code == 404


def test_a_google_series_replaces_the_instances_an_older_sync_flattened(client: TestClient, database_engine: Engine, fake: FakeCalendars) -> None:  # noqa: F811
    start = (utc_now() + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    fake.google_items = [google_item(f"gs1_{start:%Y%m%dT%H%M%SZ}", "Depot walk", 24), google_item("plain_1", "Other", 30)]
    connection = _connect(client)
    master = google_item("gs1", "Depot walk", 0, recurrence=["RRULE:FREQ=DAILY;COUNT=3"])
    master["start"] = {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "Asia/Dubai"}
    master["end"] = {"dateTime": (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "Asia/Dubai"}
    fake.google_changes = [master]
    assert client.post(f"/api/v1/calendar/sync/connections/{connection['id']}/sync", headers=A).status_code == 200
    assert _scalar(database_engine, "select count(*) from calendar_events where external_id like 'gs1\\_%'") == 0
    assert _scalar(database_engine, "select count(*) from calendar_events where external_id = 'gs1' and recurrence is not null") == 1
    assert _scalar(database_engine, "select count(*) from calendar_events where external_id = 'plain_1'") == 1


def test_an_outlook_deleted_occurrence_of_an_anum_series_is_skipped_in_postgres(client: TestClient, database_engine: Engine, fake: FakeCalendars) -> None:  # noqa: F811
    from test_calendar_sync_series import graph_master, graph_occurrence

    fake.graph_items = []
    connection = _connect(client, "microsoft")
    created = client.post("/api/v1/calendar/events", headers=A, json=_series(attendees=[]))
    assert created.status_code == 201, created.text
    series = created.json()
    assert series["external_id"] == "m_new"
    start = (utc_now() + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    starts = [start + timedelta(weeks=n) for n in range(4)]
    fake.graph_masters["m_new"] = graph_master("m_new", start, fake.graph_writes[-1]["recurrence"], title="Weekly depot review",
                                               changeKey="ck_deleted", end={"dateTime": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"})
    fake.graph_instances["m_new"] = [graph_occurrence(f"m_new_{n}", "m_new", s, "Weekly depot review") for n, s in enumerate(starts) if n != 2]
    fake.graph_changes = [{"id": "m_new_2", "@removed": {"reason": "deleted"}}]
    assert client.post(f"/api/v1/calendar/sync/connections/{connection['id']}/sync", headers=A).status_code == 200
    skipped = _scalar(database_engine, "select skipped from calendar_events where id = :id", {"id": series["id"]})
    assert skipped == [starts[2].isoformat()]
    # Another tenant's sync never reads this workspace's series.
    assert _scalar(database_engine, "select count(*) from calendar_events where external_id = 'm_new'", tenant=TENANT_B, workspace=WORKSPACE_B) == 0


def test_occurrence_replies_and_email_reminders_persist_once(client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    now = utc_now().replace(microsecond=0)
    start = now + timedelta(minutes=40)
    body = {**_series(), "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(), "reminders": [60], "email_reminders": [60], "sync": False}
    series = client.post("/api/v1/calendar/events", headers=A, json=body).json()
    sara = series["attendees"][0]["id"]
    second = start + timedelta(weeks=1)
    replied = client.put(f"/api/v1/calendar/events/{series['id']}/attendees/{sara}/response", headers=A,
                         json={"response": "declined", "scope": "this", "occurrence_start": second.isoformat()})
    assert replied.status_code == 200, replied.text
    assert _scalar(database_engine, "select jsonb_array_length(occurrence_responses) from calendar_events where id = :id", {"id": series["id"]}) == 1
    opened = client.get(f"/api/v1/calendar/events/{series['id']}", headers=A, params={"occurrence_start": second.isoformat()}).json()
    assert opened["attendees"][0]["response"] == "declined" and opened["occurrence_replies"] == [sara] and opened["email_reminders"] == [60]
    # Email reminders: claimed once per person even when the pass runs twice.
    sent: list[str] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    monkeypatch.setattr(notifications, "send_email", lambda to, subject, text: sent.append(to))
    run_calendar_reminder_pass(now=now)
    run_calendar_reminder_pass(now=now)
    assert _scalar(database_engine, "select count(*) from calendar_reminder_email_sends") == 1
    assert _scalar(database_engine, "select status from calendar_reminder_email_sends") in {"skipped", "sent"}
    assert _scalar(database_engine, "select count(*) from calendar_reminder_email_sends", tenant=TENANT_B, workspace=WORKSPACE_B) == 0
    # The claims go with the event.
    assert client.delete(f"/api/v1/calendar/events/{series['id']}", headers=A).status_code == 204
    assert _scalar(database_engine, "select count(*) from calendar_reminder_email_sends") == 0
