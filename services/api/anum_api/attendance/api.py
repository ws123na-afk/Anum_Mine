"""HTTP routes under ``/api/v1/attendance`` (docs/attendance.md#api).

Who can do what (checked here, on the server, for every route):

* ``attendance:use`` (owners and members): keep their own work profile, sites, schedule,
  arrivals and departures, corrections and daily reports, and share them with a colleague.
* Reading someone else's attendance needs that person's active share naming the caller
  (``?person=<user id>``); a share never allows a change. Without one the answer is 404, the
  same as for a person with no records, so nobody can probe who keeps attendance.
* The workspace manager view (``/workspace-settings``, ``/manager``, ``/manager/export.csv``): workspace
  owners and organisation administrators read everyone's attendance times, read only, only while an owner has
  turned ``manager_view`` on (off by default). Daily reports and corrections are never part of it. Everyone
  who keeps attendance may read whether it is on.
* Viewers have no Attendance. The database enforces the same rules (forced RLS, migrations
  ``0132_work_attendance`` and ``0179_attendance_manager_view``).

This is a personal log kept by the person, never an employer-certified timesheet, and the
exports say so. Exact coordinates are never accepted or stored.
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..audit import AuditRecord
from ..authorization import Permission
from ..dependencies import repository_context, require_owner_or_org_admin, require_permission, tenant_context
from ..repository import AnumRepository
from ..schemas import TenantContext, new_id, utc_now
from . import engine as E
from . import service as S
from . import summary as AI
from .devices import DevicePrincipal, device_principal
from .models import (
    AttendanceEvent,
    Correction,
    CorrectionAction,
    DailyReport,
    EventKind,
    EventSource,
    Priority,
    Profile,
    Share,
    ShiftModel,
    Site,
    SiteKind,
    SiteVisit,
    WorkspaceSettings,
)
from .store import MAX_SHARES, MAX_SITES, AttendanceStore, DuplicateError, manager_reads, open_attendance_store

router = APIRouter(prefix="/api/v1/attendance", tags=["attendance"])

DENIED = "Attendance is for workspace owners and members. Ask a workspace owner to change your role from viewer to member."
NOT_SHARED = "Not found. This person has not shared their attendance with you, or stopped sharing it."
MAX_PAGE = 200


def _use(context: TenantContext) -> None:
    try:
        require_permission(context, Permission.ATTENDANCE_USE)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(status.HTTP_403_FORBIDDEN, DENIED) from None
        raise


def _fail(error: S.AttendanceError) -> HTTPException:
    return HTTPException(error.status_code, error.message)


def _audit(repository: AnumRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any] | None = None) -> None:
    """Ids and codes only: never a time, a site name or report text."""
    repository.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action=action, target=target, outcome="success", correlation_id=target, created_at=utc_now(), metadata=metadata or {},
    ))


def _owner(store: AttendanceStore, context: TenantContext, person: str | None, *, reports: bool = False) -> str:
    """Whose records to read: the caller's own, or a person whose active share names the caller."""
    if not person or person == context.user_id:
        return context.user_id
    share = store.share_for(context, person)
    if share is None or (reports and not share.include_reports):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_SHARED)
    return person


def _clean(value: str | None, limit: int) -> str | None:
    text = " ".join((value or "").split())[:limit]
    return text or None


def _today(store: AttendanceStore, context: TenantContext, owner: str) -> date:
    return S.today_for(S.profile_for(store, context, owner), utc_now())


def _range(store: AttendanceStore, context: TenantContext, owner: str, start: date | None, end: date | None, default_days: int = 6) -> tuple[date, date]:
    end = end or _today(store, context, owner)
    start = start or end - timedelta(days=default_days)
    try:
        S.check_range(start, end)
    except S.AttendanceError as exc:
        raise _fail(exc) from None
    return start, end


# --------------------------------------------------------------------------------- profile


class ProfileView(Profile):
    model_config = ConfigDict(from_attributes=True)
    saved: bool = False
    location_consent: bool = False


def _profile_view(store: AttendanceStore, context: TenantContext, owner: str) -> ProfileView:
    found = store.get_profile(context, owner)
    profile = found or S.default_profile(context, owner)
    return ProfileView(**profile.model_dump(), saved=found is not None, location_consent=profile.location_consent_at is not None)


class ProfileInput(BaseModel):
    organisation: str | None = Field(default=None, max_length=160)
    department: str | None = Field(default=None, max_length=120)
    role_title: str | None = Field(default=None, max_length=120)
    workplace_site_id: str | None = Field(default=None, max_length=80)
    time_zone: str = Field(default="Asia/Dubai", max_length=64)
    week_starts_on: int = Field(default=0, ge=0, le=6)
    schedule: list[ShiftModel] = Field(default_factory=list, max_length=21)
    daily_overtime_minutes: int | None = Field(default=None, ge=60, le=1440)
    weekly_overtime_minutes: int | None = Field(default=None, ge=60, le=10080)
    ai_drafts_enabled: bool = False
    missed_check_alerts: bool = True
    expected_version: int = Field(ge=0)

    @field_validator("time_zone")
    @classmethod
    def _zone(cls, value: str) -> str:
        if not E.valid_zone(value):
            raise ValueError("Choose a time zone from the list, for example Asia/Dubai or Asia/Riyadh.")
        return value


@router.get("/profile", response_model=ProfileView)
def get_profile(person: str | None = None, context: TenantContext = Depends(tenant_context)) -> ProfileView:
    _use(context)
    with open_attendance_store(context) as store:
        return _profile_view(store, context, _owner(store, context, person))


@router.put("/profile", response_model=ProfileView)
def save_profile(body: ProfileInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> ProfileView:
    _use(context)
    with open_attendance_store(context) as store:
        current = S.profile_for(store, context, context.user_id)
        if body.workplace_site_id and store.get_site(context, context.user_id, body.workplace_site_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That workplace was not found. Add it under Sites first.")
        updated = current.model_copy(update={
            "organisation": _clean(body.organisation, 160), "department": _clean(body.department, 120),
            "role_title": _clean(body.role_title, 120), "workplace_site_id": body.workplace_site_id, "time_zone": body.time_zone,
            "week_starts_on": body.week_starts_on, "schedule": body.schedule, "daily_overtime_minutes": body.daily_overtime_minutes,
            "weekly_overtime_minutes": body.weekly_overtime_minutes, "ai_drafts_enabled": body.ai_drafts_enabled,
            "missed_check_alerts": body.missed_check_alerts, "updated_at": utc_now(),
        })
        if not store.save_profile(updated, body.expected_version):
            raise HTTPException(status.HTTP_409_CONFLICT, "Your profile changed in another tab or device. Reload it and try again.")
        view = _profile_view(store, context, context.user_id)
    _audit(repository, context, "attendance.profile.update", f"attendance_profile:{context.user_id}",
           {"shifts": len(body.schedule), "ai_drafts": body.ai_drafts_enabled})
    return view


class TrackingInput(BaseModel):
    action: Literal["consent", "withdraw", "pause", "resume"]


@router.post("/tracking", response_model=ProfileView)
def change_tracking(body: TrackingInput, context: TenantContext = Depends(tenant_context),
                    repository: AnumRepository = Depends(repository_context)) -> ProfileView:
    """Location check in: consent (or withdraw it), pause or resume. Takes effect at once."""
    _use(context)
    now = utc_now()
    with open_attendance_store(context) as store:
        current = S.profile_for(store, context, context.user_id)
        changes: dict[str, Any] = {
            "consent": {"location_consent_at": current.location_consent_at or now, "tracking_paused": False, "paused_at": None},
            "withdraw": {"location_consent_at": None, "tracking_paused": False, "paused_at": None},
            "pause": {"tracking_paused": True, "paused_at": now},
            "resume": {"tracking_paused": False, "paused_at": None},
        }[body.action]
        if body.action in ("pause", "resume") and current.location_consent_at is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Turn on location check in first.")
        if not store.save_profile(current.model_copy(update={**changes, "updated_at": now}), current.version):
            raise HTTPException(status.HTTP_409_CONFLICT, "Your profile changed in another tab or device. Try again.")
        view = _profile_view(store, context, context.user_id)
    _audit(repository, context, f"attendance.tracking.{body.action}", f"attendance_profile:{context.user_id}")
    return view


# --------------------------------------------------------------------------------- sites


class SiteInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: SiteKind = SiteKind.SITE
    address_label: str | None = Field(default=None, max_length=200)
    radius_m: int = Field(default=150, ge=50, le=2000)


class SitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: SiteKind | None = None
    address_label: str | None = Field(default=None, max_length=200)
    radius_m: int | None = Field(default=None, ge=50, le=2000)
    archived: bool | None = None


@router.get("/sites", response_model=list[Site])
def list_sites(person: str | None = None, context: TenantContext = Depends(tenant_context)) -> list[Site]:
    _use(context)
    with open_attendance_store(context) as store:
        return store.list_sites(context, _owner(store, context, person))


@router.post("/sites", response_model=Site, status_code=status.HTTP_201_CREATED)
def create_site(body: SiteInput, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> Site:
    _use(context)
    now = utc_now()
    name = _clean(body.name, 120)
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Give the site a name.")
    with open_attendance_store(context) as store:
        if len(store.list_sites(context, context.user_id)) >= MAX_SITES:
            raise HTTPException(status.HTTP_409_CONFLICT, f"You can keep up to {MAX_SITES} sites. Archive one you no longer use.")
        site = Site(id=new_id("att_site"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                    owner_user_id=context.user_id, name=name, kind=body.kind, address_label=_clean(body.address_label, 200),
                    radius_m=body.radius_m, created_at=now, updated_at=now)
        try:
            store.save_site(site)
        except DuplicateError:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already have a site with that name.") from None
    _audit(repository, context, "attendance.site.create", site.id, {"kind": site.kind.value})
    return site


@router.patch("/sites/{site_id}", response_model=Site)
def update_site(site_id: str, body: SitePatch, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> Site:
    _use(context)
    with open_attendance_store(context) as store:
        site = store.get_site(context, context.user_id, site_id)
        if site is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That site was not found. It may have been deleted.")
        changes = body.model_dump(exclude_unset=True)
        if "name" in changes:
            changes["name"] = _clean(changes["name"], 120) or site.name
        if "address_label" in changes:
            changes["address_label"] = _clean(changes["address_label"], 200)
        updated = site.model_copy(update={**{k: v for k, v in changes.items() if v is not None or k == "address_label"}, "updated_at": utc_now()})
        try:
            store.save_site(updated)
        except DuplicateError:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already have a site with that name.") from None
    _audit(repository, context, "attendance.site.update", site_id, {"fields": sorted(changes)})
    return updated


@router.delete("/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_site(site_id: str, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Response:
    """Archives the site: past records keep its name, new events cannot use it."""
    _use(context)
    with open_attendance_store(context) as store:
        site = store.get_site(context, context.user_id, site_id)
        if site is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That site was not found. It may have been deleted.")
        store.save_site(site.model_copy(update={"archived": True, "updated_at": utc_now()}))
    _audit(repository, context, "attendance.site.archive", site_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------- events


class EventView(BaseModel):
    id: str
    kind: EventKind
    occurred_at: datetime
    received_at: datetime
    time_zone: str
    site_id: str | None
    source: EventSource
    quality: str
    status: str
    note: str | None


def _event_view(event: AttendanceEvent) -> EventView:
    return EventView(**event.model_dump(include=set(EventView.model_fields)))


class RecordResult(BaseModel):
    event: EventView
    duplicate: bool


class CheckInput(BaseModel):
    kind: EventKind
    site_id: str | None = Field(default=None, max_length=80)
    idempotency_key: str = Field(max_length=128)
    note: str | None = Field(default=None, max_length=300)


def _record(context: TenantContext, repository: AnumRepository, *, kind: EventKind, source: EventSource, key: str,
            site_id: str | None, occurred_at: datetime | None = None, device_id: str | None = None,
            accuracy_m: float | None = None, note: str | None = None, response: Response | None = None) -> RecordResult:
    with open_attendance_store(context) as store:
        try:
            recorded = S.record_event(store, context, kind=kind, source=source, idempotency_key=key, now=utc_now(),
                                      occurred_at=occurred_at, site_id=site_id, device_id=device_id, accuracy_m=accuracy_m, note=note)
        except S.AttendanceError as exc:
            raise _fail(exc) from None
    if recorded.created:
        _audit(repository, context, "attendance.event.record", recorded.event.id,
               {"kind": kind.value, "source": source.value, "quality": recorded.event.quality})
    elif response is not None:
        response.status_code = status.HTTP_200_OK
    return RecordResult(event=_event_view(recorded.event), duplicate=not recorded.created)


@router.post("/check", response_model=RecordResult, status_code=status.HTTP_201_CREATED)
def check(body: CheckInput, response: Response, context: TenantContext = Depends(tenant_context),
          repository: AnumRepository = Depends(repository_context)) -> RecordResult:
    """The web buttons: check in or out, start or end a break or remote work, now."""
    _use(context)
    return _record(context, repository, kind=body.kind, source=EventSource.WEB, key=body.idempotency_key, site_id=body.site_id,
                   note=body.note, response=response)


class DeviceEventInput(BaseModel):
    """What an iPhone Shortcut sends: never coordinates, only the site the phone matched."""

    model_config = ConfigDict(extra="forbid")
    event: Literal["arrive", "leave"]
    site_id: str | None = Field(default=None, max_length=80)
    site: str | None = Field(default=None, max_length=120)
    occurred_at: datetime | None = None
    idempotency_key: str = Field(max_length=128)
    accuracy_m: float | None = Field(default=None, ge=0, le=100_000)


def _device_record(principal: DevicePrincipal, body: DeviceEventInput, response: Response) -> RecordResult:
    context = principal.context
    _use(context)
    site_id = body.site_id
    if site_id is None and body.site:
        with open_attendance_store(context) as store:
            wanted = " ".join(body.site.split()).casefold()
            match = [s for s in store.list_sites(context, context.user_id) if s.name.casefold() == wanted and not s.archived]
        if not match:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No active site has that name. Check the name in the Shortcut matches a site in ANUM.")
        site_id = match[0].id
    from ..dependencies import units_of_work

    with units_of_work()(context) as repository:
        return _record(context, repository, kind=EventKind(body.event), source=EventSource.DEVICE, key=body.idempotency_key,
                       site_id=site_id, occurred_at=body.occurred_at, device_id=principal.device_id, accuracy_m=body.accuracy_m,
                       response=response)


@router.post("/device/events", response_model=RecordResult, status_code=status.HTTP_201_CREATED)
async def device_event(body: DeviceEventInput, response: Response,
                       principal: DevicePrincipal = Depends(device_principal)) -> RecordResult:
    """An iPhone Shortcut location automation: arrived at or left a workplace or site.

    Safe to retry: the same idempotency key records the event once and answers 200 with
    ``duplicate: true``. The server stamps when it was received; ``occurred_at`` (optional) is
    when the phone saw it, so a late delivery still lands at the right time."""
    try:
        return await asyncio.to_thread(_device_record, principal, body, response)
    except HTTPException as exc:
        if principal.via == "device_token":
            # A shared iPhone token's refused event shows on Settings, Integrations, iPhone (docs/iphone-integrations.md).
            from ..finance.devices import report_failure

            await asyncio.to_thread(report_failure, principal.context, principal.device_id, "attendance", f"http_{exc.status_code}")
        raise


@router.get("/events", response_model=list[EventView])
def list_events(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                person: str | None = None, include_changed: bool = False,
                context: TenantContext = Depends(tenant_context)) -> list[EventView]:
    _use(context)
    with open_attendance_store(context) as store:
        owner = _owner(store, context, person)
        start, end = _range(store, context, owner, start, end)
        tz = S.profile_for(store, context, owner).time_zone
        lo, hi = E.local_instant(start, time(0), tz), E.local_instant(end + timedelta(days=1), time(0), tz)
        return [_event_view(e) for e in store.list_events(context, owner, lo, hi, active_only=not include_changed)]


class TodayStatus(BaseModel):
    at_site: bool
    site_id: str | None
    on_break: bool
    remote: bool
    since: datetime | None


class TodayView(BaseModel):
    day: date
    time_zone: str
    status: TodayStatus
    totals: S.DayView | None
    events: list[EventView]
    tracking_paused: bool
    location_consent: bool
    profile_saved: bool


def _status(day: S.DayView | None) -> TodayStatus:
    if day is None:
        return TodayStatus(at_site=False, site_id=None, on_break=False, remote=False, since=None)
    open_ = [s for s in day.segments if s.end_kind == "open"]
    site = next((s for s in open_ if s.kind == "site"), None)
    remote = next((s for s in open_ if s.kind == "remote"), None)
    brk = next((s for s in open_ if s.kind == "break"), None)
    since = (brk or site or remote).start if (brk or site or remote) else None
    return TodayStatus(at_site=site is not None, site_id=site.site_id if site else None, on_break=brk is not None,
                       remote=remote is not None, since=since)


@router.get("/today", response_model=TodayView)
def today(person: str | None = None, context: TenantContext = Depends(tenant_context)) -> TodayView:
    _use(context)
    now = utc_now()
    with open_attendance_store(context) as store:
        owner = _owner(store, context, person)
        found = store.get_profile(context, owner)
        profile = found or S.default_profile(context, owner)
        day = S.today_for(profile, now)
        days = S.compute_days(store, context, owner, day, day, now, profile=profile)
        current = days[-1] if days and days[-1].day == day else None
        # Yesterday's overnight shift still open counts as now too.
        if current is None or not any(s.end_kind == "open" for s in current.segments):
            earlier = S.compute_days(store, context, owner, day - timedelta(days=1), day - timedelta(days=1), now, profile=profile)
            if earlier and any(s.end_kind == "open" for s in earlier[-1].segments):
                current = earlier[-1] if current is None else current
                status_day = earlier[-1]
            else:
                status_day = current
        else:
            status_day = current
        tz = profile.time_zone
        lo = E.local_instant(day - timedelta(days=1), time(0), tz)
        events = [_event_view(e) for e in store.list_events(context, owner, lo, now + timedelta(minutes=10))]
        return TodayView(day=day, time_zone=tz, status=_status(status_day), totals=current, events=events,
                         tracking_paused=profile.tracking_paused, location_consent=profile.location_consent_at is not None,
                         profile_saved=found is not None)


# ---- team today (docs/attendance.md#team-attendance) ---------------------------------------------
#
# The caller's own row plus one row per colleague whose active share names the caller. Nobody else
# appears: a person who never shared, or stopped sharing, is not listed and cannot be probed. A
# report's status and summary appear only for the caller and for shares that include reports.

class TeamBreak(BaseModel):
    start: datetime
    end: datetime | None


class TeamReport(BaseModel):
    status: Literal["none", "draft", "submitted"]
    updated_at: datetime | None = None
    submitted_at: datetime | None = None
    summary: str | None = None


class TeamRow(BaseModel):
    user_id: str
    me: bool
    day: date
    time_zone: str
    role_title: str | None
    status: TodayStatus | None
    site_names: list[str]
    first_check_in: datetime | None
    first_check_in_source: EventSource | None
    breaks: list[TeamBreak]
    break_seconds: int
    worked_seconds: int
    presence_seconds: int
    remote_seconds: int
    scheduled_seconds: int
    open: bool
    flags: list[str]
    reports_shared: bool
    report: TeamReport | None


class TeamView(BaseModel):
    day: date | None
    generated_at: datetime
    rows: list[TeamRow]
    total: int


TEAM_PAGE = 100


def _report_summary(report: DailyReport) -> str | None:
    text = "; ".join(report.tasks_done) or report.project or report.notes or "; ".join(report.tasks_pending)
    text = " ".join((text or "").split())
    return (text[:157] + "...") if len(text) > 160 else (text or None)


def _team_row(store: AttendanceStore, context: TenantContext, owner: str, reports: bool, day: date | None, now: datetime) -> TeamRow:
    found = store.get_profile(context, owner)
    profile = found or S.default_profile(context, owner)
    today = S.today_for(profile, now)
    wanted = day or today
    days = S.compute_days(store, context, owner, wanted, wanted, now, profile=profile)
    view = days[-1] if days and days[-1].day == wanted else None
    status_view = view
    # Like /today: a shift begun yesterday and still open after midnight is what the person is doing now.
    if wanted == today and not (view and any(s.end_kind == "open" for s in view.segments)):
        earlier = S.compute_days(store, context, owner, wanted - timedelta(days=1), wanted - timedelta(days=1), now, profile=profile)
        if earlier and any(s.end_kind == "open" for s in earlier[-1].segments):
            status_view = earlier[-1]
            if view is None or not any(s.kind in ("site", "remote") for s in view.segments):
                view = earlier[-1]
    names = {s.id: s.name for s in store.list_sites(context, owner)}
    working = [s for s in (view.segments if view else []) if s.kind in ("site", "remote")]
    first = min(working, key=lambda s: s.start) if working else None
    source: EventSource | None = None
    if first is not None:
        event = store.get_event(context, owner, first.start_event_id)
        source = event.source if event else None
    report: TeamReport | None = None
    if reports or owner == context.user_id:
        saved = store.get_report(context, owner, wanted)
        report = TeamReport(status="none") if saved is None else TeamReport(
            status="submitted" if saved.submitted_at else "draft", updated_at=saved.updated_at, submitted_at=saved.submitted_at,
            summary=_report_summary(saved))
    return TeamRow(
        user_id=owner, me=owner == context.user_id, day=wanted, time_zone=profile.time_zone, role_title=profile.role_title,
        status=_status(status_view) if wanted == today else None,
        site_names=[names.get(i, "") for i in (view.site_ids if view else []) if names.get(i)],
        first_check_in=first.start if first else None, first_check_in_source=source,
        breaks=[TeamBreak(start=s.start, end=s.end) for s in (view.segments if view else []) if s.kind == "break"],
        break_seconds=view.break_seconds if view else 0, worked_seconds=view.worked_seconds if view else 0,
        presence_seconds=view.presence_seconds if view else 0, remote_seconds=view.remote_seconds if view else 0,
        scheduled_seconds=view.scheduled_seconds if view else 0,
        open=bool(view and view.open_seconds), flags=view.flags if view else [],
        reports_shared=reports or owner == context.user_id, report=report,
    )


@router.get("/team", response_model=TeamView)
def team(day: date | None = Query(default=None, alias="date"), limit: int = Query(default=50, ge=1, le=TEAM_PAGE),
         offset: int = Query(default=0, ge=0), context: TenantContext = Depends(tenant_context)) -> TeamView:
    """The caller's own day and each colleague's day that colleague shares with the caller, read only."""
    _use(context)
    now = utc_now()
    with open_attendance_store(context) as store:
        people: list[tuple[str, bool]] = [(context.user_id, True)]
        people += [(s.owner_user_id, s.include_reports) for s in store.list_shares(context)
                   if s.viewer_user_id == context.user_id and s.owner_user_id != context.user_id and s.revoked_at is None]
        page = people[offset:offset + limit]
        try:
            rows = [_team_row(store, context, owner, reports, day, now) for owner, reports in page]
        except S.AttendanceError as error:
            raise _fail(error) from None
    return TeamView(day=day, generated_at=now, rows=rows, total=len(people))


@router.get("/days", response_model=list[S.DayView])
def list_days(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
              person: str | None = None, context: TenantContext = Depends(tenant_context)) -> list[S.DayView]:
    _use(context)
    with open_attendance_store(context) as store:
        owner = _owner(store, context, person)
        start, end = _range(store, context, owner, start, end)
        return S.compute_days(store, context, owner, start, end, utc_now())


@router.get("/summary", response_model=S.PeriodView)
def summary(period: Literal["day", "week", "month"] = "week", anchor: date | None = Query(default=None, alias="date"),
            person: str | None = None, context: TenantContext = Depends(tenant_context)) -> S.PeriodView:
    _use(context)
    with open_attendance_store(context) as store:
        owner = _owner(store, context, person)
        profile = S.profile_for(store, context, owner)
        start, end = S.period_bounds(period, anchor or S.today_for(profile, utc_now()), profile.week_starts_on)
        return S.period(store, context, owner, start, end, utc_now())


@router.get("/site-history", response_model=list[S.SiteVisitRow])
def site_history(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                 site_id: str | None = None, person: str | None = None,
                 context: TenantContext = Depends(tenant_context)) -> list[S.SiteVisitRow]:
    _use(context)
    with open_attendance_store(context) as store:
        owner = _owner(store, context, person)
        start, end = _range(store, context, owner, start, end, default_days=29)
        return S.site_history(store, context, owner, start, end, utc_now(), site_id)


def _export(context: TenantContext, start: date | None, end: date | None, person: str | None) -> tuple[S.PeriodView, dict[str, Site], Profile, str]:
    with open_attendance_store(context) as store:
        owner = _owner(store, context, person)
        start, end = _range(store, context, owner, start, end, default_days=29)
        view = S.period(store, context, owner, start, end, utc_now())
        sites = {s.id: s for s in store.list_sites(context, owner)}
        return view, sites, S.profile_for(store, context, owner), owner


@router.get("/export.csv")
def export_csv(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
               person: str | None = None, context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> Response:
    _use(context)
    view, sites, _, owner = _export(context, start, end, person)
    _audit(repository, context, "attendance.export", f"attendance:{owner}", {"format": "csv", "days": len(view.days)})
    name = f"attendance-{view.start.isoformat()}-{view.end.isoformat()}.csv"
    return Response(S.export_csv(view, sites), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/export.pdf")
def export_pdf(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
               person: str | None = None, context: TenantContext = Depends(tenant_context),
               repository: AnumRepository = Depends(repository_context)) -> Response:
    _use(context)
    view, sites, profile, owner = _export(context, start, end, person)
    _audit(repository, context, "attendance.export", f"attendance:{owner}", {"format": "pdf", "days": len(view.days)})
    pdf = S.export_pdf(view, sites, person=owner, profile=profile, generated=utc_now())
    name = f"attendance-{view.start.isoformat()}-{view.end.isoformat()}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# --------------------------------------------------------------------------------- corrections


class CorrectionInput(BaseModel):
    action: CorrectionAction
    event_id: str | None = Field(default=None, max_length=80)
    kind: EventKind | None = None
    occurred_at: datetime | None = None
    site_id: str | None = Field(default=None, max_length=80)
    reason: str = Field(max_length=500)


class CorrectionPage(BaseModel):
    items: list[Correction]
    total: int


@router.post("/corrections", response_model=Correction, status_code=status.HTTP_201_CREATED)
def add_correction(body: CorrectionInput, context: TenantContext = Depends(tenant_context),
                   repository: AnumRepository = Depends(repository_context)) -> Correction:
    _use(context)
    with open_attendance_store(context) as store:
        try:
            correction = S.correct(store, context, action=body.action, reason=body.reason, now=utc_now(), event_id=body.event_id,
                                   kind=body.kind, occurred_at=body.occurred_at, site_id=body.site_id)
        except S.AttendanceError as exc:
            raise _fail(exc) from None
    _audit(repository, context, "attendance.correction", correction.id,
           {"action": correction.action.value, "event": correction.event_id, "replacement": correction.replacement_event_id})
    return correction


@router.get("/corrections", response_model=CorrectionPage)
def list_corrections(limit: int = Query(default=50, ge=1, le=MAX_PAGE), offset: int = Query(default=0, ge=0),
                     person: str | None = None, context: TenantContext = Depends(tenant_context)) -> CorrectionPage:
    _use(context)
    with open_attendance_store(context) as store:
        items, total = store.list_corrections(context, _owner(store, context, person), limit=limit, offset=offset)
        return CorrectionPage(items=items, total=total)


# --------------------------------------------------------------------------------- daily reports


class ReportInput(BaseModel):
    project: str | None = Field(default=None, max_length=160)
    tasks_done: list[str] = Field(default_factory=list, max_length=50)
    tasks_pending: list[str] = Field(default_factory=list, max_length=50)
    site_visits: list[SiteVisit] = Field(default_factory=list, max_length=30)
    issues: str | None = Field(default=None, max_length=4000)
    time_spent_minutes: int | None = Field(default=None, ge=0, le=1440)
    priority: Priority = Priority.NORMAL
    follow_up_on: date | None = None
    notes: str | None = Field(default=None, max_length=8000)
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    drafted_with_ai: bool = False
    expected_version: int | None = Field(default=None, ge=1)
    # True submits the report (it stays submitted after later edits); false keeps a draft a draft.
    submit: bool = False

    @field_validator("tasks_done", "tasks_pending")
    @classmethod
    def _tasks(cls, value: list[str]) -> list[str]:
        return [" ".join(v.split())[:500] for v in value if v and v.strip()]


class ReportPage(BaseModel):
    items: list[DailyReport]
    total: int


def _check_files(context: TenantContext, file_ids: list[str]) -> None:
    if not file_ids:
        return
    from ..files import open_file_metadata_store

    try:
        require_permission(context, Permission.FILE_READ)
    except HTTPException:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Attaching files needs access to Files.") from None
    with open_file_metadata_store(context) as files:
        missing = [fid for fid in file_ids if files.get(context, fid) is None]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "An attached file was not found in this workspace's Files. Upload it again.")


@router.get("/daily-reports", response_model=ReportPage)
def list_reports(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                 q: str | None = Query(default=None, max_length=120), limit: int = Query(default=30, ge=1, le=MAX_PAGE),
                 offset: int = Query(default=0, ge=0), person: str | None = None,
                 context: TenantContext = Depends(tenant_context)) -> ReportPage:
    _use(context)
    with open_attendance_store(context) as store:
        owner = _owner(store, context, person, reports=True)
        items, total = store.list_reports(context, owner, start, end, _clean(q, 120), limit=limit, offset=offset)
        return ReportPage(items=items, total=total)


@router.get("/daily-reports/{day}", response_model=DailyReport)
def get_report(day: date, person: str | None = None, context: TenantContext = Depends(tenant_context)) -> DailyReport:
    _use(context)
    with open_attendance_store(context) as store:
        found = store.get_report(context, _owner(store, context, person, reports=True), day)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No daily report for that day yet.")
    return found


@router.put("/daily-reports/{day}", response_model=DailyReport)
def save_report(day: date, body: ReportInput, response: Response, context: TenantContext = Depends(tenant_context),
                repository: AnumRepository = Depends(repository_context)) -> DailyReport:
    _use(context)
    now = utc_now()
    _check_files(context, body.file_ids)
    with open_attendance_store(context) as store:
        if day > _today(store, context, context.user_id) + timedelta(days=1):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A daily report is for today or a past day.")
        current = store.get_report(context, context.user_id, day)
        if current is not None and body.expected_version is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "There is already a report for that day. Open it to change it.")
        report = DailyReport(
            id=current.id if current else new_id("att_rep"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            owner_user_id=context.user_id, day=day, project=_clean(body.project, 160), tasks_done=body.tasks_done,
            tasks_pending=body.tasks_pending, site_visits=body.site_visits, issues=(body.issues or "").strip() or None,
            time_spent_minutes=body.time_spent_minutes, priority=body.priority, follow_up_on=body.follow_up_on,
            notes=(body.notes or "").strip() or None, file_ids=list(dict.fromkeys(body.file_ids)), drafted_with_ai=body.drafted_with_ai,
            version=(current.version + 1) if current else 1, created_at=current.created_at if current else now, updated_at=now,
            submitted_at=(current.submitted_at if current and current.submitted_at else (now if body.submit else None)),
        )
        try:
            saved = store.save_report(report, body.expected_version)
        except DuplicateError:
            raise HTTPException(status.HTTP_409_CONFLICT, "There is already a report for that day. Open it to change it.") from None
        if not saved:
            raise HTTPException(status.HTTP_409_CONFLICT, "This report changed in another tab or device. Reload it and try again.")
    response.status_code = status.HTTP_200_OK if current else status.HTTP_201_CREATED
    _audit(repository, context, "attendance.report.save", report.id,
           {"version": report.version, "files": len(report.file_ids), "ai": report.drafted_with_ai,
            "submitted": report.submitted_at is not None})
    return report


@router.delete("/daily-reports/{day}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(day: date, context: TenantContext = Depends(tenant_context),
                  repository: AnumRepository = Depends(repository_context)) -> Response:
    _use(context)
    with open_attendance_store(context) as store:
        if not store.delete_report(context, day):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No daily report for that day.")
    _audit(repository, context, "attendance.report.delete", f"attendance_report:{day.isoformat()}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class DraftInput(BaseModel):
    period: Literal["day", "week"] = "day"
    date: date
    language: Literal["en", "ar"] = "en"


class DraftView(BaseModel):
    text: str
    period: str
    start: date
    end: date
    report_days: list[date]


def _draft_inputs(context: TenantContext, body: DraftInput) -> tuple[date, date, list[DailyReport]]:
    with open_attendance_store(context) as store:
        profile = S.profile_for(store, context, context.user_id)
        if not profile.ai_drafts_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "AI drafts are off. Turn them on under Attendance, Settings.")
        start, end = S.period_bounds(body.period, body.date, profile.week_starts_on)
        reports, _ = store.list_reports(context, context.user_id, start, end, None, limit=31, offset=0)
    return start, end, reports


@router.post("/daily-reports/draft", response_model=DraftView)
async def draft_summary(body: DraftInput, context: TenantContext = Depends(tenant_context)) -> DraftView:
    """A draft summary from the person's own report entries, through the budgeted model gateway.

    Database work runs in its own short unit in a worker thread; no transaction is open while
    the model writes (docs/realtime.md#event-loop)."""
    _use(context)
    start, end, reports = await asyncio.to_thread(_draft_inputs, context, body)
    if not AI.has_entries(reports):
        raise HTTPException(status.HTTP_409_CONFLICT, "Write at least one daily report with tasks or notes first. The draft uses only your own entries.")
    if not await asyncio.to_thread(AI.model_connected, context):
        raise HTTPException(status.HTTP_409_CONFLICT, "No AI model is connected to this workspace. An owner can connect one under Settings, Models.")
    text = await AI.draft(context, reports, period=body.period, language=body.language)
    return DraftView(text=text, period=body.period, start=start, end=end, report_days=sorted(r.day for r in reports))


# --------------------------------------------------------------------------------- sharing


class ShareInput(BaseModel):
    viewer_user_id: str = Field(min_length=1, max_length=160)
    include_reports: bool = False


class SharesView(BaseModel):
    mine: list[Share]
    with_me: list[Share]


def _active_member(context: TenantContext, user_id: str) -> bool:
    from ..dependencies import lookup_membership

    from ..settings import settings

    membership = lookup_membership(context.model_copy(update={"user_id": user_id}))
    if membership is None:
        # Header sign-in (local and test only) has no stored memberships to check against.
        return settings.auth_mode != "oidc" and settings.repository_backend == "memory"
    return membership.active and membership.role.lower() in ("owner", "member")


@router.get("/shares", response_model=SharesView)
def list_shares(context: TenantContext = Depends(tenant_context)) -> SharesView:
    _use(context)
    with open_attendance_store(context) as store:
        shares = store.list_shares(context)
    return SharesView(mine=[s for s in shares if s.owner_user_id == context.user_id],
                      with_me=[s for s in shares if s.viewer_user_id == context.user_id])


@router.post("/shares", response_model=Share, status_code=status.HTTP_201_CREATED)
def create_share(body: ShareInput, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Share:
    _use(context)
    if body.viewer_user_id == context.user_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose someone else. You always see your own attendance.")
    if not _active_member(context, body.viewer_user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person is not an active owner or member of this workspace.")
    with open_attendance_store(context) as store:
        if len([s for s in store.list_shares(context) if s.owner_user_id == context.user_id]) >= MAX_SHARES:
            raise HTTPException(status.HTTP_409_CONFLICT, f"You can share with up to {MAX_SHARES} people.")
        share = Share(id=new_id("att_shr"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                      owner_user_id=context.user_id, viewer_user_id=body.viewer_user_id, include_reports=body.include_reports,
                      created_at=utc_now())
        try:
            store.save_share(share)
        except DuplicateError:
            raise HTTPException(status.HTTP_409_CONFLICT, "You already share with that person. Stop sharing first to change it.") from None
    _audit(repository, context, "attendance.share.create", share.id, {"viewer": body.viewer_user_id, "reports": body.include_reports})
    return share


@router.delete("/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share(share_id: str, context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> Response:
    _use(context)
    with open_attendance_store(context) as store:
        if not store.revoke_share(context, share_id, utc_now()):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That share was not found or was already stopped.")
    _audit(repository, context, "attendance.share.revoke", share_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/limits")
def limits(context: TenantContext = Depends(tenant_context)) -> dict[str, int]:
    """Fixed rules the screens explain (retention, how late a phone event may be)."""
    _use(context)
    return {"raw_retention_days": S.RAW_RETENTION_DAYS, "device_max_age_days": S.DEVICE_MAX_AGE.days,
            "poor_accuracy_m": S.POOR_ACCURACY_M, "max_range_days": S.MAX_RANGE_DAYS}


# ---- the workspace manager view (docs/attendance.md#manager-view) ---------------------------------------
#
# Off by default. When an owner turns it on, workspace owners and organisation administrators see every
# person's attendance times in this workspace, read only: arrivals, breaks, hours, sites and flags. Daily
# reports and corrections stay with the person and their own shares. Each unit that reads for the view sets
# anum.attendance_manager, and the read policies also check the workspace's switch in the database.

MANAGER_DENIED = ("Only workspace owners and organisation administrators see the whole workspace's attendance. "
                  "Ask a workspace owner if you need it.")
MANAGER_OFF = ("The workspace attendance view is off. A workspace owner can turn it on under Attendance, Workspace. "
               "Members are told it is on.")
MANAGER_PAGE = 100
MANAGER_EXPORT_PEOPLE = 500
MANAGER_EXPORT_DAYS = 92  # people times days stays bounded for one request


def _manager(context: TenantContext) -> None:
    _use(context)
    require_owner_or_org_admin(context, Permission.WORKSPACE_MANAGE, MANAGER_DENIED)


def _can_manage(context: TenantContext) -> bool:
    try:
        _manager(context)
    except HTTPException:
        return False
    return True


class WorkspaceSettingsView(BaseModel):
    manager_view: bool
    version: int
    updated_by: str | None
    updated_at: datetime | None
    can_manage: bool


class WorkspaceSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manager_view: bool
    expected_version: int = Field(ge=0)


def _settings_view(context: TenantContext, found: WorkspaceSettings) -> WorkspaceSettingsView:
    return WorkspaceSettingsView(manager_view=found.manager_view, version=found.version, updated_by=found.updated_by,
                                 updated_at=found.updated_at, can_manage=_can_manage(context))


@router.get("/workspace-settings", response_model=WorkspaceSettingsView)
def get_workspace_settings(context: TenantContext = Depends(tenant_context)) -> WorkspaceSettingsView:
    """Whether the manager view is on (everyone who keeps attendance may know), and who may change it."""
    _use(context)
    with open_attendance_store(context) as store:
        found = store.get_workspace_settings(context)
    return _settings_view(context, found)


@router.put("/workspace-settings", response_model=WorkspaceSettingsView)
def put_workspace_settings(body: WorkspaceSettingsInput, context: TenantContext = Depends(tenant_context),
                           repository: AnumRepository = Depends(repository_context)) -> WorkspaceSettingsView:
    _manager(context)
    now = utc_now()
    with manager_reads(), open_attendance_store(context) as store:
        current = store.get_workspace_settings(context)
        saved = WorkspaceSettings(tenant_id=context.tenant_id, workspace_id=context.workspace_id, manager_view=body.manager_view,
                                  version=current.version, updated_by=context.user_id, updated_at=now)
        if not store.save_workspace_settings(saved, body.expected_version):
            raise HTTPException(status.HTTP_409_CONFLICT, "Someone changed this setting since you opened it. Reload and try again.")
        found = store.get_workspace_settings(context)
    _audit(repository, context, "attendance.manager_view", f"workspace:{context.workspace_id}", {"on": body.manager_view})
    return _settings_view(context, found)


def _require_view_on(store: AttendanceStore, context: TenantContext) -> None:
    if not store.get_workspace_settings(context).manager_view:
        raise HTTPException(status.HTTP_409_CONFLICT, MANAGER_OFF)


@router.get("/manager", response_model=TeamView)
def manager_view(day: date | None = Query(default=None, alias="date"), limit: int = Query(default=50, ge=1, le=MANAGER_PAGE),
                 offset: int = Query(default=0, ge=0, le=100000), context: TenantContext = Depends(tenant_context),
                 repository: AnumRepository = Depends(repository_context)) -> TeamView:
    """Every person who keeps attendance in this workspace, one day, a page at a time; read only."""
    _manager(context)
    now = utc_now()
    with manager_reads(), open_attendance_store(context) as store:
        _require_view_on(store, context)
        people, total = store.list_people(context, limit=limit, offset=offset)
        try:
            # Reports stay private here: a row carries one only for the caller's own day.
            rows = [_team_row(store, context, owner, False, day, now) for owner in people]
        except S.AttendanceError as error:
            raise _fail(error) from None
    _audit(repository, context, "attendance.manager.read", f"workspace:{context.workspace_id}", {"rows": len(rows), "offset": offset})
    return TeamView(day=day, generated_at=now, rows=rows, total=total)


MANAGER_CSV_COLUMNS = ("person", "name", "role_title", *S.CSV_COLUMNS)


def _names(context: TenantContext) -> dict[str, str]:
    """Display names of the workspace's members, in its own short unit (never inside the attendance unit)."""
    from ..team_work import open_team_unit

    try:
        with open_team_unit(context, require_membership=True) as unit:
            return {p.user_id: p.display_name for p in unit.people()}
    except HTTPException:
        return {}


@router.get("/manager/export.csv")
def manager_export(start: date | None = Query(default=None, alias="from"), end: date | None = Query(default=None, alias="to"),
                   context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """One CSV of everyone's days in the range (owners and organisation administrators, manager view on)."""
    _manager(context)
    names = _names(context)
    now = utc_now()
    with manager_reads(), open_attendance_store(context) as store:
        _require_view_on(store, context)
        end = end or S.today_for(S.profile_for(store, context, context.user_id), now)
        start = start or end - timedelta(days=29)
        try:
            S.check_range(start, end)
        except S.AttendanceError as exc:
            raise _fail(exc) from None
        if (end - start).days + 1 > MANAGER_EXPORT_DAYS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"Export at most {MANAGER_EXPORT_DAYS} days of the whole workspace at a time.")
        people, total = store.list_people(context, limit=MANAGER_EXPORT_PEOPLE, offset=0)
        if total > MANAGER_EXPORT_PEOPLE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"More than {MANAGER_EXPORT_PEOPLE} people keep attendance here. Export a shorter range per page from the list.")
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(MANAGER_CSV_COLUMNS)
        days = 0
        for person in people:
            profile = S.profile_for(store, context, person)
            view = S.period(store, context, person, start, end, now)
            sites = {s.id: s for s in store.list_sites(context, person)}
            for row in csv.reader(io.StringIO(S.export_csv(view, sites))):
                if row and row[0] != "date":
                    writer.writerow([person, S.safe_cell(names.get(person, "")), S.safe_cell(profile.role_title or ""), *row])
            days += len(view.days)
    _audit(repository, context, "attendance.manager.export", f"workspace:{context.workspace_id}", {"people": len(people), "days": days})
    name = f"attendance-workspace-{start.isoformat()}-{end.isoformat()}.csv"
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
