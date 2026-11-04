"""Records of the business books (docs/business-books.md). Migration ``0137_business_books``.

Amounts are ``Decimal`` in the record's own currency, rounded once when they enter ANUM to the
currency's minor unit (``finance.money.quantize``, half to even). Different currencies are never
added: every report keeps a figure per currency, or converts it with a dated rate whose source is
shown (``books_report``). Money paid from a person's Finance ledger is never a record here: it is an
allocation row of ``business_ledger_allocations`` (docs/finance-links.md#business-allocations).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ..finance.money import quantize


class Section(StrEnum):
    COST_OF_SALES = "cost_of_sales"
    OPERATING_EXPENSE = "operating_expense"
    CAPITAL = "capital"


# An allocation row may also be left out of the books (a private payment allocated by mistake is removed in Finance;
# ``excluded`` is for one the business does not count, for example a deposit it holds for someone else).
TAG_SECTIONS = (*(item.value for item in Section), "excluded")


class Basis(StrEnum):
    ACCRUAL = "accrual"
    CASH = "cash"


class BillStatus(StrEnum):
    OPEN = "open"
    VOID = "void"


class Recurrence(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


RECURRENCE_MONTHS = {Recurrence.MONTHLY: 1, Recurrence.QUARTERLY: 3, Recurrence.YEARLY: 12}


class PaymentKind(StrEnum):
    RECORDED = "recorded"  # cash or a bank transfer outside ANUM, typed by a member (like an invoice payment by hand)
    ALLOCATION = "allocation"  # an allocation row of this business: paid from a member's own Finance ledger


class PaymentMethod(StrEnum):
    ALLOCATION = "allocation"
    BANK_TRANSFER = "bank_transfer"
    CASH = "cash"
    CARD = "card"
    CHEQUE = "cheque"
    OTHER = "other"


class AdjustmentKind(StrEnum):
    ACCRUAL = "accrual"  # a cost incurred and not yet billed (accrual view only); may reverse on a date
    PREPAYMENT = "prepayment"  # a cost paid ahead, spread over its months (accrual view only)
    CAPITAL_ITEM = "capital_item"  # an asset bought outside a bill: depreciated straight line over its months
    LOAN_RECEIVED = "loan_received"
    LOAN_REPAYMENT = "loan_repayment"
    LOAN_INTEREST = "loan_interest"
    OWNER_CONTRIBUTION = "owner_contribution"  # cash the owner put in (allocations paid by the owner count on their own)
    OWNER_DRAWING = "owner_drawing"
    OWNER_REIMBURSEMENT = "owner_reimbursement"  # the business paid back what a member paid for it (reimbursable allocations)


class IntercompanyKind(StrEnum):
    RECHARGE = "recharge"  # income for the side that charges, a cost for the other
    LOAN = "loan"  # a receivable for the lender, a debt for the borrower
    SETTLEMENT = "settlement"  # money paid between them, which lowers what one owes the other


class Direction(StrEnum):
    RECEIVABLE = "receivable"  # the other business owes this one
    PAYABLE = "payable"  # this business owes the other


class _Scoped(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    created_at: datetime


class BookSettings(_Scoped):
    base_currency: str
    country: str | None = None
    fiscal_year_start_month: int = 1
    basis: Basis = Basis.ACCRUAL
    tax_label: str = ""
    # Finance category of an allocation row -> P&L section (``operating_expense`` when unlisted).
    category_sections: dict[str, str] = Field(default_factory=dict)
    rate_max_age_days: int = 31
    version: int = 1
    updated_by: str
    updated_at: datetime


class CostCentre(_Scoped):
    code: str
    name: str
    archived: bool = False
    version: int = 1
    created_by: str
    updated_at: datetime


class Vendor(_Scoped):
    name: str
    name_key: str
    tax_number: str = ""
    country: str | None = None
    default_category: str | None = None
    default_section: Section | None = None
    notes: str = ""
    archived: bool = False
    version: int = 1
    created_by: str
    updated_at: datetime


class Bill(_Scoped):
    vendor_id: str
    reference: str = ""
    description: str
    bill_date: date
    due_date: date
    currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    category: str
    section: Section = Section.OPERATING_EXPENSE
    cost_centre_id: str | None = None
    project_id: str | None = None
    recurrence: Recurrence | None = None
    useful_life_months: int | None = None
    status: BillStatus = BillStatus.OPEN
    idem_key: str | None = None
    version: int = 1
    created_by: str
    updated_at: datetime
    voided_by: str | None = None
    voided_at: datetime | None = None
    void_reason: str = ""


class BillPayment(_Scoped):
    bill_id: str
    kind: PaymentKind
    allocation_id: str | None = None
    amount: Decimal
    currency: str
    paid_on: date
    method: PaymentMethod
    reference: str = ""
    note: str = ""
    recorded_by: str
    idem_key: str | None = None
    voided_by: str | None = None
    voided_at: datetime | None = None
    void_reason: str = ""


class AllocationTag(_Scoped):
    allocation_id: str
    cost_centre_id: str | None = None
    section: str | None = None
    note: str = ""
    version: int = 1
    updated_by: str
    updated_at: datetime


class Budget(_Scoped):
    name: str
    period_start: date
    months: int
    currency: str
    amount: Decimal
    section: Section | None = None
    category: str | None = None
    project_id: str | None = None
    cost_centre_id: str | None = None
    version: int = 1
    created_by: str
    updated_at: datetime


class Rate(_Scoped):
    base: str
    quote: str
    rate: Decimal
    as_of: date
    source: str
    created_by: str


class Adjustment(_Scoped):
    kind: AdjustmentKind
    entry_date: date
    currency: str
    amount: Decimal
    section: Section | None = None
    category: str | None = None
    cost_centre_id: str | None = None
    project_id: str | None = None
    description: str
    start_month: date | None = None
    months: int | None = None
    reverse_on: date | None = None
    status: str = "active"
    idem_key: str | None = None
    created_by: str
    voided_by: str | None = None
    voided_at: datetime | None = None
    void_reason: str = ""


class Intercompany(_Scoped):
    pair_id: str
    counterparty_workspace_id: str
    counterparty_name: str = ""
    kind: IntercompanyKind
    direction: Direction
    entry_date: date
    currency: str
    amount: Decimal
    description: str
    status: str = "active"
    created_by: str
    voided_by: str | None = None
    voided_at: datetime | None = None
    void_reason: str = ""


KINDS: dict[str, type[BaseModel]] = {
    "cost_centres": CostCentre,
    "vendors": Vendor,
    "bills": Bill,
    "bill_payments": BillPayment,
    "allocation_tags": AllocationTag,
    "budgets": Budget,
    "rates": Rate,
    "adjustments": Adjustment,
    "intercompany": Intercompany,
}

TABLES: dict[str, str] = {
    "cost_centres": "business_book_cost_centres",
    "vendors": "business_book_vendors",
    "bills": "business_book_bills",
    "bill_payments": "business_book_bill_payments",
    "allocation_tags": "business_book_allocation_tags",
    "budgets": "business_book_budgets",
    "rates": "business_book_rates",
    "adjustments": "business_book_adjustments",
    "intercompany": "business_book_intercompany",
}


MONEY_FIELDS = ("subtotal", "tax", "total", "amount")


def dump(record: BaseModel, *hidden: str) -> dict[str, Any]:
    """A record for the API: amounts as exact strings in the currency's minor unit, never the scope columns or the
    idempotency key."""
    data = record.model_dump(mode="json", exclude={"tenant_id", "workspace_id", "idem_key", *hidden})
    currency = getattr(record, "currency", None)
    for name in MONEY_FIELDS:
        value = getattr(record, name, None)
        if isinstance(value, Decimal) and currency:
            data[name] = str(quantize(value, currency))
    if isinstance(getattr(record, "rate", None), Decimal):
        data["rate"] = format(record.rate.normalize(), "f")  # type: ignore[attr-defined]
    return data
