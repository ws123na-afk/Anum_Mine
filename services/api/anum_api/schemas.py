from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class TenantContext(BaseModel):
    tenant_id: str
    workspace_id: str
    user_id: str
    roles: list[str] = Field(default_factory=list)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=8000)


class Task(BaseModel):
    id: str
    title: str
    prompt: str
    status: TaskStatus
    tenant_id: str
    workspace_id: str
    created_at: datetime
    updated_at: datetime


class AgentRunStep(BaseModel):
    id: str
    type: str
    summary: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRun(BaseModel):
    id: str
    task_id: str
    status: TaskStatus
    steps: list[AgentRunStep] = Field(default_factory=list)
    result: str | None = None
    created_at: datetime
    updated_at: datetime


class Approval(BaseModel):
    id: str
    task_id: str
    action: str
    risk_level: RiskLevel
    status: ApprovalStatus
    reason: str
    created_at: datetime
    decided_at: datetime | None = None


class DomainEvent(BaseModel):
    id: str
    type: str
    version: int = 1
    tenant_id: str
    workspace_id: str | None = None
    subject: str
    correlation_id: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class RunTaskResponse(BaseModel):
    task: Task
    run: AgentRun
    approval: Approval | None = None


class ApprovalDecisionResponse(BaseModel):
    approval: Approval
    task: Task
    run: AgentRun | None = None
