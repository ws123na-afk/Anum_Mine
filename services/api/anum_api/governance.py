from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .audit import AuditRecord, InMemoryAuditRecorder
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now


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


class ApprovalRule(ApprovalRuleCreate):
    id: str
    tenant_id: str
    enabled: bool = True
    created_at: datetime


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


class GovernanceStore:
    def __init__(self) -> None:
        self.policy_packs: dict[str, list[PolicyPack]] = {}
        self.role_templates: dict[str, list[RoleTemplate]] = {}
        self.approval_rules: dict[str, list[ApprovalRule]] = {}
        self.memory_governance: dict[str, MemoryGovernance] = {}
        self.audit = InMemoryAuditRecorder()
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.policy_packs.clear()
            self.role_templates.clear()
            self.approval_rules.clear()
            self.memory_governance.clear()
            self.audit = InMemoryAuditRecorder()


governance_store = GovernanceStore()
router = APIRouter(prefix="/api/v1", tags=["governance"])


def _owner(context: TenantContext, permission: Permission) -> None:
    require_permission(context, permission)


def _audit(context: TenantContext, action: str, target: str, metadata: dict[str, Any]) -> None:
    governance_store.audit.record(
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
    packs = governance_store.policy_packs.get(context.tenant_id, [])
    return GovernanceSummary(
        tenant_id=context.tenant_id, policy_packs=len(packs),
        active_policy_packs=sum(pack.active for pack in packs),
        role_templates=len(governance_store.role_templates.get(context.tenant_id, [])),
        approval_rules=len(governance_store.approval_rules.get(context.tenant_id, [])),
        memory_governance_configured=context.tenant_id in governance_store.memory_governance,
    )


@router.post("/policy-packs", response_model=PolicyPack, status_code=status.HTTP_201_CREATED)
def create_policy_pack(payload: PolicyPackCreate, context: TenantContext = Depends(tenant_context)) -> PolicyPack:
    _owner(context, Permission.POLICY_MANAGE)
    with governance_store._lock:
        existing = governance_store.policy_packs.setdefault(context.tenant_id, [])
        previous = [pack for pack in existing if pack.name.casefold() == payload.name.casefold()]
        for pack in previous:
            pack.active = False
        result = PolicyPack(
            id=new_id("policy"), tenant_id=context.tenant_id, name=payload.name,
            description=payload.description, version=max((p.version for p in previous), default=0) + 1,
            active=True, rules=payload.rules, created_by=context.user_id, created_at=utc_now(),
        )
        existing.append(result)
    _audit(context, "policy_pack.created", f"policy_pack:{result.id}", {"name": result.name, "version": result.version})
    return result


@router.get("/policy-packs", response_model=list[PolicyPack])
def list_policy_packs(active_only: bool = False, context: TenantContext = Depends(tenant_context)) -> list[PolicyPack]:
    require_permission(context, Permission.POLICY_READ)
    packs = governance_store.policy_packs.get(context.tenant_id, [])
    return [pack for pack in packs if pack.active or not active_only]


@router.post("/role-templates", response_model=RoleTemplate, status_code=status.HTTP_201_CREATED)
def create_role_template(payload: RoleTemplateCreate, context: TenantContext = Depends(tenant_context)) -> RoleTemplate:
    _owner(context, Permission.ORGANIZATION_MANAGE)
    templates = governance_store.role_templates.setdefault(context.tenant_id, [])
    if any(item.name.casefold() == payload.name.casefold() for item in templates):
        raise HTTPException(status_code=409, detail="Role template name already exists")
    result = RoleTemplate(id=new_id("role"), tenant_id=context.tenant_id, name=payload.name,
                          permissions=sorted(set(payload.permissions)), created_at=utc_now())
    templates.append(result)
    _audit(context, "role_template.created", f"role_template:{result.id}", {"name": result.name})
    return result


@router.get("/role-templates", response_model=list[RoleTemplate])
def list_role_templates(context: TenantContext = Depends(tenant_context)) -> list[RoleTemplate]:
    require_permission(context, Permission.ORGANIZATION_READ)
    return governance_store.role_templates.get(context.tenant_id, [])


@router.post("/organization/approval-rules", response_model=ApprovalRule, status_code=201)
def create_approval_rule(payload: ApprovalRuleCreate, context: TenantContext = Depends(tenant_context)) -> ApprovalRule:
    _owner(context, Permission.GOVERNANCE_MANAGE)
    result = ApprovalRule(id=new_id("approval_rule"), tenant_id=context.tenant_id,
                          created_at=utc_now(), **payload.model_dump())
    governance_store.approval_rules.setdefault(context.tenant_id, []).append(result)
    _audit(context, "approval_rule.created", f"approval_rule:{result.id}", {"action_pattern": result.action_pattern})
    return result


@router.get("/organization/approval-rules", response_model=list[ApprovalRule])
def list_approval_rules(context: TenantContext = Depends(tenant_context)) -> list[ApprovalRule]:
    require_permission(context, Permission.ORGANIZATION_READ)
    return governance_store.approval_rules.get(context.tenant_id, [])


@router.put("/organization/memory-governance", response_model=MemoryGovernance)
def update_memory_governance(payload: MemoryGovernanceUpdate, context: TenantContext = Depends(tenant_context)) -> MemoryGovernance:
    _owner(context, Permission.GOVERNANCE_MANAGE)
    result = MemoryGovernance(tenant_id=context.tenant_id, updated_by=context.user_id,
                              updated_at=utc_now(), **payload.model_dump())
    governance_store.memory_governance[context.tenant_id] = result
    _audit(context, "memory_governance.updated", f"tenant:{context.tenant_id}", payload.model_dump())
    return result


@router.get("/organization/memory-governance", response_model=MemoryGovernance)
def get_memory_governance(context: TenantContext = Depends(tenant_context)) -> MemoryGovernance:
    require_permission(context, Permission.ORGANIZATION_READ)
    result = governance_store.memory_governance.get(context.tenant_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory governance is not configured")
    return result


@router.get("/audit/export")
def export_audit(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    context: TenantContext = Depends(tenant_context),
) -> Response:
    _owner(context, Permission.AUDIT_EXPORT)
    records = governance_store.audit.query(context)
    rows = [{
        "id": r.id, "tenant_id": r.tenant_id, "workspace_id": r.workspace_id,
        "actor": r.actor, "action": r.action, "target": r.target,
        "outcome": r.outcome, "correlation_id": r.correlation_id,
        "created_at": r.created_at.isoformat(), "metadata": dict(r.metadata),
    } for r in records]
    if format == "json":
        return Response(json.dumps(rows, default=list), media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename=audit-export.json"})
    output = io.StringIO()
    fields = ["id", "tenant_id", "workspace_id", "actor", "action", "target", "outcome", "correlation_id", "created_at", "metadata"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        row["metadata"] = json.dumps(row["metadata"], sort_keys=True, default=list)
        writer.writerow(row)
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=audit-export.csv"})
