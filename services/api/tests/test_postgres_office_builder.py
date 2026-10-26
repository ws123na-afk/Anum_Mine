"""Office builder against PostgreSQL under the non-owner app role (pytest -m database).

Covers edit and delete of buildings and floors, room details, desk positions, the
atomic layout save (swap, overlap, bounds, stale revision, nothing partial), copy,
templates, audit before and after values, the layout event, the team link and
tenant isolation of every new write.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
client = TestClient(app)
API = "/api/v1/office-spaces"


@pytest.fixture
def db(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield


def H(tenant: str = TENANT_A, workspace: str = WORKSPACE_A, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": "owner_test", "x-user-roles": role}


def post(path: str, body: dict, headers: dict | None = None, ok: int = 201) -> dict:
    r = client.post(API + path, headers=headers or H(), json=body)
    assert r.status_code == ok, r.text
    return r.json()


def patch(path: str, body: dict, headers: dict | None = None, ok: int = 200) -> dict:
    r = client.patch(API + path, headers=headers or H(), json=body)
    assert r.status_code == ok, r.text
    return r.json()


def get(path: str, headers: dict | None = None) -> dict:
    r = client.get(API + path, headers=headers or H())
    assert r.status_code == 200, r.text
    return r.json()


def types(response) -> list[str]:
    return [d["type"] for d in response.json()["error"]["details"]]


def setup_floor(headers: dict | None = None, width: float | None = None, depth: float | None = None) -> None:
    post("/buildings", {"id": "hq", "name": "HQ", "timezone": "Asia/Riyadh"}, headers)
    body = {"id": "g", "building_id": "hq", "level": 0, "name": "Ground"}
    if width:
        body.update(width_m=width, depth_m=depth)
    post("/floors", body, headers)


def room(rid: str, kind: str = "office", capacity: int = 4, headers: dict | None = None) -> dict:
    return post("/rooms", {"id": rid, "floor_id": "g", "name": rid.upper(), "kind": kind, "capacity": capacity}, headers)


def place(rid: str, x: float, z: float, w: float = 4, d: float = 4) -> dict:
    current = get(f"/rooms/{rid}")
    return patch(f"/rooms/{rid}/geometry", {"expected_revision": current["revision"], "x": x, "z": z, "width": w, "depth": d})


def rows(engine: Engine, sql: str, **params):
    with engine.begin() as connection:
        return connection.execute(text(sql), params).mappings().all()


def test_floor_defaults_and_edit_with_bounds_guard(db: None) -> None:
    setup_floor()
    floor = get("/floors/g")
    assert (floor["width_m"], floor["depth_m"]) == (60, 40)
    room("a")
    place("a", 50, 30)
    too_small = client.patch(f"{API}/floors/g", headers=H(), json={"expected_revision": 1, "width_m": 30, "depth_m": 20})
    assert too_small.status_code == 422, too_small.text
    assert types(too_small) == ["ROOM_OUT_OF_BOUNDS"]
    assert get("/floors/g")["revision"] == 1
    edited = patch("/floors/g", {"expected_revision": 1, "name": "Ground floor", "width_m": 80, "depth_m": 50})
    assert (edited["name"], edited["width_m"], edited["revision"]) == ("Ground floor", 80, 2)
    stale = client.patch(f"{API}/floors/g", headers=H(), json={"expected_revision": 1, "name": "x"})
    assert stale.status_code == 409 and types(stale) == ["STALE_REVISION"]
    post("/floors", {"id": "f1", "building_id": "hq", "level": 1, "name": "First"})
    taken = client.patch(f"{API}/floors/f1", headers=H(), json={"expected_revision": 1, "level": 0})
    assert taken.status_code == 409 and types(taken) == ["FLOOR_LEVEL_TAKEN"]


def test_building_edit_and_guarded_deletes(db: None) -> None:
    setup_floor()
    b = patch("/buildings/hq", {"expected_revision": 1, "name": "Head office", "latitude": None})
    assert b["name"] == "Head office"
    room("a")
    post("/desks", {"id": "d1", "room_id": "a", "label": "D1"})
    blocked = client.delete(f"{API}/buildings/hq?expected_revision=2", headers=H())
    assert blocked.status_code == 409 and types(blocked) == ["FLOOR_PRESENT"]
    assert blocked.json()["error"]["details"][0]["message"].startswith("Floor Ground")
    floor_blocked = client.delete(f"{API}/floors/g?expected_revision=1", headers=H())
    assert floor_blocked.status_code == 409
    assert "Room A (1 desk(s))" in floor_blocked.json()["error"]["details"][0]["message"]
    room_blocked = client.delete(f"{API}/rooms/a?expected_revision=1", headers=H())
    assert room_blocked.status_code == 409
    assert room_blocked.json()["error"]["details"][0]["type"] == "DESK_PRESENT"
    assert client.delete(f"{API}/desks/d1?expected_revision=1", headers=H()).status_code == 204
    assert client.delete(f"{API}/rooms/a?expected_revision=1", headers=H()).status_code == 204
    assert client.delete(f"{API}/floors/g?expected_revision=0", headers=H()).status_code == 422
    assert client.delete(f"{API}/floors/g?expected_revision=9", headers=H()).status_code == 409
    assert client.delete(f"{API}/floors/g?expected_revision=1", headers=H()).status_code == 204
    assert client.delete(f"{API}/buildings/hq?expected_revision=2", headers=H()).status_code == 204
    assert client.get(f"{API}/buildings/hq", headers=H()).status_code == 404


def test_room_details_kind_capacity_and_team(db: None, database_engine: Engine) -> None:
    setup_floor()
    room("a", capacity=3)
    for n in (1, 2):
        post("/desks", {"id": f"d{n}", "room_id": "a", "label": f"D{n}"})
    detail = patch("/rooms/a", {
        "expected_revision": 1, "description": "Ops team", "notes": "Window seat\nNear exit", "color": "forest",
        "name_ar": "العمليات", "rotation": 90, "door_side": "east", "team_department": "operations",
        "equipment": [{"kind": "monitor", "quantity": 2, "note": "27 inch"}, {"kind": "whiteboard", "quantity": 1}],
    })
    assert (detail["rotation"], detail["door_side"], detail["color"], detail["name_ar"]) == (90, "east", "forest", "العمليات")
    assert detail["equipment"][0] == {"kind": "monitor", "quantity": 2, "note": "27 inch"}
    below = client.patch(f"{API}/rooms/a", headers=H(), json={"expected_revision": 2, "capacity": 1})
    assert below.status_code == 409 and types(below) == ["CAPACITY_BELOW_DESKS"]
    forbid = client.patch(f"{API}/rooms/a", headers=H(), json={"expected_revision": 2, "kind": "meeting"})
    assert forbid.status_code == 409 and types(forbid) == ["KIND_FORBIDS_DESKS"]
    cleared = patch("/rooms/a", {"expected_revision": 2, "color": None, "name_ar": None, "team_department": None})
    assert cleared["color"] is None and cleared["team_department"] is None
    workshop = patch("/rooms/a", {"expected_revision": 3, "capacity": 10, "kind": "workshop"})
    assert workshop["kind"] == "workshop"
    # The command centre kind is stored (migration 0113 widened the check) and keeps its operator desks.
    command = patch("/rooms/a", {"expected_revision": workshop["revision"], "kind": "command"})
    assert command["kind"] == "command"
    assert post("/desks", {"id": "d3", "room_id": "a", "label": "D3"})["room_id"] == "a"
    bad = client.patch(f"{API}/rooms/a", headers=H(), json={"expected_revision": command["revision"], "kind": "stadium"})
    assert bad.status_code == 422
    room("b")
    dup = client.patch(f"{API}/rooms/b", headers=H(), json={"expected_revision": 1, "name": "A"})
    assert dup.status_code == 409 and types(dup) == ["ROOM_NAME_TAKEN"]
    # Team link: real counts only; none linked means no counts.
    assert get("/rooms/b/team") == {"room_id": "b", "department": None, "member_profiles": None, "assistants": None}
    patch("/rooms/b", {"expected_revision": 1, "team_department": "sales"})
    assert get("/rooms/b/team")["member_profiles"] == 0
    with database_engine.begin() as connection:
        connection.execute(text("""
            insert into team_member_profiles (tenant_id, workspace_id, user_id, display_name, department, timezone)
            values (:t, :w, 'person_one', 'Person One', 'sales', 'UTC'), (:t, :w, 'person_two', 'Person Two', 'finance', 'UTC')
        """), {"t": TENANT_A, "w": WORKSPACE_A})
    summary = get("/rooms/b/team")
    assert (summary["department"], summary["member_profiles"], summary["assistants"]) == ("sales", 1, 0)


def test_desk_rename_and_position(db: None) -> None:
    setup_floor()
    room("a")
    post("/desks", {"id": "d1", "room_id": "a", "label": "D1"})
    unplaced = client.patch(f"{API}/desks/d1", headers=H(), json={"expected_revision": 1, "pos_x": 1, "pos_z": 1})
    assert unplaced.status_code == 422 and types(unplaced) == ["ROOM_NOT_PLACED"]
    place("a", 2, 2, 6, 4)
    moved = patch("/desks/d1", {"expected_revision": 1, "label": "Front desk", "pos_x": 1.5, "pos_z": 2})
    assert (moved["label"], moved["pos_x"], moved["pos_z"]) == ("Front desk", 1.5, 2)
    outside = client.patch(f"{API}/desks/d1", headers=H(), json={"expected_revision": 2, "pos_x": 6.5, "pos_z": 1})
    assert outside.status_code == 422 and types(outside) == ["DESK_OUT_OF_ROOM"]
    shrink = client.patch(f"{API}/rooms/a/geometry", headers=H(),
                          json={"expected_revision": get("/rooms/a")["revision"], "x": 2, "z": 2, "width": 1.2, "depth": 4})
    assert shrink.status_code == 422 and types(shrink) == ["DESK_OUT_OF_ROOM"]
    assert patch("/desks/d1", {"expected_revision": 2, "pos_x": None, "pos_z": None})["pos_x"] is None
    post("/desks", {"id": "d2", "room_id": "a", "label": "D2"})
    same = client.patch(f"{API}/desks/d2", headers=H(), json={"expected_revision": 1, "label": "Front desk"})
    assert same.status_code == 409 and types(same) == ["DESK_LABEL_TAKEN"]


def test_atomic_layout_swap_and_nothing_partial(db: None, database_engine: Engine) -> None:
    setup_floor(width=20, depth=10)
    for rid in ("a", "b", "c"):
        room(rid)
    place("a", 0, 0, 6, 4)
    place("b", 6, 0, 4, 4)
    place("c", 0, 6, 4, 4)
    revisions = {r: get(f"/rooms/{r}")["revision"] for r in "abc"}

    def rm(rid, x, z, w, d, **extra):
        return {"id": rid, "expected_revision": revisions[rid], "x": x, "z": z, "width": w, "depth": d, **extra}

    swap = client.put(f"{API}/floors/g/layout", headers=H(), json={"rooms": [
        rm("a", 4, 0, 6, 4, rotation=90, door_side="north"), rm("b", 0, 0, 4, 4)]})
    assert swap.status_code == 200, swap.text
    by_id = {r["id"]: r for r in swap.json()["rooms"]}
    assert (by_id["a"]["scene_x"], by_id["b"]["scene_x"], by_id["a"]["rotation"], by_id["a"]["door_side"]) == (4, 0, 90, "north")
    assert by_id["a"]["revision"] == revisions["a"] + 1
    assert get("/rooms/c")["revision"] == revisions["c"]
    revisions = {r: get(f"/rooms/{r}")["revision"] for r in "abc"}
    before = {r: get(f"/rooms/{r}") for r in "abc"}

    def unchanged() -> bool:
        return {r: get(f"/rooms/{r}") for r in "abc"} == before

    overlap = client.put(f"{API}/floors/g/layout", headers=H(), json={"rooms": [
        rm("a", 15, 0, 3, 3), rm("c", 14, 1, 4, 4)]})
    assert overlap.status_code == 422 and types(overlap) == ["ROOM_OVERLAP"], overlap.text
    assert unchanged()
    outside = client.put(f"{API}/floors/g/layout", headers=H(), json={"rooms": [rm("a", 0, 0, 4, 4), rm("c", 18, 8, 4, 4)]})
    assert outside.status_code == 422 and "ROOM_OUT_OF_BOUNDS" in types(outside)
    assert unchanged()
    stale = rm("c", 10, 6, 2, 2)
    stale["expected_revision"] = 99
    conflict = client.put(f"{API}/floors/g/layout", headers=H(), json={"rooms": [rm("a", 11, 6, 2, 2), stale]})
    assert conflict.status_code == 409 and types(conflict) == ["STALE_REVISION"]
    assert unchanged()
    ghost = client.put(f"{API}/floors/g/layout", headers=H(), json={"rooms": [{"id": "zzz", "expected_revision": 1, "x": 0, "z": 0, "width": 1, "depth": 1}]})
    assert ghost.status_code == 422 and types(ghost) == ["ROOM_NOT_ON_FLOOR"]
    # Desks move with the layout and must stay inside their room.
    post("/desks", {"id": "d1", "room_id": "a", "label": "D1"})
    revisions["a"] = get("/rooms/a")["revision"]
    ok = client.put(f"{API}/floors/g/layout", headers=H(), json={
        "rooms": [rm("a", 4, 0, 6, 4)], "desks": [{"id": "d1", "expected_revision": 1, "pos_x": 5.5, "pos_z": 3}]})
    assert ok.status_code == 200, ok.text
    bad_desk = client.put(f"{API}/floors/g/layout", headers=H(), json={
        "rooms": [], "desks": [{"id": "d1", "expected_revision": 2, "pos_x": 6.5, "pos_z": 3}]})
    assert bad_desk.status_code == 422 and types(bad_desk) == ["DESK_OUT_OF_ROOM"]
    revisions["a"] = get("/rooms/a")["revision"]
    shrink = client.put(f"{API}/floors/g/layout", headers=H(), json={"rooms": [rm("a", 4, 0, 3, 4)]})
    assert shrink.status_code == 422 and types(shrink) == ["DESK_OUT_OF_ROOM"]
    # Audit keeps before and after values; the event names the floor and revisions only.
    audit = rows(database_engine, "select metadata_json from (select metadata as metadata_json, created_at from audit_records "
                 "where action = 'office.floor.layout' order by created_at) t")
    first = audit[0]["metadata_json"]
    assert first["before"]["rooms"][0]["scene_x"] == 0 and first["after"]["rooms"][0]["scene_x"] == 4
    events = rows(database_engine, "select subject, payload from domain_events where type = 'office.layout_changed' and tenant_id = :t", t=TENANT_A)
    assert events and events[0]["subject"] in ("g", "hq", "a", "b", "c") and "name" not in events[0]["payload"]


def test_geometry_patch_respects_floor_bounds(db: None) -> None:
    setup_floor(width=10, depth=10)
    room("a")
    current = get("/rooms/a")
    out = client.patch(f"{API}/rooms/a/geometry", headers=H(),
                       json={"expected_revision": current["revision"], "x": 8, "z": 0, "width": 4, "depth": 4})
    assert out.status_code == 422 and types(out) == ["ROOM_OUT_OF_BOUNDS"]


def test_copy_room_and_templates(db: None) -> None:
    setup_floor(width=30, depth=20)
    room("a", capacity=2)
    place("a", 0, 0, 6, 4)
    patch("/rooms/a", {"expected_revision": get("/rooms/a")["revision"], "color": "brass", "notes": "Copy me",
                       "equipment": [{"kind": "printer", "quantity": 1}]})
    post("/desks", {"id": "d1", "room_id": "a", "label": "D1"})
    patch("/desks/d1", {"expected_revision": 1, "pos_x": 1, "pos_z": 1})
    assign = client.patch(f"{API}/desks/d1/assignment", headers=H(), json={"agent_id": None, "expected_revision": 2})
    assert assign.status_code == 200
    copied = post("/rooms/a/copy", {"id": "a2", "copy_desks": True})
    assert copied["room"]["name"] == "A copy" and copied["room"]["color"] == "brass"
    assert copied["room"]["notes"] == "Copy me" and (copied["room"]["scene_x"], copied["room"]["scene_z"]) == (6, 0)
    assert copied["desks"][0]["label"] == "D1" and copied["desks"][0]["agent_id"] is None
    assert copied["desks"][0]["pos_x"] == 1
    again = client.post(f"{API}/rooms/a/copy", headers=H(), json={"id": "a2"})
    assert again.status_code == 409
    explicit = client.post(f"{API}/rooms/a/copy", headers=H(), json={"id": "a3", "name": "Far", "x": 1, "z": 1})
    assert explicit.status_code == 422 and set(types(explicit)) == {"ROOM_OVERLAP"}
    catalogue = get("/templates")
    assert [t["id"] for t in catalogue["items"]] == ["open_office", "meeting_room", "reception", "warehouse", "workshop", "command_center"]
    assert catalogue["units"] == "metres" and "monitor" in catalogue["equipment_kinds"]
    command = post("/rooms/from-template", {"id": "cmd1", "floor_id": "g", "template_id": "command_center", "with_desks": True})
    assert command["room"]["kind"] == "command" and len(command["desks"]) == 6 and command["room"]["color"] == "crimson"
    made = post("/rooms/from-template", {"id": "open1", "floor_id": "g", "template_id": "open_office", "with_desks": True})
    assert made["room"]["kind"] == "office" and len(made["desks"]) == 12
    assert all(d["pos_x"] is not None for d in made["desks"]) and made["room"]["name_ar"] == "مكتب مفتوح"
    meeting = post("/rooms/from-template", {"id": "meet1", "floor_id": "g", "template_id": "meeting_room",
                                            "name": "Board room", "with_desks": True})
    assert meeting["desks"] == [] and meeting["room"]["equipment"][0]["kind"] == "display"
    full = client.post(f"{API}/rooms/from-template", headers=H(),
                       json={"id": "wh", "floor_id": "g", "template_id": "warehouse", "x": 0, "z": 0})
    assert full.status_code == 422 and "ROOM_OVERLAP" in types(full)
    for n in range(6):
        res = client.post(f"{API}/rooms/from-template", headers=H(),
                          json={"id": f"w{n}", "floor_id": "g", "template_id": "warehouse", "name": f"W{n}"})
        if res.status_code != 201:
            assert res.status_code == 409 and types(res) == ["NO_FREE_SPACE"]
            break
    else:
        pytest.fail("a 30 by 20 floor cannot hold six more warehouses")


def test_members_cannot_use_builder_writes_and_tenants_are_isolated(db: None) -> None:
    setup_floor()
    room("a")
    member = H(role="member")
    viewer = H(role="viewer")
    assert client.get(f"{API}/templates", headers=viewer).status_code == 200
    attempts = [
        ("patch", "/buildings/hq", {"expected_revision": 1, "name": "x"}),
        ("patch", "/floors/g", {"expected_revision": 1, "name": "x"}),
        ("patch", "/rooms/a", {"expected_revision": 1, "name": "x"}),
        ("patch", "/desks/d", {"expected_revision": 1, "label": "x"}),
        ("put", "/floors/g/layout", {"rooms": [{"id": "a", "expected_revision": 1, "x": 0, "z": 0, "width": 2, "depth": 2}]}),
        ("post", "/rooms/a/copy", {"id": "z"}),
        ("post", "/rooms/from-template", {"id": "z", "floor_id": "g", "template_id": "reception"}),
    ]
    for method, path, body in attempts:
        assert getattr(client, method)(API + path, headers=member, json=body).status_code == 403, path
    for path in ("/buildings/hq", "/floors/g", "/rooms/a"):
        assert client.delete(f"{API}{path}?expected_revision=1", headers=member).status_code == 403
    other = H(TENANT_B, WORKSPACE_B)
    post("/buildings", {"id": "hq", "name": "Other HQ", "timezone": "Asia/Riyadh"}, other)
    outsider = [
        ("patch", "/floors/g", {"expected_revision": 1, "name": "x"}),
        ("patch", "/rooms/a", {"expected_revision": 1, "name": "x"}),
        ("put", "/floors/g/layout", {"rooms": [{"id": "a", "expected_revision": 1, "x": 0, "z": 0, "width": 2, "depth": 2}]}),
        ("post", "/rooms/a/copy", {"id": "z"}),
        ("post", "/rooms/from-template", {"id": "z", "floor_id": "g", "template_id": "reception"}),
    ]
    for method, path, body in outsider:
        assert getattr(client, method)(API + path, headers=other, json=body).status_code == 404, path
    assert client.get(f"{API}/rooms/a/team", headers=other).status_code == 404
    assert client.delete(f"{API}/floors/g?expected_revision=1", headers=other).status_code == 404
    assert get("/rooms/a")["name"] == "A"
    # Deleting the other tenant's building leaves ours alone.
    assert client.delete(f"{API}/buildings/hq?expected_revision=1", headers=other).status_code == 204
    assert get("/buildings/hq")["name"] == "HQ"
