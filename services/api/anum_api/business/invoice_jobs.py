"""The Business invoice pass of the scheduler (docs/business.md#recurring-invoices, #reminders-planned-by-the-daily-pass).

Registered as a pass of the automation scheduler (``automation.register_scheduler_pass``), so it runs in the API
process next to the delivery tools, on every replica, in a worker thread. Each pass:

* **Recurring invoices.** Drafts every period that is due (``invoice_schedules.materialize``, one unit of work per
  schedule with its row locked) and, for a schedule whose ``issue_mode`` is ``request_approval``, asks an owner to
  approve sending each new draft on the path a person's request takes (``api_invoices.request_send``). Nothing is
  sent: the owner approves the exact invoice in Approvals.
* **Planned emails, once a day per workspace.** Where the owner turned them on (``invoice_automation``), it plans the
  payment reminders of overdue invoices and the email of an invoice a schedule made once it is sent, through
  ``invoice_send.request_email``: each one is a delivery task that waits for an owner's approval of its exact
  payload, and a workspace that would let the email tool send without asking is refused. A reminder step is asked
  for once per invoice (``schedule_step``); an invoice's account or contact marked do not contact is skipped, and the
  delivery path refuses an address on the opt-out list. The day is claimed under a lock on the workspace's row
  (``last_run_on``), so two replicas never plan the same day twice.

Workspaces are discovered as ``anum_maintenance``, which may read only the scope, status and next date of active
schedules and the scope and switches of the automation rows (migration ``0174_business_depth``); the work runs as
the application role inside each workspace's own RLS context. Nothing bypasses row level security. The counts it
returns are all it reports.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from .invoice_models import Invoice, InvoiceAutomation, InvoiceStatus, IssueMode, MessageKind, MessageStatus
from .invoice_schedules import SYSTEM_ACTOR, materialize
from .invoice_send import LIVE, MAX_REMINDERS, REMINDER_DAYS, days_overdue, messages_of, refresh_messages
from .invoice_service import business_day
from .service import record_activity
from .store import MAX_SCAN, Query, business_store, open_business_store

logger = logging.getLogger("anum_api.business.invoice_jobs")

_DISCOVER_SCHEDULES = text(
    "select distinct tenant_id, workspace_id from business_invoice_schedules "
    "where status = 'active' and next_date is not null and next_date <= :today order by 1, 2"
)
_DISCOVER_AUTOMATION = text(
    "select tenant_id, workspace_id from business_invoice_automation "
    "where (auto_reminders or email_scheduled) and (last_run_on is null or last_run_on < :today) order by 1, 2"
)


@dataclass
class PassResult:
    workspaces: int = 0
    invoices_drafted: int = 0
    sent_for_approval: int = 0
    approval_failed: int = 0
    reminders_planned: int = 0
    emails_planned: int = 0
    skipped_do_not_contact: int = 0
    failed: int = 0


def system_context(tenant_id: str, workspace_id: str) -> TenantContext:
    """The pass acts as a member of the workspace (it may draft and ask; only an owner approves)."""
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_ACTOR, roles=["member"])


def _archived(context: TenantContext) -> bool:
    from ..dependencies import workspace_archived

    return bool(workspace_archived(context))


def _units() -> Any:
    from ..dependencies import _unit_of_work

    return functools.partial(_unit_of_work, require_membership=False)


def discover_schedules(today: date) -> list[tuple[str, str]]:
    if settings.repository_backend == "memory":
        return sorted({(item.tenant_id, item.workspace_id) for item in business_store._rows["invoice_schedules"].values()  # type: ignore[attr-defined]
                       if item.status == "active" and item.next_date is not None and item.next_date <= today})
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1]) for row in discover(default_session_factory, _DISCOVER_SCHEDULES, {"today": today})]


def discover_automation(today: date) -> list[tuple[str, str]]:
    if settings.repository_backend == "memory":
        return sorted({(item.tenant_id, item.workspace_id) for item in business_store._rows["invoice_automation"].values()  # type: ignore[attr-defined]
                       if (item.auto_reminders or item.email_scheduled) and (item.last_run_on is None or item.last_run_on < today)})
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1]) for row in discover(default_session_factory, _DISCOVER_AUTOMATION, {"today": today})]


# -- recurring invoices ---------------------------------------------------------------------------------


def send_for_approval(context: TenantContext, invoice_id: str, repository: Any = None) -> str | None:
    """Ask an owner to approve sending a draft, exactly as a person's "Ask for approval" does. The refusal in plain words, or None."""
    from fastapi import HTTPException

    from .api_invoices import request_send

    try:
        if repository is not None:
            request_send(invoice_id, context=context, repository=repository)
        else:
            with _units()(context) as detached:
                request_send(invoice_id, context=context, repository=detached)
    except HTTPException as exc:
        return str(exc.detail)[:500]
    return None


def note_failure(context: TenantContext, schedule_id: str, invoice: Invoice, reason: str, now: datetime) -> None:
    """The draft waits for a person: say why on the schedule and in the account's activity."""
    with open_business_store(context) as store:
        schedule = store.get("invoice_schedules", schedule_id, for_update=True)
        if schedule is not None:
            store.save("invoice_schedules", schedule.model_copy(update={"last_error": f"Invoice for {invoice.period_date}: {reason}"[:500], "updated_at": now}))
        record_activity(store, context, "invoice_schedule_waits", f"A recurring invoice draft waits for a person: {reason}"[:500],
                        account_id=invoice.account_id, project_id=invoice.project_id, metadata={"invoice_id": invoice.id, "schedule_id": schedule_id},
                        idempotency_key=f"schedule-waits:{invoice.id}")


def run_schedule(context: TenantContext, schedule_id: str, *, today: date, now: datetime, table: Any, repository: Any = None,
                 result: PassResult | None = None) -> list[Invoice]:
    """Draft one schedule's due invoices and, when it says so, send each for approval. Returns the drafts made."""
    result = result or PassResult()
    with open_business_store(context) as store:
        schedule, made = materialize(store, context, schedule_id, today=today, now=now, table=table, actor=context.user_id)
    result.invoices_drafted += len(made)
    if schedule is None or schedule.issue_mode != IssueMode.REQUEST_APPROVAL:
        return made
    for invoice in made:
        reason = send_for_approval(context, invoice.id, repository)
        if reason is None:
            result.sent_for_approval += 1
        else:
            result.approval_failed += 1
            note_failure(context, schedule_id, invoice, reason, now)
    return made


def run_schedules(result: PassResult, *, today: date, now: datetime) -> None:
    from .tax_service import table_for

    for tenant_id, workspace_id in discover_schedules(today):
        context = system_context(tenant_id, workspace_id)
        try:
            if _archived(context):
                continue
            result.workspaces += 1
            table = table_for(context)
            with open_business_store(context) as store:
                day = business_day(store, now)  # the business's own day (its time zone), as the invoice routes use
                page, _ = store.find("invoice_schedules", Query(where={"status": "active"}, order_by="id", descending=False, limit=MAX_SCAN))
            for schedule_id in [item.id for item in page if item.next_date is not None and item.next_date <= day]:
                run_schedule(context, schedule_id, today=day, now=now, table=table, result=result)
        except Exception as exc:  # noqa: BLE001 - one workspace's failure never stops the pass
            logger.warning("business_invoice_schedules_failed error_class=%s", type(exc).__name__)
            result.failed += 1


# -- planned emails ---------------------------------------------------------------------------------------


def due_step(invoice: Invoice, today: date) -> int:
    """The reminder step the schedule has reached (0 to 4): one per threshold of ``REMINDER_DAYS`` passed."""
    late = days_overdue(invoice, today)
    return sum(1 for days in REMINDER_DAYS if late >= days)


def _do_not_contact(store: Any, invoice: Invoice) -> bool:
    account = store.get("accounts", invoice.account_id)
    if account is None or account.do_not_contact:
        return True
    address = (invoice.bill_to_email or "").strip().lower()
    if not address:
        return False
    contacts, _ = store.find("contacts", Query(where={"account_id": invoice.account_id}, order_by="created_at", descending=False, limit=200))
    return any(item.do_not_contact and (item.email or "").strip().lower() == address for item in contacts)


@dataclass
class Plan:
    invoice_id: str
    reminder: bool
    step: int | None = None


def plans_for(store: Any, context: TenantContext, automation: InvoiceAutomation, today: date, result: PassResult, repository: Any = None) -> list[Plan]:
    """What to plan today in one workspace: emails of sent scheduled invoices and the reminder steps reached. The earlier
    requests' statuses are brought up to date from their approvals first (``refresh_messages``), so a decided one counts."""
    invoices, _ = store.find("invoices", Query(where={"status": InvoiceStatus.SENT}, order_by="created_at", descending=False, limit=MAX_SCAN))
    plans: list[Plan] = []
    for invoice in invoices:
        messages = refresh_messages(store, repository, context, invoice.id) if repository is not None else messages_of(store, invoice.id)
        wants_email = automation.email_scheduled and invoice.schedule_id is not None and not any(item.kind == MessageKind.INVOICE for item in messages)
        step = due_step(invoice, today) if automation.auto_reminders and invoice.balance_due() > 0 else 0
        wants_reminder = False
        if step:
            reminders = [item for item in messages if item.kind == MessageKind.REMINDER]
            step_day = (invoice.due_date or today) + timedelta(days=REMINDER_DAYS[step - 1])
            wants_reminder = not (
                any(item.schedule_step == step for item in reminders)  # this step was planned before (whatever came of it)
                or any(item.status == MessageStatus.WAITING_APPROVAL for item in reminders)  # one waits already
                or any(item.status in LIVE and item.created_at.date() >= step_day for item in reminders)  # a person asked for this step
                or sum(1 for item in reminders if item.status in LIVE) >= MAX_REMINDERS
            )
        if not (wants_email or wants_reminder):
            continue
        if _do_not_contact(store, invoice):
            result.skipped_do_not_contact += 1
            continue
        if wants_email:
            plans.append(Plan(invoice.id, reminder=False))
        elif wants_reminder:
            plans.append(Plan(invoice.id, reminder=True, step=step))
    return plans


def claim_day(context: TenantContext, today: date, now: datetime) -> InvoiceAutomation | None:
    """Take today's run for the workspace under a lock on its row: None when another replica (or an earlier pass) has it."""
    with open_business_store(context) as store:
        found = store.first("invoice_automation")
        if found is None:
            return None
        row: InvoiceAutomation | None = store.get("invoice_automation", found.id, for_update=True)
        if row is None or not (row.auto_reminders or row.email_scheduled) or (row.last_run_on is not None and row.last_run_on >= today):
            return None
        return store.save("invoice_automation", row.model_copy(update={"last_run_on": today, "last_run_at": now, "updated_at": now}))


async def _plan_all(context: TenantContext, plans: list[Plan], language: Any, result: PassResult, notes: list[str]) -> None:
    from fastapi import HTTPException

    from .invoice_send import request_email

    units = _units()
    for plan in plans:
        try:
            message = await request_email(context, units, plan.invoice_id, to=None, language=language, reminder=plan.reminder, step=plan.step)
        except HTTPException as exc:
            result.failed += 1
            notes.append(str(exc.detail))
            continue
        if message.status == MessageStatus.BLOCKED:
            result.failed += 1
            notes.append(message.detail or "The send was blocked (the address may be on the opt-out list).")
        elif plan.reminder:
            result.reminders_planned += 1
        else:
            result.emails_planned += 1


def run_workspace_day(context: TenantContext, *, today: date, now: datetime, result: PassResult | None = None, force: bool = False) -> dict[str, Any] | None:
    """Plan one workspace's emails for today. ``force`` (the owner's "Run now") ignores today's claim but never plans a step twice."""
    result = result or PassResult()
    if force:
        with open_business_store(context) as store:
            automation = store.first("invoice_automation")
    else:
        automation = claim_day(context, today, now)
    if automation is None:
        return None
    before = PassResult(**asdict(result))
    with _units()(context) as repository, open_business_store(context) as store:
        plans = plans_for(store, context, automation, today, result, repository)
    notes: list[str] = []
    if plans:
        asyncio.run(_plan_all(context, plans, automation.language, result, notes))
    summary: dict[str, Any] = {key: getattr(result, key) - getattr(before, key)
                               for key in ("reminders_planned", "emails_planned", "skipped_do_not_contact", "failed")}
    summary["note"] = notes[0][:300] if notes else ""
    with open_business_store(context) as store:
        row = store.get("invoice_automation", automation.id, for_update=True)
        if row is not None:
            store.save("invoice_automation", row.model_copy(update={"last_run_on": today, "last_run_at": now, "last_run_summary": summary, "updated_at": now}))
    return summary


def run_automation(result: PassResult, *, today: date, now: datetime) -> None:
    for tenant_id, workspace_id in discover_automation(today):
        context = system_context(tenant_id, workspace_id)
        try:
            if _archived(context):
                continue
            result.workspaces += 1
            with open_business_store(context) as store:
                day = business_day(store, now)
            run_workspace_day(context, today=day, now=now, result=result)
        except Exception as exc:  # noqa: BLE001 - the next day's pass tries again
            logger.warning("business_invoice_automation_failed error_class=%s", type(exc).__name__)
            result.failed += 1


def run(*, now: datetime | None = None) -> dict[str, int]:
    moment = now or utc_now()
    result = PassResult()
    # Discovery reads the UTC day; the work then uses the business's own day, so a business east of UTC is served once
    # both have turned (a few hours late at most), and a pass never rediscovers work that is not due yet.
    run_schedules(result, today=moment.date(), now=moment)
    run_automation(result, today=moment.date(), now=moment)
    return asdict(result)


def run_due_invoice_work() -> int:
    """The scheduler pass: how many invoices were drafted, sent for approval or emails planned."""
    outcome = run()
    return outcome["invoices_drafted"] + outcome["sent_for_approval"] + outcome["reminders_planned"] + outcome["emails_planned"]


def automation_row(context: TenantContext, *, actor: str, now: datetime) -> InvoiceAutomation:
    """The workspace's row (a new, switched-off one when there is none yet; not stored)."""
    with open_business_store(context) as store:
        found = store.first("invoice_automation")
    return found or InvoiceAutomation(id=new_id("binauto"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now,
                                      updated_at=now, updated_by=actor)


__all__ = ["PassResult", "automation_row", "due_step", "plans_for", "run", "run_due_invoice_work", "run_schedule", "run_workspace_day",
           "send_for_approval", "system_context"]
