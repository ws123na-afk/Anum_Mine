"""One lead list: WhatsApp leads waiting to join it, and duplicate suggestions (docs/business.md#whatsapp-leads).

* ``GET  /business/lead-sources/whatsapp``: the WhatsApp Sales CRM leads that are not in the lead
  list yet, each with the Business leads it may be the same person as (same normalised phone).
* ``POST /business/lead-sources/whatsapp/{id}/map``: bring one in, as a new lead (``separate_from``
  records that the suggested leads are other people) or linked to a lead the owner confirmed
  (``link_to_lead_id``).
* ``POST /business/lead-sources/whatsapp/map-all``: owners bring in every waiting lead that has no
  suggestion; leads with a suggestion keep waiting for the owner's answer.
* ``GET  /business/lead-duplicates``, ``POST .../merge`` and ``POST .../separate``: open Business
  leads that share a phone number or e-mail address, and the owner's answer.

Reading needs ``business:read`` and ``whatsapp:read``; mapping ``business:write`` (``business:manage``
to map everything or merge two leads, because a merge closes a lead). Plain ``def`` routes: the
database work runs in the threadpool, never on the event loop.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query as Q
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import repository_context, require_permission, tenant_context
from ..repository import AnumRepository
from ..sales_lead_lifecycle import Lead as SalesLead
from ..schemas import TenantContext, utc_now
from .common import manage, read, write
from .models import LeadState
from .service import audit, http_error, normal_phone
from .store import open_business_store
from .whatsapp_leads import (
    MAX_SOURCE,
    _contact_keys,
    _decisions,
    _open_leads,
    duplicate_pairs,
    keep_separate,
    lead_ref,
    map_whatsapp_lead,
    mapped_ids,
    merge_leads,
    open_whatsapp_leads,
    suggestions_for,
    whatsapp_ref,
)

router = APIRouter(prefix="/api/v1/business", tags=["business"])

WHATSAPP_DENIED = "Reading WhatsApp leads needs the owner or member role in this workspace."


def _read_whatsapp(context: TenantContext) -> None:
    read(context)
    try:
        require_permission(context, Permission.WHATSAPP_READ)
    except Exception:
        raise http_error(403, WHATSAPP_DENIED) from None


class Suggested(BaseModel):
    lead_id: str
    title: str
    reason: str


class WhatsAppLeadView(BaseModel):
    id: str
    contact_name: str
    company_name: str
    phone: str | None
    requirements: str
    stage: str
    conversation_id: str
    campaign_hint: str | None
    created_at: datetime
    updated_at: datetime
    suggestions: list[Suggested]


class WhatsAppPending(BaseModel):
    items: list[WhatsAppLeadView]
    total: int
    limit: int
    offset: int
    # True when the WhatsApp Sales CRM holds more rows than one pass reads (MAX_SOURCE).
    truncated: bool


def _view(wa: SalesLead, suggestions: list[Suggested]) -> WhatsAppLeadView:
    return WhatsAppLeadView(
        id=wa.id, contact_name=wa.contact_name, company_name=wa.company_name, phone=normal_phone(wa.phone_e164),
        requirements=wa.requirements[:500], stage=getattr(wa.status, "value", str(wa.status)), conversation_id=wa.source_conversation_id,
        campaign_hint=wa.source_campaign_hint, created_at=wa.created_at, updated_at=wa.updated_at, suggestions=suggestions,
    )


def _source(context: TenantContext) -> list[SalesLead]:
    with open_whatsapp_leads(context) as source:
        return source.list_for_mapping(limit=MAX_SOURCE)


def _one(context: TenantContext, sales_lead_id: str) -> SalesLead:
    with open_whatsapp_leads(context) as source:
        found = source.get(sales_lead_id)
    if found is None:
        raise http_error(404, "WhatsApp lead not found.")
    return found


def _matches(wa: SalesLead, q: str) -> bool:
    needle = q.casefold()
    return any(needle in (value or "").casefold() for value in (wa.contact_name, wa.company_name, wa.phone_e164, wa.requirements))


@router.get("/lead-sources/whatsapp", response_model=WhatsAppPending)
def list_waiting_whatsapp_leads(
    q: str | None = Q(default=None, max_length=100), limit: int = Q(default=25, ge=1, le=100), offset: int = Q(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> WhatsAppPending:
    _read_whatsapp(context)
    rows = _source(context)
    with open_business_store(context) as store:
        decisions = _decisions(store)
        done = mapped_ids(store, decisions)
        waiting = [wa for wa in rows if wa.id not in done and (not q or _matches(wa, q.strip()))]
        page = waiting[offset : offset + limit]
        leads = _open_leads(store)
        keys = _contact_keys(store, leads)
        items = [
            _view(wa, [Suggested(lead_id=s.lead_id, title=s.title, reason=s.reason) for s in suggestions_for(wa, leads, keys, decisions)])
            for wa in page
        ]
    return WhatsAppPending(items=items, total=len(waiting), limit=limit, offset=offset, truncated=len(rows) >= MAX_SOURCE)


class MapRequest(BaseModel):
    # The owner confirmed this enquiry is the same person as that lead: link it, create nothing.
    link_to_lead_id: str | None = Field(default=None, max_length=80)
    # The owner confirmed these suggested leads are other people: a new lead is created.
    separate_from: list[str] = Field(default_factory=list, max_length=10)


class MapResult(BaseModel):
    lead_id: str
    linked: bool


@router.post("/lead-sources/whatsapp/{sales_lead_id}/map", response_model=MapResult)
def map_one_whatsapp_lead(
    sales_lead_id: str, payload: MapRequest, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context),
) -> MapResult:
    _read_whatsapp(context)
    write(context)
    wa = _one(context, sales_lead_id)
    now = utc_now()
    with open_business_store(context) as store:
        if payload.link_to_lead_id is None:
            leads = _open_leads(store)
            keys = _contact_keys(store, leads)
            for lead_id in dict.fromkeys(payload.separate_from):
                if store.get("leads", lead_id) is None:
                    raise http_error(404, "Lead not found.")
                keep_separate(store, context, whatsapp_ref(wa.id), lead_ref(lead_id), now)
            open_suggestions = suggestions_for(wa, leads, keys, _decisions(store))
            if open_suggestions:
                # Never merge or split people on our own: the owner answers each suggestion first.
                names = ", ".join(f"\"{item.title}\"" for item in open_suggestions)
                raise http_error(409, f"This enquiry may be the same person as {names}. Link it to that lead or confirm they are different people.")
        lead = map_whatsapp_lead(store, context, wa, now, link_to=payload.link_to_lead_id)
    audit(repository, context, "business.lead.whatsapp_mapped", lead.id, metadata={"sales_lead_id": wa.id, "linked": payload.link_to_lead_id is not None})
    return MapResult(lead_id=lead.id, linked=payload.link_to_lead_id is not None)


class MapAllResult(BaseModel):
    mapped: int
    waiting_for_owner: int


@router.post("/lead-sources/whatsapp/map-all", response_model=MapAllResult)
def map_all_whatsapp_leads(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> MapAllResult:
    """Every waiting WhatsApp lead with no duplicate suggestion becomes a Business lead; the
    others wait for the owner. Checked one at a time, so two enquiries from the same number
    in one pass become one lead and one suggestion, never two leads."""
    _read_whatsapp(context)
    manage(context)
    rows = _source(context)
    mapped = waiting = 0
    now = utc_now()
    with open_business_store(context) as store:
        decisions = _decisions(store)
        done = mapped_ids(store, decisions)
        for wa in rows:
            if wa.id in done:
                continue
            leads = _open_leads(store)
            if suggestions_for(wa, leads, _contact_keys(store, leads), decisions):
                waiting += 1
                continue
            map_whatsapp_lead(store, context, wa, now)
            mapped += 1
    audit(repository, context, "business.lead.whatsapp_mapped_all", "whatsapp", metadata={"mapped": mapped, "waiting": waiting})
    return MapAllResult(mapped=mapped, waiting_for_owner=waiting)


class PairLead(BaseModel):
    id: str
    title: str
    state: LeadState
    channel: str | None
    score_total: int
    updated_at: datetime


class DuplicatePair(BaseModel):
    first: PairLead
    second: PairLead
    reason: str


def _pair_lead(lead) -> PairLead:  # type: ignore[no-untyped-def]
    return PairLead(id=lead.id, title=lead.title, state=lead.state, channel=lead.channel, score_total=lead.score_total, updated_at=lead.updated_at)


@router.get("/lead-duplicates", response_model=list[DuplicatePair])
def list_lead_duplicates(context: TenantContext = Depends(tenant_context)) -> list[DuplicatePair]:
    read(context)
    with open_business_store(context) as store:
        pairs = duplicate_pairs(store)
    return [DuplicatePair(first=_pair_lead(p.first), second=_pair_lead(p.second), reason=p.reason) for p in pairs]


class MergeRequest(BaseModel):
    keep_id: str = Field(min_length=1, max_length=80)
    merge_id: str = Field(min_length=1, max_length=80)


class SeparateRequest(BaseModel):
    first_id: str = Field(min_length=1, max_length=80)
    second_id: str = Field(min_length=1, max_length=80)


class PairResult(BaseModel):
    kept_id: str | None
    closed_id: str | None


@router.post("/lead-duplicates/merge", response_model=PairResult)
def merge_duplicate_leads(payload: MergeRequest, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> PairResult:
    manage(context)
    with open_business_store(context) as store:
        keep, merged = merge_leads(store, context, payload.keep_id, payload.merge_id, utc_now())
    audit(repository, context, "business.lead.merged", keep.id, metadata={"merged": merged.id})
    return PairResult(kept_id=keep.id, closed_id=merged.id)


@router.post("/lead-duplicates/separate", response_model=PairResult)
def keep_leads_separate(payload: SeparateRequest, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> PairResult:
    write(context)
    with open_business_store(context) as store:
        for lead_id in (payload.first_id, payload.second_id):
            if store.get("leads", lead_id) is None:
                raise http_error(404, "Lead not found.")
        keep_separate(store, context, lead_ref(payload.first_id), lead_ref(payload.second_id), utc_now())
    audit(repository, context, "business.lead.kept_separate", payload.first_id, metadata={"other": payload.second_id})
    return PairResult(kept_id=None, closed_id=None)
