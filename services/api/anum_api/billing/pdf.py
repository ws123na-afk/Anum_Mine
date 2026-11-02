"""Invoice, receipt and credit note PDFs, written with ANUM's dependency-free PDF writer
(``pdf_writer.py``). Every label is in English and Arabic (English on the left, Arabic on the
right, as Gulf tax invoices commonly are), and Arabic names and addresses print as Arabic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..pdf_writer import PdfDocument

DECIMALS = {"KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "JPY": 0}

# English and Arabic labels; the Arabic follows the wording UAE and Saudi tax invoices use.
L = {
    "invoice": ("Invoice", "فاتورة"),
    "tax_invoice": ("Tax invoice", "فاتورة ضريبية"),
    "number": ("Invoice number", "رقم الفاتورة"),
    "issued": ("Issue date", "تاريخ الإصدار"),
    "due": ("Due date", "تاريخ الاستحقاق"),
    "status": ("Status", "الحالة"),
    "period": ("Period", "الفترة"),
    "seller": ("Seller", "البائع"),
    "buyer": ("Bill to", "فاتورة إلى"),
    "seller_vat": ("Seller VAT number", "الرقم الضريبي للبائع"),
    "buyer_vat": ("Buyer VAT number", "الرقم الضريبي للمشتري"),
    "lines": ("Lines", "البنود"),
    "description": ("Description", "الوصف"),
    "quantity": ("Quantity", "الكمية"),
    "unit": ("Unit price", "سعر الوحدة"),
    "amount": ("Amount", "المبلغ"),
    "totals": ("Totals", "الإجماليات"),
    "subtotal": ("Subtotal", "المجموع الفرعي"),
    "vat": ("VAT", "ضريبة القيمة المضافة"),
    "total": ("Total", "الإجمالي"),
    "paid": ("Paid", "المدفوع"),
    "credited": ("Credited", "الخصم بإشعار دائن"),
    "due_amount": ("Amount due", "المبلغ المستحق"),
    "payments": ("Payments", "الدفعات"),
    "date": ("Date", "التاريخ"),
    "method": ("Method", "طريقة الدفع"),
    "reference": ("Reference", "المرجع"),
    "credit_notes": ("Credit notes", "الإشعارات الدائنة"),
    "credit_note": ("Credit note", "إشعار دائن"),
    "credit_number": ("Credit note number", "رقم الإشعار الدائن"),
    "reason": ("Reason", "السبب"),
    "for_invoice": ("For invoice", "للفاتورة"),
    "receipt": ("Payment receipt", "إيصال دفع"),
    "received_by": ("Received by", "المستلم"),
    "received_from": ("Received from", "الدافع"),
    "invoice_total": ("Invoice total", "إجمالي الفاتورة"),
    "still_due": ("Still due on the invoice", "المتبقي على الفاتورة"),
    "refunds": ("Refunds of this payment", "المبالغ المستردة من هذه الدفعة"),
    "bank": ("Bank transfer", "تحويل بنكي"),
}


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


def _pair(key: str, value: Any) -> tuple[str, str, Any]:
    english, arabic = L[key]
    return english, arabic, value


def _join(*values: Any) -> str:
    return ", ".join(str(v) for v in values if v) or "-"


def invoice_filename(invoice: dict[str, Any]) -> str:
    return f"{invoice.get('number') or invoice['id']}.pdf"


def render_invoice(detail: dict[str, Any]) -> bytes:
    invoice = detail["invoice"]
    currency = invoice["currency"]
    issuer = invoice.get("issuer") or {}
    bill_to = invoice.get("bill_to") or {}
    key = "tax_invoice" if int(invoice.get("tax_rate_bp") or 0) else "invoice"
    title, title_ar = L[key]
    document = PdfDocument(footer=f"{issuer.get('issuer_name') or 'ANUM'} - {title} {invoice.get('number') or ''}")
    document.heading(f"{title} {invoice.get('number') or '(draft)'}", ar=title_ar)
    rows = [
        _pair("number", invoice.get("number") or "-"),
        _pair("issued", _date(invoice.get("issued_at"))),
        _pair("due", _date(invoice.get("due_at"))),
        _pair("status", invoice["status"]),
    ]
    if invoice.get("period_start"):
        rows.append(_pair("period", f"{_date(invoice.get('period_start'))} to {_date(invoice.get('period_end'))}"))
    rows += [
        _pair("seller", _join(issuer.get("issuer_name"), issuer.get("issuer_address"),
                              f"Registration {issuer['issuer_registration']}" if issuer.get("issuer_registration") else "", issuer.get("issuer_email"))),
        _pair("seller_vat", issuer.get("issuer_vat_number") or "-"),
    ]
    address = _join(bill_to.get("address_line1"), bill_to.get("address_line2"), bill_to.get("city"), bill_to.get("postal_code"), bill_to.get("country"))
    rows += [
        _pair("buyer", _join(bill_to.get("company_name") or bill_to.get("display_name"), bill_to.get("contact_name"), bill_to.get("contact_email"),
                             address if address != "-" else "")),
        _pair("buyer_vat", bill_to.get("vat_number") or "-"),
    ]
    document.pairs(rows)
    document.table(
        "Lines", [L["description"], L["quantity"], L["unit"], L["amount"]],
        [[line["description"], line["quantity"], money(int(line["unit_minor"]), currency), money(int(line["amount_minor"]), currency)]
         for line in detail["lines"]],
        title_ar=L["lines"][1],
    )
    rate = int(invoice.get("tax_rate_bp") or 0)
    vat_en, vat_ar = L["vat"]
    totals = [
        _pair("subtotal", money(int(invoice["subtotal_minor"]), currency)),
        (f"{vat_en} {rate / 100:g}% ({invoice.get('tax_country') or '-'})", vat_ar, money(int(invoice["tax_minor"]), currency)),
        _pair("total", money(int(invoice["total_minor"]), currency)),
        _pair("paid", money(int(invoice["amount_paid_minor"]), currency)),
        _pair("credited", money(int(invoice["amount_credited_minor"]), currency)),
        _pair("due_amount", money(int(invoice["amount_due_minor"]), currency)),
    ]
    document.heading("Totals", size=11, ar=L["totals"][1])
    document.pairs(totals)
    if detail.get("payments"):
        document.table(
            "Payments", [L["date"], L["method"], L["reference"], L["amount"]],
            [[_date(p["received_at"]), p["method"].replace("_", " "), p.get("reference") or "-", money(int(p["amount_minor"]), currency)]
             for p in detail["payments"]],
            title_ar=L["payments"][1],
        )
    if detail.get("credit_notes"):
        document.table(
            "Credit notes", [L["credit_number"], L["date"], L["reason"], L["amount"]],
            [[n["number"], _date(n["issued_at"]), n["reason"], money(int(n["total_minor"]), currency)] for n in detail["credit_notes"]],
            title_ar=L["credit_notes"][1],
        )
    if issuer.get("bank_iban") and int(invoice["amount_due_minor"]) > 0:
        document.paragraph(
            f"Pay by bank transfer to {issuer.get('bank_account_name') or issuer.get('issuer_name')}, {issuer.get('bank_name')}, "
            f"IBAN {issuer['bank_iban']}" + (f", SWIFT {issuer['bank_swift']}" if issuer.get("bank_swift") else "")
            + f". Use {invoice.get('number')} as the reference.",
            size=9,
        )
        document.paragraph(f"الدفع بالتحويل البنكي إلى الحساب أعلاه، مع ذكر رقم الفاتورة {invoice.get('number')} مرجعًا للتحويل.", size=9)
    return document.render()


def receipt_filename(invoice: dict[str, Any], payment: dict[str, Any]) -> str:
    return f"receipt-{invoice.get('number') or invoice['id']}-{payment['id'][-8:]}.pdf"


def render_receipt(invoice: dict[str, Any], payment: dict[str, Any], refunds: list[dict[str, Any]]) -> bytes:
    """A payment receipt: who was paid, by whom, how much, for which invoice, and any refunds of it."""
    currency = payment.get("currency") or invoice["currency"]
    issuer = invoice.get("issuer") or {}
    bill_to = invoice.get("bill_to") or {}
    document = PdfDocument(footer=f"{issuer.get('issuer_name') or 'ANUM'} - Receipt for {invoice.get('number') or ''}")
    document.heading(f"Payment receipt for invoice {invoice.get('number') or '-'}", ar=L["receipt"][1])
    method = str(payment.get("method") or "").replace("_", " ")
    provider = f" through {payment['provider'].title()}" if payment.get("provider") else ""
    document.pairs([
        _pair("received_by", _join(issuer.get("issuer_name"), issuer.get("issuer_address"),
                                   f"Registration {issuer['issuer_registration']}" if issuer.get("issuer_registration") else "",
                                   f"VAT {issuer['issuer_vat_number']}" if issuer.get("issuer_vat_number") else "", issuer.get("issuer_email"))),
        _pair("received_from", _join(bill_to.get("company_name") or bill_to.get("display_name"), bill_to.get("contact_email"),
                                     f"VAT {bill_to['vat_number']}" if bill_to.get("vat_number") else "")),
        _pair("date", _date(payment.get("received_at"))),
        _pair("amount", money(int(payment["amount_minor"]), currency)),
        _pair("method", f"{method}{provider}"),
        _pair("reference", payment.get("reference") or "-"),
        _pair("for_invoice", invoice.get("number") or "-"),
        _pair("invoice_total", money(int(invoice["total_minor"]), invoice["currency"])),
        _pair("still_due", money(int(invoice["amount_due_minor"]), invoice["currency"])),
    ])
    if refunds:
        document.table(
            "Refunds of this payment", [L["date"], L["status"], L["amount"]],
            [[_date(r.get("created_at")), r["status"], money(int(r["amount_minor"]), currency)] for r in refunds],
            title_ar=L["refunds"][1],
        )
    document.paragraph("Card details are entered on the payment provider's page and are never seen or stored by ANUM.", size=8)
    document.paragraph("تُدخل بيانات البطاقة في صفحة مزود الدفع، ولا يراها ANUM ولا يحفظها.", size=8)
    return document.render()


def credit_note_filename(note: dict[str, Any]) -> str:
    return f"{note.get('number') or note['id']}.pdf"


def render_credit_note(invoice: dict[str, Any], note: dict[str, Any]) -> bytes:
    """A credit note: the amount taken off one invoice, with its VAT share and the reason."""
    currency = invoice["currency"]
    issuer = invoice.get("issuer") or {}
    bill_to = invoice.get("bill_to") or {}
    document = PdfDocument(footer=f"{issuer.get('issuer_name') or 'ANUM'} - Credit note {note.get('number') or ''}")
    document.heading(f"Credit note {note.get('number') or ''}", ar=L["credit_note"][1])
    document.pairs([
        _pair("credit_number", note.get("number") or "-"),
        _pair("date", _date(note.get("issued_at"))),
        _pair("for_invoice", invoice.get("number") or "-"),
        _pair("seller", _join(issuer.get("issuer_name"), issuer.get("issuer_address"), issuer.get("issuer_email"))),
        _pair("seller_vat", issuer.get("issuer_vat_number") or "-"),
        _pair("buyer", _join(bill_to.get("company_name") or bill_to.get("display_name"), bill_to.get("contact_email"))),
        _pair("buyer_vat", bill_to.get("vat_number") or "-"),
        _pair("reason", note.get("reason") or "-"),
        _pair("subtotal", money(int(note.get("subtotal_minor") or 0), currency)),
        _pair("vat", money(int(note.get("tax_minor") or 0), currency)),
        _pair("total", money(int(note.get("total_minor") or 0), currency)),
    ])
    return document.render()
