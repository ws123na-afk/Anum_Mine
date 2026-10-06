from datetime import datetime, timezone

import pytest

from anum_api.cron import CronError, CronExpression, validate


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_daily_expression_fires_at_the_next_matching_minute() -> None:
    daily = CronExpression.parse("0 8 * * *")

    assert daily.next_after(utc(2026, 10, 6, 7, 59)) == utc(2026, 10, 6, 8, 0)
    # Strictly after: the fire time itself moves to the next day.
    assert daily.next_after(utc(2026, 10, 6, 8, 0)) == utc(2026, 10, 7, 8, 0)


def test_steps_ranges_lists_and_names() -> None:
    expression = CronExpression.parse("*/15 9-17 * JAN,jul MON-FRI")

    assert expression.minutes == {0, 15, 30, 45}
    assert expression.hours == set(range(9, 18))
    assert expression.months == {1, 7}
    assert expression.weekdays == {1, 2, 3, 4, 5}
    # Saturday 2026-07-04 -> Monday 2026-07-06 09:00.
    assert expression.next_after(utc(2026, 7, 4, 12, 0)) == utc(2026, 7, 6, 9, 0)


def test_day_of_month_and_weekday_match_either_when_both_are_restricted() -> None:
    expression = CronExpression.parse("0 0 1 * 0")  # the 1st, or any Sunday

    # 2026-10-06 is a Tuesday: Sunday the 11th comes before 1 November.
    assert expression.next_after(utc(2026, 10, 6)) == utc(2026, 10, 11)
    assert CronExpression.parse("0 0 * * 7").weekdays == {0}


def test_schedule_runs_in_its_own_time_zone() -> None:
    riyadh = CronExpression.parse("0 9 * * *")

    assert riyadh.next_after(utc(2026, 10, 6, 0, 0), "Asia/Riyadh") == utc(2026, 10, 6, 6, 0)


def test_daylight_saving_gap_fires_once_after_the_jump() -> None:
    expression = CronExpression.parse("30 2 * * *")

    # 2026-03-08 02:30 does not exist in New York; the fire lands after the jump.
    fired = expression.next_after(utc(2026, 3, 8, 5, 0), "America/New_York")
    assert fired.date() == datetime(2026, 3, 8).date()
    assert fired > utc(2026, 3, 8, 5, 0)


@pytest.mark.parametrize(
    "expression",
    ["* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "5-1 * * * *", "*/0 * * * *", "a * * * *", "1,,2 * * * *"],
)
def test_invalid_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(CronError):
        CronExpression.parse(expression)


def test_expressions_that_never_fire_and_unknown_zones_are_rejected() -> None:
    with pytest.raises(CronError, match="never fires"):
        validate("0 0 30 2 *")
    with pytest.raises(CronError, match="time zone"):
        validate("0 8 * * *", "Mars/Olympus")
