"""Setup plans: apply a checked-in business blueprint to a workspace (docs/setup-plan.md).

A blueprint (``anum_api/blueprints/*.json``) describes a whole ANUM setup: agents, skills,
department assistants, governance, knowledge folders, saved commands, automations and an
AI budget. ``GET /api/v1/setup-plans/{id}/preview`` says, per item, what applying it would
do here (create, update, already present, kept because you changed it, skipped and why,
later, by hand, not supported). ``POST /api/v1/setup-plans/{id}/apply`` does it.

No side doors: every write goes through the same route function a person's click would
call (agents, skills, assistants, governance, knowledge, quick commands, automations,
model budgets, workspaces), with the caller's own context, so every permission check,
validation rule and audit record of that feature applies. A step the caller may not take
(an owner without the organization administrator role publishing governance) is skipped
with the reason, never forced.

Idempotent: the ledger (``setup_plan_ledger``) records what each blueprint item key created.
Applying again never duplicates; an object the owner changed after it was applied (or
removed) is left alone unless ``overwrite_edited`` asks for the plan's values again.
Each item is written in its own transaction(s); a failure is reported for that item and
the next apply continues from where it stopped.

Safety defaults: automations are created turned off (their schedules and triggers too);
agents start at the plan's starting autonomy (never higher); in the pilot phase only the
pilot agents are active and everything else is created paused or turned off.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import _unit_of_work, has_permission, is_organization_admin, require_owner_or_org_admin, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .setup_plan_checks import CheckResult, CheckResultUpdate, CheckStatus, open_check_store
from .setup_plan_ledger import TENANT_SCOPE, LedgerEntry, LedgerOrigin, LedgerState, open_setup_plan_ledger

logger = logging.getLogger(__name__)

BLUEPRINT_DIR = Path(__file__).parent / "blueprints"
T = TypeVar("T")

ACCESS_REQUIRED = (
    "Only a workspace owner or an organization administrator can preview and apply a setup plan, because it "
    "creates agents, automations and organization policy. Ask an owner of this workspace to apply it, or to "
    "make you an owner in Members."
)


# --------------------------------------------------------------------------- blueprint


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlueprintWeek(_Strict):
    week: int
    title: str


class BlueprintNamed(_Strict):
    key: str
    name: str


class BlueprintFolder(BlueprintNamed):
    description: str


class BlueprintPolicyRule(_Strict):
    action: str
    effect: Literal["allow", "require_approval", "deny"]
    conditions: dict[str, Any] = Field(default_factory=dict)


class BlueprintPolicyPack(BlueprintNamed):
    description: str = ""
    rules: list[BlueprintPolicyRule] = Field(default_factory=list)
    mapped_to: str | None = None
    reason: str | None = None


class BlueprintRoleTemplate(BlueprintNamed):
    permissions: list[str]
    maps_to: str
    # From the owner's pilot role cards (docs/setup-plan.md#role-cards).
    may_approve: list[str] = Field(default_factory=list)
    must_escalate: list[str] = Field(default_factory=list)


class PilotCheck(_Strict):
    """One Week 1 pilot test case, recorded by a person (docs/setup-plan.md#pilot-checks)."""

    id: str
    group: Literal["customer_care", "cod", "owner"]
    title: str
    input: str
    expected: str
    approval_gate: str
    pass_criteria: str
    support: Literal["supported", "partial", "unsupported"]
    anum: str
    # The API test that encodes the governance part of this case, when there is one.
    verified_by: str | None = None


class BlueprintApprovalRule(BlueprintNamed):
    action_pattern: str
    minimum_approvers: int = 1
    required_roles: list[str] = Field(default_factory=lambda: ["owner"])


class SupportNote(_Strict):
    label: str
    plan: str
    support: Literal["supported", "partial", "unsupported"]
    anum: str


class BlueprintMemory(_Strict):
    default_retention_days: int
    allow_permanent_retention: bool
    require_provenance: bool
    allowed_source_types: list[str]
    rules: list[SupportNote]


class BlueprintSkill(_Strict):
    key: str
    code: str
    skill_id: str
    version: str
    name: str
    description: str
    instructions: str
    tools: list[str]
    risk_level: Literal["low", "medium", "high"]
    agents: list[str]


class UnmappedTool(_Strict):
    label: str
    reason: str


class BlueprintAgent(_Strict):
    key: str
    code: str
    name: str
    department: str
    business: str
    icon: str
    color: str
    mission: str
    instructions: str
    plan_autonomy: str
    autonomy: Literal["observe", "internal"]
    skills: list[str]
    builtin_skills: list[str]
    tools: list[str]
    unmapped_tools: list[UnmappedTool]
    approval_required: str
    schedule: str
    kpi: str
    supervisor: str
    budget_aed: float
    week: int


class BlueprintAssistant(_Strict):
    key: str
    name: str
    plan_department: str
    department: str
    agents: list[str]
    tools: list[str]
    routing_keywords: list[str]
    autonomy: Literal["observe", "suggest", "act_low_risk"]
    instructions: str
    week: int


class BlueprintCommand(_Strict):
    key: str
    title: str
    text: str


class BlueprintAutomation(_Strict):
    key: str
    number: int
    name: str
    agent: str
    trigger: dict[str, Any]
    week: int
    prompt: str | None = None


class BlueprintNote(_Strict):
    key: str
    title: str
    detail: str | None = None
    reason: str | None = None


class Blueprint(_Strict):
    id: str
    version: int
    name: str
    summary: str
    currency: str
    timezone: str
    business_hours: str
    languages: list[str]
    aed_per_usd: float
    pilot_agents: list[str]
    weeks: list[BlueprintWeek]
    workspaces: list[BlueprintNamed]
    knowledge_folders: list[BlueprintFolder]
    policy_packs: list[BlueprintPolicyPack]
    role_templates: list[BlueprintRoleTemplate]
    approval_rules: list[BlueprintApprovalRule]
    approval_thresholds: list[SupportNote]
    memory_governance: BlueprintMemory
    skills: list[BlueprintSkill]
    agents: list[BlueprintAgent]
    assistants: list[BlueprintAssistant]
    saved_commands: list[BlueprintCommand]
    automations: list[BlueprintAutomation]
    pilot_checks: list[PilotCheck]
    pilot_gate: str
    manual_steps: list[BlueprintNote]
    unsupported: list[BlueprintNote]

    def agent(self, key: str) -> BlueprintAgent:
        return next(agent for agent in self.agents if agent.key == key)

    def budget_aed(self, phase: Phase) -> float:
        return sum(a.budget_aed for a in self.agents if phase == Phase.ALL or a.key in self.pilot_agents)


@lru_cache(maxsize=1)
def load_blueprints() -> dict[str, Blueprint]:
    """Every checked-in blueprint, by id (validated once per process)."""
    blueprints: dict[str, Blueprint] = {}
    for path in sorted(BLUEPRINT_DIR.glob("*.json")):
        blueprint = Blueprint.model_validate(json.loads(path.read_text(encoding="utf-8")))
        blueprints[blueprint.id] = blueprint
    return blueprints


def blueprint_or_404(plan_id: str) -> Blueprint:
    blueprint = load_blueprints().get(plan_id)
    if blueprint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setup plan not found")
    return blueprint


# --------------------------------------------------------------------------- wire types


class Phase(StrEnum):
    PILOT = "pilot"
    ALL = "all"


class Section(StrEnum):
    WORKSPACES = "workspaces"
    GOVERNANCE = "governance"
    KNOWLEDGE = "knowledge"
    BUDGET = "budget"
    SKILLS = "skills"
    AGENTS = "agents"
    ASSISTANTS = "assistants"
    COMMANDS = "commands"
    AUTOMATIONS = "automations"
    MANUAL = "manual"
    UNSUPPORTED = "unsupported"


class ItemAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    PRESENT = "present"
    KEPT = "kept"
    SKIPPED = "skipped"
    LATER = "later"
    MANUAL = "manual"
    BUILTIN = "builtin"
    UNSUPPORTED = "unsupported"


class Outcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    PRESENT = "present"
    KEPT = "kept"
    SKIPPED = "skipped"
    FAILED = "failed"
    BUSY = "busy"
    NOT_APPLIED = "not_applied"


class Link(BaseModel):
    """Where the item lives in the app: a web view id and, when there is one, the object id."""

    view: str
    id: str | None = None


class PlanItem(BaseModel):
    key: str
    section: Section
    kind: str
    name: str
    action: ItemAction
    reason_code: str
    reason: str
    week: int | None = None
    link: Link | None = None
    facts: list[str] = Field(default_factory=list)
    outcome: Outcome | None = None
    error: str | None = None


class SectionCount(BaseModel):
    section: Section
    total: int
    actions: dict[str, int]


class PlanSummary(BaseModel):
    id: str
    version: int
    name: str
    summary: str
    currency: str
    timezone: str
    business_hours: str
    pilot_agents: list[str]
    weeks: list[BlueprintWeek]
    counts: dict[str, int]
    applied_items: int
    last_applied_at: str | None = None


class CallerAccess(BaseModel):
    owner: bool
    organization_admin: bool
    can_apply: bool
    notes: list[str]


class PlanPreview(BaseModel):
    plan: PlanSummary
    phase: Phase
    overwrite_edited: bool
    access: CallerAccess
    budget_aed: float
    budget_usd: float
    items: list[PlanItem]
    sections: list[SectionCount]
    approval_thresholds: list[SupportNote]
    memory_rules: list[SupportNote]


class ApplyRequest(_Strict):
    phase: Phase = Phase.PILOT
    overwrite_edited: bool = False
    # The plan version the person previewed: a newer checked-in plan answers 409 instead of
    # applying something they did not see.
    plan_version: int = Field(ge=1)


class ApplyResult(PlanPreview):
    outcomes: dict[str, int]
    applied_at: str


# --------------------------------------------------------------------------- helpers


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _run(awaitable: Coroutine[Any, Any, T]) -> T:
    """Run an ``async def`` route function from this worker thread (never on the event loop)."""
    return asyncio.run(awaitable)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return detail if isinstance(detail, str) else json.dumps(detail, default=str)[:500]
    if isinstance(exc, ValidationError):
        return "; ".join(str(error.get("msg", "")) for error in exc.errors())[:500]
    return f"{type(exc).__name__}: {exc}"[:500]


@dataclass
class Applied:
    target_id: str | None
    target_version: int | None = None
    fingerprint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    origin: LedgerOrigin = LedgerOrigin.CREATED


@dataclass
class Planned:
    item: PlanItem
    ledger_key: str
    tenant_level: bool
    run: Callable[[], Applied] | None = None


NEEDS_ORG_ADMIN = (
    "Needs the organization administrator role (org_admin): this setting binds every workspace of the "
    "organization, so owning this workspace is not enough."
)
NEEDS_OWNER = "Needs a workspace owner: an organization administrator who is not an owner here cannot set this."


# --------------------------------------------------------------------------- planning


class Planner:
    """Reads the current state once, in the caller's scope, and plans every blueprint item."""

    def __init__(self, blueprint: Blueprint, context: TenantContext, phase: Phase, overwrite_edited: bool) -> None:
        self.bp = blueprint
        self.context = context
        self.phase = phase
        self.overwrite = overwrite_edited
        self.org_admin = is_organization_admin(context)
        self.owner = "owner" in {role.strip().lower() for role in context.roles}
        self.now = utc_now()
        with open_setup_plan_ledger(context) as ledger:
            self.ledger = {entry.item_key: entry for entry in ledger.entries(context, blueprint.id)}
        self.pending_installs: set[str] = set()
        self.active_agents = {a.key for a in blueprint.agents if phase == Phase.ALL or a.key in blueprint.pilot_agents}

    # ---- shared rules

    def _ledger_key(self, key: str) -> str:
        return key

    def _existing(self, key: str) -> LedgerEntry | None:
        entry = self.ledger.get(key)
        if entry is not None and entry.state == LedgerState.CLAIMED and not entry.stale_claim(self.now):
            return entry
        if entry is not None and entry.state == LedgerState.CLAIMED:
            return None  # a stale claim: plan the item again (the claim is retaken)
        return entry

    def item(self, key: str, section: Section, kind: str, name: str, action: ItemAction, code: str, reason: str, **extra: Any) -> PlanItem:
        return PlanItem(key=key, section=section, kind=kind, name=name, action=action, reason_code=code, reason=reason, **extra)

    def busy(self, entry: LedgerEntry | None) -> bool:
        return entry is not None and entry.state == LedgerState.CLAIMED and not entry.stale_claim(self.now)

    def removed(self, key: str, section: Section, kind: str, name: str, link: Link | None, create: Callable[[], Applied], tenant: bool, **extra: Any) -> Planned:
        if self.overwrite:
            return Planned(self.item(key, section, kind, name, ItemAction.CREATE, "recreate",
                                     "It was removed after the plan created it; it is created again because you chose to replace your changes.", **extra),
                           key, tenant, create)
        return Planned(self.item(key, section, kind, name, ItemAction.KEPT, "removed_by_owner",
                                 "Removed after the plan created it. The plan leaves it removed; choose 'Replace my changes' to create it again.", link=link, **extra),
                       key, tenant)

    # ---- the whole plan

    def plan(self) -> list[Planned]:
        planned: list[Planned] = []
        planned += self.workspaces()
        planned += self.governance()
        planned += self.knowledge()
        planned += self.budget()
        planned += self.skills()
        planned += self.agents()
        planned += self.assistants()
        planned += self.commands()
        planned += self.automations()
        planned += [Planned(self.item(n.key, Section.MANUAL, "manual", n.title, ItemAction.MANUAL, "manual", n.detail or ""), n.key, False)
                    for n in self.bp.manual_steps]
        planned += [Planned(self.item(n.key, Section.UNSUPPORTED, "unsupported", n.title, ItemAction.UNSUPPORTED, "unsupported", n.reason or ""), n.key, False)
                    for n in self.bp.unsupported]
        return planned

    # ---- workspaces

    def workspaces(self) -> list[Planned]:
        from .workspace_admin import WorkspaceCreateRequest, create_workspace_in_organization

        out: list[Planned] = []
        can_create = has_permission(self.context, Permission.WORKSPACE_CREATE) or self.org_admin
        for ws in self.bp.workspaces:
            entry = self._existing(ws.key)
            facts = ["You become its first owner.", "This plan's agents and settings stay in the workspace you apply it from."]
            if entry is not None:
                out.append(Planned(self.item(ws.key, Section.WORKSPACES, "workspace", ws.name, ItemAction.PRESENT, "already_applied",
                                             "Created by this plan.", facts=facts), ws.key, True))
                continue
            if self.phase == Phase.PILOT:
                out.append(Planned(self.item(ws.key, Section.WORKSPACES, "workspace", ws.name, ItemAction.LATER, "not_in_phase",
                                             "Created with Apply all; the pilot runs in this workspace.", facts=facts), ws.key, True))
                continue
            if not can_create:
                out.append(Planned(self.item(ws.key, Section.WORKSPACES, "workspace", ws.name, ItemAction.SKIPPED, "needs_owner",
                                             NEEDS_OWNER, facts=facts), ws.key, True))
                continue

            def create(ws: BlueprintNamed = ws) -> Applied:
                with _unit_of_work(self.context, require_membership=True) as repository:
                    created = _run(create_workspace_in_organization(WorkspaceCreateRequest(name=ws.name), self.context, repository))
                return Applied(target_id=created.id)

            out.append(Planned(self.item(ws.key, Section.WORKSPACES, "workspace", ws.name, ItemAction.CREATE, "will_create",
                                         "A new, empty workspace in this organization.", facts=facts), ws.key, True, create))
        return out

    # ---- governance (tenant level)

    def governance(self) -> list[Planned]:
        from . import governance as gov

        with gov.open_governance_store(self.context) as store:
            packs = store.list_policy_packs(self.context)
            templates = store.list_role_templates(self.context)
            rules = store.list_approval_rules(self.context)
            memory = store.get_memory_governance(self.context)
        out: list[Planned] = []
        link = Link(view="governance")

        def org_admin_or_skip(key: str, kind: str, name: str, **extra: Any) -> Planned | None:
            if self.org_admin:
                return None
            return Planned(self.item(key, Section.GOVERNANCE, kind, name, ItemAction.SKIPPED, "needs_org_admin", NEEDS_ORG_ADMIN, link=link, **extra), key, True)

        # Memory governance
        mg = self.bp.memory_governance
        desired = gov.MemoryGovernanceUpdate(default_retention_days=mg.default_retention_days, allow_permanent_retention=mg.allow_permanent_retention,
                                             require_provenance=mg.require_provenance, allowed_source_types=mg.allowed_source_types)
        facts = [f"Remembered chat facts expire after {mg.default_retention_days} days.", "Every memory keeps where it came from (provenance)."]
        current = gov.MemoryGovernanceUpdate(**memory.model_dump(include=set(gov.MemoryGovernanceUpdate.model_fields))) if memory else None
        key, name = "memory_governance", "Memory governance"
        entry = self._existing(key)

        def save_memory() -> Applied:
            saved = gov.update_memory_governance(desired, self.context)
            return Applied(target_id=f"tenant:{self.context.tenant_id}",
                           fingerprint=fingerprint(gov.MemoryGovernanceUpdate(**saved.model_dump(include=set(gov.MemoryGovernanceUpdate.model_fields))).model_dump()))

        if current is not None and current.model_dump() == desired.model_dump():
            out.append(Planned(self.item(key, Section.GOVERNANCE, "memory_governance", name, ItemAction.PRESENT, "already_set", "Already set to the plan's rules.", link=link, facts=facts), key, True))
        elif (skip := org_admin_or_skip(key, "memory_governance", name, facts=facts)) is not None:
            out.append(skip)
        elif current is None:
            out.append(Planned(self.item(key, Section.GOVERNANCE, "memory_governance", name, ItemAction.CREATE, "will_create", "Sets the organization's memory rules.", link=link, facts=facts), key, True, save_memory))
        elif self.overwrite or (entry is not None and entry.origin == LedgerOrigin.CREATED and entry.fingerprint == fingerprint(current.model_dump())):
            out.append(Planned(self.item(key, Section.GOVERNANCE, "memory_governance", name, ItemAction.UPDATE, "will_update", "Changes the organization's memory rules to the plan's.", link=link, facts=facts), key, True, save_memory))
        else:
            out.append(Planned(self.item(key, Section.GOVERNANCE, "memory_governance", name, ItemAction.KEPT, "set_by_someone",
                                         "Your organization already has memory rules; the plan leaves them. Choose 'Replace my changes' to use the plan's.", link=link, facts=facts), key, True))

        # Approval rules
        for rule in self.bp.approval_rules:
            facts = [f"Tool pattern: {rule.action_pattern}", f"Approved by: {', '.join(rule.required_roles)}"]
            entry = self._existing(rule.key)
            found = next((r for r in rules if (entry and r.id == entry.target_id) or r.name.casefold() == rule.name.casefold()), None)
            out.append(self._tenant_object(rule.key, "approval_rule", rule.name, entry, found.id if found else None, facts, link,
                                           lambda rule=rule: Applied(target_id=gov.create_approval_rule(gov.ApprovalRuleCreate(
                                               name=rule.name, action_pattern=rule.action_pattern, minimum_approvers=rule.minimum_approvers,
                                               required_roles=rule.required_roles), self.context).id),
                                           "An organization approval rule: matching tool calls wait for an owner's approval."))

        # Policy packs
        for pack in self.bp.policy_packs:
            if pack.mapped_to:
                out.append(Planned(self.item(pack.key, Section.GOVERNANCE, "policy_pack", pack.name, ItemAction.BUILTIN, f"mapped_{pack.mapped_to}",
                                             pack.reason or "", link=link), pack.key, True))
                continue
            facts = [f"{r.effect.replace('_', ' ')}: {r.action}" for r in pack.rules]
            entry = self._existing(pack.key)
            found = next((p for p in packs if (entry and p.id == entry.target_id) or p.name.casefold() == pack.name.casefold()), None)

            def create_pack(pack: BlueprintPolicyPack = pack) -> Applied:
                created = gov.create_policy_pack(gov.PolicyPackCreate(
                    name=pack.name, description=pack.description,
                    rules=[gov.PolicyRule(action=r.action, effect=gov.PolicyEffect(r.effect), conditions=r.conditions) for r in pack.rules]), self.context)
                activated = gov.activate_policy_pack(created.id, self.context)
                return Applied(target_id=activated.id, target_version=activated.version)

            out.append(self._tenant_object(pack.key, "policy_pack", pack.name, entry, found.id if found else None, facts, link, create_pack,
                                           "A policy pack, published and activated. Its rules only add approvals; they never loosen policy."))

        # Role templates
        for template in self.bp.role_templates:
            facts = [f"Maps to: {template.maps_to}", f"Permissions: {', '.join(template.permissions)}",
                     "ANUM enforces the owner, member and viewer roles and org_admin; a role template documents the intended permission set."]
            facts += [f"May approve: {line}" for line in template.may_approve]
            facts += [f"Must escalate: {line}" for line in template.must_escalate]
            entry = self._existing(template.key)
            found = next((t for t in templates if (entry and t.id == entry.target_id) or t.name.casefold() == template.name.casefold()), None)
            out.append(self._tenant_object(template.key, "role_template", template.name, entry, found.id if found else None, facts, link,
                                           lambda template=template: Applied(target_id=gov.create_role_template(gov.RoleTemplateCreate(
                                               name=template.name, permissions=template.permissions), self.context).id),
                                           "A role template for the organization."))
        return out

    def _tenant_object(self, key: str, kind: str, name: str, entry: LedgerEntry | None, found_id: str | None, facts: list[str], link: Link,
                       create: Callable[[], Applied], create_reason: str) -> Planned:
        if self.busy(entry):
            return Planned(self.item(key, Section.GOVERNANCE, kind, name, ItemAction.SKIPPED, "busy", "Another apply is creating it right now.", link=link, facts=facts), key, True)
        if found_id is not None:
            if entry is None:
                return Planned(self.item(key, Section.GOVERNANCE, kind, name, ItemAction.PRESENT, "found_by_name",
                                         "One with this name already exists; the plan links it and does not change it.", link=link, facts=facts), key, True,
                               lambda: Applied(target_id=found_id, origin=LedgerOrigin.LINKED))
            return Planned(self.item(key, Section.GOVERNANCE, kind, name, ItemAction.PRESENT, "already_applied", "Already applied.", link=link, facts=facts), key, True)
        if not self.org_admin:
            return Planned(self.item(key, Section.GOVERNANCE, kind, name, ItemAction.SKIPPED, "needs_org_admin", NEEDS_ORG_ADMIN, link=link, facts=facts), key, True)
        if entry is not None:
            return self.removed(key, Section.GOVERNANCE, kind, name, link, create, True, facts=facts)
        return Planned(self.item(key, Section.GOVERNANCE, kind, name, ItemAction.CREATE, "will_create", create_reason, link=link, facts=facts), key, True, create)

    # ---- knowledge folders

    def knowledge(self) -> list[Planned]:
        from .knowledge_base import CollectionCreate, Visibility, create_collection, open_knowledge_store

        with open_knowledge_store(self.context) as store:
            collections = store.list_collections(self.context)
        out: list[Planned] = []
        for folder in self.bp.knowledge_folders:
            entry = self._existing(folder.key)
            ids = {c.id for c in collections}
            facts = ["Shared with the workspace.", folder.description]
            link_id = entry.target_id if entry and entry.target_id in ids else None
            by_name = next((c for c in collections if c.name.casefold() == folder.name.casefold() and c.owner_user_id is None), None)

            def create(folder: BlueprintFolder = folder) -> Applied:
                view = _run(create_collection(CollectionCreate(name=folder.name, description=folder.description, visibility=Visibility.WORKSPACE), self.context))
                return Applied(target_id=view.id)

            out.append(self._workspace_object(folder.key, Section.KNOWLEDGE, "knowledge_folder", folder.name, entry, link_id, by_name.id if by_name else None,
                                              Link(view="knowledge", id=link_id or (by_name.id if by_name else None)), facts,
                                              has_permission(self.context, Permission.MEMORY_CREATE), "You need permission to add to the knowledge base.",
                                              create, "A shared knowledge base collection."))
        return out

    def _workspace_object(self, key: str, section: Section, kind: str, name: str, entry: LedgerEntry | None, current_id: str | None, by_name_id: str | None,
                          link: Link | None, facts: list[str], allowed: bool, refusal: str, create: Callable[[], Applied], create_reason: str,
                          week: int | None = None, ledger_key: str | None = None) -> Planned:
        ledger_key = ledger_key or key
        if self.busy(entry):
            return Planned(self.item(key, section, kind, name, ItemAction.SKIPPED, "busy", "Another apply is creating it right now.", facts=facts, week=week), ledger_key, False)
        if entry is not None and current_id is not None:
            return Planned(self.item(key, section, kind, name, ItemAction.PRESENT, "already_applied", "Already applied.", link=link, facts=facts, week=week), ledger_key, False)
        if entry is None and by_name_id is not None:
            return Planned(self.item(key, section, kind, name, ItemAction.PRESENT, "found_by_name",
                                     "One with this name already exists; the plan links it and does not change it.", link=link, facts=facts, week=week), ledger_key, False,
                           lambda: Applied(target_id=by_name_id, origin=LedgerOrigin.LINKED))
        if not allowed:
            return Planned(self.item(key, section, kind, name, ItemAction.SKIPPED, "needs_owner", refusal, facts=facts, week=week), ledger_key, False)
        if entry is not None:
            return self.removed(key, section, kind, name, None, create, False, facts=facts, week=week)
        return Planned(self.item(key, section, kind, name, ItemAction.CREATE, "will_create", create_reason, facts=facts, week=week), ledger_key, False, create)

    # ---- AI budget

    def budget(self) -> list[Planned]:
        from .model_budget import BudgetScope, ModelBudgetLimits, open_model_budget_store, set_model_budget

        aed = self.bp.budget_aed(self.phase)
        usd = round(aed / self.bp.aed_per_usd, 2)
        with open_model_budget_store(self.context) as store:
            current = store.get_budget(self.context, BudgetScope.WORKSPACE)
        limits = (current.monthly_cost_limit_usd, current.monthly_token_limit) if current else (None, None)
        key, name = "budget", "Monthly AI budget for this workspace"
        facts = [f"AED {aed:,.0f} a month (USD {usd:,.2f} at {self.bp.aed_per_usd} AED per USD).",
                 "At 100% ANUM blocks further AI calls until the next month.",
                 "The plan's per-agent caps are added up: ANUM budgets per workspace, not per agent."]
        entry = self._existing(key)
        link = Link(view="budgets")

        def save() -> Applied:
            payload = ModelBudgetLimits(monthly_cost_limit_usd=usd, monthly_token_limit=limits[1])
            with _unit_of_work(self.context, require_membership=True) as repository:
                _run(set_model_budget(BudgetScope.WORKSPACE, payload, self.context, repository))
            return Applied(target_id=f"workspace:{self.context.workspace_id}", fingerprint=fingerprint([usd, limits[1]]))

        allowed = has_permission(self.context, Permission.ORGANIZATION_MANAGE)
        ours = entry is not None and entry.origin == LedgerOrigin.CREATED and entry.fingerprint == fingerprint(list(limits))
        if limits[0] is not None and abs(limits[0] - usd) < 0.005:
            action, code, reason, run = ItemAction.PRESENT, "already_set", "Already set to the plan's amount.", None
        elif limits[0] is None:
            action, code, reason, run = ItemAction.CREATE, "will_create", "Sets this workspace's monthly AI cost limit.", save
        elif ours and usd > limits[0]:
            action, code, reason, run = ItemAction.UPDATE, "will_update", "Raises the limit the plan set for the pilot to the full plan's amount.", save
        elif ours:
            action, code, reason, run = ItemAction.PRESENT, "already_set", "The plan already set a higher limit; it never lowers it.", None
        elif self.overwrite:
            action, code, reason, run = ItemAction.UPDATE, "will_update", "Replaces the current limit because you chose to replace your changes.", save
        else:
            action, code, reason, run = (ItemAction.KEPT, "set_by_someone",
                                         f"This workspace already has a limit of USD {limits[0]:,.2f}; the plan leaves it.", None)
        if run is not None and not allowed:
            action, code, reason, run = ItemAction.SKIPPED, "needs_owner", NEEDS_OWNER, None
        return [Planned(self.item(key, Section.BUDGET, "model_budget", name, action, code, reason, link=link, facts=facts), key, False, run)]

    # ---- skills

    def skills(self) -> list[Planned]:
        from . import skills_api
        from .agent_profiles import runtime_tools

        tools_here = runtime_tools().names
        with skills_api.open_skill_store(self.context) as store:
            versions = {(v.skill_id, v.version): v for v in store.list_versions(self.context)}
            installations = {i.skill_id: i for i in store.list_installations(self.context)}
        self.skill_versions = versions
        self.skill_installations = installations
        out: list[Planned] = []
        for skill in self.bp.skills:
            used_by = ", ".join(self.bp.agent(a).name for a in skill.agents)
            tools = [t for t in skill.tools if t in tools_here]
            dropped = [t for t in skill.tools if t not in tools_here]
            facts = [f"{skill.code}: used by {used_by}.", f"Tools: {', '.join(tools) or 'none (instructions only)'}."]
            if dropped:
                facts.append(f"Not in this deployment, left out: {', '.join(dropped)}.")
            link = Link(view="skills", id=skill.skill_id)
            pub_key = f"{skill.key}:publish"
            published = (skill.skill_id, skill.version) in versions

            def publish(skill: BlueprintSkill = skill, tools: list[str] = tools) -> Applied:
                from .schemas import RiskLevel

                created = skills_api.publish_skill(skills_api.SkillVersionCreate(
                    skill_id=skill.skill_id, version=skill.version, name=skill.name, description=skill.description,
                    instructions=skill.instructions, required_tools=tools, risk_level=RiskLevel(skill.risk_level)), self.context)
                return Applied(target_id=created.id, details={"skill_id": skill.skill_id, "version": skill.version})

            if published:
                pub = Planned(self.item(pub_key, Section.SKILLS, "skill_version", f"Publish {skill.name} {skill.version}", ItemAction.PRESENT,
                                        "already_applied", "Published for the organization.", link=link, facts=facts), pub_key, True,
                              None if self._existing(pub_key) else (lambda v=versions[(skill.skill_id, skill.version)]: Applied(target_id=v.id, origin=LedgerOrigin.LINKED)))
            elif not self.org_admin:
                pub = Planned(self.item(pub_key, Section.SKILLS, "skill_version", f"Publish {skill.name} {skill.version}", ItemAction.SKIPPED,
                                        "needs_org_admin", NEEDS_ORG_ADMIN + " The agents that use it get their built-in skills only.", link=link, facts=facts), pub_key, True)
            else:
                pub = Planned(self.item(pub_key, Section.SKILLS, "skill_version", f"Publish {skill.name} {skill.version}", ItemAction.CREATE,
                                        "will_create", "Publishes the skill's reviewed instructions for this organization.", link=link, facts=facts), pub_key, True, publish)
            out.append(pub)

            inst_key = f"{skill.key}:install"
            installed = installations.get(skill.skill_id)
            will_publish = published or pub.item.action == ItemAction.CREATE

            def install(skill: BlueprintSkill = skill) -> Applied:
                with skills_api.open_skill_store(self.context) as store:
                    version = store.get_version(self.context, skill.skill_id, skill.version)
                if version is None:
                    raise HTTPException(status_code=409, detail="The skill is not published yet; an organization administrator must apply the plan first.")
                result = skills_api.install_skill(skills_api.SkillInstallCreate(skill_id=skill.skill_id, version=skill.version,
                                                                                approved_tools=list(version.required_tools)), self.context)
                return Applied(target_id=result.id, details={"skill_id": skill.skill_id, "version": result.version})

            entry = self._existing(inst_key)
            name = f"Install {skill.name}"
            if installed is not None:
                out.append(Planned(self.item(inst_key, Section.SKILLS, "skill_installation", name, ItemAction.PRESENT,
                                             "already_applied" if entry else "found_by_name",
                                             "Installed in this workspace." + ("" if installed.enabled else " It is turned off; turn it on in Skills for agents to load it."),
                                             link=link, facts=facts), inst_key, False,
                                   None if entry else (lambda i=installed: Applied(target_id=i.id, origin=LedgerOrigin.LINKED))))
            elif not has_permission(self.context, Permission.POLICY_MANAGE):
                out.append(Planned(self.item(inst_key, Section.SKILLS, "skill_installation", name, ItemAction.SKIPPED, "needs_owner", NEEDS_OWNER, link=link, facts=facts), inst_key, False))
            elif not will_publish:
                out.append(Planned(self.item(inst_key, Section.SKILLS, "skill_installation", name, ItemAction.SKIPPED, "missing_dependency",
                                             "Not published yet: an organization administrator must apply the plan (or publish the skill) first.", link=link, facts=facts), inst_key, False))
            elif entry is not None:
                out.append(self.removed(inst_key, Section.SKILLS, "skill_installation", name, link, install, False, facts=facts))
            else:
                self.pending_installs.add(skill.skill_id)
                out.append(Planned(self.item(inst_key, Section.SKILLS, "skill_installation", name, ItemAction.CREATE, "will_create",
                                             "Installs it in this workspace, with only the tools it declares.", link=link, facts=facts), inst_key, False, install))
        return out

    # ---- agents

    def _agent_fields(self, agent: BlueprintAgent, assume_pending: bool = True) -> tuple[dict[str, Any], list[str], list[str]]:
        """The agent's fields as this deployment can hold them, plus what was left out and why.

        While planning, skills this apply is about to install count as installed
        (``assume_pending``); when the agent is written, only what is installed now counts.
        """
        from .agent_profiles import ALWAYS_ALLOWED_TOOL, AgentAutonomy, AutonomyLevel, catalog_skills, runtime_tools, tool_refusal

        registry = runtime_tools()
        tools: list[str] = []
        dropped: list[str] = []
        for name in agent.tools:
            definition = registry.definition(name)
            if definition is None:
                dropped.append(f"{name} (not in this deployment)")
            elif (refusal := tool_refusal(definition, self.context)) is not None:
                dropped.append(f"{name} ({refusal})")
            else:
                tools.append(name)
        catalog = {skill.id: skill for skill in catalog_skills(self.context)}
        allowed = set(tools) | {ALWAYS_ALLOWED_TOOL}
        skills: list[str] = []
        for skill_id in agent.builtin_skills:
            skill = catalog.get(skill_id)
            if skill is None or not set(skill.required_tools) <= allowed:
                dropped.append(f"skill {skill_id} (its tools are not available here)")
            else:
                skills.append(skill_id)
        for key in agent.skills:
            plan_skill = next(s for s in self.bp.skills if s.key == key)
            pending = assume_pending and plan_skill.skill_id in self.pending_installs
            if pending or (plan_skill.skill_id in catalog and catalog[plan_skill.skill_id].source == "installed"):
                skills.append(plan_skill.skill_id)
            else:
                dropped.append(f"skill {plan_skill.code} {plan_skill.name} (not installed and turned on in this workspace)")
        # Observe: reads on their own, changes inside ANUM ask, nothing outside ANUM (drafts only).
        # Internal: reads and changes inside ANUM on their own (as workspace policy allows), outside actions ask.
        autonomy = (AgentAutonomy(read=AutonomyLevel.AUTO, write=AutonomyLevel.ASK, external=AutonomyLevel.OFF)
                    if agent.autonomy == "observe"
                    else AgentAutonomy(read=AutonomyLevel.AUTO, write=AutonomyLevel.AUTO, external=AutonomyLevel.ASK))
        fields = {
            "name": agent.name, "icon": agent.icon, "color": agent.color, "category": "work", "department": agent.department,
            "description": f"{agent.code}: {agent.mission} KPI: {agent.kpi}"[:500], "instructions": agent.instructions,
            "model_route": "capable", "allowed_skills": sorted(set(skills)), "allowed_tools": sorted(set(tools)),
            "autonomy": autonomy.model_dump(mode="json"), "knowledge_mode": "workspace", "knowledge_sources": [],
            "sharing": "workspace", "template_id": None,
        }
        return fields, dropped, [u.label for u in agent.unmapped_tools]

    def agents(self) -> list[Planned]:
        from .agent_profiles import (
            AgentProfileCreate,
            AgentProfileUpdate,
            AgentStatus,
            can_edit,
            create_agent,
            open_agent_store,
            update_agent,
        )

        with open_agent_store(self.context) as store:
            profiles = store.list(self.context)
        by_id = {p.id: p for p in profiles}
        out: list[Planned] = []
        compare = ("name", "icon", "color", "category", "department", "description", "instructions", "model_route",
                   "allowed_skills", "allowed_tools", "autonomy", "knowledge_mode", "sharing")
        for agent in self.bp.agents:
            fields, dropped, unmapped = self._agent_fields(agent)
            active = agent.key in self.active_agents
            facts = [
                f"{agent.code} · {agent.department} · starts at {agent.plan_autonomy}"
                + (" (reads on their own, changes ask, nothing sent outside ANUM)" if agent.autonomy == "observe"
                   else " (reads and internal changes on their own, outside actions ask)"),
                f"Tools: {', '.join(fields['allowed_tools']) or 'answers in text only'}.",
                f"Skills: {', '.join(fields['allowed_skills']) or 'none'}.",
                f"Needs approval: {agent.approval_required}", f"KPI: {agent.kpi}", f"Budget share: AED {agent.budget_aed:,.0f} a month.",
            ]
            if dropped:
                facts.append(f"Left out here: {'; '.join(dropped)}.")
            for item in agent.unmapped_tools:
                facts.append(f"Plan tool '{item.label}' is not an agent tool: {item.reason}")
            entry = self._existing(agent.key)
            current = by_id.get(entry.target_id) if entry and entry.target_id else None
            by_name = next((p for p in profiles if p.name.casefold() == agent.name.casefold()), None)
            link = Link(view="agents", id=(current or by_name).id if (current or by_name) else None)
            status_word = "active" if active else "paused"

            def create(agent: BlueprintAgent = agent, active: bool = active) -> Applied:
                fields, _, _ = self._agent_fields(agent, assume_pending=False)
                view = _run(create_agent(AgentProfileCreate.model_validate(fields), self.context))
                version = view.version
                if not active:
                    view = _run(update_agent(view.id, AgentProfileUpdate(status=AgentStatus.PAUSED, version=version), self.context))
                return Applied(target_id=view.id, target_version=view.version)

            if self.busy(entry):
                out.append(Planned(self.item(agent.key, Section.AGENTS, "agent", agent.name, ItemAction.SKIPPED, "busy", "Another apply is creating it right now.", facts=facts, week=agent.week), agent.key, False))
                continue
            if current is None and entry is None and by_name is not None:
                out.append(Planned(self.item(agent.key, Section.AGENTS, "agent", agent.name, ItemAction.PRESENT, "found_by_name",
                                             "An agent with this name already exists; the plan links it and does not change it.", link=link, facts=facts, week=agent.week),
                                   agent.key, False, lambda p=by_name: Applied(target_id=p.id, target_version=p.version, origin=LedgerOrigin.LINKED)))
                continue
            if current is None:
                if not has_permission(self.context, Permission.TASK_CREATE):
                    out.append(Planned(self.item(agent.key, Section.AGENTS, "agent", agent.name, ItemAction.SKIPPED, "needs_owner", NEEDS_OWNER, facts=facts, week=agent.week), agent.key, False))
                elif entry is not None:
                    out.append(self.removed(agent.key, Section.AGENTS, "agent", agent.name, None, create, False, facts=facts, week=agent.week))
                else:
                    out.append(Planned(self.item(agent.key, Section.AGENTS, "agent", agent.name, ItemAction.CREATE, "will_create",
                                                 f"Created {status_word}, shared with the workspace.", facts=facts, week=agent.week), agent.key, False, create))
                continue
            # It exists and the plan made or linked it.
            untouched = entry is not None and entry.origin == LedgerOrigin.CREATED and entry.target_version == current.version
            desired_status = AgentStatus.ACTIVE if active else current.status
            changes = {k: v for k, v in fields.items() if k in compare and _plain(getattr(current, k)) != v}
            if desired_status != current.status:
                changes["status"] = desired_status.value
            if not changes:
                out.append(Planned(self.item(agent.key, Section.AGENTS, "agent", agent.name, ItemAction.PRESENT, "already_applied",
                                             f"Already applied ({current.status.value}).", link=link, facts=facts, week=agent.week), agent.key, False))
                continue
            if not (untouched or self.overwrite):
                out.append(Planned(self.item(agent.key, Section.AGENTS, "agent", agent.name, ItemAction.KEPT, "edited_by_owner",
                                             "Changed after the plan applied it (or not made by the plan); the plan leaves your version. Choose 'Replace my changes' to use the plan's.",
                                             link=link, facts=facts, week=agent.week), agent.key, False))
                continue
            if not can_edit(current, self.context):
                out.append(Planned(self.item(agent.key, Section.AGENTS, "agent", agent.name, ItemAction.SKIPPED, "needs_owner",
                                             "Only the agent's creator or a workspace owner can change it.", link=link, facts=facts, week=agent.week), agent.key, False))
                continue

            def update(agent_id: str = current.id, version: int = current.version, changes: dict[str, Any] = changes) -> Applied:
                view = _run(update_agent(agent_id, AgentProfileUpdate.model_validate({**changes, "version": version}), self.context))
                return Applied(target_id=view.id, target_version=view.version)

            what = "turns it on" if changes.keys() == {"status"} else f"updates {', '.join(sorted(changes))}"
            out.append(Planned(self.item(agent.key, Section.AGENTS, "agent", agent.name, ItemAction.UPDATE, "will_update",
                                         f"The plan {what}.", link=link, facts=facts, week=agent.week), agent.key, False, update))
        return out

    # ---- department assistants

    def assistants(self) -> list[Planned]:
        from .agent_profiles import runtime_tools
        from .assistants import create_assistant, update_assistant
        from .team_models import AssistantWrite
        from .team_work import open_team_unit

        with open_team_unit(self.context, require_membership=True) as unit:
            current_all = unit.store.list_assistants(self.context)
        by_id = {a.id: a for a in current_all}
        tools_here = runtime_tools().names
        allowed = has_permission(self.context, Permission.ASSISTANT_MANAGE)
        out: list[Planned] = []
        for assistant in self.bp.assistants:
            entry = self._existing(assistant.key)
            current = by_id.get(entry.target_id) if entry and entry.target_id else None
            by_name = next((a for a in current_all if a.name.casefold() == assistant.name.casefold()), None)
            enabled = self.phase == Phase.ALL or (current.enabled if current else False)
            payload = AssistantWrite(department=assistant.department, name=assistant.name, instructions=assistant.instructions,
                                     allowed_tools=[t for t in assistant.tools if t in tools_here], routing_keywords=assistant.routing_keywords,
                                     autonomy=assistant.autonomy, enabled=enabled)
            facts = [f"Routes and proposes work for {', '.join(self.bp.agent(k).name for k in assistant.agents)}.",
                     "Proposes only: every action waits for a person (no approval authority).",
                     f"Routing words: {', '.join(assistant.routing_keywords)}.",
                     "Takes no new messages on arrival until an owner ticks a channel."]
            link = Link(view="assistants", id=(current or by_name).id if (current or by_name) else None)
            state = "turned on" if enabled else "turned off"

            def create(payload: AssistantWrite = payload) -> Applied:
                created = create_assistant(payload, self.context)
                return Applied(target_id=created.id, fingerprint=_assistant_fingerprint(created))

            if self.busy(entry):
                out.append(Planned(self.item(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, ItemAction.SKIPPED, "busy", "Another apply is creating it right now.", facts=facts, week=assistant.week), assistant.key, False))
                continue
            if current is None and entry is None and by_name is not None:
                out.append(Planned(self.item(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, ItemAction.PRESENT, "found_by_name",
                                             "An assistant with this name already exists; the plan links it and does not change it.", link=link, facts=facts, week=assistant.week),
                                   assistant.key, False, lambda a=by_name: Applied(target_id=a.id, fingerprint=_assistant_fingerprint(a), origin=LedgerOrigin.LINKED)))
                continue
            if current is None:
                if not allowed:
                    out.append(Planned(self.item(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, ItemAction.SKIPPED, "needs_owner", NEEDS_OWNER, facts=facts, week=assistant.week), assistant.key, False))
                elif entry is not None:
                    out.append(self.removed(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, None, create, False, facts=facts, week=assistant.week))
                else:
                    out.append(Planned(self.item(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, ItemAction.CREATE, "will_create",
                                                 f"Created {state}.", facts=facts, week=assistant.week), assistant.key, False, create))
                continue
            desired = _assistant_fingerprint(payload)
            if desired == _assistant_fingerprint(current):
                out.append(Planned(self.item(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, ItemAction.PRESENT, "already_applied",
                                             f"Already applied ({'on' if current.enabled else 'off'}).", link=link, facts=facts, week=assistant.week), assistant.key, False))
                continue
            untouched = entry is not None and entry.origin == LedgerOrigin.CREATED and entry.fingerprint == _assistant_fingerprint(current)
            if not (untouched or self.overwrite):
                out.append(Planned(self.item(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, ItemAction.KEPT, "edited_by_owner",
                                             "Changed after the plan applied it (or not made by the plan); the plan leaves your version. Choose 'Replace my changes' to use the plan's.",
                                             link=link, facts=facts, week=assistant.week), assistant.key, False))
                continue
            if not allowed:
                out.append(Planned(self.item(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, ItemAction.SKIPPED, "needs_owner", NEEDS_OWNER, link=link, facts=facts, week=assistant.week), assistant.key, False))
                continue

            def update(assistant_id: str = current.id, payload: AssistantWrite = payload) -> Applied:
                updated = update_assistant(assistant_id, payload, self.context)
                return Applied(target_id=updated.id, fingerprint=_assistant_fingerprint(updated))

            out.append(Planned(self.item(assistant.key, Section.ASSISTANTS, "assistant", assistant.name, ItemAction.UPDATE, "will_update",
                                         "The plan turns it on." if enabled and not current.enabled else "The plan updates it.", link=link, facts=facts, week=assistant.week),
                               assistant.key, False, update))
        return out

    # ---- saved commands (the caller's own quick commands)

    def commands(self) -> list[Planned]:
        from .commands import QuickCommandCategory, QuickCommandCreate, command_store, create_quick_command

        with _unit_of_work(self.context, require_membership=True) as repository:
            quick = command_store(repository, self.context).list_quick()
        ids = {q.id for q in quick}
        out: list[Planned] = []
        for command in self.bp.saved_commands:
            ledger_key = f"{command.key}@{self.context.user_id}"
            entry = self._existing(ledger_key)
            by_title = next((q for q in quick if q.title.casefold() == command.title.casefold()), None)
            facts = [f'Say or type: "{command.text}"', "Saved for you; other people save their own."]

            def create(command: BlueprintCommand = command) -> Applied:
                with _unit_of_work(self.context, require_membership=True) as repository:
                    created = _run(create_quick_command(QuickCommandCreate(title=command.title, text=command.text, category=QuickCommandCategory.WORK),
                                                        self.context, repository))
                return Applied(target_id=created.id)

            out.append(self._workspace_object(command.key, Section.COMMANDS, "saved_command", command.title, entry,
                                              entry.target_id if entry and entry.target_id in ids else None, by_title.id if by_title else None,
                                              Link(view="command"), facts, has_permission(self.context, Permission.TASK_CREATE),
                                              "You need permission to create tasks to save commands.", create,
                                              "A saved command under Work, with its voice phrase.", ledger_key=ledger_key))
        return out

    # ---- automations (always created turned off)

    def automations(self) -> list[Planned]:
        from . import automation as auto
        from .automation_triggers import TriggerCondition, TriggerCreate, TriggerKind

        workflows = auto.current_engine().list_workflows(self.context)
        by_id = {w.id: w for w in workflows}
        allowed = has_permission(self.context, Permission.AUTOMATION_MANAGE)
        out: list[Planned] = []
        for automation in self.bp.automations:
            trigger = automation.trigger
            kind = trigger.get("type")
            agent = self.bp.agent(automation.agent)
            base_facts = [f"#{automation.number} · for {agent.name}"]
            if kind in {"builtin", "unsupported", "manual_step", "covered"}:
                action = {"builtin": ItemAction.BUILTIN, "unsupported": ItemAction.UNSUPPORTED, "manual_step": ItemAction.MANUAL,
                          "covered": ItemAction.BUILTIN}[kind]
                out.append(Planned(self.item(automation.key, Section.AUTOMATIONS, "automation", automation.name, action, kind,
                                             str(trigger.get("reason", "")), facts=base_facts, week=automation.week), automation.key, False))
                continue
            facts = list(base_facts)
            if kind == "schedule":
                from .cron import describe

                facts.append(f"Schedule (turned off): {describe(trigger['cron'], 'en')}, {self.bp.timezone}.")
            elif kind == "event":
                facts.append(f"Starts on (turned off): {', '.join(trigger['events'])}" +
                             (f" when {', '.join(c['field'] + ' ' + c['operator'] + ' ' + str(c.get('value')) for c in trigger.get('conditions', []))}" if trigger.get("conditions") else "") + ".")
            elif kind == "webhook":
                facts.append(f"Needs a webhook trigger from {trigger['source']}: add it in Automations > Triggers (its secret is shown once).")
            elif kind == "manual":
                facts.append(str(trigger.get("reason", "Run it by hand from Automations.")))
            facts.append("The task runs as the person who owns the automation, with that person's role, budget and approvals.")
            entry = self._existing(automation.key)
            current = by_id.get(entry.target_id) if entry and entry.target_id else None
            by_name = next((w for w in workflows if w.name.casefold() == automation.name.casefold()), None)

            def create(automation: BlueprintAutomation = automation) -> Applied:
                trig = automation.trigger
                payload = auto.WorkflowCreate(
                    name=automation.name,
                    description=f"Setup plan automation #{automation.number}. Created turned off: run it by hand, check the result, then turn it on.",
                    steps=[auto.WorkflowStep(id="task", name=automation.name, action="task.create",
                                             input={"title": automation.name, "prompt": automation.prompt or automation.name, "run": True})],
                    overlap_policy=auto.OverlapPolicy.SKIP,
                )
                workflow = auto.create_workflow(payload, self.context)
                details: dict[str, Any] = {"schedules": [], "triggers": []}
                # Turned off first, so it cannot run before its schedule and triggers exist (also off).
                auto.disable_workflow(workflow.id, self.context)
                if trig.get("type") == "schedule":
                    schedule = auto.create_schedule(auto.ScheduleCreate(workflow_id=workflow.id, name=automation.name, cron=trig["cron"],
                                                                        timezone=self.bp.timezone, enabled=False), self.context)
                    details["schedules"].append(schedule.id)
                for event in trig.get("events", []) if trig.get("type") == "event" else []:
                    created = auto.create_trigger(TriggerCreate(
                        workflow_id=workflow.id, name=f"{automation.name} ({event})", kind=TriggerKind.EVENT, event_type=event,
                        conditions=[TriggerCondition(**c) for c in trig.get("conditions", [])], enabled=False), self.context)
                    details["triggers"].append(created.trigger.id)
                return Applied(target_id=workflow.id, target_version=workflow.version, details=details)

            link = Link(view="automation", id=(current or by_name).id if (current or by_name) else None)
            out.append(self._workspace_object(automation.key, Section.AUTOMATIONS, "automation", automation.name, entry,
                                              current.id if current else None, by_name.id if by_name else None, link, facts, allowed,
                                              "You need permission to manage automations.", create,
                                              "Created turned off with its schedule or trigger also off.", week=automation.week))
        return out


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _assistant_fingerprint(value: Any) -> str:
    return fingerprint({
        "department": _plain(value.department), "name": value.name, "instructions": value.instructions,
        "allowed_tools": sorted(value.allowed_tools), "routing_keywords": list(value.routing_keywords),
        "autonomy": _plain(value.autonomy), "enabled": value.enabled,
    })


# --------------------------------------------------------------------------- preview and apply


def _sections(items: list[PlanItem]) -> list[SectionCount]:
    counts: list[SectionCount] = []
    for section in Section:
        rows = [item for item in items if item.section == section]
        if rows:
            actions: dict[str, int] = {}
            for row in rows:
                actions[row.action.value] = actions.get(row.action.value, 0) + 1
            counts.append(SectionCount(section=section, total=len(rows), actions=actions))
    return counts


def _summary(blueprint: Blueprint, context: TenantContext) -> PlanSummary:
    with open_setup_plan_ledger(context) as ledger:
        entries = [e for e in ledger.entries(context, blueprint.id) if e.state == LedgerState.APPLIED]
    last = max((e.updated_at for e in entries), default=None)
    return PlanSummary(
        id=blueprint.id, version=blueprint.version, name=blueprint.name, summary=blueprint.summary, currency=blueprint.currency,
        timezone=blueprint.timezone, business_hours=blueprint.business_hours, pilot_agents=blueprint.pilot_agents, weeks=blueprint.weeks,
        counts={
            "agents": len(blueprint.agents), "skills": len(blueprint.skills), "assistants": len(blueprint.assistants),
            "policy_packs": len(blueprint.policy_packs), "role_templates": len(blueprint.role_templates),
            "approval_rules": len(blueprint.approval_rules), "knowledge_folders": len(blueprint.knowledge_folders),
            "saved_commands": len(blueprint.saved_commands), "automations": len(blueprint.automations),
            "workspaces": len(blueprint.workspaces),
        },
        applied_items=len(entries),
        last_applied_at=last.isoformat() if last else None,
    )


def _access(context: TenantContext) -> CallerAccess:
    owner = "owner" in {role.strip().lower() for role in context.roles}
    org_admin = is_organization_admin(context)
    notes: list[str] = []
    if not org_admin:
        notes.append("You are not an organization administrator: policy packs, approval rules, role templates, memory rules and skill publishing are skipped. "
                     "An organization administrator can apply the plan again to add them.")
    if not owner:
        notes.append("You are not an owner of this workspace: assistants, skill installations and the AI budget are skipped.")
    return CallerAccess(owner=owner, organization_admin=org_admin, can_apply=True, notes=notes)


def build_preview(blueprint: Blueprint, context: TenantContext, phase: Phase, overwrite_edited: bool) -> tuple[PlanPreview, list[Planned]]:
    planned = Planner(blueprint, context, phase, overwrite_edited).plan()
    items = [p.item for p in planned]
    aed = blueprint.budget_aed(phase)
    preview = PlanPreview(
        plan=_summary(blueprint, context), phase=phase, overwrite_edited=overwrite_edited, access=_access(context),
        budget_aed=aed, budget_usd=round(aed / blueprint.aed_per_usd, 2), items=items, sections=_sections(items),
        approval_thresholds=blueprint.approval_thresholds, memory_rules=blueprint.memory_governance.rules,
    )
    return preview, planned


def _ledger_entry(blueprint: Blueprint, context: TenantContext, planned: Planned, state: LedgerState, applied: Applied | None = None) -> LedgerEntry:
    now = utc_now()
    return LedgerEntry(
        tenant_id=context.tenant_id, workspace_id=TENANT_SCOPE if planned.tenant_level else context.workspace_id,
        plan_id=blueprint.id, item_key=planned.ledger_key, kind=planned.item.kind, state=state,
        origin=applied.origin if applied else LedgerOrigin.CREATED,
        target_id=applied.target_id if applied else None, target_version=applied.target_version if applied else None,
        fingerprint=applied.fingerprint if applied else None, details=applied.details if applied else {},
        plan_version=blueprint.version, applied_by=context.user_id, created_at=now, updated_at=now,
    )


def apply_plan(blueprint: Blueprint, context: TenantContext, request: ApplyRequest) -> ApplyResult:
    preview, planned = build_preview(blueprint, context, request.phase, request.overwrite_edited)
    outcomes: dict[str, int] = {}
    for step in planned:
        item = step.item
        if step.run is None:
            item.outcome = {
                ItemAction.PRESENT: Outcome.PRESENT, ItemAction.KEPT: Outcome.KEPT, ItemAction.SKIPPED: Outcome.SKIPPED,
            }.get(item.action, Outcome.NOT_APPLIED)
        else:
            creating = item.action in {ItemAction.CREATE, ItemAction.PRESENT}
            claim = _ledger_entry(blueprint, context, step, LedgerState.CLAIMED)
            try:
                if creating:
                    with open_setup_plan_ledger(context) as ledger:
                        # Re-creating a removed object replaces its own applied row.
                        claimed = ledger.save(claim) is not None if item.reason_code == "recreate" else ledger.claim(claim)
                    if not claimed:
                        item.outcome = Outcome.BUSY
                        item.error = "Another apply is creating it right now; apply again in a minute."
                        outcomes[item.outcome.value] = outcomes.get(item.outcome.value, 0) + 1
                        continue
                applied = step.run()
                with open_setup_plan_ledger(context) as ledger:
                    ledger.save(_ledger_entry(blueprint, context, step, LedgerState.APPLIED, applied))
                item.outcome = (Outcome.PRESENT if item.action == ItemAction.PRESENT
                                else Outcome.UPDATED if item.action == ItemAction.UPDATE else Outcome.CREATED)
                if applied.target_id and item.link is not None and item.link.id is None and item.kind not in {"model_budget", "saved_command"}:
                    item.link = item.link.model_copy(update={"id": applied.target_id})
                elif applied.target_id and item.link is None:
                    item.link = _default_link(item.kind, applied.target_id)
            except Exception as exc:  # reported per item; the next apply continues from here
                logger.warning("setup_plan_item_failed", extra={"anum_plan": blueprint.id, "anum_item": item.key, "anum_error_class": type(exc).__name__})
                if creating:
                    try:
                        with open_setup_plan_ledger(context) as ledger:
                            ledger.release(claim)
                    except Exception:  # pragma: no cover - the stale claim is retaken after CLAIM_STALE_AFTER
                        logger.warning("setup_plan_release_failed", extra={"anum_plan": blueprint.id, "anum_item": item.key})
                item.outcome = Outcome.FAILED
                item.error = _error_text(exc)
        outcomes[item.outcome.value] = outcomes.get(item.outcome.value, 0) + 1
    now = utc_now()
    with _unit_of_work(context, require_membership=True) as repository:
        repository.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="setup_plan.applied", target=f"setup_plan:{blueprint.id}", outcome="success" if not outcomes.get("failed") else "partial",
            correlation_id=new_id("correlation"), created_at=now,
            metadata={"plan_version": blueprint.version, "phase": request.phase.value, "overwrite_edited": request.overwrite_edited, "outcomes": outcomes},
        ))
    items = [step.item for step in planned]
    return ApplyResult(
        **preview.model_dump(exclude={"items", "plan", "sections"}), plan=_summary(blueprint, context), items=items, sections=_sections(items),
        outcomes=outcomes, applied_at=now.isoformat(),
    )


def _default_link(kind: str, target_id: str) -> Link | None:
    view = {"agent": "agents", "assistant": "assistants", "knowledge_folder": "knowledge", "automation": "automation",
            "skill_installation": "skills", "skill_version": "skills", "workspace": "members"}.get(kind)
    return Link(view=view, id=target_id) if view else None


# --------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api/v1/setup-plans", tags=["setup-plans"])


def _require_access(context: TenantContext) -> None:
    require_owner_or_org_admin(context, Permission.POLICY_MANAGE, ACCESS_REQUIRED)


@router.get("", response_model=list[PlanSummary])
def list_setup_plans(context: TenantContext = Depends(tenant_context)) -> list[PlanSummary]:
    """The checked-in setup plans, with how much of each is applied here."""
    _require_access(context)
    return [_summary(blueprint, context) for blueprint in load_blueprints().values()]


@router.get("/{plan_id}/preview", response_model=PlanPreview)
def preview_setup_plan(
    plan_id: str,
    phase: Phase = Query(default=Phase.PILOT),
    overwrite_edited: bool = Query(default=False),
    context: TenantContext = Depends(tenant_context),
) -> PlanPreview:
    """What applying the plan would do here, per item. Read-only: nothing is created or audited."""
    _require_access(context)
    preview, _ = build_preview(blueprint_or_404(plan_id), context, phase, overwrite_edited)
    return preview


@router.post("/{plan_id}/apply", response_model=ApplyResult)
def apply_setup_plan(plan_id: str, payload: ApplyRequest, context: TenantContext = Depends(tenant_context)) -> ApplyResult:
    """Apply the plan's items through each feature's own routes; re-applying never duplicates."""
    _require_access(context)
    blueprint = blueprint_or_404(plan_id)
    if payload.plan_version != blueprint.version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"The setup plan changed to version {blueprint.version} since you previewed it. Review the new preview, then apply.")
    return apply_plan(blueprint, context, payload)


# --------------------------------------------------------------------------- pilot checks


class PilotCheckView(PilotCheck):
    result: CheckResult | None = None


class PilotChecksView(BaseModel):
    plan_id: str
    gate: str
    total: int
    passed: int
    failed: int
    not_run: int
    # The Week 1 "done when" gate: every check recorded as passed.
    done: bool
    checks: list[PilotCheckView]


def _checks_view(blueprint: Blueprint, context: TenantContext) -> PilotChecksView:
    with open_check_store(context) as store:
        results = {r.check_id: r for r in store.list(context, blueprint.id)}
    checks = [PilotCheckView(**check.model_dump(), result=results.get(check.id)) for check in blueprint.pilot_checks]
    passed = sum(1 for c in checks if c.result and c.result.status == CheckStatus.PASSED)
    failed = sum(1 for c in checks if c.result and c.result.status == CheckStatus.FAILED)
    return PilotChecksView(plan_id=blueprint.id, gate=blueprint.pilot_gate, total=len(checks), passed=passed, failed=failed,
                           not_run=len(checks) - passed - failed, done=passed == len(checks), checks=checks)


@router.get("/{plan_id}/checks", response_model=PilotChecksView)
def list_pilot_checks(plan_id: str, context: TenantContext = Depends(tenant_context)) -> PilotChecksView:
    """The plan's Week 1 pilot checks with what people recorded for this organization."""
    _require_access(context)
    return _checks_view(blueprint_or_404(plan_id), context)


@router.put("/{plan_id}/checks/{check_id}", response_model=PilotChecksView)
def record_pilot_check(plan_id: str, check_id: str, payload: CheckResultUpdate, context: TenantContext = Depends(tenant_context)) -> PilotChecksView:
    """Record pass, fail or not run for one check, with a note and evidence (audited)."""
    _require_access(context)
    blueprint = blueprint_or_404(plan_id)
    if not any(check.id == check_id for check in blueprint.pilot_checks):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pilot check not found in this plan")
    now = utc_now()
    result = CheckResult(plan_id=plan_id, check_id=check_id, status=payload.status, note=payload.note, evidence=payload.evidence,
                         updated_by=context.user_id, updated_at=now)
    with open_check_store(context) as store:
        store.save(context, result)
        # Never the note or the evidence text: only what changed.
        store.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="setup_plan.check_recorded", target=f"setup_plan:{plan_id}:{check_id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=now,
            metadata={"status": payload.status.value, "has_note": bool(payload.note), "has_evidence": bool(payload.evidence)},
        ))
    return _checks_view(blueprint, context)
