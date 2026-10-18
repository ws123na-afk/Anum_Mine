"""Records of the prospecting pipeline and the outreach outbox (docs/business.md#prospecting-pipeline).

The Harvey patterns (docs/business/HARVEY_PARITY.md) written as ANUM records, one tenant and
workspace each (migration ``0075_business_pipeline``):

* :class:`OutreachSettings`: one row per workspace: which signals the owner confirmed,
  the daily page-fetch cap, sending (off by default), per-channel daily quotas, the bounce
  threshold and the pause (kill switch) with its reason.
* :class:`Observation`: one signal seen on one public page: the value, the page URL, the
  excerpt it rests on and the day it was read. A fact, never an opinion.
* :class:`PipelineRun`: a discovery or profile run: its plan, the estimate a person
  confirmed, its progress and what it actually used.
* :class:`OutboxItem`: one outbound message tied to a lead and an approved draft, waiting for
  an approval bound to its payload hash, held, sent, cancelled or failed.
* :class:`ReplyEvent`: an inbound reply, opt-out or bounce for a lead, which stops its
  follow-ups (stop-on-reply) and feeds the bounce kill switch.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .models import Scoped


class SignalStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class SignalDecision(BaseModel):
    status: SignalStatus = SignalStatus.PROPOSED
    decided_by: str | None = None
    decided_at: datetime | None = None


SENDABLE_CHANNELS = ("email", "whatsapp")


class OutreachSettings(Scoped):
    """Per workspace. Sending is off until an owner turns it on; every send still waits for an approval."""

    signals: dict[str, SignalDecision] = Field(default_factory=dict)
    fetches_per_day: int = Field(default=100, ge=0, le=2000)
    pages_per_lead: int = Field(default=5, ge=1, le=8)
    sending_enabled: bool = False
    daily_quota: dict[str, int] = Field(default_factory=lambda: {"email": 20, "whatsapp": 20})
    max_bounce_rate_pct: float = Field(default=5.0, ge=0, le=50)
    follow_up_max: int = Field(default=2, ge=0, le=5)
    follow_up_min_days: int = Field(default=3, ge=1, le=60)
    paused: bool = False
    paused_reason: str = Field(default="", max_length=300)
    paused_at: datetime | None = None
    updated_by: str | None = None

    def confirmed(self) -> set[str]:
        return {code for code, decision in self.signals.items() if decision.status == SignalStatus.CONFIRMED}


class TargetProfile(Scoped):
    """Who one product is for (its ideal customer profile), so discovery, fit and drafts are per product.

    GCC first: regions default to the six GCC countries. Disqualifiers drop a discovered company
    whose result text names one; keywords raise the fit when the company's own pages use them.
    """

    product_id: str
    industries: list[str] = Field(default_factory=list, max_length=20)
    company_sizes: list[str] = Field(default_factory=list, max_length=6)  # micro | small | medium | large | enterprise
    regions: list[str] = Field(default_factory=lambda: ["Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain", "Oman"], max_length=20)
    roles: list[str] = Field(default_factory=list, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    disqualifiers: list[str] = Field(default_factory=list, max_length=30)
    value_proposition: str = Field(default="", max_length=1000)
    value_proposition_ar: str = Field(default="", max_length=1000)
    updated_by: str | None = None


class Observation(Scoped):
    """A signal seen on a public page (a fact): value, URL, excerpt and the day it was read."""

    lead_id: str
    account_id: str
    run_id: str | None = None
    evidence_id: str | None = None
    signal_code: str = Field(max_length=60)
    value_num: float | None = None
    value_text: str = Field(default="", max_length=300)
    url: str = Field(max_length=2048)
    excerpt: str = Field(default="", max_length=600)
    observed_on: date
    confidence: float = Field(default=1.0, ge=0, le=1)
    created_by: str


class RunKind(StrEnum):
    DISCOVER = "discover"
    PROFILE = "profile"


class RunStatus(StrEnum):
    ESTIMATED = "estimated"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    STOPPED = "stopped"
    FAILED = "failed"


class RunStep(BaseModel):
    at: datetime
    step: str = Field(max_length=60)
    status: str = Field(max_length=20)  # done | skipped | blocked | failed | stopped
    summary: str = Field(max_length=500)
    target: str | None = Field(default=None, max_length=200)


class Estimate(BaseModel):
    """What a run will use at most, and whether it fits today's caps and the model budget."""

    searches: int = 0
    page_fetches: int = 0
    model_calls: int = 0
    estimated_tokens: int = 0
    estimated_cost_usd: float | None = 0.0
    searches_left_today: int = 0
    fetches_left_today: int = 0
    budget_cost_left_usd: float | None = None
    budget_tokens_left: int | None = None
    fits: bool = True
    problems: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Usage(BaseModel):
    searches: int = 0
    page_fetches: int = 0
    model_calls: int = 0


class PipelineRun(Scoped):
    kind: RunKind
    status: RunStatus = RunStatus.ESTIMATED
    product_id: str | None = None
    plan: dict[str, Any] = Field(default_factory=dict)
    estimate: Estimate = Field(default_factory=Estimate)
    used: Usage = Field(default_factory=Usage)
    steps: list[RunStep] = Field(default_factory=list)
    results: dict[str, Any] = Field(default_factory=dict)
    stop_requested: bool = False
    stop_reason: str = Field(default="", max_length=300)
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None
    created_by: str


class OutboxStatus(StrEnum):
    SCHEDULED = "scheduled"  # a follow-up waiting for its day; no approval asked yet
    PENDING_APPROVAL = "pending_approval"
    HELD = "held"  # approved, but sending is off, paused, over quota or failed a send-time check
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


OPEN_OUTBOX = frozenset({OutboxStatus.SCHEDULED, OutboxStatus.PENDING_APPROVAL, OutboxStatus.HELD})


class OutboxItem(Scoped):
    lead_id: str
    contact_id: str
    draft_id: str
    channel: str = Field(pattern=r"^(email|whatsapp)$")
    step: int = Field(default=1, ge=1, le=6)
    follow_up_of: str | None = None
    recipient: str = Field(max_length=254)
    subject: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    content_hash: str
    status: OutboxStatus = OutboxStatus.PENDING_APPROVAL
    gate_issues: list[str] = Field(default_factory=list)
    approval_id: str | None = None
    approval_task_id: str | None = None
    send_after: datetime | None = None
    sent_at: datetime | None = None
    provider_ref: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=500)
    created_by: str


class ReplyKind(StrEnum):
    REPLY = "reply"
    OPT_OUT = "opt_out"
    BOUNCE = "bounce"
    ESCALATE = "escalate"


class ReplyEvent(Scoped):
    lead_id: str
    contact_id: str | None = None
    outbox_id: str | None = None
    channel: str = Field(max_length=20)
    kind: ReplyKind
    recipient_hash: str | None = Field(default=None, max_length=64)
    cancelled: int = 0
    source: str = Field(default="manual", max_length=20)  # manual | email_inbox | whatsapp
    note: str = Field(default="", max_length=300)
    created_by: str
