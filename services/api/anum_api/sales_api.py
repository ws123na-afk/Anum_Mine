"""Authenticated Sales CRM API for ANUM Marketing and WhatsApp.

All mutations use one PostgreSQL transaction with transaction-local RLS scope.
This API never dispatches messages or sends quotations without existing approval
workflows; quotes and follow-ups are drafts/proposals.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Iterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .db.session import SessionLocal, set_tenant_context
from .db.sales_repository import SalesCRMStore
from .marketing_sales_orchestration import plan_inbound_handoff
from .sales_lead_lifecycle import (
    Lead, LeadStatus, create_lead, edit_lead, transition_lead, draft_quote,
    propose_follow_up, sales_overview,
)
from .schemas import TenantContext, utc_now

router = APIRouter(prefix="/api/v1/sales", tags=["sales"])


class InboundLeadRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=80)
    message_id: str = Field(min_length=1, max_length=160)
    contact_id: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=4000)


class LeadEditRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    company_name: str | None = Field(default=None, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    phone_e164: str | None = None
    requirements: str | None = Field(default=None, max_length=4000)
    assigned_to: str | None = Field(default=None, max_length=160)
    budget_min: Decimal | None = Field(default=None, ge=0)
    budget_max: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None


class StatusRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    status: LeadStatus


class QuoteLine(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(ge=0, decimal_places=2)


class QuoteRequest(BaseModel):
    items: list[QuoteLine] = Field(min_length=1, max_length=50)
    terms: str = Field(default="", max_length=4000)
    currency: str | None = None


class FollowUpRequest(BaseModel):
    due_at: datetime
    channel: str
    purpose: str = Field(min_length=1, max_length=500)


@contextmanager
def sales_store(context: TenantContext) -> Iterator[SalesCRMStore]:
    with SessionLocal() as session:
        with session.begin():
            set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
            yield SalesCRMStore(session, context.tenant_id, context.workspace_id)


def require_read(context: TenantContext) -> None:
    require_permission(context, Permission.WHATSAPP_READ)


def require_write(context: TenantContext) -> None:
    require_permission(context, Permission.MARKETING_WRITE)
    require_permission(context, Permission.WHATSAPP_REPLY)


def view(lead: Lead) -> dict:
    return {
        "id": lead.id, "contact_id": lead.contact_id,
        "company_name": lead.company_name, "contact_name": lead.contact_name,
        "phone_e164": lead.phone_e164, "intent": lead.intent.value,
        "status": lead.status.value, "revision": lead.revision,
        "language": lead.preferred_language, "requirements": lead.requirements,
        "budget_min": str(lead.budget_min) if lead.budget_min is not None else None,
        "budget_max": str(lead.budget_max) if lead.budget_max is not None else None,
        "currency": lead.currency, "assigned_to": lead.assigned_to,
        "campaign_hint": lead.source_campaign_hint,
        "marketing_consent": lead.marketing_consent,
        "created_at": lead.created_at.isoformat(), "updated_at": lead.updated_at.isoformat(),
    }


def required_lead(store: SalesCRMStore, lead_id: str) -> Lead:
    lead = store.get(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Sales lead not found")
    return lead


@router.get("/leads")
def list_leads(
    status_filter: LeadStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_read(context)
    with sales_store(context) as store:
        leads = store.list_leads(status=status_filter, limit=limit)
        return {"items": [view(lead) for lead in leads], "count": len(leads)}


@router.get("/leads/{lead_id}")
def get_lead(lead_id: str, context: TenantContext = Depends(tenant_context)) -> dict:
    require_read(context)
    with sales_store(context) as store:
        return view(required_lead(store, lead_id))


@router.post("/leads/inbound", status_code=status.HTTP_201_CREATED)
def create_inbound(
    request: InboundLeadRequest, context: TenantContext = Depends(tenant_context)
) -> dict:
    """Authenticated import; webhook and workers must supply verified IDs.

    This endpoint does NOT verify raw Meta signatures and must not be public.
    Consent defaults false regardless of text. Duplicate imports return the
    original lead, with no duplicate audit events.
    """
    require_write(context)
    handoff = plan_inbound_handoff(
        tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        conversation_id=request.conversation_id, message_id=request.message_id,
        inbound_text=request.message,
    )
    try:
        with sales_store(context) as store:
            existing = store.find_by_inbound_message(request.message_id)
            if existing is not None:
                return view(existing)
            lead, event = create_lead(
                handoff, lead_id=str(uuid4()), contact_id=request.contact_id, now=utc_now()
            )
            store.insert(lead, event)
            store.session.flush()
            return view(lead)
    except IntegrityError as exc:
        # A concurrent duplicate is recoverable via a GET; never expose SQL.
        raise HTTPException(status_code=409, detail="Inbound message already imported") from exc


@router.patch("/leads/{lead_id}")
def update_lead(
    lead_id: str, request: LeadEditRequest,
    context: TenantContext = Depends(tenant_context)
) -> dict:
    require_write(context)
    try:
        with sales_store(context) as store:
            original = required_lead(store, lead_id)
            updated, event = edit_lead(
                original, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                actor_id=context.user_id, now=utc_now(), **request.model_dump(),
            )
            store.update(original, updated, event)
            return view(updated)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/leads/{lead_id}/transition")
def change_status(
    lead_id: str, request: StatusRequest,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_write(context)
    try:
        with sales_store(context) as store:
            original = required_lead(store, lead_id)
            updated, event = transition_lead(
                original, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                actor_id=context.user_id, now=utc_now(),
                expected_revision=request.expected_revision, to_status=request.status,
            )
            store.update(original, updated, event)
            return view(updated)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/leads/{lead_id}/quotes", status_code=201)
def create_quote(
    lead_id: str, request: QuoteRequest,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_write(context)
    with sales_store(context) as store:
        lead = required_lead(store, lead_id)
        try:
            quote = draft_quote(
                lead, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                line_items=[(line.description, line.amount) for line in request.items],
                terms=request.terms, currency=request.currency,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        quote_id = str(uuid4())
        store.save_quote(quote, quote_id=quote_id, created_by=context.user_id, now=utc_now())
        return {"id": quote_id, "lead_id": lead.id, "total": str(quote.total),
                "currency": quote.currency, "status": "draft", "approval_required": True}


@router.post("/leads/{lead_id}/followups", status_code=201)
def create_followup(
    lead_id: str, request: FollowUpRequest,
    context: TenantContext = Depends(tenant_context),
) -> dict:
    require_write(context)
    with sales_store(context) as store:
        lead = required_lead(store, lead_id)
        try:
            followup = propose_follow_up(
                lead, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                due_at=request.due_at, now=utc_now(), channel=request.channel,
                purpose=request.purpose,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        followup_id = str(uuid4())
        store.save_followup(followup, followup_id=followup_id, created_by=context.user_id, now=utc_now())
        return {"id": followup_id, "lead_id": lead.id, "status": "proposed",
                "due_at": followup.due_at.isoformat(), "approval_required": True}


@router.get("/overview")
def overview(context: TenantContext = Depends(tenant_context)) -> dict:
    require_read(context)
    with sales_store(context) as store:
        # Bounded summary until server-side aggregation/paging is implemented.
        leads = store.list_leads(limit=100)
        summary = sales_overview(leads, tenant_id=context.tenant_id, workspace_id=context.workspace_id)
        return {
            "leads": summary.leads, "qualified": summary.qualified,
            "quoted": summary.quoted, "won": summary.won, "lost": summary.lost,
            "by_intent": summary.by_intent,
            "scope": "latest_100_leads", "revenue_estimate": None,
        }


@router.get("/leads/{lead_id}/history")
def lead_history(lead_id: str, context: TenantContext = Depends(tenant_context)) -> dict:
    """Audit trail, quotations and follow-up proposals; server-side scoped."""
    require_read(context)
    with sales_store(context) as store:
        required_lead(store, lead_id)
        return store.lead_history(lead_id)


@router.get("/campaign-conversions")
def campaign_conversions(context: TenantContext = Depends(tenant_context)) -> dict:
    """All lead counts by self-reported campaign marker, never verified ad attribution."""
    require_permission(context, Permission.MARKETING_READ)
    with sales_store(context) as store:
        return store.campaign_conversion_report()


@router.get("/operations")
def operations_queue(context: TenantContext = Depends(tenant_context)) -> dict:
    """Quote/follow-up review queue; no sending or approval side effects."""
    require_read(context)
    with sales_store(context) as store:
        return store.sales_operations()
