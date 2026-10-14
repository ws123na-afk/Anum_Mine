"""The Financial Twin's read-outs (docs/finance.md#summary).

All figures are per currency: nothing is converted or added across currencies. Dates are
the person's own (``FinanceSettings.timezone``, Asia/Dubai by default): "this month" and
"today" are in that time zone.

* Balances: an account's opening balance plus every transaction in it.
* Spend by category: money out in the month, transfers and income excluded.
* Income versus spend: money in (transfers excluded) against money out in the month.
* Upcoming bills: every active bill's due dates in the next ``days`` days (default 30).
* Debts: what is owed and the minimum payments, per currency.
* Bill suggestions: money out with the same description at a steady interval and a
  steady amount (three times or more in 180 days), not yet saved as a bill.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from ..schemas import TenantContext
from .categories import NOT_SPEND
from .models import (
    AccountKind,
    Bill,
    BillSource,
    Cadence,
    Category,
    Debt,
    FinanceSettings,
    Goal,
    Transaction,
)
from .money import quantize, totals
from .store import FinanceStore

UPCOMING_DAYS = 30
MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class CurrencyAmount(BaseModel):
    currency: str
    amount: Decimal


class AccountBalance(BaseModel):
    id: str
    name: str
    kind: AccountKind
    currency: str
    institution: str | None
    last4: str | None
    balance: Decimal
    archived: bool


class CategorySpend(BaseModel):
    category: Category
    currency: str
    amount: Decimal  # money out, as a positive figure
    count: int


class UpcomingBill(BaseModel):
    id: str
    name: str
    amount: Decimal
    currency: str
    cadence: Cadence
    due_on: date
    days_until: int
    category: Category


class GoalProgress(BaseModel):
    id: str
    name: str
    currency: str
    target_amount: Decimal
    saved_amount: Decimal
    percent: int
    target_on: date | None


class FinanceSummary(BaseModel):
    month: str
    start: date
    end: date
    today: date
    base_currency: str
    timezone: str
    accounts: list[AccountBalance]
    balances: list[CurrencyAmount]
    spend_by_category: list[CategorySpend]
    income: list[CurrencyAmount]
    spend: list[CurrencyAmount]
    net: list[CurrencyAmount]
    transaction_count: int
    upcoming_bills: list[UpcomingBill]
    bills_due: list[CurrencyAmount]
    debts: list[CurrencyAmount]
    minimum_payments: list[CurrencyAmount]
    debt_count: int
    goals: list[GoalProgress]


class BillSuggestion(BaseModel):
    name: str
    amount: Decimal
    currency: str
    cadence: Cadence
    next_due_on: date
    category: Category
    account_id: str
    occurrences: int


def settings_for(store: FinanceStore, context: TenantContext) -> FinanceSettings:
    return store.get_settings(context) or FinanceSettings()


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Dubai")


def today_in(settings: FinanceSettings, now: datetime | None = None) -> date:
    moment = now or datetime.now(tz=ZoneInfo("UTC"))
    return moment.astimezone(zone(settings.timezone)).date()


def month_range(month: str | None, today: date) -> tuple[str, date, date]:
    """``("2026-10", 1 Oct, 31 Oct)``; ``None`` is the month of ``today``."""
    if month:
        found = MONTH.match(month)
        if not found:
            raise ValueError("A month is written YYYY-MM, for example 2026-10.")
        year, number = int(found.group(1)), int(found.group(2))
    else:
        year, number = today.year, today.month
    last = calendar.monthrange(year, number)[1]
    return f"{year:04d}-{number:02d}", date(year, number, 1), date(year, number, last)


def add_months(day: date, months: int, anchor_day: int | None = None) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    wanted = anchor_day or day.day
    return date(year, month, min(wanted, calendar.monthrange(year, month)[1]))


def next_after(due: date, cadence: Cadence, anchor_day: int) -> date | None:
    if cadence == Cadence.WEEKLY:
        return due + timedelta(days=7)
    if cadence == Cadence.MONTHLY:
        return add_months(due, 1, anchor_day)
    if cadence == Cadence.QUARTERLY:
        return add_months(due, 3, anchor_day)
    if cadence == Cadence.YEARLY:
        return add_months(due, 12, anchor_day)
    return None


def due_dates(bill: Bill, start: date, end: date) -> list[date]:
    """The bill's due dates from ``start`` to ``end`` (inclusive); a past one rolls forward."""
    found: list[date] = []
    due: date | None = bill.next_due_on
    anchor = bill.next_due_on.day
    guard = 0
    while due is not None and due <= end and guard < 600:
        guard += 1
        if due >= start:
            found.append(due)
        due = next_after(due, bill.cadence, anchor)
    return found


def account_balances(store: FinanceStore, context: TenantContext) -> list[AccountBalance]:
    sums = store.account_sums(context)
    return [
        AccountBalance(
            id=a.id, name=a.name, kind=a.kind, currency=a.currency, institution=a.institution, last4=a.last4,
            balance=quantize(a.opening_balance + sums.get(a.id, Decimal(0)), a.currency), archived=a.archived,
        )
        for a in store.list_accounts(context)
    ]


def _amounts(values: dict[str, Decimal]) -> list[CurrencyAmount]:
    return [CurrencyAmount(currency=c, amount=quantize(v, c)) for c, v in values.items()]


def upcoming_bills(bills: list[Bill], today: date, days: int = UPCOMING_DAYS) -> list[UpcomingBill]:
    end = today + timedelta(days=days)
    rows = [
        UpcomingBill(
            id=b.id, name=b.name, amount=b.amount, currency=b.currency, cadence=b.cadence, due_on=due,
            days_until=(due - today).days, category=b.category,
        )
        for b in bills
        if b.active
        for due in due_dates(b, today, end)
    ]
    return sorted(rows, key=lambda r: (r.due_on, r.name.lower()))


def build_summary(store: FinanceStore, context: TenantContext, month: str | None = None, *, now: datetime | None = None) -> FinanceSummary:
    settings = settings_for(store, context)
    today = today_in(settings, now)
    label, start, end = month_range(month, today)
    accounts = account_balances(store, context)
    month_rows: list[Transaction] = store.transactions_between(context, start, end)
    spending = [t for t in month_rows if t.amount < 0 and t.category not in NOT_SPEND]
    by_category: dict[tuple[Category, str], tuple[Decimal, int]] = {}
    for t in spending:
        amount, count = by_category.get((t.category, t.currency), (Decimal(0), 0))
        by_category[(t.category, t.currency)] = (amount - t.amount, count + 1)
    category_rows = sorted(
        (CategorySpend(category=cat, currency=cur, amount=quantize(v, cur), count=n) for (cat, cur), (v, n) in by_category.items()),
        key=lambda r: (r.currency, -r.amount, r.category.value),
    )
    income = totals((t.currency, t.amount) for t in month_rows if t.amount > 0 and t.category != Category.TRANSFER)
    spend = totals((t.currency, -t.amount) for t in spending)
    net_keys = sorted(set(income) | set(spend))
    net = {c: income.get(c, Decimal(0)) - spend.get(c, Decimal(0)) for c in net_keys}
    bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    upcoming = upcoming_bills(bills, today)
    debts = [d for d in store.list_items(context, "debt") if isinstance(d, Debt)]
    goals = [g for g in store.list_items(context, "goal") if isinstance(g, Goal)]
    return FinanceSummary(
        month=label, start=start, end=end, today=today, base_currency=settings.base_currency, timezone=settings.timezone,
        accounts=accounts,
        balances=_amounts(totals((a.currency, a.balance) for a in accounts if not a.archived)),
        spend_by_category=category_rows,
        income=_amounts(income), spend=_amounts(spend), net=_amounts(net),
        transaction_count=len(month_rows),
        upcoming_bills=upcoming,
        bills_due=_amounts(totals((b.currency, b.amount) for b in upcoming)),
        debts=_amounts(totals((d.currency, d.balance) for d in debts)),
        minimum_payments=_amounts(totals((d.currency, d.minimum_payment) for d in debts)),
        debt_count=len(debts),
        goals=[
            GoalProgress(
                id=g.id, name=g.name, currency=g.currency, target_amount=g.target_amount, saved_amount=g.saved_amount,
                percent=max(0, min(100, int(g.saved_amount * 100 / g.target_amount))) if g.target_amount > 0 else 0,
                target_on=g.target_on,
            )
            for g in goals
        ],
    )


# -- recurring bill detection -------------------------------------------------------------------

_CADENCE_DAYS = ((Cadence.WEEKLY, 6, 8), (Cadence.MONTHLY, 26, 35), (Cadence.QUARTERLY, 84, 98), (Cadence.YEARLY, 350, 380))


def _payee(description: str) -> str:
    text = re.sub(r"\d+", " ", description.lower())
    text = re.sub(r"[^\w\s&.]", " ", text)
    return " ".join(text.split())[:60]


def suggest_bills(store: FinanceStore, context: TenantContext, *, now: datetime | None = None) -> list[BillSuggestion]:
    settings = settings_for(store, context)
    today = today_in(settings, now)
    rows = store.transactions_between(context, today - timedelta(days=400), today)
    known = {_payee(b.name) for b in store.list_items(context, "bill") if isinstance(b, Bill)}
    groups: dict[tuple[str, str, str], list[Transaction]] = {}
    for t in rows:
        if t.amount >= 0 or t.category in NOT_SPEND:
            continue
        key = _payee(t.description)
        if key:
            groups.setdefault((t.account_id, t.currency, key), []).append(t)
    found: list[BillSuggestion] = []
    for (account_id, currency, key), items in groups.items():
        if len(items) < 3 or key in known:
            continue
        items.sort(key=lambda t: t.posted_on)
        gaps = [(b.posted_on - a.posted_on).days for a, b in pairwise(items)]
        typical = median(gaps)
        cadence = next((c for c, low, high in _CADENCE_DAYS if low <= typical <= high), None)
        if cadence is None or any(not (typical * 0.6 <= g <= typical * 1.4) for g in gaps):
            continue
        amounts = [-t.amount for t in items]
        middle = Decimal(median(amounts))
        if any(abs(a - middle) > middle * Decimal("0.15") for a in amounts):
            continue
        last = items[-1]
        due = next_after(last.posted_on, cadence, last.posted_on.day) or last.posted_on
        while due < today:
            due = next_after(due, cadence, last.posted_on.day) or today
        found.append(
            BillSuggestion(
                name=last.description[:120], amount=quantize(middle, currency), currency=currency, cadence=cadence,
                next_due_on=due, category=last.category if last.category != Category.OTHER else Category.UTILITIES,
                account_id=account_id, occurrences=len(items),
            )
        )
    return sorted(found, key=lambda s: (s.next_due_on, s.name.lower()))[:50]


__all__ = ["BillSource", "build_summary", "month_range", "settings_for", "suggest_bills", "today_in", "upcoming_bills"]
