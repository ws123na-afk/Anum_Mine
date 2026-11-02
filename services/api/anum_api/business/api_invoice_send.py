"""HTTP routes for Business invoices, part 3: emailing the invoice, payment reminders and Moyasar
payment links (``/api/v1/business``, docs/business.md#emailing-an-invoice-and-reminders and
docs/business.md#payment-links).

Every route is a plain ``def`` route (FastAPI runs it in a worker thread), so no store or unit
of work is opened on the event loop. Sending is never done here: an email is planned as a
delivery task that waits for an owner's approval of its exact payload.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from .. import delivery
from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext
from ..settings import settings
from .api_invoices import InvoiceView, invoice_view
from .common import get_or_404, read, write
from .invoice_models import InvoiceMessage
from .invoice_send import ATTACHMENT_KIND, create_payment_link, moyasar_state, refresh_messages, request_email, resolve_attachment
from .service import audit, http_error
from .store import open_business_store

router = APIRouter(prefix="/api/v1/business", tags=["business"])


class EmailRequest(BaseModel):
    # Empty: the invoice's buyer e-mail.
    to: str | None = Field(default=None, max_length=254)
    language: Literal["en", "ar"] = "en"


class EmailResult(BaseModel):
    message: InvoiceMessage
    payload_hash: str | None
    invoice: InvoiceView


@router.get("/invoices/{invoice_id}/messages", response_model=list[InvoiceMessage])
def list_messages(invoice_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> list[InvoiceMessage]:
    """The invoice emails and reminders asked for, with their approval status."""
    read(context)
    with open_business_store(context) as store:
        get_or_404(store, "invoices", invoice_id, "Invoice")
        return refresh_messages(store, repository, context, invoice_id)


def _send(invoice_id: str, payload: EmailRequest, context: TenantContext, repository: AnumRepository, *, reminder: bool) -> EmailResult:
    write(context)
    message = request_email(context, repository, invoice_id, to=(payload.to or "").strip() or None, language=payload.language, reminder=reminder)
    with open_business_store(context) as store:
        invoice = get_or_404(store, "invoices", invoice_id, "Invoice")
        view = invoice_view(store, repository, context, invoice)
    audit(repository, context, "business.invoice.reminder_requested" if reminder else "business.invoice.email_requested", invoice_id,
          metadata={"message_id": message.id, "status": message.status.value, "approval_id": message.approval_id, "reminder": message.reminder_number})
    return EmailResult(message=message, payload_hash=message.payload_hash, invoice=view)


@router.post("/invoices/{invoice_id}/email", response_model=EmailResult, status_code=status.HTTP_201_CREATED)
def email_invoice(invoice_id: str, payload: EmailRequest, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> EmailResult:
    """Ask to email a sent invoice (PDF attached). It waits in Approvals; nothing is sent until an owner approves it."""
    return _send(invoice_id, payload, context, repository, reminder=False)


@router.post("/invoices/{invoice_id}/reminders", response_model=EmailResult, status_code=status.HTTP_201_CREATED)
def remind_invoice(invoice_id: str, payload: EmailRequest, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> EmailResult:
    """Ask to send the next payment reminder for an overdue invoice. It waits for an owner's approval."""
    return _send(invoice_id, payload, context, repository, reminder=True)


class ProviderState(BaseModel):
    moyasar: Literal["on", "off"]
    mode: str | None = None


@router.get("/payment-provider", response_model=ProviderState)
def payment_provider(context: TenantContext = Depends(tenant_context)) -> ProviderState:
    """Whether card payment links can be made: the owner's Moyasar (Platform billing) is on."""
    read(context)
    state: dict[str, Any] = moyasar_state(context.tenant_id)
    return ProviderState(**state)


@router.post("/invoices/{invoice_id}/payment-link", response_model=InvoiceView, status_code=status.HTTP_201_CREATED)
def make_payment_link(invoice_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> InvoiceView:
    """A Moyasar payment page for the balance. The same link comes back while the balance is unchanged."""
    write(context)
    if not settings.web_app_url:
        raise http_error(409, "The web address of ANUM is not set (ANUM_WEB_APP_URL), so Moyasar would have nowhere to send the customer back.")
    link = create_payment_link(context, invoice_id, app_url=settings.web_app_url)
    with open_business_store(context) as store:
        view = invoice_view(store, repository, context, get_or_404(store, "invoices", invoice_id, "Invoice"))
    audit(repository, context, "business.invoice.payment_link_created", invoice_id, metadata={"link_id": link.id, "amount": str(link.amount)})
    return view


delivery.register_attachment_resolver(ATTACHMENT_KIND, resolve_attachment)
