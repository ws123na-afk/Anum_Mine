"""Safe orchestration plan for existing ANUM Marketing, WhatsApp and sales agents.

This module prepares workspace-scoped lead handoffs without dispatching messages,
creating approvals or writing to a CRM. The calling service must persist the
plan with its authenticated tenant/workspace and apply policy to each action.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from .whatsapp_sales import SalesIntent, inbound_campaign_marker, qualify_lead


class SalesStage(StrEnum):
    NEW = "new"
    NEEDS_DETAILS = "needs_details"
    READY_FOR_REVIEW = "ready_for_review"
    HUMAN_REQUIRED = "human_required"


class AgentRole(StrEnum):
    MARKETING = "marketing"
    WHATSAPP = "whatsapp"
    SALES = "sales"
    QUOTATIONS = "quotations"
    FOLLOW_UP = "follow_up"
    ANALYTICS = "analytics"


@dataclass(frozen=True)
class SalesHandoff:
    tenant_id: str
    workspace_id: str
    conversation_id: str
    message_id: str
    idempotency_key: str
    intent: SalesIntent
    language: str
    campaign_hint: str | None
    stage: SalesStage
    next_agent: AgentRole
    suggested_question: str
    requires_owner_review: bool
    customer_opted_in_to_marketing: bool = False


def plan_inbound_handoff(
    *,
    tenant_id: str,
    workspace_id: str,
    conversation_id: str,
    message_id: str,
    inbound_text: str,
    verified_marketing_consent: bool = False,
) -> SalesHandoff:
    """Derive a deterministic handoff from one already-verified inbound webhook.

    Consent is only trusted when explicitly passed from the server's consent
    record. Sending a WhatsApp message does not, on its own, grant recurring
    marketing permission. Campaign hints are self-reported and unverified.
    """
    identifiers = (tenant_id, workspace_id, conversation_id, message_id)
    if any(not value or not value.strip() or len(value) > 200 for value in identifiers):
        raise ValueError("Tenant, workspace, conversation and message IDs are required")
    if not isinstance(inbound_text, str):
        raise ValueError("Inbound message must be text")
    qualification = qualify_lead(inbound_text)
    stage = (
        SalesStage.HUMAN_REQUIRED if qualification.requires_human
        else SalesStage.READY_FOR_REVIEW if qualification.urgent
        else SalesStage.NEEDS_DETAILS if qualification.intent != SalesIntent.OTHER
        else SalesStage.NEW
    )
    next_agent = (
        AgentRole.SALES if stage in {SalesStage.HUMAN_REQUIRED, SalesStage.READY_FOR_REVIEW}
        else AgentRole.WHATSAPP
    )
    # Include full workspace context to prevent collisions across tenants.
    identity = "\x1f".join(identifiers).encode("utf-8")
    return SalesHandoff(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        message_id=message_id,
        idempotency_key=sha256(identity).hexdigest(),
        intent=qualification.intent,
        language=qualification.language,
        campaign_hint=inbound_campaign_marker(inbound_text),
        stage=stage,
        next_agent=next_agent,
        suggested_question=qualification.follow_up_question,
        requires_owner_review=qualification.requires_human or qualification.urgent,
        customer_opted_in_to_marketing=verified_marketing_consent,
    )


@dataclass(frozen=True)
class SalesAction:
    agent: AgentRole
    operation: str
    approval_required: bool


def permitted_next_actions(handoff: SalesHandoff) -> tuple[SalesAction, ...]:
    """Return a proposed agent work plan, never execute or auto-send actions."""
    base = (
        SalesAction(AgentRole.SALES, "review_or_create_lead", False),
        SalesAction(AgentRole.ANALYTICS, "record_inbound_enquiry", False),
    )
    if handoff.requires_owner_review:
        return base + (SalesAction(AgentRole.SALES, "human_takeover", True),)
    return base + (
        SalesAction(AgentRole.WHATSAPP, "draft_reply", False),
        SalesAction(AgentRole.QUOTATIONS, "prepare_quote_draft", True),
        SalesAction(AgentRole.FOLLOW_UP, "propose_follow_up", True),
    )
