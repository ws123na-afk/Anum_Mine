"""Live flow, stuck work, item trails and office teams for the ANUM MINE headquarters.

* ``GET /api/v1/office/flow``: every open (and just finished) work item that a persisted record
  places in the workspace, where it sits (office, floor, building) and whether it is stuck, with
  the rule, the age and the next action (office_flow.py). Sources that are not connected say so.
* ``GET /api/v1/office/flow/items/{kind}/{item_id}/trail``: the stored events of one item.
* ``GET /api/v1/office/team``: the real agents at the desks of a room, floor, building or the
  whole workspace, with role, state, current work, stuck reason and skills.

Live updates reuse ``GET /api/v1/office/presence/stream`` and the domain event stream: an event
only tells the browser to read ``/flow`` again; the state itself always comes from the records.
Nothing here joins workspaces or tenants; every read is one RLS-scoped session.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text

from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_flow import (
    MAX_ITEMS,
    FlowDesk,
    build_team,
    derive_flow,
    load_flow_evidence,
    thresholds_from_settings,
)
from .office_presence import derive_presence, load_evidence
from .office_scope import require_postgres, workspace_session
from .schemas import TenantContext, utc_now
from .settings import settings

router = APIRouter(prefix="/api/v1/office", tags=["office flow"])
FLOW_VERSION = 1
TEAM_SKILLS_MAX_AGENTS = 30
UNASSIGNED_LISTED = 50
IdPart = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
SAFE_EVENT_KEYS = ("agent_id", "status", "approval_id", "run_id", "task_id", "decision")


def _flow_for(context: TenantContext) -> tuple[dict[str, Any], Any]:
    """Blocking: one short session. Returns (flow answer parts, evidence)."""
    now = utc_now()
    th = thresholds_from_settings(settings)
    with workspace_session(context) as session:
        evidence = load_flow_evidence(
            session, context, now, th,
            can_leads=has_permission(context, Permission.WHATSAPP_READ),
            can_orders=has_permission(context, Permission.COMMERCE_READ),
            can_approvals=has_permission(context, Permission.APPROVAL_READ),
        )
    derived = derive_flow(evidence, th)
    return {
        "version": FLOW_VERSION,
        "workspace_id": context.workspace_id,
        "as_of": now.isoformat(),
        "cursor": evidence.cursor,
        "thresholds": th.as_json(),
        "sources": evidence.sources,
        **derived,
    }, evidence


def flow_snapshot(context: TenantContext) -> dict[str, Any]:
    return _flow_for(context)[0]


@router.get("/flow")
def get_flow(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    return flow_snapshot(context)


def _safe_payload(payload: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in SAFE_EVENT_KEYS:
        value = (payload or {}).get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            out[key] = str(value)[:80]
    return out


@router.get("/flow/items/{kind}/{item_id}/trail")
def get_trail(
    kind: Literal["task", "lead", "order"], item_id: IdPart,
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    p = {"t": context.tenant_id, "w": context.workspace_id, "i": item_id}
    steps: list[dict[str, Any]] = []
    source = "connected"
    if kind == "lead":
        require_permission(context, Permission.WHATSAPP_READ)
    if kind == "order":
        require_permission(context, Permission.COMMERCE_READ)
    with workspace_session(context) as session:
        if kind == "task":
            exists = session.execute(text(
                "select 1 from tasks where tenant_id = :t and workspace_id = :w and id = :i"), p).first()
            if exists is None:
                raise HTTPException(404, "Item not found")
            for r in session.execute(text(
                "select id, type, created_at, payload from domain_events "
                "where tenant_id = :t and workspace_id = :w and (subject = :i or payload->>'task_id' = :i) "
                "order by created_at, id limit 100"), p):
                steps.append({"id": r.id, "type": r.type, "at": r.created_at.isoformat(), "data": _safe_payload(r.payload)})
        elif kind == "lead":
            try:
                with session.begin_nested():
                    rows = session.execute(text(
                        "select id, event, from_status, to_status, occurred_at from sales_lead_events "
                        "where tenant_id = :t and workspace_id = :w and lead_id = :i "
                        "order by occurred_at, revision limit 100"), p).all()
            except Exception:  # noqa: BLE001
                rows, source = [], "not_connected"
            for r in rows:
                data = {k: v for k, v in (("from_status", r.from_status), ("to_status", r.to_status)) if v}
                steps.append({"id": r.id, "type": r.event, "at": r.occurred_at.isoformat(), "data": data})
        else:
            source = "not_recorded"  # synced orders carry no local event history
    return {"version": FLOW_VERSION, "kind": kind, "item_id": item_id, "source": source, "steps": steps}


def _scope_desks(desks: list[FlowDesk], building_id: str | None, floor_id: str | None, room_id: str | None) -> list[FlowDesk]:
    out = desks
    if building_id:
        out = [d for d in out if d.building_id == building_id]
    if floor_id:
        out = [d for d in out if d.floor_id == floor_id]
    if room_id:
        out = [d for d in out if d.room_id == room_id]
    return out


@router.get("/team")
def get_team(
    building_id: Annotated[str | None, Query(max_length=80)] = None,
    floor_id: Annotated[str | None, Query(max_length=80)] = None,
    room_id: Annotated[str | None, Query(max_length=80)] = None,
    with_skills: bool = True,
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    """The team of an office. Without a filter it is the whole workspace; ``room_id`` is one office."""
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    from .agent_profiles import open_agent_store
    from .agent_skill_loadout import skill_states

    now = utc_now()
    th = thresholds_from_settings(settings)
    with workspace_session(context) as session:
        flow_evidence = load_flow_evidence(
            session, context, now, th, can_leads=False, can_orders=False,
            can_approvals=has_permission(context, Permission.APPROVAL_READ))
        presence_evidence = load_evidence(session, context, now)
        if room_id and not any(d.room_id == room_id for d in flow_evidence.desks):
            exists = session.execute(text(
                "select 1 from office_rooms where tenant_id = :t and workspace_id = :w and id = :r"),
                {"t": context.tenant_id, "w": context.workspace_id, "r": room_id}).first()
            if exists is None:
                raise HTTPException(404, "Room not found")
    derived = derive_flow(flow_evidence, th)
    with open_agent_store(context) as store:
        profiles = store.list(context)
    presence = {i["agent_id"]: i for i in derive_presence(presence_evidence) if i["agent_id"]}
    stuck_by_agent: dict[str, dict[str, Any]] = {}
    for item in derived["items"]:
        loc, stuck = item["location"], item["stuck"]
        if loc and loc["agent_id"] and stuck and loc["agent_id"] not in stuck_by_agent:
            stuck_by_agent[loc["agent_id"]] = {**stuck, "item_key": item["key"], "title": item["title"]}
    scoped = _scope_desks(flow_evidence.desks, building_id, floor_id, room_id)
    skills: dict[str, list[dict[str, Any]]] | None = None
    skills_included = False
    assigned_ids = [d.agent_id for d in scoped if d.agent_id]
    visible = {p.id: p for p in profiles}
    if with_skills and 0 < len(assigned_ids) <= TEAM_SKILLS_MAX_AGENTS:
        skills = {}
        for agent_id in assigned_ids:
            profile = visible.get(agent_id)
            if profile is None:
                continue
            states, _ = skill_states(profile, context)
            skills[agent_id] = [
                {"id": s.id, "name": s.name, "state": s.state, "reason": s.reason, "source": s.source}
                for s in states
            ]
        skills_included = True
    team = build_team(profiles=profiles, desks=scoped, all_desks_in_scope=flow_evidence.desks,
                      presence=presence, stuck_by_agent=stuck_by_agent, skills=skills)
    desk_agents = {d.agent_id for d in flow_evidence.desks if d.agent_id}
    unassigned = [
        {"agent_id": p.id, "name": p.name, "department": p.department, "status": str(p.status.value)}
        for p in sorted(profiles, key=lambda x: (x.name.lower(), x.id)) if p.id not in desk_agents
    ]
    return {
        "version": FLOW_VERSION,
        "workspace_id": context.workspace_id,
        "as_of": now.isoformat(),
        "scope": {"building_id": building_id, "floor_id": floor_id, "room_id": room_id},
        "skills_included": skills_included,
        **team,
        "unassigned_agents": unassigned[:UNASSIGNED_LISTED],
        "unassigned_total": len(unassigned),
        "limits": {"items": MAX_ITEMS, "skills_agents": TEAM_SKILLS_MAX_AGENTS},
    }
