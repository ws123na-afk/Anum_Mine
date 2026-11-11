"""HTTP routes for recurring invoices and the daily invoice pass (``/api/v1/business``, docs/business.md#recurring-invoices).

Members and owners make, edit, pause, resume, end and delete a recurring invoice and run its due periods now; owners
turn the daily pass's planned emails on or off. Nothing here sends anything: a schedule drafts invoices and may ask
for an owner's approval to send each one; the planned emails each wait in Approvals (``invoice_jobs``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query as Q, Response, status
from pydantic import BaseModel, Field

from ..dependencies import repository_context, tenant_context, workspace_archived
from ..repository import AnumRepository
from ..schemas import TenantContext, utc_now
from .common import Page, get_or_404, manage, page_bounds, read, write
from .invoice_jobs import automation_row, run_schedule, run_workspace_day
from .invoice_models import (
    Invoice,
    InvoiceAutomation,
    InvoiceLine,
    InvoiceSchedule,
    IssueMode,
    ScheduleFrequency,
    ScheduleLine,
    ScheduleStatus,
)
from .invoice_schedules import end, history, resume, schedule_from, upcoming
from .invoice_service import business_day
from .invoices import line_totals, payment_state, reprice
from .models import Currency, Money
from .service import audit, http_error, record_activity
from .store import MAX_SCAN, Query, open_business_store

router = APIRouter(prefix="/api/v1/business", tags=["business"])
ARCHIVED = "This workspace is archived, so recurring invoices are paused. Restore it to change them."


class ScheduleInput(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    account_id: str | None = Field(default=None, max_length=80)
    project_id: str | None = Field(default=None, max_length=80)
    contact_id: str | None = Field(default=None, max_length=80)
    currency: Currency | None = None
    lines: list[ScheduleLine] | None = Field(default=None, max_length=60)
    due_in_days: int | None = Field(default=None, ge=0, le=365)
    customer_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)
    payment_terms: str | None = Field(default=None, max_length=1000)
    frequency: ScheduleFrequency | None = None
    interval: int | None = Field(default=None, ge=1, le=366)
    start_date: date | None = None
    end_date: date | None = None
    max_count: int | None = Field(default=None, ge=1, le=1000)
    issue_mode: IssueMode | None = None


class ScheduleCreate(ScheduleInput):
    lines: list[ScheduleLine] = Field(min_length=1, max_length=60)
    frequency: ScheduleFrequency
    start_date: date


class ScheduleUpdate(ScheduleInput):
    # The version the person edited; a newer one is refused (someone else saved or the pass drafted meanwhile).
    version: int = Field(ge=1)


class VersionBody(BaseModel):
    version: int = Field(ge=1)


class ScheduleInvoice(BaseModel):
    id: str
    number: str | None
    status: str
    period_date: date | None
    total: Money
    currency: Currency
    payment_state: str
    created_at: Any


class ScheduleView(InvoiceSchedule):
    account_name: str | None
    project_name: str | None
    subtotal: Money
    tax: Money
    total: Money
    upcoming: list[date]


class ScheduleDetail(ScheduleView):
    invoices: list[ScheduleInvoice]
    invoices_total: int


def _priced(schedule: InvoiceSchedule) -> tuple[Decimal, Decimal, Decimal]:
    lines = [InvoiceLine(description=item.description, kind=item.kind, quantity=item.quantity, unit_price=item.unit_price,
                         tax_rate_pct=item.tax_rate_pct, tax_code=item.tax_code) for item in schedule.lines]
    return line_totals(reprice(lines))


def _view(store: Any, schedule: InvoiceSchedule, names: dict[tuple[str, str], str | None] | None = None) -> ScheduleView:
    names = names if names is not None else {}

    def name(kind: str, item_id: str | None) -> str | None:
        if not item_id:
            return None
        if (kind, item_id) not in names:
            found = store.get(kind, item_id)
            names[(kind, item_id)] = found.name if found is not None else None
        return names[(kind, item_id)]

    subtotal, tax, total = _priced(schedule)
    return ScheduleView(**schedule.model_dump(), account_name=name("accounts", schedule.account_id), project_name=name("projects", schedule.project_id),
                        subtotal=subtotal, tax=tax, total=total, upcoming=upcoming(schedule))


def _detail(store: Any, schedule: InvoiceSchedule, limit: int = 50) -> ScheduleDetail:
    today = business_day(store, utc_now())
    made: list[Invoice] = history(store, schedule.id)
    rows = [ScheduleInvoice(id=item.id, number=item.number, status=item.status.value, period_date=item.period_date, total=item.total, currency=item.currency,
                            payment_state=str(payment_state(item, today)["payment_state"]), created_at=item.created_at) for item in made[:limit]]
    return ScheduleDetail(**_view(store, schedule).model_dump(), invoices=rows, invoices_total=len(made))


def _writable(context: TenantContext) -> None:
    write(context)
    if workspace_archived(context):
        raise http_error(409, ARCHIVED)


def _today(store: Any) -> date:
    return business_day(store, utc_now())


@router.get("/invoice-schedules", response_model=Page[ScheduleView])
def list_schedules(status_: ScheduleStatus | None = Q(default=None, alias="status"), account_id: str | None = Q(default=None, max_length=80),
                   q: str | None = Q(default=None, max_length=120), limit: int = 25, offset: int = 0,
                   context: TenantContext = Depends(tenant_context)) -> Page[ScheduleView]:
    """The recurring invoices, newest first; search matches the title and the customer reference."""
    read(context)
    limit, offset = page_bounds(limit, offset)
    where: dict[str, Any] = {}
    if status_ is not None:
        where["status"] = status_
    if account_id:
        where["account_id"] = account_id
    with open_business_store(context) as store:
        items, total = store.find("invoice_schedules", Query(where=where, text=(q or "").strip() or None, text_fields=("title", "customer_reference"),
                                                             order_by="created_at", limit=limit, offset=offset))
        names: dict[tuple[str, str], str | None] = {}
        return Page(items=[_view(store, item, names) for item in items], total=total, limit=limit, offset=offset)


@router.post("/invoice-schedules", response_model=ScheduleDetail, status_code=status.HTTP_201_CREATED)
def create_schedule(payload: ScheduleCreate, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> ScheduleDetail:
    _writable(context)
    now = utc_now()
    with open_business_store(context) as store:
        if store.count("invoice_schedules", Query(where={"status": {ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED}})) >= 200:
            raise http_error(409, "A workspace keeps at most 200 recurring invoices that are active or paused. End one first.")
        schedule = schedule_from(context, store, payload.model_dump(), now=now, today=_today(store))
        store.add("invoice_schedules", schedule)
        record_activity(store, context, "invoice_schedule_created", f"Recurring invoice “{schedule.title}” set up ({schedule.frequency.value}, from {schedule.start_date}).",
                        account_id=schedule.account_id, project_id=schedule.project_id, metadata={"schedule_id": schedule.id})
        view = _detail(store, schedule)
    audit(repository, context, "business.invoice_schedule.created", schedule.id, metadata={"frequency": schedule.frequency.value, "lines": len(schedule.lines),
                                                                                           "issue_mode": schedule.issue_mode.value})
    return view


@router.get("/invoice-schedules/{schedule_id}", response_model=ScheduleDetail)
def get_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context)) -> ScheduleDetail:
    read(context)
    with open_business_store(context) as store:
        return _detail(store, get_or_404(store, "invoice_schedules", schedule_id, "Recurring invoice"))


def _locked(store: Any, schedule_id: str, version: int) -> InvoiceSchedule:
    schedule: InvoiceSchedule = get_or_404(store, "invoice_schedules", schedule_id, "Recurring invoice", for_update=True)
    if schedule.version != version:
        raise http_error(409, "This recurring invoice changed since you opened it (someone saved it, or it drafted an invoice). Reload and try again.")
    return schedule


@router.patch("/invoice-schedules/{schedule_id}", response_model=ScheduleDetail)
def update_schedule(schedule_id: str, payload: ScheduleUpdate, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> ScheduleDetail:
    """Change what future invoices carry or when they fall. Invoices already drafted stay as they are."""
    _writable(context)
    now = utc_now()
    with open_business_store(context) as store:
        existing = _locked(store, schedule_id, payload.version)
        changes = payload.model_dump(exclude_unset=True, exclude={"version"})
        schedule = schedule_from(context, store, changes, now=now, today=_today(store), existing=existing)
        store.save("invoice_schedules", schedule)
        record_activity(store, context, "invoice_schedule_updated", f"Recurring invoice “{schedule.title}” changed; invoices already drafted stay as they are.",
                        account_id=schedule.account_id, project_id=schedule.project_id, metadata={"schedule_id": schedule.id})
        view = _detail(store, schedule)
    audit(repository, context, "business.invoice_schedule.updated", schedule_id, metadata={"fields": sorted(changes)})
    return view


def _change(schedule_id: str, body: VersionBody, context: TenantContext, repository: AnumRepository, action: Literal["pause", "resume", "end"]) -> ScheduleDetail:
    _writable(context)
    now = utc_now()
    with open_business_store(context) as store:
        schedule = _locked(store, schedule_id, body.version)
        if action == "pause":
            if schedule.status != ScheduleStatus.ACTIVE:
                raise http_error(409, "Only an active recurring invoice can be paused.")
            updated = schedule.model_copy(update={"status": ScheduleStatus.PAUSED, "updated_by": context.user_id, "updated_at": now, "version": schedule.version + 1})
        elif action == "resume":
            if schedule.status != ScheduleStatus.PAUSED:
                raise http_error(409, "Only a paused recurring invoice can be resumed.")
            updated = resume(schedule, today=_today(store), now=now, actor=context.user_id)
        else:
            if schedule.status == ScheduleStatus.ENDED:
                raise http_error(409, "This recurring invoice has already ended.")
            updated = end(schedule, "owner", now, context.user_id).model_copy(update={"version": schedule.version + 1})
        store.save("invoice_schedules", updated)
        words = {"pause": "paused", "resume": "resumed", "end": "ended"}[action]
        record_activity(store, context, f"invoice_schedule_{words}", f"Recurring invoice “{updated.title}” {words}.",
                        account_id=updated.account_id, project_id=updated.project_id, metadata={"schedule_id": updated.id})
        view = _detail(store, updated)
    audit(repository, context, f"business.invoice_schedule.{words}", schedule_id)
    return view


@router.post("/invoice-schedules/{schedule_id}/pause", response_model=ScheduleDetail)
def pause_schedule(schedule_id: str, body: VersionBody, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> ScheduleDetail:
    """No invoice is drafted while paused; the periods that pass are skipped when it resumes."""
    return _change(schedule_id, body, context, repository, "pause")


@router.post("/invoice-schedules/{schedule_id}/resume", response_model=ScheduleDetail)
def resume_schedule(schedule_id: str, body: VersionBody, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> ScheduleDetail:
    return _change(schedule_id, body, context, repository, "resume")


@router.post("/invoice-schedules/{schedule_id}/end", response_model=ScheduleDetail)
def end_schedule(schedule_id: str, body: VersionBody, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> ScheduleDetail:
    """Stop it for good. The invoices it drafted stay, with their history."""
    return _change(schedule_id, body, context, repository, "end")


@router.delete("/invoice-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Delete one that never drafted an invoice. One with invoices is ended instead, so its history stays."""
    _writable(context)
    with open_business_store(context) as store:
        schedule: InvoiceSchedule = get_or_404(store, "invoice_schedules", schedule_id, "Recurring invoice", for_update=True)
        if schedule.created_count or store.count("invoices", Query(where={"schedule_id": schedule_id})):
            raise http_error(409, "This recurring invoice already drafted invoices, so it is kept for their history. End it instead.")
        store.delete("invoice_schedules", schedule_id)
        record_activity(store, context, "invoice_schedule_deleted", f"Recurring invoice “{schedule.title}” deleted before it drafted anything.",
                        account_id=schedule.account_id, project_id=schedule.project_id, metadata={"schedule_id": schedule_id})
    audit(repository, context, "business.invoice_schedule.deleted", schedule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RunResult(BaseModel):
    drafted: int
    sent_for_approval: int
    note: str
    schedule: ScheduleDetail


@router.post("/invoice-schedules/{schedule_id}/run", response_model=RunResult)
def run_now(schedule_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> RunResult:
    """Draft the periods that are due now (the same work the scheduler does), as the person who pressed it."""
    from .invoice_jobs import PassResult
    from .tax_service import table_for

    _writable(context)
    now = utc_now()
    with open_business_store(context) as store:
        schedule: InvoiceSchedule = get_or_404(store, "invoice_schedules", schedule_id, "Recurring invoice")
        today = _today(store)
    if schedule.status != ScheduleStatus.ACTIVE:
        raise http_error(409, "Only an active recurring invoice drafts invoices. Resume it first.")
    result = PassResult()
    made = run_schedule(context, schedule_id, today=today, now=now, table=table_for(context), repository=repository, result=result)
    with open_business_store(context) as store:
        after = get_or_404(store, "invoice_schedules", schedule_id, "Recurring invoice")
        view = _detail(store, after)
    note = after.last_error or ("" if made else f"Nothing is due yet. The next invoice is drafted on {after.next_date or '-'}.")
    audit(repository, context, "business.invoice_schedule.run", schedule_id, metadata={"drafted": len(made), "sent_for_approval": result.sent_for_approval})
    return RunResult(drafted=len(made), sent_for_approval=result.sent_for_approval, note=note, schedule=view)


# -- the daily pass's switches ---------------------------------------------------------------------------


class AutomationView(BaseModel):
    auto_reminders: bool
    email_scheduled: bool
    language: Literal["en", "ar"]
    last_run_on: date | None
    last_run_at: Any
    last_run_summary: dict[str, Any] | None
    can_change: bool


class AutomationInput(BaseModel):
    auto_reminders: bool
    email_scheduled: bool
    language: Literal["en", "ar"] = "en"


def _automation_view(row: InvoiceAutomation, context: TenantContext) -> AutomationView:
    from ..authorization import Permission
    from ..dependencies import has_permission

    return AutomationView(auto_reminders=row.auto_reminders, email_scheduled=row.email_scheduled, language=row.language, last_run_on=row.last_run_on,
                          last_run_at=row.last_run_at, last_run_summary=row.last_run_summary, can_change=has_permission(context, Permission.BUSINESS_MANAGE))


@router.get("/invoice-automation", response_model=AutomationView)
def get_automation(context: TenantContext = Depends(tenant_context)) -> AutomationView:
    read(context)
    return _automation_view(automation_row(context, actor=context.user_id, now=utc_now()), context)


@router.put("/invoice-automation", response_model=AutomationView)
def put_automation(payload: AutomationInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> AutomationView:
    """Owners only: whether the daily pass plans reminders and the emails of scheduled invoices. Each one still waits in Approvals."""
    manage(context)
    now = utc_now()
    with open_business_store(context) as store:
        found = store.first("invoice_automation")
        if found is None:
            row = automation_row(context, actor=context.user_id, now=now).model_copy(update=payload.model_dump())
            store.add("invoice_automation", row)
        else:
            row = store.get("invoice_automation", found.id, for_update=True).model_copy(update={**payload.model_dump(), "updated_by": context.user_id, "updated_at": now})
            store.save("invoice_automation", row)
    audit(repository, context, "business.invoice_automation.updated", row.id, metadata=payload.model_dump())
    return _automation_view(row, context)


@router.post("/invoice-automation/run", response_model=AutomationView)
def run_automation_now(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> AutomationView:
    """Owners only: plan today's emails now (each still waits in Approvals). A step planned before is never planned again."""
    manage(context)
    if workspace_archived(context):
        raise http_error(409, ARCHIVED)
    now = utc_now()
    with open_business_store(context) as store:
        found = store.first("invoice_automation")
        today = _today(store)
    if found is None or not (found.auto_reminders or found.email_scheduled):
        raise http_error(409, "Turn on planned reminders or planned invoice emails first.")
    from .invoice_jobs import system_context

    run_workspace_day(system_context(context.tenant_id, context.workspace_id), today=today, now=now, force=True)
    audit(repository, context, "business.invoice_automation.run", found.id)
    return _automation_view(automation_row(context, actor=context.user_id, now=now), context)


__all__ = ["router"]
