"""HTTP routes for Business support tickets (``/api/v1/business/support``, docs/business.md#support-tickets).

Permissions are the Business ones (``anum_api/authorization.py``): owners and members read and
work tickets (``business:read``, ``business:write``); owners set the support policy, reopen a
closed ticket and delete a ticket (``business:manage``). Viewers are refused because a ticket
holds a requester's contact details.

A ticket links to a Business account, contact and project where known, and to the inbound
email or WhatsApp conversation it came from (by id: the inbox keeps the text). Replies stay
drafts here; asking to deliver one hands exactly its text to that inbox's own approval path.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query as Q, Response, status
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..punctuation import plain_dashes
from ..repository import AnumRepository
from ..schemas import Approval, TenantContext, new_id, utc_now
from . import support, support_channels
from .common import MANAGE_DENIED, Page, get_or_404, manage, page_bounds, read, write
from .models import (
    OPEN_TICKET_STATUSES,
    TICKET_TRANSITIONS,
    Activity,
    Entitlement,
    OfferStatus,
    ProjectStatus,
    ReplyChannel,
    ReplyStatus,
    ResolutionCode,
    Severity,
    SupportPolicy,
    SupportTicket,
    TicketReply,
    TicketSource,
    TicketStatus,
)
from .service import audit, http_error, normal_email, normal_phone, record_activity
from .store import MAX_SCAN, BusinessStore, Query, open_business_store

router = APIRouter(prefix="/api/v1/business/support", tags=["business"])
logger = logging.getLogger("anum.business")

TEXT_FIELDS = ("title", "requester_name", "requester_email", "requester_phone", "description")
REPLY_LIMITS = {ReplyChannel.EMAIL: 4000, ReplyChannel.WHATSAPP: 4000}


# -- views ------------------------------------------------------------------------------------


class Sla(BaseModel):
    response: str
    resolution: str
    breached: bool


class TicketView(SupportTicket):
    reference: str
    account_name: str | None = None
    contact_name: str | None = None
    project_name: str | None = None
    support_plan_name: str | None = None
    sla: Sla
    allowed: list[TicketStatus]


class ReplyView(TicketReply):
    approval: Approval | None = None


class TicketDetail(TicketView):
    replies: list[ReplyView]
    activities: list[Activity]
    can_reply: bool
    reply_channel: ReplyChannel | None


def reference(ticket: SupportTicket) -> str:
    return "T-" + ticket.id.rsplit("_", 1)[-1][-6:].upper()


def _name(store: BusinessStore, kind: str, item_id: str | None, attr: str = "name") -> str | None:
    if not item_id:
        return None
    found = store.get(kind, item_id)
    return getattr(found, attr) if found is not None else None


def _plan_name(store: BusinessStore, offer_id: str | None) -> str | None:
    if not offer_id:
        return None
    offer = store.get("offers", offer_id)
    if offer is None:
        return None
    product = store.get("products", offer.product_id)
    return f"{product.name if product else 'Plan'} v{offer.version}"


def ticket_view(store: BusinessStore, ticket: SupportTicket, now: datetime) -> TicketView:
    allowed = sorted(TICKET_TRANSITIONS[ticket.status], key=list(TicketStatus).index)
    return TicketView(
        **ticket.model_dump(), reference=reference(ticket), account_name=_name(store, "accounts", ticket.account_id),
        contact_name=_name(store, "contacts", ticket.contact_id), project_name=_name(store, "projects", ticket.project_id),
        support_plan_name=_plan_name(store, ticket.support_offer_id), sla=Sla(**support.sla_state(ticket, now)), allowed=allowed,
    )


def reply_channel(ticket: SupportTicket) -> ReplyChannel | None:
    return {TicketSource.EMAIL: ReplyChannel.EMAIL, TicketSource.WHATSAPP: ReplyChannel.WHATSAPP}.get(ticket.source)


def _policy(store: BusinessStore) -> SupportPolicy:
    return store.get_settings().support_policy


# -- policy and setup ---------------------------------------------------------------------------


class SupportSetup(BaseModel):
    policy: SupportPolicy
    severities: list[Severity]
    statuses: list[TicketStatus]
    open_statuses: list[TicketStatus]
    transitions: dict[str, list[str]]
    resolution_codes: list[ResolutionCode]
    entitlements: list[Entitlement]
    can_write: bool
    can_manage: bool
    can_decide: bool
    can_link_email: bool
    can_link_whatsapp: bool


@router.get("/setup", response_model=SupportSetup)
def get_support_setup(context: TenantContext = Depends(tenant_context)) -> SupportSetup:
    read(context)
    with open_business_store(context) as store:
        policy = _policy(store)
    return SupportSetup(
        policy=policy, severities=list(Severity), statuses=list(TicketStatus),
        open_statuses=[item for item in TicketStatus if item in OPEN_TICKET_STATUSES],
        transitions={key.value: [item.value for item in TicketStatus if item in value] for key, value in TICKET_TRANSITIONS.items()},
        resolution_codes=list(ResolutionCode), entitlements=list(Entitlement),
        can_write=has_permission(context, Permission.BUSINESS_WRITE), can_manage=has_permission(context, Permission.BUSINESS_MANAGE),
        can_decide=has_permission(context, Permission.APPROVAL_DECIDE),
        can_link_email=has_permission(context, Permission.EMAIL_READ), can_link_whatsapp=has_permission(context, Permission.WHATSAPP_READ),
    )


@router.put("/policy", response_model=SupportPolicy)
def put_policy(payload: SupportPolicy, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> SupportPolicy:
    manage(context)
    payload.note = plain_dashes(payload.note.strip())
    problems = support.policy_problems(payload)
    if problems:
        raise http_error(422, " ".join(problems))
    with open_business_store(context) as store:
        settings = store.get_settings()
        store.save_settings(settings.model_copy(update={"support_policy": payload, "updated_by": context.user_id, "updated_at": utc_now()}))
    audit(repository, context, "business.support.policy_updated", context.workspace_id,
          metadata={"work_days": len(payload.work_days), "hours": payload.end_hour - payload.start_hour})
    return payload


class Assignee(BaseModel):
    user_id: str
    role: str
    you: bool


@router.get("/assignees", response_model=list[Assignee])
def list_assignees(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> list[Assignee]:
    """Who a ticket can be assigned to: active owners and members of this workspace."""
    read(context)
    return _assignees(repository, context)


def _assignees(repository: AnumRepository, context: TenantContext) -> list[Assignee]:
    found = [
        Assignee(user_id=member.user_id, role=str(member.role), you=member.user_id == context.user_id)
        for member in repository.list_memberships(context)
        if member.active and str(member.role) in {"owner", "member"}
    ]
    if not any(item.you for item in found):
        found.insert(0, Assignee(user_id=context.user_id, role=next((role for role in context.roles if role in {"owner", "member"}), "member"), you=True))
    return sorted(found, key=lambda item: (not item.you, item.user_id))


def _check_assignee(repository: AnumRepository, context: TenantContext, assignee_id: str | None) -> None:
    if assignee_id is None:
        return
    if assignee_id not in {item.user_id for item in _assignees(repository, context)}:
        raise http_error(422, "Assign the ticket to an active owner or member of this workspace.")


# -- summary and list -----------------------------------------------------------------------------


class SupportSummary(BaseModel):
    open: int
    new: int
    unassigned: int
    escalated: int
    breached: int
    waiting_on_customer: int
    truncated: bool


@router.get("/summary", response_model=SupportSummary)
def support_summary(context: TenantContext = Depends(tenant_context)) -> SupportSummary:
    read(context)
    now = utc_now()
    with open_business_store(context) as store:
        tickets, total = store.find("tickets", Query(where={"status": OPEN_TICKET_STATUSES}, limit=MAX_SCAN, order_by="created_at"))
    return SupportSummary(
        open=total, new=sum(item.status == TicketStatus.NEW for item in tickets),
        unassigned=sum(item.assignee_id is None for item in tickets), escalated=sum(item.escalated for item in tickets),
        breached=sum(support.sla_state(item, now)["breached"] for item in tickets),
        waiting_on_customer=sum(item.status == TicketStatus.WAITING_ON_CUSTOMER for item in tickets), truncated=total > len(tickets),
    )


@router.get("/tickets", response_model=Page[TicketView])
def list_tickets(
    q: str | None = Q(default=None, max_length=100),
    status_: list[TicketStatus] = Q(default=[], alias="status"),
    severity: list[Severity] = Q(default=[]),
    open_only: bool = False,
    assignee: str | None = Q(default=None, max_length=160),
    unassigned: bool = False,
    escalated: bool | None = None,
    account_id: str | None = Q(default=None, max_length=80),
    project_id: str | None = Q(default=None, max_length=80),
    source: TicketSource | None = None,
    sort: Literal["updated", "due", "created"] = "updated",
    limit: int = 50,
    offset: int = 0,
    context: TenantContext = Depends(tenant_context),
) -> Page[TicketView]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {}
    if status_:
        where["status"] = set(status_)
    elif open_only:
        where["status"] = OPEN_TICKET_STATUSES
    if severity:
        where["severity"] = set(severity)
    if unassigned:
        where["assignee_id"] = None
    elif assignee:
        where["assignee_id"] = context.user_id if assignee == "me" else assignee
    if escalated is not None:
        where["escalated"] = escalated
    if account_id:
        where["account_id"] = account_id
    if project_id:
        where["project_id"] = project_id
    if source is not None:
        where["source"] = source
    order = {"updated": ("updated_at", True), "due": ("resolution_due_at", False), "created": ("created_at", True)}[sort]
    now = utc_now()
    with open_business_store(context) as store:
        items, total = store.find("tickets", Query(where=where, text=q, text_fields=TEXT_FIELDS, order_by=order[0], descending=order[1], limit=limit, offset=offset))
        views = [ticket_view(store, item, now) for item in items]
    return Page[TicketView](items=views, total=total, limit=limit, offset=offset)


# -- create ---------------------------------------------------------------------------------------


class TicketFields(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    severity: Severity | None = None
    account_id: str | None = Field(default=None, max_length=80)
    contact_id: str | None = Field(default=None, max_length=80)
    project_id: str | None = Field(default=None, max_length=80)
    requester_name: str | None = Field(default=None, max_length=160)
    requester_email: str | None = Field(default=None, max_length=254)
    requester_phone: str | None = Field(default=None, max_length=40)
    entitlement: Entitlement | None = None
    support_offer_id: str | None = Field(default=None, max_length=80)
    entitlement_note: str | None = Field(default=None, max_length=300)
    assignee_id: str | None = Field(default=None, max_length=160)


class TicketCreate(TicketFields):
    title: str = Field(min_length=3, max_length=200)
    severity: Severity = Severity.NORMAL


class TicketFromMessage(TicketFields):
    source: Literal["email", "whatsapp"]
    ref_id: str = Field(min_length=1, max_length=80)
    severity: Severity = Severity.NORMAL


def _clean(text: str | None) -> str:
    return plain_dashes(" ".join((text or "").split()))


def _apply_links(store: BusinessStore, ticket: SupportTicket, fields: dict[str, Any]) -> None:
    """Account, contact, project and entitlement: every id must be this workspace's, and they must agree."""
    for key in ("account_id", "contact_id", "project_id", "support_offer_id"):
        if key in fields:
            setattr(ticket, key, fields[key] or None)
    contact = store.get("contacts", ticket.contact_id) if ticket.contact_id else None
    if ticket.contact_id and contact is None:
        raise http_error(404, "Contact not found")
    if contact is not None:
        if ticket.account_id and ticket.account_id != contact.account_id:
            raise http_error(422, "That contact belongs to another account.")
        ticket.account_id = contact.account_id
    project = store.get("projects", ticket.project_id) if ticket.project_id else None
    if ticket.project_id and project is None:
        raise http_error(404, "Project not found")
    if project is not None:
        if ticket.account_id and ticket.account_id != project.account_id:
            raise http_error(422, "That project belongs to another account.")
        ticket.account_id = project.account_id
    if ticket.account_id and store.get("accounts", ticket.account_id) is None:
        raise http_error(404, "Account not found")
    if "entitlement" in fields and fields["entitlement"] is not None:
        ticket.entitlement = fields["entitlement"]
    if ticket.entitlement == Entitlement.PROJECT:
        if project is None:
            raise http_error(422, "Choose the project that covers this request, or set the entitlement to none.")
        if project.status == ProjectStatus.CLOSED:
            raise http_error(422, "That project is closed, so it no longer covers support. Use a support plan or none.")
        ticket.support_offer_id = None
    elif ticket.entitlement == Entitlement.SUPPORT_PLAN:
        offer = store.get("offers", ticket.support_offer_id) if ticket.support_offer_id else None
        if offer is None:
            raise http_error(422, "Choose the support plan (a catalog version) that covers this request.")
        if offer.status != OfferStatus.ACTIVE:
            raise http_error(422, "That support plan version is not active in the catalog.")
    else:
        ticket.support_offer_id = None


def _apply_requester(store: BusinessStore, ticket: SupportTicket, fields: dict[str, Any]) -> None:
    if "requester_name" in fields:
        ticket.requester_name = _clean(fields["requester_name"])[:160]
    if "requester_email" in fields:
        raw = (fields["requester_email"] or "").strip()
        email = normal_email(raw) if raw else None
        if raw and email is None:
            raise http_error(422, "Enter the requester's e-mail address in full, or leave it empty.")
        ticket.requester_email = email
    if "requester_phone" in fields:
        raw = (fields["requester_phone"] or "").strip()
        phone = normal_phone(raw) if raw else None
        if raw and phone is None:
            raise http_error(422, "Enter the requester's phone in international format, or leave it empty.")
        ticket.requester_phone = phone
    # Verified: the requester is a known Business contact (matched by e-mail or phone).
    match = None
    if ticket.requester_email:
        match = store.first("contacts", email=ticket.requester_email)
    if match is None and ticket.requester_phone:
        match = store.first("contacts", phone=ticket.requester_phone)
    ticket.requester_verified = match is not None
    if match is not None and ticket.contact_id is None and (ticket.account_id in (None, match.account_id)):
        ticket.contact_id = match.id
        ticket.account_id = match.account_id
    if match is not None and not ticket.requester_name:
        ticket.requester_name = match.name


def _new_ticket(context: TenantContext, payload: TicketFields, *, title: str, now: datetime, reported_at: datetime,
                source: TicketSource = TicketSource.MANUAL, source_ref: str | None = None, source_account_id: str | None = None) -> SupportTicket:
    return SupportTicket(
        id=new_id("btkt"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, title=title,
        description=(payload.description or "").strip(), severity=payload.severity or Severity.NORMAL, source=source, source_ref=source_ref,
        source_account_id=source_account_id, reported_at=min(reported_at, now), entitlement_note=_clean(payload.entitlement_note)[:300],
        assignee_id=payload.assignee_id, created_by=context.user_id, created_at=now, updated_at=now,
    )


def _create(store: BusinessStore, context: TenantContext, ticket: SupportTicket, payload: TicketFields) -> TicketDetail:
    fields = payload.model_dump(exclude_unset=True)
    _apply_links(store, ticket, fields)
    _apply_requester(store, ticket, fields)
    support.apply_due_times(ticket, _policy(store))
    store.add("tickets", ticket)
    record_activity(store, context, "ticket_opened", f"Ticket {reference(ticket)} opened ({ticket.severity.value}, from {ticket.source.value}).",
                    ticket_id=ticket.id, account_id=ticket.account_id, project_id=ticket.project_id,
                    metadata={"severity": ticket.severity.value, "source": ticket.source.value})
    return _detail(store, None, context, ticket)


@router.post("/tickets", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
def create_ticket(payload: TicketCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    write(context)
    _check_assignee(repository, context, payload.assignee_id)
    now = utc_now()
    ticket = _new_ticket(context, payload, title=_clean(payload.title), now=now, reported_at=now)
    with open_business_store(context) as store:
        detail = _create(store, context, ticket, payload)
    audit(repository, context, "business.ticket.created", ticket.id, metadata={"severity": ticket.severity.value, "source": "manual"})
    return detail


@router.post("/tickets/from-message", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
def create_ticket_from_message(payload: TicketFromMessage, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    """A ticket from an inbound email or WhatsApp conversation: it links the message by id and
    copies only who wrote in and when (the inbox keeps the text)."""
    write(context)
    _check_assignee(repository, context, payload.assignee_id)
    source = TicketSource(payload.source)
    info = (support_channels.load_email_source if source == TicketSource.EMAIL else support_channels.load_whatsapp_source)(context, payload.ref_id)
    now = utc_now()
    title = _clean(payload.title) if payload.title else (info.title if len(info.title) >= 3 else f"Support request from {info.requester_name or 'a customer'}")
    ticket = _new_ticket(context, payload, title=title[:200], now=now, reported_at=info.reported_at, source=source,
                         source_ref=info.ref, source_account_id=info.account_id)
    fields = payload.model_dump(exclude_unset=True)
    fields.setdefault("requester_name", info.requester_name)
    fields.setdefault("requester_email", info.requester_email)
    fields.setdefault("requester_phone", info.requester_phone)
    with open_business_store(context) as store:
        existing = store.find("tickets", Query(where={"source": source, "source_ref": info.ref, "status": OPEN_TICKET_STATUSES}, limit=1))[0]
        if existing:
            raise http_error(409, f"This {source.value} is already linked to open ticket {reference(existing[0])} ({existing[0].id}).")
        merged = TicketFields.model_validate({key: value for key, value in fields.items() if key in TicketFields.model_fields})
        detail = _create(store, context, ticket, merged)
    audit(repository, context, "business.ticket.created", ticket.id, metadata={"severity": ticket.severity.value, "source": source.value, "source_ref": info.ref})
    return detail


# -- read and update ------------------------------------------------------------------------------


def _refresh_replies(store: BusinessStore, context: TenantContext, ticket: SupportTicket) -> list[TicketReply]:
    """Bring pending replies up to date with the inbox (sent, failed or discarded there)."""
    replies, _ = store.find("ticket_replies", Query(where={"ticket_id": ticket.id}, order_by="created_at", descending=False, limit=MAX_SCAN))
    now = utc_now()
    for reply in replies:
        if reply.status != ReplyStatus.PENDING_APPROVAL or not reply.external_ref:
            continue
        lookup = support_channels.email_reply_status if reply.channel == ReplyChannel.EMAIL else support_channels.whatsapp_reply_status
        try:
            state, error = lookup(context, reply.external_ref)
        except Exception:  # the inbox being unavailable must not hide the ticket
            logger.warning("business_reply_status_unavailable", extra={"anum_business": {"event": "reply_status_unavailable", "channel": reply.channel.value}})
            state, error = reply.status.value, reply.error
        if state == reply.status.value:
            continue
        reply.status = ReplyStatus(state)
        reply.error = error[:500]
        reply.updated_at = now
        if reply.status == ReplyStatus.SENT:
            reply.sent_at = now
            if ticket.first_responded_at is None:
                ticket.first_responded_at = now
                ticket.updated_at = now
                store.save("tickets", ticket)
            record_activity(store, context, "reply_sent", f"Reply sent by {reply.channel.value}.", ticket_id=ticket.id,
                            account_id=ticket.account_id, metadata={"reply_id": reply.id}, idempotency_key=f"reply-sent:{reply.id}")
        store.save("ticket_replies", reply)
    return replies


def _detail(store: BusinessStore, repository: AnumRepository | None, context: TenantContext, ticket: SupportTicket) -> TicketDetail:
    replies = _refresh_replies(store, context, ticket)
    activities, _ = store.find("activities", Query(where={"ticket_id": ticket.id}, order_by="created_at", limit=100))
    views = []
    for reply in replies:
        approval = repository.get_approval(reply.approval_id, context) if repository is not None and reply.approval_id else None
        views.append(ReplyView(**reply.model_dump(), approval=approval))
    channel = reply_channel(ticket)
    base = ticket_view(store, ticket, utc_now())
    return TicketDetail(**base.model_dump(), replies=views, activities=activities,
                        can_reply=channel is not None and ticket.status != TicketStatus.CLOSED, reply_channel=channel)


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    read(context)
    with open_business_store(context) as store:
        return _detail(store, repository, context, get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True))


@router.patch("/tickets/{ticket_id}", response_model=TicketDetail)
def update_ticket(ticket_id: str, payload: TicketFields, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    write(context)
    fields = payload.model_dump(exclude_unset=True)
    if "assignee_id" in fields:
        _check_assignee(repository, context, fields["assignee_id"])
    now = utc_now()
    with open_business_store(context) as store:
        ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
        if ticket.status == TicketStatus.CLOSED:
            raise http_error(409, "A closed ticket keeps its record. Reopen it (owners) to change it.")
        before = ticket.model_copy()
        if fields.get("title"):
            ticket.title = _clean(fields["title"])
        if "description" in fields:
            ticket.description = (fields["description"] or "").strip()
        if "entitlement_note" in fields:
            ticket.entitlement_note = _clean(fields["entitlement_note"])[:300]
        if "assignee_id" in fields:
            ticket.assignee_id = fields["assignee_id"] or None
        _apply_links(store, ticket, fields)
        _apply_requester(store, ticket, fields)
        if fields.get("severity") and fields["severity"] != ticket.severity:
            ticket.severity = fields["severity"]
            support.apply_due_times(ticket, _policy(store))
        changed = sorted(key for key in SupportTicket.model_fields if getattr(before, key) != getattr(ticket, key) and key not in {"updated_at"})
        if changed:
            ticket.updated_at = now
            store.save("tickets", ticket)
            summary = f"Changed {', '.join(name.replace('_', ' ') for name in changed if not name.endswith('_due_at'))}."
            if before.severity != ticket.severity:
                summary = f"Severity {before.severity.value} to {ticket.severity.value}; SLA due times recomputed. " + summary
            if before.assignee_id != ticket.assignee_id:
                record_activity(store, context, "ticket_assigned", f"Assigned to {ticket.assignee_id or 'nobody'}.", ticket_id=ticket.id,
                                account_id=ticket.account_id, metadata={"assignee_id": ticket.assignee_id})
            record_activity(store, context, "ticket_updated", summary, ticket_id=ticket.id, account_id=ticket.account_id, metadata={"fields": changed})
        detail = _detail(store, repository, context, ticket)
    audit(repository, context, "business.ticket.updated", ticket_id, metadata={"fields": sorted(fields)})
    return detail


class StatusChange(BaseModel):
    to: TicketStatus
    resolution_code: ResolutionCode | None = None
    resolution_summary: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=500)


@router.post("/tickets/{ticket_id}/status", response_model=TicketDetail)
def change_status(ticket_id: str, payload: StatusChange, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
        current = ticket.status
        if payload.to == current:
            raise http_error(409, f"The ticket is already {current.value.replace('_', ' ')}.")
        if payload.to not in TICKET_TRANSITIONS[current]:
            allowed = ", ".join(item.value.replace("_", " ") for item in TicketStatus if item in TICKET_TRANSITIONS[current])
            raise http_error(409, f"A {current.value.replace('_', ' ')} ticket can move to: {allowed}.")
        if current == TicketStatus.CLOSED and not has_permission(context, Permission.BUSINESS_MANAGE):
            raise http_error(403, MANAGE_DENIED)
        if payload.to == TicketStatus.RESOLVED:
            summary = plain_dashes((payload.resolution_summary or "").strip())
            if payload.resolution_code is None or len(summary) < 10:
                raise http_error(422, "Say how it was resolved: choose a resolution and write at least a sentence (10 characters).")
            ticket.resolution_code = payload.resolution_code
            ticket.resolution_summary = summary
        if payload.to == TicketStatus.CLOSED and ticket.resolved_at is None:
            raise http_error(409, "Resolve the ticket before closing it.")
        support.move(ticket, payload.to, now, context.user_id, _policy(store))
        store.save("tickets", ticket)
        text = f"Status {current.value.replace('_', ' ')} to {payload.to.value.replace('_', ' ')}."
        if payload.to == TicketStatus.RESOLVED:
            text += f" Resolution: {ticket.resolution_code.value.replace('_', ' ') if ticket.resolution_code else '-'}."
        if payload.note and payload.note.strip():
            text += " " + _clean(payload.note)
        kind = {TicketStatus.RESOLVED: "ticket_resolved", TicketStatus.CLOSED: "ticket_closed"}.get(payload.to, "ticket_status")
        if current in {TicketStatus.RESOLVED, TicketStatus.CLOSED} and payload.to == TicketStatus.IN_PROGRESS:
            kind = "ticket_reopened"
        record_activity(store, context, kind, text, ticket_id=ticket.id, account_id=ticket.account_id,
                        metadata={"from": current.value, "to": payload.to.value,
                                  "resolution_code": ticket.resolution_code.value if ticket.resolution_code else None})
        detail = _detail(store, repository, context, ticket)
    audit(repository, context, "business.ticket.status", ticket_id, metadata={"from": current.value, "to": payload.to.value})
    return detail


class EscalationChange(BaseModel):
    escalated: bool
    reason: str = Field(default="", max_length=300)


@router.post("/tickets/{ticket_id}/escalation", response_model=TicketDetail)
def set_escalation(ticket_id: str, payload: EscalationChange, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    write(context)
    reason = _clean(payload.reason)
    if payload.escalated and len(reason) < 3:
        raise http_error(422, "Say why the ticket is escalated (at least 3 characters).")
    now = utc_now()
    with open_business_store(context) as store:
        ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
        if ticket.status not in OPEN_TICKET_STATUSES:
            raise http_error(409, "Only an open ticket can be escalated or de-escalated.")
        if ticket.escalated == payload.escalated:
            raise http_error(409, "The ticket is already escalated." if payload.escalated else "The ticket is not escalated.")
        ticket.escalated = payload.escalated
        ticket.escalation_reason = reason if payload.escalated else ""
        ticket.escalated_at = now if payload.escalated else None
        ticket.escalated_by = context.user_id if payload.escalated else None
        ticket.updated_at = now
        store.save("tickets", ticket)
        record_activity(store, context, "ticket_escalated" if payload.escalated else "ticket_deescalated",
                        f"Escalated: {reason}" if payload.escalated else ("De-escalated. " + reason).strip(), ticket_id=ticket.id, account_id=ticket.account_id)
        detail = _detail(store, repository, context, ticket)
    audit(repository, context, "business.ticket.escalation", ticket_id, metadata={"escalated": payload.escalated})
    return detail


class NoteInput(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    responded: bool = False


@router.post("/tickets/{ticket_id}/notes", response_model=TicketDetail)
def add_note(ticket_id: str, payload: NoteInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    """A note on the trail. ``responded``: someone answered the requester outside ANUM (a call,
    their own phone); the first one stops the response clock."""
    write(context)
    text = _clean(payload.text)
    if not text:
        raise http_error(422, "Write the note first.")
    now = utc_now()
    with open_business_store(context) as store:
        ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
        if payload.responded and ticket.first_responded_at is None:
            ticket.first_responded_at = now
            ticket.updated_at = now
            store.save("tickets", ticket)
        record_activity(store, context, "response_recorded" if payload.responded else "note", text, ticket_id=ticket.id, account_id=ticket.account_id)
        detail = _detail(store, repository, context, ticket)
    audit(repository, context, "business.ticket.note", ticket_id, metadata={"responded": payload.responded, "chars": len(text)})
    return detail


@router.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(ticket_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Owners remove a ticket opened by mistake. A ticket with a sent reply is kept (it is a record)."""
    manage(context)
    with open_business_store(context) as store:
        ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
        if store.count("ticket_replies", Query(where={"ticket_id": ticket.id, "status": {ReplyStatus.SENT, ReplyStatus.PENDING_APPROVAL}})):
            raise http_error(409, "This ticket has a sent or waiting reply, so it is kept as a record. Close it instead.")
        for activity in store.find("activities", Query(where={"ticket_id": ticket.id}, order_by="created_at", limit=MAX_SCAN))[0]:
            store.delete("activities", activity.id)
        for reply in store.find("ticket_replies", Query(where={"ticket_id": ticket.id}, limit=MAX_SCAN))[0]:
            store.delete("ticket_replies", reply.id)
        store.delete("tickets", ticket.id)
    audit(repository, context, "business.ticket.deleted", ticket_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- replies ----------------------------------------------------------------------------------------


class ReplyInput(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


def reply_problems(body: str, channel: ReplyChannel) -> list[str]:
    """Deterministic checks before a reply can go to an approval (as Business drafts are checked)."""
    problems: list[str] = []
    if len(body) > REPLY_LIMITS[channel]:
        problems.append(f"The reply is longer than {REPLY_LIMITS[channel]} characters.")
    if "[" in body and "]" in body and any(marker in body.lower() for marker in ("[name]", "[date]", "[insert", "[your", "[link]")):
        problems.append("Fill in the placeholders in square brackets.")
    if any(word in body.lower() for word in ("guarantee", "guaranteed", "24/7", "24 / 7", "around the clock")):
        problems.append("Do not promise guarantees or 24/7 cover; describe what will happen and when.")
    return problems


def create_reply(store: BusinessStore, context: TenantContext, ticket: SupportTicket, body: str, generator: str) -> TicketReply:
    channel = reply_channel(ticket)
    if channel is None:
        raise http_error(409, "This ticket was opened by hand, so ANUM has no conversation to reply in. Reply yourself, then add a note marked as a response.")
    if ticket.status == TicketStatus.CLOSED:
        raise http_error(409, "The ticket is closed.")
    text = plain_dashes(body.strip())
    if not text:
        raise http_error(422, "Write the reply first.")
    now = utc_now()
    reply = TicketReply(id=new_id("brpl"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, ticket_id=ticket.id, channel=channel,
                        body=text, generator=generator, created_by=context.user_id, created_at=now, updated_at=now)
    store.add("ticket_replies", reply)
    record_activity(store, context, "reply_drafted", f"Reply drafted by {'the agent' if generator == 'agent' else 'a person'} ({channel.value}).",
                    ticket_id=ticket.id, account_id=ticket.account_id, metadata={"reply_id": reply.id, "generator": generator})
    return reply


@router.post("/tickets/{ticket_id}/replies", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
def add_reply(ticket_id: str, payload: ReplyInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    write(context)
    with open_business_store(context) as store:
        ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
        reply = create_reply(store, context, ticket, payload.body, "person")
        detail = _detail(store, repository, context, ticket)
    audit(repository, context, "business.ticket.reply_drafted", ticket_id, metadata={"reply_id": reply.id, "chars": len(reply.body)})
    return detail


def _editable_reply(store: BusinessStore, ticket_id: str, reply_id: str) -> tuple[SupportTicket, TicketReply]:
    ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
    reply = store.get("ticket_replies", reply_id, for_update=True)
    if reply is None or reply.ticket_id != ticket.id:
        raise http_error(404, "Reply not found")
    if reply.status not in {ReplyStatus.DRAFT, ReplyStatus.FAILED}:
        raise http_error(409, f"This reply is {reply.status.value.replace('_', ' ')}; it can no longer change here.")
    return ticket, reply


@router.patch("/tickets/{ticket_id}/replies/{reply_id}", response_model=TicketDetail)
def edit_reply(ticket_id: str, reply_id: str, payload: ReplyInput, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    write(context)
    with open_business_store(context) as store:
        ticket, reply = _editable_reply(store, ticket_id, reply_id)
        reply.body = plain_dashes(payload.body.strip())
        reply.generator = "person" if reply.generator == "person" else "agent_edited"
        reply.status, reply.error, reply.updated_at = ReplyStatus.DRAFT, "", utc_now()
        store.save("ticket_replies", reply)
        detail = _detail(store, repository, context, ticket)
    audit(repository, context, "business.ticket.reply_edited", ticket_id, metadata={"reply_id": reply_id, "chars": len(reply.body)})
    return detail


@router.delete("/tickets/{ticket_id}/replies/{reply_id}", response_model=TicketDetail)
def discard_reply(ticket_id: str, reply_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> TicketDetail:
    write(context)
    with open_business_store(context) as store:
        ticket, reply = _editable_reply(store, ticket_id, reply_id)
        reply.status, reply.updated_at = ReplyStatus.DISCARDED, utc_now()
        store.save("ticket_replies", reply)
        record_activity(store, context, "reply_discarded", "Reply draft discarded.", ticket_id=ticket.id, account_id=ticket.account_id, metadata={"reply_id": reply.id})
        detail = _detail(store, repository, context, ticket)
    audit(repository, context, "business.ticket.reply_discarded", ticket_id, metadata={"reply_id": reply_id})
    return detail


class ReplyRequestResult(BaseModel):
    ticket: TicketDetail
    reply: ReplyView
    detail: str


def _claim_reply(context: TenantContext, ticket_id: str, reply_id: str) -> tuple[SupportTicket, TicketReply]:
    with open_business_store(context) as store:
        ticket, reply = _editable_reply(store, ticket_id, reply_id)
        if ticket.status == TicketStatus.CLOSED:
            raise http_error(409, "The ticket is closed.")
        problems = reply_problems(reply.body, reply.channel)
        if problems:
            raise http_error(422, " ".join(problems))
        if not ticket.source_ref:
            raise http_error(409, "The ticket has no linked conversation to reply in.")
        return ticket, reply


def _record_request(context: TenantContext, ticket_id: str, reply_id: str, outcome: support_channels.DeliveryRequest) -> None:
    now = utc_now()
    with open_business_store(context) as store:
        ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
        reply = store.get("ticket_replies", reply_id, for_update=True)
        if reply is None:
            return
        reply.status = ReplyStatus(outcome.status)
        reply.external_ref, reply.approval_id, reply.task_id = outcome.external_ref, outcome.approval_id, outcome.task_id
        reply.error = "" if outcome.status != "failed" else outcome.detail[:500]
        reply.requested_by, reply.requested_at, reply.updated_at = context.user_id, now, now
        if reply.status == ReplyStatus.SENT:
            reply.sent_at = now
            if ticket.first_responded_at is None:
                ticket.first_responded_at = now
                ticket.updated_at = now
                store.save("tickets", ticket)
        store.save("ticket_replies", reply)
        record_activity(store, context, "reply_requested", f"Reply handed to the {reply.channel.value} approval path: {outcome.status.replace('_', ' ')}.",
                        ticket_id=ticket.id, account_id=ticket.account_id,
                        metadata={"reply_id": reply.id, "approval_id": outcome.approval_id, "status": outcome.status})


def _result(context: TenantContext, repository: AnumRepository, ticket_id: str, reply_id: str, detail: str) -> ReplyRequestResult:
    with open_business_store(context) as store:
        ticket = get_or_404(store, "tickets", ticket_id, "Ticket", for_update=True)
        full = _detail(store, repository, context, ticket)
    reply = next(item for item in full.replies if item.id == reply_id)
    return ReplyRequestResult(ticket=full, reply=reply, detail=detail)


@router.post("/tickets/{ticket_id}/replies/{reply_id}/request-approval", response_model=ReplyRequestResult)
async def request_reply_approval(
    ticket_id: str, reply_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ReplyRequestResult:
    """Hand exactly this reply to the inbox's approval path. Nothing is sent until an owner
    approves it there with the payload hash (the inbox's own rules decide)."""
    write(context)
    ticket, reply = await asyncio.to_thread(_claim_reply, context, ticket_id, reply_id)
    if reply.channel == ReplyChannel.EMAIL:
        outcome = await support_channels.request_email_reply(context, repository, ticket.source_ref or "", reply.body)
    else:
        outcome = await support_channels.request_whatsapp_reply(context, repository, ticket.source_ref or "", reply.body)
    await asyncio.to_thread(_record_request, context, ticket_id, reply_id, outcome)
    audit(repository, context, "business.ticket.reply_requested", ticket_id,
          metadata={"reply_id": reply_id, "channel": reply.channel.value, "status": outcome.status, "approval_id": outcome.approval_id,
                    "payload_hash": outcome.payload_hash},
          outcome="success" if outcome.status != "failed" else "failure")
    return await asyncio.to_thread(_result, context, repository, ticket_id, reply_id, outcome.detail)
