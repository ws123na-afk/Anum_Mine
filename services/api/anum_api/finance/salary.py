"""The salary profile, the month by month salary check and the one income figure (docs/finance.md#salary).

Pure: no store, clock or network. Everything comes from the person's own records.

* **Expected each month** (``expected_for``): the expected gross less the standing deductions active that
  month (loan, absence, social insurance such as GOSI, other) and the repayment of any salary advance
  (``amount`` a month from its first month until what was advanced is back). **A payslip for the month
  overrides it**: its gross and deductions are that month's figures.
* **Detection** (``salary_months``): from the joining month to this month, each month's money in on the
  salary account (any source: statement, SMS or typed by hand) in the salary's currency within
  ``WINDOW_BEFORE`` days before to ``WINDOW_AFTER`` days after the usual pay day. A candidate is scored on
  the amount (against the latest confirmed salary, else the expected net), the day (against the pay day)
  and the payer (the same payer as a confirmed salary, else the employer's name or a salary word). The best
  one is suggested with its confidence; the person confirms or rejects it, and a rejected transaction is
  never suggested again. A confirmed month, or a credit linked to that month's payslip, is the salary.
  Money lent or borrowed with a person and the credit of a salary advance are never a salary.
* **Flags**: ``late`` (more than ``LATE_DAYS`` after the pay day), ``partial`` (less than 95 percent of
  the expected net), ``missing`` (nothing found once the pay day and ``LATE_DAYS`` have passed),
  ``upcoming`` (the pay day has not come yet).
* **The one income figure** (``monthly_income``): with a salary profile in that currency, this month's
  expected net; otherwise the average monthly income of the last three full months that have records (the
  shared rule of ``flows.py``). The advisor, the planner, Safe to spend's payday and the fixed-cost share
  all read it.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from . import commitment_math as M
from .flows import figures, kinds_of
from .models import Account, Transaction
from .money import quantize
from .personal import Payslip, SalaryDeductionReason, SalaryProfile, SalaryRule, SalaryRuleKind, payslip_figures

WINDOW_BEFORE = 7
WINDOW_AFTER = 10
LATE_DAYS = 3
PARTIAL_SHARE = Decimal("0.95")
MIN_SCORE = 40
HIGH, MEDIUM = 75, 50
MAX_MONTHS = 36
SALARY_WORDS = re.compile(r"(?<![\w])(salary|payroll|wps|راتب|رواتب)(?![\w])", re.IGNORECASE)

MonthStatus = Literal["received", "late", "partial", "missing", "upcoming"]
Confidence = Literal["high", "medium", "low"]
Basis = Literal["confirmed", "payslip", "suggested"]


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _month_date(month: str) -> date:
    return date(int(month[:4]), int(month[5:7]), 1)


def _add_months(month: str, count: int) -> str:
    start = _month_date(month)
    index = start.year * 12 + start.month - 1 + count
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _months_from(first: str, last: str) -> int:
    a, b = _month_date(first), _month_date(last)
    return (b.year - a.year) * 12 + b.month - a.month


def pay_date(profile: SalaryProfile, month: str) -> date:
    """The usual pay day in ``month`` (the month's last day when it is shorter)."""
    start = _month_date(month)
    return start.replace(day=min(profile.pay_day, calendar.monthrange(start.year, start.month)[1]))


def next_payday(profile: SalaryProfile, today: date) -> date:
    """The next usual pay day on or after today."""
    return M.next_due_day(profile.pay_day, today)


# -- what is expected ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Deducted:
    reason: str
    label: str | None
    amount: Decimal


@dataclass(frozen=True)
class Expected:
    month: str
    currency: str
    gross: Decimal
    deductions: list[Deducted]
    advance_repayment: Decimal
    net: Decimal
    payslip: bool  # the month's payslip gave the figures


def _active(rule: SalaryRule, month: str) -> bool:
    return rule.starts <= month and (rule.ends is None or month <= rule.ends)


def advance_repaid(rule: SalaryRule, month: str) -> Decimal:
    """What the advance takes off ``month``'s salary: ``amount`` a month from ``starts`` until ``total`` is back."""
    if rule.kind != SalaryRuleKind.ADVANCE or month < rule.starts or rule.amount <= 0:
        return Decimal(0)
    total = rule.total if rule.total is not None and rule.total > 0 else rule.amount
    before = min(total, rule.amount * _months_from(rule.starts, month))
    return max(Decimal(0), min(rule.amount, total - before))


def advance_left(rule: SalaryRule, month: str) -> Decimal:
    """What is still to be repaid of an advance after ``month``'s deduction."""
    total = rule.total if rule.total is not None and rule.total > 0 else rule.amount
    if month < rule.starts:
        return total
    return max(Decimal(0), total - rule.amount * (_months_from(rule.starts, month) + 1))


def expected_for(profile: SalaryProfile, month: str, payslips: list[Payslip]) -> Expected:
    c = profile.currency
    slips = [s for s in payslips if s.month == month and s.currency == c]
    if slips:
        gross = sum((s.gross for s in slips), Decimal(0))
        rows = [Deducted(d.kind.value, d.label, d.amount) for s in slips for d in s.deductions]
        advance = sum((d.amount for d in rows if d.reason == SalaryDeductionReason.ADVANCE.value), Decimal(0))
        net = sum((payslip_figures(s, []).net for s in slips), Decimal(0))
        return Expected(month, c, quantize(gross, c), [Deducted(d.reason, d.label, quantize(d.amount, c)) for d in rows if d.reason != "advance"],
                        quantize(advance, c), quantize(net, c), True)
    rows = [Deducted(r.reason.value, r.label, quantize(r.amount, c)) for r in profile.rules if r.kind == SalaryRuleKind.DEDUCTION and _active(r, month)]
    advance = sum((advance_repaid(r, month) for r in profile.rules), Decimal(0))
    net = max(Decimal(0), profile.expected_gross - sum((d.amount for d in rows), Decimal(0)) - advance)
    return Expected(month, c, quantize(profile.expected_gross, c), rows, quantize(advance, c), quantize(net, c), False)


# -- detection ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    transaction_id: str
    posted_on: date
    amount: Decimal
    description: str
    account_id: str
    score: int
    confidence: Confidence


@dataclass
class SalaryMonth:
    month: str
    pay_on: date
    expected: Expected
    status: MonthStatus
    received: Decimal
    transaction: Candidate | None
    basis: Basis | None
    flags: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # transaction ids the person said are not this month's salary


def _payee(description: str) -> str:
    text = re.sub(r"\d+", " ", description.lower())
    text = re.sub(r"[^\w\s&.]", " ", text)
    return " ".join(text.split())[:60]


def _confidence(score: int) -> Confidence:
    return "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"


def _first_month(profile: SalaryProfile) -> str:
    first = month_key(profile.joined_on)
    return _add_months(first, 1) if profile.joined_on > pay_date(profile, first) + timedelta(days=WINDOW_AFTER) else first


def _eligible(t: Transaction, profile: SalaryProfile, accounts: dict[str, Account], excluded: set[str]) -> bool:
    if t.amount <= 0 or t.currency != profile.currency or t.id in excluded:
        return False
    if profile.account_id:
        return t.account_id == profile.account_id
    account = accounts.get(t.account_id)
    return account is not None and account.kind in M.SPENDABLE


def _advance_credits(profile: SalaryProfile, rows: list[Transaction]) -> set[str]:
    """The credits that are a salary advance arriving (same day, the amount advanced): never a salary."""
    out: set[str] = set()
    for rule in profile.rules:
        if rule.kind != SalaryRuleKind.ADVANCE or rule.received_on is None:
            continue
        total = rule.total if rule.total is not None else rule.amount
        for t in rows:
            if abs((t.posted_on - rule.received_on).days) <= 2 and t.amount > 0 and abs(t.amount - total) <= total * Decimal("0.01"):
                out.add(t.id)
    return out


def _score(t: Transaction, reference: Decimal, pay_on: date, payers: set[str], employer: str) -> int | None:
    payer = _payee(t.description)
    known = bool(payer) and payer in payers
    named = bool(SALARY_WORDS.search(t.description)) or (len(employer) >= 3 and employer in t.description.casefold())
    gap = abs(t.amount - reference) / reference if reference > 0 else Decimal(1)
    if gap <= Decimal("0.02"):
        amount = 40
    elif gap <= Decimal("0.10"):
        amount = 30
    elif gap <= Decimal("0.25"):
        amount = 15
    elif (known or named) and reference > 0 and Decimal("0.3") <= t.amount / reference <= Decimal("1.5"):
        amount = 5  # the usual payer, a partial or unusual amount
    else:
        return None
    days = abs((t.posted_on - pay_on).days)
    when = 30 if days <= 1 else 22 if days <= 3 else 12 if days <= 7 else 5
    who = 30 if known else 20 if named else 0
    return amount + when + who


def salary_months(profile: SalaryProfile, transactions: list[Transaction], accounts: list[Account], today: date, payslips: list[Payslip],
                  *, lending_ids: frozenset[str] = frozenset(), payslip_credits: dict[str, set[str]] | None = None) -> list[SalaryMonth]:
    """Every month from the joining month to this month, oldest first (at most ``MAX_MONTHS``).

    ``payslip_credits`` maps a payslip month to the transaction ids linked to that payslip (the salary credit)."""
    by_account = {a.id: a for a in accounts}
    by_id = {t.id: t for t in transactions}
    last = month_key(today)
    first = _first_month(profile)
    if first > last:
        return []
    first = max(first, _add_months(last, -(MAX_MONTHS - 1)))
    excluded = set(lending_ids) | _advance_credits(profile, transactions)
    confirmed = {d.month: d.transaction_id for d in profile.decisions if d.decision == "confirmed"}
    rejected: dict[str, set[str]] = {}
    for d in profile.decisions:
        if d.decision == "rejected":
            rejected.setdefault(d.month, set()).add(d.transaction_id)
    linked = payslip_credits or {}
    used = {tid for tid in confirmed.values()} | {tid for ids in linked.values() for tid in ids}
    payers = {_payee(by_id[tid].description) for tid in used if tid in by_id} - {""}
    employer = " ".join(profile.employer.casefold().split())
    out: list[SalaryMonth] = []
    latest: Decimal | None = None
    month = first
    while month <= last:
        pay_on = pay_date(profile, month)
        expected = expected_for(profile, month, payslips)
        chosen: Candidate | None = None
        basis: Basis | None = None
        own = linked.get(month, set())
        if month in confirmed and confirmed[month] in by_id:
            basis = "confirmed"
            t = by_id[confirmed[month]]
            chosen = Candidate(t.id, t.posted_on, t.amount, t.description[:120], t.account_id, 100, "high")
        elif own and any(tid in by_id for tid in own):
            basis = "payslip"
            rows = [by_id[tid] for tid in sorted(own) if tid in by_id]
            t = rows[0]
            chosen = Candidate(t.id, t.posted_on, sum((r.amount for r in rows), Decimal(0)), t.description[:120], t.account_id, 100, "high")
        else:
            reference = latest if latest is not None else expected.net
            start, end = pay_on - timedelta(days=WINDOW_BEFORE), pay_on + timedelta(days=WINDOW_AFTER)
            best: tuple[int, Transaction] | None = None
            for t in transactions:
                if not (start <= t.posted_on <= end) or t.id in used or t.id in rejected.get(month, set()):
                    continue
                if not _eligible(t, profile, by_account, excluded):
                    continue
                score = _score(t, reference, pay_on, payers, employer)
                if score is None or score < MIN_SCORE:
                    continue
                if best is None or (score, -abs((t.posted_on - pay_on).days), t.id) > (best[0], -abs((best[1].posted_on - pay_on).days), best[1].id):
                    best = (score, t)
            if best is not None:
                score, t = best
                basis = "suggested"
                chosen = Candidate(t.id, t.posted_on, t.amount, t.description[:120], t.account_id, min(score, 100), _confidence(score))
        flags: list[str] = []
        if chosen is not None:
            used.add(chosen.transaction_id)
            if basis != "suggested":
                latest = chosen.amount
            if chosen.posted_on > pay_on + timedelta(days=LATE_DAYS):
                flags.append("late")
            if expected.net > 0 and chosen.amount < expected.net * PARTIAL_SHARE:
                flags.append("partial")
            status: MonthStatus = "partial" if "partial" in flags else "late" if "late" in flags else "received"
            received = quantize(chosen.amount, profile.currency)
        else:
            status = "upcoming" if today <= pay_on + timedelta(days=LATE_DAYS) else "missing"
            if status == "missing":
                flags.append("missing")
            received = quantize(Decimal(0), profile.currency)
        out.append(SalaryMonth(month=month, pay_on=pay_on, expected=expected, status=status, received=received, transaction=chosen, basis=basis,
                               flags=flags, rejected=sorted(rejected.get(month, set()))))
        month = _add_months(month, 1)
    return out


# -- the one income figure ----------------------------------------------------------------------------


IncomeSource = Literal["salary_profile", "records"]


@dataclass(frozen=True)
class MonthlyIncome:
    currency: str
    amount: Decimal | None
    source: IncomeSource | None
    next_pay_on: date | None = None  # with a salary profile


def records_income(transactions: list[Transaction], accounts: list[Account], lending_ids: frozenset[str], today: date, currency: str) -> Decimal | None:
    """The average monthly income over the last three full months that have records (the shared rule: no
    transfers, refunds, cashback or money lent or borrowed with a person)."""
    kinds = kinds_of(accounts)
    rows = [t for t in transactions if t.currency == currency]
    if not rows:
        return None
    first = min(t.posted_on for t in rows)
    months = [(s, e) for s, e in M.previous_months(today, 3) if first <= s + timedelta(days=7)]
    if not months:
        return None
    total = Decimal(0)
    for s, e in months:
        total += figures([t for t in rows if s <= t.posted_on <= e], kinds, lending_ids).income.get(currency, Decimal(0))
    if total <= 0:
        return None
    return quantize(total / len(months), currency)


def monthly_income(*, currency: str, today: date, transactions: list[Transaction], accounts: list[Account], lending_ids: frozenset[str],
                   profile: SalaryProfile | None, payslips: list[Payslip]) -> MonthlyIncome:
    """The one monthly income figure: the salary profile's expected net for this month when the profile is in
    ``currency``, else the average of the records."""
    if profile is not None and profile.currency == currency:
        net = expected_for(profile, month_key(today), payslips).net
        return MonthlyIncome(currency, net if net > 0 else None, "salary_profile", next_payday(profile, today))
    found = records_income(transactions, accounts, lending_ids, today, currency)
    return MonthlyIncome(currency, found, "records" if found is not None else None)


__all__ = [
    "Candidate", "Deducted", "Expected", "LATE_DAYS", "MonthlyIncome", "SalaryMonth", "WINDOW_AFTER", "WINDOW_BEFORE", "advance_left", "advance_repaid",
    "expected_for", "month_key", "monthly_income", "next_payday", "pay_date", "records_income", "salary_months",
]
