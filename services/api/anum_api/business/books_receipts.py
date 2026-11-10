"""Receipts to bills: propose a vault receipt as the evidence of the bill it matches (docs/business-books.md#receipts-to-bills).

A receipt, invoice or purchase order in this business's Document vault (a **workspace** document whose scanned values
the person confirmed in the vault: total, currency, date and merchant, ``DocumentRecord.receipt``) is proposed for a
live bill that has no document attached when:

* the **amount** is the bill's total in the same currency (to the minor unit),
* the **date** is within :data:`WINDOW_DAYS` days of the bill date, and
* the **vendor** agrees: the receipt's merchant and the vendor's name share a meaningful word (``merchants_agree``).
  A receipt without a merchant is proposed only when exactly one bill fits the amount and date; one whose merchant names
  someone else is never proposed.

A document already attached to a bill is not proposed again, and a bill gets at most one proposal (the closest date).
It is only a suggestion: the review item (kind ``receipt_match``) and ``GET /evidence/receipt-matches`` show it, and
``POST /evidence/receipt-matches/accept`` attaches it through ``books_evidence.attach`` (every vault and evidence rule
applies). Nothing is attached on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..finance.merchant_match import merchants_agree
from ..finance.money import quantize
from ..schemas import TenantContext
from . import books_evidence as E
from .books_store import MAX_SCAN, Between, open_books_store

WINDOW_DAYS = 7
LOOKBACK_DAYS = 400
MAX_DOCUMENTS = 500


@dataclass(frozen=True)
class ReceiptMatch:
    document_id: str
    document_title: str
    bill_id: str
    bill_reference: str
    vendor: str
    merchant: str
    bill_date: date
    receipt_date: date
    amount: Decimal
    currency: str
    vendor_agrees: bool
    confidence: Decimal

    def view(self) -> dict[str, Any]:
        return {"document_id": self.document_id, "document_title": self.document_title, "bill_id": self.bill_id, "bill_reference": self.bill_reference,
                "vendor": self.vendor, "merchant": self.merchant, "bill_date": self.bill_date.isoformat(), "receipt_date": self.receipt_date.isoformat(),
                "amount": str(self.amount), "currency": self.currency, "vendor_agrees": self.vendor_agrees, "confidence": str(self.confidence)}


def _receipts(context: TenantContext, today: date) -> list[Any]:
    from ..vault.models import RECEIPT_CATEGORIES, DocumentQuery
    from ..vault.store import open_vault_unit

    out: list[Any] = []
    with open_vault_unit(context) as unit:
        for category in sorted(RECEIPT_CATEGORIES):
            docs = unit.store.list_documents(context, DocumentQuery(category=category, access_class="workspace", limit=MAX_DOCUMENTS), today)
            out.extend(d for d in docs if d.status == "active" and d.access_class == "workspace" and isinstance(d.receipt, dict))
    return out


def _value(doc: Any) -> tuple[Decimal, str, date] | None:
    receipt = doc.receipt or {}
    try:
        currency = str(receipt.get("currency") or "").upper()
        total = Decimal(str(receipt.get("total")))
        on = date.fromisoformat(str(receipt.get("date")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if len(currency) != 3 or not total.is_finite() or total <= 0:
        return None
    return quantize(total, currency), currency, on


def candidates(context: TenantContext, today: date) -> list[ReceiptMatch]:
    """Every proposal for this business: one per bill at most, never a document already on a bill."""
    docs = [(d, v) for d in _receipts(context, today) if (v := _value(d)) is not None]
    if not docs:
        return []
    start = today - timedelta(days=LOOKBACK_DAYS)
    with open_books_store(context) as store:
        bills, _ = store.find("bills", where={"bill_date": Between(start, today + timedelta(days=WINDOW_DAYS))}, limit=MAX_SCAN)
        bills = [b for b in bills if str(getattr(b.status, "value", b.status)) != "void"]
        vendor_ids = {b.vendor_id for b in bills}
        vendors = {v.id: v.name for v in store.find("vendors", where={"id": vendor_ids}, limit=MAX_SCAN)[0]} if vendor_ids else {}
    if not bills:
        return []
    attached = E.attached(context, "bill", [b.id for b in bills])
    taken_docs = {d["document_id"] for rows in attached.values() for d in rows}
    open_bills = [b for b in bills if not attached.get(b.id)]
    found: dict[str, ReceiptMatch] = {}
    for doc, (total, currency, on) in docs:
        if doc.id in taken_docs:
            continue
        merchant = str((doc.receipt or {}).get("merchant") or "")
        fits = [b for b in open_bills if b.currency == currency and quantize(b.total, currency) == total and abs((b.bill_date - on).days) <= WINDOW_DAYS]
        agreeing = [b for b in fits if merchants_agree(merchant, vendors.get(b.vendor_id, "")) is True]
        if agreeing:
            chosen, agrees = agreeing, True
        elif not merchant.strip() and len(fits) == 1:
            chosen, agrees = fits, False
        else:
            continue  # a merchant that names someone else, or several bills it could be
        best = min(chosen, key=lambda b: (abs((b.bill_date - on).days), b.id))
        days = abs((best.bill_date - on).days)
        match = ReceiptMatch(document_id=doc.id, document_title=doc.title[:120], bill_id=best.id, bill_reference=best.reference[:60],
                             vendor=vendors.get(best.vendor_id, "")[:80], merchant=merchant[:80], bill_date=best.bill_date, receipt_date=on,
                             amount=total, currency=currency, vendor_agrees=agrees,
                             confidence=Decimal("0.9") if agrees and days <= 1 else Decimal("0.75") if agrees else Decimal("0.5"))
        earlier = found.get(best.id)
        if earlier is None or (abs((earlier.bill_date - earlier.receipt_date).days), earlier.document_id) > (days, doc.id):
            found[best.id] = match
    return sorted(found.values(), key=lambda m: (m.bill_date, m.bill_id), reverse=True)


def accept(context: TenantContext, today: date, bill_id: str, document_id: str) -> dict[str, Any]:
    """Attach the proposed receipt to its bill, if it is still proposed (the evidence rules apply)."""
    from .books_service import BooksError

    if not any(m.bill_id == bill_id and m.document_id == document_id for m in candidates(context, today)):
        raise BooksError(409, "This receipt is no longer proposed for this bill: it was attached, or the bill or receipt changed.", "match_gone")
    return E.attach(context, "bill", bill_id, document_id)


def produce(ctx: Any) -> list[Any]:
    from . import books_review as BR

    out = []
    for m in candidates(ctx.context, ctx.today):
        out.append(BR.ReviewItem(
            key=f"receipt_match:{m.bill_id}:{m.document_id}", producer="receipts", kind="receipt_match",
            detected="A receipt in the Document vault matches a bill that has no document attached.",
            params={"document_id": m.document_id, "document": m.document_title, "bill_id": m.bill_id, "reference": m.bill_reference,
                    "vendor": m.vendor, "merchant": m.merchant, "receipt_date": m.receipt_date.isoformat(), "vendor_agrees": "1" if m.vendor_agrees else "0",
                    "confidence": str(m.confidence)},
            severity="warning", occurred_on=m.bill_date, amount=m.amount, currency=m.currency, ref_id=m.bill_id, section="bills", action="link",
        ))
    return out


def _register() -> None:
    from . import books_review as BR

    BR.register_producer("receipts", produce)


_register()

__all__ = ["LOOKBACK_DAYS", "WINDOW_DAYS", "ReceiptMatch", "accept", "candidates", "produce"]
