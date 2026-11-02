"""The setup plan's "For you to do by hand" as a live checklist (docs/setup-plan.md#your-checklist).

``GET /api/v1/setup-plans/{id}/checklist`` reads real state, never a stored tick: the people
in this workspace and pending invitations, whether the delegated approvers are named, the
two-person rule, each integration of the plan's list (from the integrations setup state), the
documents in each of the plan's knowledge folders, the webhook triggers of the plan's webhook
automations, how many plan automations are turned on, the WhatsApp auto-send list, the AI
model and the last backups. Each item says ``done``, ``todo``, ``partial``, ``cannot_check``
(ANUM cannot read it, for example two-step sign-in in the sign-in service) or ``no_access``
(the caller may not read that module), with a plain summary and the page to fix it on.
Owners and organization administrators only, like the rest of the plan. Read-only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from .authorization import Permission
from .dependencies import _unit_of_work, has_permission
from .schemas import TenantContext
from .setup_plan_ledger import LedgerState, open_setup_plan_ledger

logger = logging.getLogger(__name__)

State = Literal["done", "todo", "partial", "cannot_check", "no_access"]

# The plan's integration order (manual step m_integrations), as integration setup ids.
PLAN_INTEGRATIONS = (
    "model", "notification_email", "push", "email_gmail", "email_outlook", "email_zoho", "whatsapp", "commerce",
    "meetings_zoom", "meetings_teams", "fleet_data", "speech", "web_search", "external_webhook",
)
# Mail providers count once: any one connected mailbox is enough.
MAIL = ("email_gmail", "email_outlook", "email_zoho")
MEETINGS = ("meetings_zoom", "meetings_teams")
# The roles the plan invites (m_people): four members and one viewer.
WANTED_MEMBERS, WANTED_VIEWERS = 4, 1
# Approval rules the plan delegates; done once each names a person (``user:<id>``).
DELEGATED_RULES = ("ar_refund_small", "ar_quote_small", "ar_commitment")


class ChecklistDetail(BaseModel):
    label: str
    state: State
    note: str | None = None


class ChecklistItem(BaseModel):
    key: str
    check: str | None
    title: str
    detail: str
    state: State
    summary: str
    view: str | None = None
    details: list[ChecklistDetail] = Field(default_factory=list)


class Checklist(BaseModel):
    plan_id: str
    done: int
    total: int
    items: list[ChecklistItem]


def _guard(fn: Callable[[], tuple[State, str, list[ChecklistDetail]]]) -> tuple[State, str, list[ChecklistDetail]]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one module never hides the rest of the checklist
        logger.warning("setup_plan_checklist_failed error_class=%s", type(exc).__name__)
        return "cannot_check", "ANUM could not read this just now. Try again in a minute.", []


def _ledger_targets(context: TenantContext, plan_id: str) -> dict[str, str]:
    with open_setup_plan_ledger(context) as ledger:
        return {e.item_key: e.target_id for e in ledger.entries(context, plan_id) if e.state == LedgerState.APPLIED and e.target_id}


def check_people(context: TenantContext) -> tuple[State, str, list[ChecklistDetail]]:
    with _unit_of_work(context, require_membership=True) as repository:
        members = [m for m in repository.list_memberships(context) if m.active]
        invitations = [i for i in repository.list_invitations(context) if i.status.value == "pending"]
    by_role: dict[str, int] = {}
    for m in members:
        by_role[m.role.lower()] = by_role.get(m.role.lower(), 0) + 1
    members_n, viewers_n = by_role.get("member", 0), by_role.get("viewer", 0)
    details = [
        ChecklistDetail(label="Owners", state="done", note=str(by_role.get("owner", 0))),
        ChecklistDetail(label="Members (the plan names 4)", state="done" if members_n >= WANTED_MEMBERS else "todo", note=str(members_n)),
        ChecklistDetail(label="Viewers (the plan names 1)", state="done" if viewers_n >= WANTED_VIEWERS else "todo", note=str(viewers_n)),
        ChecklistDetail(label="Invitations waiting", state="done" if not invitations else "partial", note=str(len(invitations))),
    ]
    done = members_n >= WANTED_MEMBERS and viewers_n >= WANTED_VIEWERS
    state: State = "done" if done else ("partial" if members_n or viewers_n or invitations else "todo")
    summary = (f"{len(members)} people here: {members_n} members, {viewers_n} viewers"
               + (f", {len(invitations)} invitations waiting." if invitations else "."))
    return state, summary, details


def check_approvers(context: TenantContext, plan_rules: dict[str, str]) -> tuple[State, str, list[ChecklistDetail]]:
    from . import governance as gov

    if not has_permission(context, Permission.ORGANIZATION_READ) and not has_permission(context, Permission.POLICY_MANAGE):
        return "no_access", "Only owners and organization administrators can see approval rules.", []
    with gov.open_governance_store(context) as store:
        rules = {r.id: r for r in store.list_approval_rules(context)}
    details: list[ChecklistDetail] = []
    named = 0
    for key, name in plan_rules.items():
        rule = next((r for r in rules.values() if r.name == name), None)
        if rule is None:
            details.append(ChecklistDetail(label=name, state="todo", note="Not here yet: apply the plan as an organization administrator."))
            continue
        people = [a for step in (getattr(rule, "approver_steps", None) or []) for a in (step.approvers if hasattr(step, "approvers") else step.get("approvers", []))
                  if str(a).startswith("user:")]
        named += bool(people)
        details.append(ChecklistDetail(label=name, state="done" if people else "todo",
                                       note=f"{len(people)} named" if people else "Owners only until you name a person"))
    state: State = "done" if details and named == len(details) else ("partial" if named else "todo")
    return state, f"{named} of {len(details)} delegated rules name a person.", details


def check_two_person(context: TenantContext) -> tuple[State, str, list[ChecklistDetail]]:
    with _unit_of_work(context, require_membership=True) as repository:
        policy = repository.get_approval_policy(context)
        owners = sum(1 for m in repository.list_memberships(context) if m.active and m.role.lower() == "owner")
    if policy.two_person_rule:
        return "done", "On: a high-risk action needs a second owner's approval.", []
    if owners < 2:
        return "todo", f"Off. This workspace has {owners} owner; invite a second owner first, then turn it on.", []
    return "todo", f"Off, and this workspace has {owners} owners. Turn it on in Approval policy.", []


def check_integrations(context: TenantContext) -> tuple[State, str, list[ChecklistDetail]]:
    from .integration_setup import _BY_ID, evaluate_item

    if not has_permission(context, Permission.INTEGRATION_READ):
        return "no_access", "You cannot read the integrations here.", []
    statuses = {key: evaluate_item(_BY_ID[key], context, details=False).status.value for key in PLAN_INTEGRATIONS if key in _BY_ID}
    labels = {"model": "AI model", "notification_email": "Email alerts", "push": "Phone alerts", "whatsapp": "WhatsApp",
              "commerce": "Online store", "fleet_data": "Tyre planning data", "speech": "Speech", "web_search": "Web search",
              "external_webhook": "Webhooks"}
    rows: list[tuple[str, str]] = []
    for key in PLAN_INTEGRATIONS:
        if key in MAIL[1:] or key in MEETINGS[1:]:
            continue
        if key == MAIL[0]:
            group = [statuses.get(k, "not_configured") for k in MAIL]
            rows.append(("Mailbox", "connected" if "connected" in group else ("error" if "error" in group else max(group, key=_rank))))
        elif key == MEETINGS[0]:
            group = [statuses.get(k, "not_configured") for k in MEETINGS]
            rows.append(("Zoom or Teams", "connected" if "connected" in group else max(group, key=_rank)))
        else:
            rows.append((labels.get(key, key), statuses.get(key, "not_configured")))
    words = {"connected": "Connected", "ready": "Ready to connect", "not_configured": "Needs server settings", "error": "Needs attention"}
    details = [ChecklistDetail(label=label, state="done" if value == "connected" else "todo", note=words.get(value, value)) for label, value in rows]
    connected = sum(1 for _, value in rows if value == "connected")
    state: State = "done" if connected == len(rows) else ("partial" if connected else "todo")
    return state, f"{connected} of {len(rows)} connected.", details


def _rank(value: str) -> int:
    return {"connected": 3, "ready": 2, "error": 1, "not_configured": 0}.get(value, 0)


def check_documents(context: TenantContext, folders: list[Any], targets: dict[str, str]) -> tuple[State, str, list[ChecklistDetail]]:
    from .knowledge_base import open_knowledge_store

    if not has_permission(context, Permission.MEMORY_READ):
        return "no_access", "You cannot read the knowledge base here.", []
    with open_knowledge_store(context) as store:
        collections = {c.id: c for c in store.list_collections(context)}
        counts = store.item_counts(context)
    details: list[ChecklistDetail] = []
    filled = 0
    for folder in folders:
        found = collections.get(targets.get(folder.key, "")) or next(
            (c for c in collections.values() if c.name.casefold() in {n.casefold() for n in [folder.name, *folder.former_names]}), None)
        if found is None:
            details.append(ChecklistDetail(label=folder.name, state="todo", note="Folder not here: apply the plan first"))
            continue
        n = counts.get(found.id, 0)
        filled += n > 0
        details.append(ChecklistDetail(label=folder.name, state="done" if n else "todo", note=f"{n} documents"))
    state: State = "done" if filled == len(folders) else ("partial" if filled else "todo")
    return state, f"{filled} of {len(folders)} folders have documents.", details


def check_webhooks(context: TenantContext, automations: list[Any], targets: dict[str, str]) -> tuple[State, str, list[ChecklistDetail]]:
    from . import automation as auto

    if not has_permission(context, Permission.AUTOMATION_READ):
        return "no_access", "You cannot read automations here.", []
    engine = auto.current_engine()
    webhook_plans = [a for a in automations if a.trigger.get("type") == "webhook"]
    details: list[ChecklistDetail] = []
    ready = 0
    for plan in webhook_plans:
        workflow_id = targets.get(plan.key)
        if not workflow_id:
            details.append(ChecklistDetail(label=f"#{plan.number} {plan.name}", state="todo", note="Automation not here: apply the plan first"))
            continue
        hooks = [t for t in engine.list_triggers(context, workflow_id=workflow_id) if getattr(t.kind, "value", t.kind) == "webhook"]
        ready += bool(hooks)
        details.append(ChecklistDetail(label=f"#{plan.number} {plan.name}", state="done" if hooks else "todo",
                                       note=f"{len(hooks)} webhook trigger" + ("" if len(hooks) == 1 else "s")))
    state: State = "done" if webhook_plans and ready == len(webhook_plans) else ("partial" if ready else "todo")
    return state, f"{ready} of {len(webhook_plans)} have a webhook trigger.", details


def check_automations_on(context: TenantContext, automations: list[Any], targets: dict[str, str]) -> tuple[State, str, list[ChecklistDetail]]:
    from . import automation as auto

    if not has_permission(context, Permission.AUTOMATION_READ):
        return "no_access", "You cannot read automations here.", []
    engine = auto.current_engine()
    made = [(a, targets[a.key]) for a in automations if a.key in targets and a.trigger.get("type") not in {"setting"}]
    on = 0
    details: list[ChecklistDetail] = []
    for plan, workflow_id in made:
        try:
            workflow = engine.get_workflow(context, workflow_id)
        except Exception:  # noqa: BLE001 - removed by the owner
            details.append(ChecklistDetail(label=f"#{plan.number} {plan.name}", state="todo", note="Removed"))
            continue
        starts = [s for s in engine.list_schedules(context, workflow_id=workflow_id) if s.enabled]
        starts += [t for t in engine.list_triggers(context, workflow_id=workflow_id) if t.enabled]
        running = workflow.status.value == "active" and (bool(starts) or plan.trigger.get("type") == "manual")
        on += running
        details.append(ChecklistDetail(label=f"#{plan.number} {plan.name}", state="done" if running else "todo",
                                       note="On" if running else "Off: run it by hand, check it, then turn it on"))
    if not made:
        return "todo", "No plan automation is here yet: apply the plan first.", []
    state: State = "done" if on == len(made) else ("partial" if on else "todo")
    return state, f"{on} of {len(made)} turned on.", details


def check_whatsapp_templates(context: TenantContext) -> tuple[State, str, list[ChecklistDetail]]:
    from .whatsapp_store import open_whatsapp_store

    if not has_permission(context, Permission.INTEGRATION_READ):
        return "no_access", "You cannot read WhatsApp here.", []
    with open_whatsapp_store(context) as store:
        account = store.get_account()
    if account is None:
        return "todo", "WhatsApp is not connected yet.", []
    chosen = len(account.auto_send_templates)
    return ("done" if chosen else "todo"), (f"{chosen} templates may send without asking." if chosen else "No template is on the auto-send list yet."), []


def check_ai_model(context: TenantContext) -> tuple[State, str, list[ChecklistDetail]]:
    from .onboarding import _model_is_configured

    return ("done", "An AI model is set up for this workspace.", []) if _model_is_configured(context) else (
        "todo", "No AI model is set up yet. Choose one in Settings > AI model and test it.", [])


def check_backups(context: TenantContext) -> tuple[State, str, list[ChecklistDetail]]:
    from .billing.company import backup_status

    status = backup_status()
    if not status["recorded"]:
        return "cannot_check", "No backup run is recorded on this server. Run infra/oracle/backup.sh backup there.", []
    details = []
    for kind, label in (("local", "Backups on the server"), ("offsite", "Off-site backups")):
        row = status.get(kind)
        if row is None:
            details.append(ChecklistDetail(label=label, state="todo", note="Not recorded"))
        else:
            details.append(ChecklistDetail(label=label, state="done" if row["ok"] else "todo",
                                           note=("Last run succeeded" if row["ok"] else "Last run failed")
                                           + (f", last success {row['last_success_at']}" if row.get("last_success_at") else "")))
    good = sum(1 for d in details if d.state == "done")
    state: State = "done" if good == len(details) else ("partial" if good else "todo")
    return state, f"{good} of {len(details)} backups succeeded on their last run.", details


VIEWS = {"people": "members", "approvers": "governance", "two_factor": None, "two_person": "policy",
         "integrations": "integrations", "documents": "knowledge", "webhooks": "automation", "automations_on": "automation",
         "whatsapp_templates": "whatsapp", "ai_model": "settings", "backups": None}


def build_checklist(blueprint: Any, context: TenantContext) -> Checklist:
    targets = _ledger_targets(context, blueprint.id)
    rules = {r.key: r.name for r in blueprint.approval_rules if r.key in DELEGATED_RULES} or {
        r.key: r.name for r in blueprint.approval_rules if "delegat" in (r.note or "").lower()}
    checks: dict[str, Callable[[], tuple[State, str, list[ChecklistDetail]]]] = {
        "people": lambda: check_people(context),
        "approvers": lambda: check_approvers(context, rules),
        "two_factor": lambda: ("cannot_check", "Two-step sign-in is set in the sign-in service (Keycloak), which ANUM cannot read. "
                               "Turn it on there for every account.", []),
        "two_person": lambda: check_two_person(context),
        "integrations": lambda: check_integrations(context),
        "documents": lambda: check_documents(context, blueprint.knowledge_folders, targets),
        "webhooks": lambda: check_webhooks(context, blueprint.automations, targets),
        "automations_on": lambda: check_automations_on(context, blueprint.automations, targets),
        "whatsapp_templates": lambda: check_whatsapp_templates(context),
        "ai_model": lambda: check_ai_model(context),
        "backups": lambda: check_backups(context),
    }
    items: list[ChecklistItem] = []
    for step in blueprint.manual_steps:
        if step.check and step.check in checks:
            state, summary, details = _guard(checks[step.check])
        else:
            state, summary, details = "cannot_check", "A step you take yourself; ANUM has nothing to read for it.", []
        items.append(ChecklistItem(key=step.key, check=step.check, title=step.title, detail=step.detail or "", state=state,
                                   summary=summary, view=VIEWS.get(step.check or ""), details=details))
    return Checklist(plan_id=blueprint.id, done=sum(1 for i in items if i.state == "done"), total=len(items), items=items)
