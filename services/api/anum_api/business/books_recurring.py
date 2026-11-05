"""Recurring bills (docs/business-books.md#recurring-bills): the template rules and making each due bill exactly once.

A recurring bill is a template of a vendor bill (vendor, what it is for, net and tax, category, section,
cost centre, project, useful life) with a schedule. Each period it makes one **normal bill** (``open``,
or ``draft`` when the template says so), dated on the period with the due date offset copied. The
bill carries ``template_id`` and ``period_date``; a partial unique index makes one bill per template
and period, and the template row is locked while its bills are made, so two runs that overlap (the
daily job twice, or the job and a member saving the template) make each bill once.

Editing a template changes the bills it makes from then on, never one already made. Pausing makes
nothing (periods passed while paused are skipped when it resumes); ending stops it for good. A
schedule also ends on its own after its end date or its number of bills.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..schemas import TenantContext, new_id
from . import books_schedule as schedule
from .books_models import Bill, BillStatus, BillTemplate, Frequency, TemplateStatus
from .books_service import BooksError, bill_from
from .books_store import BooksStore, DuplicateError

SYSTEM_ACTOR = "system:books"
MAX_PER_RUN = 60  # bills one template makes in one run; a longer backlog continues on the next run
MAX_BACKDATE_DAYS = 366


def template_from(context: TenantContext, store: BooksStore, payload: dict[str, Any], *, now: datetime, today: date,
                  existing: BillTemplate | None = None) -> BillTemplate:
    """A validated template. The bill fields are checked exactly as a bill's are (``books_service.bill_from``)."""
    frequency = Frequency(payload["frequency"])
    interval = int(payload.get("interval") or 1)
    if frequency in (Frequency.MONTHLY, Frequency.QUARTERLY, Frequency.YEARLY):
        interval = 1
    elif not 1 <= interval <= (366 if frequency == Frequency.DAYS else 52 if frequency == Frequency.WEEKS else 24):
        raise BooksError(422, "Repeat every 1 to 366 days, 1 to 52 weeks or 1 to 24 months.", "invalid_interval")
    start: date = payload["start_date"]
    due_days = int(payload.get("due_days") or 0)
    if not 0 <= due_days <= 366:
        raise BooksError(422, "A bill falls due 0 to 366 days after its date.", "invalid_due_days")
    end_date: date | None = payload.get("end_date")
    max_count = payload.get("max_count")
    if end_date is not None and end_date < start:
        raise BooksError(422, "The end date is on or after the first bill's date.", "invalid_end_date")
    if max_count is not None and not 1 <= int(max_count) <= 1000:
        raise BooksError(422, "A recurring bill makes 1 to 1,000 bills.", "invalid_count")
    create_as = payload.get("create_as") or "open"
    if create_as not in ("open", "draft"):
        raise BooksError(422, "New bills are made open or as drafts.", "invalid_create_as")
    schedule_changed = existing is None or (frequency, interval, start) != (existing.frequency, existing.interval, existing.start_date)
    if schedule_changed and start < today - timedelta(days=MAX_BACKDATE_DAYS):
        raise BooksError(422, "The first bill is at most a year in the past.", "start_too_old")
    if existing is not None and schedule_changed and existing.created_count and start <= (existing.last_period or start):
        raise BooksError(422, "Bills already made stay as they are: a new schedule starts after the last bill made.", "start_before_last_bill",
                         last_period=existing.last_period.isoformat() if existing.last_period else None)
    # The bill fields, checked exactly as a bill's (vendor, amounts, category, section, cost centre, project, useful life).
    # An archived vendor or cost centre the template already uses stays allowed, as on a bill.
    probe = bill_from(context, store, {**payload, "bill_date": start, "due_date": start + timedelta(days=due_days), "recurrence": None},
                      now=now, idem_key=None, existing=bill_for(existing, start, now, context.user_id) if existing is not None else None)
    fields: dict[str, Any] = dict(
        vendor_id=probe.vendor_id, reference=probe.reference, description=probe.description, currency=probe.currency, subtotal=probe.subtotal, tax=probe.tax,
        total=probe.total, category=probe.category, section=probe.section, cost_centre_id=probe.cost_centre_id, project_id=probe.project_id,
        useful_life_months=probe.useful_life_months, frequency=frequency, interval=interval, start_date=start, due_days=due_days, end_date=end_date,
        max_count=int(max_count) if max_count is not None else None, create_as=create_as, updated_by=context.user_id, updated_at=now,
    )
    if existing is None:
        return BillTemplate(id=new_id("btpl"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, created_by=context.user_id,
                            anchor_date=start, next_index=0, next_date=start, **fields)
    if existing.status == TemplateStatus.ENDED:
        raise BooksError(409, "This recurring bill has ended. Start a new one instead.", "template_ended")
    if schedule_changed:
        fields.update(anchor_date=start, next_index=0, next_date=start)
    return existing.model_copy(update={**fields, "version": existing.version + 1})


def _end(template: BillTemplate, reason: str, now: datetime) -> BillTemplate:
    return template.model_copy(update={"status": TemplateStatus.ENDED, "next_date": None, "end_reason": reason, "ended_at": now, "updated_at": now})


def bill_for(template: BillTemplate, period: date, now: datetime, actor: str) -> Bill:
    """The normal bill a template makes for one period: its fields copied, dated on the period."""
    return Bill(
        id=new_id("bill"), tenant_id=template.tenant_id, workspace_id=template.workspace_id, created_at=now, vendor_id=template.vendor_id,
        reference=template.reference, description=template.description, bill_date=period, due_date=period + timedelta(days=template.due_days),
        currency=template.currency, subtotal=template.subtotal, tax=template.tax, total=template.total, category=template.category, section=template.section,
        cost_centre_id=template.cost_centre_id, project_id=template.project_id, useful_life_months=template.useful_life_months,
        status=BillStatus.DRAFT if template.create_as == "draft" else BillStatus.OPEN, created_by=actor, updated_at=now,
        template_id=template.id, period_date=period,
    )


def materialize(store: BooksStore, template_id: str, *, today: date, now: datetime, actor: str = SYSTEM_ACTOR,
                limit: int = MAX_PER_RUN) -> tuple[BillTemplate | None, list[Bill]]:
    """Make every bill of ``template_id`` that is due by ``today`` (at most ``limit``), each once, under a lock on the template.

    Returns the template as saved and the bills made by this call. A period whose bill already exists (another run made
    it) is skipped, never made twice."""
    template = store.get("templates", template_id, for_update=True)
    if template is None or template.status != TemplateStatus.ACTIVE or template.next_date is None:
        return template, []
    made: list[Bill] = []
    current = template
    while current.next_date is not None and current.next_date <= today and len(made) < limit:
        reason = schedule.finished(current, current.next_date)
        if reason:
            current = _end(current, reason, now)
            break
        period = current.next_date
        if store.first("bills", template_id=current.id, period_date=period) is None:
            try:
                made.append(store.add("bills", bill_for(current, period, now, actor)))
            except DuplicateError:
                pass  # another run made this period's bill first
        index = current.next_index + 1
        current = current.model_copy(update={
            "next_index": index, "next_date": schedule.occurrence(current.anchor_date, current.frequency, current.interval, index),
            "created_count": current.created_count + 1, "last_period": period, "updated_at": now,
        })
    if current.status == TemplateStatus.ACTIVE and current.next_date is not None and (reason := schedule.finished(current, current.next_date)):
        current = _end(current, reason, now)
    if current is not template:
        current = current.model_copy(update={"version": template.version + 1})
        if not store.save("templates", current, expected_version=template.version):
            raise BooksError(409, "Someone changed this recurring bill at the same time. Reload and try again.", "stale")
    return current, made


def resume(template: BillTemplate, *, today: date, now: datetime, actor: str) -> BillTemplate:
    """Active again from the first period on or after ``today``: periods passed while paused are skipped, never made late."""
    index = max(template.next_index, schedule.first_index_on_or_after(template.anchor_date, template.frequency, template.interval, today))
    return template.model_copy(update={"status": TemplateStatus.ACTIVE, "next_index": index,
                                       "next_date": schedule.occurrence(template.anchor_date, template.frequency, template.interval, index),
                                       "updated_by": actor, "updated_at": now, "version": template.version + 1})


__all__ = ["MAX_PER_RUN", "SYSTEM_ACTOR", "bill_for", "materialize", "resume", "template_from"]
