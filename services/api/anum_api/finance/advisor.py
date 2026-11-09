"""The weekly Money advisor: pure arithmetic on the person's own records (docs/finance.md#money-advisor).

No I/O and no model call: the same inputs always give the same note. Everything is per currency and
nothing is ever added across currencies. ANUM only advises: nothing here moves money, pays a bill or
touches a bank or a card.

* **Survival runway** if income stops: liquid money (bank and cash balances from the ledger; a card's
  available limit is shown on its own and never counted) divided by the monthly outflow, three ways:
  bare minimum (recurring bills and debt minimums), essentials (plus the average of the essential
  spending categories over the last three full months) and current spending (every spending category,
  never less than essentials). With fewer than 30 days of records only the bare minimum is worked out
  and the note says so instead of guessing.
* **Where money went**: the last 7 days and this month to date, against the 7 days before, the same days
  of last month and the three-month average; top categories and payees; categories already 30% above a
  usual whole month, and categories new this week.
* **Coming payments** in the next 30 days: saved bills, debt and card minimums, and recurring payments
  found in the records but not saved as bills (subscriptions marked).
* **Card advice** from real data only: what each card owes, its limit when the person entered one, the
  minimum, the due date, and what paying only the minimum costs when the person entered the card's
  rate. A rate is never invented.
* **Actions**: a short ranked list of next steps from fixed English and Arabic templates
  (``advisor_text.py``); a step that needs missing data says what to add.
"""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from . import commitment_math as M
from . import obligations as O
from .flows import Flow, figures, flow_of, kinds_of, spend_rows
from .models import Account, AccountKind, Bill, Category, Debt, DebtKind, Transaction
from .money import minor_digits, quantize
from .summary import _payee, detect_recurring, next_after

VERSION = 1
DAYS_PER_MONTH = Decimal("30.4375")  # 365.25 / 12
MIN_HISTORY_DAYS = 30
AVERAGE_MONTHS = 3
COMING_DAYS = 30
SOON_DAYS = 7
RUNWAY_LOW_DAYS = 60
RUNWAY_TARGET_DAYS = 90
STALE_DAYS = 7
SPIKE_RATIO = Decimal("1.3")
HIGH_UTILIZATION = 30
MAX_ACTIONS = 10
SALARY_WINDOW_DAYS = 60
# A month counts towards the average only when the records start in its first week.
MONTH_COVERAGE_DAYS = 6
LOOKBACK_DAYS = 400

ESSENTIAL: frozenset[Category] = frozenset({
    Category.HOUSING, Category.UTILITIES, Category.TELECOM, Category.GROCERIES,
    Category.TRANSPORT, Category.FUEL, Category.HEALTH, Category.EDUCATION,
})
SPENDABLE = M.SPENDABLE
_SALARY = re.compile(r"(?<![\w])(salary|payroll|wps|راتب)(?![\w])", re.IGNORECASE)
_SUBSCRIPTION = re.compile(r"(?<![\w])(netflix|spotify|shahid|osn|anghami|apple\.com|icloud|youtube|prime|subscription|google storage)(?![\w])",
                           re.IGNORECASE)

NOTICE_EN = "ANUM only advises. It never moves money, pays a bill or touches a bank or card."
NOTICE_AR = "ANUM يقدّم النصيحة فقط. لا ينقل المال أبدًا ولا يدفع فاتورة ولا يتعامل مع بنك أو بطاقة."


# -- inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdvisorInputs:
    """Everything the note is worked out from: read from the person's own records, nothing else."""

    today: date
    timezone: str
    base_currency: str
    accounts: list[Account]
    balances: dict[str, Decimal]  # account id: sum of its transactions (the opening balance is added here)
    transactions: list[Transaction]  # the last ``LOOKBACK_DAYS`` days, up to today
    bills: list[Bill]
    debts: list[Debt]
    card_limits: dict[str, Decimal] = field(default_factory=dict)  # card account id: credit limit the person entered
    observations: dict[str, tuple[Decimal, datetime]] = field(default_factory=dict)  # last balance a bank message stated
    bill_links: list = field(default_factory=list)  # the person's ``bill`` links: a matched due date is paid
    # Transactions lent or borrowed with a person: transfers by the shared rule (``flows.py``).
    lending_ids: frozenset[str] = frozenset()


# -- output ------------------------------------------------------------------------------------

RunwayStatus = Literal["ok", "not_enough_history", "no_records"]
ScenarioKind = Literal["bare", "essential", "full"]


class Scenario(BaseModel):
    kind: ScenarioKind
    monthly_outflow: Decimal
    days: int | None  # None: nothing goes out, so the money does not run out on these figures
    runs_out_on: date | None  # None also when the day is past the last date the calendar holds


class EssentialCategory(BaseModel):
    category: Category
    monthly_average: Decimal
    bills_monthly: Decimal  # bills saved in this category, taken out so nothing is counted twice
    counted: Decimal


class Runway(BaseModel):
    status: RunwayStatus
    history_days: int
    first_record_on: date | None
    basis: Literal["months", "last_30_days", "none"]
    months: list[str]  # the full months averaged, "YYYY-MM"
    liquid: Decimal
    accounts_counted: int
    bills_monthly: Decimal
    minimums_monthly: Decimal
    essential_spending_monthly: Decimal | None
    total_spending_monthly: Decimal | None
    essential_categories: list[EssentialCategory]
    scenarios: list[Scenario]  # bare always; essential and full only with enough history


class PeriodSpend(BaseModel):
    start: date
    end: date
    amount: Decimal
    previous: Decimal
    previous_start: date
    previous_end: date
    change_percent: int | None  # None when the previous period had nothing
    average: Decimal | None  # the three-month average for a period this long
    vs_average_percent: int | None


class CategoryLine(BaseModel):
    category: Category
    week: Decimal
    previous_week: Decimal
    month: Decimal
    previous_month: Decimal
    average_month: Decimal | None


class PayeeLine(BaseModel):
    name: str
    amount: Decimal
    count: int


class Spike(BaseModel):
    category: Category
    period: Literal["month", "new"]  # new: nothing in this category in the averaged months
    amount: Decimal
    expected: Decimal
    percent: int | None


class Spending(BaseModel):
    week: PeriodSpend
    month: PeriodSpend
    categories: list[CategoryLine]
    payees_week: list[PayeeLine]
    payees_month: list[PayeeLine]
    spikes: list[Spike]


ComingKind = Literal["bill", "minimum", "card_minimum", "recurring", "subscription"]


class ComingPayment(BaseModel):
    kind: ComingKind
    name: str
    ref_id: str | None
    amount: Decimal
    due_on: date | None  # None: a minimum with no due day set
    days_until: int | None
    category: Category | None = None
    detected: bool = False  # found in the records, not saved as a bill


class Coming(BaseModel):
    items: list[ComingPayment]
    total: Decimal
    saved_total: Decimal  # bills and minimums only
    detected_total: Decimal
    within_7_days: Decimal


class CurrencyAdvice(BaseModel):
    currency: str
    runway: Runway
    card_available: Decimal | None  # never counted as money you have
    income_month: Decimal
    spend_month: Decimal
    spending: Spending
    coming: Coming


CardMissing = Literal["debt_record", "rate", "due_day", "minimum", "limit"]


class CardAdvice(BaseModel):
    name: str
    account_id: str | None
    debt_id: str | None
    currency: str
    last4: str | None
    owed: Decimal
    owed_source: Literal["ledger", "debt"]
    observed_balance: Decimal | None  # the last balance a bank message stated
    observed_at: datetime | None
    credit_limit: Decimal | None
    available: Decimal | None
    utilization_percent: int | None
    minimum_payment: Decimal | None
    due_on: date | None
    days_until: int | None
    apr_percent: Decimal | None  # None when the person entered no rate
    interest_if_minimum: Decimal | None  # about one month's interest on what the minimum leaves
    months_to_clear_at_minimum: int | None
    total_interest_at_minimum: Decimal | None
    never_clears_at_minimum: bool
    missing: list[CardMissing]


Severity = Literal["urgent", "high", "medium", "info"]


class Action(BaseModel):
    code: str
    severity: Severity
    currency: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    text_en: str
    text_ar: str
    tab: str | None = None  # the Finance page to open for it


class Income(BaseModel):
    salary_found: bool
    last_salary_on: date | None


class AdvisorNote(BaseModel):
    version: int = VERSION
    today: date
    timezone: str
    base_currency: str
    week_start: date
    week_end: date
    month_start: date
    newest_record_on: date | None
    history_days: int
    has_accounts: bool
    currencies: list[CurrencyAdvice]
    cards: list[CardAdvice]
    income: Income
    actions: list[Action]
    notice_en: str = NOTICE_EN
    notice_ar: str = NOTICE_AR


# -- helpers -----------------------------------------------------------------------------------


def _ceil(amount: Decimal, currency: str) -> Decimal:
    return amount.quantize(Decimal(1).scaleb(-minor_digits(currency)), rounding=ROUND_CEILING)


def _q(amount: Decimal, currency: str) -> Decimal:
    return quantize(amount, currency)


def _percent(now: Decimal, before: Decimal) -> int | None:
    if before <= 0:
        return None
    return int(((now - before) * 100 / before).to_integral_value())


# One rule for every reader of bills and debts (commitment_math.py).
month_start = M.month_start
month_end = M.month_end
previous_months = M.previous_months
next_due_day = M.next_due_day


def scenario(kind: ScenarioKind, liquid: Decimal, outflow: Decimal, today: date, currency: str) -> Scenario:
    """Days the liquid money lasts at ``outflow`` a month, rounded down, and the day it runs out."""
    outflow = _q(outflow, currency)
    if outflow <= 0:
        return Scenario(kind=kind, monthly_outflow=outflow, days=None, runs_out_on=None)
    if liquid <= 0:
        return Scenario(kind=kind, monthly_outflow=outflow, days=0, runs_out_on=today)
    days = int((liquid * DAYS_PER_MONTH / outflow).to_integral_value(rounding=ROUND_FLOOR))
    # A runway past the last date the calendar can hold keeps its day count and has no end date.
    ends = today + timedelta(days=days) if days <= (date.max - today).days else None
    return Scenario(kind=kind, monthly_outflow=outflow, days=days, runs_out_on=ends)


def _spend(rows: list[Transaction], kinds: dict[str, AccountKind] | None = None, lending: frozenset[str] | None = None) -> list[Transaction]:
    """Purchases and the refunds filed in their categories (net spend, the shared rule in ``flows.py``)."""
    return spend_rows(rows, kinds, lending)


def _between(rows: list[Transaction], start: date, end: date) -> list[Transaction]:
    return [t for t in rows if start <= t.posted_on <= end]


def _by_category(rows: list[Transaction]) -> dict[Category, Decimal]:
    out: dict[Category, Decimal] = defaultdict(Decimal)
    for t in rows:
        out[t.category] -= t.amount
    return out


def _sum_out(rows: list[Transaction]) -> Decimal:
    return sum((-t.amount for t in rows), Decimal(0))


def interest_at_minimum(owed: Decimal, apr: Decimal, minimum: Decimal, currency: str) -> tuple[Decimal, int | None, Decimal | None, bool]:
    """Next month's interest when only the minimum is paid, then months to clear and the interest paid
    until then (simple monthly rate, APR / 12). One order everywhere: each month the minimum is paid
    first, then interest is charged on what is left. ``never`` when the minimum does not even cover
    the interest, so the balance never goes down."""
    rate = apr / Decimal(1200)
    first = _ceil(max(owed - minimum, Decimal(0)) * rate, currency)
    if minimum <= 0:
        return first, None, None, True
    remaining, months, paid_interest = owed, 0, Decimal(0)
    while remaining > 0 and months < 600:
        remaining -= min(minimum, remaining)
        months += 1
        interest = remaining * rate
        if remaining > 0 and interest >= minimum:
            return first, None, None, True
        remaining += interest
        paid_interest += interest
    if remaining > 0:
        return first, None, None, True
    return first, months, _ceil(paid_interest, currency), False


# -- the note ----------------------------------------------------------------------------------


def _history(rows: list[Transaction], today: date) -> tuple[date | None, int]:
    first = min((t.posted_on for t in rows), default=None)
    return first, ((today - first).days + 1 if first else 0)


def _averages(spend: list[Transaction], first: date | None, days: int, today: date) -> tuple[Literal["months", "last_30_days", "none"], list[str], dict[Category, Decimal]]:
    """The monthly average per category: the last three full months the records cover, else the last 30 days."""
    if first is None or days < MIN_HISTORY_DAYS:
        return "none", [], {}
    months = [(s, e) for s, e in previous_months(today, AVERAGE_MONTHS) if first <= s + timedelta(days=MONTH_COVERAGE_DAYS)]
    if months:
        totals: dict[Category, Decimal] = defaultdict(Decimal)
        for s, e in months:
            for category, amount in _by_category(_between(spend, s, e)).items():
                totals[category] += amount
        count = Decimal(len(months))
        return "months", [f"{s.year:04d}-{s.month:02d}" for s, _ in sorted(months)], {c: v / count for c, v in totals.items()}
    window = _between(spend, today - timedelta(days=30), today - timedelta(days=1))
    scale = DAYS_PER_MONTH / Decimal(30)
    return "last_30_days", [], {c: v * scale for c, v in _by_category(window).items()}


def _runway(currency: str, today: date, accounts: list[Account], balances: dict[str, Decimal],
            bills: list[Bill], debts: list[Debt], averages: dict[Category, Decimal], basis: str, months: list[str],
            first: date | None, days: int) -> Runway:
    spendable = [a for a in accounts if a.currency == currency and a.kind in SPENDABLE and not a.archived]
    liquid = _q(M.liquid(accounts, balances, currency), currency)
    active = [b for b in bills if b.active and b.currency == currency]
    bills_monthly = sum((M.bill_monthly(b) for b in active), Decimal(0))
    bills_by_category: dict[Category, Decimal] = defaultdict(Decimal)
    for b in active:
        bills_by_category[b.category] += M.bill_monthly(b)
    owing = [d for d in debts if d.currency == currency and M.owes_minimum(d) and M.bill_for_debt(d, active) is None]
    minimums = sum((M.debt_minimum(d) for d in owing), Decimal(0))
    # Card purchases are already in the spending averages and the card's minimum pays for them, so the
    # "current spending" outflow adds only the minimums of loans and other debts, never a card's.
    cards = {a.id for a in accounts if a.kind == AccountKind.CARD}
    loan_minimums = sum((M.debt_minimum(d) for d in owing if d.kind != DebtKind.CARD and d.account_id not in cards), Decimal(0))
    bare = scenario("bare", liquid, bills_monthly + minimums, today, currency)
    if basis == "none":
        status: RunwayStatus = "no_records" if first is None else "not_enough_history"
        return Runway(status=status, history_days=days, first_record_on=first, basis="none", months=[], liquid=liquid,
                      accounts_counted=len(spendable), bills_monthly=_q(bills_monthly, currency), minimums_monthly=_q(minimums, currency),
                      essential_spending_monthly=None, total_spending_monthly=None, essential_categories=[], scenarios=[bare])
    essentials: list[EssentialCategory] = []
    extra = Decimal(0)
    for category in sorted(ESSENTIAL, key=lambda c: c.value):
        average = averages.get(category, Decimal(0))
        in_bills = bills_by_category.get(category, Decimal(0))
        counted = max(average - in_bills, Decimal(0))
        if average > 0 or in_bills > 0:
            essentials.append(EssentialCategory(category=category, monthly_average=_q(average, currency), bills_monthly=_q(in_bills, currency),
                                                counted=_q(counted, currency)))
        extra += counted
    total = sum(averages.values(), Decimal(0))
    essential_outflow = bills_monthly + minimums + extra
    full_outflow = max(total + loan_minimums, essential_outflow)
    return Runway(
        status="ok", history_days=days, first_record_on=first, basis=basis, months=months, liquid=liquid,  # type: ignore[arg-type]
        accounts_counted=len(spendable), bills_monthly=_q(bills_monthly, currency), minimums_monthly=_q(minimums, currency),
        essential_spending_monthly=_q(extra, currency), total_spending_monthly=_q(total, currency), essential_categories=essentials,
        scenarios=[bare, scenario("essential", liquid, essential_outflow, today, currency), scenario("full", liquid, full_outflow, today, currency)],
    )


def _period(spend: list[Transaction], start: date, end: date, prev_start: date, prev_end: date, average: Decimal | None, currency: str) -> PeriodSpend:
    now = _q(_sum_out(_between(spend, start, end)), currency)
    before = _q(_sum_out(_between(spend, prev_start, prev_end)), currency)
    avg = _q(average, currency) if average is not None else None
    return PeriodSpend(start=start, end=end, amount=now, previous=before, previous_start=prev_start, previous_end=prev_end,
                       change_percent=_percent(now, before), average=avg, vs_average_percent=_percent(now, avg) if avg is not None else None)


def _payees(rows: list[Transaction], currency: str, limit: int = 5) -> list[PayeeLine]:
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in rows:
        groups[_payee(t.description) or t.description.lower()[:60]].append(t)
    lines = []
    for items in groups.values():
        newest = max(items, key=lambda t: t.posted_on)
        lines.append(PayeeLine(name=" ".join(newest.description.split())[:60], amount=_q(_sum_out(items), currency), count=len(items)))
    return sorted(lines, key=lambda p: (-p.amount, p.name.lower()))[:limit]


def _spending(currency: str, today: date, spend: list[Transaction], averages: dict[Category, Decimal], enough: bool) -> Spending:
    week_end, week_start = today - timedelta(days=1), today - timedelta(days=7)
    prev_week_end, prev_week_start = week_start - timedelta(days=1), week_start - timedelta(days=7)
    m_start = month_start(today)
    prev_start = month_start(m_start - timedelta(days=1))
    prev_end = prev_start.replace(day=min(today.day, calendar.monthrange(prev_start.year, prev_start.month)[1]))
    total_avg = sum(averages.values(), Decimal(0)) if enough else None
    week_avg = total_avg * 7 / DAYS_PER_MONTH if total_avg is not None else None
    week = _period(spend, week_start, week_end, prev_week_start, prev_week_end, week_avg, currency)
    month = _period(spend, m_start, today, prev_start, prev_end, total_avg, currency)
    w_rows, pw_rows = _between(spend, week_start, week_end), _between(spend, prev_week_start, prev_week_end)
    m_rows, pm_rows = _between(spend, m_start, today), _between(spend, prev_start, prev_end)
    w, pw, m, pm = _by_category(w_rows), _by_category(pw_rows), _by_category(m_rows), _by_category(pm_rows)
    categories = sorted({*w, *pw, *m, *pm, *(averages if enough else {})}, key=lambda c: c.value)
    lines = [
        CategoryLine(category=c, week=_q(w.get(c, Decimal(0)), currency), previous_week=_q(pw.get(c, Decimal(0)), currency),
                     month=_q(m.get(c, Decimal(0)), currency), previous_month=_q(pm.get(c, Decimal(0)), currency),
                     average_month=_q(averages.get(c, Decimal(0)), currency) if enough else None)
        for c in categories
    ]
    lines.sort(key=lambda line: (-line.month, -line.week, line.category.value))
    spikes: list[Spike] = []
    if enough and total_avg:
        floor = total_avg * Decimal("0.05")
        for category, average in averages.items():
            # Already more than a usual whole month (so a rent paid on the 1st is not a spike).
            so_far = m.get(category, Decimal(0))
            if average > 0 and so_far >= average * SPIKE_RATIO and so_far - average >= floor:
                spikes.append(Spike(category=category, period="month", amount=_q(so_far, currency), expected=_q(average, currency),
                                    percent=_percent(so_far, average) or 0))
        # A category with no spending in the averaged months that took a real share of a usual month in 7 days.
        for category, amount in w.items():
            if averages.get(category, Decimal(0)) <= 0 and amount >= total_avg * Decimal("0.1"):
                spikes.append(Spike(category=category, period="new", amount=_q(amount, currency), expected=_q(Decimal(0), currency), percent=None))
        spikes.sort(key=lambda s: (-(s.amount - s.expected), s.category.value))
    return Spending(week=week, month=month, categories=lines[:10], payees_week=_payees(w_rows, currency), payees_month=_payees(m_rows, currency),
                    spikes=spikes[:5])


def _coming(today: date, bills: list[Bill], debts: list[Debt], card_debt_ids: set[str], recurring: list, currency: str,  # type: ignore[type-arg]
            bill_links: list | None = None) -> Coming:
    end = today + timedelta(days=COMING_DAYS)
    items: list[ComingPayment] = []
    # Due dates with a matched payment are paid; this month's passed ones without one are still to pay.
    for found in O.unpaid([b for b in bills if b.currency == currency], bill_links or [], today, end):
        bill, due = found.bill, found.due_on
        items.append(ComingPayment(kind="bill", name=bill.name, ref_id=bill.id, amount=_q(bill.amount, currency), due_on=due, days_until=(due - today).days,
                                   category=bill.category))
    for debt in debts:
        if debt.currency != currency or not M.owes_minimum(debt):
            continue
        if M.bill_for_debt(debt, bills) is not None:
            continue  # its minimum is saved as a bill above: counted once
        kind: ComingKind = "card_minimum" if debt.id in card_debt_ids or debt.kind == DebtKind.CARD else "minimum"
        amount = _q(M.debt_minimum(debt), currency)
        if isinstance(debt, O.PersonDebt) and debt.due_on is not None:
            # Borrowed from a person: due on its own date (an overdue one is still to pay).
            items.append(ComingPayment(kind=kind, name=debt.name, ref_id=debt.id, amount=amount, due_on=debt.due_on,
                                       days_until=(debt.due_on - today).days))
            continue
        if debt.due_day is None:
            items.append(ComingPayment(kind=kind, name=debt.name, ref_id=debt.id, amount=amount, due_on=None, days_until=None))
            continue
        due = next_due_day(debt.due_day, today)
        if due <= end:
            items.append(ComingPayment(kind=kind, name=debt.name, ref_id=debt.id, amount=amount, due_on=due, days_until=(due - today).days))
    for found in recurring:
        if found.currency != currency:
            continue
        subscription = found.category == Category.SUBSCRIPTIONS or bool(_SUBSCRIPTION.search(found.name))
        # Every date in the window, by cadence, as saved bills are (a weekly payment counts several times).
        due: date | None = found.next_due_on
        anchor = found.next_due_on.day
        while due is not None and due <= end:
            if due >= today:
                items.append(ComingPayment(kind="subscription" if subscription else "recurring", name=found.name, ref_id=None,
                                           amount=_q(found.amount, currency), due_on=due, days_until=(due - today).days, category=found.category,
                                           detected=True))
            due = next_after(due, found.cadence, anchor)
    items.sort(key=lambda i: (i.due_on is None, i.due_on or today, i.name.lower()))
    saved = sum((i.amount for i in items if not i.detected), Decimal(0))
    detected = sum((i.amount for i in items if i.detected), Decimal(0))
    soon = sum((i.amount for i in items if i.days_until is not None and i.days_until <= SOON_DAYS), Decimal(0))
    return Coming(items=items, total=_q(saved + detected, currency), saved_total=_q(saved, currency), detected_total=_q(detected, currency),
                  within_7_days=_q(soon, currency))


def _cards(inputs: AdvisorInputs) -> list[CardAdvice]:
    today = inputs.today
    debts_by_account = {d.account_id: d for d in inputs.debts if d.account_id}
    linked_debts: set[str] = set()
    out: list[CardAdvice] = []

    def advice(name: str, currency: str, account: Account | None, debt: Debt | None, owed: Decimal, source: Literal["ledger", "debt"]) -> CardAdvice:
        missing: list[CardMissing] = []
        if debt is None:
            missing.append("debt_record")
        apr = debt.apr_percent if debt is not None and debt.apr_percent > 0 else None
        minimum = min(debt.minimum_payment, owed) if debt is not None and debt.minimum_payment > 0 and owed > 0 else (
            debt.minimum_payment if debt is not None and debt.minimum_payment > 0 else None)
        due = next_due_day(debt.due_day, today) if debt is not None and debt.due_day else None
        if debt is not None:
            if apr is None:
                missing.append("rate")
            if debt.due_day is None:
                missing.append("due_day")
            if debt.minimum_payment <= 0:
                missing.append("minimum")
        limit = inputs.card_limits.get(account.id) if account is not None else None
        if account is not None and not limit:
            missing.append("limit")
            limit = None
        seen = inputs.observations.get(account.id) if account is not None else None
        interest = months = total = None
        never = False
        if apr is not None and minimum is not None and owed > 0:
            interest, months, total, never = interest_at_minimum(owed, apr, minimum, currency)
        return CardAdvice(
            name=name, account_id=account.id if account else None, debt_id=debt.id if debt else None, currency=currency,
            last4=account.last4 if account else None, owed=_q(owed, currency), owed_source=source,
            observed_balance=seen[0] if seen else None, observed_at=seen[1] if seen else None,
            credit_limit=limit, available=_q(max(limit - owed, Decimal(0)), currency) if limit else None,
            utilization_percent=min(999, int((owed * 100 / limit).to_integral_value())) if limit else None,
            minimum_payment=_q(minimum, currency) if minimum is not None else None, due_on=due, days_until=(due - today).days if due else None,
            apr_percent=apr, interest_if_minimum=interest, months_to_clear_at_minimum=months, total_interest_at_minimum=total,
            never_clears_at_minimum=never, missing=missing,
        )

    for account in inputs.accounts:
        if account.kind != AccountKind.CARD or account.archived:
            continue
        balance = account.opening_balance + inputs.balances.get(account.id, Decimal(0))
        owed = max(-balance, Decimal(0))
        debt = debts_by_account.get(account.id)
        source: Literal["ledger", "debt"] = "ledger"
        if debt is not None:
            linked_debts.add(debt.id)
            if owed == 0 and debt.balance > 0:
                owed, source = debt.balance, "debt"
        out.append(advice(account.name, account.currency, account, debt, owed, source))
    for debt in inputs.debts:
        if debt.kind == DebtKind.CARD and debt.id not in linked_debts and (debt.account_id is None or debt.account_id not in {a.id for a in inputs.accounts}):
            out.append(advice(debt.name, debt.currency, None, debt, max(debt.balance, Decimal(0)), "debt"))
    return sorted(out, key=lambda c: (c.due_on is None, c.due_on or today, -c.owed, c.name.lower()))


def _income(rows: list[Transaction], today: date, kinds: dict[str, AccountKind] | None = None, lending: frozenset[str] | None = None) -> Income:
    window = [t for t in rows if t.amount > 0 and flow_of(t, kinds, lending) == Flow.INCOME and t.posted_on >= today - timedelta(days=SALARY_WINDOW_DAYS)]
    salary = [t for t in window if _SALARY.search(t.description)]
    if not salary:
        # A steady monthly credit from the same payer counts as a salary too.
        groups: dict[str, list[Transaction]] = defaultdict(list)
        for t in window:
            groups[_payee(t.description)].append(t)
        salary = next((sorted(items, key=lambda t: t.posted_on) for key, items in groups.items()
                       if key and len(items) >= 2 and 25 <= (max(i.posted_on for i in items) - min(i.posted_on for i in items)).days <= 35), [])
    return Income(salary_found=bool(salary), last_salary_on=max((t.posted_on for t in salary), default=None))


def build_note(inputs: AdvisorInputs) -> AdvisorNote:
    """The whole note, worked out from ``inputs`` only."""
    from . import advisor_text

    today = inputs.today
    lend = inputs.lending_ids
    rows = [t for t in inputs.transactions if t.posted_on <= today]
    card_debt_ids = {d.id for d in inputs.debts if d.kind == DebtKind.CARD} | {
        d.id for d in inputs.debts if d.account_id and any(a.id == d.account_id and a.kind == AccountKind.CARD for a in inputs.accounts)}
    recurring = detect_recurring([t for t in rows if t.id not in lend], today, [b.name for b in inputs.bills], inputs.bills)
    kinds = kinds_of(inputs.accounts)
    currencies = sorted(
        {a.currency for a in inputs.accounts if not a.archived} | {b.currency for b in inputs.bills if b.active}
        | {d.currency for d in inputs.debts if d.balance > 0} | {t.currency for t in rows},
        key=lambda c: (c != inputs.base_currency, c),
    )
    cards = _cards(inputs)
    blocks: list[CurrencyAdvice] = []
    for currency in currencies:
        mine = [t for t in rows if t.currency == currency]
        spend = _spend(mine, kinds, lend)
        first, days = _history(mine, today)
        basis, months, averages = _averages(spend, first, days, today)
        runway = _runway(currency, today, inputs.accounts, inputs.balances, inputs.bills, inputs.debts, averages, basis, months, first, days)
        limits = [c.available for c in cards if c.currency == currency and c.available is not None]
        m_start = month_start(today)
        month_rows = _between(mine, m_start, today)
        blocks.append(CurrencyAdvice(
            currency=currency, runway=runway, card_available=_q(sum(limits, Decimal(0)), currency) if limits else None,
            # The same income rule as the summary: no refunds, cashback or transfers.
            income_month=_q(figures(month_rows, kinds, lend).income.get(currency, Decimal(0)), currency),
            spend_month=_q(_sum_out(_spend(month_rows, kinds, lend)), currency),
            spending=_spending(currency, today, spend, averages, basis != "none"),
            coming=_coming(today, inputs.bills, inputs.debts, card_debt_ids, recurring, currency, inputs.bill_links),
        ))
    _first, days_all = _history(rows, today)
    note = AdvisorNote(
        today=today, timezone=inputs.timezone, base_currency=inputs.base_currency, week_start=today - timedelta(days=7),
        week_end=today - timedelta(days=1), month_start=month_start(today), newest_record_on=max((t.posted_on for t in rows), default=None),
        history_days=days_all, has_accounts=any(not a.archived for a in inputs.accounts), currencies=blocks, cards=cards,
        income=_income(rows, today, kinds, lend), actions=[],
    )
    note.actions = advisor_text.actions(note)
    return note


__all__ = [
    "AdvisorInputs", "AdvisorNote", "Action", "CardAdvice", "Coming", "ComingPayment", "CurrencyAdvice", "ESSENTIAL", "MIN_HISTORY_DAYS",
    "Runway", "Scenario", "Spending", "build_note", "interest_at_minimum", "month_end", "month_start", "next_due_day", "previous_months", "scenario",
]
