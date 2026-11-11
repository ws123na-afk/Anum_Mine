"""The P&L, cash flow, balances and dashboard of a business: pure functions (docs/business-books.md#how-the-figures-are-made).

Nothing here reads or writes storage. ``books_service.gather`` hands over the records; the same
records always give the same figures. The rules:

* **One source per figure, nothing twice.** Revenue is the business's own invoices and credit
  notes (accrual) or the payments recorded on them (cash); never a second invoice. Costs are
  vendor bills and the allocation rows members made from their own Finance ledger. An allocation
  that pays a bill is that bill's payment, never a second cost. The ledger's other dimensions
  (vehicles, purchases, documents) are never read, so a payment that is also a vehicle cost or a
  purchase counts once here, through its business allocation.
* **Never add currencies.** A figure in another currency than the business's base currency is
  converted only with a rate the business recorded, dated on or before the figure's date and no
  older than the settings allow, and the rate, its date and its source are listed. Without one it
  stays apart, per currency, as "not converted".
* **A cash surplus is not profit.** The P&L (accrual or cash basis) and the cash flow (money in
  and out of the business, per currency) are separate, and money members paid from their own
  pocket is shown as theirs, not as business cash.
* **Tax is kept apart.** Output tax from invoices and credit notes, input tax from bills;
  allocation rows carry no tax split and are counted so the gap is visible.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..finance import fx
from ..finance.money import quantize
from .books_models import (
    RECURRENCE_MONTHS,
    Adjustment,
    AdjustmentKind,
    AllocationTag,
    Basis,
    Bill,
    BillPayment,
    BillStatus,
    BookSettings,
    Budget,
    Direction,
    Intercompany,
    IntercompanyKind,
    PaymentKind,
    Rate,
    RateOrigin,
    Section,
)
from .books_schedule import per_month as template_per_month
from .books_schedule import upcoming as template_upcoming

ZERO = Decimal(0)
REVENUE = "revenue"
COST_OF_SALES = "cost_of_sales"
OPERATING = "operating_expense"
FINANCE_COSTS = "finance_costs"
OTHER_INCOME = "other_income"
FX_DIFFERENCE = "fx_difference"
GROUPS = (REVENUE, COST_OF_SALES, OPERATING, OTHER_INCOME, FINANCE_COSTS, FX_DIFFERENCE)
ISSUED = frozenset({"sent", "paid"})
AGING = ((0, "current"), (30, "days_1_30"), (60, "days_31_60"), (90, "days_61_90"))


@dataclass
class Inputs:
    """Everything one business's figures are made from (read by ``books_service.gather``)."""

    settings: BookSettings
    invoices: list[Any] = field(default_factory=list)  # business.invoice_models.Invoice
    payments: list[Any] = field(default_factory=list)  # InvoicePayment
    # Refunds made in the card provider's dashboard (business.invoice_refunds): money given back on a payment.
    refunds: list[Any] = field(default_factory=list)  # InvoiceRefund
    credit_notes: list[Any] = field(default_factory=list)  # CreditNote
    allocations: list[Any] = field(default_factory=list)  # finance.links_store.BusinessAllocation
    tags: dict[str, AllocationTag] = field(default_factory=dict)
    bills: list[Bill] = field(default_factory=list)
    bill_payments: list[BillPayment] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    intercompany: list[Intercompany] = field(default_factory=list)
    budgets: list[Budget] = field(default_factory=list)
    rates: list[Rate] = field(default_factory=list)
    projects: dict[str, tuple[str, str]] = field(default_factory=dict)  # id -> (name, account id)
    accounts: dict[str, str] = field(default_factory=dict)  # id -> name
    vendors: dict[str, str] = field(default_factory=dict)  # id -> name
    cost_centres: dict[str, str] = field(default_factory=dict)  # id -> "code name"
    templates: list[Any] = field(default_factory=list)  # books_models.BillTemplate (recurring bills)
    # The fixed asset register and loan schedules (migration 0155): an asset replaces its source's depreciation.
    assets: list[Any] = field(default_factory=list)  # books_models.Asset
    asset_impairments: list[Any] = field(default_factory=list)  # books_models.AssetImpairment
    loans: list[Any] = field(default_factory=list)  # books_models.Loan
    loan_repayments: list[Any] = field(default_factory=list)  # books_models.LoanRepayment
    truncated: bool = False


@dataclass(frozen=True)
class Entry:
    """One P&L figure: positive for revenue and for costs; a credit note or a deferral is negative."""

    on: date
    group: str
    line: str
    currency: str
    amount: Decimal
    project_id: str | None = None
    account_id: str | None = None
    cost_centre_id: str | None = None
    category: str | None = None
    counterparty: str | None = None  # the other business of an intercompany entry (eliminated in a consolidation)


# -- dates -------------------------------------------------------------------------------------------


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def fiscal_year(today: date, start_month: int) -> tuple[date, date]:
    """The financial year that contains ``today``: its first and last day."""
    year = today.year if today.month >= start_month else today.year - 1
    first = date(year, start_month, 1)
    return first, add_months(first, 12) - timedelta(days=1)


def default_period(settings: BookSettings, today: date) -> tuple[date, date]:
    """The current financial year, whole: records dated in a member's own time zone (a day ahead of UTC) are never cut off."""
    return fiscal_year(today, settings.fiscal_year_start_month)


def _months(first: date, count: int) -> Iterable[date]:
    for index in range(count):
        yield add_months(first, index)


def _spread(amount: Decimal, currency: str, count: int) -> list[Decimal]:
    """``amount`` in ``count`` monthly parts that add up exactly (the last part takes the rounding)."""
    part = quantize(amount / count, currency)
    parts = [part] * (count - 1)
    return [*parts, amount - sum(parts, ZERO)]


# -- currency ---------------------------------------------------------------------------------------


class Converter:
    """Converts into the base currency with the business's own dated rates (``finance.fx``), never an invented one."""

    def __init__(self, rates: list[Rate], base: str, max_age_days: int) -> None:
        self.base = base
        self.max_age = max_age_days
        self.rates = prefer_manual(rates)
        self.cache: dict[tuple[str, date], Any] = {}
        self.used: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def path(self, currency: str, on: date) -> Any:
        key = (currency, on)
        if key not in self.cache:
            usable = [rate for rate in self.rates if rate.as_of <= on]
            self.cache[key] = fx.find_rate(usable, currency, self.base, on, self.max_age)  # type: ignore[arg-type]
        return self.cache[key]

    def convert(self, amount: Decimal, currency: str, on: date) -> Decimal | None:
        if currency == self.base:
            return amount
        path = self.path(currency, on)
        if path is None:
            return None
        converted = quantize(amount * path.rate, self.base)
        key = (currency, format(path.rate.normalize(), "f"), path.as_of.isoformat(), path.source)
        record = self.used.setdefault(key, {"from_currency": currency, "to_currency": self.base, "rate": key[1], "as_of": key[2],
                                            "source": path.source, "via": path.via, "figures": 0})
        record["figures"] += 1
        return converted

    def silent(self) -> Converter:
        """The same rates for a second pass (trend, budgets, breakdowns) without counting its conversions again."""
        other = Converter(self.rates, self.base, self.max_age)
        other.cache = self.cache
        return other

    def conversions(self) -> list[dict[str, Any]]:
        return sorted(self.used.values(), key=lambda item: (item["from_currency"], item["as_of"]))


def prefer_manual(rates: list[Rate]) -> list[Rate]:
    """A rate an owner typed always wins over a fetched one for the same pair (either way round) and day: the fetched one is
    dropped. (The database already keeps one rate per pair and day; this covers the inverse pair.)"""
    typed = {(frozenset((rate.base, rate.quote)), rate.as_of) for rate in rates if getattr(rate, "origin", RateOrigin.MANUAL) == RateOrigin.MANUAL}
    return [rate for rate in rates if getattr(rate, "origin", RateOrigin.MANUAL) == RateOrigin.MANUAL
            or (frozenset((rate.base, rate.quote)), rate.as_of) not in typed]


def _s(amount: Decimal, currency: str) -> str:
    return str(quantize(amount, currency))


def _per_currency(values: dict[str, Decimal]) -> list[dict[str, str]]:
    return [{"currency": currency, "amount": _s(amount, currency)} for currency, amount in sorted(values.items()) if amount != 0]


# -- the records, read once ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefundAsPayment:
    """A refund as the cash figures see it: a customer payment of a negative amount on the refund's day."""

    id: str
    invoice_id: str
    amount: Decimal
    paid_on: date


class Book:
    """Indexes over the inputs, shared by every figure."""

    def __init__(self, inputs: Inputs) -> None:
        self.inputs = inputs
        self.settings = inputs.settings
        self.invoices = {item.id: item for item in inputs.invoices if _status(item) in ISSUED}
        self.bills = {item.id: item for item in inputs.bills if item.status == BillStatus.OPEN}
        self.allocations = {item.id: item for item in inputs.allocations}
        self.live_allocations = {item.id: item for item in inputs.allocations if item.status == "active"}
        # A bill payment counts while it is not voided and, for an allocation, while the allocation is still active.
        # An allocation payment is dated by the allocation as it is now: a date corrected in Finance moves it.
        self.bill_payments = [item if item.kind != PaymentKind.ALLOCATION else item.model_copy(update={"paid_on": self.live_allocations[item.allocation_id].posted_on})
                              for item in inputs.bill_payments if item.voided_at is None and item.bill_id in self.bills
                              and (item.kind != PaymentKind.ALLOCATION or item.allocation_id in self.live_allocations)]
        self.paying = {item.allocation_id for item in inputs.bill_payments if item.voided_at is None and item.allocation_id}
        self.credit_notes = [item for item in inputs.credit_notes if _status(item) == "issued" and item.invoice_id in self.invoices]
        self.payments = [item for item in inputs.payments if item.invoice_id in self.invoices]
        # A recorded refund is money given back to the customer: the cash figures count it as a negative customer payment
        # on its own day (``RefundAsPayment``); the posting run posts it as its own event (``books_posting``).
        self.refunds = [item for item in inputs.refunds if _status(item) == "recorded" and Decimal(item.amount) > 0 and item.invoice_id in self.invoices]
        self.payments += [RefundAsPayment(f"refund:{item.id}", item.invoice_id, -Decimal(item.amount), item.refunded_on) for item in self.refunds]
        self.adjustments = [item for item in inputs.adjustments if item.status == "active"]
        self.intercompany = [item for item in inputs.intercompany if item.status == "active"]
        # Bills that are a loan repayment (the loan schedules split them into principal and interest).
        self.loan_bills = {row.bill_id for row in inputs.loan_repayments if row.status == "active" and row.bill_id}

    def allocation_section(self, allocation: Any) -> str:
        tag = self.inputs.tags.get(allocation.id)
        if tag is not None and tag.section:
            return tag.section
        return self.settings.category_sections.get(allocation.category, Section.OPERATING_EXPENSE.value)

    def project_account(self, project_id: str | None) -> str | None:
        found = self.inputs.projects.get(project_id or "")
        return found[1] if found else None


def _status(item: Any) -> str:
    value = getattr(item, "status", "")
    return getattr(value, "value", value)


def _kind(line: Any) -> str:
    value = getattr(line, "kind", "other")
    return getattr(value, "value", value)


# -- P&L entries ---------------------------------------------------------------------------------------


def _expense(section: str) -> str | None:
    return {Section.COST_OF_SALES.value: COST_OF_SALES, Section.OPERATING_EXPENSE.value: OPERATING}.get(section)


def entries(book: Book, basis: Basis, start: date, end: date, converter: Converter, notes: set[str]) -> list[Entry]:
    """Every P&L figure dated inside ``start``..``end`` on the chosen basis."""
    out: list[Entry] = []

    def add(entry: Entry) -> None:
        if start <= entry.on <= end and entry.amount != 0:
            out.append(entry)

    if basis == Basis.ACCRUAL:
        for invoice in book.invoices.values():
            if invoice.issue_date is None:
                continue
            for line in invoice.lines:
                add(Entry(invoice.issue_date, REVENUE, _kind(line), _cur(invoice), Decimal(line.net), invoice.project_id, invoice.account_id))
        for note in book.credit_notes:
            invoice = book.invoices[note.invoice_id]
            add(Entry(note.issue_date, REVENUE, "credit_notes", _cur(note), -Decimal(note.subtotal), invoice.project_id, invoice.account_id))
        for bill in book.bills.values():
            group = _expense(bill.section.value)
            if group and bill.id not in book.loan_bills:
                add(Entry(bill.bill_date, group, bill.category, bill.currency, bill.subtotal, bill.project_id, book.project_account(bill.project_id),
                          bill.cost_centre_id, bill.category))
    else:
        for payment in book.payments:
            invoice = book.invoices[payment.invoice_id]
            total = Decimal(invoice.total)
            if total <= 0:
                continue
            net = quantize(Decimal(payment.amount) * Decimal(invoice.subtotal) / total, _cur(invoice))
            kinds: dict[str, Decimal] = defaultdict(Decimal)
            for line in invoice.lines:
                kinds[_kind(line)] += Decimal(line.net)
            subtotal = sum(kinds.values(), ZERO)
            parts = sorted(kinds.items())
            given = ZERO
            for index, (kind, amount) in enumerate(parts):
                share = net - given if index == len(parts) - 1 else (quantize(net * amount / subtotal, _cur(invoice)) if subtotal else ZERO)
                given += share
                add(Entry(payment.paid_on, REVENUE, kind, _cur(invoice), share, invoice.project_id, invoice.account_id))
        for payment in book.bill_payments:
            bill = book.bills[payment.bill_id]
            group = _expense(bill.section.value)
            if group and bill.id not in book.loan_bills:
                net = quantize(payment.amount * bill.subtotal / bill.total, bill.currency)
                add(Entry(payment.paid_on, group, bill.category, bill.currency, net, bill.project_id, book.project_account(bill.project_id),
                          bill.cost_centre_id, bill.category))

    # Allocation rows count on the date the member's payment was made, on both bases, unless they pay a bill.
    for allocation in book.live_allocations.values():
        if allocation.id in book.paying:
            continue
        group = _expense(book.allocation_section(allocation))
        if group:
            tag = book.inputs.tags.get(allocation.id)
            add(Entry(allocation.posted_on, group, allocation.category, allocation.currency, Decimal(allocation.amount), allocation.project_id,
                      book.project_account(allocation.project_id), tag.cost_centre_id if tag else None, allocation.category))

    # Depreciation of capital bills with a useful life, straight line from the bill's month (both bases), unless the fixed asset
    # register holds the bill: then the register's depreciation, impairments and disposal count instead.
    registered = {(asset.source_kind, asset.source_id) for asset in book.inputs.assets}
    out.extend(_register(book, start, end))
    for bill in book.bills.values():
        if bill.section == Section.CAPITAL and bill.useful_life_months and ("bill", bill.id) not in registered:
            for month, part in zip(_months(month_start(bill.bill_date), bill.useful_life_months),
                                   _spread(bill.subtotal, bill.currency, bill.useful_life_months), strict=True):
                add(Entry(month, OPERATING, "depreciation", bill.currency, part, bill.project_id, book.project_account(bill.project_id),
                          bill.cost_centre_id, "depreciation"))

    for item in book.adjustments:
        project, centre = item.project_id, item.cost_centre_id
        account = book.project_account(project)
        group = _expense(item.section.value if item.section else Section.OPERATING_EXPENSE.value) or OPERATING
        category = item.category or item.kind.value
        if item.kind == AdjustmentKind.CAPITAL_ITEM and item.start_month and item.months:
            if ("adjustment", item.id) in registered:
                continue
            for month, part in zip(_months(item.start_month, item.months), _spread(item.amount, item.currency, item.months), strict=True):
                add(Entry(month, OPERATING, "depreciation", item.currency, part, project, account, centre, "depreciation"))
        elif item.kind in (AdjustmentKind.LOAN_INTEREST, AdjustmentKind.LOAN_INTEREST_ACCRUED):
            # Interest owed is a cost when it is charged; paying it later (``loan_interest_paid``) is cash only (defect B5).
            add(Entry(item.entry_date, FINANCE_COSTS, "loan_interest", item.currency, item.amount, project, account, centre, category))
        elif basis == Basis.ACCRUAL and item.kind == AdjustmentKind.ACCRUAL:
            add(Entry(item.entry_date, group, category, item.currency, item.amount, project, account, centre, category))
            if item.reverse_on:
                add(Entry(item.reverse_on, group, category, item.currency, -item.amount, project, account, centre, category))
        elif basis == Basis.ACCRUAL and item.kind == AdjustmentKind.PREPAYMENT and item.start_month and item.months:
            add(Entry(item.entry_date, group, category, item.currency, -item.amount, project, account, centre, category))
            for month, part in zip(_months(item.start_month, item.months), _spread(item.amount, item.currency, item.months), strict=True):
                add(Entry(month, group, category, item.currency, part, project, account, centre, category))

    out.extend(_loan_interest(book, basis, start, end))

    for item in book.intercompany:
        if item.kind == IntercompanyKind.RECHARGE:
            group = OTHER_INCOME if item.direction == Direction.RECEIVABLE else OPERATING
            add(Entry(item.entry_date, group, "intercompany", item.currency, item.amount, category="intercompany",
                      counterparty=item.counterparty_workspace_id))
    if any(item.kind == IntercompanyKind.RECHARGE for item in book.intercompany) and basis == Basis.CASH:
        notes.add("intercompany_on_entry_date")

    if basis == Basis.ACCRUAL:
        out.extend(_fx_differences(book, start, end, converter, notes))
    return out


def _register(book: Book, start: date, end: date) -> list[Entry]:
    """The fixed asset register's P&L figures: depreciation, impairments and gains or losses on disposal (both bases)."""
    from .books_assets import approved, disposal, schedule

    out: list[Entry] = []
    bills = book.bills
    adjustments = {item.id: item for item in book.adjustments if item.kind == AdjustmentKind.CAPITAL_ITEM}
    for asset in book.inputs.assets:
        source = bills.get(asset.source_id) if asset.source_kind == "bill" else adjustments.get(asset.source_id)
        if source is None or (asset.source_kind == "bill" and source.section != Section.CAPITAL):
            continue
        project, centre = source.project_id, source.cost_centre_id
        account = book.project_account(project)
        impairments = book.inputs.asset_impairments
        for row in schedule(asset, impairments):
            if start <= row.month <= end:
                out.append(Entry(row.month, OPERATING, "depreciation", asset.currency, row.amount, project, account, centre, "depreciation"))
        for loss in approved(impairments, asset.id):
            if start <= loss.entry_date <= end:
                out.append(Entry(loss.entry_date, OPERATING, "impairment", asset.currency, Decimal(loss.amount), project, account, centre, "impairment"))
        sold = disposal(asset, impairments)
        if sold is not None and asset.disposed_on is not None and start <= asset.disposed_on <= end and sold["gain"] != 0:
            if sold["gain"] > 0:
                out.append(Entry(asset.disposed_on, OTHER_INCOME, "disposal_gain", asset.currency, sold["gain"], project, account, centre, "disposal"))
            else:
                out.append(Entry(asset.disposed_on, OPERATING, "disposal_loss", asset.currency, -sold["gain"], project, account, centre, "disposal"))
    return out


def _loan_interest(book: Book, basis: Basis, start: date, end: date) -> list[Entry]:
    """Interest of the loan schedules: each repayment's interest when paid; on the accrual basis also the interest accrued and
    unpaid at each month end, reversed the next day (as the journal posts it)."""
    from .books_loans import month_end_accruals, replay

    out: list[Entry] = []
    for loan in book.inputs.loans:
        if loan.status != "active":
            continue
        repayments = [row for row in book.inputs.loan_repayments if row.loan_id == loan.id]
        _, splits = replay(loan, repayments)
        for split in splits:
            if start <= split.paid_on <= end and split.interest > 0:
                out.append(Entry(split.paid_on, FINANCE_COSTS, "loan_interest", loan.currency, split.interest, category="loan_interest"))
        if basis != Basis.ACCRUAL:
            continue
        for month_end, amount in month_end_accruals(loan, repayments, end):
            following = month_end + timedelta(days=1)
            if start <= month_end <= end:
                out.append(Entry(month_end, FINANCE_COSTS, "loan_interest", loan.currency, amount, category="loan_interest"))
            if start <= following <= end:
                out.append(Entry(following, FINANCE_COSTS, "loan_interest", loan.currency, -amount, category="loan_interest"))
    return out


def _cur(item: Any) -> str:
    value = item.currency
    return getattr(value, "value", value)


def _fx_differences(book: Book, start: date, end: date, converter: Converter, notes: set[str]) -> list[Entry]:
    """Realised differences on money settled in another currency: the base value when paid less the base value booked."""
    out: list[Entry] = []
    base = converter.base
    for payment in book.payments:
        invoice = book.invoices[payment.invoice_id]
        if _cur(invoice) == base or not (start <= payment.paid_on <= end) or invoice.issue_date is None:
            continue
        paid, booked = converter.convert(Decimal(payment.amount), _cur(invoice), payment.paid_on), converter.convert(Decimal(payment.amount), _cur(invoice), invoice.issue_date)
        if paid is None or booked is None:
            notes.add("fx_difference_incomplete")
            continue
        if paid != booked:
            out.append(Entry(payment.paid_on, FX_DIFFERENCE, "invoices", base, paid - booked, invoice.project_id, invoice.account_id))
    for payment in book.bill_payments:
        bill = book.bills[payment.bill_id]
        if bill.currency == base or not (start <= payment.paid_on <= end):
            continue
        paid, booked = converter.convert(payment.amount, bill.currency, payment.paid_on), converter.convert(payment.amount, bill.currency, bill.bill_date)
        if paid is None or booked is None:
            notes.add("fx_difference_incomplete")
            continue
        if paid != booked:
            out.append(Entry(payment.paid_on, FX_DIFFERENCE, "bills", base, booked - paid, bill.project_id, book.project_account(bill.project_id)))
    return out


# -- the statement ---------------------------------------------------------------------------------------


@dataclass
class Totals:
    base: str
    lines: dict[tuple[str, str], Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    groups: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    not_converted: dict[tuple[str, str], Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    counted: int = 0


def total_up(items: list[Entry], converter: Converter) -> Totals:
    totals = Totals(converter.base)
    for entry in items:
        value = converter.convert(entry.amount, entry.currency, entry.on)
        if value is None:
            totals.not_converted[(entry.group, entry.currency)] += entry.amount
            continue
        totals.lines[(entry.group, entry.line)] += value
        totals.groups[entry.group] += value
        totals.counted += 1
    return totals


def statement(totals: Totals) -> dict[str, Any]:
    base = totals.base
    g = totals.groups
    gross = g[REVENUE] - g[COST_OF_SALES]
    operating = gross - g[OPERATING]
    net = operating + g[OTHER_INCOME] - g[FINANCE_COSTS] + g[FX_DIFFERENCE]

    def lines(group: str) -> list[dict[str, str]]:
        found = [(line, amount) for (name, line), amount in totals.lines.items() if name == group and amount != 0]
        return [{"key": line, "amount": _s(amount, base)} for line, amount in sorted(found, key=lambda item: (-abs(item[1]), item[0]))]

    not_converted: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for (group, currency), amount in totals.not_converted.items():
        if amount != 0:
            not_converted[currency][group] = amount
    return {
        "currency": base,
        "revenue": {"lines": lines(REVENUE), "total": _s(g[REVENUE], base)},
        "cost_of_sales": {"lines": lines(COST_OF_SALES), "total": _s(g[COST_OF_SALES], base)},
        "gross_profit": _s(gross, base),
        "gross_margin_pct": _pct(gross, g[REVENUE]),
        "operating_expenses": {"lines": lines(OPERATING), "total": _s(g[OPERATING], base)},
        "operating_profit": _s(operating, base),
        "other_income": {"lines": lines(OTHER_INCOME), "total": _s(g[OTHER_INCOME], base)},
        "finance_costs": {"lines": lines(FINANCE_COSTS), "total": _s(g[FINANCE_COSTS], base)},
        "fx_differences": _s(g[FX_DIFFERENCE], base),
        "net_profit": _s(net, base),
        "net_margin_pct": _pct(net, g[REVENUE]),
        "not_converted": [{"currency": currency, "groups": {k: _s(v, currency) for k, v in sorted(groups.items())}}
                          for currency, groups in sorted(not_converted.items())],
        "complete": not not_converted,
    }


def _pct(part: Decimal, whole: Decimal) -> str | None:
    if whole <= 0:
        return None
    return str((part * Decimal(100) / whole).quantize(Decimal("0.1")))


# -- tax, cash flow and balances (per currency, never converted) -------------------------------------


def tax_summary(book: Book, basis: Basis, start: date, end: date) -> dict[str, Any]:
    output: dict[str, Decimal] = defaultdict(Decimal)
    inputs: dict[str, Decimal] = defaultdict(Decimal)
    if basis == Basis.ACCRUAL:
        for invoice in book.invoices.values():
            if invoice.issue_date and start <= invoice.issue_date <= end:
                output[_cur(invoice)] += Decimal(invoice.tax)
        for note in book.credit_notes:
            if start <= note.issue_date <= end:
                output[_cur(note)] -= Decimal(note.tax)
        for bill in book.bills.values():
            if start <= bill.bill_date <= end:
                inputs[bill.currency] += bill.tax
    else:
        for payment in book.payments:
            invoice = book.invoices[payment.invoice_id]
            if start <= payment.paid_on <= end and Decimal(invoice.total) > 0:
                amount = Decimal(payment.amount)
                output[_cur(invoice)] += amount - quantize(amount * Decimal(invoice.subtotal) / Decimal(invoice.total), _cur(invoice))
        for payment in book.bill_payments:
            bill = book.bills[payment.bill_id]
            if start <= payment.paid_on <= end:
                inputs[bill.currency] += payment.amount - quantize(payment.amount * bill.subtotal / bill.total, bill.currency)
    untracked = sum(1 for item in book.live_allocations.values() if start <= item.posted_on <= end and item.id not in book.paying)
    currencies = sorted(set(output) | set(inputs))
    return {
        "label": book.settings.tax_label,
        "country": book.settings.country,
        "by_currency": [{"currency": c, "output": _s(output[c], c), "input": _s(inputs[c], c), "net": _s(output[c] - inputs[c], c)} for c in currencies],
        "allocations_without_tax_split": untracked,
    }


@dataclass(frozen=True)
class Flow:
    """One movement of business money (or, with ``line`` ``paid_by_members:<treatment>``, money a member paid)."""

    on: date
    line: str
    currency: str
    amount: Decimal
    counterparty: str | None = None  # the other business of an intercompany settlement or loan


CASH_IN = ("customers_paid", "loans_received", "owner_contributions", "intercompany_in", "intercompany_borrowed", "asset_sales")
MEMBERS = "paid_by_members:"


def cash_flows(book: Book, start: date, end: date) -> list[Flow]:
    """Every movement in ``start``..``end``, dated, so a consolidation can convert each on its own day."""
    out: list[Flow] = []

    def put(line: str, currency: str, amount: Decimal, on: date, counterparty: str | None = None) -> None:
        if start <= on <= end:
            out.append(Flow(on, line, currency, amount, counterparty))

    for payment in book.payments:
        put("customers_paid", _cur(book.invoices[payment.invoice_id]), Decimal(payment.amount), payment.paid_on)
    for payment in book.bill_payments:
        if payment.kind == PaymentKind.RECORDED:
            bill = book.bills[payment.bill_id]
            put("capital_spend" if bill.section == Section.CAPITAL else "suppliers_paid", bill.currency, payment.amount, payment.paid_on)
    flows = {
        AdjustmentKind.LOAN_RECEIVED: "loans_received", AdjustmentKind.LOAN_REPAYMENT: "loans_repaid", AdjustmentKind.LOAN_INTEREST: "interest_paid",
        AdjustmentKind.LOAN_INTEREST_PAID: "interest_paid",
        AdjustmentKind.OWNER_CONTRIBUTION: "owner_contributions", AdjustmentKind.OWNER_DRAWING: "owner_drawings",
        AdjustmentKind.OWNER_REIMBURSEMENT: "members_reimbursed",
    }
    for item in book.adjustments:
        if item.kind in flows:
            put(flows[item.kind], item.currency, item.amount, item.entry_date)
    for item in book.intercompany:
        if item.kind == IntercompanyKind.SETTLEMENT:
            put("intercompany_in" if item.direction == Direction.RECEIVABLE else "intercompany_out", item.currency, item.amount, item.entry_date,
                item.counterparty_workspace_id)
        elif item.kind == IntercompanyKind.LOAN:
            put("intercompany_lent" if item.direction == Direction.RECEIVABLE else "intercompany_borrowed", item.currency, item.amount, item.entry_date,
                item.counterparty_workspace_id)
    for allocation in book.live_allocations.values():
        put(MEMBERS + allocation.treatment, allocation.currency, Decimal(allocation.amount), allocation.posted_on)
    _register_and_loan_flows(book, put)
    return out


def _register_and_loan_flows(book: Book, put: Any) -> None:
    """Cash of the loan schedules (received, principal repaid, interest paid; a repayment matched to an adjustment or a bill is
    already that record's cash) and of assets sold."""
    from .books_loans import replay

    live_adjustments = {item.id for item in book.adjustments}
    for loan in book.inputs.loans:
        if loan.status != "active":
            continue
        if not (loan.received_adjustment_id and loan.received_adjustment_id in live_adjustments):
            put("loans_received", loan.currency, Decimal(loan.principal), loan.start_date)
        repayments = [row for row in book.inputs.loan_repayments if row.loan_id == loan.id]
        rows = {row.id: row for row in repayments}
        _, splits = replay(loan, repayments)
        for split in splits:
            row = rows[split.repayment_id]  # type: ignore[index]
            if row.adjustment_id or row.bill_id:
                continue
            if split.principal > 0:
                put("loans_repaid", loan.currency, split.principal, split.paid_on)
            if split.interest > 0:
                put("interest_paid", loan.currency, split.interest, split.paid_on)
    for asset in book.inputs.assets:
        if asset.status == "disposed" and asset.disposed_on and Decimal(asset.proceeds or 0) > 0:
            put("asset_sales", asset.currency, Decimal(asset.proceeds), asset.disposed_on)


def cash_flow(book: Book, start: date, end: date) -> dict[str, Any]:
    """Business money in and out per currency; what members paid from their own money is shown apart."""
    lines: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    paid_by_members: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for flow in cash_flows(book, start, end):
        if flow.line.startswith(MEMBERS):
            paid_by_members[flow.line[len(MEMBERS):]][flow.currency] += flow.amount
        else:
            lines[flow.line][flow.currency] += flow.amount
    inflow = CASH_IN
    currencies = sorted({c for values in lines.values() for c in values})
    net = {c: sum((lines[k].get(c, ZERO) for k in inflow if k in lines), ZERO) - sum((v.get(c, ZERO) for k, v in lines.items() if k not in inflow), ZERO)
           for c in currencies}
    return {
        "lines": {key: _per_currency(values) for key, values in sorted(lines.items())},
        "net": _per_currency(net),
        "paid_by_members": {key: _per_currency(values) for key, values in sorted(paid_by_members.items())},
    }


def _today() -> date:
    from ..schemas import utc_now

    return utc_now().date()


def _age(due: date | None, end: date) -> str:
    days = (end - due).days if due else 0
    for limit, name in AGING:
        if days <= limit:
            return name
    return "days_over_90"


def balances(book: Book, end: date, today: date | None = None) -> dict[str, Any]:
    """What is owed on the last day of the period, per currency, aged on that day (or today, for a period that has not ended)."""
    on = min(end, today) if today else end
    receivable: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    outstanding: list[dict[str, Any]] = []
    paid: dict[str, Decimal] = defaultdict(Decimal)
    for payment in book.payments:
        if payment.paid_on <= end:
            paid[payment.invoice_id] += Decimal(payment.amount)
    credited: dict[str, Decimal] = defaultdict(Decimal)
    for note in book.credit_notes:
        if note.issue_date <= end:
            credited[note.invoice_id] += Decimal(note.amount)
    for invoice in book.invoices.values():
        if invoice.issue_date is None or invoice.issue_date > end:
            continue
        balance = Decimal(invoice.total) - paid[invoice.id] - credited[invoice.id]
        if balance > 0:
            bucket = _age(invoice.due_date, on)
            receivable[_cur(invoice)][bucket] += balance
            outstanding.append({"invoice_id": invoice.id, "number": invoice.number, "account_id": invoice.account_id,
                                "account_name": book.inputs.accounts.get(invoice.account_id, ""), "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
                                "days_overdue": max(0, (on - invoice.due_date).days) if invoice.due_date else 0, "currency": _cur(invoice),
                                "balance": _s(balance, _cur(invoice))})
    outstanding.sort(key=lambda item: (-item["days_overdue"], item["number"] or ""))

    payable: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    bills_paid: dict[str, Decimal] = defaultdict(Decimal)
    for payment in book.bill_payments:
        if payment.paid_on <= end:
            bills_paid[payment.bill_id] += payment.amount
    unpaid_bills: list[dict[str, Any]] = []
    for bill in book.bills.values():
        if bill.bill_date > end:
            continue
        balance = bill.total - bills_paid[bill.id]
        if balance > 0:
            payable[bill.currency][_age(bill.due_date, on)] += balance
            unpaid_bills.append({"bill_id": bill.id, "vendor_name": book.inputs.vendors.get(bill.vendor_id, ""), "reference": bill.reference,
                                 "due_date": bill.due_date.isoformat(), "days_overdue": max(0, (on - bill.due_date).days), "currency": bill.currency,
                                 "balance": _s(balance, bill.currency)})
    unpaid_bills.sort(key=lambda item: (-item["days_overdue"], item["due_date"]))

    owed_members: dict[str, Decimal] = defaultdict(Decimal)
    for allocation in book.live_allocations.values():
        if allocation.treatment == "reimbursable" and allocation.posted_on <= end:
            owed_members[allocation.currency] += Decimal(allocation.amount)
    debt: dict[str, Decimal] = defaultdict(Decimal)
    interest: dict[str, Decimal] = defaultdict(Decimal)
    for item in book.adjustments:
        if item.entry_date > end:
            continue
        if item.kind == AdjustmentKind.OWNER_REIMBURSEMENT:
            owed_members[item.currency] -= item.amount
        elif item.kind == AdjustmentKind.LOAN_RECEIVED:
            debt[item.currency] += item.amount
        elif item.kind == AdjustmentKind.LOAN_REPAYMENT:
            debt[item.currency] -= item.amount
        elif item.kind == AdjustmentKind.LOAN_INTEREST_ACCRUED:
            interest[item.currency] += item.amount
        elif item.kind == AdjustmentKind.LOAN_INTEREST_PAID:
            interest[item.currency] -= item.amount

    from .books_loans import owed_on

    by_id = {item.id: item for item in book.adjustments}
    for loan in book.inputs.loans:
        if loan.status != "active" or loan.start_date > end:
            continue
        repayments = [row for row in book.inputs.loan_repayments if row.loan_id == loan.id]
        # Interest accrues day by day: what is owed on a day still to come is what is owed today (no interest guessed ahead).
        principal, unpaid = owed_on(loan, repayments, min(end, today or _today()))
        debt[loan.currency] += principal
        interest[loan.currency] += unpaid
        # Adjustments matched to the schedule already moved the debt above: the schedule's figure replaces theirs.
        received = by_id.get(loan.received_adjustment_id or "")
        if received is not None and received.entry_date <= end:
            debt[received.currency] -= received.amount
        for row in repayments:
            matched = by_id.get(row.adjustment_id or "")
            if row.status == "active" and matched is not None and matched.entry_date <= end:
                debt[matched.currency] += matched.amount

    intercompany: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    names: dict[str, str] = {}
    for item in book.intercompany:
        if item.entry_date > end:
            continue
        names[item.counterparty_workspace_id] = item.counterparty_name
        # A recharge or a loan raises what one side owes; a settlement lowers it.
        sign = Decimal(1) if item.kind != IntercompanyKind.SETTLEMENT else Decimal(-1)
        owed_to_us = item.direction == Direction.RECEIVABLE
        intercompany[(item.counterparty_workspace_id, item.currency)] += item.amount * sign * (1 if owed_to_us else -1)

    def aged(values: dict[str, dict[str, Decimal]]) -> list[dict[str, Any]]:
        return [{"currency": c, "total": _s(sum(buckets.values(), ZERO), c), **{k: _s(v, c) for k, v in sorted(buckets.items())}}
                for c, buckets in sorted(values.items())]

    return {
        "as_of": end.isoformat(),
        "aged_on": on.isoformat(),
        "receivables": aged(receivable),
        "outstanding_invoices": outstanding[:50],
        "outstanding_invoices_count": len(outstanding),
        "payables": aged(payable),
        "unpaid_bills": unpaid_bills[:50],
        "unpaid_bills_count": len(unpaid_bills),
        "owed_to_members": _per_currency(owed_members),
        "debt": _per_currency(debt),
        "interest_payable": _per_currency(interest),
        "intercompany": [{"workspace_id": w, "name": names.get(w, ""), "currency": c, "amount": _s(v, c)}
                         for (w, c), v in sorted(intercompany.items()) if v != 0],
    }


def equity(book: Book, start: date, end: date) -> dict[str, Any]:
    """Owner contributions and drawings in the period: allocation rows the owner paid, plus adjustments."""
    contributions: dict[str, Decimal] = defaultdict(Decimal)
    drawings: dict[str, Decimal] = defaultdict(Decimal)
    for allocation in book.live_allocations.values():
        if allocation.treatment == "owner_contribution" and start <= allocation.posted_on <= end:
            contributions[allocation.currency] += Decimal(allocation.amount)
    for item in book.adjustments:
        if start <= item.entry_date <= end:
            if item.kind == AdjustmentKind.OWNER_CONTRIBUTION:
                contributions[item.currency] += item.amount
            elif item.kind == AdjustmentKind.OWNER_DRAWING:
                drawings[item.currency] += item.amount
    return {"contributions": _per_currency(contributions), "drawings": _per_currency(drawings)}


# -- dashboard breakdowns ---------------------------------------------------------------------------------


def _by(items: list[Entry], converter: Converter, key: str) -> dict[str, dict[str, Any]]:
    """Revenue and costs in the base currency per ``key``, and what could not be converted (per currency, kept apart: never a
    false zero, defect B1)."""
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"revenue": ZERO, "costs": ZERO, "unconverted": defaultdict(Decimal)})
    for entry in items:
        name = getattr(entry, key)
        if not name or entry.group == FX_DIFFERENCE:
            continue
        value = converter.convert(entry.amount, entry.currency, entry.on)
        if value is None:
            out[name]["unconverted"][entry.currency] += entry.amount if entry.group in (REVENUE, OTHER_INCOME) else -entry.amount
            continue
        if entry.group in (REVENUE, OTHER_INCOME):
            out[name]["revenue"] += value
        else:
            out[name]["costs"] += value
    return out


BREAKDOWN_LIMIT = 50


def breakdown(items: list[Entry], converter: Converter, key: str, names: dict[str, str], limit: int | None = BREAKDOWN_LIMIT) -> list[dict[str, Any]]:
    """Rows sorted by revenue, then costs. A row with figures that could not be converted says so (``complete: false`` and the
    amounts per currency); ``limit=None`` keeps every row."""
    base = converter.base
    rows = []
    for name, figures in _by(items, converter, key).items():
        margin = figures["revenue"] - figures["costs"]
        missing = [{"currency": c, "net": _s(v, c)} for c, v in sorted(figures["unconverted"].items()) if v != 0]
        rows.append({"id": name, "name": names.get(name, ""), "revenue": _s(figures["revenue"], base), "costs": _s(figures["costs"], base),
                     "margin": _s(margin, base), "margin_pct": _pct(margin, figures["revenue"]), "complete": not missing, "not_converted": missing})
    rows.sort(key=lambda row: (-Decimal(row["revenue"]), -Decimal(row["costs"]), row["id"]))
    return rows if limit is None else rows[:limit]


def trend(book: Book, basis: Basis, end: date, converter: Converter, months: int = 12) -> list[dict[str, Any]]:
    first = add_months(month_start(end), -(months - 1))
    items = entries(book, basis, first, end, converter, set())
    base = converter.base
    rows: dict[date, dict[str, Any]] = {m: {"revenue": ZERO, "costs": ZERO, "fx": ZERO, "unconverted": False} for m in _months(first, months)}
    for entry in items:
        bucket = rows[month_start(entry.on)]
        value = converter.convert(entry.amount, entry.currency, entry.on)
        if value is None:
            bucket["unconverted"] = True
            continue
        if entry.group == FX_DIFFERENCE:
            bucket["fx"] += value  # an exchange difference is neither revenue nor a cost (defect B3); it is in the profit
        elif entry.group in (REVENUE, OTHER_INCOME):
            bucket["revenue"] += value
        else:
            bucket["costs"] += value
    return [{"month": m.isoformat()[:7], "revenue": _s(v["revenue"], base), "costs": _s(v["costs"], base), "fx_differences": _s(v["fx"], base),
             "profit": _s(v["revenue"] - v["costs"] + v["fx"], base), "unconverted": v["unconverted"]} for m, v in sorted(rows.items())]


def budget_vs_actual(book: Book, basis: Basis, start: date, end: date, converter: Converter) -> list[dict[str, Any]]:
    """Each budget whose period overlaps the report, against the costs of its own period (base currency only)."""
    out = []
    base = converter.base
    spans = [(budget, add_months(budget.period_start, budget.months) - timedelta(days=1)) for budget in book.inputs.budgets]
    spans = [(budget, last) for budget, last in spans if last >= start and budget.period_start <= end]
    # One pass over the records for every budget's own period, filtered per budget below.
    pool = entries(book, basis, min(b.period_start for b, _ in spans), max(last for _, last in spans), converter, set()) if spans else []
    for budget, last in spans:
        row: dict[str, Any] = {"id": budget.id, "name": budget.name, "period_start": budget.period_start.isoformat(), "period_end": last.isoformat(),
                               "currency": budget.currency, "budget": _s(budget.amount, budget.currency)}
        if budget.currency != base:
            out.append({**row, "actual": None, "variance": None, "used_pct": None, "comparable": False})
            continue
        actual = ZERO
        missing = False
        for entry in pool:
            if not (budget.period_start <= entry.on <= last) or entry.group not in (COST_OF_SALES, OPERATING, FINANCE_COSTS):
                continue
            if budget.section and _expense(budget.section.value) != entry.group:
                continue
            if budget.category and entry.category != budget.category:
                continue
            if budget.project_id and entry.project_id != budget.project_id:
                continue
            if budget.cost_centre_id and entry.cost_centre_id != budget.cost_centre_id:
                continue
            value = converter.convert(entry.amount, entry.currency, entry.on)
            if value is None:
                missing = True
                continue
            actual += value
        out.append({**row, "actual": _s(actual, base), "variance": _s(budget.amount - actual, base), "used_pct": _pct(actual, budget.amount),
                    "comparable": True, "unconverted": missing})
    out.sort(key=lambda item: (item["period_start"], item["name"]))
    return out


def recurring_costs(book: Book) -> list[dict[str, Any]]:
    """Each recurring cost and its cost per month: every recurring bill that still runs (active or paused), and the newest
    bill of each cost only marked recurring (vendor, category, currency) that no recurring bill covers."""
    rows: list[dict[str, Any]] = []
    covered: set[tuple[str, str, str]] = set()
    for template in book.inputs.templates:
        if template.status == "ended":
            continue
        covered.add((template.vendor_id, template.category, template.currency))
        rows.append({"bill_id": None, "template_id": template.id, "vendor_id": template.vendor_id, "vendor_name": book.inputs.vendors.get(template.vendor_id, ""),
                     "category": template.category, "recurrence": template.frequency.value, "interval": template.interval, "currency": template.currency,
                     "amount": _s(template.subtotal, template.currency), "per_month": _s(template_per_month(template), template.currency),
                     "last_bill_date": template.last_period.isoformat() if template.last_period else None,
                     "next_date": template.next_date.isoformat() if template.next_date and template.status == "active" else None,
                     "status": template.status.value})
    latest: dict[tuple[str, str, str], Bill] = {}
    for bill in book.bills.values():
        if bill.recurrence is None or bill.template_id is not None:
            continue
        key = (bill.vendor_id, bill.category, bill.currency)
        if key not in latest or (bill.bill_date, bill.created_at) > (latest[key].bill_date, latest[key].created_at):
            latest[key] = bill
    for (vendor, category, currency), bill in latest.items():
        if (vendor, category, currency) in covered:
            continue
        months = RECURRENCE_MONTHS[bill.recurrence]  # type: ignore[index]
        rows.append({"bill_id": bill.id, "template_id": None, "vendor_id": vendor, "vendor_name": book.inputs.vendors.get(vendor, ""), "category": category,
                     "recurrence": bill.recurrence.value if bill.recurrence else None, "interval": 1, "currency": currency,
                     "amount": _s(bill.subtotal, currency), "per_month": _s(bill.subtotal / months, currency), "last_bill_date": bill.bill_date.isoformat(),
                     "next_date": None, "status": None})
    rows.sort(key=lambda row: (row["currency"], -Decimal(row["per_month"])))
    return rows


def recurring_totals(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What recurring costs come to per month, per currency (a paused recurring bill makes nothing, so it is left out)."""
    per_month: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        if row.get("status") != "paused":
            per_month[row["currency"]] += Decimal(row["per_month"])
    return _per_currency(per_month)


UPCOMING_DAYS = 90


def upcoming_bills(templates: list[Any], vendors: dict[str, str], today: date, days: int = UPCOMING_DAYS, limit: int = 30) -> list[dict[str, Any]]:
    """The bills active recurring bills will make in the next ``days`` days (and any period already due), soonest first."""
    until = today + timedelta(days=days)
    out = []
    for template in templates:
        for day in template_upcoming(template, until):
            out.append({"template_id": template.id, "vendor_name": vendors.get(template.vendor_id, ""), "description": template.description,
                        "bill_date": day.isoformat(), "due_date": (day + timedelta(days=template.due_days)).isoformat(), "currency": template.currency,
                        "total": _s(template.total, template.currency), "create_as": template.create_as})
    out.sort(key=lambda row: (row["bill_date"], row["vendor_name"], row["template_id"]))
    return out[:limit]


# -- the whole report ----------------------------------------------------------------------------------------


def report(inputs: Inputs, start: date, end: date, basis: Basis, cost_centres: dict[str, str] | None = None, today: date | None = None) -> dict[str, Any]:
    """The P&L on ``basis`` for ``start``..``end``, the cash flow, what is owed at ``end`` and the dashboard breakdowns."""
    book = Book(inputs)
    settings = inputs.settings
    converter = Converter(inputs.rates, settings.base_currency, settings.rate_max_age_days)
    notes: set[str] = set()
    items = entries(book, basis, start, end, converter, notes)
    totals = total_up(items, converter)
    pnl = statement(totals)
    if not pnl["complete"]:
        notes.add("not_converted")
    review = [item for item in inputs.allocations if item.status == "needs_review" and start <= item.posted_on <= end]
    if review:
        notes.add("allocations_in_review")
    if inputs.truncated:
        notes.add("truncated")
    recurring = recurring_costs(book)
    project_names = {key: value[0] for key, value in inputs.projects.items()}
    quiet = converter.silent()
    customers = breakdown(items, quiet, "account_id", inputs.accounts, limit=None)
    projects = breakdown(items, quiet, "project_id", project_names, limit=None)
    centres = breakdown(items, quiet, "cost_centre_id", cost_centres if cost_centres is not None else inputs.cost_centres, limit=None)
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "basis": basis.value},
        "base_currency": settings.base_currency,
        "pnl": pnl,
        "tax": tax_summary(book, basis, start, end),
        "cash_flow": cash_flow(book, start, end),
        "balances": balances(book, end, today),
        "equity": equity(book, start, end),
        "trend": trend(book, basis, min(end, today) if today else end, quiet),
        # Each list keeps its first rows; ``*_total`` says how many there are, so the page says "N more not shown", never 0 (B1).
        "customers": customers[:BREAKDOWN_LIMIT], "customers_total": len(customers),
        "projects": projects[:BREAKDOWN_LIMIT], "projects_total": len(projects),
        "cost_centres": centres[:BREAKDOWN_LIMIT], "cost_centres_total": len(centres),
        "budgets": budget_vs_actual(book, basis, start, end, quiet),
        "recurring": {"items": recurring, "per_month": recurring_totals(recurring), "upcoming": upcoming_bills(inputs.templates, inputs.vendors, today or end)},
        "allocations_in_review": len(review),
        "conversions": converter.conversions(),
        "notes": sorted(notes),
        "truncated": inputs.truncated,
    }


__all__ = ["Book", "Converter", "Entry", "Inputs", "add_months", "default_period", "entries", "fiscal_year", "month_start", "report"]
