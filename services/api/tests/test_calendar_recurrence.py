"""The RRULE subset of repeating events (docs/calendar.md#recurrence): rules, text, expansion."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from anum_api.calendar_recurrence import (
    Recurrence, all_day_bounds, all_day_days, occurrence_count_before, occurrences, series_end, valid_zone,
)

UTC = timezone.utc


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def dubai(*parts: int) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime(*parts, tzinfo=ZoneInfo("Asia/Dubai")).astimezone(UTC)


def starts(rule: Recurrence, first: datetime, zone: str, window_from: datetime, window_to: datetime, **kw):  # type: ignore[no-untyped-def]
    return occurrences(rule, first, zone, window_from, window_to, **kw)


def test_rules_round_trip_through_rrule_text() -> None:
    rule = Recurrence(freq="weekly", interval=2, by_weekday=["WE", "MO"], until=date(2026, 12, 31))
    assert rule.by_weekday == ["MO", "WE"]
    assert rule.rrule() == "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;UNTIL=20261231"
    assert Recurrence.parse("RRULE:" + rule.rrule()) == rule
    monthly = Recurrence.parse("FREQ=MONTHLY;BYDAY=-1FR;COUNT=6")
    assert monthly.by_setpos == -1 and monthly.by_weekday == ["FR"] and monthly.count == 6
    assert monthly.rrule() == "FREQ=MONTHLY;BYDAY=-1FR;COUNT=6"
    assert Recurrence.parse("FREQ=MONTHLY;BYMONTHDAY=-1").rrule() == "FREQ=MONTHLY;BYMONTHDAY=-1"
    assert Recurrence.parse("FREQ=DAILY").rrule() == "FREQ=DAILY"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("FREQ=HOURLY", "not supported"),
        ("FREQ=WEEKLY;BYSECOND=1", "not supported"),
        ("INTERVAL=2", "needs FREQ"),
        ("FREQ=DAILY;COUNT=3;UNTIL=20270101", "not both"),
        ("FREQ=MONTHLY;BYDAY=MO", "needs its position"),
        ("FREQ=MONTHLY;BYDAY=1MO,2TU", "exactly one weekday"),
        ("FREQ=DAILY;BYDAY=MO", "weekly or monthly"),
        ("FREQ=WEEKLY;BYMONTHDAY=3", "monthly repeat only"),
        ("FREQ=DAILY;INTERVAL=0", "greater than or equal to 1"),
        ("FREQ=DAILY;COUNT=5000", "less than or equal to 730"),
    ],
)
def test_rules_outside_the_subset_are_refused(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Recurrence.parse(text)


def test_daily_and_weekly_expansion_stays_on_the_wall_clock_across_a_clock_change() -> None:
    from zoneinfo import ZoneInfo

    london = ZoneInfo("Europe/London")
    first = datetime(2026, 10, 22, 9, 0, tzinfo=london).astimezone(UTC)  # BST, UTC+1
    found = starts(Recurrence(freq="daily"), first, "Europe/London", utc(2026, 10, 24), utc(2026, 10, 27))
    assert [s.astimezone(london).hour for s in found] == [9, 9, 9]
    assert found[0] == utc(2026, 10, 24, 8) and found[-1] == utc(2026, 10, 26, 9)  # clocks went back on the 25th
    weekly = Recurrence(freq="weekly", by_weekday=["MO", "WE"])
    found = starts(weekly, dubai(2026, 11, 2, 10), "Asia/Dubai", utc(2026, 11, 1), utc(2026, 11, 15))
    assert found == [dubai(2026, 11, 2, 10), dubai(2026, 11, 4, 10), dubai(2026, 11, 9, 10), dubai(2026, 11, 11, 10)]


def test_the_first_occurrence_is_the_start_even_when_the_rule_names_other_days() -> None:
    # A Wednesday start repeating on Mondays: the Wednesday itself counts, then Mondays.
    rule = Recurrence(freq="weekly", by_weekday=["MO"])
    found = starts(rule, dubai(2026, 11, 4, 9), "Asia/Dubai", utc(2026, 11, 1), utc(2026, 11, 20))
    assert found == [dubai(2026, 11, 4, 9), dubai(2026, 11, 9, 9), dubai(2026, 11, 16, 9)]


def test_monthly_by_day_skips_short_months_and_last_weekday_works() -> None:
    thirty_first = Recurrence(freq="monthly")
    found = starts(thirty_first, dubai(2026, 1, 31, 8), "Asia/Dubai", utc(2026, 1, 1), utc(2026, 6, 1))
    assert [s.astimezone(timezone(timedelta(hours=4))).date() for s in found] == [date(2026, 1, 31), date(2026, 3, 31), date(2026, 5, 31)]
    last_friday = Recurrence(freq="monthly", by_weekday=["FR"], by_setpos=-1)
    found = starts(last_friday, dubai(2026, 11, 27, 15), "Asia/Dubai", utc(2026, 11, 1), utc(2027, 2, 1))
    assert [s.astimezone(timezone(timedelta(hours=4))).date() for s in found] == [date(2026, 11, 27), date(2026, 12, 25), date(2027, 1, 29)]
    last_day = Recurrence(freq="monthly", by_monthday=-1, interval=2)
    found = starts(last_day, dubai(2026, 2, 28, 9), "Asia/Dubai", utc(2026, 2, 1), utc(2026, 7, 1))
    assert [s.astimezone(timezone(timedelta(hours=4))).date() for s in found] == [date(2026, 2, 28), date(2026, 4, 30), date(2026, 6, 30)]


def test_yearly_on_29_february_happens_in_leap_years_only() -> None:
    rule = Recurrence(freq="yearly")
    found = starts(rule, utc(2024, 2, 29, 12), "UTC", utc(2024, 1, 1), utc(2033, 1, 1))
    assert [s.year for s in found] == [2024, 2028, 2032]


def test_count_and_until_bound_the_series_and_series_end_is_the_last_occurrence() -> None:
    rule = Recurrence(freq="daily", count=3)
    first = utc(2026, 11, 10, 7)
    assert starts(rule, first, "UTC", utc(2026, 11, 1), utc(2026, 12, 1)) == [first, first + timedelta(days=1), first + timedelta(days=2)]
    # The count is counted from the series start, not from the window.
    assert starts(rule, first, "UTC", utc(2026, 11, 12), utc(2026, 12, 1)) == [first + timedelta(days=2)]
    assert series_end(rule, first, "UTC", timedelta(hours=1)) == utc(2026, 11, 12, 8)
    until = Recurrence(freq="weekly", until=date(2026, 11, 24))
    assert series_end(until, first, "UTC", timedelta(hours=1)) == utc(2026, 11, 24, 8)
    assert series_end(Recurrence(freq="daily"), first, "UTC", timedelta(hours=1)) is None
    assert occurrence_count_before(Recurrence(freq="daily"), first, "UTC", utc(2026, 11, 13, 7)) == 3


def test_a_long_event_overlapping_the_window_is_included_and_the_limit_holds() -> None:
    rule = Recurrence(freq="daily")
    first = utc(2026, 11, 1, 22)
    found = starts(rule, first, "UTC", utc(2026, 11, 3), utc(2026, 11, 4), duration=timedelta(hours=4))
    assert found == [utc(2026, 11, 2, 22), utc(2026, 11, 3, 22)]
    assert len(starts(rule, first, "UTC", utc(2026, 11, 1), utc(2027, 11, 1), limit=5)) == 5


def test_all_day_bounds_follow_the_zone() -> None:
    begin, end = all_day_bounds(date(2026, 11, 10), date(2026, 11, 11), "Asia/Dubai")
    assert begin == utc(2026, 11, 9, 20) and end == utc(2026, 11, 11, 20)
    assert all_day_days(begin, end, "Asia/Dubai") == (date(2026, 11, 10), date(2026, 11, 11))
    assert all_day_days(begin, end, "UTC") == (date(2026, 11, 9), date(2026, 11, 11))
    assert valid_zone("Asia/Riyadh") and not valid_zone("Mars/Olympus")
