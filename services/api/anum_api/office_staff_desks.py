"""Staff my campus: the pure planner (docs/ANUM_MINE_STAFF_DESKS.md).

Takes the office records of ONE workspace as plain data and returns, for every desk, what a confirm would do:
create a paused agent for it (``plan``), seat an agent an earlier interrupted run already built (``resume``), or
leave it alone and say why. No database, no web framework, nothing created here.

Rules the planner enforces:

* A desk that already has an agent is never touched (``occupied``).
* A desk the owner marked "keep empty" is never touched (``keep_empty``).
* The 200 agents per workspace limit is counted against the agents that exist now. Desks past it stay empty
  (``over_limit``), and the owner can set a lower cap for the run (``over_cap``).
* A room with no matching role keeps empty desks (``no_role``); an unticked room is ``excluded``.
* Order is fixed (building id, floor level, room name, desk number) so "the first N" means the same thing in the
  preview and in the confirm, and a retry picks the same desks.
* ``batch_hash`` binds one building's batch to the tenant, workspace, confirming user and the exact list of
  desks, roles, names and caps, so a changed list can never be confirmed with an old hash.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

from . import office_provisioning as op
from . import office_staff_templates as st

CATALOG_VERSION: Final = 1
MAX_AGENTS: Final = 200

# Desk states, in the words the web translates.
PLAN: Final = "plan"
RESUME: Final = "resume"
OCCUPIED: Final = "occupied"
KEEP_EMPTY: Final = "keep_empty"
OVER_LIMIT: Final = "over_limit"
OVER_CAP: Final = "over_cap"
NO_ROLE: Final = "no_role"
EXCLUDED: Final = "excluded"
AGENT_ELSEWHERE: Final = "agent_elsewhere"
STATES: Final = (PLAN, RESUME, OCCUPIED, KEEP_EMPTY, OVER_LIMIT, OVER_CAP, NO_ROLE, EXCLUDED, AGENT_ELSEWHERE)

_NUM = re.compile(r"(\d+)$")


def _ordinal_key(desk: Mapping[str, Any]) -> tuple[int, str, str]:
    """Natural order: d2 before d10, then label, then id. The position in this order is the number in the name."""
    match = _NUM.search(str(desk["id"]))
    return (int(match.group(1)) if match else 10**9, str(desk["label"]), str(desk["id"]))


def plan_staffing(
    *, buildings: Iterable[Mapping[str, Any]], floors: Iterable[Mapping[str, Any]], rooms: Iterable[Mapping[str, Any]],
    desks: Iterable[Mapping[str, Any]], ledger: Mapping[str, Mapping[str, Any]], live_agents: set[str],
    markers: Mapping[str, str], agent_count: int, selected: set[str] | None, exclude_rooms: set[str],
    max_agents: int | None, lang: str,
) -> dict[str, Any]:
    """The whole plan. ``ledger`` maps desk id to its staffing row; ``markers`` maps a desk marker to the id of an
    agent that carries it; ``selected`` is the set of building ids (None means all)."""
    building_list = sorted(buildings, key=lambda b: b["id"])
    floor_by_id = {f["id"]: f for f in floors}
    desks_by_room: dict[str, list[Mapping[str, Any]]] = {}
    for d in desks:
        desks_by_room.setdefault(d["room_id"], []).append(d)
    seated = {d["agent_id"]: d["id"] for ds in desks_by_room.values() for d in ds if d["agent_id"]}
    rooms_by_building: dict[str, list[Mapping[str, Any]]] = {}
    for r in rooms:
        floor = floor_by_id.get(r["floor_id"])
        if floor is not None:
            rooms_by_building.setdefault(floor["building_id"], []).append(r)

    slots = max(0, MAX_AGENTS - agent_count)
    cap_left = None if max_agents is None else max(0, max_agents)
    out_buildings: list[dict[str, Any]] = []
    for b in building_list:
        in_run = selected is None or b["id"] in selected
        room_list = sorted(rooms_by_building.get(b["id"], []), key=lambda r: (
            floor_by_id[r["floor_id"]]["level"], str(r["name"]).lower(), r["id"]))
        room_views: list[dict[str, Any]] = []
        for r in room_list:
            floor = floor_by_id[r["floor_id"]]
            template_id, source = st.template_for_room(r)
            template = st.STAFF_TEMPLATE_BY_ID.get(template_id) if template_id else None
            desk_views: list[dict[str, Any]] = []
            ordered = sorted(desks_by_room.get(r["id"], []), key=_ordinal_key)
            for number, d in enumerate(ordered, start=1):
                view: dict[str, Any] = {"id": d["id"], "label": d["label"], "number": number, "state": None, "reason": None,
                                        "agent": None}
                row = ledger.get(d["id"])
                existing = None
                if template is not None:
                    marker = st.desk_marker(template.id, d["id"])
                    if row and row.get("agent_id") in live_agents:
                        existing = row["agent_id"]
                    elif markers.get(marker):
                        existing = markers[marker]
                if d["agent_id"]:
                    view["state"] = OCCUPIED
                elif row and row["status"] == "keep_empty":
                    view["state"] = KEEP_EMPTY
                elif not in_run or r["id"] in exclude_rooms:
                    view["state"] = EXCLUDED
                elif template is None:
                    view["state"], view["reason"] = NO_ROLE, "no_matching_role"
                elif existing is not None and existing in seated:
                    view["state"], view["reason"] = AGENT_ELSEWHERE, "agent_seated_elsewhere"
                elif existing is not None:
                    view["state"] = RESUME
                else:
                    if slots <= 0:
                        view["state"], view["reason"] = OVER_LIMIT, "agent_limit"
                    elif cap_left is not None and cap_left <= 0:
                        view["state"], view["reason"] = OVER_CAP, "run_cap"
                    else:
                        view["state"] = PLAN
                        slots -= 1
                        if cap_left is not None:
                            cap_left -= 1
                if view["state"] in (PLAN, RESUME) and template is not None:
                    view["agent"] = {
                        "name": st.agent_name(template, r["name"], r.get("name_ar"), number, lang),
                        "template_id": template.id, "monthly_cost_limit_usd": template.monthly_cost_limit_usd,
                        "existing_agent_id": existing,
                    }
                desk_views.append(view)
            room_views.append({
                "id": r["id"], "name": r["name"], "name_ar": r.get("name_ar"), "kind": r["kind"],
                "team_department": r.get("team_department"), "floor_id": floor["id"], "floor_level": floor["level"],
                "floor_name": floor.get("name"), "template_id": template.id if template else None, "role_source": source,
                "desks": desk_views, "counts": _count(desk_views),
            })
        out_buildings.append({
            "id": b["id"], "name": b["name"], "selected": in_run, "rooms": room_views,
            "counts": _count([d for rv in room_views for d in rv["desks"]]),
        })
    return {"buildings": out_buildings, "free_slots_after": slots}


def _count(desk_views: list[Mapping[str, Any]]) -> dict[str, int]:
    out = {s: 0 for s in STATES}
    for d in desk_views:
        out[d["state"]] += 1
    out["total"] = len(desk_views)
    return out


def batch_items(building: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The exact desks a confirm for this building will work on, in order."""
    items = []
    for room in building["rooms"]:
        for d in room["desks"]:
            if d["state"] in (PLAN, RESUME):
                items.append({
                    "desk_id": d["id"], "room_id": room["id"], "mode": d["state"], "template_id": d["agent"]["template_id"],
                    "name": d["agent"]["name"], "cap": d["agent"]["monthly_cost_limit_usd"],
                    "existing_agent_id": d["agent"]["existing_agent_id"],
                })
    return items


def batch_hash(tenant_id: str, workspace_id: str, user_id: str, building_id: str, lang: str, items: list[dict[str, Any]]) -> str:
    return op.proposal_hash(tenant_id, workspace_id, user_id, {
        "kind": "staff_desks", "catalog": CATALOG_VERSION, "building": building_id, "lang": lang, "items": items})


def cost_summary(buildings: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Maximum monthly cost if every agent in the plan were turned on: the sum of their caps, per role."""
    per: dict[str, dict[str, Any]] = {}
    for b in buildings:
        for item in batch_items(b):
            row = per.setdefault(item["template_id"], {"template_id": item["template_id"], "count": 0,
                                                       "cap_usd": item["cap"], "subtotal_usd": 0.0})
            row["count"] += 1
            row["subtotal_usd"] = round(row["subtotal_usd"] + item["cap"], 2)
    rows = sorted(per.values(), key=lambda r: r["template_id"])
    return {"per_template": rows, "max_monthly_usd": round(sum(r["subtotal_usd"] for r in rows), 2),
            "agents": sum(r["count"] for r in rows)}


def turn_on_hash(tenant_id: str, workspace_id: str, user_id: str, items: list[dict[str, Any]]) -> str:
    return op.proposal_hash(tenant_id, workspace_id, user_id, {"kind": "staff_desks_turn_on", "items": items})
