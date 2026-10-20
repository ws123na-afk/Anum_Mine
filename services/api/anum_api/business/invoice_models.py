"""Records of invoices and the customer portal (docs/business.md#invoices, docs/business-portal.md).

Money is a two-place ``Decimal`` rounded half up (``models.money``). An invoice is built from
priced lines; every figure on it is computed by the server from those lines, never typed.
Once it is sent, its lines, totals and the seller and buyer details never change.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .models import BillingProfile, Currency, LineKind, Money, Percent, Quantity, Scoped, money


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    SENT = "sent"
    PAID = "paid"
    VOID = "void"


OPEN_INVOICE_STATES = frozenset({InvoiceStatus.DRAFT, InvoiceStatus.PENDING_APPROVAL})
ISSUED_INVOICE_STATES = frozenset({InvoiceStatus.SENT, InvoiceStatus.PAID, InvoiceStatus.VOID})


class LineSource(StrEnum):
    """Where a line came from. Milestone, contract and usage lines are billed once."""

    MANUAL = "manual"
    OFFER = "offer"
    MILESTONE = "milestone"
    CONTRACT = "contract"
    USAGE = "usage"


class InvoiceLine(BaseModel):
    """One priced line. ``unit_price`` is net of tax. A line with ``gross_total`` is a tax
    inclusive amount from a contract (a milestone or a share of the contract total): its
    net and tax are split from that figure so the invoice reconciles to the contract."""

    description: str = Field(min_length=1, max_length=300)
    kind: LineKind = LineKind.SERVICE
    quantity: Quantity = Decimal("1")
    unit_price: Money = Decimal("0")
    tax_rate_pct: Percent = Decimal("0")
    source: LineSource = LineSource.MANUAL
    source_id: str | None = Field(default=None, max_length=80)
    gross_total: Money | None = None
    # Computed by the server (``price_line``); whatever a client sends is replaced.
    net: Money = Decimal("0")
    tax: Money = Decimal("0")
    total: Money = Decimal("0")


def price_line(line: InvoiceLine) -> InvoiceLine:
    """The line with its net, tax and total worked out in Decimal."""
    rate = line.tax_rate_pct
    if line.gross_total is not None:
        total = money(line.gross_total)
        net = money(total * Decimal(100) / (Decimal(100) + rate)) if rate > 0 else total
        return line.model_copy(update={"net": net, "tax": money(total - net), "total": total, "quantity": Decimal("1"), "unit_price": net})
    net = money(line.quantity * line.unit_price)
    tax = money(net * rate / Decimal(100))
    return line.model_copy(update={"net": net, "tax": tax, "total": money(net + tax)})


class PaymentMethod(StrEnum):
    BANK_TRANSFER = "bank_transfer"
    CASH = "cash"
    CARD = "card"
    CHEQUE = "cheque"
    OTHER = "other"


class Invoice(Scoped):
    account_id: str
    project_id: str | None = None
    status: InvoiceStatus = InvoiceStatus.DRAFT
    currency: Currency = Currency.SAR
    lines: list[InvoiceLine] = Field(default_factory=list, max_length=60)
    subtotal: Money = Decimal("0")
    tax: Money = Decimal("0")
    total: Money = Decimal("0")
    amount_paid: Money = Decimal("0")
    # Credit notes in force (the sum of the credit notes that are not void).
    amount_credited: Money = Decimal("0")
    due_in_days: int = Field(default=14, ge=0, le=365)
    # Assigned when the owner approves sending it: the next number of the year, never reused.
    seq: int | None = None
    number: str | None = Field(default=None, max_length=40)
    issue_date: date | None = None
    due_date: date | None = None
    customer_reference: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=1000)
    payment_terms: str = Field(default="", max_length=1000)
    bill_to_name: str = Field(default="", max_length=200)
    bill_to_address: str = Field(default="", max_length=1000)
    bill_to_email: str = Field(default="", max_length=254)
    bill_to_tax_number: str = Field(default="", max_length=60)
    # The seller details as they were when the invoice was sent.
    seller: BillingProfile | None = None
    content_hash: str | None = None
    requested_hash: str | None = None
    approval_id: str | None = None
    approval_task_id: str | None = None
    requested_by: str | None = None
    requested_at: datetime | None = None
    sent_by: str | None = None
    sent_at: datetime | None = None
    paid_at: datetime | None = None
    voided_by: str | None = None
    voided_at: datetime | None = None
    void_reason: str = Field(default="", max_length=300)
    # Milestones this invoice marked paid when it was paid in full (undone if a payment is removed).
    marked_milestones: list[str] = Field(default_factory=list, max_length=60)
    decision_note: str = Field(default="", max_length=500)
    created_by: str

    def balance_due(self) -> Decimal:
        return money(self.total - self.amount_paid - self.amount_credited)


class InvoicePayment(Scoped):
    """Money a person says arrived. ANUM collects nothing and moves no money."""

    invoice_id: str
    amount: Money
    paid_on: date
    method: PaymentMethod = PaymentMethod.BANK_TRANSFER
    reference: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=300)
    recorded_by: str


class CreditNoteStatus(StrEnum):
    ISSUED = "issued"
    VOID = "void"


class CreditNote(Scoped):
    """Money an owner takes off a sent invoice (docs/business.md#credit-notes). The invoice itself never
    changes; it carries the running total of its credit notes. ``amount`` is tax inclusive and is split
    into ``subtotal`` and ``tax`` by the server in the invoice's own proportion. Numbered
    ``<invoice number>-CN<n>``; a mistake is voided, never deleted."""

    invoice_id: str
    account_id: str
    seq: int = Field(ge=1)
    number: str = Field(max_length=60)
    status: CreditNoteStatus = CreditNoteStatus.ISSUED
    currency: Currency
    amount: Money
    subtotal: Money
    tax: Money
    reason: str = Field(min_length=3, max_length=300)
    issue_date: date
    issued_by: str
    voided_by: str | None = None
    voided_at: datetime | None = None
    void_reason: str = Field(default="", max_length=300)


class PaymentNoticeStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class PaymentNotice(Scoped):
    """What a customer says they paid, reported from the portal link. It moves no money and changes no
    balance: an owner confirms it (which records an ordinary payment) or rejects it."""

    invoice_id: str
    account_id: str
    access_id: str
    contact_id: str
    payer_name: str = Field(min_length=2, max_length=160)
    amount: Money
    paid_on: date
    method: PaymentMethod = PaymentMethod.BANK_TRANSFER
    reference: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=300)
    status: PaymentNoticeStatus = PaymentNoticeStatus.PENDING
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str = Field(default="", max_length=300)
    payment_id: str | None = None


class InvoiceCounter(Scoped):
    """The last number issued in a calendar year (one row per workspace and year)."""

    year: int = Field(ge=2000, le=9999)
    last_seq: int = Field(default=0, ge=0)


# -- the customer portal ---------------------------------------------------------------------------


class PortalAccessStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class PortalAccess(Scoped):
    """A customer contact's access link. Only the SHA-256 of the secret is stored; the link
    itself is shown once, when an owner creates it."""

    account_id: str
    contact_id: str
    contact_name: str = Field(max_length=160)
    status: PortalAccessStatus = PortalAccessStatus.ACTIVE
    token_hash: str = Field(min_length=64, max_length=64)
    can_decide: bool = True
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    created_by: str


class DecisionSubject(StrEnum):
    PROPOSAL = "proposal"
    CHANGE_ORDER = "change_order"


class PortalDecision(Scoped):
    """What a customer decided about one exact version: the hash they were shown. It is a
    record for the owner, who still confirms the acceptance in Approvals."""

    access_id: str
    account_id: str
    contact_id: str
    subject_kind: DecisionSubject
    subject_id: str
    version: int | None = None
    content_hash: str = Field(min_length=64, max_length=64)
    decision: Literal["approved", "declined"]
    signer_name: str = Field(min_length=2, max_length=160)
    note: str = Field(default="", max_length=500)
    decided_at: datetime


class PortalDecisionSummary(BaseModel):
    """A customer's decision as the owner sees it on the proposal or change order."""

    decision: Literal["approved", "declined"]
    signer_name: str
    note: str
    decided_at: datetime
    version: int | None = None
    content_hash: str
    # True when the decision is about the version that is waiting for the owner now.
    current: bool = False
