"""The attendance time engine (docs/attendance.md#time-engine): pure, no storage, no clock.

It turns one person's attendance events into time segments and per-day totals. Events never
are the state: the engine replays them in time order every time, so a late event, a duplicate
or a correction changes the answer without any stored state to repair (the pattern of
``office_presence.derive_presence``).

Rules, in short:

* Every duration is measured on aware UTC instants, so a shift across a daylight saving change
  lasts its real length (a 22:00 to 06:00 shift is 7 or 9 hours on those nights).
* A segment belongs to the **shift day**: the local date of its start, in the time zone the
  start event was recorded in. An overnight shift therefore counts on the day it began, and a
  person who flies to another zone keeps each day in the zone they were in.
* **Presence** is time on a site (workplace, site or client), breaks included. **Remote** is
  remote work. **Worked** time is site and remote time together minus breaks, counting an open
  or estimated end. **Verified** time is the part of worked time whose start and end were both
  recorded with good quality: never an estimate, never an open interval, never a poor location.
* A missing checkout on a past day ends at the scheduled end when the schedule has one after
  the arrival (estimated, never verified), else at the arrival itself, and is flagged.
* Duplicates (an arrival where the person already is, a second break start) are ignored and
  flagged; arriving at another site ends the current site visit at that moment (multiple sites
  a day); a departure with no arrival is ignored and flagged.

Nothing here infers work from location: segments are only what the events say.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KINDS = ("arrive", "leave", "break_start", "break_end", "remote_start", "remote_end")
# An event received this long after it happened is counted but flagged as late.
LATE_AFTER = timedelta(minutes=15)
# An open interval younger than this is ongoing (counted up to now); older is a missing checkout.
MAX_OPEN = timedelta(hours=16)
DEFAULT_ZONE = "Asia/Dubai"


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZONE)


def valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def local_day(at: datetime, zone_name: str) -> date:
    return at.astimezone(zone(zone_name)).date()


def local_instant(day: date, clock: time, zone_name: str) -> datetime:
    """``day`` at ``clock`` in the zone, as an aware UTC instant, DST-correct.

    A clock time that does not exist (the spring-forward gap) moves forward by the gap, and an
    ambiguous one (the autumn repeat) takes its first occurrence, as calendars do.
    """
    # fold=0 (PEP 495): in the gap zoneinfo applies the offset from before the change, which is
    # the clock time moved forward by the gap; in the repeat it is the first occurrence.
    return datetime.combine(day, clock).replace(tzinfo=zone(zone_name), fold=0).astimezone(UTC)


# --------------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Shift:
    """One scheduled shift: weekday (Monday 0 to Sunday 6) and local start and end clock times.

    An end at or before the start ends on the next day (an overnight shift)."""

    weekday: int
    start: time
    end: time

    @property
    def overnight(self) -> bool:
        return self.end <= self.start


@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    at: datetime  # aware
    zone: str = DEFAULT_ZONE
    site_id: str | None = None
    source: str = "web"  # web, device, manual
    quality: str = "ok"  # ok, poor
    received_at: datetime | None = None

    def __post_init__(self) -> None:
        # Python subtracts two datetimes that share a tzinfo on the wall clock, ignoring a DST
        # change between them: every instant is held in UTC so durations are real elapsed time.
        if self.at.tzinfo is None:
            raise ValueError("event times must be aware")
        object.__setattr__(self, "at", self.at.astimezone(UTC))
        if self.received_at is not None:
            object.__setattr__(self, "received_at", self.received_at.astimezone(UTC))


@dataclass(frozen=True)
class Flag:
    code: str
    event_id: str | None = None


@dataclass
class Segment:
    kind: str  # site, remote, break
    start: datetime
    zone: str
    start_event_id: str
    site_id: str | None = None
    end: datetime | None = None
    end_event_id: str | None = None
    # event, switch (arrival elsewhere), open (ongoing), estimated (scheduled end), missing (no end known)
    end_kind: str = "open"
    good: bool = True  # start (and end, once closed) recorded with good quality

    @property
    def day(self) -> date:
        return local_day(self.start, self.zone)

    @property
    def verified(self) -> bool:
        return self.good and self.end is not None and self.end_kind in ("event", "switch")

    def seconds(self) -> int:
        return 0 if self.end is None else max(0, int((self.end - self.start).total_seconds()))


@dataclass
class DayTotals:
    day: date
    zone: str
    presence_seconds: int = 0
    remote_seconds: int = 0
    break_seconds: int = 0
    worked_seconds: int = 0
    verified_seconds: int = 0
    estimated_seconds: int = 0
    open_seconds: int = 0
    scheduled_seconds: int = 0
    overtime_seconds: int | None = None
    first_arrival: datetime | None = None
    last_departure: datetime | None = None
    site_ids: list[str] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)

    @property
    def flag_codes(self) -> list[str]:
        return sorted({flag.code for flag in self.flags})


@dataclass
class Replay:
    segments: list[Segment]
    flags: list[Flag]


# --------------------------------------------------------------------------------- replay


def _order(events: Iterable[Event]) -> list[Event]:
    epoch = datetime.min.replace(tzinfo=UTC)
    return sorted(events, key=lambda e: (e.at, e.received_at or epoch, e.id))


def scheduled_end_after(start: datetime, zone_name: str, shifts: Sequence[Shift]) -> datetime | None:
    """The end of the scheduled shift that contains ``start`` (or begins later on its shift day)."""
    day = local_day(start, zone_name)
    best: datetime | None = None
    for candidate_day in (day - timedelta(days=1), day):
        for shift in shifts:
            if shift.weekday != candidate_day.weekday():
                continue
            begin = local_instant(candidate_day, shift.start, zone_name)
            end = local_instant(candidate_day + timedelta(days=1) if shift.overnight else candidate_day, shift.end, zone_name)
            if end > start and begin - timedelta(hours=12) <= start and (best is None or end < best):
                best = end
    return best


def replay(events: Iterable[Event], *, now: datetime, shifts: Sequence[Shift] = ()) -> Replay:
    """Segments and flags from events in any order (sorted here by when they happened)."""
    segments: list[Segment] = []
    flags: list[Flag] = []
    site: Segment | None = None
    remote: Segment | None = None
    brk: Segment | None = None

    def close(segment: Segment, at: datetime, event: Event | None, how: str) -> None:
        segment.end, segment.end_kind = at, how
        segment.end_event_id = event.id if event else None
        if event is not None and event.quality != "ok":
            segment.good = False

    def close_break(at: datetime, event: Event | None, how: str) -> None:
        nonlocal brk
        if brk is not None:
            close(brk, max(at, brk.start), event, how)
            brk = None

    def expire(at: datetime) -> None:
        """Finish an interval left open longer than MAX_OPEN before ``at`` as a missing checkout, so a
        later shift never joins it (a forgotten checkout yesterday cannot swallow today)."""
        nonlocal site, remote, brk
        for name in ("site", "remote"):
            segment = site if name == "site" else remote
            if segment is None or at - segment.start <= MAX_OPEN:
                continue
            finish_missing(segment)
            if name == "site":
                site = None
            else:
                remote = None
        if brk is not None and site is None and remote is None:
            ends = [s.end for s in segments if s.kind != "break" and s.end is not None and s.start <= brk.start]
            close_break(max(ends) if ends else brk.start, None, "missing")

    def finish_missing(segment: Segment) -> None:
        flags.append(Flag("missing_checkout", segment.start_event_id))
        estimate = scheduled_end_after(segment.start, segment.zone, shifts)
        if estimate is not None and estimate - segment.start <= MAX_OPEN:
            close(segment, estimate, None, "estimated")
        else:
            close(segment, segment.start, None, "missing")

    for event in _order(events):
        expire(event.at)
        if event.received_at is not None and event.received_at - event.at > LATE_AFTER:
            flags.append(Flag("late_event", event.id))
        if event.source == "manual":
            flags.append(Flag("adjusted", event.id))
        if event.quality != "ok":
            flags.append(Flag("poor_location", event.id))
        good = event.quality == "ok"
        if event.kind == "arrive":
            if site is not None and site.site_id == event.site_id:
                flags.append(Flag("duplicate", event.id))
                continue
            if site is not None:
                close(site, event.at, event, "switch")
                if remote is None:
                    close_break(event.at, event, "switch")
            site = Segment("site", event.at, event.zone, event.id, site_id=event.site_id, good=good)
            segments.append(site)
        elif event.kind == "leave":
            if site is None:
                flags.append(Flag("leave_without_arrival", event.id))
                continue
            if event.site_id is not None and site.site_id is not None and event.site_id != site.site_id:
                flags.append(Flag("leave_other_site", event.id))
                continue
            close(site, event.at, event, "event")
            site = None
            if remote is None:
                close_break(event.at, event, "event")
        elif event.kind == "remote_start":
            if remote is not None:
                flags.append(Flag("duplicate", event.id))
                continue
            remote = Segment("remote", event.at, event.zone, event.id, good=good)
            segments.append(remote)
        elif event.kind == "remote_end":
            if remote is None:
                flags.append(Flag("end_without_start", event.id))
                continue
            close(remote, event.at, event, "event")
            remote = None
            if site is None:
                close_break(event.at, event, "event")
        elif event.kind == "break_start":
            if brk is not None:
                flags.append(Flag("duplicate", event.id))
                continue
            if site is None and remote is None:
                flags.append(Flag("break_outside_work", event.id))
                continue
            brk = Segment("break", event.at, event.zone, event.id, good=good)
            segments.append(brk)
        elif event.kind == "break_end":
            if brk is None:
                flags.append(Flag("end_without_start", event.id))
                continue
            close_break(event.at, event, "event")
        else:  # pragma: no cover - the API refuses other kinds
            flags.append(Flag("unknown_kind", event.id))

    # Whatever is still open: ongoing when recent, else a missing checkout.
    for segment in (site, remote):
        if segment is None:
            continue
        if now - segment.start <= MAX_OPEN:
            close(segment, max(now, segment.start), None, "open")
        else:
            finish_missing(segment)
    if brk is not None:
        ends = [s.end for s in segments if s.kind != "break" and s.end is not None and s.start <= brk.start]
        close_break(max(ends) if ends else brk.start, None, "open" if now - brk.start <= MAX_OPEN else "missing")
    return Replay(segments=segments, flags=flags)


# --------------------------------------------------------------------------------- totals


Interval = tuple[datetime, datetime]


def _union(intervals: Iterable[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _seconds(intervals: Iterable[Interval]) -> int:
    return int(sum((end - start).total_seconds() for start, end in intervals))


def _minus(base: list[Interval], cut: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in base:
        pieces = [(start, end)]
        for c_start, c_end in cut:
            next_pieces: list[Interval] = []
            for p_start, p_end in pieces:
                if c_end <= p_start or c_start >= p_end:
                    next_pieces.append((p_start, p_end))
                    continue
                if c_start > p_start:
                    next_pieces.append((p_start, c_start))
                if c_end < p_end:
                    next_pieces.append((c_end, p_end))
            pieces = next_pieces
        out.extend(pieces)
    return out


def _intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a_start, a_end in a:
        for b_start, b_end in b:
            start, end = max(a_start, b_start), min(a_end, b_end)
            if end > start:
                out.append((start, end))
    return _union(out)


def scheduled_seconds(day: date, zone_name: str, shifts: Sequence[Shift]) -> int:
    total = 0
    for shift in shifts:
        if shift.weekday != day.weekday():
            continue
        start = local_instant(day, shift.start, zone_name)
        end = local_instant(day + timedelta(days=1) if shift.overnight else day, shift.end, zone_name)
        total += max(0, int((end - start).total_seconds()))
    return total


def _intervals(segments: Iterable[Segment]) -> list[Interval]:
    return [(s.start, s.end) for s in segments if s.end is not None]


def totals_for(day: date, zone_name: str, segments: Sequence[Segment], flags: Sequence[Flag], shifts: Sequence[Shift] = (),
               *, daily_overtime_minutes: int | None = None) -> DayTotals:
    """The totals of one shift day from that day's segments (and the flags of its events)."""
    work = [s for s in segments if s.kind in ("site", "remote")]
    breaks = [s for s in segments if s.kind == "break"]
    site_union = _union(_intervals(s for s in work if s.kind == "site"))
    remote_union = _union(_intervals(s for s in work if s.kind == "remote"))
    work_union = _union(site_union + remote_union)
    break_union = _intersect(_union(_intervals(breaks)), work_union)
    worked = _minus(work_union, break_union)
    verified_work = _union(_intervals(s for s in work if s.verified))
    verified = _minus(verified_work, break_union)
    estimated = _union((s.start, s.end) for s in work if s.end is not None and s.end_kind == "estimated")
    open_ = _union((s.start, s.end) for s in work if s.end is not None and s.end_kind == "open")
    result = DayTotals(day=day, zone=zone_name)
    result.presence_seconds = _seconds(site_union)
    result.remote_seconds = _seconds(remote_union)
    result.break_seconds = _seconds(break_union)
    result.worked_seconds = _seconds(worked)
    result.verified_seconds = _seconds(verified)
    result.estimated_seconds = _seconds(_minus(estimated, break_union))
    result.open_seconds = _seconds(_minus(open_, break_union))
    result.scheduled_seconds = scheduled_seconds(day, zone_name, shifts)
    if daily_overtime_minutes is not None:
        result.overtime_seconds = max(0, result.worked_seconds - daily_overtime_minutes * 60)
    site_segments = [s for s in work if s.kind == "site"]
    result.first_arrival = min((s.start for s in work), default=None)
    ended = [s.end for s in site_segments if s.end is not None and s.end_kind in ("event", "switch")]
    ended += [s.end for s in work if s.kind == "remote" and s.end is not None and s.end_kind == "event"]
    result.last_departure = max(ended, default=None)
    seen: list[str] = []
    for s in site_segments:
        if s.site_id and s.site_id not in seen:
            seen.append(s.site_id)
    result.site_ids = seen
    result.segments = sorted(segments, key=lambda s: (s.start, s.kind))
    result.flags = list(flags)
    return result


def days(events: Iterable[Event], *, now: datetime, shifts: Sequence[Shift] = (), default_zone: str = DEFAULT_ZONE,
         start: date | None = None, end: date | None = None, daily_overtime_minutes: int | None = None,
         include_scheduled: bool = True) -> list[DayTotals]:
    """Per shift day totals between ``start`` and ``end`` (inclusive), in day order.

    Days with only a schedule (no events) are included when ``include_scheduled`` is true and a
    range is given, so a scheduled day with no arrival shows up (and is flagged once past)."""
    events = list(events)
    played = replay(events, now=now, shifts=shifts)
    by_event = {e.id: e for e in events}
    grouped: dict[date, list[Segment]] = {}
    zones: dict[date, str] = {}
    work = [s for s in played.segments if s.kind != "break"]

    def shift_day(segment: Segment) -> date:
        """A break belongs to the shift it falls in (a break after midnight on a night shift)."""
        if segment.kind == "break":
            for owner in work:
                if owner.start <= segment.start and (owner.end is None or segment.start <= owner.end):
                    return owner.day
        return segment.day

    for segment in played.segments:
        key = shift_day(segment)
        grouped.setdefault(key, []).append(segment)
        if segment.kind != "break":
            zones.setdefault(key, segment.zone)
    day_flags: dict[date, list[Flag]] = {}
    for flag in played.flags:
        event = by_event.get(flag.event_id or "")
        if event is not None:
            day_flags.setdefault(local_day(event.at, event.zone), []).append(flag)
    wanted = set(grouped) | set(day_flags)
    if include_scheduled and start is not None and end is not None:
        cursor = start
        while cursor <= end:
            if scheduled_seconds(cursor, default_zone, shifts):
                wanted.add(cursor)
            cursor += timedelta(days=1)
    results: list[DayTotals] = []
    today = local_day(now, default_zone)
    for day in sorted(wanted):
        if (start is not None and day < start) or (end is not None and day > end):
            continue
        zone_name = zones.get(day, default_zone)
        result = totals_for(day, zone_name, grouped.get(day, []), day_flags.get(day, []), shifts,
                            daily_overtime_minutes=daily_overtime_minutes)
        if result.scheduled_seconds and not any(s.kind in ("site", "remote") for s in result.segments) and day < today:
            result.flags.append(Flag("no_arrival"))
        results.append(result)
    return results


def week_start(day: date, starts_on: int = 0) -> date:
    """The first day of the week holding ``day`` (``starts_on``: Monday 0 to Sunday 6)."""
    return day - timedelta(days=(day.weekday() - starts_on) % 7)


@dataclass
class PeriodTotals:
    start: date
    end: date
    days_worked: int = 0
    presence_seconds: int = 0
    remote_seconds: int = 0
    break_seconds: int = 0
    worked_seconds: int = 0
    verified_seconds: int = 0
    estimated_seconds: int = 0
    scheduled_seconds: int = 0
    overtime_seconds: int | None = None
    flags: dict[str, int] = field(default_factory=dict)


def period_totals(day_totals: Iterable[DayTotals], start: date, end: date, *, daily_overtime: bool = False,
                  weekly_overtime_minutes: int | None = None, week_starts_on: int = 0) -> PeriodTotals:
    """Sum of day totals within ``start`` to ``end``. Overtime (an estimate) is the larger of the
    daily overtime sum and, per week, worked time above the weekly threshold."""
    result = PeriodTotals(start=start, end=end)
    weeks: dict[date, int] = {}
    daily_sum = 0
    for item in day_totals:
        if not start <= item.day <= end:
            continue
        if item.worked_seconds:
            result.days_worked += 1
        result.presence_seconds += item.presence_seconds
        result.remote_seconds += item.remote_seconds
        result.break_seconds += item.break_seconds
        result.worked_seconds += item.worked_seconds
        result.verified_seconds += item.verified_seconds
        result.estimated_seconds += item.estimated_seconds
        result.scheduled_seconds += item.scheduled_seconds
        daily_sum += item.overtime_seconds or 0
        weeks[week_start(item.day, week_starts_on)] = weeks.get(week_start(item.day, week_starts_on), 0) + item.worked_seconds
        for code in item.flag_codes:
            result.flags[code] = result.flags.get(code, 0) + 1
    weekly_sum = 0
    if weekly_overtime_minutes is not None:
        weekly_sum = sum(max(0, worked - weekly_overtime_minutes * 60) for worked in weeks.values())
    if daily_overtime or weekly_overtime_minutes is not None:
        result.overtime_seconds = max(daily_sum, weekly_sum)
    return result


def parse_shift(weekday: int, start: str, end: str) -> Shift:
    """``"HH:MM"`` clock strings to a shift; raises ``ValueError`` on anything else."""
    if not 0 <= weekday <= 6:
        raise ValueError("weekday")
    return Shift(weekday, time.fromisoformat(start), time.fromisoformat(end))
