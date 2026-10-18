"""Platform operator routes for mail health (docs/email.md#operator-view).

Only the platform operator (``platform:operate``, granted by the ``platform_operator`` realm
role) may call them; everyone else gets 403 before anything is read. The answers hold
counts, states and timestamps per organisation the operator runs: never a message, subject,
body, mailbox address or password.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..authorization import Permission
from ..dependencies import require_permission, tenant_context
from ..schemas import TenantContext
from .config import mail_settings
from .mailcow import MailcowStatus, read_status
from .monitor import MailOperatorView, operator_view

router = APIRouter(prefix="/api/v1/platform/mail", tags=["platform"])


class MailServerSummary(BaseModel):
    """The Email Center's own settings, without anything secret."""

    enabled: bool
    configured: bool
    imap_host: str | None
    smtp_host: str | None
    allowed_tenants: int
    send_per_hour: int
    connect_attempts_per_hour: int


class MailServerView(BaseModel):
    server: MailServerSummary
    mailcow: MailcowStatus


@router.get("/health", response_model=MailOperatorView)
def mail_health(
    days: int = Query(default=7, ge=1, le=90), context: TenantContext = Depends(tenant_context)
) -> MailOperatorView:
    """Mailboxes, last checks and send outcomes per organisation this operator runs."""
    require_permission(context, Permission.PLATFORM_OPERATE)
    return operator_view(context.user_id, days, mail_settings.enabled)


@router.get("/server", response_model=MailServerView)
async def mail_server(context: TenantContext = Depends(tenant_context)) -> MailServerView:
    """The Email Center's settings and, when switched on, Mailcow's read-only status."""
    require_permission(context, Permission.PLATFORM_OPERATE)
    cfg = mail_settings
    summary = MailServerSummary(
        enabled=cfg.enabled, configured=cfg.configured, imap_host=cfg.imap_host or None, smtp_host=cfg.smtp_host or None,
        allowed_tenants=len(cfg.tenants), send_per_hour=cfg.send_per_hour, connect_attempts_per_hour=cfg.connect_attempts_per_hour,
    )
    return MailServerView(server=summary, mailcow=await asyncio.to_thread(read_status))
