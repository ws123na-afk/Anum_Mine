"""Budget alerts at 50, 75 and 90 percent (docs/model-gateway.md#budget-alerts).

When a model call takes the organisation's or a workspace's monthly AI budget, or one
agent's monthly cap (docs/agent-controls.md), past 50, 75, 90 or 100 percent, every active
owner of the workspace the call was made in gets an in-app notification (kind
``budget_alert``): which budget, how much of it is used and when it resets. In-app only:
never an email and never a phone push (``push.IN_APP_ONLY_KINDS``), because no approved
channel for budget mail exists. A crossing is found by comparing the usage before and after
one call, so each threshold notifies once per month and limit; a failed notice is logged and
never fails the model call.
"""

from __future__ import annotations

import logging
from datetime import date

from .schemas import TenantContext

logger = logging.getLogger(__name__)

ALERT_PERCENTS = (50, 75, 90, 100)
NOTICE_KIND = "budget_alert"


def crossed(before: float, after: float, limit: float | None) -> list[int]:
    """The alert percents a change from ``before`` to ``after`` passes (``limit`` > 0)."""
    if not limit or limit <= 0:
        return []
    return [p for p in ALERT_PERCENTS if before < p / 100 * limit <= after]


def _amount(kind: str, limit: float) -> str:
    return f"USD {limit:,.2f}" if kind == "cost" else f"{int(limit):,} tokens"


def notice_text(subject: str, percent: int, kind: str, limit: float, resets_on: date | None) -> tuple[str, str]:
    what = "cost" if kind == "cost" else "token"
    title = f"{subject} has used {percent}% of its monthly AI {what} budget"
    reset = f" It resets on {resets_on.isoformat()} (UTC)." if resets_on else ""
    if percent >= 100:
        body = f"{subject} has used all of its {_amount(kind, limit)} budget, so AI use for it stops until it resets.{reset} An owner can raise it."
    else:
        body = f"{subject} has used {percent}% of its {_amount(kind, limit)} budget this month.{reset} AI use stops at 100%."
    return title, body


def notify_owners(context: TenantContext, title: str, body: str, target: str | None = None) -> int:
    """An in-app notification for every active owner of the call's workspace. Best effort."""
    from .notifications import create_notification
    from .team_work import open_team_unit

    sent = 0
    try:
        with open_team_unit(context) as unit:
            owners = [m.user_id for m in unit.repository.list_memberships(context) if m.active and m.role.lower() == "owner"]
            for user_id in owners:
                notification = create_notification(
                    unit.store, context, user_id=user_id, kind=NOTICE_KIND, title=title, body=body, work_item_id=None, in_app_only=True
                )
                from .events import CanonicalEventName, create_event

                unit.repository.record_event(create_event(
                    CanonicalEventName.NOTIFICATION_CREATED, context, notification.id,
                    {"recipient": user_id, "kind": NOTICE_KIND, "work_item_id": None},
                ).event)
                sent += 1
    except Exception:  # noqa: BLE001 - the model call already happened
        logger.warning("budget_alert_failed", extra={"anum_budget_alert": {"target": (target or "")[:80]}})
        return 0
    return sent


def alert_scope(context: TenantContext, scope: str, kind: str, percent: int, limit: float, resets_on: date | None) -> int:
    subject = "Your organization" if scope == "tenant" else "This workspace"
    title, body = notice_text(subject, percent, kind, limit, resets_on)
    return notify_owners(context, title, body, f"{scope}:{kind}:{percent}")


def alert_agent(context: TenantContext, agent_name: str, kind: str, percent: int, limit: float, resets_on: date | None) -> int:
    title, body = notice_text(f"The agent {agent_name}", percent, kind, limit, resets_on)
    return notify_owners(context, title, body, f"agent:{kind}:{percent}")
