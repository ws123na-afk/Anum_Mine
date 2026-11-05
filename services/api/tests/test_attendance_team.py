"""Team attendance and report submission (docs/attendance.md#team-attendance), in-memory stores.

The team view lists the caller and only the colleagues whose active share names the caller; a share
never allows a write, and a report's status is visible only when the share includes reports.
``test_postgres_attendance.py`` checks the same rules under RLS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.attendance.devices import register_device_authority
from anum_api.attendance.store import attendance_store

TENANT, WORKSPACE = "tenant_att_team", "workspace_att_team"
ME = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "member_team", "x-user-roles": "member"}
AIMAL = {**ME, "x-user-id": "aimal_team"}
MAJID = {**ME, "x-user-id": "majid_team"}
STRANGER = {**ME, "x-user-id": "stranger_team"}
VIEWER = {**ME, "x-user-id": "viewer_team", "x-user-roles": "viewer"}
ELSEWHERE = {**ME, "x-workspace-id": "workspace_other_team"}
API = "/api/v1/attendance"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    attendance_store.clear()
    register_device_authority(None)
    yield
    attendance_store.clear()


def site(headers: dict[str, str], name: str = "Riyadh Office") -> str:
    response = client.post(f"{API}/sites", json={"name": name, "kind": "workplace"}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add(headers: dict[str, str], kind: str, at: datetime, site_id: str | None = None) -> None:
    response = client.post(f"{API}/corrections", json={"action": "add", "kind": kind, "occurred_at": at.isoformat(), "site_id": site_id,
                                                       "reason": "Recorded later"}, headers=headers)
    assert response.status_code == 201, response.text


def share(owner: dict[str, str], viewer: dict[str, str], reports: bool) -> str:
    response = client.post(f"{API}/shares", json={"viewer_user_id": viewer["x-user-id"], "include_reports": reports}, headers=owner)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def team(headers: dict[str, str], **params: object):  # type: ignore[no-untyped-def]
    return client.get(f"{API}/team", params=params, headers=headers)


def test_team_lists_only_me_and_people_who_share_with_me() -> None:
    alone = team(ME)
    assert alone.status_code == 200
    body = alone.json()
    assert body["total"] == 1 and [r["user_id"] for r in body["rows"]] == [ME["x-user-id"]]
    assert body["rows"][0]["me"] is True and body["rows"][0]["report"] == {"status": "none", "updated_at": None, "submitted_at": None, "summary": None}
    site(STRANGER)
    share(AIMAL, ME, reports=False)
    share(MAJID, ME, reports=True)
    share(STRANGER, MAJID, reports=True)  # shared with someone else: never in my roster
    rows = team(ME).json()["rows"]
    assert [r["user_id"] for r in rows] == [ME["x-user-id"], AIMAL["x-user-id"], MAJID["x-user-id"]]
    assert all(r["user_id"] != STRANGER["x-user-id"] for r in rows)
    # Another workspace sees nothing of these shares.
    assert [r["user_id"] for r in team(ELSEWHERE).json()["rows"]] == [ME["x-user-id"]]


def test_team_row_has_first_check_in_source_breaks_sites_and_hours() -> None:
    office = site(AIMAL)
    day = date.today() - timedelta(days=2)
    base = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=4)  # Dubai local midnight
    add(AIMAL, "arrive", base + timedelta(hours=8, minutes=14), office)
    add(AIMAL, "break_start", base + timedelta(hours=12, minutes=36))
    add(AIMAL, "break_end", base + timedelta(hours=13, minutes=4))
    add(AIMAL, "leave", base + timedelta(hours=16), office)
    share(AIMAL, ME, reports=False)
    rows = team(ME, date=day.isoformat()).json()["rows"]
    row = next(r for r in rows if r["user_id"] == AIMAL["x-user-id"])
    assert row["day"] == day.isoformat() and row["site_names"] == ["Riyadh Office"]
    assert row["first_check_in"].startswith((base + timedelta(hours=8, minutes=14)).strftime("%Y-%m-%dT%H:%M"))
    assert row["first_check_in_source"] == "manual"
    assert len(row["breaks"]) == 1 and row["break_seconds"] == 28 * 60
    assert row["worked_seconds"] == (7 * 60 + 46 - 28) * 60
    assert row["status"] is None  # a past day has no "now"
    # Reports were not shared: no status, no summary.
    assert row["reports_shared"] is False and row["report"] is None


def test_report_status_only_through_a_share_that_includes_reports() -> None:
    today = date.today().isoformat()
    saved = client.put(f"{API}/daily-reports/{today}", json={"tasks_done": ["Fleet readiness checks"]}, headers=MAJID)
    assert saved.status_code == 201 and saved.json()["submitted_at"] is None
    share(MAJID, ME, reports=True)
    share(AIMAL, ME, reports=False)
    client.put(f"{API}/daily-reports/{today}", json={"tasks_done": ["Hidden work"]}, headers=AIMAL)
    rows = {r["user_id"]: r for r in team(ME, date=today).json()["rows"]}
    assert rows[MAJID["x-user-id"]]["report"]["status"] == "draft"
    assert rows[MAJID["x-user-id"]]["report"]["summary"] == "Fleet readiness checks"
    assert rows[AIMAL["x-user-id"]]["report"] is None
    assert "Hidden work" not in str(team(ME, date=today).json())


def test_submit_sets_submitted_once_and_later_edits_keep_it() -> None:
    today = date.today().isoformat()
    draft = client.put(f"{API}/daily-reports/{today}", json={"tasks_done": ["Supplier follow ups"]}, headers=ME)
    assert draft.json()["submitted_at"] is None
    submitted = client.put(f"{API}/daily-reports/{today}", json={"tasks_done": ["Supplier follow ups"], "expected_version": 1, "submit": True}, headers=ME)
    assert submitted.status_code == 200 and submitted.json()["submitted_at"] is not None
    first = submitted.json()["submitted_at"]
    edited = client.put(f"{API}/daily-reports/{today}", json={"tasks_done": ["Supplier follow ups", "More"], "expected_version": 2}, headers=ME)
    assert edited.status_code == 200 and edited.json()["submitted_at"] == first
    mine = team(ME, date=today).json()["rows"][0]
    assert mine["report"]["status"] == "submitted" and mine["report"]["submitted_at"] == first


def test_team_is_read_only_paged_and_refused_to_viewers() -> None:
    assert team(VIEWER).status_code == 403
    for person in (AIMAL, MAJID, STRANGER):
        share(person, ME, reports=False)
    page = team(ME, limit=2, offset=1).json()
    assert page["total"] == 4 and [r["user_id"] for r in page["rows"]] == [AIMAL["x-user-id"], MAJID["x-user-id"]]
    assert team(ME, limit=0).status_code == 422 and team(ME, limit=101).status_code == 422
    assert client.post(f"{API}/team", headers=ME).status_code == 405
    # A revoked share leaves the roster at once.
    shares = client.get(f"{API}/shares", headers=AIMAL).json()["mine"]
    assert client.delete(f"{API}/shares/{shares[0]['id']}", headers=AIMAL).status_code == 204
    assert AIMAL["x-user-id"] not in [r["user_id"] for r in team(ME).json()["rows"]]


def test_submit_migration_is_expand_only_and_chained() -> None:
    text = (Path(__file__).parents[1] / "migrations" / "versions" / "0141_attendance_report_submit.py").read_text(encoding="utf-8")
    assert 'revision = "0141_attendance_report_submit"' in text and 'down_revision = "0140_finance_guardian_reviews"' in text
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert 'op.add_column("attendance_daily_reports", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))' in upgrade
    assert "drop" not in upgrade.lower()


def test_an_overnight_shift_still_open_after_midnight_shows_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Checked in at 22:00 Dubai, still working at 00:30: the roster shows them on site, like /today does."""
    from anum_api.attendance import api as attendance_api

    office = site(AIMAL)
    arrived = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)  # 22:00 in Dubai on 4 Nov
    monkeypatch.setattr(attendance_api, "utc_now", lambda: datetime(2026, 11, 4, 20, 30, tzinfo=UTC))  # 00:30 on 5 Nov
    add(AIMAL, "arrive", arrived, office)
    share(AIMAL, ME, reports=False)
    row = next(r for r in team(ME).json()["rows"] if r["user_id"] == AIMAL["x-user-id"])
    assert row["day"] == "2026-11-05"
    assert row["status"]["at_site"] is True
    assert row["first_check_in"] is not None and row["first_check_in"].startswith("2026-11-04T18:00")
    assert row["site_names"] == ["Riyadh Office"] and row["open"] is True
