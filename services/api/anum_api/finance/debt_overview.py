"""One personal debt overview: everything the person owes and is owed, read from the records that already hold it
(docs/finance.md#debt-overview).

Nothing here is a second copy of a debt. Each obligation is read from its own record and its own figures:

* **Bank and finance loans** (a Debt with loan terms, ``bank_loans.py``): the schedule, the payments matched to it (bank
  debits linked in the ``debt`` dimension and the salary months of accepted batches), partial and extra payments,
  early settlement (paid off with its date), missed and ambiguous months.
* **Other debts** (a Debt without terms): kept on a card or loan account it owes what that account's ledger says; else
  the balance the person entered less the principal of the payments matched since (``obligations.debt_balance``).
* **Credit cards** (``credit_cards.py``): what each card account owes on the ledger, with its statement.
* **Buy now, pay later** (the provider's installment statement, ``Planning.installment_entries``): each order's
  installments still due. The provider's own "paid" is only **reported** by the provider; an installment is **verified**
  paid only when a bill saved from that statement row has an active matched payment for its due date. Nothing here
  re-reads Tamara.
* **Employer loans and salary advances** (the salary profile's rules, ``salary.py``): a loan deduction that no debt
  already carries, and an advance with what is left to repay. The payslip months that show the deduction are its history.
* **Loans with people** (``personal.py``), in both directions: what is left, overdue, promises to pay and the balance
  both sides last agreed.

For each obligation the view names the **source of every figure** (``sources``), the evidence of each payment in its
history (``verified`` from an active link, ``provisional`` for a link waiting for review, ``reported`` by a provider,
``payslip``), what is missing to work out more (``missing``), and compares the lender's own balance (``verified_balance``,
entered by the person from a statement) with the balance ANUM works out (``estimated_balance``): a difference above
half a percent (at least one minor unit) is flagged. A disputed amount is shown, never taken off.

The **calendar** lists every due date in the next 45 days and every overdue one; the **plan** orders what is owed per
currency (overdue first, then the highest known rate, then an unknown rate, then interest free; a smaller balance first
on a tie) and each **payoff forecast** comes from the obligation's own terms, with its assumptions written out. Nothing
is added across currencies and no rate is invented.

``build`` is pure; ``gather`` reads in short owner-scoped units of work, one after another.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from . import bank_loans as BL
from . import commitment_math as M
from . import obligations as O
from . import personal as P
from .credit_cards import CardView
from .models import Account, Bill, Debt, DebtKind, InstallmentStatementEntry
from .money import minor_digits, quantize

CALENDAR_DAYS = 45
HISTORY_ROWS = 24
DISCREPANCY_SHARE = Decimal("0.005")

DebtType = Literal["personal_loan", "vehicle_finance", "mortgage", "credit_card", "employer_loan", "salary_advance", "bnpl", "informal",
                   "borrowed_person", "lent_person", "other"]
DEBT_TYPES: tuple[str, ...] = ("personal_loan", "vehicle_finance", "mortgage", "credit_card", "employer_loan", "salary_advance", "bnpl", "informal", "other")
Direction = Literal["owe", "owed_to_me"]
Evidence = Literal["verified", "provisional", "reported", "payslip", "entered"]
Status = Literal["open", "due_soon", "overdue", "paid_off", "closed", "disputed"]
InterestKind = Literal["interest", "interest_free", "unknown"]


class Source(BaseModel):
    figure: str  # "remaining", "installment", "rate", "next_due", "original"
    source: str  # "lender_terms", "matched_payments", "ledger", "entered", "provider_statement", "salary_profile", "payslip", "schedule"


class HistoryItem(BaseModel):
    on: date | None
    amount: Decimal
    currency: str
    kind: Literal["payment", "handed", "salary_month", "installment", "promise"]
    evidence: Evidence
    ref_type: str  # "transaction", "link", "payslip", "salary_rule", "installment"
    ref_id: str | None
    note: str | None = None


class PromiseView(BaseModel):
    id: str
    made_on: date
    due_by: date
    amount: Decimal
    note: str | None
    status: P.PromiseStatus
    paid: Decimal


class Obligation(BaseModel):
    id: str  # "<ref_type>:<ref_id>"
    ref_type: Literal["debt", "card", "person_loan", "installment_order", "salary_rule"]
    ref_id: str
    type: DebtType
    direction: Direction
    name: str
    counterparty: str | None
    currency: str
    original_amount: Decimal | None
    start_on: date | None
    schedule: Literal["monthly", "single", "revolving", "salary", "none"]
    interest: InterestKind
    rate: Decimal | None
    expected_installment: Decimal | None
    paid: Decimal
    remaining: Decimal  # what ANUM works out is owed now (the estimated balance)
    interest_paid: Decimal | None = None
    fees: Decimal | None = None
    next_due_on: date | None
    next_due_amount: Decimal
    overdue_amount: Decimal
    status: Status
    verified_balance: Decimal | None = None
    verified_on: date | None = None
    difference: Decimal | None = None  # the lender's balance less ANUM's estimate
    discrepancy: bool = False
    disputed_amount: Decimal | None = None
    unverified_amount: Decimal = Decimal(0)  # payments linked but waiting for review, or months that need a pick
    payoff_on: date | None = None
    payoff_basis: str | None = None  # "schedule", "minimum_at_rate", "installments", "advance_repayments", "salary_rule_end", "due_date"
    assumptions: list[str] = []
    history: list[HistoryItem] = []
    promises: list[PromiseView] = []
    sources: list[Source] = []
    missing: list[str] = []
    contact_id: str | None = None


class CalendarItem(BaseModel):
    on: date
    obligation_id: str
    name: str
    amount: Decimal
    currency: str
    direction: Direction
    status: Literal["paid", "review", "reported_paid", "overdue", "due"]


class PlanRow(BaseModel):
    rank: int
    obligation_id: str
    name: str
    currency: str
    remaining: Decimal
    rate: Decimal | None
    reason: Literal["overdue", "highest_rate", "rate_unknown", "interest_free"]


class CurrencyTotal(BaseModel):
    currency: str
    owe: Decimal
    owed_to_me: Decimal
    overdue: Decimal
    due_30_days: Decimal


class Overview(BaseModel):
    today: date
    items: list[Obligation]
    totals: list[CurrencyTotal]
    calendar: list[CalendarItem]
    plan: list[PlanRow]
    discrepancies: int
    note: str = ("Each figure names its source. A provider's own 'paid' is reported, not verified, until a bank payment is matched. "
                 "Nothing is added across currencies and no rate is guessed.")


@dataclass
class Inputs:
    today: date
    debts: list[Debt]
    accounts: list[Account]
    balances: dict[str, Decimal]
    links: O.Links
    loan_states: list[Any]  # bank_loan_service.LoanState
    cards: list[CardView]
    installments: list[InstallmentStatementEntry]
    bills: list[Bill]
    bill_links: list[Any] = field(default_factory=list)
    review_links: list[Any] = field(default_factory=list)  # debt and person_loan links waiting for review
    loan_dates: dict[str, date] = field(default_factory=dict)  # canonical id: the day it posted (person loan links)


def _unit(currency: str) -> Decimal:
    return Decimal(1).scaleb(-minor_digits(currency))


def _compare(verified: Decimal | None, estimated: Decimal, currency: str) -> tuple[Decimal | None, bool]:
    if verified is None:
        return None, False
    difference = quantize(verified - estimated, currency)
    tolerance = max(_unit(currency), abs(verified) * DISCREPANCY_SHARE)
    return difference, abs(difference) > tolerance


def debt_type(debt: Debt) -> DebtType:
    if debt.debt_type in DEBT_TYPES:
        return debt.debt_type  # type: ignore[return-value]
    if debt.kind == DebtKind.CARD:
        return "credit_card"
    if debt.kind == DebtKind.MORTGAGE:
        return "mortgage"
    if debt.kind == DebtKind.LOAN:
        return "personal_loan"
    return "other"


def _status(remaining: Decimal, overdue: Decimal, next_due: date | None, today: date, disputed: Decimal | None) -> Status:
    if remaining <= 0:
        return "paid_off"
    if disputed:
        return "disputed"
    if overdue > 0:
        return "overdue"
    if next_due is not None and (next_due - today).days <= 7:
        return "due_soon"
    return "open"


def _review_amount(links: list[Any], target_id: str, currency: str) -> Decimal:
    from .links import settled

    return sum((settled(l, currency) for l in links if l.target_id == target_id), Decimal(0))


# -- bank and finance loans, and other debts ----------------------------------------------------------------


def _loan_item(state: Any, inputs: Inputs) -> Obligation:
    debt: Debt = state.debt
    c = debt.currency
    view: BL.Figures = state.view
    schedule: BL.Schedule = state.schedule
    today = inputs.today
    missed = [r for r in view.rows if r.status == "missed"]
    overdue = sum((r.payment - r.paid for r in missed), Decimal(0))
    ambiguous = [f for f in state.findings if f.state == "ambiguous"]
    unverified = _review_amount(inputs.review_links, debt.id, c) + sum((f.expected for f in ambiguous), Decimal(0))
    history: list[HistoryItem] = []
    seen: set[tuple[str, str]] = set()
    for row in view.rows:
        for p in row.payments:
            key = (p.ref, p.kind)
            if key in seen:
                continue
            seen.add(key)
            history.append(HistoryItem(on=p.on, amount=p.amount, currency=c, kind="salary_month" if p.kind == "salary" else "payment",
                                       evidence="payslip" if p.kind == "salary" else "verified", ref_type="payslip" if p.kind == "salary" else "transaction",
                                       ref_id=p.ref or None))
    history.sort(key=lambda h: (h.on or date.min), reverse=True)
    rate = debt.loan_rate
    assumptions: list[str] = []
    if schedule.basis == "plain":
        assumptions.append("No rate entered: the schedule is the lender's instalments, and their split into principal and profit is unknown.")
    elif schedule.basis == "flat":
        assumptions.append("Flat rate: the profit is the amount times the rate times the years, spread evenly over the instalments.")
    else:
        assumptions.append("Reducing rate: each month a twelfth of the yearly rate is charged on what is left.")
    if view.months_ahead < 0:
        assumptions.append(f"{-view.months_ahead} instalment(s) are behind: the last date holds only if they are paid.")
    remaining = view.balance
    difference, flagged = _compare(debt.verified_balance, remaining, c)
    sources = [Source(figure="remaining", source="schedule_and_matched_payments"), Source(figure="installment", source="lender_terms" if
                                                                                       schedule.installment_from == "lender" else "schedule"),
               Source(figure="rate", source="lender_terms" if rate else "not_entered")]
    missing = [] if rate else ["rate"]
    if debt.loan_repayment == "bank_debit" and not debt.loan_account_id:
        missing.append("repayment_account")
    return Obligation(
        id=f"debt:{debt.id}", ref_type="debt", ref_id=debt.id, type=debt_type(debt), direction="owe", name=debt.name, counterparty=debt.lender,
        currency=c, original_amount=debt.loan_amount, start_on=debt.loan_start_on, schedule="salary" if debt.loan_repayment == "salary_deduction" else "monthly",
        interest="interest" if rate else "unknown", rate=rate, expected_installment=schedule.installment, paid=view.paid, remaining=remaining,
        interest_paid=view.interest_paid, fees=debt.fees, next_due_on=view.next_due_on, next_due_amount=view.next_amount, overdue_amount=quantize(overdue, c),
        status="closed" if view.status == "closed" else _status(remaining, overdue, view.next_due_on, today, debt.disputed_amount),
        verified_balance=debt.verified_balance, verified_on=debt.verified_on, difference=difference, discrepancy=flagged, disputed_amount=debt.disputed_amount,
        unverified_amount=quantize(unverified, c), payoff_on=view.payoff_on, payoff_basis="schedule", assumptions=assumptions, history=history[:HISTORY_ROWS],
        sources=sources, missing=missing,
    )


def _months_to_clear(owed: Decimal, apr: Decimal, payment: Decimal, currency: str) -> int | None:
    """Months of ``payment`` that clear ``owed`` at ``apr`` (interest charged on what is left each month); None if never."""
    from .advisor import interest_at_minimum

    _first, months, _total, never = interest_at_minimum(owed, apr, payment, currency)
    return None if never else months


def _add_months(day: date, months: int) -> date:
    year, month = divmod(day.month - 1 + months, 12)
    first = date(day.year + year, month + 1, 1)
    return first.replace(day=min(day.day, M.month_end(first).day))


def _plain_debt(debt: Debt, inputs: Inputs) -> Obligation:
    c = debt.currency
    today = inputs.today
    by_id = {a.id: a for a in inputs.accounts}
    remaining = O.debt_balance(debt, by_id, inputs.balances, inputs.links.debt_payments)
    on_account = O.has_account(debt, by_id)
    payments = [p for p in inputs.links.debt_payments if p.debt_id == debt.id and p.currency == c]
    history = [HistoryItem(on=p.posted_on, amount=p.amount, currency=c, kind="payment", evidence="verified", ref_type="transaction", ref_id=p.canonical_id or None)
               for p in sorted(payments, key=lambda p: p.posted_on, reverse=True)]
    apr = debt.apr_percent if debt.apr_percent > 0 else None
    minimum = debt.minimum_payment if debt.minimum_payment > 0 else None
    next_due = M.next_due_day(debt.due_day, today) if debt.due_day and remaining > 0 else None
    payoff = None
    basis = None
    assumptions: list[str] = []
    missing: list[str] = []
    if apr is None:
        missing.append("rate")
    if minimum is None:
        missing.append("minimum")
    if minimum is not None and remaining > 0:
        months = _months_to_clear(remaining, apr or Decimal(0), minimum, c) if apr else math.ceil(remaining / minimum)
        if months is not None:
            payoff = _add_months(next_due or today, months - 1)
            basis = "minimum_at_rate" if apr else "minimum_no_interest"
            assumptions.append(f"Paying {c} {minimum} a month" + (f" at {apr} percent a year." if apr else ", with no interest because no rate is entered."))
        else:
            assumptions.append("The payment does not cover the interest at the rate entered, so this debt never clears at it.")
    difference, flagged = _compare(debt.verified_balance, remaining, c)
    return Obligation(
        id=f"debt:{debt.id}", ref_type="debt", ref_id=debt.id, type=debt_type(debt), direction="owe", name=debt.name, counterparty=debt.lender,
        currency=c, original_amount=debt.original_balance, start_on=None, schedule="monthly" if minimum else "none",
        interest="interest" if apr else "unknown", rate=apr, expected_installment=minimum, paid=quantize(sum((p.amount for p in payments), Decimal(0)), c),
        remaining=quantize(remaining, c), fees=debt.fees, next_due_on=next_due, next_due_amount=quantize(min(minimum or Decimal(0), remaining), c),
        overdue_amount=Decimal(0), status=_status(remaining, Decimal(0), next_due, today, debt.disputed_amount),
        verified_balance=debt.verified_balance, verified_on=debt.verified_on, difference=difference, discrepancy=flagged, disputed_amount=debt.disputed_amount,
        unverified_amount=quantize(_review_amount(inputs.review_links, debt.id, c), c), payoff_on=payoff, payoff_basis=basis, assumptions=assumptions,
        history=history[:HISTORY_ROWS], sources=[Source(figure="remaining", source="ledger" if on_account else "entered_less_matched_payments"),
                                                 Source(figure="rate", source="entered" if apr else "not_entered")], missing=missing,
    )


def _card_item(card: CardView, debts: dict[str, Debt], today: date) -> Obligation:
    c = card.currency
    debt = debts.get(card.debt_id or "")
    minimum = next((o for o in card.pay_options if o.code == "minimum"), None)
    overdue = Decimal(0)
    if card.payment_state == "not_paid" or card.payment_state == "part_paid":
        if card.due_on is not None and today > card.due_on and card.statement_minimum is not None:
            overdue = max(card.statement_minimum - card.paid_since_statement, Decimal(0))
    payoff = None
    basis = None
    assumptions: list[str] = []
    if minimum is not None and minimum.months_to_clear and card.outstanding > 0:
        payoff = _add_months(card.due_on or today, minimum.months_to_clear - 1)
        basis = "minimum_at_rate"
        assumptions.append(f"Paying only the minimum each month at the {card.apr_percent} percent yearly rate you entered, with no new spending.")
    elif card.apr_percent is None:
        assumptions.append("Enter the card's yearly rate from its terms to see what paying only the minimum costs.")
    history = [HistoryItem(on=p.posted_on, amount=p.amount, currency=c, kind="payment", evidence="verified" if p.linked or p.evidence == "posted" else "provisional",
                           ref_type="transaction", ref_id=p.transaction_id, note=p.from_account_name) for p in card.payments]
    difference, flagged = (None, False)
    if card.check.status == "differs":
        difference, flagged = card.check.difference, True
    return Obligation(
        id=f"card:{card.account_id}", ref_type="card", ref_id=card.account_id, type="credit_card", direction="owe", name=card.name,
        counterparty=None, currency=c, original_amount=None, start_on=None, schedule="revolving", interest="interest" if card.apr_percent else "unknown",
        rate=card.apr_percent, expected_installment=card.statement_minimum, paid=card.paid_since_statement, remaining=card.outstanding,
        interest_paid=card.cycle.interest, fees=card.cycle.fees, next_due_on=card.due_on if card.payment_state not in ("paid_full", "no_statement") else None,
        next_due_amount=minimum.amount if minimum is not None and minimum.amount is not None else Decimal(0), overdue_amount=quantize(overdue, c),
        status=_status(card.outstanding, overdue, card.due_on, today, debt.disputed_amount if debt else None),
        verified_balance=card.statement_balance, verified_on=card.statement_on, difference=difference, discrepancy=flagged,
        disputed_amount=debt.disputed_amount if debt else None, unverified_amount=card.paid_provisional, payoff_on=payoff, payoff_basis=basis,
        assumptions=assumptions, history=history, sources=[Source(figure="remaining", source="ledger"), Source(figure="statement", source="entered" if card.statement_on else "not_entered"),
                                                            Source(figure="rate", source="entered" if card.apr_percent else "not_entered")],
        missing=list(card.missing),
    )


# -- buy now, pay later -------------------------------------------------------------------------------------


def _verified_installments(bills: list[Bill], bill_links: list[Any]) -> dict[tuple[str, int], str]:
    """(order, installment number): the status of its bill due date: "paid" (an active matched payment) or "review"."""
    from .commitments import BILL_DIMENSION, bill_target_id
    from .installment_refunds import bill_installment

    out: dict[tuple[str, int], str] = {}
    for bill in bills:
        found = bill_installment(bill)
        if found is None:
            continue
        target = bill_target_id(bill.id, bill.next_due_on)
        matched = [l for l in bill_links if l.dimension == BILL_DIMENSION and (l.target_id == target or l.target_id.startswith(f"{bill.id}:"))]
        statuses = {getattr(l.status, "value", l.status) for l in matched}
        if "active" in statuses:
            out[found] = "paid"
        elif "needs_review" in statuses:
            out[found] = "review"
    return out


def _bnpl_items(inputs: Inputs) -> tuple[list[Obligation], list[CalendarItem]]:
    today = inputs.today
    verified = _verified_installments(inputs.bills, inputs.bill_links)
    orders: dict[tuple[str, str, str], list[InstallmentStatementEntry]] = {}
    for entry in inputs.installments:
        orders.setdefault((entry.provider, entry.order_id, entry.currency), []).append(entry)
    out: list[Obligation] = []
    calendar: list[CalendarItem] = []
    for (provider, order_id, c), entries in sorted(orders.items()):
        entries.sort(key=lambda e: (e.installment, e.due_on))
        history: list[HistoryItem] = []
        remaining = paid = overdue = unverified = Decimal(0)
        next_due: date | None = None
        next_amount = Decimal(0)
        for e in entries:
            due = max(e.amount - e.refunded_amount, Decimal(0))
            state = verified.get((order_id.casefold(), e.installment))
            oid = f"installment_order:{provider}:{order_id}"
            if state == "paid":
                paid += due
                history.append(HistoryItem(on=e.due_on, amount=due, currency=c, kind="installment", evidence="verified", ref_type="installment",
                                           ref_id=f"{order_id}:{e.installment}"))
                status = "paid"
            elif e.source_status == "paid":
                # The provider says it is paid; no bank payment is matched to it yet: reported, never counted as verified.
                unverified += due
                history.append(HistoryItem(on=e.due_on, amount=due, currency=c, kind="installment", evidence="reported", ref_type="installment",
                                           ref_id=f"{order_id}:{e.installment}", note="reported paid by the provider"))
                status = "reported_paid"
            elif e.source_status == "refunded" or due <= 0:
                continue
            else:
                remaining += due
                if state == "review":
                    unverified += due
                if e.due_on < today:
                    overdue += due
                elif next_due is None:
                    next_due, next_amount = e.due_on, due
                status = "review" if state == "review" else ("overdue" if e.due_on < today else "due")
            if e.due_on < today and status != "overdue":
                continue
            if e.due_on <= today + timedelta(days=CALENDAR_DAYS):
                calendar.append(CalendarItem(on=e.due_on, obligation_id=oid, name=f"{provider}: {e.merchant}", amount=quantize(due, c), currency=c,
                                             direction="owe", status=status))  # type: ignore[arg-type]
        if remaining <= 0 and unverified <= 0:
            continue
        first = entries[0]
        last = max(e.due_on for e in entries)
        out.append(Obligation(
            id=f"installment_order:{provider}:{order_id}", ref_type="installment_order", ref_id=f"{provider}:{order_id}", type="bnpl", direction="owe",
            name=f"{provider}: {first.merchant}", counterparty=provider, currency=c,
            original_amount=quantize(sum((e.amount for e in entries), Decimal(0)), c), start_on=first.purchase_on, schedule="monthly",
            interest="interest_free", rate=None, expected_installment=first.amount, paid=quantize(paid, c), remaining=quantize(remaining, c),
            next_due_on=next_due, next_due_amount=quantize(next_amount, c), overdue_amount=quantize(overdue, c),
            status=_status(remaining, overdue, next_due, today, None), unverified_amount=quantize(unverified, c), payoff_on=last, payoff_basis="installments",
            assumptions=["The provider's schedule, with each installment paid on its date."], history=history,
            sources=[Source(figure="remaining", source="provider_statement"), Source(figure="paid", source="matched_bill_payments")],
        ))
    return out, calendar


# -- employer loans and salary advances ---------------------------------------------------------------------


def _salary_items(inputs: Inputs, loan_debts: list[Debt]) -> list[Obligation]:
    from . import salary as S

    profile = inputs.links.salary_profile
    if not isinstance(profile, P.SalaryProfile):
        return []
    today = inputs.today
    month = S.month_key(today)
    c = profile.currency
    out: list[Obligation] = []
    carried = [d for d in loan_debts if d.loan_repayment == "salary_deduction" and d.currency == c]
    for rule in profile.rules:
        kind = getattr(rule.kind, "value", rule.kind)
        reason = getattr(rule.reason, "value", rule.reason)
        history = []
        for slip in inputs.links.payslips:
            if getattr(slip, "currency", None) != c:
                continue
            for item in slip.deductions:
                k = getattr(item.kind, "value", item.kind)
                if (k == "loan" and kind == "deduction" and reason == "loan" and BL.close_enough(item.amount, rule.amount, c)) or (
                        k == "advance" and kind == "advance"):
                    history.append(HistoryItem(on=slip.paid_on or date.fromisoformat(f"{slip.month}-01"), amount=item.amount, currency=c, kind="salary_month",
                                               evidence="payslip", ref_type="payslip", ref_id=slip.id))
        history.sort(key=lambda h: h.on or date.min, reverse=True)
        if kind == "advance":
            # This month's repayment is taken only once this month's salary is paid.
            left = S.advance_left(rule, month) + (S.advance_repaid(rule, month) if today < S.pay_date(profile, month) else Decimal(0))
            if left <= 0:
                continue
            months = math.ceil(left / rule.amount) if rule.amount > 0 else None
            next_pay = S.next_payday(profile, today)
            out.append(Obligation(
                id=f"salary_rule:{rule.id}", ref_type="salary_rule", ref_id=rule.id, type="salary_advance", direction="owe",
                name=rule.label or "Salary advance", counterparty=profile.employer, currency=c, original_amount=rule.total, start_on=rule.received_on,
                schedule="salary", interest="interest_free", rate=None, expected_installment=rule.amount,
                paid=quantize((rule.total or Decimal(0)) - left, c), remaining=quantize(left, c), next_due_on=next_pay,
                next_due_amount=quantize(min(rule.amount, left), c), overdue_amount=Decimal(0), status=_status(left, Decimal(0), next_pay, today, None),
                payoff_on=_add_months(next_pay, (months or 1) - 1) if months else None, payoff_basis="advance_repayments",
                assumptions=[f"{c} {rule.amount} is taken off each salary until the advance is back."], history=history[:HISTORY_ROWS],
                sources=[Source(figure="remaining", source="salary_profile")],
            ))
        elif kind == "deduction" and reason == "loan" and S._active(rule, month):
            if any(BL.close_enough(d.loan_installment or Decimal(0), rule.amount, c) for d in carried):
                continue  # a bank loan with its terms already carries this deduction
            left = None
            payoff = None
            if rule.ends:
                months_left = S._months_from(month, rule.ends) + 1
                left = rule.amount * max(months_left, 0)
                payoff = S.pay_date(profile, rule.ends)
            next_pay = S.next_payday(profile, today)
            out.append(Obligation(
                id=f"salary_rule:{rule.id}", ref_type="salary_rule", ref_id=rule.id, type="employer_loan", direction="owe",
                name=rule.label or "Loan from salary", counterparty=profile.employer, currency=c, original_amount=None, start_on=None, schedule="salary",
                interest="unknown", rate=None, expected_installment=rule.amount, paid=quantize(sum((h.amount for h in history), Decimal(0)), c),
                remaining=quantize(left, c) if left is not None else Decimal(0), next_due_on=next_pay, next_due_amount=rule.amount, overdue_amount=Decimal(0),
                status="open", payoff_on=payoff, payoff_basis="salary_rule_end" if payoff else None,
                assumptions=[f"{c} {rule.amount} a month is deducted from the salary" + (f" until {rule.ends}." if rule.ends else "; no last month is entered.")],
                history=history[:HISTORY_ROWS], sources=[Source(figure="installment", source="salary_profile"), Source(figure="history", source="payslip")],
                missing=[] if rule.ends else ["end_month"],
            ))
    return out


# -- loans with people --------------------------------------------------------------------------------------


def _person_item(loan: P.PersonLoan, inputs: Inputs, saved_names: set[tuple[str, str]]) -> Obligation | None:
    today = inputs.today
    c = loan.currency
    links = inputs.links.loan_links
    f = P.loan_figures(loan, links, today)
    lent = loan.direction == P.LoanDirection.LENT
    if not lent and (M.name_key(loan.person), c) in saved_names:
        return None  # the same person is a saved Debt: counted there
    mine = [l for l in links if l.target_id == loan.id]
    history: list[HistoryItem] = []
    repayments: list[tuple[date, Decimal]] = []
    for link in mine:
        on = inputs.loan_dates.get(link.canonical_id)
        active = getattr(link.status, "value", link.status) == "active"
        is_repayment = link.role == P.repaid_role(loan.direction)
        history.append(HistoryItem(on=on, amount=link.amount or Decimal(0), currency=link.currency or c, kind="payment" if is_repayment else "handed",
                                   evidence="verified" if active else "provisional", ref_type="transaction", ref_id=link.canonical_id))
        if is_repayment and active and on is not None and link.currency == c:
            repayments.append((on, link.amount or Decimal(0)))
    history.sort(key=lambda h: h.on or date.min, reverse=True)
    promises = []
    for promise in loan.promises:
        state, paid = P.promise_status(promise, repayments, today)
        promises.append(PromiseView(id=promise.id, made_on=promise.made_on, due_by=promise.due_by, amount=promise.amount, note=promise.note, status=state,
                                    paid=quantize(paid, c)))
    remaining = f.outstanding if f.status not in ("repaid", "closed") else Decimal(0)
    unverified = sum((l.amount or Decimal(0) for l in mine if getattr(l.status, "value", l.status) == "needs_review" and l.currency == c), Decimal(0))
    difference, flagged = _compare(loan.verified_balance, remaining, c)
    schedule = "monthly" if loan.installments else ("single" if loan.due_on else "none")
    last_due = f.schedule[-1].due_on if f.schedule else None
    status: Status = "closed" if f.status == "closed" else "paid_off" if f.status == "repaid" else _status(remaining, f.overdue_amount, f.next_due_on, today, None)
    return Obligation(
        id=f"person_loan:{loan.id}", ref_type="person_loan", ref_id=loan.id, type="lent_person" if lent else "borrowed_person",
        direction="owed_to_me" if lent else "owe", name=loan.person, counterparty=loan.person, currency=c, original_amount=loan.amount, start_on=loan.lent_on,
        schedule=schedule, interest="interest" if loan.interest_rate else "interest_free", rate=loan.interest_rate,
        expected_installment=f.schedule[0].amount if loan.installments and f.schedule else None, paid=f.repaid, remaining=quantize(remaining, c),
        next_due_on=f.next_due_on, next_due_amount=f.next_due_amount, overdue_amount=f.overdue_amount, status=status,
        verified_balance=loan.verified_balance, verified_on=loan.verified_on, difference=difference, discrepancy=flagged,
        unverified_amount=quantize(unverified, c), payoff_on=last_due if remaining > 0 else None, payoff_basis="due_date" if last_due else None,
        assumptions=(["Each repayment on its agreed date."] if last_due else ["No due date is agreed, so no payoff date can be worked out."]),
        history=history[:HISTORY_ROWS], promises=promises,
        sources=[Source(figure="remaining", source="amount_less_linked_repayments"), Source(figure="rate", source="entered" if loan.interest_rate else "interest_free")],
        missing=[] if (loan.due_on or loan.installments) else ["due_date"], contact_id=loan.contact_id,
    )


# -- the calendar and the plan ------------------------------------------------------------------------------


def _calendar(items: list[Obligation], extra: list[CalendarItem], states: list[Any], today: date) -> list[CalendarItem]:
    out = list(extra)
    end = today + timedelta(days=CALENDAR_DAYS)
    loan_rows = {f"debt:{s.debt.id}": s for s in states}
    for item in items:
        if item.ref_type == "installment_order":
            continue
        state = loan_rows.get(item.id)
        if state is not None:
            for row in state.view.rows:
                if row.status in ("paid", "settled") or row.due_on > end:
                    continue
                if row.status == "missed" or row.due_on >= today:
                    out.append(CalendarItem(on=row.due_on, obligation_id=item.id, name=item.name, amount=quantize(row.payment - row.paid, item.currency),
                                            currency=item.currency, direction="owe", status="overdue" if row.status == "missed" else "due"))
            continue
        if item.overdue_amount > 0:
            out.append(CalendarItem(on=item.next_due_on or today, obligation_id=item.id, name=item.name, amount=item.overdue_amount, currency=item.currency,
                                    direction=item.direction, status="overdue"))
        if item.next_due_on is not None and today <= item.next_due_on <= end and item.next_due_amount > 0 and item.status not in ("paid_off", "closed"):
            if item.overdue_amount > 0 and item.next_due_on <= today:
                continue
            out.append(CalendarItem(on=item.next_due_on, obligation_id=item.id, name=item.name, amount=item.next_due_amount, currency=item.currency,
                                    direction=item.direction, status="due"))
    return sorted(out, key=lambda c: (c.on, c.name.casefold(), c.obligation_id))


def plan(items: list[Obligation]) -> list[PlanRow]:
    """Per currency: overdue first, then the highest known rate, then an unknown rate, then interest free; smaller balance first on a tie."""
    out: list[PlanRow] = []
    owe = [i for i in items if i.direction == "owe" and i.remaining > 0 and i.status not in ("paid_off", "closed")]
    for currency in sorted({i.currency for i in owe}):
        rows = [i for i in owe if i.currency == currency]

        def key(i: Obligation) -> tuple:
            group = 0 if i.overdue_amount > 0 else 1 if i.rate else 2 if i.interest == "unknown" else 3
            return (group, -(i.rate or Decimal(0)), i.remaining, i.name.casefold())

        for rank, item in enumerate(sorted(rows, key=key), start=1):
            reason = ("overdue" if item.overdue_amount > 0 else "highest_rate" if item.rate else "rate_unknown" if item.interest == "unknown" else "interest_free")
            out.append(PlanRow(rank=rank, obligation_id=item.id, name=item.name, currency=currency, remaining=item.remaining, rate=item.rate, reason=reason))
    return out


def _totals(items: list[Obligation], calendar: list[CalendarItem], today: date) -> list[CurrencyTotal]:
    out = []
    soon = today + timedelta(days=30)
    for c in sorted({i.currency for i in items}):
        mine = [i for i in items if i.currency == c and i.status not in ("paid_off", "closed")]
        out.append(CurrencyTotal(
            currency=c, owe=quantize(sum((i.remaining for i in mine if i.direction == "owe"), Decimal(0)), c),
            owed_to_me=quantize(sum((i.remaining for i in mine if i.direction == "owed_to_me"), Decimal(0)), c),
            overdue=quantize(sum((i.overdue_amount for i in mine if i.direction == "owe"), Decimal(0)), c),
            due_30_days=quantize(sum((x.amount for x in calendar if x.currency == c and x.direction == "owe" and x.status in ("due", "overdue", "review")
                                      and x.on <= soon), Decimal(0)), c),
        ))
    return out


def build(inputs: Inputs) -> Overview:
    today = inputs.today
    with_terms = {s.debt.id: s for s in inputs.loan_states}
    by_id = {a.id: a for a in inputs.accounts}
    card_accounts = {c.account_id for c in inputs.cards}
    items: list[Obligation] = []
    for debt in inputs.debts:
        if debt.account_id in card_accounts and debt.account_id is not None:
            continue  # the card's own view carries it
        if debt.id in with_terms and not O.has_account(debt, by_id):
            items.append(_loan_item(with_terms[debt.id], inputs))
        else:
            items.append(_plain_debt(debt, inputs))
    debts_by_id = {d.id: d for d in inputs.debts}
    items.extend(_card_item(card, debts_by_id, today) for card in inputs.cards)
    bnpl, bnpl_calendar = _bnpl_items(inputs)
    items.extend(bnpl)
    items.extend(_salary_items(inputs, [s.debt for s in inputs.loan_states]))
    saved = {(M.name_key(d.name), d.currency) for d in inputs.debts}
    for loan in inputs.links.person_loans:
        if isinstance(loan, P.PersonLoan):
            found = _person_item(loan, inputs, saved)
            if found is not None:
                items.append(found)
    order = {"overdue": 0, "disputed": 1, "due_soon": 2, "open": 3, "paid_off": 4, "closed": 5}
    items.sort(key=lambda i: (i.direction != "owe", order.get(i.status, 9), i.next_due_on or date.max, i.name.casefold(), i.id))
    calendar = _calendar(items, bnpl_calendar, inputs.loan_states, today)
    return Overview(today=today, items=items, totals=_totals(items, calendar, today), calendar=calendar, plan=plan(items),
                    discrepancies=sum(1 for i in items if i.discrepancy))


def gather(context: Any, today: date) -> Inputs:
    """The overview's inputs, each read in its own short owner-scoped unit of work (never nested)."""
    from . import bank_loan_service as S
    from . import credit_cards as CC
    from .commitments import BILL_DIMENSION, DEBT_DIMENSION
    from .links import live_entry
    from .links_store import LinkStatus, open_link_stores
    from .store import open_finance_store

    links = O.load(context)
    with open_finance_store(context) as store:
        debts = [d for d in store.list_items(context, "debt") if isinstance(d, Debt)]
        accounts = store.list_accounts(context)
        balances = store.account_sums(context)
        planning = store.get_planning(context)
        bills = [b for b in store.list_items(context, "bill") if isinstance(b, Bill)]
    states = S.states(context, today)
    cards = CC.build(CC.gather(context, today))
    with open_link_stores(context) as (_finance, sms, link_store):
        review: list[Any] = []
        for dimension in (DEBT_DIMENSION, P.PERSON_LOAN_DIMENSION):
            rows, _ = link_store.list_links(context, canonical_id=None, dimension=dimension, target_type=None, target_id=None,
                                            status=LinkStatus.NEEDS_REVIEW.value, limit=2_000, offset=0)
            review.extend(rows)
        bill_rows, _ = link_store.list_links(context, canonical_id=None, dimension=BILL_DIMENSION, target_type=None, target_id=None, status=None,
                                             limit=10_000, offset=0)
        dates: dict[str, date] = {}
        for link in links.loan_links:
            if link.canonical_id not in dates:
                entry = live_entry(sms.chain(context, link.canonical_id))
                if entry is not None:
                    dates[link.canonical_id] = entry.posted_on
    return Inputs(today=today, debts=debts, accounts=accounts, balances=balances, links=links, loan_states=states, cards=cards,
                  installments=list(planning.installment_entries), bills=bills,
                  bill_links=[l for l in bill_rows if getattr(l.status, "value", l.status) != "void"], review_links=review, loan_dates=dates)


def overview(context: Any, today: date) -> Overview:
    return build(gather(context, today))


__all__ = ["DEBT_TYPES", "CalendarItem", "HistoryItem", "Inputs", "Obligation", "Overview", "PlanRow", "PromiseView", "build", "debt_type", "gather",
           "overview", "plan"]

