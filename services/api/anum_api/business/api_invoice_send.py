"""HTTP routes for Business invoices, part 3: emailing the invoice, payment reminders and Moyasar
payment links (``/api/v1/business``, docs/business.md#emailing-an-invoice-and-reminders and
docs/business.md#payment-links).

The routes that call out (the delivery planner, Moyasar) are ``async`` and take short units of
work (``dependencies.units_of_work``): every store step runs in its own unit in a worker thread,
so no transaction is open on the event loop or across the call (docs/realtime.md#event-loop).
Sending is never done here: an email is planned as a delivery task that waits for an owner's
approval of its exact payload.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from .. import delivery
from ..dependencies import UnitsOfWork, repository_context, run_in_unit, tenant_context, units_of_work
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


def _view(repository: AnumRepository, context: TenantContext, invoice_id: str) -> InvoiceView:
    with open_business_store(context) as store:
        return invoice_view(store, repository, context, get_or_404(store, "invoices", invoice_id, "Invoice"))


async def _send(invoice_id: str, payload: EmailRequest, context: TenantContext, units: UnitsOfWork, *, reminder: bool) -> EmailResult:
    write(context)
    message = await request_email(context, units, invoice_id, to=(payload.to or "").strip() or None, language=payload.language, reminder=reminder)
    view = await run_in_unit(units, context, _view, context, invoice_id)
    await run_in_unit(units, context, audit, context, "business.invoice.reminder_requested" if reminder else "business.invoice.email_requested", invoice_id,
                      metadata={"message_id": message.id, "status": message.status.value, "approval_id": message.approval_id, "reminder": message.reminder_number})
    return EmailResult(message=message, payload_hash=message.payload_hash, invoice=view)


@router.post("/invoices/{invoice_id}/email", response_model=EmailResult, status_code=status.HTTP_201_CREATED)
async def email_invoice(invoice_id: str, payload: EmailRequest, context: TenantContext = Depends(tenant_context),
                        units: UnitsOfWork = Depends(units_of_work)) -> EmailResult:
    """Ask to email a sent invoice (PDF attached). It waits in Approvals; nothing is sent until an owner approves it."""
    return await _send(invoice_id, payload, context, units, reminder=False)


@router.post("/invoices/{invoice_id}/reminders", response_model=EmailResult, status_code=status.HTTP_201_CREATED)
async def remind_invoice(invoice_id: str, payload: EmailRequest, context: TenantContext = Depends(tenant_context),
                         units: UnitsOfWork = Depends(units_of_work)) -> EmailResult:
    """Ask to send the next payment reminder for an overdue invoice. It waits for an owner's approval."""
    return await _send(invoice_id, payload, context, units, reminder=True)


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
async def make_payment_link(invoice_id: str, context: TenantContext = Depends(tenant_context), units: UnitsOfWork = Depends(units_of_work)) -> InvoiceView:
    """A Moyasar payment page for the balance. The same link comes back while the balance is unchanged.
    Moyasar is called with no transaction open (``create_payment_link`` uses its own short units)."""
    write(context)
    if not settings.web_app_url:
        raise http_error(409, "The web address of ANUM is not set (ANUM_WEB_APP_URL), so Moyasar would have nowhere to send the customer back.")
    link = await asyncio.to_thread(create_payment_link, context, invoice_id, app_url=settings.web_app_url)
    view = await run_in_unit(units, context, _view, context, invoice_id)
    await run_in_unit(units, context, audit, context, "business.invoice.payment_link_created", invoice_id, metadata={"link_id": link.id, "amount": str(link.amount)})
    return view


delivery.register_attachment_resolver(ATTACHMENT_KIND, resolve_attachment)
