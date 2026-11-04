"""The attendance time engine (anum_api/attendance/engine.py, docs/attendance.md#time-engine)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from anum_api.attendance import engine as E

H = 3600


def at(zone: str, *parts: int) -> datetime:
    return datetime(*parts, tzinfo=ZoneInfo(zone))  # type: ignore[misc]


def ev(i: int, kind: str, when: datetime, zone: str = "Asia/Dubai", **extra: object) -> E.Event:
    return E.Event(id=f"e{i}", kind=kind, at=when, zone=zone, **extra)  # type: ignore[arg-type]


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def one_day(events: list[E.Event], **kwargs: object) -> E.DayTotals:
    result = E.days(events, now=kwargs.pop("now", NOW), **kwargs)  # type: ignore[arg-type]
    assert len(result) == 1, [d.day for d in result]
    return result[0]


def test_a_plain_day_in_dubai_counts_presence_breaks_and_verified_time() -> None:
    z = "Asia/Dubai"
    day = one_day([
        ev(1, "arrive", at(z, 2026, 11, 2, 8, 0), site_id="office"),
        ev(2, "break_start", at(z, 2026, 11, 2, 12, 0)),
        ev(3, "break_end", at(z, 2026, 11, 2, 12, 30)),
        ev(4, "leave", at(z, 2026, 11, 2, 17, 0), site_id="office"),
    ])
    assert day.day == date(2026, 11, 2)
    assert day.presence_seconds == 9 * H
    assert day.break_seconds == H // 2
    assert day.worked_seconds == day.verified_seconds == int(8.5 * H)
    assert day.site_ids == ["office"]
    assert day.flag_codes == []


def test_riyadh_has_no_daylight_saving_so_wall_clock_and_elapsed_time_agree() -> None:
    z = "Asia/Riyadh"
    day = one_day([ev(1, "arrive", at(z, 2026, 3, 29, 7, 0), z), ev(2, "leave", at(z, 2026, 3, 29, 15, 0), z)])
    assert day.worked_seconds == 8 * H
    assert E.scheduled_seconds(date(2026, 3, 29), z, [E.parse_shift(6, "07:00", "15:00")]) == 8 * H


def test_overnight_shift_counts_on_the_day_it_started() -> None:
    z = "Asia/Dubai"
    result = E.days([ev(1, "arrive", at(z, 2026, 11, 3, 22, 0)), ev(2, "leave", at(z, 2026, 11, 4, 6, 0))], now=NOW)
    assert [d.day for d in result] == [date(2026, 11, 3)]
    assert result[0].worked_seconds == 8 * H
    shift = E.parse_shift(date(2026, 11, 3).weekday(), "22:00", "06:00")
    assert shift.overnight and E.scheduled_seconds(date(2026, 11, 3), z, [shift]) == 8 * H


def test_overnight_shift_across_spring_forward_lasts_seven_hours() -> None:
    z = "Europe/London"  # clocks go forward at 01:00 on 29 March 2026
    day = one_day([ev(1, "arrive", at(z, 2026, 3, 28, 22, 0), z), ev(2, "leave", at(z, 2026, 3, 29, 6, 0), z)])
    assert day.day == date(2026, 3, 28)
    assert day.worked_seconds == 7 * H
    shift = E.parse_shift(date(2026, 3, 28).weekday(), "22:00", "06:00")
    assert E.scheduled_seconds(date(2026, 3, 28), z, [shift]) == 7 * H


def test_overnight_shift_across_fall_back_lasts_nine_hours() -> None:
    z = "America/New_York"  # clocks go back at 02:00 on 1 November 2026
    day = one_day([ev(1, "arrive", at(z, 2026, 10, 31, 22, 0), z), ev(2, "leave", at(z, 2026, 11, 1, 6, 0), z)])
    assert day.worked_seconds == 9 * H
    shift = E.parse_shift(date(2026, 10, 31).weekday(), "22:00", "06:00")
    assert E.scheduled_seconds(date(2026, 10, 31), z, [shift]) == 9 * H


def test_a_clock_time_in_the_spring_gap_moves_forward() -> None:
    z = "America/New_York"  # 02:30 does not exist on 8 March 2026
    instant = E.local_instant(date(2026, 3, 8), time(2, 30), z)
    assert instant.astimezone(ZoneInfo(z)).hour == 3
    # The repeated hour in November takes its first occurrence (still daylight time).
    repeat = E.local_instant(date(2026, 11, 1), time(1, 30), z)
    assert repeat.astimezone(ZoneInfo(z)).utcoffset() == timedelta(hours=-4)


def test_time_zone_change_mid_week_keeps_each_day_in_its_own_zone() -> None:
    events = [
        ev(1, "arrive", at("Asia/Dubai", 2026, 11, 2, 9, 0), "Asia/Dubai"),
        ev(2, "leave", at("Asia/Dubai", 2026, 11, 2, 17, 0), "Asia/Dubai"),
        # Flew to London: Wednesday starts at 23:30 London time, already Thursday in Dubai.
        ev(3, "arrive", at("Europe/London", 2026, 11, 4, 23, 30), "Europe/London"),
        ev(4, "leave", at("Europe/London", 2026, 11, 5, 3, 30), "Europe/London"),
    ]
    result = E.days(events, now=NOW)
    assert [(d.day, d.zone, d.worked_seconds) for d in result] == [
        (date(2026, 11, 2), "Asia/Dubai", 8 * H),
        (date(2026, 11, 4), "Europe/London", 4 * H),
    ]
    week = E.period_totals(result, date(2026, 11, 2), date(2026, 11, 8), weekly_overtime_minutes=10 * 60)
    assert week.worked_seconds == 12 * H and week.days_worked == 2 and week.overtime_seconds == 2 * H


def test_missing_checkout_ends_at_the_scheduled_end_as_an_estimate_never_verified() -> None:
    z = "Asia/Dubai"
    monday = date(2026, 11, 2)
    shifts = [E.parse_shift(monday.weekday(), "08:00", "16:00")]
    day = one_day([ev(1, "arrive", at(z, 2026, 11, 2, 8, 30))], shifts=shifts)
    assert "missing_checkout" in day.flag_codes
    assert day.presence_seconds == day.worked_seconds == day.estimated_seconds == int(7.5 * H)
    assert day.verified_seconds == 0
    # Without a schedule there is nothing to estimate from: zero, still flagged.
    bare = one_day([ev(1, "arrive", at(z, 2026, 11, 2, 8, 30))])
    assert bare.worked_seconds == 0 and "missing_checkout" in bare.flag_codes


def test_an_arrival_today_is_ongoing_up_to_now_and_not_verified() -> None:
    z = "Asia/Dubai"
    now = at(z, 2026, 11, 20, 12, 0)
    day = one_day([ev(1, "arrive", at(z, 2026, 11, 20, 9, 0))], now=now)
    assert day.open_seconds == day.worked_seconds == 3 * H
    assert day.verified_seconds == 0 and day.flag_codes == []


def test_duplicate_arrivals_and_breaks_are_ignored_and_flagged() -> None:
    z = "Asia/Dubai"
    day = one_day([
        ev(1, "arrive", at(z, 2026, 11, 2, 8, 0), site_id="office"),
        ev(2, "arrive", at(z, 2026, 11, 2, 8, 1), site_id="office"),
        ev(3, "break_start", at(z, 2026, 11, 2, 12, 0)),
        ev(4, "break_start", at(z, 2026, 11, 2, 12, 5)),
        ev(5, "break_end", at(z, 2026, 11, 2, 12, 30)),
        ev(6, "leave", at(z, 2026, 11, 2, 16, 0), site_id="office"),
        ev(7, "leave", at(z, 2026, 11, 2, 16, 1), site_id="office"),
    ])
    assert day.worked_seconds == int(7.5 * H)
    assert sorted(f.event_id for f in day.flags if f.code in ("duplicate", "leave_without_arrival")) == ["e2", "e4", "e7"]


def test_late_events_are_replayed_in_time_order_and_flagged() -> None:
    z = "Asia/Dubai"
    arrive_at, leave_at = at(z, 2026, 11, 2, 8, 0), at(z, 2026, 11, 2, 16, 0)
    # The departure arrived first; the arrival reached the server two hours late.
    events = [
        ev(2, "leave", leave_at, received_at=leave_at + timedelta(seconds=5)),
        ev(1, "arrive", arrive_at, received_at=arrive_at + timedelta(hours=2)),
    ]
    day = one_day(events)
    assert day.verified_seconds == 8 * H
    assert [f for f in day.flags if f.code == "late_event"] == [E.Flag("late_event", "e1")]


def test_multiple_sites_a_day_switch_on_arrival_and_list_every_site() -> None:
    z = "Asia/Dubai"
    day = one_day([
        ev(1, "arrive", at(z, 2026, 11, 2, 8, 0), site_id="office"),
        ev(2, "arrive", at(z, 2026, 11, 2, 11, 0), site_id="site_a"),
        ev(3, "leave", at(z, 2026, 11, 2, 13, 0), site_id="site_a"),
        ev(4, "arrive", at(z, 2026, 11, 2, 14, 0), site_id="office"),
        ev(5, "leave", at(z, 2026, 11, 2, 17, 0), site_id="office"),
    ])
    assert day.site_ids == ["office", "site_a"]
    assert day.presence_seconds == day.verified_seconds == 8 * H  # travel 13:00 to 14:00 is not presence
    visits = [(s.site_id, s.seconds()) for s in day.segments if s.kind == "site"]
    assert visits == [("office", 3 * H), ("site_a", 2 * H), ("office", 3 * H)]


def test_remote_work_counts_as_worked_but_not_presence() -> None:
    z = "Asia/Dubai"
    day = one_day([
        ev(1, "remote_start", at(z, 2026, 11, 2, 9, 0)),
        ev(2, "break_start", at(z, 2026, 11, 2, 12, 0)),
        ev(3, "break_end", at(z, 2026, 11, 2, 13, 0)),
        ev(4, "remote_end", at(z, 2026, 11, 2, 18, 0)),
    ])
    assert day.presence_seconds == 0 and day.remote_seconds == 9 * H
    assert day.worked_seconds == day.verified_seconds == 8 * H


def test_manual_corrections_count_and_are_marked_adjusted_poor_location_is_not_verified() -> None:
    z = "Asia/Dubai"
    day = one_day([
        ev(1, "arrive", at(z, 2026, 11, 2, 8, 0), quality="poor"),
        ev(2, "leave", at(z, 2026, 11, 2, 16, 0), source="manual"),
    ])
    assert day.worked_seconds == 8 * H
    assert day.verified_seconds == 0
    assert day.flag_codes == ["adjusted", "poor_location"]


def test_a_scheduled_day_with_no_arrival_is_flagged_once_past() -> None:
    z = "Asia/Dubai"
    monday = date(2026, 11, 2)
    shifts = [E.parse_shift(monday.weekday(), "08:00", "16:00")]
    result = E.days([], now=NOW, shifts=shifts, default_zone=z, start=monday, end=monday + timedelta(days=6))
    assert [(d.day, d.flag_codes, d.scheduled_seconds) for d in result] == [(monday, ["no_arrival"], 8 * H)]


def test_daily_overtime_estimate_only_when_configured() -> None:
    z = "Asia/Dubai"
    events = [ev(1, "arrive", at(z, 2026, 11, 2, 7, 0)), ev(2, "leave", at(z, 2026, 11, 2, 18, 0))]
    assert one_day(events).overtime_seconds is None
    day = one_day(events, daily_overtime_minutes=8 * 60)
    assert day.overtime_seconds == 3 * H
    period = E.period_totals([day], date(2026, 11, 1), date(2026, 11, 30), daily_overtime=True)
    assert period.overtime_seconds == 3 * H and period.flags == {}


def test_week_start_is_configurable() -> None:
    wednesday = date(2026, 11, 4)
    assert E.week_start(wednesday) == date(2026, 11, 2)
    assert E.week_start(wednesday, 6) == date(2026, 11, 1)
