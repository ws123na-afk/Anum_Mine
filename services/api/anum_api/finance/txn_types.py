"""Transaction types: what a transaction is, separate from its category (docs/finance.md#transaction-types).

The type decides how a row counts (``flows.py`` reads it); the category says what the money was for. Every
type has one counting rule:

* ``income`` is income (a negative ``income`` row, a salary reversal, lowers it).
* ``expense``, ``bank_fee``, ``interest`` (charged) and ``tax`` are spending.
* ``refund`` and ``reversal`` lower the spending of their category when it is a spending category; a refund
  that found no purchase (category ``refunds``) is its own line; a reversal filed as income lowers income.
* ``cashback`` is its own line.
* ``internal_transfer``, ``card_payment``, ``loan_received``, ``loan_principal``, ``investment_contribution``,
  ``investment_withdrawal``, ``owner_contribution``, ``owner_drawing``, ``reimbursement``,
  ``receivable_collection``, ``payable_settlement`` and ``fx_exchange`` move money the person already has or
  owes: never income and never spending.
* ``uncategorized`` is money no rule recognised: held for review and counted as neither, with a count so
  summaries say how many rows were left out (never a silent zero).

Rows stored before types existed have no stored type: :func:`derive` reads it from the category, its source,
the sign and the account kind. The rule is deterministic, so every reader gets the same answer, and it is the
backfill: a row gets its stored type the next time it is saved. Pure: no store, clock or network.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import AccountKind, Category, CategorySource, Transaction, TransactionType, TypeSource

T = TransactionType
LIABILITY_KINDS = frozenset({AccountKind.CARD, AccountKind.LOAN})
# Categories that are not spending (``flows.NOT_SPEND``; defined here too so this module stays import-free of flows).
NOT_SPEND_CATEGORIES = frozenset({Category.TRANSFER, Category.INCOME, Category.CASHBACK, Category.REFUNDS})
# Types that move money the person already has or owes: never income, never spending.
TRANSFER_TYPES = frozenset({
    T.INTERNAL_TRANSFER, T.CARD_PAYMENT, T.LOAN_RECEIVED, T.LOAN_PRINCIPAL, T.INVESTMENT_CONTRIBUTION, T.INVESTMENT_WITHDRAWAL,
    T.OWNER_CONTRIBUTION, T.OWNER_DRAWING, T.REIMBURSEMENT, T.RECEIVABLE_COLLECTION, T.PAYABLE_SETTLEMENT, T.FX_EXCHANGE,
})
SPEND_TYPES = frozenset({T.EXPENSE, T.BANK_FEE, T.INTEREST, T.TAX})
# What a held row's confidence is below which the register shows it for review.
REVIEW_BELOW = Decimal("0.6")
LEGACY = "legacy_category"  # the reason code of a type derived from an older row's category


@dataclass(frozen=True)
class Typed:
    """A type and why: what decided it, how sure it is (0 to 1) and a short machine reason code."""

    type: TransactionType
    source: TypeSource
    confidence: Decimal
    reason: str


def _typed(kind: TransactionType, source: TypeSource, confidence: str, reason: str) -> Typed:
    return Typed(kind, source, Decimal(confidence), reason)


def derive(category: Category, amount: Decimal, account_kind: AccountKind | None = None,
           source: CategorySource | None = None) -> Typed:
    """The type of a row with no stored type, from what older rows do hold (the backfill rule).

    It counts every row as before, except the two the November 2026 audit found wrong (C1, C2): money in that
    no rule recognised (``income`` by no rule) and money out that no rule recognised (``other`` by no rule)
    are ``uncategorized``, no longer income or spending."""
    chosen = source in (CategorySource.MANUAL, CategorySource.OWNER_RULE)
    by = TypeSource.OWNER if source == CategorySource.MANUAL else TypeSource.RULE
    liability_credit = amount > 0 and account_kind in LIABILITY_KINDS
    if category == Category.TRANSFER:
        return _typed(T.CARD_PAYMENT if liability_credit else T.INTERNAL_TRANSFER, by, "0.8", LEGACY)
    if category == Category.INCOME:
        if chosen:
            return _typed(T.INCOME, by, "1", LEGACY)
        if liability_credit:
            return _typed(T.CARD_PAYMENT, by, "0.8", LEGACY)  # a card or loan payment received from the person's own bank
        if source == CategorySource.NONE and amount > 0:
            return _typed(T.UNCATEGORIZED, TypeSource.RULE, "0", "no_rule_matched")
        return _typed(T.INCOME, by, "0.8", LEGACY)
    if category == Category.CASHBACK:
        return _typed(T.CASHBACK, by, "0.9", LEGACY)
    if category == Category.REFUNDS:
        return _typed(T.REFUND, by, "0.8", LEGACY)
    if category == Category.INTEREST:
        if amount > 0:
            return _typed(T.REFUND if liability_credit else T.INCOME, by, "0.8", LEGACY)
        return _typed(T.INTEREST, by, "0.8", LEGACY)
    if source == CategorySource.NONE and category == Category.OTHER:
        return _typed(T.UNCATEGORIZED, TypeSource.RULE, "0", "no_rule_matched")
    if category == Category.FEES:
        return _typed(T.BANK_FEE if amount < 0 else T.REFUND, by, "0.8", LEGACY)
    # A spending category: money out is spending, money in gives some back (a merchant's credit).
    return _typed(T.EXPENSE if amount <= 0 else T.REFUND, by, "0.8", LEGACY)


def typed(t: Transaction, account_kind: AccountKind | None = None) -> Typed:
    """The row's type: the stored one, else the one :func:`derive` reads from its category."""
    if t.transaction_type is not None:
        return Typed(t.transaction_type, t.type_source or TypeSource.RULE,
                     t.type_confidence if t.type_confidence is not None else Decimal(1), t.type_reason or "")
    return derive(t.category, t.amount, account_kind, t.category_source)


def effective(t: Transaction, account_kind: AccountKind | None = None) -> TransactionType:
    return typed(t, account_kind).type


def fill(t: Transaction, account_kind: AccountKind | None = None) -> Transaction:
    """``t`` with its type stored (a row from before types existed gets the derived one). Changes ``t`` in place."""
    if t.transaction_type is None:
        found = derive(t.category, t.amount, account_kind, t.category_source)
        t.transaction_type, t.type_source, t.type_confidence, t.type_reason = found.type, found.source, found.confidence, found.reason
    return t


def apply(t: Transaction, found: Typed) -> Transaction:
    t.transaction_type, t.type_source, t.type_confidence, t.type_reason = found.type, found.source, found.confidence, found.reason[:40]
    return t


def owner_locked(t: Transaction) -> bool:
    """True when the owner chose this row's category or type: no machine may change either (audit C4)."""
    return t.category_source == CategorySource.MANUAL or t.type_source == TypeSource.OWNER


def for_category(category: Category, amount: Decimal, account_kind: AccountKind | None, source: TypeSource,
                 confidence: str = "1", reason: str = "category") -> Typed:
    """The type a category the owner (or a rule) chose stands for, by the row's sign and account."""
    liability_credit = amount > 0 and account_kind in LIABILITY_KINDS
    if category == Category.TRANSFER:
        kind = T.CARD_PAYMENT if liability_credit else T.INTERNAL_TRANSFER
    elif category == Category.INCOME:
        kind = T.INCOME
    elif category == Category.CASHBACK:
        kind = T.CASHBACK
    elif category == Category.REFUNDS:
        kind = T.REFUND
    elif category == Category.INTEREST:
        kind = (T.REFUND if liability_credit else T.INCOME) if amount > 0 else T.INTEREST
    elif category == Category.FEES:
        kind = T.BANK_FEE if amount < 0 else T.REFUND
    else:
        kind = T.EXPENSE if amount <= 0 else T.REFUND
    return _typed(kind, source, confidence, reason)


def default_category(kind: TransactionType, current: Category, amount: Decimal) -> Category:
    """The category a type the owner picked files the row under, when the owner did not pick a category too."""
    if kind == T.INCOME:
        return current if current in (Category.INCOME, Category.INTEREST) else Category.INCOME
    if kind in TRANSFER_TYPES:
        return Category.TRANSFER
    if kind == T.CASHBACK:
        return Category.CASHBACK
    if kind == T.BANK_FEE:
        return Category.FEES
    if kind == T.INTEREST:
        return Category.INTEREST
    if kind == T.REFUND:
        return current if current not in (Category.INCOME, Category.TRANSFER, Category.CASHBACK) else Category.REFUNDS
    if kind in (T.EXPENSE, T.TAX):
        return current if current not in NOT_SPEND_CATEGORIES else Category.OTHER
    return current


__all__ = ["LEGACY", "LIABILITY_KINDS", "REVIEW_BELOW", "SPEND_TYPES", "TRANSFER_TYPES", "Typed", "apply", "default_category", "derive",
           "effective", "fill", "for_category", "owner_locked", "typed"]
