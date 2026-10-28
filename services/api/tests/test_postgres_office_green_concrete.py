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


def test_the_owners_look_choices_are_part_of_the_hash_and_stored_for_new_agents_only(hq: TestClient, database_engine: Engine) -> None:
    looks = {"raed": "masculine", "mai": "feminine"}
    view = preview(hq, default_look="neutral", look=[f"{k}:{v}" for k, v in looks.items()])
    assert {p["key"]: p["appearance"] for p in view["people"]}["raed"] == "masculine"
    assert {p["key"]: p["appearance"] for p in view["people"]}["hazrat"] == "neutral"
    body = {"payload_hash": view["payload_hash"], "input_mode": "click", "timezone": view["timezone"]}
    # Looks that were not the ones shown: the hash no longer matches and nothing is created.
    assert hq.post(f"{BASE}/confirm", headers=OWNER(), json={**body, "default_look": "neutral", "appearance": {"raed": "feminine", "mai": "feminine"}}).status_code == 409
    assert hq.post(f"{BASE}/confirm", headers=OWNER(), json=body).status_code == 409
    assert counts(database_engine) == (0, 0, 0, 0)
    # Unknown people and styles are refused, never guessed.
    assert hq.post(f"{BASE}/confirm", headers=OWNER(), json={**body, "appearance": {"nobody": "neutral"}}).status_code == 422
    assert hq.post(f"{BASE}/confirm", headers=OWNER(), json={**body, "appearance": {"raed": "tall"}}).status_code == 422
    assert hq.get(f"{BASE}/preview", params={"look": ["raed:tall"]}, headers=OWNER()).status_code == 422
    r = hq.post(f"{BASE}/confirm", headers=OWNER(), json={**body, "default_look": "neutral", "appearance": looks})
    assert r.status_code == 200, r.text
    stored = dict(rows(database_engine, "select name, avatar::text from agent_profiles where tenant_id = :t and template_id like 'gc:%'", t=TENANT_A))
    assert stored["Raed"] == '{"avatar_style": "masculine"}' and stored["Mai"] == '{"avatar_style": "feminine"}'
    assert all(v == '{"avatar_style": "neutral"}' for k, v in stored.items() if k not in ("Raed", "Mai")), "everyone else is neutral until the owner chooses"
    audit = rows(database_engine, "select metadata from audit_records where action = 'office.green_concrete.add' and tenant_id = :t", t=TENANT_A)
    assert audit[0][0]["after"]["looks_chosen"] == 10
    assert not any(n in str(audit[0][0]) for n in ("Raed", "Mai", "masculine", "feminine")), "ids and counts only"
    # A second run never changes the looks of agents that exist, whatever is sent.
    again = preview(hq, default_look="feminine")
    assert all(p["appearance"] is None for p in again["people"])


LH = "/api/v1/office-layout"
FLOOR = gc.FLOOR_ID
SEAT_COLS = "id, label, pos_x, pos_z, shift, seat_order, facing, seat_kind, table_group"


def seat_rows(engine: Engine, room: str = gc.ROOM_ID) -> list[tuple]:
    return rows(engine, f"select {SEAT_COLS} from office_desks where tenant_id = :t and workspace_id = :w and room_id = :r order by id",
                t=TENANT_A, w=WORKSPACE_A, r=room)


def hours(engine: Engine) -> tuple:
    return rows(engine, "select shift_day_start, shift_night_start from office_buildings where tenant_id = :t and workspace_id = :w and id = :b",
                t=TENANT_A, w=WORKSPACE_A, b=gc.BUILDING_ID)[0]


def item(hq: TestClient, kind: str, item_id: str) -> dict:
    r = hq.get(f"{SP}/{kind}/{item_id}", headers=OWNER())
    assert r.status_code == 200, r.text
    return r.json()


def patch(hq: TestClient, kind: str, item_id: str, **changes) -> dict:
    r = hq.patch(f"{SP}/{kind}/{item_id}", headers=OWNER(), json={"expected_revision": item(hq, kind, item_id)["revision"], **changes})
    assert r.status_code == 200, r.text
    return r.json()


def head(hq: TestClient) -> int:
    r = hq.get(f"{LH}/floors/{FLOOR}/revisions", headers=OWNER())
    assert r.status_code == 200, r.text
    return r.json()["head_revision"]


def test_restore_brings_green_concrete_seats_shifts_and_hours_back_exactly(hq: TestClient, database_engine: Engine) -> None:
    assert confirm(hq, preview(hq)).status_code == 200
    patch(hq, "buildings", gc.BUILDING_ID, shift_day_start="06:30", shift_night_start="18:30")
    # Any layout change after the hours were saved writes a revision that holds them with every seat.
    patch(hq, "desks", gc.desk_id(10), label="Manager desk")
    saved_no, saved_seats, saved_hours = head(hq), seat_rows(database_engine), hours(database_engine)
    assert saved_hours == ("06:30", "18:30") and len(saved_seats) == 10
    # Change seats, the L side, a shift, the hours, and remove a seat.
    patch(hq, "desks", gc.desk_id(1), shift="night", seat_order=9, facing="south", table_group="elsewhere")
    patch(hq, "desks", gc.desk_id(5), facing="west", seat_kind="manager")
    patch(hq, "buildings", gc.BUILDING_ID, shift_day_start="08:00", shift_night_start="20:00")
    six = item(hq, "desks", gc.desk_id(6))
    assert hq.patch(f"{SP}/desks/{gc.desk_id(6)}/assignment", headers=OWNER(), json={"agent_id": None, "expected_revision": six["revision"]}).status_code == 200
    assert hq.delete(f"{SP}/desks/{gc.desk_id(6)}", params={"expected_revision": item(hq, "desks", gc.desk_id(6))["revision"]}, headers=OWNER()).status_code == 204
    assert seat_rows(database_engine) != saved_seats and hours(database_engine) == ("08:00", "20:00")

    current = head(hq)
    detail = hq.get(f"{LH}/floors/{FLOOR}/revisions/{saved_no}?against=current", headers=OWNER()).json()
    assert detail["matches_current"] is False
    summary = detail["diff"]["summary"]
    assert summary["seats_changed"] == 2 and summary["shift_hours_changed"] is True and summary["desks_added"] == 1
    r = hq.post(f"{LH}/floors/{FLOOR}/revisions/{saved_no}/restore", headers=OWNER(), json={
        "payload_hash": detail["payload_hash"], "expected_head": current, "confirm_unassign": [], "input_mode": "click"})
    assert r.status_code == 200, r.text
    assert r.json()["restored"] is True
    assert seat_rows(database_engine) == saved_seats, "every seat field and position is back"
    assert hours(database_engine) == ("06:30", "18:30")
    # The restored desk comes back empty: a restore never seats an agent.
    assert rows(database_engine, "select agent_id from office_desks where tenant_id = :t and workspace_id = :w and id = :d",
                t=TENANT_A, w=WORKSPACE_A, d=gc.desk_id(6)) == [(None,)]
    again = hq.get(f"{LH}/floors/{FLOOR}/revisions/{saved_no}?against=current", headers=OWNER()).json()
    assert again["matches_current"] is True
    audit = rows(database_engine, "select metadata from audit_records where tenant_id = :t and action = 'office.floor.restore'", t=TENANT_A)
    assert len(audit) == 1 and "Raed" not in str(audit[0][0])


def test_a_version_1_revision_restores_its_fields_and_leaves_seats_alone(hq: TestClient, database_engine: Engine) -> None:
    assert confirm(hq, preview(hq)).status_code == 200
    patch(hq, "desks", gc.desk_id(10), label="Back desk")
    v1_no = head(hq)
    # Turn the newest revision into the shape the previous release wrote (no seat fields, no hours).
    run_sql(database_engine, "alter table office_floor_revisions disable trigger user")
    try:
        from anum_api import office_layout_history as lh
        snap = rows(database_engine, "select snapshot from office_floor_revisions where tenant_id = :t and floor_id = :f and revision_no = :n",
                    t=TENANT_A, f=FLOOR, n=v1_no)[0][0]
        old = {"v": 1, "floor": snap["floor"], "rooms": snap["rooms"], "desks": [{k: d[k] for k in lh.DESK_FIELDS_V1} for d in snap["desks"]]}
        import json
        run_sql(database_engine, "update office_floor_revisions set snapshot = cast(:s as jsonb), snapshot_hash = :h where tenant_id = :t and floor_id = :f and revision_no = :n",
                s=json.dumps(old), h=lh.snapshot_hash(old), t=TENANT_A, f=FLOOR, n=v1_no)
    finally:
        run_sql(database_engine, "alter table office_floor_revisions enable trigger user")
    patch(hq, "desks", gc.desk_id(10), label="Manager desk")
    patch(hq, "desks", gc.desk_id(2), facing="east")
    detail = hq.get(f"{LH}/floors/{FLOOR}/revisions/{v1_no}?against=current", headers=OWNER()).json()
    r = hq.post(f"{LH}/floors/{FLOOR}/revisions/{v1_no}/restore", headers=OWNER(), json={
        "payload_hash": detail["payload_hash"], "expected_head": head(hq), "confirm_unassign": [], "input_mode": "click"})
    assert r.status_code == 200, r.text
    by_id = {s[0]: s for s in seat_rows(database_engine)}
    assert by_id[gc.desk_id(10)][1] == "Back desk", "the version 1 fields are restored"
    assert by_id[gc.desk_id(2)][6] == "east", "seat fields it never recorded are left as they are"


def test_copying_the_command_center_keeps_every_seat_and_no_agent(hq: TestClient, database_engine: Engine) -> None:
    assert confirm(hq, preview(hq)).status_code == 200
    r = hq.post(f"{SP}/floors", headers=OWNER(), json={"id": "gc-upper", "building_id": gc.BUILDING_ID, "level": 1, "name": "Upper", "width_m": 30, "depth_m": 20})
    assert r.status_code == 201, r.text
    r = hq.post(f"{SP}/rooms/{gc.ROOM_ID}/copy", headers=OWNER(), json={"id": "gc-copy", "floor_id": "gc-upper", "x": 1, "z": 1, "copy_desks": True})
    assert r.status_code == 201, r.text
    source = [s[1:] for s in seat_rows(database_engine)]
    copied = [s[1:] for s in seat_rows(database_engine, "gc-copy")]
    assert sorted(copied, key=str) == sorted(source, key=str) and len(copied) == 10
    assert rows(database_engine, "select count(*) from office_desks where tenant_id = :t and room_id = 'gc-copy' and agent_id is not null", t=TENANT_A) == [(0,)]


def test_campus_counts_people_of_the_other_shift_as_off_shift(hq: TestClient, database_engine: Engine) -> None:
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    assert confirm(hq, preview(hq)).status_code == 200
    local = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Dubai"))
    start, end = (local - timedelta(hours=1)).strftime("%H:%M"), (local + timedelta(hours=1)).strftime("%H:%M")
    patch(hq, "buildings", gc.BUILDING_ID, timezone="Asia/Dubai", shift_day_start=start, shift_night_start=end)  # the day shift is on now
    body = hq.get("/api/v1/office/campus", headers=OWNER()).json()
    a = next(b for b in body["buildings"] if b["workspace_id"] == WORKSPACE_A)
    assert a["agents"]["total"] == 10 and a["agents"]["off_shift"] == 3
    assert sum(a["agents"]["by_state"].values()) == 7, "the night people are not counted by a work state"
    patch(hq, "buildings", gc.BUILDING_ID, shift_day_start=end, shift_night_start=start)  # now the night shift is on
    a = next(b for b in hq.get("/api/v1/office/campus", headers=OWNER()).json()["buildings"] if b["workspace_id"] == WORKSPACE_A)
    assert a["agents"]["off_shift"] == 6 and sum(a["agents"]["by_state"].values()) == 4
