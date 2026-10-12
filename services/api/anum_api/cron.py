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


_FIELDS = (
    # (label, low, high, names, example)
    ("Minute", 0, 59, None, "0, */15 or 0,30"),
    ("Hour", 0, 23, None, "9, 9-17 or */2"),
    ("Day-of-month", 1, 31, None, "1, 1,15 or 1-7"),
    ("Month", 1, 12, _MONTHS, "1-12 or JAN-DEC"),
    ("Day-of-week", 0, 7, _WEEKDAYS, "0-6 (0 and 7 are Sunday) or SUN-SAT, e.g. 1-5"),
)
FIVE_FIELDS_HELP = (
    'Use five fields separated by spaces: minute hour day-of-month month day-of-week, '
    'for example "0 9 * * 1-5" for 09:00 on weekdays.'
)


def _parse_value(token: str, label: str, low: int, high: int, names: dict[str, int] | None) -> int:
    upper = token.upper()
    if names and upper in names:
        return names[upper]
    if not token.isdigit():
        allowed = f"{low}-{high}" + (" or a three-letter name" if names else "")
        raise CronError(f"{label} field: {token!r} is not a number (allowed: {allowed}).")
    value = int(token)
    if not low <= value <= high:
        raise CronError(f"{label} field: {value} is outside {low}-{high}.")
    return value


def _parse_field(field: str, index: int) -> frozenset[int]:
    label, low, high, names, example = _FIELDS[index]
    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise CronError(f"{label} field: empty list item in {field!r}; separate values with single commas, e.g. {example}.")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"{label} field: invalid step in {part!r}; a step is a whole number of 1 or more, e.g. */15.")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start = _parse_value(first, label, low, high, names)
            end = _parse_value(last, label, low, high, names)
            if start > end:
                raise CronError(f"{label} field: range {part!r} runs backwards; write the smaller value first.")
        else:
            start = _parse_value(base, label, low, high, names)
            end = high if step_text else start
        values.update(range(start, end + 1, step))
    return frozenset(values)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronError(
            f"Unknown time zone: {name!r}. Use an IANA time zone name such as UTC, Europe/London or Asia/Riyadh."
        ) from exc


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
            raise CronError(f"Cron expressions must contain five fields; this one has {len(fields)}. {FIVE_FIELDS_HELP}")
        minute, hour, day, month, weekday = fields
        weekdays = {0 if value == 7 else value for value in _parse_field(weekday, 4)}
        return cls(
            minutes=_parse_field(minute, 0),
            hours=_parse_field(hour, 1),
            days=_parse_field(day, 2),
            months=_parse_field(month, 3),
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
        raise CronError(
            "Cron expression never fires (for example day 30 of February). Check the day-of-month and month fields."
        )


def validate(expression: str, timezone_name: str = "UTC") -> None:
    """Raise ``CronError`` unless the expression fires at some point in this zone."""
    CronExpression.parse(expression).next_after(datetime.now(timezone.utc), timezone_name)


def upcoming(expression: str, timezone_name: str = "UTC", *, count: int = 5, after: datetime | None = None) -> list[datetime]:
    """The next ``count`` fire times strictly after ``after`` (default: now), in UTC."""
    parsed = CronExpression.parse(expression)
    moment = after or datetime.now(timezone.utc)
    times: list[datetime] = []
    for _ in range(max(0, count)):
        moment = parsed.next_after(moment, timezone_name)
        times.append(moment)
    return times


# Human-readable descriptions ---------------------------------------------------------------
#
# ``describe(expression, locale)`` reads an expression in English (``en``) or Arabic
# (``ar``). Both use one structure; only the phrases differ.

_MONTH_NAMES = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس",
           "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
}
_WEEKDAY_NAMES = {
    "en": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
    "ar": ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"],
}
LOCALES = ("en", "ar")

_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "sep": ", ",
        "and": " and ",
        "every_minute": "Every minute",
        "every_n_minutes": "Every {n} minutes",
        "every_hour_on_hour": "Every hour, on the hour",
        "every_hour_at": "Every hour at {m} minutes past",
        "every_n_hours": "Every {n} hours, {at}",
        "on_the_hour": "on the hour",
        "at_minutes_past": "at {m} minutes past",
        "at_times": "At {times}",
        "between": "{lead}, between {start} and {end}",
        "during_hours": "{lead}, during hours {hours}",
        "minute_past_hour": "At minute {minutes} past hour {hours}",
        "day_of_month": "on day {days} of the month",
        "through": "{first} through {last}",
        "on_weekday": "on {names}",
        "day_or_weekday": "{day} or {weekday}",
        "every_day": "every day",
        "in_months": "in {months}",
    },
    "ar": {
        "sep": "، ",
        "and": " و",
        "every_minute": "كل دقيقة",
        "every_n_minutes": "كل {n}",
        "every_hour_on_hour": "كل ساعة، عند بداية الساعة",
        "every_hour_at": "كل ساعة عند الدقيقة {m}",
        "every_n_hours": "كل {n}، {at}",
        "on_the_hour": "عند بداية الساعة",
        "at_minutes_past": "عند الدقيقة {m}",
        "at_times": "في الساعة {times}",
        "between": "{lead}، بين {start} و{end}",
        "during_hours": "{lead}، خلال الساعات {hours}",
        "minute_past_hour": "عند الدقيقة {minutes} من الساعة {hours}",
        "day_of_month": "في اليوم {days} من الشهر",
        "through": "من {first} إلى {last}",
        "on_weekday": "يوم {names}",
        "day_or_weekday": "{day} أو {weekday}",
        "every_day": "كل يوم",
        "in_months": "في {months}",
    },
}


def _ar_count(n: int, one: str, two: str, few: str) -> str:
    """Arabic counted noun: 1 and 2 have their own forms, 3-10 the plural, 11+ the singular."""
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n <= 10:
        return f"{n} {few}"
    return f"{n} {one}"


def _count(n: int, unit: str, locale: str) -> str:
    if locale == "ar":
        return _ar_count(n, "دقيقة", "دقيقتين", "دقائق") if unit == "minute" else _ar_count(n, "ساعة", "ساعتين", "ساعات")
    return str(n)


def _locale(locale: str) -> str:
    return locale if locale in _PHRASES else "en"


def _join(items: list[str], locale: str = "en") -> str:
    if len(items) <= 1:
        return "".join(items)
    phrases = _PHRASES[_locale(locale)]
    return f"{phrases['sep'].join(items[:-1])}{phrases['and']}{items[-1]}"


def _runs(values: list[int]) -> list[tuple[int, int]]:
    """Contiguous runs in sorted values: [1, 2, 3, 5] -> [(1, 3), (5, 5)]."""
    runs: list[tuple[int, int]] = []
    for value in values:
        if runs and value == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], value)
        else:
            runs.append((value, value))
    return runs


def _named(values: frozenset[int], names: list[str], offset: int, locale: str = "en") -> tuple[str, bool]:
    """The names joined, and whether a single range ("Monday through Friday") was used."""
    phrases = _PHRASES[_locale(locale)]
    parts: list[str] = []
    ranged = False
    for first, last in _runs(sorted(values)):
        if last - first >= 2:
            ranged = True
            parts.append(phrases["through"].format(first=names[first - offset], last=names[last - offset]))
        else:
            parts.extend(names[value - offset] for value in range(first, last + 1))
    return _join(parts, locale), ranged


def _step(field: str) -> int | None:
    """``*/n`` -> n; anything else -> None."""
    if field.startswith("*/") and field[2:].isdigit():
        return int(field[2:])
    return None


def _clock(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _time_phrase(minute: str, hour: str, parsed: CronExpression, locale: str = "en") -> tuple[str, bool]:
    """The time-of-day part, and whether it names fixed clock times."""
    p = _PHRASES[locale]
    minutes, hours = sorted(parsed.minutes), sorted(parsed.hours)
    every_hour = hour == "*"
    minute_step, hour_step = _step(minute), _step(hour)
    numbers = lambda values: _join([str(v) for v in values], locale)  # noqa: E731
    if minute == "*" and every_hour:
        return p["every_minute"], False
    if minute_step and every_hour:
        return p["every_n_minutes"].format(n=_count(minute_step, "minute", locale)), False
    if len(minutes) == 1 and every_hour:
        return (p["every_hour_on_hour"] if minutes[0] == 0 else p["every_hour_at"].format(m=minutes[0])), False
    if every_hour and minute != "*" and not minute_step:
        return p["every_hour_at"].format(m=numbers(minutes)), False
    if len(minutes) == 1 and hour_step:
        at = p["on_the_hour"] if minutes[0] == 0 else p["at_minutes_past"].format(m=minutes[0])
        return p["every_n_hours"].format(n=_count(hour_step, "hour", locale), at=at), False
    if minute != "*" and not minute_step and len(minutes) * len(hours) <= 6:
        return p["at_times"].format(times=_join([_clock(h, m) for h in hours for m in minutes], locale)), True
    if minute == "*" or minute_step:
        lead = p["every_minute"] if minute == "*" else p["every_n_minutes"].format(n=_count(minute_step or 1, "minute", locale))
        hour_runs = _runs(hours)
        if len(hour_runs) == 1:
            first, last = hour_runs[0]
            return p["between"].format(lead=lead, start=_clock(first, 0), end=_clock(last, 59)), False
        return p["during_hours"].format(lead=lead, hours=numbers(hours)), False
    return p["minute_past_hour"].format(minutes=numbers(minutes), hours=numbers(hours)), False


def describe(expression: str, locale: str = "en") -> str:
    """A plain reading of a valid five-field expression in ``en`` or ``ar`` (raises ``CronError``)."""
    locale = _locale(locale)
    p = _PHRASES[locale]
    parsed = CronExpression.parse(expression)
    minute, hour, day, month, weekday = expression.split()
    time_text, fixed = _time_phrase(minute, hour, parsed, locale)
    parts = [time_text]
    day_text = p["day_of_month"].format(days=_join([str(d) for d in sorted(parsed.days)], locale)) if day != "*" else ""
    weekday_text = ""
    if weekday != "*" and len(parsed.weekdays) < 7:
        names, ranged = _named(parsed.weekdays, _WEEKDAY_NAMES[locale], 0, locale)
        # "Monday through Friday" reads without "on"; Arabic only for a single range.
        bare = ranged and (locale == "en" or len(_runs(sorted(parsed.weekdays))) == 1)
        weekday_text = names if bare else p["on_weekday"].format(names=names)
    if day_text and weekday_text:
        parts.append(p["day_or_weekday"].format(day=day_text, weekday=weekday_text))
    elif day_text or weekday_text:
        parts.append(day_text or weekday_text)
    elif fixed:
        parts.append(p["every_day"])
    if month != "*":
        parts.append(p["in_months"].format(months=_named(parsed.months, _MONTH_NAMES[locale], 1, locale)[0]))
    return p["sep"].join(parts)
