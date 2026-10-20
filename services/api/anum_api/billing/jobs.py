"""Recurring billing work: trial ends, renewals, dunning and read-only (docs/billing.md#lifecycle).

Follows the cross-tenant maintenance pattern (``anum_api.maintenance``): organisations with
a live subscription are **discovered** as ``anum_maintenance`` (migration
``0094_customer_billing`` grants it ids, statuses and dates of live subscriptions only),
then each organisation is handled as the application role inside its own tenant context,
in its own short transaction. Reminder emails are planned afterwards, outside that
transaction, through the delivery path: they wait for an approval like every external send.

``ANUM_BILLING_SCHEDULER_ENABLED=true`` runs a pass every ``ANUM_BILLING_POLL_SECONDS``
(an hour by default); the platform console's **Run billing now** runs one at once. A pass
is idempotent, so replicas running it together issue nothing twice: the subscription row
is locked for the pass and every step checks the state it moves from.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from ..schemas import TenantContext, utc_now
from ..settings import settings
from . import service as svc
from .enforce import clear_cache
from .rows import memory_db, open_billing

logger = logging.getLogger("anum.billing")
SYSTEM_USER = "system:billing"
_DUE = (
    "select distinct tenant_id, issuer_tenant_id from billing_subscriptions "
    "where status in ('trialing', 'active', 'past_due') order by tenant_id"
)


def live_tenants(issuer_tenant_id: str | None = None) -> list[tuple[str, str]]:
    if settings.repository_backend == "postgresql":
        from .rows import reader_rows

        found = [(r["tenant_id"], r["issuer_tenant_id"]) for r in reader_rows("anum_maintenance", {}, _DUE)]
    else:
        found = sorted({(r["tenant_id"], r["issuer_tenant_id"]) for r in memory_db.all_rows("billing_subscriptions")
                        if r["status"] in ("trialing", "active", "past_due")})
    return [item for item in found if issuer_tenant_id is None or item[1] == issuer_tenant_id]


def run_tenant_once(tenant_id: str, now: datetime) -> svc.PassResult:
    with open_billing(tenant_id) as rows:
        result = svc.run_tenant(rows, now=now, actor=SYSTEM_USER)
    if result.transitions:
        clear_cache(tenant_id)
    return result


def _send_reminder(reminder: svc.Reminder) -> dict[str, Any]:
    """Plan one reminder email in the issuer's sending workspace; it waits for an approval."""
    from .. import delivery
    from ..dependencies import _unit_of_work
    from .api import request_invoice_email

    with open_billing(reminder.tenant_id) as rows:
        issuer = svc.issuer_settings(rows, reminder.issuer_tenant_id)
        invoice = svc.get_invoice(rows, reminder.invoice_id)
    workspace = issuer.get("sending_workspace_id")
    if invoice is None or not reminder.to or not workspace:
        entry = {"status": "not_sent", "reason": "no billing contact email or no sending workspace", "reminder": reminder.reminder}
    else:
        context = TenantContext(tenant_id=reminder.issuer_tenant_id, workspace_id=workspace, user_id=SYSTEM_USER, roles=["member"])
        try:
            with _unit_of_work(context, require_membership=False) as repository:
                outcome = asyncio.run(request_invoice_email(context, repository, reminder.tenant_id, invoice, reminder.to, reminder=reminder.reminder))
            entry = {"status": outcome.status, "approval_id": outcome.approval_id, "task_id": outcome.task_id, "reminder": reminder.reminder}
        except delivery.DeliveryUnavailableError:
            entry = {"status": "not_sent", "reason": "no email integration in the sending workspace", "reminder": reminder.reminder}
    entry["requested_at"] = utc_now().isoformat()
    entry["requested_by"] = SYSTEM_USER
    queued = entry["status"] in ("waiting_approval", "sent")
    with open_billing(reminder.tenant_id) as rows:
        rows.update("billing_invoices", {"id": reminder.invoice_id}, {"last_email": entry, "updated_at": utc_now()})
        if queued:
            # Only a reminder that is queued for approval or sent counts; anything else is retried.
            svc.mark_reminder_sent(rows, reminder.invoice_id, reminder.reminder, utc_now())
    return entry


async def run_once(*, issuer_tenant_id: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """One pass over every organisation with a live subscription (of one issuer when given)."""
    now = now or utc_now()
    totals = {"organisations": 0, "invoices": 0, "reminders": 0, "read_only": 0, "failed": 0}
    for tenant_id, _ in await asyncio.to_thread(live_tenants, issuer_tenant_id):
        totals["organisations"] += 1
        try:
            result = await asyncio.to_thread(run_tenant_once, tenant_id, now)
        except Exception:
            totals["failed"] += 1
            logger.exception("billing_pass_failed", extra={"anum_billing": {"tenant": tenant_id}})
            continue
        totals["invoices"] += len(result.invoices)
        totals["read_only"] += result.transitions.count("read_only")
        for reminder in result.reminders:
            try:
                await asyncio.to_thread(_send_reminder, reminder)
                totals["reminders"] += 1
            except Exception:
                totals["failed"] += 1
                logger.exception("billing_reminder_failed", extra={"anum_billing": {"tenant": tenant_id}})
    return totals


class BillingScheduler:
    def __init__(self, poll_interval: float) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-billing")

    async def stop(self, timeout: float = 30.0) -> None:
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                # The organisation in progress finishes its own short transaction.
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except Exception:
                logger.exception("Billing scheduler stopped with an error")

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Billing scheduler started without a stop event")
        while not stop.is_set():
            try:
                await run_once()
            except Exception:
                logger.exception("Billing pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_billing_scheduler() -> BillingScheduler | None:
    if not settings.billing_scheduler_enabled:
        return None
    return BillingScheduler(settings.billing_poll_seconds)
