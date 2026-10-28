"""Owner-triggered "Add Green Concrete" (docs/ANUM_MINE_GREEN_CONCRETE.md), modelled on the starter campus.

* ``GET  /api/v1/office/green-concrete/preview``: the building, floor, command room, seats and people a confirm WILL
  create, and what exists and is skipped, with a ``payload_hash``. Nothing is stored. ``task:read``.
* ``POST /api/v1/office/green-concrete/confirm``: needs ``workspace:manage``, the hash you were shown and
  ``input_mode: "click"``. A spoken or typed confirmation can never confirm, and no registered tool calls it.

Everything happens inside the caller's own tenant and workspace under row level security. The records are created
through the builder's own code paths, matched by stable ids, never overwritten, under the per-workspace writer lock of
the starter campus. The people become reviewed PAUSED agents through ``agent_profiles.create_agent_governed`` (the body of
``POST /agents``): nothing is started, no tool is attached and nobody is working because of this action.
"""
from __future__ import annotations

import hmac
import logging
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import agent_profiles as ap
from . import office_green_concrete as gc
from .authorization import Permission
from .avatar_appearance import AVATAR_STYLES, AvatarAppearance, AvatarStyle
from .dependencies import has_permission, require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .office_scope import require_postgres, workspace_session
from .office_space_api import _audit as office_audit
from .office_space_api import _snap, _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/office/green-concrete", tags=["office green concrete"])
VERSION = 1
MAX_LOOKS = len(gc.PEOPLE) + 1


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Only a click on the review dialog confirms. Voice and typed sentences are refused.
    input_mode: Literal["click", "typed", "voice"] = "typed"
    timezone: str = Field(min_length=3, max_length=80)
    # The look (masculine, feminine, neutral) the owner picked for each person; it is part of the hash. Optional: nothing sent
    # means no look is stored (the scene draws the default from the id). ``default_look`` covers the people not listed.
    default_look: AvatarStyle | None = None
    appearance: dict[str, AvatarStyle] = Field(default_factory=dict, max_length=len(gc.PEOPLE))


def _looks(default: str | None, chosen: dict[str, str] | None) -> dict[str, str]:
    try:
        return gc.clean_appearance(default, chosen)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _look_params(values: list[str]) -> dict[str, str]:
    """``key:style`` pairs of the preview query. A repeated key or a malformed pair is refused."""
    out: dict[str, str] = {}
    for item in values[:MAX_LOOKS]:
        key, _, style = item.partition(":")
        if not key or not style or key in out or style not in AVATAR_STYLES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Each look is person:masculine, person:feminine or person:neutral")
        out[key] = style
    return out


def _zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid IANA time zone") from exc
    return value


def load_facts(session: Any, context: TenantContext) -> gc.Facts:
    p = {"t": context.tenant_id, "w": context.workspace_id}
    facts = gc.Facts()
    b = session.execute(text(
        "select id, name, timezone, shift_day_start, shift_night_start from office_buildings "
        "where tenant_id = :t and workspace_id = :w and id = :id"), {**p, "id": gc.BUILDING_ID}).mappings().first()
    facts.building = dict(b) if b else None
    facts.other_building_with_name = session.execute(text(
        "select 1 from office_buildings where tenant_id = :t and workspace_id = :w and id <> :id "
        "and lower(btrim(name)) = lower(:n) limit 1"), {**p, "id": gc.BUILDING_ID, "n": gc.BUILDING_NAME}).first() is not None
    f = session.execute(text(
        "select id, width_m, depth_m from office_floors where tenant_id = :t and workspace_id = :w and id = :id"),
        {**p, "id": gc.FLOOR_ID}).mappings().first()
    facts.floor = dict(f) if f else None
    facts.level_taken = session.execute(text(
        "select 1 from office_floors where tenant_id = :t and workspace_id = :w and building_id = :b and level = 0 and id <> :id limit 1"),
        {**p, "b": gc.BUILDING_ID, "id": gc.FLOOR_ID}).first() is not None
    r = session.execute(text(
        "select id, name from office_rooms where tenant_id = :t and workspace_id = :w and id = :id"),
        {**p, "id": gc.ROOM_ID}).mappings().first()
    facts.room = dict(r) if r else None
    facts.room_name_taken = session.execute(text(
        "select 1 from office_rooms where tenant_id = :t and workspace_id = :w and floor_id = :f and name = :n and id <> :id limit 1"),
        {**p, "f": gc.FLOOR_ID, "n": gc.ROOM_NAME, "id": gc.ROOM_ID}).first() is not None
    ids = [gc.desk_id(i + 1) for i in range(len(gc.PEOPLE))]
    facts.desks = {row["id"]: dict(row) for row in session.execute(text(
        "select id, agent_id, revision from office_desks where tenant_id = :t and workspace_id = :w and id = any(cast(:d as text[]))"),
        {**p, "d": ids}).mappings()}
    facts.agents = {row["template_id"]: row["id"] for row in session.execute(text(
        "select id, template_id from agent_profiles where tenant_id = :t and workspace_id = :w and template_id like 'gc:%' order by id desc"),
        p).mappings()}
    facts.agent_desk = {row["agent_id"]: row["id"] for row in session.execute(text(
        "select id, agent_id from office_desks where tenant_id = :t and workspace_id = :w and agent_id is not null"), p).mappings()}
    facts.agent_count = session.execute(text(
        "select count(*) from agent_profiles where tenant_id = :t and workspace_id = :w"), p).scalar_one()
    return facts


def _default_zone(session: Any, context: TenantContext, facts: gc.Facts) -> str:
    if facts.building:
        return facts.building["timezone"]
    row = session.execute(text(
        "select timezone from office_buildings where tenant_id = :t and workspace_id = :w order by created_at, id limit 1"),
        {"t": context.tenant_id, "w": context.workspace_id}).first()
    return row[0] if row else gc.DEFAULT_TIMEZONE


def _read(context: TenantContext, zone: str | None) -> tuple[str, gc.Facts]:
    require_postgres()
    with workspace_session(context) as session:
        facts = load_facts(session, context)
        return (zone or _default_zone(session, context, facts)), facts


def _shifts(facts: gc.Facts) -> dict[str, Any]:
    day, night, default = gc.valid_hours(*(facts.building and (facts.building["shift_day_start"], facts.building["shift_night_start"]) or (None, None)))
    return {"day": {"start": day, "end": night}, "night": {"start": night, "end": day}, "defaults": default,
            "note": "These hours are defaults the owner must confirm. Change them in the builder (building shift hours)."}


@router.get("/preview")
def preview(timezone: str | None = Query(default=None, max_length=80),
            default_look: AvatarStyle | None = Query(default=None),
            look: list[str] = Query(default_factory=list, max_length=MAX_LOOKS),
            context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    zone, facts = _read(context, _zone(timezone) if timezone else None)
    p = gc.plan(facts, zone, _looks(default_look, _look_params(look)))
    return {
        "version": VERSION, "catalog_version": gc.CATALOG_VERSION, "workspace_id": context.workspace_id, "timezone": zone,
        "payload_hash": gc.proposal_hash(context.tenant_id, context.workspace_id, context.user_id, zone, p),
        "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE),
        **p, "shifts": _shifts(facts), "monthly_cap_usd": gc.MONTHLY_CAP_USD,
        "rules": {"creates_agents": True, "agents_start_paused": True, "attaches_tools": False, "changes_existing": False,
                  "infers_gender_or_ethnicity": False},
    }


def _agent_payload(person: gc.Person, look: str | None = None) -> ap.AgentProfileCreate:
    return ap.AgentProfileCreate(
        name=person.name, icon="user", color="#2f6f4f", category="work", department=person.role_en,
        description=f"{person.role_en} at Green Concrete. Created paused by the owner's Add Green Concrete action.",
        instructions=(f"You are {person.name}, {person.role_en} at Green Concrete. No tools are attached yet: say plainly when you "
                      "lack a tool or data for a job, never invent figures, and ask a person when a step needs approval."),
        model_route="fast", allowed_skills=[], allowed_tools=[],
        autonomy={"read": "auto", "write": "off", "external": "off"},
        knowledge_mode="none", knowledge_sources=[], sharing="workspace", template_id=gc.marker(person),
        avatar=AvatarAppearance(avatar_style=look) if look in AVATAR_STYLES else None,
    )


_REFUSED = {
    "ROOM_NAME_TAKEN": "A room with the same name already exists on the floor. Nothing was created.",
    "FLOOR_LEVEL_TAKEN": "Another floor already uses this level. Nothing was created.",
    "ROOM_OVERLAP": "The room would overlap another room. Nothing was created.",
    "ROOM_OUT_OF_BOUNDS": "The room would fall outside its floor. Nothing was created.",
}


def _zero() -> dict[str, int]:
    return {"buildings": 0, "floors": 0, "rooms": 0, "desks": 0, "agents": 0, "seated": 0}


def _report(outcome: str, digest: str, zone: str, created: dict[str, int], message: str, failed: list[str] | None = None) -> dict[str, Any]:
    return {"version": VERSION, "outcome": outcome, "payload_hash": digest, "timezone": zone, "created": created,
            "message": message, "failed": failed or [], "agents_started": 0}


@router.post("/confirm")
def confirm(body: ConfirmBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    if body.input_mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
    zone = _zone(body.timezone)
    try:
        return _apply(context, zone, body.payload_hash, _looks(body.default_look, dict(body.appearance)))
    except SpaceStoreError as exc:
        logger.warning("green concrete refused", extra={"anum_reason": exc.reason or exc.code})
        raise HTTPException(status.HTTP_409_CONFLICT, _REFUSED.get(exc.reason or "", "A record conflicts with one that already exists. Nothing was created.")) from None


def _apply(context: TenantContext, zone: str, expected: str, looks: dict[str, str]) -> dict[str, Any]:
    from .db.repository import SqlAlchemyRepository
    from .office_staff_desks_api import _set_caps

    with _store(context) as store:
        store.session.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                              {"k": f"{context.tenant_id}:{context.workspace_id}:starter-campus"})
        facts = load_facts(store.session, context)
        plan = gc.plan(facts, zone, looks)
        digest = gc.proposal_hash(context.tenant_id, context.workspace_id, context.user_id, zone, plan)
        if not gc.has_work(plan):
            return _report("already_set_up", digest, zone, _zero(), "Already set up. Nothing was changed.")
        if not hmac.compare_digest(expected, digest):
            raise HTTPException(status.HTTP_409_CONFLICT, "What would be created has changed since you opened it. Review the preview again.")
        created = _zero()
        ids: list[str] = []
        snaps: dict[str, Any] = {"building": None, "floor": None, "room": None, "desks": []}
        if plan["building"]["action"] == "create":
            row = store.create(context, "buildings", gc.BUILDING_ID, {"name": gc.BUILDING_NAME, "timezone": zone, "latitude": None, "longitude": None})
            created["buildings"] += 1
            ids.append(gc.BUILDING_ID)
            snaps["building"] = _snap("buildings", row)
        if plan["floor"]["action"] == "create":
            row = store.create(context, "floors", gc.FLOOR_ID, {"building_id": gc.BUILDING_ID, "level": 0, "name": gc.FLOOR_NAME,
                                                                  "width_m": gc.FLOOR_W, "depth_m": gc.FLOOR_D})
            created["floors"] += 1
            ids.append(gc.FLOOR_ID)
            snaps["floor"] = _snap("floors", row)
        if plan["room"]["action"] == "create":
            desks = [(s["desk_id"], s["label"], (s["pos_x"], s["pos_z"]),
                      {k: s[k] for k in ("shift", "seat_order", "facing", "seat_kind", "table_group")}) for s in plan["seats"]]
            values = {"name": gc.ROOM_NAME, "kind": "command", "capacity": gc.ROOM_CAPACITY, "door_side": "south", "color": "forest",
                      "rotation": 0, "equipment": [{"kind": "monitor", "quantity": 10}, {"kind": "display", "quantity": 4},
                                                   {"kind": "network_switch", "quantity": 1}], "team_department": None}
            row, made = store.create_planned_room(context, gc.FLOOR_ID, gc.ROOM_ID, values, gc.ROOM_X, gc.ROOM_Z, gc.ROOM_W, gc.ROOM_D, desks)
            created["rooms"] += 1
            created["desks"] += len(made)
            ids.extend([gc.ROOM_ID, *[d["id"] for d in made]])
            snaps["room"] = _snap("rooms", row)
        else:
            # The room exists: only desks of the master list that are missing are added (an existing desk is never touched).
            for s in plan["seats"]:
                if s["action"] == "create":
                    made = store._insert_desks(context, gc.ROOM_ID, [(s["desk_id"], s["label"], (s["pos_x"], s["pos_z"]),
                                                {k: s[k] for k in ("shift", "seat_order", "facing", "seat_kind", "table_group")})])
                    created["desks"] += 1
                    ids.append(s["desk_id"])
                    snaps["desks"].extend(d["id"] for d in made)
        # Agents: reviewed, paused, no tools. Each is built by the governed create path (its own unit of work); one that
        # carries the marker already is adopted, never built twice.
        agent_ids: dict[str, str] = dict(facts.agents or {})
        failed: list[str] = []
        new_agents: list[tuple[str, float]] = []
        for person in gc.PEOPLE:
            if gc.marker(person) in agent_ids:
                continue
            try:
                agent_ids[gc.marker(person)] = ap.create_agent_governed(_agent_payload(person, looks.get(person.key)), context, ap.AgentStatus.PAUSED).id
                new_agents.append((agent_ids[gc.marker(person)], gc.MONTHLY_CAP_USD))
                created["agents"] += 1
            except HTTPException as exc:
                logger.warning("green concrete agent refused", extra={"anum_status": exc.status_code})
                failed.append(person.key)
        if new_agents:
            _set_caps(context, new_agents)
        # Seat each person at their own desk: only a vacant desk, only an agent that sits nowhere else.
        seated_now: list[str] = []
        for person in gc.PEOPLE:
            agent_id = agent_ids.get(gc.marker(person))
            did = gc.desk_id(gc.desk_index(person))
            if agent_id is None:
                continue
            desk = store.get(context, "desks", did)
            if desk is None or desk["agent_id"] not in (None,) or (facts.agent_desk or {}).get(agent_id) not in (None, did):
                continue
            store.assignment(context, did, agent_id, desk["revision"])
            created["seated"] += 1
            seated_now.append(did)
        ids.extend(seated_now)
        office_audit(store, context, "office.green_concrete.add", gc.BUILDING_ID, kind="buildings", ids=ids,
                     before={"existing": [x for x in (gc.BUILDING_ID, gc.FLOOR_ID, gc.ROOM_ID) if x not in ids]},
                     after={"created": created, **snaps, "payload_hash": digest, "input_mode": "click", "paused": True,
                            "catalog_version": gc.CATALOG_VERSION, "looks_chosen": len(looks)})
        repository = SqlAlchemyRepository(store.session)
        for person in gc.PEOPLE:
            agent_id = agent_ids.get(gc.marker(person))
            did = gc.desk_id(gc.desk_index(person))
            if agent_id and did in seated_now:
                event = create_event(CanonicalEventName.OFFICE_AGENT_PROVISIONED, context, agent_id,
                                     {"template_id": gc.marker(person), "agent_id": agent_id, "desk_id": did,
                                      "room_id": gc.ROOM_ID, "limited": True}).event
                store.pending_events.append(repository.record_event(event))
        outcome = "created" if not failed else "partial"
        message = "Created." if not failed else "Some people could not be created. Review the preview and try again."
        return _report(outcome, digest, zone, created, message, failed)
