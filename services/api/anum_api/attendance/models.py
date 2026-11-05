"""Attendance records (docs/attendance.md#records). Every record is one person's own.

Exact coordinates are never stored anywhere: an arrival or departure names the site that
matched on the phone (or the button pressed on the web), and a location fix only adds a
quality flag (``ok`` or ``poor``).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

DEFAULT_ZONE = "Asia/Dubai"


class Owned(BaseModel):
    tenant_id: str
    workspace_id: str
    owner_user_id: str


class ShiftModel(BaseModel):
    weekday: int = Field(ge=0, le=6)  # Monday 0 to Sunday 6
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class Profile(Owned):
    organisation: str | None = None
    department: str | None = None
    role_title: str | None = None
    workplace_site_id: str | None = None
    time_zone: str = DEFAULT_ZONE
    week_starts_on: int = 0
    schedule: list[ShiftModel] = Field(default_factory=list)
    daily_overtime_minutes: int | None = None
    weekly_overtime_minutes: int | None = None
    location_consent_at: datetime | None = None
    tracking_paused: bool = False
    paused_at: datetime | None = None
    ai_drafts_enabled: bool = False
    missed_check_alerts: bool = True
    last_alert_day: date | None = None
    version: int = 0
    updated_at: datetime | None = None


class SiteKind(StrEnum):
    WORKPLACE = "workplace"
    SITE = "site"
    CLIENT = "client"
    HOME = "home"


class Site(Owned):
    id: str
    name: str
    kind: SiteKind = SiteKind.SITE
    address_label: str | None = None
    radius_m: int = 150
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class EventKind(StrEnum):
    ARRIVE = "arrive"
    LEAVE = "leave"
    BREAK_START = "break_start"
    BREAK_END = "break_end"
    REMOTE_START = "remote_start"
    REMOTE_END = "remote_end"


class EventSource(StrEnum):
    WEB = "web"
    DEVICE = "device"
    MANUAL = "manual"


class EventStatus(StrEnum):
    ACTIVE = "active"
    VOIDED = "voided"
    SUPERSEDED = "superseded"


class AttendanceEvent(Owned):
    id: str
    kind: EventKind
    occurred_at: datetime
    received_at: datetime
    time_zone: str = DEFAULT_ZONE
    site_id: str | None = None
    source: EventSource = EventSource.WEB
    quality: str = "ok"
    device_id: str | None = None
    idem_key: str
    status: EventStatus = EventStatus.ACTIVE
    note: str | None = None


class CorrectionAction(StrEnum):
    ADD = "add"
    CHANGE = "change"
    VOID = "void"


class Correction(Owned):
    id: str
    action: CorrectionAction
    event_id: str | None = None
    replacement_event_id: str | None = None
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None
    reason: str
    created_at: datetime


class DayTotal(Owned):
    """A day reduced from its raw events once they are past the retention (90 days by default)."""

    day: date
    time_zone: str
    presence_seconds: int = 0
    remote_seconds: int = 0
    break_seconds: int = 0
    worked_seconds: int = 0
    verified_seconds: int = 0
    estimated_seconds: int = 0
    scheduled_seconds: int = 0
    overtime_seconds: int | None = None
    first_arrival: datetime | None = None
    last_departure: datetime | None = None
    site_ids: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    event_count: int = 0
    created_at: datetime


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class SiteVisit(BaseModel):
    site_id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class DailyReport(Owned):
    id: str
    day: date
    project: str | None = None
    tasks_done: list[str] = Field(default_factory=list)
    tasks_pending: list[str] = Field(default_factory=list)
    site_visits: list[SiteVisit] = Field(default_factory=list)
    issues: str | None = None
    time_spent_minutes: int | None = None
    priority: Priority = Priority.NORMAL
    follow_up_on: date | None = None
    notes: str | None = None
    file_ids: list[str] = Field(default_factory=list)
    drafted_with_ai: bool = False
    version: int = 1
    created_at: datetime
    updated_at: datetime
    # Set once when the person submits the report (docs/attendance.md#records); until then it is a draft.
    submitted_at: datetime | None = None


class Share(BaseModel):
    """The owner lets one other member of the workspace read their attendance (and reports)."""

    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    viewer_user_id: str
    include_reports: bool = False
    created_at: datetime
    revoked_at: datetime | None = None
