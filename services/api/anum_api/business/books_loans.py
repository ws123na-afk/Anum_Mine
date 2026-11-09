"""Loan schedules: the amortisation schedule, the split of each repayment and the interest accrued at month ends, pure
(docs/business-books.md#loan-schedules).

* **Schedule.** A loan of ``principal`` from ``start_date`` over ``term_months``, repaid monthly, quarterly or yearly, at
  ``annual_rate`` percent a year. The periodic rate is the annual rate over the payments a year. *Annuity:* equal payments
  ``P * r / (1 - (1 + r) ** -n)`` (``P / n`` without interest), each one interest on the balance first, the rest
  principal; the last payment clears the balance. *Equal principal* (flat principal): ``P / n`` of principal each time
  plus the interest on the balance. Due dates keep the start day (the month's last day when it is shorter).
* **Actual repayments.** A repayment pays the interest accrued since the previous repayment first, then principal.
  Interest accrues period by period on the principal still owed: a whole period earns the periodic rate, part of a
  period earns it in proportion to its days. So a repayment on its due date of a loan paid as planned has exactly the
  schedule's interest. Interest not covered by a repayment is carried to the next one.
* **Early repayment.** Paying more than the schedule lowers the principal; the rest of the schedule is worked out again
  from the balance and the due dates left, keeping the payment and ending sooner (``reduce_term``) or keeping the end
  and lowering the payment (``reduce_payment``).
* **Month ends.** Interest accrued and not paid at a month end is a finance cost of that month (``accrued``); the posting
  run posts it on the month end and reverses it on the next day, and each repayment posts its whole interest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..finance.money import quantize

ZERO = Decimal(0)
PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
MAX_PERIODS = 600


def add_months_keep_day(day: date, months: int, anchor_day: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = index // 12, index % 12 + 1
    following = date(year + (month // 12), month % 12 + 1, 1)
    last = (following - timedelta(days=1)).day
    return date(year, month, min(anchor_day, last))


def periods(loan: Any) -> int:
    return max(1, loan.term_months // PERIOD_MONTHS[loan.frequency])


def periodic_rate(loan: Any) -> Decimal:
    return Decimal(loan.annual_rate) / Decimal(100) * Decimal(PERIOD_MONTHS[loan.frequency]) / Decimal(12)


def due_date(loan: Any, number: int) -> date:
    """The ``number``-th due date (1 is the first; 0 is the start)."""
    return add_months_keep_day(loan.start_date, PERIOD_MONTHS[loan.frequency] * number, loan.start_date.day)


def annuity_payment(principal: Decimal, rate: Decimal, count: int, currency: str) -> Decimal:
    if count <= 0:
        return principal
    if rate == 0:
        return quantize(principal / count, currency)
    return quantize(principal * rate / (1 - (1 + rate) ** -count), currency)


@dataclass(frozen=True)
class Row:
    number: int
    due: date
    payment: Decimal
    interest: Decimal
    principal: Decimal
    balance: Decimal  # after this payment


def _rows(loan: Any, balance: Decimal, first_number: int, count: int, *, payment: Decimal | None = None,
          principal_part: Decimal | None = None) -> list[Row]:
    """The rows from ``first_number`` for ``count`` periods (or until the balance is paid when ``payment`` or ``principal_part`` is fixed)."""
    currency = loan.currency
    rate = periodic_rate(loan)
    out: list[Row] = []
    number = first_number
    annuity = loan.method == "annuity" or getattr(loan.method, "value", None) == "annuity"
    left = count
    if annuity and payment is None:
        payment = annuity_payment(balance, rate, count, currency)
    if not annuity and principal_part is None:
        principal_part = quantize(balance / count, currency) if count > 0 else balance
    while balance > 0 and len(out) < MAX_PERIODS:
        interest = quantize(balance * rate, currency)
        last = left == 1
        if annuity:
            principal = balance if last else min(max(payment - interest, ZERO), balance)  # type: ignore[operator]
        else:
            principal = balance if last else min(principal_part, balance)  # type: ignore[type-var]
        if principal <= 0 and not last:
            principal = balance if interest == 0 else ZERO
        balance -= principal
        out.append(Row(number, due_date(loan, number), principal + interest, interest, principal, balance))
        number += 1
        left -= 1
        if left <= 0 and balance > 0 and count > 0 and payment is None and principal_part is None:
            break
    return out


def planned(loan: Any) -> list[Row]:
    """The schedule as agreed, before any repayment."""
    return _rows(loan, Decimal(loan.principal), 1, periods(loan))


def _period_bounds(loan: Any, day: date) -> tuple[int, date, date]:
    """The schedule period that contains ``day`` (start exclusive, due date inclusive), continuing after maturity."""
    step = PERIOD_MONTHS[loan.frequency]
    months = (day.year - loan.start_date.year) * 12 + (day.month - loan.start_date.month)
    number = max(1, months // step)
    while number > 1 and due_date(loan, number - 1) >= day:
        number -= 1
    while due_date(loan, number) < day:
        number += 1
    return number, (loan.start_date if number == 1 else due_date(loan, number - 1)), due_date(loan, number)


def accrued(loan: Any, balance: Decimal, start: date, end: date) -> Decimal:
    """Interest on ``balance`` from ``start`` (exclusive) to ``end`` (inclusive): the periodic rate for a whole period, in
    proportion to the days for part of one."""
    if end <= start or balance <= 0:
        return ZERO
    rate = periodic_rate(loan)
    total = ZERO
    cursor = start
    while cursor < end:
        _, first, due = _period_bounds(loan, cursor + timedelta(days=1))
        stop = max(min(due, end), cursor + timedelta(days=1))
        days = Decimal((due - first).days or 1)
        total += balance * rate * Decimal((stop - cursor).days) / days
        cursor = stop
    return quantize(total, loan.currency)


@dataclass
class State:
    balance: Decimal
    unpaid_interest: Decimal
    last: date
    paid_interest: Decimal = ZERO
    paid_principal: Decimal = ZERO


@dataclass(frozen=True)
class Split:
    repayment_id: str | None
    paid_on: date
    amount: Decimal
    interest: Decimal
    principal: Decimal
    balance: Decimal


def live(repayments: list[Any], loan_id: str) -> list[Any]:
    return sorted((row for row in repayments if row.loan_id == loan_id and row.status == "active"), key=lambda row: (row.paid_on, row.created_at, row.id))


def replay(loan: Any, repayments: list[Any], *, through: date | None = None) -> tuple[State, list[Split]]:
    """Apply the actual repayments (dated up to ``through``) in order: what each paid of interest and principal, and the state after."""
    state = State(Decimal(loan.principal), ZERO, loan.start_date)
    splits: list[Split] = []
    for row in live(repayments, loan.id):
        if through is not None and row.paid_on > through:
            break
        split = split_payment(loan, state, row.paid_on, Decimal(row.amount))
        splits.append(Split(row.id, row.paid_on, Decimal(row.amount), split[0], split[1], state.balance))
    return state, splits


def split_payment(loan: Any, state: State, paid_on: date, amount: Decimal) -> tuple[Decimal, Decimal]:
    """Interest first, then principal; moves ``state`` on. Returns (interest, principal)."""
    due = state.unpaid_interest + accrued(loan, state.balance, state.last, paid_on)
    interest = min(amount, due)
    principal = min(amount - interest, state.balance)
    state.unpaid_interest = due - interest
    state.balance -= principal
    state.last = max(state.last, paid_on)
    state.paid_interest += interest
    state.paid_principal += principal
    return interest, principal


def owed_on(loan: Any, repayments: list[Any], day: date) -> tuple[Decimal, Decimal]:
    """(principal, interest accrued and unpaid) owed at the end of ``day``."""
    state, _ = replay(loan, repayments, through=day)
    return state.balance, state.unpaid_interest + accrued(loan, state.balance, state.last, day)


def most_payable(loan: Any, repayments: list[Any], day: date) -> Decimal:
    principal, interest = owed_on(loan, repayments, day)
    return principal + interest


def month_end_accruals(loan: Any, repayments: list[Any], through: date) -> list[tuple[date, Decimal]]:
    """Interest accrued and not paid on each month end from the start to ``through`` (positive amounts only)."""
    out: list[tuple[date, Decimal]] = []
    month = date(loan.start_date.year, loan.start_date.month, 1)
    count = 0
    while count < MAX_PERIODS * 12:
        following = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        end = following - timedelta(days=1)
        if end > through:
            break
        if end >= loan.start_date:
            principal, interest = owed_on(loan, repayments, end)
            if interest > 0:
                out.append((end, interest))
            if principal <= 0 and interest <= 0 and end >= loan.start_date:
                break
        month = following
        count += 1
    return out


def revised(loan: Any, repayments: list[Any]) -> list[Row]:
    """The rest of the schedule from the balance after the last repayment, worked out again after any early repayment."""
    state, _ = replay(loan, repayments)
    if state.balance <= 0:
        return []
    last = state.last
    number = 1
    while due_date(loan, number) <= last:
        number += 1
    total = periods(loan)
    remaining = max(total - number + 1, 1)
    plan = planned(loan)
    expected = next((row.balance for row in plan if row.due <= last and row.number == number - 1), Decimal(loan.principal)) if number > 1 else Decimal(loan.principal)
    ahead = state.balance < expected
    if not ahead or loan.early_repayment == "reduce_payment":
        rows = _rows(loan, state.balance, number, remaining)
    else:
        annuity = loan.method == "annuity" or getattr(loan.method, "value", None) == "annuity"
        if annuity:
            rows = _rows(loan, state.balance, number, MAX_PERIODS, payment=plan[0].payment if plan else None)
        else:
            rows = _rows(loan, state.balance, number, MAX_PERIODS, principal_part=plan[0].principal if plan else None)
    if rows and state.unpaid_interest > 0:
        first = rows[0]
        rows[0] = Row(first.number, first.due, first.payment + state.unpaid_interest, first.interest + state.unpaid_interest, first.principal, first.balance)
    return rows


def schedule_vs_actual(loan: Any, repayments: list[Any], today: date) -> list[dict[str, Any]]:
    """Each planned row next to what was actually paid in its period (after the previous due date, up to its own)."""
    _, splits = replay(loan, repayments)
    out = []
    previous = loan.start_date
    plan = planned(loan)
    for index, row in enumerate(plan):
        last_row = index == len(plan) - 1
        inside = [s for s in splits if (s.paid_on > previous or (index == 0 and s.paid_on >= previous)) and (s.paid_on <= row.due or last_row)]
        paid = sum((s.amount for s in inside), ZERO)
        if paid >= row.payment:
            state = "paid"
        elif paid > 0:
            state = "part_paid"
        elif row.due < today:
            state = "overdue"
        elif row.due <= today + timedelta(days=31):
            state = "due"
        else:
            state = "upcoming"
        out.append({"number": row.number, "due": row.due, "payment": row.payment, "interest": row.interest, "principal": row.principal,
                    "balance": row.balance, "paid": paid, "paid_interest": sum((s.interest for s in inside), ZERO),
                    "paid_principal": sum((s.principal for s in inside), ZERO), "difference": paid - row.payment, "state": state})
        previous = row.due
    return out


__all__ = ["PERIOD_MONTHS", "Row", "Split", "State", "accrued", "annuity_payment", "due_date", "live", "month_end_accruals", "most_payable", "owed_on",
           "periodic_rate", "periods", "planned", "replay", "revised", "schedule_vs_actual", "split_payment"]
