"""PostgreSQL store for the internal sales MVP (migrations ``0070_business``,
``0076_business_support`` and ``0080_business_change_orders``).

The session carries the tenant and workspace RLS context (``open_scoped_store``); every
query also filters on the scope explicitly, so a guessed id from another workspace reads
as missing even before the row policy applies.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.business.models import BusinessSettings, SupportPolicy
from anum_api.business.store import KINDS, Query, plain
from anum_api.schemas import TenantContext

from .models import Base
from .scope import require_workspace

TS = DateTime(timezone=True)
MONEY = Numeric(14, 2)


class _Row:
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class _Updated(_Row):
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class BusinessProductRecord(Base, _Updated):
    __tablename__ = "business_products"
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(160))
    name_ar: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text)
    maturity: Mapped[str] = mapped_column(String(20))
    integrations: Mapped[list[Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessOfferRecord(Base, _Updated):
    __tablename__ = "business_offers"
    product_id: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    price_type: Mapped[str] = mapped_column(String(20))
    price_min: Mapped[Decimal | None] = mapped_column(MONEY)
    price_max: Mapped[Decimal | None] = mapped_column(MONEY)
    deliverables: Mapped[list[Any]] = mapped_column(JSONB)
    limitations: Mapped[list[Any]] = mapped_column(JSONB)
    cost: Mapped[dict[str, Any]] = mapped_column(JSONB)
    notes: Mapped[str] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessAccountRecord(Base, _Updated):
    __tablename__ = "business_accounts"
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(253))
    industry: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(80))
    city: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))
    owner_id: Mapped[str] = mapped_column(String(160))
    do_not_contact: Mapped[bool] = mapped_column(Boolean)
    dnc_reason: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessContactRecord(Base, _Updated):
    __tablename__ = "business_contacts"
    account_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    role_title: Mapped[str] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40))
    profile_url: Mapped[str | None] = mapped_column(String(500))
    consent_basis: Mapped[str] = mapped_column(String(40))
    consent_note: Mapped[str] = mapped_column(String(500))
    consent_recorded_at: Mapped[datetime | None] = mapped_column(TS)
    do_not_contact: Mapped[bool] = mapped_column(Boolean)
    dnc_reason: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessLeadRecord(Base, _Updated):
    __tablename__ = "business_leads"
    account_id: Mapped[str] = mapped_column(String(80))
    contact_id: Mapped[str | None] = mapped_column(String(80))
    product_id: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20))
    owner_id: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(20))
    score: Mapped[dict[str, Any]] = mapped_column(JSONB)
    score_total: Mapped[int] = mapped_column(Integer)
    score_known: Mapped[int] = mapped_column(Integer)
    next_step: Mapped[str] = mapped_column(String(300))
    lost_reason: Mapped[str] = mapped_column(String(300))
    suppressed_reason: Mapped[str] = mapped_column(String(300))
    last_verified_at: Mapped[datetime | None] = mapped_column(TS)
    researched_at: Mapped[datetime | None] = mapped_column(TS)
    qualified_at: Mapped[datetime | None] = mapped_column(TS)
    contacted_at: Mapped[datetime | None] = mapped_column(TS)
    replied_at: Mapped[datetime | None] = mapped_column(TS)
    meeting_at: Mapped[datetime | None] = mapped_column(TS)
    offered_at: Mapped[datetime | None] = mapped_column(TS)
    won_at: Mapped[datetime | None] = mapped_column(TS)
    lost_at: Mapped[datetime | None] = mapped_column(TS)
    suppressed_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessEvidenceRecord(Base, _Updated):
    __tablename__ = "business_evidence"
    lead_id: Mapped[str] = mapped_column(String(80))
    account_id: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(Text)
    url_key: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(300))
    published_on: Mapped[date | None] = mapped_column(Date)
    excerpt: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20))
    research_run_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessInferenceRecord(Base, _Updated):
    __tablename__ = "business_inferences"
    lead_id: Mapped[str] = mapped_column(String(80))
    statement: Mapped[str] = mapped_column(Text)
    criterion: Mapped[str | None] = mapped_column(String(40))
    suggested_score: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[str] = mapped_column(String(10))
    basis_evidence_ids: Mapped[list[Any]] = mapped_column(JSONB)
    generator: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    reviewed_by: Mapped[str | None] = mapped_column(String(160))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessActivityRecord(Base, _Row):
    __tablename__ = "business_activities"
    lead_id: Mapped[str | None] = mapped_column(String(80))
    account_id: Mapped[str | None] = mapped_column(String(80))
    opportunity_id: Mapped[str | None] = mapped_column(String(80))
    proposal_id: Mapped[str | None] = mapped_column(String(80))
    project_id: Mapped[str | None] = mapped_column(String(80))
    ticket_id: Mapped[str | None] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(String(500))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    actor: Mapped[str] = mapped_column(String(160))
    idempotency_key: Mapped[str | None] = mapped_column(String(120))


class BusinessOpportunityRecord(Base, _Updated):
    __tablename__ = "business_opportunities"
    lead_id: Mapped[str | None] = mapped_column(String(80))
    account_id: Mapped[str] = mapped_column(String(80))
    product_id: Mapped[str | None] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    value_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    probability_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    assumption_note: Mapped[str] = mapped_column(String(500))
    next_step: Mapped[str] = mapped_column(String(300))
    expected_decision_on: Mapped[date | None] = mapped_column(Date)
    last_interaction_at: Mapped[datetime | None] = mapped_column(TS)
    lost_reason: Mapped[str] = mapped_column(String(300))
    won_at: Mapped[datetime | None] = mapped_column(TS)
    lost_at: Mapped[datetime | None] = mapped_column(TS)
    owner_id: Mapped[str] = mapped_column(String(160))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessProposalRecord(Base, _Updated):
    __tablename__ = "business_proposals"
    opportunity_id: Mapped[str] = mapped_column(String(80))
    account_id: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    current_version: Mapped[int] = mapped_column(Integer)
    accepted_version: Mapped[int | None] = mapped_column(Integer)
    accepted_hash: Mapped[str | None] = mapped_column(String(64))
    accepted_by: Mapped[str | None] = mapped_column(String(160))
    accepted_at: Mapped[datetime | None] = mapped_column(TS)
    requested_hash: Mapped[str | None] = mapped_column(String(64))
    requested_version: Mapped[int | None] = mapped_column(Integer)
    approval_id: Mapped[str | None] = mapped_column(String(80))
    approval_task_id: Mapped[str | None] = mapped_column(String(80))
    decision_note: Mapped[str] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessProposalVersionRecord(Base, _Row):
    __tablename__ = "business_proposal_versions"
    proposal_id: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    summary: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    deliverables: Mapped[list[Any]] = mapped_column(JSONB)
    exclusions: Mapped[list[Any]] = mapped_column(JSONB)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB)
    acceptance_criteria: Mapped[list[Any]] = mapped_column(JSONB)
    line_items: Mapped[list[Any]] = mapped_column(JSONB)
    milestones: Mapped[list[Any]] = mapped_column(JSONB)
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    tax_note: Mapped[str] = mapped_column(String(300))
    internal_cost: Mapped[Decimal] = mapped_column(MONEY)
    payment_terms: Mapped[str] = mapped_column(Text)
    valid_until: Mapped[date | None] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64))
    locked: Mapped[bool] = mapped_column(Boolean)
    change_order_id: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessProjectRecord(Base, _Updated):
    __tablename__ = "business_projects"
    proposal_id: Mapped[str] = mapped_column(String(80))
    proposal_version: Mapped[int] = mapped_column(Integer)
    opportunity_id: Mapped[str] = mapped_column(String(80))
    account_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    contract_total: Mapped[Decimal] = mapped_column(MONEY)
    owner_id: Mapped[str] = mapped_column(String(160))
    start_on: Mapped[date | None] = mapped_column(Date)
    contract_limits: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessMilestoneRecord(Base, _Updated):
    __tablename__ = "business_milestones"
    project_id: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    due_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20))
    acceptance_criteria: Mapped[str] = mapped_column(String(500))
    accepted_by: Mapped[str | None] = mapped_column(String(160))
    accepted_at: Mapped[datetime | None] = mapped_column(TS)
    paid_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    paid_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text)


class BusinessDraftRecord(Base, _Updated):
    __tablename__ = "business_drafts"
    lead_id: Mapped[str] = mapped_column(String(80))
    contact_id: Mapped[str | None] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    language: Mapped[str] = mapped_column(String(2))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    generator: Mapped[str] = mapped_column(String(20))
    untrusted_source: Mapped[bool] = mapped_column(Boolean)
    issues: Mapped[list[Any]] = mapped_column(JSONB)
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(160))
    approved_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessResearchRunRecord(Base, _Updated):
    __tablename__ = "business_research_runs"
    lead_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    queries: Mapped[int] = mapped_column(Integer)
    results: Mapped[int] = mapped_column(Integer)
    evidence_added: Mapped[int] = mapped_column(Integer)
    inferences_added: Mapped[int] = mapped_column(Integer)
    steps: Mapped[list[Any]] = mapped_column(JSONB)
    error: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessSupportTicketRecord(Base, _Updated):
    __tablename__ = "business_support_tickets"
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    account_id: Mapped[str | None] = mapped_column(String(80))
    contact_id: Mapped[str | None] = mapped_column(String(80))
    project_id: Mapped[str | None] = mapped_column(String(80))
    requester_name: Mapped[str] = mapped_column(String(160))
    requester_email: Mapped[str | None] = mapped_column(String(254))
    requester_phone: Mapped[str | None] = mapped_column(String(40))
    requester_verified: Mapped[bool] = mapped_column(Boolean)
    entitlement: Mapped[str] = mapped_column(String(20))
    support_offer_id: Mapped[str | None] = mapped_column(String(80))
    entitlement_note: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    assignee_id: Mapped[str | None] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str | None] = mapped_column(String(80))
    source_account_id: Mapped[str | None] = mapped_column(String(80))
    reported_at: Mapped[datetime] = mapped_column(TS)
    response_due_at: Mapped[datetime | None] = mapped_column(TS)
    resolution_due_at: Mapped[datetime | None] = mapped_column(TS)
    first_responded_at: Mapped[datetime | None] = mapped_column(TS)
    sla_paused_at: Mapped[datetime | None] = mapped_column(TS)
    sla_paused_minutes: Mapped[int] = mapped_column(Integer)
    escalated: Mapped[bool] = mapped_column(Boolean)
    escalation_reason: Mapped[str] = mapped_column(String(300))
    escalated_at: Mapped[datetime | None] = mapped_column(TS)
    escalated_by: Mapped[str | None] = mapped_column(String(160))
    resolution_code: Mapped[str | None] = mapped_column(String(30))
    resolution_summary: Mapped[str] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    resolved_by: Mapped[str | None] = mapped_column(String(160))
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    closed_by: Mapped[str | None] = mapped_column(String(160))
    reopened_count: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessTicketReplyRecord(Base, _Updated):
    __tablename__ = "business_ticket_replies"
    ticket_id: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    generator: Mapped[str] = mapped_column(String(20))
    external_ref: Mapped[str | None] = mapped_column(String(80))
    approval_id: Mapped[str | None] = mapped_column(String(80))
    task_id: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str] = mapped_column(String(500))
    requested_by: Mapped[str | None] = mapped_column(String(160))
    requested_at: Mapped[datetime | None] = mapped_column(TS)
    sent_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessChangeOrderRecord(Base, _Updated):
    __tablename__ = "business_change_orders"
    project_id: Mapped[str] = mapped_column(String(80))
    proposal_id: Mapped[str] = mapped_column(String(80))
    account_id: Mapped[str] = mapped_column(String(80))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    request: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(String(160))
    lines: Mapped[list[Any]] = mapped_column(JSONB)
    timeline_days: Mapped[int] = mapped_column(Integer)
    internal_cost_delta: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    base_version: Mapped[int | None] = mapped_column(Integer)
    base_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    subtotal_delta: Mapped[Decimal | None] = mapped_column(MONEY)
    tax_delta: Mapped[Decimal | None] = mapped_column(MONEY)
    total_delta: Mapped[Decimal | None] = mapped_column(MONEY)
    result_version: Mapped[int | None] = mapped_column(Integer)
    submitted_by: Mapped[str | None] = mapped_column(String(160))
    submitted_at: Mapped[datetime | None] = mapped_column(TS)
    decided_by: Mapped[str | None] = mapped_column(String(160))
    decided_at: Mapped[datetime | None] = mapped_column(TS)
    decision_note: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessUsageEntryRecord(Base, _Updated):
    __tablename__ = "business_usage_ledger"
    account_id: Mapped[str] = mapped_column(String(80))
    project_id: Mapped[str | None] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(300))
    occurred_on: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit_cost: Mapped[Decimal] = mapped_column(MONEY)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    billable: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(20))
    external_ref: Mapped[str | None] = mapped_column(String(160))
    created_by: Mapped[str] = mapped_column(String(160))


class BusinessSettingsRecord(Base):
    __tablename__ = "business_settings"
    tenant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    research_runs_per_day: Mapped[int] = mapped_column(Integer)
    research_searches_per_day: Mapped[int] = mapped_column(Integer)
    support_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_by: Mapped[str | None] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(TS)


RECORDS: dict[str, Any] = {
    "products": BusinessProductRecord,
    "offers": BusinessOfferRecord,
    "accounts": BusinessAccountRecord,
    "contacts": BusinessContactRecord,
    "leads": BusinessLeadRecord,
    "evidence": BusinessEvidenceRecord,
    "inferences": BusinessInferenceRecord,
    "activities": BusinessActivityRecord,
    "opportunities": BusinessOpportunityRecord,
    "proposals": BusinessProposalRecord,
    "proposal_versions": BusinessProposalVersionRecord,
    "projects": BusinessProjectRecord,
    "milestones": BusinessMilestoneRecord,
    "drafts": BusinessDraftRecord,
    "research_runs": BusinessResearchRunRecord,
    "change_orders": BusinessChangeOrderRecord,
    "usage_ledger": BusinessUsageEntryRecord,
    "tickets": BusinessSupportTicketRecord,
    "ticket_replies": BusinessTicketReplyRecord,
}
if set(RECORDS) != set(KINDS):  # pragma: no cover - import-time wiring check
    raise RuntimeError("business record types and kinds differ")


def _attr(name: str) -> str:
    # ``metadata`` is reserved on declarative classes.
    return "metadata_" if name == "metadata" else name


def _fields(record_type: Any) -> list[str]:
    return ["metadata" if column.key == "metadata_" else column.key for column in record_type.__mapper__.column_attrs]


def _to_model(kind: str, record: Any) -> Any:
    return KINDS[kind].model_validate({name: getattr(record, _attr(name)) for name in _fields(type(record))})


def _copy(model: Any, record: Any) -> None:
    data = model.model_dump(mode="json")
    native = model.model_dump()
    for name in _fields(type(record)):
        if name not in native:
            continue
        value = native[name]
        if isinstance(value, (list, dict)) or hasattr(value, "model_dump"):
            setattr(record, _attr(name), data[name])
        else:
            setattr(record, _attr(name), plain(value))


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SqlAlchemyBusinessStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self, record: Any) -> tuple[Any, Any]:
        return (record.tenant_id == self.context.tenant_id, record.workspace_id == self.context.workspace_id)

    def _record(self, kind: str, item_id: str, *, for_update: bool = False) -> Any:
        record = RECORDS[kind]
        statement = select(record).where(*self._scope(record), record.id == item_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get(self, kind: str, item_id: str, *, for_update: bool = False) -> Any:
        found = self._record(kind, item_id, for_update=for_update)
        return _to_model(kind, found) if found is not None else None

    def add(self, kind: str, item: Any) -> Any:
        if (item.tenant_id, item.workspace_id) != (self.context.tenant_id, self.context.workspace_id):
            raise PermissionError("record outside the caller's workspace")
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        record = RECORDS[kind]()
        _copy(item, record)
        self.session.add(record)
        self.session.flush()
        return item

    def save(self, kind: str, item: Any) -> Any:
        record = self._record(kind, item.id, for_update=True)
        if record is None or (item.tenant_id, item.workspace_id) != (self.context.tenant_id, self.context.workspace_id):
            raise KeyError(item.id)
        _copy(item, record)
        self.session.flush()
        return item

    def delete(self, kind: str, item_id: str) -> bool:
        record = self._record(kind, item_id, for_update=True)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def _filtered(self, kind: str, query: Query) -> Any:
        record = RECORDS[kind]
        statement = select(record).where(*self._scope(record))
        for name, wanted in query.where.items():
            column = getattr(record, _attr(name))
            if isinstance(wanted, (set, frozenset, list, tuple)):
                statement = statement.where(column.in_([plain(value) for value in wanted]))
            elif wanted is None:
                statement = statement.where(column.is_(None))
            else:
                statement = statement.where(column == plain(wanted))
        if query.created_from is not None:
            statement = statement.where(record.created_at >= query.created_from)
        if query.created_to is not None:
            statement = statement.where(record.created_at < query.created_to)
        if query.text and query.text_fields:
            pattern = _like(query.text)
            statement = statement.where(or_(*(getattr(record, name).ilike(pattern, escape="\\") for name in query.text_fields)))
        return statement

    def find(self, kind: str, query: Query) -> tuple[list[Any], int]:
        record = RECORDS[kind]
        statement = self._filtered(kind, query)
        total = int(self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        column = getattr(record, query.order_by)
        ordered = statement.order_by(
            (column.desc().nulls_last() if query.descending else column.asc().nulls_first()),
            record.id.desc() if query.descending else record.id.asc(),
        )
        rows = self.session.scalars(ordered.offset(query.offset).limit(query.limit)).all()
        return [_to_model(kind, row) for row in rows], total

    def first(self, kind: str, **where: Any) -> Any:
        items, _ = self.find(kind, Query(where=where, limit=1, order_by="created_at", descending=False))
        return items[0] if items else None

    def count(self, kind: str, query: Query) -> int:
        statement = self._filtered(kind, query)
        return int(self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)

    def get_settings(self) -> BusinessSettings:
        found = self.session.get(BusinessSettingsRecord, (self.context.tenant_id, self.context.workspace_id))
        if found is None:
            return BusinessSettings()
        return BusinessSettings(
            research_runs_per_day=found.research_runs_per_day, research_searches_per_day=found.research_searches_per_day,
            support_policy=SupportPolicy.model_validate(found.support_policy) if found.support_policy else SupportPolicy(),
            updated_by=found.updated_by, updated_at=found.updated_at,
        )

    def save_settings(self, value: BusinessSettings) -> BusinessSettings:
        require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
        found = self.session.get(BusinessSettingsRecord, (self.context.tenant_id, self.context.workspace_id), with_for_update=True)
        if found is None:
            found = BusinessSettingsRecord(tenant_id=self.context.tenant_id, workspace_id=self.context.workspace_id)
            self.session.add(found)
        found.research_runs_per_day = value.research_runs_per_day
        found.research_searches_per_day = value.research_searches_per_day
        found.support_policy = value.support_policy.model_dump(mode="json")
        found.updated_by = value.updated_by
        found.updated_at = value.updated_at or datetime.now().astimezone()
        self.session.flush()
        return value
