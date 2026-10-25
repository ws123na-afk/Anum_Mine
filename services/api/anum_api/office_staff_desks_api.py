"""Staff my campus: a reviewed, click-confirmed way to seat a paused agent at every empty desk
(docs/ANUM_MINE_STAFF_DESKS.md), built on governed agent provisioning (docs/ANUM_MINE_AGENT_PROVISIONING.md).

* ``POST /api/v1/office/staff-desks/preview``: for every desk, the agent a confirm would create (or why the desk is
  left alone), the totals, the largest possible monthly cost, and one ``payload_hash`` per building. Stores nothing.
  ``task:read``. (POST only because the room list can be long; it changes nothing.)
* ``POST /api/v1/office/staff-desks/confirm``: ONE building per call. Needs ``workspace:manage``, the hash of that
  building and ``input_mode: "click"``. A spoken or typed confirmation can never confirm, and no registered tool
  calls these routes. Work is atomic per room and the answer lists every desk: created, skipped, failed, with reasons.
* ``PUT /api/v1/office/staff-desks/keep-empty``: mark desks the owner left empty on purpose (never staffed) or clear it.
* ``GET /api/v1/office/staff-desks/paused`` and ``POST .../turn-on/preview`` and ``POST .../turn-on``: list the agents
  this feature made that are still paused, show the cap total of a selection, and turn the selection on (click only).

Agents are created by ``agent_profiles.create_agent_governed`` (the body of ``POST /agents``), paused, with only
registry tools and skills, a monthly cost cap through the agent controls store, and seated in one office
transaction per room. Everything runs in the caller's own tenant and workspace under row level security.
"""
from __future__ import annotations

import hmac
import logging
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import agent_profiles as ap
from . import dependencies as deps
from . import office_provisioning as op
from . import office_staff_desks as sd
from . import office_staff_templates as st
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .office_scope import require_postgres, workspace_session
from .office_space_api import _audit as office_audit
from .office_space_api import _snap, _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext, new_id, utc_now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/office/staff-desks", tags=["office staff desks"])
VERSION = 1
CLAIM_STALE = timedelta(seconds=120)
Ident = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
IdList = Annotated[list[Ident], Field(max_length=300)]


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buildings: Annotated[list[Ident], Field(max_length=100)] | None = None
    exclude_rooms: IdList = Field(default_factory=list)
    max_agents: int | None = Field(default=None, ge=0, le=ap.MAX_AGENTS_PER_WORKSPACE)
    lang: Literal["en", "ar"] = "en"


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Only a click on the review dialog confirms. Voice and typed sentences are refused.
    input_mode: Literal["click", "typed", "voice"] = "typed"
    building_id: Ident
    exclude_rooms: IdList = Field(default_factory=list)
    # The number of NEW agents this building's batch may create, as the preview showed it.
    quota: int = Field(ge=0, le=ap.MAX_AGENTS_PER_WORKSPACE)
    lang: Literal["en", "ar"] = "en"


class KeepEmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desk_ids: Annotated[list[Ident], Field(min_length=1, max_length=500)]
    keep_empty: bool


class TurnOnPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_ids: Annotated[list[Ident], Field(min_length=1, max_length=ap.MAX_AGENTS_PER_WORKSPACE)]


class TurnOnBody(TurnOnPreviewBody):
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_mode: Literal["click", "typed", "voice"] = "typed"


# --------------------------------------------------------------------------- reading


def _load(context: TenantContext) -> dict[str, Any]:
    """All office records of this workspace the planner needs, in one RLS scoped session."""
    require_postgres()
    p = {"t": context.tenant_id, "w": context.workspace_id}
    with workspace_session(context) as s:
        buildings = [dict(r) for r in s.execute(text(
            "select id, name from office_buildings where tenant_id = :t and workspace_id = :w order by id limit 200"), p).mappings()]
        floors = [dict(r) for r in s.execute(text(
            "select id, building_id, level, name from office_floors where tenant_id = :t and workspace_id = :w order by id limit 2000"),
            p).mappings()]
        rooms = [dict(r) for r in s.execute(text(
            "select id, floor_id, name, name_ar, kind, capacity, team_department from office_rooms "
            "where tenant_id = :t and workspace_id = :w order by id limit 5000"), p).mappings()]
        desks = [dict(r) for r in s.execute(text(
            "select id, room_id, label, agent_id from office_desks where tenant_id = :t and workspace_id = :w order by id limit 20000"),
            p).mappings()]
        ledger = {r["desk_id"]: dict(r) for r in s.execute(text(
            "select desk_id, status, template_id, agent_id, updated_at from office_desk_staffing "
            "where tenant_id = :t and workspace_id = :w limit 20000"), p).mappings()}
        agents = [dict(r) for r in s.execute(text(
            "select id, template_id, status from agent_profiles where tenant_id = :t and workspace_id = :w limit 5000"), p).mappings()]
    markers = {a["template_id"]: a["id"] for a in sorted(agents, key=lambda a: a["id"], reverse=True)
               if a["template_id"] and a["template_id"].startswith(op.TEMPLATE_PREFIX) and a["template_id"].count(":") == 2}
    return {"buildings": buildings, "floors": floors, "rooms": rooms, "desks": desks, "ledger": ledger,
            "live_agents": {a["id"] for a in agents}, "markers": markers, "agent_count": len(agents),
            "agent_status": {a["id"]: a["status"] for a in agents}}


def _plan(data: dict[str, Any], selected: set[str] | None, exclude: set[str], max_agents: int | None, lang: str) -> dict[str, Any]:
    return sd.plan_staffing(
        buildings=data["buildings"], floors=data["floors"], rooms=data["rooms"], desks=data["desks"], ledger=data["ledger"],
        live_agents=data["live_agents"], markers=data["markers"], agent_count=data["agent_count"], selected=selected,
        exclude_rooms=exclude, max_agents=max_agents, lang=lang)


def _resolved(context: TenantContext) -> dict[str, op.Resolved]:
    registry = ap.runtime_tools()
    tool_info: dict[str, dict[str, Any]] = {}
    for t in st.STAFF_TEMPLATES:
        for name in t.tools:
            definition = registry.definition(name)
            if definition is not None and name not in tool_info:
                tool_info[name] = {"risk": definition.risk_level.value,
                                   "category": ap.agent_tool_category(name, definition).value,
                                   "refusal": ap.tool_refusal(definition, context)}
    skill_info = {s.id: {"name": s.name, "source": s.source, "required_tools": s.required_tools}
                  for s in ap.catalog_skills(context)}
    return {t.id: op.resolve_capabilities(t, tool_info=tool_info, skill_info=skill_info) for t in st.STAFF_TEMPLATES}


def _workspace_budget(context: TenantContext) -> dict[str, Any]:
    """The workspace's own monthly budget, only for people who may read it (the budget screen needs the same right)."""
    if not has_permission(context, Permission.ORGANIZATION_MANAGE):
        return {"visible": False, "monthly_cost_limit_usd": None}
    try:
        from .model_budget import BudgetScope, open_model_budget_store

        with open_model_budget_store(context) as store:
            budget = store.get_budget(context, BudgetScope.WORKSPACE)
        return {"visible": True, "monthly_cost_limit_usd": budget.monthly_cost_limit_usd if budget else None}
    except Exception:  # an unreadable budget is shown as unknown, never as zero
        logger.warning("staff desks could not read the workspace budget")
        return {"visible": False, "monthly_cost_limit_usd": None}


def _template_card(t: op.Template, resolved: op.Resolved, count: int) -> dict[str, Any]:
    return {
        "id": t.id, "department": t.department, "name": {"en": t.name_en, "ar": t.name_ar},
        "count": count, "monthly_cost_limit_usd": t.monthly_cost_limit_usd,
        "limited": resolved.limited, "tools": len(resolved.attach_tools), "skills": len(resolved.attach_skills),
        "missing": [{"kind": m["kind"], "id": m["id"], "en": m["en"], "ar": m["ar"]} for m in resolved.missing],
        "autonomy": {"read": "auto", "write": t.write_autonomy, "external": "off"},
    }


def _scope_sets(body: PreviewBody, data: dict[str, Any]) -> tuple[set[str] | None, set[str]]:
    known = {b["id"] for b in data["buildings"]}
    selected = None if body.buildings is None else set(body.buildings)
    if selected is not None and (not selected or not selected <= known):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose buildings that exist in this workspace")
    return selected, set(body.exclude_rooms)


# --------------------------------------------------------------------------- preview


@router.post("/preview")
def preview(body: PreviewBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    data = _load(context)
    selected, exclude = _scope_sets(body, data)
    plan = _plan(data, selected, exclude, body.max_agents, body.lang)
    resolved = _resolved(context)
    totals = {s: 0 for s in sd.STATES}
    totals["total"] = 0
    for b in plan["buildings"]:
        for k, v in b["counts"].items():
            totals[k] += v
    cost = sd.cost_summary(b for b in plan["buildings"] if b["selected"])
    per_role: dict[str, int] = {r["template_id"]: r["count"] for r in cost["per_template"]}
    cards = [_template_card(st.STAFF_TEMPLATE_BY_ID[tid], resolved[tid], n) for tid, n in sorted(per_role.items())]
    gaps: dict[str, dict[str, Any]] = {}
    for card in cards:
        for m in card["missing"]:
            g = gaps.setdefault(f"{card['id']}:{m['id']}", {"template_id": card["id"], "id": m["id"], "kind": m["kind"],
                                                           "en": m["en"], "ar": m["ar"], "agents": card["count"]})
            g["agents"] = card["count"]
    budget = _workspace_budget(context)
    limit = budget["monthly_cost_limit_usd"]
    batches = []
    for b in plan["buildings"]:
        if not b["selected"]:
            continue
        items = sd.batch_items(b)
        if not items:
            continue
        quota = sum(1 for i in items if i["mode"] == sd.PLAN)
        batches.append({
            "building_id": b["id"], "count": len(items), "new_agents": quota, "resumed": len(items) - quota, "quota": quota,
            "max_monthly_usd": round(sum(i["cap"] for i in items), 2),
            "payload_hash": sd.batch_hash(context.tenant_id, context.workspace_id, context.user_id, b["id"], body.lang, items),
        })
    return {
        "version": VERSION, "catalog_version": sd.CATALOG_VERSION, "workspace_id": context.workspace_id,
        "as_of": utc_now().isoformat(), "lang": body.lang,
        "limits": {"agents": data["agent_count"], "max_agents": sd.MAX_AGENTS,
                   "free_slots": max(0, sd.MAX_AGENTS - data["agent_count"]), "run_cap": body.max_agents},
        "totals": totals,
        "cost": {**cost, "workspace_budget": budget,
                 "within_budget": None if limit is None else cost["max_monthly_usd"] <= limit},
        "roles": cards, "capability_gaps": sorted(gaps.values(), key=lambda g: (g["template_id"], g["id"])),
        "buildings": plan["buildings"], "batches": batches,
        "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE),
        "rules": {"created_paused": True, "touches_occupied_desks": False, "click_only": True, "least_privilege": True},
    }


# --------------------------------------------------------------------------- confirm


def _failure(item: dict[str, Any], room_id: str, reason: str, status_: str = "failed") -> dict[str, Any]:
    return {"desk_id": item["desk_id"], "room_id": room_id, "status": status_, "agent_id": None, "agent_name": item["name"],
            "reason": reason}


def _claim_room(context: TenantContext, items: list[dict[str, Any]], digest: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Take the ledger rows of the room's desks. Returns (claimed, skipped results). Two confirms at once cannot both
    claim a desk: the primary key makes the second one see the first one's row."""
    p = {"t": context.tenant_id, "w": context.workspace_id}
    now = utc_now()
    claimed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with workspace_session(context, commit=True) as s:
        for item in items:
            fresh = s.execute(text(
                "insert into office_desk_staffing (tenant_id, workspace_id, desk_id, status, template_id, proposal_hash, requested_by, "
                "created_at, updated_at) values (:t, :w, :d, 'claimed', :tpl, :h, :u, :n, :n) "
                "on conflict (tenant_id, workspace_id, desk_id) do nothing returning desk_id"),
                {**p, "d": item["desk_id"], "tpl": item["template_id"], "h": digest, "u": context.user_id, "n": now}).first()
            if fresh is not None:
                claimed.append({"item": item, "agent_id": item["existing_agent_id"]})
                continue
            row = s.execute(text(
                "select status, agent_id, updated_at from office_desk_staffing where tenant_id = :t and workspace_id = :w "
                "and desk_id = :d for update"), {**p, "d": item["desk_id"]}).mappings().one()
            alive = row["agent_id"] is not None and s.execute(text(
                "select 1 from agent_profiles where tenant_id = :t and workspace_id = :w and id = :a"),
                {**p, "a": row["agent_id"]}).first() is not None
            seat = s.execute(text(
                "select agent_id from office_desks where tenant_id = :t and workspace_id = :w and id = :d"),
                {**p, "d": item["desk_id"]}).scalar()
            if row["status"] == "keep_empty":
                skipped.append(_failure(item, item["room_id"], "kept_empty", "skipped"))
                continue
            if seat is not None:
                skipped.append(_failure(item, item["room_id"], "already_staffed", "skipped"))
                continue
            if row["status"] == "claimed" and now - row["updated_at"] < CLAIM_STALE:
                skipped.append(_failure(item, item["room_id"], "being_built", "skipped"))
                continue
            s.execute(text(
                "update office_desk_staffing set status = 'claimed', template_id = :tpl, proposal_hash = :h, requested_by = :u, "
                "updated_at = :n, agent_id = cast(:a as varchar) where tenant_id = :t and workspace_id = :w and desk_id = :d"),
                {**p, "d": item["desk_id"], "tpl": item["template_id"], "h": digest, "u": context.user_id, "n": now,
                 "a": row["agent_id"] if alive else None})
            claimed.append({"item": item, "agent_id": row["agent_id"] if alive else item["existing_agent_id"]})
    return claimed, skipped


def _release(context: TenantContext, desk_ids: list[str]) -> None:
    """Drop claims that never got an agent, so the next run starts clean."""
    if not desk_ids:
        return
    with workspace_session(context, commit=True) as s:
        s.execute(text(
            "delete from office_desk_staffing where tenant_id = :t and workspace_id = :w and status = 'claimed' "
            "and agent_id is null and desk_id = any(cast(:d as text[]))"),
            {"t": context.tenant_id, "w": context.workspace_id, "d": desk_ids})


def _unclaim(context: TenantContext, desk_ids: list[str]) -> None:
    """The run that held these claims has ended without seating them. Keep the agents they remember, but let the next
    review take the desks over at once instead of waiting for the claim to go stale."""
    if not desk_ids:
        return
    with workspace_session(context, commit=True) as s:
        s.execute(text(
            "update office_desk_staffing set updated_at = :n where tenant_id = :t and workspace_id = :w and status = 'claimed' "
            "and desk_id = any(cast(:d as text[]))"),
            {"t": context.tenant_id, "w": context.workspace_id, "d": desk_ids, "n": utc_now() - CLAIM_STALE - timedelta(seconds=1)})


def _remember_agent(context: TenantContext, desk_id: str, agent_id: str) -> None:
    with workspace_session(context, commit=True) as s:
        s.execute(text(
            "update office_desk_staffing set agent_id = :a, updated_at = :n where tenant_id = :t and workspace_id = :w and desk_id = :d"),
            {"t": context.tenant_id, "w": context.workspace_id, "d": desk_id, "a": agent_id, "n": utc_now()})


def _create_agent(context: TenantContext, item: dict[str, Any], resolved: op.Resolved, lang: str) -> str:
    template = st.STAFF_TEMPLATE_BY_ID[item["template_id"]]
    fields = op.agent_fields(template, resolved, lang)
    fields["name"] = item["name"]
    fields["template_id"] = st.desk_marker(template.id, item["desk_id"])
    return ap.create_agent_governed(ap.AgentProfileCreate(**fields), context, ap.AgentStatus.PAUSED).id


def _set_caps(context: TenantContext, caps: list[tuple[str, float]]) -> None:
    from .agent_controls import store_in

    with deps._unit_of_work(context, require_membership=True) as repository:
        controls = store_in(repository)
        for agent_id, limit in caps:
            controls.set_cap(context, agent_id, limit, None, utc_now())
            repository.record_audit(AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
                action="agent_controls.agent_cap.update", target=f"agent:{agent_id}", outcome="success",
                correlation_id=new_id("correlation"), created_at=utc_now(),
                metadata={"limits": f"cost {limit} USD per month", "source": "office_staff_desks"}))


def _seat_room(context: TenantContext, room_id: str, ready: list[dict[str, Any]], digest: str, lang: str) -> None:
    """Seat every agent of the room, close the ledger rows, audit and emit. One transaction: all of it or none."""
    with _store(context) as store:
        try:
            before: list[dict[str, Any]] = []
            after: list[dict[str, Any]] = []
            for entry in ready:
                item, agent_id = entry["item"], entry["agent_id"]
                desk = store.get(context, "desks", item["desk_id"])
                if desk is None:
                    raise SpaceStoreError("conflict", "A desk is gone. Review the preview again.")
                if desk["agent_id"] not in (None, agent_id):
                    raise SpaceStoreError("conflict", "Someone else took a desk. Review the preview again.")
                if desk["agent_id"] != agent_id:
                    seated = store.assignment(context, item["desk_id"], agent_id, desk["revision"])
                    before.append(_snap("desks", desk))
                    after.append({**_snap("desks", seated), "template_id": item["template_id"], "cap_usd": item["cap"],
                                  "mode": item["mode"]})
                store.session.execute(text(
                    "update office_desk_staffing set status = 'active', agent_id = :a, updated_at = :n "
                    "where tenant_id = :t and workspace_id = :w and desk_id = :d"),
                    {"t": context.tenant_id, "w": context.workspace_id, "d": item["desk_id"], "a": agent_id, "n": utc_now()})
            office_audit(store, context, "office.staff_desks.room", room_id, kind="desks", before={"desks": before},
                         after={"desks": after, "payload_hash": digest, "input_mode": "click", "paused": True,
                                "catalog_version": sd.CATALOG_VERSION, "lang": lang},
                         ids=[e["item"]["desk_id"] for e in ready])
            from .db.repository import SqlAlchemyRepository

            repository = SqlAlchemyRepository(store.session)
            for entry in ready:
                item = entry["item"]
                payload = {"template_id": item["template_id"], "agent_id": entry["agent_id"], "desk_id": item["desk_id"],
                           "room_id": room_id, "limited": bool(entry.get("limited"))}
                event = create_event(CanonicalEventName.OFFICE_AGENT_PROVISIONED, context, entry["agent_id"], payload).event
                store.pending_events.append(repository.record_event(event))
        except SpaceStoreError as exc:
            raise _SeatRefused(str(exc)) from exc


class _SeatRefused(Exception):
    pass


def _staff_room(context: TenantContext, room_id: str, items: list[dict[str, Any]], resolved: dict[str, op.Resolved],
                digest: str, lang: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    claimed, skipped = _claim_room(context, items, digest)
    results.extend(skipped)
    ready: list[dict[str, Any]] = []
    built_here: set[str] = set()
    release: list[str] = []
    limit_hit = False
    for entry in claimed:
        item = entry["item"]
        if limit_hit:
            release.append(item["desk_id"])
            results.append(_failure(item, room_id, "agent_limit"))
            continue
        try:
            agent_id = entry["agent_id"]
            if agent_id is None:
                agent_id = _create_agent(context, item, resolved[item["template_id"]], lang)
                built_here.add(agent_id)
            _remember_agent(context, item["desk_id"], agent_id)
            entry["agent_id"] = agent_id
            entry["limited"] = resolved[item["template_id"]].limited
            ready.append(entry)
        except HTTPException as exc:
            reason = "agent_limit" if exc.status_code == status.HTTP_409_CONFLICT else "agent_refused"
            limit_hit = limit_hit or reason == "agent_limit"
            release.append(item["desk_id"])
            results.append(_failure(item, room_id, reason))
        except Exception as exc:  # one desk failing must not hide the others
            logger.error("staff desks agent create failed", extra={"anum_room": room_id, "anum_error": type(exc).__name__})
            release.append(item["desk_id"])
            results.append(_failure(item, room_id, "agent_failed"))
    _release(context, release)
    if not ready:
        return results
    try:
        _set_caps(context, [(e["agent_id"], e["item"]["cap"]) for e in ready])
        _seat_room(context, room_id, ready, digest, lang)
    except (_SeatRefused, SpaceStoreError, HTTPException) as exc:
        logger.warning("staff desks room refused", extra={"anum_room": room_id, "anum_error": type(exc).__name__})
        reason = "seat_conflict" if isinstance(exc, (_SeatRefused, SpaceStoreError)) else "cap_failed"
        _unclaim(context, [e["item"]["desk_id"] for e in ready])
        results.extend(_failure(e["item"], room_id, reason) | {"agent_id": e["agent_id"]} for e in ready)
        return results
    except Exception as exc:
        logger.error("staff desks room failed", extra={"anum_room": room_id, "anum_error": type(exc).__name__})
        _unclaim(context, [e["item"]["desk_id"] for e in ready])
        results.extend(_failure(e["item"], room_id, "room_failed") | {"agent_id": e["agent_id"]} for e in ready)
        return results
    for e in ready:
        results.append({"desk_id": e["item"]["desk_id"], "room_id": room_id,
                        "status": "created" if e["agent_id"] in built_here else "seated_existing", "agent_id": e["agent_id"],
                        "agent_name": e["item"]["name"], "reason": None})
    return results


@router.post("/confirm")
def confirm(body: ConfirmBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    if body.input_mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
    data = _load(context)
    if body.building_id not in {b["id"] for b in data["buildings"]}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That building is not in this workspace")
    plan = _plan(data, {body.building_id}, set(body.exclude_rooms), body.quota, body.lang)
    building = next(b for b in plan["buildings"] if b["id"] == body.building_id)
    items = sd.batch_items(building)
    if not items:
        return _report("already_staffed", body.building_id, body.payload_hash, [])
    digest = sd.batch_hash(context.tenant_id, context.workspace_id, context.user_id, body.building_id, body.lang, items)
    if not hmac.compare_digest(body.payload_hash, digest):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "What would be created has changed since you opened it. Review the preview again.")
    resolved = _resolved(context)
    results: list[dict[str, Any]] = []
    by_room: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_room.setdefault(item["room_id"], []).append(item)
    for room_id, room_items in by_room.items():
        results.extend(_staff_room(context, room_id, room_items, resolved, digest, body.lang))
    return _report(None, body.building_id, digest, results)


def _report(outcome: str | None, building_id: str, digest: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"created": 0, "seated_existing": 0, "skipped": 0, "failed": 0}
    for r in results:
        counts[r["status"]] += 1
    if outcome is None:
        made = counts["created"] + counts["seated_existing"]
        outcome = ("created" if made and not counts["failed"] else "partial" if made else "failed" if counts["failed"]
                   else "in_progress" if any(r["reason"] == "being_built" for r in results) else "already_staffed")
    return {"version": VERSION, "outcome": outcome, "building_id": building_id, "payload_hash": digest, "counts": counts,
            "paused": True, "results": results}


# --------------------------------------------------------------------------- keep empty


@router.put("/keep-empty")
def keep_empty(body: KeepEmptyBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    """Mark desks the owner left empty on purpose (staffing never touches them) or clear the mark."""
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    p = {"t": context.tenant_id, "w": context.workspace_id}
    ids = sorted(set(body.desk_ids))
    with _store(context) as store:
        found = {r["id"]: r["agent_id"] for r in store.session.execute(text(
            "select id, agent_id from office_desks where tenant_id = :t and workspace_id = :w and id = any(cast(:d as text[]))"),
            {**p, "d": ids}).mappings()}
        if set(ids) - set(found):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "A desk is not in this workspace")
        rows = {r["desk_id"]: r["status"] for r in store.session.execute(text(
            "select desk_id, status from office_desk_staffing where tenant_id = :t and workspace_id = :w "
            "and desk_id = any(cast(:d as text[]))"), {**p, "d": ids}).mappings()}
        changed: list[str] = []
        if body.keep_empty:
            blocked = [d for d in ids if found[d] is not None or rows.get(d) in ("active", "claimed")]
            if blocked:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    "A desk that has an agent, or that is being staffed, cannot be marked to keep empty")
            for d in ids:
                if rows.get(d) != "keep_empty":
                    store.session.execute(text(
                        "insert into office_desk_staffing (tenant_id, workspace_id, desk_id, status, requested_by) "
                        "values (:t, :w, :d, 'keep_empty', :u) on conflict (tenant_id, workspace_id, desk_id) do nothing"),
                        {**p, "d": d, "u": context.user_id})
                    changed.append(d)
        else:
            for d in ids:
                if rows.get(d) == "keep_empty":
                    store.session.execute(text(
                        "delete from office_desk_staffing where tenant_id = :t and workspace_id = :w and desk_id = :d "
                        "and status = 'keep_empty'"), {**p, "d": d})
                    changed.append(d)
        if changed:
            office_audit(store, context, "office.staff_desks.keep_empty", changed[0], kind="desks",
                         before={"keep_empty": not body.keep_empty, "desks": changed},
                         after={"keep_empty": body.keep_empty, "desks": changed}, ids=changed)
    return {"version": VERSION, "keep_empty": body.keep_empty, "changed": changed, "unchanged": [d for d in ids if d not in changed]}


# --------------------------------------------------------------------------- turn on


def _paused(context: TenantContext, only: list[str] | None = None) -> list[dict[str, Any]]:
    """Agents this feature seated that are still paused, with their desk, role and cost cap."""
    require_postgres()
    p = {"t": context.tenant_id, "w": context.workspace_id}
    query = ("select s.agent_id, s.desk_id, s.template_id, a.name, a.status, d.room_id from office_desk_staffing s "
             "join agent_profiles a on a.tenant_id = s.tenant_id and a.workspace_id = s.workspace_id and a.id = s.agent_id "
             "left join office_desks d on d.tenant_id = s.tenant_id and d.workspace_id = s.workspace_id and d.id = s.desk_id "
             "where s.tenant_id = :t and s.workspace_id = :w and s.status = 'active' and a.status = 'paused' ")
    params: dict[str, Any] = dict(p)
    if only is not None:
        query += "and s.agent_id = any(cast(:ids as text[])) "
        params["ids"] = only
    query += "order by s.desk_id limit 1000"
    with workspace_session(context) as s:
        rows = [dict(r) for r in s.execute(text(query), params).mappings()]
    from .agent_controls import open_agent_controls_store

    with open_agent_controls_store(context) as controls:
        caps = {c.agent_id: c.monthly_cost_limit_usd for c in controls.list_caps(context)}
    return [{"agent_id": r["agent_id"], "name": r["name"], "desk_id": r["desk_id"], "room_id": r["room_id"],
             "template_id": r["template_id"], "monthly_cost_limit_usd": caps.get(r["agent_id"])} for r in rows]


@router.get("/paused")
def paused(limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0, le=100000)] = 0,
           context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    rows = _paused(context)
    return {"version": VERSION, "total": len(rows), "items": rows[offset: offset + limit], "offset": offset, "limit": limit,
            "all_ids": [r["agent_id"] for r in rows],
            "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE)}


def _turn_on_view(context: TenantContext, agent_ids: list[str]) -> dict[str, Any]:
    wanted = sorted(set(agent_ids))
    rows = _paused(context, wanted)
    found = {r["agent_id"] for r in rows}
    items = [{"agent_id": r["agent_id"], "cap": r["monthly_cost_limit_usd"]} for r in rows]
    unknown_cap = [r["agent_id"] for r in rows if r["monthly_cost_limit_usd"] is None]
    total = round(sum(r["monthly_cost_limit_usd"] or 0.0 for r in rows), 2)
    budget = _workspace_budget(context)
    limit = budget["monthly_cost_limit_usd"]
    return {
        "version": VERSION, "items": rows, "not_paused_or_unknown": [a for a in wanted if a not in found],
        "max_monthly_usd": total, "agents_without_cap": unknown_cap, "workspace_budget": budget,
        "within_budget": None if limit is None else total <= limit,
        "payload_hash": sd.turn_on_hash(context.tenant_id, context.workspace_id, context.user_id, items),
        "can_confirm": has_permission(context, Permission.WORKSPACE_MANAGE),
    }


@router.post("/turn-on/preview")
def turn_on_preview(body: TurnOnPreviewBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.TASK_READ)
    return _turn_on_view(context, body.agent_ids)


@router.post("/turn-on")
def turn_on(body: TurnOnBody, context: TenantContext = Depends(tenant_context)) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    if body.input_mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.")
    view = _turn_on_view(context, body.agent_ids)
    if not view["items"]:
        return {"version": VERSION, "outcome": "nothing_to_turn_on", "results": [], "counts": {"on": 0, "failed": 0}}
    if not hmac.compare_digest(body.payload_hash, view["payload_hash"]):
        raise HTTPException(status.HTTP_409_CONFLICT, "The selection has changed since you opened it. Review it again.")
    results: list[dict[str, Any]] = []
    for row in view["items"]:
        try:
            with ap.open_agent_store(context) as store:
                profile = ap._agent_or_404(store, context, row["agent_id"])
                ap._require_edit(profile, context)
                if profile.status != ap.AgentStatus.PAUSED:
                    results.append({"agent_id": row["agent_id"], "status": "failed", "reason": "not_paused"})
                    continue
                updated = profile.model_copy(update={"status": ap.AgentStatus.ACTIVE, "updated_at": utc_now(),
                                                     "version": profile.version + 1})
                store.save(updated)
                ap._audit(store, context, "agent.updated", row["agent_id"],
                          {"changed": ["status"], "after": ap._summary(updated), "source": "office_staff_desks"})
            results.append({"agent_id": row["agent_id"], "status": "on", "reason": None})
        except HTTPException as exc:
            results.append({"agent_id": row["agent_id"], "status": "failed",
                            "reason": "not_allowed" if exc.status_code == status.HTTP_403_FORBIDDEN else "not_found"})
        except Exception as exc:
            logger.error("staff desks turn on failed", extra={"anum_error": type(exc).__name__})
            results.append({"agent_id": row["agent_id"], "status": "failed", "reason": "failed"})
    on = [r["agent_id"] for r in results if r["status"] == "on"]
    with deps._unit_of_work(context, require_membership=True) as repository:
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="office.staff_desks.turn_on", target=f"workspace:{context.workspace_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata={"before": {"status": "paused", "agents": on}, "after": {"status": "active", "agents": on},
                      "max_monthly_usd": view["max_monthly_usd"], "payload_hash": view["payload_hash"], "input_mode": "click"}))
    failed = len(results) - len(on)
    return {"version": VERSION, "outcome": "on" if on and not failed else "partial" if on else "failed", "results": results,
            "counts": {"on": len(on), "failed": failed}, "max_monthly_usd": view["max_monthly_usd"]}
