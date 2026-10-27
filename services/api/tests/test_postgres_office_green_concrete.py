"""Add Green Concrete on PostgreSQL as the non-owner app role with forced RLS (pytest -m database)."""
from __future__ import annotations

import concurrent.futures

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture the hq fixture needs)
from test_postgres_office_hq import EXEC, OWNER_A, headers, hq, run_sql, seed_agent  # noqa: F401 (fixtures)

from anum_api import office_green_concrete as gc

pytestmark = pytest.mark.database
BASE = "/api/v1/office/green-concrete"
SP = "/api/v1/office-spaces"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def rows(engine: Engine, sql: str, **params):
    with engine.begin() as connection:
        return [tuple(r) for r in connection.execute(text(sql), params)]


def preview(hq: TestClient, user=None, **query) -> dict:
    r = hq.get(f"{BASE}/preview", params=query, headers=user or OWNER())
    assert r.status_code == 200, r.text
    return r.json()


def confirm(hq: TestClient, view: dict, *, mode: str = "click", user=None):
    return hq.post(f"{BASE}/confirm", headers=user or OWNER(),
                   json={"payload_hash": view["payload_hash"], "input_mode": mode, "timezone": view["timezone"]})


def counts(engine: Engine, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> tuple:
    p = {"t": tenant, "w": workspace}
    return (
        rows(engine, "select count(*) from office_buildings where tenant_id=:t and workspace_id=:w and id like 'gc-%'", **p)[0][0],
        rows(engine, "select count(*) from office_rooms where tenant_id=:t and workspace_id=:w and id like 'gc-%'", **p)[0][0],
        rows(engine, "select count(*) from office_desks where tenant_id=:t and workspace_id=:w and id like 'gc-%'", **p)[0][0],
        rows(engine, "select count(*) from agent_profiles where tenant_id=:t and workspace_id=:w and template_id like 'gc:%'", **p)[0][0],
    )


def test_preview_lists_everything_and_stores_nothing(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq)
    assert view["counts"]["create"] == {"buildings": 1, "floors": 1, "rooms": 1, "desks": 10, "agents": 10}
    assert view["building"]["name"]["en"] == "Green Concrete" and view["room"]["kind"] == "command"
    assert view["shifts"]["defaults"] is True and view["shifts"]["day"] == {"start": "07:00", "end": "19:00"}
    assert view["rules"]["agents_start_paused"] is True and view["rules"]["attaches_tools"] is False
    assert [p["name"] for p in view["people"]] == ["Raed", "Hazrat", "Mai", "Shahzeb", "Ijaz", "Badea", "Ali Zeehan", "Amjad", "Rana", "Ahmed Elbendari"]
    assert counts(database_engine) == (0, 0, 0, 0)


def test_only_a_click_by_an_owner_with_the_shown_hash_confirms(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq)
    for mode in ("typed", "voice"):
        assert confirm(hq, view, mode=mode).status_code == 403
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert preview(hq, user=viewer)["can_confirm"] is False
    assert confirm(hq, view, user=viewer).status_code == 403
    assert confirm(hq, {**view, "payload_hash": "0" * 64}).status_code == 409
    assert hq.post(f"{BASE}/confirm", headers=OWNER(), json={"payload_hash": view["payload_hash"], "timezone": "Asia/Dubai"}).status_code == 403
    assert counts(database_engine) == (0, 0, 0, 0)


def test_confirm_builds_the_company_seats_paused_agents_and_audits_ids_only(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq)
    r = confirm(hq, view)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "created" and body["agents_started"] == 0
    assert body["created"] == {"buildings": 1, "floors": 1, "rooms": 1, "desks": 10, "agents": 10, "seated": 10}
    assert counts(database_engine) == (1, 1, 10, 10)
    seats = rows(database_engine, """select d.label, d.shift, d.seat_order, d.facing, d.seat_kind, d.table_group, a.name, a.status, a.department, a.avatar
        from office_desks d join agent_profiles a on a.id = d.agent_id and a.tenant_id = d.tenant_id and a.workspace_id = d.workspace_id
        where d.tenant_id = :t and d.workspace_id = :w and d.id like 'gc-%' order by d.id""", t=TENANT_A, w=WORKSPACE_A)
    assert len(seats) == 10
    day = [s for s in seats if s[1] == "day"]
    assert [s[6] for s in day] == ["Raed", "Hazrat", "Mai", "Shahzeb", "Ijaz", "Badea"] and [s[2] for s in day] == [1, 2, 3, 4, 5, 6]
    assert [s[6] for s in seats if s[1] == "night"] == ["Ali Zeehan", "Amjad", "Rana"]
    manager = [s for s in seats if s[1] is None]
    assert manager == [("Manager desk", None, 1, "north", "manager", "back-desk", "Ahmed Elbendari", "paused", "PMV manager", None)]
    assert all(s[7] == "paused" and s[9] is None for s in seats), "paused, neutral avatar (never inferred)"
    tools = rows(database_engine, "select allowed_tools, sharing from agent_profiles where tenant_id = :t and template_id like 'gc:%'", t=TENANT_A)
    assert all(t[0] == [] and t[1] == "workspace" for t in tools)
    audit = rows(database_engine, "select metadata from audit_records where action = 'office.green_concrete.add' and tenant_id = :t", t=TENANT_A)
    assert len(audit) == 1
    dumped = str(audit[0][0])
    assert audit[0][0]["after"]["input_mode"] == "click" and audit[0][0]["after"]["created"]["agents"] == 10
    assert not any(n in dumped for n in ("Raed", "Ahmed", "Hazrat", "Rana")), "ids and counts only, never names"
    # The room is a command room with the L table seats inside it.
    room = rows(database_engine, "select kind, door_side from office_rooms where id = :i and tenant_id = :t", i=gc.ROOM_ID, t=TENANT_A)
    assert room == [("command", "south")]
    # The registry read shows the new fields.
    desks = hq.get(f"{SP}/desks", headers=OWNER()).json()["items"]
    mine = [d for d in desks if d["id"].startswith("gc-")]
    assert len(mine) == 10 and {"shift", "seat_order", "facing", "seat_kind", "table_group"} <= set(mine[0])
    shift = hq.get(f"{SP}/buildings/{gc.BUILDING_ID}", headers=OWNER()).json()
    assert shift["shift_day_start"] is None and shift["shift_night_start"] is None, "defaults are not stored as facts"


def test_second_click_and_a_stale_click_write_nothing(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq)
    assert confirm(hq, view).status_code == 200
    again = confirm(hq, view)
    assert again.status_code == 200 and again.json()["outcome"] == "already_set_up"
    assert counts(database_engine) == (1, 1, 10, 10)
    after = preview(hq)
    assert after["status"] == "complete" and after["counts"]["create"] == {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0, "agents": 0}


def test_two_confirms_at_once_build_it_once(hq: TestClient, database_engine: Engine) -> None:
    view = preview(hq)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: confirm(hq, view).status_code, range(2)))
    assert sorted(results)[0] == 200
    assert counts(database_engine) == (1, 1, 10, 10)


def test_rerun_adds_only_what_is_missing_and_never_overwrites(hq: TestClient, database_engine: Engine) -> None:
    assert confirm(hq, preview(hq)).status_code == 200
    # The owner renames the room, moves a person away from a desk, and a desk disappears.
    h = OWNER()
    room = hq.get(f"{SP}/rooms/{gc.ROOM_ID}", headers=h).json()
    assert hq.patch(f"{SP}/rooms/{gc.ROOM_ID}", headers=h, json={"expected_revision": room["revision"], "name": "Ops Hub"}).status_code == 200
    run_sql(database_engine, "update office_desks set agent_id = null where id = :d and tenant_id = :t", d=gc.desk_id(1), t=TENANT_A)
    view = preview(hq)
    assert view["counts"]["seat"] == 1 and view["counts"]["create"]["agents"] == 0
    assert confirm(hq, view).status_code == 200
    assert rows(database_engine, "select name from office_rooms where id = :i and tenant_id = :t", i=gc.ROOM_ID, t=TENANT_A) == [("Ops Hub",)]
    assert counts(database_engine) == (1, 1, 10, 10)
    assert rows(database_engine, "select count(*) from office_desks where tenant_id = :t and id like 'gc-%' and agent_id is not null", t=TENANT_A)[0][0] == 10


def test_a_desk_taken_by_someone_else_is_never_taken_over(hq: TestClient, database_engine: Engine) -> None:
    assert confirm(hq, preview(hq)).status_code == 200
    run_sql(database_engine, "update office_desks set agent_id = null where id = :d and tenant_id = :t", d=gc.desk_id(2), t=TENANT_A)
    seed_agent(database_engine, TENANT_A, WORKSPACE_A, "someone")
    run_sql(database_engine, "update office_desks set agent_id = :a where id = :d and tenant_id = :t", a="someone", d=gc.desk_id(2), t=TENANT_A)
    view = preview(hq)
    person = next(p for p in view["people"] if p["key"] == "hazrat")
    assert person["seat_action"] == "skip" and person["seat_reason"] == "desk_taken"


def test_a_company_with_the_same_name_blocks_a_second_one(hq: TestClient, database_engine: Engine) -> None:
    h = OWNER()
    assert hq.post(f"{SP}/buildings", headers=h, json={"id": "mine", "name": "green concrete", "timezone": "Asia/Dubai"}).status_code == 201
    view = preview(hq)
    assert view["building"]["action"] == "skip" and view["building"]["reason"] == "name_in_use"
    assert view["counts"]["create"]["buildings"] == 0 and view["counts"]["create"]["rooms"] == 0


def test_shift_hours_and_seat_fields_are_validated_and_editable(hq: TestClient, database_engine: Engine) -> None:
    assert confirm(hq, preview(hq)).status_code == 200
    h = OWNER()
    b = hq.get(f"{SP}/buildings/{gc.BUILDING_ID}", headers=h).json()
    ok = hq.patch(f"{SP}/buildings/{gc.BUILDING_ID}", headers=h, json={"expected_revision": b["revision"], "shift_day_start": "06:30", "shift_night_start": "18:30"})
    assert ok.status_code == 200 and ok.json()["shift_day_start"] == "06:30"
    for bad in ({"shift_day_start": "7am"}, {"shift_day_start": "25:00"}, {"shift_day_start": "08:00", "shift_night_start": "08:00"}):
        assert hq.patch(f"{SP}/buildings/{gc.BUILDING_ID}", headers=h, json={"expected_revision": ok.json()["revision"], **bad}).status_code == 422
    d = hq.get(f"{SP}/desks/{gc.desk_id(1)}", headers=h).json()
    moved = hq.patch(f"{SP}/desks/{gc.desk_id(1)}", headers=h, json={"expected_revision": d["revision"], "shift": "night", "seat_order": 2, "facing": "west"})
    assert moved.status_code == 200 and moved.json()["shift"] == "night" and moved.json()["facing"] == "west"
    for bad in ({"shift": "evening"}, {"seat_order": 0}, {"facing": "up"}, {"seat_kind": "boss"}):
        assert hq.patch(f"{SP}/desks/{gc.desk_id(1)}", headers=h, json={"expected_revision": moved.json()["revision"], **bad}).status_code == 422
    viewer = headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")
    assert hq.patch(f"{SP}/desks/{gc.desk_id(1)}", headers=viewer, json={"expected_revision": moved.json()["revision"], "shift": "day"}).status_code == 403
    # Database constraints hold even for a direct write.
    with pytest.raises(Exception):  # noqa: B017, PT011
        run_sql(database_engine, "update office_desks set shift = 'dusk' where id = :d and tenant_id = :t", d=gc.desk_id(1), t=TENANT_A)


def test_another_workspace_neither_counts_nor_sees_it(hq: TestClient, database_engine: Engine) -> None:
    assert confirm(hq, preview(hq)).status_code == 200
    other = headers(TENANT_B, WORKSPACE_B, EXEC)
    view = hq.get(f"{BASE}/preview", headers=other)
    assert view.status_code == 200, view.text
    assert view.json()["counts"]["create"]["buildings"] == 1, "the other workspace sees nothing of it"
    assert counts(database_engine, TENANT_B, WORKSPACE_B) == (0, 0, 0, 0)
