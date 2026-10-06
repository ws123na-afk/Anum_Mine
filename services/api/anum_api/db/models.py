from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    """Minimal pgvector type declaration without adding an ORM runtime dependency."""

    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: Any) -> str:
        return f"VECTOR({self.dimensions})"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantScopedMixin:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)


class WorkspaceScopedMixin(TenantScopedMixin):
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, server_default="active")

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="tenant")


class Workspace(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="workspaces")
    tasks: Mapped[list["TaskRecord"]] = relationship(back_populates="workspace")

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_workspaces_tenant"),
        UniqueConstraint("tenant_id", "id", name="uq_workspaces_tenant_id"),
    )


class WorkspaceMembershipRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "workspace_memberships"

    user_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, server_default="true")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_memberships_workspace",
        ),
        Index("ix_workspace_memberships_user", "user_id", "tenant_id", "workspace_id"),
    )


class TaskRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_by_user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="tasks")
    runs: Mapped[list["AgentRunRecord"]] = relationship(back_populates="task")
    approvals: Mapped[list["ApprovalRecord"]] = relationship(back_populates="task")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_tasks_workspace",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_tasks_scope_id"),
        Index("ix_tasks_tenant_workspace_status", "tenant_id", "workspace_id", "status"),
    )


class AgentRunRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    task: Mapped[TaskRecord] = relationship(back_populates="runs")
    steps: Mapped[list["AgentRunStepRecord"]] = relationship(back_populates="run")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "task_id"],
            ["tasks.tenant_id", "tasks.workspace_id", "tasks.id"],
            name="fk_agent_runs_task",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_agent_runs_scope_id"),
        Index("ix_agent_runs_tenant_workspace_task", "tenant_id", "workspace_id", "task_id"),
    )


class AgentRunStepRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "agent_run_steps"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    step_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    run: Mapped[AgentRunRecord] = relationship(back_populates="steps")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.workspace_id", "agent_runs.id"],
            name="fk_agent_run_steps_run",
        ),
        Index("ix_agent_run_steps_tenant_workspace_run", "tenant_id", "workspace_id", "run_id"),
    )


class ApprovalRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[TaskRecord] = relationship(back_populates="approvals")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "task_id"],
            ["tasks.tenant_id", "tasks.workspace_id", "tasks.id"],
            name="fk_approvals_task",
        ),
        Index("ix_approvals_tenant_workspace_status", "tenant_id", "workspace_id", "status"),
    )


class DomainEventRecord(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "domain_events"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    type: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, server_default="1")
    workspace_id: Mapped[str | None] = mapped_column(String(80))
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Durable outbox state (migration 0007). The application never writes these; the
    # outbox relay (anum_api.outbox_relay) marks rows through the anum_outbox_relay role.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    publish_next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    publish_last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_domain_events_workspace",
        ),
        Index(
            "ix_domain_events_outbox_pending",
            "publish_next_attempt_at",
            "created_at",
            "id",
            postgresql_where=text("published_at is null"),
        ),
        Index(
            "ix_events_tenant_workspace_type_created",
            "tenant_id",
            "workspace_id",
            "type",
            "created_at",
        ),
        Index("ix_events_correlation", "correlation_id"),
    )


class MemoryRecord(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(80))
    source_task_id: Mapped[str | None] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(Vector(1536))
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    retention_policy: Mapped[str] = mapped_column(
        String(80), nullable=False, server_default="default"
    )
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_memories_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "source_task_id"],
            ["tasks.tenant_id", "tasks.workspace_id", "tasks.id"],
            name="fk_memories_source_task",
        ),
        CheckConstraint(
            "source_task_id is null or workspace_id is not null",
            name="ck_memories_source_task_workspace",
        ),
        Index("ix_memories_tenant_workspace", "tenant_id", "workspace_id"),
    )


class WorkspaceModelConfigRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    """The model a workspace chose in Settings.

    The provider API key is stored only as Fernet ciphertext (``anum_api.secret_box``).
    ``credential_hint`` holds the last four characters the API already shows, so reads
    that only display the configuration never decrypt the key.
    """

    __tablename__ = "workspace_model_configs"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    credential_hint: Mapped[str | None] = mapped_column(String(16))
    updated_by_user_id: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_model_configs_workspace",
        ),
        CheckConstraint(
            "provider in ('mock', 'openai_compatible', 'ollama')",
            name="ck_workspace_model_configs_provider",
        ),
        CheckConstraint(
            "provider <> 'openai_compatible' or api_key_ciphertext is not null",
            name="ck_workspace_model_configs_credential",
        ),
    )


class WorkspaceInvitationRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    """Single-use workspace invitation. Only the SHA-256 hash of the token is stored."""

    __tablename__ = "workspace_invitations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    invitee_user_id: Mapped[str | None] = mapped_column(String(120))
    invitee_email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    created_by_user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(120))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_invitations_workspace",
        ),
        CheckConstraint(
            "role in ('owner', 'member', 'viewer')", name="ck_workspace_invitations_role"
        ),
        CheckConstraint(
            "status in ('pending', 'accepted', 'revoked')",
            name="ck_workspace_invitations_status",
        ),
        CheckConstraint(
            "invitee_user_id is not null or invitee_email is not null",
            name="ck_workspace_invitations_invitee",
        ),
        Index("ix_workspace_invitations_scope_status", "tenant_id", "workspace_id", "status"),
    )


class AuditRecordRow(Base, WorkspaceScopedMixin):
    """Append-only audit history: RLS allows select and insert only."""

    __tablename__ = "audit_records"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    record_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_audit_records_workspace",
        ),
        Index("ix_audit_records_scope_created", "tenant_id", "workspace_id", "created_at"),
    )


# Control-plane stores (migration 0008). Tenant-level settings carry ``tenant_id`` only and
# their RLS policy checks the tenant; workspace-level rows check tenant and workspace.


def _workspace_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        ["workspaces.tenant_id", "workspaces.id"],
        name=f"fk_{table}_workspace",
    )


def _tenant_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=f"fk_{table}_tenant")


class SkillVersionRecord(Base, TenantScopedMixin):
    """An immutable published skill version, owned by the publishing tenant."""

    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    required_tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _tenant_fk("skill_versions"),
        UniqueConstraint("tenant_id", "skill_id", "version", name="uq_skill_versions_tenant_skill_version"),
        UniqueConstraint("tenant_id", "id", name="uq_skill_versions_tenant_id"),
    )


class SkillInstallationRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "skill_installations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    skill_version_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    installed_by: Mapped[str] = mapped_column(String(160), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("skill_installations"),
        ForeignKeyConstraint(
            ["tenant_id", "skill_version_id"],
            ["skill_versions.tenant_id", "skill_versions.id"],
            name="fk_skill_installations_version",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "skill_id", name="uq_skill_installations_scope_skill"),
    )


class PolicyPackRecord(Base, TenantScopedMixin):
    __tablename__ = "policy_packs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _tenant_fk("policy_packs"),
        Index("uq_policy_packs_tenant_name_version", "tenant_id", func.lower(text("name")), "version", unique=True),
    )


class RoleTemplateRecord(Base, TenantScopedMixin):
    __tablename__ = "role_templates"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _tenant_fk("role_templates"),
        Index("uq_role_templates_tenant_name", "tenant_id", func.lower(text("name")), unique=True),
    )


class ApprovalRuleRecord(Base, TenantScopedMixin):
    __tablename__ = "approval_rules"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    action_pattern: Mapped[str] = mapped_column(String(160), nullable=False)
    minimum_approvers: Mapped[int] = mapped_column(nullable=False)
    required_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_tenant_fk("approval_rules"),)


class MemoryGovernanceRecord(Base):
    __tablename__ = "memory_governance"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    default_retention_days: Mapped[int] = mapped_column(nullable=False)
    allow_permanent_retention: Mapped[bool] = mapped_column(nullable=False)
    require_provenance: Mapped[bool] = mapped_column(nullable=False)
    allowed_source_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_tenant_fk("memory_governance"),)


class MarketplacePackageRecord(Base):
    """A package in the tenant's marketplace catalog."""

    __tablename__ = "marketplace_packages"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    publisher: Mapped[str] = mapped_column(String(160), nullable=False)
    verified: Mapped[bool] = mapped_column(nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_tenant_fk("marketplace_packages"),)


class MarketplaceInstallRecord(Base):
    __tablename__ = "marketplace_installs"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    package_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    installed_by: Mapped[str] = mapped_column(String(160), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("marketplace_installs"),
        # RESTRICT: a package installed in any workspace cannot leave the catalog.
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["marketplace_packages.tenant_id", "marketplace_packages.id"],
            name="fk_marketplace_installs_package",
            ondelete="RESTRICT",
        ),
    )


class RoutingTargetRecord(Base):
    __tablename__ = "routing_targets"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    region: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    modalities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    sensitivity: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cost_per_1k_tokens: Mapped[float] = mapped_column(Double, nullable=False)
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_tenant_fk("routing_targets"),)


class IntegrationConfigurationRecord(Base):
    __tablename__ = "integration_configurations"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    integration_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(500))
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("integration_configurations"),)


class WorkspaceFileRecord(Base, WorkspaceScopedMixin):
    """File metadata. The bytes live in object storage under ``storage_key``."""

    __tablename__ = "workspace_files"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("workspace_files"),
        Index("ix_workspace_files_scope_created", "tenant_id", "workspace_id", "created_at"),
    )


class NotificationPreferenceRecord(Base):
    __tablename__ = "notification_preferences"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    task_completed: Mapped[bool] = mapped_column(nullable=False)
    approval_required: Mapped[bool] = mapped_column(nullable=False)
    run_failed: Mapped[bool] = mapped_column(nullable=False)
    automation_failed: Mapped[bool] = mapped_column(nullable=False)
    email_enabled: Mapped[bool] = mapped_column(nullable=False)
    desktop_enabled: Mapped[bool] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("notification_preferences"),)
