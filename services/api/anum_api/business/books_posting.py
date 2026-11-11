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
"The rate it was booked at" is the rate on the posted entry of the invoice, bill or adjustment itself
(``Leg.booked_by``), never the rate table asked again for that day: a closer rate recorded after the
document was posted would differ, and the receivable or payable would never clear.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from ..finance.money import quantize
from .books_chart import REVENUE_KEYS
from .books_models import AdjustmentKind, BillStatus, Direction, IntercompanyKind, LedgerAccount, PaymentKind, Section
from .books_report import Book, Converter, Inputs, _months, _spread, month_start
from .tax_rules import split

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
    tax_code: str | None = None  # docs/business-tax.md#posting: the code of a revenue, cost or VAT leg
    # Settles a monetary balance in another currency at its carrying rate (what it is booked at), so the settlement's
    # exchange difference is realised (loans, members' balances; docs/business-books.md#period-end-revaluation).
    carrying: bool = False
    # The event key of the entry that booked what this leg reverses, releases or settles. In another currency the leg
    # converts at the rate that entry was posted with, never at a rate read again for the same day. Not part of the
    # fingerprint: the facts are the same, so nothing posted is posted again.
    booked_by: str | None = None
    # With ``booked_by``: pays the document's receivable or payable. It leaves at what the document is still booked at
    # (in proportion for a part payment; the last payment takes what is left, so the balance clears to zero).
    settles: bool = False


@dataclass
class Proposal:
    event_key: str
    source_kind: str
    source_id: str | None
    event_date: date
    description: str
    legs: list[Leg]
    fx_balance: bool = False  # a settlement: the difference between the rates goes to exchange gains and losses
    # The base-currency difference goes to a gain account (credit) or a loss account (debit): a disposal's gain or loss.
    gain_loss: tuple[str, str] | None = None

    @property
    def fingerprint(self) -> str:
        """The event's facts, without the rate: a posted entry keeps its rate when a better one is recorded later."""
        facts = [self.source_kind, self.source_id or "", self.event_date.isoformat(), str(self.fx_balance)]
        for leg in self.legs:
            facts.append("|".join(str(value) for value in (
                leg.account, leg.debit, leg.currency, format(leg.amount.normalize(), "f"), leg.rate_on.isoformat(), leg.category, leg.project_id,
                leg.cost_centre_id, leg.customer_id, leg.counterparty_workspace_id, leg.pair_id, leg.flow))
                         # A leg without a tax code keeps the fingerprint it had before tax codes, so nothing posted is re-posted.
                         + (f"|tc={leg.tax_code}" if leg.tax_code else "") + ("|carry" if leg.carrying else ""))
        if self.gain_loss:
            facts.append("gl=" + "/".join(self.gain_loss))
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
    tax_code: str | None = None


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


def _code(line: Any) -> str | None:
    return getattr(line, "tax_code", None)


def _taxes_by_code(invoice: Any) -> dict[str, Decimal]:
    """The invoice's tax by line tax code ("" for lines without one). Without codes: one figure, the invoice's tax, as before."""
    if not any(_code(line) for line in invoice.lines):
        return {"": Decimal(invoice.tax)}
    out: dict[str, Decimal] = defaultdict(Decimal)
    for line in invoice.lines:
        out[_code(line) or ""] += Decimal(line.tax)
    return dict(out)


def _credit_split(invoice: Any, subtotal: Decimal, tax: Decimal) -> list[tuple[str | None, Decimal, Decimal]]:
    """(tax code, net, tax) of a credit note: in the invoice's own proportion by code; one uncoded part when it has no codes."""
    if not any(_code(line) for line in invoice.lines):
        return [(None, subtotal, tax)]
    nets: dict[str, Decimal] = defaultdict(Decimal)
    for line in invoice.lines:
        nets[_code(line) or ""] += Decimal(line.net)
    taxes = _taxes_by_code(invoice)
    codes = sorted(nets)
    net_parts = split(subtotal, [nets[code] for code in codes])
    tax_parts = split(tax, [taxes.get(code, ZERO) for code in codes])
    return [(code or None, net, part) for code, net, part in zip(codes, net_parts, tax_parts, strict=True)]


def propose(inputs: Inputs, chart: Chart, today: date | None = None) -> list[Proposal]:
    """Every posting event of one business, in a stable order. Dates in the future are included; the caller posts only
    those dated on or before today. Loan interest accrued at month ends is proposed up to ``today`` only (it depends on the
    repayments made by then)."""
    book = Book(inputs)
    out: list[Proposal] = []
    vendors = inputs.vendors
    base = inputs.settings.base_currency
    registered = {(asset.source_kind, asset.source_id) for asset in inputs.assets}

    for invoice in sorted(book.invoices.values(), key=lambda item: item.id):
        if invoice.issue_date is None or Decimal(invoice.total) <= 0:
            continue
        currency, day = _cur(invoice), invoice.issue_date
        dims = {"project_id": invoice.project_id, "customer_id": invoice.account_id}
        nets: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        taxes = _taxes_by_code(invoice)
        for line in invoice.lines:
            nets[(_kind(line), _code(line) or "")] += Decimal(line.net)
        legs = [Leg("key:ar", True, currency, Decimal(invoice.total), day, absorb=True, **dims)]
        for (kind, code), net in sorted(nets.items()):
            if net > 0:
                legs.append(Leg(f"key:{REVENUE_KEYS.get(kind, 'revenue_other')}", False, currency, net, day, tax_code=code or None, **dims))
            elif net < 0:
                legs.append(Leg(f"key:{REVENUE_KEYS.get(kind, 'revenue_other')}", True, currency, -net, day, tax_code=code or None, **dims))
        for code, tax in sorted(taxes.items()):
            if tax > 0:
                legs.append(Leg("key:tax_payable", False, currency, tax, day, tax_code=code or None, **dims))
        out.append(Proposal(f"invoice:{invoice.id}", "invoice", invoice.id, day, f"Invoice {invoice.number or invoice.id} issued", legs))

    for note in sorted(book.credit_notes, key=lambda item: item.id):
        invoice = book.invoices[note.invoice_id]
        if invoice.issue_date is None:
            continue
        currency, booked = _cur(note), invoice.issue_date
        dims = {"project_id": invoice.project_id, "customer_id": invoice.account_id, "booked_by": f"invoice:{invoice.id}"}
        legs = [Leg("key:ar", False, currency, Decimal(note.amount), booked, absorb=True, **dims)]
        for code, net, tax in _credit_split(invoice, Decimal(note.subtotal), Decimal(note.tax)):
            if net > 0:
                legs.append(Leg("key:credit_notes", True, currency, net, booked, tax_code=code, **dims))
            if tax > 0:
                legs.append(Leg("key:tax_payable", True, currency, tax, booked, tax_code=code, **dims))
        out.append(Proposal(f"credit_note:{note.id}", "credit_note", note.id, note.issue_date, f"Credit note {note.number or note.id}", legs))

    for payment in sorted(book.payments, key=lambda item: item.id):
        invoice = book.invoices[payment.invoice_id]
        if invoice.issue_date is None or Decimal(payment.amount) <= 0:
            continue
        currency = _cur(invoice)
        dims = {"project_id": invoice.project_id, "customer_id": invoice.account_id}
        legs = [Leg("key:bank", True, currency, Decimal(payment.amount), payment.paid_on, flow="customers_paid", **dims),
                Leg("key:ar", False, currency, Decimal(payment.amount), invoice.issue_date, absorb=True, booked_by=f"invoice:{invoice.id}",
                    settles=True, **dims)]
        out.append(Proposal(f"invoice_payment:{payment.id}", "invoice_payment", payment.id, payment.paid_on,
                            f"Payment for invoice {invoice.number or invoice.id}", legs, fx_balance=True))

    for refund in sorted(book.refunds, key=lambda item: item.id):
        # A refund made in the card provider's dashboard: the money leaves the bank and the customer owes it again,
        # until a credit note takes it off (docs/business.md#refunds-made-in-the-provider-dashboard).
        invoice = book.invoices[refund.invoice_id]
        if invoice.issue_date is None:
            continue
        currency = _cur(invoice)
        dims = {"project_id": invoice.project_id, "customer_id": invoice.account_id}
        legs = [Leg("key:ar", True, currency, Decimal(refund.amount), invoice.issue_date, absorb=True, booked_by=f"invoice:{invoice.id}",
                    settles=True, **dims),
                Leg("key:bank", False, currency, Decimal(refund.amount), refund.refunded_on, flow="customers_paid", **dims)]
        out.append(Proposal(f"invoice_refund:{refund.id}", "invoice_refund", refund.id, refund.refunded_on,
                            f"Card refund on invoice {invoice.number or invoice.id}", legs, fx_balance=True))

    for bill in sorted(book.bills.values(), key=lambda item: item.id):
        if bill.status != BillStatus.OPEN:
            continue
        dims = {"project_id": bill.project_id, "cost_centre_id": bill.cost_centre_id}
        name = f"Bill {bill.reference or bill.id[-6:]} from {vendors.get(bill.vendor_id, 'a vendor')}"
        legs = [Leg("key:ap", False, bill.currency, bill.total, bill.bill_date, absorb=True, **dims)]
        code = getattr(bill, "tax_code", None)
        if bill.subtotal > 0:
            target = "key:fixed_assets" if bill.section == Section.CAPITAL else chart.expense(bill.section.value, bill.category)
            legs.append(Leg(target, True, bill.currency, bill.subtotal, bill.bill_date, category=bill.category, tax_code=code, **dims))
        if bill.tax > 0:
            legs.append(Leg("key:tax_receivable", True, bill.currency, bill.tax, bill.bill_date, tax_code=code, **dims))
        self_assessed = Decimal(getattr(bill, "reverse_charge_tax", 0) or 0)
        if self_assessed > 0:
            # Reverse charge: the business accounts for the VAT itself, as output tax and, recoverable, as input tax.
            legs.append(Leg("key:tax_receivable", True, bill.currency, self_assessed, bill.bill_date, tax_code=code, **dims))
            legs.append(Leg("key:tax_payable", False, bill.currency, self_assessed, bill.bill_date, tax_code=code, **dims))
        out.append(Proposal(f"bill:{bill.id}", "bill", bill.id, bill.bill_date, name, legs))
        if bill.section == Section.CAPITAL and bill.useful_life_months and bill.subtotal > 0 and ("bill", bill.id) not in registered:
            parts = _spread(bill.subtotal, bill.currency, bill.useful_life_months)
            for month, part in zip(_months(month_start(bill.bill_date), bill.useful_life_months), parts, strict=True):
                if part > 0:
                    out.append(Proposal(f"bill:{bill.id}:dep:{_month_key(month)}", "bill", bill.id, month, f"Depreciation, {name}", [
                        Leg("key:depreciation", True, bill.currency, part, bill.bill_date, category="depreciation", booked_by=f"bill:{bill.id}", **dims),
                        Leg("key:accumulated_depreciation", False, bill.currency, part, bill.bill_date, absorb=True, booked_by=f"bill:{bill.id}",
                            **dims)]))

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
                            f"Payment of bill {bill.reference or bill.id[-6:]}", [Leg("key:ap", True, bill.currency, payment.amount, bill.bill_date, absorb=True, booked_by=f"bill:{bill.id}", settles=True, **dims),
                             credit],
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
        out.extend(_adjustment(item, chart, base, ("adjustment", item.id) in registered))

    for item in sorted(book.intercompany, key=lambda row: row.id):
        out.append(_intercompany(item, chart))

    out.extend(_assets(book, inputs, chart))
    out.extend(_loans(book, inputs, chart, base, today))
    return out


def _asset_source(book: Book, asset: Any) -> tuple[Any, dict[str, Any], str] | None:
    """The live source of an asset, its dimensions and its event key prefix; None once the source is voided."""
    if asset.source_kind == "bill":
        bill = book.bills.get(asset.source_id)
        if bill is None or bill.section != Section.CAPITAL:
            return None
        return bill, {"project_id": bill.project_id, "cost_centre_id": bill.cost_centre_id}, f"bill:{bill.id}"
    item = next((row for row in book.adjustments if row.id == asset.source_id and row.kind == AdjustmentKind.CAPITAL_ITEM), None)
    if item is None:
        return None
    return item, {"project_id": item.project_id, "cost_centre_id": item.cost_centre_id}, f"adjustment:{item.id}"


def _assets(book: Book, inputs: Inputs, chart: Chart) -> list[Proposal]:
    """The register's depreciation (under the source's event keys: an unchanged straight-line asset posts exactly what its
    source posted before), approved impairments and disposals (docs/business-books.md#fixed-asset-register)."""
    from .books_assets import approved, disposal, schedule

    out: list[Proposal] = []
    for asset in sorted(inputs.assets, key=lambda row: row.id):
        found = _asset_source(book, asset)
        if found is None:
            continue
        _, dims, prefix = found
        held = {**dims, "booked_by": prefix}  # at the rate the purchase was posted with (historical cost)
        kind, source_id = asset.source_kind, asset.source_id
        cur, acquired = asset.currency, asset.acquired_on
        name = f"{asset.name}"
        for row in schedule(asset, inputs.asset_impairments):
            out.append(Proposal(f"{prefix}:dep:{_month_key(row.month)}", kind, source_id, row.month, f"Depreciation, {name}", [
                Leg("key:depreciation", True, cur, row.amount, acquired, category="depreciation", **held),
                Leg("key:accumulated_depreciation", False, cur, row.amount, acquired, absorb=True, **held)]))
        for loss in approved(inputs.asset_impairments, asset.id):
            if Decimal(loss.amount) <= 0:
                continue
            out.append(Proposal(f"asset:{asset.id}:impairment:{loss.id}", "asset", asset.id, loss.entry_date, f"Impairment, {name}", [
                Leg("key:impairment", True, cur, Decimal(loss.amount), acquired, category="impairment", **held),
                Leg("key:accumulated_depreciation", False, cur, Decimal(loss.amount), acquired, absorb=True, **held)]))
        sold = disposal(asset, inputs.asset_impairments)
        if sold is not None and asset.disposed_on is not None:
            legs = [Leg("key:fixed_assets", False, cur, sold["cost"], acquired, **held)]
            taken = sold["depreciation"] + sold["impairment"]
            if taken > 0:
                legs.append(Leg("key:accumulated_depreciation", True, cur, taken, acquired, **held))
            if sold["proceeds"] > 0:
                legs.append(Leg("key:bank", True, cur, sold["proceeds"], asset.disposed_on, flow="asset_sales", **dims))
            label = "sold" if asset.disposal_kind == "sale" else "written off"
            out.append(Proposal(f"asset:{asset.id}:disposal", "asset", asset.id, asset.disposed_on, f"{name} {label}", legs,
                                gain_loss=("key:disposal_gain", "key:disposal_loss")))
    return out


def _loans(book: Book, inputs: Inputs, chart: Chart, base: str, today: date | None) -> list[Proposal]:
    """Loans from the loan schedules: the money received, each repayment split into principal and interest, and interest
    accrued and unpaid at each month end (reversed the next day). A repayment matched to a bill or a loan repayment adjustment
    only moves what that record already posted (docs/business-books.md#loan-schedules)."""
    from .books_loans import live, month_end_accruals, replay

    out: list[Proposal] = []
    adjustments = {row.id: row for row in book.adjustments}
    for loan in sorted(inputs.loans, key=lambda row: row.id):
        if loan.status != "active":
            continue
        cur = loan.currency
        foreign = cur != base
        if not (loan.received_adjustment_id and loan.received_adjustment_id in adjustments):
            out.append(Proposal(f"loan:{loan.id}", "loan", loan.id, loan.start_date, f"Loan received from {loan.lender}", [
                Leg("key:bank", True, cur, Decimal(loan.principal), loan.start_date, flow="loans_received"),
                Leg("key:loans", False, cur, Decimal(loan.principal), loan.start_date, absorb=True)]))
        repayments = [row for row in inputs.loan_repayments if row.loan_id == loan.id]
        _, splits = replay(loan, repayments)
        rows = {row.id: row for row in live(repayments, loan.id)}
        for split in splits:
            row = rows[split.repayment_id]  # type: ignore[index]
            day, key = split.paid_on, f"loan_repayment:{row.id}"
            label = f"Repayment to {loan.lender}"
            legs: list[Leg]
            if row.adjustment_id and row.adjustment_id in adjustments:
                # The adjustment posted the whole payment against the loan: move its interest to finance costs.
                if split.interest <= 0:
                    continue
                legs = [Leg("key:finance_costs", True, cur, split.interest, day, category="loan_interest"),
                        Leg("key:loans", False, cur, split.interest, day, absorb=True)]
                out.append(Proposal(key, "loan_repayment", row.id, day, f"{label}: interest part", legs))
                continue
            if row.bill_id and row.bill_id in book.bills:
                # The bill posted the payment as a cost: split it into the loan and its interest.
                bill = book.bills[row.bill_id]
                cost = chart.expense(bill.section.value, bill.category)
                legs = [Leg(cost, False, cur, split.amount, day, category=bill.category, absorb=True)]
                if split.principal > 0:
                    legs.append(Leg("key:loans", True, cur, split.principal, day))
                if split.interest > 0:
                    legs.append(Leg("key:finance_costs", True, cur, split.interest, day, category="loan_interest"))
                out.append(Proposal(key, "loan_repayment", row.id, day, f"{label}: bill split", legs))
                continue
            legs = []
            if split.principal > 0:
                legs.append(Leg("key:loans", True, cur, split.principal, loan.start_date if not foreign else day, carrying=foreign))
                legs.append(Leg("key:bank", False, cur, split.principal, day, flow="loans_repaid", absorb=not foreign))
            if split.interest > 0:
                legs.append(Leg("key:finance_costs", True, cur, split.interest, day, category="loan_interest"))
                legs.append(Leg("key:bank", False, cur, split.interest, day, flow="interest_paid", absorb=not foreign and split.principal <= 0))
            if legs:
                out.append(Proposal(key, "loan_repayment", row.id, day, label, legs, fx_balance=foreign))
        if today is None:
            continue
        for end, amount in month_end_accruals(loan, repayments, today):
            accrual_key = f"loan:{loan.id}:accrual:{_month_key(end)}"
            out.append(Proposal(accrual_key, "loan", loan.id, end, f"Interest accrued, {loan.lender}", [
                Leg("key:finance_costs", True, cur, amount, end, category="loan_interest"),
                Leg("key:interest_payable", False, cur, amount, end, absorb=True)]))
            following = date.fromordinal(end.toordinal() + 1)
            out.append(Proposal(f"{accrual_key}:reverse", "loan", loan.id, following, f"Interest accrued reversed, {loan.lender}", [
                Leg("key:interest_payable", True, cur, amount, end, absorb=True, booked_by=accrual_key),
                Leg("key:finance_costs", False, cur, amount, end, category="loan_interest", booked_by=accrual_key)]))
    return out


# Settling a balance in another currency: the balance leaves at its carrying rate and the difference is realised.
SETTLEMENTS = frozenset({AdjustmentKind.LOAN_REPAYMENT, AdjustmentKind.LOAN_INTEREST_PAID, AdjustmentKind.OWNER_REIMBURSEMENT})


def _adjustment(item: Any, chart: Chart, base: str = "", registered: bool = False) -> list[Proposal]:
    dims = {"project_id": item.project_id, "cost_centre_id": item.cost_centre_id}
    kind = item.kind
    cur, day, amount = item.currency, item.entry_date, item.amount
    label = kind.value.replace("_", " ").capitalize()
    key = f"adjustment:{item.id}"
    category = item.category or kind.value
    if kind in ADJUSTMENT_ACCOUNTS:
        debit, credit = ADJUSTMENT_ACCOUNTS[kind]
        flow = ADJUSTMENT_FLOWS.get(kind)
        settles = kind in SETTLEMENTS and bool(base) and cur != base
        return [Proposal(key, "adjustment", item.id, day, label, [
            Leg(f"key:{debit}", True, cur, amount, day, flow=flow if debit == "bank" else None,
                category="loan_interest" if debit == "finance_costs" else None, carrying=settles, **dims),
            Leg(f"key:{credit}", False, cur, amount, day, flow=flow if credit == "bank" else None, absorb=not settles, **dims)],
            fx_balance=settles)]
    expense = chart.expense(item.section.value if item.section else Section.OPERATING_EXPENSE.value, category)
    later = {**dims, "booked_by": key}  # what the entry schedules converts at the rate the entry itself was posted with
    if kind == AdjustmentKind.ACCRUAL:
        out = [Proposal(key, "adjustment", item.id, day, label, [
            Leg(expense, True, cur, amount, day, category=category, **dims), Leg("key:accruals", False, cur, amount, day, absorb=True, **dims)])]
        if item.reverse_on:
            out.append(Proposal(f"{key}:reverse", "adjustment", item.id, item.reverse_on, f"{label} reversed", [
                Leg("key:accruals", True, cur, amount, day, absorb=True, **later), Leg(expense, False, cur, amount, day, category=category, **later)]))
        return out
    if kind == AdjustmentKind.PREPAYMENT and item.start_month and item.months:
        out = [Proposal(key, "adjustment", item.id, day, label, [
            Leg("key:prepayments", True, cur, amount, day, absorb=True, **dims), Leg(expense, False, cur, amount, day, category=category, **dims)])]
        for month, part in zip(_months(item.start_month, item.months), _spread(amount, cur, item.months), strict=True):
            if part > 0:
                out.append(Proposal(f"{key}:release:{_month_key(month)}", "adjustment", item.id, month, f"{label} released", [
                    Leg(expense, True, cur, part, day, category=category, **later), Leg("key:prepayments", False, cur, part, day, absorb=True, **later)]))
        return out
    if kind == AdjustmentKind.CAPITAL_ITEM and item.start_month and item.months:
        # An asset recorded outside a bill: brought in by the owner (a capital bill is the record for one the business bought).
        out = [Proposal(key, "adjustment", item.id, day, label, [
            Leg("key:fixed_assets", True, cur, amount, day, **dims), Leg("key:capital", False, cur, amount, day, absorb=True, **dims)])]
        if registered:
            return out  # the fixed asset register depreciates it (books_posting._assets)
        for month, part in zip(_months(item.start_month, item.months), _spread(amount, cur, item.months), strict=True):
            if part > 0:
                out.append(Proposal(f"{key}:dep:{_month_key(month)}", "adjustment", item.id, month, f"Depreciation, {label.lower()}", [
                    Leg("key:depreciation", True, cur, part, day, category="depreciation", **later),
                    Leg("key:accumulated_depreciation", False, cur, part, day, absorb=True, **later)]))
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


@dataclass(frozen=True)
class CarryingRate:
    """The rate a balance is booked at (its base value over its original amount), shown on the line that settles it."""

    rate: Decimal
    as_of: date
    source: str = "Carrying rate of the balance"


Carrying = Callable[[str, str, date], "tuple[Decimal, Decimal] | None"]


def _carried(leg: Leg, proposal: Proposal, chart: Chart, base: str, carrying: Carrying | None) -> tuple[Decimal, CarryingRate] | None:
    """A settling leg's base value at the balance's carrying rate: (base value, rate), or None to use the day's rate."""
    if carrying is None or not leg.carrying or leg.currency == base:
        return None
    found = carrying(chart.resolve(leg.account), leg.currency, proposal.event_date)
    if found is None:
        return None
    original, booked = found
    if original == 0 or booked == 0 or (original > 0) != (booked > 0):
        return None
    value = abs(booked) if leg.amount >= abs(original) else quantize(leg.amount * booked / original, base)
    rate = (booked / original).quantize(Decimal("0.0000000001"))
    if rate <= 0 or value <= 0:
        return None
    return value, CarryingRate(rate, proposal.event_date)


class Booked(Protocol):
    """What the journal holds of a posted document, for the legs that reverse, release or settle it (``Leg.booked_by``)."""

    def rate(self, event_key: str, currency: str) -> CarryingRate | None:
        """The rate the live entry of ``event_key`` was posted with for ``currency`` (with its date and source), or None."""

    def open(self, event_key: str, account_id: str, currency: str) -> tuple[Decimal, Decimal] | None:
        """What is left of the document on an account: (original amount, base value), debit positive, counting its own
        entry and every live entry that reverses or settles it."""


def _booked(leg: Leg, chart: Chart, base: str, booked: Booked | None) -> tuple[Decimal, CarryingRate] | None:
    """A leg's base value at the rate its document was posted with: (base value, rate), or None to use the rate table (the
    document is not posted, or nothing reads the journal: a dry run)."""
    if booked is None or leg.booked_by is None or leg.currency == base:
        return None
    rate = booked.rate(leg.booked_by, leg.currency)
    if leg.settles:
        left = booked.open(leg.booked_by, chart.resolve(leg.account), leg.currency)
        # A debit leg pays a credit balance and the other way round; anything else (nothing left, paid twice) uses the rate.
        if left is not None and left[0] != 0 and left[1] != 0 and (left[0] > 0) == (left[1] > 0) and (left[0] < 0) == leg.debit \
                and leg.amount <= abs(left[0]):
            original, value = abs(left[0]), abs(left[1])
            part = value if leg.amount == original else quantize(leg.amount * value / original, base)
            shown = rate or CarryingRate((value / original).quantize(Decimal("0.0000000001")), leg.rate_on, "Booked rate of the document")
            if shown.rate > 0:
                return part, shown
    if rate is None:
        return None
    return quantize(leg.amount * rate.rate, base), rate


def realise(proposal: Proposal, converter: Converter, chart: Chart, carrying: Carrying | None = None,
            booked: Booked | None = None) -> list[Line] | Issue:
    """Base-currency lines that balance, or why the event cannot be posted (never a guessed rate). ``carrying`` reads what a
    balance in another currency is booked at, for a leg that settles it (``Leg.carrying``); ``booked`` reads what a posted
    document is booked at, for the legs that reverse, release or pay it (``Leg.booked_by``)."""
    base = converter.base
    converted: list[tuple[Leg, Decimal, Any]] = []
    for leg in proposal.legs:
        if leg.currency == base:
            converted.append((leg, leg.amount, None))
            continue
        carried = _carried(leg, proposal, chart, base, carrying) or _booked(leg, chart, base, booked)
        if carried is not None:
            converted.append((leg, carried[0], carried[1]))
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
        if proposal.gain_loss:
            # A disposal: the difference is the gain (credited) or the loss (debited), in the base currency.
            gain, loss = proposal.gain_loss
            if difference > 0:
                extra.append(Line(chart.resolve(gain), ZERO, difference, base, -difference, None, None, None, category="disposal"))
            else:
                extra.append(Line(chart.resolve(loss), -difference, ZERO, base, -difference, None, None, None, category="disposal"))
        elif proposal.fx_balance:
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
                        leg.category, leg.project_id, leg.cost_centre_id, leg.customer_id, leg.counterparty_workspace_id, leg.pair_id, leg.flow,
                        leg.tax_code))
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


__all__ = ["Booked", "Carrying", "CarryingRate", "Chart", "Issue", "Leg", "Line", "Proposal", "propose", "realise"]
