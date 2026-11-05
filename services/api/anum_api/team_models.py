"""Wire and storage models for team work, department assistants and notifications.

docs/team-work.md is the contract. Every model here is scoped to one tenant and one
workspace (``user_notifications`` also to one recipient); nothing crosses workspaces.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .avatar_appearance import AvatarAppearance


class WorkStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


OPEN_STATUSES = frozenset({WorkStatus.TODO, WorkStatus.IN_PROGRESS, WorkStatus.BLOCKED})


class WorkPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


PRIORITY_RANK = {WorkPriority.URGENT: 0, WorkPriority.HIGH: 1, WorkPriority.NORMAL: 2, WorkPriority.LOW: 3}


class AssigneeType(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    # ANUM itself (the general agent): assignee_id is always "anum".
    ANUM = "anum"


class ActorType(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    ANUM = "anum"


class WorkSource(StrEnum):
    MANUAL = "manual"
    COMMAND = "command"
    INBOUND = "inbound"
    FOLLOW_UP = "follow_up"


class Autonomy(StrEnum):
    """How far a department assistant may act on its own (docs/team-work.md#autonomy).

    It only ever narrows tool policy: ``observe`` allows low-risk tools only, ``suggest``
    turns every allowed call into an approval, ``act_low_risk`` lets low-risk calls run
    and leaves everything else to the workspace policy and approvals.
    """

    OBSERVE = "observe"
    SUGGEST = "suggest"
    ACT_LOW_RISK = "act_low_risk"


class Department(StrEnum):
    SALES = "sales"
    SUPPORT = "support"
    OPERATIONS = "operations"
    FINANCE = "finance"
    TYRE_ENGINEERING = "tyre_engineering"
    # Added with the headquarters departments: stored on rooms, agents and team profiles. Only the first five
    # have a built-in department assistant; the others are used to place rooms and agents.
    MARKETING = "marketing"
    TECHNOLOGY = "technology"
    RESEARCH = "research"
    LEGAL = "legal"
    COMPLIANCE = "compliance"
    SECURITY = "security"
    HR = "hr"
    PROCUREMENT = "procurement"
    # The executive office (owner and chief of staff). Stored on rooms; opens the executive desk.
    EXECUTIVE = "executive"
    CUSTOM = "custom"


class EmailStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


class PushStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    NOT_CONFIGURED = "not_configured"
    # Handed to the push dispatcher (FCM); delivery itself is in-process and best effort.
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


ANUM_ASSIGNEE_ID = "anum"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,63}$")


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone must be an IANA time zone such as Asia/Riyadh") from None
    return value


def _require_offset(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("dates need a time zone offset, for example 2026-10-15T17:00:00+03:00")
    return value


def clean_email(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not _EMAIL.match(value):
        raise ValueError("email is not a valid address")
    return value


# --------------------------------------------------------------------------- people


class MemberProfile(BaseModel):
    user_id: str
    display_name: str
    email: str | None = None
    department: Department | None = None
    job_title: str | None = None
    capacity_hours: int | None = None
    timezone: str = "UTC"
    # How the headquarters draws this person: a style the owner or the person chooses, never a claim about them.
    avatar: AvatarAppearance | None = None
    updated_at: datetime | None = None


class MemberProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    department: Department | None = None
    job_title: str | None = Field(default=None, max_length=120)
    capacity_hours: int | None = Field(default=None, ge=0, le=168)
    timezone: str = Field(default="UTC", max_length=64)
    # Omitted keeps the stored style; null clears it (back to the default); an object replaces it.
    avatar: AvatarAppearance | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return clean_email(value)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        return valid_timezone(value)

    @field_validator("display_name", "job_title")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None


class TeamPerson(BaseModel):
    """An active workspace member as the team views show them."""

    user_id: str
    role: str
    display_name: str
    has_profile: bool
    email: str | None = None
    department: Department | None = None
    job_title: str | None = None
    capacity_hours: int | None = None
    timezone: str = "UTC"
    avatar: AvatarAppearance | None = None
    assignable: bool


# --------------------------------------------------------------------------- work items


class ChecklistItem(BaseModel):
    id: str
    label: str
    done: bool = False
    position: int = 0
    done_by: str | None = None
    done_at: datetime | None = None
    created_at: datetime


class WorkComment(BaseModel):
    id: str
    author_type: ActorType
    author_id: str
    author_name: str | None = None
    body: str
    created_at: datetime


class WorkAttachment(BaseModel):
    id: str
    file_id: str
    file_name: str
    added_by: str
    created_at: datetime
    # False when the file was deleted from Files after it was attached.
    available: bool = True


class WorkItem(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    title: str
    description: str = ""
    status: WorkStatus = WorkStatus.TODO
    priority: WorkPriority = WorkPriority.NORMAL
    assignee_type: AssigneeType | None = None
    assignee_id: str | None = None
    created_by: str
    due_at: datetime | None = None
    follow_up_at: datetime | None = None
    completed_at: datetime | None = None
    reminder_sent_at: datetime | None = None
    estimate_hours: float | None = None
    source: WorkSource = WorkSource.MANUAL
    source_detail: dict[str, Any] = Field(default_factory=dict)
    agent_task_id: str | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime


class WorkItemView(WorkItem):
    """A work item with names resolved and counts, as lists and details return it."""

    assignee_name: str | None = None
    created_by_name: str | None = None
    overdue: bool = False
    checklist_total: int = 0
    checklist_done: int = 0
    comment_count: int = 0
    attachment_count: int = 0
    can_edit: bool = False
    can_delete: bool = False


class WorkItemDetail(WorkItemView):
    checklist: list[ChecklistItem] = Field(default_factory=list)
    comments: list[WorkComment] = Field(default_factory=list)
    attachments: list[WorkAttachment] = Field(default_factory=list)


class WorkItemPage(BaseModel):
    items: list[WorkItemView]
    total: int
    limit: int
    offset: int
    next_offset: int | None = None


class Assignee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AssigneeType
    id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def _anum_id(self) -> Assignee:
        if self.type == AssigneeType.ANUM:
            self.id = ANUM_ASSIGNEE_ID
        return self


class WorkItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8000)
    priority: WorkPriority = WorkPriority.NORMAL
    assignee: Assignee | None = None
    due_at: datetime | None = None
    follow_up_at: datetime | None = None
    estimate_hours: float | None = Field(default=None, ge=0, le=1000)
    checklist: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("checklist")
    @classmethod
    def _labels(cls, value: list[str]) -> list[str]:
        labels = [" ".join(label.split()) for label in value]
        if any(not label or len(label) > 300 for label in labels):
            raise ValueError("each checklist item needs 1 to 300 characters")
        return labels

    @field_validator("due_at", "follow_up_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_offset(value)


class WorkItemUpdate(BaseModel):
    """A partial update. ``version`` must equal the stored version (409 otherwise)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    status: WorkStatus | None = None
    priority: WorkPriority | None = None
    assignee: Assignee | None = None
    clear_assignee: bool = False
    due_at: datetime | None = None
    clear_due: bool = False
    follow_up_at: datetime | None = None
    clear_follow_up: bool = False
    estimate_hours: float | None = Field(default=None, ge=0, le=1000)

    @field_validator("due_at", "follow_up_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_offset(value)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("title must not be blank")
        return value


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comment must not be blank")
        return value.strip()


class ChecklistCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=300)


class ChecklistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=300)
    done: bool | None = None


class AttachmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(min_length=3, max_length=80)


class ActivityEntry(BaseModel):
    id: str
    work_item_id: str | None = None
    actor_type: ActorType
    actor_id: str
    actor_name: str | None = None
    action: str
    summary: str
    created_at: datetime


class ActivityPage(BaseModel):
    items: list[ActivityEntry]
    next_cursor: str | None = None


class WorkloadRow(BaseModel):
    assignee_type: AssigneeType
    assignee_id: str
    name: str
    open: int = 0
    in_progress: int = 0
    blocked: int = 0
    overdue: int = 0
    due_this_week: int = 0
    estimate_hours: float = 0.0
    capacity_hours: int | None = None
    # estimate_hours / capacity_hours, rounded; null when no capacity is set.
    load_percent: int | None = None


class Workload(BaseModel):
    rows: list[WorkloadRow]
    unassigned_open: int = 0
    generated_at: datetime


# --------------------------------------------------------------------------- commands


class CommandParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=3, max_length=500)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        return valid_timezone(value) if value else None


class CommandCandidate(BaseModel):
    type: AssigneeType
    id: str
    name: str


class CommandPreview(BaseModel):
    understood: bool
    # Why the sentence could not be turned into an assignment, in the user's words.
    problem: str | None = None
    person: str | None = None
    title: str | None = None
    due_at: datetime | None = None
    due_text: str | None = None
    candidates: list[CommandCandidate] = Field(default_factory=list)
    assignee: CommandCandidate | None = None


# --------------------------------------------------------------------------- notifications


class UserNotification(BaseModel):
    id: str
    user_id: str
    kind: str
    title: str
    body: str
    work_item_id: str | None = None
    read_at: datetime | None = None
    email_status: EmailStatus = EmailStatus.NOT_REQUESTED
    email_attempts: int = 0
    email_error: str | None = None
    push_status: PushStatus = PushStatus.NOT_CONFIGURED
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[UserNotification]
    unread: int
    next_cursor: str | None = None


# --------------------------------------------------------------------------- assistants


AutoRouteChannel = Literal["email", "whatsapp"]


class AssistantProfile(BaseModel):
    id: str
    department: Department
    name: str
    instructions: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    routing_keywords: list[str] = Field(default_factory=list)
    # Channels whose new inbound messages are routed to this assistant on arrival
    # (keyword match required). Empty: only messages routed by hand reach it.
    inbound_channels: list[AutoRouteChannel] = Field(default_factory=list)
    autonomy: Autonomy = Autonomy.SUGGEST
    enabled: bool = True
    created_by: str
    created_at: datetime
    updated_at: datetime


class AssistantWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: Department
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=8000)
    allowed_tools: list[str] = Field(default_factory=list, max_length=40)
    routing_keywords: list[str] = Field(default_factory=list, max_length=40)
    # None keeps the stored value, so clients that predate the field never clear it.
    inbound_channels: list[AutoRouteChannel] | None = Field(default=None, max_length=2)
    autonomy: Autonomy = Autonomy.SUGGEST
    enabled: bool = True

    @field_validator("inbound_channels")
    @classmethod
    def _channels(cls, value: list[AutoRouteChannel] | None) -> list[AutoRouteChannel] | None:
        return None if value is None else sorted(set(value))

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("routing_keywords")
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for keyword in value:
            keyword = " ".join(keyword.lower().split())
            if not keyword or len(keyword) > 60:
                raise ValueError("each routing keyword needs 1 to 60 characters")
            if keyword not in out:
                out.append(keyword)
        return out

    @field_validator("allowed_tools")
    @classmethod
    def _tools(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class AssistantTemplate(BaseModel):
    department: Department
    name: str
    instructions: str
    allowed_tools: list[str]
    routing_keywords: list[str]
    autonomy: Autonomy


class InboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(pattern=r"^(email|whatsapp|marketplace|web|phone|other)$")
    sender: str = Field(min_length=1, max_length=200)
    subject: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    assistant_id: str | None = Field(default=None, max_length=80)


class RouteResult(BaseModel):
    assistant: AssistantProfile | None
    matched_keywords: list[str] = Field(default_factory=list)
    work_item: WorkItemView
