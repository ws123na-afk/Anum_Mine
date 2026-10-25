"""Starter campus: the owner's master list of buildings, floors, rooms and desks, and the pure planner.

Data and rules only, no database and no web framework (docs/ANUM_MINE_STARTER_CAMPUS.md).

* The master list is data: every name has an English and an Arabic form, every record has a stable key and
  a stable record id derived from it (``campus-01-hq``, ``campus-01-hq-l0``, ``campus-01-hq-reception``,
  ``campus-01-hq-reception-d1``). Matching existing records by those ids is what makes a re-run add only
  what is missing and never touch what the owner edited.
* The planner takes the records that already exist in ONE workspace and returns exactly what a confirm
  would create and what it would skip, with the reason. It uses the builder's own geometry rules
  (``office_layout``): floor bounds, no overlap, desks inside the room, desks only in work areas.
* Buildings are logical departments inside the current workspace. Nothing here reads or writes another
  workspace, creates an agent, or invents presence: new rooms are empty.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from . import office_layout as layout

CATALOG_VERSION: Final = 1
DEFAULT_TIMEZONE: Final = "Asia/Dubai"
ID_PREFIX: Final = "campus-"
FLOOR_MARGIN_M: Final = 2.0
ROOM_GAP_M: Final = 2.0
DEFAULT_WIDTH_M: Final = 36.0

# Why a record is skipped (stable codes the web turns into words).
EXISTS: Final = "exists"
NAME_IN_USE: Final = "name_in_use"
LEVEL_IN_USE: Final = "level_in_use"
FLOOR_UNAVAILABLE: Final = "floor_unavailable"
NO_SPACE: Final = "no_space"


@dataclass(frozen=True, slots=True)
class RoomSpec:
    key: str
    en: str
    ar: str
    kind: str
    width: float
    depth: float
    capacity: int
    desks: int = 0
    department: str | None = None
    color: str = "slate"
    door_side: str = "south"
    equipment: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class FloorSpec:
    level: int
    en: str
    ar: str
    rooms: tuple[RoomSpec, ...]
    width: float = DEFAULT_WIDTH_M


@dataclass(frozen=True, slots=True)
class BuildingSpec:
    number: int
    key: str
    en: str
    ar: str
    floors: tuple[FloorSpec, ...]

    @property
    def id(self) -> str:
        return f"{ID_PREFIX}{self.number:02d}-{self.key}"


_OFFICE = (("monitor", 6), ("whiteboard", 1))
_MEETING = (("display", 1), ("whiteboard", 1), ("conference_phone", 1))
_WORKSHOP = (("workbench", 4), ("shelving", 2), ("first_aid_kit", 1))
_WAREHOUSE = (("shelving", 10), ("locker", 4), ("first_aid_kit", 1))
_RECEPTION = (("display", 1),)
_LOUNGE = (("display", 1),)


def _office(key: str, en: str, ar: str, w: float, d: float, desks: int, dept: str | None = None,
            color: str = "forest", door: str = "south", equip: tuple[tuple[str, int], ...] | None = None) -> RoomSpec:
    return RoomSpec(key, en, ar, "office", w, d, max(desks + 2, 4), desks, dept, color, door,
                    equip if equip is not None else (("monitor", max(desks, 1)), ("whiteboard", 1)))


def _meeting(key: str, en: str, ar: str, w: float, d: float, seats: int, color: str = "sapphire", door: str = "south") -> RoomSpec:
    return RoomSpec(key, en, ar, "meeting", w, d, seats, 0, None, color, door, _MEETING)


def _work(kind: str, key: str, en: str, ar: str, w: float, d: float, desks: int, dept: str | None,
          color: str, door: str = "south") -> RoomSpec:
    return RoomSpec(key, en, ar, kind, w, d, max(desks + 4, 8), desks, dept, color, door,
                    _WORKSHOP if kind == "workshop" else _WAREHOUSE)


# The owner's master list. The top level is the six business buildings of the approved campus render (ANUM HQ,
# ANUM 360 Commerce, Tyre Pulse, Corporate Services, Knowledge & AI, New Ventures) plus two small ones (Personal
# Office, Career & Employment). The departments of the master list are floors and rooms inside the right building.
# A room carries a team department only where the office team concept has a matching one (sales, support,
# operations, finance, tyre_engineering, marketing, technology, research, legal, compliance, security, hr,
# procurement); that link opens the room's module and lets the Organisation gaps panel propose an agent for an
# empty department. Rooms with no matching department (Facilities, Command Center, AI Labs, Data...) stay unset.
def _floor(level: int, en: str, ar: str, rooms: tuple[RoomSpec, ...], width: float = DEFAULT_WIDTH_M) -> FloorSpec:
    return FloorSpec(level, en, ar, rooms, width)


CAMPUS: Final[tuple[BuildingSpec, ...]] = (
    BuildingSpec(1, "hq", "ANUM HQ", "مقر ANUM", (
        _floor(-1, "Basement: Facilities, Parking, Security", "القبو: المرافق والمواقف والأمن", (
            _office("facilities", "Facilities Office", "مكتب المرافق", 10, 8, 3, None, "slate"),
            _office("security-operations", "Security Operations", "عمليات الأمن", 12, 8, 5, "security", "crimson", "south",
                    (("monitor", 5), ("display", 3), ("network_switch", 1))),
            _work("warehouse", "parking-logistics", "Parking and Logistics", "المواقف والخدمات اللوجستية", 14, 10, 2, None, "slate"),
        ), 38.0),
        _floor(1, "Floor 1: Reception and Meeting Rooms", "الطابق 1: الاستقبال وغرف الاجتماعات", (
            RoomSpec("reception", "Reception", "الاستقبال", "reception", 10, 6, 4, 0, None, "brass", "south", _RECEPTION),
            _meeting("meeting-a", "Meeting Room A", "غرفة الاجتماعات أ", 8, 6, 8),
            _meeting("meeting-b", "Meeting Room B", "غرفة الاجتماعات ب", 8, 6, 8),
            _meeting("meeting-c", "Meeting Room C", "غرفة الاجتماعات ج", 10, 7, 12),
        ), 38.0),
        _floor(2, "Floor 2: Operations and Management", "الطابق 2: العمليات والإدارة", (
            _office("command-center", "Command Center", "مركز القيادة", 14, 10, 6, None, "crimson", "south",
                    (("monitor", 6), ("display", 4), ("network_switch", 1))),
            _office("ai-organization", "AI Organization", "المؤسسة الذكية", 16, 10, 8, None, "amethyst", "south",
                    (("monitor", 8), ("display", 2), ("server_rack", 1))),
            _office("operations-office", "Operations Office", "مكتب العمليات", 10, 8, 4, "operations", "teal"),
            _office("assets", "Assets", "الأصول", 10, 8, 4, "operations", "teal"),
            _office("suppliers", "Suppliers", "الموردون", 10, 8, 4, "operations", "teal"),
        ), 38.0),
        _floor(3, "Floor 3: Executive and Strategy", "الطابق 3: التنفيذي والاستراتيجية", (
            _office("executive-office", "Executive Office", "المكتب التنفيذي", 14, 9, 3, None, "brass", "south",
                    (("monitor", 3), ("display", 1), ("whiteboard", 1))),
            _meeting("boardroom", "Boardroom", "قاعة مجلس الإدارة", 16, 9, 16, "brass"),
            _meeting("strategy-room", "Strategy Room", "غرفة الاستراتيجية", 10, 7, 8, "sapphire"),
        ), 38.0),
        _floor(4, "Rooftop: Terrace and Events", "السطح: التراس والفعاليات", (
            RoomSpec("terrace", "Terrace", "التراس", "lounge", 12, 8, 20, 0, None, "sand", "south", _LOUNGE),
            RoomSpec("events-hall", "Events Hall", "قاعة الفعاليات", "lounge", 14, 9, 40, 0, None, "brass", "south", _LOUNGE),
        ), 38.0),
    )),
    BuildingSpec(2, "commerce", "ANUM 360 Commerce", "ANUM 360 التجارة", (
        _floor(0, "Ground floor: E-commerce, Retail and Marketplace", "الطابق الأرضي: التجارة الإلكترونية والتجزئة والسوق", (
            _office("ecommerce", "E-commerce", "التجارة الإلكترونية", 12, 8, 6, "sales", "forest"),
            _office("retail", "Retail", "التجزئة", 12, 8, 6, "sales", "forest"),
            _office("marketplace", "Marketplace", "السوق", 12, 8, 5, "operations", "forest"),
            _office("orders", "Orders", "الطلبات", 12, 8, 6, "operations", "forest"),
            _work("warehouse", "warehouse", "Warehouse", "المستودع", 16, 12, 4, "operations", "slate"),
        ), 36.0),
        _floor(1, "Floor 1: Sales and Marketing", "الطابق 1: المبيعات والتسويق", (
            _office("sales-office", "Sales Office", "مكتب المبيعات", 12, 8, 6, "sales", "forest"),
            _office("marketing-studio", "Marketing Studio", "استوديو التسويق", 12, 8, 5, "marketing", "crimson"),
            _meeting("meeting-room", "Meeting Room", "غرفة الاجتماعات", 8, 6, 8, "sapphire"),
        ), 34.0),
        _floor(2, "Floor 2: Customer Experience", "الطابق 2: تجربة العملاء", (
            _office("customer-service", "Customer Service", "خدمة العملاء", 12, 8, 6, "support", "forest"),
            _office("support", "Support", "الدعم", 12, 8, 6, "support", "forest"),
            _office("messages", "Messages", "الرسائل", 10, 8, 4, "support", "forest"),
            _office("tickets", "Tickets", "التذاكر", 10, 8, 4, "support", "forest"),
        ), 34.0),
    )),
    BuildingSpec(3, "tyre-pulse", "Tyre Pulse", "تاير بالس", (
        _floor(0, "Ground floor: Workshop, Warehouse and Logistics", "الطابق الأرضي: الورشة والمستودع والخدمات اللوجستية", (
            _work("workshop", "workshop", "Workshop", "الورشة", 14, 10, 4, "tyre_engineering", "sand", "east"),
            _work("workshop", "maintenance", "Maintenance", "الصيانة", 12, 9, 4, "tyre_engineering", "sand", "east"),
            _work("warehouse", "warehouse", "Warehouse", "المستودع", 16, 12, 4, "operations", "slate"),
            _office("logistics", "Logistics", "الخدمات اللوجستية", 10, 8, 4, "operations", "teal"),
        ), 36.0),
        _floor(1, "Floor 1: Fleet, Tyre Engineering and Inspections", "الطابق 1: الأسطول وهندسة الإطارات والفحص", (
            _office("fleet", "Fleet", "الأسطول", 12, 8, 6, "operations", "teal"),
            _office("tyre-engineering", "Tyre Engineering", "هندسة الإطارات", 10, 8, 4, "tyre_engineering", "teal"),
            _office("inspections", "Inspections", "الفحص", 8, 7, 3, "tyre_engineering", "teal"),
        ), 34.0),
    )),
    BuildingSpec(4, "corporate-services", "Corporate Services", "الخدمات المؤسسية", (
        _floor(0, "Ground floor: HR, Finance and Procurement", "الطابق الأرضي: الموارد البشرية والمالية والمشتريات", (
            _office("hr", "HR", "الموارد البشرية", 10, 8, 4, "hr", "forest"),
            _office("finance-office", "Finance Office", "المكتب المالي", 12, 8, 6, "finance", "brass"),
            _office("wealth-office", "Wealth Office", "مكتب الثروة", 10, 8, 3, "finance", "brass"),
            _office("procurement", "Procurement", "المشتريات", 10, 8, 4, "procurement", "teal"),
        ), 34.0),
        _floor(1, "Floor 1: Legal, Compliance and Governance", "الطابق 1: القانونية والامتثال والحوكمة", (
            _office("legal-office", "Legal Office", "المكتب القانوني", 10, 8, 4, "legal", "slate"),
            _office("compliance-office", "Compliance Office", "مكتب الامتثال", 10, 8, 4, "compliance", "slate"),
            _office("governance-office", "Governance Office", "مكتب الحوكمة", 10, 8, 3, "compliance", "slate"),
            _meeting("audit-room", "Audit Room", "غرفة التدقيق", 8, 6, 8, "sapphire"),
        ), 34.0),
    )),
    BuildingSpec(5, "knowledge-ai", "Knowledge & AI", "المعرفة والذكاء الاصطناعي", (
        _floor(0, "Ground floor: AI Labs, Data and Automation", "الطابق الأرضي: مختبرات الذكاء والبيانات والأتمتة", (
            _office("ai-labs", "AI Labs", "مختبرات الذكاء الاصطناعي", 14, 9, 8, None, "amethyst", "south",
                    (("monitor", 8), ("display", 2), ("server_rack", 1))),
            _office("data", "Data", "البيانات", 12, 8, 6, None, "sapphire"),
            _office("automation", "Automation", "الأتمتة", 10, 8, 4, None, "sapphire"),
        ), 34.0),
        _floor(1, "Floor 1: Technology and Engineering", "الطابق 1: التقنية والهندسة", (
            _office("development", "Development", "التطوير", 14, 9, 8, "technology", "sapphire", "south",
                    (("monitor", 8), ("whiteboard", 1), ("charging_station", 1))),
            _office("deployments", "Deployments", "النشر", 10, 8, 4, "technology", "sapphire", "south",
                    (("monitor", 4), ("server_rack", 1), ("network_switch", 1))),
            _office("incidents", "Incidents", "الحوادث", 10, 8, 4, "technology", "crimson", "south",
                    (("monitor", 4), ("display", 2), ("whiteboard", 1))),
        ), 34.0),
        _floor(2, "Floor 2: Research and Intelligence", "الطابق 2: البحث والاستخبارات", (
            _office("research-room", "Research Room", "غرفة البحث", 12, 8, 6, "research", "amethyst"),
            _office("intelligence-desk", "Intelligence Desk", "مكتب الاستخبارات", 10, 8, 4, "research", "amethyst"),
            _meeting("briefing-room", "Briefing Room", "غرفة الإحاطة", 8, 6, 8, "sapphire"),
        ), 34.0),
    )),
    BuildingSpec(6, "new-ventures", "New Ventures", "المشاريع الجديدة", (
        _floor(0, "Ground floor: Investments, R&D and Future Businesses", "الطابق الأرضي: الاستثمارات والبحث والتطوير والأعمال المستقبلية", (
            _office("investments", "Investments", "الاستثمارات", 12, 8, 4, "finance", "brass"),
            _office("research-development", "R&D", "البحث والتطوير", 12, 8, 6, "research", "amethyst"),
            _office("future-businesses", "Future Businesses", "الأعمال المستقبلية", 12, 8, 4, None, "teal"),
            _meeting("meeting-room", "Meeting Room", "غرفة الاجتماعات", 8, 6, 8, "sapphire"),
        ), 34.0),
    )),
    BuildingSpec(7, "personal", "Personal Office", "المكتب الشخصي", (
        _floor(0, "Ground floor", "الطابق الأرضي", (
            _office("personal-office", "Personal Office", "المكتب الشخصي", 10, 8, 2, None, "sand", "south",
                    (("monitor", 2), ("whiteboard", 1))),
            RoomSpec("lounge", "Lounge", "الاستراحة", "lounge", 8, 6, 6, 0, None, "sand", "south", _LOUNGE),
        ), 26.0),
    )),
    BuildingSpec(8, "career", "Career & Employment", "المسار المهني والتوظيف", (
        _floor(0, "Ground floor", "الطابق الأرضي", (
            _office("career-office", "Career Office", "مكتب المسار المهني", 10, 8, 3, None, "forest"),
            _office("employment-desk", "Employment Desk", "مكتب التوظيف", 10, 8, 3, None, "forest"),
            _meeting("interview-room", "Interview Room", "غرفة المقابلات", 6, 5, 4, "sapphire"),
        ), 28.0),
    )),
)
BUILDING_KEYS: Final = tuple(b.key for b in CAMPUS)
BUILDING_BY_KEY: Final = {b.key: b for b in CAMPUS}


# --------------------------------------------------------------------------- ids and packing


def floor_id(building: BuildingSpec, floor: FloorSpec) -> str:
    return f"{building.id}-l{floor.level}"


def room_id(building: BuildingSpec, room: RoomSpec) -> str:
    return f"{building.id}-{room.key}"[:80]


def desk_id(rid: str, index: int) -> str:
    return f"{rid[:72]}-d{index}"


def pack(rooms: tuple[RoomSpec, ...], width: float) -> tuple[dict[str, tuple[float, float]], float]:
    """Row by row placement with a 2 metre margin and gap. Returns positions by room key and the floor depth."""
    x = y = FLOOR_MARGIN_M
    row_depth = 0.0
    out: dict[str, tuple[float, float]] = {}
    for room in rooms:
        if x > FLOOR_MARGIN_M and x + room.width + FLOOR_MARGIN_M > width:
            y += row_depth + ROOM_GAP_M
            x, row_depth = FLOOR_MARGIN_M, 0.0
        out[room.key] = (x, y)
        x += room.width + ROOM_GAP_M
        row_depth = max(row_depth, room.depth)
    return out, float(int(y + row_depth + FLOOR_MARGIN_M + 0.999))


def floor_depth(floor: FloorSpec) -> float:
    return pack(floor.rooms, floor.width)[1]


# --------------------------------------------------------------------------- existing records


@dataclass(slots=True)
class Facts:
    """The records that already exist in this workspace, as far as the master list can meet them."""

    buildings: dict[str, dict[str, Any]] = field(default_factory=dict)
    floors: dict[str, dict[str, Any]] = field(default_factory=dict)
    rooms: dict[str, dict[str, Any]] = field(default_factory=dict)
    desks: set[str] = field(default_factory=set)

    def level_taken(self, building_id: str, level: int) -> bool:
        return any(f["building_id"] == building_id and f["level"] == level for f in self.floors.values())

    def floor_rooms(self, fid: str) -> list[dict[str, Any]]:
        return [r for r in self.rooms.values() if r["floor_id"] == fid]


# --------------------------------------------------------------------------- planner


def _room_plan(spec: RoomSpec, building: BuildingSpec, rid: str) -> dict[str, Any]:
    return {
        "key": spec.key, "id": rid, "name": {"en": spec.en, "ar": spec.ar}, "kind": spec.kind,
        "capacity": spec.capacity, "width": spec.width, "depth": spec.depth, "door_side": spec.door_side,
        "color": spec.color, "team_department": spec.department,
        "equipment": [{"kind": k, "quantity": q} for k, q in spec.equipment],
        "desk_count": spec.desks,
    }


def plan_building(spec: BuildingSpec, facts: Facts, timezone: str) -> dict[str, Any]:
    """What a confirm would do for one building. Pure: the same facts always give the same plan."""
    bid = spec.id
    b_exists = bid in facts.buildings
    floors_out: list[dict[str, Any]] = []
    for fspec in spec.floors:
        fid = floor_id(spec, fspec)
        positions, depth = pack(fspec.rooms, fspec.width)
        f_row = facts.floors.get(fid)
        if f_row is not None:
            f_action, f_reason = "skip", EXISTS
            bounds = (float(f_row["width_m"]), float(f_row["depth_m"])) if f_row.get("width_m") is not None else None
            width_m, depth_m = (bounds if bounds else (fspec.width, depth))
        elif b_exists and facts.level_taken(bid, fspec.level):
            f_action, f_reason, bounds = "skip", LEVEL_IN_USE, None
            width_m, depth_m = fspec.width, depth
        else:
            f_action, f_reason, bounds = "create", None, (fspec.width, depth)
            width_m, depth_m = fspec.width, depth
        usable = f_action == "create" or f_reason == EXISTS
        placed: list[layout.Rect] = []
        names: set[str] = set()
        if f_reason == EXISTS:
            for r in facts.floor_rooms(fid):
                names.add(r["name"])
                if r.get("scene_x") is not None:
                    placed.append(layout.Rect(r["id"], r["scene_x"], r["scene_z"], r["scene_width"], r["scene_depth"]))
        rooms_out: list[dict[str, Any]] = []
        for rspec in fspec.rooms:
            rid = room_id(spec, rspec)
            item = _room_plan(rspec, spec, rid)
            existing = facts.rooms.get(rid)
            if existing is not None:
                item.update(action="skip", reason=EXISTS, desks=[])
                fill = department_fill(existing, rspec)
                if fill is not None:
                    item["department_set"] = fill
            elif not usable:
                item.update(action="skip", reason=FLOOR_UNAVAILABLE, desks=[])
            elif rspec.en in names:
                item.update(action="skip", reason=NAME_IN_USE, desks=[])
            else:
                px, pz = positions[rspec.key]
                candidate = layout.Rect(rid, px, pz, rspec.width, rspec.depth)
                fits = (layout.within_bounds(candidate, *bounds) if bounds else True) \
                    and not any(layout.overlaps(candidate, other) for other in placed)
                if not fits:
                    spot = layout.find_free_spot(placed, rspec.width, rspec.depth, bounds)
                    if spot is None:
                        item.update(action="skip", reason=NO_SPACE, desks=[])
                        rooms_out.append(item)
                        continue
                    px, pz = spot
                    candidate = layout.Rect(rid, px, pz, rspec.width, rspec.depth)
                placed.append(candidate)
                names.add(rspec.en)
                grid = layout.desk_grid(rspec.width, rspec.depth, rspec.desks)
                item.update(action="create", reason=None, x=px, z=pz, rotation=0, desks=[
                    {"id": desk_id(rid, i + 1), "label": f"Desk {i + 1}", "pos_x": g[0] if g else None,
                     "pos_z": g[1] if g else None, "action": "create"} for i, g in enumerate(grid)])
            rooms_out.append(item)
        floors_out.append({
            "key": f"l{fspec.level}", "id": fid, "level": fspec.level, "name": {"en": fspec.en, "ar": fspec.ar},
            "width_m": width_m, "depth_m": depth_m, "action": f_action, "reason": f_reason, "rooms": rooms_out,
        })
    counts = count_plan(floors_out, b_exists)
    pending = any(counts["create"].values()) or counts["update"]["room_departments"] > 0
    status = "complete" if not pending else ("new" if not b_exists else "partial")
    return {
        "key": spec.key, "number": spec.number, "id": bid, "name": {"en": spec.en, "ar": spec.ar},
        "timezone": timezone, "action": "skip" if b_exists else "create", "reason": EXISTS if b_exists else None,
        "status": status, "floors": floors_out, "counts": counts,
    }


def department_fill(existing: Mapping[str, Any], spec: RoomSpec) -> dict[str, str | None] | None:
    """The one change a re-run may make to a room that already exists: give a campus room its department.

    Only a room of the master list (matched by its stable id) whose department is still empty or ``custom`` is
    touched, and only to the department the master list names. A department the owner chose is never replaced,
    and nothing else about the room (name, position, size, desks) is ever changed.
    """
    current = existing.get("team_department")
    if spec.department is None or current not in (None, "custom") or current == spec.department:
        return None
    return {"from": current, "to": spec.department}


def count_plan(floors: list[dict[str, Any]], building_exists: bool) -> dict[str, dict[str, int]]:
    create = {"buildings": 0 if building_exists else 1, "floors": 0, "rooms": 0, "desks": 0}
    skip = {"buildings": 1 if building_exists else 0, "floors": 0, "rooms": 0, "desks": 0}
    update = {"room_departments": 0}
    for f in floors:
        create["floors"] += 1 if f["action"] == "create" else 0
        skip["floors"] += 1 if f["action"] == "skip" else 0
        for r in f["rooms"]:
            if r["action"] == "create":
                create["rooms"] += 1
                create["desks"] += len(r["desks"])
            else:
                skip["rooms"] += 1
                update["room_departments"] += 1 if r.get("department_set") else 0
    return {"create": create, "skip": skip, "update": update}


def plan_campus(facts: Facts, timezone: str, keys: list[str] | None = None) -> list[dict[str, Any]]:
    selected = set(keys) if keys is not None else set(BUILDING_KEYS)
    return [{**plan_building(b, facts, timezone), "selected": b.key in selected} for b in CAMPUS]


def totals(plans: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out = {"create": {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0}, "skip": {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0},
           "update": {"room_departments": 0}}
    for p in plans:
        if not p["selected"]:
            continue
        for side in ("create", "skip", "update"):
            for k, v in p["counts"][side].items():
                out[side][k] += v
    return out


def has_work(plan: Mapping[str, Any]) -> bool:
    """True when a confirm would write something for this building: a new record or a department to fill."""
    return any(plan["counts"]["create"].values()) or plan["counts"]["update"]["room_departments"] > 0


# --------------------------------------------------------------------------- hash


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def building_digest(plan: Mapping[str, Any]) -> str:
    """What one building's confirm will do, ignoring whether the owner ticked it."""
    return hashlib.sha256(canonical({k: v for k, v in plan.items() if k != "selected"}).encode()).hexdigest()


def proposal_hash(tenant_id: str, workspace_id: str, user_id: str, timezone: str, plans: list[dict[str, Any]]) -> str:
    """Bound to who confirms, where, and the exact selected plans: any change to the records changes it."""
    content = {"v": CATALOG_VERSION, "tz": timezone,
               "buildings": [{"key": p["key"], "digest": building_digest(p)} for p in plans if p["selected"]]}
    return hashlib.sha256(canonical({"t": tenant_id, "w": workspace_id, "u": user_id, "p": content}).encode()).hexdigest()
