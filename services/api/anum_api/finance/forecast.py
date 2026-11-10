"""The cash-flow forecast and the emergency reserve (docs/finance.md#forecast, #emergency-reserve).

Pure: no store, clock or network. The same inputs always give the same forecast. It answers "where will my bank
and cash money be at the end of this month, and over the next three months, if things go as usual?", per
currency, day by day, from the person's own records only. **An estimate, never a promise**: every line says
whether it is today's actual balance or an expected movement, and every assumption is written out.

It reuses the shared figures, never a second calculation:

* **Start:** today's balance of every bank and cash account that is not archived (``commitment_math.liquid``,
  the same money Safe to spend counts). Cards, loans and investments are not cash.
* **Salary** from the salary profile (``salary.py``): the expected net on each usual pay day (gross less the
  deductions active that month less the advance repayment; a payslip for the month overrides it). This month's
  salary is left out once a credit for it is found (it is already in the balance), and named when it is missing.
* **Other recurring income** only when it is detected with confidence: the same payer, monthly, three times or
  more, amounts within 15 percent (``summary.detect_recurring``'s rule, read on money in). The low band leaves it out.
* **Bills** due and not matched to a payment (``obligations.unpaid``); an overdue one counts today.
* **Debt minimums** (``commitment_math.debt_minimum``, at most what is left) on their due day each month; a
  minimum whose day passed this month without a matched payment is overdue and counts today; one also saved
  as a bill is counted once, as the bill; money borrowed from a person counts what is left on its due date.
* **Installments** (Tamara, Tabby, ...) still due from the provider statements, on their due dates.
* **Regular remittances** (sending money home marked "every month"): the newest amount plus fee per recipient.
* **Expected one-off income** the person entered (a bonus, a refund, a sale) on its date until it is marked received;
  a date that passed without it being marked received is left out and named. Like other recurring income it is not on
  the low line.
* **Card interest** where the card's yearly rate (APR) is known: each month the balance left after the minimum payment
  times the rate over 12, added to the card on its due day (the month's last day without one). It is owed to the card,
  so it is shown beside the cash line and does not take bank money until it is paid; cards without a rate are named.
* **Spending** that is not a saved bill: the average monthly spending from bank and cash accounts over the last
  three full months (the advisor's averaging rule, ``advisor._averages``), less the saved bills in the same
  categories, taken off every day from tomorrow. Card purchases are left to the card's minimum payment.

The **band**: the low line spends at the busiest averaged month's pace and leaves out other recurring income; the
high line spends at the quietest month's pace. With fewer than two full months, the band is 10 percent of the
spending either way. **Warnings** give the first day the expected balance goes below zero, below the emergency
reserve target, or (on the low line only) may go below zero.

Different currencies are never added together or converted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from . import commitment_math as M
from . import obligations as O
from . import personal as P
from . import salary as SAL
from .flows import Flow, flow_of, kinds_of, spend_rows
from .models import Account, Bill, Cadence, Category, Debt, DebtKind, Goal, GoalPurpose, InstallmentStatementEntry, Transaction
from .money import minor_digits, quantize

NOTE = "An estimate from your own records, not a promise and not advice. ANUM never moves money."
HORIZON_MONTHS = 3  # this month and the two after it
DAYS_PER_MONTH = Decimal("30.4375")
DEFAULT_BAND = Decimal("0.10")
MIN_INCOME_OCCURRENCES = 3

EventKind = Literal["salary", "income", "expected_income", "bill", "minimum_payment", "installment", "remittance"]
EventStatus = Literal["expected", "overdue", "late"]
WarningKind = Literal["below_zero", "below_reserve", "may_go_below_zero"]


class ForecastEvent(BaseModel):
    on: date
    kind: EventKind
    label: str
    ref_id: str | None = None
    currency: str
    amount: Decimal  # signed: money in is positive, money out negative
    status: EventStatus = "expected"
    note: str  # a code the clients word in English or Arabic
    params: dict[str, str] = Field(default_factory=dict)


class ForecastPoint(BaseModel):
    on: date
    expected: Decimal
    low: Decimal
    high: Decimal


class MonthEnd(BaseModel):
    month: str
    on: date
    expected: Decimal
    low: Decimal
    high: Decimal
    money_in: Decimal  # expected salary and income in the month (from today)
    money_out: Decimal  # bills, minimums, installments and remittances in the month (from today)
    spending: Decimal  # the average spending taken off in the month (from tomorrow)
    card_interest: Decimal = Decimal(0)  # interest expected on cards with a known rate (owed to the card, not cash)


class InterestCharge(BaseModel):
    """Interest a card is expected to charge on a day: the balance after the minimum payment times the APR over 12."""

    on: date
    label: str
    ref_id: str
    balance: Decimal  # what the interest is charged on
    apr_percent: Decimal
    amount: Decimal


class ForecastWarning(BaseModel):
    kind: WarningKind
    on: date
    balance: Decimal
    threshold: Decimal


class Assumption(BaseModel):
    code: str
    params: dict[str, str] = Field(default_factory=dict)
    detail: str  # the same in English, for the assistant


class SpendBasis(BaseModel):
    basis: Literal["months", "last_30_days", "none"]
    months: list[str]  # the full months averaged, "YYYY-MM"
    monthly: Decimal  # average monthly spending outside saved bills
    daily: Decimal
    daily_low: Decimal  # the pace the high line uses (the quietest month)
    daily_high: Decimal  # the pace the low line uses (the busiest month)


class CurrencyForecast(BaseModel):
    currency: str
    actual: Decimal  # today's balance of bank and cash accounts: actual, not an estimate
    accounts_counted: int
    reserve_target: Decimal | None  # the emergency reserve goal's target in this currency
    spend: SpendBasis
    month_end: MonthEnd  # this month
    months: list[MonthEnd]  # this month and the next two
    points: list[ForecastPoint]
    events: list[ForecastEvent]
    warnings: list[ForecastWarning]
    assumptions: list[Assumption]
    left_out: list[Assumption]
    card_interest: list[InterestCharge] = Field(default_factory=list)


class Forecast(BaseModel):
    today: date
    month_end: date
    horizon_end: date
    timezone: str
    base_currency: str
    currencies: list[CurrencyForecast]
    note: str = NOTE


@dataclass(frozen=True)
class ForecastInputs:
    """Everything the forecast is worked out from, read from the person's own records."""

    today: date
    timezone: str
    base_currency: str
    accounts: list[Account]
    balances: dict[str, Decimal]
    transactions: list[Transaction]  # the last 400 days, up to today
    bills: list[Bill]
    debts: list[Debt]  # with today's balances (``obligations.current_debts``, money borrowed from people included)
    goals: list[Goal]
    bill_links: list = field(default_factory=list)
    debt_payments: list[O.DebtPayment] = field(default_factory=list)
    installments: list[InstallmentStatementEntry] = field(default_factory=list)
    remittances: list[P.Remittance] = field(default_factory=list)
    lending_ids: frozenset[str] = frozenset()
    salary_profile: P.SalaryProfile | None = None
    payslips: list[P.Payslip] = field(default_factory=list)
    payslip_credits: dict[str, set[str]] = field(default_factory=dict)
    expected_income: list[P.ExpectedIncome] = field(default_factory=list)


# -- helpers --------------------------------------------------------------------------------------------


def _q(amount: Decimal, currency: str) -> Decimal:
    return quantize(amount, currency)


def _floor(amount: Decimal, currency: str) -> Decimal:
    return amount.quantize(Decimal(1).scaleb(-minor_digits(currency)), rounding=ROUND_FLOOR)


def horizon_end(today: date) -> date:
    """The last day of the second month after this one (three calendar months, this one included)."""
    cursor = M.month_start(today)
    for _ in range(HORIZON_MONTHS - 1):
        cursor = M.month_start(M.month_end(cursor) + timedelta(days=1))
    return M.month_end(cursor)


def month_list(today: date) -> list[tuple[date, date]]:
    """This month and the next two, as (first day, last day)."""
    out: list[tuple[date, date]] = []
    cursor = M.month_start(today)
    for _ in range(HORIZON_MONTHS):
        out.append((cursor, M.month_end(cursor)))
        cursor = M.month_end(cursor) + timedelta(days=1)
    return out


def _on_day(start: date, day: int) -> date:
    """``day`` of the month that starts on ``start`` (the month's last day when shorter)."""
    return start.replace(day=min(day, M.month_end(start).day))


def reserve_targets(goals: list[Goal]) -> dict[str, Decimal]:
    """The emergency reserve target per currency: the targets of the goals whose purpose is the emergency fund
    (paused ones included: pausing stops the saving, not the reserve)."""
    out: dict[str, Decimal] = {}
    for goal in goals:
        if goal.purpose == GoalPurpose.EMERGENCY and goal.target_amount > 0:
            out[goal.currency] = out.get(goal.currency, Decimal(0)) + goal.target_amount
    return out


# -- spending ------------------------------------------------------------------------------------------


def spending_basis(inputs: ForecastInputs, currency: str) -> SpendBasis:
    """The average monthly spending from bank and cash accounts outside saved bills, and the band's two paces."""
    from .advisor import AVERAGE_MONTHS, MIN_HISTORY_DAYS, MONTH_COVERAGE_DAYS, _averages, _by_category, _between, _history

    kinds = kinds_of(inputs.accounts)
    rows = [t for t in inputs.transactions if t.currency == currency]
    cash_accounts = {a.id for a in inputs.accounts if a.kind in M.SPENDABLE}
    spend = [t for t in spend_rows(rows, kinds, inputs.lending_ids) if t.account_id in cash_accounts]
    first, days = _history(rows, inputs.today)
    basis, months, averages = _averages(spend, first, days, inputs.today)
    in_bills: dict[Category, Decimal] = defaultdict(Decimal)
    for bill in inputs.bills:
        if bill.active and bill.currency == currency:
            in_bills[bill.category] += M.bill_monthly(bill)

    def outside(by_category: dict[Category, Decimal]) -> Decimal:
        return sum((max(v - in_bills.get(c, Decimal(0)), Decimal(0)) for c, v in by_category.items()), Decimal(0))

    monthly = outside(averages)
    daily = monthly / DAYS_PER_MONTH
    low_pace = high_pace = None
    if basis == "months" and first is not None and days >= MIN_HISTORY_DAYS:
        totals = [outside(_by_category(_between(spend, s, e))) for s, e in M.previous_months(inputs.today, AVERAGE_MONTHS)
                  if first <= s + timedelta(days=MONTH_COVERAGE_DAYS)]
        if len(totals) >= 2:
            low_pace, high_pace = min(totals) / DAYS_PER_MONTH, max(totals) / DAYS_PER_MONTH
    if low_pace is None or high_pace is None:
        low_pace, high_pace = daily * (1 - DEFAULT_BAND), daily * (1 + DEFAULT_BAND)
    return SpendBasis(basis=basis, months=months, monthly=_q(monthly, currency), daily=_floor(daily, currency) if daily > 0 else _q(Decimal(0), currency),
                      daily_low=_floor(low_pace, currency) if low_pace > 0 else _q(Decimal(0), currency),
                      daily_high=_floor(high_pace, currency) if high_pace > 0 else _q(Decimal(0), currency))


# -- income -----------------------------------------------------------------------------------------------


def salary_events(inputs: ForecastInputs, currency: str, end: date) -> tuple[list[ForecastEvent], list[Assumption], list[Assumption]]:
    """Expected net salary on each usual pay day to ``end``. This month's salary is left out once found."""
    profile = inputs.salary_profile
    events: list[ForecastEvent] = []
    used: list[Assumption] = []
    left: list[Assumption] = []
    if profile is None or profile.currency != currency:
        return events, used, left
    today = inputs.today
    this_month = SAL.month_key(today)
    months = SAL.salary_months(profile, inputs.transactions, inputs.accounts, today, inputs.payslips, lending_ids=inputs.lending_ids,
                               payslip_credits=inputs.payslip_credits)
    current = next((m for m in months if m.month == this_month), None)
    for start, _ in month_list(today):
        month = SAL.month_key(start)
        pay_on = SAL.pay_date(profile, month)
        if pay_on > end or pay_on < profile.joined_on:
            continue
        expected = SAL.expected_for(profile, month, inputs.payslips)
        if expected.net <= 0:
            continue
        params = {"month": month, "date": pay_on.isoformat(), "gross": str(expected.gross),
                  "deductions": str(sum((d.amount for d in expected.deductions), Decimal(0))), "advance": str(expected.advance_repayment)}
        if month == this_month:
            if current is not None and current.transaction is not None:
                used.append(Assumption(code="salary_received", params={"month": month},
                                       detail=f"This month's salary looks received ({current.transaction.posted_on.isoformat()}): it is already in the balance."))
                continue
            if current is not None and current.status == "missing":
                left.append(Assumption(code="salary_missing", params={"month": month, "date": pay_on.isoformat()},
                                       detail=f"No salary found for {month}, expected around {pay_on.isoformat()}: not counted."))
                continue
            if pay_on < today:
                events.append(ForecastEvent(on=today, kind="salary", label=profile.employer or "Salary", ref_id=profile.id, currency=currency,
                                            amount=expected.net, status="late", note="salary_late", params=params))
                continue
        events.append(ForecastEvent(on=pay_on, kind="salary", label=profile.employer or "Salary", ref_id=profile.id, currency=currency,
                                    amount=expected.net, note="salary_expected", params=params))
    if events:
        used.append(Assumption(code="salary_profile", params={"day": str(profile.pay_day)},
                               detail="Salary from your salary profile: the expected net (gross less deductions and advance repayments) on your usual pay day."))
    return events, used, left


def recurring_income(inputs: ForecastInputs, currency: str, end: date) -> list[ForecastEvent]:
    """Money in from one payer every month, three times or more, at a steady amount: not the salary."""
    from .summary import _payee, detect_recurring, next_after

    kinds = kinds_of(inputs.accounts)
    profile = inputs.salary_profile
    salary_ids: set[str] = set()
    salary_payers: set[str] = set()
    if profile is not None:
        for decision in profile.decisions:
            if decision.decision == "confirmed":
                salary_ids.add(decision.transaction_id)
        salary_ids |= {tid for ids in inputs.payslip_credits.values() for tid in ids}
        salary_payers = {_payee(t.description) for t in inputs.transactions if t.id in salary_ids} - {""}
    # The bill finder's rule, read on money in: the copies are turned into money out only to reuse it.
    flipped = []
    for t in inputs.transactions:
        if t.currency != currency or t.amount <= 0 or t.id in salary_ids or SAL.SALARY_WORDS.search(t.description):
            continue
        if flow_of(t, kinds, inputs.lending_ids) != Flow.INCOME or _payee(t.description) in salary_payers:
            continue
        flipped.append(t.model_copy(update={"amount": -t.amount, "category": Category.OTHER}))
    out: list[ForecastEvent] = []
    for found in detect_recurring(flipped, inputs.today, [], []):
        if found.cadence != Cadence.MONTHLY or found.occurrences < MIN_INCOME_OCCURRENCES:
            continue
        due: date | None = found.next_due_on
        anchor = found.next_due_on.day
        while due is not None and due <= end:
            if due > inputs.today:
                out.append(ForecastEvent(on=due, kind="income", label=found.name, currency=currency, amount=found.amount, note="income_recurring",
                                         params={"count": str(found.occurrences)}))
            due = next_after(due, found.cadence, anchor)
    return out


def expected_income_events(inputs: ForecastInputs, currency: str, end: date) -> tuple[list[ForecastEvent], list[Assumption]]:
    """One-off income the person expects, on its date, until it is marked received. A passed date is left out and named."""
    events: list[ForecastEvent] = []
    left: list[Assumption] = []
    for item in sorted(inputs.expected_income, key=lambda i: (i.expected_on, i.id)):
        if item.currency != currency or item.received_on is not None:
            continue
        if item.expected_on < inputs.today:
            left.append(Assumption(code="expected_income_late", params={"name": item.label, "date": item.expected_on.isoformat()},
                                   detail=f"{item.label} was expected on {item.expected_on.isoformat()} and is not marked received: not counted."))
            continue
        if item.expected_on > end:
            continue
        events.append(ForecastEvent(on=item.expected_on, kind="expected_income", label=item.label, ref_id=item.id, currency=currency, amount=item.amount,
                                    note="income_expected_once", params={"date": item.expected_on.isoformat()}))
    return events, left


# -- money out --------------------------------------------------------------------------------------------


def card_interest(inputs: ForecastInputs, currency: str, end: date) -> tuple[list[InterestCharge], list[Assumption]]:
    """Interest each card with a known APR is expected to charge, month by month: the balance left after that month's
    minimum payment times the APR over 12, added to what is owed (so the next month's interest is on it too). A card
    with a balance and no rate is named instead: its interest is not guessed."""
    today = inputs.today
    out: list[InterestCharge] = []
    unknown: list[str] = []
    for debt in inputs.debts:
        if debt.currency != currency or debt.kind != DebtKind.CARD or debt.balance <= 0 or isinstance(debt, O.PersonDebt):
            continue
        if debt.apr_percent <= 0:
            unknown.append(debt.name)
            continue
        monthly = debt.apr_percent / Decimal(1200)
        remaining = debt.balance
        for start, last in month_list(today):
            on = _on_day(start, debt.due_day) if debt.due_day else last
            if on > end:
                break
            if on < today:
                continue  # this month's statement day passed: its interest is already in the balance
            paid = min(debt.minimum_payment, remaining) if M.owes_minimum(debt) else Decimal(0)
            base = remaining - paid
            interest = _q(base * monthly, currency)
            if interest <= 0:
                break
            remaining = base + interest
            out.append(InterestCharge(on=on, label=debt.name, ref_id=debt.id, balance=_q(base, currency), apr_percent=debt.apr_percent, amount=interest))
    left = [Assumption(code="card_apr_unknown", params={"name": name},
                       detail=f"{name} has no yearly interest rate (APR) saved: its interest is not estimated.") for name in sorted(unknown)]
    return out, left


def bill_events(inputs: ForecastInputs, currency: str, end: date) -> list[ForecastEvent]:
    out = []
    for found in O.unpaid([b for b in inputs.bills if b.currency == currency], inputs.bill_links, inputs.today, end):
        bill, due = found.bill, found.due_on
        out.append(ForecastEvent(on=inputs.today if found.overdue else due, kind="bill", label=bill.name, ref_id=bill.id, currency=currency,
                                 amount=-bill.amount, status="overdue" if found.overdue else "expected",
                                 note="bill_overdue" if found.overdue else "bill_due", params={"date": due.isoformat()}))
    return out


def debt_events(inputs: ForecastInputs, currency: str, end: date) -> tuple[list[ForecastEvent], list[Assumption]]:
    from .safe_to_spend import paid_this_month

    today = inputs.today
    out: list[ForecastEvent] = []
    left: list[Assumption] = []
    active = [b for b in inputs.bills if b.active]
    for debt in inputs.debts:
        if debt.currency != currency:
            continue
        if isinstance(debt, O.PersonDebt):
            if debt.installments_left:
                for on, amount in debt.installments_left:
                    if on > end:
                        break
                    late = on < today
                    out.append(ForecastEvent(on=today if late else on, kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=currency,
                                             amount=-amount, status="overdue" if late else "expected", note="person_loan_overdue" if late else "person_loan_due",
                                             params={"date": on.isoformat()}))
                continue
            if debt.due_on is None or debt.balance <= 0:
                continue
            if debt.overdue or debt.due_on < today:
                out.append(ForecastEvent(on=today, kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=currency, amount=-debt.balance,
                                         status="overdue", note="person_loan_overdue", params={"date": debt.due_on.isoformat()}))
            elif debt.due_on <= end:
                out.append(ForecastEvent(on=debt.due_on, kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=currency,
                                         amount=-debt.balance, note="person_loan_due", params={"date": debt.due_on.isoformat()}))
            continue
        if not M.owes_minimum(debt):
            continue
        covering = M.bill_for_debt(debt, active)
        if covering is not None:
            left.append(Assumption(code="minimum_in_bill", params={"name": debt.name, "bill": covering.name},
                                   detail=f"The minimum of {debt.name} is saved as the bill {covering.name}: counted once, as the bill."))
            continue
        remaining = debt.balance
        card = debt.kind == DebtKind.CARD
        for index, (start, _last) in enumerate(month_list(today)):
            if remaining <= 0:
                break
            amount = min(debt.minimum_payment, remaining)
            if debt.due_day is None:
                on, status, note = (today, "expected", "minimum_no_day") if index == 0 else (start, "expected", "minimum_no_day")
            else:
                due = _on_day(start, debt.due_day)
                if index == 0 and due < today:
                    if paid_this_month(debt, inputs.debt_payments, today):
                        continue  # this month's minimum is paid: nothing to take off, the balance already shows it
                    on, status, note = today, "overdue", "minimum_overdue"
                else:
                    on, status, note = due, "expected", "minimum_due"
            if on > end:
                break
            remaining -= amount
            out.append(ForecastEvent(on=on, kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=currency, amount=-amount,
                                     status=status, note=note, params={"card": "1" if card else "0", "date": on.isoformat()}))  # type: ignore[arg-type]
    return out, left


def installment_events(inputs: ForecastInputs, currency: str, end: date) -> tuple[list[ForecastEvent], list[Assumption]]:
    today = inputs.today
    start = M.month_start(today)
    names = {M.name_key(b.name) for b in inputs.bills if b.active and b.currency == currency}
    out: list[ForecastEvent] = []
    left: list[Assumption] = []
    seen_in_bill: set[str] = set()
    for entry in inputs.installments:
        if entry.currency != currency or entry.source_status in ("paid", "refunded"):
            continue
        due = max(Decimal(0), entry.amount - entry.refunded_amount)
        if due <= 0 or entry.due_on < start or entry.due_on > end:
            continue
        if M.name_key(entry.merchant) in names or M.name_key(entry.provider) in names:
            key = f"{entry.provider}:{entry.order_id}"
            if key not in seen_in_bill:
                seen_in_bill.add(key)
                left.append(Assumption(code="installment_in_bill", params={"name": f"{entry.provider}: {entry.merchant}"},
                                       detail=f"{entry.provider}: {entry.merchant} is saved as a bill: counted once, as the bill."))
            continue
        overdue = entry.due_on < today
        out.append(ForecastEvent(on=today if overdue else entry.due_on, kind="installment", label=f"{entry.provider}: {entry.merchant}",
                                 ref_id=f"{entry.provider}:{entry.order_id}", currency=currency, amount=-due, status="overdue" if overdue else "expected",
                                 note="installment_overdue" if overdue else "installment_due",
                                 params={"date": entry.due_on.isoformat(), "number": str(entry.installment), "of": str(entry.total_installments)}))
    return out, left


def remittance_events(inputs: ForecastInputs, currency: str, end: date) -> list[ForecastEvent]:
    today = inputs.today
    names = {M.name_key(b.name) for b in inputs.bills if b.active and b.currency == currency}
    newest: dict[str, P.Remittance] = {}
    sent_this_month: set[str] = set()
    for r in sorted((r for r in inputs.remittances if r.currency == currency), key=lambda r: (r.sent_on, r.created_at)):
        key = M.name_key(r.recipient)
        if M.month_start(r.sent_on) == M.month_start(today):
            sent_this_month.add(key)
        if r.regular:
            newest[key] = r
    out: list[ForecastEvent] = []
    for key, r in sorted(newest.items()):
        if key in names:
            continue  # saved as a bill: the bill counts it
        amount = r.amount + r.fee
        for index, (start, _last) in enumerate(month_list(today)):
            on = _on_day(start, r.sent_on.day)
            if index == 0:
                if key in sent_this_month:
                    continue
                on = max(on, today)
            if on > end:
                break
            out.append(ForecastEvent(on=on, kind="remittance", label=r.recipient, ref_id=r.id, currency=currency, amount=-amount,
                                     note="remittance_regular", params={"fee": str(r.fee)}))
    return out


# -- the forecast -----------------------------------------------------------------------------------------


def _currencies(inputs: ForecastInputs) -> list[str]:
    found = {a.currency for a in inputs.accounts if a.kind in M.SPENDABLE and not a.archived}
    found |= {b.currency for b in inputs.bills if b.active}
    found |= {d.currency for d in inputs.debts if d.balance > 0}
    if inputs.salary_profile is not None:
        found.add(inputs.salary_profile.currency)
    found |= {i.currency for i in inputs.expected_income if i.received_on is None and i.expected_on >= inputs.today}
    return sorted(found, key=lambda c: (c != inputs.base_currency, c))


def currency_forecast(inputs: ForecastInputs, currency: str) -> CurrencyForecast | None:
    today = inputs.today
    end = horizon_end(today)
    spendable = [a for a in inputs.accounts if a.currency == currency and a.kind in M.SPENDABLE and not a.archived]
    actual = _q(M.liquid(inputs.accounts, inputs.balances, currency), currency)
    spend = spending_basis(inputs, currency)
    salary, assumptions, left_out = salary_events(inputs, currency, end)
    events = list(salary)
    events += recurring_income(inputs, currency, end)
    once, once_left = expected_income_events(inputs, currency, end)
    events += once
    left_out += once_left
    events += bill_events(inputs, currency, end)
    debts, debt_left = debt_events(inputs, currency, end)
    events += debts
    installments, installment_left = installment_events(inputs, currency, end)
    events += installments
    events += remittance_events(inputs, currency, end)
    left_out += debt_left + installment_left
    interest, interest_left = card_interest(inputs, currency, end)
    left_out += interest_left
    if not spendable and not events:
        return None
    order = {"salary": 0, "income": 1, "expected_income": 1, "bill": 2, "minimum_payment": 3, "installment": 4, "remittance": 5}
    events.sort(key=lambda e: (e.on, order[e.kind], e.label.casefold()))
    reserve = reserve_targets(inputs.goals).get(currency)

    by_day: dict[date, Decimal] = defaultdict(Decimal)
    sure_by_day: dict[date, Decimal] = defaultdict(Decimal)  # without other recurring income (the low line)
    for event in events:
        by_day[event.on] += event.amount
        if event.kind not in ("income", "expected_income"):
            sure_by_day[event.on] += event.amount
    points: list[ForecastPoint] = []
    expected = low = high = actual
    day = today
    while day <= end:
        if day > today:
            expected -= spend.daily
            low -= spend.daily_high
            high -= spend.daily_low
        expected += by_day.get(day, Decimal(0))
        high += by_day.get(day, Decimal(0))
        low += sure_by_day.get(day, Decimal(0))
        points.append(ForecastPoint(on=day, expected=_q(expected, currency), low=_q(min(low, expected), currency), high=_q(max(high, expected), currency)))
        day += timedelta(days=1)

    by_on = {p.on: p for p in points}
    months: list[MonthEnd] = []
    for start, last in month_list(today):
        point = by_on[last]
        mine = [e for e in events if start <= e.on <= last]
        spend_days = (last - max(start, today)).days + (0 if start <= today else 1)
        months.append(MonthEnd(
            month=f"{start.year:04d}-{start.month:02d}", on=last, expected=point.expected, low=point.low, high=point.high,
            money_in=_q(sum((e.amount for e in mine if e.amount > 0), Decimal(0)), currency),
            money_out=_q(-sum((e.amount for e in mine if e.amount < 0), Decimal(0)), currency),
            spending=_q(spend.daily * max(spend_days, 0), currency),
            card_interest=_q(sum((i.amount for i in interest if start <= i.on <= last), Decimal(0)), currency),
        ))

    warnings: list[ForecastWarning] = []
    zero = next((p for p in points if p.expected < 0), None)
    if zero is not None:
        warnings.append(ForecastWarning(kind="below_zero", on=zero.on, balance=zero.expected, threshold=_q(Decimal(0), currency)))
    if reserve is not None and reserve > 0:
        under = next((p for p in points if p.expected < reserve), None)
        if under is not None:
            warnings.append(ForecastWarning(kind="below_reserve", on=under.on, balance=under.expected, threshold=_q(reserve, currency)))
    if zero is None:
        maybe = next((p for p in points if p.low < 0), None)
        if maybe is not None:
            warnings.append(ForecastWarning(kind="may_go_below_zero", on=maybe.on, balance=maybe.low, threshold=_q(Decimal(0), currency)))

    assumptions.insert(0, Assumption(code="start_balance", params={"count": str(len(spendable))},
                                     detail="Starts from today's balance of your bank and cash accounts (cards, loans and investments are not cash)."))
    if spend.basis == "none":
        assumptions.append(Assumption(code="spend_none", detail="Not enough history to estimate everyday spending: none is taken off, so the line is too high."))
    else:
        assumptions.append(Assumption(code=f"spend_{spend.basis}", params={"months": ", ".join(spend.months), "amount": str(spend.monthly),
                                                                          "daily": str(spend.daily)},
                                      detail=f"Everyday spending outside saved bills: {currency} {spend.monthly} a month on average, {currency} {spend.daily} a day from tomorrow."))
    assumptions.append(Assumption(code="band", params={"low": str(spend.daily_high), "high": str(spend.daily_low)},
                                  detail="The low line spends at your busiest month's pace and leaves out other recurring income; the high line at your quietest month's."))
    if any(e.kind == "income" for e in events):
        assumptions.append(Assumption(code="income_recurring", detail="Other income that arrived every month at least three times is expected again."))
    if any(e.kind == "expected_income" for e in events):
        assumptions.append(Assumption(code="expected_income", detail="One-off income you entered is expected on its date; the low line leaves it out."))
    if interest:
        assumptions.append(Assumption(code="card_interest", detail=("Card interest where the yearly rate is known: the balance after the minimum payment "
                                                                    "times the rate over 12, owed to the card and not taken from your bank money until you pay it.")))
    assumptions.append(Assumption(code="cards", detail="Card purchases are counted through the card's minimum payment, not as everyday spending."))
    return CurrencyForecast(
        currency=currency, actual=actual, accounts_counted=len(spendable), reserve_target=_q(reserve, currency) if reserve else None, spend=spend,
        month_end=months[0], months=months, points=points, events=events, warnings=warnings, assumptions=assumptions, left_out=left_out,
        card_interest=interest,
    )


def build(inputs: ForecastInputs) -> Forecast:
    rows = [row for currency in _currencies(inputs) if (row := currency_forecast(inputs, currency)) is not None]
    return Forecast(today=inputs.today, month_end=M.month_end(inputs.today), horizon_end=horizon_end(inputs.today), timezone=inputs.timezone,
                    base_currency=inputs.base_currency, currencies=rows)


# -- the emergency reserve --------------------------------------------------------------------------------


ReserveStatus = Literal["reached", "building", "empty"]


class ReserveView(BaseModel):
    goal_id: str
    name: str
    currency: str
    mode: Literal["months", "amount"]
    months: int | None  # the months of essential spending chosen (months mode)
    target: Decimal  # the goal's target (what Safe to spend and the forecast use)
    recalculated_target: Decimal | None  # months x today's essential spending (months mode), to compare
    essential_monthly: Decimal | None
    essential_basis: Literal["essential", "bare", "none"]
    saved: Decimal
    progress_percent: int | None
    months_covered: Decimal | None  # saved / essential monthly, one decimal, rounded down
    held_in: Literal["account", "goal"]
    account_id: str | None
    account_name: str | None
    paused: bool
    monthly_contribution: Decimal | None
    status: ReserveStatus


class ReserveSuggestion(BaseModel):
    currency: str
    essential_monthly: Decimal
    essential_basis: Literal["essential", "bare"]
    months: int
    target: Decimal


class Reserves(BaseModel):
    reserves: list[ReserveView]
    suggestions: list[ReserveSuggestion]  # currencies with essentials and no reserve yet
    default_months: int
    note: str = NOTE


def months_covered(saved: Decimal, essential: Decimal | None) -> Decimal | None:
    if essential is None or essential <= 0:
        return None
    return (saved / essential).quantize(Decimal("0.1"), rounding=ROUND_FLOOR)


def reserve_view(goal: Goal, accounts: list[Account], essential: tuple[Decimal | None, str]) -> ReserveView:
    """One emergency reserve: its target, what it holds now, the progress and how many months of essentials it covers."""
    by_id = {a.id: a for a in accounts}
    account = by_id.get(goal.account_id or "")
    held = account is not None and not account.archived and account.currency == goal.currency
    amount, basis = essential
    c = goal.currency
    recalculated = _q(amount * goal.reserve_months, c) if goal.reserve_months and amount else None
    progress = int((goal.saved_amount * 100 / goal.target_amount).to_integral_value(rounding=ROUND_FLOOR)) if goal.target_amount > 0 else None
    status: ReserveStatus = "reached" if goal.target_amount > 0 and goal.saved_amount >= goal.target_amount else "building" if goal.saved_amount > 0 else "empty"
    return ReserveView(
        goal_id=goal.id, name=goal.name, currency=c, mode="months" if goal.reserve_months else "amount", months=goal.reserve_months,
        target=_q(goal.target_amount, c), recalculated_target=recalculated, essential_monthly=_q(amount, c) if amount is not None else None,
        essential_basis=basis if amount is not None else "none",  # type: ignore[arg-type]
        saved=_q(goal.saved_amount, c), progress_percent=progress, months_covered=months_covered(goal.saved_amount, amount),
        held_in="account" if held else "goal", account_id=account.id if held and account else None, account_name=account.name if held and account else None,
        paused=goal.paused, monthly_contribution=goal.monthly_contribution, status=status,
    )


def reserves(goals: list[Goal], accounts: list[Account], essentials: dict[str, tuple[Decimal | None, str]], default_months: int) -> Reserves:
    """``goals`` with what they hold now (``obligations.current_goals``); ``essentials`` per currency: the monthly
    essential spending and its basis (``essential`` or ``bare``, from the advisor's runway)."""
    views = [reserve_view(g, accounts, essentials.get(g.currency, (None, "none"))) for g in goals if g.purpose == GoalPurpose.EMERGENCY]
    have = {v.currency for v in views}
    suggestions = []
    for currency, (amount, basis) in sorted(essentials.items()):
        if currency in have or amount is None or amount <= 0:
            continue
        suggestions.append(ReserveSuggestion(currency=currency, essential_monthly=_q(amount, currency), essential_basis=basis,  # type: ignore[arg-type]
                                             months=default_months, target=_q(amount * default_months, currency)))
    return Reserves(reserves=views, suggestions=suggestions, default_months=default_months)


__all__ = [
    "Assumption", "CurrencyForecast", "Forecast", "ForecastEvent", "ForecastInputs", "ForecastPoint", "ForecastWarning", "InterestCharge", "MonthEnd", "ReserveSuggestion",
    "ReserveView", "Reserves", "SpendBasis", "build", "card_interest", "currency_forecast", "expected_income_events", "horizon_end", "month_list", "months_covered", "reserve_targets", "reserve_view",
    "reserves",
]
