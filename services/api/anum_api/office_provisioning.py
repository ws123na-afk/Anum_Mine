"""Governed agent provisioning for the ANUM MINE headquarters (docs/ANUM_MINE_AGENT_PROVISIONING.md).

Pure functions and the template catalogue. Nothing here reads a database or creates anything:

* ``TEMPLATES`` is the data driven catalogue of roles an owner can add (role, department,
  description, instructions, skills, tools, autonomy, route, budget, the kind of work it owns).
  Every tool and skill is named by its registry id; a capability the platform does not have
  yet is listed as a ``Need`` (never invented as a tool) and the agent is marked ``limited``.
* ``detect_gaps`` turns stored evidence (live flow items, rooms, agents, provision rows) into
  gaps. A gap exists only because a record backs it; a source that is not connected is reported
  as such and never counted.
* ``plan_placement`` picks the desk: an existing vacant desk in the room linked to the role's
  department, else a new desk if the room has capacity, else nothing (the owner picks a room).
* ``proposal_hash`` binds a proposal to the tenant, workspace, confirming user and exact content.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

CATALOG_VERSION = 1
TEMPLATE_PREFIX = "hq:"
WORK_ROOM_KINDS = ("office", "workshop", "warehouse")
NOT_DONE = ("arrived", "in_office", "waiting_human", "blocked")
APPROVAL_CODES = ("approval_waiting", "approval_expired", "approval_missing")


@dataclass(frozen=True)
class Need:
    """A capability the role wants that the platform has no tool or skill for yet."""

    id: str
    en: str
    ar: str
    # Registry names that would satisfy it. A test keeps the list honest: if a tool matching one
    # of these appears, the template must be updated to use it instead of calling it missing.
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Template:
    id: str
    department: str              # stored on the agent; lower case, matches rooms' team department
    room_department: str | None  # the office team link this role belongs to
    name_en: str
    name_ar: str
    description_en: str
    description_ar: str
    instructions: str
    icon: str
    color: str
    model_route: str
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    missing: tuple[Need, ...]
    work_kinds: tuple[str, ...] = ()
    knowledge_mode: str = "none"
    write_autonomy: str = "ask"
    monthly_cost_limit_usd: float = 5.0


SAFETY = (
    " You work inside the permissions of your tools. You never approve your own actions or anyone's approval,"
    " you ask a person when a step needs one, and you say plainly when you lack a tool or data for the job."
)

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="orders_desk", department="operations", room_department="operations",
        name_en="Orders Desk", name_ar="مكتب الطلبات",
        description_en="Order intake, order status and shipping follow-up from the connected store.",
        description_ar="استلام الطلبات ومتابعة حالتها وشحنها من المتجر المتصل.",
        instructions=("You own incoming orders. Read orders and sales figures with the commerce tools, report which are "
                      "open, late or past their ship by date, and draft customer follow-ups for a person to review." + SAFETY),
        icon="shopping-cart", color="#b45309", model_route="fast",
        tools=("commerce.list_orders", "commerce.get_order", "commerce.sales_summary", "draft_reply", "draft_whatsapp_reply"),
        skills=("anum.commerce", "anum.document-drafting"),
        missing=(
            Need("order_status_update", "A tool that changes an order's status in the store",
                 "أداة تغيّر حالة الطلب في المتجر", ("commerce.*.update_order*", "commerce.update_order*", "commerce.*.set_status")),
            Need("carrier_tracking", "A carrier tracking lookup for shipping follow-up",
                 "أداة لتتبع الشحنات لدى شركة النقل", ("carrier.*", "shipping.*", "tracking.*")),
        ),
        work_kinds=("orders",),
    ),
    Template(
        id="sales_followup", department="sales", room_department="sales",
        name_en="Sales Follow-up", name_ar="متابعة المبيعات",
        description_en="Follows up new and quiet sales leads and drafts the next message for a person to send.",
        description_ar="يتابع العملاء المحتملين الجدد والخاملين ويكتب الرسالة التالية ليراجعها شخص.",
        instructions=("You follow up sales leads. Read WhatsApp conversations and email about a lead, say what the next step is "
                      "and draft the reply. Never send a message yourself; a person sends it." + SAFETY),
        icon="phone", color="#1d4ed8", model_route="fast",
        tools=("list_whatsapp_conversations", "read_whatsapp", "draft_whatsapp_reply", "search_email", "read_email",
               "draft_reply", "team.assign_task"),
        skills=("anum.whatsapp-inbox", "anum.email", "anum.team-assign"),
        missing=(
            Need("lead_records", "Tools that read and update sales leads and quotes",
                 "أدوات لقراءة العملاء المحتملين وعروض الأسعار وتحديثها", ("sales.*", "lead.*", "leads.*")),
        ),
        work_kinds=("leads",),
    ),
    Template(
        id="support_triage", department="support", room_department="support",
        name_en="Customer Support Triage", name_ar="فرز دعم العملاء",
        description_en="Sorts incoming customer messages by topic and urgency and drafts first replies.",
        description_ar="يفرز رسائل العملاء الواردة حسب الموضوع والأهمية ويكتب الردود الأولى.",
        instructions=("You triage customer messages. Classify each by topic and urgency, summarise it in one sentence, draft a "
                      "first reply, and hand work to a person with the assign tool when it needs one." + SAFETY),
        icon="mail", color="#0f766e", model_route="fast",
        tools=("classify_mail", "summarize_mail", "search_email", "read_email", "business.draft_support_reply",
               "list_whatsapp_conversations", "read_whatsapp", "draft_whatsapp_reply", "team.assign_task"),
        skills=("anum.email", "anum.whatsapp-inbox", "anum.team-assign"),
        missing=(
            Need("ticket_updates", "A tool that changes a support ticket's status",
                 "أداة تغيّر حالة تذكرة الدعم", ("support.*", "ticket.*", "tickets.*")),
        ),
    ),
    Template(
        id="finance_reconciliation", department="finance", room_department="finance",
        name_en="Finance Reconciliation Clerk", name_ar="موظف مطابقة مالية",
        description_en="Reads transactions and upcoming bills and reports what does not match, for a person to resolve.",
        description_ar="يقرأ المعاملات والفواتير القادمة ويبلّغ عما لا يتطابق ليعالجه شخص.",
        instructions=("You reconcile money records. Read transactions and bills, list what is unmatched, duplicated or late, and "
                      "explain each in plain words. You never move money and never change a record." + SAFETY),
        icon="chart", color="#15803d", model_route="capable",
        tools=("finance.summary", "finance.transactions.search", "finance.bills.upcoming", "finance.safe_to_spend"),
        skills=("anum.finance", "anum.document-drafting"),
        missing=(
            Need("statement_matching", "Bank statement matching against the ledger",
                 "مطابقة كشف الحساب البنكي مع السجلات", ("finance.reconcile*", "finance.match*", "bank.*")),
        ),
        write_autonomy="off",
    ),
    Template(
        id="fleet_maintenance", department="tyre_engineering", room_department="tyre_engineering",
        name_en="Fleet Maintenance Coordinator", name_ar="منسق صيانة الأسطول",
        description_en="Tyre engineering: reads fleet, inspections and alerts, and plans rotations and purchases.",
        description_ar="هندسة الإطارات: يقرأ الأسطول والفحوصات والتنبيهات ويخطط للتدوير والشراء.",
        instructions=("You coordinate tyre and vehicle maintenance. Use the tyre tools to find what is due, what is worn and what "
                      "to buy. State the vehicle, the tyre position and the reason for each recommendation." + SAFETY),
        icon="truck", color="#0f766e", model_route="capable",
        tools=("tyre_fleet_summary", "tyres_due", "tyre_alerts", "purchase_plan", "tyre_vehicles", "tyre_vehicle_detail",
               "tyre_inspections", "tyre_work_orders", "tyre_checklists", "tyre_maintenance_costs"),
        skills=("anum.tyre-planning", "anum.document-drafting"),
        missing=(),
        knowledge_mode="workspace",
    ),
    Template(
        id="operations_dispatcher", department="operations", room_department="operations",
        name_en="Operations Dispatcher", name_ar="مسؤول توزيع العمليات",
        description_en="Turns stuck or unowned work into assigned tasks for the right person.",
        description_ar="يحوّل الأعمال العالقة أو بلا مالك إلى مهام مسندة للشخص المناسب.",
        instructions=("You dispatch operations work. Read the open work you are shown, decide who should own each item, and create "
                      "the task for that person with the assign tool. Explain each choice in one sentence." + SAFETY),
        icon="briefcase", color="#7c3aed", model_route="fast",
        tools=("team.assign_task", "call.search_log", "search_email"),
        skills=("anum.team-assign", "anum.document-drafting"),
        missing=(
            Need("task_reassign", "A tool that moves an existing task to another person or agent",
                 "أداة تنقل مهمة قائمة إلى شخص أو وكيل آخر", ("team.reassign*", "tasks.reassign*", "task.reassign*")),
            Need("route_planning", "Route and schedule planning for dispatch",
                 "تخطيط المسارات والجداول للتوزيع", ("dispatch.*", "route.*", "routing.*")),
        ),
    ),
    Template(
        id="compliance_reviewer", department="compliance", room_department=None,
        name_en="Compliance Reviewer", name_ar="مراجع الامتثال",
        description_en="Prepares review notes on held approvals and money rules. It never decides an approval.",
        description_ar="يعدّ ملاحظات مراجعة للموافقات المعلّقة وقواعد المال. لا يقرر أي موافقة أبدًا.",
        instructions=("You prepare compliance review notes. Read the rules and the held requests you are shown and write what a "
                      "person should check before deciding. You never approve, reject or release anything." + SAFETY),
        icon="file-text", color="#9f1239", model_route="capable",
        tools=("finance.guardian.rules", "finance.guardian.check", "classify_mail"),
        skills=("anum.document-drafting",),
        missing=(
            Need("audit_log_read", "A tool that reads the audit log",
                 "أداة لقراءة سجل التدقيق", ("audit.*",)),
            Need("policy_register", "A policy register to check requests against",
                 "سجل سياسات للتحقق من الطلبات", ("policy.*", "policies.*")),
        ),
        knowledge_mode="workspace", write_autonomy="off",
    ),
)

TEMPLATE_BY_ID = {t.id: t for t in TEMPLATES}


# --------------------------------------------------------------------------- capabilities


@dataclass
class Resolved:
    """Template against this deployment and this person: what is attached and what is missing."""

    tools: list[dict[str, Any]] = field(default_factory=list)      # every wanted tool with its state
    skills: list[dict[str, Any]] = field(default_factory=list)
    attach_tools: list[str] = field(default_factory=list)
    attach_skills: list[str] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)

    @property
    def limited(self) -> bool:
        return bool(self.missing)


def resolve_capabilities(
    template: Template, *, tool_info: Mapping[str, Mapping[str, Any]], skill_info: Mapping[str, Mapping[str, Any]],
) -> Resolved:
    """``tool_info[name]`` = {risk, category, refusal} for a registered tool; ``skill_info[id]`` = {name, source,
    required_tools} for an available skill. Only registered, permitted tools and available skills are attached."""
    out = Resolved()
    for name in template.tools:
        info = tool_info.get(name)
        if info is None:
            state, why = "missing", "not_registered"
        elif info.get("refusal"):
            state, why = "missing", "not_permitted"
        else:
            state, why = "present", None
        out.tools.append({"name": name, "state": state, "reason": why, "risk": (info or {}).get("risk"),
                          "category": (info or {}).get("category"),
                          "detail": (info or {}).get("refusal")})
        if state == "present":
            out.attach_tools.append(name)
        else:
            out.missing.append({"kind": "tool", "id": name, "reason": why,
                                "en": f"The tool {name} is {'not in this deployment' if why == 'not_registered' else 'not allowed for you'}",
                                "ar": f"الأداة {name} " + ("غير متوفرة في هذا النشر" if why == "not_registered" else "غير مسموحة لك")})
    allowed = set(out.attach_tools) | {"anum.respond"}
    for skill_id in template.skills:
        info = skill_info.get(skill_id)
        if info is None:
            state, why = "missing", "not_available"
        else:
            needs = sorted(set(info.get("required_tools") or ()) - allowed) if info.get("source") == "builtin" else []
            state, why = ("missing", "needs_tools") if needs else ("present", None)
        out.skills.append({"id": skill_id, "name": (info or {}).get("name", skill_id), "state": state, "reason": why,
                           "source": (info or {}).get("source")})
        if state == "present":
            out.attach_skills.append(skill_id)
        else:
            out.missing.append({"kind": "skill", "id": skill_id, "reason": why,
                                "en": f"The skill {skill_id} cannot be attached ({'not available' if why == 'not_available' else 'a tool it needs is missing'})",
                                "ar": f"لا يمكن إرفاق المهارة {skill_id}"})
    for need in template.missing:
        out.missing.append({"kind": "capability", "id": need.id, "reason": "no_tool_exists", "en": need.en, "ar": need.ar})
    return out


# --------------------------------------------------------------------------- the proposal


def agent_fields(template: Template, resolved: Resolved, lang: str) -> dict[str, Any]:
    """The exact AgentProfileCreate payload the confirm call will send to the agent create path."""
    ar = lang == "ar"
    return {
        "name": template.name_ar if ar else template.name_en,
        "icon": template.icon,
        "color": template.color,
        "category": "work",
        "department": template.department,
        "description": template.description_ar if ar else template.description_en,
        "instructions": template.instructions,
        "model_route": template.model_route,
        "allowed_skills": sorted(resolved.attach_skills),
        "allowed_tools": sorted(resolved.attach_tools),
        # Least privilege: reading is automatic, writing asks (or is off), outward actions are off.
        "autonomy": {"read": "auto", "write": template.write_autonomy, "external": "off"},
        "knowledge_mode": template.knowledge_mode,
        "knowledge_sources": [],
        "sharing": "workspace",
        "template_id": (TEMPLATE_PREFIX + template.id)[:40],
    }


def plan_placement(
    template: Template, rooms: Iterable[Mapping[str, Any]], desks: Iterable[Mapping[str, Any]],
    preferred_room_id: str | None = None,
) -> dict[str, Any]:
    """Where the agent sits. Pure over the rooms and desks as stored (raw, nothing redacted)."""
    desk_list = sorted(desks, key=lambda d: d["id"])
    by_room: dict[str, list[Mapping[str, Any]]] = {}
    for d in desk_list:
        by_room.setdefault(d["room_id"], []).append(d)
    work_rooms = sorted((r for r in rooms if r["kind"] in WORK_ROOM_KINDS), key=lambda r: (r["name"].lower(), r["id"]))

    def fit(room: Mapping[str, Any]) -> dict[str, Any]:
        mine = by_room.get(room["id"], [])
        vacant = next((d for d in mine if d["agent_id"] is None), None)
        if vacant is not None:
            return {"mode": "existing_desk", "desk_id": vacant["id"], "desk_label": vacant["label"],
                    "desk_revision": vacant["revision"]}
        if len(mine) < room["capacity"]:
            labels = {d["label"] for d in mine}
            label = template.name_en if template.name_en not in labels else f"{template.name_en} {len(mine) + 1}"
            return {"mode": "new_desk", "desk_id": f"{room['id'][:50]}-{template.id}"[:80], "desk_label": label[:100],
                    "desk_revision": None}
        return {"mode": "none", "desk_id": None, "desk_label": None, "desk_revision": None}

    candidates = []
    for r in work_rooms:
        f = fit(r)
        candidates.append({"room_id": r["id"], "room_name": r["name"], "floor_id": r["floor_id"],
                           "building_id": r["building_id"], "team_department": r.get("team_department"),
                           "linked": template.room_department is not None and r.get("team_department") == template.room_department,
                           "fits": f["mode"] != "none", "mode": f["mode"], "vacant": sum(1 for d in by_room.get(r["id"], []) if d["agent_id"] is None),
                           "desks": len(by_room.get(r["id"], [])), "capacity": r["capacity"]})
    chosen = None
    if preferred_room_id is not None:
        chosen = next((c for c in candidates if c["room_id"] == preferred_room_id), None)
        if chosen is None:
            return {"mode": "invalid_room", "reason": "room_not_a_work_area", "candidates": candidates, "room_id": preferred_room_id}
        reason = "chosen_by_you"
    else:
        chosen = next((c for c in candidates if c["linked"] and c["fits"]), None)
        reason = "linked_room"
    if chosen is None or not chosen["fits"]:
        linked = [c for c in candidates if c["linked"]]
        why = ("linked_rooms_full" if linked else "no_linked_room") if preferred_room_id is None else "room_full"
        return {"mode": "none", "reason": why, "candidates": candidates, "room_id": None}
    f = fit(next(r for r in work_rooms if r["id"] == chosen["room_id"]))
    return {**f, "reason": reason, "room_id": chosen["room_id"], "room_name": chosen["room_name"],
            "floor_id": chosen["floor_id"], "building_id": chosen["building_id"], "candidates": candidates}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def proposal_hash(tenant_id: str, workspace_id: str, user_id: str, content: Mapping[str, Any]) -> str:
    """Bound to who is confirming and where: another owner, workspace or edited content cannot reuse it."""
    return hashlib.sha256(canonical({"t": tenant_id, "w": workspace_id, "u": user_id, "p": content}).encode()).hexdigest()


# --------------------------------------------------------------------------- gaps


def norm_department(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "_")


@dataclass(frozen=True)
class AgentFact:
    agent_id: str
    name: str
    status: str
    department: str | None
    template_id: str | None


def _reason(code: str, **facts: Any) -> dict[str, Any]:
    return {"code": code, **{k: v for k, v in facts.items() if v is not None}}


def detect_gaps(
    *, items: list[Mapping[str, Any]], sources: Mapping[str, str], rooms: list[Mapping[str, Any]],
    agents: list[AgentFact], provisions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Gaps (kinds of work or departments with nobody assigned) and per-role coverage.

    ``items`` are live flow items; ``provisions`` are rows of the provisioning ledger. An item counts
    only if it is not finished. Nothing is guessed: no connector means no orders and no gap.
    """
    live_agents = {a.agent_id: a for a in agents}
    active_provision: dict[str, Mapping[str, Any]] = {}
    for p in provisions:
        agent = live_agents.get(p.get("agent_id") or "")
        if p["status"] == "active" and agent is not None:
            active_provision[p["template_id"]] = {**p, "agent_name": agent.name, "agent_status": agent.status}
    open_items = [i for i in items if i["stage"] in NOT_DONE]

    evidence: dict[str, list[dict[str, Any]]] = {t.id: [] for t in TEMPLATES}

    orders = [i for i in open_items if i["kind"] == "order" and i["location"] is None]
    if sources.get("orders") == "connected" and orders:
        overdue = sum(1 for i in orders if i["stuck"] and i["stuck"]["code"] == "order_ship_overdue")
        evidence["orders_desk"].append(_reason("orders_without_office", count=len(orders), stuck=overdue,
                                               critical=overdue if overdue else None))
    leads = [i for i in open_items if i["kind"] == "lead" and i["location_reason"] == "no_owner"]
    if sources.get("leads") == "connected" and leads:
        idle = sum(1 for i in leads if i["stuck"])
        evidence["sales_followup"].append(_reason("leads_without_owner", count=len(leads), stuck=idle))
    lost = [i for i in open_items if i["kind"] == "task" and i["stuck"] and i["location"] is None]
    if lost:
        evidence["operations_dispatcher"].append(_reason(
            "stuck_without_office", count=len(lost), critical=sum(1 for i in lost if i["stuck"]["severity"] == "critical") or None))
    held = [i for i in open_items if i["stuck"] and i["stuck"]["code"] in APPROVAL_CODES]
    if held:
        evidence["compliance_reviewer"].append(_reason(
            "approvals_stuck", count=len(held), critical=sum(1 for i in held if i["stuck"]["severity"] == "critical") or None))

    for t in TEMPLATES:
        if t.room_department is None:
            continue
        linked = [r for r in rooms if r.get("team_department") == t.room_department]
        if not linked:
            continue
        staffed = any(a.status == "active" and norm_department(a.department) == t.department for a in agents)
        provisioned = t.id in active_provision and active_provision[t.id]["agent_status"] == "active"
        if not staffed and not provisioned:
            evidence[t.id].append(_reason("empty_department", department=t.room_department,
                                          rooms=[{"id": r["id"], "name": r["name"]} for r in linked][:10]))

    gaps: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for t in TEMPLATES:
        reasons = evidence[t.id]
        handler = active_provision.get(t.id)
        flow_based = [r for r in reasons if r["code"] != "empty_department"]
        # Flow work is routed only through a confirmed handler, so a handler clears every reason.
        open_reasons = [] if handler is not None else reasons
        severity = ("critical" if any(r.get("critical") for r in open_reasons) else "warning") if open_reasons else None
        needs_state = None
        if t.id == "orders_desk" and sources.get("orders") in ("not_connected", "no_access") and handler is None:
            needs_state = sources["orders"]
        coverage.append({
            "template_id": t.id, "work_kinds": list(t.work_kinds),
            "state": "gap" if open_reasons else ("covered" if handler else ("source_" + needs_state if needs_state else "no_signal")),
            "handler": ({"agent_id": handler["agent_id"], "agent_name": handler["agent_name"],
                         "agent_status": handler["agent_status"], "desk_id": handler.get("desk_id")} if handler else None),
        })
        if open_reasons:
            gaps.append({"key": f"gap:{t.id}", "template_id": t.id, "severity": severity, "reasons": open_reasons,
                         "flow_based": bool(flow_based)})
    gaps.sort(key=lambda g: (0 if g["severity"] == "critical" else 1, g["template_id"]))
    return {"gaps": gaps, "coverage": coverage}
