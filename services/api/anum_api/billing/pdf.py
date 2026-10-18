"""Invoice PDF, written with ANUM's dependency-free PDF writer (``tyre/pdf.py``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..tyre.pdf import PdfDocument

DECIMALS = {"KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "JPY": 0}


def money(amount_minor: int, currency: str) -> str:
    digits = DECIMALS.get(currency, 2)
    sign = "-" if amount_minor < 0 else ""
    value = abs(int(amount_minor))
    if digits == 0:
        return f"{sign}{value:,} {currency}"
    whole, fraction = divmod(value, 10**digits)
    return f"{sign}{whole:,}.{fraction:0{digits}d} {currency}"


def _date(value: datetime | None) -> str:
    return value.date().isoformat() if value else "-"


def invoice_filename(invoice: dict[str, Any]) -> str:
    return f"{invoice.get('number') or invoice['id']}.pdf"


def render_invoice(detail: dict[str, Any]) -> bytes:
    invoice = detail["invoice"]
    currency = invoice["currency"]
    issuer = invoice.get("issuer") or {}
    bill_to = invoice.get("bill_to") or {}
    title = "Tax invoice" if int(invoice.get("tax_rate_bp") or 0) else "Invoice"
    document = PdfDocument(footer=f"{issuer.get('issuer_name') or 'ANUM'} - {title} {invoice.get('number') or ''}")
    document.heading(f"{title} {invoice.get('number') or '(draft)'}")
    document.paragraph(
        f"Issued {_date(invoice.get('issued_at'))}. Due {_date(invoice.get('due_at'))}. Status: {invoice['status']}."
        + (f" Period {_date(invoice.get('period_start'))} to {_date(invoice.get('period_end'))}." if invoice.get("period_start") else "")
    )
    document.paragraph(
        "From: " + ", ".join(v for v in (issuer.get("issuer_name"), issuer.get("issuer_address"),
                                       f"VAT {issuer['issuer_vat_number']}" if issuer.get("issuer_vat_number") else "",
                                       issuer.get("issuer_email")) if v),
        size=9,
    )
    address = ", ".join(v for v in (bill_to.get("address_line1"), bill_to.get("address_line2"), bill_to.get("city"),
                                    bill_to.get("postal_code"), bill_to.get("country")) if v)
    document.paragraph(
        "Bill to: " + ", ".join(v for v in (bill_to.get("company_name") or bill_to.get("display_name"), bill_to.get("contact_name"),
                                          bill_to.get("contact_email"), address,
                                          f"VAT {bill_to['vat_number']}" if bill_to.get("vat_number") else "") if v),
        size=9,
    )
    document.table(
        "Lines", ["Description", "Quantity", "Unit", "Amount"],
        [[line["description"], line["quantity"], money(int(line["unit_minor"]), currency), money(int(line["amount_minor"]), currency)]
         for line in detail["lines"]],
    )
    rate = int(invoice.get("tax_rate_bp") or 0)
    totals = [
        ["Subtotal", money(int(invoice["subtotal_minor"]), currency)],
        [f"VAT {rate / 100:g}% ({invoice.get('tax_country') or '-'})", money(int(invoice["tax_minor"]), currency)],
        ["Total", money(int(invoice["total_minor"]), currency)],
        ["Paid", money(int(invoice["amount_paid_minor"]), currency)],
        ["Credited", money(int(invoice["amount_credited_minor"]), currency)],
        ["Amount due", money(int(invoice["amount_due_minor"]), currency)],
    ]
    document.table("Totals", ["", "Amount"], totals)
    if detail.get("payments"):
        document.table(
            "Payments", ["Date", "Method", "Reference", "Amount"],
            [[_date(p["received_at"]), p["method"].replace("_", " "), p.get("reference") or "-", money(int(p["amount_minor"]), currency)]
             for p in detail["payments"]],
        )
    if detail.get("credit_notes"):
        document.table(
            "Credit notes", ["Number", "Date", "Reason", "Amount"],
            [[n["number"], _date(n["issued_at"]), n["reason"], money(int(n["total_minor"]), currency)] for n in detail["credit_notes"]],
        )
    if issuer.get("bank_iban") and int(invoice["amount_due_minor"]) > 0:
        document.paragraph(
            f"Pay by bank transfer to {issuer.get('bank_account_name') or issuer.get('issuer_name')}, {issuer.get('bank_name')}, "
            f"IBAN {issuer['bank_iban']}" + (f", SWIFT {issuer['bank_swift']}" if issuer.get("bank_swift") else "")
            + f". Use {invoice.get('number')} as the reference.",
            size=9,
        )
    return document.render()
