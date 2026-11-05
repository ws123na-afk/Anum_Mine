"""Import verified, opted-in customer enquiries into ANUM Sales CRM and the one lead list.

Called only AFTER WhatsApp's webhook signature checks, idempotent message insert
and suppression keyword processing. This path does not transmit any messages,
start an agent, treat an enquiry as marketing opt-in or produce a quotation.

The enquiry is recorded in the WhatsApp Sales CRM (``sales_leads``, which keeps its
quotes, follow-ups and consent) and then joins Business, Leads directly as a lead with
the WhatsApp source and the conversation link (the contract step of
docs/business.md#whatsapp-leads). A number already on an open lead is never merged on
its own: that enquiry waits for the owner's answer under WhatsApp leads.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from .db.sales_repository import SalesCRMStore
from .db.session import SessionLocal, set_tenant_context
from .marketing_sales_orchestration import plan_inbound_handoff
from .sales_lead_lifecycle import Lead as SalesLead
from .sales_lead_lifecycle import create_lead
from .whatsapp_sales import SalesIntent
from .whatsapp_store import Direction, open_whatsapp_store
from .schemas import TenantContext, utc_now

logger = logging.getLogger("anum.sales.whatsapp")


def import_verified_whatsapp_enquiry(context: TenantContext, received: list[dict]) -> dict[str, int]:
    """Best-effort post-webhook importer; DB message uniqueness makes retries safe.

    A verified, stored WhatsApp message is read afresh from the scoped inbox,
    rather than trusting event fields as proof of customer identity. Import only
    sales-related text. Never persist full freeform message bodies in lead fields.
    """
    summary = {"created": 0, "duplicate": 0, "skipped": 0, "failed": 0}
    for item in received:
        message_id = item.get("message_id")
        conversation_id = item.get("conversation_id")
        if not isinstance(message_id, str) or not isinstance(conversation_id, str):
            summary["skipped"] += 1
            continue
        try:
            with open_whatsapp_store(context) as wa:
                message = wa.get_message(message_id)
                conversation = wa.get_conversation(conversation_id)
                if (
                    message is None or conversation is None
                    or message.conversation_id != conversation.id
                    or message.direction != Direction.INBOUND
                    or message.kind != "text"
                    or not message.body.strip()
                ):
                    summary["skipped"] += 1
                    continue
                # Never turn STOP or similar control messages into leads.
                from .suppression import keyword_of
                if keyword_of(message.body) is not None:
                    summary["skipped"] += 1
                    continue
                handoff = plan_inbound_handoff(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    conversation_id=conversation.id,
                    message_id=message.id,
                    inbound_text=message.body,
                    verified_marketing_consent=False,
                )
                if handoff.intent == SalesIntent.OTHER:
                    summary["skipped"] += 1
                    continue
                contact_id = conversation.contact_id
                account = getattr(wa, "get_account", lambda: None)()
                owner_id = getattr(account, "updated_by", None)
            with SessionLocal.begin() as session:
                set_tenant_context(session, context.tenant_id, context.workspace_id)
                crm = SalesCRMStore(session, context.tenant_id, context.workspace_id)
                existing = crm.find_by_inbound_message(message_id) or crm.find_by_conversation(conversation_id)
                if existing:
                    joined = existing
                else:
                    lead, event = create_lead(
                        handoff, lead_id=str(uuid4()), contact_id=contact_id, now=utc_now()
                    )
                    crm.insert(lead, event)
                    session.flush()
                    joined = lead
            summary["duplicate" if existing else "created"] += 1
            # After the CRM row committed: the lead list in its own short unit of work. A row
            # that was already there (an older enquiry) joins it now too.
            join_lead_list(context, joined, owner_id)
        except IntegrityError:
            # Unique (tenant, workspace, source_message_id) handles concurrent retries.
            summary["duplicate"] += 1
        except Exception:
            summary["failed"] += 1
            logger.exception("sales lead import failed for verified WhatsApp event")
    return summary


def join_lead_list(context: TenantContext, sales_lead: object, owner_id: str | None) -> str | None:
    """Write the enquiry into Business, Leads (``whatsapp_leads.join_from_ingestion``). Best effort:
    a failure is logged and the enquiry stays under WhatsApp leads, where the owner can add it."""
    if not isinstance(sales_lead, SalesLead):
        return None
    try:
        from .business.store import open_business_store
        from .business.whatsapp_leads import join_from_ingestion

        with open_business_store(context) as store:
            outcome = join_from_ingestion(store, context, sales_lead, utc_now(), owner_id=owner_id)
    except Exception as exc:  # noqa: BLE001 - the CRM row is kept; the lead waits for the owner
        logger.warning("whatsapp lead not added to the lead list", extra={"anum_sales": {"error_class": type(exc).__name__}})
        return None
    logger.info("whatsapp lead in the lead list", extra={"anum_sales": {"outcome": outcome}})
    return outcome
