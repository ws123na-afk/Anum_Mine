"""HTTP routes for money against a sent invoice that is not a plain payment (``/api/v1/business``,
docs/business.md#credit-notes and docs/business-portal.md#reporting-a-payment).

* Credit notes: an owner takes an amount off a sent invoice (a price correction, a returned item,
  a goodwill discount). The invoice never changes what it says; its amount credited and balance
  due move, and a credit note of its own is kept, numbered ``<invoice number>-CN<n>``. A credit
  note cannot exceed what is still owed, so nobody is owed a refund by this feature. One issued
  by mistake is voided by an owner; it keeps its number.
* Payment notices: what a customer reported from the portal link. A notice changes no balance. A
  member or owner who has checked their bank confirms it (which records an ordinary payment, with
  the same rules as recording one by hand) or rejects it with a reason.

ANUM collects nothing and moves no money: there is no payment gateway.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import repository_context, tenant_context
from ..punctuation import plain_dashes
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .api_invoices import InvoiceView, invoice_view
from .common import Reason, get_or_404, manage, read, write
from .invoice_models import CreditNote, CreditNoteStatus, InvoiceStatus, PaymentNotice, PaymentNoticeStatus
from .invoice_service import apply_payment, credit_notes_of, credit_split, notices_of
from .models import Money, money
from .service import audit, http_error, record_activity
from .store import open_business_store

router = APIRouter(prefix="/api/v1/business", tags=["business"])


def _clean(text: str | None, limit: int) -> str:
    return plain_dashes(" ".join((text or "").split()))[:limit]


# -- credit notes ----------------------------------------------------------------------------------


class CreditNoteCreate(BaseModel):
    # Tax inclusive, like the invoice total it is taken from.
    amount: Money
    reason: str = Field(min_length=3, max_length=300)


@router.get("/invoices/{invoice_id}/credit-notes", response_model=list[CreditNote])
def list_credit_notes(invoice_id: str, context: TenantContext = Depends(tenant_context)) -> list[CreditNote]:
    read(context)
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice")
        return credit_notes_of(store, invoice.id)


@router.post("/invoices/{invoice_id}/credit-notes", response_model=InvoiceView, status_code=201)
def issue_credit_note(
    invoice_id: str, payload: CreditNoteCreate, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> InvoiceView:
    """Credit part or all of what is still owed on a sent invoice. Owners only. When the credits and payments
    cover the total the invoice is settled (status paid)."""
    manage(context)
    now = utc_now()
    reason = _clean(payload.reason, 300)
    if len(reason) < 3:
        raise http_error(422, "Say why the invoice is credited (at least three characters).")
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        if invoice.status == InvoiceStatus.PAID:
            raise http_error(409, "The invoice is paid in full: there is nothing left to credit. A refund is outside ANUM; record it in your own books.")
        if invoice.status != InvoiceStatus.SENT:
            raise http_error(409, f"The invoice is {invoice.status.value.replace('_', ' ')}; a credit note is issued against a sent invoice.")
        if payload.amount <= 0:
            raise http_error(422, "Enter an amount above zero.")
        balance = invoice.balance_due()
        if payload.amount > balance:
            raise http_error(422, f"A credit note is at most the balance of {invoice.currency.value} {balance}.")
        existing = credit_notes_of(store, invoice.id)
        seq = max((item.seq for item in existing), default=0) + 1
        subtotal, tax = credit_split(invoice, existing, payload.amount)
        note = CreditNote(
            id=new_id("bcrn"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, invoice_id=invoice.id, account_id=invoice.account_id, seq=seq,
            number=f"{invoice.number}-CN{seq}", currency=invoice.currency, amount=payload.amount, subtotal=subtotal, tax=tax, reason=reason,
            issue_date=now.date(), issued_by=context.user_id, created_at=now, updated_at=now,
        )
        store.add("credit_notes", note)
        credited = money(invoice.amount_credited + payload.amount)
        update: dict[str, Any] = {"amount_credited": credited, "updated_at": now}
        settled = money(invoice.amount_paid + credited) >= invoice.total
        if settled:
            update.update({"status": InvoiceStatus.PAID, "paid_at": now})
        invoice = invoice.model_copy(update=update)
        store.save("invoices", invoice)
        record_activity(store, context, "invoice_credit_note_issued", f"Credit note {note.number} of {invoice.currency.value} {note.amount} issued on invoice {invoice.number}.",
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "credit_note_id": note.id})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.credit_note_issued", invoice_id,
          metadata={"credit_note_id": note.id, "number": note.number, "amount": str(note.amount), "settled": settled})
    return view


@router.post("/invoices/{invoice_id}/credit-notes/{credit_note_id}/void", response_model=InvoiceView)
def void_credit_note(
    invoice_id: str, credit_note_id: str, payload: Reason, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> InvoiceView:
    """Take back a credit note issued by mistake. The amount is owed again; a settled invoice becomes sent again."""
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        note = get_or_404(store, "credit_notes", credit_note_id, "Credit note")
        if note.invoice_id != invoice.id:
            raise http_error(404, "Credit note not found")
        if note.status != CreditNoteStatus.ISSUED:
            raise http_error(409, "This credit note is already void.")
        if invoice.status not in {InvoiceStatus.SENT, InvoiceStatus.PAID}:
            raise http_error(409, f"The invoice is {invoice.status.value.replace('_', ' ')}; its credit notes no longer change.")
        store.save("credit_notes", note.model_copy(update={"status": CreditNoteStatus.VOID, "voided_by": context.user_id, "voided_at": now,
                                                         "void_reason": _clean(payload.reason, 300), "updated_at": now}))
        update: dict[str, Any] = {"amount_credited": money(invoice.amount_credited - note.amount), "updated_at": now}
        if invoice.status == InvoiceStatus.PAID:
            update.update({"status": InvoiceStatus.SENT, "paid_at": None})
        invoice = invoice.model_copy(update=update)
        store.save("invoices", invoice)
        record_activity(store, context, "invoice_credit_note_voided", f"Credit note {note.number} was voided on invoice {invoice.number}.", account_id=invoice.account_id,
                        project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "credit_note_id": note.id})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.credit_note_voided", invoice_id, metadata={"credit_note_id": credit_note_id, "number": note.number})
    return view


# -- payment notices from the portal ---------------------------------------------------------------


class NoticeConfirm(BaseModel):
    note: str = Field(default="", max_length=300)


@router.get("/invoices/{invoice_id}/payment-notices", response_model=list[PaymentNotice])
def list_payment_notices(invoice_id: str, context: TenantContext = Depends(tenant_context)) -> list[PaymentNotice]:
    read(context)
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice")
        return notices_of(store, invoice.id)


def _pending_notice(store: Any, invoice_id: str, notice_id: str) -> PaymentNotice:
    notice = get_or_404(store, "payment_notices", notice_id, "Payment notice")
    if notice.invoice_id != invoice_id:
        raise http_error(404, "Payment notice not found")
    if notice.status != PaymentNoticeStatus.PENDING:
        raise http_error(409, f"This notice was already {notice.status.value}.")
    return notice


@router.post("/invoices/{invoice_id}/payment-notices/{notice_id}/confirm", response_model=InvoiceView)
def confirm_payment_notice(
    invoice_id: str, notice_id: str, payload: NoticeConfirm, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> InvoiceView:
    """Check your bank, then confirm: this records the payment the customer reported (amount, date, method and
    reference as they gave them) with the same rules as a payment recorded by hand."""
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        notice = _pending_notice(store, invoice.id, notice_id)
        extra = _clean(payload.note, 200)
        note = _clean(f"Reported in the portal by {notice.payer_name}." + (f" {extra}" if extra else ""), 300)
        try:
            invoice, payment = apply_payment(store, context, invoice, amount=notice.amount, paid_on=notice.paid_on, method=notice.method, reference=notice.reference,
                                             note=note, now=now)
        except HTTPException as error:
            if error.status_code == 422:
                raise http_error(422, f"{error.detail} Reject this notice and record the payment by hand if the figures differ.") from error
            raise
        store.save("payment_notices", notice.model_copy(update={"status": PaymentNoticeStatus.CONFIRMED, "decided_by": context.user_id, "decided_at": now,
                                                              "decision_note": extra, "payment_id": payment.id, "updated_at": now}))
        record_activity(store, context, "invoice_payment_notice_confirmed", f"Payment of {invoice.currency.value} {notice.amount} reported in the portal was confirmed on invoice {invoice.number}.",
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "notice_id": notice.id, "payment_id": payment.id})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.payment_notice_confirmed", invoice_id,
          metadata={"notice_id": notice_id, "payment_id": payment.id, "amount": str(notice.amount), "paid": invoice.status == InvoiceStatus.PAID})
    return view


@router.post("/invoices/{invoice_id}/payment-notices/{notice_id}/reject", response_model=InvoiceView)
def reject_payment_notice(
    invoice_id: str, notice_id: str, payload: Reason, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> InvoiceView:
    """The money did not arrive, or not as reported. The customer sees the reason on their invoice."""
    write(context)
    now = utc_now()
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice", for_update=True)
        notice = _pending_notice(store, invoice.id, notice_id)
        store.save("payment_notices", notice.model_copy(update={"status": PaymentNoticeStatus.REJECTED, "decided_by": context.user_id, "decided_at": now,
                                                              "decision_note": _clean(payload.reason, 300), "updated_at": now}))
        record_activity(store, context, "invoice_payment_notice_rejected", f"A payment reported in the portal was rejected on invoice {invoice.number}.",
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "notice_id": notice.id})
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.payment_notice_rejected", invoice_id, metadata={"notice_id": notice_id})
    return view


__all__ = ["router"]
