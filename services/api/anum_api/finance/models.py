"""The finance ledger model (docs/finance.md#data-model).

Every record belongs to one tenant, one workspace and one person (``owner_user_id``):
finance is personal, so a workspace's other members, owners included, never see it.
Amounts are ``Decimal`` rounded by ``money.quantize``; each sits next to its currency.
An account keeps at most the last four digits of its number, never the full number.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CURRENCY = "AED"
DEFAULT_TIMEZONE = "Asia/Dubai"
MAX_RULES = 100


class AccountKind(StrEnum):
    BANK = "bank"
    CARD = "card"
    CASH = "cash"
    LOAN = "loan"
    INVESTMENT = "investment"


class Category(StrEnum):
    """Built-in categories. Rules (``categories.py``) pick one; the owner can override it."""

    INCOME = "income"
    CASHBACK = "cashback"
    REFUNDS = "refunds"
    TRANSFER = "transfer"
    GROCERIES = "groceries"
    DINING = "dining"
    FOOD = "food"
    ROOM_RENT = "room_rent"
    TRANSPORT = "transport"
    FUEL = "fuel"
    UTILITIES = "utilities"
    TELECOM = "telecom"
    HOUSING = "housing"
    SHOPPING = "shopping"
    HEALTH = "health"
    EDUCATION = "education"
    ENTERTAINMENT = "entertainment"
    SUBSCRIPTIONS = "subscriptions"
    TRAVEL = "travel"
    INSURANCE = "insurance"
    FEES = "fees"
    INTEREST = "interest"  # interest or profit: received on savings (income) or charged on a card or loan (spend)
    CASH = "cash"
    OTHER = "other"


class CategorySource(StrEnum):
    RULE = "rule"  # a built-in rule matched
    OWNER_RULE = "owner_rule"  # one of the owner's own rules matched
    MANUAL = "manual"  # the owner picked it
    NONE = "none"  # nothing matched: "other"
    MATCH = "match"  # set by matching with another record (an own-transfer pair, a refund's purchase); never the owner's choice
    SYSTEM = "system"  # set by ANUM itself for another reason; never the owner's choice


class TransactionType(StrEnum):
    """What a transaction is, separate from what it was for (its category): the type decides how it counts
    (docs/finance.md#transaction-types). ``uncategorized`` is money ANUM could not recognise: held for review and
    counted as neither income nor spend."""

    INCOME = "income"
    EXPENSE = "expense"
    INTERNAL_TRANSFER = "internal_transfer"
    CARD_PAYMENT = "card_payment"
    LOAN_RECEIVED = "loan_received"
    LOAN_PRINCIPAL = "loan_principal"
    INTEREST = "interest"  # interest charged (spend); interest received is ``income`` with category ``interest``
    REFUND = "refund"
    CASHBACK = "cashback"
    BANK_FEE = "bank_fee"
    INVESTMENT_CONTRIBUTION = "investment_contribution"
    INVESTMENT_WITHDRAWAL = "investment_withdrawal"
    OWNER_CONTRIBUTION = "owner_contribution"
    OWNER_DRAWING = "owner_drawing"
    REIMBURSEMENT = "reimbursement"
    RECEIVABLE_COLLECTION = "receivable_collection"
    PAYABLE_SETTLEMENT = "payable_settlement"
    FX_EXCHANGE = "fx_exchange"
    TAX = "tax"
    REVERSAL = "reversal"
    UNCATEGORIZED = "uncategorized"


class TypeSource(StrEnum):
    """What decided a transaction's type."""

    RULE = "rule"  # a built-in or owner rule read the description, or the stored category (older rows)
    OWNER = "owner"  # the owner chose it (or chose the category it follows): never changed by a machine
    SYSTEM = "system"  # ANUM set it for another reason
    SMS = "sms"  # the kind an SMS or Apple Pay message described
    MATCH = "match"  # matching with another record (an own-transfer pair, a refund's purchase)


class Cadence(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    ONCE = "once"


class BillSource(StrEnum):
    MANUAL = "manual"
    DETECTED = "detected"  # suggested from recurring transactions, saved by the owner


class DebtKind(StrEnum):
    LOAN = "loan"
    CARD = "card"
    MORTGAGE = "mortgage"
    OTHER = "other"


class GoalPurpose(StrEnum):
    """What a goal is for (docs/finance.md#debts-and-goals): the planner keeps an emergency fund aside."""

    GENERAL = "general"
    EMERGENCY = "emergency"
    FAMILY = "family"  # money for family at home (sending money home)
    PURCHASE = "purchase"
    EDUCATION = "education"
    TRAVEL = "travel"


class CategoryRule(BaseModel):
    """The owner's own rule: a description containing ``match`` gets ``category``."""

    match: str = Field(min_length=2, max_length=80)
    category: Category


class _Owned(BaseModel):
    tenant_id: str
    workspace_id: str
    owner_user_id: str


class FinanceSettings(BaseModel):
    """Per person and workspace. UAE defaults; change them under Finance, Settings."""

    base_currency: str = DEFAULT_CURRENCY
    timezone: str = DEFAULT_TIMEZONE
    category_rules: list[CategoryRule] = Field(default_factory=list, max_length=MAX_RULES)
    updated_at: datetime | None = None


class Account(_Owned):
    id: str
    name: str
    kind: AccountKind
    currency: str
    institution: str | None = None
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    opening_balance: Decimal = Decimal(0)
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class Transaction(_Owned):
    id: str
    account_id: str
    posted_on: date
    amount: Decimal  # signed: money in is positive, money out negative
    currency: str
    description: str
    category: Category
    category_source: CategorySource
    notes: str | None = None
    import_id: str | None = None
    dedupe_hash: str
    created_at: datetime
    updated_at: datetime
    # The transaction type (docs/finance.md#transaction-types). None on rows stored before the type existed:
    # ``txn_types.typed`` derives it from the category, the sign and the account kind, deterministically.
    transaction_type: TransactionType | None = None
    type_source: TypeSource | None = None
    type_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    type_reason: str | None = Field(default=None, max_length=40)  # a short machine code, for example ``no_rule_matched``
    # Another of the person's transactions this one relates to: the purchase a refund gives back (a match when
    # ``type_confidence`` is high, a suggestion otherwise). Never a link to someone else's record.
    related_transaction_id: str | None = None


class ImportRecord(_Owned):
    id: str
    account_id: str
    filename: str
    preset: str
    date_format: str
    row_count: int
    imported_count: int
    duplicate_count: int
    error_count: int
    file_sha256: str
    created_at: datetime
    # The statement's balance check when it was imported (docs/finance.md#reconciliation): ``status`` ("tied",
    # "differs" or "unknown"), ``opening``, ``closing``, ``rows_total``, ``difference`` as decimal strings, and
    # ``not_in_ledger``: how many statement rows the ledger does not hold (held for review or left out by the person).
    reconciliation: dict[str, str | None] | None = None
    # What happened to every row (docs/finance.md#import-history), counted and listed (migration 0149_finance_import_rows):
    # attached to a record already there, held for review, left out by the person, left out by the reader, corrected.
    attached_count: int = 0
    held_count: int = 0
    skipped_count: int = 0
    dropped_count: int = 0
    corrected_count: int = 0
    # One item per statement row: line, posted_on, amount, description, disposition (imported, held_for_review,
    # attached_to_existing, duplicate_skipped, unticked, dropped_by_reader), reason and the record it went to.
    dispositions: list[dict[str, Any]] | None = None


class Bill(_Owned):
    id: str
    name: str
    amount: Decimal
    currency: str
    cadence: Cadence
    next_due_on: date
    category: Category = Category.UTILITIES
    account_id: str | None = None
    source: BillSource = BillSource.MANUAL
    active: bool = True
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class Debt(_Owned):
    id: str
    name: str
    kind: DebtKind
    balance: Decimal  # what is owed, a positive amount
    currency: str
    apr_percent: Decimal = Decimal(0)
    minimum_payment: Decimal = Decimal(0)
    due_day: int | None = Field(default=None, ge=1, le=31)
    account_id: str | None = None
    # What was owed when the person started tracking it (payoff progress). None: not entered.
    original_balance: Decimal | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class Goal(_Owned):
    id: str
    name: str
    target_amount: Decimal
    saved_amount: Decimal = Decimal(0)
    currency: str
    target_on: date | None = None
    account_id: str | None = None
    # Stage 3: what the person plans to put aside each month. None: safe-to-spend works it
    # out from the target date (what is left over the months left), or sets nothing aside.
    monthly_contribution: Decimal | None = None
    # A paused goal keeps what is saved; nothing is set aside for it until it is resumed.
    paused: bool = False
    purpose: GoalPurpose = GoalPurpose.GENERAL
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class Holding(_Owned):
    """A manual position: quantity and what it cost. No prices, no market data."""

    id: str
    account_id: str
    symbol: str
    name: str | None = None
    quantity: Decimal
    cost_basis: Decimal  # total paid, in ``currency``
    currency: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


# -- Stage 3: safe-to-spend settings --------------------------------------------------------


class CurrencyBuffer(BaseModel):
    """Money the person always keeps back in one currency (never counted as spendable)."""

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    amount: Decimal


class PlanSettings(BaseModel):
    """Per person and workspace: the safe-to-spend buffer and whether to show a converted total."""

    buffers: list[CurrencyBuffer] = Field(default_factory=list, max_length=20)
    # Show one total in the base currency too, only when every rate needed is recorded.
    combine_currencies: bool = False
    # A rate older than this many days is not used for a combined total.
    fx_max_age_days: int = Field(default=7, ge=1, le=60)
    updated_at: datetime | None = None


# -- Debts and budgets plan (docs/finance.md#debts-and-goals) ---------------------------------


class DebtStrategy(StrEnum):
    AVALANCHE = "avalanche"  # the highest rate first
    SNOWBALL = "snowball"  # the smallest balance first


class DebtPlan(BaseModel):
    """Per currency: how extra money goes to the debts and what the person plans to pay each month."""

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    strategy: DebtStrategy = DebtStrategy.AVALANCHE
    monthly_payment: Decimal | None = None  # None: the minimum payments only


class BudgetAlert(BaseModel):
    """How one Guardian category cap (a budget) alerts: at ``alert_percent`` of the cap and at 100 percent."""

    category: Category
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    alert_percent: int = Field(default=80, ge=50, le=99)
    alert_near: bool = True
    alert_over: bool = True


class InstallmentStatementEntry(BaseModel):
    """Statement-provided schedule, not evidence of a bank payment.

    This belongs to the signed-in person's Finance planning data. Payment matches
    are separately recorded in the canonical ledger and never invented here.
    """
    provider: str = Field(min_length=1, max_length=80)
    order_id: str = Field(min_length=1, max_length=160)
    merchant: str = Field(min_length=1, max_length=160)
    purchase_on: date | None = None
    due_on: date
    installment: int = Field(ge=1, le=120)
    total_installments: int = Field(ge=1, le=120)
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # ``refunded``: the provider's own statement marks this installment refunded or cancelled.
    source_status: str = Field(pattern=r"^(paid|scheduled|review|refunded)$")
    source_email: str | None = Field(default=None, max_length=500)
    review_notes: str | None = Field(default=None, max_length=500)
    # Set only by ANUM when the owner applies refund evidence (docs/finance.md#tamara-refunds): how much of
    # ``amount`` is no longer due. ``amount`` itself stays the original scheduled figure.
    refunded_amount: Decimal = Field(default=Decimal(0), ge=0)
    # A lower amount a later statement import reported for the same installment and due date (refund evidence).
    reported_amount: Decimal | None = Field(default=None, gt=0)


class InstallmentRefundNotice(BaseModel):
    """One refund or order-updated email from the provider, kept as evidence (never as money received)."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="Tamara", min_length=1, max_length=80)
    order_id: str = Field(min_length=1, max_length=160)
    amount: Decimal | None = Field(default=None, gt=0)  # None: the email names no amount
    currency: str = Field(default="SAR", pattern=r"^[A-Z]{3}$")
    full: bool = False  # the email says the whole order was refunded or cancelled
    received_on: date
    message_ref: str = Field(min_length=1, max_length=200)  # the provider message id, the dedupe key
    source_email: str | None = Field(default=None, max_length=500)


class InstallmentRefundChange(BaseModel):
    installment: int = Field(ge=1, le=120)
    previous_refunded: Decimal
    refunded: Decimal


class InstallmentRefundBillChange(BaseModel):
    bill_id: str
    previous_amount: Decimal
    previous_active: bool
    amount: Decimal
    active: bool


class InstallmentRefundRecord(BaseModel):
    """One owner-confirmed application of refund evidence to one order, kept so it can be undone."""

    id: str
    provider: str
    order_id: str
    currency: str
    applied_at: datetime
    applied_by: str
    refund_total: Decimal
    credit_back: Decimal
    evidence: list[str] = Field(default_factory=list, max_length=200)
    changes: list[InstallmentRefundChange] = Field(default_factory=list, max_length=120)
    bills: list[InstallmentRefundBillChange] = Field(default_factory=list, max_length=120)
    undone_at: datetime | None = None
    undone_by: str | None = None


class Planning(BaseModel):
    debt_plans: list[DebtPlan] = Field(default_factory=list, max_length=20)
    budget_alerts: list[BudgetAlert] = Field(default_factory=list, max_length=100)
    installment_entries: list[InstallmentStatementEntry] = Field(default_factory=list, max_length=3000)
    installment_refund_notices: list[InstallmentRefundNotice] = Field(default_factory=list, max_length=3000)
    installment_refunds: list[InstallmentRefundRecord] = Field(default_factory=list, max_length=2000)


# -- Stage 5: FX rates ------------------------------------------------------------------------


class FxRate(_Owned):
    """One recorded exchange rate: 1 ``base`` = ``rate`` ``quote`` on ``as_of``, from ``source``.

    Rates come from a connector (a real published source, stored with its date and name)
    or from the person, typed by hand (source ``manual``). ANUM never invents a rate."""

    id: str
    base: str
    quote: str
    rate: Decimal
    as_of: date
    source: str
    source_url: str | None = None
    connection_id: str | None = None
    fetched_at: datetime
