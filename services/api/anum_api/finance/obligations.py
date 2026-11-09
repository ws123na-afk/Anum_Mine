"""What is still owed and still to pay, read with the person's matched payments
(docs/finance.md#how-figures-are-counted).

Every reader of bills and debts (the summary, Safe to spend, the advisor, the ledger dashboard and
the assistant's tools) goes through here, so a bill paid early is not taken off again and a debt's
balance follows the payments matched to it:

* **Bills**: a due date with an active matched payment (a ``bill`` link, ``commitments.py``) is
  paid and drops out. This month's due dates that have passed without a matched payment stay in as
  *overdue* until a payment is matched or the person moves the bill's next due date.
* **Debts**: a debt kept on a card or loan account (``account_id``) owes what that account's
  ledger says (its negative balance). A debt without one owes the balance the person saved, less
  the principal of the payments matched to it since then: with a rate, each payment's interest
  (``balance x APR / 12``) is taken out first.
* **Net worth** is account balances (cards and loans count by their sign, so what they owe is
  already in) less only the debts that have no account of their own.
* **Minimums**: a debt whose minimum is also saved as a bill (same currency, and the same name or
  the same account) is counted once, as the bill.

``load`` reads the links in one short owner-scoped unit of its own; the rest is pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..schemas import TenantContext
from .models import Account, AccountKind, Bill, Debt, Goal

LIABILITY_KINDS = frozenset({AccountKind.CARD, AccountKind.LOAN})
ASSET_KINDS = frozenset({AccountKind.BANK, AccountKind.CASH, AccountKind.INVESTMENT})


@dataclass(frozen=True)
class DebtPayment:
    debt_id: str
    amount: Decimal
    currency: str
    posted_on: date
    matched_at: object  # the link's created_at (a datetime)


@dataclass
class Links:
    """The person's bill and debt links, as the readers need them."""

    bill_links: list = field(default_factory=list)  # FinanceLink rows in the ``bill`` dimension
    debt_payments: list[DebtPayment] = field(default_factory=list)


def load(context: TenantContext) -> Links:
    """Bill links and matched debt payments, in one short unit of work (never nested in another)."""
    from .commitments import BILL_DIMENSION, BILL_TARGET, DEBT_DIMENSION, DEBT_TARGET
    from .links import live_entry
    from .links_store import LinkStatus, open_link_stores

    with open_link_stores(context) as (_finance, sms, links):
        bill_rows, _ = links.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=BILL_TARGET, target_id=None,
                                        status=None, limit=10_000, offset=0)
        debt_rows, _ = links.list_links(context, canonical_id=None, dimension=DEBT_DIMENSION, target_type=DEBT_TARGET, target_id=None,
                                        status=LinkStatus.ACTIVE.value, limit=10_000, offset=0)
        payments: list[DebtPayment] = []
        for link in debt_rows:
            if link.status != LinkStatus.ACTIVE or link.amount is None or not link.currency:
                continue
            entry = live_entry(sms.chain(context, link.canonical_id))
            if entry is None:
                continue
            payments.append(DebtPayment(debt_id=link.target_id, amount=link.amount, currency=link.currency, posted_on=entry.posted_on,
                                        matched_at=link.created_at))
    return Links(bill_links=[l for l in bill_rows if l.status != LinkStatus.VOID], debt_payments=payments)


# -- bills ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Due:
    bill: Bill
    due_on: date
    overdue: bool


def unpaid(bills: list[Bill], links: list, today: date, end: date) -> list[Due]:
    """Every active bill's due dates from the start of this month to ``end`` without an active matched
    payment: the passed ones are overdue, the rest still to come."""
    from .commitments import bill_schedule

    start = today.replace(day=1)
    by_id = {b.id: b for b in bills}
    out: list[Due] = []
    for row in bill_schedule([b for b in bills if b.active], links, today, start, max(end, start)):
        if row.status == "paid" or row.due_on > end:
            continue
        out.append(Due(bill=by_id[row.bill_id], due_on=row.due_on, overdue=row.due_on < today))
    return out


def _name(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def bill_for_debt(debt: Debt, bills: list[Bill]) -> Bill | None:
    """A saved bill that is this debt's payment (counted once, as the bill)."""
    for bill in bills:
        if not bill.active or bill.currency != debt.currency:
            continue
        if _name(bill.name) == _name(debt.name) or (debt.account_id and bill.account_id == debt.account_id):
            return bill
    return None


# -- debts and goals ------------------------------------------------------------------------------------


def _account(accounts: dict[str, Account], account_id: str | None) -> Account | None:
    account = accounts.get(account_id or "")
    return account if account is not None and not account.archived else None


def has_account(debt: Debt, accounts: dict[str, Account]) -> bool:
    """The debt is kept on one of the person's card or loan accounts in its currency (so the account already counts it)."""
    account = _account(accounts, debt.account_id)
    return account is not None and account.kind in LIABILITY_KINDS and account.currency == debt.currency


def debt_balance(debt: Debt, accounts: dict[str, Account], balances: dict[str, Decimal], payments: list[DebtPayment]) -> Decimal:
    """What the debt owes now (positive)."""
    if has_account(debt, accounts):
        account = accounts[debt.account_id or ""]
        return max(-(account.opening_balance + balances.get(account.id, Decimal(0))), Decimal(0))
    remaining = debt.balance
    rate = debt.apr_percent / Decimal(1200) if debt.apr_percent > 0 else Decimal(0)
    saved_on = debt.created_at.date()
    mine = sorted((p for p in payments if p.debt_id == debt.id and p.currency == debt.currency and p.posted_on >= saved_on
                   and _after(p.matched_at, debt.updated_at)), key=lambda p: (p.posted_on, str(p.matched_at)))
    for payment in mine:
        interest = min(payment.amount, remaining * rate) if remaining > 0 else Decimal(0)
        remaining -= payment.amount - interest
    return max(remaining, Decimal(0))


def _after(matched_at: object, saved_at: object) -> bool:
    try:
        return matched_at >= saved_at  # type: ignore[operator]
    except TypeError:
        return True


def current_debts(debts: list[Debt], accounts: list[Account], balances: dict[str, Decimal], links: Links | None) -> list[Debt]:
    """The debts with today's balance (copies; nothing stored changes)."""
    by_id = {a.id: a for a in accounts}
    payments = links.debt_payments if links is not None else []
    return [d.model_copy(update={"balance": debt_balance(d, by_id, balances, payments)}) for d in debts]


def unlinked(debts: list[Debt], accounts: list[Account]) -> list[Debt]:
    """The debts no account counts yet: the only ones net worth subtracts on top of account balances."""
    by_id = {a.id: a for a in accounts}
    return [d for d in debts if not has_account(d, by_id)]


def current_goals(goals: list[Goal], accounts: list[Account], balances: dict[str, Decimal]) -> list[Goal]:
    """A goal kept in one of the person's own bank, cash or investment accounts in its currency has saved what
    that account holds; any other goal keeps what the person entered."""
    by_id = {a.id: a for a in accounts}
    out = []
    for goal in goals:
        account = _account(by_id, goal.account_id)
        if account is not None and account.kind in ASSET_KINDS and account.currency == goal.currency:
            held = max(account.opening_balance + balances.get(account.id, Decimal(0)), Decimal(0))
            out.append(goal.model_copy(update={"saved_amount": held}))
        else:
            out.append(goal)
    return out


__all__ = ["Due", "DebtPayment", "Links", "bill_for_debt", "current_debts", "current_goals", "debt_balance", "has_account", "load", "unlinked",
           "unpaid"]
