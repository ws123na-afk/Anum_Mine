"""Green Concrete: the owner's company with its command center, seats, shifts and people, and the pure planner.

Data and rules only, no database and no web framework (docs/ANUM_MINE_GREEN_CONCRETE.md).

* The people, their role titles and their seats are owner supplied, so they are real records, but they are created
  only by the owner-triggered, previewed, click-confirmed action ``Add Green Concrete``, never hard coded into a scene.
* Every record has a stable id (``gc-green-concrete``, ``gc-green-concrete-l0``, ``gc-green-concrete-command``,
  ``gc-green-concrete-command-d1`` ...) and every agent a stable marker (``gc:<key>``). Matching by those makes a
  retry, a double click or a partial failure add only what is missing, and nothing that exists is ever changed.
* The shift hours are DEFAULTS the owner must confirm (day 07:00 to 19:00, night 19:00 to 07:00, read in the
  building's time zone). They are not stored by this action: a building with no hours uses them, and the owner edits
  them in the builder.
* Nobody is working because of this action: every agent is created paused and no presence is invented.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

CATALOG_VERSION: Final = 1
DEFAULT_TIMEZONE: Final = "Asia/Dubai"
DEFAULT_DAY_START: Final = "07:00"
DEFAULT_NIGHT_START: Final = "19:00"
MONTHLY_CAP_USD: Final = 3.0
AGENT_MARKER: Final = "gc:"

BUILDING_ID: Final = "gc-green-concrete"
FLOOR_ID: Final = "gc-green-concrete-l0"
ROOM_ID: Final = "gc-green-concrete-command"
BUILDING_NAME: Final = "Green Concrete"
FLOOR_NAME: Final = "Ground floor"
ROOM_NAME: Final = "Command Center"
GROUP_L: Final = "l-table"
GROUP_BACK: Final = "back-desk"

# Room and floor geometry (metres). The floor is 22 by 16, the room sits at (2, 2) and is 16 by 12, door in the south wall.
FLOOR_W: Final = 22.0
FLOOR_D: Final = 16.0
ROOM_X: Final = 2.0
ROOM_Z: Final = 2.0
ROOM_W: Final = 16.0
ROOM_D: Final = 12.0
ROOM_CAPACITY: Final = 12

EXISTS: Final = "exists"
NAME_IN_USE: Final = "name_in_use"
LEVEL_IN_USE: Final = "level_in_use"
ROOM_NAME_IN_USE: Final = "name_in_use"
PARENT_UNAVAILABLE: Final = "parent_unavailable"
DESK_TAKEN: Final = "desk_taken"
AGENT_ELSEWHERE: Final = "agent_elsewhere"
SEATED: Final = "seated"
DESK_MISSING: Final = "desk_missing"


@dataclass(frozen=True, slots=True)
class Seat:
    """One place at the L shaped table (or the back desk), in room metres from the north west corner."""

    slot: int
    x: float
    z: float
    facing: str
    group: str


# The L: a long leg of four seats across the room (people look north at the wall of screens), then a short leg of two seats
# at its right end running towards the door (those people look east). Seat 1 is the left end of the L.
L_SEATS: Final[tuple[Seat, ...]] = (
    Seat(1, 4.8, 4.2, "north", GROUP_L),
    Seat(2, 6.3, 4.2, "north", GROUP_L),
    Seat(3, 7.8, 4.2, "north", GROUP_L),
    Seat(4, 9.3, 4.2, "north", GROUP_L),
    Seat(5, 10.44, 6.4, "east", GROUP_L),
    Seat(6, 10.44, 7.9, "east", GROUP_L),
)
# The manager sits behind the team, on the door side, looking north over the whole L.
MANAGER_SEAT: Final = Seat(1, 6.6, 9.9, "north", GROUP_BACK)


@dataclass(frozen=True, slots=True)
class Person:
    key: str
    name: str
    role_en: str
    role_ar: str
    shift: str | None
    slot: int  # place along the L (day and night people share the slots 1 to 3); 0 for the manager


# The owner's words, with only obvious spelling fixed. Order is the day order from the left end of the L, then the night
# shift, then the manager.
PEOPLE: Final[tuple[Person, ...]] = (
    Person("raed", "Raed", "Business analyst", "محلل أعمال", "day", 1),
    Person("hazrat", "Hazrat", "Data monitoring", "مراقبة البيانات", "day", 2),
    Person("mai", "Mai", "Data monitoring", "مراقبة البيانات", "day", 3),
    Person("shahzeb", "Shahzeb", "Tyre planning engineer", "مهندس تخطيط الإطارات", "day", 4),
    Person("ijaz", "Ijaz", "Data collector", "جامع بيانات", "day", 5),
    Person("badea", "Badea", "Data monitoring", "مراقبة البيانات", "day", 6),
    Person("ali-zeehan", "Ali Zeehan", "Data monitoring", "مراقبة البيانات", "night", 1),
    Person("amjad", "Amjad", "Data collector", "جامع بيانات", "night", 2),
    Person("rana", "Rana", "Data collector", "جامع بيانات", "night", 3),
    Person("ahmed-elbendari", "Ahmed Elbendari", "PMV manager", "مدير PMV", None, 0),
)
PEOPLE_BY_KEY: Final = {p.key: p for p in PEOPLE}


def desk_id(index: int) -> str:
    return f"{ROOM_ID}-d{index}"


def desk_index(person: Person) -> int:
    return PEOPLE.index(person) + 1


def marker(person: Person) -> str:
    return f"{AGENT_MARKER}{person.key}"[:40]


def seat_of(person: Person) -> Seat:
    return MANAGER_SEAT if person.shift is None else L_SEATS[person.slot - 1]


def desk_label(person: Person) -> str:
    if person.shift is None:
        return "Manager desk"
    return f"{person.shift.capitalize()} seat {person.slot}"


def desk_fields(person: Person) -> dict[str, Any]:
    seat = seat_of(person)
    return {"shift": person.shift, "seat_order": seat.slot, "facing": seat.facing,
            "seat_kind": "manager" if person.shift is None else "team", "table_group": seat.group}


def valid_hours(day_start: str | None, night_start: str | None) -> tuple[str, str, bool]:
    """The hours in force and whether they are the unconfirmed defaults (no stored hours)."""
    return (day_start or DEFAULT_DAY_START, night_start or DEFAULT_NIGHT_START, day_start is None and night_start is None)


def shift_at(minutes: int, day_start: str, night_start: str) -> str:
    """The shift in force at a local time of day (minutes after midnight). Night wraps over midnight."""
    d, n = _minutes(day_start), _minutes(night_start)
    if d <= n:
        return "day" if d <= minutes < n else "night"
    return "night" if n <= minutes < d else "day"


def _minutes(text: str) -> int:
    hours, mins = text.split(":")
    return int(hours) * 60 + int(mins)


@dataclass(slots=True)
class Facts:
    """What exists in this workspace that the plan could meet."""

    building: dict[str, Any] | None = None
    other_building_with_name: bool = False
    floor: dict[str, Any] | None = None
    level_taken: bool = False
    room: dict[str, Any] | None = None
    room_name_taken: bool = False
    desks: dict[str, dict[str, Any]] | None = None
    agents: dict[str, str] | None = None  # marker -> agent id
    agent_desk: dict[str, str] | None = None  # agent id -> desk id (any desk of the workspace)
    agent_count: int = 0


def plan(facts: Facts, timezone: str) -> dict[str, Any]:
    """What a confirm would do. Pure: the same facts always give the same plan."""
    desks = facts.desks or {}
    agents = facts.agents or {}
    agent_desk = facts.agent_desk or {}
    b_exists = facts.building is not None
    b_skip = EXISTS if b_exists else NAME_IN_USE if facts.other_building_with_name else None
    building = {"id": BUILDING_ID, "name": {"en": BUILDING_NAME, "ar": None}, "timezone": timezone,
                "action": "skip" if b_skip else "create", "reason": b_skip}
    f_exists = facts.floor is not None
    f_reason = EXISTS if f_exists else LEVEL_IN_USE if (b_exists and facts.level_taken) else (
        PARENT_UNAVAILABLE if b_skip == NAME_IN_USE else None)
    floor = {"id": FLOOR_ID, "level": 0, "name": {"en": FLOOR_NAME, "ar": None}, "width_m": FLOOR_W, "depth_m": FLOOR_D,
             "action": "skip" if f_reason else "create", "reason": f_reason}
    r_exists = facts.room is not None
    r_reason = EXISTS if r_exists else PARENT_UNAVAILABLE if floor["reason"] not in (None, EXISTS) else (
        ROOM_NAME_IN_USE if facts.room_name_taken else None)
    room = {"id": ROOM_ID, "name": {"en": ROOM_NAME, "ar": None}, "kind": "command", "capacity": ROOM_CAPACITY,
            "x": ROOM_X, "z": ROOM_Z, "width": ROOM_W, "depth": ROOM_D, "door_side": "south",
            "action": "skip" if r_reason else "create", "reason": r_reason}
    room_usable = room["action"] == "create" or r_exists
    people: list[dict[str, Any]] = []
    seats: list[dict[str, Any]] = []
    for person in PEOPLE:
        index = desk_index(person)
        did = desk_id(index)
        seat = seat_of(person)
        existing_desk = desks.get(did)
        d_action, d_reason = ("skip", EXISTS) if existing_desk is not None else (
            ("create", None) if room_usable else ("skip", PARENT_UNAVAILABLE))
        seats.append({"desk_id": did, "label": desk_label(person), "person": person.key, "pos_x": seat.x, "pos_z": seat.z,
                      **desk_fields(person), "action": d_action, "reason": d_reason})
        agent_id = agents.get(marker(person))
        a_action = "exists" if agent_id else "create"
        if not room_usable:
            s_action, s_reason = "skip", PARENT_UNAVAILABLE
        elif existing_desk is not None and existing_desk.get("agent_id"):
            same = agent_id is not None and existing_desk["agent_id"] == agent_id
            s_action, s_reason = ("skip", SEATED) if same else ("skip", DESK_TAKEN)
        elif agent_id and agent_desk.get(agent_id) not in (None, did):
            s_action, s_reason = "skip", AGENT_ELSEWHERE
        else:
            s_action, s_reason = "seat", None
        people.append({
            "key": person.key, "name": person.name, "role": {"en": person.role_en, "ar": person.role_ar},
            "shift": person.shift, "seat_order": seat.slot, "desk_id": did, "agent_id": agent_id,
            "agent_action": a_action, "seat_action": s_action, "seat_reason": s_reason,
        })
    counts = _counts(building, floor, room, seats, people)
    pending = any(counts["create"].values()) or counts["seat"] > 0
    status = "complete" if not pending else ("new" if counts["skip"]["desks"] == 0 and building["action"] == "create" else "partial")
    return {"building": building, "floor": floor, "room": room, "seats": seats, "people": people, "counts": counts, "status": status,
            "agent_slots_left": max(0, 200 - facts.agent_count)}


def _counts(building: Mapping[str, Any], floor: Mapping[str, Any], room: Mapping[str, Any],
            seats: list[dict[str, Any]], people: list[dict[str, Any]]) -> dict[str, Any]:
    create = {
        "buildings": int(building["action"] == "create"), "floors": int(floor["action"] == "create"),
        "rooms": int(room["action"] == "create"), "desks": sum(1 for s in seats if s["action"] == "create"),
        "agents": sum(1 for p in people if p["agent_action"] == "create" and p["seat_reason"] != PARENT_UNAVAILABLE),
    }
    skip = {
        "buildings": int(building["action"] == "skip"), "floors": int(floor["action"] == "skip"),
        "rooms": int(room["action"] == "skip"), "desks": sum(1 for s in seats if s["action"] == "skip"),
        "agents": sum(1 for p in people if p["agent_action"] == "exists"),
    }
    return {"create": create, "skip": skip, "seat": sum(1 for p in people if p["seat_action"] == "seat")}


def has_work(p: Mapping[str, Any]) -> bool:
    return any(p["counts"]["create"].values()) or p["counts"]["seat"] > 0


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def proposal_hash(tenant_id: str, workspace_id: str, user_id: str, timezone: str, p: Mapping[str, Any]) -> str:
    """Bound to who confirms, where, and the exact plan shown: any change to the records changes it."""
    content = {"v": CATALOG_VERSION, "tz": timezone, "building": p["building"], "floor": p["floor"], "room": p["room"],
               "seats": p["seats"], "people": [{k: v for k, v in x.items() if k != "agent_id"} | {"has_agent": x["agent_id"] is not None}
                                              for x in p["people"]]}
    return hashlib.sha256(canonical({"op": "green-concrete", "t": tenant_id, "w": workspace_id, "u": user_id, "p": content}).encode()).hexdigest()
