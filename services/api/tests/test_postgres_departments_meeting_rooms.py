"""Departments, the meeting to room link and the starter campus department fill, as the non-owner app role."""
from __future__ import annotations

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture the hq fixture needs)
from test_postgres_office_hq import (  # noqa: F401 (fixtures)
    OWNER_A,
    _scoped,
    headers,
    hq,
    run_sql,
    seed_agent,
    seed_member,
)

pytestmark = pytest.mark.database
MEETINGS = "/api/v1/agent-meetings"
CAMPUS = "/api/v1/office/starter-campus"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def seed_room(engine: Engine, tenant: str, workspace: str, room: str, kind: str = "meeting", department: str | None = None) -> None:
    run_sql(engine, "insert into office_buildings (id, tenant_id, workspace_id, created_by, name, timezone) values (:b, :t, :w, 'x', 'HQ', 'Asia/Dubai') "
                    "on conflict do nothing", b=f"b-{workspace}", t=tenant, w=workspace)
    run_sql(engine, "insert into office_floors (id, tenant_id, workspace_id, created_by, building_id, level, name) values (:f, :t, :w, 'x', :b, 0, 'Ground') "
                    "on conflict do nothing", f=f"f-{workspace}", b=f"b-{workspace}", t=tenant, w=workspace)
    run_sql(engine, "insert into office_rooms (id, tenant_id, workspace_id, created_by, floor_id, name, kind, capacity, team_department) "
                    "values (:r, :t, :w, 'x', :f, :n, :k, 8, :d)", r=room, f=f"f-{workspace}", t=tenant, w=workspace, n=room, k=kind, d=department)


def create(hq: TestClient, **body):
    return hq.post(MEETINGS, headers=OWNER(), json={"title": "Weekly sync", **body})


def one(engine: Engine, sql: str, **params):
    with engine.begin() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params)]


def test_every_new_department_is_storable_and_an_unknown_one_is_refused(hq: TestClient, database_engine: Engine) -> None:
    for i, department in enumerate(("sales", "marketing", "technology", "research", "legal", "compliance", "security", "hr", "procurement", "executive", "custom")):
        seed_room(database_engine, TENANT_A, WORKSPACE_A, f"dept-{i}", "office", department)
    assert one(database_engine, "select count(*) from office_rooms where id like 'dept-%'") == [(11,)]
    with pytest.raises(DBAPIError):
        seed_room(database_engine, TENANT_A, WORKSPACE_A, "bad", "office", "astrology")
    # Through the API as well: the builder accepts the new values and refuses others.
    patched = hq.patch(f"/api/v1/office-spaces/rooms/dept-0", headers=OWNER(), json={"expected_revision": 1, "team_department": "legal"})
    assert patched.status_code == 200 and patched.json()["team_department"] == "legal", patched.text
    executive = hq.patch(f"/api/v1/office-spaces/rooms/dept-0", headers=OWNER(), json={"expected_revision": 2, "team_department": "executive"})
    assert executive.status_code == 200 and executive.json()["team_department"] == "executive", executive.text
    refused = hq.patch(f"/api/v1/office-spaces/rooms/dept-0", headers=OWNER(), json={"expected_revision": 3, "team_department": "astrology"})
    assert refused.status_code == 422


def test_a_meeting_started_in_a_room_stores_the_link_and_reports_it(hq: TestClient, database_engine: Engine) -> None:
    seed_room(database_engine, TENANT_A, WORKSPACE_A, "meet-1")
    made = create(hq, room_id="meet-1")
    assert made.status_code == 201, made.text
    meeting = made.json()
    assert meeting["room_id"] == "meet-1"
    assert hq.get(f"{MEETINGS}/{meeting['id']}", headers=OWNER()).json()["room_id"] == "meet-1"
    # A meeting with no room stays valid and is not in any room's list.
    plain = create(hq).json()
    assert plain["room_id"] is None
    listed = hq.get(MEETINGS, headers=OWNER(), params={"room_id": "meet-1"}).json()["items"]
    assert [m["id"] for m in listed] == [meeting["id"]]
    assert {m["id"] for m in hq.get(MEETINGS, headers=OWNER()).json()["items"]} == {meeting["id"], plain["id"]}
    # The audit record carries the room and the state event announces it.
    assert one(database_engine, "select metadata->>'room_id' from audit_records where action = 'agent_meeting.create' and target = :m", m=meeting["id"]) == [("meet-1",)]
    joined = hq.post(f"{MEETINGS}/{meeting['id']}/join-self", headers=OWNER(), json={"expected_revision": 1})
    assert joined.status_code == 200
    started = hq.post(f"{MEETINGS}/{meeting['id']}/state", headers=OWNER(), json={"status": "active", "expected_revision": joined.json()["revision"]})
    assert started.status_code == 200 and started.json()["room_id"] == "meet-1"
    events = one(database_engine, "select payload->>'room_id', payload->>'status' from agent_meeting_events where session_id = :m and kind = 'state'", m=meeting["id"])
    assert events == [("meet-1", "active")]
    live = hq.get(MEETINGS, headers=OWNER(), params={"status": "active"}).json()["items"]
    assert [m["id"] for m in live] == [meeting["id"]]
    ended = hq.post(f"{MEETINGS}/{meeting['id']}/state", headers=OWNER(), json={"status": "finished", "expected_revision": started.json()["revision"]})
    assert ended.status_code == 200
    assert hq.get(MEETINGS, headers=OWNER(), params={"status": "active"}).json()["items"] == [], "a finished meeting is no longer live"


def test_only_a_meeting_room_of_this_workspace_can_hold_a_meeting(hq: TestClient, database_engine: Engine) -> None:
    seed_room(database_engine, TENANT_A, WORKSPACE_A, "desk-room", kind="office")
    seed_room(database_engine, TENANT_B, WORKSPACE_B, "other-room")
    assert create(hq, room_id="desk-room").status_code == 422
    assert create(hq, room_id="no-such-room").status_code == 404
    assert create(hq, room_id="other-room").status_code == 404, "another tenant's room is invisible"
    assert create(hq, room_id="bad id!").status_code == 422
    assert one(database_engine, "select count(*) from agent_meeting_sessions") == [(0,)]


def test_the_database_itself_rejects_a_room_of_another_workspace(hq: TestClient, database_engine: Engine) -> None:
    seed_room(database_engine, TENANT_B, WORKSPACE_B, "other-room")
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A, OWNER_A)
        with pytest.raises(DBAPIError):
            connection.execute(text("insert into agent_meeting_sessions (id, tenant_id, workspace_id, created_by, title, room_id) "
                                    "values ('x1', :t, :w, 'u', 'T', 'other-room')"), {"t": TENANT_A, "w": WORKSPACE_A})
        tx.rollback()
    # The row of another workspace never shows through the list filter either.
    seed_room(database_engine, TENANT_A, WORKSPACE_A, "meet-1")
    run_sql(database_engine, "insert into agent_meeting_sessions (id, tenant_id, workspace_id, created_by, title, room_id) values ('b1', :t, :w, 'u', 'B', NULL)",
            t=TENANT_B, w=WORKSPACE_B)
    assert hq.get(MEETINGS, headers=OWNER(), params={"room_id": "meet-1"}).json()["items"] == []


def test_deleting_a_room_clears_only_the_link_and_keeps_the_meeting(hq: TestClient, database_engine: Engine) -> None:
    seed_room(database_engine, TENANT_A, WORKSPACE_A, "meet-1")
    meeting = create(hq, room_id="meet-1").json()
    run_sql(database_engine, "delete from office_rooms where id = 'meet-1' and tenant_id = :t", t=TENANT_A)
    after = hq.get(f"{MEETINGS}/{meeting['id']}", headers=OWNER()).json()
    assert after["id"] == meeting["id"] and after["room_id"] is None and after["title"] == "Weekly sync"


def test_starter_campus_fills_only_an_empty_or_custom_department_of_its_own_rooms(hq: TestClient, database_engine: Engine) -> None:
    def preview(**query):
        r = hq.get(f"{CAMPUS}/preview", params=query, headers=OWNER())
        assert r.status_code == 200, r.text
        return r.json()

    def confirm(view):
        return hq.post(f"{CAMPUS}/confirm", headers=OWNER(), json={
            "payload_hash": view["payload_hash"], "input_mode": "click", "buildings": view["selected"], "timezone": view["timezone"]})

    first = preview()
    assert first["totals"]["update"] == {"room_departments": 0}
    assert confirm(first).status_code == 200
    depts = dict(one(database_engine, "select id, team_department from office_rooms where tenant_id = :t", t=TENANT_A))
    assert depts["campus-02-commerce-marketing-studio"] == "marketing" and depts["campus-04-corporate-services-legal-office"] == "legal"
    assert depts["campus-04-corporate-services-compliance-office"] == "compliance" and depts["campus-01-hq-security-operations"] == "security"
    assert depts["campus-05-knowledge-ai-development"] == "technology" and depts["campus-05-knowledge-ai-research-room"] == "research"
    assert depts["campus-01-hq-facilities"] is None, "no matching department: left empty"

    # Rooms from an earlier run (empty or custom) and one the owner set on purpose.
    run_sql(database_engine, "update office_rooms set team_department = null, revision = revision + 1, name = 'My studio' where id = 'campus-02-commerce-marketing-studio'")
    run_sql(database_engine, "update office_rooms set team_department = 'custom' where id = 'campus-04-corporate-services-legal-office'")
    run_sql(database_engine, "update office_rooms set team_department = 'sales' where id = 'campus-04-corporate-services-hr'")
    run_sql(database_engine, "update office_rooms set team_department = null where id = 'campus-01-hq-facilities'")
    seed_member(database_engine, TENANT_B, WORKSPACE_B, "owner_b", "owner")
    again = preview()
    assert again["totals"]["update"] == {"room_departments": 2} and again["totals"]["create"] == {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0}
    assert again["rules"]["fills_empty_departments"] is True
    result = confirm(again)
    assert result.status_code == 200 and result.json()["outcome"] == "created" and result.json()["departments_set"] == 2, result.text
    depts = dict(one(database_engine, "select id, team_department from office_rooms where tenant_id = :t", t=TENANT_A))
    assert depts["campus-02-commerce-marketing-studio"] == "marketing" and depts["campus-04-corporate-services-legal-office"] == "legal"
    assert depts["campus-04-corporate-services-hr"] == "sales", "a department the owner chose is kept"
    assert depts["campus-01-hq-facilities"] is None
    assert one(database_engine, "select name from office_rooms where id = 'campus-02-commerce-marketing-studio'") == [("My studio",)], "nothing else changes"
    audit = one(database_engine, "select metadata->'after'->'departments_set' from audit_records where action = 'office.starter_campus.building' and tenant_id = :t", t=TENANT_A)
    flat = sorted((item for row in audit if row[0] for item in row[0]), key=lambda x: x["id"])
    assert flat == [{"id": "campus-02-commerce-marketing-studio", "from": None, "to": "marketing"},
                    {"id": "campus-04-corporate-services-legal-office", "from": "custom", "to": "legal"}]
    # Idempotent: nothing left to do, and a second confirm writes nothing.
    done = preview()
    assert done["totals"]["update"] == {"room_departments": 0}
    assert confirm(done).json()["outcome"] == "already_set_up"


def test_a_room_linked_to_a_new_department_gets_a_least_privilege_paused_agent_proposal(hq: TestClient, database_engine: Engine) -> None:
    seed_room(database_engine, TENANT_A, WORKSPACE_A, "mkt", "office", "marketing")
    base = "/api/v1/office/provisioning"
    gaps = hq.get(f"{base}/gaps", headers=OWNER()).json()
    assert "marketing_specialist" in {g["template_id"] for g in gaps["gaps"]}
    view = hq.get(f"{base}/proposals/marketing_specialist", headers=OWNER(), params={"lang": "en"}).json()
    agent = view["content"]["agent"]
    assert agent["department"] == "marketing" and view["content"]["initial_status"] == "paused"
    assert agent["autonomy"]["external"] == "off" and agent["allowed_tools"]
    assert set(agent["allowed_skills"]) <= {"anum.brevo", "anum.document-drafting"}
    assert view["content"]["budget"]["monthly_cost_limit_usd"] == 6.0
    assert view["template"]["limited"] is True and any(m["kind"] == "capability" for m in view["template"]["missing"])
    arabic = hq.get(f"{base}/proposals/marketing_specialist", headers=OWNER(), params={"lang": "ar"}).json()
    assert arabic["content"]["agent"]["name"] == "أخصائي التسويق"
    done = hq.post(f"{base}/proposals/marketing_specialist/confirm", headers=OWNER(), json={
        "payload_hash": view["payload_hash"], "input_mode": "click", "lang": "en"})
    assert done.status_code == 200, done.text
    assert done.json()["paused"] is True and done.json()["agent"]["department"] == "marketing"
    rows = one(database_engine, "select department, status from agent_profiles where tenant_id = :t", t=TENANT_A)
    assert rows == [("marketing", "paused")]
    covered = hq.get(f"{base}/gaps", headers=OWNER()).json()
    assert "marketing_specialist" not in {g["template_id"] for g in covered["gaps"]}
