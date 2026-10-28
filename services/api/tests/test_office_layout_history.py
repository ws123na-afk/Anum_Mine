"""Pure rules of the layout history: snapshot, hash, diff, restore plan, payload binding, migration shape."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from anum_api import office_layout_history as h

ROOT = Path(__file__).resolve().parents[1]


def room(rid: str, x: float = 0, z: float = 0, **extra: object) -> dict:
    base = {"id": rid, "name": rid.title(), "kind": "office", "capacity": 4, "scene_x": x, "scene_z": z, "scene_width": 5,
            "scene_depth": 5, "rotation": 0, "door_side": "south", "description": None, "notes": None, "color": None,
            "name_ar": None, "equipment": [], "team_department": None, "revision": 7, "agent_id": "never_stored"}
    return {**base, **extra}


def desk(did: str, rid: str, x: float = 1, z: float = 1, **extra: object) -> dict:
    return {"id": did, "room_id": rid, "label": did, "pos_x": x, "pos_z": z, "agent_id": "a1", "revision": 3, **extra}


FLOOR = {"width_m": 60.0, "depth_m": 40.0}


def snap(rooms: list[dict], desks: list[dict]) -> dict:
    return h.build_snapshot(FLOOR, rooms, desks)


def test_snapshot_has_no_agents_or_counters_and_hash_ignores_order() -> None:
    a = snap([room("b"), room("a", 10)], [desk("d2", "a"), desk("d1", "a")])
    b = snap([room("a", 10), room("b")], [desk("d1", "a"), desk("d2", "a")])
    assert h.snapshot_hash(a) == h.snapshot_hash(b)
    text = str(a)
    assert "never_stored" not in text and "a1" not in text and "revision" not in text
    assert h.snapshot_hash(a) != h.snapshot_hash(snap([room("a", 11), room("b")], [desk("d1", "a"), desk("d2", "a")]))


def test_diff_names_every_kind_of_change() -> None:
    old = snap([room("a"), room("b", 10), room("c", 20), room("d", 30)], [desk("d1", "a"), desk("d2", "a")])
    new = snap([room("a"), room("b", 12), room("c", 20, color="teal"), room("e", 40)], [desk("d1", "a", 2, 2), desk("d3", "e")])
    diff = h.diff_snapshots(old, new)
    status = {r["id"]: r["status"] for r in diff["rooms"]}
    assert status == {"a": "same", "b": "moved", "c": "changed", "d": "removed", "e": "added"}
    s = diff["summary"]
    assert (s["rooms_added"], s["rooms_removed"], s["rooms_moved"], s["rooms_changed"]) == (1, 1, 1, 1)
    assert (s["desks_added"], s["desks_removed"], s["desks_moved"]) == (1, 1, 1)
    assert h.diff_snapshots(None, new)["summary"]["rooms_added"] == 0


def test_restore_plan_lists_only_what_differs() -> None:
    cur = snap([room("a"), room("b", 10)], [desk("d1", "a"), desk("d2", "b")])
    tgt = snap([room("a", 3), room("c", 20)], [desk("d1", "a"), desk("d9", "c")])
    plan = h.restore_plan(cur, tgt)
    assert plan["room_deletes"] == ["b"] and plan["desk_deletes"] == ["d2"]
    assert [r["id"] for r in plan["room_inserts"]] == ["c"] and [d["id"] for d in plan["desk_inserts"]] == ["d9"]
    assert plan["room_updates"] == [("a", {"scene_x": 3})] and plan["desk_updates"] == []
    assert h.restore_plan(cur, cur) == {"desk_deletes": [], "room_deletes": [], "room_inserts": [], "desk_inserts": [],
                                        "room_updates": [], "desk_updates": []}


def test_payload_hash_is_bound_to_person_place_and_revision() -> None:
    base = h.restore_payload_hash("t", "w", "u", "f", 3, "x" * 64)
    assert base != h.restore_payload_hash("t", "w", "other", "f", 3, "x" * 64)
    assert base != h.restore_payload_hash("t", "w2", "u", "f", 3, "x" * 64)
    assert base != h.restore_payload_hash("t", "w", "u", "f", 4, "x" * 64)
    assert base != h.restore_payload_hash("t", "w", "u", "f", 3, "y" * 64)


def test_assignment_never_writes_a_revision() -> None:
    assert "office.desk.assignment" not in h.LAYOUT_ACTIONS
    assert {"office.floor.layout", "office.room.update", "office.desk.delete"} <= h.LAYOUT_ACTIONS


def test_migration_is_expand_only_with_short_id() -> None:
    spec = importlib.util.spec_from_file_location("m0112", ROOT / "migrations" / "versions" / "0112_layout_history.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert len(module.revision) <= 32 and module.down_revision == "0111_meeting_summary_links"
    source = (ROOT / "migrations" / "versions" / "0112_layout_history.py").read_text(encoding="utf-8")
    assert "drop_column" not in source and "alter table office_" not in source


SEAT = {"shift": "day", "seat_order": 2, "facing": "west", "seat_kind": "team", "table_group": "l-table"}
HOURS = {"shift_day_start": "06:30", "shift_night_start": "18:30"}


def test_snapshot_v2_keeps_seat_fields_and_shift_hours() -> None:
    a = h.build_snapshot(FLOOR, [room("a")], [desk("d1", "a", **SEAT)], HOURS)
    assert a["v"] == 2 and a["shifts"] == {"shift_day_start": "06:30", "shift_night_start": "18:30"}
    assert {k: a["desks"][0][k] for k in SEAT} == SEAT
    night = h.build_snapshot(FLOOR, [room("a")], [desk("d1", "a", **{**SEAT, "shift": "night"})], HOURS)
    other_hours = h.build_snapshot(FLOOR, [room("a")], [desk("d1", "a", **SEAT)], {**HOURS, "shift_day_start": "07:00"})
    assert len({h.snapshot_hash(a), h.snapshot_hash(night), h.snapshot_hash(other_hours)}) == 3
    s = h.diff_snapshots(a, night)["summary"]
    assert (s["seats_changed"], s["desks_moved"], s["shift_hours_changed"]) == (1, 0, False)
    assert h.diff_snapshots(a, other_hours)["summary"]["shift_hours_changed"] is True


def test_restore_plan_brings_seats_and_hours_back() -> None:
    target = h.build_snapshot(FLOOR, [room("a")], [desk("d1", "a", **SEAT), desk("d2", "a", 3, 3, **{**SEAT, "seat_order": 5, "facing": "east"})], HOURS)
    current = h.build_snapshot(FLOOR, [room("a")], [desk("d1", "a", shift=None, seat_order=None, facing=None, seat_kind=None, table_group=None)],
                               {"shift_day_start": None, "shift_night_start": None})
    plan = h.restore_plan(current, target)
    assert plan["desk_updates"] == [("d1", SEAT)]
    assert [(d["id"], d["facing"], d["seat_order"]) for d in plan["desk_inserts"]] == [("d2", "east", 5)]
    assert plan["shift_hours"] == HOURS


def test_version_1_revision_leaves_seats_and_hours_alone() -> None:
    v1 = {"v": 1, "floor": FLOOR, "rooms": [{f: room("a").get(f) for f in h.ROOM_FIELDS}],
          "desks": [{f: desk("d1", "a").get(f) for f in h.DESK_FIELDS_V1}]}
    now = h.build_snapshot(FLOOR, [room("a")], [desk("d1", "a", **SEAT)], HOURS)
    assert h.snapshot_hash(h.as_version_of(now, v1)) == h.snapshot_hash(v1)
    plan = h.restore_plan(now, v1)
    assert plan["desk_updates"] == [] and "shift_hours" not in plan
    assert h.as_version_of(now, now) is now


def test_migration_0115_is_the_seat_schema_the_snapshot_reads() -> None:
    spec = importlib.util.spec_from_file_location("m0115", ROOT / "migrations" / "versions" / "0115_desk_seats_shifts.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import inspect
    source = inspect.getsource(module.upgrade)
    assert all(f'"{f}"' in source for f in (*h.SEAT_FIELDS, *h.SHIFT_FIELDS))
