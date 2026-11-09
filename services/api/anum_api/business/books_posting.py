"""What each source document event posts to the journal: pure rules (docs/business-books.md#what-is-posted-from-what).

``propose`` turns the records ``books_service.gather`` reads into one :class:`Proposal` per event, each
with a stable ``event_key`` (``invoice:<id>``, ``bill_payment:<id>``, ``bill:<id>:dep:2026-03`` ...). The
same records always give the same proposals, so posting is deterministic and idempotent: the posting
run (``books_ledger.post``) posts a key once, and reverses and re-posts it only when the event's facts
change (its ``fingerprint``, which leaves out the exchange rate: a posted entry keeps the rate it was
posted with).

``realise`` converts a proposal into base-currency lines with the business's own dated rates
(``books_report.Converter``: on or before the day, no older than the settings allow; never an invented
rate). A line in another currency keeps its currency, signed original amount, rate, rate date and
rate source. Without a rate the event is not posted: it becomes an :class:`Issue` with the reason,
listed for the owner, and the reports say they are incomplete.

Exchange rates (IAS 21): a transaction converts at the rate of its own day. A settlement (a customer
or supplier payment) clears the receivable or payable at the rate it was booked at and books the
difference to exchange gains and losses. Non-monetary items (an asset's depreciation, the release of
a prepayment, a credit note reversing part of a sale) use the rate of the original transaction.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from ..finance.money import quantize
from .books_chart import REVENUE_KEYS
from .books_models import AdjustmentKind, BillStatus, Direction, IntercompanyKind, LedgerAccount, PaymentKind, Section
from .books_report import Book, Converter, Inputs, _months, _spread, month_start

ZERO = Decimal(0)
ADJUSTMENT_FLOWS = {
    AdjustmentKind.LOAN_RECEIVED: "loans_received", AdjustmentKind.LOAN_REPAYMENT: "loans_repaid", AdjustmentKind.LOAN_INTEREST: "interest_paid",
    AdjustmentKind.LOAN_INTEREST_PAID: "interest_paid", AdjustmentKind.OWNER_CONTRIBUTION: "owner_contributions",
    AdjustmentKind.OWNER_DRAWING: "owner_drawings", AdjustmentKind.OWNER_REIMBURSEMENT: "members_reimbursed",
}
# (debit account key, credit account key) of the adjustments that move money or debt, with "bank" for business cash.
ADJUSTMENT_ACCOUNTS = {
    AdjustmentKind.LOAN_RECEIVED: ("bank", "loans"), AdjustmentKind.LOAN_REPAYMENT: ("loans", "bank"),
    AdjustmentKind.LOAN_INTEREST: ("finance_costs", "bank"), AdjustmentKind.LOAN_INTEREST_ACCRUED: ("finance_costs", "interest_payable"),
    AdjustmentKind.LOAN_INTEREST_PAID: ("interest_payable", "bank"), AdjustmentKind.OWNER_CONTRIBUTION: ("bank", "capital"),
    AdjustmentKind.OWNER_DRAWING: ("drawings", "bank"), AdjustmentKind.OWNER_REIMBURSEMENT: ("owed_to_members", "bank"),
}
MEMBER_CREDIT = {"reimbursable": "owed_to_members", "owner_contribution": "capital"}


@dataclass(frozen=True)
class Leg:
    account: str  # "key:<system key>" or "id:<account id>"
    debit: bool
    currency: str
    amount: Decimal  # positive, in ``currency``
    rate_on: date  # the day whose rate converts it
    category: str | None = None
    project_id: str | None = None
    cost_centre_id: str | None = None
    customer_id: str | None = None
    counterparty_workspace_id: str | None = None
    pair_id: str | None = None
    flow: str | None = None
    absorb: bool = False  # takes the rounding of a conversion so the entry balances to the minor unit


@dataclass
class Proposal:
    event_key: str
    source_kind: str
    source_id: str | None
    event_date: date
    description: str
    legs: list[Leg]
    fx_balance: bool = False  # a settlement: the difference between the rates goes to exchange gains and losses

    @property
    def fingerprint(self) -> str:
        """The event's facts, without the rate: a posted entry keeps its rate when a better one is recorded later."""
        facts = [self.source_kind, self.source_id or "", self.event_date.isoformat(), str(self.fx_balance)]
        for leg in self.legs:
            facts.append("|".join(str(value) for value in (
                leg.account, leg.debit, leg.currency, format(leg.amount.normalize(), "f"), leg.rate_on.isoformat(), leg.category, leg.project_id,
                leg.cost_centre_id, leg.customer_id, leg.counterparty_workspace_id, leg.pair_id, leg.flow)))
        return hashlib.sha256("\x1f".join(facts).encode()).hexdigest()


@dataclass(frozen=True)
class Line:
    account_id: str
    debit: Decimal
    credit: Decimal
    currency: str
    amount: Decimal  # signed original amount
    rate: Decimal | None
    rate_date: date | None
    rate_source: str | None
    category: str | None = None
    project_id: str | None = None
    cost_centre_id: str | None = None
    customer_id: str | None = None
    counterparty_workspace_id: str | None = None
    pair_id: str | None = None
    flow: str | None = None


@dataclass(frozen=True)
class Issue:
    event_key: str
    source_kind: str
    source_id: str | None
    event_date: date
    reason: str  # missing_rate or too_small
    currency: str | None
    rate_date: date | None
    description: str


@dataclass
class Chart:
    """The accounts a business posts to: by system key, and the expense accounts bound to a category."""

    by_key: dict[str, str] = field(default_factory=dict)
    by_category: dict[str, tuple[str, str]] = field(default_factory=dict)  # category -> (account id, subtype)

    @classmethod
    def of(cls, accounts: list[LedgerAccount]) -> Chart:
        chart = cls()
        for account in accounts:
            if account.system_key:
                chart.by_key[account.system_key] = account.id
            if account.category and not account.archived:
                chart.by_category[account.category] = (account.id, account.subtype)
        return chart

    def resolve(self, ref: str) -> str:
        kind, _, value = ref.partition(":")
        return self.by_key[value] if kind == "key" else value

    def expense(self, section: str, category: str | None) -> str:
        """The account of a cost: the account bound to its category when that account is of the same kind, else the default."""
        default = "cost_of_sales" if section == Section.COST_OF_SALES.value else "operating"
        bound = self.by_category.get(category or "")
        if bound is not None and bound[1] == ("cost_of_sales" if default == "cost_of_sales" else "operating_expense"):
            return f"id:{bound[0]}"
        return f"key:{default}"


def _status(item: Any) -> str:
    value = getattr(item, "status", "")
    return getattr(value, "value", value)


def _cur(item: Any) -> str:
    value = item.currency
    return getattr(value, "value", value)


def _kind(line: Any) -> str:
    value = getattr(line, "kind", "other")
    return getattr(value, "value", value)


def _month_key(day: date) -> str:
    return day.isoformat()[:7]


def propose(inputs: Inputs, chart: Chart) -> list[Proposal]:
    """Every posting event of one business, in a stable order. Dates in the future are included; the caller posts only
    those dated on or before today."""
    book = Book(inputs)
    out: list[Proposal] = []
    vendors = inputs.vendors

    for invoice in sorted(book.invoices.values(), key=lambda item: item.id):
        if invoice.issue_date is None or Decimal(invoice.total) <= 0:
            continue
        currency, day = _cur(invoice), invoice.issue_date
        dims = {"project_id": invoice.project_id, "customer_id": invoice.account_id}
        nets: dict[str, Decimal] = defaultdict(Decimal)
        for line in invoice.lines:
            nets[_kind(line)] += Decimal(line.net)
        legs = [Leg("key:ar", True, currency, Decimal(invoice.total), day, absorb=True, **dims)]
        for kind, net in sorted(nets.items()):
            if net > 0:
                legs.append(Leg(f"key:{REVENUE_KEYS.get(kind, 'revenue_other')}", False, currency, net, day, **dims))
            elif net < 0:
                legs.append(Leg(f"key:{REVENUE_KEYS.get(kind, 'revenue_other')}", True, currency, -net, day, **dims))
        if Decimal(invoice.tax) > 0:
            legs.append(Leg("key:tax_payable", False, currency, Decimal(invoice.tax), day, **dims))
        out.append(Proposal(f"invoice:{invoice.id}", "invoice", invoice.id, day, f"Invoice {invoice.number or invoice.id} issued", legs))

    for note in sorted(book.credit_notes, key=lambda item: item.id):
        invoice = book.invoices[note.invoice_id]
        if invoice.issue_date is None:
            continue
        currency, booked = _cur(note), invoice.issue_date
        dims = {"project_id": invoice.project_id, "customer_id": invoice.account_id}
        legs = [Leg("key:ar", False, currency, Decimal(note.amount), booked, absorb=True, **dims)]
        if Decimal(note.subtotal) > 0:
            legs.append(Leg("key:credit_notes", True, currency, Decimal(note.subtotal), booked, **dims))
        if Decimal(note.tax) > 0:
            legs.append(Leg("key:tax_payable", True, currency, Decimal(note.tax), booked, **dims))
        out.append(Proposal(f"credit_note:{note.id}", "credit_note", note.id, note.issue_date, f"Credit note {note.number or note.id}", legs))

    for payment in sorted(book.payments, key=lambda item: item.id):
        invoice = book.invoices[payment.invoice_id]
        if invoice.issue_date is None or Decimal(payment.amount) <= 0:
            continue
        currency = _cur(invoice)
        dims = {"project_id": invoice.project_id, "customer_id": invoice.account_id}
        legs = [Leg("key:bank", True, currency, Decimal(payment.amount), payment.paid_on, flow="customers_paid", **dims),
                Leg("key:ar", False, currency, Decimal(payment.amount), invoice.issue_date, absorb=True, **dims)]
        out.append(Proposal(f"invoice_payment:{payment.id}", "invoice_payment", payment.id, payment.paid_on,
                            f"Payment for invoice {invoice.number or invoice.id}", legs, fx_balance=True))

    for bill in sorted(book.bills.values(), key=lambda item: item.id):
        if bill.status != BillStatus.OPEN:
            continue
        dims = {"project_id": bill.project_id, "cost_centre_id": bill.cost_centre_id}
        name = f"Bill {bill.reference or bill.id[-6:]} from {vendors.get(bill.vendor_id, 'a vendor')}"
        legs = [Leg("key:ap", False, bill.currency, bill.total, bill.bill_date, absorb=True, **dims)]
        if bill.subtotal > 0:
            target = "key:fixed_assets" if bill.section == Section.CAPITAL else chart.expense(bill.section.value, bill.category)
            legs.append(Leg(target, True, bill.currency, bill.subtotal, bill.bill_date, category=bill.category, **dims))
        if bill.tax > 0:
            legs.append(Leg("key:tax_receivable", True, bill.currency, bill.tax, bill.bill_date, **dims))
        out.append(Proposal(f"bill:{bill.id}", "bill", bill.id, bill.bill_date, name, legs))
        if bill.section == Section.CAPITAL and bill.useful_life_months and bill.subtotal > 0:
            parts = _spread(bill.subtotal, bill.currency, bill.useful_life_months)
            for month, part in zip(_months(month_start(bill.bill_date), bill.useful_life_months), parts, strict=True):
                if part > 0:
                    out.append(Proposal(f"bill:{bill.id}:dep:{_month_key(month)}", "bill", bill.id, month, f"Depreciation, {name}", [
                        Leg("key:depreciation", True, bill.currency, part, bill.bill_date, category="depreciation", **dims),
                        Leg("key:accumulated_depreciation", False, bill.currency, part, bill.bill_date, absorb=True, **dims)]))

    for payment in sorted(book.bill_payments, key=lambda item: item.id):
        bill = book.bills[payment.bill_id]
        dims = {"project_id": bill.project_id, "cost_centre_id": bill.cost_centre_id}
        if payment.kind == PaymentKind.ALLOCATION:
            allocation = book.live_allocations[payment.allocation_id]
            credit = Leg(f"key:{MEMBER_CREDIT.get(allocation.treatment, 'owed_to_members')}", False, bill.currency, payment.amount, payment.paid_on, **dims)
        else:
            credit = Leg("key:bank", False, bill.currency, payment.amount, payment.paid_on,
                         flow="capital_spend" if bill.section == Section.CAPITAL else "suppliers_paid", **dims)
        out.append(Proposal(f"bill_payment:{payment.id}", "bill_payment", payment.id, payment.paid_on,
                            f"Payment of bill {bill.reference or bill.id[-6:]}", [Leg("key:ap", True, bill.currency, payment.amount, bill.bill_date, absorb=True, **dims), credit],
                            fx_balance=True))

    for allocation in sorted(book.live_allocations.values(), key=lambda item: item.id):
        if allocation.id in book.paying:
            continue  # it is that bill's payment, posted with it
        section = book.allocation_section(allocation)
        if section == "excluded":
            continue
        tag = inputs.tags.get(allocation.id)
        amount = Decimal(allocation.amount)
        if amount <= 0:
            continue
        dims = {"project_id": allocation.project_id, "cost_centre_id": tag.cost_centre_id if tag else None}
        target = "key:fixed_assets" if section == Section.CAPITAL.value else chart.expense(section, allocation.category)
        out.append(Proposal(f"allocation:{allocation.id}", "allocation", allocation.id, allocation.posted_on,
                            f"Paid by a member, #{allocation.source_ref}", [
                                Leg(target, True, allocation.currency, amount, allocation.posted_on, category=allocation.category, **dims),
                                Leg(f"key:{MEMBER_CREDIT.get(allocation.treatment, 'owed_to_members')}", False, allocation.currency, amount,
                                    allocation.posted_on, absorb=True, **dims)]))

    for item in sorted(book.adjustments, key=lambda row: row.id):
        out.extend(_adjustment(item, chart))

    for item in sorted(book.intercompany, key=lambda row: row.id):
        out.append(_intercompany(item, chart))
    return out


def _adjustment(item: Any, chart: Chart) -> list[Proposal]:
    dims = {"project_id": item.project_id, "cost_centre_id": item.cost_centre_id}
    kind = item.kind
    cur, day, amount = item.currency, item.entry_date, item.amount
    label = kind.value.replace("_", " ").capitalize()
    key = f"adjustment:{item.id}"
    category = item.category or kind.value
    if kind in ADJUSTMENT_ACCOUNTS:
        debit, credit = ADJUSTMENT_ACCOUNTS[kind]
        flow = ADJUSTMENT_FLOWS.get(kind)
        return [Proposal(key, "adjustment", item.id, day, label, [
            Leg(f"key:{debit}", True, cur, amount, day, flow=flow if debit == "bank" else None,
                category="loan_interest" if debit == "finance_costs" else None, **dims),
            Leg(f"key:{credit}", False, cur, amount, day, flow=flow if credit == "bank" else None, absorb=True, **dims)])]
    expense = chart.expense(item.section.value if item.section else Section.OPERATING_EXPENSE.value, category)
    if kind == AdjustmentKind.ACCRUAL:
        out = [Proposal(key, "adjustment", item.id, day, label, [
            Leg(expense, True, cur, amount, day, category=category, **dims), Leg("key:accruals", False, cur, amount, day, absorb=True, **dims)])]
        if item.reverse_on:
            out.append(Proposal(f"{key}:reverse", "adjustment", item.id, item.reverse_on, f"{label} reversed", [
                Leg("key:accruals", True, cur, amount, day, absorb=True, **dims), Leg(expense, False, cur, amount, day, category=category, **dims)]))
        return out
    if kind == AdjustmentKind.PREPAYMENT and item.start_month and item.months:
        out = [Proposal(key, "adjustment", item.id, day, label, [
            Leg("key:prepayments", True, cur, amount, day, absorb=True, **dims), Leg(expense, False, cur, amount, day, category=category, **dims)])]
        for month, part in zip(_months(item.start_month, item.months), _spread(amount, cur, item.months), strict=True):
            if part > 0:
                out.append(Proposal(f"{key}:release:{_month_key(month)}", "adjustment", item.id, month, f"{label} released", [
                    Leg(expense, True, cur, part, day, category=category, **dims), Leg("key:prepayments", False, cur, part, day, absorb=True, **dims)]))
        return out
    if kind == AdjustmentKind.CAPITAL_ITEM and item.start_month and item.months:
        # An asset recorded outside a bill: brought in by the owner (a capital bill is the record for one the business bought).
        out = [Proposal(key, "adjustment", item.id, day, label, [
            Leg("key:fixed_assets", True, cur, amount, day, **dims), Leg("key:capital", False, cur, amount, day, absorb=True, **dims)])]
        for month, part in zip(_months(item.start_month, item.months), _spread(amount, cur, item.months), strict=True):
            if part > 0:
                out.append(Proposal(f"{key}:dep:{_month_key(month)}", "adjustment", item.id, month, f"Depreciation, {label.lower()}", [
                    Leg("key:depreciation", True, cur, part, day, category="depreciation", **dims),
                    Leg("key:accumulated_depreciation", False, cur, part, day, absorb=True, **dims)]))
        return out
    return []


def _intercompany(item: Any, chart: Chart) -> Proposal:
    dims = {"counterparty_workspace_id": item.counterparty_workspace_id, "pair_id": item.pair_id}
    cur, day, amount = item.currency, item.entry_date, item.amount
    receivable = item.direction == Direction.RECEIVABLE
    if item.kind == IntercompanyKind.RECHARGE:
        legs = ([Leg("key:ic_receivable", True, cur, amount, day, absorb=True, **dims), Leg("key:other_income", False, cur, amount, day, category="intercompany", **dims)]
                if receivable else
                [Leg(chart.expense(Section.OPERATING_EXPENSE.value, "intercompany"), True, cur, amount, day, category="intercompany", **dims),
                 Leg("key:ic_payable", False, cur, amount, day, absorb=True, **dims)])
    elif item.kind == IntercompanyKind.LOAN:
        legs = ([Leg("key:ic_receivable", True, cur, amount, day, absorb=True, **dims), Leg("key:bank", False, cur, amount, day, flow="intercompany_lent", **dims)]
                if receivable else
                [Leg("key:bank", True, cur, amount, day, flow="intercompany_borrowed", **dims), Leg("key:ic_payable", False, cur, amount, day, absorb=True, **dims)])
    else:
        legs = ([Leg("key:bank", True, cur, amount, day, flow="intercompany_in", **dims), Leg("key:ic_receivable", False, cur, amount, day, absorb=True, **dims)]
                if receivable else
                [Leg("key:ic_payable", True, cur, amount, day, absorb=True, **dims), Leg("key:bank", False, cur, amount, day, flow="intercompany_out", **dims)])
    return Proposal(f"intercompany:{item.id}", "intercompany", item.id, day, f"Intercompany {item.kind.value} with {item.counterparty_name or 'a related business'}", legs)


def realise(proposal: Proposal, converter: Converter, chart: Chart) -> list[Line] | Issue:
    """Base-currency lines that balance, or why the event cannot be posted (never a guessed rate)."""
    base = converter.base
    converted: list[tuple[Leg, Decimal, Any]] = []
    for leg in proposal.legs:
        if leg.currency == base:
            converted.append((leg, leg.amount, None))
            continue
        path = converter.path(leg.currency, leg.rate_on)
        if path is None:
            return Issue(proposal.event_key, proposal.source_kind, proposal.source_id, proposal.event_date, "missing_rate", leg.currency, leg.rate_on,
                         proposal.description)
        converted.append((leg, quantize(leg.amount * path.rate, base), path))
    lines: list[dict[str, Any]] = []
    for leg, value, path in converted:
        lines.append({"leg": leg, "value": value, "path": path})
    difference = sum((row["value"] if row["leg"].debit else -row["value"] for row in lines), ZERO)
    extra: list[Line] = []
    if difference != 0:
        if proposal.fx_balance:
            # A settlement at another rate than the booking: a gain when more was debited than credited, a loss otherwise.
            account = chart.resolve("key:fx")
            extra.append(Line(account, ZERO if difference > 0 else -difference, difference if difference > 0 else ZERO, base,
                              -difference, None, None, None, category="fx"))
        else:
            target = next((row for row in lines if row["leg"].absorb), lines[0])
            target["value"] = target["value"] - difference if target["leg"].debit else target["value"] + difference
    out: list[Line] = []
    for row in lines:
        leg, value, path = row["leg"], row["value"], row["path"]
        if value <= 0:
            if value == 0 and leg.amount > 0 and leg.currency != base:
                continue  # converts to less than the base currency's minor unit
            return Issue(proposal.event_key, proposal.source_kind, proposal.source_id, proposal.event_date, "too_small", leg.currency, leg.rate_on,
                         proposal.description)
        signed = leg.amount if leg.debit else -leg.amount
        out.append(Line(chart.resolve(leg.account), value if leg.debit else ZERO, ZERO if leg.debit else value, leg.currency, signed,
                        path.rate if path is not None else None, path.as_of if path is not None else None, path.source if path is not None else None,
                        leg.category, leg.project_id, leg.cost_centre_id, leg.customer_id, leg.counterparty_workspace_id, leg.pair_id, leg.flow))
    out.extend(extra)
    if len(out) < 2 or not any(line.debit > 0 for line in out) or not any(line.credit > 0 for line in out):
        return Issue(proposal.event_key, proposal.source_kind, proposal.source_id, proposal.event_date, "too_small", None, None, proposal.description)
    if sum((line.debit - line.credit for line in out), ZERO) != 0:
        # Dropping a sub-minor-unit line left a rounding difference: the absorbing line takes it.
        gap = sum((line.debit - line.credit for line in out), ZERO)
        index = next((i for i, line in enumerate(out) if line.credit > gap or line.debit > -gap), 0)
        line = out[index]
        out[index] = Line(**{**line.__dict__, "debit": line.debit - gap if line.debit > 0 else ZERO, "credit": line.credit + gap if line.credit > 0 else ZERO})
    return out


__all__ = ["Chart", "Issue", "Leg", "Line", "Proposal", "propose", "realise"]
