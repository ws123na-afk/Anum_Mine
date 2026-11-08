"""Preview Tamara installment records from an ANUM-connected, owner-owned Gmail mailbox.

This route never stores financial data, modifies mail, or marks a payment as bank-verified.
The user must explicitly review and import the preview in Finance.
"""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..authorization import Permission
from ..dependencies import has_permission
from ..dependencies import tenant_context
from ..email_inbox import MessageFilters, fetch_body, open_email_store
from ..email_providers import EmailProvider
from ..schemas import TenantContext
from .api import _manage
from .models import InstallmentStatementEntry
from .tamara_email import MailEvidence, extract_tamara_statement

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


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
    rows, skipped = extract_tamara_statement(evidence)
    warnings.extend(skipped[:50])
    if len(messages) == limit:
        warnings.append("Mailbox scan reached its message limit; older receipts may be missing. Sync/import historical mail separately.")
    return TamaraEmailPreview(account_id=account.id, address=account.address,
        messages_checked=len(messages), messages_matched=len(evidence), rows=rows, warnings=warnings[:80])
