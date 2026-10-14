"""Safe-to-spend, Stage 3 (docs/finance.md#safe-to-spend).

What the person can spend for the rest of this month, and per day, after what is already
promised. Per currency, line by line, from their own records only:

1. **Money you have:** the balance of every bank and cash account that is not archived.
   Cards, loans and investments are listed as not counted: what a card owes is covered by
   its minimum payment below, and an investment is not spending money.
2. **Bills:** every active bill's due dates from today to the end of the month.
3. **Minimum debt payments:** each debt's minimum payment, unless its due day has already
   passed this month (then it is listed as not counted: it is taken as paid). A debt with
   no due day is counted.
4. **Goals:** the monthly amount the person set for a goal, or, with a target date, what
   is left divided by the months left (rounded up to the minor unit). A goal without
   either, or already reached, is listed as not counted.
5. **Buffer:** what the person always keeps back, per currency.

``safe_to_spend = money you have - bills - minimum payments - goals - buffer``. It can be
negative: the page then says by how much the month is short. ``per_day`` divides a positive
figure by the days left (today included) and rounds **down** to the minor unit, so it is
never more than the month allows.

Different currencies are never added together. A combined figure in the base currency is
shown only when the person asks for it and every rate needed is recorded and recent; each
conversion shows its rate, date and source (``fx.py``). This is arithmetic on the
person's own records, not advice and not a forecast.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from . import fx
from .models import Account, AccountKind, Bill, Debt, FxRate, Goal, PlanSettings
from .money import fmt, minor_digits, quantize
from .store import FinanceStore
from .summary import due_dates, settings_for, today_in

SPENDABLE = frozenset({AccountKind.BANK, AccountKind.CASH})
NOTE = "An estimate from your own records: not advice and not a forecast."

LineKind = Literal["balance", "bill", "minimum_payment", "goal", "buffer"]
# Why a line is there or not counted; clients show these in the person's language.
NoteCode = Literal[
    "account_balance", "bill_due", "minimum_due", "minimum_no_day", "minimum_passed", "goal_set", "goal_derived",
    "goal_no_plan", "goal_reached", "buffer", "account_card", "account_loan", "account_investment", "account_archived",
]


class SafeLine(BaseModel):
    kind: LineKind
    label: str
    ref_id: str | None = None
    currency: str
    # The line's effect on the figure: positive for money you have, negative for a deduction.
    amount: Decimal
    note: NoteCode
    params: dict[str, str] = Field(default_factory=dict)
    detail: str  # the same in English, for the assistant


class CurrencySafeToSpend(BaseModel):
    currency: str
    available: Decimal
    bills: Decimal
    minimum_payments: Decimal
    goals: Decimal
    buffer: Decimal
    safe_to_spend: Decimal
    per_day: Decimal
    short: bool
    lines: list[SafeLine]


class CombinedTotal(BaseModel):
    currency: str
    safe_to_spend: Decimal
    per_day: Decimal
    conversions: list[fx.Conversion]


class SafeToSpend(BaseModel):
    month: str
    today: date
    month_end: date
    days_left: int
    base_currency: str
    timezone: str
    currencies: list[CurrencySafeToSpend]
    excluded: list[SafeLine]
    combine_currencies: bool
    combined_status: Literal["off", "single_currency", "ready", "missing_rates"]
    combined: CombinedTotal | None
    missing_rates: list[str]
    fx_max_age_days: int
    accounts_counted: int
    note: str = NOTE


def _floor(amount: Decimal, currency: str) -> Decimal:
    return amount.quantize(Decimal(1).scaleb(-minor_digits(currency)), rounding=ROUND_FLOOR)


def _ceil(amount: Decimal, currency: str) -> Decimal:
    return amount.quantize(Decimal(1).scaleb(-minor_digits(currency)), rounding=ROUND_CEILING)


def months_between(today: date, target: date) -> int:
    """Months left to save, this month included (at least one)."""
    return max(1, (target.year - today.year) * 12 + (target.month - today.month) + 1)


def compute(
    *,
    today: date,
    base_currency: str,
    timezone: str,
    accounts: list[Account],
    balances: dict[str, Decimal],
    bills: list[Bill],
    debts: list[Debt],
    goals: list[Goal],
    plan: PlanSettings,
    rates: list[FxRate],
) -> SafeToSpend:
    """The pure calculation: the same inputs always give the same figure."""
    month_end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    days_left = (month_end - today).days + 1
    lines: list[SafeLine] = []
    excluded: list[SafeLine] = []

    def money(value: Decimal, currency: str) -> str:
        return f"{currency} {fmt(value, currency)}"

    counted = 0
    for account in accounts:
        balance = quantize(account.opening_balance + balances.get(account.id, Decimal(0)), account.currency)
        if account.archived:
            excluded.append(SafeLine(kind="balance", label=account.name, ref_id=account.id, currency=account.currency, amount=balance,
                                     note="account_archived", detail="Archived account: not counted."))
        elif account.kind in SPENDABLE:
            counted += 1
            lines.append(SafeLine(kind="balance", label=account.name, ref_id=account.id, currency=account.currency, amount=balance,
                                  note="account_balance", detail=f"Balance of {account.kind.value} account."))
        else:
            reason = {
                AccountKind.CARD: "Card: what it owes is covered by its minimum payment.",
                AccountKind.LOAN: "Loan: what it owes is covered by its minimum payment.",
                AccountKind.INVESTMENT: "Investment: not spending money.",
            }[account.kind]
            excluded.append(SafeLine(kind="balance", label=account.name, ref_id=account.id, currency=account.currency, amount=balance,
                                     note=f"account_{account.kind.value}", detail=reason))  # type: ignore[arg-type]

    for bill in bills:
        if not bill.active:
            continue
        for due in due_dates(bill, today, month_end):
            lines.append(SafeLine(kind="bill", label=bill.name, ref_id=bill.id, currency=bill.currency, amount=-bill.amount,
                                  note="bill_due", params={"date": due.isoformat()}, detail=f"Bill due {due.isoformat()}."))

    for debt in debts:
        if debt.minimum_payment <= 0 or debt.balance <= 0:
            continue
        payment = min(debt.minimum_payment, debt.balance)
        if debt.due_day is None:
            lines.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                  note="minimum_no_day", detail="Minimum payment, no due day set: counted."))
            continue
        day = min(debt.due_day, month_end.day)
        if day < today.day:
            excluded.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                     note="minimum_passed", params={"day": str(day)},
                                     detail=f"Due on day {day}, already passed this month: taken as paid."))
        else:
            lines.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                  note="minimum_due", params={"day": str(day)}, detail=f"Minimum payment due on day {day}."))

    for goal in goals:
        left = goal.target_amount - goal.saved_amount
        if left <= 0:
            excluded.append(SafeLine(kind="goal", label=goal.name, ref_id=goal.id, currency=goal.currency, amount=Decimal(0),
                                     note="goal_reached", detail="Goal reached: nothing set aside."))
            continue
        if goal.monthly_contribution is not None and goal.monthly_contribution > 0:
            amount = min(goal.monthly_contribution, left)
            lines.append(SafeLine(kind="goal", label=goal.name, ref_id=goal.id, currency=goal.currency, amount=-amount,
                                  note="goal_set", detail="The monthly amount you set."))
        elif goal.target_on is not None:
            months = months_between(today, goal.target_on)
            amount = min(_ceil(left / months, goal.currency), left)
            lines.append(SafeLine(kind="goal", label=goal.name, ref_id=goal.id, currency=goal.currency, amount=-amount,
                                  note="goal_derived", params={"left": fmt(left, goal.currency), "months": str(months)},
                                  detail=f"{money(left, goal.currency)} left over {months} month(s)."))
        else:
            excluded.append(SafeLine(kind="goal", label=goal.name, ref_id=goal.id, currency=goal.currency, amount=Decimal(0),
                                     note="goal_no_plan", detail="No monthly amount or target date: nothing set aside."))

    for buffer in plan.buffers:
        if buffer.amount > 0:
            lines.append(SafeLine(kind="buffer", label=buffer.currency, currency=buffer.currency, amount=-buffer.amount,
                                  note="buffer", detail="The buffer you keep back."))

    order = {"balance": 0, "bill": 1, "minimum_payment": 2, "goal": 3, "buffer": 4}
    currencies: list[CurrencySafeToSpend] = []
    for currency in sorted({line.currency for line in lines}, key=lambda c: (c != base_currency, c)):
        mine = sorted((line for line in lines if line.currency == currency), key=lambda line: (order[line.kind], line.params.get("date", ""), line.label.lower()))

        def total(kind: str, rows: list[SafeLine] = mine) -> Decimal:
            return sum((line.amount for line in rows if line.kind == kind), Decimal(0))

        available, bills_total, minimums = total("balance"), -total("bill"), -total("minimum_payment")
        goals_total, buffer_total = -total("goal"), -total("buffer")
        safe = available - bills_total - minimums - goals_total - buffer_total
        per_day = _floor(safe / days_left, currency) if safe > 0 else Decimal(0)
        currencies.append(CurrencySafeToSpend(
            currency=currency, available=available, bills=bills_total, minimum_payments=minimums, goals=goals_total,
            buffer=buffer_total, safe_to_spend=safe, per_day=quantize(per_day, currency), short=safe < 0, lines=mine,
        ))

    combined: CombinedTotal | None = None
    missing: list[str] = []
    if not plan.combine_currencies:
        status: Literal["off", "single_currency", "ready", "missing_rates"] = "off"
    elif len(currencies) <= 1 and all(c.currency == base_currency for c in currencies):
        status = "single_currency"
    else:
        conversions: list[fx.Conversion] = []
        for row in currencies:
            found = fx.convert(row.safe_to_spend, row.currency, base_currency, rates, today, plan.fx_max_age_days)
            if found is None:
                missing.append(f"{row.currency}/{base_currency}")
            else:
                conversions.append(found)
        if missing:
            status = "missing_rates"
        else:
            status = "ready"
            amount = sum((c.converted for c in conversions), Decimal(0))
            combined = CombinedTotal(
                currency=base_currency, safe_to_spend=amount,
                per_day=_floor(amount / days_left, base_currency) if amount > 0 else quantize(Decimal(0), base_currency),
                conversions=conversions,
            )
    return SafeToSpend(
        month=f"{today.year:04d}-{today.month:02d}", today=today, month_end=month_end, days_left=days_left,
        base_currency=base_currency, timezone=timezone, currencies=currencies, excluded=excluded,
        combine_currencies=plan.combine_currencies, combined_status=status, combined=combined, missing_rates=missing,
        fx_max_age_days=plan.fx_max_age_days, accounts_counted=counted,
    )


def build_safe_to_spend(store: FinanceStore, context: TenantContext, *, now: datetime | None = None) -> SafeToSpend:
    settings = settings_for(store, context)
    today = today_in(settings, now)
    plan = store.get_plan(context) or PlanSettings()
    return compute(
        today=today, base_currency=settings.base_currency, timezone=settings.timezone,
        accounts=store.list_accounts(context), balances=store.account_sums(context),
        bills=[b for b in store.list_items(context, "bill") if isinstance(b, Bill)],
        debts=[d for d in store.list_items(context, "debt") if isinstance(d, Debt)],
        goals=[g for g in store.list_items(context, "goal") if isinstance(g, Goal)],
        plan=plan, rates=store.list_fx_rates(context, limit=2000) if plan.combine_currencies else [],
    )
