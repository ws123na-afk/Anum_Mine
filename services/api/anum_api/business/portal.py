"""The customer portal API (``/api/v1/portal``, docs/business-portal.md).

What one client account sees of its supplier: the offers sent to it, change orders waiting for
it, its invoices and its support requests; and what it may do: approve or decline an offer or a
change order, and open a support request. Every route is scoped to the one account the link was
issued for, reads through explicit portal views (never an internal record: no cost, margin,
internal notes, assignee or SLA), and refuses anything else. A customer's approval is a
record for the owner, who still confirms the acceptance in Approvals.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query as Q, Response, status
from pydantic import BaseModel, Field

from ..dependencies import workspace_archived
from ..punctuation import plain_dashes
from ..schemas import new_id, utc_now
from .api_changes import change_view
from .api_support import TicketFields, _create, _new_ticket, reference
from .invoice_models import (
    ISSUED_INVOICE_STATES,
    DecisionSubject,
    Invoice,
    InvoiceStatus,
    PaymentMethod,
    PortalDecision,
)
from .invoice_print import render_invoice
from .invoice_service import payments_of
from .invoices import payment_state
from .models import (
    ChangeAction,
    ChangeOrderStatus,
    Currency,
    Money,
    Percent,
    ProjectStatus,
    ProposalStatus,
    Severity,
    SignedMoney,
    TicketStatus,
    ReplyStatus,
)
from .portal_auth import PortalSession, portal_session
from .portal_service import latest_decision
from .service import audit, http_error, proposal_totals, record_activity
from .store import BusinessStore, Query, open_business_store

router = APIRouter(prefix="/api/v1/portal", tags=["portal"])

PRINT_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
NO_DECISION = "This link can read but not approve. Ask the business to turn on approvals for you."
ARCHIVED = "This business has paused its account, so nothing can be changed now. You can still read everything."
TICKETS_PER_DAY = 5


def _writable(session: PortalSession) -> None:
    if workspace_archived(session.context):
        raise http_error(403, ARCHIVED)


def _business_name(store: BusinessStore) -> str:
    return store.get_settings().billing.legal_name


# -- views -----------------------------------------------------------------------------------------


class PortalDecisionView(BaseModel):
    decision: Literal["approved", "declined"]
    signer_name: str
    note: str
    decided_at: datetime


def _decision_view(found: PortalDecision | None, content_hash: str | None) -> PortalDecisionView | None:
    if found is None or found.content_hash != content_hash:
        return None
    return PortalDecisionView(decision=found.decision, signer_name=found.signer_name, note=found.note, decided_at=found.decided_at)


class PortalProject(BaseModel):
    id: str
    name: str


class PortalCounts(BaseModel):
    offers_waiting: int
    change_orders_waiting: int
    invoices_open: int
    invoices_overdue: int
    tickets_open: int


class PortalMe(BaseModel):
    business_name: str
    account_name: str
    contact_name: str
    expires_at: datetime
    can_decide: bool
    counts: PortalCounts
    projects: list[PortalProject]


class OfferLine(BaseModel):
    description: str
    quantity: float
    unit_price: Money
    amount: Money


class OfferMilestone(BaseModel):
    title: str
    amount: Money
    due_in_days: int | None
    acceptance: str


class PortalOfferSummary(BaseModel):
    id: str
    title: str
    status: Literal["waiting", "accepted"]
    currency: Currency
    total: Money
    version: int
    sent_at: datetime | None
    valid_until: date | None
    expired: bool
    decision: PortalDecisionView | None
    can_decide: bool


class PortalOffer(PortalOfferSummary):
    summary: str
    scope: str
    deliverables: list[str]
    exclusions: list[str]
    assumptions: list[str]
    acceptance_criteria: list[str]
    line_items: list[OfferLine]
    milestones: list[OfferMilestone]
    subtotal: Money
    tax: Money
    tax_rate_pct: Percent
    tax_note: str
    payment_terms: str
    content_hash: str


class DecisionInput(BaseModel):
    decision: Literal["approved", "declined"]
    content_hash: str = Field(min_length=64, max_length=64)
    signer_name: str = Field(min_length=2, max_length=160)
    note: str = Field(default="", max_length=500)


class ChangeLine(BaseModel):
    action: ChangeAction
    description: str
    quantity: float
    unit_price: Money
    amount: Money


class PortalChangeSummary(BaseModel):
    id: str
    number: int
    title: str
    project_name: str
    status: Literal["waiting", "approved"]
    currency: Currency
    total_delta: SignedMoney | None
    timeline_days: int
    decision: PortalDecisionView | None
    can_decide: bool
    stale: bool


class PortalChange(PortalChangeSummary):
    request: str
    lines: list[ChangeLine]
    subtotal_delta: SignedMoney | None
    tax_delta: SignedMoney | None
    content_hash: str


class PortalPayment(BaseModel):
    paid_on: date
    amount: Money
    method: PaymentMethod
    reference: str


class PortalInvoiceSummary(BaseModel):
    id: str
    number: str
    status: InvoiceStatus
    currency: Currency
    total: Money
    amount_paid: Money
    balance: Money
    issue_date: date | None
    due_date: date | None
    overdue: bool
    days_overdue: int
    payment_state: Literal["unpaid", "partially_paid", "paid"]
    customer_reference: str


class PortalInvoiceLine(BaseModel):
    description: str
    quantity: float
    unit_price: Money
    tax_rate_pct: Percent
    total: Money


class PortalSeller(BaseModel):
    legal_name: str
    address: str
    tax_label: str
    tax_number: str
    email: str
    phone: str
    bank_details: str


class PortalInvoice(PortalInvoiceSummary):
    lines: list[PortalInvoiceLine]
    subtotal: Money
    tax: Money
    notes: str
    payment_terms: str
    bill_to_name: str
    bill_to_address: str
    bill_to_tax_number: str
    seller: PortalSeller
    payments: list[PortalPayment]
    void_reason: str


class PortalReply(BaseModel):
    body: str
    sent_at: datetime | None


class PortalTicketSummary(BaseModel):
    id: str
    reference: str
    title: str
    severity: Severity
    status: TicketStatus
    project_name: str | None
    reported_at: datetime
    updated_at: datetime
    opened_here: bool


class PortalTicket(PortalTicketSummary):
    description: str
    resolution_summary: str
    resolved_at: datetime | None
    replies: list[PortalReply]


class TicketInput(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=4000)
    severity: Literal["low", "normal", "high"] = "normal"
    project_id: str | None = Field(default=None, max_length=80)


# -- me --------------------------------------------------------------------------------------------


def _visible_proposals(store: BusinessStore, account_id: str):  # type: ignore[no-untyped-def]
    items, _ = store.find("proposals", Query(where={"account_id": account_id, "status": {ProposalStatus.PENDING_APPROVAL, ProposalStatus.ACCEPTED}},
                                              order_by="updated_at", limit=200))
    return [item for item in items if item.sent_at is not None]


def _visible_changes(store: BusinessStore, account_id: str):  # type: ignore[no-untyped-def]
    items, _ = store.find("change_orders", Query(where={"account_id": account_id, "status": {ChangeOrderStatus.PENDING_APPROVAL, ChangeOrderStatus.APPROVED}},
                                                  order_by="number", limit=200))
    return items


def _issued_invoices(store: BusinessStore, account_id: str) -> list[Invoice]:
    items, _ = store.find("invoices", Query(where={"account_id": account_id, "status": set(ISSUED_INVOICE_STATES)}, order_by="issue_date", limit=500))
    return items


@router.get("/me", response_model=PortalMe)
def me(session: PortalSession = Depends(portal_session)) -> PortalMe:
    today = utc_now().date()
    account_id = session.account.id
    with open_business_store(session.context) as store:
        proposals = [item for item in _visible_proposals(store, account_id) if item.status == ProposalStatus.PENDING_APPROVAL]
        waiting_offers = [item for item in proposals if latest_decision(store, DecisionSubject.PROPOSAL, item.id) is None
                          or latest_decision(store, DecisionSubject.PROPOSAL, item.id).content_hash != item.requested_hash]  # type: ignore[union-attr]
        waiting_changes = [view for view in (change_view(store, item) for item in _visible_changes(store, account_id) if item.status == ChangeOrderStatus.PENDING_APPROVAL)
                           if not view.stale and (view.portal_decision is None or not view.portal_decision.current)]
        invoices = _issued_invoices(store, account_id)
        open_invoices = [item for item in invoices if item.status == InvoiceStatus.SENT]
        tickets = store.count("tickets", Query(where={"account_id": account_id, "status": {TicketStatus.NEW, TicketStatus.TRIAGED, TicketStatus.IN_PROGRESS,
                                                                                         TicketStatus.WAITING_ON_CUSTOMER}}))
        projects, _ = store.find("projects", Query(where={"account_id": account_id}, order_by="name", descending=False, limit=100))
        business = _business_name(store)
    return PortalMe(
        business_name=business, account_name=session.account.name, contact_name=session.contact.name, expires_at=session.access.expires_at,
        can_decide=session.access.can_decide,
        counts=PortalCounts(offers_waiting=len(waiting_offers), change_orders_waiting=len(waiting_changes), invoices_open=len(open_invoices),
                            invoices_overdue=sum(1 for item in open_invoices if payment_state(item, today)["overdue"]), tickets_open=tickets),
        projects=[PortalProject(id=item.id, name=item.name) for item in projects if item.status != ProjectStatus.CLOSED],
    )


# -- offers --------------------------------------------------------------------------------------------


def _offer(store: BusinessStore, session: PortalSession, proposal, detailed: bool):  # type: ignore[no-untyped-def]
    waiting = proposal.status == ProposalStatus.PENDING_APPROVAL
    version_number = proposal.requested_version if waiting else proposal.accepted_version
    version = store.first("proposal_versions", proposal_id=proposal.id, version=version_number) if version_number else None
    if version is None:  # pragma: no cover - a sent or accepted proposal always names its version
        return None
    totals = proposal_totals(version)
    shown_hash = proposal.requested_hash if waiting else proposal.accepted_hash
    decision = latest_decision(store, DecisionSubject.PROPOSAL, proposal.id)
    decision_view = _decision_view(decision, shown_hash)
    expired = bool(waiting and version.valid_until is not None and version.valid_until < utc_now().date())
    base = dict(
        id=proposal.id, title=proposal.title, status="waiting" if waiting else "accepted", currency=version.currency, total=totals["total"], version=version.version,
        sent_at=proposal.sent_at, valid_until=version.valid_until, expired=expired, decision=decision_view,
        can_decide=bool(session.access.can_decide and waiting and decision_view is None and not expired),
    )
    if not detailed:
        return PortalOfferSummary(**base)
    return PortalOffer(
        **base, summary=version.summary, scope=version.scope, deliverables=list(version.deliverables), exclusions=list(version.exclusions),
        assumptions=list(version.assumptions), acceptance_criteria=list(version.acceptance_criteria),
        line_items=[OfferLine(description=item.description, quantity=float(item.quantity), unit_price=item.unit_price, amount=item.amount()) for item in version.line_items],
        milestones=[OfferMilestone(title=item.title, amount=item.amount, due_in_days=item.due_in_days, acceptance=item.acceptance) for item in version.milestones],
        subtotal=totals["subtotal"], tax=totals["tax"], tax_rate_pct=version.tax_rate_pct, tax_note=version.tax_note, payment_terms=version.payment_terms,
        content_hash=version.content_hash,
    )


@router.get("/offers", response_model=list[PortalOfferSummary])
def list_offers(session: PortalSession = Depends(portal_session)) -> list[PortalOfferSummary]:
    with open_business_store(session.context) as store:
        return [view for view in (_offer(store, session, item, False) for item in _visible_proposals(store, session.account.id)) if view is not None]


def _proposal_of(store: BusinessStore, session: PortalSession, proposal_id: str):  # type: ignore[no-untyped-def]
    proposal = store.get("proposals", proposal_id)
    if proposal is None or proposal.account_id != session.account.id or proposal.status not in {ProposalStatus.PENDING_APPROVAL, ProposalStatus.ACCEPTED} or proposal.sent_at is None:
        raise http_error(404, "Offer not found")
    return proposal


@router.get("/offers/{proposal_id}", response_model=PortalOffer)
def get_offer(proposal_id: str, session: PortalSession = Depends(portal_session)) -> PortalOffer:
    with open_business_store(session.context) as store:
        return _offer(store, session, _proposal_of(store, session, proposal_id), True)


def _record_decision(store: BusinessStore, session: PortalSession, kind: DecisionSubject, subject_id: str, version: int | None, shown_hash: str,
                     payload: DecisionInput, label: str, proposal_id: str | None, project_id: str | None) -> tuple[PortalDecision, bool]:
    """Store the decision once per exact version; repeating it is harmless, reversing it is refused."""
    if not session.access.can_decide:
        raise http_error(403, NO_DECISION)
    if payload.content_hash != shown_hash:
        raise http_error(409, f"This {label} changed after you opened it, so nothing was recorded. Reload the page to read the latest version.")
    existing = latest_decision(store, kind, subject_id)
    if existing is not None and existing.content_hash == shown_hash:
        if existing.decision == payload.decision:
            return existing, False
        raise http_error(409, f"You already {existing.decision} this {label}. Contact the business to change it.")
    now = utc_now()
    decision = PortalDecision(
        id=new_id("bpdec"), tenant_id=session.context.tenant_id, workspace_id=session.context.workspace_id, access_id=session.access.id, account_id=session.account.id,
        contact_id=session.contact.id, subject_kind=kind, subject_id=subject_id, version=version, content_hash=shown_hash, decision=payload.decision,
        signer_name=" ".join(plain_dashes(payload.signer_name).split()), note=" ".join(plain_dashes(payload.note).split()), decided_at=now, created_at=now, updated_at=now,
    )
    store.add("portal_decisions", decision)
    record_activity(store, session.context, f"portal_{kind.value}_{payload.decision}", f"{session.contact.name} {payload.decision} the {label} in the customer portal.",
                    account_id=session.account.id, proposal_id=proposal_id, project_id=project_id, metadata={"subject_id": subject_id, "content_hash": shown_hash,
                                                                                                         "access_id": session.access.id})
    return decision, True


@router.post("/offers/{proposal_id}/decision", response_model=PortalOffer)
def decide_offer(proposal_id: str, payload: DecisionInput, session: PortalSession = Depends(portal_session)) -> PortalOffer:
    """Approve or decline the exact version shown. The business owner confirms the acceptance afterwards."""
    _writable(session)
    with open_business_store(session.context) as store:
        proposal = _proposal_of(store, session, proposal_id)
        if proposal.status != ProposalStatus.PENDING_APPROVAL:
            raise http_error(409, "This offer was already accepted; there is nothing left to approve.")
        version = store.first("proposal_versions", proposal_id=proposal.id, version=proposal.requested_version)
        if version is not None and version.valid_until is not None and version.valid_until < utc_now().date():
            raise http_error(409, f"This offer was valid until {version.valid_until}. Ask the business for a new one.")
        decision, created = _record_decision(store, session, DecisionSubject.PROPOSAL, proposal.id, proposal.requested_version, proposal.requested_hash or "", payload,
                                             "offer", proposal.id, None)
        view = _offer(store, session, proposal, True)
    if created:
        audit(None, session.context, f"business.portal.offer_{decision.decision}", proposal_id, metadata={"access_id": session.access.id, "content_hash": decision.content_hash})
    return view


# -- change orders ---------------------------------------------------------------------------------------


def _change(store: BusinessStore, session: PortalSession, order, detailed: bool):  # type: ignore[no-untyped-def]
    view = change_view(store, order)
    waiting = order.status == ChangeOrderStatus.PENDING_APPROVAL
    decision_view = _decision_view(latest_decision(store, DecisionSubject.CHANGE_ORDER, order.id), order.content_hash)
    base = dict(
        id=order.id, number=order.number, title=order.title, project_name=view.project_name, status="waiting" if waiting else "approved", currency=order.currency,
        total_delta=order.total_delta, timeline_days=order.timeline_days, decision=decision_view, stale=view.stale,
        can_decide=bool(session.access.can_decide and waiting and not view.stale and decision_view is None),
    )
    if not detailed:
        return PortalChangeSummary(**base)
    return PortalChange(
        **base, request=order.request, subtotal_delta=order.subtotal_delta, tax_delta=order.tax_delta, content_hash=order.content_hash or "",
        lines=[ChangeLine(action=item.action, description=item.description, quantity=float(item.quantity), unit_price=item.unit_price, amount=item.amount()) for item in order.lines],
    )


@router.get("/change-orders", response_model=list[PortalChangeSummary])
def list_changes(session: PortalSession = Depends(portal_session)) -> list[PortalChangeSummary]:
    with open_business_store(session.context) as store:
        return [_change(store, session, item, False) for item in _visible_changes(store, session.account.id)]


def _order_of(store: BusinessStore, session: PortalSession, change_order_id: str):  # type: ignore[no-untyped-def]
    order = store.get("change_orders", change_order_id)
    if order is None or order.account_id != session.account.id or order.status not in {ChangeOrderStatus.PENDING_APPROVAL, ChangeOrderStatus.APPROVED}:
        raise http_error(404, "Change order not found")
    return order


@router.get("/change-orders/{change_order_id}", response_model=PortalChange)
def get_change(change_order_id: str, session: PortalSession = Depends(portal_session)) -> PortalChange:
    with open_business_store(session.context) as store:
        return _change(store, session, _order_of(store, session, change_order_id), True)


@router.post("/change-orders/{change_order_id}/decision", response_model=PortalChange)
def decide_change(change_order_id: str, payload: DecisionInput, session: PortalSession = Depends(portal_session)) -> PortalChange:
    """Approve or decline the change order shown. The business owner approves it in Approvals afterwards."""
    _writable(session)
    with open_business_store(session.context) as store:
        order = _order_of(store, session, change_order_id)
        if order.status != ChangeOrderStatus.PENDING_APPROVAL:
            raise http_error(409, "This change order was already approved; there is nothing left to approve.")
        if change_view(store, order).stale:
            raise http_error(409, "The contract changed after this change order was sent, so it will be sent again. Nothing was recorded.")
        decision, created = _record_decision(store, session, DecisionSubject.CHANGE_ORDER, order.id, order.base_version, order.content_hash or "", payload,
                                             "change order", order.proposal_id, order.project_id)
        view = _change(store, session, order, True)
    if created:
        audit(None, session.context, f"business.portal.change_order_{decision.decision}", change_order_id,
              metadata={"access_id": session.access.id, "content_hash": decision.content_hash})
    return view


# -- invoices ---------------------------------------------------------------------------------------


def _invoice_summary(invoice: Invoice, today: date) -> PortalInvoiceSummary:
    return PortalInvoiceSummary(
        id=invoice.id, number=invoice.number or "", status=invoice.status, currency=invoice.currency, total=invoice.total, amount_paid=invoice.amount_paid,
        issue_date=invoice.issue_date, due_date=invoice.due_date, customer_reference=invoice.customer_reference, **payment_state(invoice, today),
    )


@router.get("/invoices", response_model=list[PortalInvoiceSummary])
def list_invoices(status_: list[InvoiceStatus] = Q(default=[], alias="status"), session: PortalSession = Depends(portal_session)) -> list[PortalInvoiceSummary]:
    today = utc_now().date()
    with open_business_store(session.context) as store:
        items = _issued_invoices(store, session.account.id)
    return [_invoice_summary(item, today) for item in items if not status_ or item.status in status_]


def _invoice_of(store: BusinessStore, session: PortalSession, invoice_id: str) -> Invoice:
    invoice = store.get("invoices", invoice_id)
    if invoice is None or invoice.account_id != session.account.id or invoice.status not in ISSUED_INVOICE_STATES:
        raise http_error(404, "Invoice not found")
    return invoice


@router.get("/invoices/{invoice_id}", response_model=PortalInvoice)
def get_invoice(invoice_id: str, session: PortalSession = Depends(portal_session)) -> PortalInvoice:
    today = utc_now().date()
    with open_business_store(session.context) as store:
        invoice = _invoice_of(store, session, invoice_id)
        payments = payments_of(store, invoice.id)
    seller = invoice.seller
    return PortalInvoice(
        **_invoice_summary(invoice, today).model_dump(),
        lines=[PortalInvoiceLine(description=item.description, quantity=float(item.quantity), unit_price=item.unit_price, tax_rate_pct=item.tax_rate_pct, total=item.total)
               for item in invoice.lines],
        subtotal=invoice.subtotal, tax=invoice.tax, notes=invoice.notes, payment_terms=invoice.payment_terms, bill_to_name=invoice.bill_to_name,
        bill_to_address=invoice.bill_to_address, bill_to_tax_number=invoice.bill_to_tax_number, void_reason=invoice.void_reason,
        seller=PortalSeller(legal_name=seller.legal_name, address=seller.address, tax_label=seller.tax_label, tax_number=seller.tax_number, email=seller.email,
                            phone=seller.phone, bank_details=seller.bank_details) if seller else PortalSeller(legal_name="", address="", tax_label="", tax_number="", email="", phone="", bank_details=""),
        payments=[PortalPayment(paid_on=item.paid_on, amount=item.amount, method=item.method, reference=item.reference) for item in payments],
    )


@router.get("/invoices/{invoice_id}/print")
def print_invoice(invoice_id: str, lang: Literal["en", "ar"] = "en", session: PortalSession = Depends(portal_session)) -> Response:
    with open_business_store(session.context) as store:
        invoice = _invoice_of(store, session, invoice_id)
        html = render_invoice(invoice, payments_of(store, invoice.id), store.get_settings().billing, lang)
    return Response(content=html, media_type="text/html; charset=utf-8", headers=PRINT_HEADERS)


# -- support requests ---------------------------------------------------------------------------------


def _ticket_summary(store: BusinessStore, ticket) -> PortalTicketSummary:  # type: ignore[no-untyped-def]
    project = store.get("projects", ticket.project_id) if ticket.project_id else None
    return PortalTicketSummary(
        id=ticket.id, reference=reference(ticket), title=ticket.title, severity=ticket.severity, status=ticket.status, project_name=project.name if project else None,
        reported_at=ticket.reported_at, updated_at=ticket.updated_at, opened_here=ticket.created_by.startswith("portal:"),
    )


def _ticket_detail(store: BusinessStore, ticket) -> PortalTicket:  # type: ignore[no-untyped-def]
    replies, _ = store.find("ticket_replies", Query(where={"ticket_id": ticket.id, "status": ReplyStatus.SENT}, order_by="sent_at", descending=False, limit=100))
    closed = ticket.status in {TicketStatus.RESOLVED, TicketStatus.CLOSED}
    return PortalTicket(
        **_ticket_summary(store, ticket).model_dump(), description=ticket.description, resolution_summary=ticket.resolution_summary if closed else "",
        resolved_at=ticket.resolved_at, replies=[PortalReply(body=item.body, sent_at=item.sent_at) for item in replies],
    )


@router.get("/tickets", response_model=list[PortalTicketSummary])
def list_tickets(session: PortalSession = Depends(portal_session)) -> list[PortalTicketSummary]:
    with open_business_store(session.context) as store:
        items, _ = store.find("tickets", Query(where={"account_id": session.account.id}, order_by="updated_at", limit=200))
        return [_ticket_summary(store, item) for item in items]


def _ticket_of(store: BusinessStore, session: PortalSession, ticket_id: str):  # type: ignore[no-untyped-def]
    ticket = store.get("tickets", ticket_id)
    if ticket is None or ticket.account_id != session.account.id:
        raise http_error(404, "Request not found")
    return ticket


@router.get("/tickets/{ticket_id}", response_model=PortalTicket)
def get_ticket(ticket_id: str, session: PortalSession = Depends(portal_session)) -> PortalTicket:
    with open_business_store(session.context) as store:
        return _ticket_detail(store, _ticket_of(store, session, ticket_id))


@router.post("/tickets", response_model=PortalTicket, status_code=status.HTTP_201_CREATED)
def open_ticket(payload: TicketInput, session: PortalSession = Depends(portal_session)) -> PortalTicket:
    """A support request from the customer. A few a day per link, so a leaked link cannot flood the desk."""
    _writable(session)
    now = utc_now()
    with open_business_store(session.context) as store:
        if payload.project_id:
            project = store.get("projects", payload.project_id)
            if project is None or project.account_id != session.account.id:
                raise http_error(404, "Project not found")
        if store.count("tickets", Query(where={"created_by": session.context.user_id}, created_from=now - timedelta(days=1))) >= TICKETS_PER_DAY:
            raise http_error(429, f"You can open {TICKETS_PER_DAY} requests a day from this link. Add to an earlier one, or call the business.")
        fields = TicketFields(
            title=" ".join(plain_dashes(payload.title).split()), description=plain_dashes(payload.description).strip(), severity=Severity(payload.severity),
            account_id=session.account.id, contact_id=session.contact.id, project_id=payload.project_id, requester_name=session.contact.name,
            requester_email=session.contact.email, requester_phone=session.contact.phone,
        )
        ticket = _new_ticket(session.context, fields, title=fields.title or "", now=now, reported_at=now)
        _create(store, session.context, ticket, fields)
        stored = store.get("tickets", ticket.id)
        view = _ticket_detail(store, stored)
    audit(None, session.context, "business.portal.ticket_opened", ticket.id, metadata={"access_id": session.access.id, "severity": payload.severity})
    return view
