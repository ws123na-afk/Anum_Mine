"""Office staffing: each office's agents against its blueprint (docs/ANUM_MINE_OFFICE_STAFFING.md).

* ``GET  /api/v1/office/staffing/blueprint``: the reviewed blueprint resolved against this deployment and you.
* ``GET  /api/v1/office/staffing/offices?q=&limit=&offset=``: every office (building) of the workspace with its
  coverage, agents and data sources. ``task:read``.
* ``GET  /api/v1/office/staffing/offices/{building_id}?lang=``: one office in full: per role the agents, what each
  lacks, the data sources and the apply actions, each with its own ``hash``. Nothing is stored.
* ``POST /api/v1/office/staffing/offices/{building_id}/apply``: ``workspace:manage``, ``input_mode: "click"`` and
  the actions the owner ticked with their hashes. A missing role becomes a PAUSED agent built by the governed
  create path (``agent_profiles.create_agent_governed``), claimed in the provisioning ledger
  (``office_agent_provisions``) so a retry never builds two, capped through agent controls and seated at a
  vacant desk of the office. An existing agent gets the missing skills, tools and rules through the governed
  edit path (``agent_profiles.update_agent_governed``): tools and skills are only added, autonomy and the cost
  cap only tightened. No tool above the role's allowed risk is ever attached.

PostgreSQL only (503 otherwise). The route functions are synchronous so the database work runs off the event loop.
"""
from __future__ import annotations

import hmac
from datetime import timedelta
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import agent_profiles as ap
from . import dependencies as deps
from . import office_provisioning as op
from . import office_staffing_blueprint as bp
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .office_scope import require_postgres, workspace_session
from .office_space_api import _audit as office_audit
from .office_space_api import _snap, _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext, new_id, utc_now
from .settings import settings

router = APIRouter(prefix="/api/v1/office/staffing", tags=["office staffing"])
VERSION = 1
CLAIM_STALE = timedelta(seconds=120)
MAX_ACTIONS = 20
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
BuildingId = Annotated[str, Path(min_length=1, max_length=80, pattern=ID_PATTERN)]
Lang = Annotated[Literal["en", "ar"], Query()]


class ApplyAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=120, pattern=r"^(create|update):[A-Za-z0-9._:-]+$")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Only a click on the confirm step applies. Voice and typed sentences are refused.
    input_mode: Literal["click", "typed", "voice"] = "typed"
    lang: Literal["en", "ar"] = "en"
    actions: list[ApplyAction] = Field(min_length=1, max_length=MAX_ACTIONS)


# --------------------------------------------------------------------------- reading


def _capabilities(context: TenantContext) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    registry = ap.runtime_tools()
    tool_info: dict[str, dict[str, Any]] = {}
    for role in bp.ROLES:
        for name in role.template.tools:
            definition = registry.definition(name)
            if definition is not None and name not in tool_info:
                tool_info[name] = {"risk": definition.risk_level.value,
                                   "category": ap.agent_tool_category(name, definition).value,
                                   "refusal": ap.tool_refusal(definition, context)}
    skill_info = {s.id: {"name": s.name, "source": s.source, "required_tools": s.required_tools}
                  for s in ap.catalog_skills(context)}
    return tool_info, skill_info


def _tool_risk(names: set[str]) -> dict[str, str | None]:
    registry = ap.runtime_tools()
    out: dict[str, str | None] = {}
    for name in names:
        definition = registry.definition(name)
        out[name] = definition.risk_level.value if definition is not None else None
    return out


def _source_states(session: Any, context: TenantContext, p: dict[str, str]) -> dict[str, str]:
    states: dict[str, str] = {}
    for source in bp.SOURCES:
        if not has_permission(context, Permission(source.permission)):
            states[source.id] = "no_access"
        elif source.kind == "module":
            states[source.id] = "available"
        elif source.kind == "setting":
            states[source.id] = "connected" if str(getattr(settings, source.setting or "", "") or "").strip() else "not_connected"
        else:
            found = False
            for table in source.tables:
                try:
                    with session.begin_nested():
                        found = session.execute(text(
                            f"select 1 from {table} where tenant_id = :t and workspace_id = :w limit 1"), p).first() is not None  # noqa: S608 (fixed names)
                except Exception:  # noqa: BLE001 - an unreadable optional source is Not connected, never an error
                    found = False
                if found:
                    break
            states[source.id] = "connected" if found else "not_connected"
    return states


def _load(context: TenantContext) -> dict[str, Any]:
    require_postgres()
    p = {"t": context.tenant_id, "w": context.workspace_id}
    with workspace_session(context) as session:
        buildings = [dict(r) for r in session.execute(text(
            "select id, name, name_ar from office_buildings where tenant_id = :t and workspace_id = :w order by name, id limit 500"),
            p).mappings()]
        rooms = [dict(r) for r in session.execute(text(
            "select r.id, r.name, r.kind, r.team_department, f.building_id from office_rooms r join office_floors f "
            "on f.tenant_id = r.tenant_id and f.workspace_id = r.workspace_id and f.id = r.floor_id "
            "where r.tenant_id = :t and r.workspace_id = :w order by r.id limit 5000"), p).mappings()]
        desks = [dict(r) for r in session.execute(text(
            "select id, room_id, label, agent_id, revision from office_desks where tenant_id = :t and workspace_id = :w "
            "order by id limit 10000"), p).mappings()]
        held: set[str] = set()
        try:
            with session.begin_nested():
                held = {r[0] for r in session.execute(text(
                    "select desk_id from office_desk_staffing where tenant_id = :t and workspace_id = :w "
                    "and status in ('keep_empty', 'claimed')"), p)}
        except Exception:  # noqa: BLE001 - the staffing ledger is optional for this read
            held = set()
        agents = [dict(r) for r in session.execute(text(
            "select id, name, status, department, template_id, allowed_tools, allowed_skills, autonomy, owner_user_id, "
            "sharing, version, knowledge_mode, knowledge_sources, position(:mark in instructions) > 0 as has_rules from agent_profiles "
            "where tenant_id = :t and workspace_id = :w order by id limit 1000"), {**p, "mark": bp.RULES_MARKER}).mappings()]
        ledger = {r.template_id: dict(r._mapping) for r in session.execute(text(
            "select template_id, status, agent_id, desk_id from office_agent_provisions "
            "where tenant_id = :t and workspace_id = :w and template_id like 'bp:%'"), p)}
        sources = _source_states(session, context, p)
    caps: dict[str, float | None] = {}
    from .agent_controls import open_agent_controls_store

    with open_agent_controls_store(context) as store:
        for cap in store.list_caps(context):
            caps[cap.agent_id] = cap.monthly_cost_limit_usd
    from .green_concrete_kit_api import gc_collection_id

    return {"now": utc_now(), "buildings": buildings, "rooms": rooms, "desks": desks, "held": held,
            "agents": agents, "ledger": ledger, "sources": sources, "caps": caps,
            "gc_collection": gc_collection_id(context)}


def _can_edit(row: dict[str, Any], context: TenantContext) -> bool:
    return ap.can_edit(SimpleNamespace(owner_user_id=row["owner_user_id"], sharing=row["sharing"]), context)  # type: ignore[arg-type]


def _office_agents(data: dict[str, Any], context: TenantContext, building_id: str) -> list[bp.OfficeAgent]:
    room_ids = {r["id"] for r in data["rooms"] if r["building_id"] == building_id}
    seat = {d["agent_id"]: d["id"] for d in data["desks"] if d["room_id"] in room_ids and d["agent_id"]}
    prefix = bp.marker(building_id, "")
    out = []
    for a in data["agents"]:
        if a["id"] not in seat and not (a["template_id"] or "").startswith(prefix):
            continue
        out.append(bp.OfficeAgent(
            id=a["id"], name=a["name"], status=a["status"], department=a["department"], template_id=a["template_id"],
            allowed_tools=tuple(a["allowed_tools"] or ()), allowed_skills=tuple(a["allowed_skills"] or ()),
            autonomy=dict(a["autonomy"] or {}), has_rules=bool(a["has_rules"]), cap_usd=data["caps"].get(a["id"]),
            can_edit=_can_edit(a, context), version=int(a["version"]), desk_id=seat.get(a["id"]),
            knowledge_mode=str(a.get("knowledge_mode") or "none"),
            knowledge_sources=tuple((str(k.get("type")), str(k.get("id"))) for k in (a.get("knowledge_sources") or []))))
    return out


def _name(building: dict[str, Any], lang: str) -> str:
    return (building.get("name_ar") if lang == "ar" else None) or building["name"]


def _placements(data: dict[str, Any], building_id: str, roles: list[bp.Role]) -> dict[str, dict[str, Any] | None]:
    """A vacant desk of this office per missing role: a room linked to the role's department first, then a room
    with no department. Desks the owner keeps empty, or that Staff my campus is filling, are never used."""
    rooms = {r["id"]: r for r in data["rooms"] if r["building_id"] == building_id and r["kind"] in op.WORK_ROOM_KINDS}
    vacant = sorted((d for d in data["desks"] if d["room_id"] in rooms and d["agent_id"] is None and d["id"] not in data["held"]),
                    key=lambda d: (rooms[d["room_id"]]["name"].lower(), d["id"]))
    used: set[str] = set()
    out: dict[str, dict[str, Any] | None] = {}
    for role in roles:
        wanted = set(role.room_departments) or ({role.template.room_department} if role.template.room_department else set())
        pick = next((d for d in vacant if d["id"] not in used and rooms[d["room_id"]].get("team_department") in wanted), None)
        if pick is None:
            pick = next((d for d in vacant if d["id"] not in used and not rooms[d["room_id"]].get("team_department")), None)
        if pick is None:
            out[role.key] = None
            continue
        used.add(pick["id"])
        out[role.key] = {"desk_id": pick["id"], "desk_label": pick["label"], "room_id": pick["room_id"],
                         "room_name": rooms[pick["room_id"]]["name"]}
    return out


def _office(data: dict[str, Any], context: TenantContext, building: dict[str, Any], lang: str,
            caps: tuple[dict[str, Any], dict[str, Any]] | None, detail: bool) -> dict[str, Any]:
    bid = building["id"]
    kind, units = bp.classify(bid, [r for r in data["rooms"] if r["building_id"] == bid])
    roles = bp.roles_for(units)
    tool_info, skill_info = caps or _capabilities(context)
    views = {r.key: bp.role_view(r, tool_info, skill_info) for r in roles}
    agents = _office_agents(data, context, bid)
    by_role, others = bp.match_agents(bid, roles, agents)
    risk = _tool_risk({t for a in agents for t in a.allowed_tools})
    source_ids = sorted({s for r in roles for s in r.sources}, key=[s.id for s in bp.SOURCES].index)
    sources = [{"id": s, "state": data["sources"][s], "kind": bp.SOURCE_BY_ID[s].kind,
                "name": {"en": bp.SOURCE_BY_ID[s].en, "ar": bp.SOURCE_BY_ID[s].ar}, "view": bp.SOURCE_BY_ID[s].view,
                "roles": [r.key for r in roles if s in r.sources]} for s in source_ids]
    role_items, needing = [], 0
    for role in roles:
        members = []
        for a in by_role[role.key]:
            gaps = bp.agent_gaps(role, views[role.key], a, risk, data["gc_collection"])
            needing += 0 if gaps["complete"] else 1
            members.append({"id": a.id, "name": a.name, "status": a.status, "desk_id": a.desk_id, "can_edit": a.can_edit,
                            "skills": list(a.allowed_skills), "tools": list(a.allowed_tools), **gaps})
        role_items.append({**views[role.key], "agents": members,
                           "state": "missing" if not members else ("needs_update" if any(not m["complete"] for m in members) else "staffed")})
    summary = {
        "building_id": bid, "name": {"en": building["name"], "ar": building.get("name_ar")},
        "kind": kind, "kind_name": {"en": bp.OFFICE_KIND_BY_ID[kind].en, "ar": bp.OFFICE_KIND_BY_ID[kind].ar},
        "units": [{"id": u, "name": {"en": bp.UNIT_BY_ID[u].en, "ar": bp.UNIT_BY_ID[u].ar}} for u in units],
        "roles_total": len(roles), "roles_staffed": sum(1 for r in role_items if r["agents"]),
        "missing_roles": [r["key"] for r in role_items if not r["agents"]],
        "agents_total": len(agents), "agents_active": sum(1 for a in agents if a.status == "active"),
        "agents_needing_update": needing,
        "sources_not_connected": sum(1 for s in sources if s["state"] == "not_connected"),
        "sources": [{"id": s["id"], "state": s["state"]} for s in sources],
    }
    if not detail:
        return summary
    actions = _actions(context, data, building, roles, views, by_role, lang)
    return {**summary, "roles": role_items, "sources": sources,
            "other_agents": [{"id": a.id, "name": a.name, "status": a.status, "desk_id": a.desk_id,
                              "skills": list(a.allowed_skills), "tools": list(a.allowed_tools)} for a in others],
            "actions": actions, "can_apply": has_permission(context, Permission.WORKSPACE_MANAGE),
            "limits": {"agents": len(data["agents"]), "max_agents": ap.MAX_AGENTS_PER_WORKSPACE}}


def _actions(context: TenantContext, data: dict[str, Any], building: dict[str, Any], roles: list[bp.Role],
             views: dict[str, dict[str, Any]], by_role: dict[str, list[bp.OfficeAgent]], lang: str) -> list[dict[str, Any]]:
    bid = building["id"]
    missing = [r for r in roles if not by_role[r.key]]
    places = _placements(data, bid, missing)
    out: list[dict[str, Any]] = []
    collection = data["gc_collection"]
    for role in missing:
        view, t = views[role.key], role.template
        mode, sources = (bp.with_collection(t.knowledge_mode, [], collection) if collection and role.knowledge_collection
                         else (t.knowledge_mode, []))
        name = f"{t.name_ar if lang == 'ar' else t.name_en}{'، ' if lang == 'ar' else ', '}{_name(building, lang)}"[:80]
        content = {
            "kind": "create", "role": role.key, "lang": lang, "status": "paused",
            "agent": {"name": name, "icon": t.icon, "color": t.color, "category": "work", "department": t.department,
                      "description": (role.purpose_ar if lang == "ar" else role.purpose_en)[:500],
                      "instructions": bp.instructions(role), "model_route": t.model_route,
                      "allowed_tools": view["attach_tools"], "allowed_skills": view["attach_skills"],
                      "autonomy": bp.autonomy(role), "knowledge_mode": mode, "knowledge_sources": sources,
                      "sharing": "workspace", "template_id": bp.marker(bid, role.key)},
            "monthly_cost_limit_usd": t.monthly_cost_limit_usd, "placement": places[role.key],
        }
        out.append({"id": f"create:{role.key}", "kind": "create", "role": role.key, "content": content,
                    "hash": bp.action_hash(context.tenant_id, context.workspace_id, context.user_id, bid, content)})
    for role in roles:
        view = views[role.key]
        for a in by_role[role.key]:
            gaps = bp.agent_gaps(role, view, a, {}, collection)
            if gaps["complete"] or not a.can_edit:
                continue
            content = {
                "kind": "update", "role": role.key, "agent_id": a.id, "version": a.version,
                "add_tools": gaps["missing_tools"], "add_skills": gaps["missing_skills"],
                "autonomy": bp.tightened(a.autonomy, role) if "autonomy" in gaps["missing_rules"] else None,
                "add_rules_text": "never_list" in gaps["missing_rules"],
                "monthly_cost_limit_usd": role.template.monthly_cost_limit_usd if "cost_cap" in gaps["missing_rules"] else None,
                "add_collection": collection if gaps["missing_knowledge"] else None,
            }
            out.append({"id": f"update:{a.id}", "kind": "update", "role": role.key, "agent_id": a.id, "content": content,
                        "hash": bp.action_hash(context.tenant_id, context.workspace_id, context.user_id, bid, content)})
    return out


def _building(data: dict[str, Any], building_id: str) -> dict[str, Any]:
    building = next((b for b in data["buildings"] if b["id"] == building_id), None)
    if building is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Office not found in this workspace")
    return building


# --------------------------------------------------------------------------- routes: reads


@router.get("/blueprint")
def get_blueprint(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    tool_info, skill_info = _capabilities(context)
    return {**bp.blueprint_document(), "roles": [bp.role_view(r, tool_info, skill_info) for r in bp.ROLES]}


@router.get("/offices")
def list_offices(q: Annotated[str | None, Query(max_length=100)] = None,
                 limit: Annotated[int, Query(ge=1, le=50)] = 12, offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
                 context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    data = _load(context)
    needle = (q or "").strip().lower()
    buildings = [b for b in data["buildings"]
                 if not needle or needle in b["name"].lower() or needle in (b.get("name_ar") or "").lower()]
    caps = _capabilities(context)
    page = buildings[offset: offset + limit]
    return {"version": VERSION, "blueprint_version": bp.BLUEPRINT_VERSION, "workspace_id": context.workspace_id,
            "as_of": data["now"].isoformat(), "total": len(buildings), "limit": limit, "offset": offset,
            "buildings_total": len(data["buildings"]),
            "items": [_office(data, context, b, "en", caps, detail=False) for b in page],
            "can_apply": has_permission(context, Permission.WORKSPACE_MANAGE)}


@router.get("/offices/{building_id}")
def get_office(building_id: BuildingId, lang: Lang = "en", context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    data = _load(context)
    return {"version": VERSION, "blueprint_version": bp.BLUEPRINT_VERSION, "as_of": data["now"].isoformat(), "lang": lang,
            **_office(data, context, _building(data, building_id), lang, None, detail=True)}


# --------------------------------------------------------------------------- routes: apply


def _claim(context: TenantContext, key: str, digest: str) -> dict[str, Any]:
    """Take the ledger row for (workspace, office role) in its own short transaction."""
    p = {"t": context.tenant_id, "w": context.workspace_id, "k": key}
    now = utc_now()
    with workspace_session(context, commit=True) as session:
        claimed = session.execute(text(
            "insert into office_agent_provisions (tenant_id, workspace_id, template_id, status, work_kinds, proposal_hash, "
            "requested_by, created_at, updated_at) values (:t, :w, :k, 'claimed', '[]'::jsonb, :h, :u, :n, :n) "
            "on conflict (tenant_id, workspace_id, template_id) do nothing returning template_id"),
            {**p, "h": digest, "u": context.user_id, "n": now}).first()
        if claimed is not None:
            return {"state": "new", "agent_id": None}
        row = session.execute(text(
            "select status, agent_id, updated_at from office_agent_provisions "
            "where tenant_id = :t and workspace_id = :w and template_id = :k for update"), p).mappings().one()
        alive = bool(row["agent_id"]) and session.execute(text(
            "select 1 from agent_profiles where tenant_id = :t and workspace_id = :w and id = :a"),
            {**p, "a": row["agent_id"]}).first() is not None
        if row["status"] == "active" and alive:
            return {"state": "done", "agent_id": row["agent_id"]}
        if row["status"] == "claimed" and now - row["updated_at"] < CLAIM_STALE:
            return {"state": "busy", "agent_id": None}
        session.execute(text(
            "update office_agent_provisions set status = 'claimed', proposal_hash = :h, requested_by = :u, updated_at = :n, "
            "agent_id = cast(:a as varchar), desk_id = null where tenant_id = :t and workspace_id = :w and template_id = :k"),
            {**p, "h": digest, "u": context.user_id, "n": now, "a": row["agent_id"] if alive else None})
        return {"state": "resume" if alive else "new", "agent_id": row["agent_id"] if alive else None}


def _adopt(context: TenantContext, key: str) -> str | None:
    with workspace_session(context) as session:
        return session.execute(text(
            "select id from agent_profiles where tenant_id = :t and workspace_id = :w and template_id = :k "
            "order by created_at, id limit 1"), {"t": context.tenant_id, "w": context.workspace_id, "k": key}).scalar()


def _set_cap(context: TenantContext, agent_id: str, limit_usd: float) -> None:
    from .agent_controls import store_in

    with deps._unit_of_work(context, require_membership=True) as repository:
        store = store_in(repository)
        current = store.get_cap(context, agent_id)
        tokens = current.monthly_token_limit if current else None
        if current and current.monthly_cost_limit_usd is not None and current.monthly_cost_limit_usd <= limit_usd:
            return
        store.set_cap(context, agent_id, limit_usd, tokens, utc_now())
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="agent_controls.agent_cap.update", target=f"agent:{agent_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata={"limits": f"cost {limit_usd} USD per month", "source": "office_staffing"}))


def _seat(context: TenantContext, key: str, building_id: str, role: str, agent_id: str,
          placement: dict[str, Any] | None) -> str | None:
    """Seat the agent at the planned desk (if it is still vacant), close the ledger row and emit; one transaction."""
    from .db.repository import SqlAlchemyRepository

    desk_id = None
    with _store(context) as store:
        if placement is not None:
            desk = store.get(context, "desks", placement["desk_id"])
            if desk is not None and desk["agent_id"] in (None, agent_id):
                if desk["agent_id"] is None:
                    try:
                        with store.session.begin_nested():
                            after = store.assignment(context, desk["id"], agent_id, desk["revision"])
                        office_audit(store, context, "office.desk.assignment", desk["id"], kind="desks",
                                     before=_snap("desks", desk), after=_snap("desks", after))
                        desk_id = desk["id"]
                    except SpaceStoreError:
                        desk_id = None
                else:
                    desk_id = desk["id"]
        store.session.execute(text(
            "update office_agent_provisions set status = 'active', agent_id = :a, desk_id = :d, updated_at = :n "
            "where tenant_id = :t and workspace_id = :w and template_id = :k"),
            {"t": context.tenant_id, "w": context.workspace_id, "k": key, "a": agent_id, "d": desk_id, "n": utc_now()})
        repository = SqlAlchemyRepository(store.session)
        payload = {"template_id": bp.ROLE_BY_KEY[role].template_id, "agent_id": agent_id, "desk_id": desk_id,
                   "room_id": placement["room_id"] if placement and desk_id else None, "building_id": building_id,
                   "role": role, "limited": False}
        event = create_event(CanonicalEventName.OFFICE_AGENT_PROVISIONED, context, agent_id, payload).event
        store.pending_events.append(repository.record_event(event))  # type: ignore[attr-defined]
    return desk_id


def _run_create(context: TenantContext, building_id: str, action: dict[str, Any]) -> dict[str, Any]:
    content = action["content"]
    key = content["agent"]["template_id"]
    claim = _claim(context, key, action["hash"])
    if claim["state"] == "done":
        return {"outcome": "already_done", "agent_id": claim["agent_id"]}
    if claim["state"] == "busy":
        return {"outcome": "being_built", "detail": "This role is already being built. Reload in a few seconds."}
    agent_id = claim["agent_id"] or _adopt(context, key)
    outcome = "resumed" if agent_id else "created"
    if agent_id is None:
        try:
            agent_id = ap.create_agent_governed(ap.AgentProfileCreate(**content["agent"]), context, ap.AgentStatus.PAUSED).id
        except HTTPException as exc:
            _release(context, key)
            return {"outcome": "failed", "detail": str(exc.detail)}
    with workspace_session(context, commit=True) as session:
        session.execute(text(
            "update office_agent_provisions set agent_id = :a, updated_at = :n where tenant_id = :t and workspace_id = :w "
            "and template_id = :k"), {"t": context.tenant_id, "w": context.workspace_id, "k": key, "a": agent_id, "n": utc_now()})
    _set_cap(context, agent_id, float(content["monthly_cost_limit_usd"]))
    desk_id = _seat(context, key, building_id, content["role"], agent_id, content["placement"])
    return {"outcome": outcome, "agent_id": agent_id, "desk_id": desk_id, "seated": desk_id is not None, "paused": True}


def _release(context: TenantContext, key: str) -> None:
    with workspace_session(context, commit=True) as session:
        session.execute(text(
            "delete from office_agent_provisions where tenant_id = :t and workspace_id = :w and template_id = :k "
            "and status = 'claimed' and agent_id is null"), {"t": context.tenant_id, "w": context.workspace_id, "k": key})


def _run_update(context: TenantContext, action: dict[str, Any]) -> dict[str, Any]:
    content = action["content"]
    agent = ap.load_agent(context, content["agent_id"])
    if agent is None:
        return {"outcome": "failed", "detail": "The agent is gone or not visible to you."}
    role = bp.ROLE_BY_KEY[content["role"]]
    changes: dict[str, Any] = {"version": content["version"]}
    if content["add_tools"]:
        changes["allowed_tools"] = sorted(set(agent.allowed_tools) | set(content["add_tools"]))
    if content["add_skills"]:
        changes["allowed_skills"] = sorted(set(agent.allowed_skills) | set(content["add_skills"]))
    if content["autonomy"]:
        changes["autonomy"] = bp.tightened(agent.autonomy.model_dump(mode="json"), role)
    if content.get("add_collection"):
        mode, sources = bp.with_collection(agent.knowledge_mode.value, [k.model_dump() for k in agent.knowledge_sources],
                                           content["add_collection"])
        changes["knowledge_mode"], changes["knowledge_sources"] = mode, sources
    if content["add_rules_text"] and bp.RULES_MARKER not in agent.instructions:
        changes["instructions"] = f"{agent.instructions}\n\n{bp.rules_text(role)}".strip()[:8000]
    if len(changes) > 1:
        try:
            ap.update_agent_governed(agent.id, ap.AgentProfileUpdate(**changes), context)
        except HTTPException as exc:
            return {"outcome": "failed", "detail": str(exc.detail)}
    if content["monthly_cost_limit_usd"] is not None:
        _set_cap(context, agent.id, float(content["monthly_cost_limit_usd"]))
    return {"outcome": "updated", "agent_id": agent.id,
            "added": {"tools": content["add_tools"], "skills": content["add_skills"],
                      "knowledge": bool(content.get("add_collection")),
                      "rules": [r for r, on in (("autonomy", bool(content["autonomy"])), ("never_list", content["add_rules_text"]),
                                                ("cost_cap", content["monthly_cost_limit_usd"] is not None)) if on]}}


def _satisfied(office: dict[str, Any], action_id: str) -> bool:
    kind, _, ref = action_id.partition(":")
    if kind == "create":
        role = next((r for r in office["roles"] if r["key"] == ref), None)
        return role is not None and bool(role["agents"])
    for role in office["roles"]:
        for a in role["agents"]:
            if a["id"] == ref:
                return bool(a["complete"])
    return False


@router.post("/offices/{building_id}/apply")
def apply_office(building_id: BuildingId, body: ApplyBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    if body.input_mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Confirm this on screen with a click. A spoken or typed answer cannot apply it.")
    ids = [a.id for a in body.actions]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Each action may be listed once")
    data = _load(context)
    building = _building(data, building_id)
    office = _office(data, context, building, body.lang, None, detail=True)
    plan = {a["id"]: a for a in office["actions"]}
    results: list[dict[str, Any]] = []
    for item in body.actions:
        action = plan.get(item.id)
        if action is None:
            results.append({"id": item.id, **({"outcome": "already_done"} if _satisfied(office, item.id)
                                              else {"outcome": "stale", "detail": "Review the office again: this change is no longer planned."})})
            continue
        if not hmac.compare_digest(item.hash, action["hash"]):
            results.append({"id": item.id, "outcome": "stale",
                            "detail": "What this change would do has changed since you opened it. Review it again."})
            continue
        run = _run_create(context, building_id, action) if action["kind"] == "create" else _run_update(context, action)
        results.append({"id": item.id, **run})
    if all(r["outcome"] == "stale" for r in results):
        raise HTTPException(status.HTTP_409_CONFLICT, "The office changed since you opened it. Review it again.")
    with deps._unit_of_work(context, require_membership=True) as repository:
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="office.staffing.applied", target=f"building:{building_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata={"building_id": building_id, "blueprint_version": bp.BLUEPRINT_VERSION, "input_mode": "click",
                      "results": [{k: r.get(k) for k in ("id", "outcome", "agent_id", "desk_id")} for r in results]}))
    return {"version": VERSION, "building_id": building_id, "results": results,
            "created": sum(1 for r in results if r["outcome"] in ("created", "resumed")),
            "updated": sum(1 for r in results if r["outcome"] == "updated")}
