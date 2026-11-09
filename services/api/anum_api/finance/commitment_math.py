"""The building blocks every Finance reader of bills and debts shares (docs/finance.md#how-figures-are-counted).

Fixed costs (``personal.fixed_commitments``), Safe to spend (``safe_to_spend.compute``), the money advisor's
runway and coming payments (``advisor``) and the planner (``planning_service``) each answer a different
question, but the same bill or debt gives the same number in all of them because they take it from here:

* ``monthly_equivalent``: a bill's amount per month. Weekly x 52 / 12, monthly as is, quarterly / 3, yearly / 12.
  **A one-off bill has no monthly equivalent** (``None``): Fixed costs lists it as left out, and a monthly
  total (the advisor's runway) counts nothing for it (``bill_monthly``). Safe to spend still counts a one-off
  bill on its due date, because it asks what is due, not what recurs.
* ``debt_minimum``: what a debt's minimum payment takes this month, never more than it owes.
* ``bill_for_debt``: a debt whose minimum is also saved as a bill is counted once, as the bill.
* ``liquid``: money in bank and cash accounts that are not archived, per currency.
* Month helpers: ``month_start``, ``month_end``, ``previous_months``, ``months_between``, ``next_due_day``.

Pure: no store, clock or network.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal

from .models import Account, AccountKind, Bill, Cadence, Debt

SPENDABLE = frozenset({AccountKind.BANK, AccountKind.CASH})

_PER_MONTH: dict[Cadence, tuple[int, int]] = {
    Cadence.WEEKLY: (52, 12),
    Cadence.MONTHLY: (1, 1),
    Cadence.QUARTERLY: (1, 3),
    Cadence.YEARLY: (1, 12),
}


# -- bills ----------------------------------------------------------------------------------------------


def monthly_equivalent(amount: Decimal, cadence: Cadence) -> Decimal | None:
    """``amount`` paid at ``cadence``, per month (unrounded). A one-off amount is not monthly: ``None``."""
    factor = _PER_MONTH.get(cadence)
    if factor is None:
        return None
    times, per = factor
    return amount * times / per


def bill_monthly(bill: Bill) -> Decimal:
    """A bill's share of a monthly total: its monthly equivalent, or nothing for a one-off bill."""
    found = monthly_equivalent(bill.amount, bill.cadence)
    return found if found is not None else Decimal(0)


def name_key(text: str) -> str:
    """A name compared without case, punctuation or extra spaces ("Car-Loan " == "car loan")."""
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def bill_for_debt(debt: Debt, bills: list[Bill]) -> Bill | None:
    """A saved bill that is this debt's payment (counted once, as the bill): an active bill in the debt's currency
    with the same name, or kept on the same account."""
    for bill in bills:
        if not bill.active or bill.currency != debt.currency:
            continue
        if name_key(bill.name) == name_key(debt.name) or (debt.account_id and bill.account_id == debt.account_id):
            return bill
    return None


# -- debts ----------------------------------------------------------------------------------------------


def debt_minimum(debt: Debt) -> Decimal:
    """What the debt's minimum payment takes: at most what it owes, nothing once it is paid off or without a minimum."""
    if debt.balance <= 0 or debt.minimum_payment <= 0:
        return Decimal(0)
    return min(debt.minimum_payment, debt.balance)


def owes_minimum(debt: Debt) -> bool:
    return debt_minimum(debt) > 0


# -- accounts -------------------------------------------------------------------------------------------


def account_balance(account: Account, balances: dict[str, Decimal]) -> Decimal:
    """The account's opening balance plus the sum of its transactions."""
    return account.opening_balance + balances.get(account.id, Decimal(0))


def liquid(accounts: list[Account], balances: dict[str, Decimal], currency: str) -> Decimal:
    """Money in the person's bank and cash accounts in ``currency`` that are not archived (unrounded)."""
    return sum((account_balance(a, balances) for a in accounts if a.currency == currency and a.kind in SPENDABLE and not a.archived), Decimal(0))


# -- months ---------------------------------------------------------------------------------------------


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def previous_months(today: date, count: int) -> list[tuple[date, date]]:
    """The ``count`` full calendar months before today's month, newest first."""
    out: list[tuple[date, date]] = []
    cursor = month_start(today)
    for _ in range(count):
        cursor = month_start(cursor - timedelta(days=1))
        out.append((cursor, month_end(cursor)))
    return out


def months_between(today: date, target: date) -> int:
    """Months left until ``target``, this month included (at least one)."""
    return max(1, (target.year - today.year) * 12 + (target.month - today.month) + 1)


def next_due_day(due_day: int, today: date) -> date:
    """The next date on or after today that falls on ``due_day`` (the month's last day when shorter)."""
    this = today.replace(day=min(due_day, calendar.monthrange(today.year, today.month)[1]))
    if this >= today:
        return this
    following = month_start(month_end(today) + timedelta(days=1))
    return following.replace(day=min(due_day, calendar.monthrange(following.year, following.month)[1]))


__all__ = [
    "SPENDABLE", "account_balance", "bill_for_debt", "bill_monthly", "debt_minimum", "liquid", "month_end", "month_start", "monthly_equivalent",
    "months_between", "name_key", "next_due_day", "owes_minimum", "previous_months",
]
