"""Repeating calendar events: the RFC 5545 RRULE subset ANUM stores and expands (docs/calendar.md#recurrence).

A series is one ``calendar_events`` row with a ``recurrence`` rule, a time zone and the first
occurrence's ``starts_at`` and ``ends_at``. Occurrences are never stored: they are computed for
the window a reader asks for, in the series time zone, so a 09:00 meeting stays at 09:00 across a
daylight-saving change. The subset is daily, weekly (on chosen weekdays), monthly (on a day of
the month or on the nth weekday) and yearly, with an interval and either an end date or a
count. Nothing here depends on a third-party library: the API has no RRULE dependency and the
subset is small enough to keep in one file with its own tests (``tests/test_calendar_recurrence.py``).

Exceptions live on the series: ``skipped`` holds the original start of every occurrence that
was deleted, and an occurrence changed on its own is a child row (``series_id``,
``recurrence_start``) that replaces the computed one.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UTC = timezone.utc
MAX_COUNT = 730
MAX_INTERVAL = 99
# A safety cap on the candidates one expansion walks (a daily series over ten years).
MAX_STEPS = 4000
WEEKDAYS: tuple[str, ...] = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
Weekday = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


def zone_of(name: str | None) -> ZoneInfo:
    """The IANA zone, or UTC when the name is missing or unknown."""
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")


def valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


class Recurrence(BaseModel):
    """The rule of a series. ``by_weekday`` names the weekdays of a weekly series (the start's
    weekday when empty) or, with ``by_setpos``, the nth weekday of a monthly one (``2`` and
    ``["TU"]`` is the second Tuesday; ``-1`` the last). ``by_monthday`` is a day of the month
    (``-1`` the last day). ``until`` is the last day (in the series time zone) an occurrence may
    start on; ``count`` the number of occurrences; at most one of them."""

    model_config = ConfigDict(extra="forbid")

    freq: Frequency
    interval: int = Field(default=1, ge=1, le=MAX_INTERVAL)
    by_weekday: list[Weekday] = Field(default_factory=list, max_length=7)
    by_monthday: int | None = Field(default=None, ge=-1, le=31)
    by_setpos: int | None = Field(default=None, ge=-1, le=4)
    until: date | None = None
    count: int | None = Field(default=None, ge=1, le=MAX_COUNT)

    @field_validator("by_weekday")
    @classmethod
    def _weekdays(cls, value: list[Weekday]) -> list[Weekday]:
        seen: list[Weekday] = []
        for day in value:
            if day not in seen:
                seen.append(day)
        return sorted(seen, key=WEEKDAYS.index)

    @model_validator(mode="after")
    def _shape(self) -> Recurrence:
        if self.until is not None and self.count is not None:
            raise ValueError("A repeat ends on a date or after a number of times, not both")
        if self.by_monthday == 0 or self.by_setpos == 0:
            raise ValueError("A day of the month or a position cannot be 0")
        if self.freq != Frequency.MONTHLY and (self.by_monthday is not None or self.by_setpos is not None):
            raise ValueError("A day of the month or an nth weekday applies to a monthly repeat only")
        if self.freq == Frequency.MONTHLY:
            if self.by_monthday is not None and self.by_weekday:
                raise ValueError("A monthly repeat is on a day of the month or on a weekday, not both")
            if self.by_setpos is not None and len(self.by_weekday) != 1:
                raise ValueError("An nth weekday needs exactly one weekday")
            if self.by_weekday and self.by_setpos is None:
                raise ValueError("A monthly repeat on a weekday needs its position (1 to 4, or -1 for the last)")
        if self.freq in (Frequency.DAILY, Frequency.YEARLY) and self.by_weekday:
            raise ValueError("Weekdays apply to a weekly or monthly repeat only")
        return self

    def rrule(self) -> str:
        """The rule as RFC 5545 text, for interoperability (``FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE``)."""
        parts = [f"FREQ={self.freq.value.upper()}"]
        if self.interval != 1:
            parts.append(f"INTERVAL={self.interval}")
        if self.by_weekday:
            prefix = str(self.by_setpos) if self.by_setpos is not None else ""
            parts.append("BYDAY=" + ",".join(f"{prefix}{day}" for day in self.by_weekday))
        if self.by_monthday is not None:
            parts.append(f"BYMONTHDAY={self.by_monthday}")
        if self.count is not None:
            parts.append(f"COUNT={self.count}")
        if self.until is not None:
            parts.append(f"UNTIL={self.until:%Y%m%d}")
        return ";".join(parts)

    @classmethod
    def parse(cls, text: str) -> Recurrence:
        """The subset read back from RRULE text; anything outside it is refused."""
        values: dict[str, object] = {}
        for raw in text.removeprefix("RRULE:").split(";"):
            if not raw.strip():
                continue
            if "=" not in raw:
                raise ValueError(f"Cannot read the repeat rule part {raw!r}")
            key, value = raw.split("=", 1)
            key, value = key.strip().upper(), value.strip().upper()
            if key == "FREQ":
                if value.lower() not in Frequency.__members__.values():
                    raise ValueError(f"Repeat frequency {value} is not supported")
                values["freq"] = value.lower()
            elif key == "INTERVAL":
                values["interval"] = int(value)
            elif key == "COUNT":
                values["count"] = int(value)
            elif key == "UNTIL":
                match = re.match(r"^(\d{4})(\d{2})(\d{2})", value)
                if not match:
                    raise ValueError("UNTIL must be a date (YYYYMMDD)")
                values["until"] = date(int(match[1]), int(match[2]), int(match[3]))
            elif key == "BYMONTHDAY":
                values["by_monthday"] = int(value)
            elif key == "BYDAY":
                days: list[str] = []
                position: int | None = None
                for item in value.split(","):
                    match = re.match(r"^([+-]?\d)?(MO|TU|WE|TH|FR|SA|SU)$", item.strip())
                    if not match:
                        raise ValueError(f"Cannot read the weekday {item!r}")
                    if match[1]:
                        position = int(match[1])
                    days.append(match[2])
                values["by_weekday"] = days
                if position is not None:
                    values["by_setpos"] = position
            else:
                raise ValueError(f"The repeat rule part {key} is not supported")
        if "freq" not in values:
            raise ValueError("A repeat rule needs FREQ")
        return cls.model_validate(values)


def _local(moment: datetime, zone: ZoneInfo) -> datetime:
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(zone)


def _instant(day: date, wall: time, zone: ZoneInfo) -> datetime:
    """The UTC instant of a wall-clock time on a day in the zone (a time skipped by a clock
    change moves forward with the clock, as the zoneinfo rules place it)."""
    return datetime.combine(day, wall.replace(tzinfo=None), tzinfo=zone).astimezone(UTC)


def _nth_weekday(year: int, month: int, weekday: int, position: int) -> date | None:
    """The nth (1 to 4) or last (-1) weekday (0 = Monday) of a month."""
    days = [date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1) if date(year, month, d).weekday() == weekday]
    if position == -1:
        return days[-1]
    return days[position - 1] if position <= len(days) else None


def _month_day(year: int, month: int, day: int) -> date | None:
    last = calendar.monthrange(year, month)[1]
    if day == -1:
        return date(year, month, last)
    return date(year, month, day) if day <= last else None


def _add_months(year: int, month: int, count: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + count
    return index // 12, index % 12 + 1


def _candidates(rule: Recurrence, first: date):
    """Occurrence days in order: the first occurrence's day (always, as the person placed it,
    even when the rule names other days), then the days the rule generates after it."""
    yield first
    for day in _generated(rule, first):
        if day > first:
            yield day


def _generated(rule: Recurrence, first: date):
    step = 0
    if rule.freq == Frequency.DAILY:
        while True:
            yield first + timedelta(days=step * rule.interval)
            step += 1
    elif rule.freq == Frequency.WEEKLY:
        weekdays = [WEEKDAYS.index(d) for d in rule.by_weekday] or [first.weekday()]
        monday = first - timedelta(days=first.weekday())
        while True:
            week = monday + timedelta(weeks=step * rule.interval)
            for weekday in weekdays:
                day = week + timedelta(days=weekday)
                if day >= first:
                    yield day
            step += 1
    elif rule.freq == Frequency.MONTHLY:
        while True:
            year, month = _add_months(first.year, first.month, step * rule.interval)
            if rule.by_weekday and rule.by_setpos is not None:
                day = _nth_weekday(year, month, WEEKDAYS.index(rule.by_weekday[0]), rule.by_setpos)
            else:
                day = _month_day(year, month, rule.by_monthday if rule.by_monthday is not None else first.day)
            if day is not None and day >= first:
                yield day
            step += 1
    else:
        while True:
            year = first.year + step * rule.interval
            day = _month_day(year, first.month, first.day)
            if day is not None:
                yield day
            step += 1


def occurrences(
    rule: Recurrence,
    starts_at: datetime,
    zone_name: str | None,
    window_start: datetime,
    window_end: datetime,
    *,
    duration: timedelta = timedelta(0),
    limit: int = 1000,
) -> list[datetime]:
    """The UTC starts of the series' occurrences that overlap ``[window_start, window_end)``,
    earliest first, at most ``limit``. The first occurrence is ``starts_at`` itself (the rule's
    weekdays or month day may add others in the same period; none before it)."""
    zone = zone_of(zone_name)
    first = _local(starts_at, zone)
    wall = first.timetz().replace(tzinfo=None)
    window_start = _local(window_start, UTC)
    window_end = _local(window_end, UTC)
    out: list[datetime] = []
    seen = 0
    for steps, day in enumerate(_candidates(rule, first.date())):
        if steps >= MAX_STEPS or (rule.count is not None and seen >= rule.count) or (rule.until is not None and day > rule.until):
            break
        seen += 1
        start = _instant(day, wall, zone)
        if start >= window_end:
            break
        if start + duration > window_start:
            out.append(start)
            if len(out) >= limit:
                break
    return out


def series_end(rule: Recurrence, starts_at: datetime, zone_name: str | None, duration: timedelta) -> datetime | None:
    """When the last occurrence ends (UTC), or None for a series without an end."""
    if rule.until is None and rule.count is None:
        return None
    far = datetime(9999, 12, 31, tzinfo=UTC)
    starts = occurrences(rule, starts_at, zone_name, starts_at - timedelta(days=1), far, duration=duration, limit=MAX_COUNT)
    return (starts[-1] + duration) if starts else starts_at + duration


def occurrence_count_before(rule: Recurrence, starts_at: datetime, zone_name: str | None, moment: datetime) -> int:
    """How many occurrences start before ``moment`` (used to split a counted series)."""
    return len(occurrences(rule, starts_at, zone_name, starts_at - timedelta(days=1), moment, limit=MAX_COUNT))


def all_day_bounds(start_day: date, end_day: date, zone_name: str | None) -> tuple[datetime, datetime]:
    """The UTC instants an all-day event covers: local midnight of its first day to local
    midnight after its last day (``end_day`` is inclusive)."""
    zone = zone_of(zone_name)
    return _instant(start_day, time(0, 0), zone), _instant(end_day + timedelta(days=1), time(0, 0), zone)


def all_day_days(starts_at: datetime, ends_at: datetime, zone_name: str | None) -> tuple[date, date]:
    """The first and last (inclusive) local day of an all-day event stored as instants."""
    zone = zone_of(zone_name)
    first = _local(starts_at, zone).date()
    last = (_local(ends_at, zone) - timedelta(seconds=1)).date()
    return first, max(first, last)
