"""Calendar reminders (docs/calendar.md#reminders): due once, at the right time, to the right
people, never for a skipped or cancelled occurrence, and through the team reminder sweep."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.calendar_api import calendar_backend
from anum_api.calendar_reminders import NOTICE_KIND, memory_send_store, run_calendar_reminder_pass
from anum_api.schemas import TenantContext, utc_now
from anum_api.store import store
from anum_api.team_models import MemberProfile
from anum_api.team_store import memory_team_store
from anum_api.team_work import run_reminder_pass

from test_calendar import MEMBER, OWNER, TENANT, VIEWER, WORKSPACE, meeting

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    for cleared in (calendar_backend, memory_send_store, memory_team_store):
        cleared.clear()
    store.audit_records.clear()
    yield
    for cleared in (calendar_backend, memory_send_store, memory_team_store):
        cleared.clear()


def ctx(user: str) -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=["owner"])


def notices(user: str) -> list[Any]:
    return [n for n in memory_team_store.list_notifications(ctx(user), limit=200, before=None, unread_only=False) if n.kind == NOTICE_KIND]


def create(**overrides: Any) -> dict[str, Any]:
    response = client.post("/api/v1/calendar/events", headers=MEMBER, json=meeting(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_a_reminder_fires_once_at_its_time_and_never_after_the_start() -> None:
    now = utc_now().replace(microsecond=0)
    start = now + timedelta(minutes=30)
    event = create(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat(), reminders=[10, 60], timezone="Asia/Dubai")
    # Too early for both.
    assert run_calendar_reminder_pass(now=now - timedelta(minutes=45)) == 0
    # The one-hour reminder is due (its time passed while the job was not running); the ten-minute one is not.
    assert run_calendar_reminder_pass(now=now) == 1
    sent = notices("member_c")
    assert len(sent) == 1 and sent[0].title == "PMV Team Meeting starts in 30 minutes"
    assert "Asia/Dubai" in sent[0].body and "Microsoft Teams" in sent[0].body and sent[0].email_status.value == "not_requested"
    # A second pass at the same time (a restart) sends nothing again.
    assert run_calendar_reminder_pass(now=now) == 0 and len(notices("member_c")) == 1
    assert run_calendar_reminder_pass(now=now + timedelta(minutes=21)) == 1
    assert {n.title for n in notices("member_c")} == {"PMV Team Meeting starts in 30 minutes", "PMV Team Meeting starts in 9 minutes"}
    # Once the event has started nothing more is sent, and a cancelled event sends nothing.
    assert run_calendar_reminder_pass(now=start + timedelta(minutes=1)) == 0
    later = create(starts_at=(now + timedelta(hours=3)).isoformat(), ends_at=(now + timedelta(hours=4)).isoformat(), reminders=[5])
    assert client.patch(f"/api/v1/calendar/events/{later['id']}", headers=MEMBER, json={"status": "cancelled"}).status_code == 200
    assert run_calendar_reminder_pass(now=now + timedelta(hours=2, minutes=58)) == 0
    audits = [a for a in store.audit_records if a.action == "calendar.reminder_sent"]
    assert len(audits) == 2 and audits[0].target == event["id"] and audits[0].metadata["recipients"] == 1
    assert all("PMV" not in str(a.metadata) for a in audits)


def test_series_remind_per_occurrence_but_not_for_skipped_cancelled_or_moved_ones() -> None:
    now = utc_now().replace(microsecond=0)
    first = now + timedelta(hours=2)
    series = create(starts_at=first.isoformat(), ends_at=(first + timedelta(hours=1)).isoformat(), reminders=[15], timezone="Asia/Dubai",
                    recurrence={"freq": "daily", "count": 5})
    occurrences = [first + timedelta(days=n) for n in range(5)]
    # Skip the second, cancel the third, move the fourth two hours later.
    assert client.delete(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER, params={"scope": "this", "occurrence_start": occurrences[1].isoformat()}).status_code == 204
    assert client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER,
                        json={"scope": "this", "occurrence_start": occurrences[2].isoformat(), "status": "cancelled"}).status_code == 200
    moved = occurrences[3] + timedelta(hours=2)
    assert client.patch(f"/api/v1/calendar/events/{series['id']}", headers=MEMBER,
                        json={"scope": "this", "occurrence_start": occurrences[3].isoformat(), "starts_at": moved.isoformat(), "ends_at": (moved + timedelta(hours=1)).isoformat()}).status_code == 200
    # First occurrence: reminded once.
    assert run_calendar_reminder_pass(now=occurrences[0] - timedelta(minutes=14)) == 1
    assert run_calendar_reminder_pass(now=occurrences[0] - timedelta(minutes=5)) == 0
    # Skipped and cancelled occurrences: nothing.
    assert run_calendar_reminder_pass(now=occurrences[1] - timedelta(minutes=10)) == 0
    assert run_calendar_reminder_pass(now=occurrences[2] - timedelta(minutes=10)) == 0
    # The moved occurrence reminds at its new time, not the old one.
    assert run_calendar_reminder_pass(now=occurrences[3] - timedelta(minutes=10)) == 0
    assert run_calendar_reminder_pass(now=moved - timedelta(minutes=10)) == 1
    assert run_calendar_reminder_pass(now=moved - timedelta(minutes=5)) == 0
    # The fifth is an ordinary occurrence again; after the series ends nothing is due.
    assert run_calendar_reminder_pass(now=occurrences[4] - timedelta(minutes=1)) == 1
    assert run_calendar_reminder_pass(now=occurrences[4] + timedelta(days=1)) == 0
    assert len(notices("member_c")) == 3 and all(n.title.startswith("PMV Team Meeting starts") for n in notices("member_c"))


def test_recipients_are_the_creator_and_members_whose_profile_email_is_invited() -> None:
    now = utc_now().replace(microsecond=0)
    start = now + timedelta(minutes=20)
    memory_team_store.save_profile(ctx("owner_c"), MemberProfile(user_id="owner_c", display_name="Sajid", email="Sajid@example.com"))
    memory_team_store.save_profile(ctx("viewer_c"), MemberProfile(user_id="viewer_c", display_name="Someone", email="someone@example.com"))
    create(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat(), reminders=[30])
    assert run_calendar_reminder_pass(now=now) == 2
    assert len(notices("member_c")) == 1 and len(notices("owner_c")) == 1 and notices("viewer_c") == []
    # Another workspace's events never reach this one.
    assert memory_team_store.list_notifications(TenantContext(tenant_id=TENANT, workspace_id="workspace_elsewhere", user_id="member_c", roles=["owner"]),
                                                limit=50, before=None, unread_only=False) == []


def test_all_day_reminders_name_the_day_and_the_sweep_runs_the_pass() -> None:
    now = utc_now().replace(microsecond=0)
    body = meeting(all_day=True, timezone="UTC", start_date=(now + timedelta(days=1)).date().isoformat(), reminders=[1440])
    body.pop("starts_at"), body.pop("ends_at")
    assert client.post("/api/v1/calendar/events", headers=MEMBER, json=body).status_code == 201
    counts = run_reminder_pass(now=now + timedelta(hours=1))
    assert counts["calendar_reminded"] == 1
    sent = notices("member_c")[0]
    assert sent.title.startswith("PMV Team Meeting:") and sent.body.startswith("All day")
    assert client.get("/api/v1/notifications", headers=MEMBER).json()["items"][0]["kind"] == NOTICE_KIND
    assert client.get("/api/v1/calendar/events", headers=VIEWER).status_code == 200 and OWNER


# -- email reminders --------------------------------------------------------------------------------


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str, str]]]:
    from anum_api import notifications
    from anum_api.calendar_reminders import memory_email_store
    from anum_api.contacts.store import memory_data
    from anum_api.onboarding import _notifications
    from anum_api.settings import settings

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "anum@example.com")
    monkeypatch.setattr(notifications, "send_email", lambda to, subject, text: sent.append((to, subject, text)))
    for cleared in (memory_email_store, memory_data):
        cleared.clear()
    _notifications._items.clear()
    yield sent
    for cleared in (memory_email_store, memory_data):
        cleared.clear()
    _notifications._items.clear()


def _email_on(user: str, on: bool = True) -> None:
    from anum_api.onboarding import NotificationPreferences, _notifications

    _notifications.save(ctx(user), NotificationPreferences(email_enabled=on))


def test_an_email_reminder_goes_once_in_english_and_arabic_to_people_who_want_email(mail: list[tuple[str, str, str]]) -> None:
    now = utc_now().replace(microsecond=0)
    start = now + timedelta(minutes=50)
    memory_team_store.save_profile(ctx("member_c"), MemberProfile(user_id="member_c", display_name="Mona", email="mona@example.com"))
    memory_team_store.save_profile(ctx("owner_c"), MemberProfile(user_id="owner_c", display_name="Sajid", email="sajid@example.com"))
    _email_on("member_c")
    _email_on("owner_c", False)
    event = create(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat(), reminders=[10, 60], email_reminders=[60],
                   timezone="Asia/Dubai")
    assert event["email_reminders"] == [60]
    # The hour reminder is due: in app for both, by email only for the person with email on.
    assert run_calendar_reminder_pass(now=now) == 2
    assert [m[0] for m in mail] == ["mona@example.com"]
    to, subject, body = mail[0]
    assert subject == "ANUM: PMV Team Meeting starts in 50 minutes"
    assert "Asia/Dubai" in body and "At Microsoft Teams" in body and "teams.example.com" not in body
    assert "تذكير: PMV Team Meeting" in body and "بعد 50 دقيقة" in body and "المكان: Microsoft Teams" in body
    assert "—" not in body and "–" not in body
    # A second pass (a restart, a second process) emails nobody again.
    assert run_calendar_reminder_pass(now=now) == 0 and len(mail) == 1
    # The ten-minute reminder is in app only.
    assert run_calendar_reminder_pass(now=start - timedelta(minutes=9)) == 2 and len(mail) == 1
    audits = [a for a in store.audit_records if a.action == "calendar.reminder_emailed"]
    assert sorted(a.metadata["status"] for a in audits) == ["sent", "skipped"]
    skipped = next(a for a in audits if a.metadata["status"] == "skipped")
    assert skipped.metadata["recipient"] == "owner_c" and "Email notifications are off" in skipped.metadata["detail"]
    assert all("mona@" not in str(a.metadata) and "PMV" not in str(a.metadata) for a in audits)


def test_an_email_reminder_honours_do_not_contact_and_needs_an_address(mail: list[tuple[str, str, str]]) -> None:
    now = utc_now().replace(microsecond=0)
    start = now + timedelta(minutes=20)
    memory_team_store.save_profile(ctx("member_c"), MemberProfile(user_id="member_c", display_name="Mona", email="mona@example.com"))
    memory_team_store.save_profile(ctx("owner_c"), MemberProfile(user_id="owner_c", display_name="Sajid", email="sajid@example.com"))
    _email_on("member_c")
    _email_on("owner_c")
    person = client.post("/api/v1/contacts", headers=MEMBER, json={"name": "Mona", "emails": ["mona@example.com"]})
    assert person.status_code == 201, person.text
    blocked = client.put(f"/api/v1/contacts/{person.json()['person']['id']}/do-not-contact", headers=MEMBER, json={"channel": "email", "reason": "Asked by email"})
    assert blocked.status_code == 201, blocked.text
    create(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat(), reminders=[30], email_reminders=[30])
    assert run_calendar_reminder_pass(now=now) == 2
    assert [m[0] for m in mail] == ["sajid@example.com"]
    details = {a.metadata["recipient"]: a.metadata for a in store.audit_records if a.action == "calendar.reminder_emailed"}
    assert details["member_c"]["status"] == "skipped" and "do not contact" in details["member_c"]["detail"]
    assert details["owner_c"]["status"] == "sent"


def test_no_mail_server_means_no_email_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.calendar_reminders import memory_email_store

    memory_email_store.clear()
    now = utc_now().replace(microsecond=0)
    start = now + timedelta(minutes=20)
    create(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat(), reminders=[30], email_reminders=[30])
    assert run_calendar_reminder_pass(now=now) == 1
    assert memory_email_store.rows == {}
