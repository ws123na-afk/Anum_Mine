"""HTTP routes for Business, part 4: the billing profile and invoices (``/api/v1/business``,
docs/business.md#invoices).

An invoice is built from the contract (milestones, which include the milestones change
orders add, or the unbilled share of a contract without milestones), billable client costs
from the usage ledger, an active catalog offer and manual lines. Members and owners draft
one; sending it is ``business.send_invoice`` in the ordinary approvals flow: an owner
approves it with the hash they were shown, and only then does it get a number and appear in
the customer's portal. Payments are recorded by hand. ANUM sends no e-mail and takes no
payment. A sent invoice never changes; a mistake is voided and issued again.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query as Q, Response, status
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..punctuation import plain_dashes
from ..repository import AnumRepository
from ..settings import settings
from ..schemas import Approval, ApprovalStatus, TenantContext, new_id, utc_now
from .common import Page, Reason, get_or_404, manage, page_bounds, read, write
from .invoice_models import (
    OPEN_INVOICE_STATES,
    CreditNote,
    CreditNoteStatus,
    Invoice,
    InvoiceLine,
    InvoicePayment,
    InvoiceStatus,
    LineSource,
    PaymentMethod,
    PaymentNotice,
    PaymentNoticeStatus,
)
from .invoice_pdf import invoice_filename, render_invoice_pdf
from .invoice_print import render_invoice
from .invoice_service import (
    apply_payment,
    billed_sources,
    contract_billed,
    credit_notes_of,
    notices_of,
    payments_of,
    release_milestones,
)
from .invoices import apply_lines, invoice_hash, payment_state, problems
from .models import (
    BillingProfile,
    Currency,
    LineKind,
    MilestoneStatus,
    Money,
    OfferStatus,
    PriceType,
    Percent,
    Quantity,
    UsageKind,
    money,
)
from .service import SEND_INVOICE_TOOL, audit, http_error, record_activity
from .store import MAX_SCAN, BusinessStore, Query, open_business_store
from .tools import invoice_arguments, request_approval, retire_approval

router = APIRouter(prefix="/api/v1/business", tags=["business"])

ApprovalState = Literal["none", "pending", "approved", "rejected", "expired"]
TEXT_LIMITS = {"customer_reference": 120, "bill_to_name": 200, "bill_to_email": 254, "bill_to_tax_number": 60}


def _clean(text: str | None, limit: int) -> str:
    return plain_dashes(" ".join((text or "").split()))[:limit]


def _block(text: str | None, limit: int) -> str:
    """Several lines (an address, bank details): line breaks kept, runs of spaces folded."""
    lines = [" ".join(part.split()) for part in plain_dashes(text or "").splitlines()]
    return "\n".join(part for part in lines if part)[:limit]


# -- billing profile ---------------------------------------------------------------------------


@router.get("/billing-profile", response_model=BillingProfile)
def get_billing_profile(context: TenantContext = Depends(tenant_context)) -> BillingProfile:
    read(context)
    with open_business_store(context) as store:
        return store.get_settings().billing


@router.put("/billing-profile", response_model=BillingProfile)
def put_billing_profile(payload: BillingProfile, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> BillingProfile:
    """Who the invoices come from. A sent invoice keeps the profile it was sent with."""
    manage(context)
    clean = payload.model_copy(update={
        "legal_name": _clean(payload.legal_name, 200), "address": _block(payload.address, 1000), "tax_label": _clean(payload.tax_label, 60) or "Tax registration number",
        "tax_number": _clean(payload.tax_number, 60), "email": _clean(payload.email, 254), "phone": _clean(payload.phone, 40),
        "bank_details": _block(payload.bank_details, 1500), "footer_note": _clean(payload.footer_note, 500),
    })
    with open_business_store(context) as store:
        current = store.get_settings()
        store.save_settings(current.model_copy(update={"billing": clean, "updated_by": context.user_id, "updated_at": utc_now()}))
    audit(repository, context, "business.billing_profile.updated", context.workspace_id,
          metadata={"prefix": clean.invoice_prefix, "has_tax_number": bool(clean.tax_number), "has_bank_details": bool(clean.bank_details)})
    return clean


# -- views ---------------------------------------------------------------------------------------


class Can(BaseModel):
    edit: bool
    request_send: bool
    cancel_request: bool
    delete: bool
    void: bool
    record_payment: bool
    remove_payment: bool
    credit: bool
    credit_enabled: bool
    void_credit_note: bool
    decide_notice: bool


class InvoiceListItem(BaseModel):
    id: str
    number: str | None
    status: InvoiceStatus
    account_id: str
    account_name: str
    project_id: str | None
    project_name: str | None
    currency: Currency
    total: Money
    amount_paid: Money
    amount_credited: Money
    balance: Money
    issue_date: date | None
    due_date: date | None
    overdue: bool
    days_overdue: int
    payment_state: Literal["unpaid", "partially_paid", "paid", "credited"]
    customer_reference: str
    notices_pending: int
    lines: int
    updated_at: datetime
    created_at: datetime


class InvoiceView(Invoice):
    account_name: str = ""
    project_name: str | None = None
    balance: Money = Decimal("0")
    overdue: bool = False
    days_overdue: int = 0
    payment_state: Literal["unpaid", "partially_paid", "paid", "credited"] = "unpaid"
    payments: list[InvoicePayment] = Field(default_factory=list)
    credit_notes: list[CreditNote] = Field(default_factory=list)
    payment_notices: list[PaymentNotice] = Field(default_factory=list)
    approval_state: ApprovalState = "none"
    approval: Approval | None = None
    # Why a draft cannot be sent for approval yet.
    issues: list[str] = Field(default_factory=list)
    can: Can


def _names(store: BusinessStore, invoice: Invoice, cache: dict[tuple[str, str], str | None] | None = None) -> tuple[str, str | None]:
    cache = cache if cache is not None else {}

    def name(kind: str, item_id: str | None) -> str | None:
        if not item_id:
            return None
        if (kind, item_id) not in cache:
            found = store.get(kind, item_id)
            cache[(kind, item_id)] = found.name if found else None
        return cache[(kind, item_id)]

    return name("accounts", invoice.account_id) or "", name("projects", invoice.project_id)


def _can(context: TenantContext, invoice: Invoice) -> Can:
    writer = has_permission(context, Permission.BUSINESS_WRITE)
    owner = has_permission(context, Permission.BUSINESS_MANAGE)
    return Can(
        edit=writer and invoice.status in OPEN_INVOICE_STATES,
        request_send=writer and invoice.status == InvoiceStatus.DRAFT,
        cancel_request=writer and invoice.status == InvoiceStatus.PENDING_APPROVAL,
        delete=writer and invoice.status == InvoiceStatus.DRAFT,
        void=owner and invoice.status == InvoiceStatus.SENT and invoice.amount_paid == 0 and invoice.amount_credited == 0,
        record_payment=writer and invoice.status == InvoiceStatus.SENT,
        remove_payment=owner and invoice.status in {InvoiceStatus.SENT, InvoiceStatus.PAID},
        credit=owner and settings.business_credit_notes_enabled and invoice.status == InvoiceStatus.SENT and invoice.balance_due() > 0,
        credit_enabled=settings.business_credit_notes_enabled,
        void_credit_note=owner and invoice.status in {InvoiceStatus.SENT, InvoiceStatus.PAID},
        decide_notice=writer and invoice.status in {InvoiceStatus.SENT, InvoiceStatus.PAID},
    )


def _approval_state(approval: Approval | None) -> ApprovalState:
    if approval is None:
        return "none"
    if approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at <= utc_now():
        return "expired"
    return {ApprovalStatus.PENDING: "pending", ApprovalStatus.APPROVED: "approved", ApprovalStatus.REJECTED: "rejected",
            ApprovalStatus.EXPIRED: "expired"}.get(approval.status, "none")  # type: ignore[return-value]


def invoice_view(store: BusinessStore, repository: AnumRepository, context: TenantContext, invoice: Invoice) -> InvoiceView:
    """With its payments and approval; a rejected or lapsed send request returns the invoice to draft here."""
    approval = repository.get_approval(invoice.approval_id, context) if invoice.approval_id else None
    state = _approval_state(approval)
    if invoice.status == InvoiceStatus.PENDING_APPROVAL and state in {"rejected", "expired"} and approval is not None:
        note = (f"Rejected by {approval.decided_by or 'an approver'}" + (f": {approval.decision_reason}" if approval.decision_reason else "."))\
            if state == "rejected" else "The approval request lapsed before anyone decided."
        invoice = invoice.model_copy(update={"status": InvoiceStatus.DRAFT, "decision_note": plain_dashes(note)[:500], "updated_at": utc_now()})
        store.save("invoices", invoice)
    profile = store.get_settings().billing
    account_name, project_name = _names(store, invoice)
    checked = invoice.model_copy(update={"seller": invoice.seller or profile})
    return InvoiceView(
        **invoice.model_dump(), account_name=account_name, project_name=project_name, payments=payments_of(store, invoice.id),
        credit_notes=credit_notes_of(store, invoice.id), payment_notices=notices_of(store, invoice.id),
        approval_state=state, approval=approval if state == "pending" else None,
        issues=problems(checked) if invoice.status in OPEN_INVOICE_STATES else [], can=_can(context, invoice), **payment_state(invoice, utc_now().date()),
    )


def list_item(store: BusinessStore, invoice: Invoice, today: date, names: dict[tuple[str, str], str | None], pending: dict[str, int] | None = None) -> InvoiceListItem:
    account_name, project_name = _names(store, invoice, names)
    return InvoiceListItem(
        id=invoice.id, number=invoice.number, status=invoice.status, account_id=invoice.account_id, account_name=account_name, project_id=invoice.project_id,
        project_name=project_name, currency=invoice.currency, total=invoice.total, amount_paid=invoice.amount_paid, amount_credited=invoice.amount_credited,
        issue_date=invoice.issue_date, due_date=invoice.due_date, customer_reference=invoice.customer_reference, notices_pending=(pending or {}).get(invoice.id, 0),
        lines=len(invoice.lines), updated_at=invoice.updated_at,
        created_at=invoice.created_at, **payment_state(invoice, today),
    )


# -- what can be billed ------------------------------------------------------------------------


class MilestoneCandidate(BaseModel):
    id: str
    position: int
    title: str
    amount: Money
    due_on: date | None
    status: MilestoneStatus
    change_order_number: int | None
    billable: bool
    reason: str


class UsageCandidate(BaseModel):
    id: str
    description: str
    kind: UsageKind
    occurred_on: date
    quantity: float
    unit_cost: Money
    amount: Money
    currency: Currency
    project_id: str | None
    billable: bool
    reason: str


class ContractCandidate(BaseModel):
    contract_total: Money
    invoiced: Money
    remaining: Money


class OfferCandidate(BaseModel):
    id: str
    label: str
    currency: Currency
    price_type: PriceType
    price: Money | None


class Billable(BaseModel):
    account_id: str
    project_id: str | None
    currency: Currency
    tax_rate_pct: Percent
    milestones: list[MilestoneCandidate]
    contract: ContractCandidate | None
    usage: list[UsageCandidate]
    offers: list[OfferCandidate]


def _project_tax_rate(store: BusinessStore, project: Any) -> Decimal:
    version = store.first("proposal_versions", proposal_id=project.proposal_id, version=project.proposal_version)
    return version.tax_rate_pct if version is not None else Decimal(0)


def _default_tax(store: BusinessStore, project: Any) -> Decimal:
    return _project_tax_rate(store, project) if project is not None else store.get_settings().billing.default_tax_rate_pct


def _change_order_numbers(store: BusinessStore, project_id: str) -> dict[str, int]:
    """The title a change order's milestone gets (``changes.apply_change``) to the change order's number."""
    orders, _ = store.find("change_orders", Query(where={"project_id": project_id, "status": "approved"}, limit=500, order_by="number"))
    return {f"Change order {order.number}: {order.title}"[:200]: order.number for order in orders}


@router.get("/invoices/billable", response_model=Billable)
def billable(account_id: str | None = None, project_id: str | None = None, context: TenantContext = Depends(tenant_context)) -> Billable:
    """What could go on the next invoice of this client or project, and what is already billed."""
    read(context)
    with open_business_store(context) as store:
        project = get_or_404(store, "projects", project_id, "Project") if project_id else None
        if project is None and not account_id:
            raise http_error(422, "Choose the client account or the project to invoice.")
        if project is not None and account_id and account_id != project.account_id:
            raise http_error(422, "The project belongs to another account.")
        account = get_or_404(store, "accounts", project.account_id if project else account_id, "Account")
        billed = billed_sources(store, account.id)
        invoices_by_id: dict[str, Invoice] = {}

        def billed_on(key: tuple[str, str]) -> str:
            invoice_id = billed.get(key)
            if invoice_id is None:
                return ""
            invoices_by_id.setdefault(invoice_id, store.get("invoices", invoice_id))
            found = invoices_by_id[invoice_id]
            return f"Already on invoice {found.number}." if found and found.number else "Already on a draft invoice."

        milestones: list[MilestoneCandidate] = []
        contract = None
        if project is not None:
            orders = _change_order_numbers(store, project.id)
            rows, _ = store.find("milestones", Query(where={"project_id": project.id}, order_by="position", descending=False, limit=100))
            for item in rows:
                reason = ""
                if item.paid_amount is not None:
                    reason = "Already marked paid."
                elif item.amount <= 0:
                    reason = "The amount is zero."
                else:
                    reason = billed_on(("milestone", item.id))
                milestones.append(MilestoneCandidate(id=item.id, position=item.position, title=item.title, amount=item.amount, due_on=item.due_on,
                                                     status=item.status, change_order_number=orders.get(item.title), billable=not reason, reason=reason))
            if not rows:
                done = contract_billed(store, project.id)
                contract = ContractCandidate(contract_total=project.contract_total, invoiced=done, remaining=money(max(project.contract_total - done, Decimal(0))))
        scope = {"account_id": account.id} | ({"project_id": project.id} if project else {})
        entries, _ = store.find("usage_ledger", Query(where={**scope, "billable": True}, order_by="occurred_on", limit=MAX_SCAN))
        currency = project.currency if project else None
        usage = []
        for entry in entries:
            reason = billed_on(("usage", entry.id))
            if not reason and currency is not None and entry.currency != currency:
                reason = f"In {entry.currency.value}, not {currency.value}."
            usage.append(UsageCandidate(id=entry.id, description=entry.description, kind=entry.kind, occurred_on=entry.occurred_on, quantity=float(entry.quantity),
                                        unit_cost=entry.unit_cost, amount=entry.amount, currency=entry.currency, project_id=entry.project_id,
                                        billable=not reason, reason=reason))
        offers: list[OfferCandidate] = []
        if project is None:
            active, _ = store.find("offers", Query(where={"status": OfferStatus.ACTIVE}, limit=100, order_by="activated_at"))
            for offer in active:
                product = store.get("products", offer.product_id)
                offers.append(OfferCandidate(id=offer.id, label=f"{product.name if product else 'Offer'} v{offer.version}", currency=offer.currency,
                                             price_type=offer.price_type, price=offer.price_min if offer.price_type == PriceType.FIXED else None))
        return Billable(
            account_id=account.id, project_id=project.id if project else None, currency=project.currency if project else Currency.SAR,
            tax_rate_pct=_default_tax(store, project), milestones=milestones, contract=contract, usage=usage, offers=offers,
        )


# -- create and edit ---------------------------------------------------------------------------


class ManualLine(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    kind: LineKind = LineKind.SERVICE
    quantity: Quantity = Decimal("1")
    unit_price: Money = Decimal("0")
    tax_rate_pct: Percent | None = None


class InvoiceFields(BaseModel):
    due_in_days: int | None = Field(default=None, ge=0, le=365)
    customer_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)
    payment_terms: str | None = Field(default=None, max_length=1000)
    bill_to_name: str | None = Field(default=None, max_length=200)
    bill_to_address: str | None = Field(default=None, max_length=1000)
    bill_to_email: str | None = Field(default=None, max_length=254)
    bill_to_tax_number: str | None = Field(default=None, max_length=60)


class InvoiceCreate(InvoiceFields):
    account_id: str | None = None
    project_id: str | None = None
    contact_id: str | None = None
    currency: Currency | None = None
    milestone_ids: list[str] = Field(default_factory=list, max_length=60)
    # A share of the contract total (tax included) for a project that has no milestones.
    contract_amount: Money | None = None
    usage_ids: list[str] = Field(default_factory=list, max_length=60)
    usage_markup_pct: Percent = Decimal("0")
    offer_id: str | None = None
    offer_quantity: Quantity = Decimal("1")
    offer_unit_price: Money | None = None
    lines: list[ManualLine] = Field(default_factory=list, max_length=60)
    tax_rate_pct: Percent | None = None


class InvoiceUpdate(InvoiceFields):
    lines: list[ManualLine] | None = Field(default=None, max_length=60)
    # Drop lines that came from the contract, the ledger or an offer, by position.
    remove_lines: list[int] = Field(default_factory=list, max_length=60)
    tax_rate_pct: Percent | None = None


def _manual(items: list[ManualLine], default_tax: Decimal) -> list[InvoiceLine]:
    return [InvoiceLine(description=_clean(item.description, 300), kind=item.kind, quantity=item.quantity, unit_price=item.unit_price,
                        tax_rate_pct=item.tax_rate_pct if item.tax_rate_pct is not None else default_tax) for item in items if _clean(item.description, 300)]


def _bill_to(store: BusinessStore, account: Any, contact_id: str | None) -> dict[str, str]:
    contact = None
    if contact_id:
        contact = store.get("contacts", contact_id)
        if contact is None or contact.account_id != account.id:
            raise http_error(422, "That contact does not belong to the account.")
    else:
        contacts, _ = store.find("contacts", Query(where={"account_id": account.id}, order_by="created_at", descending=False, limit=50))
        contact = next((item for item in contacts if item.email), None)
    place = ", ".join(part for part in (account.city, account.country) if part)
    return {"bill_to_name": account.name, "bill_to_address": place, "bill_to_email": (contact.email if contact and contact.email else "")}


def _source_lines(store: BusinessStore, payload: InvoiceCreate, account: Any, project: Any, currency: Currency, tax: Decimal, exclude: str | None = None) -> list[InvoiceLine]:
    """Lines from the contract, the ledger and an offer, checked: one project, one currency, billed once."""
    lines: list[InvoiceLine] = []
    billed = billed_sources(store, account.id, exclude=exclude)
    if payload.milestone_ids or payload.contract_amount is not None:
        if project is None:
            raise http_error(422, "Milestones and contract amounts belong to a project: choose one.")
        contract_rate = _project_tax_rate(store, project)
        for milestone_id in dict.fromkeys(payload.milestone_ids):
            item = store.get("milestones", milestone_id)
            if item is None or item.project_id != project.id:
                raise http_error(404, "Milestone not found in this project.")
            if item.paid_amount is not None:
                raise http_error(409, f"\"{item.title}\" is already marked paid.")
            if item.amount <= 0:
                raise http_error(409, f"\"{item.title}\" has no amount to invoice.")
            if ("milestone", item.id) in billed:
                raise http_error(409, f"\"{item.title}\" is already on an invoice.")
            lines.append(InvoiceLine(description=_clean(item.title, 300), kind=LineKind.SERVICE, tax_rate_pct=contract_rate, source=LineSource.MILESTONE,
                                     source_id=item.id, gross_total=item.amount))
        if payload.contract_amount is not None:
            rows = store.count("milestones", Query(where={"project_id": project.id}))
            if rows:
                raise http_error(409, "This project has milestones: invoice them instead of a share of the contract.")
            remaining = money(project.contract_total - contract_billed(store, project.id, exclude=exclude))
            if payload.contract_amount <= 0 or payload.contract_amount > remaining:
                raise http_error(422, f"Invoice between 0.01 and {currency.value} {remaining}: the part of the contract not yet invoiced.")
            lines.append(InvoiceLine(description=_clean(f"Contract: {project.name}", 300), kind=LineKind.SERVICE, tax_rate_pct=contract_rate,
                                     source=LineSource.CONTRACT, source_id=project.id, gross_total=payload.contract_amount))
    for usage_id in dict.fromkeys(payload.usage_ids):
        entry = store.get("usage_ledger", usage_id)
        if entry is None or entry.account_id != account.id or (project is not None and entry.project_id not in {None, project.id}):
            raise http_error(404, "Ledger entry not found for this client.")
        if not entry.billable:
            raise http_error(409, f"\"{entry.description}\" is not marked billable in the ledger.")
        if entry.currency != currency:
            raise http_error(409, f"\"{entry.description}\" is in {entry.currency.value}; this invoice is in {currency.value}.")
        if ("usage", entry.id) in billed:
            raise http_error(409, f"\"{entry.description}\" is already on an invoice.")
        price = money(entry.unit_cost * (Decimal(100) + payload.usage_markup_pct) / Decimal(100))
        lines.append(InvoiceLine(description=_clean(entry.description, 300), kind=LineKind.LABOR if entry.kind == UsageKind.LABOUR else LineKind.OTHER,
                                 quantity=entry.quantity, unit_price=price, tax_rate_pct=tax, source=LineSource.USAGE, source_id=entry.id))
    if payload.offer_id:
        offer = store.get("offers", payload.offer_id)
        if offer is None or offer.status != OfferStatus.ACTIVE:
            raise http_error(404, "Active offer not found.")
        if offer.currency != currency:
            raise http_error(409, f"The offer is priced in {offer.currency.value}; this invoice is in {currency.value}.")
        price = payload.offer_unit_price if payload.offer_unit_price is not None else (offer.price_min if offer.price_type == PriceType.FIXED else None)
        if price is None:
            raise http_error(422, "This offer has no fixed price. Enter the price to invoice (offer_unit_price).")
        product = store.get("products", offer.product_id)
        lines.append(InvoiceLine(description=_clean(f"{product.name if product else 'Offer'} (version {offer.version})", 300), kind=LineKind.SERVICE,
                                 quantity=payload.offer_quantity, unit_price=price, tax_rate_pct=tax, source=LineSource.OFFER, source_id=offer.id))
    return lines


@router.post("/invoices", response_model=InvoiceView, status_code=status.HTTP_201_CREATED)
def create_invoice(payload: InvoiceCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        project = get_or_404(store, "projects", payload.project_id, "Project") if payload.project_id else None
        account_id = project.account_id if project else payload.account_id
        if not account_id:
            raise http_error(422, "Choose the client account or the project to invoice.")
        if project is not None and payload.account_id and payload.account_id != project.account_id:
            raise http_error(422, "The project belongs to another account.")
        account = get_or_404(store, "accounts", account_id, "Account")
        currency = project.currency if project else (payload.currency or Currency.SAR)
        if project is not None and payload.currency not in (None, project.currency):
            raise http_error(422, f"The project is in {project.currency.value}.")
        profile = store.get_settings().billing
        tax = payload.tax_rate_pct if payload.tax_rate_pct is not None else _default_tax(store, project)
        lines = _source_lines(store, payload, account, project, currency, tax) + _manual(payload.lines, tax)
        if not lines:
            raise http_error(422, "Choose what to invoice: milestones, billable costs, an offer or a line you write.")
        if len(lines) > 60:
            raise http_error(422, "An invoice has at most 60 lines.")
        invoice = Invoice(
            id=new_id("binv"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=account.id, project_id=project.id if project else None,
            currency=currency, due_in_days=payload.due_in_days if payload.due_in_days is not None else profile.default_due_days,
            customer_reference=_clean(payload.customer_reference, 120), notes=_block(payload.notes, 1000), payment_terms=_block(payload.payment_terms, 1000),
            created_by=context.user_id, created_at=now, updated_at=now, **_bill_to(store, account, payload.contact_id),
        )
        overrides = {key: (_clean(value, TEXT_LIMITS[key]) if key != "bill_to_address" else _block(value, 1000)) for key, value in
                     payload.model_dump(include={"bill_to_name", "bill_to_address", "bill_to_email", "bill_to_tax_number"}, exclude_unset=True).items() if value is not None}
        invoice = apply_lines(invoice.model_copy(update=overrides), lines)
        store.add("invoices", invoice)
        record_activity(store, context, "invoice_drafted", f"Invoice drafted for {account.name} ({currency.value} {invoice.total}).", account_id=account.id,
                        project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "lines": len(lines)})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.created", invoice.id, metadata={"project_id": invoice.project_id, "lines": len(invoice.lines), "total": str(invoice.total)})
    return view


@router.get("/invoices", response_model=Page[InvoiceListItem])
def list_invoices(
    status_: list[InvoiceStatus] = Q(default=[], alias="status"), account_id: str | None = None, project_id: str | None = None, overdue: bool | None = None,
    q: str | None = Q(default=None, max_length=100), limit: int = 50, offset: int = 0, context: TenantContext = Depends(tenant_context),
) -> Page[InvoiceListItem]:
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {}
    if status_:
        where["status"] = set(status_)
    if account_id:
        where["account_id"] = account_id
    if project_id:
        where["project_id"] = project_id
    today = utc_now().date()
    names: dict[tuple[str, str], str | None] = {}
    with open_business_store(context) as store:
        waiting, _ = store.find("payment_notices", Query(where={"status": PaymentNoticeStatus.PENDING}, order_by="created_at", limit=MAX_SCAN))
        pending: dict[str, int] = {}
        for notice in waiting:
            pending[notice.invoice_id] = pending.get(notice.invoice_id, 0) + 1
        if overdue is True:
            where["status"] = {InvoiceStatus.SENT}
            items, _ = store.find("invoices", Query(where=where, order_by="due_date", descending=False, limit=MAX_SCAN))
            late = [item for item in items if item.due_date is not None and item.due_date < today]
            page = late[offset : offset + limit]
            rows = [list_item(store, item, today, names, pending) for item in page]
            return Page[InvoiceListItem](items=rows, total=len(late), limit=limit, offset=offset)
        items, total = store.find("invoices", Query(where=where, text=q, text_fields=("number", "bill_to_name", "customer_reference"), order_by="updated_at", limit=limit, offset=offset))
        rows = [list_item(store, item, today, names, pending) for item in items]
    return Page[InvoiceListItem](items=rows, total=total, limit=limit, offset=offset)


class CurrencyFigures(BaseModel):
    currency: Currency
    outstanding: Money
    overdue: Money
    overdue_count: int
    invoiced_this_month: Money
    collected_this_month: Money
    drafts: Money


class InvoiceSummary(BaseModel):
    counts: dict[str, int]
    currencies: list[CurrencyFigures]
    truncated: bool
    # Customer payment notices waiting for an owner or member to confirm or reject them.
    notices_pending: int
    notes: list[str]


@router.get("/invoices/summary", response_model=InvoiceSummary)
def invoice_summary(context: TenantContext = Depends(tenant_context)) -> InvoiceSummary:
    """Outstanding, late, invoiced and collected, one line per currency (never converted)."""
    read(context)
    today = utc_now().date()
    month = today.replace(day=1)
    with open_business_store(context) as store:
        items, total = store.find("invoices", Query(order_by="created_at", limit=MAX_SCAN))
        payments, _ = store.find("invoice_payments", Query(where={}, order_by="paid_on", limit=MAX_SCAN))
        notices_pending = store.count("payment_notices", Query(where={"status": PaymentNoticeStatus.PENDING}))
    by_invoice = {item.id: item for item in items}
    counts = {state.value: 0 for state in InvoiceStatus}
    figures: dict[Currency, dict[str, Any]] = {}

    def row(currency: Currency) -> dict[str, Any]:
        return figures.setdefault(currency, {"outstanding": Decimal(0), "overdue": Decimal(0), "overdue_count": 0, "invoiced": Decimal(0), "collected": Decimal(0), "drafts": Decimal(0)})

    for item in items:
        counts[item.status.value] += 1
        figure = row(item.currency)
        if item.status == InvoiceStatus.SENT:
            figure["outstanding"] += item.balance_due()
            if item.due_date is not None and item.due_date < today:
                figure["overdue"] += item.balance_due()
                figure["overdue_count"] += 1
        if item.status in {InvoiceStatus.SENT, InvoiceStatus.PAID} and item.issue_date is not None and item.issue_date >= month:
            figure["invoiced"] += item.total
        if item.status in OPEN_INVOICE_STATES:
            figure["drafts"] += item.total
    for payment in payments:
        invoice = by_invoice.get(payment.invoice_id)
        if invoice is not None and payment.paid_on >= month:
            row(invoice.currency)["collected"] += payment.amount
    return InvoiceSummary(
        counts=counts, truncated=total > len(items), notices_pending=notices_pending,
        currencies=[CurrencyFigures(currency=currency, outstanding=money(value["outstanding"]), overdue=money(value["overdue"]), overdue_count=value["overdue_count"],
                                    invoiced_this_month=money(value["invoiced"]), collected_this_month=money(value["collected"]), drafts=money(value["drafts"]))
                    for currency, value in sorted(figures.items())],
        notes=["Collected is the payments a person recorded on invoices; ANUM takes no payments.", "Amounts in different currencies are listed apart and never converted."],
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceView)
def get_invoice(invoice_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    read(context)
    with open_business_store(context) as store:
        return invoice_view(store, repository, context, get_or_404(store, "invoices", invoice_id, "Invoice"))


def _retire(repository: AnumRepository, context: TenantContext, invoice: Invoice) -> Invoice:
    """Cancel a waiting send request; the invoice goes back to a draft."""
    if invoice.status == InvoiceStatus.PENDING_APPROVAL:
        retire_approval(repository, context, invoice.approval_task_id, invoice.approval_id)
    return invoice.model_copy(update={"status": InvoiceStatus.DRAFT, "requested_hash": None, "approval_id": None, "approval_task_id": None,
                                      "requested_by": None, "requested_at": None, "content_hash": None, "seller": None})


@router.patch("/invoices/{invoice_id}", response_model=InvoiceView)
def update_invoice(invoice_id: str, payload: InvoiceUpdate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    """Edit a draft. Editing one waiting for approval lapses that request and returns it to a draft."""
    write(context)
    now = utc_now()
    changes = payload.model_dump(exclude_unset=True, exclude={"lines", "remove_lines", "tax_rate_pct"})
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        if invoice.status not in OPEN_INVOICE_STATES:
            raise http_error(409, f"The invoice is {invoice.status.value}; a sent invoice never changes. Void it and issue a new one.")
        invoice = _retire(repository, context, invoice)
        update: dict[str, Any] = {}
        for key, value in changes.items():
            if value is None and key != "due_in_days":
                value = ""
            if key in {"bill_to_address", "notes", "payment_terms"}:
                update[key] = _block(value, 1000)
            elif key == "due_in_days":
                if value is not None:
                    update[key] = value
            else:
                update[key] = _clean(value, TEXT_LIMITS[key])
        if "bill_to_name" in update and not update["bill_to_name"]:
            raise http_error(422, "Name the customer the invoice is made out to.")
        invoice = invoice.model_copy(update={**update, "updated_at": now})
        lines = list(invoice.lines)
        if payload.remove_lines:
            drop = set(payload.remove_lines)
            if any(index < 0 or index >= len(lines) for index in drop):
                raise http_error(422, "A line to remove does not exist.")
            lines = [item for index, item in enumerate(lines) if index not in drop]
        if payload.lines is not None:
            default_tax = payload.tax_rate_pct if payload.tax_rate_pct is not None else _default_tax(store, store.get("projects", invoice.project_id) if invoice.project_id else None)
            lines = [item for item in lines if item.source != LineSource.MANUAL] + _manual(payload.lines, default_tax)
        if len(lines) > 60:
            raise http_error(422, "An invoice has at most 60 lines.")
        invoice = apply_lines(invoice, lines)
        store.save("invoices", invoice)
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.updated", invoice_id, metadata={"fields": sorted(changes), "lines": len(invoice.lines)})
    return view


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(invoice_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Delete a draft. A numbered invoice is voided, never deleted."""
    write(context)
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        if invoice.status != InvoiceStatus.DRAFT:
            raise http_error(409, f"The invoice is {invoice.status.value.replace('_', ' ')}; only a draft is deleted." if invoice.status == InvoiceStatus.PENDING_APPROVAL
                             else f"The invoice is {invoice.status.value}; a numbered invoice is voided, not deleted.")
        store.delete("invoices", invoice_id)
    audit(repository, context, "business.invoice.deleted", invoice_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- send, void ----------------------------------------------------------------------------------


@router.post("/invoices/{invoice_id}/request-approval", response_model=InvoiceView)
def request_send(invoice_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    """Freeze the draft and ask an owner to approve sending it. Nothing leaves until they do."""
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        invoice_view(store, repository, context, invoice)  # a rejected request goes back to draft first
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        if invoice.status != InvoiceStatus.DRAFT:
            raise http_error(409, f"The invoice is {invoice.status.value.replace('_', ' ')}; only a draft is sent for approval.")
        invoice = invoice.model_copy(update={"seller": store.get_settings().billing})
        found = problems(invoice)
        if found:
            raise http_error(409, "Fix the invoice first: " + " ".join(found))
        account = get_or_404(store, "accounts", invoice.account_id, "Account")
        invoice = invoice.model_copy(update={"content_hash": invoice_hash(invoice)})
        task, approval = request_approval(
            repository, context, invoice_arguments(invoice, account.name), f"Send invoice to {account.name}: {invoice.currency.value} {invoice.total}",
            tool=SEND_INVOICE_TOOL, prompt=f"Issue an invoice of {invoice.currency.value} {invoice.total} to {account.name}.",
        )
        invoice = invoice.model_copy(update={"status": InvoiceStatus.PENDING_APPROVAL, "requested_hash": invoice.content_hash, "approval_id": approval.id,
                                             "approval_task_id": task.id, "requested_by": context.user_id, "requested_at": now, "decision_note": "", "updated_at": now})
        store.save("invoices", invoice)
        record_activity(store, context, "invoice_send_requested", f"Sending an invoice of {invoice.currency.value} {invoice.total} to {account.name} is waiting for approval.",
                        account_id=account.id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "approval_id": approval.id})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.send_requested", invoice_id, metadata={"approval_id": approval.id, "content_hash": invoice.content_hash})
    return view


@router.post("/invoices/{invoice_id}/cancel-request", response_model=InvoiceView)
def cancel_request(invoice_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    write(context)
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        if invoice.status != InvoiceStatus.PENDING_APPROVAL:
            raise http_error(409, "Only an invoice waiting for approval has a request to cancel.")
        invoice = _retire(repository, context, invoice).model_copy(update={"decision_note": "The request was cancelled.", "updated_at": utc_now()})
        store.save("invoices", invoice)
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.send_cancelled", invoice_id)
    return view


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceView)
def void_invoice(invoice_id: str, payload: Reason, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    """Cancel a sent invoice nobody paid. It keeps its number; its lines can be invoiced again."""
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        if invoice.status != InvoiceStatus.SENT:
            raise http_error(409, f"The invoice is {invoice.status.value.replace('_', ' ')}; only a sent invoice is voided." if invoice.status != InvoiceStatus.PAID
                             else "A paid invoice cannot be voided: remove its payments first if they were a mistake.")
        if invoice.amount_paid > 0:
            raise http_error(409, "The invoice has payments recorded. Remove them first, then void it.")
        if invoice.amount_credited > 0:
            raise http_error(409, "The invoice has credit notes. Void them first, then void the invoice.")
        invoice = invoice.model_copy(update={"status": InvoiceStatus.VOID, "voided_by": context.user_id, "voided_at": now, "void_reason": _clean(payload.reason, 300), "updated_at": now})
        store.save("invoices", invoice)
        record_activity(store, context, "invoice_voided", f"Invoice {invoice.number} voided: {invoice.void_reason}", account_id=invoice.account_id, project_id=invoice.project_id,
                        metadata={"invoice_id": invoice.id, "number": invoice.number})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.voided", invoice_id, metadata={"number": invoice.number})
    return view


# -- payments recorded by hand -------------------------------------------------------------------


class PaymentCreate(BaseModel):
    amount: Money
    paid_on: date
    method: PaymentMethod = PaymentMethod.BANK_TRANSFER
    reference: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=300)


@router.post("/invoices/{invoice_id}/payments", response_model=InvoiceView, status_code=status.HTTP_201_CREATED)
def record_payment(invoice_id: str, payload: PaymentCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    """Money that arrived outside ANUM. When the balance reaches zero the invoice is paid and its
    milestones are marked paid (cash collected)."""
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        invoice, payment = apply_payment(store, context, invoice, amount=payload.amount, paid_on=payload.paid_on, method=payload.method,
                                         reference=_clean(payload.reference, 120), note=_clean(payload.note, 300), now=now)
        record_activity(store, context, "invoice_payment_recorded", f"Payment of {invoice.currency.value} {payload.amount} recorded on invoice {invoice.number}.",
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "payment_id": payment.id})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.payment_recorded", invoice_id, metadata={"payment_id": payment.id, "amount": str(payload.amount), "paid": invoice.status.value == "paid"})
    return view


@router.delete("/invoices/{invoice_id}/payments/{payment_id}", response_model=InvoiceView)
def remove_payment(invoice_id: str, payment_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    """Take back a payment entered by mistake. A paid invoice becomes sent again."""
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        payment = get_or_404(store, "invoice_payments", payment_id, "Payment")
        if payment.invoice_id != invoice.id:
            raise http_error(404, "Payment not found")
        if invoice.status not in {InvoiceStatus.SENT, InvoiceStatus.PAID}:
            raise http_error(409, f"The invoice is {invoice.status.value}; its payments no longer change.")
        store.delete("invoice_payments", payment_id)
        update: dict[str, Any] = {"amount_paid": money(invoice.amount_paid - payment.amount), "updated_at": now}
        if invoice.status == InvoiceStatus.PAID:
            release_milestones(store, invoice, now)
            update.update({"status": InvoiceStatus.SENT, "paid_at": None, "marked_milestones": []})
        invoice = invoice.model_copy(update=update)
        store.save("invoices", invoice)
        record_activity(store, context, "invoice_payment_removed", f"A payment of {invoice.currency.value} {payment.amount} was removed from invoice {invoice.number}.",
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "payment_id": payment_id})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.payment_removed", invoice_id, metadata={"payment_id": payment_id})
    return view


# -- printable copy ----------------------------------------------------------------------------


PRINT_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}


@router.get("/invoices/{invoice_id}/print")
def print_invoice(invoice_id: str, lang: Literal["en", "ar"] = "en", context: TenantContext = Depends(tenant_context)) -> Response:
    """The invoice as a page that prints (Save as PDF in any browser). A draft says so on every page."""
    read(context)
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice")
        html = render_invoice(invoice, payments_of(store, invoice.id), store.get_settings().billing, lang, credit_notes_of(store, invoice.id))
    return Response(content=html, media_type="text/html; charset=utf-8", headers=PRINT_HEADERS)


# -- PDF copy ------------------------------------------------------------------------------------


def pdf_response(invoice: Invoice, content: bytes) -> Response:
    return Response(content=content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{invoice_filename(invoice)}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
    })


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """The invoice as a PDF file made on the server (Latin text only; the printable page covers Arabic). A draft says so on every page."""
    read(context)
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice")
        content = render_invoice_pdf(invoice, payments_of(store, invoice.id), credit_notes_of(store, invoice.id), store.get_settings().billing)
    return pdf_response(invoice, content)
