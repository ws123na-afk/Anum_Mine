"""Emailing a Business invoice, payment reminders and Moyasar payment links (docs/business.md).

* **Email and reminders.** A person asks; nothing is sent then. The email (the invoice PDF
  attached) is planned as a delivery task through ``delivery.request_delivery``, and it waits
  for an owner to approve that exact payload in Approvals, like every external send. Before
  planning, ANUM checks the workspace's policy would ask for that send: a workspace that let the
  email tool send without asking is refused, so an invoice email never goes out unapproved.
  Each request is a ``business_invoice_messages`` row; its status follows the approval and the
  task (``refresh_messages``).
* **Reminders** are for a sent invoice past its due date with a balance. The schedule
  (``REMINDER_DAYS`` after the due date) says when the next one is due; a person still presses
  the button, and the owner still approves each one. One live request per reminder number.
* **Payment links.** When the owner has a card provider on (Platform billing, the same account
  the owner's ANUM subscriptions use), a member can make a hosted payment page for a sent
  invoice's balance: a Moyasar invoice page (``billing/moyasar.py``) when Moyasar is on, else a
  Stripe Payment Link (``billing/providers.py``; a UAE company's Stripe account, AED in fils).
  The provider's webhook reaches the billing webhook; a payment for a Business invoice is
  recorded here once per provider payment id (``apply_card_payment``): for Moyasar with the
  amount and status read back from Moyasar's API, for Stripe from the signed event.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from ..agent_tools import ToolCall, ToolPolicyOutcome
from ..repository import AnumRepository
from ..schemas import ApprovalStatus, TaskStatus, TenantContext, new_id, utc_now
from .invoice_models import (
    Invoice,
    InvoiceMessage,
    InvoicePayment,
    InvoiceStatus,
    MessageKind,
    MessageStatus,
    PaymentLink,
    PaymentLinkStatus,
    PaymentMethod,
)
from .zatca import qr_for_invoice, tax_settings_for
from .invoice_pdf import invoice_filename, render_invoice_pdf
from .invoice_service import apply_payment, business_day, credit_notes_of, payments_of
from .models import money
from .service import http_error, record_activity
from .store import MAX_SCAN, BusinessStore, Query, open_business_store

logger = logging.getLogger("anum.business")

ATTACHMENT_KIND = "business_invoice"
# Days after the due date when reminder 1, 2, 3 and 4 fall due.
REMINDER_DAYS = (1, 7, 14, 30)
MAX_REMINDERS = 10
LIVE = (MessageStatus.WAITING_APPROVAL, MessageStatus.SENT)
EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
MOYASAR_USER = "system:moyasar"
PROVIDER_USERS = {"moyasar": MOYASAR_USER, "stripe": "system:stripe"}
PROVIDER_NAMES = {"moyasar": "Moyasar", "stripe": "Stripe"}
ID = re.compile(r"^[A-Za-z0-9_-]{3,80}$")


def is_email(value: str) -> bool:
    return bool(EMAIL.match(value or ""))


# -- messages -----------------------------------------------------------------------------------


def messages_of(store: BusinessStore, invoice_id: str) -> list[InvoiceMessage]:
    items, _ = store.find("invoice_messages", Query(where={"invoice_id": invoice_id}, order_by="created_at", descending=False, limit=MAX_SCAN))
    return items


def refresh_messages(store: BusinessStore, repository: AnumRepository | None, context: TenantContext, invoice_id: str) -> list[InvoiceMessage]:
    """Messages with their status brought up to date from the approval and the delivery task."""
    items = messages_of(store, invoice_id)
    if repository is None:
        return items
    now = utc_now()
    for item in items:
        if item.status != MessageStatus.WAITING_APPROVAL or not item.approval_id:
            continue
        approval = repository.get_approval(item.approval_id, context)
        if approval is None:
            continue
        status, detail = item.status, item.detail
        if approval.status == ApprovalStatus.REJECTED:
            status, detail = MessageStatus.REJECTED, f"Rejected by {approval.decided_by or 'an approver'}" + (
                f": {approval.decision_reason}" if approval.decision_reason else ".")
        elif approval.status == ApprovalStatus.EXPIRED or (approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at <= now):
            status, detail = MessageStatus.REJECTED, "The approval request lapsed before anyone decided."
        elif approval.status == ApprovalStatus.APPROVED and item.task_id:
            task = repository.get_task(item.task_id, context)
            if task is not None and task.status == TaskStatus.COMPLETED:
                status, detail = MessageStatus.SENT, "Sent after approval."
            elif task is not None and task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                status, detail = MessageStatus.FAILED, (task.result or "The email tool could not send it.")
        if status != item.status:
            item = item.model_copy(update={"status": status, "detail": detail[:500], "updated_at": now})
            store.save("invoice_messages", item)
            if status == MessageStatus.SENT:
                invoice = store.get("invoices", invoice_id)
                record_activity(store, context, "invoice_emailed" if item.kind == MessageKind.INVOICE else "invoice_reminder_sent",
                                f"{'Invoice' if item.kind == MessageKind.INVOICE else 'Payment reminder'} {invoice.number if invoice else ''} emailed after approval.",
                                account_id=invoice.account_id if invoice else None, metadata={"invoice_id": invoice_id, "message_id": item.id},
                                idempotency_key=f"invoice-message-sent:{item.id}")
    return messages_of(store, invoice_id)


def live_reminders(messages: list[InvoiceMessage]) -> int:
    return sum(1 for item in messages if item.kind == MessageKind.REMINDER and item.status in LIVE)


def days_overdue(invoice: Invoice, today: date) -> int:
    if invoice.status != InvoiceStatus.SENT or invoice.due_date is None or invoice.balance_due() <= 0:
        return 0
    return max(0, (today - invoice.due_date).days)


def next_reminder_due(invoice: Invoice, messages: list[InvoiceMessage], today: date) -> int | None:
    """The reminder the schedule says is due now (1 to 4), or None."""
    late = days_overdue(invoice, today)
    if late <= 0:
        return None
    count = live_reminders(messages)
    if count >= len(REMINDER_DAYS) or any(item.kind == MessageKind.REMINDER and item.status == MessageStatus.WAITING_APPROVAL for item in messages):
        return None
    return count + 1 if late >= REMINDER_DAYS[count] else None


def email_text(invoice: Invoice, *, language: Literal["en", "ar"], reminder: int | None, seller_name: str, link: str | None) -> tuple[str, str]:
    """The subject and body: short, factual, in the language chosen. The PDF itself is bilingual."""
    number = invoice.number or ""
    amount = f"{invoice.currency.value} {invoice.balance_due():,.2f}"
    due = invoice.due_date.isoformat() if invoice.due_date else "-"
    name = invoice.bill_to_name or ""
    if language == "ar":
        pay = f"\n\nيمكنكم الدفع إلكترونيًا من هذا الرابط: {link}" if link else ""
        if reminder:
            subject = f"تذكير: الفاتورة {number} متأخرة السداد"
            body = (f"مرحبًا {name}،\n\nكان تاريخ استحقاق الفاتورة {number} هو {due}، وما زال مبلغ {amount} مستحقًا. "
                    f"الفاتورة مرفقة. نرجو السداد، أو الرد على هذه الرسالة إن كان هناك خطأ.{pay}\n\n{seller_name}")
        else:
            subject = f"فاتورة {number} من {seller_name}"
            body = f"مرحبًا {name}،\n\nمرفقة الفاتورة رقم {number} بمبلغ {amount}، وتاريخ استحقاقها {due}.{pay}\n\n{seller_name}"
    else:
        pay = f"\n\nYou can pay it online here: {link}" if link else ""
        if reminder:
            subject = f"Reminder: invoice {number} is overdue"
            body = (f"Hello {name},\n\nInvoice {number} was due on {due} and {amount} is still open. The invoice is attached. "
                    f"Please pay it, or reply if something is wrong.{pay}\n\n{seller_name}")
        else:
            subject = f"Invoice {number} from {seller_name}"
            body = f"Hello {name},\n\nInvoice {number} for {amount} is attached. It is due on {due}.{pay}\n\n{seller_name}"
    return subject[:200], body


def resolve_attachment(context: TenantContext, item_id: str, fmt: str) -> tuple[str, str, bytes] | None:
    """The invoice PDF for an approved email (``delivery.resolve_attachment`` checks its hash)."""
    if fmt != "pdf":
        return None
    tax = tax_settings_for(context)  # read first: the ZATCA QR is printed only when the business turned it on
    with open_business_store(context) as store:
        invoice = store.get("invoices", item_id)
        if invoice is None or invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PAID):
            return None
        content = render_invoice_pdf(invoice, payments_of(store, invoice.id), credit_notes_of(store, invoice.id), store.get_settings().billing,
                                     zatca_qr=qr_for_invoice(tax, invoice),
                                     payment_link=open_link_url(store, invoice))
    return invoice_filename(invoice), "application/pdf", content


def _prepare_email(context: TenantContext, repository: AnumRepository, invoice_id: str, to: str | None, language: Literal["en", "ar"],
                   reminder: bool) -> tuple[Invoice, str, int | None, str, str]:
    """Read and check, in one short unit of work: the invoice, the address, the reminder number and the text."""
    today = utc_now().date()
    with open_business_store(context) as store:
        invoice: Invoice | None = store.get("invoices", invoice_id)
        if invoice is None:
            raise http_error(404, "Invoice not found")
        if reminder:
            if invoice.status != InvoiceStatus.SENT or invoice.balance_due() <= 0:
                raise http_error(409, "Only a sent invoice with a balance gets a payment reminder.")
            if days_overdue(invoice, today) <= 0:
                raise http_error(409, f"Invoice {invoice.number} is not overdue yet; it is due on {invoice.due_date}.")
        elif invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PAID):
            raise http_error(409, f"The invoice is {invoice.status.value.replace('_', ' ')}; only a sent invoice is emailed. Ask for approval to send it first.")
        messages = refresh_messages(store, repository, context, invoice.id)
        if any(item.kind == (MessageKind.REMINDER if reminder else MessageKind.INVOICE) and item.status == MessageStatus.WAITING_APPROVAL for item in messages):
            raise http_error(409, "An email for this invoice is already waiting for approval. Approve or reject it in Approvals first.")
        number = live_reminders(messages) + 1 if reminder else None
        if number is not None and number > MAX_REMINDERS:
            raise http_error(409, f"This invoice already had {MAX_REMINDERS} reminders. Call the customer instead.")
        address = (to or invoice.bill_to_email or "").strip()
        if not is_email(address):
            raise http_error(422, "Enter the email address to send to, or add one to the invoice's buyer.")
        seller = invoice.seller or store.get_settings().billing
        link = open_link_url(store, invoice) if invoice.status == InvoiceStatus.SENT else None
    subject, text = email_text(invoice, language=language, reminder=number, seller_name=seller.legal_name or "ANUM", link=link)
    return invoice, address, number, subject, text


def _policy_asks(repository: AnumRepository, context: TenantContext, call: ToolCall) -> None:
    """Never an unapproved send: the workspace's policy must ask an owner for this exact call."""
    from .. import delivery
    from ..model_gateway import MockModelGateway
    from ..runtime import AgentRuntime

    decision = AgentRuntime(MockModelGateway(), repository, tools=delivery.registry()).evaluate(call, context)
    if decision.outcome == ToolPolicyOutcome.BLOCK:
        raise http_error(409, f"The email was not planned: {decision.reason}")
    if decision.outcome != ToolPolicyOutcome.REQUIRE_APPROVAL:
        raise http_error(409, "This workspace lets the email tool send without asking. Invoice emails always need an owner's approval, "
                              "so nothing was sent. Turn approval back on for email sending in Settings > Approvals.")


def _remember(context: TenantContext, invoice: Invoice, message: InvoiceMessage, reminder: bool) -> None:
    with open_business_store(context) as store:
        store.add("invoice_messages", message)
        record_activity(store, context, "invoice_reminder_requested" if reminder else "invoice_email_requested",
                        f"{'Payment reminder ' + str(message.reminder_number) if reminder else 'Invoice email'} for {invoice.number} asked for; it waits for approval.",
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "message_id": message.id})


async def request_email(context: TenantContext, units: Any, invoice_id: str, *, to: str | None, language: Literal["en", "ar"],
                        reminder: bool) -> InvoiceMessage:
    """Plan the invoice email (or the next reminder) as a delivery that waits for an approval.

    ``units`` opens short units of work (``dependencies.units_of_work``): every store step runs in
    its own unit in a worker thread, and no transaction is open while the delivery is planned."""
    from .. import delivery
    from ..dependencies import run_in_unit

    invoice, address, number, subject, text = await run_in_unit(units, context, lambda repository: _prepare_email(
        context, repository, invoice_id, to, language, reminder))
    tools = delivery.registry()
    tool_name = delivery.channels(tools).get("email")
    if tools is None or tool_name is None:
        raise http_error(409, "No email integration that can send attachments is connected, so nothing can be sent. "
                              "Download the PDF and send it yourself, or connect one in Integrations.")
    try:
        reference = await asyncio.to_thread(delivery.attachment_reference, ATTACHMENT_KIND, invoice.id, "pdf", context)
    except LookupError:
        raise http_error(404, "Invoice not found") from None
    probe = ToolCall(name=tool_name, arguments={"to": address, "subject": subject, "text": text, "attachments": [reference]})
    await run_in_unit(units, context, _policy_asks, context, probe)
    title = f"Send payment reminder {number} for invoice {invoice.number}" if reminder else f"Email invoice {invoice.number}"
    try:
        outcome = await delivery.request_delivery(context, units, channel="email", to=address, subject=subject, text=text,
                                                  attachments=[reference], title=title)
    except delivery.DeliveryUnavailableError as exc:
        raise http_error(409, str(exc)) from None
    status = {"waiting_approval": MessageStatus.WAITING_APPROVAL, "sent": MessageStatus.SENT, "failed": MessageStatus.FAILED}.get(outcome.status, MessageStatus.BLOCKED)
    if status == MessageStatus.WAITING_APPROVAL and not outcome.approval_id:
        status = MessageStatus.BLOCKED
    now = utc_now()
    message = InvoiceMessage(
        id=new_id("binmsg"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, invoice_id=invoice.id,
        kind=MessageKind.REMINDER if reminder else MessageKind.INVOICE, reminder_number=number, language=language, to_email=address,
        subject=subject, status=status, approval_id=outcome.approval_id, task_id=outcome.task_id, payload_hash=outcome.payload_hash,
        detail=(outcome.summary or "")[:500], requested_by=context.user_id, created_at=now, updated_at=now,
    )
    await asyncio.to_thread(_remember, context, invoice, message, reminder)
    return message


# -- payment links (Moyasar or Stripe) -------------------------------------------------------------


def _issuer(tenant_id: str) -> dict[str, Any]:
    from ..billing import service as billing
    from ..billing.rows import open_billing

    with open_billing(tenant_id) as rows:
        return billing.issuer_settings(rows, tenant_id)


def _active(issuer: dict[str, Any]) -> tuple[bool, bool, str | None]:
    """(Moyasar ready, Stripe ready, the provider links use): Moyasar first, as for ANUM's own invoices."""
    from ..billing.api import stripe_ready

    moyasar = bool(issuer.get("moyasar_enabled") and issuer.get("moyasar_secret_ciphertext") and issuer.get("moyasar_webhook_secret_ciphertext"))
    stripe = stripe_ready(issuer)
    return moyasar, stripe, "moyasar" if moyasar else ("stripe" if stripe else None)


def provider_state(tenant_id: str) -> dict[str, Any]:
    """Which card provider this organisation's owner has on (Platform billing > Settings)."""
    issuer = _issuer(tenant_id)
    moyasar, stripe, active = _active(issuer)
    mode = issuer.get(f"{active}_key_mode") if active else None
    return {"moyasar": "on" if moyasar else "off", "stripe": "on" if stripe else "off", "provider": active, "mode": mode}


def links_of(store: BusinessStore, invoice_id: str) -> list[PaymentLink]:
    items, _ = store.find("payment_links", Query(where={"invoice_id": invoice_id}, order_by="created_at", descending=True, limit=20))
    return items


def open_link(store: BusinessStore, invoice: Invoice) -> PaymentLink | None:
    """The open link for exactly the current balance of a sent invoice."""
    if invoice.status != InvoiceStatus.SENT:
        return None
    balance = invoice.balance_due()
    for item in links_of(store, invoice.id):
        if item.status == PaymentLinkStatus.OPEN and item.amount == balance and item.currency == invoice.currency:
            return item
    return None


def open_link_url(store: BusinessStore, invoice: Invoice) -> str | None:
    found = open_link(store, invoice)
    return found.url if found else None


def to_minor(amount: Decimal) -> int:
    return int((money(amount) * 100).to_integral_value())


def create_payment_link(context: TenantContext, invoice_id: str, *, app_url: str) -> PaymentLink:
    """A hosted page for the invoice's balance (Moyasar, else Stripe); the same link again while the balance is unchanged."""
    import hashlib

    from ..billing import moyasar as moy
    from ..billing.api import stripe_for
    from ..billing.providers import STRIPE_CURRENCIES, ProviderError
    from ..billing.selling import moyasar_for

    with open_business_store(context) as store:
        invoice: Invoice | None = store.get("invoices", invoice_id)
        if invoice is None:
            raise http_error(404, "Invoice not found")
        if invoice.status != InvoiceStatus.SENT or invoice.balance_due() <= 0:
            raise http_error(409, "A payment link is made for a sent invoice with a balance.")
        existing = open_link(store, invoice)
        if existing is not None:
            return existing
        earlier = len(links_of(store, invoice_id))
    issuer = _issuer(context.tenant_id)
    _, _, active = _active(issuer)
    if active is None:
        raise http_error(409, "Card payments are off. The owner turns Stripe or Moyasar on in Platform billing > Settings.")
    currencies = moy.CURRENCIES if active == "moyasar" else STRIPE_CURRENCIES
    if invoice.currency.value not in currencies:
        raise http_error(409, f"{PROVIDER_NAMES[active]} card payments are set up for {', '.join(currencies)}; this invoice is in {invoice.currency.value}.")
    balance = invoice.balance_due()
    back = f"{app_url.rstrip('/')}/business?tab=invoices"
    metadata = {"anum_kind": "business_invoice", "anum_tenant_id": context.tenant_id, "anum_workspace_id": context.workspace_id,
                "anum_business_invoice_id": invoice.id}
    try:
        if active == "moyasar":
            moyasar = moyasar_for(issuer)
            if moyasar is None:
                raise http_error(409, "Card payments are off. The owner turns Moyasar on in Platform billing > Settings > Moyasar.")
            page = moyasar.create_payment_page(
                amount_minor=to_minor(balance), currency=invoice.currency.value, description=f"Invoice {invoice.number}",
                success_url=back, back_url=back, metadata=metadata,
            )
        else:
            stripe = stripe_for(issuer)
            if stripe is None:
                raise http_error(409, "Card payments are off. The owner turns Stripe on in Platform billing > Settings.")
            # The same invoice, balance and link count always maps to the same Stripe objects, so a retry makes no second link.
            key = hashlib.sha256(f"binlink:{context.tenant_id}:{invoice.id}:{to_minor(balance)}:{earlier}".encode()).hexdigest()
            page = stripe.create_payment_link(
                amount_minor=to_minor(balance), currency=invoice.currency.value, description=f"Invoice {invoice.number}",
                redirect_url=back, metadata=metadata, idempotency_key=key,
            )
    except ProviderError as exc:
        raise http_error(502, str(exc)) from None
    now = utc_now()
    replaced: list[PaymentLink] = []
    with open_business_store(context) as store:
        current: Invoice | None = store.get("invoices", invoice_id, for_update=True)
        if current is None or current.status != InvoiceStatus.SENT or current.balance_due() != balance:
            raise http_error(409, "The invoice changed while the payment page was made. Make the link again.")
        for item in links_of(store, invoice_id):
            if item.status == PaymentLinkStatus.OPEN:
                store.save("payment_links", item.model_copy(update={"status": PaymentLinkStatus.REPLACED, "updated_at": now}))
                replaced.append(item)
        link = PaymentLink(
            id=new_id("binlink"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, invoice_id=invoice_id, provider=active,
            provider_invoice_id=page.session_id[:200], url=page.url[:500], amount=balance, currency=current.currency, created_by=context.user_id,
            created_at=now, updated_at=now,
        )
        store.add("payment_links", link)
        record_activity(store, context, "invoice_payment_link_created", f"Payment link made for invoice {current.number}.",
                        account_id=current.account_id, project_id=current.project_id, metadata={"invoice_id": invoice_id, "link_id": link.id})
    _turn_off_stripe_links(issuer, replaced)
    return link


def _turn_off_stripe_links(issuer: dict[str, Any], links: list[PaymentLink]) -> None:
    """Best effort, with no transaction open: a replaced Stripe link stops taking payments.

    A Moyasar page and a Stripe link that could not be turned off stay payable for their old
    amount; a payment on one is still recorded once, and any surplus is flagged for a refund.
    """
    from ..billing.api import stripe_for
    from ..billing.providers import ProviderError

    stale = [item for item in links if item.provider == "stripe"]
    stripe = stripe_for(issuer) if stale else None
    for item in stale:
        if stripe is None:
            break
        try:
            stripe.deactivate_payment_link(item.provider_invoice_id)
        except ProviderError:
            logger.warning("business_stripe_link_not_deactivated", extra={"anum_business": {"event": "stripe_link_not_deactivated"}})


def apply_moyasar_payment(issuer_tenant_id: str, metadata: dict[str, str], payment_id: str, status: str, amount_minor: int, currency: str,
                          now: datetime | None = None) -> str:
    """Record a Moyasar payment on a Business invoice (values read back from Moyasar's API)."""
    return apply_card_payment("moyasar", issuer_tenant_id, metadata, payment_id, status, amount_minor, currency, now)


def apply_card_payment(provider: Literal["moyasar", "stripe"], issuer_tenant_id: str, metadata: dict[str, str], payment_id: str, status: str,
                       amount_minor: int, currency: str, now: datetime | None = None) -> str:
    """Record a card payment on a Business invoice, once per provider payment id.

    For Moyasar, ``payment_id``, ``status``, ``amount_minor`` and ``currency`` come from Moyasar's
    API (the billing webhook reads the payment back), never from the delivered body; for Stripe they
    come from the signed ``checkout.session.completed`` event (amount in minor units, fils for AED).
    Only the issuer's own Business invoices are paid through its provider account.
    """
    now = now or utc_now()
    name = PROVIDER_NAMES[provider]
    tenant_id = metadata.get("anum_tenant_id", "")
    workspace_id = metadata.get("anum_workspace_id", "")
    invoice_id = metadata.get("anum_business_invoice_id", "")
    if tenant_id != issuer_tenant_id or not ID.match(workspace_id) or not ID.match(invoice_id):
        logger.warning("business_card_foreign_invoice", extra={"anum_business": {"event": f"{provider}_foreign_invoice"}})
        return "ignored"
    if status != "paid" or amount_minor <= 0:
        return "ignored"
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=PROVIDER_USERS[provider], roles=["member"])
    with open_business_store(context) as store:
        invoice: Invoice | None = store.get("invoices", invoice_id, for_update=True)
        if invoice is None:
            return "ignored"
        if store.first("invoice_payments", provider=provider, provider_payment_id=payment_id[:200]) is not None:
            return "duplicate_payment"
        if currency.upper() != invoice.currency.value:
            logger.warning("business_card_currency_mismatch", extra={"anum_business": {"event": f"{provider}_currency_mismatch"}})
            return "ignored"
        amount = money(Decimal(amount_minor) / Decimal(100))
        if invoice.status != InvoiceStatus.SENT or invoice.balance_due() <= 0:
            record_activity(store, context, "invoice_payment_needs_review",
                            f"{name} reported a card payment of {invoice.currency.value} {amount} on invoice {invoice.number}, which is "
                            f"{invoice.status.value}. Check it in the {name} dashboard and refund it there if it is not owed.",
                            account_id=invoice.account_id, metadata={"invoice_id": invoice.id, f"{provider}_payment": payment_id[:200]},
                            idempotency_key=f"{provider}-review:{payment_id[:100]}")
            return "needs_review"
        applied = min(amount, invoice.balance_due())
        reference = f"{name} {payment_id}"[:120]
        note = f"Paid by card on the {name} payment page." + (f" {name} took {amount}; {amount - applied} more than the balance, refund it there." if applied < amount else "")
        invoice, payment = apply_payment(store, context, invoice, amount=applied, paid_on=business_day(store, now), method=PaymentMethod.CARD,
                                         reference=reference, note=note[:300], now=now, provider=provider, provider_payment_id=payment_id[:200])
        for link in links_of(store, invoice.id):
            if link.status == PaymentLinkStatus.OPEN:
                store.save("payment_links", link.model_copy(update={"status": PaymentLinkStatus.PAID if invoice.status == InvoiceStatus.PAID else PaymentLinkStatus.REPLACED,
                                                                   "updated_at": now}))
        record_activity(store, context, "invoice_payment_recorded", f"Card payment of {invoice.currency.value} {applied} received through {name} on invoice {invoice.number}.",
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "payment_id": payment.id})
    return "paid"


def payment_by_key(store: BusinessStore, invoice_id: str, key: str) -> InvoicePayment | None:
    return store.first("invoice_payments", invoice_id=invoice_id, idempotency_key=key)
