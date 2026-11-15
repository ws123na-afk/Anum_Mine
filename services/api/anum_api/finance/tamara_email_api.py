"""Preview Tamara installment records from an ANUM-connected, owner-owned Gmail mailbox.

This route never stores financial data, modifies mail, or marks a payment as bank-verified.
The user must explicitly review and import the preview in Finance.

A Tamara email of the last two weeks that could not be read (no order reference, no due amounts, a truncated or
unavailable body) is noted by its ANUM message id and received day only (``finance_notice_claims`` kind
``alert:tamara_unread``, pruned with the other claims), so the daily alerts can tell it as ``capture_failed``
(docs/finance.md#financial-alerts). No body, subject, amount or address is kept.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..authorization import Permission
from ..dependencies import has_permission
from ..dependencies import tenant_context
from ..email_inbox import MessageFilters, fetch_body, open_email_store
from ..email_providers import EmailProvider
from ..schemas import TenantContext
from .api import _manage
from .models import InstallmentRefundNotice, InstallmentStatementEntry
from .tamara_email import MailEvidence, extract_tamara_refunds, extract_tamara_statement, local_day

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])
logger = logging.getLogger("anum_api.finance.tamara_email")
UNREAD_CLAIM = "alert:tamara_unread"
UNREAD_NOTE_DAYS = 14


class ConnectedMailbox(BaseModel):
    id: str
    address: str
    selected: bool


class TamaraEmailPreview(BaseModel):
    account_id: str
    address: str
    messages_checked: int
    messages_matched: int
    rows: list[InstallmentStatementEntry]
    # Refund and order-updated emails: refund evidence, saved only when the owner approves the import.
    refunds: list[InstallmentRefundNotice] = []
    warnings: list[str]


def _owner_gmail_accounts(context: TenantContext):
    if not has_permission(context, Permission.EMAIL_READ):
        raise HTTPException(status_code=403, detail="Email read access is required.")
    with open_email_store(context) as store:
        return [a for a in store.list_accounts() if a.provider == EmailProvider.GMAIL and a.connected_by == context.user_id]


def _load_tamara_messages(context: TenantContext, account_id: str, limit: int):
    """The owner's Gmail account and its cached Tamara messages (active and archived), newest first."""
    accounts = _owner_gmail_accounts(context)
    account = next((a for a in accounts if a.id == account_id), None)
    if account is None:
        raise HTTPException(status_code=404, detail="Connect your own Gmail account in ANUM Email first.")
    # Read the existing ANUM email metadata cache only; the remote mailbox is never changed.
    with open_email_store(context) as store:
        messages = store.list_messages([account.id], MessageFilters(account_id=account.id, archived=False, query='tamara'),
                                       limit=limit, before=None)
        # Archived Tamara evidence may still be relevant.
        archived = store.list_messages([account.id], MessageFilters(account_id=account.id, archived=True, query='tamara'),
                                       limit=limit, before=None)
    ordered = sorted({m.id: m for m in messages + archived}.values(), key=lambda m: m.received_at, reverse=True)
    return account, ordered[:limit]


def owner_timezone(context: TenantContext) -> str:
    """The owner's Finance time zone, the one rule for a local day in Personal Finance."""
    from .store import open_finance_store
    from .summary import settings_for

    with open_finance_store(context) as store:
        return settings_for(store, context).timezone


def note_unread(context: TenantContext, found: list[tuple[str, date]]) -> int:
    """Note the Tamara emails of the last ``UNREAD_NOTE_DAYS`` days that could not be read (id and the owner's local day only), once each."""
    from ..schemas import utc_now
    from .rules_store import open_rule_stores
    from .summary import owner_today

    now = utc_now()
    recent = [(i, d) for i, d in found if d >= owner_today(context, now) - timedelta(days=UNREAD_NOTE_DAYS)][:200]
    if not recent:
        return 0
    try:
        with open_rule_stores(context) as (_finance, store):
            return sum(len(store.claim_item_notices(context, UNREAD_CLAIM, [message_id], day, now)) for message_id, day in recent)
    except Exception as exc:  # noqa: BLE001 - the preview still answers; the warnings show the emails at once
        logger.warning("finance_tamara_unread_not_noted error_class=%s", type(exc).__name__)
        return 0


@router.get("/installment-email/mailboxes", response_model=list[ConnectedMailbox])
def installment_mailboxes(context: TenantContext = Depends(tenant_context)) -> list[ConnectedMailbox]:
    _manage(context)
    accounts = _owner_gmail_accounts(context)
    return [ConnectedMailbox(id=a.id, address=a.address,
        selected=a.address.casefold() == "shahzeb.rahmanllc@gmail.com") for a in accounts]


@router.get("/installment-email/preview", response_model=TamaraEmailPreview)
async def installment_email_preview(
    account_id: str = Query(min_length=1, max_length=80),
    limit: int = Query(default=250, ge=1, le=500),
    context: TenantContext = Depends(tenant_context),
) -> TamaraEmailPreview:
    _manage(context)
    # Every database read runs in a worker thread, in its own short unit of work, so the event
    # loop never waits for a pooled connection (docs/realtime.md#event-loop).
    account, messages = await asyncio.to_thread(_load_tamara_messages, context, account_id, limit)
    timezone = await asyncio.to_thread(owner_timezone, context)
    matches = [m for m in messages if m.from_address.lower().strip().endswith("@tamara.co")
               or m.from_address.lower().strip().endswith(".tamara.co")]
    # Strict sender verification occurs inside the extraction function as well.
    semaphore = asyncio.Semaphore(6)

    async def inspect(message):
        async with semaphore:
            try:
                body = await fetch_body(context, account, message)
                if body.truncated:
                    return None, message.id + ": truncated message, manual review required"
                return MailEvidence(message_id=message.id, provider_message_id=message.provider_message_id,
                    from_address=message.from_address, subject=message.subject, text=body.text,
                    received_at=message.received_at), None
            except Exception:
                # No OAuth tokens, provider body or exception details leave this endpoint.
                return None, message.id + ": email body unavailable; reconnect or resync Gmail"

    checked = await asyncio.gather(*(inspect(m) for m in matches))
    evidence = [m for m, _ in checked if m is not None]
    warnings = [warning for _, warning in checked if warning]
    rows, skipped = extract_tamara_statement(evidence, timezone)
    warnings.extend(skipped[:50])
    unreadable = {w.partition(": ")[0] for w in [*warnings, *skipped]}
    await asyncio.to_thread(note_unread, context, [(m.id, local_day(m.received_at, timezone)) for m in matches if m.id in unreadable])
    if len(messages) == limit:
        warnings.append("Mailbox scan reached its message limit; older receipts may be missing. Sync/import historical mail separately.")
    return TamaraEmailPreview(account_id=account.id, address=account.address,
        messages_checked=len(messages), messages_matched=len(evidence), rows=rows, refunds=extract_tamara_refunds(evidence, timezone),
        warnings=warnings[:80])
