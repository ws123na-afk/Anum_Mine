"""Governed agent provisioning for the ANUM MINE headquarters (docs/ANUM_MINE_AGENT_PROVISIONING.md).

* ``GET  /api/v1/office/provisioning/gaps``: kinds of work and departments nobody handles, from stored
  records only, plus per-role coverage. ``task:read``.
* ``GET  /api/v1/office/provisioning/templates``: the catalogue resolved against this deployment and you.
* ``GET  /api/v1/office/provisioning/proposals/{template_id}``: exactly what confirming would create, with
  a ``payload_hash``. Nothing is stored; a proposal is a pure function of the records and you.
* ``POST /api/v1/office/provisioning/proposals/{template_id}/confirm``: needs ``workspace:manage``, the hash
  you were shown and ``input_mode: "click"``. A spoken or typed confirmation can never confirm, and no
  registered tool calls this route. The agent is built by the same governed path as ``POST /agents``, is
  created paused, gets only registry tools and skills, is seated at a desk and recorded in the ledger.

Every read is one RLS scoped session. The route functions are synchronous so the database work runs off the
event loop.
"""
from __future__ import annotations

import hmac
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import agent_profiles as ap
from . import dependencies as deps
from . import office_provisioning as op
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .office_flow import derive_flow, load_flow_evidence, thresholds_from_settings
from .office_scope import require_postgres, workspace_session
from .office_space_api import _audit as office_audit
from .office_space_api import _error as office_error
from .office_space_api import _snap, _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext, new_id, utc_now
from .settings import settings

router = APIRouter(prefix="/api/v1/office/provisioning", tags=["office provisioning"])
VERSION = 1
CLAIM_STALE = timedelta(seconds=120)
TemplateId = Annotated[str, Path(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")]
Lang = Annotated[Literal["en", "ar"], Query()]
RoomQuery = Annotated[str | None, Query(max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Only a click on the review dialog confirms. Voice and typed sentences are refused.
    input_mode: Literal["click", "typed", "voice"] = "typed"
    lang: Literal["en", "ar"] = "en"
    room_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _template(template_id: str) -> op.Template:
    template = op.TEMPLATE_BY_ID.get(template_id)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown role template")
    return template


# --------------------------------------------------------------------------- evidence


def _capabilities(context: TenantContext) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    registry = ap.runtime_tools()
    tool_info: dict[str, dict[str, Any]] = {}
    for template in op.TEMPLATES:
        for name in template.tools:
            definition = registry.definition(name)
            if definition is not None and name not in tool_info:
                tool_info[name] = {"risk": definition.risk_level.value,
                                   "category": ap.agent_tool_category(name, definition).value,
                                   "refusal": ap.tool_refusal(definition, context)}
    skill_info = {s.id: {"name": s.name, "source": s.source, "required_tools": s.required_tools}
                  for s in ap.catalog_skills(context)}
    return tool_info, skill_info


def _rooms_desks(session: Any, p: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rooms = [dict(r) for r in session.execute(text(
        "select r.id, r.name, r.kind, r.capacity, r.team_department, r.floor_id, f.building_id "
        "from office_rooms r join office_floors f on f.tenant_id = r.tenant_id and f.workspace_id = r.workspace_id "
        "and f.id = r.floor_id where r.tenant_id = :t and r.workspace_id = :w order by r.id limit 1000"), p).mappings()]
    desks = [dict(r) for r in session.execute(text(
        "select id, room_id, label, agent_id, revision from office_desks "
        "where tenant_id = :t and workspace_id = :w order by id limit 5000"), p).mappings()]
    return rooms, desks


def _agents(session: Any, p: dict[str, str]) -> list[op.AgentFact]:
    return [op.AgentFact(r.id, r.name, r.status, r.department, r.template_id) for r in session.execute(text(
        "select id, name, status, department, template_id from agent_profiles "
        "where tenant_id = :t and workspace_id = :w order by id limit 1000"), p)]


def _provisions(session: Any, p: dict[str, str]) -> list[dict[str, Any]]:
    return [dict(r) for r in session.execute(text(
        "select template_id, status, work_kinds, agent_id, desk_id, updated_at from office_agent_provisions "
        "where tenant_id = :t and workspace_id = :w order by template_id"), p).mappings()]


def _inputs(context: TenantContext) -> dict[str, Any]:
    require_postgres()
    now = utc_now()
    th = thresholds_from_settings(settings)
    p = {"t": context.tenant_id, "w": context.workspace_id}
    with workspace_session(context) as session:
        evidence = load_flow_evidence(
            session, context, now, th,
            can_leads=has_permission(context, Permission.WHATSAPP_READ),
            can_orders=has_permission(context, Permission.COMMERCE_READ),
            can_approvals=has_permission(context, Permission.APPROVAL_READ))
        rooms, desks = _rooms_desks(session, p)
        agents = _agents(session, p)
        provisions = _provisions(session, p)
    flow = derive_flow(evidence, th)
    return {"now": now, "flow": flow, "sources": evidence.sources, "rooms": rooms, "desks": desks,
            "agents": agents, "provisions": provisions}


def _detected(data: dict[str, Any]) -> dict[str, Any]:
    return op.detect_gaps(items=data["flow"]["items"], sources=data["sources"], rooms=data["rooms"],
                          agents=data["agents"], provisions=data["provisions"])


# --------------------------------------------------------------------------- proposals


def _proposal(context: TenantContext, template: op.Template, lang: str, room_id: str | None,
              data: dict[str, Any]) -> dict[str, Any]:
    tool_info, skill_info = _capabilities(context)
    resolved = op.resolve_capabilities(template, tool_info=tool_info, skill_info=skill_info)
    placement = op.plan_placement(template, data["rooms"], data["desks"], room_id)
    core = {k: placement.get(k) for k in ("mode", "room_id", "desk_id", "desk_label", "reason")}
    content = {
        "template_id": template.id, "catalog_version": op.CATALOG_VERSION, "lang": lang,
        "agent": op.agent_fields(template, resolved, lang),
        "initial_status": "paused",
        "work_kinds": list(template.work_kinds),
        "missing": [{"kind": m["kind"], "id": m["id"], "reason": m["reason"]} for m in resolved.missing],
        "limited": resolved.limited,
        "budget": {"monthly_cost_limit_usd": template.monthly_cost_limit_usd},
        "placement": core,
    }
    digest = op.proposal_hash(context.tenant_id, context.workspace_id, context.user_id, content)
    detected = _detected(data)
    gap = next((g for g in detected["gaps"] if g["template_id"] == template.id), None)
    coverage = next(c for c in detected["coverage"] if c["template_id"] == template.id)
    return {
        "version": VERSION, "payload_hash": digest, "content": content,
        "template": _template_view(template, resolved),
        "names": {"en": template.name_en, "ar": template.name_ar},
        "placement": {**core, "room_name": placement.get("room_name"), "floor_id": placement.get("floor_id"),
                      "building_id": placement.get("building_id"), "candidates": placement.get("candidates", [])},
        "gap": gap, "coverage": coverage,
        "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE),
        "creates": {"agent": 1, "desk": 1 if core["mode"] == "new_desk" else 0,
                    "seat": 1 if core["mode"] in ("existing_desk", "new_desk") else 0,
                    "budget_cap": 1, "ledger_row": 1},
    }, resolved


def _template_view(t: op.Template, resolved: op.Resolved) -> dict[str, Any]:
    return {
        "id": t.id, "department": t.department, "room_department": t.room_department,
        "name": {"en": t.name_en, "ar": t.name_ar}, "description": {"en": t.description_en, "ar": t.description_ar},
        "work_kinds": list(t.work_kinds), "icon": t.icon, "color": t.color, "model_route": t.model_route,
        "autonomy": {"read": "auto", "write": t.write_autonomy, "external": "off"},
        "monthly_cost_limit_usd": t.monthly_cost_limit_usd, "knowledge_mode": t.knowledge_mode,
        "tools": resolved.tools, "skills": resolved.skills, "limited": resolved.limited,
        "missing": [{"kind": m["kind"], "id": m["id"], "reason": m["reason"], "en": m["en"], "ar": m["ar"]}
                    for m in resolved.missing],
    }


# --------------------------------------------------------------------------- reads


@router.get("/gaps")
def get_gaps(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    data = _inputs(context)
    detected = _detected(data)
    return {
        "version": VERSION, "workspace_id": context.workspace_id, "as_of": data["now"].isoformat(),
        "sources": data["sources"], **detected,
        "limits": {"agents": len(data["agents"]), "max_agents": ap.MAX_AGENTS_PER_WORKSPACE},
        "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE),
        "templates": [t.id for t in op.TEMPLATES],
    }


@router.get("/templates")
def get_templates(context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    tool_info, skill_info = _capabilities(context)
    return {"version": VERSION, "catalog_version": op.CATALOG_VERSION, "items": [
        _template_view(t, op.resolve_capabilities(t, tool_info=tool_info, skill_info=skill_info)) for t in op.TEMPLATES]}


@router.get("/proposals/{template_id}")
def get_proposal(template_id: TemplateId, lang: Lang = "en", room_id: RoomQuery = None,
                 context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    template = _template(template_id)
    view, _ = _proposal(context, template, lang, room_id, _inputs(context))
    if view["placement"]["mode"] == "invalid_room":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That room is not a work area (office, workshop or warehouse)")
    return view


# --------------------------------------------------------------------------- confirm


def _agent_exists(session: Any, p: dict[str, Any], agent_id: str | None) -> bool:
    if not agent_id:
        return False
    return session.execute(text(
        "select 1 from agent_profiles where tenant_id = :t and workspace_id = :w and id = :a"), {**p, "a": agent_id}).first() is not None


def _claim(context: TenantContext, template: op.Template, digest: str) -> dict[str, Any]:
    """Take the ledger row for (workspace, template) in its own short transaction.

    Returns {"state": "new" | "resume" | "done", "agent_id", "desk_id"}. Two confirms at once cannot both
    claim: the primary key makes the second one see the first one's row.
    """
    p = {"t": context.tenant_id, "w": context.workspace_id}
    now = utc_now()
    with workspace_session(context, commit=True) as session:
        claimed = session.execute(text(
            "insert into office_agent_provisions (tenant_id, workspace_id, template_id, status, work_kinds, proposal_hash, "
            "requested_by, created_at, updated_at) values (:t, :w, :tpl, 'claimed', cast(:kinds as jsonb), :h, :u, :n, :n) "
            "on conflict (tenant_id, workspace_id, template_id) do nothing returning template_id"),
            {**p, "tpl": template.id, "kinds": op.canonical(list(template.work_kinds)), "h": digest,
             "u": context.user_id, "n": now}).first()
        if claimed is not None:
            return {"state": "new", "agent_id": None, "desk_id": None}
        row = session.execute(text(
            "select status, agent_id, desk_id, updated_at from office_agent_provisions "
            "where tenant_id = :t and workspace_id = :w and template_id = :tpl for update"), {**p, "tpl": template.id}).mappings().one()
        alive = _agent_exists(session, p, row["agent_id"])
        if row["status"] == "active" and alive:
            return {"state": "done", "agent_id": row["agent_id"], "desk_id": row["desk_id"]}
        if row["status"] == "claimed" and now - row["updated_at"] < CLAIM_STALE:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "This role is already being built. Wait a few seconds, then reload to see the result.")
        # Stale claim, or an active row whose agent was deleted: take it over and finish the job.
        session.execute(text(
            "update office_agent_provisions set status = 'claimed', proposal_hash = :h, requested_by = :u, updated_at = :n, "
            "agent_id = cast(:a as varchar), desk_id = case when cast(:a as varchar) is null then null else desk_id end "
            "where tenant_id = :t and workspace_id = :w and template_id = :tpl"),
            {**p, "tpl": template.id, "h": digest, "u": context.user_id, "n": now, "a": row["agent_id"] if alive else None})
        return {"state": "resume" if alive else "new", "agent_id": row["agent_id"] if alive else None,
                "desk_id": row["desk_id"] if alive else None}


def _adopt(context: TenantContext, template: op.Template) -> str | None:
    """An agent an earlier, interrupted confirm already built for this role."""
    p = {"t": context.tenant_id, "w": context.workspace_id, "tpl": (op.TEMPLATE_PREFIX + template.id)[:40]}
    with workspace_session(context) as session:
        return session.execute(text(
            "select id from agent_profiles where tenant_id = :t and workspace_id = :w and template_id = :tpl "
            "order by created_at, id limit 1"), p).scalar()


def _set_budget(context: TenantContext, agent_id: str, limit_usd: float) -> None:
    from .agent_controls import store_in

    with deps._unit_of_work(context, require_membership=True) as repository:
        store_in(repository).set_cap(context, agent_id, limit_usd, None, utc_now())
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="agent_controls.agent_cap.update", target=f"agent:{agent_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata={"limits": f"cost {limit_usd} USD per month", "source": "office_provisioning"}))


def _seat(context: TenantContext, template: op.Template, proposal: dict[str, Any], agent_id: str,
          gap: dict[str, Any] | None) -> dict[str, Any]:
    """Create the desk if the plan says so, seat the agent, close the ledger row, audit and emit. One transaction."""
    plan = proposal["content"]["placement"]
    with _store(context) as store:
        try:
            desk = None
            if plan["mode"] in ("existing_desk", "new_desk"):
                desk = store.get(context, "desks", plan["desk_id"])
                if desk is None:
                    if plan["mode"] != "new_desk":
                        raise SpaceStoreError("conflict", "The desk is gone. Review the proposal again.")
                    created = store.create(context, "desks", plan["desk_id"],
                                           {"room_id": plan["room_id"], "label": plan["desk_label"], "agent_id": None})
                    office_audit(store, context, "office.desk.create", plan["desk_id"], kind="desks",
                                 after=_snap("desks", created), floor_id=None)
                    desk = created
                if desk["agent_id"] != agent_id:
                    if desk["agent_id"] is not None:
                        raise SpaceStoreError("conflict", "Someone else took that desk. Review the proposal again.")
                    before = desk
                    desk = store.assignment(context, plan["desk_id"], agent_id, desk["revision"])
                    office_audit(store, context, "office.desk.assignment", plan["desk_id"], kind="desks",
                                 before=_snap("desks", before), after=_snap("desks", desk))
            params = {"t": context.tenant_id, "w": context.workspace_id, "tpl": template.id, "a": agent_id,
                      "d": desk["id"] if desk else None, "n": utc_now()}
            store.session.execute(text(
                "update office_agent_provisions set status = 'active', agent_id = :a, desk_id = :d, updated_at = :n "
                "where tenant_id = :t and workspace_id = :w and template_id = :tpl"), params)
            _record(store, context, template, proposal, agent_id, desk, gap)
            return desk or {}
        except SpaceStoreError as exc:
            raise office_error(exc) from exc


def _record(store: Any, context: TenantContext, template: op.Template, proposal: dict[str, Any], agent_id: str,
            desk: dict[str, Any] | None, gap: dict[str, Any] | None) -> None:
    from .db.repository import SqlAlchemyRepository

    content = proposal["content"]
    repository = SqlAlchemyRepository(store.session)
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action="office.agent_provisioned", target=agent_id, outcome="success", correlation_id=new_id("correlation"),
        created_at=utc_now(),
        metadata={
            "before": {"gap": gap and {"template_id": gap["template_id"], "reasons": gap["reasons"]},
                       "coverage": proposal["coverage"]["state"]},
            "after": {"template_id": template.id, "agent_id": agent_id, "status": "paused",
                      "department": template.department, "tools": content["agent"]["allowed_tools"],
                      "skills": content["agent"]["allowed_skills"], "missing": content["missing"],
                      "limited": content["limited"], "budget": content["budget"], "work_kinds": content["work_kinds"],
                      "desk_id": desk["id"] if desk else None, "room_id": desk["room_id"] if desk else None},
            "payload_hash": proposal["payload_hash"], "input_mode": "click",
        }))
    payload = {"template_id": template.id, "agent_id": agent_id, "desk_id": desk["id"] if desk else None,
               "room_id": desk["room_id"] if desk else None, "limited": content["limited"]}
    event = create_event(CanonicalEventName.OFFICE_AGENT_PROVISIONED, context, agent_id, payload).event
    store.pending_events.append(repository.record_event(event))


@router.post("/proposals/{template_id}/confirm")
def confirm_proposal(template_id: TemplateId, body: ConfirmBody,
                     context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    template = _template(template_id)
    if body.input_mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
    data = _inputs(context)
    proposal, _ = _proposal(context, template, body.lang, body.room_id, data)
    if proposal["placement"]["mode"] == "invalid_room":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That room is not a work area (office, workshop or warehouse)")
    if not hmac.compare_digest(body.payload_hash, proposal["payload_hash"]):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "What would be created has changed since you opened it. Review the proposal again.")
    claim = _claim(context, template, proposal["payload_hash"])
    if claim["state"] == "done":
        return _result("already_provisioned", context, template, proposal, claim["agent_id"], claim["desk_id"])
    agent_id = claim["agent_id"] or _adopt(context, template)
    outcome = "resumed" if agent_id else "created"
    if agent_id is None:
        payload = ap.AgentProfileCreate(**proposal["content"]["agent"])
        agent_id = ap.create_agent_governed(payload, context, ap.AgentStatus.PAUSED).id
    with workspace_session(context, commit=True) as session:
        session.execute(text(
            "update office_agent_provisions set agent_id = :a, updated_at = :n "
            "where tenant_id = :t and workspace_id = :w and template_id = :tpl"),
            {"t": context.tenant_id, "w": context.workspace_id, "tpl": template.id, "a": agent_id, "n": utc_now()})
    _set_budget(context, agent_id, template.monthly_cost_limit_usd)
    desk = _seat(context, template, proposal, agent_id, proposal["gap"])
    return _result(outcome, context, template, proposal, agent_id, desk.get("id"))


def _result(outcome: str, context: TenantContext, template: op.Template, proposal: dict[str, Any],
            agent_id: str, desk_id: str | None) -> dict[str, Any]:
    agent = ap.load_agent(context, agent_id)
    return {
        "version": VERSION, "outcome": outcome, "template_id": template.id,
        "agent": agent and {"id": agent.id, "name": agent.name, "status": agent.status.value, "version": agent.version,
                            "department": agent.department, "allowed_tools": agent.allowed_tools,
                            "allowed_skills": agent.allowed_skills},
        "desk_id": desk_id, "placement": proposal["placement"], "missing": proposal["template"]["missing"],
        "limited": proposal["template"]["limited"], "budget": proposal["content"]["budget"],
        "work_kinds": list(template.work_kinds), "paused": True,
    }
