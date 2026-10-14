"""The Guardian kernel applied to the person's ledger (docs/finance.md#guardian).

``guardian.py`` is pure. This module reads what it needs from the store: the current rule
version, the ledger state (this month's spend per category, a day's spend per currency),
and turns a ledger record into an action. It also lists the month's alerts (caps reached
or nearly reached, blocked merchants seen, days over the daily total).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from .categories import NOT_SPEND
from .guardian import (
    ActionKind,
    Decision,
    GuardianAction,
    GuardianRules,
    LedgerState,
    SpendRow,
    evaluate,
    merchant_matches,
)
from .models import Category, Transaction
from .store import FinanceStore
from .summary import month_range, settings_for, today_in

NEAR = Decimal("0.8")


def current_rules(store: FinanceStore, context: TenantContext) -> tuple[GuardianRules, int]:
    policy = store.get_policy(context)
    return (policy.rules, policy.version) if policy else (GuardianRules(), 0)


def _spend(rows: list[Transaction], exclude_id: str | None = None) -> list[Transaction]:
    return [t for t in rows if t.amount < 0 and t.category not in NOT_SPEND and t.id != exclude_id]


def ledger_state(store: FinanceStore, context: TenantContext, day: date, *, exclude_id: str | None = None) -> LedgerState:
    """The month of ``day`` up to and including it, and ``day`` itself."""
    settings = settings_for(store, context)
    start = day.replace(day=1)
    rows = _spend(store.transactions_between(context, start, day), exclude_id)
    month: dict[tuple[Category, str], Decimal] = {}
    today: dict[str, Decimal] = {}
    for t in rows:
        month[(t.category, t.currency)] = month.get((t.category, t.currency), Decimal(0)) - t.amount
        if t.posted_on == day:
            today[t.currency] = today.get(t.currency, Decimal(0)) - t.amount
    return LedgerState(
        timezone=settings.timezone,
        month_spend=[SpendRow(category=c, currency=cur, amount=v) for (c, cur), v in sorted(month.items(), key=lambda kv: (kv[0][0].value, kv[0][1]))],
        day_spend=[SpendRow(currency=cur, amount=v) for cur, v in sorted(today.items())],
    )


def check_record(store: FinanceStore, context: TenantContext, transaction: Transaction, now: datetime) -> Decision | None:
    """What the person's rules say about a ledger record of money out (None for money in)."""
    if transaction.amount >= 0 or transaction.category in NOT_SPEND:
        return None
    rules, _ = current_rules(store, context)
    if rules.is_empty():
        return None
    action = GuardianAction(
        kind=ActionKind.RECORD, amount=-transaction.amount, currency=transaction.currency, category=transaction.category,
        merchant=transaction.description, at=now,
    )
    return evaluate(action, rules, ledger_state(store, context, transaction.posted_on, exclude_id=transaction.id))


class GuardianAlert(BaseModel):
    code: Literal["cap_over", "cap_near", "merchant_blocked_seen", "category_blocked_seen", "daily_over", "single_over"]
    severity: Literal["over", "near"]
    message: str
    rule: str
    params: dict[str, str] = Field(default_factory=dict)


class GuardianAlerts(BaseModel):
    month: str
    version: int
    alerts: list[GuardianAlert]


def month_alerts(store: FinanceStore, context: TenantContext, month: str | None = None, *, now: datetime | None = None) -> GuardianAlerts:
    settings = settings_for(store, context)
    label, start, end = month_range(month, today_in(settings, now))
    rules, version = current_rules(store, context)
    alerts: list[GuardianAlert] = []
    if rules.is_empty():
        return GuardianAlerts(month=label, version=version, alerts=alerts)
    rows = _spend(store.transactions_between(context, start, end))
    by_cat: dict[tuple[Category, str], Decimal] = {}
    by_day: dict[tuple[date, str], Decimal] = {}
    for t in rows:
        by_cat[(t.category, t.currency)] = by_cat.get((t.category, t.currency), Decimal(0)) - t.amount
        by_day[(t.posted_on, t.currency)] = by_day.get((t.posted_on, t.currency), Decimal(0)) - t.amount
    for cap in rules.category_caps:
        spent = by_cat.get((cap.category, cap.currency), Decimal(0))
        params = {"category": cap.category.value, "currency": cap.currency, "spent": str(spent), "limit": str(cap.amount)}
        if spent > cap.amount:
            alerts.append(GuardianAlert(code="cap_over", severity="over", rule="category_caps", params=params,
                                        message=f"{cap.category.value}: {cap.currency} {spent} spent, over your cap of {cap.currency} {cap.amount}."))
        elif spent >= cap.amount * NEAR:
            alerts.append(GuardianAlert(code="cap_near", severity="near", rule="category_caps", params=params,
                                        message=f"{cap.category.value}: {cap.currency} {spent} spent, near your cap of {cap.currency} {cap.amount}."))
    for phrase in rules.blocked_merchants:
        seen = [t for t in rows if merchant_matches(phrase, t.description)]
        if seen:
            alerts.append(GuardianAlert(code="merchant_blocked_seen", severity="over", rule="blocked_merchants",
                                        params={"merchant": phrase, "count": str(len(seen))},
                                        message=f"{len(seen)} payment(s) to {phrase}, which you blocked."))
    for category in rules.blocked_categories:
        seen = [t for t in rows if t.category == category]
        if seen:
            alerts.append(GuardianAlert(code="category_blocked_seen", severity="over", rule="blocked_categories",
                                        params={"category": category.value, "count": str(len(seen))},
                                        message=f"{len(seen)} payment(s) in {category.value}, which you blocked."))
    for limit in rules.max_daily_total:
        days = sorted(d for (d, cur), v in by_day.items() if cur == limit.currency and v > limit.amount)
        if days:
            alerts.append(GuardianAlert(code="daily_over", severity="over", rule="max_daily_total",
                                        params={"currency": limit.currency, "limit": str(limit.amount), "count": str(len(days)), "last": days[-1].isoformat()},
                                        message=f"{len(days)} day(s) over your daily total of {limit.currency} {limit.amount}."))
    for limit in rules.max_single:
        over = [t for t in rows if t.currency == limit.currency and -t.amount > limit.amount]
        if over:
            alerts.append(GuardianAlert(code="single_over", severity="over", rule="max_single",
                                        params={"currency": limit.currency, "limit": str(limit.amount), "count": str(len(over))},
                                        message=f"{len(over)} payment(s) above your single limit of {limit.currency} {limit.amount}."))
    return GuardianAlerts(month=label, version=version, alerts=alerts)
