"""The printable invoice: one self-contained HTML page (docs/business.md#printing-and-pdf).

No script, no external file and no font download: it prints from any browser ("Save as PDF"
gives the PDF) and shows the same figures the invoice stores. Every value is escaped. A draft
or a voided invoice says so across the page, so an unsent copy cannot pass for a real one.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Literal

from .invoice_models import CreditNote, CreditNoteStatus, Invoice, InvoiceLine, InvoicePayment, InvoiceStatus
from .models import BillingProfile

Language = Literal["en", "ar"]

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "invoice": "Invoice", "tax_invoice": "Tax invoice", "number": "Invoice number", "issued": "Issue date", "due": "Due date",
        "reference": "Your reference", "bill_to": "Bill to", "from": "From", "description": "Description", "qty": "Qty",
        "unit": "Unit price", "tax": "Tax", "rate": "Rate", "amount": "Amount", "subtotal": "Subtotal", "total": "Total",
        "paid": "Paid", "balance": "Balance due", "terms": "Payment terms", "bank": "How to pay", "notes": "Notes",
        "payments": "Payments received", "credited": "Credit notes", "credit_notes": "Credit notes", "reason": "Reason", "date": "Date", "draft": "Draft: not sent", "pending": "Waiting for approval: not sent", "void": "Void",
        "paid_stamp": "Paid", "page_title": "Invoice", "email": "Email", "phone": "Phone", "tax_no": "Tax number",
    },
    "ar": {
        "invoice": "فاتورة", "tax_invoice": "فاتورة ضريبية", "number": "رقم الفاتورة", "issued": "تاريخ الإصدار", "due": "تاريخ الاستحقاق",
        "reference": "مرجعكم", "bill_to": "فاتورة إلى", "from": "من", "description": "الوصف", "qty": "الكمية",
        "unit": "سعر الوحدة", "tax": "الضريبة", "rate": "النسبة", "amount": "المبلغ", "subtotal": "المجموع الفرعي", "total": "الإجمالي",
        "paid": "المدفوع", "balance": "المستحق", "terms": "شروط الدفع", "bank": "طريقة الدفع", "notes": "ملاحظات",
        "payments": "الدفعات المستلمة", "credited": "الإشعارات الدائنة", "credit_notes": "الإشعارات الدائنة", "reason": "السبب", "date": "التاريخ", "draft": "مسودة: لم ترسل", "pending": "بانتظار الموافقة: لم ترسل", "void": "ملغاة",
        "paid_stamp": "مدفوعة", "page_title": "فاتورة", "email": "البريد الإلكتروني", "phone": "الهاتف", "tax_no": "الرقم الضريبي",
    },
}

STYLE = """
@page { size: A4; margin: 16mm; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; color: #14213d; background: #fff; font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Tahoma, "Noto Naskh Arabic", sans-serif; }
.sheet { max-width: 800px; margin: 0 auto; position: relative; }
h1 { font-size: 26px; margin: 0 0 4px; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 16px 0 24px; }
.meta dt { font-size: 12px; color: #5c6784; }
.meta dd { margin: 0; font-weight: 600; }
.parties { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 24px; margin-bottom: 24px; }
.parties h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #5c6784; margin: 0 0 4px; }
.parties p { margin: 0; white-space: pre-line; }
table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #d9deea; text-align: start; vertical-align: top; }
th { font-size: 12px; color: #5c6784; font-weight: 600; background: #f4f6fb; }
td.num, th.num { text-align: end; font-variant-numeric: tabular-nums; white-space: nowrap; }
.totals { margin-inline-start: auto; width: min(340px, 100%); }
.totals td { border-bottom: 0; padding: 4px 10px; }
.totals tr.grand td { font-weight: 700; font-size: 16px; border-top: 2px solid #14213d; }
.block { margin-top: 20px; }
.block h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #5c6784; margin: 0 0 4px; }
.block p { margin: 0; white-space: pre-line; }
.stamp { position: absolute; inset-block-start: 40px; inset-inline-end: 0; border: 3px solid currentColor; padding: 4px 14px; font-size: 20px; font-weight: 700; transform: rotate(-6deg); opacity: .8; }
.stamp.draft { color: #b35c00; }
.stamp.void { color: #b00020; }
.stamp.paid { color: #1b7a3a; }
.foot { margin-top: 28px; font-size: 12px; color: #5c6784; border-top: 1px solid #d9deea; padding-top: 8px; }
@media print { body { padding: 0; } }
"""


def _amount(value: Decimal | float) -> str:
    return f"{Decimal(str(value)):,.2f}"


def _text(value: str | None) -> str:
    return escape(value or "", quote=True)


def _party(heading: str, lines: list[tuple[str, str]]) -> str:
    rows = "".join(f"<p>{_text(text)}</p>" if not name else f"<p>{_text(name)}: {_text(text)}</p>" for name, text in lines if text)
    return f"<div><h2>{_text(heading)}</h2>{rows}</div>"


def _row(line: InvoiceLine) -> str:
    quantity = f"{Decimal(str(line.quantity)).normalize():f}"
    return (
        f"<tr><td>{_text(line.description)}</td><td class=\"num\">{_text(quantity)}</td><td class=\"num\">{_text(_amount(line.unit_price))}</td>"
        f"<td class=\"num\">{_text(f'{Decimal(str(line.tax_rate_pct)).normalize():f}')}%</td><td class=\"num\">{_text(_amount(line.total))}</td></tr>"
    )


def render_invoice(invoice: Invoice, payments: list[InvoicePayment], seller_fallback: BillingProfile, language: Language = "en", credit_notes: list[CreditNote] | None = None) -> str:
    """The invoice as an HTML document. The seller printed is the one stored on a sent
    invoice; a draft shows the current billing profile."""
    language = "ar" if language == "ar" else "en"  # a constant, never the request's own string
    label = LABELS[language]
    seller = invoice.seller or seller_fallback
    direction = "rtl" if language == "ar" else "ltr"
    currency = invoice.currency.value
    title = label["tax_invoice"] if invoice.tax > 0 else label["invoice"]
    number = invoice.number or ""
    stamp = ""
    if invoice.status == InvoiceStatus.DRAFT:
        stamp = f"<div class=\"stamp draft\">{_text(label['draft'])}</div>"
    elif invoice.status == InvoiceStatus.PENDING_APPROVAL:
        stamp = f"<div class=\"stamp draft\">{_text(label['pending'])}</div>"
    elif invoice.status == InvoiceStatus.VOID:
        stamp = f"<div class=\"stamp void\">{_text(label['void'])}</div>"
    elif invoice.status == InvoiceStatus.PAID:
        stamp = f"<div class=\"stamp paid\">{_text(label['paid_stamp'])}</div>"
    meta = [(label["number"], number), (label["issued"], invoice.issue_date.isoformat() if invoice.issue_date else ""),
            (label["due"], invoice.due_date.isoformat() if invoice.due_date else ""), (label["reference"], invoice.customer_reference)]
    meta_html = "".join(f"<div><dt>{_text(name)}</dt><dd>{_text(value)}</dd></div>" for name, value in meta if value)
    seller_lines = [("", seller.legal_name), ("", seller.address), (seller.tax_label, seller.tax_number), (label["email"], seller.email), (label["phone"], seller.phone)]
    buyer_lines = [("", invoice.bill_to_name), ("", invoice.bill_to_address), (label["tax_no"], invoice.bill_to_tax_number), (label["email"], invoice.bill_to_email)]
    rows = "".join(_row(item) for item in invoice.lines)
    totals = [(label["subtotal"], invoice.subtotal), (label["tax"], invoice.tax)]
    totals_html = "".join(f"<tr><td>{_text(name)}</td><td class=\"num\">{_text(_amount(value))}</td></tr>" for name, value in totals)
    totals_html += f"<tr class=\"grand\"><td>{_text(label['total'])} ({_text(currency)})</td><td class=\"num\">{_text(_amount(invoice.total))}</td></tr>"
    if invoice.amount_credited > 0:
        totals_html += f"<tr><td>{_text(label['credited'])}</td><td class=\"num\">-{_text(_amount(invoice.amount_credited))}</td></tr>"
    if invoice.amount_paid > 0:
        totals_html += f"<tr><td>{_text(label['paid'])}</td><td class=\"num\">{_text(_amount(invoice.amount_paid))}</td></tr>"
    if invoice.amount_paid > 0 or invoice.amount_credited > 0:
        totals_html += f"<tr class=\"grand\"><td>{_text(label['balance'])}</td><td class=\"num\">{_text(_amount(invoice.balance_due()))}</td></tr>"
    blocks = ""
    for heading, value in ((label["terms"], invoice.payment_terms), (label["bank"], seller.bank_details), (label["notes"], invoice.notes)):
        if value.strip():
            blocks += f"<div class=\"block\"><h2>{_text(heading)}</h2><p>{_text(value)}</p></div>"
    if payments:
        paid_rows = "".join(
            f"<tr><td>{_text(payment.paid_on.isoformat())}</td><td>{_text(payment.reference)}</td><td class=\"num\">{_text(_amount(payment.amount))}</td></tr>" for payment in payments
        )
        blocks += f"<div class=\"block\"><h2>{_text(label['payments'])}</h2><table><tbody>{paid_rows}</tbody></table></div>"
    if credit_notes:
        credit_rows = "".join(
            f"<tr><td>{_text(item.number)}</td><td>{_text(item.issue_date.isoformat())}</td><td>{_text(item.reason)}"
            f"{' (' + _text(label['void']) + ')' if item.status == CreditNoteStatus.VOID else ''}</td><td class=\"num\">{_text(_amount(item.amount))}</td></tr>"
            for item in credit_notes
        )
        blocks += f"<div class=\"block\"><h2>{_text(label['credit_notes'])}</h2><table><tbody>{credit_rows}</tbody></table></div>"
    foot = f"<div class=\"foot\">{_text(seller.footer_note)}</div>" if seller.footer_note.strip() else ""
    return (
        f"<!doctype html><html lang=\"{language}\" dir=\"{direction}\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{_text(label['page_title'])} {_text(number)}</title>"
        f"<style>{STYLE}</style></head><body><main class=\"sheet\">{stamp}<h1>{_text(title)}</h1><dl class=\"meta\">{meta_html}</dl>"
        f"<section class=\"parties\">{_party(label['from'], seller_lines)}{_party(label['bill_to'], buyer_lines)}</section>"
        f"<table><thead><tr><th>{_text(label['description'])}</th><th class=\"num\">{_text(label['qty'])}</th><th class=\"num\">{_text(label['unit'])}</th>"
        f"<th class=\"num\">{_text(label['rate'])}</th><th class=\"num\">{_text(label['amount'])} ({_text(currency)})</th></tr></thead><tbody>{rows}</tbody></table>"
        f"<table class=\"totals\"><tbody>{totals_html}</tbody></table>{blocks}{foot}</main></body></html>"
    )
