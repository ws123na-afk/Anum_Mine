from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantScopedMixin:
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active")

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="tenant")


class Workspace(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="workspaces")
    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),)


class TaskRecord(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    runs: Mapped[list["AgentRunRecord"]] = relationship(back_populates="task")
    approvals: Mapped[list["ApprovalRecord"]] = relationship(back_populates="task")

    __table_args__ = (
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        Index("ix_tasks_tenant_workspace_status", "tenant_id", "workspace_id", "status"),
    )


class AgentRunRecord(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    result: Mapped[str | None] = mapped_column(Text)

    task: Mapped[TaskRecord] = relationship(back_populates="runs")
    steps: Mapped[list["AgentRunStepRecord"]] = relationship(back_populates="run")


class AgentRunStepRecord(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "agent_run_steps"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    step_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    run: Mapped[AgentRunRecord] = relationship(back_populates="steps")


class ApprovalRecord(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[TaskRecord] = relationship(back_populates="approvals")


class DomainEventRecord(Base, TimestampMixin, TenantScopedMixin):
    __tablename__ = "domain_events"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    type: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    workspace_id: Mapped[str | None] = mapped_column(String(80), index=True)
    subject: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_events_tenant_type_created", "tenant_id", "type", "created_at"),)
