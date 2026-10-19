from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
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
    # Migration 0029: an archived workspace refuses writes until an owner restores it.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

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
    # The agent the task runs as (migration 0041_agent_profiles); no foreign key: a
    # deleted agent's tasks stay in the workspace history.
    agent_id: Mapped[str | None] = mapped_column(String(80))
    # Archive (0026_task_archive, docs/tasks.md): null while the task is in the active list.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_user_id: Mapped[str | None] = mapped_column(String(120))
    # Migration 0039_team_work: the department assistant the task runs as, if any.
    assistant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Planning (0048_task_planning, docs/tasks.md): null priority reads as medium.
    priority: Mapped[str | None] = mapped_column(String(10))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
        # Keyset paging of GET /api/v1/tasks (migration 0017_tasks_scope_created).
        Index(
            "ix_tasks_scope_created",
            "tenant_id",
            "workspace_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_tasks_scope_agent",
            "tenant_id",
            "workspace_id",
            "agent_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("agent_id is not null"),
        ),
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
        # Keyset paging of GET /api/v1/tasks/{id}/runs (migration 0026_task_archive).
        Index(
            "ix_agent_runs_task_created",
            "tenant_id",
            "workspace_id",
            "task_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
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
    # Approval integrity (0010_approval_integrity, docs/approvals-and-risk.md).
    run_id: Mapped[str | None] = mapped_column(String(80))
    step_id: Mapped[str | None] = mapped_column(String(80))
    arguments: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(160))
    # Decision reason, requester and integration target (0012_approval_policy).
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    requested_by: Mapped[str | None] = mapped_column(String(160))
    target: Mapped[str | None] = mapped_column(String(255))

    task: Mapped[TaskRecord] = relationship(back_populates="approvals")

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "task_id"],
            ["tasks.tenant_id", "tasks.workspace_id", "tasks.id"],
            name="fk_approvals_task",
        ),
        CheckConstraint(
            "payload_hash is null or payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_approvals_payload_hash",
        ),
        Index("ix_approvals_tenant_workspace_status", "tenant_id", "workspace_id", "status"),
        # Target of the approval_approvers scope-bound foreign key (0013).
        UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_approvals_scope_id"),
    )


class ApprovalApproverRow(Base):
    """One approve decision of an approval chain (0013_approvers_and_directory).

    One row per distinct approver; the approval turns ``approved`` when the number of
    rows reaches the requirement of the matching organization approval rules.
    """

    __tablename__ = "approval_approvers"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    approval_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "approval_id"],
            ["approvals.tenant_id", "approvals.workspace_id", "approvals.id"],
            name="fk_approval_approvers_approval",
        ),
        CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'", name="ck_approval_approvers_payload_hash"
        ),
    )


class WorkspaceApprovalPolicyRecord(Base):
    """Per-workspace approval policy (0012_approval_policy, docs/approvals-and-risk.md)."""

    __tablename__ = "workspace_approval_policies"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    two_person_rule: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    medium_risk_requires_approval: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_approval_policies_workspace",
        ),
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
    # Private to this user when set (facts from their conversations, migration 0021).
    owner_user_id: Mapped[str | None] = mapped_column(String(160))
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
    # Model routing (migration 0024_model_config_routes); null means "not set".
    fast_model: Mapped[str | None] = mapped_column(String(160))
    capable_model: Mapped[str | None] = mapped_column(String(160))
    fast_effort: Mapped[str | None] = mapped_column(String(10))
    capable_effort: Mapped[str | None] = mapped_column(String(10))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_workspace_model_configs_workspace",
        ),
        CheckConstraint(
            "provider in ('mock', 'openai_compatible', 'ollama', 'anthropic')",
            name="ck_workspace_model_configs_provider",
        ),
        CheckConstraint(
            "provider not in ('openai_compatible', 'anthropic') or api_key_ciphertext is not null",
            name="ck_workspace_model_configs_credential",
        ),
        CheckConstraint(
            "(fast_effort is null or fast_effort in ('low', 'medium', 'high', 'xhigh', 'max')) "
            "and (capable_effort is null or capable_effort in ('low', 'medium', 'high', 'xhigh', 'max'))",
            name="ck_workspace_model_configs_effort",
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
        # Migration 0029: the audit log's actor and action filters, newest first.
        Index("ix_audit_records_scope_actor", "tenant_id", "workspace_id", "actor", "created_at"),
        Index("ix_audit_records_scope_action", "tenant_id", "workspace_id", "action", "created_at"),
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
    # Ed25519 signature over the canonical package digest (0083, docs/skills.md#signing).
    signature_key_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

    __table_args__ = (
        _tenant_fk("skill_versions"),
        UniqueConstraint("tenant_id", "skill_id", "version", name="uq_skill_versions_tenant_skill_version"),
        UniqueConstraint("tenant_id", "id", name="uq_skill_versions_tenant_id"),
    )


class SkillPublisherKeyRecord(Base):
    """A tenant's trusted skill publisher key: public key only (0083, docs/skills.md#signing)."""

    __tablename__ = "skill_publisher_keys"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    publisher: Mapped[str] = mapped_column(String(160), nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (_tenant_fk("skill_publisher_keys"),)


class SkillSigningSettingsRecord(Base):
    """The tenant's skill signing settings (0083, docs/skills.md#signing)."""

    __tablename__ = "skill_signing_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    disable_flagged: Mapped[bool] = mapped_column(nullable=False, default=True)
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_tenant_fk("skill_signing_settings"),)


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
    # Why the API turned the installation off (0083: a revoked signing key).
    disabled_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

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
    # Approval routing (0054_routing_suppression): the AED band and conditions, and the
    # approver steps ([{"approvers": [...]}, ...]).
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    approver_steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

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
    # Integration package signature (0083, docs/governance-and-scale.md#package-signing).
    signature_key_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

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
    # Why the API turned the install off (0083: a revoked signing key).
    disabled_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

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


class IntegrationHealthCheckRecord(Base):
    """The latest health check of one integration in one workspace, and its latest failure."""

    __tablename__ = "integration_health_checks"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    integration_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    checked_by: Mapped[str] = mapped_column(String(160), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (_workspace_fk("integration_health_checks"),)


class IntegrationCredentialRecord(Base):
    """A workspace's own credential for an integration; Fernet ciphertext only (``secret_box``)."""

    __tablename__ = "integration_credentials"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    integration_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    credential_hint: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("integration_credentials"),)


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
    # Last rename (migration 0029_file_management); null for a file never renamed.
    updated_by: Mapped[str | None] = mapped_column(String(160))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Malware scan (migration 0075_file_scan_status): null until scanned; `infected` is
    # quarantined (docs/files.md#malware-scanning).
    scan_status: Mapped[str | None] = mapped_column(String(16))
    scan_signature: Mapped[str | None] = mapped_column(String(200))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The current version number (migration 0089_file_versions); older versions live in
    # workspace_file_versions.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)

    __table_args__ = (
        _workspace_fk("workspace_files"),
        CheckConstraint("scan_status is null or scan_status in ('clean', 'infected')", name="ck_workspace_files_scan_status"),
        Index("ix_workspace_files_scope_created", "tenant_id", "workspace_id", "created_at"),
        # Keyset paging by name and by size (migration 0029_file_management).
        Index("ix_workspace_files_scope_name", "tenant_id", "workspace_id", text('(lower(name) collate "C")'), "id"),
        Index("ix_workspace_files_scope_size", "tenant_id", "workspace_id", "size_bytes", "id"),
        # Target of the scoped foreign key from workspace_file_versions (migration 0089_file_versions).
        Index("uq_workspace_files_scope_id", "tenant_id", "workspace_id", "id", unique=True),
    )


class WorkspaceFileVersionRecord(Base, WorkspaceScopedMixin):
    """An earlier version of a workspace file (migration 0089_file_versions, docs/files.md#versions).

    The bytes stay in object storage under ``storage_key``; a key can be shared with the current
    version or another old version of the same file when the content is identical (a restore).
    """

    __tablename__ = "workspace_file_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replaced_by: Mapped[str] = mapped_column(String(160), nullable=False)
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scan_status: Mapped[str | None] = mapped_column(String(16))
    scan_signature: Mapped[str | None] = mapped_column(String(200))
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _workspace_fk("workspace_file_versions"),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "file_id"],
            ["workspace_files.tenant_id", "workspace_files.workspace_id", "workspace_files.id"],
            name="fk_workspace_file_versions_file",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "file_id", "version", name="uq_workspace_file_versions_number"),
        CheckConstraint("size_bytes > 0 and version > 0", name="ck_workspace_file_versions_values"),
        CheckConstraint(
            "scan_status is null or scan_status in ('clean', 'infected')", name="ck_workspace_file_versions_scan_status"
        ),
        Index("ix_workspace_file_versions_sha", "tenant_id", "workspace_id", "file_id", "sha256"),
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
    # Migration 0027_push_devices; the server default keeps older releases' inserts valid.
    push_enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    # Migration 0038_commerce; the server default keeps older releases' inserts valid.
    commerce_alerts: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    tyre_alerts: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("notification_preferences"),)


class PushDeviceRecord(Base):
    """A phone registered for push by one user in one workspace (migration ``0027_push_devices``).

    Private to the user: the RLS policy checks ``anum.user_id`` as well as the tenant and
    workspace. The token is stored only encrypted (``token_ciphertext``).
    """

    __tablename__ = "push_devices"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str | None] = mapped_column(String(80))
    token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    # Migration 0072_push_device_language: the app's language for the push text (null is English).
    language: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _workspace_fk("push_devices"),
        UniqueConstraint("tenant_id", "workspace_id", "user_id", "token_sha256", name="uq_push_devices_user_token"),
        CheckConstraint("language is null or language in ('en', 'ar')", name="ck_push_devices_language"),
        Index("ix_push_devices_scope_user", "tenant_id", "workspace_id", "user_id", "created_at"),
    )


class UserPreferenceRecord(Base):
    """One user's preferences in one tenant (migration ``0025_user_preferences``).

    Private to the user: the RLS policy checks ``anum.user_id`` as well as the tenant.
    """

    __tablename__ = "user_preferences"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    remember_conversations: Mapped[bool] = mapped_column(nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_tenant_fk("user_preferences"),)


# Voice and automation (migration 0009). Voice rows are private to one user: their RLS
# policies check ``anum.user_id`` as well as the tenant and workspace.


class VoiceSessionRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "voice_sessions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    locale: Mapped[str] = mapped_column(String(35), nullable=False)
    retention: Mapped[str] = mapped_column(String(20), nullable=False)
    assistant_name: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    ask_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _workspace_fk("voice_sessions"),
        UniqueConstraint("tenant_id", "workspace_id", "user_id", "id", name="uq_voice_sessions_scope_id"),
    )


class VoiceTranscriptSegmentRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "voice_transcript_segments"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    session_id: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_final: Mapped[bool] = mapped_column(nullable=False)
    client_sequence: Mapped[int] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "user_id", "session_id"],
            [
                "voice_sessions.tenant_id",
                "voice_sessions.workspace_id",
                "voice_sessions.user_id",
                "voice_sessions.id",
            ],
            name="fk_voice_transcript_segments_session",
            ondelete="CASCADE",
        ),
    )


class AutomationWorkflowRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "automation_workflows"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    # Migration 0034: allow | skip | cancel_previous, and the last editor.
    overlap_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="allow", server_default=text("'allow'")
    )
    updated_by: Mapped[str | None] = mapped_column(String(160))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("automation_workflows"),
        UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_automation_workflows_scope_id"),
    )


def _workflow_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "workflow_id"],
        [
            "automation_workflows.tenant_id",
            "automation_workflows.workspace_id",
            "automation_workflows.id",
        ],
        name=f"fk_{table}_workflow",
    )


class AutomationScheduleRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "automation_schedules"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Migration 0034: what the scheduler did at the last fire time.
    last_outcome: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("automation_schedules"), _workflow_fk("automation_schedules"))


class AutomationRunRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "automation_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(80), nullable=False)
    schedule_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    retry_of: Mapped[str | None] = mapped_column(String(80))
    current_step: Mapped[int] = mapped_column(nullable=False, default=0)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # Migration 0034: the workflow version the run executed (null for older runs).
    workflow_version: Mapped[int | None] = mapped_column()
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Migration 0049: the trigger that started the run and what it delivered; when the
    # engine looks at the run again; the agent task the current step waits on.
    trigger_id: Mapped[str | None] = mapped_column(String(80))
    trigger_type: Mapped[str | None] = mapped_column(String(160))
    trigger_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_task_id: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        _workspace_fk("automation_runs"),
        _workflow_fk("automation_runs"),
        Index("ix_automation_runs_workflow", "tenant_id", "workspace_id", "workflow_id", "created_at"),
        Index("ix_automation_runs_wake", "wake_at", postgresql_where=text("wake_at is not null")),
        Index(
            "ix_automation_runs_waiting_task",
            "tenant_id",
            "workspace_id",
            "waiting_task_id",
            postgresql_where=text("waiting_task_id is not null"),
        ),
        Index("ix_automation_runs_trigger", "tenant_id", "workspace_id", "trigger_id", "created_at"),
    )


class AutomationTriggerRecord(Base, WorkspaceScopedMixin):
    """An event or webhook trigger of a workflow (migration 0049)."""

    __tablename__ = "automation_triggers"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(160))
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
    rate_limit_per_minute: Mapped[int] = mapped_column(nullable=False, default=30, server_default=text("30"))
    replay_window_seconds: Mapped[int] = mapped_column(nullable=False, default=300, server_default=text("300"))
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    secret_hint: Mapped[str | None] = mapped_column(String(8))
    secret_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outcome: Mapped[str | None] = mapped_column(String(40))
    fire_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("automation_triggers"),
        _workflow_fk("automation_triggers"),
        CheckConstraint("kind in ('event', 'webhook')", name="ck_automation_triggers_kind"),
        CheckConstraint(
            "(kind = 'event' and event_type is not null) or (kind = 'webhook' and secret_ciphertext is not null)",
            name="ck_automation_triggers_shape",
        ),
        CheckConstraint("rate_limit_per_minute between 1 and 600", name="ck_automation_triggers_rate"),
        CheckConstraint("replay_window_seconds between 30 and 3600", name="ck_automation_triggers_replay"),
        Index("ix_automation_triggers_event", "tenant_id", "workspace_id", "event_type", "enabled"),
        Index("ix_automation_triggers_workflow", "tenant_id", "workspace_id", "workflow_id"),
    )


class ConversationRecord(Base, WorkspaceScopedMixin):
    """An assistant conversation, private to its user (migration 0018)."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    # A thread with an agent (migration 0041_agent_profiles); None for the Assistant.
    agent_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Pinned to the top of the list (migration 0051_chat_depth); null when not pinned.
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        _workspace_fk("conversations"),
        UniqueConstraint("tenant_id", "workspace_id", "user_id", "id", name="uq_conversations_scope_id"),
    )


class ConversationTurnRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "conversation_turns"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The card of a question run as a task (migration 0047_chat_cards); null for plain chat.
    card: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # The user's thumbs on an answer (migration 0051_chat_depth): 'up', 'down' or null.
    feedback: Mapped[str | None] = mapped_column(String(8), nullable=True)
    feedback_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Insertion order: turns are read back in the order they were written.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "user_id", "conversation_id"],
            [
                "conversations.tenant_id",
                "conversations.workspace_id",
                "conversations.user_id",
                "conversations.id",
            ],
            name="fk_conversation_turns_conversation",
            ondelete="CASCADE",
        ),
    )


class AutonomyGuardrailsRecord(Base):
    """The organization's autonomy floors (migration ``0034_autonomy``, docs/autonomy.md)."""

    __tablename__ = "autonomy_guardrails"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_tenant_fk("autonomy_guardrails"),)


class WorkspaceAutonomyRecord(Base):
    """One workspace's autonomy settings (migration ``0034_autonomy``)."""

    __tablename__ = "workspace_autonomy_settings"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("workspace_autonomy_settings"),)


class AutonomyRuleRecord(Base):
    __tablename__ = "autonomy_rules"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    pattern_key: Mapped[str] = mapped_column(String(320), nullable=False)
    tool: Mapped[str] = mapped_column(String(160), nullable=False)
    template: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    action_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("autonomy_rules"),
        UniqueConstraint("tenant_id", "workspace_id", "pattern_key", name="uq_autonomy_rules_pattern"),
    )


class AutonomyProposalRecord(Base):
    __tablename__ = "autonomy_proposals"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    pattern_key: Mapped[str] = mapped_column(String(320), nullable=False)
    tool: Mapped[str] = mapped_column(String(160), nullable=False)
    template: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    action_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    proposed_level: Mapped[str] = mapped_column(String(20), nullable=False)
    approvals: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    last_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(160))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _workspace_fk("autonomy_proposals"),
        UniqueConstraint("tenant_id", "workspace_id", "pattern_key", name="uq_autonomy_proposals_pattern"),
    )


class AutonomousActionRecord(Base):
    __tablename__ = "autonomy_actions"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), nullable=False)
    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    tool: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    action_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    pattern_key: Mapped[str] = mapped_column(String(320), nullable=False)
    template: Mapped[str | None] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(160), nullable=False)
    undo_tool: Mapped[str | None] = mapped_column(String(160))
    undo_arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    undo_token: Mapped[str | None] = mapped_column(String(80))
    undo_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_by: Mapped[str | None] = mapped_column(String(160))
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undo_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("autonomy_actions"),
        Index("ix_autonomy_actions_feed", "tenant_id", "workspace_id", "created_at", "id"),
    )


class AutonomyRecipientRecord(Base):
    """A recipient already contacted in a category, stored only as a SHA-256 digest."""

    __tablename__ = "autonomy_recipients"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    category: Mapped[str] = mapped_column(String(20), primary_key=True)
    recipient_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("autonomy_recipients"),)


# --------------------------------------------------------------------------- learning
# What ANUM learned (migration ``0036_learning``, docs/learning.md). Every row is private
# to one user in one workspace: forced RLS checks the tenant, workspace and user.


class LearningProfileRecord(Base):
    __tablename__ = "learning_profiles"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    derived: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    derived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("learning_profiles"),)


class LearningSignalRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "learning_signals"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    surface: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(120))
    skill: Mapped[str | None] = mapped_column(String(120))
    tool: Mapped[str | None] = mapped_column(String(120))
    contact: Mapped[str | None] = mapped_column(String(160))
    language: Mapped[str | None] = mapped_column(String(10))
    original_text: Mapped[str | None] = mapped_column(Text)
    final_text: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(String(500))
    signal_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("learning_signals"),)


class LearningPreferenceRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "learning_preferences"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    scope: Mapped[str] = mapped_column(String(200), nullable=False, default="all")
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    suggestion_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("learning_preferences"),)


class LearningRoutineRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "learning_routines"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    cadence: Mapped[str] = mapped_column(String(10), nullable=False)
    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    steps: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    occurrences: Mapped[int] = mapped_column(nullable=False, default=0)
    weeks_observed: Mapped[int] = mapped_column(nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Double, nullable=False, default=0.0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    workflow_id: Mapped[str | None] = mapped_column(String(80))
    schedule_id: Mapped[str | None] = mapped_column(String(80))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _workspace_fk("learning_routines"),
        UniqueConstraint("tenant_id", "workspace_id", "user_id", "fingerprint", name="uq_learning_routines_fingerprint"),
    )


class LearningNoteRecord(Base):
    __tablename__ = "learning_notes"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    week: Mapped[str] = mapped_column(String(10), primary_key=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("learning_notes"),)


class LearningSuggestionRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "learning_suggestions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    week: Mapped[str] = mapped_column(String(10), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(200), nullable=False)
    rule: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200))
    change: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _workspace_fk("learning_suggestions"),
        UniqueConstraint("tenant_id", "workspace_id", "user_id", "fingerprint", name="uq_learning_suggestions_fingerprint"),
    )


# Calls & Meetings (migration 0026_meetings, docs/meetings.md). Meetings and their
# transcript lines belong to the workspace (tenant and workspace RLS); connector accounts
# hold one user's encrypted OAuth tokens (tenant, workspace *and* user RLS).


class MeetingRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    requested_language: Mapped[str] = mapped_column(String(8), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column()
    agent_skill_id: Mapped[str | None] = mapped_column(String(160))
    recording_file_id: Mapped[str | None] = mapped_column(String(80))
    source_file_name: Mapped[str | None] = mapped_column(String(255))
    external_id: Mapped[str | None] = mapped_column(String(512))
    segment_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    decisions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    action_items: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    follow_up: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("meetings"),
        UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_meetings_scope_id"),
        Index("ix_meetings_scope_created", "tenant_id", "workspace_id", "created_at"),
        Index(
            "uq_meetings_external",
            "tenant_id",
            "workspace_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id is not null"),
        ),
    )


class MeetingTranscriptSegmentRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "meeting_transcript_segments"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(String(80), nullable=False)
    seq: Mapped[int] = mapped_column(nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(120))
    start_ms: Mapped[int | None] = mapped_column(BigInteger)
    end_ms: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "meeting_id"],
            ["meetings.tenant_id", "meetings.workspace_id", "meetings.id"],
            name="fk_meeting_transcript_segments_meeting",
            ondelete="CASCADE",
        ),
        Index("ix_meeting_segments_meeting_seq", "tenant_id", "workspace_id", "meeting_id", "seq"),
    )


class MeetingConnectorAccountRecord(Base):
    __tablename__ = "meeting_connector_accounts"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    account_label: Mapped[str] = mapped_column(String(200), nullable=False)
    external_user_id: Mapped[str | None] = mapped_column(String(200))
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("meeting_connector_accounts"),)


# Favorites, knowledge base and reports (migration 0042_library). Favorites are private to
# one user (RLS checks anum.user_id); collections, their items and reports are shared with
# the workspace when owner_user_id is null and private to that user otherwise (a
# restrictive owner policy, like memories).


class FavoriteRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "favorites"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)
    item_id: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("favorites"),
        UniqueConstraint("tenant_id", "workspace_id", "user_id", "item_type", "item_id", name="uq_favorites_item"),
    )


class KnowledgeCollectionRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "kb_collections"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    owner_user_id: Mapped[str | None] = mapped_column(String(160))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("kb_collections"),
        UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_kb_collections_scope_id"),
    )


class KnowledgeItemRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "kb_items"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    collection_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "collection_id"],
            ["kb_collections.tenant_id", "kb_collections.workspace_id", "kb_collections.id"],
            name="fk_kb_items_collection",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "collection_id", "kind", "source_id", name="uq_kb_items_source"),
    )


class ReportRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    report_type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metrics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    highlights: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_user_id: Mapped[str | None] = mapped_column(String(160))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_by_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_conversation_id: Mapped[str | None] = mapped_column(String(80))
    source_turn_id: Mapped[str | None] = mapped_column(String(80))
    source_automation_run_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Typed sections and their tabs (migration 0045_reports_calendar, docs/reports.md).
    tabs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (_workspace_fk("reports"),)
# Team work and organisation tables are Core tables on the same metadata (migration
# 0039_team_work); importing them here registers them with Base.metadata.
from . import team_repository as _team_tables  # noqa: E402,F401


class AgentProfileRecord(Base, WorkspaceScopedMixin):
    """A user-built agent (migration 0041_agent_profiles, docs/agents.md).

    Forced RLS: the tenant and workspace, and either ``sharing = 'workspace'`` or the
    owner's ``anum.user_id``, so a private agent is invisible to everyone else.
    """

    __tablename__ = "agent_profiles"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    icon: Mapped[str] = mapped_column(String(40), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_route: Mapped[str] = mapped_column(String(20), nullable=False)
    allowed_skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    allowed_tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    autonomy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    knowledge_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    knowledge_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    sharing: Mapped[str] = mapped_column(String(20), nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("agent_profiles"),
        UniqueConstraint("tenant_id", "workspace_id", "id", name="uq_agent_profiles_scope_id"),
        CheckConstraint("category in ('work', 'personal', 'custom')", name="ck_agent_profiles_category"),
        CheckConstraint("status in ('active', 'paused')", name="ck_agent_profiles_status"),
        CheckConstraint("sharing in ('private', 'workspace')", name="ck_agent_profiles_sharing"),
        CheckConstraint("model_route in ('fast', 'capable')", name="ck_agent_profiles_model_route"),
        CheckConstraint(
            "knowledge_mode in ('none', 'workspace', 'selected')", name="ck_agent_profiles_knowledge_mode"
        ),
        Index("ix_agent_profiles_scope_updated", "tenant_id", "workspace_id", text("updated_at DESC")),
    )


class AgentMemoryRecord(Base, WorkspaceScopedMixin):
    """A memory that belongs to one agent (migration 0041_agent_profiles).

    Visible only where its agent is (the RLS policy checks the agent row, itself under RLS).
    """

    __tablename__ = "agent_memories"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "agent_id"],
            ["agent_profiles.tenant_id", "agent_profiles.workspace_id", "agent_profiles.id"],
            name="fk_agent_memories_agent",
            ondelete="CASCADE",
        ),
        CheckConstraint("source in ('manual', 'task')", name="ck_agent_memories_source"),
        Index("ix_agent_memories_agent", "tenant_id", "workspace_id", "agent_id", text("created_at DESC")),
    )


class SetupPlanItemRecord(Base):
    """What a setup plan applied, per blueprint item (migration ``0052_setup_plans``).

    ``workspace_id`` is the workspace for workspace-level items and ``''`` for tenant-level
    ones (policy packs, approval rules, role templates, memory governance, published skill
    versions, created workspaces); the RLS policy admits the request's workspace and ``''``.
    """

    __tablename__ = "setup_plan_items"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    item_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(160))
    target_version: Mapped[int | None] = mapped_column(Integer)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _tenant_fk("setup_plan_items"),
        CheckConstraint("state in ('claimed', 'applied')", name="ck_setup_plan_items_state"),
        CheckConstraint("origin in ('created', 'linked')", name="ck_setup_plan_items_origin"),
    )


class SetupPlanCheckRecord(Base):
    """A Week 1 pilot check a person recorded (migration ``0052_setup_plans``); tenant-level."""

    __tablename__ = "setup_plan_checks"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    check_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    evidence: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _tenant_fk("setup_plan_checks"),
        CheckConstraint("status in ('pass', 'fail', 'not_run')", name="ck_setup_plan_checks_status"),
    )


class ContactSuppressionRecord(Base):
    """One entry of a tenant's contact suppression list (migration ``0054_routing_suppression``).

    Tenant-level: forced RLS on ``anum.tenant_id``. The contact is stored only as a keyed
    hash and a masked form (docs/privacy.md#contact-suppression).
    """

    __tablename__ = "contact_suppressions"

    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recipient_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    masked: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _tenant_fk("contact_suppressions"),
        UniqueConstraint("tenant_id", "recipient_hash", "channel", name="uq_contact_suppressions_contact"),
        CheckConstraint("kind in ('phone', 'email')", name="ck_contact_suppressions_kind"),
        CheckConstraint("channel in ('email', 'whatsapp', 'sms', 'all')", name="ck_contact_suppressions_channel"),
        CheckConstraint("purpose in ('marketing', 'all')", name="ck_contact_suppressions_purpose"),
    )


class AppCallRecord(Base):
    """One in-app call between two teammates (migration ``0073_app_calls``).

    Visible to its two participants only: a restrictive policy checks ``anum.user_id``
    against the caller and the callee, and only the caller inserts the row.
    """

    __tablename__ = "app_calls"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    caller_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    callee_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    caller_name: Mapped[str] = mapped_column(String(120), nullable=False)
    callee_name: Mapped[str] = mapped_column(String(120), nullable=False)
    caller_client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    callee_client_id: Mapped[str | None] = mapped_column(String(64))
    media: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(16))
    ended_by_user_id: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        _workspace_fk("app_calls"),
        Index("ix_app_calls_scope_caller", "tenant_id", "workspace_id", "caller_user_id", "created_at"),
        Index("ix_app_calls_scope_callee", "tenant_id", "workspace_id", "callee_user_id", "created_at"),
    )


class AppCallSignalRecord(Base):
    """An offer, answer or ICE batch for one device of a call (migration ``0073_app_calls``)."""

    __tablename__ = "app_call_signals"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    call_id: Mapped[str] = mapped_column(String(80), nullable=False)
    sender_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    recipient_user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    recipient_client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        _workspace_fk("app_call_signals"),
        ForeignKeyConstraint(["call_id"], ["app_calls.id"], name="fk_app_call_signals_call", ondelete="CASCADE"),
        Index("ix_app_call_signals_recipient", "tenant_id", "workspace_id", "recipient_client_id", "call_id", "created_at"),
    )
