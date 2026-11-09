"""The Financial Twin's read-outs (docs/finance.md#summary).

All figures are per currency: nothing is converted or added across currencies. Dates are
the person's own (``FinanceSettings.timezone``, Asia/Dubai by default): "this month" and
"today" are in that time zone.

* Balances: an account's opening balance plus every transaction in it.
* Spend, income, refunds, cashback and transfers: counted by the one shared rule
  (``flows.py``, docs/finance.md#how-figures-are-counted). Spend by category is net of the
  refunds filed in that category; transfers and card payments are neither spend nor income.
* Uncategorized: rows no rule recognised are in none of those figures; the summary counts them
  (``uncategorized_count`` and the amounts in and out) so the page says what it left out.
* Upcoming bills: every active bill's due dates in the next ``days`` days (default 30) that
  have no matched payment, and this month's passed ones that still have none (overdue).
* Debts: what is owed (following matched payments and linked accounts, ``obligations.py``)
  and the minimum payments, per currency. Net worth is account balances less only the debts
  that have no account of their own.
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

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from . import obligations as O
from .flows import NOT_SPEND, figures, kinds_of
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
    days_until: int  # negative: overdue
    category: Category
    overdue: bool = False  # passed this month with no matched payment


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
    cashback: list[CurrencyAmount] = Field(default_factory=list)
    refunds: list[CurrencyAmount] = Field(default_factory=list)
    spend: list[CurrencyAmount]
    net: list[CurrencyAmount]
    transaction_count: int
    upcoming_bills: list[UpcomingBill]
    bills_due: list[CurrencyAmount]
    debts: list[CurrencyAmount]
    # Debts no account counts yet (no card or loan account of their own): the only ones net worth subtracts.
    unlinked_debts: list[CurrencyAmount] = Field(default_factory=list)
    net_worth: list[CurrencyAmount] = Field(default_factory=list)
    minimum_payments: list[CurrencyAmount]
    debt_count: int
    goals: list[GoalProgress]
    month_rows_capped: bool = False  # the month had more rows than one read takes: figures are partial
    # Rows no rule recognised (type ``uncategorized``): in none of the figures above, held for review. Shown as
    # "N uncategorized, not counted" so a missing income or spend is never a silent zero.
    uncategorized_count: int = 0
    uncategorized_in: list[CurrencyAmount] = Field(default_factory=list)
    uncategorized_out: list[CurrencyAmount] = Field(default_factory=list)


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


def upcoming_bills(bills: list[Bill], today: date, days: int = UPCOMING_DAYS, links: list | None = None) -> list[UpcomingBill]:
    """Due dates in the next ``days`` days without a matched payment, and this month's overdue ones.

    ``links`` are the person's ``bill`` links (``obligations.load``); a due date with an active one is paid."""
    end = today + timedelta(days=days)
    rows = [
        UpcomingBill(
            id=d.bill.id, name=d.bill.name, amount=d.bill.amount, currency=d.bill.currency, cadence=d.bill.cadence, due_on=d.due_on,
            days_until=(d.due_on - today).days, category=d.bill.category, overdue=d.overdue,
        )
        for d in O.unpaid(bills, links or [], today, end)
    ]
    return sorted(rows, key=lambda r: (r.due_on, r.name.lower()))


MONTH_ROW_LIMIT = 50_000  # what one ``transactions_between`` read returns at most (store.py)


def build_summary(store: FinanceStore, context: TenantContext, month: str | None = None, *, now: datetime | None = None,
                  links: O.Links | None = None) -> FinanceSummary:
    """``links`` are the person's bill and debt links (``obligations.load``, read in its own unit of work first)."""
    settings = settings_for(store, context)
    today = today_in(settings, now)
    label, start, end = month_range(month, today)
    raw_accounts = store.list_accounts(context)
    sums = store.account_sums(context)
    accounts = account_balances(store, context)
    month_rows: list[Transaction] = store.transactions_between(context, start, end)
    found = figures(month_rows, kinds_of(raw_accounts), links.lending_ids if links else None)
    category_rows = sorted(
        (CategorySpend(category=cat, currency=cur, amount=quantize(v, cur), count=n) for (cat, cur), (v, n) in found.by_category.items() if v != 0),
        key=lambda r: (r.currency, -r.amount, r.category.value),
    )
    bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    upcoming = upcoming_bills(bills, today, links=links.bill_links if links else None)
    debts = O.current_debts([d for d in store.list_items(context, "debt") if isinstance(d, Debt)], raw_accounts, sums, links)
    goals = O.current_goals([g for g in store.list_items(context, "goal") if isinstance(g, Goal)], raw_accounts, sums)
    balances = totals((a.currency, a.balance) for a in accounts if not a.archived)
    loose = totals((d.currency, d.balance) for d in O.unlinked(debts, raw_accounts) if d.balance > 0)
    worth = {c: balances.get(c, Decimal(0)) - loose.get(c, Decimal(0)) for c in sorted(set(balances) | set(loose))}
    return FinanceSummary(
        month=label, start=start, end=end, today=today, base_currency=settings.base_currency, timezone=settings.timezone,
        accounts=accounts,
        balances=_amounts(balances),
        spend_by_category=category_rows,
        income=_amounts(found.income), cashback=_amounts(found.cashback), refunds=_amounts(found.refunds), spend=_amounts(found.spend),
        net=_amounts(found.net()),
        uncategorized_count=found.uncategorized_count, uncategorized_in=_amounts(found.uncategorized_in),
        uncategorized_out=_amounts(found.uncategorized_out),
        transaction_count=len(month_rows), month_rows_capped=len(month_rows) >= MONTH_ROW_LIMIT,
        upcoming_bills=upcoming,
        bills_due=_amounts(totals((b.currency, b.amount) for b in upcoming)),
        debts=_amounts(totals((d.currency, d.balance) for d in debts)),
        unlinked_debts=_amounts(loose), net_worth=_amounts(worth),
        minimum_payments=_amounts(totals((d.currency, min(d.minimum_payment, d.balance)) for d in debts if d.balance > 0)),
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
    bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    return detect_recurring(rows, today, [b.name for b in bills], bills)


def _regular(gap: int, typical: float) -> bool:
    return typical * 0.6 <= gap <= typical * 1.4


def _skipped(gap: int, typical: float) -> bool:
    """One period missed (a month without the payment) keeps the pattern."""
    return typical * 1.6 <= gap <= typical * 2.4


def _covered(bills: list[Bill], currency: str, cadence: Cadence, middle: Decimal, account_ids: set[str]) -> bool:
    """A saved bill already stands for this payment, whatever the bank calls it ("Electricity" and "DEWA BILL")."""
    for bill in bills:
        if not bill.active or bill.currency != currency or bill.cadence != cadence:
            continue
        if bill.account_id and bill.account_id not in account_ids:
            continue
        if abs(bill.amount - middle) <= max(bill.amount, middle) * Decimal("0.15"):
            return True
    return False


def detect_recurring(rows: list[Transaction], today: date, known_names: list[str], known_bills: list[Bill] | None = None) -> list[BillSuggestion]:
    """Pure: money out with the same payee at a steady interval and amount, not yet a bill.

    One payee is one group across accounts (a bill paid from two accounts is still one bill), one missed
    period does not break the pattern, and a saved bill with the same currency, cadence and amount (within
    15 percent) counts as already saved even when its name differs from the bank's wording."""
    known = {_payee(name) for name in known_names}
    groups: dict[tuple[str, str], list[Transaction]] = {}
    for t in rows:
        if t.amount >= 0 or t.category in NOT_SPEND:
            continue
        key = _payee(t.description)
        if key:
            groups.setdefault((t.currency, key), []).append(t)
    found: list[BillSuggestion] = []
    for (currency, key), items in groups.items():
        if len(items) < 3 or key in known:
            continue
        items.sort(key=lambda t: t.posted_on)
        gaps = [(b.posted_on - a.posted_on).days for a, b in pairwise(items)]
        base = min(gaps) if gaps else 0
        # A gap of about two periods is one missed payment: halve it to find the usual interval.
        typical = median([g / 2 if base > 0 and g >= base * 1.6 else g for g in gaps]) if gaps else 0
        cadence = next((c for c, low, high in _CADENCE_DAYS if low <= typical <= high), None)
        if cadence is None or not all(_regular(g, typical) or _skipped(g, typical) for g in gaps):
            continue
        if sum(1 for g in gaps if _regular(g, typical)) * 2 < len(gaps):
            continue
        amounts = [-t.amount for t in items]
        middle = Decimal(median(amounts))
        if any(abs(a - middle) > middle * Decimal("0.15") for a in amounts):
            continue
        if _covered(known_bills or [], currency, cadence, middle, {t.account_id for t in items}):
            continue
        last = items[-1]
        account_id = last.account_id
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


__all__ = ["BillSource", "build_summary", "detect_recurring", "month_range", "settings_for", "suggest_bills", "today_in", "upcoming_bills"]
