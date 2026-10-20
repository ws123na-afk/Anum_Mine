"""Invoice rules, pure and in Decimal (docs/business.md#invoices).

The totals, the hash the owner's send approval binds, the number format and the payment
state. Nothing here reads a store or sends anything.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from .invoice_models import Invoice, InvoiceLine, InvoiceStatus, price_line
from .models import money


def reprice(lines: list[InvoiceLine]) -> list[InvoiceLine]:
    return [price_line(item) for item in lines]


def line_totals(lines: list[InvoiceLine]) -> tuple[Decimal, Decimal, Decimal]:
    """(subtotal, tax, total) of lines that were already priced."""
    subtotal = money(sum((item.net for item in lines), Decimal(0)))
    tax = money(sum((item.tax for item in lines), Decimal(0)))
    return subtotal, tax, money(subtotal + tax)


def apply_lines(invoice: Invoice, lines: list[InvoiceLine]) -> Invoice:
    """The invoice with these lines priced and its totals worked out."""
    priced = reprice(lines)
    subtotal, tax, total = line_totals(priced)
    return invoice.model_copy(update={"lines": priced, "subtotal": subtotal, "tax": tax, "total": total})


def invoice_hash(invoice: Invoice) -> str:
    """SHA-256 of what the owner approves sending: the buyer, the seller as it will print,
    every line and every figure. Dates are not in it; they are set when it is sent."""
    document: dict[str, Any] = {
        "account_id": invoice.account_id,
        "project_id": invoice.project_id,
        "currency": invoice.currency.value,
        "lines": [item.model_dump(mode="json") for item in invoice.lines],
        "subtotal": str(money(invoice.subtotal)),
        "tax": str(money(invoice.tax)),
        "total": str(money(invoice.total)),
        "due_in_days": invoice.due_in_days,
        "customer_reference": invoice.customer_reference,
        "notes": invoice.notes,
        "payment_terms": invoice.payment_terms,
        "bill_to": [invoice.bill_to_name, invoice.bill_to_address, invoice.bill_to_email, invoice.bill_to_tax_number],
        "seller": invoice.seller.model_dump(mode="json") if invoice.seller is not None else None,
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def format_number(prefix: str, year: int, seq: int) -> str:
    return f"{prefix}-{year}-{seq:04d}"


def problems(invoice: Invoice) -> list[str]:
    """Why this draft cannot be sent for approval yet (shown to the person)."""
    found: list[str] = []
    if not invoice.lines:
        found.append("Add at least one line.")
    if invoice.lines and invoice.total <= 0:
        found.append("The invoice total is zero. Price at least one line.")
    if any(item.net <= 0 and item.gross_total is None for item in invoice.lines):
        found.append("Every line needs a price above zero.")
    if not invoice.bill_to_name.strip():
        found.append("Name the customer the invoice is made out to.")
    if invoice.seller is None or not invoice.seller.legal_name.strip():
        found.append("Fill in your billing profile (your legal name) before sending an invoice.")
    elif invoice.tax > 0 and not invoice.seller.tax_number.strip():
        found.append(f"This invoice charges tax: add your {invoice.seller.tax_label.lower()} to the billing profile.")
    return found


def payment_state(invoice: Invoice, today: date) -> dict[str, Any]:
    """The balance and whether a sent invoice is late. A paid or void invoice is never late."""
    balance = invoice.balance_due()
    open_ = invoice.status == InvoiceStatus.SENT
    overdue = bool(open_ and invoice.due_date is not None and invoice.due_date < today and balance > 0)
    if invoice.status == InvoiceStatus.PAID or (invoice.total > 0 and balance <= 0):
        # Settled by credit notes alone is "credited", not "paid": no money arrived.
        state = "credited" if invoice.amount_paid <= 0 and invoice.amount_credited > 0 else "paid"
    elif invoice.amount_paid > 0:
        state = "partially_paid"
    else:
        state = "unpaid"
    return {
        "balance": balance,
        "overdue": overdue,
        "days_overdue": (today - invoice.due_date).days if overdue and invoice.due_date else 0,
        "payment_state": state,
    }
