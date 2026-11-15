"""Safe-to-spend, Stage 3 (docs/finance.md#safe-to-spend).

What the person can spend for the rest of this month, and per day, after what is already
promised. Per currency, line by line, from their own records only:

1. **Money you have:** the balance of every bank and cash account that is not archived.
   Cards, loans and investments are listed as not counted: what a card owes is covered by
   its minimum payment below, and an investment is not spending money.
2. **Bills:** every active bill's due dates from today to the end of the month that have
   no matched payment (a bill paid early is not taken off again), and this month's passed
   due dates that still have none (overdue: the money has not gone out yet).
3. **Minimum debt payments:** each debt's minimum payment. Once its due day has passed this
   month it is paid only when a payment is matched to the debt this month (then it is listed
   as not counted); without one it stays counted and is called overdue. A debt with no due
   day is counted. A minimum that is also saved as a bill is counted once, as the bill, and
   a debt's balance follows its matched payments (``obligations.py``). Money borrowed from a
   person is a debt here too (``obligations.borrowed_debts``): the amount left is counted
   when its due date is in this month, and called overdue once the date has passed.
4. **Goals:** the monthly amount the person set for a goal, or, with a target date, what
   is left divided by the months left (rounded up to the minor unit). A goal without
   either, or already reached, is listed as not counted.
5. **Buffer:** what the person always keeps back, per currency.
6. **Planned commitments** (docs/finance.md#safe-to-spend): this month's unpaid installments from the provider
   statements (Tamara, Tabby; an order saved as a bill is the bill), regular remittances not sent yet this month (one
   saved as a bill is the bill), and what is left of each budget the person keeps back here
   (``Budget.reserve_in_safe_to_spend``), less the bills in its categories already taken off above, so nothing counts twice.

With a salary profile (``salary.py``) the salary's currency also shows the next usual pay day and the net
expected then (the one income figure), so the page can say how long the money has to last.

``safe_to_spend = money you have - bills - minimum payments - goals - buffer - planned commitments``. It can be
negative: the page then says by how much the month is short. ``per_day`` divides a positive
figure by the days left (today included) and rounds **down** to the minor unit, so it is
never more than the month allows.

Different currencies are never added together. A combined figure in the base currency is
shown only when the person asks for it and every rate needed is recorded and recent; each
conversion shows its rate, date and source (``fx.py``). This is arithmetic on the
person's own records, not advice and not a forecast.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..schemas import TenantContext
from . import commitment_math as M
from . import fx
from . import obligations as O
from .models import Account, AccountKind, Bill, Debt, FxRate, Goal, PlanSettings
from .money import fmt, minor_digits, quantize
from .store import FinanceStore
from .summary import settings_for, today_in

SPENDABLE = M.SPENDABLE
NOTE = "An estimate from your own records: not advice and not a forecast."

LineKind = Literal["balance", "bill", "minimum_payment", "goal", "buffer", "installment", "remittance", "budget"]
# Why a line is there or not counted; clients show these in the person's language.
NoteCode = Literal[
    "account_balance", "bill_due", "bill_overdue", "minimum_due", "minimum_no_day", "minimum_passed", "minimum_overdue", "minimum_in_bill", "goal_set",
    "goal_derived",
    "goal_no_plan", "goal_reached", "goal_paused", "buffer", "account_card", "account_loan", "account_investment", "account_archived",
    "installment_due", "installment_overdue", "installment_in_bill", "remittance_due", "remittance_in_bill", "remittance_sent", "budget_reserve",
    "budget_spent",
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
    # Installments, regular remittances and budgets kept back (docs/finance.md#safe-to-spend). Zero without any.
    planned: Decimal = Decimal(0)
    # The figure in one English sentence, for the assistant (clients word the lines themselves).
    explanation: str = ""
    # With a salary profile in this currency: the next usual pay day and the net expected then (``salary.py``).
    next_pay_on: date | None = None
    expected_pay: Decimal | None = None
    days_to_payday: int | None = None


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


months_between = M.months_between


def paid_this_month(debt: Debt, payments: list[O.DebtPayment], today: date) -> bool:
    """A payment matched to the debt (in its currency) was made this month: its minimum is paid."""
    start = M.month_start(today)
    return any(p.debt_id == debt.id and p.currency == debt.currency and start <= p.posted_on <= today for p in payments)


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
    bill_links: list | None = None,
    debt_payments: list[O.DebtPayment] | None = None,
    salary_profile: object | None = None,
    payslips: list | None = None,
    installments: list | None = None,
    remittances: list | None = None,
    budgets: list | None = None,
    month_rows: list | None = None,
    lending: frozenset[str] | None = None,
) -> SafeToSpend:
    """The pure calculation: the same inputs always give the same figure.

    ``bill_links`` are the person's ``bill`` links: a due date with an active matched payment is paid.
    ``debt_payments`` are the payments matched to debts: a passed due day is paid only by one this month."""
    month_end = M.month_end(today)
    days_left = (month_end - today).days + 1
    lines: list[SafeLine] = []
    excluded: list[SafeLine] = []

    def money(value: Decimal, currency: str) -> str:
        return f"{currency} {fmt(value, currency)}"

    counted = 0
    for account in accounts:
        balance = quantize(M.account_balance(account, balances), account.currency)
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

    for found in O.unpaid(bills, bill_links or [], today, month_end):
        bill, due = found.bill, found.due_on
        if found.overdue:
            lines.append(SafeLine(kind="bill", label=bill.name, ref_id=bill.id, currency=bill.currency, amount=-bill.amount,
                                  note="bill_overdue", params={"date": due.isoformat()},
                                  detail=f"Bill was due {due.isoformat()} and no payment is matched to it yet."))
        else:
            lines.append(SafeLine(kind="bill", label=bill.name, ref_id=bill.id, currency=bill.currency, amount=-bill.amount,
                                  note="bill_due", params={"date": due.isoformat()}, detail=f"Bill due {due.isoformat()}."))

    for debt in debts:
        payment = M.debt_minimum(debt)
        if payment <= 0:
            continue
        covering = M.bill_for_debt(debt, bills)
        if covering is not None:
            excluded.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                     note="minimum_in_bill", params={"bill": covering.name},
                                     detail=f"Saved as the bill {covering.name}: counted once, as the bill."))
            continue
        if isinstance(debt, O.PersonDebt) and debt.overdue and debt.due_on is not None:
            # Borrowed from a person and not repaid by its date: the amount left is still to pay.
            lines.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                  note="minimum_overdue", params={"date": debt.due_on.isoformat()},
                                  detail=f"Was due {debt.due_on.isoformat()} and is not repaid yet: counted."))
            continue
        if debt.due_day is None:
            lines.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                  note="minimum_no_day", detail="Minimum payment, no due day set: counted."))
            continue
        day = min(debt.due_day, month_end.day)
        if day < today.day:
            due_on = today.replace(day=day)
            if paid_this_month(debt, debt_payments or [], today):
                excluded.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                         note="minimum_passed", params={"day": str(day)},
                                         detail=f"Due on day {day}: a payment is matched to it this month, so it is paid."))
            else:
                lines.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                      note="minimum_overdue", params={"date": due_on.isoformat()},
                                      detail=f"Was due {due_on.isoformat()} and no payment is matched to it yet: counted."))
        else:
            lines.append(SafeLine(kind="minimum_payment", label=debt.name, ref_id=debt.id, currency=debt.currency, amount=-payment,
                                  note="minimum_due", params={"day": str(day)}, detail=f"Minimum payment due on day {day}."))

    for goal in goals:
        left = goal.target_amount - goal.saved_amount
        if goal.paused and left > 0:
            excluded.append(SafeLine(kind="goal", label=goal.name, ref_id=goal.id, currency=goal.currency, amount=Decimal(0),
                                     note="goal_paused", detail="Goal paused: nothing set aside until you resume it."))
            continue
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

    planned, left_out = planned_lines(today, month_end, bills, installments or [], remittances or [], budgets or [], month_rows or [],
                                      bill_links or [], lending)
    lines += planned
    excluded += left_out

    order = {"balance": 0, "bill": 1, "minimum_payment": 2, "goal": 3, "buffer": 4, "installment": 5, "remittance": 6, "budget": 7}
    currencies: list[CurrencySafeToSpend] = []
    for currency in sorted({line.currency for line in lines}, key=lambda c: (c != base_currency, c)):
        mine = sorted((line for line in lines if line.currency == currency), key=lambda line: (order[line.kind], line.params.get("date", ""), line.label.lower()))

        def total(kind: str, rows: list[SafeLine] = mine) -> Decimal:
            return sum((line.amount for line in rows if line.kind == kind), Decimal(0))

        available, bills_total, minimums = total("balance"), -total("bill"), -total("minimum_payment")
        goals_total, buffer_total = -total("goal"), -total("buffer")
        planned_total = -(total("installment") + total("remittance") + total("budget"))
        safe = available - bills_total - minimums - goals_total - buffer_total - planned_total
        per_day = _floor(safe / days_left, currency) if safe > 0 else Decimal(0)
        explanation = (f"{money(available, currency)} in bank and cash, less bills {money(bills_total, currency)}, minimum payments "
                       f"{money(minimums, currency)}, goals {money(goals_total, currency)}, buffer {money(buffer_total, currency)} and planned "
                       f"commitments {money(planned_total, currency)}: {money(safe, currency)} to spend until {month_end.isoformat()}. "
                       "Card credit is not cash and is not counted.")
        currencies.append(CurrencySafeToSpend(
            currency=currency, available=available, bills=bills_total, minimum_payments=minimums, goals=goals_total,
            buffer=buffer_total, safe_to_spend=safe, per_day=quantize(per_day, currency), short=safe < 0, lines=mine,
            planned=planned_total, explanation=explanation,
        ))

    from . import salary as SAL
    from .personal import SalaryProfile

    if isinstance(salary_profile, SalaryProfile):
        for row in currencies:
            if row.currency == salary_profile.currency:
                pay_on = SAL.next_payday(salary_profile, today)
                pay = SAL.expected_for(salary_profile, SAL.month_key(pay_on), payslips or []).net
                row.next_pay_on, row.expected_pay, row.days_to_payday = pay_on, pay, (pay_on - today).days

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


def planned_lines(today: date, month_end: date, bills: list[Bill], installments: list, remittances: list, budgets: list, month_rows: list,
                  bill_links: list, lending: frozenset[str] | None) -> tuple[list[SafeLine], list[SafeLine]]:
    """The planned commitments: unpaid installments and regular remittances to the month's end, and budgets kept back."""
    lines: list[SafeLine] = []
    excluded: list[SafeLine] = []
    start = M.month_start(today)
    names = {(M.name_key(b.name), b.currency) for b in bills if b.active}
    in_bill: set[str] = set()
    for entry in installments:
        if entry.source_status in ("paid", "refunded"):
            continue
        due = max(Decimal(0), entry.amount - entry.refunded_amount)
        if due <= 0 or entry.due_on < start or entry.due_on > month_end:
            continue
        label = f"{entry.provider}: {entry.merchant}"
        key = f"{entry.provider}:{entry.order_id}"
        if (M.name_key(entry.merchant), entry.currency) in names or (M.name_key(entry.provider), entry.currency) in names:
            if key not in in_bill:
                in_bill.add(key)
                excluded.append(SafeLine(kind="installment", label=label, ref_id=key, currency=entry.currency, amount=Decimal(0),
                                         note="installment_in_bill", detail="Saved as a bill: counted once, as the bill."))
            continue
        overdue = entry.due_on < today
        lines.append(SafeLine(kind="installment", label=label, ref_id=key, currency=entry.currency,
                              amount=-quantize(due, entry.currency), note="installment_overdue" if overdue else "installment_due",
                              params={"date": entry.due_on.isoformat(), "number": str(entry.installment), "of": str(entry.total_installments)},
                              detail=f"Installment {entry.installment} of {entry.total_installments} due {entry.due_on.isoformat()}"
                                     + (" and not paid yet." if overdue else ".")))
    newest: dict[tuple[str, str], Any] = {}
    sent: set[tuple[str, str]] = set()
    for r in sorted(remittances, key=lambda r: (r.sent_on, r.created_at)):
        key2 = (M.name_key(r.recipient), r.currency)
        if M.month_start(r.sent_on) == start:
            sent.add(key2)
        if r.regular:
            newest[key2] = r
    for key2, r in sorted(newest.items()):
        amount = quantize(r.amount + r.fee, r.currency)
        if key2 in names:
            excluded.append(SafeLine(kind="remittance", label=r.recipient, ref_id=r.id, currency=r.currency, amount=-amount,
                                     note="remittance_in_bill", detail="Saved as a bill: counted once, as the bill."))
        elif key2 in sent:
            excluded.append(SafeLine(kind="remittance", label=r.recipient, ref_id=r.id, currency=r.currency, amount=-amount,
                                     note="remittance_sent", detail="Already sent this month: it is in the balance."))
        else:
            lines.append(SafeLine(kind="remittance", label=r.recipient, ref_id=r.id, currency=r.currency, amount=-amount,
                                  note="remittance_due", params={"day": str(r.sent_on.day)}, detail="Sent every month and not sent yet this month."))
    kept = [b for b in budgets if getattr(b, "reserve_in_safe_to_spend", False) and b.active and getattr(b.kind, "value", b.kind) == "spending"]
    if kept:
        from .commitments import plan_budget_report

        report = plan_budget_report(kept, month_rows, on=today, today=today, bills=bills, bill_links=bill_links, lending=lending)
        for line in report.budgets:
            # What is left of the budget, less its bills still to pay: those are taken off above already.
            reserve = max((line.remaining or Decimal(0)) - line.coming, Decimal(0))
            if reserve > 0:
                lines.append(SafeLine(kind="budget", label=line.name, ref_id=line.id, currency=line.currency, amount=-quantize(reserve, line.currency),
                                      note="budget_reserve", params={"spent": fmt(line.spent or Decimal(0), line.currency),
                                                                      "amount": fmt(line.amount, line.currency)},
                                      detail="What is left of this budget, kept back (its bills are counted above)."))
            else:
                excluded.append(SafeLine(kind="budget", label=line.name, ref_id=line.id, currency=line.currency, amount=Decimal(0),
                                         note="budget_spent", detail="Nothing left of this budget to keep back."))
    return lines, excluded


def build_safe_to_spend(store: FinanceStore, context: TenantContext, *, now: datetime | None = None, links: O.Links | None = None) -> SafeToSpend:
    """``links`` come from ``obligations.load``, read in its own short unit of work before this one."""
    from .commitments import budget_period

    settings = settings_for(store, context)
    today = today_in(settings, now)
    plan = store.get_plan(context) or PlanSettings()
    accounts = store.list_accounts(context)
    balances = store.account_sums(context)
    planning = store.get_planning(context)
    kept = [b for b in planning.budgets if b.reserve_in_safe_to_spend and b.active]
    periods = [budget_period(b, today) for b in kept]
    month_rows = store.transactions_between(context, min(p[0] for p in periods), max(p[1] for p in periods)) if periods else []
    # Regular remittances come in with ``links`` (``obligations.load`` reads the personal store in its own unit).
    remittances = list(getattr(links, "remittances", []) or [])
    return compute(
        today=today, base_currency=settings.base_currency, timezone=settings.timezone,
        accounts=accounts, balances=balances,
        bills=[b for b in store.list_items(context, "bill") if isinstance(b, Bill)],
        debts=O.current_debts([d for d in store.list_items(context, "debt") if isinstance(d, Debt)], accounts, balances, links, today),
        goals=O.current_goals([g for g in store.list_items(context, "goal") if isinstance(g, Goal)], accounts, balances),
        plan=plan, rates=store.list_fx_rates(context, limit=2000) if plan.combine_currencies else [],
        bill_links=links.bill_links if links is not None else None,
        debt_payments=links.debt_payments if links is not None else None,
        salary_profile=links.salary_profile if links is not None else None, payslips=links.payslips if links is not None else None,
        installments=list(planning.installment_entries), remittances=remittances, budgets=kept, month_rows=month_rows,
        lending=links.lending_ids if links is not None else None,
    )
