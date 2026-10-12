"""The workspace calendar (docs/calendar.md): events, attendees, permissions, next steps."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
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
