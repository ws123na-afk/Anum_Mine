from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .approval_routing import MAX_STEPS, ApprovalConditions, ApproverStep
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyRule(BaseModel):
    action: str = Field(min_length=1, max_length=160)
    effect: PolicyEffect
    conditions: dict[str, Any] = Field(default_factory=dict)


class PolicyPackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    rules: list[PolicyRule] = Field(min_length=1, max_length=100)


class PolicyPack(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    version: int
    active: bool
    rules: list[PolicyRule]
    created_by: str
    created_at: datetime


class RoleTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    permissions: list[str] = Field(min_length=1, max_length=100)


class RoleTemplate(BaseModel):
    id: str
    tenant_id: str
    name: str
    permissions: list[str]
    created_at: datetime


class ApprovalRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    action_pattern: str = Field(min_length=1, max_length=160)
    minimum_approvers: int = Field(default=1, ge=1, le=10)
    required_roles: list[str] = Field(default_factory=lambda: ["owner"])
    # Approval routing (docs/approvals-and-risk.md#approval-routing): when the rule applies
    # (an AED amount band, a share of the order value, a disputed flag) and who approves,
    # in one or two sequential steps. Without steps the rule keeps the original behaviour
    # (owners holding ``required_roles``, ``minimum_approvers`` distinct people).
    conditions: ApprovalConditions = Field(default_factory=ApprovalConditions)
    approver_steps: list[ApproverStep] = Field(default_factory=list, max_length=MAX_STEPS)
    enabled: bool = True


class ApprovalRule(ApprovalRuleCreate):
    id: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime | None = None
    updated_by: str | None = None


class MemoryGovernanceUpdate(BaseModel):
    default_retention_days: int = Field(ge=1, le=3650)
    allow_permanent_retention: bool = False
    require_provenance: bool = True
    allowed_source_types: list[str] = Field(default_factory=list, max_length=50)


class MemoryGovernance(MemoryGovernanceUpdate):
    tenant_id: str
    updated_by: str
    updated_at: datetime


class GovernanceSummary(BaseModel):
    tenant_id: str
    policy_packs: int
    active_policy_packs: int
    role_templates: int
    approval_rules: int
    memory_governance_configured: bool
    # Whether the caller may change the tenant-wide settings above (organization
    # administrator). Clients show the controls read-only, with the reason, when false.
    can_manage_organization_policy: bool = False


class RoleTemplateExistsError(ValueError):
    """Role template names are unique per tenant (case-insensitive)."""


class GovernanceRepository(Protocol):
    """Tenant governance settings and their audit trail.

    Policy packs, role templates, approval rules and memory governance are tenant-level;
    audit records are per workspace. The PostgreSQL store
    (``anum_api.db.governance_repository``) runs inside the tenant's RLS scope and writes
    audit records to the append-only ``audit_records`` table in the same transaction.
    """

    def list_policy_packs(self, context: TenantContext) -> list[PolicyPack]: ...
    def create_policy_pack(self, context: TenantContext, payload: PolicyPackCreate) -> PolicyPack: ...
    def get_policy_pack(self, context: TenantContext, policy_id: str) -> PolicyPack | None: ...
    def activate_policy_pack(self, context: TenantContext, pack: PolicyPack) -> PolicyPack: ...
    def archive_policy_pack(self, context: TenantContext, pack: PolicyPack) -> PolicyPack: ...
    def list_role_templates(self, context: TenantContext) -> list[RoleTemplate]: ...
    def add_role_template(self, context: TenantContext, template: RoleTemplate) -> RoleTemplate: ...
    def list_approval_rules(self, context: TenantContext) -> list[ApprovalRule]: ...
    def add_approval_rule(self, context: TenantContext, rule: ApprovalRule) -> ApprovalRule: ...
    def save_approval_rule(self, context: TenantContext, rule: ApprovalRule) -> ApprovalRule: ...
    def delete_approval_rule(self, context: TenantContext, rule_id: str) -> bool: ...
    def get_memory_governance(self, context: TenantContext) -> MemoryGovernance | None: ...
    def save_memory_governance(self, context: TenantContext, value: MemoryGovernance) -> MemoryGovernance: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...
    def query_audit(self, context: TenantContext) -> list[AuditRecord]: ...


class GovernanceStore:
    """In-memory governance store for ``ANUM_REPOSITORY_BACKEND=memory`` (local and tests)."""

    def __init__(self) -> None:
        self.policy_packs: dict[str, list[PolicyPack]] = {}
        self.role_templates: dict[str, list[RoleTemplate]] = {}
        self.approval_rules: dict[str, list[ApprovalRule]] = {}
        self.memory_governance: dict[str, MemoryGovernance] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.policy_packs.clear()
            self.role_templates.clear()
            self.approval_rules.clear()
            self.memory_governance.clear()

    def list_policy_packs(self, context: TenantContext) -> list[PolicyPack]:
        with self._lock:
            return list(self.policy_packs.get(context.tenant_id, []))

    def create_policy_pack(self, context: TenantContext, payload: PolicyPackCreate) -> PolicyPack:
        with self._lock:
            existing = self.policy_packs.setdefault(context.tenant_id, [])
            previous = [pack for pack in existing if pack.name.casefold() == payload.name.casefold()]
            for pack in previous:
                pack.active = False
            result = PolicyPack(
                id=new_id("policy"), tenant_id=context.tenant_id, name=payload.name,
                description=payload.description, version=max((p.version for p in previous), default=0) + 1,
                active=True, rules=payload.rules, created_by=context.user_id, created_at=utc_now(),
            )
            existing.append(result)
        return result

    def get_policy_pack(self, context: TenantContext, policy_id: str) -> PolicyPack | None:
        with self._lock:
            return next((item for item in self.policy_packs.get(context.tenant_id, []) if item.id == policy_id), None)

    def activate_policy_pack(self, context: TenantContext, pack: PolicyPack) -> PolicyPack:
        with self._lock:
            for item in self.policy_packs.get(context.tenant_id, []):
                if item.name.casefold() == pack.name.casefold():
                    item.active = item.id == pack.id
        return pack

    def archive_policy_pack(self, context: TenantContext, pack: PolicyPack) -> PolicyPack:
        with self._lock:
            pack.active = False
        return pack

    def list_role_templates(self, context: TenantContext) -> list[RoleTemplate]:
        with self._lock:
            return list(self.role_templates.get(context.tenant_id, []))

    def add_role_template(self, context: TenantContext, template: RoleTemplate) -> RoleTemplate:
        with self._lock:
            templates = self.role_templates.setdefault(template.tenant_id, [])
            if any(item.name.casefold() == template.name.casefold() for item in templates):
                raise RoleTemplateExistsError(template.name)
            templates.append(template)
        return template

    def list_approval_rules(self, context: TenantContext) -> list[ApprovalRule]:
        with self._lock:
            return list(self.approval_rules.get(context.tenant_id, []))

    def add_approval_rule(self, context: TenantContext, rule: ApprovalRule) -> ApprovalRule:
        with self._lock:
            self.approval_rules.setdefault(rule.tenant_id, []).append(rule)
        return rule

    def save_approval_rule(self, context: TenantContext, rule: ApprovalRule) -> ApprovalRule:
        with self._lock:
            rules = self.approval_rules.setdefault(context.tenant_id, [])
            for index, item in enumerate(rules):
                if item.id == rule.id:
                    rules[index] = rule
        return rule

    def delete_approval_rule(self, context: TenantContext, rule_id: str) -> bool:
        with self._lock:
            rules = self.approval_rules.get(context.tenant_id, [])
            kept = [item for item in rules if item.id != rule_id]
            self.approval_rules[context.tenant_id] = kept
            return len(kept) != len(rules)

    def get_memory_governance(self, context: TenantContext) -> MemoryGovernance | None:
        return self.memory_governance.get(context.tenant_id)

    def save_memory_governance(self, context: TenantContext, value: MemoryGovernance) -> MemoryGovernance:
        with self._lock:
            self.memory_governance[value.tenant_id] = value
        return value

    # Audit records go to the same in-memory store as every other audit record, so the
    # audit log and its export show governance changes next to membership changes, as
    # with PostgreSQL (one ``audit_records`` table).
    def record_audit(self, record: AuditRecord) -> AuditRecord:
        from .dependencies import memory_repository

        with self._lock:
            return memory_repository.record_audit(record)

    def query_audit(self, context: TenantContext) -> list[AuditRecord]:
        from .dependencies import memory_repository

        return memory_repository.list_audit_records(context)


governance_store = GovernanceStore()


@contextmanager
def open_governance_store(context: TenantContext) -> Iterator[GovernanceRepository]:
    """The governance store for one unit of work, chosen by ``ANUM_REPOSITORY_BACKEND``."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.governance_repository import SqlAlchemyGovernanceStore

        return SqlAlchemyGovernanceStore(session)

    with open_scoped_store(context, governance_store, sql_store) as store:
        yield store


router = APIRouter(prefix="/api/v1", tags=["governance"])


def _organization_admin(context: TenantContext) -> None:
    """Policy packs, role templates, approval rules and memory governance bind every
    workspace of the tenant, so changing them needs the organization administrator role;
    owning one workspace is not enough (docs/governance-and-scale.md#who-may-change-what)."""
    require_permission(context, Permission.ORG_POLICY_MANAGE)


def _owner(context: TenantContext, permission: Permission) -> None:
    require_permission(context, permission)


def _audit(
    store: GovernanceRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any]
) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id,
            workspace_id=context.workspace_id, actor=context.user_id,
            action=action, target=target, outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(), metadata=metadata,
        )
    )


@router.get("/organization/governance", response_model=GovernanceSummary)
def summary(context: TenantContext = Depends(tenant_context)) -> GovernanceSummary:
    require_permission(context, Permission.ORGANIZATION_READ)
    with open_governance_store(context) as store:
        packs = store.list_policy_packs(context)
        return GovernanceSummary(
            tenant_id=context.tenant_id, policy_packs=len(packs),
            active_policy_packs=sum(pack.active for pack in packs),
            role_templates=len(store.list_role_templates(context)),
            approval_rules=len(store.list_approval_rules(context)),
            memory_governance_configured=store.get_memory_governance(context) is not None,
            can_manage_organization_policy=has_permission(context, Permission.ORG_POLICY_MANAGE),
        )


@router.post("/policy-packs", response_model=PolicyPack, status_code=status.HTTP_201_CREATED)
def create_policy_pack(payload: PolicyPackCreate, context: TenantContext = Depends(tenant_context)) -> PolicyPack:
    _organization_admin(context)
    with open_governance_store(context) as store:
        result = store.create_policy_pack(context, payload)
        _audit(store, context, "policy_pack.created", f"policy_pack:{result.id}", {"name": result.name, "version": result.version})
    return result


@router.get("/policy-packs", response_model=list[PolicyPack])
def list_policy_packs(active_only: bool = False, context: TenantContext = Depends(tenant_context)) -> list[PolicyPack]:
    require_permission(context, Permission.POLICY_READ)
    with open_governance_store(context) as store:
        packs = store.list_policy_packs(context)
    return [pack for pack in packs if pack.active or not active_only]


def _policy_pack(store: GovernanceRepository, policy_id: str, context: TenantContext) -> PolicyPack:
    pack = store.get_policy_pack(context, policy_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Policy pack not found")
    return pack


@router.post("/policy-packs/{policy_id}/activate", response_model=PolicyPack)
def activate_policy_pack(policy_id: str, context: TenantContext = Depends(tenant_context)) -> PolicyPack:
    _organization_admin(context)
    with open_governance_store(context) as store:
        pack = store.activate_policy_pack(context, _policy_pack(store, policy_id, context))
        _audit(store, context, "policy_pack.activated", f"policy_pack:{pack.id}", {"name": pack.name, "version": pack.version})
    return pack


@router.post("/policy-packs/{policy_id}/archive", response_model=PolicyPack)
def archive_policy_pack(policy_id: str, context: TenantContext = Depends(tenant_context)) -> PolicyPack:
    _organization_admin(context)
    with open_governance_store(context) as store:
        pack = store.archive_policy_pack(context, _policy_pack(store, policy_id, context))
        _audit(store, context, "policy_pack.archived", f"policy_pack:{pack.id}", {"name": pack.name, "version": pack.version})
    return pack


@router.post("/role-templates", response_model=RoleTemplate, status_code=status.HTTP_201_CREATED)
def create_role_template(payload: RoleTemplateCreate, context: TenantContext = Depends(tenant_context)) -> RoleTemplate:
    _organization_admin(context)
    template = RoleTemplate(id=new_id("role"), tenant_id=context.tenant_id, name=payload.name,
                            permissions=sorted(set(payload.permissions)), created_at=utc_now())
    try:
        with open_governance_store(context) as store:
            result = store.add_role_template(context, template)
            _audit(store, context, "role_template.created", f"role_template:{result.id}", {"name": result.name})
    except RoleTemplateExistsError as exc:
        raise HTTPException(status_code=409, detail="Role template name already exists") from exc
    return result


@router.get("/role-templates", response_model=list[RoleTemplate])
def list_role_templates(context: TenantContext = Depends(tenant_context)) -> list[RoleTemplate]:
    require_permission(context, Permission.ORGANIZATION_READ)
    with open_governance_store(context) as store:
        return store.list_role_templates(context)


def _rule_audit(rule: ApprovalRule) -> dict[str, Any]:
    """What an approval rule change records: its settings, never anything secret."""
    return {
        "name": rule.name,
        "action_pattern": rule.action_pattern,
        "enabled": rule.enabled,
        "minimum_approvers": rule.minimum_approvers,
        "required_roles": list(rule.required_roles),
        "conditions": rule.conditions.model_dump(exclude_none=True),
        "approver_steps": [step.approvers for step in rule.approver_steps],
    }


@router.post("/organization/approval-rules", response_model=ApprovalRule, status_code=201)
def create_approval_rule(payload: ApprovalRuleCreate, context: TenantContext = Depends(tenant_context)) -> ApprovalRule:
    _organization_admin(context)
    rule = ApprovalRule(id=new_id("approval_rule"), tenant_id=context.tenant_id,
                        created_at=utc_now(), **payload.model_dump())
    with open_governance_store(context) as store:
        result = store.add_approval_rule(context, rule)
        _audit(store, context, "approval_rule.created", f"approval_rule:{result.id}", _rule_audit(result))
    return result


def _approval_rule(store: GovernanceRepository, context: TenantContext, rule_id: str) -> ApprovalRule:
    rule = next((item for item in store.list_approval_rules(context) if item.id == rule_id), None)
    if rule is None:
        raise HTTPException(status_code=404, detail="Approval rule not found")
    return rule


@router.put("/organization/approval-rules/{rule_id}", response_model=ApprovalRule)
def update_approval_rule(
    rule_id: str, payload: ApprovalRuleCreate, context: TenantContext = Depends(tenant_context)
) -> ApprovalRule:
    """Replace an approval rule's settings (org_admin). Takes effect at the next evaluation,
    including approvals already waiting: the route is re-read at every approve."""
    _organization_admin(context)
    with open_governance_store(context) as store:
        before = _approval_rule(store, context, rule_id)
        rule = ApprovalRule(id=before.id, tenant_id=before.tenant_id, created_at=before.created_at,
                            updated_at=utc_now(), updated_by=context.user_id, **payload.model_dump())
        result = store.save_approval_rule(context, rule)
        _audit(store, context, "approval_rule.updated", f"approval_rule:{result.id}",
               {"before": _rule_audit(before), "after": _rule_audit(result)})
    return result


@router.delete("/organization/approval-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_approval_rule(rule_id: str, context: TenantContext = Depends(tenant_context)) -> None:
    """Delete an approval rule (org_admin). Approvals it routed fall back to the remaining
    rules (or the owners) at their next decision."""
    _organization_admin(context)
    with open_governance_store(context) as store:
        before = _approval_rule(store, context, rule_id)
        store.delete_approval_rule(context, rule_id)
        _audit(store, context, "approval_rule.deleted", f"approval_rule:{rule_id}", _rule_audit(before))


@router.get("/organization/approval-rules", response_model=list[ApprovalRule])
def list_approval_rules(context: TenantContext = Depends(tenant_context)) -> list[ApprovalRule]:
    require_permission(context, Permission.ORGANIZATION_READ)
    with open_governance_store(context) as store:
        return store.list_approval_rules(context)


@router.put("/organization/memory-governance", response_model=MemoryGovernance)
def update_memory_governance(payload: MemoryGovernanceUpdate, context: TenantContext = Depends(tenant_context)) -> MemoryGovernance:
    _organization_admin(context)
    value = MemoryGovernance(tenant_id=context.tenant_id, updated_by=context.user_id,
                             updated_at=utc_now(), **payload.model_dump())
    with open_governance_store(context) as store:
        result = store.save_memory_governance(context, value)
        _audit(store, context, "memory_governance.updated", f"tenant:{context.tenant_id}", payload.model_dump())
    return result


@router.get("/organization/memory-governance", response_model=MemoryGovernance)
def get_memory_governance(context: TenantContext = Depends(tenant_context)) -> MemoryGovernance:
    require_permission(context, Permission.ORGANIZATION_READ)
    with open_governance_store(context) as store:
        result = store.get_memory_governance(context)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory governance is not configured")
    return result
