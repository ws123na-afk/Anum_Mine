"""A receipt's confirmed values and the records it may belong to (docs/documents.md#receipt-matches).

A receipt, invoice, maintenance invoice or purchase order in the vault carries the values the person confirmed after a
scan (``DocumentRecord.receipt``: total, currency, date, merchant). Nothing here is read from an unconfirmed scan.

:func:`matches` proposes, for one such document, the records it may be the receipt of:

* **payments**: the caller's own live payments out in the Finance ledger with the receipt's total in its currency (to
  the minor unit), posted from :data:`PAYMENT_EARLY_DAYS` days before the receipt's date to :data:`PAYMENT_LATE_DAYS`
  after (a card is billed a day or two later), whose words do not name another merchant;
* **purchases**: the caller's own purchases (purchases module) with no receipt yet, the same total and currency,
  ordered within :data:`PURCHASE_DAYS` days, whose shop does not name another merchant.

Each proposal carries a confidence and whether the merchant words agree. It is only a proposal: the person confirms it
(``POST /documents/{id}/matches/accept``), which re-checks it and goes through the same paths as linking by hand: the
finance links foundation for a payment (an ``evidence`` link, never money), the purchase's own receipt field for a
purchase. The vehicles module reads :func:`values` to propose a fuel entry's payment from its scanned receipt.

Reads only, each store in its own short unit of work (never nested).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..schemas import TenantContext
from .models import RECEIPT_CATEGORIES

PAYMENT_EARLY_DAYS = 3
PAYMENT_LATE_DAYS = 5
PURCHASE_DAYS = 7
MAX_PROPOSALS = 5


@dataclass(frozen=True)
class ReceiptValue:
    total: Decimal
    currency: str
    on: date
    merchant: str


def value_of(doc: Any) -> ReceiptValue | None:
    """The confirmed receipt values of a document, or None (not a receipt category, not confirmed, or unusable)."""
    if getattr(doc, "category", None) not in RECEIPT_CATEGORIES or not isinstance(getattr(doc, "receipt", None), dict):
        return None
    from ..finance.money import quantize

    receipt = doc.receipt
    try:
        currency = str(receipt.get("currency") or "").upper()
        total = Decimal(str(receipt.get("total")))
        on = date.fromisoformat(str(receipt.get("date")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if len(currency) != 3 or not currency.isalpha() or not total.is_finite() or total <= 0:
        return None
    return ReceiptValue(total=quantize(total, currency), currency=currency, on=on, merchant=str(receipt.get("merchant") or "")[:120])


def values(context: TenantContext, document_ids: set[str]) -> dict[str, ReceiptValue]:
    """The confirmed receipt values of the documents the caller can see (one short vault unit of work)."""
    if not document_ids:
        return {}
    from .store import open_vault_unit

    out: dict[str, ReceiptValue] = {}
    with open_vault_unit(context) as unit:
        for document_id in sorted(document_ids)[:500]:
            doc = unit.store.get_document(context, document_id)
            found = value_of(doc) if doc is not None and doc.status == "active" else None
            if found is not None:
                out[document_id] = found
    return out


@dataclass(frozen=True)
class Proposal:
    kind: str  # "payment" or "purchase"
    target_id: str  # the payment's canonical id, or the purchase id
    on: date
    amount: Decimal
    currency: str
    label: str
    merchant_agrees: bool | None
    confidence: Decimal

    def view(self) -> dict[str, Any]:
        return {"kind": self.kind, "target_id": self.target_id, "on": self.on.isoformat(), "amount": str(self.amount), "currency": self.currency,
                "label": self.label, "merchant_agrees": self.merchant_agrees, "confidence": str(self.confidence)}


def _confidence(agrees: bool | None, days: int) -> Decimal:
    if agrees:
        return Decimal("0.9") if days <= 1 else Decimal("0.8")
    return Decimal("0.6") if days <= 1 else Decimal("0.5")


def payment_proposals(context: TenantContext, receipt: ReceiptValue, *, linked: set[str]) -> list[Proposal]:
    """The caller's own live payments out that fit the receipt (``linked``: canonical ids the document is already on)."""
    from ..finance import links as K
    from ..finance.links_store import open_link_stores
    from ..finance.merchant_match import merchants_agree

    start, end = receipt.on - timedelta(days=PAYMENT_EARLY_DAYS), receipt.on + timedelta(days=PAYMENT_LATE_DAYS)
    with open_link_stores(context) as (_finance, sms, _links):
        entries = sms.list_entries(context, start=start, end=end, limit=5_000)
    chains: dict[str, list[Any]] = {}
    for entry in entries:
        chains.setdefault(entry.canonical_id or entry.id, []).append(entry)
    out: list[Proposal] = []
    for canonical, chain in chains.items():
        current = K.live_entry(chain)
        if current is None or canonical in linked or (current.category or "") in ("income", "transfer"):
            continue
        if K.capacity(chain).get(receipt.currency) != receipt.total:
            continue
        words = f"{current.merchant or ''} {current.description}".strip()
        agrees = merchants_agree(receipt.merchant, words)
        if agrees is False:
            continue  # the payment names another merchant
        days = abs((current.posted_on - receipt.on).days)
        out.append(Proposal(kind="payment", target_id=canonical, on=current.posted_on, amount=receipt.total, currency=receipt.currency,
                            label=(current.merchant or current.description)[:80], merchant_agrees=agrees, confidence=_confidence(agrees, days)))
    return sorted(out, key=lambda p: (-p.confidence, abs((p.on - receipt.on).days), p.target_id))[:MAX_PROPOSALS]


def purchase_proposals(context: TenantContext, receipt: ReceiptValue) -> list[Proposal]:
    """The caller's own purchases with no receipt yet that fit the receipt."""
    from ..finance.merchant_match import merchants_agree
    from ..finance.money import quantize
    from ..purchases.store import open_purchase_store

    with open_purchase_store(context) as store:
        found, _ = store.list_purchases(context, query=None, status=None, archived=False, limit=500, offset=0)
    out: list[Proposal] = []
    for purchase in found:
        if purchase.owner_user_id != context.user_id or purchase.document_id or purchase.total is None or purchase.currency != receipt.currency:
            continue
        if quantize(purchase.total, purchase.currency) != receipt.total:
            continue
        days = abs((purchase.ordered_on - receipt.on).days)
        if days > PURCHASE_DAYS:
            continue
        agrees = merchants_agree(receipt.merchant, purchase.merchant)
        if agrees is False:
            continue
        out.append(Proposal(kind="purchase", target_id=purchase.id, on=purchase.ordered_on, amount=receipt.total, currency=receipt.currency,
                            label=purchase.merchant[:80], merchant_agrees=agrees, confidence=_confidence(agrees, days)))
    return sorted(out, key=lambda p: (-p.confidence, abs((p.on - receipt.on).days), p.target_id))[:MAX_PROPOSALS]


def matches(context: TenantContext, document_id: str) -> dict[str, Any]:
    """What one document may be the receipt of, for the person who asks (``GET /documents/{id}/matches``).

    A payment is proposed only to someone with finance read access and only while the document is on no payment yet;
    a purchase only to someone with purchases read access and only while no purchase names the document. A section
    the caller cannot use says why (``*_unavailable``) instead of failing the whole answer."""
    from ..authorization import Permission
    from ..dependencies import has_permission
    from .service import can_edit, get_visible
    from .store import open_vault_unit

    with open_vault_unit(context) as unit:
        doc = get_visible(unit, context, document_id)
        links = unit.store.links(context, document_id)
    receipt = value_of(doc) if doc.status == "active" else None
    out: dict[str, Any] = {"document_id": document_id, "receipt": None, "reason": None, "can_edit": can_edit(context, doc),
                           "payments": [], "payments_unavailable": None, "purchases": [], "purchases_unavailable": None,
                           "linked_payments": sorted(l.target_id for l in links if l.target_type == "finance_transaction"),
                           "linked_purchases": sorted(l.target_id for l in links if l.target_type == "purchase")}
    if receipt is None:
        out["reason"] = "not_a_receipt" if doc.category not in RECEIPT_CATEGORIES else "not_confirmed"
        return out
    out["receipt"] = {"total": str(receipt.total), "currency": receipt.currency, "on": receipt.on.isoformat(), "merchant": receipt.merchant}
    if not has_permission(context, Permission.FINANCE_READ):
        out["payments_unavailable"] = "finance_access"
    elif not out["linked_payments"]:
        out["payments"] = [p.view() for p in payment_proposals(context, receipt, linked=set())]
    if not has_permission(context, Permission.PURCHASE_READ):
        out["purchases_unavailable"] = "purchase_access"
    elif not out["linked_purchases"]:
        try:
            out["purchases"] = [p.view() for p in purchase_proposals(context, receipt)]
        except ModuleNotFoundError:  # the purchases module is not installed
            out["purchases_unavailable"] = "not_installed"
    return out


def accept(context: TenantContext, document_id: str, kind: str, target_id: str) -> dict[str, Any]:
    """Confirm one proposal of :func:`matches`, if it is still proposed (``409 match_gone`` otherwise): a payment becomes
    the document's finance link (an ``evidence`` link on the ledger, ``finance:manage``, never money), a purchase gets the
    document as its receipt (the purchase's own rules). Raises ``HTTPException``."""
    from fastapi import HTTPException

    current = matches(context, document_id)
    if not current["can_edit"]:
        from .service import EDIT_DENIED

        raise HTTPException(403, EDIT_DENIED)
    pool = current["payments"] if kind == "payment" else current["purchases"]
    chosen = next((p for p in pool if p["target_id"] == target_id), None)
    if chosen is None:
        raise HTTPException(409, "This match is no longer proposed: the receipt, the payment or the purchase changed, or it was linked already.")
    if kind == "payment":
        from .service import add_link

        add_link(context, document_id, "finance_transaction", target_id, f"{chosen['label']} {chosen['currency']} {chosen['amount']}")
    else:
        from ..purchases import service as PS
        from ..purchases.api import attach_document

        try:
            attach_document(context, target_id, document_id)
        except PS.PurchaseError as exc:
            raise HTTPException(exc.status_code, exc.message) from None
    return matches(context, document_id)


__all__ = ["MAX_PROPOSALS", "Proposal", "ReceiptValue", "accept", "matches", "payment_proposals", "purchase_proposals", "value_of", "values"]
