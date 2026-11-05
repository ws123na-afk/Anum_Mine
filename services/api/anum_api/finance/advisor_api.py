"""Money advisor routes under ``/api/v1/finance/advisor`` (docs/finance.md#money-advisor).

* ``finance:read``: this week's note, past notes (paged) and the switches, the caller's own only.
* ``finance:manage``: "Generate now" and changing the switches.

Viewers have neither. Nobody sees anyone else's notes, workspace owners included (owner-only RLS).
ANUM only advises: no route here moves money, pays a bill or touches a bank or card.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import repository_context, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, utc_now
from .advisor import AdvisorNote
from .advisor_jobs import advisor_settings, generate, week_start_for
from .advisor_store import AdvisorReport, AdvisorSettings, open_advisor_store
from .api import _audit, _manage, _read
from .store import open_finance_store
from .summary import settings_for, today_in

router = APIRouter(prefix="/api/v1/finance/advisor", tags=["finance"])
MAX_NOTES_PAGE = 50


class NoteView(BaseModel):
    id: str
    week_start: date
    trigger: Literal["schedule", "manual"]
    generated_at: datetime
    delivered_at: datetime | None
    note: AdvisorNote


class SurvivalLine(BaseModel):
    currency: str
    status: str
    days: int | None


class CurrencyTotal(BaseModel):
    currency: str
    amount: Decimal


class NoteSummary(BaseModel):
    id: str
    week_start: date
    trigger: Literal["schedule", "manual"]
    generated_at: datetime
    delivered_at: datetime | None
    survival: list[SurvivalLine]
    coming: list[CurrencyTotal]
    actions: int
    urgent: int


class NotePage(BaseModel):
    items: list[NoteSummary]
    total: int
    limit: int
    offset: int


class AdvisorOverview(BaseModel):
    settings: AdvisorSettings
    timezone: str
    today: date
    week_start: date
    next_note_on: date | None  # None while the weekly note is off
    latest: NoteView | None


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    weekday: int = Field(ge=0, le=6)


def _view(report: AdvisorReport) -> NoteView:
    return NoteView(id=report.id, week_start=report.week_start, trigger=report.trigger, generated_at=report.generated_at,
                    delivered_at=report.delivered_at, note=report.note)


def _summary(report: AdvisorReport) -> NoteSummary:
    survival = []
    for block in report.note.currencies:
        essential = next((s for s in block.runway.scenarios if s.kind == "essential"), None)
        survival.append(SurvivalLine(currency=block.currency, status=block.runway.status, days=essential.days if essential else None))
    return NoteSummary(
        id=report.id, week_start=report.week_start, trigger=report.trigger, generated_at=report.generated_at, delivered_at=report.delivered_at,
        survival=survival, coming=[CurrencyTotal(currency=b.currency, amount=b.coming.total) for b in report.note.currencies],
        actions=len(report.note.actions), urgent=sum(1 for a in report.note.actions if a.severity == "urgent"),
    )


@router.get("", response_model=AdvisorOverview)
def overview(context: TenantContext = Depends(tenant_context)) -> AdvisorOverview:
    """The switches, this advice week and the newest note (None before the first one)."""
    _read(context)
    with open_finance_store(context) as store:
        finance_settings = settings_for(store, context)
    today = today_in(finance_settings)
    chosen = advisor_settings(context)
    week_start = week_start_for(today, chosen.weekday)
    with open_advisor_store(context) as store:
        page, _total = store.list_reports(context, limit=1, offset=0)
    next_note = (week_start if week_start == today else week_start + timedelta(days=7)) if chosen.enabled else None
    return AdvisorOverview(settings=chosen, timezone=finance_settings.timezone, today=today, week_start=week_start, next_note_on=next_note,
                           latest=_view(page[0]) if page else None)


@router.get("/notes", response_model=NotePage)
def list_notes(limit: int = Query(default=10, ge=1, le=MAX_NOTES_PAGE), offset: int = Query(default=0, ge=0, le=10_000),
               context: TenantContext = Depends(tenant_context)) -> NotePage:
    """Past weekly notes, newest week first."""
    _read(context)
    with open_advisor_store(context) as store:
        page, total = store.list_reports(context, limit=limit, offset=offset)
    return NotePage(items=[_summary(r) for r in page], total=total, limit=limit, offset=offset)


@router.get("/notes/{note_id}", response_model=NoteView)
def get_note(note_id: str, context: TenantContext = Depends(tenant_context)) -> NoteView:
    _read(context)
    with open_advisor_store(context) as store:
        found = store.get_report(context, note_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found. It may have been deleted.")
    return _view(found)


@router.post("/generate", response_model=NoteView, status_code=status.HTTP_201_CREATED)
def generate_now(context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> NoteView:
    """Work this week's note out again from the records as they are now. Sends no notification."""
    _manage(context)
    saved = generate(context, trigger="manual")
    _audit(repository, context, "finance.advisor.generate", saved.id, {"actions": len(saved.note.actions)})
    return _view(saved)


@router.get("/settings", response_model=AdvisorSettings)
def get_settings(context: TenantContext = Depends(tenant_context)) -> AdvisorSettings:
    _read(context)
    return advisor_settings(context)


@router.put("/settings", response_model=AdvisorSettings)
def put_settings(body: SettingsInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> AdvisorSettings:
    _manage(context)
    saved = AdvisorSettings(enabled=body.enabled, weekday=body.weekday, updated_at=utc_now())
    with open_advisor_store(context) as store:
        store.save_settings(context, saved)
    _audit(repository, context, "finance.advisor.settings", context.user_id, {"enabled": body.enabled, "weekday": body.weekday})
    return saved


__all__ = ["router"]
