"""Live flow and stuck detection for the ANUM MINE headquarters (docs/ANUM_MINE_HQ_LIVE_FLOW.md).

``derive_flow`` is a pure function from persisted records to flow items. An item appears only
because a record backs it (a task, a sales lead, a commerce order) and it sits in an office only
because a record places it there (the task's agent has a desk, the lead's ``assigned_to`` is an
agent with a desk). Nothing is invented: when no record places an item, ``location`` is null and
``location_reason`` says why. An item is *stuck* only when a rule with a stated threshold
(``FlowThresholds``) matches the records; the rule code, the age and the next action travel with it.

``load_flow_evidence`` reads one workspace inside that workspace's RLS context (the caller opens
the session). Optional sources (sales leads, commerce orders) are read inside a savepoint and
report ``not_connected`` instead of failing the whole answer.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .schemas import TenantContext

MAX_ITEMS = 500
MAX_ROWS = 1000
OPEN_TASK_STATUSES = ("created", "queued", "running", "waiting_approval")
OPEN_LEAD_STATUSES = ("new", "qualifying", "qualified", "quoted", "negotiating")
CLOSED_LEAD_STATUSES = ("won", "lost", "disqualified")
OPEN_ORDER_STATUSES = ("pending", "unshipped", "partially_shipped")
DONE_ORDER_STATUSES = ("shipped", "delivered")
STAGES = ("arrived", "in_office", "waiting_human", "blocked", "done")
SEVERITY_RANK = {"critical": 0, "warning": 1}
SOURCE_STATES = ("connected", "no_access", "not_connected", "error")
FLOW_EVENT_PREFIXES = ("task.", "agent_run.", "approval.", "agent_meeting.", "office.", "work_item.")


@dataclass(frozen=True)
class FlowThresholds:
    """How long a real record may sit before it is called stuck. Defaults are stated in the doc."""

    approval_wait: timedelta = timedelta(minutes=240)
    queue_wait: timedelta = timedelta(minutes=30)
    run_silent: timedelta = timedelta(minutes=60)
    lead_unassigned: timedelta = timedelta(minutes=240)
    lead_idle: timedelta = timedelta(hours=72)
    order_ship_grace: timedelta = timedelta(minutes=0)
    recent_done: timedelta = timedelta(minutes=30)
    failed_window: timedelta = timedelta(hours=24)

    def as_json(self) -> dict[str, int]:
        return {
            "approval_wait_minutes": int(self.approval_wait.total_seconds() // 60),
            "queue_wait_minutes": int(self.queue_wait.total_seconds() // 60),
            "run_silent_minutes": int(self.run_silent.total_seconds() // 60),
            "lead_unassigned_minutes": int(self.lead_unassigned.total_seconds() // 60),
            "lead_idle_hours": int(self.lead_idle.total_seconds() // 3600),
            "order_ship_grace_minutes": int(self.order_ship_grace.total_seconds() // 60),
            "recent_done_minutes": int(self.recent_done.total_seconds() // 60),
            "failed_window_hours": int(self.failed_window.total_seconds() // 3600),
        }


def thresholds_from_settings(cfg: Any) -> FlowThresholds:
    return FlowThresholds(
        approval_wait=timedelta(minutes=cfg.office_flow_approval_wait_minutes),
        queue_wait=timedelta(minutes=cfg.office_flow_queue_wait_minutes),
        run_silent=timedelta(minutes=cfg.office_flow_run_silent_minutes),
        lead_unassigned=timedelta(minutes=cfg.office_flow_lead_unassigned_minutes),
        lead_idle=timedelta(hours=cfg.office_flow_lead_idle_hours),
        order_ship_grace=timedelta(minutes=cfg.office_flow_order_ship_grace_minutes),
        recent_done=timedelta(minutes=cfg.office_flow_recent_done_minutes),
        failed_window=timedelta(hours=cfg.office_flow_failed_window_hours),
    )


@dataclass(frozen=True)
class FlowAgent:
    agent_id: str
    name: str
    status: str  # active | paused


@dataclass(frozen=True)
class FlowDesk:
    desk_id: str
    room_id: str
    room_name: str
    floor_id: str
    building_id: str
    agent_id: str | None


@dataclass(frozen=True)
class FlowTask:
    task_id: str
    title: str
    status: str
    agent_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FlowRun:
    run_id: str
    task_id: str
    status: str
    updated_at: datetime
    result: str | None = None


@dataclass(frozen=True)
class FlowApproval:
    approval_id: str
    task_id: str
    action: str
    status: str  # pending | expired
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class FlowLead:
    lead_id: str
    title: str
    status: str
    assigned_to: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FlowOrder:
    order_id: str
    external_id: str
    status: str
    purchased_at: datetime
    ship_by: datetime | None
    synced_at: datetime | None


@dataclass
class FlowEvidence:
    as_of: datetime
    agents: list[FlowAgent] = field(default_factory=list)
    desks: list[FlowDesk] = field(default_factory=list)
    tasks: list[FlowTask] = field(default_factory=list)
    runs: list[FlowRun] = field(default_factory=list)
    approvals: list[FlowApproval] = field(default_factory=list)
    leads: list[FlowLead] = field(default_factory=list)
    orders: list[FlowOrder] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    cursor: str | None = None
    can_see_approval_text: bool = False
    truncated: bool = False


def _iso(value: datetime) -> str:
    return value.isoformat()


def _short(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _location(desk: FlowDesk, agent: FlowAgent | None) -> dict[str, Any]:
    return {
        "building_id": desk.building_id, "floor_id": desk.floor_id, "room_id": desk.room_id,
        "room_name": desk.room_name, "desk_id": desk.desk_id,
        "agent_id": desk.agent_id, "agent_name": agent.name if agent else None,
    }


def _stuck(code: str, severity: str, since: datetime, now: datetime, threshold: timedelta | None,
           action: str, **facts: Any) -> dict[str, Any]:
    return {
        "code": code, "severity": severity, "since": _iso(since),
        "age_seconds": max(0, int((now - since).total_seconds())),
        "threshold_seconds": int(threshold.total_seconds()) if threshold is not None else None,
        "facts": {k: v for k, v in facts.items() if v is not None},
        "action": action,
    }


def _item(key: str, kind: str, item_id: str, title: str, stage: str, status: str, since: datetime,
          created_at: datetime, location: dict[str, Any] | None, reason: str | None,
          stuck: dict[str, Any] | None, waiting: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "key": key, "kind": kind, "id": item_id, "title": title, "stage": stage, "status": status,
        "since": _iso(since), "created_at": _iso(created_at),
        "location": location, "location_reason": None if location else reason,
        "stuck": stuck, "waiting": waiting,
    }


def derive_flow(evidence: FlowEvidence, thresholds: FlowThresholds | None = None) -> dict[str, Any]:
    th = thresholds or FlowThresholds()
    now = evidence.as_of
    agents = {a.agent_id: a for a in evidence.agents}
    desk_of = {d.agent_id: d for d in evidence.desks if d.agent_id}
    run_of = {r.task_id: r for r in evidence.runs}
    approval_of: dict[str, FlowApproval] = {}
    for a in sorted(evidence.approvals, key=lambda x: (x.created_at, x.approval_id)):
        approval_of[a.task_id] = a  # newest wins

    items: list[dict[str, Any]] = []
    for task in evidence.tasks:
        item = _task_item(task, agents, desk_of, run_of.get(task.task_id), approval_of.get(task.task_id),
                          evidence.can_see_approval_text, th, now)
        if item is not None:
            items.append(item)
    for lead in evidence.leads:
        item = _lead_item(lead, agents, desk_of, th, now)
        if item is not None:
            items.append(item)
    for order in evidence.orders:
        item = _order_item(order, th, now)
        if item is not None:
            items.append(item)

    items.sort(key=lambda i: (
        SEVERITY_RANK.get(i["stuck"]["severity"], 2) if i["stuck"] else 3,
        -_epoch(i["since"]), i["key"],
    ))
    truncated = evidence.truncated or len(items) > MAX_ITEMS
    items = items[:MAX_ITEMS]
    return {
        "items": items,
        "rooms": room_rollup(items),
        "totals": flow_totals(items),
        "truncated": truncated,
    }


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _task_item(task: FlowTask, agents: dict[str, FlowAgent], desk_of: dict[str, FlowDesk],
               run: FlowRun | None, approval: FlowApproval | None, show_text: bool,
               th: FlowThresholds, now: datetime) -> dict[str, Any] | None:
    agent = agents.get(task.agent_id) if task.agent_id else None
    if task.agent_id and agent is None:
        return None  # a private agent the viewer cannot see: never reveal its work
    desk = desk_of.get(task.agent_id) if task.agent_id else None
    location = _location(desk, agent) if desk else None
    reason = None if desk else ("plain_runtime" if task.agent_id is None else "agent_has_no_desk")
    key, title = f"task:{task.task_id}", task.title[:160]

    def make(stage: str, since: datetime, stuck: dict | None, waiting: dict | None = None) -> dict[str, Any]:
        return _item(key, "task", task.task_id, title, stage, task.status, since, task.created_at,
                     location, reason, stuck, waiting)

    paused = agent is not None and agent.status == "paused"
    if task.status == "completed":
        return make("done", task.updated_at, None) if now - task.updated_at <= th.recent_done else None
    if task.status == "cancelled":
        return None
    if task.status == "failed":
        if now - task.updated_at > th.failed_window:
            return None
        message = _short(run.result, 300) if run and run.status == "failed" else None
        return make("blocked", task.updated_at, _stuck(
            "run_failed", "critical", task.updated_at, now, th.failed_window, "retry_task",
            error=message, run_id=run.run_id if run else None))
    if paused:
        return make("blocked", task.updated_at, _stuck(
            "agent_paused", "warning", task.updated_at, now, None, "resume_agent", agent_name=agent.name))
    if task.status == "waiting_approval":
        live = approval is not None and approval.status == "pending" and (
            approval.expires_at is None or approval.expires_at > now)
        if live:
            waited = now - approval.created_at
            waiting = {"code": "approval_pending", "since": _iso(approval.created_at),
                       "approval_id": approval.approval_id,
                       "action": _short(approval.action, 160) if show_text else None,
                       "expires_at": _iso(approval.expires_at) if approval.expires_at else None}
            stuck = _stuck("approval_waiting", "warning", approval.created_at, now, th.approval_wait,
                           "decide_approval", approval_id=approval.approval_id,
                           approval_action=waiting["action"]) if waited >= th.approval_wait else None
            return make("waiting_human", approval.created_at, stuck, waiting)
        since = approval.expires_at if approval and approval.expires_at else task.updated_at
        return make("blocked", since, _stuck(
            "approval_expired" if approval else "approval_missing", "critical", since, now, None,
            "retry_task", approval_id=approval.approval_id if approval else None))
    if task.status == "running":
        if run is None or run.status != "running":
            return make("arrived", task.updated_at, None)  # a running task without a running run proves nothing
        stuck = _stuck("run_silent", "warning", run.updated_at, now, th.run_silent, "open_task",
                       run_id=run.run_id) if now - run.updated_at >= th.run_silent else None
        return make("in_office", run.updated_at, stuck)
    # created / queued
    stuck = _stuck("queued_too_long", "warning", task.updated_at, now, th.queue_wait, "open_task"
                   ) if now - task.updated_at >= th.queue_wait else None
    return make("arrived", task.updated_at, stuck)


def _lead_item(lead: FlowLead, agents: dict[str, FlowAgent], desk_of: dict[str, FlowDesk],
               th: FlowThresholds, now: datetime) -> dict[str, Any] | None:
    key = f"lead:{lead.lead_id}"
    closed = lead.status in CLOSED_LEAD_STATUSES
    if closed and now - lead.updated_at > th.recent_done:
        return None
    owner = lead.assigned_to
    agent = agents.get(owner) if owner else None
    desk = desk_of.get(owner) if owner else None
    location = _location(desk, agent) if desk else None
    if desk:
        reason = None
    elif owner is None:
        reason = "no_owner"
    elif agent is not None:
        reason = "agent_has_no_desk"
    else:
        reason = "owner_is_person"
    if closed:
        stage, stuck = "done", None
    elif lead.status == "new" and owner is None:
        stage = "arrived"
        stuck = _stuck("lead_unassigned", "warning", lead.created_at, now, th.lead_unassigned, "assign_owner"
                       ) if now - lead.created_at >= th.lead_unassigned else None
    else:
        stage = "in_office" if desk else "arrived"
        stuck = _stuck("lead_idle", "warning", lead.updated_at, now, th.lead_idle, "open_lead",
                       lead_status=lead.status) if now - lead.updated_at >= th.lead_idle else None
    return _item(key, "lead", lead.lead_id, lead.title[:160], stage, lead.status, lead.updated_at,
                 lead.created_at, location, reason, stuck)


def _order_item(order: FlowOrder, th: FlowThresholds, now: datetime) -> dict[str, Any] | None:
    key = f"order:{order.order_id}"
    title = f"Order {order.external_id}"[:160]
    if order.status in DONE_ORDER_STATUSES:
        since = order.synced_at or order.purchased_at
        if now - since > th.recent_done:
            return None
        return _item(key, "order", order.order_id, title, "done", order.status, since, order.purchased_at,
                     None, "no_order_office", None)
    if order.status not in OPEN_ORDER_STATUSES:
        return None
    stuck = None
    if order.ship_by is not None and now - order.ship_by >= th.order_ship_grace and order.ship_by < now:
        stuck = _stuck("order_ship_overdue", "critical", order.ship_by, now, th.order_ship_grace,
                       "open_order", order_status=order.status)
    return _item(key, "order", order.order_id, title, "arrived", order.status, order.purchased_at,
                 order.purchased_at, None, "no_order_office", stuck)


def room_rollup(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rooms: dict[str, dict[str, Any]] = {}
    for item in items:
        loc = item["location"]
        if loc is None:
            continue
        room = rooms.setdefault(loc["room_id"], {
            "room_id": loc["room_id"], "floor_id": loc["floor_id"], "building_id": loc["building_id"],
            "items": 0, "waiting": 0, "stuck": 0, "worst": None,
        })
        if item["stage"] == "done":
            continue
        room["items"] += 1
        if item["stage"] == "waiting_human":
            room["waiting"] += 1
        if item["stuck"]:
            room["stuck"] += 1
            sev = item["stuck"]["severity"]
            if room["worst"] is None or SEVERITY_RANK[sev] < SEVERITY_RANK[room["worst"]]:
                room["worst"] = sev
    return rooms


def flow_totals(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage = Counter(i["stage"] for i in items)
    stuck = [i["stuck"] for i in items if i["stuck"]]
    return {
        "items": len(items),
        "by_stage": {s: by_stage.get(s, 0) for s in STAGES},
        "by_kind": dict(Counter(i["kind"] for i in items)),
        "waiting_human": by_stage.get("waiting_human", 0),
        "stuck": len(stuck),
        "stuck_by_code": dict(Counter(s["code"] for s in stuck)),
        "critical": sum(1 for s in stuck if s["severity"] == "critical"),
        "unlocated": sum(1 for i in items if i["location"] is None and i["stage"] != "done"),
    }


def diff_flow(previous: dict[str, dict[str, Any]], current: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Items that are new or changed in stage, location, stuck code or status; keys that vanished."""
    def signature(item: dict[str, Any]) -> tuple:
        loc = item["location"]
        return (item["stage"], item["status"], loc["room_id"] if loc else None,
                item["stuck"]["code"] if item["stuck"] else None)

    changed = [i for i in current if i["key"] not in previous or signature(previous[i["key"]]) != signature(i)]
    gone = sorted(set(previous) - {i["key"] for i in current})
    return changed, gone


def is_flow_event(event_type: str) -> bool:
    return event_type.startswith(FLOW_EVENT_PREFIXES)


# --------------------------------------------------------------------------- loading


def _table_state(session: Session, sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """Run one optional read inside a savepoint; a missing table or privilege is not_connected."""
    try:
        with session.begin_nested():
            return "connected", list(session.execute(text(sql), params).all())
    except Exception:  # noqa: BLE001 - any storage failure of an optional source degrades only that source
        return "not_connected", []


def load_flow_evidence(session: Session, context: TenantContext, now: datetime, th: FlowThresholds, *,
                       can_leads: bool, can_orders: bool, can_approvals: bool) -> FlowEvidence:
    p = {"t": context.tenant_id, "w": context.workspace_id}
    evidence = FlowEvidence(as_of=now, can_see_approval_text=can_approvals)
    for row in session.execute(text(
        "select id, name, status from agent_profiles where tenant_id = :t and workspace_id = :w "
        "order by id limit :n"), {**p, "n": MAX_ROWS}):
        evidence.agents.append(FlowAgent(row.id, row.name, row.status))
    visible = {a.agent_id for a in evidence.agents}
    for row in session.execute(text(
        "select d.id as desk_id, d.room_id, d.agent_id, r.floor_id, r.name as room_name, f.building_id "
        "from office_desks d "
        "join office_rooms r on r.tenant_id = d.tenant_id and r.workspace_id = d.workspace_id and r.id = d.room_id "
        "join office_floors f on f.tenant_id = r.tenant_id and f.workspace_id = r.workspace_id and f.id = r.floor_id "
        "where d.tenant_id = :t and d.workspace_id = :w order by d.id limit :n"), {**p, "n": MAX_ROWS}):
        evidence.desks.append(FlowDesk(row.desk_id, row.room_id, row.room_name, row.floor_id, row.building_id, row.agent_id))
    task_rows = session.execute(text(
        "select id, agent_id, title, status, created_at, updated_at from tasks "
        "where tenant_id = :t and workspace_id = :w and archived_at is null "
        "and (status in ('created', 'queued', 'running', 'waiting_approval') "
        "     or (status = 'failed' and updated_at > :failed_since) "
        "     or (status = 'completed' and updated_at > :done_since)) "
        "order by updated_at desc, id desc limit :n"),
        {**p, "failed_since": now - th.failed_window, "done_since": now - th.recent_done, "n": MAX_ROWS + 1}).all()
    if len(task_rows) > MAX_ROWS:
        evidence.truncated = True
        task_rows = task_rows[:MAX_ROWS]
    evidence.tasks = [FlowTask(r.id, r.title, r.status, r.agent_id, r.created_at, r.updated_at)
                      for r in task_rows if r.agent_id is None or r.agent_id in visible]
    task_ids = [t.task_id for t in evidence.tasks]
    if task_ids:
        for row in session.execute(text(
            "select distinct on (task_id) id, task_id, status, updated_at, result from agent_runs "
            "where tenant_id = :t and workspace_id = :w and task_id = any(:ids) "
            "order by task_id, created_at desc, id desc"), {**p, "ids": task_ids}):
            evidence.runs.append(FlowRun(row.id, row.task_id, row.status, row.updated_at, row.result))
        for row in session.execute(text(
            "select id, task_id, action, status, expires_at, created_at from approvals "
            "where tenant_id = :t and workspace_id = :w and status in ('pending', 'expired') "
            "and task_id = any(:ids) order by created_at desc, id desc"), {**p, "ids": task_ids}):
            evidence.approvals.append(
                FlowApproval(row.id, row.task_id, row.action or "", row.status, row.expires_at, row.created_at))
    evidence.sources["tasks"] = "connected"
    evidence.sources["approvals"] = "connected" if can_approvals else "no_access"

    if not can_leads:
        evidence.sources["leads"] = "no_access"
    else:
        state, rows = _table_state(session, (
            "select id, company_name, contact_name, status, assigned_to, created_at, updated_at from sales_leads "
            "where tenant_id = :t and workspace_id = :w "
            "and (status in ('new', 'qualifying', 'qualified', 'quoted', 'negotiating') or updated_at > :done_since) "
            "order by updated_at desc, id desc limit :n"),
            {**p, "done_since": now - th.recent_done, "n": MAX_ROWS})
        evidence.sources["leads"] = state
        for r in rows:
            title = (r.company_name or r.contact_name or "").strip() or f"Lead {r.id[-6:]}"
            evidence.leads.append(FlowLead(r.id, title, r.status, r.assigned_to, r.created_at, r.updated_at))

    if not can_orders:
        evidence.sources["orders"] = "no_access"
    else:
        state, rows = _table_state(session, (
            "select o.id, o.external_id, o.status, o.purchased_at, o.ship_by, o.synced_at from commerce_orders o "
            "where o.tenant_id = :t and o.workspace_id = :w and o.purchased_at > :since "
            "and (o.status in ('pending', 'unshipped', 'partially_shipped') "
            "     or (o.status in ('shipped', 'delivered') and o.synced_at > :done_since)) "
            "order by o.purchased_at desc, o.id desc limit :n"),
            {**p, "since": now - timedelta(days=30), "done_since": now - th.recent_done, "n": MAX_ROWS})
        evidence.sources["orders"] = state
        if state == "connected":
            has_connector = _table_state(session, (
                "select 1 from commerce_connectors where tenant_id = :t and workspace_id = :w limit 1"), p)
            if has_connector[0] != "connected" or not has_connector[1]:
                evidence.sources["orders"] = "not_connected"
        for r in rows:
            evidence.orders.append(FlowOrder(r.id, r.external_id, r.status, r.purchased_at, r.ship_by, r.synced_at))
    evidence.cursor = session.execute(text(
        "select id from domain_events where tenant_id = :t and workspace_id = :w "
        "order by created_at desc, id desc limit 1"), p).scalar()
    return evidence


# --------------------------------------------------------------------------- team roster


def build_team(
    *, profiles: list[Any], desks: list[FlowDesk], all_desks_in_scope: list[FlowDesk],
    presence: dict[str, dict[str, Any]], stuck_by_agent: dict[str, dict[str, Any]],
    skills: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Members of the desks in ``desks`` (each with its agent's profile, skills and state).

    ``profiles`` are the agent profiles the viewer may see; a desk whose agent is hidden is
    counted as ``hidden`` and never named. ``presence`` maps agent id to its presence item;
    ``stuck_by_agent`` maps agent id to the stuck block of its most urgent item.
    """
    by_id = {p.id: p for p in profiles}
    members: list[dict[str, Any]] = []
    vacant: list[dict[str, Any]] = []
    hidden = 0
    for desk in sorted(desks, key=lambda d: (d.room_name, d.desk_id)):
        if desk.agent_id is None:
            vacant.append({"desk_id": desk.desk_id, "room_id": desk.room_id, "room_name": desk.room_name})
            continue
        profile = by_id.get(desk.agent_id)
        if profile is None:
            hidden += 1
            continue
        pres = presence.get(profile.id)
        stuck = stuck_by_agent.get(profile.id)
        listed = skills.get(profile.id) if skills is not None else None
        members.append({
            "agent_id": profile.id, "name": profile.name, "icon": profile.icon, "color": profile.color,
            "category": str(profile.category.value if hasattr(profile.category, "value") else profile.category),
            "department": profile.department,
            "description": _short(profile.description, 240) or "",
            "agent_status": str(profile.status.value if hasattr(profile.status, "value") else profile.status),
            "state": pres["state"] if pres else "unknown",
            "state_reason": pres["provenance"]["reason"] if pres else None,
            "last_verified_at": pres["last_verified_at"] if pres else None,
            "stale": pres["stale"] if pres else False,
            "task": pres["task"] if pres else None,
            "stuck": stuck,
            "desk": {"id": desk.desk_id, "room_id": desk.room_id, "room_name": desk.room_name,
                     "floor_id": desk.floor_id, "building_id": desk.building_id},
            "skills": listed,
            "skills_total": len(profile.allowed_skills),
            "tools_total": len(profile.allowed_tools),
        })
    states = Counter(m["state"] for m in members)
    return {
        "members": members,
        "vacant_desks": vacant,
        "hidden_assignments": hidden,
        "counts": {
            "agents": len(members), "vacant_desks": len(vacant), "hidden": hidden,
            "working": states.get("working", 0), "waiting_approval": states.get("waiting_approval", 0),
            "blocked": states.get("blocked", 0), "stuck": sum(1 for m in members if m["stuck"]),
        },
    }
