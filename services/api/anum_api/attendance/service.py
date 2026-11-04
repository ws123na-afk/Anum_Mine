"""Attendance rules between the API and the store (docs/attendance.md).

Recording events, corrections, the per-day view that joins live events with reduced day
totals, period totals, site history and the CSV and PDF exports. No route, no HTTP: the API
turns :class:`AttendanceError` into a response.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel

from ..schemas import TenantContext, new_id
from . import engine as E
from .models import (
    AttendanceEvent,
    Correction,
    CorrectionAction,
    DayTotal,
    EventKind,
    EventSource,
    EventStatus,
    Profile,
    Site,
)
from .store import AttendanceStore

# Raw arrival and departure events are kept this long, then reduced to day totals (owner default).
RAW_RETENTION_DAYS = 90
# An event may say it happened at most this far in the future (clock drift) ...
FUTURE_SLACK = timedelta(minutes=5)
# ... and a phone or web event at most this far back; older times need a correction with a reason.
DEVICE_MAX_AGE = timedelta(days=7)
# A location fix less precise than this (metres) marks the event as poor quality.
POOR_ACCURACY_M = 200
MAX_RANGE_DAYS = 400
IDEM_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class AttendanceError(Exception):
    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status_code, self.message, self.code = status_code, message, code


def default_profile(context: TenantContext, owner: str | None = None) -> Profile:
    return Profile(tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=owner or context.user_id)


def profile_for(store: AttendanceStore, context: TenantContext, owner: str) -> Profile:
    return store.get_profile(context, owner) or default_profile(context, owner)


def shifts_of(profile: Profile) -> list[E.Shift]:
    return [E.parse_shift(s.weekday, s.start, s.end) for s in profile.schedule]


def today_for(profile: Profile, now: datetime) -> date:
    return E.local_day(now, profile.time_zone)


# --------------------------------------------------------------------------------- events


@dataclass
class Recorded:
    event: AttendanceEvent
    created: bool


def _same_request(existing: AttendanceEvent, kind: EventKind, site_id: str | None) -> bool:
    return existing.kind == kind and existing.site_id == site_id


def record_event(store: AttendanceStore, context: TenantContext, *, kind: EventKind, source: EventSource, idempotency_key: str,
                 now: datetime, occurred_at: datetime | None = None, site_id: str | None = None, device_id: str | None = None,
                 accuracy_m: float | None = None, note: str | None = None) -> Recorded:
    """Store one event, once per idempotency key. The server clock is the receive time; the event
    time is the phone's when it sends one (late or offline delivery), else now."""
    if not IDEM_KEY.match(idempotency_key or ""):
        raise AttendanceError(422, "Send an idempotency key of 8 to 128 letters, digits, dots, dashes, colons or underscores.", "idempotency_key")
    profile = profile_for(store, context, context.user_id)
    if source == EventSource.DEVICE:
        if profile.location_consent_at is None:
            raise AttendanceError(409, "Turn on location check in under Attendance, Settings first. Nothing was recorded.", "no_consent")
        if profile.tracking_paused:
            raise AttendanceError(409, "Tracking is paused, so this arrival or departure was not recorded. Resume it under Attendance, Settings.", "paused")
    when = occurred_at.astimezone(UTC) if occurred_at else now
    if occurred_at is not None and occurred_at.tzinfo is None:
        raise AttendanceError(422, "Send the time with its offset, for example 2026-11-04T08:00:00+04:00.", "naive_time")
    if when > now + FUTURE_SLACK:
        raise AttendanceError(422, "That time is in the future. Check the phone's date and time.", "future")
    if source != EventSource.MANUAL and now - when > DEVICE_MAX_AGE:
        raise AttendanceError(422, "That time is more than 7 days ago. Add it as a correction with a reason instead.", "too_old")
    if source == EventSource.MANUAL and now - when > timedelta(days=RAW_RETENTION_DAYS):
        raise AttendanceError(422, f"Days older than {RAW_RETENTION_DAYS} days are kept as totals only and cannot be changed.", "reduced")
    if site_id is not None:
        site = store.get_site(context, context.user_id, site_id)
        if site is None:
            raise AttendanceError(404, "That site was not found. It may have been deleted.", "site_not_found")
        if site.archived:
            raise AttendanceError(409, "That site is archived. Restore it under Attendance, Settings to use it again.", "site_archived")
    if source == EventSource.DEVICE and kind in (EventKind.ARRIVE, EventKind.LEAVE) and site_id is None:
        raise AttendanceError(422, "Name the workplace or site the phone arrived at or left.", "site_required")
    quality = "poor" if accuracy_m is not None and accuracy_m > POOR_ACCURACY_M else "ok"
    key = f"{source.value}:{idempotency_key}"
    event = AttendanceEvent(
        id=new_id("att_evt"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        kind=kind, occurred_at=when, received_at=now, time_zone=profile.time_zone, site_id=site_id, source=source,
        quality=quality, device_id=device_id, idem_key=key, note=(note or "").strip()[:300] or None,
    )
    stored, created = store.add_event(event)
    if not created and not _same_request(stored, kind, site_id):
        raise AttendanceError(409, "That idempotency key was already used for a different event. Send a new key for a new event.", "key_reused")
    return Recorded(stored, created)


def correct(store: AttendanceStore, context: TenantContext, *, action: CorrectionAction, reason: str, now: datetime,
            event_id: str | None = None, kind: EventKind | None = None, occurred_at: datetime | None = None,
            site_id: str | None = None) -> Correction:
    """A manual adjustment with a required reason. The old event is kept (voided or superseded)
    and the correction row records before and after, so every change can be traced."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise AttendanceError(422, "Say why you are changing this record (at least 3 characters).", "reason_required")
    before = after = None
    old: AttendanceEvent | None = None
    if action in (CorrectionAction.CHANGE, CorrectionAction.VOID):
        old = store.get_event(context, context.user_id, event_id or "")
        if old is None:
            raise AttendanceError(404, "That record was not found. It may be older than the retention period or already changed.", "event_not_found")
        if old.status != EventStatus.ACTIVE:
            raise AttendanceError(409, "That record was already changed. Refresh to see the current one.", "event_changed")
        before = _snapshot(old)
    replacement: AttendanceEvent | None = None
    if action in (CorrectionAction.ADD, CorrectionAction.CHANGE):
        new_kind = kind or (old.kind if old else None)
        new_time = occurred_at or (old.occurred_at if old else None)
        if new_kind is None or new_time is None:
            raise AttendanceError(422, "Choose what happened and when.", "kind_and_time")
        new_site = site_id if site_id is not None or old is None else old.site_id
        if old is not None and (new_kind, new_time.astimezone(UTC), new_site) == (old.kind, old.occurred_at, old.site_id):
            raise AttendanceError(422, "Nothing changed. Change the time, the site or the kind.", "no_change")
        replacement = record_event(store, context, kind=new_kind, source=EventSource.MANUAL, idempotency_key=new_id("fix"),
                                   now=now, occurred_at=new_time, site_id=new_site, note=reason).event
        after = _snapshot(replacement)
    if old is not None:
        store.set_event_status(context, old.id, EventStatus.VOIDED if action == CorrectionAction.VOID else EventStatus.SUPERSEDED)
    return store.add_correction(Correction(
        id=new_id("att_fix"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        action=action, event_id=old.id if old else None, replacement_event_id=replacement.id if replacement else None,
        before=before, after=after, reason=reason[:500], created_at=now,
    ))


def _snapshot(event: AttendanceEvent) -> dict[str, object]:
    return {"kind": event.kind.value, "occurred_at": event.occurred_at.isoformat(), "site_id": event.site_id,
            "source": event.source.value}


def to_engine(event: AttendanceEvent) -> E.Event:
    return E.Event(id=event.id, kind=event.kind.value, at=event.occurred_at, zone=event.time_zone, site_id=event.site_id,
                   source=event.source.value, quality=event.quality, received_at=event.received_at)


# --------------------------------------------------------------------------------- days


class SegmentView(BaseModel):
    kind: str
    start: datetime
    end: datetime | None
    end_kind: str
    site_id: str | None
    verified: bool
    seconds: int
    start_event_id: str
    end_event_id: str | None


class DayView(BaseModel):
    day: date
    time_zone: str
    presence_seconds: int
    remote_seconds: int
    break_seconds: int
    worked_seconds: int
    verified_seconds: int
    estimated_seconds: int
    open_seconds: int = 0
    scheduled_seconds: int
    overtime_seconds: int | None
    first_arrival: datetime | None
    last_departure: datetime | None
    site_ids: list[str]
    flags: list[str]
    segments: list[SegmentView]
    reduced: bool = False


def _view(totals: E.DayTotals) -> DayView:
    return DayView(
        day=totals.day, time_zone=totals.zone, presence_seconds=totals.presence_seconds, remote_seconds=totals.remote_seconds,
        break_seconds=totals.break_seconds, worked_seconds=totals.worked_seconds, verified_seconds=totals.verified_seconds,
        estimated_seconds=totals.estimated_seconds, open_seconds=totals.open_seconds, scheduled_seconds=totals.scheduled_seconds,
        overtime_seconds=totals.overtime_seconds, first_arrival=totals.first_arrival, last_departure=totals.last_departure,
        site_ids=totals.site_ids, flags=totals.flag_codes,
        segments=[SegmentView(kind=s.kind, start=s.start, end=s.end, end_kind=s.end_kind, site_id=s.site_id, verified=s.verified,
                              seconds=s.seconds(), start_event_id=s.start_event_id, end_event_id=s.end_event_id) for s in totals.segments],
    )


def _reduced_view(total: DayTotal) -> DayView:
    return DayView(
        day=total.day, time_zone=total.time_zone, presence_seconds=total.presence_seconds, remote_seconds=total.remote_seconds,
        break_seconds=total.break_seconds, worked_seconds=total.worked_seconds, verified_seconds=total.verified_seconds,
        estimated_seconds=total.estimated_seconds, scheduled_seconds=total.scheduled_seconds, overtime_seconds=total.overtime_seconds,
        first_arrival=total.first_arrival, last_departure=total.last_departure, site_ids=total.site_ids, flags=total.flags,
        segments=[], reduced=True,
    )


def check_range(start: date, end: date) -> None:
    if end < start:
        raise AttendanceError(422, "The end date is before the start date.", "range")
    if (end - start).days > MAX_RANGE_DAYS:
        raise AttendanceError(422, f"Choose at most {MAX_RANGE_DAYS} days at a time.", "range_too_long")


def compute_days(store: AttendanceStore, context: TenantContext, owner: str, start: date, end: date, now: datetime,
                 *, profile: Profile | None = None) -> list[DayView]:
    """Every day between ``start`` and ``end`` that has events, a schedule or a reduced total."""
    check_range(start, end)
    profile = profile or profile_for(store, context, owner)
    tz = profile.time_zone
    # One day either side: overnight shifts and people who changed zone stay whole.
    lo = E.local_instant(start - timedelta(days=1), time(0), tz)
    hi = E.local_instant(end + timedelta(days=2), time(0), tz)
    events = [to_engine(e) for e in store.list_events(context, owner, lo, hi)]
    computed = E.days(events, now=now, shifts=shifts_of(profile), default_zone=tz, start=start, end=end,
                      daily_overtime_minutes=profile.daily_overtime_minutes)
    out = {d.day: _view(d) for d in computed}
    for total in store.list_day_totals(context, owner, start, end):
        out[total.day] = _reduced_view(total)
    return [out[day] for day in sorted(out)]


class PeriodView(BaseModel):
    start: date
    end: date
    days_worked: int
    presence_seconds: int
    remote_seconds: int
    break_seconds: int
    worked_seconds: int
    verified_seconds: int
    estimated_seconds: int
    scheduled_seconds: int
    overtime_seconds: int | None
    overtime_configured: bool
    flags: dict[str, int]
    days: list[DayView]


def _engine_totals(view: DayView) -> E.DayTotals:
    totals = E.DayTotals(day=view.day, zone=view.time_zone, presence_seconds=view.presence_seconds,
                         remote_seconds=view.remote_seconds, break_seconds=view.break_seconds, worked_seconds=view.worked_seconds,
                         verified_seconds=view.verified_seconds, estimated_seconds=view.estimated_seconds,
                         scheduled_seconds=view.scheduled_seconds, overtime_seconds=view.overtime_seconds)
    totals.flags = [E.Flag(code) for code in view.flags]
    return totals


def period_bounds(period: str, anchor: date, week_starts_on: int) -> tuple[date, date]:
    if period == "day":
        return anchor, anchor
    if period == "week":
        first = E.week_start(anchor, week_starts_on)
        return first, first + timedelta(days=6)
    if period == "month":
        first = anchor.replace(day=1)
        following = (first + timedelta(days=32)).replace(day=1)
        return first, following - timedelta(days=1)
    raise AttendanceError(422, "Choose day, week or month.", "period")


def period(store: AttendanceStore, context: TenantContext, owner: str, start: date, end: date, now: datetime) -> PeriodView:
    profile = profile_for(store, context, owner)
    day_views = compute_days(store, context, owner, start, end, now, profile=profile)
    totals = E.period_totals([_engine_totals(d) for d in day_views], start, end,
                             daily_overtime=profile.daily_overtime_minutes is not None,
                             weekly_overtime_minutes=profile.weekly_overtime_minutes, week_starts_on=profile.week_starts_on)
    return PeriodView(
        start=start, end=end, days_worked=totals.days_worked, presence_seconds=totals.presence_seconds,
        remote_seconds=totals.remote_seconds, break_seconds=totals.break_seconds, worked_seconds=totals.worked_seconds,
        verified_seconds=totals.verified_seconds, estimated_seconds=totals.estimated_seconds,
        scheduled_seconds=totals.scheduled_seconds, overtime_seconds=totals.overtime_seconds,
        overtime_configured=profile.daily_overtime_minutes is not None or profile.weekly_overtime_minutes is not None,
        flags=totals.flags, days=day_views,
    )


class SiteVisitRow(BaseModel):
    day: date
    site_id: str
    start: datetime
    end: datetime | None
    seconds: int
    end_kind: str


def site_history(store: AttendanceStore, context: TenantContext, owner: str, start: date, end: date, now: datetime,
                 site_id: str | None) -> list[SiteVisitRow]:
    rows: list[SiteVisitRow] = []
    for day in compute_days(store, context, owner, start, end, now):
        for segment in day.segments:
            if segment.kind == "site" and segment.site_id and (site_id is None or segment.site_id == site_id):
                rows.append(SiteVisitRow(day=day.day, site_id=segment.site_id, start=segment.start, end=segment.end,
                                         seconds=segment.seconds, end_kind=segment.end_kind))
    return rows


# --------------------------------------------------------------------------------- reduction


def reduce_old_events(store: AttendanceStore, context: TenantContext, now: datetime, *, days_kept: int = RAW_RETENTION_DAYS) -> dict[str, int]:
    """Reduce this person's raw events older than the retention to day totals, then delete them.

    A day is reduced only once every event of it is past the cutoff (the day before the cutoff's
    local date, in the person's zone), so a day is never split between raw and reduced."""
    profile = profile_for(store, context, context.user_id)
    tz = profile.time_zone
    cutoff_day = E.local_day(now, tz) - timedelta(days=days_kept)
    cutoff = E.local_instant(cutoff_day, time(0), tz)
    old = store.list_events(context, context.user_id, datetime(1970, 1, 1, tzinfo=UTC), cutoff, active_only=False)
    if not old:
        return {"days": 0, "events": 0, "corrections": store.delete_corrections_before(context, cutoff)}
    first_day = min(E.local_day(e.occurred_at, e.time_zone) for e in old)
    active = [to_engine(e) for e in old if e.status == EventStatus.ACTIVE]
    computed = E.days(active, now=now, shifts=shifts_of(profile), default_zone=tz, start=first_day,
                      end=cutoff_day - timedelta(days=1), daily_overtime_minutes=profile.daily_overtime_minutes, include_scheduled=False)
    counts: dict[date, int] = {}
    for e in old:
        local = E.local_day(e.occurred_at, e.time_zone)
        counts[local] = counts.get(local, 0) + 1
    existing = {t.day for t in store.list_day_totals(context, context.user_id, first_day, cutoff_day)}
    totals = [
        DayTotal(
            tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, day=d.day,
            time_zone=d.zone, presence_seconds=d.presence_seconds, remote_seconds=d.remote_seconds, break_seconds=d.break_seconds,
            worked_seconds=d.worked_seconds, verified_seconds=d.verified_seconds, estimated_seconds=d.estimated_seconds,
            scheduled_seconds=d.scheduled_seconds, overtime_seconds=d.overtime_seconds, first_arrival=d.first_arrival,
            last_departure=d.last_departure, site_ids=d.site_ids, flags=d.flag_codes, event_count=counts.get(d.day, 0), created_at=now,
        )
        for d in computed if d.day not in existing
    ]
    store.save_day_totals(totals)
    return {"days": len(totals), "events": store.delete_events_before(context, cutoff),
            "corrections": store.delete_corrections_before(context, cutoff)}


# --------------------------------------------------------------------------------- exports


def _hours(seconds: int | None) -> str:
    return "" if seconds is None else f"{seconds / 3600:.2f}"


def _clock(value: datetime | None, tz: str) -> str:
    return "" if value is None else value.astimezone(E.zone(tz)).strftime("%H:%M")


def safe_cell(value: str) -> str:
    """A CSV text cell a spreadsheet will not run as a formula."""
    text = value or ""
    return "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r", "|", "%"} else text


CSV_COLUMNS = ("date", "time_zone", "first_arrival", "last_departure", "presence_hours", "remote_hours", "break_hours",
               "worked_hours", "verified_hours", "estimated_hours", "scheduled_hours", "overtime_estimate_hours", "sites", "flags")


def export_csv(view: PeriodView, sites: dict[str, Site]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for d in view.days:
        names = "; ".join(sites[s].name if s in sites else s for s in d.site_ids)
        writer.writerow([d.day.isoformat(), d.time_zone, _clock(d.first_arrival, d.time_zone), _clock(d.last_departure, d.time_zone),
                         _hours(d.presence_seconds), _hours(d.remote_seconds), _hours(d.break_seconds), _hours(d.worked_seconds),
                         _hours(d.verified_seconds), _hours(d.estimated_seconds), _hours(d.scheduled_seconds),
                         _hours(d.overtime_seconds), safe_cell(names), " ".join(d.flags)])
    writer.writerow(["total", "", "", "", _hours(view.presence_seconds), _hours(view.remote_seconds), _hours(view.break_seconds),
                     _hours(view.worked_seconds), _hours(view.verified_seconds), _hours(view.estimated_seconds),
                     _hours(view.scheduled_seconds), _hours(view.overtime_seconds), "", ""])
    return buffer.getvalue()


NOT_CERTIFIED = "Personal work log kept by the person. Not certified by an employer."


def export_pdf(view: PeriodView, sites: dict[str, Site], *, person: str, profile: Profile, generated: datetime) -> bytes:
    from ..pdf_writer import PdfDocument

    document = PdfDocument(footer=f"Attendance {view.start.isoformat()} to {view.end.isoformat()} - {NOT_CERTIFIED}")
    document.heading("Attendance report", ar="تقرير الحضور")
    details = [("Person", "الشخص", person), ("Period", "الفترة", f"{view.start.isoformat()} to {view.end.isoformat()}"),
               ("Time zone", "المنطقة الزمنية", profile.time_zone),
               ("Generated", "تاريخ الإنشاء", generated.astimezone(E.zone(profile.time_zone)).strftime("%Y-%m-%d %H:%M"))]
    if profile.organisation:
        details.insert(1, ("Organisation", "الجهة", profile.organisation))
    document.pairs(details)
    document.paragraph(NOT_CERTIFIED)
    document.pairs([
        ("Worked hours", "ساعات العمل", _hours(view.worked_seconds)),
        ("Verified hours", "الساعات المؤكدة", _hours(view.verified_seconds)),
        ("Presence hours", "ساعات التواجد", _hours(view.presence_seconds)),
        ("Remote hours", "العمل عن بعد", _hours(view.remote_seconds)),
        ("Scheduled hours", "الساعات المجدولة", _hours(view.scheduled_seconds)),
        ("Overtime estimate", "تقدير العمل الإضافي", _hours(view.overtime_seconds) if view.overtime_configured else "Not configured"),
    ])
    rows = []
    for d in view.days:
        names = ", ".join(sites[s].name if s in sites else s for s in d.site_ids)
        rows.append([d.day.isoformat(), _clock(d.first_arrival, d.time_zone), _clock(d.last_departure, d.time_zone),
                     _hours(d.worked_seconds), _hours(d.verified_seconds), names, " ".join(d.flags)])
    document.table("Days", [("Date", "التاريخ"), ("In", "الدخول"), ("Out", "الخروج"), ("Worked", "العمل"), ("Verified", "المؤكد"),
                            ("Sites", "المواقع"), ("Notes", "ملاحظات")], rows, title_ar="الأيام")
    return document.render()


def stable_key(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:48]
