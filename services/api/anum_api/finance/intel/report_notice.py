"""The in-app notice that a post-close trading report is ready (docs/finance-trading-intel.md#daily-report).

Uses the existing notification path (``notifications.create_notification`` through a team unit): the
row is the in-app inbox entry (web bell, phone Notifications screen), the ``notification.created``
event refreshes the person's clients and triggers phone push where it is on, and email follows the
person's own email preference over the server's existing SMTP settings (no new credentials).

The text holds counts only: no symbol, price, amount or recorded reason, because email and push
leave ANUM. The full report stays behind ``finance:read`` in the app. A failure here is logged and
never undoes the cycle, which has already been stored.
"""

from __future__ import annotations

import logging
from typing import Any

from ...schemas import TenantContext

logger = logging.getLogger("anum_api.finance.intel")

KIND = "finance_daily_report"


def notice_text(result: dict[str, Any]) -> tuple[str, str]:
    """Title and body for one cycle's result (counts only, SIMULATED, never a promise)."""
    title = "Daily trading report ready (SIMULATED)"
    if result.get("halted"):
        body = ("Today's post-close cycle was halted, so nothing was evaluated or proposed. "
                "Open Finance, Trading intelligence, Daily reports to see why. No real order was placed.")
        return title, body
    body = (f"After the close. Active paper strategies: {int(result.get('active_strategies', 0))}. "
            f"SIMULATED orders proposed for your approval: {int(result.get('proposed_orders', 0))}. "
            f"Signals refused: {int(result.get('refused_signals', 0))}. Strategies suspended: {int(result.get('suspended', 0))}. "
            "Open Finance, Trading intelligence, Daily reports for the details. No real order was placed.")
    return title, body


def notify_report_ready(context: TenantContext, result: dict[str, Any]) -> bool:
    """Notify the person whose cycle wrote a report. True when the notice was stored."""
    if result.get("skipped") or not result.get("report_id") or not context.user_id:
        return False
    from ...notifications import deliver_pending_email
    from ...schemas import utc_now
    from ...team_work import open_team_unit
    from .commentary import write_commentary

    # Model commentary first, so the notice arrives when the report is complete. Best effort:
    # without a model, or past the budget cap, the report stands alone (docs/finance-trading-intel.md#model-commentary).
    try:
        write_commentary(context, str(result["report_id"]), now=utc_now())
    except Exception:
        logger.exception("Finance report commentary failed", extra={"anum_report_id": result.get("report_id")})
    title, body = notice_text(result)
    try:
        with open_team_unit(context) as unit:
            unit.notify(context.user_id, KIND, title, body, None)
            pending = list(unit.pending_email)
    except Exception:  # the report is stored either way; the notice is best effort
        logger.exception("Finance daily report notice failed", extra={"anum_report_id": result.get("report_id")})
        return False
    if pending:
        deliver_pending_email(context, pending)
    return True
