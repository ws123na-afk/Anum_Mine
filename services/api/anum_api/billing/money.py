"""Money arithmetic for billing: integer minor units, half-up rounding, no floats."""

from __future__ import annotations

import calendar
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def tax_for(subtotal_minor: int, rate_bp: int) -> int:
    """Tax on a subtotal at ``rate_bp`` basis points (1500 = 15%), rounded half up."""
    if subtotal_minor <= 0 or rate_bp <= 0:
        return 0
    return round_half_up(Decimal(subtotal_minor) * Decimal(rate_bp) / Decimal(10_000))


def split_gross(total_minor: int, rate_bp: int) -> tuple[int, int]:
    """A tax-inclusive amount split into (subtotal, tax); the two always add up to the total."""
    if rate_bp <= 0:
        return total_minor, 0
    subtotal = round_half_up(Decimal(total_minor) * Decimal(10_000) / Decimal(10_000 + rate_bp))
    return subtotal, total_minor - subtotal


def add_months(moment: datetime, months: int) -> datetime:
    """``moment`` plus whole calendar months, the day clamped to the target month's length."""
    month_index = moment.month - 1 + months
    year, month = moment.year + month_index // 12, month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def period_end(start: datetime, interval: str) -> datetime:
    return add_months(start, 12 if interval == "year" else 1)


def prorate(old_amount: int, new_amount: int, start: datetime, end: datetime, at: datetime) -> int:
    """The price difference for the rest of a period: ``(new - old) x remaining / length``.

    Positive is a charge (upgrade), negative a credit (downgrade now). Outside the period
    nothing is prorated.
    """
    total = (end - start).total_seconds()
    remaining = (end - max(at, start)).total_seconds()
    if total <= 0 or remaining <= 0:
        return 0
    return round_half_up(Decimal(new_amount - old_amount) * Decimal(remaining) / Decimal(total))


def months_of(interval: str) -> int:
    return 12 if interval == "year" else 1


def normalise(amount_minor: int, from_interval: str, to_interval: str) -> int:
    """``amount_minor`` priced per ``from_interval`` expressed per ``to_interval``, rounded half up."""
    return round_half_up(Decimal(amount_minor) * Decimal(months_of(to_interval)) / Decimal(months_of(from_interval)))


def plan_amount(price_minor: int, per_seat: bool, seats: int) -> int:
    return price_minor * max(1, seats) if per_seat else price_minor
