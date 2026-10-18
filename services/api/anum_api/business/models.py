"""Records of the internal sales MVP (docs/business.md).

Everything here belongs to one tenant and workspace (the owner's business workspace).
Money is a ``Decimal`` with two places, sent to clients as a JSON number. Values the owner
estimates (an opportunity's value and probability, a version's cost assumptions) are
called assumptions in the API and the UI, and nothing computes them from guesses.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

CENT = Decimal("0.01")


def money(value: Decimal | int | float | str | None) -> Decimal:
    """Two places, half up (the way invoices round)."""
    return Decimal(str(value if value is not None else 0)).quantize(CENT, rounding=ROUND_HALF_UP)


Money = Annotated[
    Decimal,
    Field(ge=0, max_digits=14, decimal_places=2),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
]
Quantity = Annotated[
    Decimal,
    Field(gt=0, max_digits=10, decimal_places=2),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
]
Percent = Annotated[
    Decimal,
    Field(ge=0, le=100, max_digits=5, decimal_places=2),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
]


class Currency(StrEnum):
    SAR = "SAR"
    AED = "AED"
    USD = "USD"


# -- catalog -----------------------------------------------------------------------------


class ProductKind(StrEnum):
    PRODUCT = "product"
    SERVICE_PACKAGE = "service_package"


class Maturity(StrEnum):
    IDEA = "idea"
    PROTOTYPE = "prototype"
    PILOT = "pilot"
    LIVE = "live"
    RETIRED = "retired"


class PriceType(StrEnum):
    FIXED = "fixed"
    FROM = "from"
    RANGE = "range"
    HOURLY = "hourly"
    QUOTE = "quote"


class OfferStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class Scoped(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: str
    tenant_id: str
    workspace_id: str
    created_at: datetime
    updated_at: datetime


class Product(Scoped):
    kind: ProductKind
    name: str = Field(min_length=2, max_length=160)
    name_ar: str = Field(default="", max_length=160)
    summary: str = Field(default="", max_length=2000)
    maturity: Maturity = Maturity.IDEA
    integrations: list[str] = Field(default_factory=list, max_length=30)
    created_by: str


class CostAssumptions(BaseModel):
    """What one delivery costs, as the owner estimates it (roadmap 2.2 pricing formula)."""

    labor_hours: Annotated[Decimal, Field(ge=0, le=10000, max_digits=8, decimal_places=2), PlainSerializer(float, return_type=float, when_used="json")] = Decimal("0")
    hourly_cost: Money = Decimal("0")
    external_costs: Money = Decimal("0")
    contingency_pct: Percent = Decimal("0")

    def total(self) -> Decimal:
        base = money(self.labor_hours * self.hourly_cost) + money(self.external_costs)
        return money(base + base * self.contingency_pct / Decimal(100))


class Offer(Scoped):
    """One priced version of a product or package. Only drafts change; an active
    version is retired when a newer one is activated."""

    product_id: str
    version: int = Field(ge=1)
    status: OfferStatus = OfferStatus.DRAFT
    currency: Currency = Currency.SAR
    price_type: PriceType = PriceType.FIXED
    price_min: Money | None = None
    price_max: Money | None = None
    deliverables: list[str] = Field(default_factory=list, max_length=40)
    limitations: list[str] = Field(default_factory=list, max_length=40)
    cost: CostAssumptions = Field(default_factory=CostAssumptions)
    notes: str = Field(default="", max_length=2000)
    activated_at: datetime | None = None
    created_by: str


# -- accounts, contacts and leads --------------------------------------------------------------


class RecordSource(StrEnum):
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"
    RESEARCH = "research"
    REFERRAL = "referral"
    INBOUND = "inbound"


class ConsentBasis(StrEnum):
    """Why ANUM may contact this person. ``unknown`` stays unknown: no contact."""

    UNKNOWN = "unknown"
    CONSENT_GIVEN = "consent_given"
    INBOUND_INQUIRY = "inbound_inquiry"
    EXISTING_RELATIONSHIP = "existing_relationship"
    REFERRAL = "referral"
    PUBLIC_BUSINESS_CHANNEL = "public_business_channel"


CONTACTABLE_BASES = frozenset(
    {
        ConsentBasis.CONSENT_GIVEN,
        ConsentBasis.INBOUND_INQUIRY,
        ConsentBasis.EXISTING_RELATIONSHIP,
        ConsentBasis.REFERRAL,
        ConsentBasis.PUBLIC_BUSINESS_CHANNEL,
    }
)


class Account(Scoped):
    name: str = Field(min_length=2, max_length=200)
    name_key: str
    website: str | None = Field(default=None, max_length=500)
    domain: str | None = Field(default=None, max_length=253)
    industry: str = Field(default="", max_length=120)
    country: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=4000)
    source: RecordSource = RecordSource.MANUAL
    owner_id: str
    do_not_contact: bool = False
    dnc_reason: str = Field(default="", max_length=300)
    created_by: str


class Contact(Scoped):
    account_id: str
    name: str = Field(min_length=1, max_length=160)
    role_title: str = Field(default="", max_length=160)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    profile_url: str | None = Field(default=None, max_length=500)
    consent_basis: ConsentBasis = ConsentBasis.UNKNOWN
    consent_note: str = Field(default="", max_length=500)
    consent_recorded_at: datetime | None = None
    do_not_contact: bool = False
    dnc_reason: str = Field(default="", max_length=300)
    source: RecordSource = RecordSource.MANUAL
    created_by: str


class LeadState(StrEnum):
    DISCOVERED = "discovered"
    RESEARCHED = "researched"
    QUALIFIED = "qualified"
    CONTACTED = "contacted"
    REPLIED = "replied"
    MEETING = "meeting"
    OFFERED = "offered"
    WON = "won"
    LOST = "lost"
    SUPPRESSED = "suppressed"


OPEN_LEAD_STATES = frozenset(set(LeadState) - {LeadState.WON, LeadState.LOST, LeadState.SUPPRESSED})

# Which state a lead may move to from each state (docs/business.md#lead-states).
LEAD_TRANSITIONS: dict[LeadState, frozenset[LeadState]] = {
    LeadState.DISCOVERED: frozenset({LeadState.RESEARCHED, LeadState.QUALIFIED, LeadState.LOST, LeadState.SUPPRESSED}),
    LeadState.RESEARCHED: frozenset({LeadState.QUALIFIED, LeadState.LOST, LeadState.SUPPRESSED}),
    LeadState.QUALIFIED: frozenset({LeadState.CONTACTED, LeadState.LOST, LeadState.SUPPRESSED}),
    LeadState.CONTACTED: frozenset({LeadState.REPLIED, LeadState.LOST, LeadState.SUPPRESSED}),
    LeadState.REPLIED: frozenset({LeadState.MEETING, LeadState.OFFERED, LeadState.LOST, LeadState.SUPPRESSED}),
    LeadState.MEETING: frozenset({LeadState.OFFERED, LeadState.LOST, LeadState.SUPPRESSED}),
    LeadState.OFFERED: frozenset({LeadState.WON, LeadState.LOST, LeadState.SUPPRESSED}),
    LeadState.WON: frozenset({LeadState.SUPPRESSED}),
    LeadState.LOST: frozenset({LeadState.DISCOVERED, LeadState.SUPPRESSED}),
    LeadState.SUPPRESSED: frozenset({LeadState.DISCOVERED}),
}
# The time stamp each state sets the first time a lead reaches it (the funnel reads these).
STATE_STAMP: dict[LeadState, str] = {
    LeadState.RESEARCHED: "researched_at",
    LeadState.QUALIFIED: "qualified_at",
    LeadState.CONTACTED: "contacted_at",
    LeadState.REPLIED: "replied_at",
    LeadState.MEETING: "meeting_at",
    LeadState.OFFERED: "offered_at",
    LeadState.WON: "won_at",
    LeadState.LOST: "lost_at",
    LeadState.SUPPRESSED: "suppressed_at",
}


class Criterion(StrEnum):
    """The six 0 to 5 criteria of the lead score (roadmap 3.2)."""

    INDUSTRY_FIT = "industry_fit"
    PROBLEM_EVIDENCE = "problem_evidence"
    REACHABLE_CHANNEL = "reachable_channel"
    PROJECT_SCALE = "project_scale"
    URGENCY_CONFIRMED = "urgency_confirmed"
    AUTHORIZED_BUYER = "authorized_buyer"


# Only a conversation can confirm these: research never suggests them.
CONVERSATION_CRITERIA = frozenset({Criterion.URGENCY_CONFIRMED, Criterion.AUTHORIZED_BUYER})


class CriterionScore(BaseModel):
    score: int | None = Field(default=None, ge=0, le=5)
    reason: str = Field(default="", max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    scored_by: str | None = None
    scored_at: datetime | None = None


class Lead(Scoped):
    account_id: str
    contact_id: str | None = None
    product_id: str | None = None
    title: str = Field(min_length=2, max_length=200)
    state: LeadState = LeadState.DISCOVERED
    owner_id: str
    source: RecordSource = RecordSource.MANUAL
    score: dict[str, CriterionScore] = Field(default_factory=dict)
    score_total: int = 0
    score_known: int = 0
    next_step: str = Field(default="", max_length=300)
    lost_reason: str = Field(default="", max_length=300)
    suppressed_reason: str = Field(default="", max_length=300)
    last_verified_at: datetime | None = None
    researched_at: datetime | None = None
    qualified_at: datetime | None = None
    contacted_at: datetime | None = None
    replied_at: datetime | None = None
    meeting_at: datetime | None = None
    offered_at: datetime | None = None
    won_at: datetime | None = None
    lost_at: datetime | None = None
    suppressed_at: datetime | None = None
    created_by: str


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Evidence(Scoped):
    """A public source fact: what a page says, where and when. Never an opinion."""

    lead_id: str
    account_id: str
    url: str = Field(max_length=2048)
    url_key: str
    title: str = Field(default="", max_length=300)
    published_on: date | None = None
    excerpt: str = Field(default="", max_length=1200)
    confidence: Confidence = Confidence.MEDIUM
    source: RecordSource = RecordSource.MANUAL
    research_run_id: str | None = None
    created_by: str


class InferenceStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Inference(Scoped):
    """A conclusion drawn from evidence (by a person or the research skill). Kept apart
    from evidence, with the evidence it rests on; a proposed one is unreviewed."""

    lead_id: str
    statement: str = Field(min_length=3, max_length=1000)
    criterion: Criterion | None = None
    suggested_score: int | None = Field(default=None, ge=0, le=5)
    confidence: Confidence = Confidence.LOW
    basis_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    generator: str = "human"  # human | template | model
    status: InferenceStatus = InferenceStatus.PROPOSED
    reviewed_by: str | None = None
    created_by: str


class Activity(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    lead_id: str | None = None
    account_id: str | None = None
    opportunity_id: str | None = None
    proposal_id: str | None = None
    project_id: str | None = None
    kind: str = Field(max_length=60)
    summary: str = Field(max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor: str
    idempotency_key: str | None = Field(default=None, max_length=120)
    created_at: datetime


# -- opportunities, proposals and projects ------------------------------------------------------


class Stage(StrEnum):
    DISCOVERY = "discovery"
    QUALIFIED = "qualified"
    DEMO = "demo"
    QUOTE = "quote"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


OPEN_STAGES = frozenset({Stage.DISCOVERY, Stage.QUALIFIED, Stage.DEMO, Stage.QUOTE, Stage.NEGOTIATION})
STAGE_ORDER = [Stage.DISCOVERY, Stage.QUALIFIED, Stage.DEMO, Stage.QUOTE, Stage.NEGOTIATION, Stage.WON, Stage.LOST]


class Opportunity(Scoped):
    lead_id: str | None = None
    account_id: str
    product_id: str | None = None
    name: str = Field(min_length=2, max_length=200)
    stage: Stage = Stage.DISCOVERY
    currency: Currency = Currency.SAR
    value_amount: Money | None = None
    probability_pct: Percent | None = None
    assumption_note: str = Field(default="", max_length=500)
    next_step: str = Field(default="", max_length=300)
    expected_decision_on: date | None = None
    last_interaction_at: datetime | None = None
    lost_reason: str = Field(default="", max_length=300)
    won_at: datetime | None = None
    lost_at: datetime | None = None
    owner_id: str
    created_by: str


class LineKind(StrEnum):
    LABOR = "labor"
    SERVICE = "service"
    EXTERNAL_COST = "external_cost"
    OTHER = "other"


class LineItem(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    kind: LineKind = LineKind.SERVICE
    quantity: Quantity = Decimal("1")
    unit_price: Money = Decimal("0")

    def amount(self) -> Decimal:
        return money(self.quantity * self.unit_price)


class MilestonePlan(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    amount: Money = Decimal("0")
    due_in_days: int | None = Field(default=None, ge=0, le=3650)
    acceptance: str = Field(default="", max_length=500)


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ACCEPTED = "accepted"
    WITHDRAWN = "withdrawn"


class ProposalContent(BaseModel):
    """What a proposal version says; its hash binds the acceptance approval."""

    currency: Currency = Currency.SAR
    summary: str = Field(default="", max_length=2000)
    scope: str = Field(default="", max_length=8000)
    deliverables: list[str] = Field(default_factory=list, max_length=40)
    exclusions: list[str] = Field(default_factory=list, max_length=40)
    assumptions: list[str] = Field(default_factory=list, max_length=40)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=40)
    line_items: list[LineItem] = Field(default_factory=list, max_length=60)
    milestones: list[MilestonePlan] = Field(default_factory=list, max_length=20)
    tax_rate_pct: Percent = Decimal("0")
    tax_note: str = Field(default="Tax is tentative until the jurisdiction is verified.", max_length=300)
    internal_cost: Money = Decimal("0")
    payment_terms: str = Field(default="", max_length=1000)
    valid_until: date | None = None


class ProposalVersion(ProposalContent):
    id: str
    tenant_id: str
    workspace_id: str
    proposal_id: str
    version: int = Field(ge=1)
    content_hash: str
    locked: bool = False
    # Set on a version an approved change order produced (migration 0077); the version it
    # amends stays as it was.
    change_order_id: str | None = None
    created_by: str
    created_at: datetime


class Proposal(Scoped):
    opportunity_id: str
    account_id: str
    title: str = Field(min_length=2, max_length=200)
    status: ProposalStatus = ProposalStatus.DRAFT
    current_version: int = 1
    accepted_version: int | None = None
    accepted_hash: str | None = None
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    requested_hash: str | None = None
    requested_version: int | None = None
    approval_id: str | None = None
    approval_task_id: str | None = None
    decision_note: str = Field(default="", max_length=500)
    sent_at: datetime | None = None
    created_by: str


class ProjectStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    DELIVERED = "delivered"
    CLOSED = "closed"


class Project(Scoped):
    proposal_id: str
    proposal_version: int
    opportunity_id: str
    account_id: str
    name: str = Field(min_length=2, max_length=200)
    status: ProjectStatus = ProjectStatus.PLANNED
    currency: Currency = Currency.SAR
    contract_total: Money = Decimal("0")
    owner_id: str
    start_on: date | None = None
    contract_limits: str = Field(default="", max_length=2000)
    created_by: str


class MilestoneStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class Milestone(Scoped):
    project_id: str
    position: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    amount: Money = Decimal("0")
    currency: Currency = Currency.SAR
    due_on: date | None = None
    status: MilestoneStatus = MilestoneStatus.PLANNED
    acceptance_criteria: str = Field(default="", max_length=500)
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    paid_amount: Money | None = None
    paid_on: date | None = None
    notes: str = Field(default="", max_length=1000)


# -- change orders and the usage and cost ledger (roadmap P2, docs/business.md#change-orders) ----


SignedMoney = Annotated[
    Decimal,
    Field(ge=Decimal("-999999999999.99"), le=Decimal("999999999999.99"), max_digits=14, decimal_places=2),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
]


class ChangeOrderStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


OPEN_CHANGE_ORDER_STATES = frozenset({ChangeOrderStatus.DRAFT, ChangeOrderStatus.PENDING_APPROVAL})


class ChangeAction(StrEnum):
    ADD = "add"
    REMOVE = "remove"


class ChangeOrderLine(LineItem):
    """One line of the scope delta: a line added to the contract, or an existing line of
    the version it amends removed (matched exactly on description, kind, quantity and price)."""

    action: ChangeAction = ChangeAction.ADD


class ChangeOrder(Scoped):
    """A change to an accepted proposal and its project. Approving it (an owner, with the
    hash they were shown) creates a new locked proposal version; the amended one stays."""

    project_id: str
    proposal_id: str
    account_id: str
    number: int = Field(ge=1)
    title: str = Field(min_length=2, max_length=200)
    request: str = Field(default="", max_length=2000)
    requested_by: str = Field(default="", max_length=160)
    lines: list[ChangeOrderLine] = Field(default_factory=list, max_length=40)
    timeline_days: int = Field(default=0, ge=-3650, le=3650)
    internal_cost_delta: SignedMoney = Decimal("0")
    status: ChangeOrderStatus = ChangeOrderStatus.DRAFT
    currency: Currency = Currency.SAR
    base_version: int | None = None
    base_hash: str | None = None
    content_hash: str | None = None
    subtotal_delta: SignedMoney | None = None
    tax_delta: SignedMoney | None = None
    total_delta: SignedMoney | None = None
    result_version: int | None = None
    submitted_by: str | None = None
    submitted_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str = Field(default="", max_length=500)
    created_by: str


class UsageKind(StrEnum):
    LABOUR = "labour"
    AI_USAGE = "ai_usage"
    HOSTING = "hosting"
    MESSAGING = "messaging"
    OTHER = "other"


class UsageSource(StrEnum):
    MANUAL = "manual"
    MODEL_BUDGET = "model_budget"


class UsageEntry(Scoped):
    """One cost or labour line for a client account (and optionally one of its projects).
    Labour is hours times an hourly cost; the rest is an amount. Never a payment."""

    account_id: str
    project_id: str | None = None
    kind: UsageKind
    description: str = Field(min_length=2, max_length=300)
    occurred_on: date
    quantity: Annotated[Decimal, Field(gt=0, le=100000, max_digits=10, decimal_places=2), PlainSerializer(float, return_type=float, when_used="json")] = Decimal("1")
    unit_cost: Money = Decimal("0")
    amount: Money = Decimal("0")
    currency: Currency = Currency.SAR
    billable: bool = False
    source: UsageSource = UsageSource.MANUAL
    external_ref: str | None = Field(default=None, max_length=160)
    created_by: str


# -- outbound drafts and research ------------------------------------------------------------------


class DraftChannel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    LINKEDIN = "linkedin"
    CALL_SCRIPT = "call_script"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    DISCARDED = "discarded"


class Draft(Scoped):
    """An outbound message draft. ANUM never sends it (sending is off in P1)."""

    lead_id: str
    contact_id: str | None = None
    channel: DraftChannel
    language: str = Field(pattern=r"^(en|ar)$")
    subject: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    status: DraftStatus = DraftStatus.DRAFT
    generator: str = "template"  # template | model | human
    untrusted_source: bool = False
    issues: list[str] = Field(default_factory=list)
    approved_hash: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_by: str


class ResearchStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


class ResearchStep(BaseModel):
    step: str  # find | profile | score | draft
    status: str  # done | skipped | failed | blocked
    summary: str = Field(max_length=500)


class ResearchRun(Scoped):
    lead_id: str
    status: ResearchStatus
    queries: int = 0
    results: int = 0
    evidence_added: int = 0
    inferences_added: int = 0
    steps: list[ResearchStep] = Field(default_factory=list)
    error: str = Field(default="", max_length=500)
    created_by: str


class BusinessSettings(BaseModel):
    research_runs_per_day: int = Field(default=10, ge=0, le=100)
    research_searches_per_day: int = Field(default=30, ge=0, le=500)
    updated_by: str | None = None
    updated_at: datetime | None = None
