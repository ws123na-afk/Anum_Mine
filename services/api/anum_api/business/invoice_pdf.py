"""The invoice as a PDF file made on the server (docs/business.md#printing-and-pdf).

Written with ANUM's dependency-free PDF writer (``tyre/pdf.py``): A4 pages, the built-in
Helvetica fonts, no network and no font download. Those fonts cover Latin text only, so a
character outside it (Arabic, for instance) prints as "?"; the printable HTML page
(``invoice_print.py``) covers Arabic and right-to-left layout, and the PDF says nothing the
HTML page does not. The figures come from the stored invoice, never from the request. A draft
or a voided invoice says so on its first line, so an unsent copy cannot pass for a real one.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..tyre.pdf import PdfDocument
from .invoice_models import CreditNote, Invoice, InvoicePayment, InvoiceStatus
from .models import BillingProfile

STATUS_LINE = {
    InvoiceStatus.DRAFT: "DRAFT: not sent. This copy is not an invoice.",
    InvoiceStatus.PENDING_APPROVAL: "WAITING FOR APPROVAL: not sent. This copy is not an invoice.",
    InvoiceStatus.VOID: "VOID: this invoice was cancelled.",
    InvoiceStatus.PAID: "PAID",
}
METHODS = {"bank_transfer": "Bank transfer", "cash": "Cash", "card": "Card", "cheque": "Cheque", "other": "Other"}


def _amount(value: Decimal | float) -> str:
    return f"{Decimal(str(value)):,.2f}"


def _plain(value: Decimal | float) -> str:
    return f"{Decimal(str(value)).normalize():f}"


def invoice_filename(invoice: Invoice) -> str:
    """A safe file name: the invoice number, or ``draft-<id>`` before it has one."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", invoice.number or f"draft-{invoice.id}").strip("-") or "invoice"
    return f"{stem}.pdf"


def render_invoice_pdf(invoice: Invoice, payments: list[InvoicePayment], credit_notes: list[CreditNote], seller_fallback: BillingProfile) -> bytes:
    """The PDF bytes. The seller printed is the one stored on a sent invoice; a draft shows the current billing profile."""
    seller = invoice.seller or seller_fallback
    currency = invoice.currency.value
    title = "Tax invoice" if invoice.tax > 0 else "Invoice"
    number = invoice.number or "(draft)"
    document = PdfDocument(footer=f"{seller.legal_name or 'ANUM'} - {title} {number}")
    document.heading(f"{title} {number}")
    stamp = STATUS_LINE.get(invoice.status)
    if stamp:
        document.paragraph(stamp, size=10)
    dates = [f"Issued {invoice.issue_date.isoformat()}" if invoice.issue_date else "", f"Due {invoice.due_date.isoformat()}" if invoice.due_date else "",
             f"Your reference {invoice.customer_reference}" if invoice.customer_reference else ""]
    if any(dates):
        document.paragraph(". ".join(part for part in dates if part) + ".")
    document.paragraph("From: " + ", ".join(part for part in (seller.legal_name, seller.address.replace("\n", ", "),
                                                               f"{seller.tax_label}: {seller.tax_number}" if seller.tax_number else "", seller.email, seller.phone) if part))
    document.paragraph("Bill to: " + ", ".join(part for part in (invoice.bill_to_name, invoice.bill_to_address.replace("\n", ", "),
                                                                  f"Tax number: {invoice.bill_to_tax_number}" if invoice.bill_to_tax_number else "", invoice.bill_to_email) if part))
    document.table(f"Lines ({currency})", ["Description", "Qty", "Unit price", "Tax %", "Amount"],
                   [[line.description, _plain(line.quantity), _amount(line.unit_price), _plain(line.tax_rate_pct), _amount(line.total)] for line in invoice.lines])
    totals: list[list[str]] = [["Subtotal", _amount(invoice.subtotal)], ["Tax", _amount(invoice.tax)], ["Total", _amount(invoice.total)]]
    if invoice.amount_credited > 0:
        totals.append(["Credit notes", "-" + _amount(invoice.amount_credited)])
    if invoice.amount_paid > 0:
        totals.append(["Paid", _amount(invoice.amount_paid)])
    if invoice.amount_paid > 0 or invoice.amount_credited > 0:
        totals.append(["Balance due", _amount(invoice.balance_due())])
    document.table(f"Totals ({currency})", ["Item", "Amount"], totals)
    if credit_notes:
        document.table("Credit notes", ["Number", "Date", "Reason", "Amount", "Status"],
                       [[item.number, item.issue_date.isoformat(), item.reason, _amount(item.amount), item.status.value] for item in credit_notes])
    if payments:
        document.table("Payments received", ["Date", "Method", "Reference", "Amount"],
                       [[item.paid_on.isoformat(), METHODS.get(item.method.value, item.method.value), item.reference, _amount(item.amount)] for item in payments])
    for heading, value in (("Payment terms", invoice.payment_terms), ("How to pay", seller.bank_details), ("Notes", invoice.notes), ("", seller.footer_note)):
        if value.strip():
            if heading:
                document.paragraph(heading + ":", size=9)
            for line in value.splitlines():
                if line.strip():
                    document.paragraph(line.strip())
    return document.render()
