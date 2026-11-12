"""Refunds of a Business invoice's card payment started from inside ANUM (docs/business.md#refunds-from-anum).

A member or owner asks for a refund of part or all of a card payment the invoice recorded through Moyasar or Stripe.
The request never moves money by itself:

* **Approval with the payload hash.** The request is a paused call of ``business.refund_card_payment`` (high risk,
  always asks: members ask, owners decide) in the ordinary approvals flow. An owner approves it by sending back the
  payload hash they were shown; the runtime runs only the exact call that was hashed.
* **Never above what the payment can give back.** What the card was charged, less the refunds already recorded and
  less every other open request on that payment (an open request holds its amount). Checked when it is asked for and
  again, under the payment's row lock, when the approved call runs.
* **One idempotency key per request.** The form sends one key per submit (a retry is the same request); the provider
  call carries the request's own key (``anum-binref-<request id>`` for Stripe), and a request that already reached
  the provider is never sent again: a replayed Stripe request first looks for the refund carrying its id in the
  metadata, a replayed Moyasar request compares the payment's refunded total.
* **Recorded like a webhook.** The provider's answer is recorded through ``invoice_refunds.apply_card_refunds`` (the
  path the refund webhook uses), keyed by the provider's refund id (Stripe) or the payment and its running total
  (Moyasar), so the webhook that follows finds the refund and counts nothing twice. A Stripe refund that is still
  pending waits for that webhook.

No transaction is open while the provider is called: the claim, the provider call and the recording each run on
their own, and the tool's blocking work runs in a worker thread, never on the event loop.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError

from ..agent_tools import ToolCall, ToolDefinition, ToolResult
from ..repository import AnumRepository
from ..schemas import ApprovalStatus, RiskLevel, TenantContext, new_id, utc_now
from .invoice_models import (
    OPEN_REFUND_REQUESTS,
    Invoice,
    InvoicePayment,
    InvoiceStatus,
    RefundRequest,
    RefundRequestStatus,
    RefundStatus,
)
from .invoice_refunds import PROVIDER_NAMES, ProviderRefund, apply_card_refunds, apply_moyasar_refunds, payment_refunds
from .models import money
from .service import audit, http_error, record_activity
from .store import BusinessStore, Query, UniqueViolation, open_business_store

REFUND_TOOL = "business.refund_card_payment"
CARD_PROVIDERS = ("moyasar", "stripe")


def refund_definition() -> ToolDefinition:
    return ToolDefinition(
        name=REFUND_TOOL,
        description="Refund part or all of one card payment of a Business invoice through Moyasar or Stripe (moves money back to the customer's card).",
        risk_level=RiskLevel.HIGH,
        # Members ask; only an owner decides the approval (approval:decide).
        required_roles=frozenset({"owner", "member"}),
        timeout_seconds=60,
        idempotent=True,
        category="general",
        action_kind="other",
        always_ask=True,
    )


def requests_of(store: BusinessStore, invoice_id: str) -> list[RefundRequest]:
    items, _ = store.find("refund_requests", Query(where={"invoice_id": invoice_id}, order_by="created_at", descending=True, limit=200))
    return items


def _open_requests(store: BusinessStore, payment_id: str) -> list[RefundRequest]:
    items, _ = store.find("refund_requests", Query(where={"payment_id": payment_id, "status": set(OPEN_REFUND_REQUESTS)},
                                                   order_by="created_at", limit=200))
    return items


def charged(payment: InvoicePayment) -> Decimal:
    return money(payment.provider_amount if payment.provider_amount is not None else payment.amount)


def refundable(store: BusinessStore, payment: InvoicePayment, *, excluding: str | None = None) -> Decimal:
    """What the payment can still give back: charged, less recorded refunds, less every other open request."""
    given = sum((row.provider_amount for row in payment_refunds(store, payment.id) if row.status == RefundStatus.RECORDED), Decimal(0))
    held = sum((row.amount for row in _open_requests(store, payment.id) if row.id != excluding), Decimal(0))
    return money(max(charged(payment) - given - held, Decimal(0)))


def refund_options(store: BusinessStore, invoice: Invoice, payments: list[InvoicePayment]) -> dict[str, str]:
    """payment id -> what a new request may ask for, for every card payment that can still give money back."""
    if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PAID):
        return {}
    out: dict[str, str] = {}
    for payment in payments:
        if payment.provider in CARD_PROVIDERS and payment.provider_payment_id:
            left = refundable(store, payment)
            if left > 0:
                out[payment.id] = str(left)
    return out


def refresh_requests(store: BusinessStore, repository: AnumRepository | None, context: TenantContext, invoice_id: str) -> list[RefundRequest]:
    """The invoice's refund requests with a rejected or lapsed approval closed (its amount is no longer held)."""
    items = requests_of(store, invoice_id)
    if repository is None:
        return items
    now = utc_now()
    out: list[RefundRequest] = []
    for item in items:
        if item.status == RefundRequestStatus.WAITING_APPROVAL and item.approval_id:
            approval = repository.get_approval(item.approval_id, context)
            lapsed = approval is not None and (approval.status == ApprovalStatus.EXPIRED or (
                approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at <= now))
            if approval is None or approval.status == ApprovalStatus.REJECTED or lapsed:
                detail = ("The approval request lapsed before anyone decided." if lapsed else
                          f"Rejected by {approval.decided_by or 'an approver'}" + (f": {approval.decision_reason}" if approval and approval.decision_reason else ".")
                          if approval is not None else "The approval request is gone.")
                item = item.model_copy(update={"status": RefundRequestStatus.REJECTED, "detail": detail[:500], "decided_at": now, "updated_at": now,
                                               "decided_by": approval.decided_by if approval is not None else None})
                store.save("refund_requests", item)
        out.append(item)
    return out


def arguments_of(request: RefundRequest, invoice: Invoice, payment: InvoicePayment, client: str) -> dict[str, Any]:
    """What the approver sees and the payload hash binds: the invoice, the payment, the money and the reason."""
    return {
        "refund_request_id": request.id,
        "invoice_id": invoice.id,
        "invoice_number": invoice.number or "",
        "client": client,
        "payment_id": payment.id,
        "provider": request.provider,
        "provider_payment_id": payment.provider_payment_id or "",
        "currency": request.currency.value,
        "amount": str(request.amount),
        "charged": str(charged(payment)),
        "reason": request.reason,
    }


def _provider_or_refusal(tenant_id: str, provider: str) -> Any:
    """The issuer's adapter for ``provider``, or raise 409 saying how to turn it on."""
    from ..billing.api import stripe_for
    from ..billing.selling import moyasar_for
    from .invoice_send import _issuer

    issuer = _issuer(tenant_id)
    adapter = moyasar_for(issuer) if provider == "moyasar" else stripe_for(issuer)
    if adapter is None:
        raise http_error(409, f"{PROVIDER_NAMES[provider]} is turned off, so the card cannot be refunded from ANUM. The owner turns it on again "
                              f"in Platform billing, Settings, or refunds it in the {PROVIDER_NAMES[provider]} dashboard (ANUM records that refund itself).")
    return adapter


def request_refund(context: TenantContext, repository: AnumRepository, invoice_id: str, payment_id: str, *, amount: Decimal, reason: str,
                   idempotency_key: str) -> tuple[RefundRequest, bool]:
    """Ask for a refund; it waits for an owner's approval. Returns the request and whether it is a replay of the same key."""
    from .tools import retire_approval

    amount = money(amount)
    with open_business_store(context) as store:
        existing: RefundRequest | None = store.first("refund_requests", idempotency_key=idempotency_key)
        if existing is not None:
            if existing.payment_id != payment_id or existing.invoice_id != invoice_id or existing.amount != amount:
                raise http_error(409, "That form was already sent for a different refund. Reload the invoice and try again.")
            return existing, True
        invoice: Invoice | None = store.get("invoices", invoice_id)
        if invoice is None:
            raise http_error(404, "Invoice not found")
        payment: InvoicePayment | None = store.get("invoice_payments", payment_id)
        if payment is None or payment.invoice_id != invoice.id:
            raise http_error(404, "That payment is not on this invoice.")
        if payment.provider not in CARD_PROVIDERS or not payment.provider_payment_id:
            raise http_error(409, "Only a card payment taken through Moyasar or Stripe can be refunded from ANUM. Give a bank transfer back "
                                  "yourself and issue a credit note for it.")
        provider = payment.provider
    _provider_or_refusal(context.tenant_id, provider)
    now = utc_now()
    asked: list[tuple[str, str]] = []  # (task id, approval id) of the approval this call planned
    try:
        return _write_request(context, repository, invoice_id, payment_id, amount, reason, idempotency_key, provider, now, asked), False
    except (IntegrityError, UniqueViolation):
        # Another request with the same key was written between the check above and this insert (two submits at
        # once). This unit was rolled back; the approval it planned lapses, and the key's rules decide the answer.
        for task_id, approval_id in asked:
            retire_approval(repository, context, task_id, approval_id)
        with open_business_store(context) as store:
            existing = store.first("refund_requests", idempotency_key=idempotency_key)
        if existing is not None and existing.payment_id == payment_id and existing.invoice_id == invoice_id and existing.amount == amount:
            return existing, True
        raise http_error(409, "That form was already sent for a different refund. Reload the invoice and try again.") from None


def _write_request(context: TenantContext, repository: AnumRepository, invoice_id: str, payment_id: str, amount: Decimal, reason: str,
                   idempotency_key: str, provider: str, now: datetime, asked: list[tuple[str, str]]) -> RefundRequest:
    """One unit of work: re-check under the payment's lock, plan the approval and write the request."""
    from .tools import request_approval

    with open_business_store(context) as store:
        invoice = store.get("invoices", invoice_id)
        payment = store.get("invoice_payments", payment_id, for_update=True)  # one request at a time per payment
        if invoice is None or payment is None:
            raise http_error(404, "Invoice not found")
        if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PAID):
            raise http_error(409, f"The invoice is {invoice.status.value.replace('_', ' ')}; only a sent or paid invoice's payment is refunded.")
        left = refundable(store, payment)
        if amount <= 0 or amount > left:
            raise http_error(422, f"At most {invoice.currency.value} {left} can be refunded from this payment: it was charged "
                                  f"{invoice.currency.value} {charged(payment)}, and refunds already made or waiting for approval hold the rest.")
        account = store.get("accounts", invoice.account_id)
        client = account.name if account is not None else invoice.bill_to_name
        request = RefundRequest(
            id=new_id("binrfq"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, invoice_id=invoice.id, payment_id=payment.id,
            provider=provider, currency=invoice.currency, amount=amount, reason=reason, idempotency_key=idempotency_key, requested_by=context.user_id,
            created_at=now, updated_at=now,
        )
        title = f"Refund {invoice.currency.value} {amount} of a card payment on invoice {invoice.number or invoice.id}"
        task, approval = request_approval(repository, context, arguments_of(request, invoice, payment, client), title, tool=REFUND_TOOL,
                                          prompt=f"{title} through {PROVIDER_NAMES[provider]}.")
        asked.append((task.id, approval.id))
        request = request.model_copy(update={"approval_id": approval.id, "task_id": task.id, "payload_hash": approval.payload_hash})
        store.add("refund_requests", request)
        record_activity(store, context, "invoice_refund_requested", f"{title} is waiting for an owner's approval.",
                        account_id=invoice.account_id, project_id=invoice.project_id,
                        metadata={"invoice_id": invoice.id, "payment_id": payment.id, "request_id": request.id, "approval_id": approval.id})
    audit(repository, context, "business.invoice.refund_requested", invoice_id,
          metadata={"request_id": request.id, "payment_id": payment_id, "approval_id": approval.id, "provider": provider})
    return request


def cancel_request(context: TenantContext, repository: AnumRepository, invoice_id: str, request_id: str) -> RefundRequest:
    """Withdraw a request still waiting for approval: the approval lapses and the amount is free again."""
    from .tools import retire_approval

    now = utc_now()
    with open_business_store(context) as store:
        request: RefundRequest | None = store.get("refund_requests", request_id, for_update=True)
        if request is None or request.invoice_id != invoice_id:
            raise http_error(404, "Refund request not found")
        if request.status != RefundRequestStatus.WAITING_APPROVAL:
            raise http_error(409, f"The refund request is {request.status.value.replace('_', ' ')}; only one waiting for approval can be withdrawn.")
        if request.requested_by != context.user_id and "owner" not in context.roles:
            raise http_error(403, "Only the person who asked for this refund, or a workspace owner, can withdraw it.")
        retire_approval(repository, context, request.task_id, request.approval_id)
        request = request.model_copy(update={"status": RefundRequestStatus.CANCELLED, "detail": "Withdrawn before approval.", "decided_by": context.user_id,
                                             "decided_at": now, "updated_at": now})
        store.save("refund_requests", request)
    audit(repository, context, "business.invoice.refund_cancelled", invoice_id, metadata={"request_id": request_id})
    return request


# -- the approved call ------------------------------------------------------------------------------


def _failed(summary: str) -> ToolResult:
    return ToolResult(status="failed", summary=summary)


def _close(context: TenantContext, request_id: str, status: RefundRequestStatus, detail: str, **extra: Any) -> None:
    now = utc_now()
    with open_business_store(context) as store:
        current: RefundRequest | None = store.get("refund_requests", request_id, for_update=True)
        if current is None or current.status not in OPEN_REFUND_REQUESTS:
            return
        store.save("refund_requests", current.model_copy(update={"status": status, "detail": detail[:500], "updated_at": now, **extra}))


def _claim(call: ToolCall, context: TenantContext) -> tuple[RefundRequest, InvoicePayment, bool] | ToolResult:
    """One short transaction: the request is the one approved, its amount still fits, and it moves to processing.
    Returns the request, its payment and whether an earlier run already claimed it (a replay)."""
    args = call.arguments
    request_id = str(args.get("refund_request_id", ""))
    now = utc_now()
    with open_business_store(context) as store:
        request: RefundRequest | None = store.get("refund_requests", request_id, for_update=True)
        if request is None:
            return _failed("The refund request is gone; nothing was refunded.")
        if request.status in (RefundRequestStatus.SUCCEEDED, RefundRequestStatus.PENDING_PROVIDER):
            return ToolResult(status="succeeded", summary="This refund was already sent to the provider.", output={"refund_request_id": request.id})
        if request.status not in (RefundRequestStatus.WAITING_APPROVAL, RefundRequestStatus.PROCESSING):
            return _failed(f"The refund request is {request.status.value.replace('_', ' ')}; nothing was refunded.")
        if (str(request.amount) != str(args.get("amount")) or request.currency.value != args.get("currency") or request.payment_id != args.get("payment_id")
                or request.invoice_id != args.get("invoice_id") or request.provider != args.get("provider")):
            return _failed("The refund request does not match what was approved; nothing was refunded.")
        payment: InvoicePayment | None = store.get("invoice_payments", request.payment_id, for_update=True)
        if payment is None or payment.provider_payment_id != args.get("provider_payment_id"):
            store.save("refund_requests", request.model_copy(update={"status": RefundRequestStatus.FAILED, "updated_at": now,
                                                                     "detail": "The payment was removed after the request; nothing was refunded."}))
            return _failed("The payment was removed after the request; nothing was refunded.")
        replay = request.status == RefundRequestStatus.PROCESSING
        if not replay:
            left = refundable(store, payment, excluding=request.id)
            if request.amount > left:
                detail = f"Only {request.currency.value} {left} can be refunded from this payment now (another refund was recorded); nothing was refunded."
                store.save("refund_requests", request.model_copy(update={"status": RefundRequestStatus.FAILED, "detail": detail, "updated_at": now,
                                                                         "decided_by": context.user_id, "decided_at": now}))
                return _failed(detail)
            request = request.model_copy(update={"status": RefundRequestStatus.PROCESSING, "decided_by": context.user_id, "decided_at": now, "updated_at": now})
            store.save("refund_requests", request)
    return request, payment, replay


def _metadata(request: RefundRequest) -> dict[str, str]:
    return {"anum_kind": "business_invoice", "anum_tenant_id": request.tenant_id, "anum_workspace_id": request.workspace_id,
            "anum_business_invoice_id": request.invoice_id}


def _minor(amount: Decimal) -> int:
    return int((money(amount) * 100).to_integral_value())


def _run_refund(call: ToolCall, context: TenantContext) -> ToolResult:
    from ..billing.providers import ProviderError, ProviderUnreachable

    claimed = _claim(call, context)
    if isinstance(claimed, ToolResult):
        return claimed
    request, payment, replay = claimed
    name = PROVIDER_NAMES[request.provider]
    try:
        adapter = _provider_or_refusal(context.tenant_id, request.provider)
    except Exception as exc:  # the HTTPException that says how to turn it on
        detail = str(getattr(exc, "detail", exc))
        _close(context, request.id, RefundRequestStatus.FAILED, detail)
        return _failed(detail)
    provider_payment_id = payment.provider_payment_id or ""
    actor = context.user_id
    try:
        if request.provider == "stripe":
            found = None
            if replay:
                found = next((r for r in adapter.refunds_of(provider_payment_id) if r.metadata.get("anum_refund_request_id") == request.id), None)
            result = found or adapter.refund(
                payment_intent=provider_payment_id, amount_minor=_minor(request.amount), idempotency_key=f"anum-binref-{request.id}",
                metadata={**_metadata(request), "anum_refund_request_id": request.id},
            )
            provider_refund_id = result.id[:200]
            if result.status in ("failed", "canceled"):
                _close(context, request.id, RefundRequestStatus.FAILED, f"Stripe did not refund the card (status {result.status}).", provider_refund_id=provider_refund_id)
                return _failed(f"Stripe did not refund the card (status {result.status}).")
            with open_business_store(context) as store:
                current = store.get("refund_requests", request.id, for_update=True)
                if current is not None:
                    store.save("refund_requests", current.model_copy(update={"provider_refund_id": provider_refund_id, "updated_at": utc_now()}))
            if result.status != "succeeded":
                _close(context, request.id, RefundRequestStatus.PENDING_PROVIDER,
                       "Stripe accepted the refund and is still processing it; it is recorded when Stripe reports it finished.")
                outcome = "pending"
            else:
                outcome = apply_card_refunds("stripe", context.tenant_id, _metadata(request), provider_payment_id,
                                             [ProviderRefund(result.id, result.amount_minor, result.currency, result.status)], actor=actor)
        else:
            with open_business_store(context) as store:
                booked = sum((_minor(row.provider_amount) for row in payment_refunds(store, payment.id) if row.status == RefundStatus.RECORDED), 0)
            already = adapter.fetch_payment(provider_payment_id).refunded_minor if replay else 0
            if replay and already >= booked + _minor(request.amount):
                total = already  # the earlier run reached Moyasar; its answer was lost
            else:
                total = adapter.refund(provider_payment_id, _minor(request.amount)).refunded_minor
            outcome = apply_moyasar_refunds(context.tenant_id, _metadata(request), provider_payment_id, total, request.currency.value, actor=actor)
            with open_business_store(context) as store:
                current = store.get("refund_requests", request.id, for_update=True)
                if current is not None:
                    store.save("refund_requests", current.model_copy(update={"provider_refund_id": f"{provider_payment_id[:150]}:{total}", "updated_at": utc_now()}))
    except ProviderUnreachable as exc:
        # It may have happened: the request stays processing; approving runs nothing twice, and the webhook or a retry settles it.
        detail = f"{exc} The refund is kept as processing; {name}'s webhook records it if it went through."
        with open_business_store(context) as store:
            current = store.get("refund_requests", request.id, for_update=True)
            if current is not None:
                store.save("refund_requests", current.model_copy(update={"detail": detail[:500], "updated_at": utc_now()}))
        return _failed(detail)
    except ProviderError as exc:
        _close(context, request.id, RefundRequestStatus.FAILED, f"{name} refused the refund: {exc}")
        return _failed(f"{name} refused the refund: {exc}")
    with open_business_store(context) as store:
        current = store.get("refund_requests", request.id, for_update=True)
        if current is not None and outcome != "pending":
            refund_id = current.refund_id
            if refund_id is None and current.provider_refund_id:
                recorded = store.first("invoice_refunds", provider=current.provider, provider_refund_id=current.provider_refund_id)
                refund_id = recorded.id if recorded is not None else None
            current = current.model_copy(update={"status": RefundRequestStatus.SUCCEEDED, "refund_id": refund_id, "updated_at": utc_now(),
                                                 "detail": f"Refunded through {name}."})
            store.save("refund_requests", current)
        invoice = store.get("invoices", request.invoice_id)
        number = invoice.number if invoice is not None else request.invoice_id
    audit(None, context, "business.invoice.refunded", request.invoice_id,
          metadata={"request_id": request.id, "provider": request.provider, "outcome": outcome})
    summary = (f"{name} is refunding {request.currency.value} {request.amount} of the card payment on invoice {number}; it is recorded when {name} reports it finished."
               if outcome == "pending" else f"Refunded {request.currency.value} {request.amount} of the card payment on invoice {number} through {name}.")
    return ToolResult(status="succeeded", summary=summary, output={"refund_request_id": request.id, "outcome": outcome},
                      audit_metadata={"refund_request_id": request.id, "invoice_id": request.invoice_id})


async def refund_card_payment(call: ToolCall, context: TenantContext) -> ToolResult:
    """The approved refund: its store steps and the provider's HTTP run in a worker thread, never on the event loop."""
    return await asyncio.to_thread(_run_refund, call, context)


__all__ = ["REFUND_TOOL", "cancel_request", "refund_card_payment", "refund_definition", "refund_options", "refundable", "refresh_requests",
           "request_refund", "requests_of"]
