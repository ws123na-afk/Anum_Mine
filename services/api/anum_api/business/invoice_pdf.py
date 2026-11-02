"""The invoice, a payment receipt and a credit note as PDF files made on the server
(docs/business.md#printing-and-pdf).

Written with ANUM's dependency-free PDF writer (``pdf_writer.py``): A4 pages, no network and no
font download. Every label is in English and Arabic (English on the left, Arabic on the right,
as UAE and Saudi tax invoices commonly are). Latin text uses the built-in Helvetica; Arabic
names, addresses and descriptions are shaped and drawn with the embedded Noto Sans Arabic, so
they print as Arabic. The figures come from the stored invoice, never from the request. A
draft or a voided invoice says so on its first line, so an unsent copy cannot pass for a real one.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..pdf_writer import PdfDocument
from .invoice_models import CreditNote, CreditNoteStatus, Invoice, InvoicePayment, InvoiceStatus
from .models import BillingProfile

STATUS_LINE = {
    InvoiceStatus.DRAFT: ("DRAFT: not sent. This copy is not an invoice.", "مسودة: لم ترسل. هذه النسخة ليست فاتورة."),
    InvoiceStatus.PENDING_APPROVAL: ("WAITING FOR APPROVAL: not sent. This copy is not an invoice.", "بانتظار الموافقة: لم ترسل. هذه النسخة ليست فاتورة."),
    InvoiceStatus.VOID: ("VOID: this invoice was cancelled.", "ملغاة: أُلغيت هذه الفاتورة."),
    InvoiceStatus.PAID: ("PAID", "مدفوعة"),
}
METHODS = {"bank_transfer": "Bank transfer", "cash": "Cash", "card": "Card", "cheque": "Cheque", "other": "Other"}
METHODS_AR = {"bank_transfer": "تحويل بنكي", "cash": "نقدًا", "card": "بطاقة", "cheque": "شيك", "other": "أخرى"}

L = {
    "invoice": ("Invoice", "فاتورة"),
    "tax_invoice": ("Tax invoice", "فاتورة ضريبية"),
    "number": ("Invoice number", "رقم الفاتورة"),
    "issued": ("Issue date", "تاريخ الإصدار"),
    "due": ("Due date", "تاريخ الاستحقاق"),
    "reference": ("Your reference", "مرجعكم"),
    "seller": ("From", "من"),
    "seller_tax": ("Seller tax number", "الرقم الضريبي للبائع"),
    "buyer": ("Bill to", "فاتورة إلى"),
    "buyer_tax": ("Buyer tax number", "الرقم الضريبي للمشتري"),
    "lines": ("Lines", "البنود"),
    "description": ("Description", "الوصف"),
    "qty": ("Qty", "الكمية"),
    "unit": ("Unit price", "سعر الوحدة"),
    "rate": ("Tax %", "نسبة الضريبة"),
    "amount": ("Amount", "المبلغ"),
    "totals": ("Totals", "الإجماليات"),
    "subtotal": ("Subtotal", "المجموع الفرعي"),
    "tax": ("Tax", "الضريبة"),
    "total": ("Total", "الإجمالي"),
    "credited": ("Credit notes", "الإشعارات الدائنة"),
    "paid": ("Paid", "المدفوع"),
    "balance": ("Balance due", "الرصيد المستحق"),
    "credit_notes": ("Credit notes", "الإشعارات الدائنة"),
    "credit_number": ("Credit note number", "رقم الإشعار الدائن"),
    "date": ("Date", "التاريخ"),
    "reason": ("Reason", "السبب"),
    "status": ("Status", "الحالة"),
    "payments": ("Payments received", "الدفعات المستلمة"),
    "method": ("Method", "طريقة الدفع"),
    "pay_reference": ("Reference", "المرجع"),
    "terms": ("Payment terms", "شروط الدفع"),
    "bank": ("How to pay", "طريقة الدفع"),
    "notes": ("Notes", "ملاحظات"),
    "pay_online": ("Pay online", "الدفع الإلكتروني"),
    "receipt": ("Payment receipt", "إيصال دفع"),
    "received_by": ("Received by", "المستلم"),
    "received_from": ("Received from", "الدافع"),
    "for_invoice": ("For invoice", "للفاتورة"),
    "invoice_total": ("Invoice total", "إجمالي الفاتورة"),
    "still_due": ("Still due on the invoice", "المتبقي على الفاتورة"),
    "credit_note": ("Credit note", "إشعار دائن"),
}


def _amount(value: Decimal | float) -> str:
    return f"{Decimal(str(value)):,.2f}"


def _plain(value: Decimal | float) -> str:
    return f"{Decimal(str(value)).normalize():f}"


def _pair(key: str, value: object) -> tuple[str, str, object]:
    english, arabic = L[key]
    return english, arabic, value


def _seller_line(seller: BillingProfile) -> str:
    return ", ".join(part for part in (seller.legal_name, seller.address.replace("\n", ", "), seller.email, seller.phone) if part) or "-"


def _buyer_line(invoice: Invoice) -> str:
    return ", ".join(part for part in (invoice.bill_to_name, invoice.bill_to_address.replace("\n", ", "), invoice.bill_to_email) if part) or "-"


def _safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def invoice_filename(invoice: Invoice) -> str:
    """A safe file name: the invoice number, or ``draft-<id>`` before it has one."""
    stem = _safe_stem(invoice.number or f"draft-{invoice.id}") or "invoice"
    return f"{stem}.pdf"


def receipt_filename(invoice: Invoice, payment: InvoicePayment) -> str:
    return f"receipt-{_safe_stem(invoice.number or invoice.id) or 'invoice'}-{_safe_stem(payment.id[-8:]) or 'payment'}.pdf"


def credit_note_filename(note: CreditNote) -> str:
    return f"{_safe_stem(note.number) or 'credit-note'}.pdf"


def render_invoice_pdf(invoice: Invoice, payments: list[InvoicePayment], credit_notes: list[CreditNote], seller_fallback: BillingProfile,
                       payment_link: str | None = None) -> bytes:
    """The PDF bytes. The seller printed is the one stored on a sent invoice; a draft shows the current billing profile."""
    seller = invoice.seller or seller_fallback
    currency = invoice.currency.value
    key = "tax_invoice" if invoice.tax > 0 else "invoice"
    title, title_ar = L[key]
    number = invoice.number or "(draft)"
    document = PdfDocument(footer=f"{seller.legal_name or 'ANUM'} - {title} {number}")
    document.heading(f"{title} {number}", ar=title_ar)
    stamp = STATUS_LINE.get(invoice.status)
    if stamp:
        document.paragraph(stamp[0], size=10)
        document.paragraph(stamp[1], size=10)
    rows = [_pair("number", invoice.number or "-")]
    if invoice.issue_date:
        rows.append(_pair("issued", invoice.issue_date.isoformat()))
    if invoice.due_date:
        rows.append(_pair("due", invoice.due_date.isoformat()))
    if invoice.customer_reference:
        rows.append(_pair("reference", invoice.customer_reference))
    rows.append(_pair("seller", _seller_line(seller)))
    if seller.tax_number:
        rows.append((seller.tax_label or L["seller_tax"][0], L["seller_tax"][1], seller.tax_number))
    rows.append(_pair("buyer", _buyer_line(invoice)))
    if invoice.bill_to_tax_number:
        rows.append(_pair("buyer_tax", invoice.bill_to_tax_number))
    document.pairs(rows)
    document.table(f"Lines ({currency})", [L["description"], L["qty"], L["unit"], L["rate"], L["amount"]],
                   [[line.description, _plain(line.quantity), _amount(line.unit_price), _plain(line.tax_rate_pct), _amount(line.total)]
                    for line in invoice.lines], title_ar=f"{L['lines'][1]} ({currency})")
    totals = [_pair("subtotal", _amount(invoice.subtotal)), _pair("tax", _amount(invoice.tax)), _pair("total", _amount(invoice.total))]
    if invoice.amount_credited > 0:
        totals.append(_pair("credited", "-" + _amount(invoice.amount_credited)))
    if invoice.amount_paid > 0:
        totals.append(_pair("paid", _amount(invoice.amount_paid)))
    if invoice.amount_paid > 0 or invoice.amount_credited > 0:
        totals.append(_pair("balance", _amount(invoice.balance_due())))
    document.heading(f"Totals ({currency})", size=11, ar=f"{L['totals'][1]} ({currency})")
    document.pairs(totals)
    if credit_notes:
        document.table("Credit notes", [L["credit_number"], L["date"], L["reason"], L["amount"], L["status"]],
                       [[item.number, item.issue_date.isoformat(), item.reason, _amount(item.amount), item.status.value] for item in credit_notes],
                       title_ar=L["credit_notes"][1])
    if payments:
        document.table("Payments received", [L["date"], L["method"], L["pay_reference"], L["amount"]],
                       [[item.paid_on.isoformat(), METHODS.get(item.method.value, item.method.value), item.reference, _amount(item.amount)]
                        for item in payments], title_ar=L["payments"][1])
    if payment_link and invoice.status == InvoiceStatus.SENT and invoice.balance_due() > 0:
        document.heading("Pay online", size=11, ar=L["pay_online"][1])
        document.paragraph(f"Pay by card, mada or Apple Pay on the payment page: {payment_link}", size=9)
        document.paragraph("ادفع بالبطاقة أو مدى أو Apple Pay من صفحة الدفع على الرابط أعلاه.", size=9)
    for key_name, value in (("terms", invoice.payment_terms), ("bank", seller.bank_details), ("notes", invoice.notes)):
        if value.strip():
            document.heading(L[key_name][0], size=10, ar=L[key_name][1])
            for line in value.splitlines():
                if line.strip():
                    document.paragraph(line.strip())
    if seller.footer_note.strip():
        for line in seller.footer_note.splitlines():
            if line.strip():
                document.paragraph(line.strip())
    return document.render()


def render_receipt_pdf(invoice: Invoice, payment: InvoicePayment, seller_fallback: BillingProfile) -> bytes:
    """A receipt for one recorded payment: who was paid, by whom, how much, for which invoice."""
    seller = invoice.seller or seller_fallback
    currency = invoice.currency.value
    document = PdfDocument(footer=f"{seller.legal_name or 'ANUM'} - Receipt for {invoice.number or ''}")
    document.heading(f"Payment receipt {invoice.number or ''}".strip(), ar=L["receipt"][1])
    method = payment.method.value
    document.pairs([
        _pair("received_by", _seller_line(seller)),
        _pair("received_from", _buyer_line(invoice)),
        _pair("date", payment.paid_on.isoformat()),
        _pair("amount", f"{_amount(payment.amount)} {currency}"),
        (L["method"][0], L["method"][1], f"{METHODS.get(method, method)} / {METHODS_AR.get(method, method)}"),
        _pair("pay_reference", payment.reference or "-"),
        _pair("for_invoice", invoice.number or "-"),
        _pair("invoice_total", f"{_amount(invoice.total)} {currency}"),
        _pair("still_due", f"{_amount(invoice.balance_due())} {currency}"),
    ])
    document.paragraph("This receipt records money the seller says it received. Card details are entered only on the payment provider's page.", size=8)
    document.paragraph("يثبت هذا الإيصال مبلغًا استلمه البائع. تُدخل بيانات البطاقة في صفحة مزود الدفع فقط.", size=8)
    return document.render()


def render_credit_note_pdf(invoice: Invoice, note: CreditNote, seller_fallback: BillingProfile) -> bytes:
    """A credit note: the amount taken off one invoice, its tax share and the reason."""
    seller = invoice.seller or seller_fallback
    currency = note.currency.value
    document = PdfDocument(footer=f"{seller.legal_name or 'ANUM'} - Credit note {note.number}")
    document.heading(f"Credit note {note.number}", ar=L["credit_note"][1])
    if note.status == CreditNoteStatus.VOID:
        document.paragraph("VOID: this credit note was cancelled.", size=10)
        document.paragraph("ملغى: أُلغي هذا الإشعار الدائن.", size=10)
    rows = [
        _pair("credit_number", note.number),
        _pair("date", note.issue_date.isoformat()),
        _pair("for_invoice", invoice.number or "-"),
        _pair("seller", _seller_line(seller)),
    ]
    if seller.tax_number:
        rows.append((seller.tax_label or L["seller_tax"][0], L["seller_tax"][1], seller.tax_number))
    rows.append(_pair("buyer", _buyer_line(invoice)))
    if invoice.bill_to_tax_number:
        rows.append(_pair("buyer_tax", invoice.bill_to_tax_number))
    rows += [
        _pair("reason", note.reason),
        _pair("subtotal", f"{_amount(note.subtotal)} {currency}"),
        _pair("tax", f"{_amount(note.tax)} {currency}"),
        _pair("total", f"{_amount(note.amount)} {currency}"),
    ]
    document.pairs(rows)
    return document.render()
