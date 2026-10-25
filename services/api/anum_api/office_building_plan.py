"""Pure planning for "add a building" requests (docs/ANUM_MINE_EXECUTIVE_PA.md#add-a-building).

No database and no web framework. ``plan_building`` turns a name and a floor count into exactly what the
builder API would create: the building, its floors and one or two template rooms per floor, with ids and
placements. The plan is a pure function of the request, the caller and the names already in use, so it can be
shown to the owner, hashed, and recomputed at confirm time. Nothing here stores or approves anything.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Final, Iterable, Literal

from . import office_layout as layout

MAX_FLOORS: Final = 20
MAX_NAME: Final = 100
DEFAULT_TIMEZONE: Final = "Asia/Dubai"
FLOOR_W: Final = layout.DEFAULT_FLOOR_WIDTH_M
FLOOR_D: Final = layout.DEFAULT_FLOOR_DEPTH_M
GROUND_ROOMS: Final = ("reception", "open_office")
UPPER_ROOMS: Final = ("open_office", "meeting_room")


# Fixed, client safe messages by stable code. Nothing from an exception object is ever sent to a client.
MESSAGES: Final = {
    "name_required": "A building needs a name.",
    "floors_required": "How many floors?",
    "name_too_long": f"The name can have at most {MAX_NAME} characters.",
    "floors_invalid": f"A building has 1 to {MAX_FLOORS} floors.",
    "name_exists": "A building with this name already exists.",
    "no_space": "The template rooms do not fit the floor.",
    "no_template": "A room template is missing.",
}


def message_for(code: str) -> str:
    return MESSAGES.get(code, "This building cannot be planned.")


class PlanError(ValueError):
    """The request cannot become a plan. ``code`` is stable for clients."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def clean_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(value: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()).strip("-")[:32]
    if ascii_part:
        return ascii_part
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def floor_name(level: int, lang: Literal["en", "ar"]) -> str:
    if lang == "ar":
        return "الطابق الأرضي" if level == 0 else f"الطابق {level}"
    return "Ground floor" if level == 0 else f"Floor {level}"


def plan_building(
    name: str | None, floors: int | None, lang: Literal["en", "ar"],
    existing_ids: Iterable[str], existing_names: Iterable[str], timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    cleaned = clean_name(name)
    if not cleaned:
        raise PlanError("name_required", "A building needs a name.")
    if len(cleaned) > MAX_NAME:
        raise PlanError("name_too_long", f"The name can have at most {MAX_NAME} characters.")
    if floors is None:
        raise PlanError("floors_required", "How many floors?")
    if not isinstance(floors, int) or isinstance(floors, bool) or floors < 1 or floors > MAX_FLOORS:
        raise PlanError("floors_invalid", f"A building has 1 to {MAX_FLOORS} floors.")
    if cleaned.casefold() in {clean_name(n).casefold() for n in existing_names}:
        raise PlanError("name_exists", "A building with this name already exists.")
    taken = set(existing_ids)
    base = f"bld-{slug(cleaned)}"
    bid, n = base, 1
    while bid in taken:
        n += 1
        bid = f"{base}-{n}"
    plan_floors = []
    for level in range(floors):
        fid = f"{bid}-f{level}"
        placed: list[layout.Rect] = []
        rooms = []
        for template_id in GROUND_ROOMS if level == 0 else UPPER_ROOMS:
            t = layout.template_by_id(template_id)
            if t is None:  # pragma: no cover - fixed catalogue, covered by a test
                raise PlanError("no_template", "A room template is missing.")
            spot = layout.find_free_spot(placed, t.width, t.depth, (FLOOR_W, FLOOR_D), None)
            if spot is None:  # pragma: no cover - two small rooms always fit a 60 by 40 floor
                raise PlanError("no_space", "The template rooms do not fit the floor.")
            placed.append(layout.Rect(template_id, spot[0], spot[1], t.width, t.depth))
            rooms.append({"id": f"{fid}-{template_id.replace('_', '-')}", "template_id": template_id,
                          "name": t.name, "name_ar": t.name_ar, "kind": t.kind, "capacity": t.capacity,
                          "x": spot[0], "z": spot[1], "width": t.width, "depth": t.depth})
        plan_floors.append({"id": fid, "level": level, "name": floor_name(level, lang),
                            "width_m": FLOOR_W, "depth_m": FLOOR_D, "rooms": rooms})
    return {"lang": lang, "building": {"id": bid, "name": cleaned, "timezone": timezone},
            "floors": plan_floors,
            "totals": {"buildings": 1, "floors": floors, "rooms": sum(len(f["rooms"]) for f in plan_floors), "desks": 0, "agents": 0}}


def plan_hash(tenant_id: str, workspace_id: str, user_id: str, content: dict[str, Any]) -> str:
    canonical = json.dumps({"t": tenant_id, "w": workspace_id, "u": user_id, "tool": "add_building", "content": content},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
