"""HTTP routes for Business, part 2: opportunities, proposals, projects, milestones and the
owner's KPI summary (``/api/v1/business``, docs/business.md).

A proposal version is locked once it is sent for acceptance; changing it then creates a new
version and lapses the waiting approval. Acceptance is ``business.accept_proposal`` in the
ordinary approvals flow: an owner approves it with the payload hash they were shown, and
the tool refuses if the proposal changed since. An accepted proposal never changes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query as Q, Response, status
from pydantic import BaseModel, Field

from ..repository import AnumRepository
from ..schemas import Approval, ApprovalStatus, TenantContext, new_id, utc_now
from ..dependencies import repository_context, tenant_context
from .common import Page, Reason, get_or_404, page_bounds, read, write
from .models import (
    OPEN_LEAD_STATES,
    OPEN_STAGES,
    STAGE_ORDER,
    Currency,
    LeadState,
    LineItem,
    LineKind,
    Milestone,
    MilestoneStatus,
    Money,
    OfferStatus,
    Opportunity,
    Percent,
    PriceType,
    Project,
    ProjectStatus,
    Proposal,
    ProposalContent,
    ProposalStatus,
    ProposalVersion,
    Stage,
    SignedMoney,
    money,
)
from .invoice_models import DecisionSubject, PortalDecisionSummary
from .portal_service import decision_summary
from .service import audit, content_hash, http_error, proposal_totals, record_activity, stamp_state
from .store import MAX_SCAN, BusinessStore, Query, open_business_store
from .tools import acceptance_arguments, request_approval, retire_approval

router = APIRouter(prefix="/api/v1/business", tags=["business"])


# -- opportunities ------------------------------------------------------------------------------


class OpportunityCreate(BaseModel):
    lead_id: str
    name: str | None = Field(default=None, min_length=2, max_length=200)
    currency: Currency = Currency.SAR
    value_amount: Money | None = None
    probability_pct: Percent | None = None
    assumption_note: str = Field(default="", max_length=500)
    next_step: str = Field(default="", max_length=300)
    expected_decision_on: date | None = None


class OpportunityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    currency: Currency | None = None
    value_amount: Money | None = None
    probability_pct: Percent | None = None
    assumption_note: str | None = Field(default=None, max_length=500)
    next_step: str | None = Field(default=None, max_length=300)
    expected_decision_on: date | None = None


class StageChange(BaseModel):
    to: Stage
    reason: str = Field(default="", max_length=300)


class OpportunityView(Opportunity):
    account_name: str = ""
    lead_title: str | None = None
    product_name: str | None = None
    weighted_value: Money | None = None
    proposals: int = 0


def opportunity_view(store: BusinessStore, item: Opportunity) -> OpportunityView:
    account = store.get("accounts", item.account_id)
    lead = store.get("leads", item.lead_id) if item.lead_id else None
    product = store.get("products", item.product_id) if item.product_id else None
    weighted = money(item.value_amount * item.probability_pct / Decimal(100)) if item.value_amount is not None and item.probability_pct is not None else None
    return OpportunityView(**item.model_dump(), account_name=account.name if account else "", lead_title=lead.title if lead else None,
                           product_name=product.name if product else None, weighted_value=weighted,
                           proposals=store.count("proposals", Query(where={"opportunity_id": item.id})))


@router.get("/opportunities", response_model=Page[OpportunityView])
def list_opportunities(
    stage: list[Stage] = Q(default=[]), q: str | None = Q(default=None, max_length=100), limit: int = 50, offset: int = 0,
    context: TenantContext = Depends(tenant_context),
) -> Page[OpportunityView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {"stage": set(stage)} if stage else {}
    with open_business_store(context) as store:
        items, total = store.find("opportunities", Query(where=where, text=q, text_fields=("name", "next_step"), limit=limit, offset=offset))
        views = [opportunity_view(store, item) for item in items]
    return Page[OpportunityView](items=views, total=total, limit=limit, offset=offset)


@router.post("/opportunities", response_model=OpportunityView, status_code=status.HTTP_201_CREATED)
def create_opportunity(payload: OpportunityCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> OpportunityView:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        lead = get_or_404(store, "leads", payload.lead_id, "Lead", for_update=True)
        if lead.state not in OPEN_LEAD_STATES or lead.state in {LeadState.DISCOVERED, LeadState.RESEARCHED}:
            raise http_error(409, "Qualify the lead before opening an opportunity (Leads, then Qualify).")
        existing = store.find("opportunities", Query(where={"lead_id": lead.id, "stage": OPEN_STAGES}, limit=1))[0]
        if existing:
            raise http_error(409, "This lead already has an open opportunity.")
        item = Opportunity(
            id=new_id("bopp"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, lead_id=lead.id, account_id=lead.account_id,
            product_id=lead.product_id, name=payload.name or lead.title, stage=Stage.QUALIFIED, currency=payload.currency,
            value_amount=payload.value_amount, probability_pct=payload.probability_pct, assumption_note=payload.assumption_note.strip(),
            next_step=payload.next_step.strip(), expected_decision_on=payload.expected_decision_on, last_interaction_at=now,
            owner_id=context.user_id, created_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("opportunities", item)
        record_activity(store, context, "opportunity_created", f"Opportunity \"{item.name}\" opened.", lead_id=lead.id, account_id=lead.account_id, opportunity_id=item.id)
        view = opportunity_view(store, item)
    audit(repository, context, "business.opportunity.created", item.id, metadata={"lead_id": lead.id})
    return view


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityView)
def get_opportunity(opportunity_id: str, context: TenantContext = Depends(tenant_context)) -> OpportunityView:
    read(context)
    with open_business_store(context) as store:
        return opportunity_view(store, get_or_404(store, "opportunities", opportunity_id, "Opportunity"))


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityView)
def update_opportunity(opportunity_id: str, payload: OpportunityUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> OpportunityView:
    write(context)
    changes = payload.model_dump(exclude_unset=True)
    with open_business_store(context) as store:
        item = get_or_404(store, "opportunities", opportunity_id, "Opportunity", for_update=True)
        if item.stage == Stage.WON:
            raise http_error(409, "A won opportunity keeps the accepted proposal's value.")
        clean = {key: (value.strip() if isinstance(value, str) and key != "name" else value) for key, value in changes.items() if not (key in {"name", "currency"} and value is None)}
        item = item.model_copy(update={**clean, "updated_at": utc_now()})
        store.save("opportunities", item)
        view = opportunity_view(store, item)
    audit(repository, context, "business.opportunity.updated", opportunity_id, metadata={"fields": sorted(changes)})
    return view


@router.post("/opportunities/{opportunity_id}/stage", response_model=OpportunityView)
def move_opportunity(opportunity_id: str, payload: StageChange, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> OpportunityView:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        item = get_or_404(store, "opportunities", opportunity_id, "Opportunity", for_update=True)
        before = item.stage
        if before == Stage.WON:
            raise http_error(409, "A won opportunity is final: its proposal was accepted.")
        if payload.to == Stage.WON:
            raise http_error(409, "An opportunity is won when its proposal is accepted (Proposals, then Approvals).")
        if payload.to == before:
            raise http_error(409, f"The opportunity is already at {before.value}.")
        reason = payload.reason.strip()
        if payload.to == Stage.LOST:
            if len(reason) < 3:
                raise http_error(422, "Give a reason of at least 3 characters.")
            item.lost_reason, item.lost_at = reason, now
            for proposal in store.find("proposals", Query(where={"opportunity_id": item.id, "status": {ProposalStatus.DRAFT, ProposalStatus.PENDING_APPROVAL}}, limit=50))[0]:
                retire_approval(repository, context, proposal.approval_task_id, proposal.approval_id)
                store.save("proposals", proposal.model_copy(update={"status": ProposalStatus.WITHDRAWN, "decision_note": "The opportunity was lost.", "updated_at": now}))
            if item.lead_id:
                lead = store.get("leads", item.lead_id, for_update=True)
                if lead is not None and lead.state in OPEN_LEAD_STATES:
                    stamp_state(lead, LeadState.LOST, now)
                    lead.lost_reason, lead.updated_at = reason, now
                    store.save("leads", lead)
        elif before == Stage.LOST:
            item.lost_reason, item.lost_at = "", None
        item.stage = payload.to
        item.last_interaction_at = now
        item.updated_at = now
        store.save("opportunities", item)
        record_activity(store, context, "stage_changed", f"{before.value.capitalize()} to {payload.to.value}" + (f": {reason}" if reason else "."),
                        opportunity_id=item.id, lead_id=item.lead_id, account_id=item.account_id, metadata={"from": before.value, "to": payload.to.value})
        view = opportunity_view(store, item)
    audit(repository, context, "business.opportunity.stage", opportunity_id, metadata={"from": before.value, "to": payload.to.value})
    return view


# -- proposals ---------------------------------------------------------------------------------


ApprovalState = Literal["none", "pending", "approved", "rejected", "expired"]


class VersionView(ProposalVersion):
    subtotal: Money
    external_costs: Money
    tax: Money
    total: Money
    milestone_total: Money
    margin: Decimal
    margin_pct: Decimal | None = None
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VersionSummary(BaseModel):
    version: int
    content_hash: str
    locked: bool
    change_order_id: str | None = None
    total: Money
    created_at: datetime


class ProposalView(Proposal):
    account_name: str = ""
    opportunity_name: str = ""
    current: VersionView
    versions: list[VersionSummary] = Field(default_factory=list)
    approval_state: ApprovalState = "none"
    approval: Approval | None = None
    project_id: str | None = None
    # What the customer decided in their portal about the version waiting for acceptance.
    portal_decision: PortalDecisionSummary | None = None


class ProposalCreate(BaseModel):
    opportunity_id: str
    title: str = Field(min_length=2, max_length=200)
    content: ProposalContent | None = None


def version_view(version: ProposalVersion) -> VersionView:
    totals = proposal_totals(version)
    return VersionView(**version.model_dump(), **totals)


def _versions(store: BusinessStore, proposal_id: str) -> list[ProposalVersion]:
    items, _ = store.find("proposal_versions", Query(where={"proposal_id": proposal_id}, order_by="version", descending=True, limit=200))
    return items


def proposal_view(store: BusinessStore, repository: AnumRepository, context: TenantContext, proposal: Proposal) -> ProposalView:
    """With its approval; a rejected or lapsed request returns the proposal to draft here."""
    approval = repository.get_approval(proposal.approval_id, context) if proposal.approval_id else None
    state: ApprovalState = "none"
    if approval is not None:
        state = {ApprovalStatus.PENDING: "pending", ApprovalStatus.APPROVED: "approved", ApprovalStatus.REJECTED: "rejected", ApprovalStatus.EXPIRED: "expired"}.get(approval.status, "none")  # type: ignore[assignment]
        if approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at <= utc_now():
            state = "expired"
    if proposal.status == ProposalStatus.PENDING_APPROVAL and state in {"rejected", "expired"} and approval is not None:
        note = (f"Rejected by {approval.decided_by or 'an approver'}" + (f": {approval.decision_reason}" if approval.decision_reason else "."))\
            if state == "rejected" else "The approval request lapsed before anyone decided."
        proposal = proposal.model_copy(update={"status": ProposalStatus.DRAFT, "decision_note": note[:500], "updated_at": utc_now()})
        store.save("proposals", proposal)
    versions = _versions(store, proposal.id)
    current = next(item for item in versions if item.version == proposal.current_version)
    account = store.get("accounts", proposal.account_id)
    opportunity = store.get("opportunities", proposal.opportunity_id)
    project = store.first("projects", proposal_id=proposal.id)
    return ProposalView(
        **proposal.model_dump(), account_name=account.name if account else "", opportunity_name=opportunity.name if opportunity else "",
        current=version_view(current),
        versions=[VersionSummary(version=item.version, content_hash=item.content_hash, locked=item.locked, change_order_id=item.change_order_id, total=proposal_totals(item)["total"], created_at=item.created_at) for item in versions],
        approval_state=state, approval=approval if state == "pending" else None, project_id=project.id if project else None,
        portal_decision=decision_summary(store, DecisionSubject.PROPOSAL, proposal.id, proposal.requested_hash or proposal.accepted_hash),
    )


def _new_version(context: TenantContext, proposal_id: str, number: int, content: ProposalContent) -> ProposalVersion:
    content = ProposalContent.model_validate(content.model_dump())
    content.deliverables = [" ".join(item.split()) for item in content.deliverables if item.strip()]
    content.exclusions = [" ".join(item.split()) for item in content.exclusions if item.strip()]
    content.assumptions = [" ".join(item.split()) for item in content.assumptions if item.strip()]
    content.acceptance_criteria = [" ".join(item.split()) for item in content.acceptance_criteria if item.strip()]
    return ProposalVersion(
        **content.model_dump(), id=new_id("bpver"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, proposal_id=proposal_id,
        version=number, content_hash=content_hash(content), created_by=context.user_id, created_at=utc_now(),
    )


def starting_content(store: BusinessStore, opportunity: Opportunity) -> ProposalContent:
    """A first version from the catalog's active offer (the owner's own prices), when there is one."""
    if not opportunity.product_id:
        return ProposalContent(currency=opportunity.currency)
    product = store.get("products", opportunity.product_id)
    offers, _ = store.find("offers", Query(where={"product_id": opportunity.product_id, "status": OfferStatus.ACTIVE}, limit=1))
    if product is None or not offers:
        return ProposalContent(currency=opportunity.currency)
    offer = offers[0]
    lines = []
    if offer.price_min is not None and offer.price_type in {PriceType.FIXED, PriceType.FROM, PriceType.RANGE}:
        lines.append(LineItem(description=f"{product.name} (catalog version {offer.version})", kind=LineKind.SERVICE, unit_price=offer.price_min))
    return ProposalContent(
        currency=offer.currency, summary=product.summary, deliverables=list(offer.deliverables), exclusions=list(offer.limitations), line_items=lines,
        internal_cost=offer.cost.total(),
    )


@router.get("/proposals", response_model=Page[ProposalView])
def list_proposals(
    status_: list[ProposalStatus] = Q(default=[], alias="status"), q: str | None = Q(default=None, max_length=100), opportunity_id: str | None = None,
    limit: int = 50, offset: int = 0, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> Page[ProposalView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {}
    if status_:
        where["status"] = set(status_)
    if opportunity_id:
        where["opportunity_id"] = opportunity_id
    with open_business_store(context) as store:
        items, total = store.find("proposals", Query(where=where, text=q, text_fields=("title",), limit=limit, offset=offset))
        views = [proposal_view(store, repository, context, item) for item in items]
    return Page[ProposalView](items=views, total=total, limit=limit, offset=offset)


@router.post("/proposals", response_model=ProposalView, status_code=status.HTTP_201_CREATED)
def create_proposal(payload: ProposalCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ProposalView:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        opportunity = get_or_404(store, "opportunities", payload.opportunity_id, "Opportunity", for_update=True)
        if opportunity.stage not in OPEN_STAGES:
            raise http_error(409, f"The opportunity is {opportunity.stage.value}; proposals are for open opportunities.")
        if store.find("proposals", Query(where={"opportunity_id": opportunity.id, "status": {ProposalStatus.DRAFT, ProposalStatus.PENDING_APPROVAL}}, limit=1))[0]:
            raise http_error(409, "This opportunity already has an open proposal. Change that one (it keeps its versions).")
        proposal = Proposal(id=new_id("bprop"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, opportunity_id=opportunity.id,
                            account_id=opportunity.account_id, title=" ".join(payload.title.split()), created_by=context.user_id, created_at=now, updated_at=now)
        store.add("proposals", proposal)
        store.add("proposal_versions", _new_version(context, proposal.id, 1, payload.content or starting_content(store, opportunity)))
        if STAGE_ORDER.index(opportunity.stage) < STAGE_ORDER.index(Stage.QUOTE):
            opportunity = opportunity.model_copy(update={"stage": Stage.QUOTE, "last_interaction_at": now, "updated_at": now})
            store.save("opportunities", opportunity)
        record_activity(store, context, "proposal_created", f"Proposal \"{proposal.title}\" drafted (version 1).", proposal_id=proposal.id,
                        opportunity_id=opportunity.id, lead_id=opportunity.lead_id, account_id=opportunity.account_id)
        view = proposal_view(store, repository, context, proposal)
    audit(repository, context, "business.proposal.created", proposal.id, metadata={"opportunity_id": opportunity.id})
    return view


@router.get("/proposals/{proposal_id}", response_model=ProposalView)
def get_proposal(proposal_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ProposalView:
    read(context)
    with open_business_store(context) as store:
        return proposal_view(store, repository, context, get_or_404(store, "proposals", proposal_id, "Proposal"))


@router.get("/proposals/{proposal_id}/versions/{version}", response_model=VersionView)
def get_proposal_version(proposal_id: str, version: int, context: TenantContext = Depends(tenant_context)) -> VersionView:
    read(context)
    with open_business_store(context) as store:
        get_or_404(store, "proposals", proposal_id, "Proposal")
        found = store.first("proposal_versions", proposal_id=proposal_id, version=version)
        if found is None:
            raise http_error(404, "Version not found")
        return version_view(found)


@router.put("/proposals/{proposal_id}/content", response_model=ProposalView)
def update_proposal(proposal_id: str, payload: ProposalContent, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ProposalView:
    """Change what the proposal says. A locked version (sent for acceptance) is never edited:
    the change becomes a new version and any waiting approval lapses."""
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        proposal = get_or_404(store, "proposals", proposal_id, "Proposal", for_update=True)
        if proposal.status == ProposalStatus.ACCEPTED:
            raise http_error(409, "An accepted proposal never changes. Its accepted version is the contract record.")
        if proposal.status == ProposalStatus.WITHDRAWN:
            raise http_error(409, "This proposal was withdrawn. Create a new one.")
        versions = _versions(store, proposal_id)
        current = next(item for item in versions if item.version == proposal.current_version)
        fresh = _new_version(context, proposal_id, current.version, payload)
        if fresh.content_hash == current.content_hash:
            return proposal_view(store, repository, context, proposal)
        if current.locked:
            fresh = fresh.model_copy(update={"version": versions[0].version + 1})
            store.add("proposal_versions", fresh)
        else:
            store.save("proposal_versions", fresh.model_copy(update={"id": current.id, "created_at": current.created_at}))
        if proposal.status == ProposalStatus.PENDING_APPROVAL:
            retire_approval(repository, context, proposal.approval_task_id, proposal.approval_id)
        proposal = proposal.model_copy(update={
            "current_version": fresh.version, "status": ProposalStatus.DRAFT, "requested_hash": None, "requested_version": None,
            "decision_note": "Changed after it was sent; ask for acceptance again." if proposal.status == ProposalStatus.PENDING_APPROVAL else proposal.decision_note,
            "updated_at": now,
        })
        store.save("proposals", proposal)
        record_activity(store, context, "proposal_changed", f"Proposal \"{proposal.title}\" changed (version {fresh.version}).", proposal_id=proposal.id,
                        opportunity_id=proposal.opportunity_id, account_id=proposal.account_id, metadata={"version": fresh.version})
        view = proposal_view(store, repository, context, proposal)
    audit(repository, context, "business.proposal.changed", proposal_id, metadata={"version": fresh.version})
    return view


@router.post("/proposals/{proposal_id}/request-acceptance", response_model=ProposalView)
def request_acceptance(proposal_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ProposalView:
    """Lock the current version and ask an owner to record the client's acceptance of it."""
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        proposal = get_or_404(store, "proposals", proposal_id, "Proposal", for_update=True)
        proposal_view(store, repository, context, proposal)  # a rejected request goes back to draft first
        proposal = get_or_404(store, "proposals", proposal_id, "Proposal", for_update=True)
        if proposal.status != ProposalStatus.DRAFT:
            raise http_error(409, f"The proposal is {proposal.status.value.replace('_', ' ')}; only a draft is sent for acceptance.")
        version = store.first("proposal_versions", proposal_id=proposal_id, version=proposal.current_version)
        totals = proposal_totals(version)
        if totals["issues"]:
            raise http_error(409, "Fix the proposal first: " + " ".join(totals["issues"]))
        if totals["total"] <= 0:
            raise http_error(409, "The proposal total is zero. Add priced line items.")
        account = get_or_404(store, "accounts", proposal.account_id, "Account")
        if not version.locked:
            version = version.model_copy(update={"locked": True})
            store.save("proposal_versions", version)
        task, approval = request_approval(repository, context, acceptance_arguments(proposal, version, account.name), f"Accept proposal: {proposal.title} (v{version.version})")
        proposal = proposal.model_copy(update={
            "status": ProposalStatus.PENDING_APPROVAL, "requested_hash": version.content_hash, "requested_version": version.version,
            "sent_at": proposal.sent_at or now, "approval_id": approval.id, "approval_task_id": task.id, "decision_note": "", "updated_at": now,
        })
        store.save("proposals", proposal)
        opportunity = store.get("opportunities", proposal.opportunity_id, for_update=True)
        if opportunity is not None:
            if STAGE_ORDER.index(opportunity.stage) < STAGE_ORDER.index(Stage.NEGOTIATION):
                opportunity = opportunity.model_copy(update={"stage": Stage.NEGOTIATION, "last_interaction_at": now, "updated_at": now})
                store.save("opportunities", opportunity)
            if opportunity.lead_id:
                lead = store.get("leads", opportunity.lead_id, for_update=True)
                if lead is not None and lead.state in {LeadState.QUALIFIED, LeadState.CONTACTED, LeadState.REPLIED, LeadState.MEETING}:
                    stamp_state(lead, LeadState.OFFERED, now)
                    lead.updated_at = now
                    store.save("leads", lead)
                    record_activity(store, context, "state_changed", "Offered: the proposal was sent for acceptance.", lead_id=lead.id, account_id=lead.account_id,
                                    metadata={"to": LeadState.OFFERED.value})
        record_activity(store, context, "acceptance_requested", f"Acceptance of \"{proposal.title}\" version {version.version} sent to Approvals.",
                        proposal_id=proposal.id, opportunity_id=proposal.opportunity_id, account_id=proposal.account_id,
                        metadata={"version": version.version, "approval_id": approval.id})
        view = proposal_view(store, repository, context, proposal)
    audit(repository, context, "business.proposal.acceptance_requested", proposal_id, metadata={"version": version.version, "approval_id": approval.id})
    return view


@router.post("/proposals/{proposal_id}/withdraw", response_model=ProposalView)
def withdraw_proposal(proposal_id: str, payload: Reason, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ProposalView:
    write(context)
    with open_business_store(context) as store:
        proposal = get_or_404(store, "proposals", proposal_id, "Proposal", for_update=True)
        if proposal.status in {ProposalStatus.ACCEPTED, ProposalStatus.WITHDRAWN}:
            raise http_error(409, f"The proposal is already {proposal.status.value}.")
        if proposal.status == ProposalStatus.PENDING_APPROVAL:
            retire_approval(repository, context, proposal.approval_task_id, proposal.approval_id)
        proposal = proposal.model_copy(update={"status": ProposalStatus.WITHDRAWN, "decision_note": payload.reason.strip(), "updated_at": utc_now()})
        store.save("proposals", proposal)
        record_activity(store, context, "proposal_withdrawn", f"Proposal \"{proposal.title}\" withdrawn: {payload.reason.strip()}", proposal_id=proposal.id,
                        opportunity_id=proposal.opportunity_id, account_id=proposal.account_id)
        view = proposal_view(store, repository, context, proposal)
    audit(repository, context, "business.proposal.withdrawn", proposal_id)
    return view


def _markdown_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- None stated"


def proposal_markdown(view: ProposalView) -> str:
    """The proposal as a Markdown document (the accepted version when there is one)."""
    current = view.current
    lines = [
        f"# {view.title}", "",
        f"Client: {view.account_name}  ",
        f"Version: {current.version} ({'accepted' if view.status == ProposalStatus.ACCEPTED else view.status.value.replace('_', ' ')})  ",
        f"Content hash: `{current.content_hash}`", "",
    ]
    if view.status == ProposalStatus.ACCEPTED and view.accepted_version and current.version != view.accepted_version:
        lines[-1:-1] = [f"Amends accepted version {view.accepted_version} through approved change orders.  "]
    if current.summary:
        lines += ["## Summary", "", current.summary, ""]
    if current.scope:
        lines += ["## Scope", "", current.scope, ""]
    lines += ["## Deliverables", "", _markdown_list(current.deliverables), "", "## Not included", "", _markdown_list(current.exclusions), ""]
    lines += ["## Price", "", f"| Item | Quantity | Unit price ({current.currency.value}) | Amount |", "|---|---:|---:|---:|"]
    lines += [f"| {item.description} | {item.quantity} | {item.unit_price} | {item.amount()} |" for item in current.line_items]
    lines += ["", f"Subtotal: {current.currency.value} {current.subtotal}  ", f"Tax ({current.tax_rate_pct}%): {current.currency.value} {current.tax}. {current.tax_note}  ",
              f"Total: {current.currency.value} {current.total}", ""]
    if current.milestones:
        lines += ["## Payment milestones", "", "| Milestone | Amount | Due | Acceptance |", "|---|---:|---|---|"]
        lines += [f"| {item.title} | {current.currency.value} {item.amount} | {f'{item.due_in_days} days after start' if item.due_in_days is not None else 'To agree'} | {item.acceptance or '-'} |" for item in current.milestones]
        lines.append("")
    lines += ["## Assumptions", "", _markdown_list(current.assumptions), "", "## Acceptance criteria", "", _markdown_list(current.acceptance_criteria), ""]
    if current.payment_terms:
        lines += ["## Payment terms", "", current.payment_terms, ""]
    if current.valid_until:
        lines += [f"Valid until {current.valid_until.isoformat()}.", ""]
    return "\n".join(lines)


@router.get("/proposals/{proposal_id}/export")
def export_proposal(proposal_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    read(context)
    with open_business_store(context) as store:
        # An accepted proposal exports its contract record: the accepted version, or the
        # newest version approved change orders produced from it (docs/business.md#change-orders).
        view = proposal_view(store, repository, context, get_or_404(store, "proposals", proposal_id, "Proposal"))
    audit(repository, context, "business.proposal.exported", proposal_id, metadata={"version": view.current.version})
    name = "".join(char if char.isalnum() else "-" for char in view.title.lower()).strip("-")[:60] or "proposal"
    return Response(content=proposal_markdown(view), media_type="text/markdown; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{name}-v{view.current.version}.md"'})


# -- projects and milestones ---------------------------------------------------------------------


class ProjectCreate(BaseModel):
    proposal_id: str
    name: str | None = Field(default=None, min_length=2, max_length=200)
    start_on: date | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    status: ProjectStatus | None = None
    start_on: date | None = None
    contract_limits: str | None = Field(default=None, max_length=2000)


class MilestoneUpdate(BaseModel):
    status: MilestoneStatus | None = None
    due_on: date | None = None
    paid_amount: Money | None = None
    paid_on: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


class ProjectView(Project):
    account_name: str = ""
    milestones: list[Milestone] = Field(default_factory=list)
    accepted_milestones: int = 0
    accepted_value: Money = Decimal("0")
    paid_value: Money = Decimal("0")


def project_view(store: BusinessStore, project: Project) -> ProjectView:
    milestones, _ = store.find("milestones", Query(where={"project_id": project.id}, order_by="position", descending=False, limit=100))
    account = store.get("accounts", project.account_id)
    accepted = [item for item in milestones if item.status == MilestoneStatus.ACCEPTED]
    return ProjectView(**project.model_dump(), account_name=account.name if account else "", milestones=milestones, accepted_milestones=len(accepted),
                       accepted_value=money(sum((item.amount for item in accepted), Decimal(0))),
                       paid_value=money(sum((item.paid_amount or Decimal(0) for item in milestones), Decimal(0))))


@router.get("/projects", response_model=Page[ProjectView])
def list_projects(
    status_: list[ProjectStatus] = Q(default=[], alias="status"), q: str | None = Q(default=None, max_length=100), limit: int = 50, offset: int = 0,
    context: TenantContext = Depends(tenant_context),
) -> Page[ProjectView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    with open_business_store(context) as store:
        items, total = store.find("projects", Query(where={"status": set(status_)} if status_ else {}, text=q, text_fields=("name",), limit=limit, offset=offset))
        views = [project_view(store, item) for item in items]
    return Page[ProjectView](items=views, total=total, limit=limit, offset=offset)


@router.post("/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ProjectView:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        proposal = get_or_404(store, "proposals", payload.proposal_id, "Proposal", for_update=True)
        if proposal.status != ProposalStatus.ACCEPTED or proposal.accepted_version is None:
            raise http_error(409, "Start a project from an accepted proposal (its acceptance is approved in Approvals).")
        existing = store.first("projects", proposal_id=proposal.id)
        if existing is not None:
            raise http_error(409, "This proposal already has a project.")
        version = store.first("proposal_versions", proposal_id=proposal.id, version=proposal.accepted_version)
        if version is None or version.content_hash != proposal.accepted_hash:  # pragma: no cover - locked in the database
            raise http_error(409, "The accepted version does not match its hash; nothing was created.")
        totals = proposal_totals(version)
        project = Project(
            id=new_id("bproj"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, proposal_id=proposal.id, proposal_version=version.version,
            opportunity_id=proposal.opportunity_id, account_id=proposal.account_id, name=payload.name or proposal.title, currency=version.currency,
            contract_total=totals["total"], owner_id=context.user_id, start_on=payload.start_on,
            contract_limits="Not included: " + "; ".join(version.exclusions) if version.exclusions else "", created_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("projects", project)
        for position, plan in enumerate(version.milestones):
            due = payload.start_on + timedelta(days=plan.due_in_days) if payload.start_on and plan.due_in_days is not None else None
            store.add("milestones", Milestone(
                id=new_id("bmile"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, project_id=project.id, position=position, title=plan.title,
                amount=plan.amount, currency=version.currency, due_on=due, acceptance_criteria=plan.acceptance, created_at=now, updated_at=now,
            ))
        record_activity(store, context, "project_created", f"Project \"{project.name}\" started from proposal version {version.version}.", project_id=project.id,
                        proposal_id=proposal.id, opportunity_id=proposal.opportunity_id, account_id=proposal.account_id)
        view = project_view(store, project)
    audit(repository, context, "business.project.created", project.id, metadata={"proposal_id": proposal.id, "milestones": len(view.milestones)})
    return view


@router.get("/projects/{project_id}", response_model=ProjectView)
def get_project(project_id: str, context: TenantContext = Depends(tenant_context)) -> ProjectView:
    read(context)
    with open_business_store(context) as store:
        return project_view(store, get_or_404(store, "projects", project_id, "Project"))


@router.patch("/projects/{project_id}", response_model=ProjectView)
def update_project(project_id: str, payload: ProjectUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ProjectView:
    write(context)
    changes = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None or key == "start_on"}
    with open_business_store(context) as store:
        project = get_or_404(store, "projects", project_id, "Project", for_update=True)
        before = project.status
        project = project.model_copy(update={**changes, "updated_at": utc_now()})
        store.save("projects", project)
        if project.status != before:
            record_activity(store, context, "project_status", f"Project {before.value.replace('_', ' ')} to {project.status.value.replace('_', ' ')}.", project_id=project.id,
                            account_id=project.account_id)
        view = project_view(store, project)
    audit(repository, context, "business.project.updated", project_id, metadata={"fields": sorted(changes)})
    return view


@router.patch("/milestones/{milestone_id}", response_model=ProjectView)
def update_milestone(milestone_id: str, payload: MilestoneUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> ProjectView:
    """Progress, acceptance and payment received. A milestone's title and amount come from
    the accepted proposal and do not change here."""
    write(context)
    changes = payload.model_dump(exclude_unset=True)
    now = utc_now()
    with open_business_store(context) as store:
        item = get_or_404(store, "milestones", milestone_id, "Milestone", for_update=True)
        project = get_or_404(store, "projects", item.project_id, "Project")
        update: dict[str, Any] = {key: value for key, value in changes.items() if key in {"due_on", "paid_amount", "paid_on"} or value is not None}
        if "notes" in update:
            update["notes"] = (update["notes"] or "").strip()
        if update.get("paid_amount") is not None and update["paid_amount"] > item.amount:
            raise http_error(422, f"The payment is more than the milestone's {item.currency.value} {item.amount}.")
        if "paid_amount" in update and update["paid_amount"] is not None and not (update.get("paid_on") or item.paid_on):
            update["paid_on"] = now.date()
        new_status = update.get("status")
        if new_status == MilestoneStatus.ACCEPTED and item.status != MilestoneStatus.ACCEPTED:
            update["accepted_by"], update["accepted_at"] = context.user_id, now
        elif new_status is not None and new_status != MilestoneStatus.ACCEPTED:
            update["accepted_by"], update["accepted_at"] = None, None
        item = item.model_copy(update={**update, "updated_at": now})
        Milestone.model_validate(item.model_dump())
        store.save("milestones", item)
        if new_status is not None or "paid_amount" in update:
            summary = f"Milestone \"{item.title}\": {item.status.value.replace('_', ' ')}"
            if item.paid_amount is not None:
                summary += f", paid {item.currency.value} {item.paid_amount}"
            record_activity(store, context, "milestone_updated", summary + ".", project_id=project.id, account_id=project.account_id,
                            metadata={"milestone_id": item.id, "status": item.status.value})
        view = project_view(store, project)
    audit(repository, context, "business.milestone.updated", milestone_id, metadata={"fields": sorted(changes)})
    return view


# -- owner KPI summary -----------------------------------------------------------------------------


class FunnelStep(BaseModel):
    stage: str
    reached: int
    of: int
    rate_pct: float | None
    from_previous_pct: float | None


class MoneyFigure(BaseModel):
    currency: Currency
    amount: Money


class SignedMoneyFigure(BaseModel):
    """A figure that a change order can lower (a reduction approved in the window)."""

    currency: Currency
    amount: SignedMoney


class KpiSummary(BaseModel):
    window_from: datetime
    window_to: datetime
    days: int
    leads_created: int
    funnel: list[FunnelStep]
    by_state: dict[str, int]
    open_opportunities: int
    pipeline_assumed: list[MoneyFigure]
    pipeline_weighted: list[MoneyFigure]
    opportunities_won: int
    opportunities_lost: int
    proposals_sent: int
    proposals_accepted: int
    contracted: list[SignedMoneyFigure]
    cash_collected: list[MoneyFigure]
    research_runs: int
    web_searches: int
    truncated: bool
    notes: list[str]


def _pct(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def _sum_by_currency(pairs: list[tuple[Currency, Decimal]]) -> list[MoneyFigure]:
    totals: dict[Currency, Decimal] = {}
    for currency, amount in pairs:
        totals[currency] = totals.get(currency, Decimal(0)) + amount
    return [MoneyFigure(currency=currency, amount=money(amount)) for currency, amount in sorted(totals.items())]


def _signed_by_currency(pairs: list[tuple[Currency, Decimal]]) -> list[tuple[Currency, Decimal]]:
    totals: dict[Currency, Decimal] = {}
    for currency, amount in pairs:
        totals[currency] = totals.get(currency, Decimal(0)) + amount
    return sorted(totals.items())


FUNNEL = (("created", None), ("qualified", "qualified_at"), ("contacted", "contacted_at"), ("meeting", "meeting_at"), ("offered", "offered_at"), ("won", "won_at"))


@router.get("/kpi", response_model=KpiSummary)
def kpi_summary(
    days: int = Q(default=30, ge=1, le=366), to: date | None = None, context: TenantContext = Depends(tenant_context),
) -> KpiSummary:
    """The funnel of leads created in the window, each step with its denominator; pipeline
    values are the owner's assumptions; contracted value and cash collected are separate."""
    read(context)
    end = datetime.combine(to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) if to else utc_now()
    start = end - timedelta(days=days)
    window = Query(created_from=start, created_to=end, order_by="created_at", limit=MAX_SCAN)
    with open_business_store(context) as store:
        leads, lead_total = store.find("leads", window)
        opportunities, opportunity_total = store.find("opportunities", Query(order_by="created_at", limit=MAX_SCAN))
        proposals, _ = store.find("proposals", Query(order_by="created_at", limit=MAX_SCAN))
        projects, _ = store.find("projects", Query(order_by="created_at", limit=MAX_SCAN))
        milestones: list[Milestone] = []
        for project in projects:
            milestones += store.find("milestones", Query(where={"project_id": project.id}, limit=100))[0]
        runs, _ = store.find("research_runs", window)
        accepted_versions = {(item.id, item.accepted_version) for item in proposals if item.status == ProposalStatus.ACCEPTED}
        contracted_pairs: list[tuple[Currency, Decimal]] = []
        for proposal_id, number in accepted_versions:
            proposal = next(item for item in proposals if item.id == proposal_id)
            if proposal.accepted_at is None or not (start <= proposal.accepted_at < end):
                continue
            version = store.first("proposal_versions", proposal_id=proposal_id, version=number)
            if version is not None:
                contracted_pairs.append((version.currency, proposal_totals(version)["total"]))
        # Approved change orders count when they were approved (docs/business.md#kpi-summary).
        changes, _ = store.find("change_orders", Query(where={"status": "approved"}, order_by="created_at", limit=MAX_SCAN))
        for change in changes:
            if change.decided_at is not None and start <= change.decided_at < end and change.total_delta is not None:
                contracted_pairs.append((change.currency, change.total_delta))
    steps: list[FunnelStep] = []
    created = len(leads)
    previous = created
    for name, stamp in FUNNEL:
        reached = created if stamp is None else sum(1 for lead in leads if getattr(lead, stamp) is not None)
        steps.append(FunnelStep(stage=name, reached=reached, of=created, rate_pct=_pct(reached, created) if stamp else (100.0 if created else None),
                                from_previous_pct=_pct(reached, previous) if stamp else None))
        previous = reached
    by_state: dict[str, int] = {}
    for lead in leads:
        by_state[lead.state.value] = by_state.get(lead.state.value, 0) + 1
    open_ops = [item for item in opportunities if item.stage in OPEN_STAGES]
    in_window = [item for item in opportunities if (item.won_at and start <= item.won_at < end) or (item.lost_at and start <= item.lost_at < end)]
    notes = [
        "Funnel: of the leads created in this window, how many reached each step by now (a lead counts once per step).",
        "Pipeline values and probabilities are the owner's assumptions, not forecasts.",
        "Contracted value is accepted proposals and approved change orders (their price change, which can be negative) in the window; cash collected is milestone payments recorded with a date in the window. Neither is revenue.",
    ]
    return KpiSummary(
        window_from=start, window_to=end, days=days, leads_created=created, funnel=steps, by_state=by_state,
        open_opportunities=len(open_ops),
        pipeline_assumed=_sum_by_currency([(item.currency, item.value_amount) for item in open_ops if item.value_amount is not None]),
        pipeline_weighted=_sum_by_currency([(item.currency, item.value_amount * item.probability_pct / Decimal(100)) for item in open_ops
                                            if item.value_amount is not None and item.probability_pct is not None]),
        opportunities_won=sum(1 for item in in_window if item.stage == Stage.WON),
        opportunities_lost=sum(1 for item in in_window if item.stage == Stage.LOST),
        proposals_sent=sum(1 for item in proposals if item.sent_at and start <= item.sent_at < end),
        proposals_accepted=sum(1 for item in proposals if item.accepted_at and start <= item.accepted_at < end),
        contracted=[SignedMoneyFigure(currency=item[0], amount=money(item[1])) for item in _signed_by_currency(contracted_pairs)],
        cash_collected=_sum_by_currency([(item.currency, item.paid_amount) for item in milestones
                                         if item.paid_amount is not None and item.paid_on is not None and start.date() <= item.paid_on <= end.date()]),
        research_runs=len(runs), web_searches=sum(item.queries for item in runs),
        truncated=lead_total > len(leads) or opportunity_total > len(opportunities), notes=notes,
    )

