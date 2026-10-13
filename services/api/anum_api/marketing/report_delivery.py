"""Deliver the weekly marketing report by email and WhatsApp, best effort (docs/marketing.md#weekly-report).

Nothing is sent directly. Each channel goes through that integration's own governed path,
so the report waits for approval like any other outgoing message:

* Email: a draft addressed to the chosen mailbox's own address (the owner's inbox), then
  ``email_api.request_send_approval`` (a paused ``send_email`` call with this exact text).
* WhatsApp: a text to the chosen conversation through ``whatsapp.send``, which needs the
  24-hour window and always asks for approval.

A channel that is not set up reports ``not_connected``; a channel that fails reports
``failed`` with the reason, and the other channel still runs.
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..authorization import Permission
from ..dependencies import has_permission
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from .analytics import WeeklyReport

ChannelOutcome = Literal["requested", "not_connected", "not_requested", "not_allowed", "failed"]


class DeliveryRequest(BaseModel):
    email: bool = True
    # A mailbox the person can see; the first connected one when omitted.
    email_account_id: str | None = Field(default=None, max_length=80)
    # The WhatsApp conversation to send to (usually the owner's own chat with the business
    # number). Omitted = no WhatsApp delivery.
    whatsapp_conversation_id: str | None = Field(default=None, max_length=80)


class ChannelResult(BaseModel):
    outcome: ChannelOutcome
    detail: str = ""
    approval_id: str | None = None
    target: str | None = None


class DeliveryResult(BaseModel):
    email: ChannelResult
    whatsapp: ChannelResult


def report_text(report: WeeklyReport) -> tuple[str, str]:
    """(subject, body) in the report's language."""
    joiner = " إلى " if report.language == "ar" else " to "
    period = f"{report.week_start.isoformat()}{joiner}{report.week_end.isoformat()}"
    subject = f"{report.headline} {period}"
    return subject, "\n\n".join([report.headline, *report.lines])


def _detail(exc: HTTPException) -> str:
    detail = exc.detail
    return detail if isinstance(detail, str) else "The request was refused."


async def deliver_email(context: TenantContext, repository: AnumRepository, report: WeeklyReport, account_id: str | None) -> ChannelResult:
    from .. import email_api, email_inbox

    if not has_permission(context, Permission.EMAIL_ACT):
        return ChannelResult(outcome="not_allowed", detail="Sending email needs the email permission (owner, admin or member).")
    with email_inbox.open_email_store(context) as store:
        accounts = [
            account for account in email_inbox.visible_accounts(store, context)
            if account.status == email_inbox.AccountStatus.CONNECTED and (account_id is None or account.id == account_id)
        ]
    if not accounts:
        detail = "That mailbox is not connected or not visible to you." if account_id else "Connect a mailbox in Email to send the report."
        return ChannelResult(outcome="not_connected", detail=detail)
    account = accounts[0]
    subject, body = report_text(report)
    now = utc_now()
    draft = email_inbox.EmailDraft(
        id=new_id("edr"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, account_id=account.id,
        to=[account.address], subject=subject[:300], body=body[: email_inbox.MAX_DRAFT_BODY], source="person",
        created_by=context.user_id, created_at=now, updated_at=now,
    )
    # The copy in the provider's Drafts folder is a convenience; its failure does not stop delivery.
    draft.provider_draft_id, _warning = await email_inbox.save_provider_draft(context, account, draft, None)
    with email_inbox.open_email_store(context) as store:
        store.add_draft(draft)
    try:
        response = await email_api.request_send_approval(draft.id, context, repository)
    except HTTPException as exc:
        return ChannelResult(outcome="failed", detail=_detail(exc), target=account.address)
    return ChannelResult(
        outcome="requested", approval_id=response.approval.id, target=account.address,
        detail="Waiting for approval in Approvals; it is sent only after that.",
    )


async def deliver_whatsapp(context: TenantContext, repository: AnumRepository, report: WeeklyReport, conversation_id: str | None) -> ChannelResult:
    from .. import whatsapp

    if conversation_id is None:
        return ChannelResult(outcome="not_requested", detail="Choose a WhatsApp conversation to send the report to.")
    if not has_permission(context, Permission.WHATSAPP_REPLY):
        return ChannelResult(outcome="not_allowed", detail="Sending WhatsApp messages needs the WhatsApp reply permission.")
    with whatsapp.open_whatsapp_store(context) as store:
        connected = store.get_account() is not None
    if not connected or whatsapp.setup_problems():
        return ChannelResult(outcome="not_connected", detail="Connect WhatsApp Business in WhatsApp to send the report.")
    _subject, body = report_text(report)
    try:
        response = await whatsapp.send(conversation_id, whatsapp.SendRequest(text=body[: whatsapp.MAX_TEXT]), context, repository)
    except HTTPException as exc:
        return ChannelResult(outcome="failed", detail=_detail(exc), target=conversation_id)
    if response.outcome == "failed":
        return ChannelResult(outcome="failed", detail=response.detail, target=conversation_id)
    return ChannelResult(
        outcome="requested", approval_id=response.approval.id if response.approval else None, target=conversation_id,
        detail=response.detail,
    )


async def deliver(context: TenantContext, repository: AnumRepository, report: WeeklyReport, request: DeliveryRequest) -> DeliveryResult:
    """Both channels, independently; one failing never stops the other."""
    email = (
        await deliver_email(context, repository, report, request.email_account_id)
        if request.email else ChannelResult(outcome="not_requested")
    )
    whatsapp_result = await deliver_whatsapp(context, repository, report, request.whatsapp_conversation_id)
    return DeliveryResult(email=email, whatsapp=whatsapp_result)
