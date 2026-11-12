"""Refunds made in the Stripe or Moyasar dashboard on a Business invoice's card payment
(docs/business.md#refunds-made-in-the-provider-dashboard).

The owner refunds a customer in the provider's own dashboard; ANUM learns it from the provider's
webhook, which the billing webhook has already verified (Stripe's signature, Moyasar's secret
token) and whose facts it has read back from the provider's API with no transaction open: for
Stripe every refund of the payment (``refunds_of``), for Moyasar the payment and the total it says
was refunded. Nothing here trusts the delivered body.

* **Once per provider refund.** A refund is a ``business_invoice_refunds`` row keyed by the
  provider's refund id (Stripe's ``re_...``; for Moyasar, which reports only a running total, the
  payment id and that total), unique in the database too, so a repeated or reordered delivery
  records nothing twice. The same event id is also remembered by the billing webhook.
* **Against the invoice and its payment.** The refund points at the card payment it gives back.
  What it takes off the invoice's amount paid is the part of the refund that gives back money the
  payment applied: a card charged above the balance kept the surplus outside ANUM, and refunding
  that surplus first takes nothing off. A paid invoice with money refunded is open again (sent);
  the owner issues a credit note when the customer no longer owes it, or asks for the money again.
* **Books.** A recorded refund is posted by the existing posting run as ``invoice_refund:<id>``:
  money out of the bank against the receivable (``books_posting``), and the cash reports count it
  as a negative customer payment (``books_report``).
* **Reversed.** A Stripe refund recorded as succeeded that Stripe later reports failed or
  cancelled counts no more: the amount it took off is paid again.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from ..schemas import TenantContext, new_id, utc_now
from .invoice_models import OPEN_REFUND_REQUESTS, Invoice, InvoicePayment, InvoiceRefund, InvoiceStatus, RefundRequestStatus, RefundReview, RefundStatus
from .invoice_service import business_day, mark_milestones_paid, release_milestones
from .models import money
from .service import record_activity
from .store import BusinessStore, Query, open_business_store

logger = logging.getLogger("anum.business")

ID = re.compile(r"^[A-Za-z0-9_-]{3,80}$")
PROVIDER_USERS = {"moyasar": "system:moyasar", "stripe": "system:stripe"}
PROVIDER_NAMES = {"moyasar": "Moyasar", "stripe": "Stripe"}
DONE = ("succeeded",)
FAILED = ("failed", "canceled")
MAX_REFUNDS = 200


@dataclass(frozen=True)
class ProviderRefund:
    """A refund as the provider's API reports it (never from the delivered body)."""

    id: str
    amount_minor: int
    currency: str
    status: str  # Stripe's refund status; a Moyasar refund is always "succeeded"


def refunds_of(store: BusinessStore, invoice_id: str) -> list[InvoiceRefund]:
    items, _ = store.find("invoice_refunds", Query(where={"invoice_id": invoice_id}, order_by="created_at", descending=False, limit=MAX_REFUNDS))
    return items


def payment_refunds(store: BusinessStore, payment_id: str) -> list[InvoiceRefund]:
    items, _ = store.find("invoice_refunds", Query(where={"payment_id": payment_id}, order_by="created_at", descending=False, limit=MAX_REFUNDS))
    return items


def _applied_part(cumulative: Decimal, surplus: Decimal, applied: Decimal) -> Decimal:
    """How much of ``cumulative`` refunded money gives back what the payment applied: the surplus goes first."""
    return min(max(cumulative - surplus, Decimal(0)), applied)


def _reopen(store: BusinessStore, invoice: Invoice, taken: Decimal, now: datetime) -> Invoice:
    """The invoice with ``taken`` less paid: a paid invoice owes again and its milestones are no longer cash collected."""
    update: dict[str, object] = {"amount_paid": money(max(invoice.amount_paid - taken, Decimal(0))), "updated_at": now}
    if invoice.status == InvoiceStatus.PAID and taken > 0:
        release_milestones(store, invoice, now)
        update.update({"status": InvoiceStatus.SENT, "paid_at": None, "marked_milestones": []})
    invoice = invoice.model_copy(update=update)
    store.save("invoices", invoice)
    return invoice


def _restore(store: BusinessStore, invoice: Invoice, amount: Decimal, now: datetime) -> Invoice:
    """A reversed refund's amount counts as paid again."""
    paid = money(invoice.amount_paid + amount)
    update: dict[str, object] = {"amount_paid": paid, "updated_at": now}
    if invoice.status == InvoiceStatus.SENT and money(paid + invoice.amount_credited) >= invoice.total:
        update.update({"status": InvoiceStatus.PAID, "paid_at": now})
        if invoice.amount_credited <= 0:
            update["marked_milestones"] = mark_milestones_paid(store, invoice, business_day(store, now), now)
    invoice = invoice.model_copy(update=update)
    store.save("invoices", invoice)
    return invoice


def _keep_for_review(store: BusinessStore, context: TenantContext, provider: Literal["moyasar", "stripe"], invoice: Invoice,
                     provider_payment_id: str, refunds: list[ProviderRefund], running_total: tuple[int, str] | None, now: datetime) -> str:
    """Each finished refund of a payment ANUM never recorded becomes one review row (once per provider refund id)."""
    if running_total is not None:
        total_minor, total_currency = running_total
        refunds = [ProviderRefund(f"{provider_payment_id[:150]}:{total_minor}", total_minor, total_currency, "succeeded")]
    added = 0
    for item in refunds:
        if item.status not in DONE or item.amount_minor <= 0:
            continue
        refund_id = item.id[:200]
        if store.first("refund_reviews", provider=provider, provider_refund_id=refund_id) is not None:
            continue
        review = RefundReview(
            id=new_id("binrev"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, invoice_id=invoice.id, invoice_number=(invoice.number or "")[:40],
            provider=provider, provider_refund_id=refund_id, provider_payment_id=provider_payment_id[:200], currency=item.currency.upper()[:3],
            amount=money(Decimal(item.amount_minor) / Decimal(100)), created_at=now, updated_at=now,
        )
        store.add("refund_reviews", review)
        record_activity(store, context, "invoice_refund_needs_review",
                        f"{PROVIDER_NAMES[provider]} refunded {review.currency} {review.amount} of a card payment ANUM has no record of on invoice "
                        f"{invoice.number or invoice.id}. Nothing was taken off the invoice; check it under Business, Invoices, Refunds to check.",
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "review_id": review.id},
                        idempotency_key=f"{provider}-refund-review:{refund_id[:100]}")
        added += 1
    return "refund_needs_review" if added else "ignored"


def apply_card_refunds(provider: Literal["moyasar", "stripe"], issuer_tenant_id: str, metadata: dict[str, str], provider_payment_id: str,
                       refunds: list[ProviderRefund], now: datetime | None = None, *, running_total: tuple[int, str] | None = None,
                       actor: str | None = None) -> str:
    """Make the invoice's refunds of one card payment match what the provider's API says.

    ``metadata`` is the payment's own (read from the provider); only the issuer's own Business
    invoices are touched. ``actor`` is set when the refund was started from ANUM (the approved request records it
    through this same path, so the provider's webhook that follows finds the refund and records nothing twice).
    Returns ``ignored``, ``unchanged``, ``refund_recorded``, ``refund_reversed`` or ``refund_needs_review``.
    """
    now = now or utc_now()
    name = PROVIDER_NAMES[provider]
    tenant_id = metadata.get("anum_tenant_id", "")
    workspace_id = metadata.get("anum_workspace_id", "")
    invoice_id = metadata.get("anum_business_invoice_id", "")
    if tenant_id != issuer_tenant_id or not ID.match(workspace_id) or not ID.match(invoice_id) or not provider_payment_id:
        logger.warning("business_card_foreign_refund", extra={"anum_business": {"event": f"{provider}_foreign_refund"}})
        return "ignored"
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=PROVIDER_USERS[provider], roles=["member"])
    outcome = "unchanged"
    with open_business_store(context) as store:
        invoice: Invoice | None = store.get("invoices", invoice_id, for_update=True)
        if invoice is None:
            return "ignored"
        payment: InvoicePayment | None = store.first("invoice_payments", provider=provider, provider_payment_id=provider_payment_id[:200])
        if payment is None or payment.invoice_id != invoice.id:
            # A refund of a payment ANUM never recorded (it was not owed, or it was removed): nothing is taken off the
            # invoice; the owner sees it in the review list (docs/business.md#refunds-anum-could-not-match).
            logger.warning("business_card_refund_unknown_payment", extra={"anum_business": {"event": f"{provider}_refund_unknown_payment"}})
            return _keep_for_review(store, context, provider, invoice, provider_payment_id, refunds, running_total, now)
        known = {item.provider_refund_id: item for item in payment_refunds(store, payment.id)}
        charged = money(payment.provider_amount if payment.provider_amount is not None else payment.amount)
        if running_total is not None:
            total_minor, total_currency = running_total
            recorded_minor = sum(int(row.provider_amount * 100) for row in known.values() if row.status == RefundStatus.RECORDED)
            refunds = [ProviderRefund(f"{provider_payment_id[:150]}:{total_minor}", total_minor - recorded_minor, total_currency, "succeeded")]
        surplus = money(max(charged - payment.amount, Decimal(0)))
        for item in refunds:
            refund_id = item.id[:200]
            found = known.get(refund_id)
            if found is not None:
                if found.status == RefundStatus.RECORDED and item.status in FAILED:
                    invoice = _restore(store, invoice, found.amount, now)
                    reversed_ = found.model_copy(update={"status": RefundStatus.REVERSED, "reversed_at": now, "updated_at": now})
                    store.save("invoice_refunds", reversed_)
                    known[refund_id] = reversed_
                    record_activity(store, context, "invoice_refund_reversed",
                                    f"{name} reports the refund of {found.currency.value} {found.provider_amount} on invoice {invoice.number} failed; it counts as paid again.",
                                    account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "refund_id": found.id},
                                    idempotency_key=f"{provider}-refund-reversed:{refund_id[:100]}")
                    outcome = "refund_reversed"
                continue
            asked = store.first("refund_requests", provider=provider, provider_refund_id=refund_id)
            if asked is not None and item.status in FAILED and asked.status == RefundRequestStatus.PENDING_PROVIDER:
                # A refund started from ANUM that the provider never finished: its amount is free again.
                store.save("refund_requests", asked.model_copy(update={"status": RefundRequestStatus.FAILED, "updated_at": now,
                                                                       "detail": f"{name} reports the refund {item.status}; nothing was refunded."}))
                continue
            if item.status not in DONE or item.amount_minor <= 0:
                continue  # not refunded yet: the next delivery records it
            if item.currency.upper() != invoice.currency.value:
                logger.warning("business_card_refund_currency_mismatch", extra={"anum_business": {"event": f"{provider}_refund_currency_mismatch"}})
                continue
            live = [row for row in known.values() if row.status == RefundStatus.RECORDED]
            before = money(sum((row.provider_amount for row in live), Decimal(0)))
            given = min(money(Decimal(item.amount_minor) / Decimal(100)), max(charged - before, Decimal(0)))
            if given <= 0:
                logger.warning("business_card_refund_above_charge", extra={"anum_business": {"event": f"{provider}_refund_above_charge"}})
                continue
            taken = money(_applied_part(before + given, surplus, payment.amount) - _applied_part(before, surplus, payment.amount))
            taken = min(taken, invoice.amount_paid)
            if actor is None and asked is not None:
                actor = asked.decided_by or asked.requested_by  # the webhook finishing a refund started from ANUM
            where = f"Refunded from ANUM through {name}" if actor else f"Refunded in the {name} dashboard"
            note = (f"{where}." if taken == given else f"{where}: {given - taken} of it was money the card took above the balance.")
            refund = InvoiceRefund(
                id=new_id("binref"), tenant_id=tenant_id, workspace_id=workspace_id, invoice_id=invoice.id, payment_id=payment.id, provider=provider,
                provider_refund_id=refund_id, provider_payment_id=provider_payment_id[:200], currency=invoice.currency, provider_amount=given, amount=taken,
                refunded_on=business_day(store, now), note=note[:300], recorded_by=actor or PROVIDER_USERS[provider], created_at=now, updated_at=now,
            )
            store.add("invoice_refunds", refund)
            known[refund_id] = refund
            if asked is not None and asked.status in OPEN_REFUND_REQUESTS:
                store.save("refund_requests", asked.model_copy(update={"status": RefundRequestStatus.SUCCEEDED, "refund_id": refund.id, "updated_at": now,
                                                                       "detail": f"Refunded through {name}."}))
            if taken > 0:
                invoice = _reopen(store, invoice, taken, now)
            owed = (f" The invoice is open again with a balance of {invoice.currency.value} {invoice.balance_due()}: issue a credit note if the customer no longer owes it."
                    if taken > 0 else "")
            record_activity(store, context, "invoice_refund_recorded",
                            f"{name} refunded {invoice.currency.value} {given} of a card payment on invoice {invoice.number}.{owed}",
                            account_id=invoice.account_id, project_id=invoice.project_id,
                            metadata={"invoice_id": invoice.id, "payment_id": payment.id, "refund_id": refund.id, "amount": str(taken)},
                            idempotency_key=f"{provider}-refund:{refund_id[:100]}")
            outcome = "refund_recorded"
    return outcome


def apply_moyasar_refunds(issuer_tenant_id: str, metadata: dict[str, str], payment_id: str, refunded_minor: int, currency: str,
                          now: datetime | None = None, *, actor: str | None = None) -> str:
    """Moyasar's running total refunded on a payment (read back from Moyasar's API). Moyasar lists no refunds, so each new
    total is one refund of the difference, keyed by the payment and that total; it is worked out under the invoice's lock."""
    if refunded_minor <= 0:
        return "unchanged"
    return apply_card_refunds("moyasar", issuer_tenant_id, metadata, payment_id, [], now, running_total=(refunded_minor, currency), actor=actor)


__all__ = ["ProviderRefund", "apply_card_refunds", "apply_moyasar_refunds", "payment_refunds", "refunds_of"]
