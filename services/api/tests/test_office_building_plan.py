"""Pure planning for "add a building": ids, floors, template rooms, hash, refusals."""
from __future__ import annotations

import pytest

from anum_api import office_building_plan as bp
from anum_api import office_layout as layout


def test_three_floors_with_template_rooms_inside_bounds() -> None:
    plan = bp.plan_building("Tyre Pulse", 3, "en", [], [])
    assert plan["building"] == {"id": "bld-tyre-pulse", "name": "Tyre Pulse", "timezone": "Asia/Dubai"}
    assert [f["name"] for f in plan["floors"]] == ["Ground floor", "Floor 1", "Floor 2"]
    assert plan["totals"] == {"buildings": 1, "floors": 3, "rooms": 6, "desks": 0, "agents": 0}
    assert [r["template_id"] for r in plan["floors"][0]["rooms"]] == ["reception", "open_office"]
    assert [r["template_id"] for r in plan["floors"][2]["rooms"]] == ["open_office", "meeting_room"]
    for floor in plan["floors"]:
        rects = [layout.Rect(r["id"], r["x"], r["z"], r["width"], r["depth"]) for r in floor["rooms"]]
        assert layout.validate_layout(rects, {r.id for r in rects}, (60.0, 40.0)) == []
    ids = [plan["building"]["id"], *[f["id"] for f in plan["floors"]], *[r["id"] for f in plan["floors"] for r in f["rooms"]]]
    assert len(ids) == len(set(ids)) and all(len(i) <= 80 for i in ids)


def test_arabic_floor_names_and_non_ascii_building_name() -> None:
    plan = bp.plan_building("برج المحرك", 2, "ar", [], [])
    assert [f["name"] for f in plan["floors"]] == ["الطابق الأرضي", "الطابق 1"]
    assert plan["building"]["id"].startswith("bld-") and plan["building"]["name"] == "برج المحرك"


@pytest.mark.parametrize(("name", "floors", "code"), [
    (None, 3, "name_required"), ("  ", 3, "name_required"), ("X", None, "floors_required"),
    ("X", 0, "floors_invalid"), ("X", 21, "floors_invalid"), ("X" * 101, 2, "name_too_long"),
])
def test_missing_or_invalid_input_has_a_stable_code(name, floors, code) -> None:
    with pytest.raises(bp.PlanError) as err:
        bp.plan_building(name, floors, "en", [], [])
    assert err.value.code == code


def test_existing_name_is_refused_and_existing_id_is_not_reused() -> None:
    with pytest.raises(bp.PlanError) as err:
        bp.plan_building("tyre pulse", 1, "en", ["bld-tyre-pulse"], ["Tyre Pulse"])
    assert err.value.code == "name_exists"
    plan = bp.plan_building("Tyre-Pulse", 1, "en", ["bld-tyre-pulse"], ["Other"])
    assert plan["building"]["id"] == "bld-tyre-pulse-2"


def test_hash_binds_user_workspace_and_content() -> None:
    plan = bp.plan_building("A", 1, "en", [], [])
    base = bp.plan_hash("t", "w", "u", plan)
    assert base != bp.plan_hash("t", "w", "other", plan) and base != bp.plan_hash("t", "w2", "u", plan)
    assert base != bp.plan_hash("t", "w", "u", bp.plan_building("A", 2, "en", [], []))
    assert base == bp.plan_hash("t", "w", "u", bp.plan_building("A", 1, "en", [], []))
