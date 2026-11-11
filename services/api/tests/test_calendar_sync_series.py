"""ANUM's series and all-day events at Google and Microsoft, provider series read back as series,
and failed sends kept on the event with a bounded retry (docs/calendar.md#how-events-sync).

Every provider call goes to the ``httpx.MockTransport`` of ``test_calendar_sync``; nothing leaves the process.
"""

from __future__ import annotations

import json
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
from test_calendar_sync import stored as stored_connection

from anum_api.calendar_api import MAX_PUSH_ATTEMPTS, calendar_backend
from anum_api.calendar_providers import (
    Recurrence,
    google_rrule,
    graph_recurrence,
    graph_removed_occurrences,
    graph_zone,
    parse_google_recurrence,
    parse_graph_recurrence,
)
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


# -- Microsoft series into ANUM ------------------------------------------------------------------------


def _graph_time(moment: datetime) -> dict[str, str]:
    return {"dateTime": moment.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}


def graph_master(master_id: str, start: datetime, recurrence: dict[str, Any], title: str = "Depot walk", **extra: Any) -> dict[str, Any]:
    return {
        "id": master_id, "type": "seriesMaster", "subject": title, "changeKey": "cks1", "lastModifiedDateTime": stamp(-2),
        "start": _graph_time(start), "end": _graph_time(start + timedelta(minutes=30)), "isOrganizer": True,
        "organizer": {"emailAddress": {"address": "owner@contoso.example"}}, "attendees": [], "recurrence": recurrence,
        "originalStartTimeZone": "Arabian Standard Time", **extra,
    }


def graph_occurrence(occurrence_id: str, master_id: str, start: datetime, title: str = "Depot walk", kind: str = "occurrence", **extra: Any) -> dict[str, Any]:
    return {
        "id": occurrence_id, "type": kind, "seriesMasterId": master_id, "subject": title, "changeKey": f"ck_{occurrence_id}",
        "lastModifiedDateTime": stamp(-2), "originalStart": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start": _graph_time(start), "end": _graph_time(start + timedelta(minutes=30)), "isOrganizer": True,
        "organizer": {"emailAddress": {"address": "owner@contoso.example"}}, "attendees": [], **extra,
    }


def daily(count: int, zone: str = "Arabian Standard Time") -> dict[str, Any]:
    return {"pattern": {"type": "daily", "interval": 1, "firstDayOfWeek": "sunday"},
            "range": {"type": "numbered", "numberOfOccurrences": count, "startDate": first_start().date().isoformat(), "recurrenceTimeZone": zone}}


def microsoft_connection(fake: FakeCalendars, items: list[dict[str, Any]]) -> str:
    fake.graph_items = items
    connection = connect("microsoft")
    return connection["id"]


def series_rows(title_prefix: str) -> list[dict[str, Any]]:
    return [e for e in events() if e["title"].startswith(title_prefix)]


def test_a_microsoft_series_is_read_as_one_series_with_its_exceptions_and_removed_occurrences(fake: FakeCalendars) -> None:
    start = first_start()
    starts = [start + timedelta(days=n) for n in range(5)]
    occurrences = [graph_occurrence(f"m_occ{n}", "m_s1", s) for n, s in enumerate(starts)]
    # Before this change (and while its master cannot be read) the occurrences are single events.
    connection_id = microsoft_connection(fake, occurrences)
    assert len(series_rows("Depot walk")) == 5 and not any(e["is_recurring"] for e in series_rows("Depot walk"))
    # The master is readable now. Outlook changed the second occurrence on its own and deleted the third.
    moved = graph_occurrence("m_occ1", "m_s1", starts[1], "Depot walk with Sara", kind="exception", changeKey="ck_moved")
    fake.graph_masters["m_s1"] = graph_master("m_s1", start, daily(5))
    fake.graph_instances["m_s1"] = [occurrences[0], moved, occurrences[3], occurrences[4]]
    fake.graph_changes = [moved, {"id": "m_occ2", "@removed": {"reason": "deleted"}}]
    sync(connection_id)
    assert any(p.get("master") == "/events/m_s1" for p in fake.graph_params)
    shown = series_rows("Depot walk")
    assert [e["title"] for e in shown] == ["Depot walk", "Depot walk with Sara", "Depot walk", "Depot walk"]
    assert all(e["is_recurring"] for e in shown) and shown[1]["overridden"] is True
    series = calendar_backend.events[shown[0]["id"]]
    assert series.recurrence == Recurrence(freq="daily", count=5) and series.timezone == "Asia/Dubai" and series.external_id == "m_s1"
    assert series.skipped == [starts[2]] and series.source == "microsoft" and series.sync_state == "synced"
    # The single events the older sync stored for its occurrences are gone (no duplicates).
    assert not [e for e in calendar_backend.events.values() if (e.external_id or "").startswith("m_occ") and e.series_id is None]
    # Later Outlook deletes another occurrence: Graph reports only that occurrence's id as removed.
    fake.graph_masters["m_s1"]["changeKey"] = "cks2"
    fake.graph_instances["m_s1"] = [occurrences[0], moved, occurrences[4]]
    fake.graph_changes = [{"id": "m_occ3", "@removed": {"reason": "deleted"}}]
    summary = sync(connection_id)["summary"]
    assert summary["deleted"] == 1
    assert calendar_backend.events[series.id].skipped == [starts[2], starts[3]]
    assert len(series_rows("Depot walk")) == 3
    # Graph lists the series' own occurrences again on a change: none is imported as a single event.
    fake.graph_changes = [occurrences[0], occurrences[4]]
    sync(connection_id)
    assert len(series_rows("Depot walk")) == 3 and len([e for e in calendar_backend.events.values() if e.external_id == "m_occ0"]) == 0


def test_an_occurrence_deleted_in_outlook_from_a_series_anum_sent_is_removed_in_anum(fake: FakeCalendars) -> None:
    connect("microsoft")
    connection_id = client.get("/api/v1/calendar/sync", headers=OWNER).json()["connections"][0]["id"]
    series = create(series_body())
    sent = fake.graph_writes[-1]
    assert series["external_id"] == "m_new" and sent["recurrence"]["range"]["numberOfOccurrences"] == 6
    start = first_start()
    starts = [start + timedelta(days=n) for n in range(6)]
    # Outlook deleted the fourth occurrence; the master's change key moved, nothing else changed.
    fake.graph_masters["m_new"] = graph_master("m_new", start, sent["recurrence"], title="Fleet stand-up", changeKey="ck_after_delete")
    fake.graph_instances["m_new"] = [graph_occurrence(f"m_new_{n}", "m_new", s, "Fleet stand-up") for n, s in enumerate(starts) if n != 3]
    fake.graph_changes = [{"id": "m_new_3", "@removed": {"reason": "deleted"}}]
    writes = len(fake.graph_writes)
    sync(connection_id)
    stored_series = calendar_backend.events[series["id"]]
    assert stored_series.skipped == [starts[3]] and stored_series.recurrence == Recurrence(freq="daily", count=6)
    assert stored_series.external_etag == "ck_after_delete" and stored_series.source != "microsoft"
    assert len(occurrences_of(series["id"])) == 5
    # Nothing is written back: the removal came from the provider.
    assert len(fake.graph_writes) == writes


def test_a_microsoft_series_deleted_in_outlook_is_removed_in_anum(fake: FakeCalendars) -> None:
    start = first_start()
    fake.graph_masters["m_s2"] = graph_master("m_s2", start, daily(3), title="Yard check")
    fake.graph_instances["m_s2"] = [graph_occurrence(f"m_y{n}", "m_s2", start + timedelta(days=n), "Yard check") for n in range(3)]
    connection_id = microsoft_connection(fake, list(fake.graph_instances["m_s2"]))
    assert len(series_rows("Yard check")) == 3 and all(e["is_recurring"] for e in series_rows("Yard check"))
    del fake.graph_masters["m_s2"]
    fake.graph_changes = [{"id": f"m_y{n}", "@removed": {"reason": "deleted"}} for n in range(3)]
    sync(connection_id)
    assert series_rows("Yard check") == []


def test_a_microsoft_pattern_outside_anums_subset_keeps_its_occurrences_as_single_events(fake: FakeCalendars) -> None:
    start = first_start()
    yearly = {"pattern": {"type": "relativeYearly", "interval": 1, "daysOfWeek": ["monday"], "index": "first", "month": 3},
              "range": {"type": "noEnd", "startDate": start.date().isoformat(), "recurrenceTimeZone": "Arabian Standard Time"}}
    fake.graph_masters["m_s3"] = graph_master("m_s3", start, yearly, title="Annual audit")
    occurrence = graph_occurrence("m_a1", "m_s3", start, "Annual audit")
    fake.graph_instances["m_s3"] = [occurrence]
    microsoft_connection(fake, [occurrence])
    audits = series_rows("Annual audit")
    assert len(audits) == 1 and not audits[0]["is_recurring"]


def test_microsoft_series_over_the_per_pass_limit_wait_in_the_cursor(fake: FakeCalendars, monkeypatch: Any) -> None:
    from anum_api import calendar_providers

    monkeypatch.setattr(calendar_providers, "MAX_GRAPH_SERIES", 1)
    start = first_start()
    items = []
    for master, title in (("m_w1", "Wash bay"), ("m_w2", "Tyre bay")):
        fake.graph_masters[master] = graph_master(master, start, daily(2), title=title)
        fake.graph_instances[master] = [graph_occurrence(f"{master}_{n}", master, start + timedelta(days=n), title) for n in range(2)]
        items.extend(fake.graph_instances[master])
    connection_id = microsoft_connection(fake, items)
    state = json.loads(stored_connection(connection_id).sync_cursor)
    assert state["v"] == 2 and state["waiting"] == ["m_w2"]
    assert all(e["is_recurring"] for e in series_rows("Wash bay"))
    # The waiting series is read on the next pass, and its single events become one series.
    sync(connection_id)
    assert all(e["is_recurring"] for e in series_rows("Tyre bay")) and len(series_rows("Tyre bay")) == 2
    assert "waiting" not in json.loads(stored_connection(connection_id).sync_cursor)


def test_a_graph_cursor_from_before_series_were_read_starts_one_full_sync(fake: FakeCalendars) -> None:
    connection_id = microsoft_connection(fake, [graph_item("m1", "Teams stand-up", 6)])
    row = stored_connection(connection_id)
    state = json.loads(row.sync_cursor)
    del state["v"]
    row.sync_cursor = json.dumps(state)
    assert sync(connection_id)["summary"]["full"] is True and "startDateTime" in fake.graph_params[-2]


def test_graph_rules_and_zones_read_back() -> None:
    start = datetime.fromisoformat("2030-03-04T05:00:00+00:00")  # a Monday, 09:00 in Dubai
    for rule in (
        Recurrence(freq="weekly", interval=2, by_weekday=["MO", "WE"], until=datetime(2030, 6, 30).date()),
        Recurrence(freq="monthly", by_weekday=["TU"], by_setpos=-1, count=4),
        Recurrence(freq="monthly", by_monthday=15),
        Recurrence(freq="yearly"),
        Recurrence(freq="daily", interval=3),
    ):
        assert parse_graph_recurrence(graph_recurrence(rule, start, "Asia/Dubai"), start, "Asia/Dubai") == rule
    assert parse_graph_recurrence({"pattern": {"type": "relativeYearly"}, "range": {"type": "noEnd"}}, start, "UTC") is None
    assert parse_graph_recurrence({"pattern": {"type": "absoluteYearly", "month": 7, "dayOfMonth": 1}, "range": {"type": "noEnd"}}, start, "UTC") is None
    sunday_weeks = {"pattern": {"type": "weekly", "interval": 2, "daysOfWeek": ["monday", "friday"], "firstDayOfWeek": "sunday"}, "range": {"type": "noEnd"}}
    assert parse_graph_recurrence(sunday_weeks, start, "UTC") is None
    assert parse_graph_recurrence({"pattern": {"type": "daily", "interval": 1}, "range": {"type": "numbered", "numberOfOccurrences": 5000}}, start, "UTC") is None
    assert graph_zone("Arabian Standard Time") == "Asia/Dubai" and graph_zone("Asia/Riyadh") == "Asia/Riyadh"
    assert graph_zone("UTC") == "UTC" and graph_zone("Mars Standard Time") is None and graph_zone(None) is None


def test_removed_occurrences_are_only_inferred_when_graphs_instances_match_the_rule() -> None:
    from anum_api.calendar_providers import RemoteEvent

    now = utc_now().replace(minute=0, second=0, microsecond=0)
    since, until = now - timedelta(days=30), now + timedelta(days=365)
    start = now + timedelta(days=20)
    master = RemoteEvent(external_id="m", title="x", starts_at=start, ends_at=start + timedelta(hours=1), updated_at=now)
    rule = Recurrence(freq="daily", count=4)

    def inst(moment: datetime) -> RemoteEvent:
        return RemoteEvent(external_id=f"i{moment:%d}", title="x", starts_at=moment, ends_at=moment + timedelta(hours=1), updated_at=now, original_start=moment)

    starts = [start + timedelta(days=n) for n in range(4)]
    assert graph_removed_occurrences(rule, master, "UTC", [inst(starts[0]), inst(starts[2]), inst(starts[3])], since=since, until=until) == [starts[1]]
    # An instance the rule does not make: ANUM's reading disagrees with Graph's, so nothing is inferred.
    assert graph_removed_occurrences(rule, master, "UTC", [inst(starts[0] + timedelta(hours=1))], since=since, until=until) is None
    # No instance at all: nothing is taken as removed.
    assert graph_removed_occurrences(rule, master, "UTC", [], since=since, until=until) == []
    # Near the window's edge an occurrence may only have moved out of it: not taken as removed.
    edge = until - timedelta(days=3)
    edge_master = RemoteEvent(external_id="m", title="x", starts_at=edge, ends_at=edge + timedelta(hours=1), updated_at=now)
    assert graph_removed_occurrences(Recurrence(freq="daily", count=2), edge_master, "UTC", [inst(edge)], since=since, until=until) == []


# -- removed in ANUM, outside the sync window ------------------------------------------------------------


def test_an_occurrence_removed_in_anum_beyond_the_sync_window_is_removed_at_the_provider(fake: FakeCalendars) -> None:
    connect()
    start = first_start()
    series = create(series_body(recurrence={"freq": "weekly", "count": 60}))
    starts = [start + timedelta(weeks=n) for n in range(60)]
    far = starts[56]  # about 392 days ahead: past the 365 days that sync
    assert far > utc_now() + timedelta(days=365)
    fake.google_instances["g_new"] = [instance("g_new", s) for s in starts]
    response = client.delete(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, params={"scope": "this", "occurrence_start": far.isoformat()})
    assert response.status_code == 204
    assert fake.google_writes[-1] == {"method": "DELETE", "id": f"g_new_{far:%Y%m%dT%H%M%SZ}", "params": {"sendUpdates": "none"}}
    read = [q for q in fake.google_query if q.get("instances") == "/g_new/instances"][-1]
    assert read["timeMin"] < far.strftime("%Y-%m-%dT%H:%M:%SZ") < read["timeMax"]
