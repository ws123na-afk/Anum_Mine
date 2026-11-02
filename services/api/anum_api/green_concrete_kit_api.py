"""The Green Concrete agents kit for the owner (docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md#api).

* ``GET  /api/v1/office/green-concrete/kit?lang=``: ``task:read``. The role skills with the data each needs and
  whether that data is connected now, the read tools with their risk, the data gaps, the shared Green Concrete
  knowledge collection, the routines and the Green Concrete agents with their memory counts. ``items`` are the
  setup steps an owner can take, each with its own ``hash``. Nothing is stored.
* ``POST /api/v1/office/green-concrete/kit/setup``: ``workspace:manage``, ``input_mode: "click"`` and the items the
  owner ticked with their hashes. ``knowledge`` creates the shared collection through the knowledge base's own
  create route function; ``routine:<key>`` creates the automation through the automation route functions, as the
  owner, turned off with its schedule turned off. Each item is claimed in the setup plan ledger
  (``plan_id = gc-agents-kit``) so a retry or a double click never builds two.
* ``POST /api/v1/office/green-concrete/kit/routines/{key}/state``: ``workspace:manage`` plus ``automation:manage``,
  ``input_mode: "click"``: turns a routine (its workflow and its schedule) on or off.

The skills, tools and the collection reach the agents through the office staffing Apply (only adds, never above a
role's risk ceiling). PostgreSQL only (503 otherwise), like every office route. Plain ``def`` routes: the database
work runs in the thread pool, never on the event loop.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field

from . import green_concrete_kit as gk
from . import office_staffing_blueprint as bp
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .office_scope import require_postgres, workspace_session
from .schemas import TenantContext, new_id, utc_now

router = APIRouter(prefix="/api/v1/office/green-concrete/kit", tags=["green concrete agents"])
VERSION = 1
GC_BUILDING = "gc-green-concrete"
KNOWLEDGE_ITEM = "knowledge"
Lang = Annotated[Literal["en", "ar"], Query()]
RoutineKey = Annotated[str, Path(pattern=r"^[a-z_]{3,40}$")]


class SetupItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^(knowledge|routine:[a-z_]{3,40})$")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SetupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_mode: Literal["click", "typed", "voice"] = "typed"
    items: list[SetupItem] = Field(min_length=1, max_length=10)


class RoutineState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_mode: Literal["click", "typed", "voice"] = "typed"
    enabled: bool


def _click(mode: str) -> None:
    if mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Confirm this on screen with a click. A spoken or typed answer cannot do it.")


def _hash(context: TenantContext, content: Any) -> str:
    raw = json.dumps({"v": gk.KIT_VERSION, "t": context.tenant_id, "w": context.workspace_id, "u": context.user_id, "c": content},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


# --------------------------------------------------------------------------- reads


def _ledger(context: TenantContext) -> dict[str, Any]:
    from .setup_plan_ledger import open_setup_plan_ledger

    with open_setup_plan_ledger(context) as ledger:
        return {e.item_key: e for e in ledger.entries(context, gk.PLAN_ID) if e.workspace_id == context.workspace_id}


def gc_collection_id(context: TenantContext) -> str | None:
    """The shared Green Concrete knowledge collection, if the kit created it and it still exists and is shared."""
    entry = _ledger(context).get(KNOWLEDGE_ITEM)
    if entry is None or not entry.target_id or entry.state != "applied":
        return None
    if not has_permission(context, Permission.MEMORY_READ):
        return None
    from .knowledge_base import open_knowledge_store

    with open_knowledge_store(context) as store:
        found = store.get_collection(context, entry.target_id)
    return found.id if found is not None and found.owner_user_id is None else None


def _knowledge(context: TenantContext, ledger: dict[str, Any]) -> dict[str, Any]:
    if not has_permission(context, Permission.MEMORY_READ):
        return {"state": "no_access", "collection": None}
    from .knowledge_base import open_knowledge_store

    entry = ledger.get(KNOWLEDGE_ITEM)
    with open_knowledge_store(context) as store:
        found = store.get_collection(context, entry.target_id) if entry is not None and entry.target_id else None
        count = store.item_counts(context).get(found.id, 0) if found is not None else 0
    if found is None or found.owner_user_id is not None:
        return {"state": "missing", "collection": None}
    return {"state": "ready" if count else "empty",
            "collection": {"id": found.id, "name": found.name, "item_count": count, "description": found.description}}


def _gc_agents(context: TenantContext) -> tuple[dict[str, Any] | None, dict[str, list[bp.OfficeAgent]], dict[str, str], str]:
    """The Green Concrete building, its agents per blueprint role, the source states and the building's time zone."""
    from sqlalchemy import text

    from . import office_staffing_api as osa

    data = osa._load(context)
    building = next((b for b in data["buildings"] if b["id"] == GC_BUILDING), None)
    if building is None:
        return None, {}, data["sources"], "UTC"
    _, units = bp.classify(GC_BUILDING, [])
    roles = bp.roles_for(units)
    by_role, _ = bp.match_agents(GC_BUILDING, roles, osa._office_agents(data, context, GC_BUILDING))
    with workspace_session(context) as session:
        tz = session.execute(text("select timezone from office_buildings where tenant_id = :t and workspace_id = :w and id = :b"),
                             {"t": context.tenant_id, "w": context.workspace_id, "b": GC_BUILDING}).scalar()
    return building, by_role, data["sources"], (tz or "UTC")


def _memory_counts(context: TenantContext, agent_ids: list[str]) -> dict[str, int | None]:
    from .agent_profiles import open_agent_store

    if not has_permission(context, Permission.MEMORY_READ):
        return {a: None for a in agent_ids}
    now = utc_now()
    out: dict[str, int | None] = {}
    with open_agent_store(context) as store:
        for agent_id in agent_ids:
            out[agent_id] = sum(1 for m in store.list_memories(context, agent_id) if not m.expired(now))
    return out


def _routine_agent(role_key: str, by_role: dict[str, list[bp.OfficeAgent]]) -> bp.OfficeAgent | None:
    """The agent a routine runs as: an active agent of the role first, else the first one (paused until the owner turns it on)."""
    agents = sorted(by_role.get(role_key) or [], key=lambda a: (a.status != "active", a.id))
    return agents[0] if agents else None


def _routines(context: TenantContext, ledger: dict[str, Any], by_role: dict[str, list[bp.OfficeAgent]], tz: str,
              lang: str) -> list[dict[str, Any]]:
    from . import automation as auto

    can_read = has_permission(context, Permission.AUTOMATION_READ)
    engine = auto.current_engine() if can_read else None
    out = []
    for r in gk.ROUTINES:
        agent = _routine_agent(r.role, by_role)
        entry = ledger.get(f"routine:{r.key}")
        workflow = schedule = None
        if engine is not None and entry is not None and entry.target_id:
            try:
                workflow = engine.get_workflow(context, entry.target_id)
                schedule = next(iter(engine.list_schedules(context, workflow.id)), None)
            except auto.AutomationNotFound:
                workflow = None
        if not can_read:
            state = "no_access"
        elif workflow is not None:
            state = "on" if workflow.status == auto.WorkflowStatus.ACTIVE and schedule is not None and schedule.enabled else "off"
        elif agent is None:
            state = "needs_agent"
        else:
            state = "missing"
        out.append({
            "key": r.key, "name": {"en": r.name.en, "ar": r.name.ar}, "description": {"en": r.description.en, "ar": r.description.ar},
            "role": r.role, "skill": r.skill, "cron": r.cron, "timezone": tz, "state": state,
            "workflow_id": workflow.id if workflow else None, "schedule_id": schedule.id if schedule else None,
            "next_run_at": schedule.next_run_at.isoformat() if schedule and schedule.next_run_at else None,
            "agent": {"id": agent.id, "name": agent.name, "status": agent.status} if agent else None,
            "name_used": r.name.ar if lang == "ar" else r.name.en,
        })
    return out


def _view(context: TenantContext, lang: str) -> dict[str, Any]:
    from .agent_profiles import runtime_tools

    require_postgres()
    building, by_role, sources, tz = _gc_agents(context)
    ledger = _ledger(context)
    registry = runtime_tools()
    tools = []
    for t in gk.TOOLS:
        d = registry.definition(t.name)
        tools.append({"name": t.name, "label": {"en": t.label.en, "ar": t.label.ar}, "description": t.description,
                      "risk": d.risk_level.value if d else None, "registered": d is not None, "read_only": True,
                      "permissions": list(t.permissions), "sources": list(t.sources)})
    skills = [gk.skill_view(s, sources, registry.definition(s.tool) is not None) for s in gk.SKILLS]
    roles = []
    for role_key, (skill_ids, tool_ids) in gk.ROLE_KIT.items():
        role = bp.ROLE_BY_KEY[role_key]
        roles.append({"key": role_key, "name": {"en": role.template.name_en, "ar": role.template.name_ar},
                      "purpose": {"en": role.purpose_en, "ar": role.purpose_ar}, "max_risk": role.max_risk,
                      "skills": list(skill_ids), "tools": list(tool_ids)})
    agents_flat = [(role_key, a) for role_key, items in by_role.items() for a in items]
    counts = _memory_counts(context, [a.id for _, a in agents_flat])
    collection = gc_collection_id(context)
    agents = [{"id": a.id, "name": a.name, "status": a.status, "role": role_key, "can_edit": a.can_edit,
               "skills": [s for s in a.allowed_skills if s.startswith("gc.")], "tools": [t for t in a.allowed_tools if t.startswith("gc.")],
               "reads_collection": bool(collection and a.reads_collection(collection)), "memories": counts.get(a.id)}
              for role_key, a in sorted(agents_flat, key=lambda x: (list(gk.ROLE_KIT).index(x[0]) if x[0] in gk.ROLE_KIT else 99, x[1].name))]
    knowledge = _knowledge(context, ledger)
    routines = _routines(context, ledger, by_role, tz, lang)
    can_manage = has_permission(context, Permission.WORKSPACE_MANAGE)
    items = []
    if knowledge["state"] == "missing":
        content = {"kind": "knowledge", "name": gk.KNOWLEDGE_NAME}
        items.append({"id": KNOWLEDGE_ITEM, "kind": "knowledge", "content": content, "hash": _hash(context, content)})
    for r in routines:
        if r["state"] == "missing" and r["agent"] is not None:
            content = {"kind": "routine", "key": r["key"], "agent_id": r["agent"]["id"], "cron": r["cron"], "timezone": tz,
                       "name": r["name_used"]}
            items.append({"id": f"routine:{r['key']}", "kind": "routine", "content": content, "hash": _hash(context, content)})
    return {
        "version": VERSION, "kit_version": gk.KIT_VERSION, "as_of": utc_now().isoformat(), "lang": lang,
        "building": {"id": building["id"], "name": building["name"]} if building else None,
        "skills": skills, "tools": tools, "roles": roles, "agents": agents,
        "sources": [{"id": s, "state": sources.get(s, "not_connected"), "name": {"en": bp.SOURCE_BY_ID[s].en, "ar": bp.SOURCE_BY_ID[s].ar},
                     "view": bp.SOURCE_BY_ID[s].view}
                    for s in sorted({x for sk in gk.SKILLS for x in sk.sources}, key=[x.id for x in bp.SOURCES].index)],
        "gaps": [{"id": g.id, "name": {"en": g.name.en, "ar": g.name.ar}, "detail": {"en": g.detail.en, "ar": g.detail.ar}} for g in gk.GAPS],
        "knowledge": knowledge, "routines": [{k: v for k, v in r.items() if k != "name_used"} for r in routines],
        "items": items, "can_manage": can_manage,
        "can_manage_automations": can_manage and has_permission(context, Permission.AUTOMATION_MANAGE),
    }


@router.get("")
def get_kit(lang: Lang = "en", context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    return _view(context, lang)


# --------------------------------------------------------------------------- setup


def _claim(context: TenantContext, key: str, kind: str, existing: Any) -> Any:
    from .setup_plan_ledger import LedgerEntry, LedgerState, open_setup_plan_ledger

    now = utc_now()
    entry = LedgerEntry(tenant_id=context.tenant_id, workspace_id=context.workspace_id, plan_id=gk.PLAN_ID, item_key=key,
                        kind=kind, state=LedgerState.CLAIMED, plan_version=gk.KIT_VERSION, applied_by=context.user_id,
                        created_at=now, updated_at=now)
    with open_setup_plan_ledger(context) as ledger:
        ok = ledger.reclaim(entry, existing) if existing is not None and existing.state == LedgerState.APPLIED else ledger.claim(entry)
    return entry if ok else None


def _finish(context: TenantContext, entry: Any, target_id: str, details: dict[str, Any]) -> None:
    from .setup_plan_ledger import LedgerState, open_setup_plan_ledger

    done = entry.model_copy(update={"state": LedgerState.APPLIED, "target_id": target_id, "details": details, "updated_at": utc_now()})
    with open_setup_plan_ledger(context) as ledger:
        ledger.save(done)


def _release(context: TenantContext, entry: Any) -> None:
    from .setup_plan_ledger import open_setup_plan_ledger

    with open_setup_plan_ledger(context) as ledger:
        ledger.release(entry)


def _create_knowledge(context: TenantContext) -> str:
    from .knowledge_base import CollectionCreate, create_collection

    return create_collection(CollectionCreate(name=gk.KNOWLEDGE_NAME, description=gk.KNOWLEDGE_DESCRIPTION), context).id


def _create_routine(context: TenantContext, content: dict[str, Any]) -> tuple[str, str]:
    from . import automation as auto

    routine = gk.ROUTINE_BY_KEY[content["key"]]
    step = auto.WorkflowStep(id="task", name=content["name"][:160], action="task.create",
                             input={"title": content["name"][:160], "prompt": routine.prompt, "run": True, "agent_id": content["agent_id"]})
    workflow = auto.create_workflow(auto.WorkflowCreate(
        name=content["name"][:160], steps=[step], overlap_policy=auto.OverlapPolicy.SKIP,
        description=(f"Green Concrete routine ({routine.key}), skill {routine.skill}. Created turned off: run it by hand, "
                     "check the result, then turn it on.")), context)
    try:
        # Off first, so it cannot run before its schedule exists (also off).
        auto.disable_workflow(workflow.id, context)
        schedule = auto.create_schedule(auto.ScheduleCreate(workflow_id=workflow.id, name=content["name"][:160], cron=content["cron"],
                                                            timezone=content["timezone"], enabled=False), context)
    except Exception:
        auto.delete_workflow(workflow.id, context)
        raise
    return workflow.id, schedule.id


@router.post("/setup")
def setup_kit(body: SetupBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    _click(body.input_mode)
    ids = [i.id for i in body.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Each item may be listed once")
    if any(i.startswith("routine:") for i in ids):
        require_permission(context, Permission.AUTOMATION_MANAGE)
    view = _view(context, "en")
    plan = {i["id"]: i for i in view["items"]}
    ledger = _ledger(context)
    results: list[dict[str, Any]] = []
    for item in body.items:
        planned = plan.get(item.id)
        if planned is None:
            done = (item.id == KNOWLEDGE_ITEM and view["knowledge"]["state"] in ("ready", "empty")) or any(
                f"routine:{r['key']}" == item.id and r["state"] in ("on", "off") for r in view["routines"])
            results.append({"id": item.id, "outcome": "already_done" if done else "stale",
                            **({} if done else {"detail": "Review Green Concrete again: this step is no longer planned."})})
            continue
        if not hmac.compare_digest(item.hash, planned["hash"]):
            results.append({"id": item.id, "outcome": "stale", "detail": "What this step would do has changed. Review it again."})
            continue
        entry = _claim(context, item.id, planned["kind"], ledger.get(item.id))
        if entry is None:
            results.append({"id": item.id, "outcome": "being_built", "detail": "This step is already being done. Reload in a few seconds."})
            continue
        try:
            if planned["kind"] == "knowledge":
                target = _create_knowledge(context)
                _finish(context, entry, target, {})
                results.append({"id": item.id, "outcome": "created", "target_id": target})
            else:
                workflow_id, schedule_id = _create_routine(context, planned["content"])
                _finish(context, entry, workflow_id, {"schedule_id": schedule_id, "agent_id": planned["content"]["agent_id"]})
                results.append({"id": item.id, "outcome": "created", "target_id": workflow_id, "enabled": False})
        except HTTPException as exc:
            _release(context, entry)
            results.append({"id": item.id, "outcome": "failed", "detail": str(exc.detail)})
    if all(r["outcome"] == "stale" for r in results):
        raise HTTPException(status.HTTP_409_CONFLICT, "Green Concrete changed since you opened it. Review it again.")
    _audit(context, "office.green_concrete.kit_setup",
           {"input_mode": "click", "kit_version": gk.KIT_VERSION,
            "results": [{k: r.get(k) for k in ("id", "outcome", "target_id")} for r in results]})
    return {"version": VERSION, "results": results, "created": sum(1 for r in results if r["outcome"] == "created")}


@router.post("/routines/{key}/state")
def set_routine_state(key: RoutineKey, body: RoutineState, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """Turn a routine on or off: its workflow and its schedule together."""
    from . import automation as auto

    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_permission(context, Permission.AUTOMATION_MANAGE)
    require_postgres()
    _click(body.input_mode)
    if key not in gk.ROUTINE_BY_KEY:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such Green Concrete routine")
    entry = _ledger(context).get(f"routine:{key}")
    if entry is None or not entry.target_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Add this routine first.")
    try:
        schedules = auto.current_engine().list_schedules(context, entry.target_id)
        if body.enabled:
            auto.enable_workflow(entry.target_id, context)
            for s in schedules:
                auto.update_schedule(s.id, auto.ScheduleUpdate(enabled=True), context)
        else:
            for s in schedules:
                auto.update_schedule(s.id, auto.ScheduleUpdate(enabled=False), context)
            auto.disable_workflow(entry.target_id, context)
    except auto.AutomationNotFound:
        raise HTTPException(status.HTTP_409_CONFLICT, "This routine's automation was deleted. Add the routine again.") from None
    _audit(context, "office.green_concrete.routine_state", {"routine": key, "enabled": body.enabled, "workflow_id": entry.target_id})
    return {"key": key, "enabled": body.enabled, "workflow_id": entry.target_id}


def _audit(context: TenantContext, action: str, metadata: dict[str, Any]) -> None:
    from .dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=True) as repository:
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action=action, target=f"building:{GC_BUILDING}", outcome="success", correlation_id=new_id("correlation"),
            created_at=utc_now(), metadata=metadata))
