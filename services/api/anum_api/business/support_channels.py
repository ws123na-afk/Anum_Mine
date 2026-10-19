"""Links between support tickets and the existing inboxes (docs/business.md#support-tickets).

A ticket opened from an inbound email or WhatsApp conversation stores only the message or
conversation id (a link, never a copy of the text); the inbox stays the record of what was
said and keeps its own permissions. A reply drafted on a ticket is delivered through the
inbox's own approval path: the email inbox's ``send_email`` (an ``EmailDraft`` with exactly
the reply's text, paused for an owner's approval bound to the payload hash) or WhatsApp's
send (free-form text always waits for approval and needs the 24-hour window). Nothing here
sends on its own.

These adapters are small on purpose: tests replace them, and a new channel adds one pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from ..authorization import Permission
from ..dependencies import has_permission
from ..schemas import TenantContext
from .service import http_error


@dataclass(frozen=True)
class SourceInfo:
    """Who wrote in and when, read from the inbox in the caller's scope (nothing is copied but these)."""

    ref: str
    account_id: str | None
    requester_name: str
    requester_email: str | None
    requester_phone: str | None
    title: str
    reported_at: datetime


@dataclass(frozen=True)
class DeliveryRequest:
    status: str  # pending_approval | sent | failed
    external_ref: str | None
    approval_id: str | None
    task_id: str | None
    payload_hash: str | None
    detail: str
    approval: Any = None


def load_email_source(context: TenantContext, message_id: str) -> SourceInfo:
    from .. import email_inbox

    if not has_permission(context, Permission.EMAIL_READ):
        raise http_error(403, "You cannot read the email inbox, so you cannot open a ticket from an email. Ask an owner.")
    try:
        account, message = email_inbox.load_message(context, message_id)
    except email_inbox.EmailError as exc:
        raise http_error(exc.status, exc.message) from None
    if account.visibility == email_inbox.Visibility.PRIVATE:
        raise http_error(409, "This email is in a private mailbox. Tickets link messages from workspace mailboxes only, so others on the ticket can open it.")
    return SourceInfo(
        ref=message.id, account_id=account.id, requester_name=(message.from_name or "").strip()[:160],
        requester_email=(message.from_address or "").strip().lower()[:254] or None, requester_phone=None,
        title=(message.subject or "").strip()[:200], reported_at=message.received_at,
    )


def load_whatsapp_source(context: TenantContext, conversation_id: str) -> SourceInfo:
    from ..whatsapp_store import open_whatsapp_store

    if not has_permission(context, Permission.WHATSAPP_READ):
        raise http_error(403, "You cannot read WhatsApp, so you cannot open a ticket from a conversation. Ask an owner.")
    with open_whatsapp_store(context) as store:
        conversation = store.get_conversation(conversation_id)
    if conversation is None:
        raise http_error(404, "Conversation not found.")
    name = (conversation.contact_name or "").strip()
    return SourceInfo(
        ref=conversation.id, account_id=None, requester_name=name[:160], requester_email=None,
        requester_phone=f"+{conversation.wa_id}"[:40], title=f"WhatsApp from {name or '+' + conversation.wa_id}"[:200],
        reported_at=conversation.last_inbound_at or conversation.last_message_at,
    )


async def request_email_reply(context: TenantContext, repository: Any, message_id: str, body: str) -> DeliveryRequest:
    """An email inbox reply draft with exactly ``body``, then its ``send_email`` approval request."""
    from .. import email_api, email_inbox

    if not has_permission(context, Permission.EMAIL_ACT):
        raise http_error(403, "You cannot reply from the email inbox. Ask an owner to reply or to give you inbox access.")
    try:
        draft, _warning = await email_inbox.create_draft(context, message_id=message_id, body=body, instructions=None)
    except email_inbox.EmailError as exc:
        raise http_error(exc.status, exc.message) from None
    try:
        response = await email_api.request_send_approval(draft.id, context, repository)
    except HTTPException as exc:
        return DeliveryRequest("failed", draft.id, None, None, None, str(exc.detail))
    approval = response.approval
    return DeliveryRequest("pending_approval", draft.id, approval.id, approval.task_id, approval.payload_hash,
                           "Waiting for approval: an owner approves it in Approvals, then the inbox sends it.", approval)


async def request_whatsapp_reply(context: TenantContext, repository: Any, conversation_id: str, body: str) -> DeliveryRequest:
    """A WhatsApp reply through the WhatsApp send path (free text always waits for approval)."""
    from .. import whatsapp

    response = await whatsapp.send(conversation_id, whatsapp.SendRequest(text=body), context, repository)
    approval = response.approval
    status = {"awaiting_approval": "pending_approval", "sent": "sent"}.get(response.outcome, "failed")
    return DeliveryRequest(status, response.message.id, approval.id if approval else None, response.message.task_id,
                           approval.payload_hash if approval else None, response.detail, approval)


def email_reply_status(context: TenantContext, draft_id: str) -> tuple[str, str]:
    """(pending_approval | sent | failed | discarded, error) of the linked email draft."""
    from .. import email_inbox

    with email_inbox.open_email_store(context) as store:
        draft = store.get_draft(draft_id)
    if draft is None:
        return "failed", "The email draft no longer exists."
    mapping = {"sent": "sent", "failed": "failed", "discarded": "discarded"}
    return mapping.get(draft.status.value, "pending_approval"), draft.error or ""


def whatsapp_reply_status(context: TenantContext, message_id: str) -> tuple[str, str]:
    from ..whatsapp_store import open_whatsapp_store

    with open_whatsapp_store(context) as store:
        message = store.get_message(message_id)
    if message is None:
        return "failed", "The WhatsApp message no longer exists."
    value = message.status.value
    if value in {"sent", "delivered", "read"}:
        return "sent", ""
    if value in {"failed", "not_sent"}:
        return "failed", message.error or ""
    return "pending_approval", ""
