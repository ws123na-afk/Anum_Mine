"""One click "Set up my campus" for the ANUM MINE headquarters (docs/ANUM_MINE_STARTER_CAMPUS.md).

* ``GET  /api/v1/office/starter-campus/preview``: every building, floor, room and desk a confirm WILL create,
  and what already exists and will be skipped, with a ``payload_hash``. Nothing is stored. ``task:read``.
* ``POST /api/v1/office/starter-campus/confirm``: needs ``workspace:manage``, the hash you were shown and
  ``input_mode: "click"``. A spoken or typed confirmation can never confirm, and no registered tool calls it.

Everything happens inside the caller's own tenant and workspace under row level security. Each building is
one transaction (all of it or none of it), records are matched by stable ids derived from the master list,
and a record that exists is never changed. No agent is created and nothing is seated: new rooms are empty.
"""
from __future__ import annotations

import hmac
import logging
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import office_starter_campus as sc
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_scope import require_postgres, workspace_session
from .office_space_api import _audit as office_audit
from .office_space_api import _snap, _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/office/starter-campus", tags=["office starter campus"])
VERSION = 1
KeyList = Annotated[list[str], Field(min_length=1, max_length=len(sc.BUILDING_KEYS))]


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Only a click on the review dialog confirms. Voice and typed sentences are refused.
    input_mode: Literal["click", "typed", "voice"] = "typed"
    buildings: KeyList
    timezone: str = Field(min_length=3, max_length=80)


class _Changed(Exception):
    """The records moved between the preview and the write; nothing was written for this building."""


def _keys(raw: list[str] | None) -> list[str]:
    if raw is None:
        return list(sc.BUILDING_KEYS)
    unknown = [k for k in raw if k not in sc.BUILDING_BY_KEY]
    if unknown or not raw or len(set(raw)) != len(raw):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose at least one building from the list, each once")
    return [b.key for b in sc.CAMPUS if b.key in set(raw)]


def _zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid IANA time zone") from exc
    return value


# --------------------------------------------------------------------------- reading what exists


def load_facts(session: Any, context: TenantContext, specs: tuple[sc.BuildingSpec, ...] | list[sc.BuildingSpec]) -> sc.Facts:
    """Only the records the master list could meet, read in this workspace's RLS session."""
    p = {"t": context.tenant_id, "w": context.workspace_id}
    bids = [b.id for b in specs]
    fids = [sc.floor_id(b, f) for b in specs for f in b.floors]
    rids = [sc.room_id(b, r) for b in specs for f in b.floors for r in f.rooms]
    dids = [sc.desk_id(sc.room_id(b, r), i + 1) for b in specs for f in b.floors for r in f.rooms for i in range(r.desks)]
    facts = sc.Facts()
    for r in session.execute(text(
            "select id, name, timezone from office_buildings where tenant_id = :t and workspace_id = :w "
            "and id = any(cast(:ids as text[]))"), {**p, "ids": bids}).mappings():
        facts.buildings[r["id"]] = dict(r)
    for r in session.execute(text(
            "select id, building_id, level, name, width_m, depth_m from office_floors "
            "where tenant_id = :t and workspace_id = :w and (building_id = any(cast(:b as text[])) or id = any(cast(:f as text[])))"),
            {**p, "b": bids, "f": fids}).mappings():
        facts.floors[r["id"]] = dict(r)
    for r in session.execute(text(
            "select id, floor_id, name, scene_x, scene_z, scene_width, scene_depth, team_department from office_rooms "
            "where tenant_id = :t and workspace_id = :w and (floor_id = any(cast(:f as text[])) or id = any(cast(:r as text[])))"),
            {**p, "f": fids, "r": rids}).mappings():
        facts.rooms[r["id"]] = dict(r)
    facts.desks = {r[0] for r in session.execute(text(
        "select id from office_desks where tenant_id = :t and workspace_id = :w and id = any(cast(:d as text[]))"),
        {**p, "d": dids})}
    return facts


def _default_zone(session: Any, context: TenantContext) -> str:
    row = session.execute(text(
        "select timezone from office_buildings where tenant_id = :t and workspace_id = :w "
        "order by created_at, id limit 1"), {"t": context.tenant_id, "w": context.workspace_id}).first()
    return row[0] if row else sc.DEFAULT_TIMEZONE


def _read(context: TenantContext, zone: str | None) -> tuple[str, sc.Facts]:
    require_postgres()
    with workspace_session(context) as session:
        facts = load_facts(session, context, sc.CAMPUS)
        return (zone or _default_zone(session, context)), facts


# --------------------------------------------------------------------------- preview


def _summary(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"key": p["key"], "selected": p["selected"], "status": p["status"], "counts": p["counts"]} for p in plans]


@router.get("/preview")
def preview(buildings: Annotated[str | None, Query(max_length=400, pattern=r"^[a-z0-9,-]*$")] = None,
            timezone: Annotated[str | None, Query(max_length=80)] = None,
            context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    keys = _keys(None if buildings is None else buildings.split(","))
    zone, facts = _read(context, _zone(timezone) if timezone else None)
    plans = sc.plan_campus(facts, zone, keys)
    return {
        "version": VERSION, "catalog_version": sc.CATALOG_VERSION, "workspace_id": context.workspace_id,
        "timezone": zone, "selected": keys,
        "payload_hash": sc.proposal_hash(context.tenant_id, context.workspace_id, context.user_id, zone, plans),
        "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE),
        "buildings": plans, "totals": sc.totals(plans),
        "rules": {"creates_agents": False, "seats_agents": False, "changes_existing": False, "fills_empty_departments": True},
    }


# --------------------------------------------------------------------------- confirm


def _apply_building(context: TenantContext, spec: sc.BuildingSpec, zone: str, expected: str, digest: str) -> dict[str, Any]:
    """One building in one transaction. Returns what was created; raises _Changed when the records moved."""
    with _store(context) as store:
        # One writer per workspace at a time; the plan is then re-read under the lock.
        store.session.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                              {"k": f"{context.tenant_id}:{context.workspace_id}:starter-campus"})
        fresh = sc.plan_building(spec, load_facts(store.session, context, [spec]), zone)
        if sc.building_digest(fresh) != expected:
            if not sc.has_work(fresh):
                return {"status": "already_exists", "created": _zero(), "ids": [], "departments_set": 0}
            raise _Changed
        created = _zero()
        ids: list[str] = []
        snaps: dict[str, Any] = {"building": None, "floors": [], "rooms": []}
        skipped: list[dict[str, str]] = []
        filled: list[dict[str, Any]] = []
        if fresh["action"] == "create":
            row = store.create(context, "buildings", spec.id, {"name": spec.en, "name_ar": spec.ar, "timezone": zone,
                                                                "latitude": None, "longitude": None})
            created["buildings"] += 1
            ids.append(spec.id)
            snaps["building"] = _snap("buildings", row)
        else:
            skipped.append({"id": spec.id, "reason": sc.EXISTS})
        for floor in fresh["floors"]:
            if floor["action"] == "create":
                row = store.create(context, "floors", floor["id"], {
                    "building_id": spec.id, "level": floor["level"], "name": floor["name"]["en"],
                    "name_ar": floor["name"]["ar"], "width_m": floor["width_m"], "depth_m": floor["depth_m"]})
                created["floors"] += 1
                ids.append(floor["id"])
                snaps["floors"].append(_snap("floors", row))
            else:
                skipped.append({"id": floor["id"], "reason": floor["reason"]})
            for room in floor["rooms"]:
                if room["action"] != "create":
                    skipped.append({"id": room["id"], "reason": room["reason"]})
                    wanted = room.get("department_set")
                    if wanted:
                        # The only change to an existing room: an empty or custom department becomes the campus one.
                        done = store.fill_room_department(context, room["id"], wanted["to"])
                        if done is not None:
                            filled.append({"id": room["id"], "from": done[0]["team_department"], "to": wanted["to"]})
                    continue
                values = {
                    "name": room["name"]["en"], "name_ar": room["name"]["ar"], "kind": room["kind"],
                    "capacity": room["capacity"], "door_side": room["door_side"], "color": room["color"],
                    "rotation": room["rotation"], "equipment": room["equipment"], "team_department": room["team_department"],
                }
                row, desks = store.create_planned_room(
                    context, floor["id"], room["id"], values, room["x"], room["z"], room["width"], room["depth"],
                    [(d["id"], d["label"], (d["pos_x"], d["pos_z"]) if d["pos_x"] is not None else None) for d in room["desks"]])
                created["rooms"] += 1
                created["desks"] += len(desks)
                ids.extend([room["id"], *[d["id"] for d in desks]])
                snaps["rooms"].append({**_snap("rooms", row), "desks": [d["id"] for d in desks]})
        ids.extend(f["id"] for f in filled)
        office_audit(
            store, context, "office.starter_campus.building", spec.id, kind="buildings", ids=ids,
            before={"existing": [s["id"] for s in skipped if s["reason"] == sc.EXISTS], "skipped": skipped},
            after={"created": created, **snaps, "departments_set": filled, "payload_hash": digest, "input_mode": "click",
                   "catalog_version": sc.CATALOG_VERSION})
        return {"status": "created", "created": created, "ids": ids, "skipped": skipped, "departments_set": len(filled)}


_REFUSED_DEFAULT = "A record of this building conflicts with one that already exists. Nothing was created for this building."
_REFUSED = {
    "ROOM_NAME_TAKEN": "A room with the same name already exists on the floor. Nothing was created for this building.",
    "FLOOR_LEVEL_TAKEN": "Another floor already uses this level. Nothing was created for this building.",
    "DESK_LABEL_TAKEN": "A desk with the same label already exists in the room. Nothing was created for this building.",
    "ROOM_OVERLAP": "A room would overlap another room. Nothing was created for this building.",
    "ROOM_OUT_OF_BOUNDS": "A room would fall outside its floor. Nothing was created for this building.",
    "NO_FREE_SPACE": "There is no free space on the floor. Nothing was created for this building.",
}


def _zero() -> dict[str, int]:
    return {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0}


@router.post("/confirm")
def confirm(body: ConfirmBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    if body.input_mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
    keys = _keys(body.buildings)
    zone, facts = _read(context, _zone(body.timezone))
    plans = sc.plan_campus(facts, zone, keys)
    chosen = [p for p in plans if p["selected"]]
    digest = sc.proposal_hash(context.tenant_id, context.workspace_id, context.user_id, zone, plans)
    if not any(sc.has_work(p) for p in chosen):
        # A retry or a double click after it all exists: say so, write nothing, ask nothing.
        return _report("already_set_up", digest, zone, [
            _item(p, "already_exists", _zero(), "Already set up. Nothing was changed.") for p in chosen])
    if not hmac.compare_digest(body.payload_hash, digest):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "What would be created has changed since you opened it. Review the preview again.")
    items: list[dict[str, Any]] = []
    for plan in chosen:
        spec = sc.BUILDING_BY_KEY[plan["key"]]
        try:
            done = _apply_building(context, spec, zone, sc.building_digest(plan), digest)
            set_count = done.get("departments_set", 0)
            nothing_new = not any(done["created"].values())
            message = ("Already set up. Nothing was changed." if done["status"] != "created"
                       else f"Department set on {set_count} existing room(s)." if nothing_new and set_count else "Created.")
            items.append(_item(plan, done["status"], done["created"], message, set_count))
        except _Changed:
            items.append(_item(plan, "changed", _zero(), "The records changed while saving. Nothing was created for this building. Review the preview again."))
        except SpaceStoreError as exc:
            # A stable code and a fixed sentence: the exception text itself never reaches the response.
            logger.warning("starter campus building refused", extra={"anum_building": spec.key, "anum_reason": exc.reason or exc.code})
            items.append(_item(plan, "failed", _zero(), _REFUSED.get(exc.reason or "", _REFUSED_DEFAULT)))
        except Exception as exc:  # one building failing must not hide the others
            logger.error("starter campus building failed", extra={"anum_building": spec.key, "anum_error": type(exc).__name__})
            items.append(_item(plan, "failed", _zero(), "This building could not be saved. Nothing was created for it. Try again."))
    made = [i for i in items if i["status"] == "created"]
    broken = [i for i in items if i["status"] in ("failed", "changed")]
    outcome = "created" if made and not broken else "partial" if made else "failed" if broken else "already_set_up"
    return _report(outcome, digest, zone, items)


def _item(plan: dict[str, Any], state: str, created: dict[str, int], message: str, departments_set: int = 0) -> dict[str, Any]:
    return {"key": plan["key"], "id": plan["id"], "name": plan["name"], "status": state, "created": created,
            "skipped": plan["counts"]["skip"] if state != "created" else None, "message": message,
            "departments_set": departments_set}


def _report(outcome: str, digest: str, zone: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    total = _zero()
    for i in items:
        for k, v in i["created"].items():
            total[k] += v
    return {"version": VERSION, "outcome": outcome, "payload_hash": digest, "timezone": zone, "buildings": items,
            "totals": total, "departments_set": sum(i.get("departments_set", 0) for i in items), "agents_created": 0}
