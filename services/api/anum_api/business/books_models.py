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
    DRAFT = "draft"  # made by a recurring bill set to "draft": not counted until a member approves it (migration 0138)


class Recurrence(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


RECURRENCE_MONTHS = {Recurrence.MONTHLY: 1, Recurrence.QUARTERLY: 3, Recurrence.YEARLY: 12}


class Frequency(StrEnum):
    """How often a recurring bill repeats (docs/business-books.md#recurring-bills)."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    DAYS = "days"  # every ``interval`` days
    WEEKS = "weeks"  # every ``interval`` weeks
    MONTHS = "months"  # every ``interval`` months


class TemplateStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class RateOrigin(StrEnum):
    MANUAL = "manual"  # typed by an owner with its source
    ECB = "ecb"  # fetched by the daily job from the European Central Bank (docs/business-books.md#automatic-exchange-rates)


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
    # Interest owed and interest paid are separate events (audit defect B5, migration 0148): ``loan_interest`` stays for
    # interest paid on the day it is charged.
    LOAN_INTEREST_ACCRUED = "loan_interest_accrued"  # a finance cost owed, not yet paid
    LOAN_INTEREST_PAID = "loan_interest_paid"  # paying interest already owed: cash, not a second cost


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
    policy_note: str = ""  # the owner's own accounting policy text, shown with the generated policy notes (migration 0148)
    # Bills and adjustments at or above this, in the base currency, need a supporting document (migration 0157_finance_evidence,
    # docs/business-books.md#supporting-documents-and-evidence). None: the "evidence missing" flag is off.
    evidence_threshold: Decimal | None = None
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
    # A bill made by a recurring bill: the template and the period it is for (one bill per template and period).
    template_id: str | None = None
    period_date: date | None = None
    # The bill's tax code (``tax_rules.TaxCode``, migration 0154); None for a bill recorded before tax codes: "unknown".
    tax_code: str | None = None
    # VAT the business accounts for itself on a reverse-charge purchase (the supplier charged none), worked out from the
    # business's rate table when the bill is saved; zero otherwise.
    reverse_charge_tax: Decimal = Decimal(0)


class BillTemplate(_Scoped):
    """A recurring bill: the daily job makes one normal bill per period (docs/business-books.md#recurring-bills)."""

    vendor_id: str
    reference: str = ""
    description: str
    currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    category: str
    section: Section = Section.OPERATING_EXPENSE
    cost_centre_id: str | None = None
    project_id: str | None = None
    useful_life_months: int | None = None
    frequency: Frequency
    interval: int = 1
    start_date: date
    due_days: int = 0
    end_date: date | None = None
    max_count: int | None = None
    create_as: str = "open"  # "open" or "draft"
    status: TemplateStatus = TemplateStatus.ACTIVE
    anchor_date: date  # the schedule counts from here (the start, or the first period after an edit or a resume)
    next_index: int = 0
    next_date: date | None = None  # None once ended
    created_count: int = 0
    last_period: date | None = None
    end_reason: str = ""  # "owner", "end_date" or "count"
    ended_at: datetime | None = None
    version: int = 1
    created_by: str
    updated_by: str
    updated_at: datetime


class RateFeed(_Scoped):
    """A business's opt-in to automatic ECB rates, and how the last fetch went."""

    enabled: bool = False
    currencies: list[str] = Field(default_factory=list)
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_as_of: date | None = None
    last_error: str = ""
    unavailable: list[str] = Field(default_factory=list)  # chosen currencies the ECB does not publish
    version: int = 1
    updated_by: str
    updated_at: datetime


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
    origin: RateOrigin = RateOrigin.MANUAL


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


# -- the journal (migration 0151_business_ledger, docs/business-books.md#the-journal) ---------------------------------


class AccountType(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


# What an account is: its type, the first digit of its code, and where it shows on the statements.
SUBTYPES: dict[str, tuple[AccountType, str]] = {
    "bank": (AccountType.ASSET, "1"), "receivable": (AccountType.ASSET, "1"), "tax_receivable": (AccountType.ASSET, "1"),
    "prepayment": (AccountType.ASSET, "1"), "current_asset": (AccountType.ASSET, "1"), "fixed_asset": (AccountType.ASSET, "1"),
    "accumulated_depreciation": (AccountType.ASSET, "1"),
    "payable": (AccountType.LIABILITY, "2"), "tax_payable": (AccountType.LIABILITY, "2"), "accrual": (AccountType.LIABILITY, "2"),
    "interest_payable": (AccountType.LIABILITY, "2"), "owed_to_members": (AccountType.LIABILITY, "2"), "loan": (AccountType.LIABILITY, "2"),
    "current_liability": (AccountType.LIABILITY, "2"),
    "capital": (AccountType.EQUITY, "3"), "drawings": (AccountType.EQUITY, "3"), "retained_earnings": (AccountType.EQUITY, "3"),
    "current_result": (AccountType.EQUITY, "3"),
    "revenue": (AccountType.REVENUE, "4"), "other_income": (AccountType.REVENUE, "4"),
    "cost_of_sales": (AccountType.EXPENSE, "5"), "operating_expense": (AccountType.EXPENSE, "6"), "depreciation": (AccountType.EXPENSE, "6"),
    "finance_cost": (AccountType.EXPENSE, "7"), "fx": (AccountType.EXPENSE, "7"),
}
PNL_TYPES = frozenset({AccountType.REVENUE, AccountType.EXPENSE})


class LedgerAccount(_Scoped):
    code: str
    name: str
    subtype: str
    system_key: str | None = None  # the account a posting rule uses (one per key); never archived
    category: str | None = None  # an expense or revenue category posted here instead of the default account
    archived: bool = False
    version: int = 1
    created_by: str
    updated_at: datetime

    @property
    def type(self) -> AccountType:
        return SUBTYPES[self.subtype][0]


class JournalEntry(_Scoped):
    entry_date: date  # the day it is posted on: the event's day, or the first open day when that day is in a closed period
    event_date: date  # the day the source document says
    # invoice, credit_note, invoice_payment, bill, bill_payment, allocation, adjustment, intercompany, manual, closing,
    # asset, loan, loan_repayment, revaluation (migration 0155)
    source_kind: str
    source_id: str | None = None
    event_key: str
    fingerprint: str
    description: str
    status: str = "posted"  # or "reversed"
    reverses_id: str | None = None
    reversed_by_id: str | None = None
    late: bool = False
    created_by: str


class JournalLine(_Scoped):
    entry_id: str
    line_no: int
    entry_date: date
    source_kind: str
    account_id: str
    debit: Decimal = Decimal(0)  # base currency
    credit: Decimal = Decimal(0)
    currency: str  # the original currency
    amount: Decimal  # the original amount, signed: positive on a debit line, negative on a credit line
    rate: Decimal | None = None  # base per unit of the original currency; None when the line is in the base currency
    rate_date: date | None = None
    rate_source: str | None = None
    category: str | None = None
    project_id: str | None = None
    cost_centre_id: str | None = None
    customer_id: str | None = None
    counterparty_workspace_id: str | None = None
    pair_id: str | None = None
    flow: str | None = None  # how a bank line shows in the cash-flow statement
    tax_code: str | None = None  # the tax code of a revenue, cost or VAT line (migration 0154); None before tax codes


class PostingIssue(_Scoped):
    event_key: str
    source_kind: str
    source_id: str | None = None
    event_date: date
    reason: str  # missing_rate, too_small or missing_closing_rate (a revaluation without the period end's rate)
    currency: str | None = None
    rate_date: date | None = None
    description: str = ""


class PeriodClose(_Scoped):
    period_start: date
    period_end: date
    kind: str  # month or year (the last month of a financial year)
    status: str = "closed"
    closed_by: str
    closing_entry_id: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)
    reopened_by: str | None = None
    reopened_at: datetime | None = None
    reopen_reason: str = ""


class Accountant(_Scoped):
    user_id: str
    added_by: str


# -- tax (migration 0154_business_tax, docs/business-tax.md) ---------------------------------------------------------------


class TaxSettings(_Scoped):
    """One business's VAT registration and its e-invoicing switch. ZATCA e-invoicing stays off until an owner turns it on;
    ``zatca_onboarding`` is only ever ``not_started`` in this release (no credentials are stored)."""

    jurisdiction: str  # tax_rules.Jurisdiction
    registered: bool = False
    registration_number: str = ""
    registration_date: date | None = None
    filing_frequency: str = "quarterly"  # tax_rules.Frequency
    period_start_month: int = 1  # the first month of a quarter cycle (1, 2 or 3)
    zatca_enabled: bool = False
    zatca_onboarding: str = "not_started"
    seller_crn: str = ""  # commercial registration number (the seller's other identifier in the e-invoice)
    seller_street: str = ""
    seller_building: str = ""
    seller_district: str = ""
    seller_city: str = ""
    seller_postal_code: str = ""
    version: int = 1
    updated_by: str
    updated_at: datetime


class TaxRate(_Scoped):
    """A row of a business's rate table: a code's rate from a day (to a day)."""

    jurisdiction: str
    code: str
    rate_pct: Decimal
    effective_from: date
    effective_to: date | None = None
    note: str = ""
    created_by: str


class ZatcaChain(_Scoped):
    """The e-invoice counter (ICV) and the hash of the last generated document of one business (one row per business)."""

    last_counter: int = 0
    last_hash: str = ""
    version: int = 1
    updated_at: datetime


class ZatcaDocument(_Scoped):
    """E-invoice data generated for one sent invoice or credit note: UBL 2.1 XML, its hash, its place in the chain and what
    the required-fields check found missing. Generated, never cleared or reported: ANUM is not onboarded with ZATCA."""

    source_kind: str  # invoice or credit_note
    source_id: str
    document_number: str
    type_code: str  # 388 tax invoice, 381 credit note
    subtype: str  # 0100000 standard, 0200000 simplified
    counter: int
    uuid: str
    invoice_hash: str
    previous_hash: str
    issue_date: date
    xml: str
    qr: str = ""
    missing: list[str] = Field(default_factory=list)
    status: str = "generated"
    created_by: str


# -- fixed assets and loans (migration 0155_books_assets_fx, docs/business-books.md#fixed-asset-register) ------------------


class DepreciationMethod(StrEnum):
    STRAIGHT_LINE = "straight_line"
    REDUCING_BALANCE = "reducing_balance"


class Asset(_Scoped):
    """One fixed asset, registered from the capital bill or capital-item adjustment that bought it (one asset per source).
    Cost, currency and acquisition date come from the source; the register adds how it is depreciated, its disposal and
    impairments. Its depreciation replaces the source's straight-line depreciation and posts under the source's event keys."""

    source_kind: str  # bill or adjustment
    source_id: str
    name: str
    category: str = "equipment"
    acquired_on: date
    currency: str
    cost: Decimal
    useful_life_months: int | None = None  # None: not depreciated until an owner or accountant sets it
    residual_value: Decimal = Decimal(0)
    method: DepreciationMethod = DepreciationMethod.STRAIGHT_LINE
    rate_pct: Decimal | None = None  # the annual rate of the reducing balance method
    depreciation_start: date  # the first month depreciated (a month's first day)
    status: str = "active"  # active or disposed
    disposed_on: date | None = None
    disposal_kind: str | None = None  # sale or write_off
    proceeds: Decimal = Decimal(0)
    version: int = 1
    created_by: str
    updated_at: datetime


class AssetImpairment(_Scoped):
    """An impairment loss on an asset: requested by one owner or accountant, posted only once another one approved it."""

    asset_id: str
    entry_date: date
    amount: Decimal
    currency: str
    reason: str
    status: str = "pending"  # pending, approved or rejected
    requested_by: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    version: int = 1


class LoanMethod(StrEnum):
    ANNUITY = "annuity"  # equal payments
    EQUAL_PRINCIPAL = "equal_principal"  # equal principal parts ("flat" principal), interest on the balance


class Loan(_Scoped):
    lender: str
    principal: Decimal
    currency: str
    annual_rate: Decimal  # percent a year
    start_date: date
    term_months: int
    frequency: str = "monthly"  # monthly, quarterly or yearly
    method: LoanMethod = LoanMethod.ANNUITY
    early_repayment: str = "reduce_term"  # reduce_term or reduce_payment: how the schedule is worked out after paying more
    received_adjustment_id: str | None = None  # an existing loan_received adjustment that already posts the money received
    status: str = "active"  # active or void
    idem_key: str | None = None
    version: int = 1
    created_by: str
    updated_at: datetime
    voided_by: str | None = None
    voided_at: datetime | None = None
    void_reason: str = ""


class LoanRepayment(_Scoped):
    """A repayment actually made. Split into interest (what had accrued since the last repayment) and principal when it is
    recorded; matched to the bill or the loan repayment adjustment that already records the cash, when there is one."""

    loan_id: str
    paid_on: date
    amount: Decimal
    currency: str
    interest: Decimal
    principal: Decimal
    bill_id: str | None = None
    adjustment_id: str | None = None
    status: str = "active"  # active or void
    idem_key: str | None = None
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
    "templates": BillTemplate,
    "rate_feeds": RateFeed,
    "ledger_accounts": LedgerAccount,
    "journal_entries": JournalEntry,
    "journal_lines": JournalLine,
    "posting_issues": PostingIssue,
    "closes": PeriodClose,
    "accountants": Accountant,
    "tax_settings": TaxSettings,
    "tax_rates": TaxRate,
    "zatca_chain": ZatcaChain,
    "zatca_documents": ZatcaDocument,
    "assets": Asset,
    "asset_impairments": AssetImpairment,
    "loans": Loan,
    "loan_repayments": LoanRepayment,
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
    "templates": "business_book_bill_templates",
    "rate_feeds": "business_book_rate_feeds",
    "ledger_accounts": "business_book_ledger_accounts",
    "journal_entries": "business_book_journal_entries",
    "journal_lines": "business_book_journal_lines",
    "posting_issues": "business_book_posting_issues",
    "closes": "business_book_closes",
    "accountants": "business_book_accountants",
    "tax_settings": "business_tax_settings",
    "tax_rates": "business_tax_rates",
    "zatca_chain": "business_zatca_chains",
    "zatca_documents": "business_zatca_documents",
    "assets": "business_book_assets",
    "asset_impairments": "business_book_asset_impairments",
    "loans": "business_book_loans",
    "loan_repayments": "business_book_loan_repayments",
}


MONEY_FIELDS = ("subtotal", "tax", "total", "amount", "reverse_charge_tax", "cost", "residual_value", "proceeds", "principal", "interest")


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
