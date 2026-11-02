"""One Email module over both mail back ends (docs/email.md#one-email-module).

ANUM has two mail back ends and keeps both: the OAuth provider inbox (Gmail, Outlook, Zoho;
``email_api.py``, ``/api/v1/email``) and the Email Center on the workspace's own mail server
(``mailbox/``, ``/api/v1/mail``). This module is the thin read layer the one Email page sits on:

* ``GET /api/v1/email/unified/mailboxes``: every mailbox the caller may read, from both back ends,
  each with its source (``gmail``, ``outlook``, ``zoho`` or ``mailserver``), state, unread count
  and whether the caller may send from it; plus why a back end shows nothing (off, not set up).
* ``GET /api/v1/email/unified/messages``: "All inboxes" over both back ends, newest first. One page
  holds the next page of each back end merged by date; the cursor keeps each back end's own cursor,
  so no message is shown twice or skipped. A back end that cannot be read is reported, never fatal.

Nothing is copied: provider messages come from the synced metadata, own-server messages are read
live from the inboxes (``mailbox/all_inboxes.py``). Opening, replying, AI and sending stay with the
back end that owns the message (its own routes, permissions, tool policy and approvals), so the
approval rules for sending are unchanged and nothing is sent without an owner's approval.

Permissions: ``email:read`` (owners and members), as on both back ends. The provider half runs in
a worker thread (``asyncio.to_thread``), never on the event loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .email_inbox import MessageFilters, open_email_store, visible_accounts
from .schemas import TenantContext

router = APIRouter(prefix="/api/v1/email/unified", tags=["email"])

READ_DENIED = "Reading email needs the owner or member role in this workspace."
MAX_LIMIT = 50
MAX_CURSOR = 9_000
DONE = "~"

Source = Literal["gmail", "outlook", "zoho", "mailserver"]
Backend = Literal["provider", "server"]
BackendState = Literal["ok", "none", "off", "not_configured", "not_allowed", "error"]


def _require_read(context: TenantContext) -> None:
    try:
        require_permission(context, Permission.EMAIL_READ)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=READ_DENIED) from None


class UnifiedMailbox(BaseModel):
    # "provider:<email_accounts.id>" or "server:<mail_accounts.id>": the key the page routes on.
    ref: str
    id: str
    backend: Backend
    source: Source
    address: str
    display_name: str | None
    # provider: connected, error, reauth_required; server: connected, auth_failed, error.
    status: str
    # Provider mailboxes: private (only the person who connected it) or shared with the workspace.
    # Server mailboxes: owner, or shared with the caller to read or send.
    access: str
    unread: int | None
    can_send: bool


class BackendInfo(BaseModel):
    state: BackendState
    detail: str | None


class UnifiedMailboxes(BaseModel):
    items: list[UnifiedMailbox]
    provider: BackendInfo
    server: BackendInfo
    can_act: bool
    can_manage: bool


def _provider_mailboxes(context: TenantContext, can_act: bool) -> tuple[list[UnifiedMailbox], BackendInfo]:
    try:
        with open_email_store(context) as store:
            accounts = visible_accounts(store, context)
            counts = store.counts([a.id for a in accounts])
    except Exception:  # one back end failing never hides the other
        return [], BackendInfo(state="error", detail="The connected inboxes could not be read. Try again.")
    items = [
        UnifiedMailbox(
            ref=f"provider:{a.id}", id=a.id, backend="provider", source=a.provider.value, address=a.address, display_name=a.display_name,
            status=a.status.value, access=a.visibility.value, unread=counts.get(a.id, (0, 0))[1],
            can_send=can_act and a.status.value == "connected",
        )
        for a in accounts
    ]
    return items, BackendInfo(state="ok" if items else "none", detail=None)


def _server_state(context: TenantContext) -> BackendInfo:
    from .mailbox.service import MailError, require_available

    try:
        require_available(context)
    except MailError as exc:
        state: BackendState = "off" if exc.kind == "disabled" else "not_configured" if exc.kind == "not_configured" else "not_allowed"
        return BackendInfo(state=state, detail=exc.message)
    return BackendInfo(state="ok", detail=None)


def _server_mailboxes(context: TenantContext, can_act: bool) -> tuple[list[UnifiedMailbox], BackendInfo]:
    info = _server_state(context)
    if info.state != "ok":
        return [], info
    from .mailbox.api import accounts_for
    from .mailbox.automation import open_automation_store

    try:
        accounts = accounts_for(context)
        with open_automation_store(context) as store:
            states = store.sync_for([a.id for a in accounts])
    except Exception:
        return [], BackendInfo(state="error", detail="The mailboxes on the mail server could not be read. Try again.")
    items = []
    for account in accounts:
        state = states.get(account.id)
        items.append(UnifiedMailbox(
            ref=f"server:{account.id}", id=account.id, backend="server", source="mailserver", address=account.address,
            display_name=account.display_name, status=account.status.value if hasattr(account.status, "value") else str(account.status),
            access=account.access, unread=state.inbox_unread if state else None,
            can_send=can_act and account.access in {"owner", "send"},
        ))
    return items, BackendInfo(state="ok" if items else "none", detail=None)


@router.get("/mailboxes", response_model=UnifiedMailboxes)
def list_mailboxes(context: TenantContext = Depends(tenant_context)) -> UnifiedMailboxes:
    """Every mailbox the caller may read, provider mailboxes first, then the own server's."""
    _require_read(context)
    can_act = has_permission(context, Permission.EMAIL_ACT)
    provider, provider_info = _provider_mailboxes(context, can_act)
    server, server_info = _server_mailboxes(context, can_act)
    return UnifiedMailboxes(
        items=provider + server, provider=provider_info, server=server_info, can_act=can_act,
        can_manage=has_permission(context, Permission.EMAIL_MANAGE),
    )


# -- All inboxes ----------------------------------------------------------------------------------


class UnifiedMessage(BaseModel):
    # "provider:<message id>" or "server:<account id>:<folder>:<uid>".
    ref: str
    backend: Backend
    source: Source
    mailbox_id: str
    mailbox_address: str
    # Provider messages: the message id. Server messages: folder and uid in that mailbox.
    message_id: str | None
    folder: str | None
    uid: int | None
    from_name: str | None
    from_address: str | None
    subject: str
    snippet: str
    received_at: str
    unread: bool
    flagged: bool
    has_attachments: bool
    # Server mailboxes shared with the caller to read only: no reply, star or flag.
    read_only: bool
    # Own-server messages: the Email Center's own summary, so its reader opens the message as is.
    server_message: dict[str, Any] | None = None


class UnreadableMailbox(BaseModel):
    ref: str
    address: str
    state: str
    reason: str | None


class UnifiedPage(BaseModel):
    items: list[UnifiedMessage]
    next_cursor: str | None
    provider: BackendInfo
    server: BackendInfo
    # Mailboxes of the own server that could not be read for this page (offline, refused, slow).
    unreadable: list[UnreadableMailbox]


def encode_cursor(provider: str | None, server: str | None) -> str:
    raw = json.dumps({"v": 1, "p": provider, "s": server}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str | None, str | None]:
    bad = HTTPException(status_code=422, detail="This page link is no longer valid. Load the list again.")
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError):
        raise bad from None
    if not isinstance(data, dict) or data.get("v") != 1:
        raise bad
    provider, server = data.get("p"), data.get("s")
    if not all(value is None or (isinstance(value, str) and len(value) <= MAX_CURSOR) for value in (provider, server)):
        raise bad
    return provider, server


def _provider_page(context: TenantContext, before: str | None, limit: int, q: str | None, unread: bool) -> tuple[list[UnifiedMessage], str | None]:
    filters = MessageFilters(unread=True if unread else None, archived=False, query=q)
    with open_email_store(context) as store:
        accounts = {a.id: a for a in visible_accounts(store, context)}
        if not accounts:
            return [], None
        cursor = store.get_message(before) if before else None
        if before and (cursor is None or cursor.account_id not in accounts):
            # A cursor that names a message the caller can no longer see starts over safely.
            raise HTTPException(status_code=422, detail="This page link is no longer valid. Load the list again.")
        items = store.list_messages(list(accounts), filters, limit=limit, before=cursor)
    found = [
        UnifiedMessage(
            ref=f"provider:{m.id}", backend="provider", source=accounts[m.account_id].provider.value, mailbox_id=m.account_id,
            mailbox_address=accounts[m.account_id].address, message_id=m.id, folder=None, uid=None, from_name=m.from_name,
            from_address=m.from_address, subject=m.subject, snippet=m.snippet, received_at=m.received_at.isoformat() if isinstance(m.received_at, datetime) else str(m.received_at),
            unread=not m.is_read, flagged=False, has_attachments=m.has_attachments, read_only=False,
        )
        for m in items
    ]
    return found, (items[-1].id if len(items) == limit else None)


def _when(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@router.get("/messages", response_model=UnifiedPage)
async def list_all_messages(
    cursor: str | None = Query(default=None, max_length=MAX_CURSOR * 2),
    limit: int = Query(default=20, ge=1, le=MAX_LIMIT),
    q: str | None = Query(default=None, max_length=200),
    unread: bool = False,
    context: TenantContext = Depends(tenant_context),
) -> UnifiedPage:
    """All inboxes over both back ends: each page is the next ``limit`` of each, merged newest first."""
    _require_read(context)
    query = re.sub(r"[\x00-\x1f\x7f]", " ", q or "").strip() or None
    provider_cursor, server_cursor = decode_cursor(cursor) if cursor else (None, None)

    items: list[UnifiedMessage] = []
    provider_info = BackendInfo(state="ok", detail=None)
    next_provider: str | None = DONE
    if provider_cursor != DONE:
        try:
            page, next_provider = await asyncio.to_thread(_provider_page, context, provider_cursor, limit, query, unread)
            items.extend(page)
            next_provider = next_provider or DONE
            if not page and provider_cursor is None:
                provider_info = BackendInfo(state="none", detail=None)
        except HTTPException:
            raise
        except Exception:
            # This pass leaves the provider inboxes out (no repeats on the next page); Try again reloads.
            provider_info = BackendInfo(state="error", detail="The connected inboxes could not be read. Try again.")
            next_provider = DONE

    server_info = await asyncio.to_thread(_server_state, context)
    unreadable: list[UnreadableMailbox] = []
    next_server: str | None = DONE
    if server_info.state == "ok" and server_cursor != DONE:
        from .mailbox.all_inboxes import list_all_inboxes

        try:
            page = await list_all_inboxes(
                account_id=[], cursor=server_cursor, limit=limit, q=query, unread=unread, flagged=False, since=None, until=None, context=context,
            )
        except HTTPException as exc:
            if exc.status_code == 422:
                raise
            server_info = BackendInfo(state="error", detail=str(exc.detail))
            next_server = DONE
        else:
            if not page.mailboxes:
                server_info = BackendInfo(state="none", detail=None)
            next_server = page.next_cursor or DONE
            unreadable = [
                UnreadableMailbox(ref=f"server:{m.account_id}", address=m.address, state=m.state, reason=m.error)
                for m in page.mailboxes if m.state not in {"ok", "done"}
            ]
            for m in page.items:
                sender = m.sender
                items.append(UnifiedMessage(
                    ref=f"server:{m.account_id}:{m.folder}:{m.uid}", backend="server", source="mailserver", mailbox_id=m.account_id,
                    mailbox_address=m.account_address, message_id=None, folder=m.folder, uid=m.uid,
                    from_name=sender.name if sender else None, from_address=sender.address if sender else None, subject=m.subject,
                    snippet="", received_at=m.date, unread=not m.seen, flagged=m.flagged, has_attachments=m.maybe_attachments,
                    read_only=m.access == "read", server_message=m.model_dump(mode="json"),
                ))
    elif server_info.state == "ok":
        next_server = DONE

    items.sort(key=lambda item: _when(item.received_at), reverse=True)
    more = next_provider != DONE or next_server != DONE
    return UnifiedPage(
        items=items, next_cursor=encode_cursor(next_provider, next_server) if more else None,
        provider=provider_info, server=server_info, unreadable=unreadable,
    )
