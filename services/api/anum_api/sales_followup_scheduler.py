"""Durable, consent-gated sales WhatsApp follow-ups.

An explicit owner approves the exact Meta template and parameters and enables
a bounded workspace policy. The scheduler discovers only due approved rows.
Each row moves to dispatching *before* the network call. If the process dies
mid-send, the action becomes unknown and is NEVER automatically resent.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update

from .db.sales_repository import (
    SalesAutomationSettingsRecord, SalesCRMStore, SalesFollowUpRecord, SalesLeadRecord,
)
from .db.session import SessionLocal, set_tenant_context
from .schemas import TenantContext, new_id, utc_now
from .settings import settings
from .suppression import suppressed_for_call
from .whatsapp import (
    TEMPLATE_TOOL, find_template, graph_client, template_problem,
    templates_for, tool_definitions,
)
from .whatsapp_store import (
    Direction, Message, MessageStatus, TemplateUse, open_whatsapp_store,
)

logger = logging.getLogger("anum.sales.followups")
SYSTEM_ACTOR = "system:sales-followup"
STALE_INTERVAL = timedelta(minutes=15)
RIYADH = ZoneInfo("Asia/Riyadh")

_DISCOVER = text(
    """
    select id, tenant_id, workspace_id
    from sales_followups
    where (status = 'approved' and due_at <= :now)
       or (status = 'dispatching' and last_attempt_at <= :stale)
    order by due_at
    limit :limit
    """
)


def approval_payload_hash(*, tenant: str, workspace: str, followup: str,
                          lead: str, conversation: str, to: str, due_at: datetime,
                          template: str, language: str,
                          parameters: list[str]) -> str:
    """Bind an owner's approval to exactly one channel, recipient and payload."""
    data = {
        "tenant": tenant, "workspace": workspace,
        "followup": followup, "lead": lead,
        "conversation": conversation, "to": to, "due_at": due_at.isoformat(),
        "template": template, "language": language, "parameters": parameters,
    }
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def _context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id,
                         user_id=SYSTEM_ACTOR, roles=[])


def _due(limit: int) -> list[tuple[str, str, str]]:
    if settings.repository_backend != "postgresql":
        return []
    from .maintenance import default_session_factory, discover

    now = utc_now()
    return [(row.tenant_id, row.workspace_id, row.id)
            for row in discover(default_session_factory, _DISCOVER, {
                "now": now, "stale": now - STALE_INTERVAL,
                "limit": min(max(limit, 1), 50),
            })]


def _mark(context: TenantContext, item_id: str, status: str,
          error: str | None = None, wamid: str | None = None,
          *, from_status: str = "dispatching") -> bool:
    with SessionLocal.begin() as session:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        result = session.execute(update(SalesFollowUpRecord).where(
            SalesFollowUpRecord.id == item_id,
            SalesFollowUpRecord.tenant_id == context.tenant_id,
            SalesFollowUpRecord.workspace_id == context.workspace_id,
            SalesFollowUpRecord.status == from_status,
        ).values(status=status, last_error=(error or None) and error[:1000],
                 sent_wamid=wamid))
        return result.rowcount == 1


def _workspace_policy(context: TenantContext) -> dict:
    with SessionLocal.begin() as session:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        return SalesCRMStore(session, context.tenant_id,
                             context.workspace_id).get_automation_settings()


def _governance_allows(context: TenantContext) -> bool:
    """Fail closed on any organization policy that requires further approvals."""
    from .dependencies import _unit_of_work
    from .tool_governance import match_governance

    definition = tool_definitions()[4]
    try:
        with _unit_of_work(context, require_membership=False) as repository:
            governance = repository.get_tool_governance(context)
    except Exception:
        return False
    match = match_governance(
        governance, tool=TEMPLATE_TOOL,
        target=definition.target, risk_level=definition.risk_level,
    )
    # The owner approved one exact template. Anything more restrictive must
    # go through the regular approvals tool, not this unattended scheduler.
    return not (match.deny or match.require_approval or match.approval_rules)


def _consent_still_valid(context: TenantContext, lead_id: str) -> bool:
    with SessionLocal.begin() as session:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        lead = session.scalar(select(SalesLeadRecord).where(
            SalesLeadRecord.id == lead_id,
            SalesLeadRecord.tenant_id == context.tenant_id,
            SalesLeadRecord.workspace_id == context.workspace_id,
        ))
        return bool(lead and lead.marketing_consent and
                    lead.status not in {"lost", "disqualified"})


def _claim(context: TenantContext, item_id: str, now: datetime) -> bool:
    """Serialize the workspace quota on the policy row and claim one due action."""
    with SessionLocal.begin() as session:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        policy = session.scalar(select(SalesAutomationSettingsRecord).where(
            SalesAutomationSettingsRecord.tenant_id == context.tenant_id,
            SalesAutomationSettingsRecord.workspace_id == context.workspace_id,
        ).with_for_update())
        if policy is None or not policy.enabled or policy.paused or policy.max_sends_per_day < 1:
            return False
        item = session.scalar(select(SalesFollowUpRecord).where(
            SalesFollowUpRecord.id == item_id,
            SalesFollowUpRecord.tenant_id == context.tenant_id,
            SalesFollowUpRecord.workspace_id == context.workspace_id,
        ).with_for_update(skip_locked=True))
        if item is None or item.status != "approved" or item.due_at > now:
            return False
        lead = session.scalar(select(SalesLeadRecord).where(
            SalesLeadRecord.id == item.lead_id,
            SalesLeadRecord.tenant_id == context.tenant_id,
            SalesLeadRecord.workspace_id == context.workspace_id,
        ))
        if lead is None or not lead.marketing_consent or lead.status in {"lost", "disqualified"}:
            item.status = "held"
            item.last_error = "Consent revoked or lead closed; owner review required"
            return False
        # Count claims as well as completed sends: two replicas cannot both
        # exceed the cap while a request is still in flight.
        local_day = now.astimezone(RIYADH).date()
        midnight = datetime(local_day.year, local_day.month, local_day.day,
                            tzinfo=RIYADH).astimezone(timezone.utc)
        used = session.scalar(select(func.count(SalesFollowUpRecord.id)).where(
            SalesFollowUpRecord.tenant_id == context.tenant_id,
            SalesFollowUpRecord.workspace_id == context.workspace_id,
            SalesFollowUpRecord.status.in_(["dispatching", "sent", "unknown"]),
            SalesFollowUpRecord.last_attempt_at >= midnight,
        )) or 0
        if used >= policy.max_sends_per_day:
            return False
        item.status = "dispatching"
        item.attempts += 1
        item.last_attempt_at = now
        item.last_error = None
        return True


async def _execute(context: TenantContext, item_id: str) -> bool:
    from .agent_controls import agents_stopped
    from .demo import is_demo

    if is_demo(context) or await agents_stopped(context):
        return False
    with SessionLocal.begin() as session:
        set_tenant_context(session, context.tenant_id, context.workspace_id)
        store = SalesCRMStore(session, context.tenant_id, context.workspace_id)
        item = session.scalar(select(SalesFollowUpRecord).where(
            SalesFollowUpRecord.id == item_id,
            SalesFollowUpRecord.tenant_id == context.tenant_id,
            SalesFollowUpRecord.workspace_id == context.workspace_id,
        ))
        if item is None:
            return False
        if item.status == "dispatching":
            if item.last_attempt_at and utc_now() - item.last_attempt_at >= STALE_INTERVAL:
                item.status = "unknown"
                item.last_error = "Dispatch interrupted; check Meta before any manual retry"
            return False
        if item.status != "approved" or item.due_at > utc_now():
            return False
        lead = store.get(item.lead_id)
        if lead is None or not lead.marketing_consent or lead.status.value in {"lost", "disqualified"}:
            item.status = "held"
            item.last_error = "Customer consent or lead status changed; owner review required"
            return False
        saved = {
            "id": item.id, "lead_id": item.lead_id, "due_at": item.due_at,
            "template_name": item.template_name,
            "template_language": item.template_language,
            "parameters": list(item.template_parameters or []),
            "payload_hash": item.payload_hash,
            "approved_by": item.approved_by, "approved_at": item.approved_at,
            "conversation_id": lead.source_conversation_id,
        }
    if not saved["approved_by"] or not saved["payload_hash"] or not saved["approved_at"]:
        _mark(context, item_id, "held", "Missing owner authorization", from_status="approved")
        return False
    if not _governance_allows(context):
        _mark(context, item_id, "held", "Organization tool policy requires a separate approval", from_status="approved")
        return False
    current_policy = _workspace_policy(context)
    if not current_policy["enabled"] or current_policy["paused"]:
        return False

    with open_whatsapp_store(context) as wa:
        account = wa.get_account(include_secrets=True)
        conversation = wa.get_conversation(saved["conversation_id"])
    if account is None or account.access_token is None or conversation is None:
        _mark(context, item_id, "held", "WhatsApp account or conversation disconnected", from_status="approved")
        return False
    if saved["template_name"] not in account.auto_send_templates:
        _mark(context, item_id, "held", "Template no longer on owner allowlist", from_status="approved")
        return False
    if conversation.last_inbound_at and conversation.last_inbound_at > saved["approved_at"]:
        _mark(context, item_id, "held", "Customer replied after the scheduled follow-up was approved", from_status="approved")
        return False
    if approval_payload_hash(
        tenant=context.tenant_id, workspace=context.workspace_id,
        followup=item_id, lead=saved["lead_id"], conversation=saved["conversation_id"],
        to=conversation.wa_id, due_at=saved["due_at"],
        template=saved["template_name"], language=saved["template_language"],
        parameters=saved["parameters"],
    ) != saved["payload_hash"]:
        _mark(context, item_id, "held", "Recipient or template changed after approval", from_status="approved")
        return False

    definition = tool_definitions()[4]
    hits = suppressed_for_call(
        context, TEMPLATE_TOOL,
        {"to": "+" + conversation.wa_id, "purpose": "marketing"},
        definition,
    )
    if hits:
        _mark(context, item_id, "held", "Customer is on the suppression list", from_status="approved")
        return False
    try:
        templates = await templates_for(account, refresh=True)
        template = find_template(templates, saved["template_name"], saved["template_language"])
        problem = template_problem(template, saved["template_name"],
                                   saved["template_language"], saved["parameters"])
        if problem:
            _mark(context, item_id, "held", problem, from_status="approved")
            return False
    except Exception:
        logger.exception("sales_followup_template_validation_failed", extra={"followup": item_id})
        return False

    if not _claim(context, item_id, utc_now()):
        return False
    # Re-check both revocation and pause after the committed quota claim.
    policy = _workspace_policy(context)
    if (not policy["enabled"] or policy["paused"]
            or not _governance_allows(context)
            or not _consent_still_valid(context, saved["lead_id"])
            or suppressed_for_call(context, TEMPLATE_TOOL,
                                   {"to": "+" + conversation.wa_id, "purpose": "marketing"},
                                   definition)):
        _mark(context, item_id, "held", "Sales automation paused or contact suppressed")
        return False
    try:
        wamid = await graph_client(account).send_template(
            account.phone_number_id, conversation.wa_id,
            saved["template_name"], saved["template_language"],
            saved["parameters"],
        )
    except Exception as exc:
        # Network timeouts can be ambiguous: NEVER auto-retry a possibly sent message.
        logger.exception("sales_followup_provider_failed", extra={"followup": item_id})
        _mark(context, item_id, "unknown", f"Provider outcome uncertain ({type(exc).__name__}); inspect Meta")
        return False

    # Meta accepted the template: persist the wamid before doing any optional
    # inbox bookkeeping so no additional send can be attempted on a retry.
    _mark(context, item_id, "sent", wamid=wamid)
    from .whatsapp_graph import fill_template
    from .whatsapp_store import preview
    now = utc_now()
    try:
        message = Message(
            id=new_id("wam"), tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            conversation_id=conversation.id, wamid=wamid,
            direction=Direction.OUTBOUND, kind="template",
            body=fill_template(template.body if template else "", saved["parameters"]),
            template=TemplateUse(name=saved["template_name"],
                                 language=saved["template_language"],
                                 parameters=saved["parameters"]),
            status=MessageStatus.SENT, sent_by=SYSTEM_ACTOR,
            timestamp=now, created_at=now, updated_at=now,
        )
        with open_whatsapp_store(context) as wa:
            wa.add_message(message)
            recent = wa.get_conversation(conversation.id)
            if recent and now >= recent.last_message_at:
                recent.last_message_at = now
                recent.last_direction = Direction.OUTBOUND
                recent.last_message_preview = preview(message)
                wa.save_conversation(recent)
    except Exception:
        logger.exception("sales_followup_inbox_record_failed", extra={"followup": item_id})
    logger.info("sales_followup_template_sent", extra={"followup": item_id})
    return True


async def _all_due(rows: list[tuple[str, str, str]]) -> int:
    sent = 0
    for tenant, workspace, item in rows:
        try:
            sent += bool(await _execute(_context(tenant, workspace), item))
        except Exception:
            logger.exception("sales_followup_dispatch_failed", extra={"followup": item})
    return sent


def run_due_sales_followups(limit: int = 25) -> int:
    """One scheduler pass, shared across replicas by SQL row/owner policy locks."""
    rows = _due(limit)
    if not rows:
        return 0
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_all_due(rows))
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _all_due(rows)).result()
