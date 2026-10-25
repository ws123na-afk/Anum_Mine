"""Workspace-isolated lead lifecycle for ANUM Marketing and WhatsApp sales.

Pure domain logic, deliberately independent of a database adapter. Calling
services must authenticate the tenant/workspace, persist using a transaction,
authorize mutations and enforce idempotency in SQL before exposing this API.
Do not send messages or book meetings from this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Iterable, Sequence

from .marketing_sales_orchestration import SalesHandoff
from .whatsapp_sales import SalesIntent


class LeadStatus(StrEnum):
    NEW = "new"
    QUALIFYING = "qualifying"
    QUALIFIED = "qualified"
    QUOTED = "quoted"
    NEGOTIATING = "negotiating"
    WON = "won"
    LOST = "lost"
    DISQUALIFIED = "disqualified"


class FollowUpStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    LeadStatus.NEW: frozenset({LeadStatus.QUALIFYING, LeadStatus.DISQUALIFIED}),
    LeadStatus.QUALIFYING: frozenset({LeadStatus.QUALIFIED, LeadStatus.DISQUALIFIED, LeadStatus.LOST}),
    LeadStatus.QUALIFIED: frozenset({LeadStatus.QUOTED, LeadStatus.NEGOTIATING, LeadStatus.LOST}),
    LeadStatus.QUOTED: frozenset({LeadStatus.NEGOTIATING, LeadStatus.WON, LeadStatus.LOST}),
    LeadStatus.NEGOTIATING: frozenset({LeadStatus.QUOTED, LeadStatus.WON, LeadStatus.LOST}),
    LeadStatus.WON: frozenset(),
    LeadStatus.LOST: frozenset({LeadStatus.QUALIFYING}),
    LeadStatus.DISQUALIFIED: frozenset({LeadStatus.QUALIFYING}),
}
_ALLOWED_CURRENCIES = frozenset({"SAR", "AED", "USD"})
_PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")


@dataclass(frozen=True)
class Lead:
    id: str
    tenant_id: str
    workspace_id: str
    contact_id: str
    source_conversation_id: str
    source_message_id: str
    source_campaign_hint: str | None
    preferred_language: str
    intent: SalesIntent
    status: LeadStatus
    created_at: datetime
    updated_at: datetime
    revision: int = 1
    company_name: str = ""
    contact_name: str = ""
    phone_e164: str | None = None
    requirements: str = ""
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    currency: str = "SAR"
    assigned_to: str | None = None
    marketing_consent: bool = False
    last_contact_at: datetime | None = None


@dataclass(frozen=True)
class LeadEvent:
    lead_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    event: str
    occurred_at: datetime
    from_status: LeadStatus | None
    to_status: LeadStatus | None
    revision: int


@dataclass(frozen=True)
class QuoteDraft:
    lead_id: str
    tenant_id: str
    workspace_id: str
    currency: str
    line_items: tuple[tuple[str, Decimal], ...]
    total: Decimal
    terms: str
    approval_required: bool = True


@dataclass(frozen=True)
class FollowUp:
    lead_id: str
    tenant_id: str
    workspace_id: str
    due_at: datetime
    channel: str
    purpose: str
    status: FollowUpStatus = FollowUpStatus.PROPOSED
    approval_required: bool = True


def _datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{field} is required and must be at most 200 characters")
    return value.strip()


def _money(value: Decimal | str | int) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid amount") from None
    if not result.is_finite() or result < 0 or result.as_tuple().exponent < -2:
        raise ValueError("Amount must be nonnegative, finite, and have at most 2 decimals")
    return result


def _scope(lead: Lead, tenant_id: str, workspace_id: str) -> None:
    if lead.tenant_id != tenant_id or lead.workspace_id != workspace_id:
        raise PermissionError("Lead is not available in this workspace")


def create_lead(
    handoff: SalesHandoff, *, lead_id: str, contact_id: str, now: datetime
) -> tuple[Lead, LeadEvent]:
    """Build a new lead for transactional insert with unique message/lead constraints."""
    lead = Lead(
        id=_required(lead_id, "lead_id"),
        tenant_id=_required(handoff.tenant_id, "tenant_id"),
        workspace_id=_required(handoff.workspace_id, "workspace_id"),
        contact_id=_required(contact_id, "contact_id"),
        source_conversation_id=_required(handoff.conversation_id, "conversation_id"),
        source_message_id=_required(handoff.message_id, "message_id"),
        source_campaign_hint=handoff.campaign_hint,
        preferred_language=handoff.language,
        intent=handoff.intent,
        status=LeadStatus.NEW,
        created_at=_datetime(now),
        updated_at=_datetime(now),
        marketing_consent=handoff.customer_opted_in_to_marketing,
    )
    return lead, LeadEvent(
        lead.id, lead.tenant_id, lead.workspace_id, "system:whatsapp",
        "lead.created", lead.created_at, None, LeadStatus.NEW, lead.revision
    )


def edit_lead(
    lead: Lead, *, tenant_id: str, workspace_id: str, actor_id: str,
    expected_revision: int, now: datetime, company_name: str | None = None,
    contact_name: str | None = None, phone_e164: str | None = None,
    requirements: str | None = None, assigned_to: str | None = None,
    budget_min: Decimal | str | int | None = None,
    budget_max: Decimal | str | int | None = None,
    currency: str | None = None,
) -> tuple[Lead, LeadEvent]:
    """Optimistic mutation; ensure the repository commits only if revision matches."""
    _scope(lead, tenant_id, workspace_id)
    _required(actor_id, "actor_id")
    if expected_revision != lead.revision:
        raise ValueError("Revision conflict")
    changes: dict[str, object] = {}
    for name, value, limit in (
        ("company_name", company_name, 200),
        ("contact_name", contact_name, 200),
        ("requirements", requirements, 4000),
    ):
        if value is not None:
            if len(value) > limit:
                raise ValueError(f"{name} too long")
            changes[name] = value.strip()
    if phone_e164 is not None:
        if not _PHONE.fullmatch(phone_e164):
            raise ValueError("Phone number must use international E.164 format")
        changes["phone_e164"] = phone_e164
    if assigned_to is not None:
        changes["assigned_to"] = _required(assigned_to, "assigned_to")
    if currency is not None:
        if currency not in _ALLOWED_CURRENCIES:
            raise ValueError("Unsupported currency")
        changes["currency"] = currency
    low = _money(budget_min) if budget_min is not None else lead.budget_min
    high = _money(budget_max) if budget_max is not None else lead.budget_max
    if low is not None and high is not None and low > high:
        raise ValueError("Minimum budget cannot exceed maximum budget")
    changes.update(budget_min=low, budget_max=high)
    next_lead = replace(lead, **changes, updated_at=_datetime(now), revision=lead.revision + 1)
    return next_lead, LeadEvent(
        lead.id, tenant_id, workspace_id, actor_id, "lead.edited",
        next_lead.updated_at, lead.status, lead.status, next_lead.revision
    )


def transition_lead(
    lead: Lead, *, tenant_id: str, workspace_id: str, actor_id: str,
    expected_revision: int, to_status: LeadStatus, now: datetime,
) -> tuple[Lead, LeadEvent]:
    _scope(lead, tenant_id, workspace_id)
    _required(actor_id, "actor_id")
    if expected_revision != lead.revision:
        raise ValueError("Revision conflict")
    if to_status not in _TRANSITIONS[lead.status]:
        raise ValueError(f"Invalid status transition: {lead.status} -> {to_status}")
    next_lead = replace(lead, status=to_status, revision=lead.revision + 1, updated_at=_datetime(now))
    return next_lead, LeadEvent(
        lead.id, tenant_id, workspace_id, actor_id, "lead.status_changed",
        next_lead.updated_at, lead.status, to_status, next_lead.revision
    )


def draft_quote(
    lead: Lead, *, tenant_id: str, workspace_id: str,
    line_items: Sequence[tuple[str, Decimal | str | int]],
    terms: str, currency: str | None = None,
) -> QuoteDraft:
    """Requires supplied, verified commercial amounts; never invent prices."""
    _scope(lead, tenant_id, workspace_id)
    chosen = currency or lead.currency
    if chosen not in _ALLOWED_CURRENCIES:
        raise ValueError("Unsupported currency")
    if not line_items or len(line_items) > 50:
        raise ValueError("Quote requires 1-50 line items")
    items = tuple((_required(description, "description"), _money(amount)) for description, amount in line_items)
    if len(terms) > 4000:
        raise ValueError("Terms too long")
    return QuoteDraft(lead.id, tenant_id, workspace_id, chosen, items, sum((price for _, price in items), Decimal("0")), terms.strip())


def propose_follow_up(
    lead: Lead, *, tenant_id: str, workspace_id: str, due_at: datetime,
    now: datetime, channel: str, purpose: str,
) -> FollowUp:
    _scope(lead, tenant_id, workspace_id)
    scheduled = _datetime(due_at)
    if scheduled <= _datetime(now):
        raise ValueError("Follow-up must be in the future")
    if channel not in {"whatsapp", "email", "call"}:
        raise ValueError("Unsupported follow-up channel")
    if len(purpose) > 500:
        raise ValueError("Purpose too long")
    if lead.status in {LeadStatus.WON, LeadStatus.DISQUALIFIED}:
        raise ValueError("Lead is closed")
    return FollowUp(lead.id, tenant_id, workspace_id, scheduled, channel, _required(purpose, "purpose"))


@dataclass(frozen=True)
class SalesOverview:
    leads: int
    qualified: int
    quoted: int
    won: int
    lost: int
    revenue_estimate: Decimal
    by_intent: dict[str, int]


def sales_overview(
    leads: Iterable[Lead], *, tenant_id: str, workspace_id: str,
    won_quote_totals: dict[str, Decimal] | None = None
) -> SalesOverview:
    """Scoped summary. Revenue only reflects explicit supplied won quote amounts."""
    selected = [lead for lead in leads if lead.tenant_id == tenant_id and lead.workspace_id == workspace_id]
    counts = {kind.value: 0 for kind in SalesIntent}
    for lead in selected:
        counts[lead.intent.value] += 1
    verified = won_quote_totals or {}
    revenue = sum(
        (_money(verified[lead.id]) for lead in selected if lead.status == LeadStatus.WON and lead.id in verified),
        Decimal("0")
    )
    return SalesOverview(
        len(selected),
        sum(lead.status in {LeadStatus.QUALIFIED, LeadStatus.QUOTED, LeadStatus.NEGOTIATING, LeadStatus.WON} for lead in selected),
        sum(lead.status in {LeadStatus.QUOTED, LeadStatus.NEGOTIATING} for lead in selected),
        sum(lead.status == LeadStatus.WON for lead in selected),
        sum(lead.status == LeadStatus.LOST for lead in selected),
        revenue, counts,
    )
