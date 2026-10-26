"""Pure rules of the office builder: overlap, bounds, free spots, catalogue drift."""
import pytest
from pydantic import ValidationError

from anum_api import office_layout as layout
from anum_api.office_space_api import (
    BuildingUpdate, DeskUpdate, FloorLayout, FloorUpdate, RoomCopy, RoomUpdate,
)
from anum_api.team_models import Department


def R(rid, x, z, w, d):
    return layout.Rect(rid, x, z, w, d)


def test_touching_rooms_do_not_overlap_but_intersecting_rooms_do():
    assert not layout.overlaps(R("a", 0, 0, 4, 4), R("b", 4, 0, 4, 4))
    assert layout.overlaps(R("a", 0, 0, 4, 4), R("b", 3.5, 0, 4, 4))


def test_swap_is_valid_as_a_final_state():
    final = [R("a", 6, 0, 4, 4), R("b", 0, 0, 6, 4)]
    assert layout.validate_layout(final, {"a", "b"}, (20, 10)) == []


def test_bounds_and_overlap_report_every_problem():
    final = [R("a", 0, 0, 5, 5), R("b", 4, 0, 5, 5), R("c", 18, 0, 5, 5)]
    codes = sorted((i.code, i.location) for i in layout.validate_layout(final, {"a", "b", "c"}, (20, 10)))
    assert codes == [("ROOM_OUT_OF_BOUNDS", "rooms.c"), ("ROOM_OVERLAP", "rooms.a")]


def test_old_overlap_between_untouched_rooms_does_not_block():
    final = [R("a", 0, 0, 5, 5), R("b", 4, 0, 5, 5), R("c", 20, 0, 2, 2)]
    assert layout.validate_layout(final, {"c"}, None) == []


def test_desk_position_is_relative_to_room():
    room = R("a", 10, 10, 4, 3)
    assert layout.point_in_room(room, 4, 3)
    assert not layout.point_in_room(room, 4.1, 1)
    assert not layout.point_in_room(room, -0.1, 1)


def test_free_spot_prefers_beside_then_scans_and_can_fail():
    placed = [R("a", 0, 0, 10, 10)]
    assert layout.find_free_spot(placed, 5, 5, (40, 20), placed[0]) == (10, 0)
    assert layout.find_free_spot([R("a", 0, 0, 10, 10)], 10, 10, (10, 10), None) is None
    spot = layout.find_free_spot([R("a", 0, 0, 10, 10)], 5, 5, (20, 12), None)
    assert spot is not None and not layout.overlaps(R("n", *spot, 5, 5), placed[0])


def test_templates_are_consistent_with_catalogues():
    assert {t.id for t in layout.TEMPLATES} == set(layout.TEMPLATE_IDS)
    for t in layout.TEMPLATES:
        assert t.kind in layout.ROOM_KINDS and t.color in layout.ROOM_COLORS
        assert t.door_side in layout.DOOR_SIDES
        assert all(k in layout.EQUIPMENT_KINDS and q >= 1 for k, q in t.equipment)
        assert t.desks <= t.capacity
        assert t.desks == 0 or t.kind in layout.DESK_ROOM_KINDS
        assert all(spot is not None for spot in layout.desk_grid(t.width, t.depth, t.desks))


def test_team_departments_match_the_team_model():
    assert set(layout.TEAM_DEPARTMENTS) == {d.value for d in Department}


def test_literals_match_catalogues():
    from typing import get_args
    from anum_api import office_space_api as api
    assert get_args(api.EquipmentKind) == layout.EQUIPMENT_KINDS
    assert get_args(api.RoomColor) == layout.ROOM_COLORS
    assert get_args(api.RoomKind) == layout.ROOM_KINDS
    assert get_args(api.TeamDepartment) == layout.TEAM_DEPARTMENTS
    assert get_args(api.DoorSide) == layout.DOOR_SIDES
    assert get_args(api.Rotation) == layout.ROTATIONS
    assert get_args(api.TemplateId) == layout.TEMPLATE_IDS


@pytest.mark.parametrize("model,payload", [
    (RoomUpdate, {"expected_revision": 1}),
    (RoomUpdate, {"expected_revision": 1, "rotation": 45}),
    (RoomUpdate, {"expected_revision": 1, "kind": None}),
    (RoomUpdate, {"expected_revision": 1, "color": "pink"}),
    (RoomUpdate, {"expected_revision": 1, "team_department": "astrology"}),
    (RoomUpdate, {"expected_revision": 1, "notes": "x" * 2001}),
    (RoomUpdate, {"expected_revision": 1, "name": "   "}),
    (RoomUpdate, {"expected_revision": 1, "notes": "bad\x00text"}),
    (RoomUpdate, {"expected_revision": 1, "equipment": [{"kind": "monitor", "quantity": 1}, {"kind": "monitor", "quantity": 2}]}),
    (RoomUpdate, {"expected_revision": 1, "equipment": [{"kind": "jet_engine", "quantity": 1}]}),
    (RoomUpdate, {"expected_revision": 1, "tenant_id": "other"}),
    (BuildingUpdate, {"expected_revision": 1, "timezone": "Not/AZone"}),
    (BuildingUpdate, {"expected_revision": 1, "name": None}),
    (FloorUpdate, {"expected_revision": 1, "width_m": 50}),
    (FloorUpdate, {"expected_revision": 1, "width_m": 5000, "depth_m": 5}),
    (DeskUpdate, {"expected_revision": 1, "pos_x": 1}),
    (DeskUpdate, {"expected_revision": 1, "pos_x": 1, "pos_z": None}),
    (DeskUpdate, {"expected_revision": 1, "pos_x": -1, "pos_z": 1}),
    (FloorLayout, {}),
    (FloorLayout, {"rooms": [{"id": "a", "expected_revision": 1, "x": 0, "z": 0, "width": 2, "depth": 2},
                              {"id": "a", "expected_revision": 1, "x": 3, "z": 0, "width": 2, "depth": 2}]}),
    (FloorLayout, {"rooms": [{"id": "a", "expected_revision": 1, "x": 0, "z": 0, "width": 2, "depth": 2, "rotation": 30}]}),
    (RoomCopy, {"id": "c", "x": 1}),
])
def test_invalid_builder_bodies_are_rejected(model, payload):
    with pytest.raises(ValidationError):
        model(**payload)


def test_valid_patches_keep_only_what_was_sent():
    body = RoomUpdate(expected_revision=2, name="  Ops   room ", color=None, equipment=[
        {"kind": "monitor", "quantity": 2, "note": " dual "}])
    assert body.changes() == {"name": "Ops room", "color": None,
                              "equipment": [{"kind": "monitor", "quantity": 2, "note": "dual"}]}
    assert DeskUpdate(expected_revision=1, pos_x=None, pos_z=None).changes() == {"pos_x": None, "pos_z": None}
    assert FloorUpdate(expected_revision=1, width_m=30, depth_m=20).changes() == {"width_m": 30, "depth_m": 20}


def test_command_room_kind_has_a_template_and_desks() -> None:
    assert "command" in layout.ROOM_KINDS and "command" in layout.DESK_ROOM_KINDS
    template = layout.template_by_id("command_center")
    assert template is not None and template.kind == "command" and template.desks == 6
