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

from pydantic import BaseModel, Field

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
    TRANSFER = "transfer"
    GROCERIES = "groceries"
    DINING = "dining"
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
    CASH = "cash"
    OTHER = "other"


class CategorySource(StrEnum):
    RULE = "rule"  # a built-in rule matched
    OWNER_RULE = "owner_rule"  # one of the owner's own rules matched
    MANUAL = "manual"  # the owner picked it
    NONE = "none"  # nothing matched: "other"


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
