"""Bills matched to recorded payments, debt payments, and budgets (docs/finance.md#bills-and-budgets).

A bill is a **forecast** until a recorded payment is matched to one of its due dates. A match is a
finance link (docs/finance-links.md) in the ``bill`` dimension: target ``finance_bill_due`` with the
id ``<bill id>:<YYYY-MM-DD>``, pointing at the payment's ``canonical_id``. Nothing here marks a bill
paid any other way: the status of a due date is worked out from its live links on every read.

A debt payment is a link in the ``debt`` dimension (target ``finance_debt``, the debt's id). The
debt's balance stays what the person entered: ANUM does not split a payment into interest and
principal, because it does not know the lender's terms.

A **budget** is one of the person's Guardian category caps (a monthly limit per category and
currency); the alert threshold per budget is kept with the plan (``Planning.budget_alerts``).

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
from .categories import NOT_SPEND
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


def _spend_rows(rows: list[Transaction]) -> list[Transaction]:
    return [t for t in rows if t.amount < 0 and t.category not in NOT_SPEND]


def budget_report(rules: GuardianRules, version: int, alerts: list[BudgetAlert], rows: list[Transaction], *, month: str, start: date, end: date,
                  today: date) -> BudgetReport:
    """Each cap with what was spent, what is left, its state and, this month, where the pace ends."""
    spend = _spend_rows(rows)
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
        spent = quantize(by_cat.get((cap.category, cap.currency), Decimal(0)), cap.currency)
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


__all__ = [
    "BILL_DIMENSION",
    "BILL_TARGET",
    "DEBT_DIMENSION",
    "DEBT_TARGET",
    "BillOccurrence",
    "BudgetLine",
    "BudgetReport",
    "alert_for",
    "bill_schedule",
    "bill_target_id",
    "is_money_out",
    "budget_report",
    "occurrence_status",
    "occurrences",
    "parse_bill_target",
    "plug_finance_links",
]
