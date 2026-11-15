"""Bills matched to recorded payments, debt payments, and budgets (docs/finance.md#bills-and-budgets).

A bill is a **forecast** until a recorded payment is matched to one of its due dates. A match is a
finance link (docs/finance-links.md) in the ``bill`` dimension: target ``finance_bill_due`` with the
id ``<bill id>:<YYYY-MM-DD>``, pointing at the payment's ``canonical_id``. Nothing here marks a bill
paid any other way: the status of a due date is worked out from its live links on every read.

A debt payment is a link in the ``debt`` dimension (target ``finance_debt``, the debt's id). The one loan
policy (docs/finance.md#transaction-types): a repayment is ``loan_principal`` (a transfer) and only the interest
is spending (type ``interest``). The split comes from what the message or the person states (an SMS loan
repayment's interest is charged on the loan as its own row, ``ledger.plan``); for a debt with no account of its
own, ``obligations.current_debts`` estimates each matched payment's interest from the debt's rate (balance times
APR over 12) and lowers the balance by the rest. Nothing here invents the lender's terms beyond that estimate.

A **budget** is one of the person's Guardian category caps (a monthly limit per category and
currency); the alert threshold per budget is kept with the plan (``Planning.budget_alerts``). The owner's own
planned budgets (``Planning.budgets``: spending, savings, reserve, sinking funds, remittances, debts; monthly,
weekly or custom) are read by ``plan_budget_report`` and split the income by ``salary_allocation``.

The figures (``bill_schedule``, ``budget_report``) are pure functions over records already read.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from ..schemas import TenantContext
from .budget_rollover import ChainPeriod
from .flows import spend_rows
from .guardian import GuardianRules
from .links_store import FinanceLink, LinkStatus
from .models import Bill, BudgetAlert, Cadence, Category, Debt, Transaction
from .money import quantize
from .summary import next_after

BILL_DIMENSION = "bill"
BILL_TARGET = "finance_bill_due"
DEBT_DIMENSION = "debt"
DEBT_TARGET = "finance_debt"
DUE_SOON_DAYS = 7
MAX_WINDOW_DAYS = 400

OccurrenceStatus = Literal["paid", "review", "overdue", "due_soon", "planned"]
BudgetState = Literal["ok", "near", "over"]


def bill_target_id(bill_id: str, due_on: date) -> str:
    return f"{bill_id}:{due_on.isoformat()}"


def parse_bill_target(target_id: str) -> tuple[str, date] | None:
    bill_id, sep, day = target_id.rpartition(":")
    if not sep or not bill_id:
        return None
    try:
        return bill_id, date.fromisoformat(day)
    except ValueError:
        return None


def occurrences(bill: Bill, start: date, end: date) -> list[date]:
    """Every due date of the bill from ``start`` to ``end``, inclusive, forward from ``next_due_on``.

    Never back from it: ``next_due_on`` is the person's to move, and a date stepped back from a new
    anchor day (30 April back to 30 March) is not a date the bill was ever due. Due dates already matched
    to a payment are kept by ``bill_schedule`` from their links, whatever ``next_due_on`` is now."""
    anchor = bill.next_due_on.day
    found: list[date] = []
    due: date | None = bill.next_due_on
    guard = 0
    while due is not None and due <= end and guard < 600:
        guard += 1
        if due >= start:
            found.append(due)
        due = next_after(due, bill.cadence, anchor)
    return found


class BillOccurrence(BaseModel):
    bill_id: str
    name: str
    category: Category
    cadence: Cadence
    account_id: str | None
    due_on: date
    days_until: int
    expected: Decimal
    currency: str
    status: OccurrenceStatus
    paid_amount: Decimal
    link_ids: list[str]
    canonical_ids: list[str]


def occurrence_status(due_on: date, today: date, links: list[FinanceLink]) -> OccurrenceStatus:
    """Paid only with an active matched payment; a match waiting for review is ``review``; else by date."""
    if any(l.status == LinkStatus.ACTIVE for l in links):
        return "paid"
    if any(l.status == LinkStatus.NEEDS_REVIEW for l in links):
        return "review"
    days = (due_on - today).days
    if days < 0:
        return "overdue"
    if days <= DUE_SOON_DAYS:
        return "due_soon"
    return "planned"


def bill_schedule(bills: list[Bill], links: list[FinanceLink], today: date, start: date, end: date) -> list[BillOccurrence]:
    """Every active bill's due dates in the window, each with its matched payments and status."""
    by_target: dict[str, list[FinanceLink]] = {}
    for link in links:
        if link.dimension == BILL_DIMENSION and link.status != LinkStatus.VOID:
            by_target.setdefault(link.target_id, []).append(link)
    rows: list[BillOccurrence] = []
    for bill in bills:
        # A matched due date stays in its window even after next_due_on moved on.
        matched_days = {parsed[1] for target in by_target if (parsed := parse_bill_target(target)) and parsed[0] == bill.id and start <= parsed[1] <= end}
        for due in sorted(set(occurrences(bill, start, end)) | matched_days):
            matched = by_target.get(bill_target_id(bill.id, due), [])
            if not bill.active and not matched:
                continue
            paid = sum((l.amount or Decimal(0) for l in matched if l.status == LinkStatus.ACTIVE and l.currency == bill.currency), Decimal(0))
            rows.append(BillOccurrence(
                bill_id=bill.id, name=bill.name, category=bill.category, cadence=bill.cadence, account_id=bill.account_id,
                due_on=due, days_until=(due - today).days, expected=bill.amount, currency=bill.currency,
                status=occurrence_status(due, today, matched), paid_amount=quantize(paid, bill.currency),
                link_ids=[l.id for l in matched], canonical_ids=sorted({l.canonical_id for l in matched}),
            ))
    return sorted(rows, key=lambda r: (r.due_on, r.name.lower(), r.bill_id))


# -- budgets -------------------------------------------------------------------------------------------


class BudgetLine(BaseModel):
    category: Category
    currency: str
    cap: Decimal
    spent: Decimal
    remaining: Decimal  # never below zero; ``over`` says by how much it went past
    over: Decimal
    percent: int  # spent as a whole percent of the cap (can pass 100)
    state: BudgetState
    alert_percent: int
    alert_near: bool
    alert_over: bool
    projected: Decimal | None  # at this month's pace, for the current month only
    projected_over: Decimal | None  # how far past the cap that pace ends, when it does


class BudgetPoint(BaseModel):
    day: date
    spent: Decimal  # cumulative spend of the budgeted categories to the end of this day


class BudgetSeries(BaseModel):
    currency: str
    budget: Decimal  # the caps in this currency together
    spent: Decimal
    points: list[BudgetPoint]


class BudgetReport(BaseModel):
    month: str
    start: date
    end: date
    today: date
    rules_version: int
    budgets: list[BudgetLine]
    series: list[BudgetSeries]


def alert_for(alerts: list[BudgetAlert], category: Category, currency: str) -> BudgetAlert:
    for alert in alerts:
        if alert.category == category and alert.currency == currency:
            return alert
    return BudgetAlert(category=category, currency=currency)


def _spend_rows(rows: list[Transaction], lending: frozenset[str] | None = None) -> list[Transaction]:
    # The shared rule (flows.py): purchases, less the refunds filed in their category.
    return spend_rows(rows, None, lending)


def budget_report(rules: GuardianRules, version: int, alerts: list[BudgetAlert], rows: list[Transaction], *, month: str, start: date, end: date,
                  today: date, lending: frozenset[str] | None = None) -> BudgetReport:
    """Each cap with what was spent, what is left, its state and, this month, where the pace ends.

    ``lending``: transactions lent or borrowed with a person, never spending (``flows.py``)."""
    spend = _spend_rows(rows, lending)
    by_cat: dict[tuple[Category, str], Decimal] = {}
    by_day: dict[tuple[str, date], Decimal] = {}
    capped = {(c.category, c.currency) for c in rules.category_caps}
    for t in spend:
        by_cat[(t.category, t.currency)] = by_cat.get((t.category, t.currency), Decimal(0)) - t.amount
        if (t.category, t.currency) in capped:
            by_day[(t.currency, t.posted_on)] = by_day.get((t.currency, t.posted_on), Decimal(0)) - t.amount
    current = start <= today <= end
    elapsed = (today - start).days + 1
    days = calendar.monthrange(start.year, start.month)[1]
    lines: list[BudgetLine] = []
    for cap in rules.category_caps:
        spent = quantize(max(by_cat.get((cap.category, cap.currency), Decimal(0)), Decimal(0)), cap.currency)
        alert = alert_for(alerts, cap.category, cap.currency)
        percent = int(spent * 100 / cap.amount) if cap.amount > 0 else 0
        state: BudgetState = "over" if spent > cap.amount else "near" if spent * 100 >= cap.amount * alert.alert_percent else "ok"
        projected = quantize(spent * days / elapsed, cap.currency) if current and elapsed > 0 else None
        lines.append(BudgetLine(
            category=cap.category, currency=cap.currency, cap=quantize(cap.amount, cap.currency), spent=spent,
            remaining=quantize(max(cap.amount - spent, Decimal(0)), cap.currency), over=quantize(max(spent - cap.amount, Decimal(0)), cap.currency), percent=percent, state=state, alert_percent=alert.alert_percent,
            alert_near=alert.alert_near, alert_over=alert.alert_over, projected=projected,
            projected_over=quantize(projected - cap.amount, cap.currency) if projected is not None and projected > cap.amount else None,
        ))
    lines.sort(key=lambda l: (-l.percent, l.category.value, l.currency))
    series: list[BudgetSeries] = []
    for currency in sorted({c.currency for c in rules.category_caps}):
        last = min(end, today) if current else end
        running, points = Decimal(0), []
        day = start
        while day <= last:
            running += by_day.get((currency, day), Decimal(0))
            points.append(BudgetPoint(day=day, spent=quantize(running, currency)))
            day += timedelta(days=1)
        budget = sum((c.amount for c in rules.category_caps if c.currency == currency), Decimal(0))
        series.append(BudgetSeries(currency=currency, budget=quantize(budget, currency), spent=quantize(running, currency), points=points))
    return BudgetReport(month=month, start=start, end=end, today=today, rules_version=version, budgets=lines, series=series)


# -- planned budgets (docs/finance.md#budgets-and-planning) -------------------------------------------------
#
# The owner's own budgets (``Planning.budgets``), beside the Guardian caps above: spending in chosen categories and
# subcategories, savings into a goal (the emergency reserve, a sinking fund), money sent home and debt payments, each
# monthly, weekly or over a custom period. Every figure is worked out on read from linked records:
#
# * **Spent** is the shared spending rule (``flows.spend_rows``: purchases less the refunds filed in their category;
#   transfers, card repayments, money lent or borrowed and uncategorized rows never count) over the rows the budget covers.
# * **Coming** is each saved bill in the budget's categories with a due date left in the period and no matched payment
#   (``obligations.unpaid``). A bill paid by a matched payment drops out of Coming and its payment is in Spent: once.
# * **Forecast** to the period end: spent plus coming, plus, for a variable budget in the current period, the pace of
#   the spending that was not a paid bill over the days left. A fixed budget adds no pace.
# * **Risk**: ``over`` once spent passes the amount, ``high`` when the forecast does, ``watch`` from the alert percent.
#   A savings, remittance or debt budget is ``ok`` on pace, ``behind`` below the share of the period gone, ``unknown``
#   when nothing records it (a goal kept by hand, with no account).

RiskLevel = Literal["ok", "watch", "high", "over", "behind", "unknown"]


class ComingItem(BaseModel):
    bill_id: str
    name: str
    due_on: date
    amount: Decimal
    overdue: bool


class PlanBudgetLine(BaseModel):
    id: str
    name: str
    kind: str
    period: str
    currency: str
    amount: Decimal
    period_start: date
    period_end: date
    days_left: int
    categories: list[Category]
    subcategory_ids: list[str]
    fixed: bool
    essential: bool
    allocation: str
    alert_percent: int
    reserve_in_safe_to_spend: bool
    active: bool
    notes: str | None
    goal_id: str | None
    debt_ids: list[str]
    spent: Decimal | None  # None: nothing records it (a goal saved by hand)
    remaining: Decimal | None
    over: Decimal
    percent: int | None
    coming: Decimal
    coming_items: list[ComingItem]
    forecast: Decimal | None
    risk: RiskLevel
    rows: int  # transactions counted in spent
    basis: str  # what spent is read from: ``spending``, ``goal_account``, ``goal``, ``remittances``, ``family_support``, ``debt_payments``
    goal_saved: Decimal | None = None
    goal_target: Decimal | None = None
    # Rollover (docs/finance.md#budget-rollover): the terms this period used, what came in from the period before, the
    # amount plus that, and the chain's latest periods (oldest first). Without rollover: ``none``, ``absorb``, 0, the amount.
    rollover: str = "none"
    rollover_cap: Decimal | None = None
    overspend: str = "absorb"
    carried_in: Decimal = Decimal(0)
    available: Decimal | None = None
    chain: list[ChainPeriod] = []
    chain_periods: int = 0
    starts_on: date | None = None  # the budget's own first day (a weekly anchor or the first custom window)
    ends_on: date | None = None


class PlanTotals(BaseModel):
    currency: str
    budget: Decimal
    carried_in: Decimal
    available: Decimal
    spent: Decimal
    remaining: Decimal
    coming: Decimal
    forecast: Decimal


class PlanReport(BaseModel):
    on: date
    today: date
    budgets: list[PlanBudgetLine]
    totals: list[PlanTotals]  # spending budgets in the monthly period, per currency, never added across currencies


def budget_period(budget: Any, on: date) -> tuple[date, date]:
    """The period of ``budget`` that holds ``on``: the calendar month, the week (from the weekday of ``starts_on``,
    Monday without one) or the custom dates."""
    period = getattr(budget.period, "value", budget.period)
    if period == "weekly":
        anchor = budget.starts_on.weekday() if budget.starts_on is not None else 0
        start = on - timedelta(days=(on.weekday() - anchor) % 7)
        return start, start + timedelta(days=6)
    if period == "custom" and budget.starts_on is not None and budget.ends_on is not None:
        return budget.starts_on, budget.ends_on
    last = calendar.monthrange(on.year, on.month)[1]
    return on.replace(day=1), on.replace(day=last)


def covers(budget: Any, t: Transaction) -> bool:
    """A row counts in a spending budget when its subcategory is listed, or its category is listed and none of that
    category's subcategories is (listing a subcategory narrows its category to it)."""
    if t.currency != budget.currency:
        return False
    if t.subcategory_id and t.subcategory_id in budget.subcategory_ids:
        return True
    if t.category not in budget.categories:
        return False
    from .categories import subcategory_parent

    narrowed = {subcategory_parent(s) for s in budget.subcategory_ids}
    return t.category not in narrowed


def default_allocation(budget: Any) -> str:
    from .categories import GROUP_OF

    kind = getattr(budget.kind, "value", budget.kind)
    if budget.allocation:
        return str(budget.allocation)
    if kind in ("savings", "emergency", "sinking_fund"):
        return "savings"
    if kind == "remittance":
        return "remittances"
    if kind == "debt":
        return "debts"
    groups = {GROUP_OF.get(c, "other") for c in budget.categories}
    by_group = {"housing": "housing", "food_drinks": "food", "transport": "transport", "family": "family", "family_support": "remittances",
                "debt": "debts"}
    picked = {by_group.get(g, "other") for g in groups}
    return picked.pop() if len(picked) == 1 else "other"


def _risk(amount: Decimal, spent: Decimal, forecast: Decimal, alert_percent: int) -> RiskLevel:
    if spent > amount:
        return "over"
    if forecast > amount:
        return "high"
    if forecast * 100 >= amount * alert_percent:
        return "watch"
    return "ok"


def _pace_risk(amount: Decimal, saved: Decimal, start: date, end: date, today: date) -> RiskLevel:
    if saved >= amount:
        return "ok"
    if today < start:
        return "ok"
    total = (end - start).days + 1
    gone = min(total, (today - start).days + 1)
    return "behind" if saved * total < amount * gone else "ok"


def plan_budget_report(budgets: list[Any], rows: list[Transaction], *, on: date, today: date, bills: list[Bill], bill_links: list,
                       lending: frozenset[str] | None = None, goals: list[Any] | None = None, remittances: list[Any] | None = None,
                       debt_payments: list[Any] | None = None, include_inactive: bool = False,
                       rollovers: list[Any] | None = None) -> PlanReport:
    """Each budget's figures for the period holding ``on``. ``rows`` must cover every budget's period and, with
    ``rollovers`` (each budget's terms period by period), every chain back to its start (``budget_rollover.window``)."""
    from . import budget_rollover as RO
    from .obligations import unpaid

    spend = spend_rows(rows, None, lending)
    goal_by_id = {g.id: g for g in goals or []}
    lines: list[PlanBudgetLine] = []
    for budget in budgets:
        if not budget.active and not include_inactive:
            continue
        history = RO.history_for(budget.id, rollovers)
        start, end = RO.period_for(budget, on, RO.repeats(budget, history))
        kind = getattr(budget.kind, "value", budget.kind)
        currency = budget.currency
        amount = quantize(budget.amount, currency)
        periods: list[RO.ChainPeriod] = []
        carried_in = Decimal(0)
        shown = RO.terms_on(history, start) if kind == "spending" else None
        current = start <= today <= end
        days_left = (end - today).days + 1 if current else (0 if today > end else (end - start).days + 1)
        coming_items: list[ComingItem] = []
        goal_saved = goal_target = None
        count = 0
        if kind == "spending":
            mine = [t for t in spend if start <= t.posted_on <= end and covers(budget, t)]
            count = sum(1 for t in mine if t.amount < 0)
            spent: Decimal | None = quantize(max(-sum((t.amount for t in mine), Decimal(0)), Decimal(0)), currency)
            basis = "spending"
            periods = RO.chain(budget, history, spend, on=on, today=today)
            if periods and periods[-1].start == start and periods[-1].currency == currency:
                # This period by the terms it used, counted the same way, plus what came in from the period before.
                amount, spent, count, carried_in = periods[-1].amount, periods[-1].spent, periods[-1].rows, periods[-1].carried_in
            if today <= end:
                # The current period also carries this month's passed due dates with no matched payment (still to pay).
                for due in unpaid(bills, bill_links, today if current else start, end):
                    bill = due.bill
                    if bill.currency != currency or bill.category not in budget.categories or due.due_on > end:
                        continue
                    if due.due_on < start and not (current and due.overdue):
                        continue
                    coming_items.append(ComingItem(bill_id=bill.id, name=bill.name, due_on=due.due_on, amount=quantize(bill.amount, currency),
                                                   overdue=current and due.overdue))
        elif kind in ("savings", "emergency", "sinking_fund"):
            goal = goal_by_id.get(budget.goal_id or "")
            if goal is not None:
                goal_saved, goal_target = quantize(goal.saved_amount, goal.currency), quantize(goal.target_amount, goal.currency)
            if goal is not None and goal.account_id:
                mine = [t for t in rows if t.account_id == goal.account_id and t.currency == currency and start <= t.posted_on <= end]
                count = len(mine)
                spent = quantize(sum((t.amount for t in mine), Decimal(0)), currency)
                basis = "goal_account"
            else:
                spent, basis = None, "goal"
        elif kind == "remittance":
            sent = [r for r in remittances or [] if r.currency == currency and start <= r.sent_on <= end]
            if sent or remittances:
                count = len(sent)
                spent = quantize(sum((r.amount + r.fee for r in sent), Decimal(0)), currency)
                basis = "remittances"
            else:
                mine = [t for t in spend if start <= t.posted_on <= end and t.currency == currency and t.category == Category.FAMILY_SUPPORT]
                count = sum(1 for t in mine if t.amount < 0)
                spent = quantize(max(-sum((t.amount for t in mine), Decimal(0)), Decimal(0)), currency)
                basis = "family_support"
        else:  # debt
            paid = [p for p in debt_payments or [] if p.currency == currency and start <= p.posted_on <= end
                    and (not budget.debt_ids or p.debt_id in budget.debt_ids)]
            count = len(paid)
            spent = quantize(sum((p.amount for p in paid), Decimal(0)), currency)
            basis = "debt_payments"
        coming = quantize(sum((c.amount for c in coming_items), Decimal(0)), currency)
        available = quantize(amount + carried_in, currency)
        if spent is None:
            forecast, risk = None, "unknown"
        elif kind == "spending":
            forecast = spent + coming
            if current and not budget.fixed:
                paid_bills = sum((o.paid_amount for o in bill_schedule([b for b in bills if b.category in budget.categories and b.currency == currency],
                                                                       bill_links, today, start, today) if o.status == "paid"), Decimal(0))
                elapsed = (today - start).days + 1
                variable = max(spent - paid_bills, Decimal(0))
                forecast += variable * (days_left - 1) / elapsed if elapsed > 0 else Decimal(0)
            forecast = quantize(forecast, currency)
            risk = _risk(available, spent, forecast, budget.alert_percent)
        else:
            forecast = spent
            risk = _pace_risk(amount, spent, start, end, today)
        over = quantize(max((spent or Decimal(0)) - available, Decimal(0)), currency) if kind == "spending" else quantize(Decimal(0), currency)
        if spent is None:
            percent = None
        elif available > 0:
            percent = int(spent * 100 / available)
        else:  # a carried deficit used the whole amount up
            percent = 100
        lines.append(PlanBudgetLine(
            id=budget.id, name=budget.name, kind=kind, period=getattr(budget.period, "value", budget.period), currency=currency, amount=amount,
            period_start=start, period_end=end, days_left=days_left, categories=list(budget.categories), subcategory_ids=list(budget.subcategory_ids),
            fixed=budget.fixed, essential=budget.essential, allocation=default_allocation(budget), alert_percent=budget.alert_percent,
            reserve_in_safe_to_spend=budget.reserve_in_safe_to_spend, active=budget.active, notes=budget.notes, goal_id=budget.goal_id,
            debt_ids=list(budget.debt_ids), spent=spent,
            remaining=quantize(max(available - spent, Decimal(0)), currency) if spent is not None else None, over=over,
            percent=percent, coming=coming, coming_items=coming_items,
            forecast=forecast, risk=risk, rows=count, basis=basis, goal_saved=goal_saved, goal_target=goal_target,
            rollover=shown.rollover.value if shown else "none",
            rollover_cap=quantize(shown.cap, currency) if shown and shown.cap is not None else None,
            overspend=shown.overspend.value if shown else "absorb", carried_in=quantize(carried_in, currency), available=available,
            chain=periods[-RO.CHAIN_SHOWN:], chain_periods=len(periods),
            starts_on=budget.starts_on, ends_on=budget.ends_on,
        ))
    lines.sort(key=lambda l: (l.currency, {"over": 0, "high": 1, "behind": 2, "watch": 3, "unknown": 4, "ok": 5}[l.risk], l.name.casefold()))
    totals: list[PlanTotals] = []
    for currency in sorted({l.currency for l in lines if l.kind == "spending" and l.period == "monthly"}):
        mine = [l for l in lines if l.currency == currency and l.kind == "spending" and l.period == "monthly"]
        budget_total = sum((l.amount for l in mine), Decimal(0))
        spent_total = sum((l.spent or Decimal(0) for l in mine), Decimal(0))
        carried_total = sum((l.carried_in for l in mine), Decimal(0))
        totals.append(PlanTotals(currency=currency, budget=quantize(budget_total, currency), carried_in=quantize(carried_total, currency),
                                 available=quantize(budget_total + carried_total, currency), spent=quantize(spent_total, currency),
                                 remaining=quantize(sum((l.remaining or Decimal(0) for l in mine), Decimal(0)), currency),
                                 coming=quantize(sum((l.coming for l in mine), Decimal(0)), currency),
                                 forecast=quantize(sum((l.forecast or Decimal(0) for l in mine), Decimal(0)), currency)))
    return PlanReport(on=on, today=today, budgets=lines, totals=totals)


class AllocationLine(BaseModel):
    budget_id: str
    name: str
    bucket: str
    monthly: Decimal


class AllocationView(BaseModel):
    currency: str
    income: Decimal | None  # the one monthly income figure (``salary.monthly_income``); None: no income recorded
    income_source: str | None
    buckets: dict[str, Decimal]
    allocated: Decimal
    unallocated: Decimal | None  # income less what the budgets plan; negative when they plan more than comes in
    lines: list[AllocationLine]
    left_out: list[AllocationLine]  # custom periods: not a monthly figure


def monthly_amount(budget: Any) -> Decimal | None:
    period = getattr(budget.period, "value", budget.period)
    if period == "monthly":
        return Decimal(budget.amount)
    if period == "weekly":
        return Decimal(budget.amount) * 52 / 12
    return None


def salary_allocation(budgets: list[Any], currency: str, income: Decimal | None, income_source: str | None) -> AllocationView:
    """How the month's income is split across the active budgets in ``currency``, per bucket (housing, food, transport,
    family, debts, cards, savings, remittances, other), and what is left unplanned."""
    from .models import ALLOCATION_BUCKETS

    buckets = {b: quantize(Decimal(0), currency) for b in ALLOCATION_BUCKETS}
    lines: list[AllocationLine] = []
    left: list[AllocationLine] = []
    for budget in budgets:
        if not budget.active or budget.currency != currency:
            continue
        bucket = default_allocation(budget)
        monthly = monthly_amount(budget)
        if monthly is None:
            left.append(AllocationLine(budget_id=budget.id, name=budget.name, bucket=bucket, monthly=quantize(Decimal(budget.amount), currency)))
            continue
        monthly = quantize(monthly, currency)
        buckets[bucket] = quantize(buckets[bucket] + monthly, currency)
        lines.append(AllocationLine(budget_id=budget.id, name=budget.name, bucket=bucket, monthly=monthly))
    allocated = quantize(sum((l.monthly for l in lines), Decimal(0)), currency)
    lines.sort(key=lambda l: (ALLOCATION_BUCKETS.index(l.bucket), -l.monthly, l.name.casefold()))
    return AllocationView(currency=currency, income=quantize(income, currency) if income is not None else None, income_source=income_source,
                          buckets=buckets, allocated=allocated, unallocated=quantize(income - allocated, currency) if income is not None else None,
                          lines=lines, left_out=left)


# -- link targets --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Lookup:
    bills: dict[str, Bill]
    debts: dict[str, Debt]


def _lookup(context: TenantContext) -> _Lookup:
    from .store import open_finance_store

    with open_finance_store(context) as store:
        bills = {b.id: b for b in store.list_items(context, "bill") if isinstance(b, Bill)}
        debts = {d.id: d for d in store.list_items(context, "debt") if isinstance(d, Debt)}
    return _Lookup(bills, debts)


def bill_target_exists(context: TenantContext, target_type: str, target_id: str) -> bool:
    """A bill target is one real due date of one of the caller's own bills."""
    parsed = parse_bill_target(target_id) if target_type == BILL_TARGET else None
    if parsed is None:
        return False
    bill_id, due = parsed
    bill = _lookup(context).bills.get(bill_id)
    return bill is not None and due in occurrences(bill, due, due)


def debt_target_exists(context: TenantContext, target_type: str, target_id: str) -> bool:
    return target_type == DEBT_TARGET and target_id in _lookup(context).debts


def is_money_out(entry: Any) -> bool:
    """The payment left one of the person's own accounts: a credit (negative) posting on an account.

    Read from the postings, never the category: a refund filed under shopping or an incoming transfer is
    money in, whatever its category says."""
    return (entry.category or "") != "income" and any(p.account_id and p.amount < 0 for p in entry.postings)


def vet_link(context: TenantContext, request: Any, entry: Any) -> None:
    """Only money out pays a bill or a debt, and only as a ``payment``."""
    from .links import LinkError

    if request.role != "payment":
        raise LinkError(422, "Match a payment to a bill or a debt.", "invalid_role")
    if not is_money_out(entry):
        raise LinkError(422, "Money in cannot pay a bill or a debt. Pick the payment that went out.", "not_a_payment")


def plug_finance_links() -> None:
    """Register the ``bill`` and ``debt`` link dimensions with the finance links foundation."""
    from . import links as K

    K.register_target(BILL_DIMENSION, bill_target_exists, vet=vet_link)
    K.register_target(DEBT_DIMENSION, debt_target_exists, vet=vet_link)
    # One own transfer (docs/finance.md#own-transfers): the money out links to the money in, once.
    K.register_target(TRANSFER_DIMENSION, transfer_target_exists, single_payment=True, vet=vet_transfer)


TRANSFER_DIMENSION = "transfer"
TRANSFER_TARGET = "finance_transfer"


def transfer_target_exists(context: TenantContext, target_type: str, target_id: str) -> bool:
    """The money-in side of an own transfer: a live canonical transaction of the caller's own."""
    from .links import live_entry
    from .sms_store import open_sms_stores

    if target_type != TRANSFER_TARGET:
        return False
    with open_sms_stores(context) as (_finance, store):
        current = live_entry(store.chain(context, target_id))
    return current is not None and any(p.account_id and p.amount > 0 for p in current.postings)


def vet_transfer(context: TenantContext, request: Any, entry: Any) -> None:
    """The money out of one account pays the money into another of the person's accounts, never itself."""
    from .links import LinkError, live_entry
    from .sms_store import open_sms_stores

    if request.role != "payment":
        raise LinkError(422, "Pair the money that went out with the money that came in.", "invalid_role")
    if not any(p.account_id and p.amount < 0 for p in entry.postings):
        raise LinkError(422, "Pair the money that went out with the money that came in.", "not_a_payment")
    if request.target_id == request.canonical_id:
        raise LinkError(422, "A transfer needs two different transactions.", "same_transaction")
    with open_sms_stores(context) as (_finance, store):
        other = live_entry(store.chain(context, request.target_id))
    out_accounts = {p.account_id for p in entry.postings if p.account_id and p.amount < 0}
    in_accounts = {p.account_id for p in other.postings if p.account_id and p.amount > 0} if other is not None else set()
    if not in_accounts or in_accounts <= out_accounts:
        raise LinkError(422, "A transfer moves money between two different accounts of yours.", "same_account")


__all__ = [
    "BILL_DIMENSION",
    "BILL_TARGET",
    "DEBT_DIMENSION",
    "DEBT_TARGET",
    "BillOccurrence",
    "AllocationView",
    "BudgetLine",
    "BudgetReport",
    "ComingItem",
    "PlanBudgetLine",
    "PlanReport",
    "budget_period",
    "covers",
    "default_allocation",
    "monthly_amount",
    "plan_budget_report",
    "salary_allocation",
    "alert_for",
    "bill_schedule",
    "bill_target_id",
    "is_money_out",
    "budget_report",
    "occurrence_status",
    "occurrences",
    "parse_bill_target",
    "plug_finance_links",
    "transfer_target_exists",
    "vet_transfer",
]
