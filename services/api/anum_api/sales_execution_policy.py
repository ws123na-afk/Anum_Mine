"""Policy gate for Sales CRM outbound execution.

The default is fail-closed. No network calls, schedules or side effects occur
here; existing WhatsApp / outreach approval infrastructure must perform them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class DeliveryChannel(StrEnum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    CALL = "call"


@dataclass(frozen=True)
class SalesExecutionPolicy:
    enabled: bool = False
    paused: bool = True
    require_owner_approval: bool = True
    max_actions_per_day: int = 0
    only_verified_consent: bool = True


@dataclass(frozen=True)
class ExecutionDecision:
    permitted: bool
    reason: str


def can_execute_sales_action(
    *,
    policy: SalesExecutionPolicy,
    owner_approved: bool,
    has_consent: bool,
    suppressed: bool,
    recent_customer_message_at: datetime | None,
    now: datetime,
    channel: DeliveryChannel,
    action_count_today: int,
    is_approved_template: bool = False,
) -> ExecutionDecision:
    """Evaluate policy before any external action; no flags bypass provider checks."""
    if not policy.enabled or policy.paused:
        return ExecutionDecision(False, "Sales automation is disabled or paused")
    if policy.require_owner_approval and not owner_approved:
        return ExecutionDecision(False, "Owner approval is required")
    if suppressed:
        return ExecutionDecision(False, "Customer has opted out")
    if policy.only_verified_consent and not has_consent:
        return ExecutionDecision(False, "Verified marketing consent is required")
    if policy.max_actions_per_day < 1 or action_count_today >= policy.max_actions_per_day:
        return ExecutionDecision(False, "Daily sales action limit reached")
    if channel == DeliveryChannel.CALL:
        return ExecutionDecision(False, "Automated phone calls are not supported by this policy")
    if channel == DeliveryChannel.WHATSAPP and not is_approved_template:
        if recent_customer_message_at is None:
            return ExecutionDecision(False, "WhatsApp 24-hour service window not established")
        if now.tzinfo is None or recent_customer_message_at.tzinfo is None:
            return ExecutionDecision(False, "Timezone-aware message timestamps are required")
        from datetime import timedelta
        delta = now.astimezone(timezone.utc) - recent_customer_message_at.astimezone(timezone.utc)
        if not timedelta(0) <= delta < timedelta(hours=24):
            return ExecutionDecision(False, "WhatsApp 24-hour service window has expired")
    return ExecutionDecision(True, "Policy permits requesting provider-governed execution; provider approval still applies")
