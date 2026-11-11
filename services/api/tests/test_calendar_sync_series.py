"""ANUM's series and all-day events at Google and Microsoft, provider series read back as series,
and failed sends kept on the event with a bounded retry (docs/calendar.md#how-events-sync).

Every provider call goes to the ``httpx.MockTransport`` of ``test_calendar_sync``; nothing leaves the process.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from test_calendar_sync import (  # noqa: F401 (the autouse fixture)
    MEMBER,
    OWNER,
    VIEWER,
    FakeCalendars,
    client,
    connect,
    events,
    fake,
    google_item,
    graph_item,
    stamp,
    sync,
)

from anum_api.calendar_api import MAX_PUSH_ATTEMPTS, calendar_backend
from anum_api.calendar_providers import Recurrence, google_rrule, graph_recurrence, parse_google_recurrence
from anum_api.schemas import utc_now
from anum_api.store import store


def first_start(days: int = 2) -> datetime:
    return (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)


def series_body(**overrides: Any) -> dict[str, Any]:
    start = first_start()
    return {
        "title": "Fleet stand-up", "kind": "meeting", "starts_at": start.isoformat(), "ends_at": (start + timedelta(minutes=30)).isoformat(),
        "timezone": "Asia/Dubai", "recurrence": {"freq": "daily", "count": 6}, **overrides,
    }


def create(body: dict[str, Any], who: dict[str, str] = MEMBER) -> dict[str, Any]:
    response = client.post("/api/v1/calendar/events", headers=who, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def occurrences_of(series_id: str) -> list[dict[str, Any]]:
    return [e for e in events() if e["id"] == series_id]


def instance(master: str, start: datetime, title: str = "Fleet stand-up", **extra: Any) -> dict[str, Any]:
    iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": f"{master}_{start:%Y%m%dT%H%M%SZ}", "status": "confirmed", "etag": '"i1"', "summary": title, "updated": stamp(-1),
        "recurringEventId": master, "originalStartTime": {"dateTime": iso},
        "start": {"dateTime": iso}, "end": {"dateTime": (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")}, **extra,
    }


# -- ANUM to Google -----------------------------------------------------------------------------


def test_a_series_goes_to_google_with_its_rule_and_its_exceptions_follow(fake: FakeCalendars) -> None:
    connect()
    start = first_start()
    series = create(series_body(recurrence={"freq": "weekly", "by_weekday": [], "until": (start + timedelta(days=35)).date().isoformat()}))
    assert series["sync_state"] == "synced" and series["external_id"] == "g_new" and series["sync_failed"] is False
    sent = fake.google_writes[-1]
    assert sent["method"] == "POST" and sent["params"]["sendUpdates"] == "none" and "attendees" not in sent
    assert sent["start"]["timeZone"] == "Asia/Dubai" and sent["start"]["dateTime"] == start.astimezone(ZoneInfo("Asia/Dubai")).strftime("%Y-%m-%dT%H:%M:%S")
    assert sent["recurrence"][0].startswith("RRULE:FREQ=WEEKLY;UNTIL=") and sent["recurrence"][0].endswith("Z")
    starts = [start + timedelta(weeks=n) for n in range(6)]
    fake.google_instances["g_new"] = [instance("g_new", s) for s in starts]
    # Removing one occurrence removes that instance at Google.
    second = starts[1].isoformat()
    assert client.delete(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, params={"scope": "this", "occurrence_start": second}).status_code == 204
    assert fake.google_writes[-1] == {"method": "DELETE", "id": f"g_new_{starts[1]:%Y%m%dT%H%M%SZ}", "params": {"sendUpdates": "none"}}
    # Changing one occurrence writes that instance (restoring it if Google had it cancelled).
    changed = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER,
                           json={"scope": "this", "occurrence_start": starts[2].isoformat(), "title": "Stand-up with suppliers"})
    assert changed.status_code == 200, changed.text
    write = fake.google_writes[-1]
    assert write["method"] == "PATCH" and write["id"] == f"g_new_{starts[2]:%Y%m%dT%H%M%SZ}" and write["summary"] == "Stand-up with suppliers"
    assert write["status"] == "confirmed" and "recurrence" not in write
    assert changed.json()["sync_state"] == "synced" and changed.json()["overridden"] is True
    # This and following: the series is cut short at Google and a new series starts there.
    before = len(fake.google_writes)
    moved = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER,
                         json={"scope": "following", "occurrence_start": starts[4].isoformat(), "title": "Late stand-up"})
    assert moved.status_code == 200, moved.text
    writes = fake.google_writes[before:]
    patched = next(w for w in writes if w["method"] == "PATCH" and w["id"] == "g_new")
    assert "UNTIL=" in patched["recurrence"][0]
    created = next(w for w in writes if w["method"] == "POST")
    assert created["summary"] == "Late stand-up" and created["recurrence"][0].startswith("RRULE:FREQ=WEEKLY")
    assert moved.json()["sync_state"] == "synced" and moved.json()["external_id"] not in (None, "g_new")
    # Deleting a whole series deletes the master at Google.
    assert client.delete(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER).status_code == 204
    assert fake.google_writes[-1]["method"] == "DELETE" and fake.google_writes[-1]["id"] == "g_new"
    audits = [a.action for a in store.audit_records]
    assert "calendar.event.updated" in audits and "calendar.event.deleted" in audits


def test_an_all_day_event_goes_to_google_as_dates(fake: FakeCalendars) -> None:
    connect()
    body = {"title": "Trade fair", "kind": "meeting", "all_day": True, "timezone": "Asia/Dubai", "start_date": "2031-02-03", "end_date": "2031-02-04"}
    event = create(body)
    sent = fake.google_writes[-1]
    assert sent["start"]["date"] == "2031-02-03" and sent["end"]["date"] == "2031-02-05" and sent["start"]["dateTime"] is None
    assert event["sync_state"] == "synced" and event["all_day"] is True


def test_an_all_day_event_goes_to_microsoft_as_all_day(fake: FakeCalendars) -> None:
    connect("microsoft")
    body = {"title": "Eid holiday", "kind": "reminder", "all_day": True, "timezone": "Asia/Dubai", "start_date": "2031-03-20", "end_date": "2031-03-22"}
    create(body)
    sent = fake.graph_writes[-1]
    assert sent["isAllDay"] is True and sent["start"] == {"dateTime": "2031-03-20T00:00:00", "timeZone": "Asia/Dubai"}
    assert sent["end"] == {"dateTime": "2031-03-23T00:00:00", "timeZone": "Asia/Dubai"}


def test_a_microsoft_series_uses_a_recurrence_pattern_and_its_occurrences_are_not_duplicated(fake: FakeCalendars) -> None:
    connect("microsoft")
    series = create(series_body(recurrence={"freq": "monthly", "by_weekday": ["TU"], "by_setpos": 2, "count": 4}))
    sent = fake.graph_writes[-1]
    assert sent["method"] == "POST" and "attendees" not in sent
    assert sent["recurrence"]["pattern"] == {"interval": 1, "firstDayOfWeek": "monday", "type": "relativeMonthly", "daysOfWeek": ["tuesday"], "index": "second"}
    assert sent["recurrence"]["range"]["type"] == "numbered" and sent["recurrence"]["range"]["numberOfOccurrences"] == 4
    assert sent["recurrence"]["range"]["recurrenceTimeZone"] == "Asia/Dubai" and sent["start"]["timeZone"] == "Asia/Dubai"
    assert series["sync_state"] == "synced" and series["external_id"] == "m_new"
    # Graph lists every occurrence of the series in the delta: the ones its rule makes are not imported again.
    fake.graph_changes = [graph_item("m_occ1", "Fleet stand-up", 48, seriesMasterId="m_new", type="occurrence")]
    connection_id = client.get("/api/v1/calendar/sync", headers=OWNER).json()["connections"][0]["id"]
    sync(connection_id)
    assert not [e for e in events() if e.get("external_id") == "m_occ1"]
    # The last day of the month cannot be written to Microsoft: refused at once, shown on the event.
    last_day = create(series_body(title="Month end close", recurrence={"freq": "monthly", "by_monthday": -1, "count": 3}))
    assert last_day["sync_state"] == "pending_push" and last_day["sync_failed"] is True
    assert "last day of the month" in last_day["sync_error"]


def test_daily_weekly_and_yearly_rules_in_each_providers_own_fields() -> None:
    start = datetime.fromisoformat("2030-03-04T05:00:00+00:00")  # a Monday, 09:00 in Dubai
    weekly = Recurrence(freq="weekly", interval=2, by_weekday=["MO", "WE"], until=datetime(2030, 6, 30).date())
    assert google_rrule(weekly, start, "Asia/Dubai", False) == "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;UNTIL=20300630T195959Z"
    assert google_rrule(weekly, start, "Asia/Dubai", True).endswith("UNTIL=20300630")
    pattern = graph_recurrence(weekly, start, "Asia/Dubai")
    assert pattern["pattern"]["type"] == "weekly" and pattern["pattern"]["daysOfWeek"] == ["monday", "wednesday"]
    assert pattern["range"] == {"startDate": "2030-03-04", "recurrenceTimeZone": "Asia/Dubai", "type": "endDate", "endDate": "2030-06-30"}
    yearly = graph_recurrence(Recurrence(freq="yearly"), start, "Asia/Dubai")
    assert yearly["pattern"]["type"] == "absoluteYearly" and yearly["pattern"]["month"] == 3 and yearly["range"]["type"] == "noEnd"
    assert graph_recurrence(Recurrence(freq="daily", interval=3), start, None)["pattern"]["type"] == "daily"
    # Reading Google's lines back: UNTIL in UTC becomes the last local day; EXDATE the removed starts.
    rule, excluded = parse_google_recurrence(
        ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE;INTERVAL=2;UNTIL=20300630T195959Z", "EXDATE;TZID=Asia/Dubai:20300306T090000"], start, "Asia/Dubai", False)
    assert rule == weekly and excluded == [datetime.fromisoformat("2030-03-06T05:00:00+00:00")]
    assert parse_google_recurrence(["RRULE:FREQ=MONTHLY;BYMONTH=1,6"], start, "UTC", False) == (None, [])
    assert parse_google_recurrence(["RRULE:FREQ=DAILY", "RDATE:20300310T050000Z"], start, "UTC", False) == (None, [])


# -- failures and retry ------------------------------------------------------------------------------


def test_a_failing_send_is_kept_on_the_event_retried_a_bounded_number_of_times_then_offered_as_retry(fake: FakeCalendars) -> None:
    connection = connect()
    fake.write_status = 503
    series = create(series_body())
    assert series["sync_state"] == "pending_push" and series["sync_failed"] is False and "Backend Error" in (series["sync_error"] or "")
    for _ in range(MAX_PUSH_ATTEMPTS):
        sync(connection["id"])
    stored = calendar_backend.events[series["id"]]
    assert stored.sync_attempts == MAX_PUSH_ATTEMPTS
    opened = client.get(f"/api/v1/calendar/events/{series['id']}", headers=VIEWER).json()
    assert opened["sync_failed"] is True and opened["sync_error"]
    # A further pass leaves it alone: no more writes for it.
    writes = len(fake.google_writes)
    calls = len(fake.calls)
    sync(connection["id"])
    assert len(fake.google_writes) == writes and len(fake.calls) > calls
    # Only its creator or an owner can retry; a viewer is told who can.
    denied = client.post(f"/api/v1/calendar/events/{series['id']}/sync/retry", headers=VIEWER)
    assert denied.status_code == 403 and "created this event" in denied.json()["error"]["message"]
    fake.write_status = 200
    retried = client.post(f"/api/v1/calendar/events/{series['id']}/sync/retry", headers=MEMBER)
    assert retried.status_code == 200, retried.text
    assert retried.json()["sync_state"] == "synced" and retried.json()["sync_failed"] is False and retried.json()["sync_error"] is None
    # Nothing waits any more, and an event that is not linked cannot be retried.
    assert client.post(f"/api/v1/calendar/events/{series['id']}/sync/retry", headers=MEMBER).status_code == 409
    local = create({**series_body(title="Only here"), "sync": False})
    assert client.post(f"/api/v1/calendar/events/{local['id']}/sync/retry", headers=MEMBER).status_code == 409
    assert client.post("/api/v1/calendar/events/event_missing/sync/retry", headers=MEMBER).status_code == 404
    assert any(a.action == "calendar.event.sync_retried" for a in store.audit_records)


def test_a_changed_occurrence_waits_for_its_series_to_reach_the_provider(fake: FakeCalendars) -> None:
    connection = connect()
    fake.write_status = 503
    series = create(series_body())
    start = first_start()
    changed = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER,
                           json={"scope": "this", "occurrence_start": (start + timedelta(days=1)).isoformat(), "title": "Moved"})
    assert changed.status_code == 200 and changed.json()["sync_state"] == "pending_push"
    child_id = changed.json()["override_id"]
    # Waiting for its series is not a failure of the occurrence.
    assert calendar_backend.events[child_id].sync_attempts == 0
    fake.write_status = 200
    fake.google_instances["g_new"] = [instance("g_new", start + timedelta(days=n)) for n in range(6)]
    summary = sync(connection["id"])
    assert summary["summary"]["pushed"] == 2
    assert calendar_backend.events[child_id].sync_state == "synced"
    assert calendar_backend.events[child_id].external_id == f"g_new_{start + timedelta(days=1):%Y%m%dT%H%M%SZ}"


def test_an_occurrence_gone_at_the_provider_is_refused_with_the_reason(fake: FakeCalendars) -> None:
    connect()
    series = create(series_body())
    fake.google_instances["g_new"] = []
    changed = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER,
                           json={"scope": "this", "occurrence_start": (first_start() + timedelta(days=2)).isoformat(), "title": "Moved"})
    assert changed.status_code == 200
    assert changed.json()["sync_failed"] is True and "no occurrence at this time" in changed.json()["sync_error"]


# -- provider series into ANUM ---------------------------------------------------------------------------


def test_a_google_series_is_read_as_one_series_with_its_exceptions(fake: FakeCalendars) -> None:
    start = first_start()
    master = google_item("gs1", "Depot walk", 0, recurrence=["RRULE:FREQ=DAILY;COUNT=5", f"EXDATE:{start + timedelta(days=4):%Y%m%dT%H%M%SZ}"])
    master["start"] = {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "Asia/Dubai"}
    master["end"] = {"dateTime": (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "Asia/Dubai"}
    changed = instance("gs1", start + timedelta(days=1), "Depot walk with Sara", etag='"x1"')
    cancelled = {"id": f"gs1_{start + timedelta(days=2):%Y%m%dT%H%M%SZ}", "status": "cancelled", "recurringEventId": "gs1",
                 "originalStartTime": {"dateTime": (start + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}}
    # An instance an earlier (flattened) sync stored as a single event.
    legacy = google_item(f"gs1_{start + timedelta(days=3):%Y%m%dT%H%M%SZ}", "Depot walk", 75)
    fake.google_items = [legacy]
    connection = connect()
    assert [e["title"] for e in events()] == ["Depot walk"]
    fake.google_changes = [changed, cancelled, master]
    sync(connection["id"])
    shown = [e for e in events() if e["title"].startswith("Depot walk")]
    assert [e["title"] for e in shown] == ["Depot walk", "Depot walk with Sara", "Depot walk"]
    assert all(e["is_recurring"] for e in shown) and shown[1]["overridden"] is True
    series = calendar_backend.events[shown[0]["id"]]
    assert series.recurrence is not None and series.recurrence.count == 5 and series.timezone == "Asia/Dubai"
    assert start + timedelta(days=2) in series.skipped and start + timedelta(days=4) in series.skipped
    assert not [e for e in calendar_backend.events.values() if (e.external_id or "") == legacy["id"]]
    # The series' attendees came from Google: no invitation is pending for them.
    assert shown[0]["invitations_pending"] is False and shown[0]["sync_state"] == "synced"
    # Later, Google cancels another instance on its own: it is skipped in ANUM.
    fake.google_changes = [{"id": f"gs1_{start + timedelta(days=3):%Y%m%dT%H%M%SZ}", "status": "cancelled", "recurringEventId": "gs1",
                            "originalStartTime": {"dateTime": (start + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")}}]
    sync(connection["id"])
    assert len([e for e in events() if e["title"].startswith("Depot walk")]) == 2


def test_a_google_rule_outside_anums_subset_falls_back_to_its_instances(fake: FakeCalendars) -> None:
    start = first_start()
    master = google_item("gm1", "Quarterly audit", 0, recurrence=["RRULE:FREQ=MONTHLY;BYMONTH=1,4,7,10;BYMONTHDAY=3"])
    fake.google_items = [master]
    fake.google_instances["gm1"] = [instance("gm1", start, "Quarterly audit"), instance("gm1", start + timedelta(days=20), "Quarterly audit")]
    connect()
    audits = [e for e in events() if e["title"] == "Quarterly audit"]
    assert len(audits) == 2 and not any(e["is_recurring"] for e in audits)
    assert any(q.get("instances") == "/gm1/instances" for q in fake.google_query)
