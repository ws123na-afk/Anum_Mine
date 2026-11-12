"""The workspace attendance manager view (docs/attendance.md#manager-view), in-memory stores.

Off by default; only owners (and organisation administrators) turn it on and read it; members learn it is
on; reports and corrections never appear in it. ``test_postgres_attendance.py`` checks the same rules
under RLS (migration ``0179_attendance_manager_view``).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.attendance.devices import register_device_authority
from anum_api.attendance.store import attendance_store, manager_reads
from anum_api.dependencies import memory_repository
from anum_api.schemas import TenantContext

TENANT, WORKSPACE = "tenant_att_mgr", "workspace_att_mgr"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_mgr", "x-user-roles": "owner"}
AIMAL = {**OWNER, "x-user-id": "aimal_mgr", "x-user-roles": "member"}
MAJID = {**OWNER, "x-user-id": "majid_mgr", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_mgr", "x-user-roles": "viewer"}
ELSEWHERE = {**OWNER, "x-workspace-id": "workspace_other_mgr"}
API = "/api/v1/attendance"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    attendance_store.clear()
    register_device_authority(None)
    memory_repository.store.audit_records.clear()
    yield
    attendance_store.clear()


def add(headers: dict[str, str], kind: str, at: datetime) -> None:
    response = client.post(f"{API}/corrections", json={"action": "add", "kind": kind, "occurred_at": at.isoformat(), "reason": "Recorded later"},
                           headers=headers)
    assert response.status_code == 201, response.text


def report(headers: dict[str, str], day: str) -> None:
    response = client.put(f"{API}/daily-reports/{day}", json={"project": "Private notes", "tasks_done": ["Secret task"], "expected_version": None},
                          headers=headers)
    assert response.status_code in (200, 201), response.text


def switch(on: bool, headers: dict[str, str] = OWNER, version: int | None = None):  # type: ignore[no-untyped-def]
    if version is None:
        version = client.get(f"{API}/workspace-settings", headers=headers).json()["version"]
    return client.put(f"{API}/workspace-settings", json={"manager_view": on, "expected_version": version}, headers=headers)


def worked_yesterday(headers: dict[str, str]) -> str:
    start = datetime.now(UTC).replace(hour=6, minute=0, second=0, microsecond=0) - timedelta(days=1)
    add(headers, "remote_start", start)
    add(headers, "remote_end", start + timedelta(hours=8))
    return start.date().isoformat()


def test_off_by_default_and_only_owners_may_turn_it_on() -> None:
    settings = client.get(f"{API}/workspace-settings", headers=AIMAL).json()
    assert settings["manager_view"] is False and settings["version"] == 0 and settings["can_manage"] is False
    assert client.get(f"{API}/workspace-settings", headers=OWNER).json()["can_manage"] is True
    assert switch(True, AIMAL, 0).status_code == 403
    assert client.get(f"{API}/workspace-settings", headers=VIEWER).status_code == 403
    # Off: even an owner reads nothing, and is told how to turn it on.
    off = client.get(f"{API}/manager", headers=OWNER)
    assert off.status_code == 409 and "turn it on" in off.json()["error"]["message"]
    on = switch(True)
    assert on.status_code == 200 and on.json()["manager_view"] is True and on.json()["version"] == 1
    # A stale version is refused; members now learn it is on.
    assert switch(False, version=0).status_code == 409
    assert client.get(f"{API}/workspace-settings", headers=AIMAL).json()["manager_view"] is True
    actions = [r.action for r in memory_repository.store.audit_records if r.workspace_id == WORKSPACE]
    assert "attendance.manager_view" in actions


def test_owners_see_everyone_read_only_without_reports() -> None:
    day = worked_yesterday(AIMAL)
    worked_yesterday(MAJID)
    report(AIMAL, day)
    assert switch(True).status_code == 200
    page = client.get(f"{API}/manager", params={"date": day, "limit": 1}, headers=OWNER)
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["total"] == 2 and [r["user_id"] for r in body["rows"]] == ["aimal_mgr"]
    row = body["rows"][0]
    assert row["worked_seconds"] == 8 * 3600 and row["report"] is None and row["reports_shared"] is False
    second = client.get(f"{API}/manager", params={"date": day, "limit": 1, "offset": 1}, headers=OWNER).json()
    assert [r["user_id"] for r in second["rows"]] == ["majid_mgr"]
    # Members never get the view; another workspace sees nobody of this one.
    assert client.get(f"{API}/manager", headers=AIMAL).status_code == 403
    assert client.get(f"{API}/manager", headers=ELSEWHERE).status_code == 409
    # The view is read only: an owner still cannot read a report without the person's share.
    assert client.get(f"{API}/daily-reports/{day}", params={"person": "aimal_mgr"}, headers=OWNER).status_code == 404
    assert "attendance.manager.read" in [r.action for r in memory_repository.store.audit_records]


def test_turning_it_off_closes_the_view_at_once() -> None:
    worked_yesterday(AIMAL)
    switch(True)
    assert client.get(f"{API}/manager", headers=OWNER).status_code == 200
    assert switch(False).status_code == 200
    assert client.get(f"{API}/manager", headers=OWNER).status_code == 409
    assert client.get(f"{API}/manager/export.csv", headers=OWNER).status_code == 409


def test_csv_has_one_row_per_person_and_day_and_no_formulas() -> None:
    day = worked_yesterday(AIMAL)
    client.put(f"{API}/profile", json={"role_title": "=HYPERLINK(1)", "time_zone": "Asia/Dubai", "expected_version": 0}, headers=MAJID)
    worked_yesterday(MAJID)
    switch(True)
    response = client.get(f"{API}/manager/export.csv", params={"from": day, "to": day}, headers=OWNER)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == f'attachment; filename="attendance-workspace-{day}-{day}.csv"'
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:4] == ["person", "name", "role_title", "date"]
    people = {r[0]: r for r in rows[1:] if r[3] == day}
    assert set(people) == {"aimal_mgr", "majid_mgr"}
    assert people["majid_mgr"][2] == "'=HYPERLINK(1)"
    assert people["aimal_mgr"][3 + 7] == "8.00"  # worked_hours
    assert client.get(f"{API}/manager/export.csv", headers=AIMAL).status_code == 403
    bad = client.get(f"{API}/manager/export.csv", params={"from": day, "to": "2020-01-01"}, headers=OWNER)
    assert bad.status_code == 422
    long = client.get(f"{API}/manager/export.csv", params={"from": "2026-01-01", "to": "2026-06-30"}, headers=OWNER)
    assert long.status_code == 422 and "92 days" in long.json()["error"]["message"]


def test_the_store_widens_reads_only_in_a_manager_unit_with_the_switch_on() -> None:
    worked_yesterday(AIMAL)
    owner = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_mgr")
    far = datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)
    assert attendance_store.list_events(owner, "aimal_mgr", *far) == []
    with manager_reads():
        assert attendance_store.list_events(owner, "aimal_mgr", *far) == []  # switch still off
    switch(True)
    assert attendance_store.list_events(owner, "aimal_mgr", *far) == []  # not a manager unit
    with manager_reads():
        assert len(attendance_store.list_events(owner, "aimal_mgr", *far)) == 2
        assert attendance_store.list_corrections(owner, "aimal_mgr", limit=10, offset=0)[0] == []
    # Only a manager unit may write the switch.
    with pytest.raises(PermissionError):
        attendance_store.save_workspace_settings(attendance_store.get_workspace_settings(owner), 1)
