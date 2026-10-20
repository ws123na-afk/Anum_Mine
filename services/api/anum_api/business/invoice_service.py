"""Invoice operations that read and write the store, shared by the routes and the send tool
(docs/business.md#invoices)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from ..schemas import TenantContext, new_id
from .invoice_models import (
    CreditNote,
    CreditNoteStatus,
    Invoice,
    InvoiceCounter,
    InvoicePayment,
    InvoiceStatus,
    LineSource,
    PaymentMethod,
    PaymentNotice,
)
from .invoices import format_number
from .models import Milestone, money
from .service import http_error
from .store import BusinessStore, Query

# Rows read at most when the invoices of one account or project are scanned.
SCAN = 2000


def take_number(store: BusinessStore, context: TenantContext, year: int, now: datetime) -> int:
    """The next number of the year. The counter row is locked, so two sends never get the same one."""
    found, _ = store.find("invoice_counters", Query(where={"year": year}, limit=1, order_by="created_at"))
    if found:
        counter = store.get("invoice_counters", found[0].id, for_update=True)
        counter = counter.model_copy(update={"last_seq": counter.last_seq + 1, "updated_at": now})
        store.save("invoice_counters", counter)
        return counter.last_seq
    counter = InvoiceCounter(id=new_id("binvc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, year=year, last_seq=1, created_at=now, updated_at=now)
    store.add("invoice_counters", counter)
    return 1


def issue(invoice: Invoice, store: BusinessStore, context: TenantContext, now: datetime) -> Invoice:
    """The invoice as sent: numbered, dated and attributed. The caller saves it."""
    today = now.date()
    seq = take_number(store, context, today.year, now)
    prefix = invoice.seller.invoice_prefix if invoice.seller is not None else "INV"
    return invoice.model_copy(update={
        "status": InvoiceStatus.SENT, "seq": seq, "number": format_number(prefix, today.year, seq), "issue_date": today,
        "due_date": today + timedelta(days=invoice.due_in_days), "sent_by": context.user_id, "sent_at": now, "updated_at": now,
    })


def billed_sources(store: BusinessStore, account_id: str, *, exclude: str | None = None) -> dict[tuple[str, str], str]:
    """``(source, source_id)`` of every line on a not-void invoice of the account, with the
    invoice id: a milestone or a usage entry is billed once."""
    items, _ = store.find("invoices", Query(where={"account_id": account_id}, limit=SCAN, order_by="created_at"))
    found: dict[tuple[str, str], str] = {}
    for item in items:
        if item.status == InvoiceStatus.VOID or item.id == exclude:
            continue
        for line in item.lines:
            if line.source_id and line.source in {LineSource.MILESTONE, LineSource.USAGE}:
                found[(line.source.value, line.source_id)] = item.id
    return found


def contract_billed(store: BusinessStore, project_id: str, *, exclude: str | None = None) -> Decimal:
    """The tax-inclusive contract amount already on not-void invoices of the project."""
    items, _ = store.find("invoices", Query(where={"project_id": project_id}, limit=SCAN, order_by="created_at"))
    return money(sum((line.total for item in items if item.status != InvoiceStatus.VOID and item.id != exclude
                      for line in item.lines if line.source == LineSource.CONTRACT), Decimal(0)))


def payments_of(store: BusinessStore, invoice_id: str) -> list[InvoicePayment]:
    items, _ = store.find("invoice_payments", Query(where={"invoice_id": invoice_id}, order_by="paid_on", descending=False, limit=500))
    return items


def mark_milestones_paid(store: BusinessStore, invoice: Invoice, paid_on: date, now: datetime) -> list[str]:
    """A fully paid invoice records its milestones' payment (cash collected). Returns the
    ids it marked; a milestone a person already marked paid is left alone."""
    marked: list[str] = []
    for line in invoice.lines:
        if line.source != LineSource.MILESTONE or not line.source_id:
            continue
        milestone: Milestone | None = store.get("milestones", line.source_id, for_update=True)
        if milestone is None or milestone.paid_amount is not None:
            continue
        store.save("milestones", milestone.model_copy(update={"paid_amount": milestone.amount, "paid_on": paid_on, "updated_at": now}))
        marked.append(milestone.id)
    return marked


def credit_notes_of(store: BusinessStore, invoice_id: str) -> list[CreditNote]:
    """Every credit note of the invoice, oldest first (void ones included: they keep their number)."""
    items, _ = store.find("credit_notes", Query(where={"invoice_id": invoice_id}, order_by="seq", descending=False, limit=500))
    return items


def notices_of(store: BusinessStore, invoice_id: str) -> list[PaymentNotice]:
    items, _ = store.find("payment_notices", Query(where={"invoice_id": invoice_id}, order_by="created_at", descending=True, limit=200))
    return items


def credit_split(invoice: Invoice, existing: list[CreditNote], amount: Decimal) -> tuple[Decimal, Decimal]:
    """(subtotal, tax) of a credit of ``amount`` (tax inclusive). The tax follows the invoice's own
    proportion on the running total of credits, so the notes of one invoice add up to its tax exactly."""
    in_force = [item for item in existing if item.status == CreditNoteStatus.ISSUED]
    credited = money(sum((item.amount for item in in_force), Decimal(0)))
    tax_credited = money(sum((item.tax for item in in_force), Decimal(0)))
    if invoice.total <= 0:
        return money(amount), Decimal("0.00")
    cumulative_tax = money((credited + amount) * invoice.tax / invoice.total)
    tax = min(max(money(cumulative_tax - tax_credited), Decimal(0)), amount)
    return money(amount - tax), tax


def apply_payment(
    store: BusinessStore, context: TenantContext, invoice: Invoice, *, amount: Decimal, paid_on: date, method: PaymentMethod, reference: str, note: str, now: datetime,
) -> tuple[Invoice, InvoicePayment]:
    """Record a payment on a sent invoice (the rules both the owner's form and the confirmation of a
    customer's notice follow). The invoice is paid when payments and credits cover its total; its
    milestones are marked paid (cash collected) only when no credit note was needed to settle it."""
    if invoice.status != InvoiceStatus.SENT:
        raise http_error(409, f"The invoice is {invoice.status.value.replace('_', ' ')}; payments are recorded on a sent invoice.")
    if amount <= 0:
        raise http_error(422, "Enter an amount above zero.")
    balance = invoice.balance_due()
    if amount > balance:
        raise http_error(422, f"The payment is more than the balance of {invoice.currency.value} {balance}.")
    if paid_on > now.date():
        raise http_error(422, "The payment date cannot be in the future.")
    if invoice.issue_date and paid_on < invoice.issue_date:
        raise http_error(422, f"The payment date is before the invoice was issued on {invoice.issue_date}.")
    payment = InvoicePayment(
        id=new_id("binpay"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, invoice_id=invoice.id, amount=amount, paid_on=paid_on,
        method=method, reference=reference, note=note, recorded_by=context.user_id, created_at=now, updated_at=now,
    )
    store.add("invoice_payments", payment)
    paid = money(invoice.amount_paid + amount)
    update: dict[str, object] = {"amount_paid": paid, "updated_at": now}
    if money(paid + invoice.amount_credited) >= invoice.total:
        update.update({"status": InvoiceStatus.PAID, "paid_at": now})
        if invoice.amount_credited <= 0:
            update["marked_milestones"] = mark_milestones_paid(store, invoice, paid_on, now)
    invoice = invoice.model_copy(update=update)
    store.save("invoices", invoice)
    return invoice, payment


def release_milestones(store: BusinessStore, invoice: Invoice, now: datetime) -> None:
    """Undo ``mark_milestones_paid`` when a payment is removed."""
    for milestone_id in invoice.marked_milestones:
        milestone: Milestone | None = store.get("milestones", milestone_id, for_update=True)
        if milestone is not None and milestone.paid_amount == milestone.amount:
            store.save("milestones", milestone.model_copy(update={"paid_amount": None, "paid_on": None, "updated_at": now}))
