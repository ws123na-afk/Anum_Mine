"""Pure geometry and catalogue rules for the ANUM MINE office builder.

No database and no web framework here, so the same rules can be tested with plain
vectors and mirrored by the web editor. Units are metres. A floor is a rectangle
whose origin is its north west corner: x grows east and z grows south, so a room
fits when ``0 <= x`` and ``x + width <= floor width`` (same for z and depth).
A room's footprint (x, z, width, depth) is always axis aligned and is what overlap
and bounds use. ``rotation`` and ``door_side`` describe the room's interior only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

EPS: Final = 1e-6
DEFAULT_FLOOR_WIDTH_M: Final = 60.0
DEFAULT_FLOOR_DEPTH_M: Final = 40.0
MAX_FLOOR_SIDE_M: Final = 1000.0
# Search area for a free spot on a floor that has no saved bounds (legacy floors).
UNBOUNDED_SEARCH_M: Final = 200.0

ROTATIONS: Final = (0, 90, 180, 270)
DOOR_SIDES: Final = ("north", "south", "east", "west")
# "command" is the operations command centre: a work room with operator desks whose module is the live flow and
# whose 3D interior carries the wall of live dashboards. It is a kind of its own, not an office named by words.
ROOM_KINDS: Final = ("office", "meeting", "reception", "workshop", "warehouse", "lounge", "command")
DESK_ROOM_KINDS: Final = ("office", "workshop", "warehouse", "command")
# Fixed palette names; the web maps each name to a design token.
ROOM_COLORS: Final = ("slate", "forest", "brass", "sapphire", "amethyst", "crimson", "teal", "sand")
# Fixed equipment catalogue (owner decision): kinds are not free text, notes are.
EQUIPMENT_KINDS: Final = (
    "monitor", "display", "whiteboard", "projector", "conference_phone", "video_conference_kit",
    "printer", "server_rack", "network_switch", "workbench", "shelving", "locker",
    "charging_station", "first_aid_kit",
)
# The existing team concept: anum_api.team_models.Department (kept in sync by a test).
TEAM_DEPARTMENTS: Final = ("sales", "support", "operations", "finance", "tyre_engineering", "marketing", "technology", "research", "legal", "compliance", "security", "hr", "procurement", "custom")

# Error codes carried in the error envelope's details[].type.
ROOM_OVERLAP: Final = "ROOM_OVERLAP"
ROOM_OUT_OF_BOUNDS: Final = "ROOM_OUT_OF_BOUNDS"
DESK_OUT_OF_ROOM: Final = "DESK_OUT_OF_ROOM"
ROOM_NOT_PLACED: Final = "ROOM_NOT_PLACED"
ROOM_NOT_ON_FLOOR: Final = "ROOM_NOT_ON_FLOOR"
DESK_NOT_ON_FLOOR: Final = "DESK_NOT_ON_FLOOR"
STALE_REVISION: Final = "STALE_REVISION"
NO_FREE_SPACE: Final = "NO_FREE_SPACE"


@dataclass(frozen=True, slots=True)
class Rect:
    id: str
    x: float
    z: float
    width: float
    depth: float


@dataclass(frozen=True, slots=True)
class LayoutIssue:
    code: str
    location: str
    message: str


def overlaps(a: Rect, b: Rect) -> bool:
    """True when the interiors intersect; rooms that only share an edge do not overlap."""
    return (
        min(a.x + a.width, b.x + b.width) - max(a.x, b.x) > EPS
        and min(a.z + a.depth, b.z + b.depth) - max(a.z, b.z) > EPS
    )


def within_bounds(rect: Rect, floor_width: float, floor_depth: float) -> bool:
    return (
        rect.x >= -EPS and rect.z >= -EPS
        and rect.x + rect.width <= floor_width + EPS
        and rect.z + rect.depth <= floor_depth + EPS
    )


def validate_layout(
    rects: list[Rect],
    changed_ids: set[str],
    bounds: tuple[float, float] | None,
) -> list[LayoutIssue]:
    """Every problem in the final layout that involves a room being saved.

    Untouched rooms are only checked against the rooms that changed, so an old overlap
    between two rooms nobody is editing never blocks an unrelated save.
    """
    issues: list[LayoutIssue] = []
    for rect in rects:
        if rect.id in changed_ids and bounds is not None and not within_bounds(rect, *bounds):
            issues.append(LayoutIssue(
                ROOM_OUT_OF_BOUNDS, f"rooms.{rect.id}",
                f"Room {rect.id} must stay inside the floor ({bounds[0]:g} by {bounds[1]:g} metres)",
            ))
    ordered = sorted(rects, key=lambda r: r.id)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if first.id not in changed_ids and second.id not in changed_ids:
                continue
            if overlaps(first, second):
                issues.append(LayoutIssue(
                    ROOM_OVERLAP, f"rooms.{first.id}",
                    f"Room {first.id} overlaps room {second.id}",
                ))
    return issues


def point_in_room(room: Rect, pos_x: float, pos_z: float) -> bool:
    """Desk positions are relative to the room's north west corner."""
    return -EPS <= pos_x <= room.width + EPS and -EPS <= pos_z <= room.depth + EPS


def find_free_spot(
    placed: list[Rect],
    width: float,
    depth: float,
    bounds: tuple[float, float] | None,
    near: Rect | None = None,
) -> tuple[float, float] | None:
    """A free spot for a new room: beside the source, below it, then a coarse scan."""
    limit_w, limit_d = bounds if bounds is not None else (UNBOUNDED_SEARCH_M, UNBOUNDED_SEARCH_M)

    def free(x: float, z: float) -> bool:
        candidate = Rect("new", x, z, width, depth)
        if not within_bounds(candidate, limit_w, limit_d):
            return False
        return not any(overlaps(candidate, other) for other in placed)

    if near is not None:
        for x, z in ((near.x + near.width, near.z), (near.x, near.z + near.depth)):
            if free(x, z):
                return (x, z)
    step = max(1.0, math.ceil(max(limit_w, limit_d) / 100))
    z = 0.0
    while z + depth <= limit_d + EPS:
        x = 0.0
        while x + width <= limit_w + EPS:
            if free(x, z):
                return (x, z)
            x += step
        z += step
    return None


@dataclass(frozen=True, slots=True)
class RoomTemplate:
    id: str
    name: str
    name_ar: str
    kind: str
    capacity: int
    width: float
    depth: float
    door_side: str
    color: str
    equipment: tuple[tuple[str, int], ...]
    desks: int


# A small fixed set of starting points, server side data. Sizes are metres.
TEMPLATES: Final[tuple[RoomTemplate, ...]] = (
    RoomTemplate("open_office", "Open office", "مكتب مفتوح", "office", 12, 12.0, 8.0, "south", "forest",
                 (("monitor", 12), ("printer", 1), ("whiteboard", 1)), 12),
    RoomTemplate("meeting_room", "Meeting room", "غرفة اجتماعات", "meeting", 8, 6.0, 5.0, "south", "sapphire",
                 (("display", 1), ("whiteboard", 1), ("conference_phone", 1), ("video_conference_kit", 1)), 0),
    RoomTemplate("reception", "Reception", "الاستقبال", "reception", 3, 6.0, 4.0, "south", "brass",
                 (("display", 1),), 0),
    RoomTemplate("warehouse", "Warehouse", "مستودع", "warehouse", 8, 16.0, 12.0, "south", "slate",
                 (("shelving", 10), ("locker", 4), ("first_aid_kit", 1)), 4),
    RoomTemplate("workshop", "Workshop", "ورشة عمل", "workshop", 6, 10.0, 8.0, "east", "sand",
                 (("workbench", 4), ("shelving", 2), ("first_aid_kit", 1)), 4),
    RoomTemplate("command_center", "Command centre", "مركز القيادة", "command", 10, 14.0, 10.0, "south", "crimson",
                 (("monitor", 6), ("display", 4), ("network_switch", 1)), 6),
)
TEMPLATE_IDS: Final = tuple(template.id for template in TEMPLATES)


def template_by_id(template_id: str) -> RoomTemplate | None:
    return next((template for template in TEMPLATES if template.id == template_id), None)


def desk_grid(room_width: float, room_depth: float, count: int) -> list[tuple[float, float] | None]:
    """Positions for generated desks on a 1.5 metre grid; None when one does not fit."""
    columns = max(1, int((room_width - 1.0) // 1.5) + 1)
    spots: list[tuple[float, float] | None] = []
    for index in range(count):
        px = 0.75 + (index % columns) * 1.5
        pz = 0.75 + (index // columns) * 1.5
        spots.append((px, pz) if px <= room_width and pz <= room_depth else None)
    return spots
