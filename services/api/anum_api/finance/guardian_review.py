"""The Guardian review queue (docs/finance.md#review-queue).

Guardian is a monitoring aid. For one month it checks every payment out of the person's own
ledger against the rules that apply now (paused kinds left out), as ``evaluate`` checks a
``record``: in posting order, each payment against the month's and the day's spend before
it. A payment a rule flags waits for the person in the review queue until they mark it
expected or reviewed, or snooze it for a few days. A decision covers the payment as it
was: if its amount or currency is corrected later, it waits again.

Nothing here moves, holds or approves money, and nothing is sent to a bank: a decision is
only the person's own note, stored per person under forced RLS (``finance_guardian_reviews``)
and audited with the transaction id, the decision and the rule codes, never an amount.

For each flagged payment the queue also shows what is typical: the median of the person's
earlier payments (up to a year back) with the same cleaned description and currency, and
how many there were, so they can judge it. All figures come from their own records.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from .categories import NOT_SPEND
from .guardian import (
    ActionKind,
    GuardianAction,
    GuardianReview,
    GuardianRules,
    ItemState,
    LedgerState,
    Reason,
    SpendRow,
    _merchant,
    evaluate,
)
from .models import Category, Transaction
from .policy import current_rules
from .store import FinanceStore
from .summary import month_range, settings_for, today_in, zone

StateFilter = Literal["awaiting", "resolved", "snoozed", "all"]
TYPICAL_DAYS = 365
SNOOZE_DAYS = (1, 3, 7, 30)
# Kinds that only apply to an action asked for (an order, a payment ANUM would make), never to
# money already recorded: the queue cannot flag them.
ACTION_ONLY_KINDS = ("approval_above", "quiet_hours", "cooling_off")


class FlaggedItem(BaseModel):
    transaction_id: str
    posted_on: date
    description: str
    amount: Decimal  # money out, positive
    currency: str
    category: Category
    account_id: str
    account_name: str | None
    recorded_at: datetime
    outcome: Literal["needs_approval", "deny"]
    reasons: list[Reason]
    rules: list[str]
    typical_amount: Decimal | None
    prior_count: int
    state: ItemState
    decided_at: datetime | None = None
    snoozed_until: datetime | None = None


class RuleStat(BaseModel):
    kind: str
    count: int
    last: date | None


class ReviewCounts(BaseModel):
    flagged: int
    awaiting: int
    resolved: int
    snoozed: int


class ReviewQueue(BaseModel):
    month: str
    version: int
    timezone: str
    counts: ReviewCounts
    rules: list[RuleStat]
    items: list[FlaggedItem]
    total: int
    limit: int
    offset: int


def _spend(rows: list[Transaction]) -> list[Transaction]:
    return [t for t in rows if t.amount < 0 and t.category not in NOT_SPEND]


def flag(rows: list[Transaction], rules: GuardianRules, timezone: str, now: datetime) -> list[tuple[Transaction, list[Reason]]]:
    """Each payment out in ``rows`` (one month) that a rule flags, with the rules' reasons. Pure."""
    month: dict[tuple[Category, str], Decimal] = {}
    day: dict[tuple[date, str], Decimal] = {}
    flagged: list[tuple[Transaction, list[Reason]]] = []
    for t in sorted(_spend(rows), key=lambda r: (r.posted_on, r.created_at, r.id)):
        amount = -t.amount
        state = LedgerState(
            timezone=timezone,
            month_spend=[SpendRow(category=c, currency=cur, amount=v) for (c, cur), v in month.items()],
            day_spend=[SpendRow(currency=cur, amount=v) for (d, cur), v in day.items() if d == t.posted_on],
        )
        action = GuardianAction(kind=ActionKind.RECORD, amount=amount, currency=t.currency, category=t.category,
                                merchant=t.description, at=now)
        decision = evaluate(action, rules, state)
        if decision.outcome != "allow":
            flagged.append((t, decision.reasons))
        month[(t.category, t.currency)] = month.get((t.category, t.currency), Decimal(0)) + amount
        day[(t.posted_on, t.currency)] = day.get((t.posted_on, t.currency), Decimal(0)) + amount
    return flagged


def typical(history: list[Transaction], item: Transaction) -> tuple[Decimal | None, int]:
    """The median of earlier payments with the same description and currency, and how many."""
    key = _merchant(item.description)
    since = item.posted_on - timedelta(days=TYPICAL_DAYS)
    earlier = [-t.amount for t in _spend(history)
               if t.id != item.id and t.currency == item.currency and since <= t.posted_on < item.posted_on and key and _merchant(t.description) == key]
    if not earlier:
        return None, 0
    return Decimal(median(earlier)).quantize(Decimal("0.01")), len(earlier)


def _matches(state: ItemState, wanted: StateFilter) -> bool:
    if wanted == "all":
        return True
    if wanted == "resolved":
        return state in ("expected", "reviewed")
    return state == wanted


def build_queue(store: FinanceStore, context: TenantContext, month: str | None, *, state: StateFilter, limit: int, offset: int,
                now: datetime) -> ReviewQueue:
    settings = settings_for(store, context)
    label, start, end = month_range(month, today_in(settings, now))
    rules, version = current_rules(store, context)
    active = rules.active()
    if active.is_empty():
        return ReviewQueue(month=label, version=version, timezone=settings.timezone,
                           counts=ReviewCounts(flagged=0, awaiting=0, resolved=0, snoozed=0),
                           rules=[RuleStat(kind=k, count=0, last=None) for k in rules.configured_kinds()],
                           items=[], total=0, limit=limit, offset=offset)
    history = store.transactions_between(context, start - timedelta(days=TYPICAL_DAYS), end)
    in_month = [t for t in history if start <= t.posted_on <= end]
    flagged = flag(in_month, active, settings.timezone, now)
    reviews = {r.transaction_id: r for r in store.list_reviews(context, [t.id for t, _ in flagged])}
    accounts = {a.id: a.name for a in store.list_accounts(context)}
    stats: dict[str, RuleStat] = {k: RuleStat(kind=k, count=0, last=None) for k in rules.configured_kinds()}
    items: list[FlaggedItem] = []
    for t, reasons in flagged:
        kinds = [r.rule for r in reasons if r.rule]
        for kind in dict.fromkeys(kinds):
            stat = stats.get(kind)
            if stat is not None:
                stat.count += 1
                stat.last = t.posted_on if stat.last is None or t.posted_on > stat.last else stat.last
        review = reviews.get(t.id)
        decided = review if review is not None and review.applies_to(t) else None
        item_state: ItemState = (decided.state(now) if decided else None) or "awaiting"
        amount, count = typical(history, t)
        outcome: Literal["needs_approval", "deny"] = "deny" if any(r.outcome == "deny" for r in reasons) else "needs_approval"
        items.append(FlaggedItem(
            transaction_id=t.id, posted_on=t.posted_on, description=t.description, amount=-t.amount, currency=t.currency,
            category=t.category, account_id=t.account_id, account_name=accounts.get(t.account_id), recorded_at=t.created_at,
            outcome=outcome, reasons=reasons, rules=list(dict.fromkeys(kinds)), typical_amount=amount, prior_count=count,
            state=item_state, decided_at=decided.decided_at if decided and item_state != "awaiting" else None,
            snoozed_until=decided.snoozed_until if decided and item_state == "snoozed" else None,
        ))
    # Newest first: what happened last is what the person most likely wants to check.
    items.sort(key=lambda i: (i.posted_on, i.recorded_at, i.transaction_id), reverse=True)
    counts = ReviewCounts(
        flagged=len(items),
        awaiting=sum(1 for i in items if i.state == "awaiting"),
        resolved=sum(1 for i in items if i.state in ("expected", "reviewed")),
        snoozed=sum(1 for i in items if i.state == "snoozed"),
    )
    chosen = [i for i in items if _matches(i.state, state)]
    return ReviewQueue(month=label, version=version, timezone=settings.timezone, counts=counts, rules=list(stats.values()),
                       items=chosen[offset:offset + limit], total=len(chosen), limit=limit, offset=offset)


def snooze_end(now: datetime, days: int, timezone: str) -> datetime:
    """Snoozed until the start of the day ``days`` days from now, in the person's time zone."""
    local = now.astimezone(zone(timezone))
    return (local + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
