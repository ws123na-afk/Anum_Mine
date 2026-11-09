"""Personal money records: money lent and borrowed with people, payslips and remittances, and the
fixed commitments view (docs/finance.md#lent-and-borrowed, #payslips, #sending-money-home,
#fixed-commitments).

Every record belongs to one tenant, one workspace and one person (``owner_user_id``), like the rest of
Finance. Amounts are ``Decimal`` rounded by ``money.quantize`` and sit next to their currency; nothing is
added across currencies. ANUM only records and advises: nothing here moves money.

* **Lent and borrowed** (``PersonLoan``): an interest-free loan (qard hasan) with a person, in either
  direction. What changed hands and every repayment are finance links (``person_loan`` dimension) on real
  ledger transactions. By the shared rule (``flows.py``) a transaction linked to a loan with a person is a
  transfer: lending is not spending and being repaid is not income.
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
class LoanFigures:
    repaid: Decimal
    outstanding: Decimal
    handed: Decimal  # what the linked transactions show changed hands
    status: LoanStatus
    days_until_due: int | None


def loan_figures(loan: PersonLoan, links: list, today: date) -> LoanFigures:
    """What is repaid (active repayment links in the loan's currency), what is left and the status."""
    repaid = sum((l.amount or Decimal(0) for l in links if l.target_id == loan.id and l.role == repaid_role(loan.direction)
                  and l.currency == loan.currency and str(getattr(l.status, "value", l.status)) == "active"), Decimal(0))
    handed = sum((l.amount or Decimal(0) for l in links if l.target_id == loan.id and l.role == handed_role(loan.direction)
                  and l.currency == loan.currency and str(getattr(l.status, "value", l.status)) == "active"), Decimal(0))
    outstanding = max(loan.amount - repaid, Decimal(0))
    days = (loan.due_on - today).days if loan.due_on else None
    status: LoanStatus
    if outstanding <= 0:
        status = "repaid"
    elif loan.closed_on is not None:
        status = "closed"
    elif days is not None and days < 0:
        status = "overdue"
    elif days is not None and days <= DUE_SOON_DAYS:
        status = "due_soon"
    else:
        status = "open"
    return LoanFigures(repaid=quantize(repaid, loan.currency), outstanding=quantize(outstanding, loan.currency),
                       handed=quantize(handed, loan.currency), status=status, days_until_due=days)


def reminder_due(loan: PersonLoan, figures: LoanFigures, today: date) -> Literal["due", "overdue"] | None:
    """Which reminder (if any) to send today: once ``remind_days_before`` the due date, and once when it is overdue."""
    if not loan.remind or loan.due_on is None or figures.status in ("repaid", "closed"):
        return None
    if today > loan.due_on:
        return "overdue" if loan.reminded_overdue_for != loan.due_on else None
    if (loan.due_on - today).days <= loan.remind_days_before and loan.reminded_for != loan.due_on:
        return "due"
    return None


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


def monthly_equivalent(amount: Decimal, cadence: Cadence) -> Decimal | None:
    if cadence == Cadence.MONTHLY:
        return amount
    if cadence == Cadence.WEEKLY:
        return amount * 52 / 12
    if cadence == Cadence.QUARTERLY:
        return amount / 3
    if cadence == Cadence.YEARLY:
        return amount / 12
    return None  # a one-off bill is not monthly


def _bill_kind(bill: Bill) -> CommitmentKind:
    if bill.category in (Category.HOUSING, Category.ROOM_RENT):
        return "rent"
    if bill.category == Category.SUBSCRIPTIONS:
        return "subscription"
    return "bill"


def _key(text: str) -> str:
    from .obligations import _name

    return _name(text)


def fixed_commitments(bills: list[Bill], debts: list[Debt], installments: list[InstallmentStatementEntry], remittances: list[Remittance],
                      today: date) -> tuple[list[Commitment], list[Commitment]]:
    """What goes out every month, and what was left out so it is not counted twice (with the reason).

    * Active bills by their monthly equivalent (weekly x 52 / 12, quarterly / 3, yearly / 12); a one-off bill is left out.
    * Installment plans (Tamara, Tabby, ...): this month's unpaid installments per order; an order whose merchant or
      provider is also saved as a bill is the bill.
    * Debt minimums (at most what is owed); a minimum also saved as a bill is the bill (``obligations.bill_for_debt``).
    * Regular remittances: the newest per recipient and currency, amount plus fee; one also saved as a bill is the bill.
    """
    from .obligations import bill_for_debt

    counted: list[Commitment] = []
    skipped: list[Commitment] = []
    active = [b for b in bills if b.active]
    bill_names = {(_key(b.name), b.currency) for b in active}
    for bill in active:
        monthly = monthly_equivalent(bill.amount, bill.cadence)
        row = Commitment(kind=_bill_kind(bill), name=bill.name, currency=bill.currency,
                         monthly=quantize(monthly, bill.currency) if monthly is not None else bill.amount, source="bill", ref_id=bill.id,
                         note=None if bill.cadence == Cadence.MONTHLY else bill.cadence.value)
        (counted if monthly is not None else skipped).append(row)
    month = today.strftime("%Y-%m")
    orders: dict[tuple[str, str, str], list[InstallmentStatementEntry]] = {}
    for entry in installments:
        if entry.due_on.strftime("%Y-%m") == month and entry.source_status != "paid":
            orders.setdefault((entry.provider, entry.order_id, entry.currency), []).append(entry)
    for (provider, order_id, currency), entries in sorted(orders.items()):
        merchant = entries[0].merchant
        row = Commitment(kind="installment", name=f"{provider}: {merchant}", currency=currency,
                         monthly=quantize(sum((e.amount for e in entries), Decimal(0)), currency), source="installment", ref_id=f"{provider}:{order_id}")
        if (_key(merchant), currency) in bill_names or (_key(provider), currency) in bill_names:
            skipped.append(Commitment(**{**row.__dict__, "note": "in_bill"}))
        else:
            counted.append(row)
    for debt in debts:
        if debt.balance <= 0 or debt.minimum_payment <= 0:
            continue
        kind: CommitmentKind = "card_minimum" if debt.kind == DebtKind.CARD else "loan_minimum"
        row = Commitment(kind=kind, name=debt.name, currency=debt.currency, monthly=min(debt.minimum_payment, debt.balance), source="debt",
                         ref_id=debt.id)
        if bill_for_debt(debt, active) is not None:
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
    "Commitment", "Deduction", "DeductionKind", "LoanDirection", "LoanFigures", "PAYSLIP_DIMENSION", "PAYSLIP_TARGET", "PERSONAL_DIMENSIONS",
    "PERSON_LOAN_DIMENSION", "PERSON_LOAN_TARGET", "PayMonth", "Payslip", "PayslipFigures", "PersonLoan", "ProviderCost", "REMITTANCE_DIMENSION",
    "REMITTANCE_TARGET", "RemitMonth", "Remittance", "commitment_totals", "effective_rate", "fee_percent", "fixed_commitments", "handed_role",
    "loan_figures", "monthly_equivalent", "pay_months", "payslip_figures", "provider_costs", "remit_months", "reminder_due", "repaid_role",
]
