"""HTTP routes for Business, part 3: change orders, the client usage and cost ledger and the
project profitability view (``/api/v1/business``, docs/business.md#change-orders).

A change order amends an accepted proposal's project. Members and owners draft and send
one; only an owner approves or rejects it, with the content hash they were shown. Approving
creates a new locked proposal version (the version it amends is never edited) and updates
the project's contract total and milestones. The ledger records labour and provider costs;
cash collected is only what a person records on a milestone. Nothing here takes a payment.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query as Q, Response, status
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .changes import ChangeError, apply_change, change_hash
from .common import Page, Reason, get_or_404, manage, page_bounds, read, write
from .models import (
    OPEN_CHANGE_ORDER_STATES,
    ChangeOrder,
    ChangeOrderLine,
    ChangeOrderStatus,
    Currency,
    Milestone,
    MilestoneStatus,
    Money,
    Project,
    ProjectStatus,
    ProposalStatus,
    ProposalVersion,
    SignedMoney,
    UsageEntry,
    UsageKind,
    UsageSource,
    money,
)
from .invoice_models import DecisionSubject, PortalDecisionSummary
from .portal_service import decision_summary
from .service import audit, content_hash, http_error, proposal_totals, record_activity
from .store import BusinessStore, Query, open_business_store

router = APIRouter(prefix="/api/v1/business", tags=["business"])

DECIDE_DENIED = "Only a workspace owner approves or rejects a change order. A member drafts and sends it; an owner decides."


def decide(context: TenantContext) -> None:
    """Approving changes the contract: an authenticated owner only (approval:decide)."""
    write(context)
    if not has_permission(context, Permission.APPROVAL_DECIDE):
        raise http_error(403, DECIDE_DENIED)


# -- change orders -------------------------------------------------------------------------------


class ChangeOrderCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    request: str = Field(default="", max_length=2000)
    requested_by: str = Field(default="", max_length=160)
    lines: list[ChangeOrderLine] = Field(default_factory=list, max_length=40)
    timeline_days: int = Field(default=0, ge=-3650, le=3650)
    internal_cost_delta: SignedMoney = Decimal("0")


class ChangeOrderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    request: str | None = Field(default=None, max_length=2000)
    requested_by: str | None = Field(default=None, max_length=160)
    lines: list[ChangeOrderLine] | None = Field(default=None, max_length=40)
    timeline_days: int | None = Field(default=None, ge=-3650, le=3650)
    internal_cost_delta: SignedMoney | None = None


class ChangeDecision(BaseModel):
    content_hash: str = Field(min_length=64, max_length=64)


class ChangeOrderView(ChangeOrder):
    project_name: str = ""
    # For an open change order: what it would do to the current contract, or why it cannot.
    issues: list[str] = Field(default_factory=list)
    current_version: int | None = None
    stale: bool = False
    # What the customer decided in their portal about this exact change order.
    portal_decision: PortalDecisionSummary | None = None


def _current_version(store: BusinessStore, project: Project) -> ProposalVersion:
    version = store.first("proposal_versions", proposal_id=project.proposal_id, version=project.proposal_version)
    if version is None:  # pragma: no cover - a project always points at a stored version
        raise http_error(409, "The project's contract version is missing.")
    return version


def _settled_positions(store: BusinessStore, project: Project) -> tuple[list[Milestone], set[int]]:
    milestones, _ = store.find("milestones", Query(where={"project_id": project.id}, order_by="position", descending=False, limit=100))
    settled = {item.position for item in milestones if item.status in {MilestoneStatus.ACCEPTED, MilestoneStatus.SUBMITTED} or item.paid_amount is not None}
    return milestones, settled


def change_view(store: BusinessStore, order: ChangeOrder) -> ChangeOrderView:
    project = store.get("projects", order.project_id)
    view = ChangeOrderView(**order.model_dump(), project_name=project.name if project else "",
                           portal_decision=decision_summary(store, DecisionSubject.CHANGE_ORDER, order.id, order.content_hash))
    if project is None or order.status not in OPEN_CHANGE_ORDER_STATES:
        return view
    base = _current_version(store, project)
    view.current_version = base.version
    if order.status == ChangeOrderStatus.PENDING_APPROVAL and (order.base_version != base.version or order.base_hash != base.content_hash):
        view.stale = True
        view.issues = [f"The contract changed after this was sent (version {base.version} is now current). Send it for approval again."]
        return view
    if order.status == ChangeOrderStatus.DRAFT:
        _, settled = _settled_positions(store, project)
        try:
            applied = apply_change(base, order, settled)
            view.subtotal_delta, view.tax_delta, view.total_delta = applied.subtotal_delta, applied.tax_delta, applied.total_delta
        except ChangeError as error:
            view.issues = [str(error)]
    return view


def _open_project(store: BusinessStore, project_id: str) -> Project:
    project = get_or_404(store, "projects", project_id, "Project", for_update=True)
    if project.status == ProjectStatus.CLOSED:
        raise http_error(409, "The project is closed. Reopen it (status) before changing its contract.")
    proposal = get_or_404(store, "proposals", project.proposal_id, "Proposal")
    if proposal.status != ProposalStatus.ACCEPTED:  # pragma: no cover - a project only comes from an accepted proposal
        raise http_error(409, "Change orders are for accepted proposals.")
    return project


@router.get("/projects/{project_id}/change-orders", response_model=Page[ChangeOrderView])
def list_change_orders(
    project_id: str, status_: list[ChangeOrderStatus] = Q(default=[], alias="status"), limit: int = 50, offset: int = 0,
    context: TenantContext = Depends(tenant_context),
) -> Page[ChangeOrderView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {"project_id": project_id}
    if status_:
        where["status"] = set(status_)
    with open_business_store(context) as store:
        get_or_404(store, "projects", project_id, "Project")
        items, total = store.find("change_orders", Query(where=where, order_by="number", descending=True, limit=limit, offset=offset))
        views = [change_view(store, item) for item in items]
    return Page[ChangeOrderView](items=views, total=total, limit=limit, offset=offset)


@router.post("/projects/{project_id}/change-orders", response_model=ChangeOrderView, status_code=status.HTTP_201_CREATED)
def create_change_order(project_id: str, payload: ChangeOrderCreate, context: TenantContext = Depends(tenant_context),
                        repository: AnumRepository = Depends(repository_context)) -> ChangeOrderView:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        project = _open_project(store, project_id)
        number = store.count("change_orders", Query(where={"project_id": project.id})) + 1
        order = ChangeOrder(
            id=new_id("bchg"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, project_id=project.id, proposal_id=project.proposal_id,
            account_id=project.account_id, number=number, title=" ".join(payload.title.split()), request=payload.request.strip(),
            requested_by=" ".join(payload.requested_by.split()), lines=payload.lines, timeline_days=payload.timeline_days,
            internal_cost_delta=money(payload.internal_cost_delta), currency=project.currency, created_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("change_orders", order)
        record_activity(store, context, "change_order_created", f"Change order {number} \"{order.title}\" drafted.", project_id=project.id,
                        proposal_id=project.proposal_id, account_id=project.account_id, metadata={"change_order_id": order.id, "number": number})
        view = change_view(store, order)
    audit(repository, context, "business.change_order.created", order.id, metadata={"project_id": project_id, "number": number, "lines": len(order.lines)})
    return view


@router.get("/change-orders/{change_order_id}", response_model=ChangeOrderView)
def get_change_order(change_order_id: str, context: TenantContext = Depends(tenant_context)) -> ChangeOrderView:
    read(context)
    with open_business_store(context) as store:
        return change_view(store, get_or_404(store, "change_orders", change_order_id, "Change order"))


@router.patch("/change-orders/{change_order_id}", response_model=ChangeOrderView)
def update_change_order(change_order_id: str, payload: ChangeOrderUpdate, context: TenantContext = Depends(tenant_context),
                        repository: AnumRepository = Depends(repository_context)) -> ChangeOrderView:
    write(context)
    changes = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
    with open_business_store(context) as store:
        order = get_or_404(store, "change_orders", change_order_id, "Change order", for_update=True)
        if order.status != ChangeOrderStatus.DRAFT:
            raise http_error(409, f"The change order is {order.status.value.replace('_', ' ')}; only a draft changes. Withdraw it and draft a new one.")
        if "lines" in changes:
            changes["lines"] = payload.lines
        for key in ("title", "requested_by"):
            if key in changes:
                changes[key] = " ".join(changes[key].split())
        if "request" in changes:
            changes["request"] = changes["request"].strip()
        if "internal_cost_delta" in changes:
            changes["internal_cost_delta"] = money(changes["internal_cost_delta"])
        order = order.model_copy(update={**changes, "updated_at": utc_now()})
        ChangeOrder.model_validate(order.model_dump())
        store.save("change_orders", order)
        view = change_view(store, order)
    audit(repository, context, "business.change_order.updated", change_order_id, metadata={"fields": sorted(changes)})
    return view


@router.post("/change-orders/{change_order_id}/submit", response_model=ChangeOrderView)
def submit_change_order(change_order_id: str, context: TenantContext = Depends(tenant_context),
                        repository: AnumRepository = Depends(repository_context)) -> ChangeOrderView:
    """Bind the change order to the current contract version and send it to an owner. A
    change order that went stale (the contract changed) is sent again the same way."""
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        order = get_or_404(store, "change_orders", change_order_id, "Change order", for_update=True)
        if order.status not in OPEN_CHANGE_ORDER_STATES:
            raise http_error(409, f"The change order is {order.status.value}; only a draft is sent for approval.")
        project = _open_project(store, order.project_id)
        base = _current_version(store, project)
        _, settled = _settled_positions(store, project)
        try:
            applied = apply_change(base, order, settled)
        except ChangeError as error:
            raise http_error(409, f"Fix the change order first: {error}") from None
        order = order.model_copy(update={
            "status": ChangeOrderStatus.PENDING_APPROVAL, "base_version": base.version, "base_hash": base.content_hash,
            "content_hash": change_hash(order, base.version, base.content_hash), "subtotal_delta": applied.subtotal_delta,
            "tax_delta": applied.tax_delta, "total_delta": applied.total_delta, "submitted_by": context.user_id, "submitted_at": now,
            "decision_note": "", "updated_at": now,
        })
        store.save("change_orders", order)
        record_activity(store, context, "change_order_submitted",
                        f"Change order {order.number} sent for approval ({order.currency.value} {applied.total_delta:+}, {order.timeline_days:+} days).",
                        project_id=project.id, proposal_id=project.proposal_id, account_id=project.account_id,
                        metadata={"change_order_id": order.id, "base_version": base.version})
        view = change_view(store, order)
    audit(repository, context, "business.change_order.submitted", change_order_id, metadata={"base_version": base.version, "content_hash": order.content_hash})
    return view


@router.post("/change-orders/{change_order_id}/approve", response_model=ChangeOrderView)
def approve_change_order(change_order_id: str, payload: ChangeDecision, context: TenantContext = Depends(tenant_context),
                         repository: AnumRepository = Depends(repository_context)) -> ChangeOrderView:
    """An owner approves the exact change they were shown. A new locked proposal version
    holds the amended contract; the version it amends (and the accepted one) never change."""
    decide(context)
    now = utc_now()
    with open_business_store(context) as store:
        order = get_or_404(store, "change_orders", change_order_id, "Change order", for_update=True)
        if order.status != ChangeOrderStatus.PENDING_APPROVAL:
            raise http_error(409, f"The change order is {order.status.value.replace('_', ' ')}; only one sent for approval is approved.")
        project = _open_project(store, order.project_id)
        proposal = get_or_404(store, "proposals", project.proposal_id, "Proposal", for_update=True)
        base = _current_version(store, project)
        if order.base_version != base.version or order.base_hash != base.content_hash:
            raise http_error(409, f"The contract changed after this was sent (version {base.version} is now current). Send it for approval again.")
        if payload.content_hash != order.content_hash or change_hash(order, base.version, base.content_hash) != order.content_hash:
            raise http_error(409, "The change order is not the one you were shown; nothing was approved. Reload it and approve again.")
        milestones, settled = _settled_positions(store, project)
        try:
            applied = apply_change(base, order, settled)
        except ChangeError as error:  # a milestone was paid or accepted since it was sent
            raise http_error(409, f"The change order no longer applies: {error} Send it for approval again.") from None
        if (applied.subtotal_delta, applied.tax_delta, applied.total_delta) != (order.subtotal_delta, order.tax_delta, order.total_delta):
            raise http_error(409, "The price impact changed since it was sent; nothing was approved. Send it for approval again.")
        versions, _ = store.find("proposal_versions", Query(where={"proposal_id": proposal.id}, order_by="version", descending=True, limit=1))
        number = versions[0].version + 1
        fresh = ProposalVersion(
            **applied.content.model_dump(), id=new_id("bpver"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, proposal_id=proposal.id,
            version=number, content_hash=content_hash(applied.content), locked=True, change_order_id=order.id, created_by=context.user_id, created_at=now,
        )
        store.add("proposal_versions", fresh)
        store.save("proposals", proposal.model_copy(update={"current_version": number, "updated_at": now}))
        totals = proposal_totals(fresh)
        for item in milestones:
            if item.position in applied.reduced:
                store.save("milestones", item.model_copy(update={"amount": applied.reduced[item.position], "updated_at": now}))
        if applied.added is not None:
            due = project.start_on + timedelta(days=applied.added.due_in_days) if project.start_on and applied.added.due_in_days is not None else None
            store.add("milestones", Milestone(
                id=new_id("bmile"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, project_id=project.id, position=len(milestones),
                title=applied.added.title, amount=applied.added.amount, currency=project.currency, due_on=due, acceptance_criteria=applied.added.acceptance,
                created_at=now, updated_at=now,
            ))
        store.save("projects", project.model_copy(update={"proposal_version": number, "contract_total": totals["total"], "updated_at": now}))
        order = order.model_copy(update={"status": ChangeOrderStatus.APPROVED, "result_version": number, "decided_by": context.user_id,
                                         "decided_at": now, "updated_at": now})
        store.save("change_orders", order)
        record_activity(store, context, "change_order_approved",
                        f"Change order {order.number} approved: version {number}, contract {project.currency.value} {totals['total']}.",
                        project_id=project.id, proposal_id=proposal.id, account_id=project.account_id,
                        metadata={"change_order_id": order.id, "version": number, "content_hash": fresh.content_hash},
                        idempotency_key=f"change:{order.id}:approved")
        view = change_view(store, order)
    audit(repository, context, "business.change_order.approved", change_order_id,
          metadata={"project_id": order.project_id, "version": number, "content_hash": payload.content_hash})
    return view


@router.post("/change-orders/{change_order_id}/reject", response_model=ChangeOrderView)
def reject_change_order(change_order_id: str, payload: Reason, context: TenantContext = Depends(tenant_context),
                        repository: AnumRepository = Depends(repository_context)) -> ChangeOrderView:
    decide(context)
    now = utc_now()
    with open_business_store(context) as store:
        order = get_or_404(store, "change_orders", change_order_id, "Change order", for_update=True)
        if order.status != ChangeOrderStatus.PENDING_APPROVAL:
            raise http_error(409, f"The change order is {order.status.value.replace('_', ' ')}; only one sent for approval is rejected.")
        reason = payload.reason.strip()
        order = order.model_copy(update={"status": ChangeOrderStatus.REJECTED, "decided_by": context.user_id, "decided_at": now,
                                         "decision_note": reason, "updated_at": now})
        store.save("change_orders", order)
        record_activity(store, context, "change_order_rejected", f"Change order {order.number} rejected: {reason}", project_id=order.project_id,
                        proposal_id=order.proposal_id, account_id=order.account_id, metadata={"change_order_id": order.id})
        view = change_view(store, order)
    audit(repository, context, "business.change_order.rejected", change_order_id)
    return view


@router.post("/change-orders/{change_order_id}/withdraw", response_model=ChangeOrderView)
def withdraw_change_order(change_order_id: str, payload: Reason, context: TenantContext = Depends(tenant_context),
                          repository: AnumRepository = Depends(repository_context)) -> ChangeOrderView:
    write(context)
    with open_business_store(context) as store:
        order = get_or_404(store, "change_orders", change_order_id, "Change order", for_update=True)
        if order.status not in OPEN_CHANGE_ORDER_STATES:
            raise http_error(409, f"The change order is already {order.status.value}.")
        reason = payload.reason.strip()
        order = order.model_copy(update={"status": ChangeOrderStatus.WITHDRAWN, "decision_note": reason, "updated_at": utc_now()})
        store.save("change_orders", order)
        record_activity(store, context, "change_order_withdrawn", f"Change order {order.number} withdrawn: {reason}", project_id=order.project_id,
                        proposal_id=order.proposal_id, account_id=order.account_id, metadata={"change_order_id": order.id})
        view = change_view(store, order)
    audit(repository, context, "business.change_order.withdrawn", change_order_id)
    return view


# -- usage and cost ledger -----------------------------------------------------------------------


UsageQuantity = Field(default=Decimal("1"), gt=0, le=100000, max_digits=10, decimal_places=2)


class UsageCreate(BaseModel):
    account_id: str | None = None
    project_id: str | None = None
    kind: UsageKind
    description: str = Field(min_length=2, max_length=300)
    occurred_on: date
    quantity: Decimal = UsageQuantity
    unit_cost: Money = Decimal("0")
    currency: Currency = Currency.SAR
    billable: bool = False


class UsageUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=2, max_length=300)
    occurred_on: date | None = None
    quantity: Decimal | None = Field(default=None, gt=0, le=100000, max_digits=10, decimal_places=2)
    unit_cost: Money | None = None
    currency: Currency | None = None
    billable: bool | None = None


class AiUsageImport(BaseModel):
    project_id: str
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    share_pct: Decimal = Field(default=Decimal("100"), gt=0, le=100, max_digits=5, decimal_places=2)
    billable: bool = False


class UsageView(UsageEntry):
    account_name: str = ""
    project_name: str | None = None


def usage_view(store: BusinessStore, item: UsageEntry) -> UsageView:
    account = store.get("accounts", item.account_id)
    project = store.get("projects", item.project_id) if item.project_id else None
    return UsageView(**item.model_dump(), account_name=account.name if account else "", project_name=project.name if project else None)


def _scope_of(store: BusinessStore, account_id: str | None, project_id: str | None) -> tuple[str, str | None]:
    if project_id:
        project = get_or_404(store, "projects", project_id, "Project")
        if account_id and account_id != project.account_id:
            raise http_error(422, "The project belongs to another account.")
        return project.account_id, project.id
    if not account_id:
        raise http_error(422, "Choose the client account or project this entry belongs to.")
    get_or_404(store, "accounts", account_id, "Account")
    return account_id, None


@router.get("/usage", response_model=Page[UsageView])
def list_usage(
    project_id: str | None = None, account_id: str | None = None, kind: list[UsageKind] = Q(default=[]), billable: bool | None = None,
    q: str | None = Q(default=None, max_length=100), limit: int = 50, offset: int = 0, context: TenantContext = Depends(tenant_context),
) -> Page[UsageView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {}
    if project_id:
        where["project_id"] = project_id
    if account_id:
        where["account_id"] = account_id
    if kind:
        where["kind"] = set(kind)
    if billable is not None:
        where["billable"] = billable
    with open_business_store(context) as store:
        items, total = store.find("usage_ledger", Query(where=where, text=q, text_fields=("description",), order_by="occurred_on", limit=limit, offset=offset))
        views = [usage_view(store, item) for item in items]
    return Page[UsageView](items=views, total=total, limit=limit, offset=offset)


@router.post("/usage", response_model=UsageView, status_code=status.HTTP_201_CREATED)
def create_usage(payload: UsageCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> UsageView:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        account_id, project_id = _scope_of(store, payload.account_id, payload.project_id)
        item = UsageEntry(
            id=new_id("busg"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=account_id, project_id=project_id,
            kind=payload.kind, description=" ".join(payload.description.split()), occurred_on=payload.occurred_on, quantity=payload.quantity,
            unit_cost=payload.unit_cost, amount=money(payload.quantity * payload.unit_cost), currency=payload.currency, billable=payload.billable,
            source=UsageSource.MANUAL, created_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("usage_ledger", item)
        view = usage_view(store, item)
    audit(repository, context, "business.usage.created", item.id, metadata={"kind": item.kind.value, "project_id": project_id})
    return view


@router.patch("/usage/{entry_id}", response_model=UsageView)
def update_usage(entry_id: str, payload: UsageUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> UsageView:
    write(context)
    changes = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
    with open_business_store(context) as store:
        item = get_or_404(store, "usage_ledger", entry_id, "Ledger entry", for_update=True)
        if item.source == UsageSource.MODEL_BUDGET and set(changes) - {"description", "billable"}:
            raise http_error(409, "An imported AI usage entry keeps the model budget's figures; only its description and billable flag change.")
        if "description" in changes:
            changes["description"] = " ".join(changes["description"].split())
        item = item.model_copy(update={**changes, "updated_at": utc_now()})
        item.amount = money(item.quantity * item.unit_cost)
        UsageEntry.model_validate(item.model_dump())
        store.save("usage_ledger", item)
        view = usage_view(store, item)
    audit(repository, context, "business.usage.updated", entry_id, metadata={"fields": sorted(changes)})
    return view


@router.delete("/usage/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_usage(entry_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    manage(context)
    with open_business_store(context) as store:
        if not store.delete("usage_ledger", entry_id):
            raise http_error(404, "Ledger entry not found")
    audit(repository, context, "business.usage.deleted", entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/usage/import-ai", response_model=UsageView, status_code=status.HTTP_201_CREATED)
def import_ai_usage(payload: AiUsageImport, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> UsageView:
    """One finished month of this workspace's model usage (estimated USD, from the model
    budget), or the share of it the owner attributes to the project. Imported once."""
    write(context)
    from ..model_budget import BudgetScope, open_model_budget_store

    year, month_number = (int(part) for part in payload.month.split("-"))
    month = date(year, month_number, 1)
    today = utc_now().date()
    if month >= date(today.year, today.month, 1):
        raise http_error(409, "Import a finished month: this month's model usage is still growing.")
    with open_model_budget_store(context) as budget_store:
        usage = budget_store.usage(context, BudgetScope.WORKSPACE, month)
    if usage.calls == 0:
        raise http_error(409, f"No model usage was recorded for this workspace in {payload.month}.")
    reference = f"model_budget:{context.workspace_id}:{payload.month}"
    share = payload.share_pct
    amount = money(Decimal(str(usage.estimated_cost_usd)) * share / Decimal(100))
    note = f"AI model usage {payload.month}: {share.normalize():f}% of this workspace ({usage.calls} calls, {usage.total_tokens} tokens"
    note += f", {usage.unpriced_calls} without a price estimate)" if usage.unpriced_calls else ")"
    now = utc_now()
    with open_business_store(context) as store:
        project = get_or_404(store, "projects", payload.project_id, "Project")
        if store.first("usage_ledger", account_id=project.account_id, project_id=project.id, external_ref=reference) is not None:
            raise http_error(409, f"The model usage of {payload.month} is already in this project's ledger.")
        item = UsageEntry(
            id=new_id("busg"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=project.account_id, project_id=project.id,
            kind=UsageKind.AI_USAGE, description=note[:300], occurred_on=month, quantity=Decimal("1"), unit_cost=amount, amount=amount,
            currency=Currency.USD, billable=payload.billable, source=UsageSource.MODEL_BUDGET, external_ref=reference, created_by=context.user_id,
            created_at=now, updated_at=now,
        )
        store.add("usage_ledger", item)
        view = usage_view(store, item)
    audit(repository, context, "business.usage.imported", item.id, metadata={"project_id": project.id, "month": payload.month, "calls": usage.calls})
    return view


# -- profitability -------------------------------------------------------------------------------


class CostLine(BaseModel):
    kind: UsageKind
    amount: Money
    entries: int


class OtherCurrency(BaseModel):
    currency: Currency
    amount: Money
    entries: int


class Profitability(BaseModel):
    project_id: str
    currency: Currency
    accepted_version: int
    current_version: int
    accepted_revenue: Money
    change_orders_revenue: SignedMoney
    approved_change_orders: int
    revenue: Money
    tax: Money
    contract_total: Money
    cash_collected: Money | None
    outstanding: Money | None
    labour_hours: SignedMoney | None
    labour_cost: Money | None
    direct_costs: Money | None
    costs: list[CostLine]
    billable_costs: Money | None
    non_billable_costs: Money | None
    gross_margin: SignedMoney | None
    gross_margin_pct: SignedMoney | None
    other_currency: list[OtherCurrency]
    reconciled: bool
    notes: list[str]


@router.get("/projects/{project_id}/profitability", response_model=Profitability)
def project_profitability(project_id: str, context: TenantContext = Depends(tenant_context)) -> Profitability:
    """Revenue (the accepted proposal plus approved change orders, before tax), cash
    collected (milestone payments a person recorded) and the recorded costs, kept apart."""
    read(context)
    with open_business_store(context) as store:
        project = get_or_404(store, "projects", project_id, "Project")
        proposal = get_or_404(store, "proposals", project.proposal_id, "Proposal")
        accepted = store.first("proposal_versions", proposal_id=proposal.id, version=proposal.accepted_version)
        current = _current_version(store, project)
        approved, _ = store.find("change_orders", Query(where={"project_id": project.id, "status": ChangeOrderStatus.APPROVED}, limit=500))
        milestones, _ = store.find("milestones", Query(where={"project_id": project.id}, limit=100))
        entries, _ = store.find("usage_ledger", Query(where={"project_id": project.id}, order_by="occurred_on", limit=5000))
    accepted_totals = proposal_totals(accepted) if accepted is not None else {"subtotal": Decimal(0)}
    current_totals = proposal_totals(current)
    change_revenue = money(sum((item.subtotal_delta or Decimal(0) for item in approved), Decimal(0)))
    revenue = current_totals["subtotal"]
    reconciled = money(accepted_totals["subtotal"] + change_revenue) == revenue
    payments = [item.paid_amount for item in milestones if item.paid_amount is not None]
    cash = money(sum(payments, Decimal(0))) if payments else None
    mine = [item for item in entries if item.currency == project.currency]
    others: dict[Currency, tuple[Decimal, int]] = {}
    for item in entries:
        if item.currency != project.currency:
            amount, count = others.get(item.currency, (Decimal(0), 0))
            others[item.currency] = (amount + item.amount, count + 1)
    labour = [item for item in mine if item.kind == UsageKind.LABOUR]
    direct = [item for item in mine if item.kind != UsageKind.LABOUR]
    costs = []
    for kind in UsageKind:
        of_kind = [item for item in mine if item.kind == kind]
        if of_kind:
            costs.append(CostLine(kind=kind, amount=money(sum((item.amount for item in of_kind), Decimal(0))), entries=len(of_kind)))
    labour_cost = money(sum((item.amount for item in labour), Decimal(0))) if labour else None
    direct_cost = money(sum((item.amount for item in direct), Decimal(0))) if direct else None
    total_cost = money((labour_cost or Decimal(0)) + (direct_cost or Decimal(0)))
    margin = money(revenue - total_cost) if mine else None
    notes = [
        "Revenue is the accepted proposal plus approved change orders, before tax. Cash collected is only the milestone payments recorded by a person; ANUM collects no payments.",
        "Costs are the ledger entries recorded for this project in its currency; the margin covers only what has been recorded.",
    ]
    if others:
        notes.append("Entries in another currency are listed apart and not converted.")
    if not mine:
        notes.append("No costs are recorded yet, so there is no margin. Add labour hours and provider costs.")
    return Profitability(
        project_id=project.id, currency=project.currency, accepted_version=proposal.accepted_version or project.proposal_version,
        current_version=current.version, accepted_revenue=accepted_totals["subtotal"], change_orders_revenue=change_revenue,
        approved_change_orders=len(approved), revenue=revenue, tax=current_totals["tax"], contract_total=current_totals["total"],
        cash_collected=cash, outstanding=money(current_totals["total"] - cash) if cash is not None else None,
        labour_hours=money(sum((item.quantity for item in labour), Decimal(0))) if labour else None, labour_cost=labour_cost, direct_costs=direct_cost,
        costs=costs, billable_costs=money(sum((item.amount for item in mine if item.billable), Decimal(0))) if mine else None,
        non_billable_costs=money(sum((item.amount for item in mine if not item.billable), Decimal(0))) if mine else None,
        gross_margin=margin, gross_margin_pct=money(margin * Decimal(100) / revenue) if margin is not None and revenue > 0 else None,
        other_currency=[OtherCurrency(currency=currency, amount=money(amount), entries=count) for currency, (amount, count) in sorted(others.items())],
        reconciled=reconciled, notes=notes,
    )
