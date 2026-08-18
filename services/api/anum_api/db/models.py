from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
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

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_domain_events_workspace",
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


class FileRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String(80))
    owner_user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    bucket: Mapped[str] = mapped_column(String(160), nullable=False)
    key: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_files_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "task_id"],
            ["tasks.tenant_id", "tasks.workspace_id", "tasks.id"],
            name="fk_files_task",
        ),
        UniqueConstraint("bucket", "key", name="uq_files_bucket_key"),
        Index("ix_files_tenant_workspace", "tenant_id", "workspace_id"),
        Index("ix_files_task", "task_id"),
    )
