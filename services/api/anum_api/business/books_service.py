"""Rules of the business books (docs/business-books.md): validation, bill payments, intercompany entries and
gathering the records a report is made from. Every refusal is a :class:`BooksError` with plain words and a code.

Nothing here writes the Finance ledger (``finance_journal_entries``, ``finance_postings``,
``finance_transactions``, ``finance_links``) or a business invoice: the books read invoices,
payments and credit notes, and the allocation rows of ``business_ledger_allocations``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..authorization import ROLE_PERMISSIONS, Permission
from ..finance.money import MoneyError, normalize_currency, to_money
from ..schemas import TenantContext, new_id
from .books_models import (
    Adjustment,
    AdjustmentKind,
    Bill,
    BillPayment,
    BillStatus,
    BookSettings,
    Direction,
    Intercompany,
    IntercompanyKind,
    PaymentKind,
    Section,
)
from .books_report import Inputs
from .books_store import MAX_SCAN, AtLeast, BooksStore, open_books_store

CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")
MAX_AMOUNT = Decimal(1000000000)
MAX_LIVE_PAYMENTS = 500  # payments in force on one bill


class BooksError(Exception):
    def __init__(self, status_code: int, message: str, code: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.extra = extra


def clean(value: str | None, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def amount_of(value: Any, currency: str, *, field: str = "amount", allow_zero: bool = False) -> Decimal:
    """A form amount (a string or an integer; never a float) rounded once to the currency's minor unit."""
    try:
        amount = to_money(value, currency)
    except MoneyError as exc:
        raise BooksError(422, str(exc), "invalid_amount", field=field) from None
    if amount < 0 or (amount == 0 and not allow_zero):
        raise BooksError(422, "Enter an amount above zero.", "invalid_amount", field=field)
    if amount > MAX_AMOUNT:
        raise BooksError(422, "The amount is too large.", "invalid_amount", field=field)
    return amount


def currency_of(value: str) -> str:
    try:
        return normalize_currency(value)
    except MoneyError as exc:
        raise BooksError(422, str(exc), "invalid_currency") from None


def category_of(value: str | None, *, required: bool = True) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        if required:
            raise BooksError(422, "Choose a category.", "invalid_category")
        return None
    if not CATEGORY.match(text):
        raise BooksError(422, "A category is lowercase letters, digits and underscores, for example office_rent.", "invalid_category")
    return text


def country_of(value: str | None) -> str | None:
    text = (value or "").strip().upper()
    if not text:
        return None
    if not COUNTRY.match(text):
        raise BooksError(422, "A country is a two-letter code, for example SA or AE.", "invalid_country")
    return text


def idem_hash(key: str | None, *parts: str) -> str | None:
    """The stored form of an ``Idempotency-Key``: a hash bound to what it was for."""
    if not key:
        return None
    if len(key) < 8 or len(key) > 120:
        raise BooksError(422, "The Idempotency-Key header is 8 to 120 characters.", "invalid_idempotency_key")
    return hashlib.sha256("\x1f".join((*parts, key)).encode()).hexdigest()


def not_future(day: date, today: date, what: str) -> date:
    if day > today:
        raise BooksError(422, f"The {what} cannot be in the future.", "date_in_future")
    return day


# -- settings ---------------------------------------------------------------------------------------


def settings_or_409(store: BooksStore) -> BookSettings:
    found = store.get_settings()
    if found is None:
        raise BooksError(409, "Set the business's base currency and financial year first (Books, Settings).", "books_not_set_up")
    return found


def section_map(values: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if len(values) > 100:
        raise BooksError(422, "At most 100 categories can be mapped.", "too_many_categories")
    for category, section in values.items():
        key = category_of(category) or ""
        if section not in ("cost_of_sales", "operating_expense", "capital", "excluded"):
            raise BooksError(422, "A category maps to cost of sales, operating expenses, a capital item or is excluded.", "invalid_section")
        out[key] = section
    return out


# -- references -------------------------------------------------------------------------------------


def require_cost_centre(store: BooksStore, cost_centre_id: str | None, *, keep: str | None = None) -> str | None:
    """A cost centre of this business; an archived one only when the record already uses it (``keep``)."""
    if not cost_centre_id:
        return None
    found = store.get("cost_centres", cost_centre_id)
    if found is None:
        raise BooksError(404, "Cost centre not found.", "cost_centre_not_found")
    if found.archived and found.id != keep:
        raise BooksError(409, "That cost centre is archived. Restore it or choose another.", "cost_centre_archived")
    return found.id


def require_project(context: TenantContext, project_id: str | None) -> str | None:
    """A project of this business (the Business projects), or nothing."""
    if not project_id:
        return None
    from .store import open_business_store

    with open_business_store(context) as business:
        found = business.get("projects", project_id)
    if found is None:
        raise BooksError(404, "Project not found in this business.", "project_not_found")
    return found.id


# -- bills and their payments --------------------------------------------------------------------------


def live_payments(store: BooksStore, bill_id: str, live_allocations: set[str] | None) -> list[BillPayment]:
    """Payments not voided whose allocation, for an allocation payment, is in ``live_allocations`` (``None`` skips that check).

    Rules that protect a bill (its balance, its money, voiding it) pass every allocation that still exists, so one
    waiting for review in Finance keeps holding its bill; the figures pass only the active ones."""
    # Only payments not voided are read (voided ones never crowd them out), and a bill takes at most MAX_LIVE_PAYMENTS of them.
    rows, _ = store.find("bill_payments", where={"bill_id": bill_id, "voided_at": None}, order_by="paid_on", descending=False, limit=MAX_LIVE_PAYMENTS + 1)
    return [p for p in rows if p.kind != PaymentKind.ALLOCATION or live_allocations is None or p.allocation_id in live_allocations]


def paid_amount(payments: list[BillPayment]) -> Decimal:
    return sum((p.amount for p in payments), Decimal(0))


def payment_state(bill: Bill, paid: Decimal) -> str:
    if bill.status == BillStatus.VOID:
        return "void"
    if bill.status == BillStatus.DRAFT:
        return "draft"
    if paid <= 0:
        return "unpaid"
    return "paid" if paid >= bill.total else "partly_paid"


def allocations_by_id(context: TenantContext) -> dict[str, Any]:
    """This business's allocation rows (``business_ledger_allocations``), read in its own RLS scope."""
    from ..finance.links_store import open_workspace_allocations

    with open_workspace_allocations(context) as links:
        rows, _ = links.list_allocations(context, currency=None, limit=MAX_SCAN, offset=0)
    return {row.id: row for row in rows}


def bill_from(context: TenantContext, store: BooksStore, payload: dict[str, Any], *, now: datetime, idem_key: str | None,
              existing: Bill | None = None) -> Bill:
    vendor = store.get("vendors", payload["vendor_id"])
    if vendor is None:
        raise BooksError(404, "Vendor not found.", "vendor_not_found")
    if vendor.archived and (existing is None or existing.vendor_id != vendor.id):
        raise BooksError(409, "That vendor is archived. Restore it or choose another.", "vendor_archived")
    currency = currency_of(payload["currency"])
    subtotal = amount_of(payload["subtotal"], currency, field="subtotal", allow_zero=True)
    tax = amount_of(payload.get("tax") or "0", currency, field="tax", allow_zero=True)
    if subtotal + tax <= 0:
        raise BooksError(422, "A bill is for more than zero.", "invalid_amount", field="subtotal")
    bill_date: date = payload["bill_date"]
    due_date: date = payload.get("due_date") or bill_date
    if due_date < bill_date:
        raise BooksError(422, "The due date is on or after the bill date.", "invalid_due_date")
    section = Section(payload.get("section") or (vendor.default_section.value if vendor.default_section else Section.OPERATING_EXPENSE.value))
    life = payload.get("useful_life_months")
    if life is not None and section != Section.CAPITAL:
        raise BooksError(422, "A useful life is only for a capital item.", "life_needs_capital")
    category = category_of(payload.get("category") or vendor.default_category) or "other"
    from .tax_rules import TaxRuleError
    from .tax_service import bill_tax, table_in

    try:
        tax_code, reverse_charge_tax = bill_tax(table_in(store), payload.get("tax_code"), subtotal, tax, bill_date)
    except TaxRuleError as exc:
        raise BooksError(422, exc.message, exc.code, field="tax_code") from None
    fields = dict(
        vendor_id=vendor.id, reference=clean(payload.get("reference"), 80), description=clean(payload["description"], 300), bill_date=bill_date,
        due_date=due_date, currency=currency, subtotal=subtotal, tax=tax, total=subtotal + tax, category=category, section=section,
        cost_centre_id=require_cost_centre(store, payload.get("cost_centre_id"), keep=existing.cost_centre_id if existing else None),
        project_id=require_project(context, payload.get("project_id")), recurrence=payload.get("recurrence"), useful_life_months=life, updated_at=now,
        tax_code=tax_code, reverse_charge_tax=reverse_charge_tax,
    )
    if len(fields["description"]) < 2:
        raise BooksError(422, "Describe what the bill is for.", "invalid_description")
    if existing is not None:
        return existing.model_copy(update={**fields, "version": existing.version + 1})
    return Bill(id=new_id("bill"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, created_by=context.user_id,
                idem_key=idem_key, **fields)


def same_bill(found: Bill, fresh: Bill) -> bool:
    keys = ("vendor_id", "bill_date", "currency", "subtotal", "tax", "category", "section", "reference", "tax_code")
    return all(getattr(found, key) == getattr(fresh, key) for key in keys)


def pay_bill(context: TenantContext, store: BooksStore, bill_id: str, *, kind: PaymentKind, amount: Any, paid_on: date | None, method: str,
             reference: str, note: str, allocation_id: str | None, idem_key: str | None, today: date, now: datetime) -> tuple[BillPayment, bool]:
    """Record one payment of an open bill under a lock on the bill row. Returns the payment and whether it was replayed."""
    bill = store.get("bills", bill_id, for_update=True)
    if bill is None:
        raise BooksError(404, "Bill not found.", "bill_not_found")
    stored_key = idem_hash(idem_key, "bill_payment", bill.id)
    if stored_key:
        earlier = store.first("bill_payments", bill_id=bill.id, idem_key=stored_key)
        if earlier is not None:
            same = earlier.kind == kind and (allocation_id is None or earlier.allocation_id == allocation_id) and (
                kind == PaymentKind.ALLOCATION or earlier.amount == amount_of(amount, bill.currency))
            if not same:
                raise BooksError(409, "This request was already used for another payment. Reload the bill.", "idempotency_mismatch")
            return earlier, True
    if bill.status == BillStatus.VOID:
        raise BooksError(409, "This bill is void.", "bill_void")
    if bill.status == BillStatus.DRAFT:
        raise BooksError(409, "This bill is a draft. Approve it first, then record its payment.", "bill_draft")
    allocations = allocations_by_id(context)
    holding = live_payments(store, bill.id, set(allocations))
    if len(holding) >= MAX_LIVE_PAYMENTS:
        raise BooksError(409, "This bill already has the most payments one bill can take.", "too_many_payments")
    balance = bill.total - paid_amount(holding)
    if balance <= 0:
        raise BooksError(409, "This bill is already paid.", "bill_paid")
    if kind == PaymentKind.ALLOCATION:
        allocation = allocations.get(allocation_id or "")
        if allocation is None:
            raise BooksError(404, "Allocation not found in this business.", "allocation_not_found")
        if allocation.status != "active":
            raise BooksError(409, "That allocation is waiting for review in Finance. Confirm it there first.", "allocation_in_review")
        if allocation.currency != bill.currency:
            raise BooksError(422, "The allocation is in another currency than the bill.", "currency_mismatch", currency=allocation.currency)
        if store.first("bill_payments", allocation_id=allocation.id, voided_at=None) is not None:
            raise BooksError(409, "That allocation already pays another bill.", "allocation_used")
        value = Decimal(allocation.amount)
        if value > balance:
            raise BooksError(409, "The allocation is more than the bill's balance. Split it in Finance first.", "over_balance",
                             remaining=str(balance))
        payment = BillPayment(id=new_id("bpay"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, bill_id=bill.id,
                              kind=kind, allocation_id=allocation.id, amount=value, currency=bill.currency, paid_on=allocation.posted_on,
                              method="allocation", reference=f"#{allocation.source_ref}", note=clean(note, 300), recorded_by=context.user_id,
                              idem_key=stored_key)
    else:
        value = amount_of(amount, bill.currency)
        if value > balance:
            raise BooksError(409, "The payment is more than the bill's balance.", "over_balance", remaining=str(balance))
        if method == "allocation":
            raise BooksError(422, "Choose how it was paid.", "invalid_method")
        day = not_future(paid_on or today, today, "payment date")
        payment = BillPayment(id=new_id("bpay"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, bill_id=bill.id,
                              kind=kind, amount=value, currency=bill.currency, paid_on=day, method=method, reference=clean(reference, 120),
                              note=clean(note, 300), recorded_by=context.user_id, idem_key=stored_key)
    from .books_ledger import require_open

    require_open(store, payment.paid_on, what="This payment")
    store.add("bill_payments", payment)
    # Bump the bill so a stale edit form cannot change the money under a payment.
    store.save("bills", bill.model_copy(update={"version": bill.version + 1, "updated_at": now}), expected_version=bill.version)
    return payment, False


# -- adjustments ----------------------------------------------------------------------------------------


def adjustment_from(context: TenantContext, store: BooksStore, payload: dict[str, Any], *, idem_key: str | None, today: date,
                    now: datetime) -> Adjustment:
    kind = AdjustmentKind(payload["kind"])
    currency = currency_of(payload["currency"])
    amount = amount_of(payload["amount"], currency)
    entry_date: date = payload["entry_date"]
    start_month: date | None = payload.get("start_month")
    months: int | None = payload.get("months")
    reverse_on: date | None = payload.get("reverse_on")
    spread = kind in (AdjustmentKind.PREPAYMENT, AdjustmentKind.CAPITAL_ITEM)
    if spread:
        if start_month is None or not months:
            raise BooksError(422, "Give the first month and how many months it covers.", "spread_required")
        start_month = start_month.replace(day=1)
    else:
        start_month, months = None, None
    if reverse_on is not None and (kind != AdjustmentKind.ACCRUAL or reverse_on <= entry_date):
        raise BooksError(422, "Only an accrual reverses, on a later date.", "invalid_reverse_on")
    if kind in (AdjustmentKind.LOAN_RECEIVED, AdjustmentKind.LOAN_REPAYMENT, AdjustmentKind.LOAN_INTEREST, AdjustmentKind.OWNER_CONTRIBUTION,
                AdjustmentKind.OWNER_DRAWING, AdjustmentKind.OWNER_REIMBURSEMENT):
        not_future(entry_date, today, "date")
    section = payload.get("section")
    if section is not None and kind not in (AdjustmentKind.ACCRUAL, AdjustmentKind.PREPAYMENT):
        section = None
    description = clean(payload["description"], 300)
    if len(description) < 3:
        raise BooksError(422, "Say what the adjustment is for (it stays in the trail).", "invalid_description")
    return Adjustment(
        id=new_id("badj"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, kind=kind, entry_date=entry_date,
        currency=currency, amount=amount, section=Section(section) if section and section != "capital" else None,
        category=category_of(payload.get("category"), required=False), cost_centre_id=require_cost_centre(store, payload.get("cost_centre_id")),
        project_id=require_project(context, payload.get("project_id")), description=description, start_month=start_month, months=months,
        reverse_on=reverse_on, idem_key=idem_key, created_by=context.user_id,
    )


SETTLES = {
    AdjustmentKind.OWNER_REIMBURSEMENT: ("owed_to_members", "what the business owes its members", "nothing_owed"),
    AdjustmentKind.LOAN_REPAYMENT: ("debt", "the loans outstanding", "nothing_owed"),
    AdjustmentKind.LOAN_INTEREST_PAID: ("interest_payable", "the interest owed", "nothing_owed"),
}


def check_settlement(context: TenantContext, item: Adjustment) -> None:
    """A reimbursement, a loan repayment or interest paid is at most what is owed in its currency, on its day and today (defect B4:
    owed-to-members, debt and interest payable never go below zero). Refuses with 422 and the amount still owed."""
    if item.kind not in SETTLES:
        return
    from .books_report import Book, balances

    key, words, _ = SETTLES[item.kind]
    book = Book(gather(context))
    owed = None
    for day in (item.entry_date, date.max):
        figures = balances(book, day)
        found = next((Decimal(row["amount"]) for row in figures[key] if row["currency"] == item.currency), Decimal(0))
        owed = found if owed is None else min(owed, found)
    remaining = max(owed or Decimal(0), Decimal(0))
    if item.amount > remaining:
        raise BooksError(422, f"This is more than {words} in {item.currency} ({remaining}). Record at most that amount.", "more_than_owed",
                         remaining=str(remaining), currency=item.currency)


# -- intercompany -----------------------------------------------------------------------------------------


def manageable_workspace(context: TenantContext, workspace_id: str) -> str | None:
    """The other business's name when the caller holds ``business:manage`` there (same tenant, active, not archived); else ``None``."""
    from ..dependencies import lookup_membership, workspace_archived
    from ..membership_directory import list_caller_memberships

    if workspace_id == context.workspace_id or not SAFE_ID.match(workspace_id):
        return None
    target = context.model_copy(update={"workspace_id": workspace_id})
    membership = lookup_membership(target)
    if membership is None or not membership.active:
        return None
    if Permission.BUSINESS_MANAGE not in ROLE_PERMISSIONS.get(membership.role, frozenset()):
        return None
    if workspace_archived(target):
        return None
    for row in list_caller_memberships(context):
        if row.workspace_id == workspace_id:
            return row.workspace_name or workspace_id
    return workspace_id


def counterparties(context: TenantContext) -> list[dict[str, str]]:
    from ..membership_directory import list_caller_memberships

    out = []
    for row in list_caller_memberships(context):
        if row.workspace_id == context.workspace_id or row.archived:
            continue
        try:
            role_permissions = ROLE_PERMISSIONS.get(row.role, frozenset())  # type: ignore[call-overload]
        except (KeyError, ValueError):
            continue
        if Permission.BUSINESS_MANAGE in role_permissions:
            out.append({"workspace_id": row.workspace_id, "name": row.workspace_name or row.workspace_id})
    return sorted(out, key=lambda item: item["name"].casefold())


def own_name(context: TenantContext) -> str:
    from ..membership_directory import list_caller_memberships

    for row in list_caller_memberships(context):
        if row.workspace_id == context.workspace_id:
            return row.workspace_name or row.workspace_id
    return context.workspace_id


MIRROR = {Direction.RECEIVABLE: Direction.PAYABLE, Direction.PAYABLE: Direction.RECEIVABLE}


def intercompany_pair(context: TenantContext, payload: dict[str, Any], other_name: str, *, today: date, now: datetime) -> tuple[Intercompany, Intercompany]:
    kind = IntercompanyKind(payload["kind"])
    direction = Direction(payload["direction"])
    currency = currency_of(payload["currency"])
    amount = amount_of(payload["amount"], currency)
    entry_date = not_future(payload["entry_date"], today, "date")
    description = clean(payload["description"], 300)
    if len(description) < 3:
        raise BooksError(422, "Say what the entry is for; both businesses see it.", "invalid_description")
    pair = new_id("icp")
    common = dict(tenant_id=context.tenant_id, created_at=now, pair_id=pair, kind=kind, entry_date=entry_date, currency=currency, amount=amount,
                  description=description, created_by=context.user_id)
    mine = Intercompany(id=new_id("bic"), workspace_id=context.workspace_id, counterparty_workspace_id=payload["workspace_id"],
                        counterparty_name=clean(other_name, 200), direction=direction, **common)
    theirs = Intercompany(id=new_id("bic"), workspace_id=payload["workspace_id"], counterparty_workspace_id=context.workspace_id,
                          counterparty_name=clean(own_name(context), 200), direction=MIRROR[direction], **common)
    return mine, theirs


# -- the report's inputs ------------------------------------------------------------------------------------


RATE_SCAN = 20_000  # automatic rates add a few rows a business day, so rates have their own, larger window


def gather(context: TenantContext, rates_since: date | None = None) -> Inputs:
    """Every record one business's figures are made from: three short reads, no transaction held across them.

    ``rates_since``: read only the exchange rates dated on or after this day (a report needs the rates of its own
    period and a margin before it; automatic rates grow by a few rows every business day)."""
    from .invoice_models import InvoiceStatus
    from .store import Query, open_business_store

    truncated = False
    with open_books_store(context) as store:
        settings = settings_or_409(store)
        found: dict[str, list[Any]] = {}
        for kind in ("bills", "bill_payments", "adjustments", "intercompany", "budgets", "allocation_tags", "vendors", "cost_centres", "templates"):
            # Voided bill payments never count, so they are not read (and never crowd out the ones that do).
            rows, total = store.find(kind, where={"voided_at": None} if kind == "bill_payments" else None, limit=MAX_SCAN)
            truncated = truncated or total > MAX_SCAN
            found[kind] = rows
        rows, total = store.find("rates", where={"as_of": AtLeast(rates_since)} if rates_since else None, order_by="as_of", limit=RATE_SCAN)
        truncated = truncated or total > RATE_SCAN
        found["rates"] = rows
    with open_business_store(context) as business:
        invoices, total = business.find("invoices", Query(where={"status": {InvoiceStatus.SENT, InvoiceStatus.PAID}}, order_by="created_at", limit=MAX_SCAN))
        truncated = truncated or total > MAX_SCAN
        payments, total = business.find("invoice_payments", Query(order_by="created_at", limit=MAX_SCAN))
        truncated = truncated or total > MAX_SCAN
        notes, total = business.find("credit_notes", Query(order_by="created_at", limit=MAX_SCAN))
        truncated = truncated or total > MAX_SCAN
        projects, total = business.find("projects", Query(order_by="created_at", limit=MAX_SCAN))
        truncated = truncated or total > MAX_SCAN  # defect B10: names beyond the window would read as missing
        accounts, total = business.find("accounts", Query(order_by="created_at", limit=MAX_SCAN))
        truncated = truncated or total > MAX_SCAN
    allocations = list(allocations_by_id(context).values())
    truncated = truncated or len(allocations) >= MAX_SCAN
    return Inputs(
        settings=settings, invoices=invoices, payments=payments, credit_notes=notes, allocations=allocations,
        tags={tag.allocation_id: tag for tag in found["allocation_tags"]}, bills=found["bills"], bill_payments=found["bill_payments"],
        adjustments=found["adjustments"], intercompany=found["intercompany"], budgets=found["budgets"], rates=found["rates"],
        projects={p.id: (p.name, p.account_id) for p in projects}, accounts={a.id: a.name for a in accounts},
        vendors={v.id: v.name for v in found["vendors"]}, cost_centres={c.id: f"{c.code} {c.name}" for c in found["cost_centres"]},
        templates=found["templates"], truncated=truncated,
    )


__all__ = ["BooksError", "gather", "pay_bill"]
