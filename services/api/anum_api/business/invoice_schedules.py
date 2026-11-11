"""Recurring invoices (docs/business.md#recurring-invoices): the schedule rules and drafting each period's invoice once.

A recurring invoice is a schedule (how often, from when, until a date or a number of invoices) and the invoice it
repeats (the client account, an optional project and contact, the lines and the terms). Each period the scheduler
pass drafts one **ordinary draft invoice** through the invoice rules (``invoices.apply_lines``, the same bill-to and
tax-code handling as ``POST /invoices``), carrying ``schedule_id`` and ``period_date``. A partial unique index makes
one invoice per schedule and period, and the schedule row is locked while its invoices are drafted, so two passes
that overlap draft each period once.

Nothing is sent by a schedule. With ``issue_mode: request_approval`` the draft is then sent for an owner's approval
on the same path a person's "Ask for approval" takes (``api_invoices.request_send``): the owner approves the exact
invoice in Approvals, and only then does it get its number. Emailing it is a separate request that also waits for
approval (``invoice_send.request_email``).

Editing changes the invoices made from then on, never one already made. Pausing makes nothing (periods passed while
paused are skipped when it resumes); ending stops it for good. A schedule also ends by itself after its end date or
its number of invoices. The date arithmetic is the recurring bills' (``books_schedule``).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..punctuation import plain_dashes
from ..schemas import TenantContext, new_id
from . import books_schedule
from .books_models import Frequency
from .invoice_models import (
    Invoice,
    InvoiceLine,
    InvoiceSchedule,
    IssueMode,
    ScheduleFrequency,
    ScheduleLine,
    ScheduleStatus,
)
from .invoices import apply_lines
from .models import Currency
from .service import http_error, record_activity
from .store import MAX_SCAN, BusinessStore, Query

SYSTEM_ACTOR = "system:business-invoices"
MAX_PER_RUN = 12  # invoices one schedule drafts in one pass; a longer backlog continues on the next pass
MAX_BACKDATE_DAYS = 92  # the first invoice is at most about three months in the past
UPCOMING = 6


def _clean(text: str | None, limit: int) -> str:
    return plain_dashes(" ".join((text or "").split()))[:limit]


def _block(text: str | None, limit: int) -> str:
    lines = [" ".join(part.split()) for part in plain_dashes(text or "").splitlines()]
    return "\n".join(part for part in lines if part)[:limit]


def _frequency(value: ScheduleFrequency) -> Frequency:
    return Frequency(value.value)


def occurrence(schedule: InvoiceSchedule, index: int) -> date:
    return books_schedule.occurrence(schedule.anchor_date, _frequency(schedule.frequency), schedule.interval, index)


def finished(schedule: InvoiceSchedule, next_date: date) -> str | None:
    """Why no further invoice is due (``"end_date"`` or ``"count"``), or ``None``."""
    if schedule.max_count is not None and schedule.created_count >= schedule.max_count:
        return "count"
    if schedule.end_date is not None and next_date > schedule.end_date:
        return "end_date"
    return None


def upcoming(schedule: InvoiceSchedule, limit: int = UPCOMING) -> list[date]:
    """The next periods still to be drafted (an active schedule only)."""
    if schedule.status != ScheduleStatus.ACTIVE or schedule.next_date is None:
        return []
    out: list[date] = []
    index, count = schedule.next_index, schedule.created_count
    while len(out) < limit:
        day = occurrence(schedule, index)
        if (schedule.end_date is not None and day > schedule.end_date) or (schedule.max_count is not None and count >= schedule.max_count):
            break
        out.append(day)
        index += 1
        count += 1
    return out


def _interval(frequency: ScheduleFrequency, value: int | None) -> int:
    if frequency in (ScheduleFrequency.MONTHLY, ScheduleFrequency.QUARTERLY, ScheduleFrequency.YEARLY):
        return 1
    interval = int(value or 1)
    top = 366 if frequency == ScheduleFrequency.DAYS else 52 if frequency == ScheduleFrequency.WEEKS else 24
    if not 1 <= interval <= top:
        raise http_error(422, "Repeat every 1 to 366 days, 1 to 52 weeks or 1 to 24 months.")
    return interval


def schedule_from(context: TenantContext, store: BusinessStore, payload: dict[str, Any], *, now: datetime, today: date,
                  existing: InvoiceSchedule | None = None) -> InvoiceSchedule:
    """A validated schedule. The account, project and contact are checked as an invoice's are."""
    merged: dict[str, Any] = existing.model_dump() if existing is not None else {}
    merged.update({key: value for key, value in payload.items() if value is not None or key in ("project_id", "contact_id", "end_date", "max_count")})
    if existing is not None and existing.status == ScheduleStatus.ENDED:
        raise http_error(409, "This recurring invoice has ended. Start a new one instead.")
    project = store.get("projects", merged["project_id"]) if merged.get("project_id") else None
    if merged.get("project_id") and project is None:
        raise http_error(404, "Project not found")
    account_id = project.account_id if project is not None else merged.get("account_id")
    if not account_id:
        raise http_error(422, "Choose the client account or the project to invoice.")
    if project is not None and merged.get("account_id") and merged["account_id"] != project.account_id:
        raise http_error(422, "The project belongs to another account.")
    account = store.get("accounts", account_id)
    if account is None:
        raise http_error(404, "Account not found")
    currency = Currency(merged.get("currency") or (project.currency if project is not None else Currency.SAR))
    if project is not None and currency != project.currency:
        raise http_error(422, f"The project is in {project.currency.value}.")
    contact_id = merged.get("contact_id")
    if contact_id:
        contact = store.get("contacts", contact_id)
        if contact is None or contact.account_id != account.id:
            raise http_error(422, "That contact does not belong to the account.")
    lines = [ScheduleLine.model_validate(item) if not isinstance(item, ScheduleLine) else item for item in merged.get("lines") or []]
    lines = [item.model_copy(update={"description": _clean(item.description, 300)}) for item in lines if _clean(item.description, 300)]
    if not lines:
        raise http_error(422, "Add at least one line with a description and a price.")
    if any(item.unit_price <= 0 or item.quantity <= 0 for item in lines):
        raise http_error(422, "Every line needs a quantity and a price above zero.")
    title = _clean(merged.get("title"), 200) or f"{account.name}: recurring invoice"
    frequency = ScheduleFrequency(merged["frequency"])
    interval = _interval(frequency, merged.get("interval"))
    start: date = merged["start_date"]
    end_date: date | None = merged.get("end_date")
    max_count = merged.get("max_count")
    if end_date is not None and end_date < start:
        raise http_error(422, "The end date is on or after the first invoice's date.")
    if max_count is not None and not 1 <= int(max_count) <= 1000:
        raise http_error(422, "A recurring invoice makes 1 to 1,000 invoices.")
    due_in_days = int(merged.get("due_in_days") if merged.get("due_in_days") is not None else store.get_settings().billing.default_due_days)
    if not 0 <= due_in_days <= 365:
        raise http_error(422, "An invoice falls due 0 to 365 days after it is issued.")
    schedule_changed = existing is None or (frequency, interval, start) != (existing.frequency, existing.interval, existing.start_date)
    if schedule_changed and start < today - timedelta(days=MAX_BACKDATE_DAYS):
        raise http_error(422, f"The first invoice is at most {MAX_BACKDATE_DAYS} days in the past.")
    if existing is not None and schedule_changed and existing.created_count and start <= (existing.last_period or start):
        raise http_error(422, "Invoices already made stay as they are: a new schedule starts after the last invoice made "
                              f"({existing.last_period.isoformat() if existing.last_period else '-'}).")
    fields: dict[str, Any] = dict(
        title=title, account_id=account.id, project_id=project.id if project is not None else None, contact_id=contact_id or None,
        currency=currency, lines=lines, due_in_days=due_in_days, customer_reference=_clean(merged.get("customer_reference"), 120),
        notes=_block(merged.get("notes"), 1000), payment_terms=_block(merged.get("payment_terms"), 1000), frequency=frequency, interval=interval,
        start_date=start, end_date=end_date, max_count=int(max_count) if max_count is not None else None,
        issue_mode=IssueMode(merged.get("issue_mode") or IssueMode.DRAFT), updated_by=context.user_id, updated_at=now,
    )
    if existing is None:
        return InvoiceSchedule(id=new_id("binsch"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now,
                               created_by=context.user_id, anchor_date=start, next_index=0, next_date=start, **fields)
    if schedule_changed:
        fields.update(anchor_date=start, next_index=0, next_date=start)
    updated = existing.model_copy(update={**fields, "version": existing.version + 1})
    if updated.status == ScheduleStatus.ACTIVE and updated.next_date is not None and (reason := finished(updated, updated.next_date)):
        updated = end(updated, reason, now, context.user_id)
    return updated


def end(schedule: InvoiceSchedule, reason: str, now: datetime, actor: str) -> InvoiceSchedule:
    return schedule.model_copy(update={"status": ScheduleStatus.ENDED, "next_date": None, "end_reason": reason, "ended_at": now,
                                       "updated_by": actor, "updated_at": now})


def resume(schedule: InvoiceSchedule, *, today: date, now: datetime, actor: str) -> InvoiceSchedule:
    """Active again from the first period on or after ``today``: periods passed while paused are skipped, never drafted late."""
    index = max(schedule.next_index, books_schedule.first_index_on_or_after(schedule.anchor_date, _frequency(schedule.frequency), schedule.interval, today))
    resumed = schedule.model_copy(update={"status": ScheduleStatus.ACTIVE, "next_index": index, "next_date": occurrence(schedule, index),
                                          "updated_by": actor, "updated_at": now, "version": schedule.version + 1})
    if (reason := finished(resumed, resumed.next_date or today)) is not None:
        resumed = end(resumed, reason, now, actor)
    return resumed


def _bill_to(store: BusinessStore, account: Any, contact_id: str | None) -> dict[str, str]:
    contact = store.get("contacts", contact_id) if contact_id else None
    if contact is None or contact.account_id != account.id:
        contacts, _ = store.find("contacts", Query(where={"account_id": account.id}, order_by="created_at", descending=False, limit=50))
        contact = next((item for item in contacts if item.email), None)
    place = ", ".join(part for part in (account.city, account.country) if part)
    return {"bill_to_name": account.name, "bill_to_address": place, "bill_to_email": (contact.email if contact and contact.email else "")}


def invoice_for(store: BusinessStore, schedule: InvoiceSchedule, period: date, now: datetime, actor: str, table: Any) -> Invoice:
    """The draft a schedule makes for one period: its lines priced like any draft's, its tax codes at the period's rates."""
    from .tax_service import coded_lines

    account = store.get("accounts", schedule.account_id)
    if account is None:
        raise http_error(409, "The client account of this recurring invoice was deleted. End it.")
    lines = [InvoiceLine(description=item.description, kind=item.kind, quantity=item.quantity, unit_price=item.unit_price,
                         tax_rate_pct=item.tax_rate_pct, tax_code=item.tax_code) for item in schedule.lines]
    if any(item.tax_code for item in lines):
        lines = coded_lines(lines, table, period)
    invoice = Invoice(
        id=new_id("binv"), tenant_id=schedule.tenant_id, workspace_id=schedule.workspace_id, account_id=account.id, project_id=schedule.project_id,
        currency=schedule.currency, due_in_days=schedule.due_in_days, customer_reference=schedule.customer_reference, notes=schedule.notes,
        payment_terms=schedule.payment_terms, created_by=actor, created_at=now, updated_at=now, schedule_id=schedule.id, period_date=period,
        **_bill_to(store, account, schedule.contact_id),
    )
    return apply_lines(invoice, lines)


def materialize(store: BusinessStore, context: TenantContext, schedule_id: str, *, today: date, now: datetime, table: Any,
                actor: str = SYSTEM_ACTOR, limit: int = MAX_PER_RUN) -> tuple[InvoiceSchedule | None, list[Invoice]]:
    """Draft every invoice of ``schedule_id`` due by ``today`` (at most ``limit``), each once, under a lock on the schedule.

    A period whose invoice already exists (another pass drafted it) is skipped, never drafted twice. A period that
    cannot be drafted (the account was deleted, a tax code has no rate) stops the schedule's run and is kept in
    ``last_error``; the next pass tries again."""
    from fastapi import HTTPException

    from .tax_rules import TaxRuleError

    schedule: InvoiceSchedule | None = store.get("invoice_schedules", schedule_id, for_update=True)
    if schedule is None or schedule.status != ScheduleStatus.ACTIVE or schedule.next_date is None:
        return schedule, []
    made: list[Invoice] = []
    current = schedule
    error = ""
    while current.next_date is not None and current.next_date <= today and len(made) < limit:
        reason = finished(current, current.next_date)
        if reason:
            current = end(current, reason, now, actor)
            break
        period = current.next_date
        existing = store.first("invoices", schedule_id=current.id, period_date=period)
        if existing is None:
            try:
                invoice = store.add("invoices", invoice_for(store, current, period, now, actor, table))
            except HTTPException as exc:
                error = str(exc.detail)
                break
            except TaxRuleError as exc:
                error = exc.message
                break
            made.append(invoice)
            record_activity(store, context, "invoice_drafted",
                            f"Recurring invoice “{current.title}” drafted the invoice for {period.isoformat()} ({invoice.currency.value} {invoice.total}).",
                            account_id=invoice.account_id, project_id=invoice.project_id,
                            metadata={"invoice_id": invoice.id, "schedule_id": current.id, "period": period.isoformat()},
                            idempotency_key=f"schedule-draft:{current.id}:{period.isoformat()}")
        index = current.next_index + 1
        current = current.model_copy(update={
            "next_index": index, "next_date": occurrence(current, index), "created_count": current.created_count + 1, "last_period": period,
            "last_invoice_id": (made[-1].id if made and made[-1].period_date == period else (existing.id if existing is not None else current.last_invoice_id)),
            "updated_at": now,
        })
    if current.status == ScheduleStatus.ACTIVE and current.next_date is not None and (reason := finished(current, current.next_date)):
        current = end(current, reason, now, actor)
    if error or current is not schedule or schedule.last_error:
        current = current.model_copy(update={"last_error": error[:500], "version": schedule.version + 1, "updated_at": now})
        store.save("invoice_schedules", current)
    return current, made


def history(store: BusinessStore, schedule_id: str) -> list[Invoice]:
    items, _ = store.find("invoices", Query(where={"schedule_id": schedule_id}, order_by="created_at", descending=True, limit=MAX_SCAN))
    return items


__all__ = ["MAX_PER_RUN", "SYSTEM_ACTOR", "end", "finished", "history", "invoice_for", "materialize", "occurrence", "resume", "schedule_from", "upcoming"]
