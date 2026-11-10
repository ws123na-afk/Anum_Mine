"""Bank and finance loans on a Debt: the terms, the schedule from the start date to the end, the payments found for it
and the balance read from them (docs/finance.md#bank-loans).

A Debt with a start date, the amount borrowed and the term or the instalment is a loan with a **schedule**:

* **Reducing rate**: equal instalments (the planner's annuity, ``planner.amortised``), each month a twelfth of the yearly
  rate on what is left (rounded half to even to the minor unit), the rest repays the principal; the last instalment takes
  what is left.
* **Flat rate** (common for Saudi and UAE car and personal finance, the formula of ``planner.compare``): the profit is
  ``amount x rate x years``, spread evenly; every instalment repays the same share of the amount.
* **The lender's instalment wins** over the computed one when the person knows it.
* **A rate is never invented.** Without a rate the instalments are the lender's (or the amount over the term) and their
  split into principal and interest is unknown: what is left is the instalments not paid yet.

**Paid** comes from what the person accepted or matched: payments linked to the debt (``debt`` dimension, bank debits
and any payment matched by hand) and the salary months of an accepted batch (``LoanBatch``, a loan deducted from the
salary has no bank row). Each payment is placed on the instalment nearest its date (within 20 days), else on the first
one not paid yet; what is more than an instalment covers the next. **What is owed** is the amount less the principal
those payments covered (or, without a split, the instalments not paid yet); a loan marked paid off or closed owes
nothing.

**Finding past deductions** (``find_bank``, ``find_salary``) proposes, never links: a bank debit is money out of the
chosen account, the same amount within 1 percent, posted within 5 days of an instalment's date, with a description that
does not name another merchant (``merchant_match``); more than one such payment makes the month ambiguous. A salary month
is found from that month's payslip (its loan deductions) or, without a payslip, the salary profile's loan deduction once
the pay day passed.

Pure: no store, clock or network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, localcontext
from typing import Literal

from . import commitment_math as M
from .merchant_match import merchants_agree
from .models import Debt
from .money import minor_digits, quantize

RateType = Literal["reducing", "flat"]
Repayment = Literal["bank_debit", "salary_deduction", "manual"]
LoanStatus = Literal["open", "paid_off", "closed"]
RowStatus = Literal["paid", "part_paid", "missed", "upcoming", "ambiguous", "settled"]
FindState = Literal["found", "missing", "ambiguous"]

REPAYMENTS: tuple[str, ...] = ("bank_debit", "salary_deduction", "manual")
RATE_TYPES: tuple[str, ...] = ("reducing", "flat")
STATUSES: tuple[str, ...] = ("open", "paid_off", "closed")
MAX_ROWS = 600
TOLERANCE = Decimal("0.01")  # a found debit is within 1 percent of the instalment
WINDOW_DAYS = 5  # a found debit is posted within this many days of the instalment's date
PLACE_DAYS = 20  # a matched payment is placed on the instalment within this many days, else the first one not paid
GRACE_DAYS = 5  # an instalment is missed once this many days passed without a payment


def _unit(currency: str) -> Decimal:
    return Decimal(1).scaleb(-minor_digits(currency))


def _add_months(day: date, months: int, anchor: int) -> date:
    year, month = divmod(day.month - 1 + months, 12)
    first = date(day.year + year, month + 1, 1)
    return first.replace(day=min(anchor, M.month_end(first).day))


def has_terms(debt: Debt) -> bool:
    """The debt has a schedule: a start date, the amount borrowed and the term or the instalment."""
    return bool(debt.loan_start_on and debt.loan_amount and debt.loan_amount > 0 and (debt.loan_term_months or debt.loan_installment))


def due_dates(start: date, payment_day: int | None, count: int) -> list[date]:
    """Monthly from the month after ``start``, on ``payment_day`` (the start's day without one; the month's last day when shorter)."""
    anchor = payment_day or start.day
    return [_add_months(start, n, anchor) for n in range(1, count + 1)]


# -- the schedule -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    number: int
    due_on: date
    payment: Decimal
    principal: Decimal | None  # None: the split is not known (no rate)
    interest: Decimal | None
    balance_after: Decimal  # the principal left, or (no split) the instalments left to pay


@dataclass(frozen=True)
class Schedule:
    rows: tuple[Row, ...]
    installment: Decimal  # the regular instalment
    basis: Literal["reducing", "flat", "plain"]
    installment_from: Literal["lender", "computed"]
    split_known: bool
    total_interest: Decimal | None
    total_payable: Decimal
    effective_rate: Decimal | None  # the yearly rate on a reducing balance the instalments give (the planner's rate)
    never_ends: bool = False  # the lender's instalment does not cover the interest


def _annuity(principal: Decimal, rate: Decimal, months: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        if rate <= 0:
            return principal / months
        r = rate / 1200
        return principal * r / (1 - (1 + r) ** -months)


def reducing_rate(principal: Decimal, payment: Decimal, months: int) -> Decimal | None:
    """The yearly rate on a reducing balance at which ``months`` payments of ``payment`` repay ``principal`` (bisection)."""
    if months < 1 or payment * months <= principal:
        return Decimal(0) if months >= 1 and payment * months == principal else None
    low, high = Decimal(0), Decimal(200)
    for _ in range(80):
        mid = (low + high) / 2
        if _annuity(principal, mid, months) > payment:
            high = mid
        else:
            low = mid
    return ((low + high) / 2).quantize(Decimal("0.0001"))


def build(debt: Debt) -> Schedule | None:
    """Every instalment from the start date to the end (None without terms)."""
    if not has_terms(debt):
        return None
    c = debt.currency
    unit = _unit(c)
    amount = debt.loan_amount or Decimal(0)
    rate = debt.loan_rate if debt.loan_rate is not None and debt.loan_rate > 0 else None
    lender = debt.loan_installment if debt.loan_installment is not None and debt.loan_installment > 0 else None
    term = debt.loan_term_months
    start = debt.loan_start_on
    if start is None:  # has_terms said there is one
        return None
    if rate is None:
        return _plain(amount, lender, term, start, debt.loan_payment_day, c)
    if (debt.loan_rate_type or "reducing") == "flat":
        return _flat(amount, rate, lender, term, start, debt.loan_payment_day, c)
    r = rate / 1200
    payment = lender if lender is not None else _annuity(amount, rate, term or 1).quantize(unit, rounding=ROUND_CEILING)
    first_interest = (amount * r).quantize(unit, rounding=ROUND_HALF_EVEN)
    never = payment <= first_interest and term is None
    count = term or MAX_ROWS
    rows: list[Row] = []
    left = amount
    dates = due_dates(start, debt.loan_payment_day, min(count, MAX_ROWS))
    for index, on in enumerate(dates):
        if left <= 0 or never:
            break
        interest = (left * r).quantize(unit, rounding=ROUND_HALF_EVEN)
        last = index == len(dates) - 1 or payment - interest >= left
        principal = left if last else max(payment - interest, Decimal(0))
        left -= principal
        rows.append(Row(index + 1, on, quantize(principal + interest, c), quantize(principal, c), interest, quantize(left, c)))
    interest_total = sum((x.interest or Decimal(0) for x in rows), Decimal(0))
    return Schedule(rows=tuple(rows), installment=quantize(payment, c), basis="reducing", installment_from="lender" if lender else "computed",
                    split_known=True, total_interest=quantize(interest_total, c), total_payable=quantize(amount + interest_total, c),
                    effective_rate=rate.quantize(Decimal("0.0001")), never_ends=never)


def _flat(amount: Decimal, rate: Decimal, lender: Decimal | None, term: int | None, start: date, day: int | None, c: str) -> Schedule:
    unit = _unit(c)
    never = False
    if term is None:
        if lender is None:  # has_terms: a term or an instalment
            lender = Decimal(0)
        monthly_profit = amount * rate / 1200
        if lender <= monthly_profit:
            never, term = True, 0
        else:
            term = min(math.ceil(amount / (lender - monthly_profit)), MAX_ROWS)
    rows: list[Row] = []
    if term:
        share = (amount / term).quantize(unit, rounding=ROUND_HALF_EVEN)
        profit = amount * rate * Decimal(term) / 1200
        payment = lender if lender is not None else ((amount + profit) / term).quantize(unit, rounding=ROUND_CEILING)
        left = amount
        for index, on in enumerate(due_dates(start, day, term)):
            last = index == term - 1
            principal = left if last else min(share, left)
            interest = max(payment - share, Decimal(0)) if lender is not None else (profit / term).quantize(unit, rounding=ROUND_HALF_EVEN)
            if last and lender is None:
                interest = quantize(profit, c) - sum((x.interest or Decimal(0) for x in rows), Decimal(0))
            left -= principal
            rows.append(Row(index + 1, on, quantize(principal + interest, c), quantize(principal, c), quantize(interest, c), quantize(left, c)))
    else:
        payment = lender or Decimal(0)
    interest_total = sum((x.interest or Decimal(0) for x in rows), Decimal(0))
    regular = rows[0].payment if rows else payment
    return Schedule(rows=tuple(rows), installment=quantize(lender if lender is not None else regular, c), basis="flat",
                    installment_from="lender" if lender else "computed", split_known=True, total_interest=quantize(interest_total, c),
                    total_payable=quantize(amount + interest_total, c),
                    effective_rate=reducing_rate(amount, regular, len(rows)) if rows else None, never_ends=never)


def _plain(amount: Decimal, lender: Decimal | None, term: int | None, start: date, day: int | None, c: str) -> Schedule:
    """No rate: the lender's instalments (or the amount over the term); the split into principal and interest is unknown."""
    unit = _unit(c)
    if lender is not None:
        count = min(term or math.ceil(amount / lender), MAX_ROWS)
        total = lender * count if term else amount
        regular = lender
    else:
        count = min(term or 1, MAX_ROWS)
        total = amount
        regular = (amount / count).quantize(unit, rounding=ROUND_HALF_EVEN)
    rows: list[Row] = []
    paid = Decimal(0)
    for index, on in enumerate(due_dates(start, day, count)):
        payment = total - paid if index == count - 1 else min(regular, total - paid)
        paid += payment
        rows.append(Row(index + 1, on, quantize(payment, c), None, None, quantize(total - paid, c)))
    return Schedule(rows=tuple(rows), installment=quantize(regular, c), basis="plain", installment_from="lender" if lender else "computed",
                    split_known=False, total_interest=None, total_payable=quantize(total, c),
                    # What is left already holds every instalment, interest included: the planner adds none on top.
                    effective_rate=None)


# -- paid, owed and the status of each instalment -----------------------------------------------------------------


@dataclass(frozen=True)
class Paid:
    """One payment toward the loan: a linked transaction or a salary month of an accepted batch."""

    on: date
    amount: Decimal
    kind: Literal["link", "salary"]
    ref: str  # the canonical transaction, or the payslip or salary rule
    link_id: str | None = None
    batch_id: str | None = None
    due_on: date | None = None  # a salary month names its instalment


@dataclass
class RowView:
    number: int
    due_on: date
    payment: Decimal
    principal: Decimal | None
    interest: Decimal | None
    balance_after: Decimal
    status: RowStatus
    paid: Decimal = Decimal(0)
    payments: list[Paid] = field(default_factory=list)


@dataclass(frozen=True)
class Figures:
    rows: tuple[RowView, ...]
    paid: Decimal  # every payment counted
    interest_paid: Decimal | None  # None without a split
    principal_paid: Decimal | None
    balance: Decimal  # what is owed now
    payoff_on: date | None  # the last instalment (or the day it was paid off or closed)
    due_count: int  # instalments due by today
    paid_count: int  # instalments paid in full
    months_ahead: int  # paid in full less due by today: negative is behind
    next_due_on: date | None
    next_amount: Decimal
    status: LoanStatus


def _place(rows: list[RowView], payment: Paid) -> None:
    """Put a payment on its instalment (a salary month names it; else the nearest within 20 days that is not paid), then what
    is more on the first instalments not paid yet."""
    if payment.due_on is not None:
        target = next((r for r in rows if r.due_on == payment.due_on and r.paid < r.payment), None)
    else:
        near = [r for r in rows if r.paid < r.payment and abs((r.due_on - payment.on).days) <= PLACE_DAYS]
        target = min(near, key=lambda r: (abs((r.due_on - payment.on).days), r.number)) if near else None
    left = payment.amount
    for row in ([target] if target is not None else []) + [r for r in rows if r is not target]:
        if left <= 0:
            break
        room = row.payment - row.paid
        if room <= 0:
            continue
        take = min(left, room)
        row.paid += take
        row.payments.append(payment)
        left -= take


def figures(debt: Debt, schedule: Schedule, payments: list[Paid], today: date) -> Figures:
    """Each instalment with its payments and status, and what is paid, owed and due."""
    c = debt.currency
    status: LoanStatus = debt.loan_status if debt.loan_status in STATUSES else "open"  # type: ignore[assignment]
    rows = [RowView(r.number, r.due_on, r.payment, r.principal, r.interest, r.balance_after, "upcoming") for r in schedule.rows]
    for payment in sorted(payments, key=lambda p: (p.on, p.ref)):
        _place(rows, payment)
    total = sum((p.amount for p in payments), Decimal(0))
    placed = sum((r.paid for r in rows), Decimal(0))
    extra = total - placed  # more than every instalment: it repays principal
    closed_on = debt.loan_closed_on
    for row in rows:
        if row.paid >= row.payment and row.payment > 0:
            row.status = "paid"
        elif status != "open" and closed_on is not None and row.due_on > closed_on:
            row.status = "settled"
        elif row.paid > 0:
            row.status = "part_paid"
        elif (today - row.due_on).days > GRACE_DAYS:
            row.status = "missed"
        else:
            row.status = "upcoming"
    if schedule.split_known:
        interest_paid = sum((min(r.paid, r.interest or Decimal(0)) for r in rows), Decimal(0))
        principal_paid = sum((min(max(r.paid - (r.interest or Decimal(0)), Decimal(0)), r.principal or Decimal(0)) for r in rows), Decimal(0)) + extra
        balance = max((debt.loan_amount or Decimal(0)) - principal_paid, Decimal(0))
    else:
        interest_paid = principal_paid = None
        balance = max(schedule.total_payable - total, Decimal(0))
    if status != "open":
        balance = Decimal(0)
    due = [r for r in rows if r.due_on <= today]
    paid_full = sum(1 for r in rows if r.status == "paid")
    upcoming = next((r for r in rows if r.status not in ("paid", "settled")), None)
    payoff = closed_on if status != "open" else (rows[-1].due_on if rows else None)
    return Figures(rows=tuple(rows), paid=quantize(total, c), interest_paid=quantize(interest_paid, c) if interest_paid is not None else None,
                   principal_paid=quantize(principal_paid, c) if principal_paid is not None else None, balance=quantize(balance, c),
                   payoff_on=payoff, due_count=len(due), paid_count=paid_full, months_ahead=paid_full - len(due) if status == "open" else 0,
                   next_due_on=upcoming.due_on if upcoming is not None and status == "open" else None,
                   next_amount=quantize(upcoming.payment - upcoming.paid, c) if upcoming is not None and status == "open" else Decimal(0),
                   status=status)


# -- finding past deductions ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """A recorded payment that could be an instalment: money out of one of the person's accounts, not linked to a debt yet."""

    canonical_id: str
    posted_on: date
    amount: Decimal  # positive
    currency: str
    text: str
    account_id: str


@dataclass
class Found:
    number: int
    due_on: date
    expected: Decimal
    state: FindState
    amount: Decimal | None = None
    canonical_id: str | None = None
    source: Literal["bank_debit", "payslip", "salary_rule"] | None = None
    source_id: str | None = None
    candidates: list[Entry] = field(default_factory=list)


def close_enough(amount: Decimal, expected: Decimal, currency: str) -> bool:
    """The same amount within 1 percent (at least one minor unit)."""
    return abs(amount - expected) <= max(expected * TOLERANCE, _unit(currency))


def _open_rows(view: Figures, today: date) -> list[RowView]:
    """Instalments due by today (or within a few days, for a debit posted early) that nothing is placed on yet."""
    return [r for r in view.rows if r.paid == 0 and r.status != "settled" and (r.due_on - today).days <= WINDOW_DAYS]


def find_bank(debt: Debt, view: Figures, entries: list[Entry], today: date) -> list[Found]:
    """Each open instalment with the debits that fit it: found (one), ambiguous (more than one) or missing (none)."""
    if view.status != "open" or not debt.loan_account_id:
        return []
    start = debt.loan_start_on or date.min
    rows = _open_rows(view, today)
    fits: dict[int, list[Entry]] = {}
    for row in rows:
        fits[row.number] = [
            e for e in entries
            if e.account_id == debt.loan_account_id and e.currency == debt.currency and e.posted_on >= start
            and abs((e.posted_on - row.due_on).days) <= WINDOW_DAYS and close_enough(e.amount, row.payment, debt.currency)
            and merchants_agree(debt.lender, e.text) is not False
        ]
    # One debit pays one instalment: it stays only with the instalment nearest its date.
    owner: dict[str, tuple[int, int]] = {}
    by_number = {r.number: r for r in rows}
    for number, found in fits.items():
        for e in found:
            gap = abs((e.posted_on - by_number[number].due_on).days)
            if e.canonical_id not in owner or (gap, number) < owner[e.canonical_id]:
                owner[e.canonical_id] = (gap, number)
    out: list[Found] = []
    for row in rows:
        mine = sorted((e for e in fits[row.number] if owner.get(e.canonical_id, (0, -1))[1] == row.number), key=lambda e: (e.posted_on, e.canonical_id))
        if len(mine) > 1 and debt.lender:
            named = [e for e in mine if merchants_agree(debt.lender, e.text)]
            if len(named) == 1:
                mine = named
        if len(mine) == 1:
            e = mine[0]
            out.append(Found(row.number, row.due_on, row.payment, "found", e.amount, e.canonical_id, "bank_debit", None, [e]))
        elif mine:
            out.append(Found(row.number, row.due_on, row.payment, "ambiguous", candidates=mine))
        elif row.due_on <= today:
            out.append(Found(row.number, row.due_on, row.payment, "missing"))
    return out


def find_salary(debt: Debt, view: Figures, payslips: list, profile: object | None, today: date) -> list[Found]:
    """Each open instalment a salary month paid: that month's payslip's loan deductions, else the salary profile's loan
    deduction once the pay day passed. A payslip for the month without a matching loan deduction is a missing month."""
    from .salary import _active, month_key, pay_date

    if view.status != "open":
        return []
    out: list[Found] = []
    for row in _open_rows(view, today):
        month = month_key(row.due_on)
        slips = [s for s in payslips if getattr(s, "month", None) == month and getattr(s, "currency", None) == debt.currency]
        hit: Found | None = None
        if slips:
            for slip in slips:
                for item in slip.deductions:
                    kind = getattr(item.kind, "value", item.kind)
                    if kind == "loan" and close_enough(item.amount, row.payment, debt.currency) and merchants_agree(debt.lender, item.label) is not False:
                        hit = Found(row.number, row.due_on, row.payment, "found", quantize(item.amount, debt.currency), None, "payslip", slip.id)
                        break
                if hit:
                    break
        elif profile is not None and getattr(profile, "currency", None) == debt.currency and pay_date(profile, month) <= today:  # type: ignore[arg-type]
            for rule in profile.rules:  # type: ignore[attr-defined]
                kind, reason = getattr(rule.kind, "value", rule.kind), getattr(rule.reason, "value", rule.reason)
                if kind == "deduction" and reason == "loan" and _active(rule, month) and close_enough(rule.amount, row.payment, debt.currency) \
                        and merchants_agree(debt.lender, rule.label) is not False:
                    hit = Found(row.number, row.due_on, row.payment, "found", quantize(rule.amount, debt.currency), None, "salary_rule", rule.id)
                    break
        if hit is not None:
            out.append(hit)
        elif row.due_on <= today:
            out.append(Found(row.number, row.due_on, row.payment, "missing"))
    return out


def salary_already_net(debt: Debt, profile: object | None, payslips: list, today: date) -> bool:
    """This month's expected salary already takes a loan deduction in the loan's currency, so the instalment is not a
    commitment of its own (it would be counted twice)."""
    from .salary import expected_for, month_key

    if debt.loan_repayment != "salary_deduction" or profile is None or getattr(profile, "currency", None) != debt.currency:
        return False
    expected = expected_for(profile, month_key(today), payslips)  # type: ignore[arg-type]
    return any(d.reason == "loan" and d.amount > 0 for d in expected.deductions)


__all__ = [
    "RATE_TYPES",
    "REPAYMENTS",
    "STATUSES",
    "Entry",
    "Figures",
    "Found",
    "Paid",
    "Row",
    "RowView",
    "Schedule",
    "build",
    "close_enough",
    "due_dates",
    "figures",
    "find_bank",
    "find_salary",
    "has_terms",
    "reducing_rate",
    "salary_already_net",
]
