"""How figures are counted: the one definition of spend, income, refunds, cashback and transfers
(docs/finance.md#how-figures-are-counted).

Every reader that totals money (the summary, the ledger dashboard, the advisor, budgets, the
Guardian and the web pages through the summary) asks this module, so a row is counted the same
way everywhere:

* **Transfer**: category ``transfer`` (own transfers, card payments, loan principal, money moved
  between accounts). Neither spend nor income, on either side.
* **Card or loan payment received**: money in on a card or loan account that a rule (or nothing)
  filed as ``income`` is the payment from the person's own bank, so it is a transfer. A category
  the person picked by hand or with their own rule is kept.
* **Income**: money in filed as ``income`` (a negative ``income`` row, a salary reversal, lowers it).
* **Cashback**: card rewards, separate from income and from spend.
* **Refund**: money in filed under a spending category (a refund that found its purchase, or a
  merchant's credit) lowers that category's spend; it is never income. Money in filed as
  ``refunds`` (no purchase found) is shown on its own line, also never income.
* **Spend**: money out in a spending category, net of the refunds in that category.
* **Uncategorized**: money in or out that no rule recognised (transaction type ``uncategorized``). Never
  income and never spend: it waits for review, and :class:`Figures` counts it so a summary says how many
  rows it left out.
* **The transaction type decides** (docs/finance.md#transaction-types, ``txn_types.py``): a row's stored type
  (or, for a row stored before types existed, the type its category stands for) maps to one flow; transfers,
  card payments, loan principal, collections, settlements, owner contributions and drawings and currency
  exchanges are never income or spend; interest charged, bank fees and tax are spend.
* **Lent or borrowed with a person** (``lending``: the transactions a ``person_loan`` link names,
  ``obligations.load``): money handed over or received, and every repayment, is a transfer whatever its
  category says. Lending is not spending and being repaid is not income (docs/finance.md#lent-and-borrowed).

Pure: no store, clock or network.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from . import txn_types
from .models import Account, AccountKind, Category, CategorySource, Transaction, TransactionType

LIABILITY_KINDS = frozenset({AccountKind.CARD, AccountKind.LOAN})
# The categories that are not spending.
NOT_SPEND = frozenset({Category.TRANSFER, Category.INCOME, Category.CASHBACK, Category.REFUNDS})
# Ledger accounts (finance/ledger.py) that are transfers, not earnings or spending.
TRANSFER_LEDGER = frozenset({"income:transfer_in", "expense:transfer"})
# The journal account the other side of an unrecognised row is posted to: neither income nor spend.
UNCATEGORIZED_LEDGER = "suspense:uncategorized"


class Flow(StrEnum):
    SPEND = "spend"
    INCOME = "income"
    CASHBACK = "cashback"
    REFUND = "refund"  # money in filed as ``refunds``: no purchase found
    TRANSFER = "transfer"
    UNCATEGORIZED = "uncategorized"  # no rule recognised it: held for review, neither income nor spend


def flow_for_type(kind: TransactionType, category: Category) -> Flow:
    """The one counting rule per transaction type (docs/finance.md#transaction-types)."""
    if kind == TransactionType.UNCATEGORIZED:
        return Flow.UNCATEGORIZED
    if kind == TransactionType.INCOME:
        return Flow.INCOME
    if kind in txn_types.SPEND_TYPES:
        return Flow.SPEND
    if kind == TransactionType.CASHBACK:
        return Flow.CASHBACK
    if kind in (TransactionType.REFUND, TransactionType.REVERSAL):
        if category == Category.INCOME:
            return Flow.INCOME  # a salary reversal lowers income
        if category == Category.CASHBACK:
            return Flow.CASHBACK
        if category in NOT_SPEND:
            return Flow.REFUND if category == Category.REFUNDS or kind == TransactionType.REFUND else Flow.TRANSFER
        return Flow.SPEND  # gives back spending in its category
    return Flow.TRANSFER


def classify(category: Category, amount: Decimal, account_kind: AccountKind | None = None,
             source: CategorySource | None = None, *, lending: bool = False, txn_type: TransactionType | None = None) -> Flow:
    """What one row is, by its type (when stored), else by its category, its sign and the kind of account it is on.

    ``lending``: the row is money lent to or borrowed from a person, or a repayment of it: a transfer."""
    if lending:
        return Flow.TRANSFER
    kind = txn_type if txn_type is not None else txn_types.derive(category, amount, account_kind, source).type
    return flow_for_type(kind, category)


def kinds_of(accounts: Iterable[Account]) -> dict[str, AccountKind]:
    return {a.id: a.kind for a in accounts}


def flow_of(t: Transaction, kinds: Mapping[str, AccountKind] | None = None, lending: AbstractSet[str] | None = None) -> Flow:
    """``lending``: ids of transactions linked to money lent or borrowed with a person (``obligations.Links.lending_ids``)."""
    return classify(t.category, t.amount, (kinds or {}).get(t.account_id), t.category_source, lending=bool(lending) and t.id in lending,
                    txn_type=t.transaction_type)


def spend_rows(rows: Iterable[Transaction], kinds: Mapping[str, AccountKind] | None = None,
               lending: AbstractSet[str] | None = None) -> list[Transaction]:
    """Rows that move spend: purchases (negative) and refunds in a spending category (positive).

    ``-sum(amount)`` over them is the net spend; per category it is that category's net spend."""
    return [t for t in rows if t.amount != 0 and flow_of(t, kinds, lending) == Flow.SPEND]


def is_spend_debit(t: Transaction, kinds: Mapping[str, AccountKind] | None = None, lending: AbstractSet[str] | None = None) -> bool:
    """One purchase (money out in a spending category): what a single-payment check looks at."""
    return t.amount < 0 and flow_of(t, kinds, lending) == Flow.SPEND


@dataclass
class Figures:
    """Per-currency totals of a set of rows. Spend is net of refunds; nothing is added across currencies."""

    income: dict[str, Decimal] = field(default_factory=dict)
    cashback: dict[str, Decimal] = field(default_factory=dict)
    refunds: dict[str, Decimal] = field(default_factory=dict)
    spend: dict[str, Decimal] = field(default_factory=dict)
    # (category, currency): (net spend, number of purchases)
    by_category: dict[tuple[Category, str], tuple[Decimal, int]] = field(default_factory=dict)
    # Rows no rule recognised: left out of every figure above, counted so the reader can say so.
    uncategorized_count: int = 0
    uncategorized_in: dict[str, Decimal] = field(default_factory=dict)
    uncategorized_out: dict[str, Decimal] = field(default_factory=dict)

    def net(self) -> dict[str, Decimal]:
        """Cash flow: income, cashback and unmatched refunds received, less net spend."""
        keys = sorted(set(self.income) | set(self.cashback) | set(self.refunds) | set(self.spend))
        zero = Decimal(0)
        return {c: self.income.get(c, zero) + self.cashback.get(c, zero) + self.refunds.get(c, zero) - self.spend.get(c, zero) for c in keys}


def _add(target: dict[str, Decimal], currency: str, amount: Decimal) -> None:
    target[currency] = target.get(currency, Decimal(0)) + amount


def figures(rows: Iterable[Transaction], kinds: Mapping[str, AccountKind] | None = None, lending: AbstractSet[str] | None = None) -> Figures:
    out = Figures()
    for t in rows:
        if t.amount == 0:
            continue
        flow = flow_of(t, kinds, lending)
        if flow == Flow.INCOME:
            _add(out.income, t.currency, t.amount)
        elif flow == Flow.CASHBACK:
            _add(out.cashback, t.currency, t.amount)
        elif flow == Flow.REFUND:
            _add(out.refunds, t.currency, t.amount)
        elif flow == Flow.UNCATEGORIZED:
            out.uncategorized_count += 1
            _add(out.uncategorized_in if t.amount > 0 else out.uncategorized_out, t.currency, abs(t.amount))
        elif flow == Flow.SPEND:
            _add(out.spend, t.currency, -t.amount)
            value, count = out.by_category.get((t.category, t.currency), (Decimal(0), 0))
            out.by_category[(t.category, t.currency)] = (value - t.amount, count + (1 if t.amount < 0 else 0))
    out.income = dict(sorted(out.income.items()))
    out.cashback = dict(sorted(out.cashback.items()))
    out.refunds = dict(sorted(out.refunds.items()))
    out.spend = dict(sorted(out.spend.items()))
    out.uncategorized_in = dict(sorted(out.uncategorized_in.items()))
    out.uncategorized_out = dict(sorted(out.uncategorized_out.items()))
    return out


def ledger_flow(ledger_account: str) -> Flow | None:
    """What a journal posting counts as on the ledger dashboard (None: a balance-sheet posting)."""
    if ledger_account in TRANSFER_LEDGER:
        return Flow.TRANSFER
    if ledger_account == UNCATEGORIZED_LEDGER:
        return Flow.UNCATEGORIZED
    if ledger_account in ("income:cashback",):
        return Flow.CASHBACK
    if ledger_account in ("income:refunds",):
        return Flow.REFUND
    if ledger_account.startswith("income:"):
        return Flow.INCOME
    if ledger_account.startswith("expense:"):
        return Flow.SPEND
    return None


__all__ = ["Figures", "Flow", "LIABILITY_KINDS", "NOT_SPEND", "TRANSFER_LEDGER", "UNCATEGORIZED_LEDGER", "classify", "flow_for_type", "figures", "flow_of", "is_spend_debit",
           "kinds_of", "ledger_flow", "spend_rows"]
