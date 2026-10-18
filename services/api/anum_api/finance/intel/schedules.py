"""Scheduled Supervisor cycles through the existing automation engine (docs/finance-trading-intel.md#scheduled-operations).

A schedule is an ordinary automation workflow with one ``finance.intel.cycle`` step and an
automation schedule (cron and IANA time zone), so it shows in the Workbench like any other.
When it fires, the engine commits the run and then calls :func:`run_cycle_step` outside its
transaction, which re-checks that the schedule's creator is still an active member with
``finance:manage``, acts as that person (their own owner-scoped finance rows only) and runs
one Supervisor phase. The cycle itself is idempotent per person, phase and day, so a
retried or duplicated fire never runs twice and never doubles an order request. The cycle
checks each venue's calendar: on a day the venue does not trade it records "market_closed".
"""

from __future__ import annotations

import logging
from typing import Any

from ... import automation
from ...authorization import Permission
from ...dependencies import has_permission, lookup_membership
from ...schemas import TenantContext, utc_now
from ..store import open_finance_store
from .supervisor import PHASES, Supervisor

logger = logging.getLogger("anum.finance.intel.schedules")

STEP_ACTION = "finance.intel.cycle"
DEFAULT_TIMES = {"pre_open": "08:30", "intraday": "12:00", "post_close": "17:30", "weekly": "18:00", "monthly": "07:00"}


def check_step_input(step_input: dict[str, Any]) -> str | None:
    """Why the step's input is not valid, or ``None``."""
    phase = step_input.get("phase")
    if phase not in PHASES:
        return f'the phase must be one of: {", ".join(PHASES)}.'
    return None


def acting_context(tenant_id: str, workspace_id: str, user_id: str | None) -> TenantContext | None:
    """The schedule's creator with their current role, if they may still manage finance."""
    if not user_id or user_id.startswith("system:"):
        return None
    probe = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=[])
    membership = lookup_membership(probe)
    if membership is None or not getattr(membership, "active", False):
        return None
    role = getattr(membership.role, "value", membership.role)
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=[str(role)])
    return context if has_permission(context, Permission.FINANCE_MANAGE) else None


def run_cycle_step(run: automation.WorkflowRun, step: automation.RunStepState, step_input: dict[str, Any]) -> dict[str, Any] | None:
    phase = step_input.get("phase")
    if phase not in PHASES:
        logger.warning("Finance intel schedule %s skipped: unknown phase", run.schedule_id)
        return None
    context = acting_context(run.tenant_id, run.workspace_id, run.created_by)
    if context is None:
        logger.warning("Finance intel schedule %s skipped: its creator may no longer manage finance", run.schedule_id)
        return None
    with open_finance_store(context) as store:
        return Supervisor(store, context, utc_now()).run(str(phase))


automation.register_step_handler(STEP_ACTION, run_cycle_step)
