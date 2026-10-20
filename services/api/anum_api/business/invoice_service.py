"""Invoice operations that read and write the store, shared by the routes and the send tool
(docs/business.md#invoices)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from ..schemas import TenantContext, new_id
from .invoice_models import Invoice, InvoiceCounter, InvoicePayment, InvoiceStatus, LineSource
from .invoices import format_number
from .models import Milestone, money
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


def release_milestones(store: BusinessStore, invoice: Invoice, now: datetime) -> None:
    """Undo ``mark_milestones_paid`` when a payment is removed."""
    for milestone_id in invoice.marked_milestones:
        milestone: Milestone | None = store.get("milestones", milestone_id, for_update=True)
        if milestone is not None and milestone.paid_amount == milestone.amount:
            store.save("milestones", milestone.model_copy(update={"paid_amount": None, "paid_on": None, "updated_at": now}))
