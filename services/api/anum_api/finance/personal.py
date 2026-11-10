"""Personal money records: money lent and borrowed with people, payslips and remittances, and the
fixed commitments view (docs/finance.md#lent-and-borrowed, #payslips, #sending-money-home,
#fixed-commitments).

Every record belongs to one tenant, one workspace and one person (``owner_user_id``), like the rest of
Finance. Amounts are ``Decimal`` rounded by ``money.quantize`` and sit next to their currency; nothing is
added across currencies. ANUM only records and advises: nothing here moves money.

* **Lent and borrowed** (``PersonLoan``): a loan with a person, in either direction, interest free (qard hasan)
  unless the person sets an annual rate. It is repaid once on its due date or on a monthly schedule
  (``installments`` from ``first_due_on``, :func:`loan_schedule`). What changed hands and every repayment are
  finance links (``person_loan`` dimension) on real ledger transactions. By the shared rule (``flows.py``) a
  transaction linked to a loan with a person is a transfer: lending is not spending and being repaid is not income.
* **Payslips** (``Payslip``): gross pay and the deductions on one payslip; net is gross less deductions.
  The salary credit in the ledger is linked (``payslip`` dimension) and the difference is shown.
* **Remittances** (``Remittance``): money sent home, the fee, the rate and what arrived, linked to the
  money-out transaction (``remittance`` dimension).
* **Fixed commitments**: what goes out every month, one total per currency, read from saved bills,
  installment schedules, debt minimums (not counted twice when also saved as a bill, ``obligations.py``)
  and regular remittances.

Pure: no store, clock or network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from . import commitment_math as M
from .models import Bill, Cadence, Category, Debt, DebtKind, InstallmentStatementEntry
from .money import quantize

PERSON_LOAN_DIMENSION = "person_loan"
PERSON_LOAN_TARGET = "finance_person_loan"
PAYSLIP_DIMENSION = "payslip"
PAYSLIP_TARGET = "finance_payslip"
REMITTANCE_DIMENSION = "remittance"
REMITTANCE_TARGET = "finance_remittance"
PERSONAL_DIMENSIONS = (PERSON_LOAN_DIMENSION, PAYSLIP_DIMENSION, REMITTANCE_DIMENSION)
DUE_SOON_DAYS = 7
MAX_DEDUCTIONS = 20


class _Owned(BaseModel):
    tenant_id: str
    workspace_id: str
    owner_user_id: str


# -- lent and borrowed -----------------------------------------------------------------------------


class LoanDirection(StrEnum):
    LENT = "lent"  # they owe me
    BORROWED = "borrowed"  # I owe them


class PersonLoan(_Owned):
    id: str
    direction: LoanDirection
    person: str = Field(min_length=1, max_length=120)
    amount: Decimal  # what was lent or borrowed, positive
    currency: str
    lent_on: date
    due_on: date | None = None
    notes: str | None = None
    # In-app reminder this many days before the due date (and once more when it is overdue). False: none.
    remind: bool = True
    remind_days_before: int = Field(default=3, ge=0, le=30)
    # The due date the last reminder was sent for, so one due date is announced once ("due" and "overdue" share it).
    reminded_for: date | None = None
    reminded_overdue_for: date | None = None
    # The person closed it without full repayment (forgiven, settled in kind). None: still open.
    closed_on: date | None = None
    # Migration 0158_finance_depth. An annual interest rate in percent (None: interest free) and a monthly repayment
    # schedule: ``installments`` repayments from ``first_due_on`` (both or neither). With a schedule ``due_on`` is the
    # last repayment's date.
    interest_rate: Decimal | None = Field(default=None, gt=0, le=100)
    installments: int | None = Field(default=None, ge=1, le=360)
    first_due_on: date | None = None
    created_at: datetime
    updated_at: datetime


LoanStatus = Literal["open", "due_soon", "overdue", "repaid", "closed"]


def handed_role(direction: LoanDirection) -> str:
    """The link role of the money that first changed hands: lending is money out (``payment``), borrowing money in (``deposit``)."""
    return "payment" if direction == LoanDirection.LENT else "deposit"


def repaid_role(direction: LoanDirection) -> str:
    """The link role of a repayment: they pay me back (money in) or I pay them back (money out)."""
    return "deposit" if direction == LoanDirection.LENT else "payment"


@dataclass(frozen=True)
class Installment:
    """One repayment of a loan's schedule: its number, date, principal and interest (``amount`` is both)."""

    number: int
    due_on: date
    principal: Decimal
    interest: Decimal
    amount: Decimal


def _add_months(day: date, months: int, anchor: int) -> date:
    year, month = divmod(day.month - 1 + months, 12)
    first = date(day.year + year, month + 1, 1)
    return first.replace(day=min(anchor, M.month_end(first).day))


def schedule_dates(first_due_on: date, installments: int) -> list[date]:
    """Monthly from ``first_due_on``, on the same day of the month (the month's last day when shorter)."""
    return [_add_months(first_due_on, n, first_due_on.day) for n in range(installments)]


def loan_schedule(loan: PersonLoan) -> list[Installment]:
    """What is due and when, rounded to the currency's minor unit; the last repayment takes the rounding.

    * With ``installments``: monthly repayments. With a rate, equal payments (an annuity at the rate over 12 on what is
      left each month); without one, equal parts of the amount.
    * Without a schedule but with a due date: one repayment on it. With a rate, simple interest on the amount for the
      days from the date the money changed hands to the due date (a 365-day year).
    * Without either: nothing is scheduled (the loan has no date to be late against).
    """
    c = loan.currency
    rate = (loan.interest_rate or Decimal(0)) / Decimal(100)
    if loan.installments and loan.first_due_on:
        n = loan.installments
        dates = schedule_dates(loan.first_due_on, n)
        out: list[Installment] = []
        left = loan.amount
        monthly = rate / Decimal(12)
        payment = quantize(loan.amount * monthly / (1 - (1 + monthly) ** -n), c) if monthly > 0 else None
        for index, on in enumerate(dates):
            interest = quantize(left * monthly, c)
            if index == n - 1:
                principal = quantize(left, c)
            elif payment is not None:
                principal = min(quantize(payment - interest, c), left)
            else:
                principal = quantize(loan.amount / n, c)
            left -= principal
            out.append(Installment(number=index + 1, due_on=on, principal=principal, interest=interest, amount=principal + interest))
        return out
    if loan.due_on is None:
        return []
    days = max((loan.due_on - loan.lent_on).days, 0)
    interest = quantize(loan.amount * rate * Decimal(days) / Decimal(365), c)
    return [Installment(number=1, due_on=loan.due_on, principal=quantize(loan.amount, c), interest=interest, amount=quantize(loan.amount, c) + interest)]


@dataclass(frozen=True)
class LoanFigures:
    repaid: Decimal
    outstanding: Decimal  # what is still to repay: the amount plus the scheduled interest, less what is repaid
    handed: Decimal  # what the linked transactions show changed hands
    status: LoanStatus
    days_until_due: int | None  # to the next repayment not yet covered
    interest: Decimal = Decimal(0)  # the scheduled interest in total
    total_due: Decimal = Decimal(0)  # the amount plus the scheduled interest
    next_due_on: date | None = None  # the first repayment the repaid money does not cover yet
    next_due_amount: Decimal = Decimal(0)  # what is left of it
    overdue_amount: Decimal = Decimal(0)  # what was due before today and is not repaid
    schedule: tuple[Installment, ...] = ()
    paid_through: int = 0  # how many repayments the repaid money covers in full


def loan_figures(loan: PersonLoan, links: list, today: date) -> LoanFigures:
    """What is repaid (active repayment links in the loan's currency), what is left, what is overdue and the status.

    Repayments are applied to the schedule in date order, never across currencies: a repayment in another currency is
    not linked (the links API refuses it), so it never reduces what is owed."""
    c = loan.currency
    repaid = sum((l.amount or Decimal(0) for l in links if l.target_id == loan.id and l.role == repaid_role(loan.direction)
                  and l.currency == c and str(getattr(l.status, "value", l.status)) == "active"), Decimal(0))
    handed = sum((l.amount or Decimal(0) for l in links if l.target_id == loan.id and l.role == handed_role(loan.direction)
                  and l.currency == c and str(getattr(l.status, "value", l.status)) == "active"), Decimal(0))
    schedule = loan_schedule(loan)
    interest = sum((i.interest for i in schedule), Decimal(0))
    total_due = loan.amount + interest
    outstanding = max(total_due - repaid, Decimal(0))
    cumulative = Decimal(0)
    next_due_on: date | None = None
    next_amount = Decimal(0)
    overdue = Decimal(0)
    paid_through = 0
    for item in schedule:
        cumulative += item.amount
        if cumulative <= repaid:
            paid_through = item.number
            continue
        if next_due_on is None:
            next_due_on = item.due_on
            next_amount = min(cumulative - repaid, item.amount)
        if item.due_on < today:
            overdue = cumulative - repaid
    days = (next_due_on - today).days if next_due_on else None
    status: LoanStatus
    if outstanding <= 0:
        status = "repaid"
    elif loan.closed_on is not None:
        status = "closed"
    elif overdue > 0:
        status = "overdue"
    elif days is not None and days <= DUE_SOON_DAYS:
        status = "due_soon"
    else:
        status = "open"
    return LoanFigures(repaid=quantize(repaid, c), outstanding=quantize(outstanding, c), handed=quantize(handed, c), status=status,
                       days_until_due=days, interest=quantize(interest, c), total_due=quantize(total_due, c), next_due_on=next_due_on,
                       next_due_amount=quantize(next_amount, c), overdue_amount=quantize(overdue, c), schedule=tuple(schedule),
                       paid_through=paid_through)


def due_through(figures: LoanFigures, end: date) -> Decimal:
    """What the schedule asks for on or before ``end`` that is not repaid yet (overdue repayments included)."""
    cumulative = sum((i.amount for i in figures.schedule if i.due_on <= end), Decimal(0))
    return max(cumulative - figures.repaid, Decimal(0))


def reminder_due(loan: PersonLoan, figures: LoanFigures, today: date) -> Literal["due", "overdue"] | None:
    """Which reminder (if any) to send today: once ``remind_days_before`` the next repayment's date, and once when it is
    overdue. Each repayment date of a schedule is announced once (``reminded_for`` and ``reminded_overdue_for`` hold it)."""
    due_on = figures.next_due_on
    if not loan.remind or due_on is None or figures.status in ("repaid", "closed"):
        return None
    if today > due_on:
        return "overdue" if loan.reminded_overdue_for != due_on else None
    if (due_on - today).days <= loan.remind_days_before and loan.reminded_for != due_on:
        return "due"
    return None


# -- expected one-off income (migration 0158_finance_depth, docs/finance.md#forecast) ---------------------------------


class ExpectedIncome(_Owned):
    """Money the person expects once on a date (a bonus, a refund, a sale). The forecast counts it on that date until it
    is marked received (then the money is in the balance); never added to the low line."""

    id: str
    label: str = Field(min_length=1, max_length=120)
    amount: Decimal
    currency: str
    expected_on: date
    received_on: date | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


# -- payslips --------------------------------------------------------------------------------------


class DeductionKind(StrEnum):
    SOCIAL_INSURANCE = "social_insurance"  # GOSI, pension
    MEDICAL_INSURANCE = "medical_insurance"
    LOAN = "loan"  # a loan repaid from the salary
    ADVANCE = "advance"  # a salary advance taken back
    TAX = "tax"
    OTHER = "other"


class Deduction(BaseModel):
    kind: DeductionKind
    amount: Decimal
    label: str | None = Field(default=None, max_length=80)


class Payslip(_Owned):
    id: str
    employer: str = Field(min_length=1, max_length=120)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")  # the pay period
    paid_on: date | None = None
    currency: str
    gross: Decimal
    deductions: list[Deduction] = Field(default_factory=list, max_length=MAX_DEDUCTIONS)
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


ReconcileStatus = Literal["not_linked", "matched", "differs"]


@dataclass(frozen=True)
class PayslipFigures:
    deductions: Decimal
    net: Decimal
    received: Decimal  # active salary credits linked to it, in its currency
    difference: Decimal  # received less net (negative: less arrived than the payslip says)
    status: ReconcileStatus
    by_kind: dict[str, Decimal]


def payslip_figures(slip: Payslip, links: list) -> PayslipFigures:
    by_kind: dict[str, Decimal] = {}
    for item in slip.deductions:
        by_kind[item.kind.value] = by_kind.get(item.kind.value, Decimal(0)) + item.amount
    total = sum(by_kind.values(), Decimal(0))
    net = slip.gross - total
    received = sum((l.amount or Decimal(0) for l in links if l.target_id == slip.id and l.currency == slip.currency
                    and str(getattr(l.status, "value", l.status)) == "active"), Decimal(0))
    linked = any(l.target_id == slip.id and str(getattr(l.status, "value", l.status)) == "active" for l in links)
    difference = received - net
    status: ReconcileStatus = "not_linked" if not linked else ("matched" if difference == 0 else "differs")
    c = slip.currency
    return PayslipFigures(deductions=quantize(total, c), net=quantize(net, c), received=quantize(received, c), difference=quantize(difference, c),
                          status=status, by_kind={k: quantize(v, c) for k, v in sorted(by_kind.items())})


@dataclass
class PayMonth:
    month: str
    currency: str
    gross: Decimal
    deductions: Decimal
    net: Decimal
    received: Decimal
    by_kind: dict[str, Decimal]
    payslips: int


def pay_months(slips: list[Payslip], links: list) -> list[PayMonth]:
    """Gross against net and the deductions by kind, per month and currency, newest month first."""
    months: dict[tuple[str, str], PayMonth] = {}
    for slip in slips:
        f = payslip_figures(slip, links)
        row = months.setdefault((slip.month, slip.currency), PayMonth(slip.month, slip.currency, Decimal(0), Decimal(0), Decimal(0), Decimal(0), {}, 0))
        row.gross += slip.gross
        row.deductions += f.deductions
        row.net += f.net
        row.received += f.received
        row.payslips += 1
        for kind, value in f.by_kind.items():
            row.by_kind[kind] = row.by_kind.get(kind, Decimal(0)) + value
    return sorted(months.values(), key=lambda m: (m.month, m.currency), reverse=True)


# -- the salary profile ------------------------------------------------------------------------------
# One per person: the job the salary comes from, what it is expected to be and the standing rules that take
# money off it. Detection, the month by month check and the one income figure are in ``salary.py``.


class SalaryRuleKind(StrEnum):
    DEDUCTION = "deduction"  # taken off every month from ``starts`` (to ``ends``)
    ADVANCE = "advance"  # money received early, repaid by ``amount`` a month from ``starts`` until ``total`` is back


class SalaryDeductionReason(StrEnum):
    LOAN = "loan"  # a loan repaid from the salary
    ADVANCE = "advance"  # an advance repaid (the advance rule's own repayments)
    ABSENCE = "absence"
    SOCIAL_INSURANCE = "social_insurance"  # GOSI, pension
    OTHER = "other"


MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class SalaryRule(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: SalaryRuleKind
    reason: SalaryDeductionReason = SalaryDeductionReason.OTHER
    label: str | None = Field(default=None, max_length=80)
    amount: Decimal  # taken off the salary each month, positive
    starts: str = Field(pattern=MONTH_PATTERN)
    ends: str | None = Field(default=None, pattern=MONTH_PATTERN)  # a deduction's last month (None: until removed)
    total: Decimal | None = None  # an advance: what was received; repayments stop once it is back
    received_on: date | None = None  # an advance: the day it arrived (that credit is not a salary)


SalaryDecisionKind = Literal["confirmed", "rejected"]


class SalaryDecision(BaseModel):
    month: str = Field(pattern=MONTH_PATTERN)
    transaction_id: str = Field(min_length=1, max_length=80)
    decision: SalaryDecisionKind
    decided_at: datetime


MAX_SALARY_RULES = 30
MAX_SALARY_DECISIONS = 1000


class SalaryProfile(_Owned):
    id: str
    employer: str = Field(min_length=1, max_length=120)
    joined_on: date
    currency: str
    expected_gross: Decimal  # the monthly salary before deductions
    pay_day: int = Field(ge=1, le=31)  # the usual day it arrives (the month's last day when shorter)
    account_id: str | None = None  # the account it is paid into (None: any bank account in its currency)
    rules: list[SalaryRule] = Field(default_factory=list, max_length=MAX_SALARY_RULES)
    decisions: list[SalaryDecision] = Field(default_factory=list, max_length=MAX_SALARY_DECISIONS)
    created_at: datetime
    updated_at: datetime


# -- remittances -----------------------------------------------------------------------------------


class Remittance(_Owned):
    id: str
    sent_on: date
    amount: Decimal  # what was sent, before the fee
    currency: str
    fee: Decimal = Decimal(0)  # in ``currency``
    fx_rate: Decimal | None = None  # received currency per one unit sent
    received_amount: Decimal | None = None
    received_currency: str | None = None
    recipient: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=120)
    # Sent every month (counted in fixed commitments with its newest amount and fee).
    regular: bool = False
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


def effective_rate(r: Remittance) -> Decimal | None:
    """What arrived per unit paid, fee included: the figure to compare providers by (higher is cheaper)."""
    paid = r.amount + r.fee
    if r.received_amount is None or paid <= 0:
        return None
    return (r.received_amount / paid).quantize(Decimal("0.000001"))


def fee_percent(r: Remittance) -> Decimal | None:
    if r.amount <= 0:
        return None
    return (r.fee * 100 / r.amount).quantize(Decimal("0.01"))


@dataclass
class RemitMonth:
    month: str
    currency: str
    sent: Decimal
    fees: Decimal
    count: int
    received: dict[str, Decimal]


def remit_months(rows: list[Remittance]) -> list[RemitMonth]:
    """Per month and send currency: sent, fees, transfers and what arrived per received currency, newest first."""
    out: dict[tuple[str, str], RemitMonth] = {}
    for r in rows:
        key = (r.sent_on.strftime("%Y-%m"), r.currency)
        m = out.setdefault(key, RemitMonth(key[0], r.currency, Decimal(0), Decimal(0), 0, {}))
        m.sent += r.amount
        m.fees += r.fee
        m.count += 1
        if r.received_amount is not None and r.received_currency:
            m.received[r.received_currency] = m.received.get(r.received_currency, Decimal(0)) + r.received_amount
    return sorted(out.values(), key=lambda m: (m.month, m.currency), reverse=True)


@dataclass
class ProviderCost:
    provider: str
    corridor: str  # "AED>INR"
    transfers: int
    average_fee_percent: Decimal | None
    best_effective_rate: Decimal | None
    latest_effective_rate: Decimal | None
    latest_on: date


def provider_costs(rows: list[Remittance]) -> list[ProviderCost]:
    """From the person's own transfers: per provider and corridor, the fee as a share of the amount and the
    effective rate (what arrived per unit paid, fee included). Best first (highest latest effective rate)."""
    groups: dict[tuple[str, str], list[Remittance]] = {}
    for r in rows:
        if r.received_currency:
            groups.setdefault((r.provider.strip(), f"{r.currency}>{r.received_currency}"), []).append(r)
    out: list[ProviderCost] = []
    for (provider, corridor), items in groups.items():
        items.sort(key=lambda r: (r.sent_on, r.created_at))
        fees = [p for p in (fee_percent(r) for r in items) if p is not None]
        rates = [e for e in (effective_rate(r) for r in items) if e is not None]
        latest = next((effective_rate(r) for r in reversed(items) if effective_rate(r) is not None), None)
        out.append(ProviderCost(provider=provider, corridor=corridor, transfers=len(items),
                                average_fee_percent=(sum(fees, Decimal(0)) / len(fees)).quantize(Decimal("0.01")) if fees else None,
                                best_effective_rate=max(rates) if rates else None, latest_effective_rate=latest, latest_on=items[-1].sent_on))
    out.sort(key=lambda p: (p.corridor, -(p.latest_effective_rate or Decimal(0)), p.provider.casefold()))
    return out


# -- fixed commitments ------------------------------------------------------------------------------


CommitmentKind = Literal["rent", "subscription", "bill", "installment", "loan_minimum", "card_minimum", "remittance"]


@dataclass(frozen=True)
class Commitment:
    kind: CommitmentKind
    name: str
    currency: str
    monthly: Decimal
    source: str  # "bill", "installment", "debt", "remittance"
    ref_id: str
    note: str | None = None  # a code the clients word: "weekly", "quarterly", "yearly", "in_bill", ...


def _bill_kind(bill: Bill) -> CommitmentKind:
    if bill.category in (Category.HOUSING, Category.ROOM_RENT):
        return "rent"
    if bill.category == Category.SUBSCRIPTIONS:
        return "subscription"
    return "bill"


_key = M.name_key


def _still_due(entry: InstallmentStatementEntry) -> Decimal:
    """What is left to pay on an installment after applied refunds (the web's ``outstandingAmount``)."""
    return max(Decimal(0), entry.amount - entry.refunded_amount)


def fixed_commitments(bills: list[Bill], debts: list[Debt], installments: list[InstallmentStatementEntry], remittances: list[Remittance],
                      today: date) -> tuple[list[Commitment], list[Commitment]]:
    """What goes out every month, and what was left out so it is not counted twice (with the reason).

    * Active bills by their monthly equivalent (``commitment_math.monthly_equivalent``); a one-off bill is left out.
    * Installment plans (Tamara, Tabby, ...): this month's unpaid installments per order; an order whose merchant or
      provider is also saved as a bill is the bill.
    * Debt minimums (at most what is owed); a minimum also saved as a bill is the bill (``commitment_math.bill_for_debt``).
      Money borrowed from a person comes in as a debt (``obligations.borrowed_debts``) with the amount left as its
      minimum in the month it is due (source ``person_loan``, note ``due_this_month``).
    * Regular remittances: the newest per recipient and currency, amount plus fee; one also saved as a bill is the bill.
    """
    counted: list[Commitment] = []
    skipped: list[Commitment] = []
    active = [b for b in bills if b.active]
    bill_names = {(_key(b.name), b.currency) for b in active}
    for bill in active:
        monthly = M.monthly_equivalent(bill.amount, bill.cadence)
        row = Commitment(kind=_bill_kind(bill), name=bill.name, currency=bill.currency,
                         monthly=quantize(monthly, bill.currency) if monthly is not None else bill.amount, source="bill", ref_id=bill.id,
                         note=None if bill.cadence == Cadence.MONTHLY else bill.cadence.value)
        (counted if monthly is not None else skipped).append(row)
    month = today.strftime("%Y-%m")
    orders: dict[tuple[str, str, str], list[InstallmentStatementEntry]] = {}
    for entry in installments:
        # Paid, refunded by the provider, or fully refunded by applied refund evidence: nothing left to pay.
        if entry.due_on.strftime("%Y-%m") == month and entry.source_status not in ("paid", "refunded") and _still_due(entry) > 0:
            orders.setdefault((entry.provider, entry.order_id, entry.currency), []).append(entry)
    for (provider, order_id, currency), entries in sorted(orders.items()):
        merchant = entries[0].merchant
        row = Commitment(kind="installment", name=f"{provider}: {merchant}", currency=currency,
                         monthly=quantize(sum((_still_due(e) for e in entries), Decimal(0)), currency), source="installment",
                         ref_id=f"{provider}:{order_id}")
        if (_key(merchant), currency) in bill_names or (_key(provider), currency) in bill_names:
            skipped.append(Commitment(**{**row.__dict__, "note": "in_bill"}))
        else:
            counted.append(row)
    for debt in debts:
        if not M.owes_minimum(debt):
            continue
        kind: CommitmentKind = "card_minimum" if debt.kind == DebtKind.CARD else "loan_minimum"
        # Money borrowed from a person (``obligations.PersonDebt``) has a minimum only in the month it is due.
        person = getattr(debt, "source", None) == "person_loan"
        row = Commitment(kind=kind, name=debt.name, currency=debt.currency, monthly=M.debt_minimum(debt), source="person_loan" if person else "debt",
                         ref_id=debt.id, note="due_this_month" if person else None)
        if M.bill_for_debt(debt, active) is not None:
            skipped.append(Commitment(**{**row.__dict__, "note": "in_bill"}))
        else:
            counted.append(row)
    newest: dict[tuple[str, str], Remittance] = {}
    for r in sorted((r for r in remittances if r.regular), key=lambda r: (r.sent_on, r.created_at)):
        newest[(_key(r.recipient), r.currency)] = r
    for (name, currency), r in sorted(newest.items()):
        row = Commitment(kind="remittance", name=r.recipient, currency=currency, monthly=quantize(r.amount + r.fee, currency), source="remittance",
                         ref_id=r.id, note="with_fee" if r.fee > 0 else None)
        if (name, currency) in bill_names:
            skipped.append(Commitment(**{**row.__dict__, "note": "in_bill"}))
        else:
            counted.append(row)
    order = {"rent": 0, "bill": 1, "subscription": 2, "installment": 3, "loan_minimum": 4, "card_minimum": 5, "remittance": 6}
    counted.sort(key=lambda c: (c.currency, order[c.kind], -c.monthly, c.name.casefold()))
    return counted, skipped


def commitment_totals(rows: list[Commitment]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for row in rows:
        out[row.currency] = out.get(row.currency, Decimal(0)) + row.monthly
    return dict(sorted(out.items()))


__all__ = [
    "Commitment", "Deduction", "DeductionKind", "ExpectedIncome", "Installment", "LoanDirection", "LoanFigures", "PAYSLIP_DIMENSION", "PAYSLIP_TARGET", "PERSONAL_DIMENSIONS",
    "PERSON_LOAN_DIMENSION", "PERSON_LOAN_TARGET", "PayMonth", "Payslip", "PayslipFigures", "PersonLoan", "ProviderCost", "REMITTANCE_DIMENSION",
    "REMITTANCE_TARGET", "RemitMonth", "Remittance", "SalaryDecision", "SalaryDeductionReason", "SalaryProfile", "SalaryRule", "SalaryRuleKind",
    "commitment_totals", "effective_rate", "fee_percent", "fixed_commitments", "handed_role",
    "due_through", "loan_figures", "loan_schedule", "pay_months", "payslip_figures", "provider_costs", "remit_months", "reminder_due", "repaid_role",
]
