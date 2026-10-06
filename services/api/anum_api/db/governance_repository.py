from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anum_api.audit import AuditRecord
from anum_api.governance import (
    ApprovalRule,
    MemoryGovernance,
    PolicyPack,
    PolicyPackCreate,
    PolicyRule,
    RoleTemplate,
    RoleTemplateExistsError,
)
from anum_api.schemas import TenantContext, new_id, utc_now

from .models import (
    ApprovalRuleRecord,
    MemoryGovernanceRecord,
    PolicyPackRecord,
    RoleTemplateRecord,
)
from .repository import SqlAlchemyRepository
from .scope import require_workspace


class SqlAlchemyGovernanceStore:
    """PostgreSQL governance store.

    Settings are tenant-level rows; every write is audited into ``audit_records`` for the
    caller's workspace in the same transaction, so a write needs an onboarded workspace.
    The session must already carry the tenant and workspace RLS context.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._audit = SqlAlchemyRepository(session)

    # Policy packs -------------------------------------------------------------------

    def list_policy_packs(self, context: TenantContext) -> list[PolicyPack]:
        records = self.session.scalars(
            select(PolicyPackRecord)
            .where(PolicyPackRecord.tenant_id == context.tenant_id)
            .order_by(PolicyPackRecord.created_at, PolicyPackRecord.version, PolicyPackRecord.id)
        ).all()
        return [_policy_pack(record) for record in records]

    def create_policy_pack(self, context: TenantContext, payload: PolicyPackCreate) -> PolicyPack:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        previous = self.session.scalars(
            select(PolicyPackRecord)
            .where(
                PolicyPackRecord.tenant_id == context.tenant_id,
                func.lower(PolicyPackRecord.name) == payload.name.lower(),
            )
            .with_for_update()
        ).all()
        for record in previous:
            record.active = False
        record = PolicyPackRecord(
            id=new_id("policy"),
            tenant_id=context.tenant_id,
            name=payload.name,
            description=payload.description,
            version=max((item.version for item in previous), default=0) + 1,
            active=True,
            rules=[rule.model_dump(mode="json") for rule in payload.rules],
            created_by=context.user_id,
            created_at=utc_now(),
        )
        self.session.add(record)
        self.session.flush()
        return _policy_pack(record)

    def get_policy_pack(self, context: TenantContext, policy_id: str) -> PolicyPack | None:
        record = self.session.scalar(
            select(PolicyPackRecord).where(
                PolicyPackRecord.tenant_id == context.tenant_id,
                PolicyPackRecord.id == policy_id,
            )
        )
        return None if record is None else _policy_pack(record)

    def activate_policy_pack(self, context: TenantContext, pack: PolicyPack) -> PolicyPack:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(
            update(PolicyPackRecord)
            .where(
                PolicyPackRecord.tenant_id == context.tenant_id,
                func.lower(PolicyPackRecord.name) == pack.name.lower(),
            )
            .values(active=PolicyPackRecord.id == pack.id)
            .execution_options(synchronize_session=False)
        )
        return pack.model_copy(update={"active": True})

    def archive_policy_pack(self, context: TenantContext, pack: PolicyPack) -> PolicyPack:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.execute(
            update(PolicyPackRecord)
            .where(PolicyPackRecord.tenant_id == context.tenant_id, PolicyPackRecord.id == pack.id)
            .values(active=False)
            .execution_options(synchronize_session=False)
        )
        return pack.model_copy(update={"active": False})

    # Role templates -----------------------------------------------------------------

    def list_role_templates(self, context: TenantContext) -> list[RoleTemplate]:
        records = self.session.scalars(
            select(RoleTemplateRecord)
            .where(RoleTemplateRecord.tenant_id == context.tenant_id)
            .order_by(RoleTemplateRecord.created_at, RoleTemplateRecord.id)
        ).all()
        return [
            RoleTemplate(
                id=record.id,
                tenant_id=record.tenant_id,
                name=record.name,
                permissions=list(record.permissions),
                created_at=record.created_at,
            )
            for record in records
        ]

    def add_role_template(self, context: TenantContext, template: RoleTemplate) -> RoleTemplate:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        exists = self.session.scalar(
            select(RoleTemplateRecord.id).where(
                RoleTemplateRecord.tenant_id == template.tenant_id,
                func.lower(RoleTemplateRecord.name) == template.name.lower(),
            )
        )
        if exists is not None:
            raise RoleTemplateExistsError(template.name)
        try:
            with self.session.begin_nested():
                self.session.add(
                    RoleTemplateRecord(
                        id=template.id,
                        tenant_id=template.tenant_id,
                        name=template.name,
                        permissions=list(template.permissions),
                        created_at=template.created_at,
                    )
                )
        except IntegrityError as exc:  # a concurrent create with the same name won
            raise RoleTemplateExistsError(template.name) from exc
        return template

    # Approval rules -----------------------------------------------------------------

    def list_approval_rules(self, context: TenantContext) -> list[ApprovalRule]:
        records = self.session.scalars(
            select(ApprovalRuleRecord)
            .where(ApprovalRuleRecord.tenant_id == context.tenant_id)
            .order_by(ApprovalRuleRecord.created_at, ApprovalRuleRecord.id)
        ).all()
        return [
            ApprovalRule(
                id=record.id,
                tenant_id=record.tenant_id,
                name=record.name,
                action_pattern=record.action_pattern,
                minimum_approvers=record.minimum_approvers,
                required_roles=list(record.required_roles),
                enabled=record.enabled,
                created_at=record.created_at,
            )
            for record in records
        ]

    def add_approval_rule(self, context: TenantContext, rule: ApprovalRule) -> ApprovalRule:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        self.session.add(
            ApprovalRuleRecord(
                id=rule.id,
                tenant_id=rule.tenant_id,
                name=rule.name,
                action_pattern=rule.action_pattern,
                minimum_approvers=rule.minimum_approvers,
                required_roles=list(rule.required_roles),
                enabled=rule.enabled,
                created_at=rule.created_at,
            )
        )
        self.session.flush()
        return rule

    # Memory governance --------------------------------------------------------------

    def get_memory_governance(self, context: TenantContext) -> MemoryGovernance | None:
        record = self.session.get(MemoryGovernanceRecord, context.tenant_id)
        if record is None or record.tenant_id != context.tenant_id:
            return None
        return MemoryGovernance(
            tenant_id=record.tenant_id,
            default_retention_days=record.default_retention_days,
            allow_permanent_retention=record.allow_permanent_retention,
            require_provenance=record.require_provenance,
            allowed_source_types=list(record.allowed_source_types),
            updated_by=record.updated_by,
            updated_at=record.updated_at,
        )

    def save_memory_governance(self, context: TenantContext, value: MemoryGovernance) -> MemoryGovernance:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        record = self.session.get(MemoryGovernanceRecord, value.tenant_id)
        if record is None:
            record = MemoryGovernanceRecord(tenant_id=value.tenant_id)
            self.session.add(record)
        record.default_retention_days = value.default_retention_days
        record.allow_permanent_retention = value.allow_permanent_retention
        record.require_provenance = value.require_provenance
        record.allowed_source_types = list(value.allowed_source_types)
        record.updated_by = value.updated_by
        record.updated_at = value.updated_at
        self.session.flush()
        return value

    # Audit --------------------------------------------------------------------------

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        return self._audit.record_audit(record)

    def query_audit(self, context: TenantContext) -> list[AuditRecord]:
        return self._audit.list_audit_records(context)


def _policy_pack(record: PolicyPackRecord) -> PolicyPack:
    return PolicyPack(
        id=record.id,
        tenant_id=record.tenant_id,
        name=record.name,
        description=record.description,
        version=record.version,
        active=record.active,
        rules=[PolicyRule.model_validate(rule) for rule in record.rules],
        created_by=record.created_by,
        created_at=record.created_at,
    )
