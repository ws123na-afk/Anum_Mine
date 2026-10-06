import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunPhase(StrEnum):
    PLANNING = "planning"
    TOOL_READY = "tool_ready"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING = "executing"
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


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class Tenant(BaseModel):
    id: str
    name: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class Workspace(BaseModel):
    id: str
    tenant_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class WorkspaceMembership(BaseModel):
    tenant_id: str
    workspace_id: str
    user_id: str
    role: str
    active: bool = True
    created_at: datetime
    updated_at: datetime


class CallerMembership(BaseModel):
    """One of the caller's own active memberships in their tenant (``GET /me/workspace-memberships``).

    ``workspace_name`` is the workspace's display name, or null if it cannot be read.
    ``status`` is always ``active``: deactivated memberships are not listed.
    """

    tenant_id: str
    workspace_id: str
    workspace_name: str | None = None
    role: str
    status: str = "active"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"  # derived for views; never stored


class WorkspaceInvitation(BaseModel):
    """A single-use invitation into one workspace. Only the token's SHA-256 hash is kept."""

    id: str
    tenant_id: str
    workspace_id: str
    role: str
    invitee_user_id: str | None = None
    invitee_email: str | None = None
    token_hash: str
    status: InvitationStatus = InvitationStatus.PENDING
    created_by_user_id: str
    expires_at: datetime
    accepted_by_user_id: str | None = None
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


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
    # User id of whoever created the task (the two-person rule refuses their approval).
    created_by: str | None = None


class AgentRunStep(BaseModel):
    id: str
    type: str
    summary: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunCheckpoint(BaseModel):
    phase: RunPhase = RunPhase.PLANNING
    version: int = Field(default=1, ge=1)
    selected_skills: list[str] = Field(default_factory=list)
    tool_call: dict[str, Any] | None = None
    approval_id: str | None = None
    last_step_id: str | None = None


class AgentRun(BaseModel):
    id: str
    task_id: str
    status: TaskStatus
    steps: list[AgentRunStep] = Field(default_factory=list)
    result: str | None = None
    checkpoint: RunCheckpoint = Field(default_factory=RunCheckpoint)
    created_at: datetime
    updated_at: datetime


class Approval(BaseModel):
    """A paused high-risk tool call waiting for a person (docs/approvals-and-risk.md).

    ``action`` is the exact tool name and ``arguments`` the exact arguments the agent
    will execute, with secret-looking values redacted for display. ``payload_hash`` is
    the SHA-256 of the canonical, unredacted tool call bound to its task, run and
    proposal step; approving requires sending it back, and the runtime executes only
    if the checkpointed call still hashes to it. Pending approvals lapse at
    ``expires_at``. ``decided_by`` is the user id of whoever approved or rejected and
    ``decision_reason`` the optional reason they gave. ``requested_by`` is the user whose
    run proposed the call; ``target`` is the configured integration host the tool will
    contact (host only), or null for internal tools.
    """

    id: str
    task_id: str
    action: str
    risk_level: RiskLevel
    status: ApprovalStatus
    reason: str
    created_at: datetime
    decided_at: datetime | None = None
    run_id: str | None = None
    step_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str | None = None
    expires_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    requested_by: str | None = None
    target: str | None = None
    # Approval chains (0013_approvers_and_directory): how many distinct approvals the
    # matching organization rules require (1 without a chain) and who has approved so far,
    # oldest first. Filled by the approval routes; not stored on the approval row.
    required_approvals: int = 1
    approvers: list["ApprovalApprover"] = Field(default_factory=list)


class ApprovalApprover(BaseModel):
    """One recorded approve decision on an approval (one per distinct user)."""

    user_id: str
    approved_at: datetime
    reason: str | None = None


class ApprovalApproverRecord(BaseModel):
    """Stored form of an approver row, scoped to its tenant and workspace."""

    tenant_id: str
    workspace_id: str
    approval_id: str
    user_id: str
    payload_hash: str
    reason: str | None = None
    approved_at: datetime

    def view(self) -> ApprovalApprover:
        return ApprovalApprover(user_id=self.user_id, approved_at=self.approved_at, reason=self.reason)


Approval.model_rebuild()


DECISION_REASON_MAX_CHARS = 500
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u2028\u2029]")


def _normalize_reason(value: str | None) -> str | None:
    """Trim the optional decision reason; blank is no reason. Control characters
    (other than tab and newline) are refused so a reason cannot forge log lines."""
    if value is None:
        return None
    value = value.replace("\r\n", "\n")
    if _CONTROL_CHARACTERS.search(value):
        raise ValueError("reason must not contain control characters")
    value = value.strip()
    return value or None


class ApprovalDecisionRequest(BaseModel):
    """The approval's ``payload_hash`` exactly as the client displayed it, and an
    optional reason for the decision (docs/approvals-and-risk.md, A4)."""

    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=DECISION_REASON_MAX_CHARS)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        return _normalize_reason(value)


class ApprovalRejectRequest(BaseModel):
    """Rejecting may send the displayed hash (it must then match) and a reason."""

    payload_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = Field(default=None, max_length=DECISION_REASON_MAX_CHARS)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        return _normalize_reason(value)


class WorkspaceApprovalPolicy(BaseModel):
    """Per-workspace approval policy (docs/approvals-and-risk.md, A5 and A6).

    ``two_person_rule``: a high-risk approval cannot be approved by the user who
    created the task or started the run; another owner must approve it.
    ``medium_risk_requires_approval``: medium-risk tools pause for approval too.
    Both default to off. Only owners may change them; every change is audited.
    """

    two_person_rule: bool = False
    medium_risk_requires_approval: bool = False
    updated_by: str | None = None
    updated_at: datetime | None = None


class WorkspaceApprovalPolicyUpdate(BaseModel):
    two_person_rule: bool
    medium_risk_requires_approval: bool


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
