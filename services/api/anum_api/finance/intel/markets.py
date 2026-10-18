"""Venues, trading sessions and data freshness (docs/finance-trading-intel.md#calendars).

Each venue has an IANA time zone (so daylight saving time is handled by ``zoneinfo``), the
local hours of its regular session, its trading weekdays and the holidays ANUM knows for
certain. Holidays that follow the lunar calendar (Eid al-Fitr, Eid al-Adha, the Islamic new
year and the Prophet's birthday in Saudi Arabia and the UAE) are announced by the exchange
each year; they are **not guessed**. For those venues ``holidays_known`` is false and a
closed day the list does not know shows as ``open`` with the flag, so a job reports
"check the exchange's notice" instead of trading on a guess. Owners can add dates with
``extra_holidays``.

All functions are pure: they take ``now`` and never read the clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    CRYPTO = "crypto"
    FOREX = "forex"
    COMMODITY = "commodity"


@dataclass(frozen=True)
class Venue:
    code: str
    name: str
    timezone: str
    opens: time
    closes: time
    weekdays: frozenset[int]  # Monday is 0
    holidays: frozenset[date] = field(default_factory=frozenset)
    holiday_years: frozenset[int] = field(default_factory=frozenset)
    lunar_holidays: bool = False
    always_open: bool = False
    note: str = ""

    def holidays_known(self, day: date) -> bool:
        return self.always_open or (day.year in self.holiday_years and not self.lunar_holidays)


def _days(*items: str) -> frozenset[date]:
    return frozenset(date.fromisoformat(item) for item in items)


# NYSE full-day closures for 2026 and 2027 (the exchange's published holiday calendar).
_NYSE = _days(
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
)
# London Stock Exchange closures for 2026 and 2027 (England and Wales bank holidays).
_LSE = _days(
    "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25", "2026-08-31", "2026-12-25", "2026-12-28",
    "2027-01-01", "2027-03-26", "2027-03-29", "2027-05-03", "2027-05-31", "2027-08-30", "2027-12-27", "2027-12-28",
)
_MON_FRI = frozenset({0, 1, 2, 3, 4})
_SUN_THU = frozenset({6, 0, 1, 2, 3})

VENUES: dict[str, Venue] = {
    v.code: v
    for v in (
        Venue("XNYS", "New York Stock Exchange and Nasdaq", "America/New_York", time(9, 30), time(16, 0), _MON_FRI, _NYSE,
              frozenset({2026, 2027})),
        Venue("XLON", "London Stock Exchange", "Europe/London", time(8, 0), time(16, 30), _MON_FRI, _LSE, frozenset({2026, 2027})),
        Venue("XSAU", "Saudi Exchange (Tadawul)", "Asia/Riyadh", time(10, 0), time(15, 0), _SUN_THU, lunar_holidays=True,
              note="Sunday to Thursday. Eid and national holidays are announced by the exchange each year."),
        Venue("XDFM", "Dubai Financial Market", "Asia/Dubai", time(10, 0), time(15, 0), _MON_FRI, lunar_holidays=True,
              note="Monday to Friday since January 2022. Eid holidays are announced each year."),
        Venue("XADS", "Abu Dhabi Securities Exchange", "Asia/Dubai", time(10, 0), time(15, 0), _MON_FRI, lunar_holidays=True,
              note="Monday to Friday since January 2022. Eid holidays are announced each year."),
        # Spot currencies and metals trade around the clock from Sunday 17:00 to Friday 17:00 New York time.
        Venue("FX", "Currencies and spot metals (over the counter)", "America/New_York", time(17, 0), time(17, 0), _MON_FRI,
              note="24 hours from Sunday 17:00 to Friday 17:00 New York time."),
        Venue("CRYPTO", "Crypto assets", "UTC", time(0, 0), time(0, 0), frozenset(range(7)), always_open=True,
              note="Every day, around the clock. A daily bar closes at 00:00 UTC."),
    )
}


class SessionState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    HOLIDAY = "holiday"


@dataclass(frozen=True)
class Session:
    venue: str
    state: SessionState
    local_time: datetime
    holidays_known: bool
    reason: str


def venue(code: str) -> Venue:
    try:
        return VENUES[code]
    except KeyError:
        raise ValueError(f"Unknown venue {code!r}: use one of {', '.join(sorted(VENUES))}.") from None


def is_trading_day(v: Venue, day: date, extra_holidays: frozenset[date] = frozenset()) -> bool:
    if v.always_open:
        return True
    return day.weekday() in v.weekdays and day not in v.holidays and day not in extra_holidays


def session(code: str, now: datetime, extra_holidays: frozenset[date] = frozenset()) -> Session:
    """Is the venue's regular session open at ``now`` (an aware datetime)?"""
    if now.tzinfo is None:
        raise ValueError("Pass an aware datetime.")
    v = venue(code)
    local = now.astimezone(ZoneInfo(v.timezone))
    day = local.date()
    known = v.holidays_known(day)
    if v.always_open:
        return Session(code, SessionState.OPEN, local, True, "Trades every day around the clock.")
    if code == "FX":
        weekday, clock = local.weekday(), local.time()
        closed = weekday == 5 or (weekday == 6 and clock < v.opens) or (weekday == 4 and clock >= v.closes)
        state = SessionState.CLOSED if closed else SessionState.OPEN
        return Session(code, state, local, True, "Weekend close." if closed else "24-hour session.")
    if day in v.holidays or day in extra_holidays:
        return Session(code, SessionState.HOLIDAY, local, known, "Exchange holiday.")
    if day.weekday() not in v.weekdays:
        return Session(code, SessionState.CLOSED, local, known, "Not a trading weekday.")
    if v.opens <= local.time() < v.closes:
        reason = "Regular session." if known else "Regular session, unless the exchange announced a holiday for today."
        return Session(code, SessionState.OPEN, local, known, reason)
    return Session(code, SessionState.CLOSED, local, known, "Outside regular hours.")


def last_completed_session(code: str, now: datetime, extra_holidays: frozenset[date] = frozenset()) -> date:
    """The date of the newest daily bar that should exist at ``now``.

    For an exchange: today once the session has closed, else the previous trading day. For
    crypto: yesterday in UTC (a daily bar closes at midnight). For currencies: the previous
    weekday whose New York 17:00 close has passed.
    """
    v = venue(code)
    local = now.astimezone(ZoneInfo(v.timezone))
    day = local.date()
    if v.always_open:
        return day - timedelta(days=1)
    if code == "FX":
        candidate = day if local.time() >= v.closes else day - timedelta(days=1)
        while candidate.weekday() not in v.weekdays:
            candidate -= timedelta(days=1)
        return candidate
    candidate = day if local.time() >= v.closes else day - timedelta(days=1)
    for _ in range(30):
        if is_trading_day(v, candidate, extra_holidays):
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError("No trading day in the last 30 days: check the holiday list.")


@dataclass(frozen=True)
class Freshness:
    status: str  # fresh, stale, none
    last_bar: date | None
    expected: date
    sessions_behind: int
    holidays_known: bool
    message: str


def freshness(code: str, last_bar: date | None, now: datetime, extra_holidays: frozenset[date] = frozenset()) -> Freshness:
    """Whether the newest stored daily bar is the one that should exist now. Never guesses a price."""
    v = venue(code)
    expected = last_completed_session(code, now, extra_holidays)
    known = v.holidays_known(expected)
    if last_bar is None:
        return Freshness("none", None, expected, 0, known, "No bars stored for this instrument.")
    if last_bar >= expected:
        return Freshness("fresh", last_bar, expected, 0, known, "Up to date with the last completed session.")
    behind, day = 0, last_bar + timedelta(days=1)
    while day <= expected and behind < 1000:
        if is_trading_day(v, day, extra_holidays):
            behind += 1
        day += timedelta(days=1)
    message = f"{behind} session(s) behind: the newest bar is {last_bar.isoformat()}, expected {expected.isoformat()}."
    if not known:
        message += " The venue's holidays are not all known: a recent exchange holiday may explain the gap."
    return Freshness("stale", last_bar, expected, behind, known, message)


def periods_per_year(asset_class: AssetClass) -> int:
    """Daily bars a year, for annualising: 365 for crypto, 260 for currencies, 252 for exchanges."""
    return {AssetClass.CRYPTO: 365, AssetClass.FOREX: 260}.get(asset_class, 252)
