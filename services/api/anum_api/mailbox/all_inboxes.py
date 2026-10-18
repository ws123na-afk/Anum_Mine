"""All inboxes (docs/email.md#all-inboxes): one list over the inbox of every mailbox the caller may read.

The mailboxes come from the caller's own scope in one short unit of work (their own mailboxes and
those shared with them, each read through the row policies; never another person's unshared
mailbox, never another organisation). Each inbox is then read live from the mail server, at most
``CONCURRENCY`` at a time, ``limit`` messages each, and the pages are merged newest first. The cursor
remembers, per mailbox, the UID the next page continues below, so every page is a bounded amount
of work (one IMAP SEARCH and one FETCH per mailbox) and nothing is copied into ANUM.

A message opens in its own mailbox (``GET /accounts/{id}/message``), so a reply is a draft of that
mailbox, sent from its address through the usual approval.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import heapq
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..authorization import Permission
from ..dependencies import tenant_context
from ..schemas import TenantContext
from .api import MAX_LIMIT, READ_DENIED, MessageView, _available, _message_view, _require
from .server import MailServer, Page, Summary
from .service import MailError, on_server
from .store import Access, AccountStatus, MailAccount, open_mail_store

router = APIRouter(prefix="/api/v1/mail", tags=["mail"])

INBOX = "INBOX"
CONCURRENCY = 6
# How long one inbox may take before the page goes out without it (it is flagged "slow").
INBOX_TIMEOUT_SECONDS = 8.0
MAX_MAILBOXES = 50
MAX_CURSOR = 8_000
EPOCH = datetime.fromtimestamp(0, timezone.utc)

MailboxState = Literal["ok", "auth_failed", "offline", "error", "slow", "done"]


class AllInboxMessage(MessageView):
    account_id: str
    account_address: str
    account_name: str | None
    access: Access


class AllInboxMailbox(BaseModel):
    account_id: str
    address: str
    display_name: str | None
    access: Access
    # ok: read for this page; done: no more matching mail below the cursor; slow: it did not answer
    # within INBOX_TIMEOUT_SECONDS and was left out of this page; otherwise why it was not read.
    state: MailboxState
    error: str | None
    # Matching messages in this inbox from the cursor on (null when it was not read).
    total: int | None


class AllInboxPage(BaseModel):
    items: list[AllInboxMessage]
    # Matching messages from this page on, over the mailboxes that were read.
    total: int
    next_cursor: str | None
    # True when at least one inbox was left out of this page (slow, offline, refused, error).
    partial: bool
    mailboxes: list[AllInboxMailbox]


@dataclass(frozen=True)
class _Read:
    account: MailAccount
    access: Access
    page: Page | None
    state: MailboxState
    error: str | None


def readable_mailboxes(context: TenantContext) -> list[tuple[MailAccount, Access]]:
    """The caller's own mailboxes, then those shared with them: two queries, no per-mailbox lookups."""
    with open_mail_store(context) as store:
        own: list[tuple[MailAccount, Access]] = [(account, "owner") for account in store.list_accounts()]
        shared: list[tuple[MailAccount, Access]] = [(account, grant.access) for account, grant in store.list_shared_accounts()]
    seen: set[str] = set()
    found: list[tuple[MailAccount, Access]] = []
    for account, access in own + shared:
        if account.id not in seen:
            seen.add(account.id)
            found.append((account, access))
    return found[:MAX_MAILBOXES]


def _fingerprint(account_ids: list[str], q: str | None, unread: bool, flagged: bool, since: date | None, until: date | None) -> str:
    raw = json.dumps([sorted(account_ids), q or "", unread, flagged, since.isoformat() if since else "", until.isoformat() if until else ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def encode_cursor(positions: dict[str, int], fingerprint: str) -> str:
    raw = json.dumps({"v": 1, "f": fingerprint, "m": positions}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, fingerprint: str) -> dict[str, int]:
    """Per mailbox the UID the next page continues below (0: nothing left). 422 for a bad or stale cursor."""
    bad = HTTPException(status_code=422, detail="This page link is no longer valid. Load the list again.")
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError):
        raise bad from None
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("m"), dict):
        raise bad
    if data.get("f") != fingerprint:
        raise HTTPException(status_code=422, detail="The filters changed since this page. Load the list again.")
    positions: dict[str, int] = {}
    for key, value in data["m"].items():
        if not isinstance(key, str) or len(key) > 80 or not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise bad
        positions[key] = value
    return positions


def _when(item: Summary) -> datetime:
    value = item.date or EPOCH
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def merge_pages(reads: list[_Read], positions: dict[str, int], limit: int) -> tuple[list[tuple[_Read, Summary]], dict[str, int], bool]:
    """Newest first over every inbox that was read, each inbox in its own (UID) order.

    Returns the page, the next positions and whether any inbox has more. An inbox that was not read
    keeps its position, so the next page tries it again from where it was.
    """
    heads: list[tuple[float, int, int]] = []
    for order, read in enumerate(reads):
        if read.page and read.page.items:
            heads.append((-_when(read.page.items[0]).timestamp(), order, 0))
    heapq.heapify(heads)
    taken: list[tuple[_Read, Summary]] = []
    used = [0] * len(reads)
    while heads and len(taken) < limit:
        _, order, index = heapq.heappop(heads)
        read = reads[order]
        items = read.page.items if read.page else []
        taken.append((read, items[index]))
        used[order] = index + 1
        if index + 1 < len(items):
            heapq.heappush(heads, (-_when(items[index + 1]).timestamp(), order, index + 1))
    nxt = dict(positions)
    more = False
    for order, read in enumerate(reads):
        if read.page is None:
            continue
        items, count = read.page.items, used[order]
        if count == len(items):
            # Every message this inbox gave was shown: continue below the last, or nothing is left.
            nxt[read.account.id] = items[-1].uid if items and read.page.next_before is not None else 0
        elif count > 0:
            nxt[read.account.id] = items[count - 1].uid
        if nxt.get(read.account.id, -1) != 0:
            more = True
    return taken, nxt, more


async def _read_inbox(
    context: TenantContext, account: MailAccount, access: Access, before: int | None, limit: int,
    q: str | None, unread: bool, flagged: bool, since: date | None, until: date | None, gate: asyncio.Semaphore,
) -> _Read:
    if account.status == AccountStatus.AUTH_FAILED:
        return _Read(account, access, None, "auth_failed", "The mail server refused the saved password. Its owner enters it again in Mailbox settings.")
    dates = {key: value for key, value in (("since", since), ("until", until)) if value is not None}

    def job(server: MailServer) -> Page:
        return server.messages(INBOX, before=before, limit=limit, query=q, unread=unread, flagged=flagged, **dates)

    async with gate:
        try:
            page: Page = await asyncio.wait_for(on_server(context, account, job), timeout=INBOX_TIMEOUT_SECONDS)
        except TimeoutError:
            # The page goes out with the inboxes that answered; this one keeps its place in the cursor.
            return _Read(account, access, None, "slow", "This mailbox did not answer in time. Load the list again to include it.")
        except MailError as exc:
            state: MailboxState = "auth_failed" if exc.kind == "auth" else "offline" if exc.kind in {"offline", "tls"} else "error"
            return _Read(account, access, None, state, exc.message)
    return _Read(account, access, page, "ok", None)


@router.get("/all-inboxes", response_model=AllInboxPage)
async def list_all_inboxes(
    account_id: list[str] = Query(default_factory=list, max_length=MAX_MAILBOXES),
    cursor: str | None = Query(default=None, max_length=MAX_CURSOR),
    limit: int = Query(default=25, ge=1, le=MAX_LIMIT),
    q: str | None = Query(default=None, max_length=200),
    unread: bool = False,
    flagged: bool = False,
    since: date | None = None,
    until: date | None = None,
    context: TenantContext = Depends(tenant_context),
) -> AllInboxPage:
    """Newest first over every readable inbox (or the ``account_id`` ones), ``limit`` at a time."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _available(context)
    if since and until and since > until:
        raise HTTPException(status_code=422, detail="The start date is after the end date.")
    query = re.sub(r"[\x00-\x1f\x7f]", " ", q).strip() or None if q else None
    mailboxes = await asyncio.to_thread(readable_mailboxes, context)
    wanted = list(dict.fromkeys(account_id))
    if wanted:
        known = {account.id for account, _ in mailboxes}
        if any(item not in known for item in wanted):
            # The same answer for a mailbox that does not exist and one the caller may not read.
            raise HTTPException(status_code=404, detail="Mailbox not found.")
        mailboxes = [pair for pair in mailboxes if pair[0].id in wanted]
    fingerprint = _fingerprint(wanted, query, unread, flagged, since, until)
    positions = decode_cursor(cursor, fingerprint) if cursor else {}
    positions = {key: value for key, value in positions.items() if any(account.id == key for account, _ in mailboxes)}

    gate = asyncio.Semaphore(CONCURRENCY)
    pending = [
        (account, access) for account, access in mailboxes if positions.get(account.id, -1) != 0
    ]
    reads = list(await asyncio.gather(*(
        _read_inbox(context, account, access, positions.get(account.id), limit, query, unread, flagged, since, until, gate)
        for account, access in pending
    )))
    taken, nxt, more = merge_pages(reads, positions, limit)
    by_id = {read.account.id: read for read in reads}
    states: list[AllInboxMailbox] = []
    for account, access in mailboxes:
        read = by_id.get(account.id)
        if read is None:
            states.append(AllInboxMailbox(account_id=account.id, address=account.address, display_name=account.display_name, access=access, state="done", error=None, total=0))
            continue
        states.append(AllInboxMailbox(
            account_id=account.id, address=account.address, display_name=account.display_name, access=access,
            state=read.state, error=read.error, total=read.page.total if read.page else None,
        ))
    items = [
        AllInboxMessage(
            **_message_view(INBOX, item).model_dump(), account_id=read.account.id, account_address=read.account.address,
            account_name=read.account.display_name, access=read.access,
        )
        for read, item in taken
    ]
    total = sum(read.page.total for read in reads if read.page)
    partial = any(read.page is None for read in reads)
    return AllInboxPage(items=items, total=total, next_cursor=encode_cursor(nxt, fingerprint) if more else None, partial=partial, mailboxes=states)
