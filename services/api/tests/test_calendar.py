"""The workspace calendar (docs/calendar.md): events, attendees, permissions, next steps."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.authorization import ROLE_PERMISSIONS, Permission, Role
from anum_api.calendar_api import calendar_backend
from anum_api.schemas import Approval, ApprovalStatus, RiskLevel, utc_now
from anum_api.store import store

TENANT = "tenant_calendar"
WORKSPACE = "workspace_calendar"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_c", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_c", "x-user-roles": "member"}
OTHER_MEMBER = {**OWNER, "x-user-id": "member_other", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_c", "x-user-roles": "viewer"}
ELSEWHERE = {**OWNER, "x-workspace-id": "workspace_elsewhere"}

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    calendar_backend.clear()
    for collection in (store.tasks, store.approvals, store.events, store.audit_records):
        collection.clear()
    yield
    calendar_backend.clear()


def meeting(hours_from_now: float = 2, **overrides: Any) -> dict[str, Any]:
    start = utc_now().replace(microsecond=0) + timedelta(hours=hours_from_now)
    body: dict[str, Any] = {
        "title": "PMV Team Meeting",
        "kind": "meeting",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
        "location": "Microsoft Teams",
        "conference_url": "https://teams.example.com/meet/example",
        "attendees": [
            {"name": "Sajid Kamboh", "email": "sajid@example.com", "role": "Fleet Manager"},
            {"name": "Vinay Kumar", "role": "Workshop Manager", "response": "accepted"},
        ],
    }
    body.update(overrides)
    return body


def create(who: dict[str, str] = MEMBER, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/v1/calendar/events", headers=who, json=meeting(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_roles_hold_the_calendar_permissions() -> None:
    assert Permission.CALENDAR_READ in ROLE_PERMISSIONS[Role.VIEWER]
    assert Permission.CALENDAR_WRITE not in ROLE_PERMISSIONS[Role.VIEWER]
    assert {Permission.CALENDAR_READ, Permission.CALENDAR_WRITE} <= ROLE_PERMISSIONS[Role.MEMBER]
    assert Permission.CALENDAR_MANAGE in ROLE_PERMISSIONS[Role.OWNER]
    assert Permission.CALENDAR_MANAGE not in ROLE_PERMISSIONS[Role.MEMBER]


def test_create_list_and_open_events_with_attendees() -> None:
    event = create()
    assert event["status"] == "scheduled" and event["can_manage"] is True
    assert [a["response"] for a in event["attendees"]] == ["invited", "accepted"]
    assert all(a["id"].startswith("attendee") for a in event["attendees"])
    page = client.get("/api/v1/calendar/events", headers=VIEWER).json()
    assert [e["id"] for e in page["items"]] == [event["id"]]
    assert page["can_write"] is False and page["truncated"] is False
    opened = client.get(f"/api/v1/calendar/events/{event['id']}", headers=VIEWER).json()
    assert opened["can_manage"] is False and opened["conference_url"].startswith("https://")


def test_range_overlap_order_and_limits() -> None:
    later = create(hours_from_now=30, title="Tyre Review")
    sooner = create(hours_from_now=1, title="Supplier Call", kind="call", phone="+966 50 000 0000", conference_url=None)
    start = utc_now()
    params = {"start": start.isoformat(), "end": (start + timedelta(days=2)).isoformat()}
    items = client.get("/api/v1/calendar/events", headers=OWNER, params=params).json()["items"]
    assert [e["id"] for e in items] == [sooner["id"], later["id"]]
    narrow = {"start": start.isoformat(), "end": (start + timedelta(hours=3)).isoformat()}
    assert [e["id"] for e in client.get("/api/v1/calendar/events", headers=OWNER, params=narrow).json()["items"]] == [sooner["id"]]
    limited = client.get("/api/v1/calendar/events", headers=OWNER, params={**params, "limit": 1}).json()
    assert len(limited["items"]) == 1 and limited["truncated"] is True
    wide = {"start": start.isoformat(), "end": (start + timedelta(days=200)).isoformat()}
    assert client.get("/api/v1/calendar/events", headers=OWNER, params=wide).status_code == 422
    backwards = {"start": start.isoformat(), "end": (start - timedelta(hours=1)).isoformat()}
    assert client.get("/api/v1/calendar/events", headers=OWNER, params=backwards).status_code == 422


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"conference_url": "http://teams.example.com/x"}, "https"),
        ({"phone": "call me"}, "phone"),
        ({"attendees": [{"name": "A", "email": "not-an-email"}]}, "valid email"),
        ({"title": "   "}, "title"),
    ],
)
def test_invalid_events_are_refused(change: dict[str, Any], message: str) -> None:
    response = client.post("/api/v1/calendar/events", headers=MEMBER, json=meeting(**change))
    assert response.status_code == 422 and message in response.text


def test_end_must_follow_start_and_duplicate_attendees_are_refused() -> None:
    body = meeting()
    body["ends_at"] = body["starts_at"]
    assert client.post("/api/v1/calendar/events", headers=MEMBER, json=body).status_code == 422
    twice = meeting(attendees=[{"name": "A", "email": "a@example.com"}, {"name": "B", "email": "A@example.com"}])
    response = client.post("/api/v1/calendar/events", headers=MEMBER, json=twice)
    assert response.status_code == 422 and "listed twice" in response.text


def test_only_the_creator_or_an_owner_changes_an_event() -> None:
    assert client.post("/api/v1/calendar/events", headers=VIEWER, json=meeting()).status_code == 403
    event = create(MEMBER)
    url = f"/api/v1/calendar/events/{event['id']}"
    assert client.patch(url, headers=OTHER_MEMBER, json={"title": "Taken"}).status_code == 403
    assert client.delete(url, headers=VIEWER).status_code == 403
    keep = event["attendees"][0]
    moved = client.patch(url, headers=MEMBER, json={
        "title": "PMV Tyre Cost Review",
        "attendees": [keep, {"name": "Ajay Rana", "role": "Fleet Supervisor"}],
    })
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["title"] == "PMV Tyre Cost Review" and body["attendees"][0]["id"] == keep["id"]
    assert [a["name"] for a in body["attendees"]] == ["Sajid Kamboh", "Ajay Rana"]
    bad = client.patch(url, headers=MEMBER, json={"ends_at": event["starts_at"]})
    assert bad.status_code == 422 and "end after" in bad.text
    cancelled = client.patch(url, headers=OWNER, json={"status": "cancelled"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.get("/api/v1/calendar/events", headers=OWNER).json()["items"] == []
    shown = client.get("/api/v1/calendar/events", headers=OWNER, params={"include_cancelled": "true"}).json()["items"]
    assert [e["id"] for e in shown] == [event["id"]]
    assert client.delete(url, headers=OWNER).status_code == 204
    assert client.get(url, headers=OWNER).status_code == 404
    actions = {record.action for record in store.audit_records}
    assert {"calendar.event.created", "calendar.event.updated", "calendar.event.deleted"} <= actions


def test_the_organiser_records_attendee_replies() -> None:
    event = create(MEMBER)
    attendee = event["attendees"][0]["id"]
    url = f"/api/v1/calendar/events/{event['id']}/attendees/{attendee}/response"
    assert client.put(url, headers=OTHER_MEMBER, json={"response": "accepted"}).status_code == 403
    updated = client.put(url, headers=MEMBER, json={"response": "declined"})
    assert updated.status_code == 200 and updated.json()["attendees"][0]["response"] == "declined"
    missing = f"/api/v1/calendar/events/{event['id']}/attendees/attendee_unknown/response"
    assert client.put(missing, headers=MEMBER, json={"response": "accepted"}).status_code == 404


def test_events_never_cross_workspaces() -> None:
    event = create()
    assert client.get(f"/api/v1/calendar/events/{event['id']}", headers=ELSEWHERE).status_code == 404
    assert client.get("/api/v1/calendar/events", headers=ELSEWHERE).json()["items"] == []
    assert client.patch(f"/api/v1/calendar/events/{event['id']}", headers=ELSEWHERE, json={"title": "x"}).status_code == 404


def test_next_steps_come_from_the_workspace_state() -> None:
    assert client.get("/api/v1/calendar/next-steps", headers=MEMBER).json()["items"] == []
    coming = create(hours_from_now=2)
    ended = create(hours_from_now=-3, title="Report Review", attendees=[])
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Reply", "prompt": "Reply to the supplier"}).json()
    store.approvals["approval_example"] = Approval(
        id="approval_example", task_id=task["id"], action="email.send", risk_level=RiskLevel.HIGH,
        status=ApprovalStatus.PENDING, reason="Send the reply", created_at=utc_now(),
    )
    items = client.get("/api/v1/calendar/next-steps", headers=MEMBER).json()["items"]
    by_id = {item["id"]: item for item in items}
    agenda = by_id[f"agenda:{coming['id']}"]
    assert agenda["action"]["kind"] == "task" and "Sajid Kamboh" in agenda["action"]["prompt"]
    assert by_id[f"follow_up:{coming['id']}"]["title"] == "Follow up with 1 who have not replied"
    assert by_id[f"summary:{ended['id']}"]["action"]["prompt"].startswith("Write a short summary")
    assert by_id["approvals:pending"]["action"] == {
        "kind": "open", "title": None, "prompt": None, "destination": "approvals", "event_id": None,
    }
    # Viewers cannot create tasks, so they get no task suggestions.
    viewer_items = client.get("/api/v1/calendar/next-steps", headers=VIEWER).json()["items"]
    assert all(item["action"]["kind"] == "open" for item in viewer_items)


# --------------------------------------------------------------------------- repeating, all-day, reminders (docs/calendar.md#recurrence)


def weekly(**overrides: Any) -> dict[str, Any]:
    body = meeting(hours_from_now=24, **overrides)
    body.setdefault("timezone", "Asia/Dubai")
    body.setdefault("recurrence", {"freq": "weekly", "by_weekday": [], "count": 6})
    body.setdefault("reminders", [10, 1440])
    return body


def z(value: str | datetime) -> str:
    """The API's ISO form (UTC with Z) of an instant."""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def listed(who: dict[str, str] = MEMBER, days: int = 60, **params: Any) -> list[dict[str, Any]]:
    start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    response = client.get("/api/v1/calendar/events", headers=who, params={"start": start.isoformat(), "end": (start + timedelta(days=days)).isoformat(), **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_a_series_is_stored_once_and_listed_as_its_occurrences() -> None:
    event = create(**weekly())
    assert event["is_recurring"] is True and event["rrule"] == "FREQ=WEEKLY;COUNT=6" and event["reminders"] == [10, 1440]
    assert event["recurrence_until"] is not None and event["timezone"] == "Asia/Dubai"
    items = listed()
    assert len(items) == 6 and {i["id"] for i in items} == {event["id"]}
    starts = [i["starts_at"] for i in items]
    assert starts == sorted(starts) and all(i["occurrence_start"] == i["starts_at"] for i in items)
    assert len({i["occurrence_key"] for i in items}) == 6 and items[0]["occurrence_key"].startswith(event["id"] + "@")
    # Paged: the second page of two holds the rest and the first says more follow.
    first = client.get("/api/v1/calendar/events", headers=MEMBER, params={"start": items[0]["starts_at"], "end": items[-1]["ends_at"], "limit": 4}).json()
    assert len(first["items"]) == 4 and first["truncated"] is True
    second = client.get("/api/v1/calendar/events", headers=MEMBER, params={"start": items[0]["starts_at"], "end": items[-1]["ends_at"], "limit": 4, "offset": 4}).json()
    assert len(second["items"]) == 2 and second["truncated"] is False and second["offset"] == 4
    # A rule outside the subset, a bad zone, or too many reminders are refused.
    for change in ({"recurrence": {"freq": "hourly"}}, {"timezone": "Mars/Olympus"}, {"reminders": [1, 2, 3, 4, 5, 6]}, {"reminders": [-5]},
                   {"recurrence": {"freq": "monthly", "by_weekday": ["MO"]}}, {"recurrence": {"freq": "daily", "count": 3, "until": "2030-01-01"}}):
        assert client.post("/api/v1/calendar/events", headers=MEMBER, json=weekly(**change)).status_code == 422, change


def test_one_occurrence_can_be_deleted_changed_or_cancelled_on_its_own() -> None:
    series = create(**weekly())
    items = listed()
    second, third, fourth = items[1], items[2], items[3]
    # Delete one: it is skipped, the rest stay.
    gone = client.delete(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, params={"scope": "this", "occurrence_start": second["occurrence_start"]})
    assert gone.status_code == 204
    assert [i["occurrence_start"] for i in listed()] == [i["occurrence_start"] for i in items if i is not second]
    assert client.get(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, params={"occurrence_start": second["occurrence_start"]}).status_code == 404
    # Change one: a child row with its own time and title; the series keeps the others.
    moved_start = (datetime.fromisoformat(third["starts_at"]) + timedelta(hours=2)).isoformat()
    changed = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER,
                           json={"scope": "this", "occurrence_start": third["occurrence_start"], "title": "Moved meeting", "starts_at": moved_start,
                                 "ends_at": (datetime.fromisoformat(moved_start) + timedelta(hours=1)).isoformat(), "reminders": [5]})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["id"] == series["id"] and body["overridden"] is True and body["override_id"] and body["title"] == "Moved meeting"
    assert body["occurrence_start"] == third["occurrence_start"] and body["starts_at"] == z(moved_start)
    shown = listed()
    assert len(shown) == 5 and [i for i in shown if i["overridden"]][0]["title"] == "Moved meeting" and shown[0]["reminders"] == [10, 1440]
    opened = client.get(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, params={"occurrence_start": third["occurrence_start"]}).json()
    assert opened["title"] == "Moved meeting" and opened["reminders"] == [5]
    # Editing the same occurrence again updates the same child row.
    again = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, json={"scope": "this", "occurrence_start": third["occurrence_start"], "location": "Room 9"}).json()
    assert again["override_id"] == body["override_id"] and again["location"] == "Room 9" and again["title"] == "Moved meeting"
    # Cancel one: shown only with cancelled, marked as changed.
    cancelled = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, json={"scope": "this", "occurrence_start": fourth["occurrence_start"], "status": "cancelled"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert len(listed()) == 4 and len(listed(include_cancelled="true")) == 5
    # An occurrence that is not in the series is refused.
    bad = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, json={"scope": "this", "occurrence_start": (datetime.fromisoformat(third["starts_at"]) + timedelta(days=1)).isoformat(), "title": "x"})
    assert bad.status_code == 404
    assert client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, json={"scope": "this", "title": "x"}).status_code == 422
    # Deleting the whole series removes its children too.
    assert client.delete(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER).status_code == 204
    assert listed(include_cancelled="true") == []
    assert client.get(f"/api/v1/calendar/events/{body['override_id']}", headers=MEMBER).status_code == 404


def test_this_and_following_splits_the_series_and_all_moves_it() -> None:
    series = create(**weekly())
    items = listed()
    split_at = items[2]
    new_start = (datetime.fromisoformat(split_at["starts_at"]) + timedelta(hours=3)).isoformat()
    response = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER,
                            json={"scope": "following", "occurrence_start": split_at["occurrence_start"], "title": "New time", "starts_at": new_start,
                                  "ends_at": (datetime.fromisoformat(new_start) + timedelta(minutes=30)).isoformat()})
    assert response.status_code == 200, response.text
    fresh = response.json()
    assert fresh["id"] != series["id"] and fresh["recurrence"]["count"] == 4 and fresh["title"] == "New time"
    old = client.get(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER).json()
    assert old["recurrence"]["count"] == 2 and old["title"] == "PMV Team Meeting"
    shown = listed()
    assert len(shown) == 6 and [i["title"] for i in shown] == ["PMV Team Meeting"] * 2 + ["New time"] * 4
    assert shown[2]["starts_at"] == z(new_start)
    # Following from the first occurrence is the same as all.
    whole = client.patch(f"/api/v1/calendar/events/{fresh['id']}", headers=MEMBER, json={"scope": "following", "occurrence_start": shown[2]["occurrence_start"], "location": "Hall"})
    assert whole.status_code == 200 and whole.json()["id"] == fresh["id"] and whole.json()["location"] == "Hall"
    # "all" from an occurrence moves every occurrence by the same amount (and drops exceptions).
    assert client.delete(f"/api/v1/calendar/events/{fresh['id']}", headers=MEMBER, params={"scope": "this", "occurrence_start": shown[3]["occurrence_start"]}).status_code == 204
    assert len(listed()) == 5
    shifted = (datetime.fromisoformat(shown[4]["starts_at"]) + timedelta(hours=1)).isoformat()
    moved = client.patch(f"/api/v1/calendar/events/{fresh['id']}", headers=MEMBER, json={"scope": "all", "occurrence_start": shown[4]["occurrence_start"], "starts_at": shifted})
    assert moved.status_code == 200
    after = listed()
    assert len(after) == 6 and after[2]["starts_at"] == z(datetime.fromisoformat(new_start) + timedelta(hours=1))
    # Delete this and following from the fourth: the series ends before it.
    assert client.delete(f"/api/v1/calendar/events/{fresh['id']}", headers=MEMBER, params={"scope": "following", "occurrence_start": after[3]["occurrence_start"]}).status_code == 204
    assert len(listed()) == 3
    # Only the creator or an owner changes occurrences too.
    assert client.patch(f"/api/v1/calendar/events/{fresh['id']}", headers=OTHER_MEMBER, json={"scope": "this", "occurrence_start": after[2]["occurrence_start"], "title": "x"}).status_code == 403
    assert client.delete(f"/api/v1/calendar/events/{fresh['id']}", headers=VIEWER, params={"scope": "this", "occurrence_start": after[2]["occurrence_start"]}).status_code == 403


def test_a_series_without_an_end_and_a_monthly_rule_stop_at_the_window() -> None:
    body = weekly(recurrence={"freq": "monthly", "by_weekday": ["TU"], "by_setpos": 2})
    event = create(**body)
    assert event["recurrence_until"] is None and event["rrule"] == "FREQ=MONTHLY;BYDAY=2TU"
    items = listed(days=92)
    assert 3 <= len(items) <= 5
    assert items[0]["starts_at"] == event["starts_at"]
    from zoneinfo import ZoneInfo

    assert all(datetime.fromisoformat(i["starts_at"]).astimezone(ZoneInfo("Asia/Dubai")).weekday() == 1 for i in items[1:])


def test_all_day_events_take_dates_in_their_zone() -> None:
    body = meeting(all_day=True, timezone="Asia/Dubai", start_date="2030-03-10", end_date="2030-03-11", reminders=[0])
    body.pop("starts_at"), body.pop("ends_at")
    event = create(**body)
    assert event["all_day"] is True and event["start_date"] == "2030-03-10" and event["end_date"] == "2030-03-11"
    assert event["starts_at"] == "2030-03-09T20:00:00Z" and event["ends_at"] == "2030-03-11T20:00:00Z"
    response = client.get("/api/v1/calendar/events", headers=MEMBER, params={"start": "2030-03-10T00:00:00Z", "end": "2030-03-12T00:00:00Z"})
    assert [i["id"] for i in response.json()["items"]] == [event["id"]]
    # Timed instants given for an all-day event snap to local midnights; a date edit moves it.
    snapped = create(**meeting(all_day=True, timezone="Asia/Dubai", starts_at="2030-04-01T05:00:00Z", ends_at="2030-04-01T06:00:00Z"))
    assert snapped["start_date"] == "2030-04-01" and snapped["end_date"] == "2030-04-01" and snapped["starts_at"] == "2030-03-31T20:00:00Z"
    edited = client.patch(f"/api/v1/calendar/events/{snapped['id']}", headers=MEMBER, json={"start_date": "2030-04-03", "end_date": "2030-04-03"}).json()
    assert edited["start_date"] == "2030-04-03" and edited["starts_at"] == "2030-04-02T20:00:00Z"
    # Without a zone the workspace's (the API default) is used.
    default_zone = meeting(all_day=True, start_date="2030-05-01")
    default_zone.pop("starts_at"), default_zone.pop("ends_at")
    assert create(**default_zone)["timezone"] == "Asia/Dubai"
    assert client.post("/api/v1/calendar/events", headers=MEMBER, json={**meeting(all_day=True, start_date="2030-13-01"), "starts_at": None, "ends_at": None}).status_code == 422


def test_a_synced_event_cannot_become_a_series_and_a_series_is_not_pushed() -> None:
    from anum_api.calendar_api import calendar_backend as backend

    event = create()
    stored = backend.events[event["id"]]
    stored.connection_id, stored.external_id, stored.sync_state = "cal_1", "ext_1", "synced"
    refused = client.patch(f"/api/v1/calendar/events/{event['id']}", headers=MEMBER, json={"recurrence": {"freq": "daily", "count": 3}})
    assert refused.status_code == 422 and "cannot be made repeating" in refused.text
    assert client.patch(f"/api/v1/calendar/events/{event['id']}", headers=MEMBER, json={"all_day": True}).status_code == 422
    series = create(**weekly(sync=True))
    assert series["connection_id"] is None and series["sync_state"] is None


def test_next_steps_see_the_coming_occurrence_of_a_series() -> None:
    start = utc_now().replace(microsecond=0) - timedelta(days=7) + timedelta(hours=2)
    create(**weekly(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat(), recurrence={"freq": "weekly", "count": 10}))
    steps = client.get("/api/v1/calendar/next-steps", headers=OWNER).json()["items"]
    assert any(step["id"].startswith("agenda:") and "PMV Team Meeting" in step["title"] for step in steps)


def _occurrence(series: dict[str, Any], start: datetime) -> dict[str, Any]:
    opened = client.get(f"/api/v1/calendar/events/{series['id']}", headers=VIEWER, params={"occurrence_start": start.isoformat()})
    assert opened.status_code == 200, opened.text
    return opened.json()


def test_a_reply_to_one_occurrence_overrides_the_series_reply_for_that_occurrence_only() -> None:
    series = create(**weekly())
    first = datetime.fromisoformat(series["starts_at"].replace("Z", "+00:00"))
    second, third = first + timedelta(weeks=1), first + timedelta(weeks=2)
    sajid = series["attendees"][0]["id"]
    url = f"/api/v1/calendar/events/{series['id']}/attendees/{sajid}/response"
    replied = client.put(url, headers=MEMBER, json={"response": "declined", "scope": "this", "occurrence_start": second.isoformat()})
    assert replied.status_code == 200, replied.text
    assert replied.json()["attendees"][0]["response"] == "declined" and replied.json()["occurrence_replies"] == [sajid]
    assert replied.json()["occurrence_start"] == z(second)
    # The series default holds for every other occurrence; the list shows each occurrence's own reply.
    assert _occurrence(series, first)["attendees"][0]["response"] == "invited" and _occurrence(series, first)["occurrence_replies"] == []
    listed = client.get("/api/v1/calendar/events", headers=VIEWER, params={"start": series["starts_at"], "end": (third + timedelta(hours=2)).isoformat()}).json()["items"]
    assert [i["attendees"][0]["response"] for i in listed] == ["invited", "declined", "invited"]
    # A changed occurrence carries the reply onto its own row, and a reply there stays its own.
    moved = client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, json={"scope": "this", "occurrence_start": second.isoformat(), "location": "Depot 2"})
    assert moved.status_code == 200 and moved.json()["attendees"][0]["response"] == "declined" and moved.json()["occurrence_replies"] == [sajid]
    tentative = client.put(url, headers=MEMBER, json={"response": "tentative", "scope": "this", "occurrence_start": third.isoformat()})
    assert tentative.json()["attendees"][0]["response"] == "tentative"
    # A reply for the whole series replaces the single-occurrence replies of that attendee.
    everyone = client.put(url, headers=MEMBER, json={"response": "accepted", "scope": "all"})
    assert everyone.status_code == 200 and everyone.json()["attendees"][0]["response"] == "accepted"
    assert _occurrence(series, third)["attendees"][0]["response"] == "accepted"
    assert _occurrence(series, first)["attendees"][0]["response"] == "accepted"
    # The changed occurrence keeps its own row's reply until it is answered there.
    assert _occurrence(series, second)["attendees"][0]["response"] == "declined"
    audits = [a for a in store.audit_records if a.action == "calendar.attendee.response"]
    assert [a.metadata["scope"] for a in audits] == ["this", "this", "all"] and audits[0].metadata["occurrence_start"] == second.isoformat()


def test_occurrence_replies_are_checked_and_only_the_organiser_records_them() -> None:
    series = create(**weekly())
    first = datetime.fromisoformat(series["starts_at"].replace("Z", "+00:00"))
    sajid = series["attendees"][0]["id"]
    url = f"/api/v1/calendar/events/{series['id']}/attendees/{sajid}/response"
    assert client.put(url, headers=MEMBER, json={"response": "accepted", "scope": "this"}).status_code == 422
    missing = client.put(url, headers=MEMBER, json={"response": "accepted", "scope": "this", "occurrence_start": (first + timedelta(hours=3)).isoformat()})
    assert missing.status_code == 404 and missing.json()["error"]["message"] == "That occurrence does not exist in this series"
    denied = client.put(url, headers=VIEWER, json={"response": "accepted", "scope": "this", "occurrence_start": first.isoformat()})
    assert denied.status_code == 403 and "created this event" in denied.json()["error"]["message"]
    assert client.put(url, headers=OTHER_MEMBER, json={"response": "accepted", "scope": "this", "occurrence_start": first.isoformat()}).status_code == 403
    assert client.put(url, headers=ELSEWHERE, json={"response": "accepted"}).status_code == 404
    # Removing the occurrence or the attendee removes their reply to it.
    assert client.put(url, headers=MEMBER, json={"response": "declined", "scope": "this", "occurrence_start": first.isoformat()}).status_code == 200
    second = first + timedelta(weeks=1)
    assert client.put(url, headers=MEMBER, json={"response": "declined", "scope": "this", "occurrence_start": second.isoformat()}).status_code == 200
    from anum_api.calendar_api import calendar_backend as backend

    assert len(backend.events[series["id"]].occurrence_responses) == 2
    assert client.delete(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, params={"scope": "this", "occurrence_start": second.isoformat()}).status_code == 204
    assert len(backend.events[series["id"]].occurrence_responses) == 1
    kept = [{"id": a["id"], "name": a["name"], "email": a.get("email")} for a in series["attendees"][1:]]
    assert client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, json={"attendees": kept}).status_code == 200
    assert backend.events[series["id"]].occurrence_responses == []
    # A one-off event ignores the scope: the reply is the event's.
    single = create()
    one = client.put(f"/api/v1/calendar/events/{single['id']}/attendees/{single['attendees'][0]['id']}/response", headers=MEMBER,
                     json={"response": "tentative", "scope": "this", "occurrence_start": single["starts_at"]})
    assert one.status_code == 200 and one.json()["attendees"][0]["response"] == "tentative"


def test_email_reminders_are_a_subset_of_the_reminders() -> None:
    event = create(reminders=[10, 60], email_reminders=[60, 5])
    assert event["reminders"] == [10, 60] and event["email_reminders"] == [60]
    changed = client.patch(f"/api/v1/calendar/events/{event['id']}", headers=MEMBER, json={"reminders": [10]}).json()
    assert changed["email_reminders"] == []
    again = client.patch(f"/api/v1/calendar/events/{event['id']}", headers=MEMBER, json={"reminders": [10, 30], "email_reminders": [10, 30]}).json()
    assert again["email_reminders"] == [10, 30]
    assert client.post("/api/v1/calendar/events", headers=MEMBER, json=meeting(reminders=[10], email_reminders=[-1])).status_code == 422
