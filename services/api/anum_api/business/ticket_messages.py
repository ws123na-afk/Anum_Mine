"""The conversation inside a support request (docs/business-portal.md#replying-inside-a-request).

A customer writes from their portal link (``portal.py``); the team answers in the ticket (``api_ticket_messages.py``).
Every message is a row in ``business_ticket_messages`` under forced RLS. Nothing is delivered anywhere: the customer
reads the team's answers when they open the portal, so a message to the portal is not an external send and needs no
approval. Replies by email or WhatsApp keep their own approval path (``api_support``).

* A customer message on a request that waits on the customer moves it back to in progress (the SLA pause ends); on a
  resolved request it reopens it. A closed request takes no more messages: the customer opens a new one.
* The team answers only while the customer has an active portal link for the request's account (otherwise nobody
  would read it); the first answer counts as the first response for the SLA.
* Text is cleaned (long dashes replaced, control characters dropped) and shown as plain text only. A customer submit
  carries a key the form made up, so a retry is the same message.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

from ..punctuation import plain_dashes
from ..schemas import TenantContext, new_id
from . import support
from .invoice_models import PortalAccessStatus
from .models import SupportTicket, TicketMessage, TicketStatus
from .service import http_error, record_activity
from .store import BusinessStore, Query

MESSAGES_PER_DAY = 20  # per portal link, so a leaked link cannot flood the desk
MAX_THREAD = 200  # messages shown on one request (newest kept)
CLOSED = "This request is closed. Open a new request if you need more help."


def clean_body(text: str) -> str:
    """Plain text: long dashes replaced, control and format characters dropped (line breaks kept), at most 4,000 characters."""
    kept = "".join(ch for ch in plain_dashes(text or "") if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    lines = [line.rstrip() for line in kept.replace("\t", " ").splitlines()]
    return "\n".join(lines).strip()[:4000]


def thread(store: BusinessStore, ticket_id: str) -> list[TicketMessage]:
    items, _ = store.find("ticket_messages", Query(where={"ticket_id": ticket_id}, order_by="created_at", descending=True, limit=MAX_THREAD))
    return list(reversed(items))


def unread(store: BusinessStore, ticket_id: str) -> int:
    return store.count("ticket_messages", Query(where={"ticket_id": ticket_id, "author": "customer", "read_at": None}))


def portal_link_active(store: BusinessStore, account_id: str | None, now: datetime) -> bool:
    if not account_id:
        return False
    links, _ = store.find("portal_access", Query(where={"account_id": account_id, "status": PortalAccessStatus.ACTIVE}, limit=20))
    return any(item.expires_at > now for item in links)


def customer_message(store: BusinessStore, context: TenantContext, ticket: SupportTicket, *, access_id: str, contact_id: str, contact_name: str,
                     body: str, client_key: str, now: datetime) -> tuple[TicketMessage, bool]:
    """Add a customer's message (the same key again is the same message). Returns the message and whether it is new."""
    repeat = store.first("ticket_messages", ticket_id=ticket.id, client_key=client_key)
    if repeat is not None:
        return repeat, False
    if ticket.status == TicketStatus.CLOSED:
        raise http_error(409, CLOSED)
    text = clean_body(body)
    if not text:
        raise http_error(422, "Write your message first.")
    sent_today = store.count("ticket_messages", Query(where={"access_id": access_id}, created_from=now - timedelta(days=1)))
    if sent_today >= MESSAGES_PER_DAY:
        raise http_error(429, f"You can send {MESSAGES_PER_DAY} messages a day from this link. Call the business if it is urgent.")
    message = TicketMessage(
        id=new_id("btmsg"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, ticket_id=ticket.id, account_id=ticket.account_id or "",
        author="customer", author_name=contact_name[:160], access_id=access_id, contact_id=contact_id, body=text, client_key=client_key,
        created_by=context.user_id, created_at=now, updated_at=now,
    )
    store.add("ticket_messages", message)
    policy = store.get_settings().support_policy
    before = ticket.status
    if before in {TicketStatus.WAITING_ON_CUSTOMER, TicketStatus.RESOLVED}:
        support.move(ticket, TicketStatus.IN_PROGRESS, now, context.user_id, policy)
    else:
        ticket.updated_at = now
    store.save("tickets", ticket)
    reopened = before == TicketStatus.RESOLVED
    record_activity(store, context, "ticket_reopened" if reopened else "portal_ticket_message",
                    f"{contact_name} wrote in the customer portal" + (" and reopened the request." if reopened else "."),
                    ticket_id=ticket.id, account_id=ticket.account_id, metadata={"message_id": message.id, "access_id": access_id, "from": before.value})
    return message, True


def staff_message(store: BusinessStore, context: TenantContext, ticket: SupportTicket, *, body: str, now: datetime) -> TicketMessage:
    """The team's answer, shown to the customer in the portal (nothing is sent anywhere)."""
    if ticket.status == TicketStatus.CLOSED:
        raise http_error(409, "This ticket is closed. Reopen it to answer in the portal.")
    if not ticket.account_id:
        raise http_error(409, "This ticket has no client account, so there is no portal to answer in. Reply by email or WhatsApp instead.")
    if not portal_link_active(store, ticket.account_id, now):
        raise http_error(409, "This customer has no active portal link, so nobody would read the answer. Create a link under Accounts, "
                              "or reply by email or WhatsApp.")
    text = clean_body(body)
    if not text:
        raise http_error(422, "Write the answer first.")
    message = TicketMessage(
        id=new_id("btmsg"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, ticket_id=ticket.id, account_id=ticket.account_id,
        author="staff", author_name="", body=text, created_by=context.user_id, created_at=now, updated_at=now,
    )
    store.add("ticket_messages", message)
    ticket.first_responded_at = ticket.first_responded_at or now
    ticket.updated_at = now
    store.save("tickets", ticket)
    record_activity(store, context, "portal_ticket_answer", "Answered in the customer portal.", ticket_id=ticket.id, account_id=ticket.account_id,
                    metadata={"message_id": message.id})
    return message


def mark_read(store: BusinessStore, context: TenantContext, ticket_id: str, now: datetime) -> int:
    items, _ = store.find("ticket_messages", Query(where={"ticket_id": ticket_id, "author": "customer", "read_at": None}, limit=MAX_THREAD))
    for item in items:
        store.save("ticket_messages", item.model_copy(update={"read_at": now, "read_by": context.user_id, "updated_at": now}))
    return len(items)


__all__ = ["CLOSED", "MESSAGES_PER_DAY", "clean_body", "customer_message", "mark_read", "portal_link_active", "staff_message", "thread", "unread"]
