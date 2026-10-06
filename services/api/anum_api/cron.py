"""Five-field cron expressions for automation schedules.

Fields are minute, hour, day of month, month and day of week. Each field accepts ``*``,
numbers, ranges (``1-5``), steps (``*/15``, ``10-50/10``) and comma lists; months and
weekdays also accept three-letter names (``JAN``, ``MON``). Day of week runs 0-7 with
both 0 and 7 meaning Sunday. As in Vixie cron, when both day of month and day of week
are restricted a day matches if either does.

Times are evaluated in the schedule's IANA time zone and returned in UTC. A local time
skipped by a daylight-saving jump fires at the equivalent instant after the jump; a
repeated local time fires once (the first occurrence).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_MONTHS = {name: index for index, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1
)}
_WEEKDAYS = {name: index for index, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}
# Long enough for "29 February on a given weekday"-style expressions to recur.
_SEARCH_DAYS = 366 * 8 + 2


class CronError(ValueError):
    """The expression or time zone is not valid."""


def _parse_value(token: str, low: int, high: int, names: dict[str, int] | None) -> int:
    upper = token.upper()
    if names and upper in names:
        return names[upper]
    if not token.isdigit():
        raise CronError(f"Invalid cron value: {token!r}")
    value = int(token)
    if not low <= value <= high:
        raise CronError(f"Cron value {value} is outside {low}-{high}")
    return value


def _parse_field(field: str, low: int, high: int, names: dict[str, int] | None = None) -> frozenset[int]:
    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise CronError("Empty cron list item")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"Invalid cron step: {part!r}")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _parse_value(first, low, high, names), _parse_value(last, low, high, names)
            if start > end:
                raise CronError(f"Invalid cron range: {part!r}")
        else:
            start = _parse_value(base, low, high, names)
            end = high if step_text else start
        values.update(range(start, end + 1, step))
    return frozenset(values)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronError(f"Unknown time zone: {name!r}") from exc


@dataclass(frozen=True)
class CronExpression:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    day_restricted: bool
    weekday_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> CronExpression:
        fields = expression.split()
        if len(fields) != 5:
            raise CronError("Cron expressions must contain five fields")
        minute, hour, day, month, weekday = fields
        weekdays = {0 if value == 7 else value for value in _parse_field(weekday, 0, 7, _WEEKDAYS)}
        return cls(
            minutes=_parse_field(minute, 0, 59),
            hours=_parse_field(hour, 0, 23),
            days=_parse_field(day, 1, 31),
            months=_parse_field(month, 1, 12, _MONTHS),
            weekdays=frozenset(weekdays),
            day_restricted=day != "*",
            weekday_restricted=weekday != "*",
        )

    def _day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        by_day = day.day in self.days
        by_weekday = (day.isoweekday() % 7) in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return by_day or by_weekday
        return by_day and by_weekday

    def next_after(self, after: datetime, timezone_name: str = "UTC") -> datetime:
        """The first fire time strictly after ``after`` (aware), in UTC."""
        if after.tzinfo is None:
            raise ValueError("after must be timezone-aware")
        tz = zone(timezone_name)
        local = after.astimezone(tz)
        start_day = local.date()
        hours = sorted(self.hours)
        minutes = sorted(self.minutes)
        for offset in range(_SEARCH_DAYS):
            day = start_day + timedelta(days=offset)
            if not self._day_matches(day):
                continue
            for hour in hours:
                for minute in minutes:
                    candidate = datetime.combine(day, time(hour, minute), tzinfo=tz).astimezone(timezone.utc)
                    if candidate > after:
                        return candidate
        raise CronError("Cron expression never fires")


def validate(expression: str, timezone_name: str = "UTC") -> None:
    """Raise ``CronError`` unless the expression fires at some point in this zone."""
    CronExpression.parse(expression).next_after(datetime.now(timezone.utc), timezone_name)
