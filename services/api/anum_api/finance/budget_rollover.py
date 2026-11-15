"""Budget rollover: what a spending budget carries from one period into the next (docs/finance.md#budget-rollover).

Pure. The owner's budget terms are kept period by period (``Planning.budget_rollovers``, ``models.BudgetTerms``): an
entry says which amount, currency, categories and rollover a budget used from a period's first day on, and is written
only for the period the owner saves the budget in. The chain is worked out on every read from the spending rows the
budget covers, in order from the first period whose terms carry anything:

* ``available`` = the period's amount + ``carried_in``; ``left`` = ``available`` - ``spent``.
* At the end of a closed period, money left carries as its terms say: ``none`` nothing, ``surplus`` all of it, ``capped``
  at most the cap. An overspend (``left`` below zero) carries as a deficit with ``overspend: carry`` and is absorbed with
  ``absorb``. A period that has not ended carries nothing yet (``carried_out`` is ``None``).
* Money never carries between currencies: when the next period's terms are in another currency the chain starts again
  from zero there (no rate is invented).

Each row is counted once, in the period of its posting date, by the shared spending rule (``commitments.covers`` over the
rows ``flows.spend_rows`` keeps). Nothing here writes a transaction or a stored figure; the same rows and terms always give
the same chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from .models import BudgetTerms, OverspendMode, RolloverMode, Transaction
from .money import quantize

MAX_TERMS_PER_BUDGET = 400  # one entry per period the budget was saved in
CHAIN_SHOWN = 12  # the periods a report lists (the whole chain is always worked out)


class ChainPeriod(BaseModel):
    start: date
    end: date
    currency: str
    amount: Decimal
    carried_in: Decimal
    available: Decimal
    spent: Decimal
    rows: int
    left: Decimal  # available less spent: below zero is an overspend
    carried_out: Decimal | None  # None: the period has not ended yet
    rollover: str
    cap: Decimal | None
    overspend: str
    restarted: bool = False  # the currency changed: nothing was carried in


@dataclass(frozen=True)
class _Cover:
    currency: str
    categories: list[Any]
    subcategory_ids: list[str]


def carries(terms: BudgetTerms) -> bool:
    return terms.rollover != RolloverMode.NONE or terms.overspend == OverspendMode.CARRY


def history_for(budget_id: str, rollovers: list[BudgetTerms] | None) -> list[BudgetTerms]:
    return sorted((t for t in rollovers or [] if t.budget_id == budget_id), key=lambda t: t.effective_from)


def repeats(budget: Any, history: list[BudgetTerms]) -> bool:
    """A custom period repeats back to back (the same length, from the day after its last day) once the budget has
    rollover terms: that gives it periods to carry between. Monthly and weekly budgets always repeat."""
    return getattr(budget.period, "value", budget.period) == "custom" and bool(history)


def period_for(budget: Any, on: date, repeating: bool = False) -> tuple[date, date]:
    from .commitments import budget_period

    period = getattr(budget.period, "value", budget.period)
    if repeating and period == "custom" and budget.starts_on is not None and budget.ends_on is not None:
        length = (budget.ends_on - budget.starts_on).days + 1
        index = (on - budget.starts_on).days // length
        start = budget.starts_on + timedelta(days=index * length)
        return start, start + timedelta(days=length - 1)
    return budget_period(budget, on)


def terms_on(history: list[BudgetTerms], start: date) -> BudgetTerms | None:
    """The terms a period starting ``start`` used: the newest entry from that day or earlier."""
    found = None
    for terms in history:
        if terms.effective_from <= start:
            found = terms
        else:
            break
    return found


def chain_start(budget: Any, history: list[BudgetTerms]) -> date | None:
    """The first day of the first period whose terms carry anything; None: nothing ever carried."""
    first = next((t for t in history if carries(t)), None)
    if first is None:
        return None
    return period_for(budget, first.effective_from, repeats(budget, history))[0]


def window(budgets: list[Any], rollovers: list[BudgetTerms] | None, on: date) -> tuple[date, date] | None:
    """The dates whose rows a report for ``on`` needs: every budget's period and every chain back to its start."""
    starts: list[date] = []
    ends: list[date] = []
    for budget in budgets:
        history = history_for(budget.id, rollovers)
        start, end = period_for(budget, on, repeats(budget, history))
        first = chain_start(budget, history) if getattr(budget.kind, "value", budget.kind) == "spending" else None
        starts.append(min(start, first) if first is not None else start)
        ends.append(end)
    return (min(starts), max(ends)) if starts else None


def _spent(spend: list[Transaction], cover: _Cover, start: date, end: date) -> tuple[Decimal, int]:
    from .commitments import covers

    mine = [t for t in spend if start <= t.posted_on <= end and covers(cover, t)]
    total = max(-sum((t.amount for t in mine), Decimal(0)), Decimal(0))
    return quantize(total, cover.currency), sum(1 for t in mine if t.amount < 0)


def _carry_out(terms: BudgetTerms, left: Decimal) -> Decimal:
    if left < 0:
        return left if terms.overspend == OverspendMode.CARRY else Decimal(0)
    if terms.rollover == RolloverMode.SURPLUS:
        return left
    if terms.rollover == RolloverMode.CAPPED and terms.cap is not None:
        return min(left, terms.cap)
    return Decimal(0)


def chain(budget: Any, history: list[BudgetTerms], spend: list[Transaction], *, on: date, today: date) -> list[ChainPeriod]:
    """Every period from the chain's start to the one holding ``on``, each with the terms it used. ``spend`` are the rows
    the shared spending rule keeps (``flows.spend_rows``), covering those dates. Empty: nothing ever carried."""
    first = chain_start(budget, history)
    if first is None:
        return []
    repeating = repeats(budget, history)
    last_start = period_for(budget, on, repeating)[0]
    out: list[ChainPeriod] = []
    carried = Decimal(0)
    previous: BudgetTerms | None = None
    start = first
    while start <= last_start:
        end = period_for(budget, start, repeating)[1]
        terms = terms_on(history, start)
        if terms is None:  # cannot happen past the first carrying entry; kept safe
            break
        restarted = previous is not None and previous.currency != terms.currency
        carried_in = Decimal(0) if restarted else carried
        cover = _Cover(terms.currency, list(terms.categories), list(terms.subcategory_ids))
        spent, rows = _spent(spend, cover, start, end)
        amount = quantize(terms.amount, terms.currency)
        available = quantize(amount + carried_in, terms.currency)
        left = available - spent
        closed = end < today
        carried_out = quantize(_carry_out(terms, left), terms.currency) if closed else None
        out.append(ChainPeriod(start=start, end=end, currency=terms.currency, amount=amount, carried_in=quantize(carried_in, terms.currency),
                               available=available, spent=spent, rows=rows, left=quantize(left, terms.currency), carried_out=carried_out,
                               rollover=terms.rollover.value, cap=quantize(terms.cap, terms.currency) if terms.cap is not None else None,
                               overspend=terms.overspend.value, restarted=restarted))
        carried = carried_out if carried_out is not None else Decimal(0)
        previous = terms
        start = end + timedelta(days=1)
    return out


# -- Guardian caps (docs/finance.md#budget-rollover) ----------------------------------------------------------------


@dataclass(frozen=True)
class CapCarry:
    budget_id: str
    carried_in: Decimal  # what the paired budget carried into the month (below zero: a carried overspend)


def cap_budget(category: Any, currency: str, budgets: list[Any], rollovers: list[BudgetTerms] | None) -> Any | None:
    """The one budget that stands for a Guardian cap's month: an active monthly spending budget in the cap's currency
    whose only category is the cap's (no subcategory narrowing it) and that has rollover terms. None when there is no
    such budget or more than one (two budgets cannot both decide what one cap carries)."""
    found = [b for b in budgets
             if b.active and getattr(b.kind, "value", b.kind) == "spending" and getattr(b.period, "value", b.period) == "monthly"
             and b.currency == currency and list(b.categories) == [category] and not b.subcategory_ids
             and any(carries(t) for t in history_for(b.id, rollovers))]
    return found[0] if len(found) == 1 else None


def cap_carry(caps: list[Any], budgets: list[Any], rollovers: list[BudgetTerms] | None, spend: list[Transaction], *, on: date,
              today: date) -> dict[tuple[Any, str], CapCarry]:
    """For each Guardian cap (``category``, ``currency``) with a paired rollover budget (``cap_budget``): what that budget's
    chain carried into the month holding ``on``. The cap's limit for the month is then the cap plus that, the same
    ``available`` the budget shows. ``spend`` are the rows the shared spending rule keeps over ``cap_window``."""
    out: dict[tuple[Any, str], CapCarry] = {}
    for cap in caps:
        budget = cap_budget(cap.category, cap.currency, budgets, rollovers)
        if budget is None:
            continue
        periods = chain(budget, history_for(budget.id, rollovers), spend, on=on, today=today)
        start = period_for(budget, on)[0]
        if periods and periods[-1].start == start and periods[-1].currency == cap.currency:
            out[(cap.category, cap.currency)] = CapCarry(budget.id, periods[-1].carried_in)
    return out


def cap_window(caps: list[Any], budgets: list[Any], rollovers: list[BudgetTerms] | None, on: date) -> tuple[date, date] | None:
    """The dates whose rows ``cap_carry`` needs, None when no cap has a paired budget (nothing to read)."""
    paired = [b for b in (cap_budget(c.category, c.currency, budgets, rollovers) for c in caps) if b is not None]
    return window(paired, rollovers, on) if paired else None


__all__ = [
    "CHAIN_SHOWN",
    "CapCarry",
    "cap_budget",
    "cap_carry",
    "cap_window",
    "MAX_TERMS_PER_BUDGET",
    "ChainPeriod",
    "carries",
    "chain",
    "chain_start",
    "history_for",
    "period_for",
    "repeats",
    "terms_on",
    "window",
]
