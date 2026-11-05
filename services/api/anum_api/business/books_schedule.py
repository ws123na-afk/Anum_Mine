"""When a recurring bill falls due: pure date arithmetic (docs/business-books.md#recurring-bills).

A schedule counts from its anchor date. Period ``k`` (0, 1, 2, ...) is the anchor plus ``k`` steps:
``interval`` days or weeks, or ``interval`` months (monthly is 1, quarterly 3, yearly 12). A
monthly step keeps the anchor's day of the month and falls back to the month's last day when the
month is shorter (31 January, 28 or 29 February, 31 March), so a schedule never drifts.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from ..finance.money import quantize
from .books_models import BillTemplate, Frequency, TemplateStatus

MONTH_STEPS = {Frequency.MONTHLY: 1, Frequency.QUARTERLY: 3, Frequency.YEARLY: 12}
DAYS_PER_MONTH = Decimal("30.4375")  # 365.25 / 12


def _add_months(day: date, months: int, anchor_day: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def occurrence(anchor: date, frequency: Frequency, interval: int, index: int) -> date:
    """The date of period ``index`` of a schedule that starts on ``anchor``."""
    if frequency == Frequency.DAYS:
        return anchor + timedelta(days=interval * index)
    if frequency == Frequency.WEEKS:
        return anchor + timedelta(weeks=interval * index)
    months = MONTH_STEPS.get(frequency, 0) or interval
    return _add_months(anchor, months * index, anchor.day)


def first_index_on_or_after(anchor: date, frequency: Frequency, interval: int, day: date) -> int:
    """The first period dated on or after ``day`` (used when a paused schedule resumes)."""
    if day <= anchor:
        return 0
    if frequency in (Frequency.DAYS, Frequency.WEEKS):
        step = interval * (7 if frequency == Frequency.WEEKS else 1)
        return -(-(day - anchor).days // step)
    months = MONTH_STEPS.get(frequency, 0) or interval
    index = max(0, ((day.year - anchor.year) * 12 + day.month - anchor.month) // months - 1)
    while occurrence(anchor, frequency, interval, index) < day:
        index += 1
    return index


def finished(template: BillTemplate, next_date: date) -> str | None:
    """Why no further bill is due (``"end_date"`` or ``"count"``), or ``None``."""
    if template.max_count is not None and template.created_count >= template.max_count:
        return "count"
    if template.end_date is not None and next_date > template.end_date:
        return "end_date"
    return None


def per_month(template: BillTemplate) -> Decimal:
    """The net cost per month of a schedule (the run rate), rounded to the currency."""
    if template.frequency in (Frequency.DAYS, Frequency.WEEKS):
        days = Decimal(template.interval * (7 if template.frequency == Frequency.WEEKS else 1))
        return quantize(template.subtotal * DAYS_PER_MONTH / days, template.currency)
    months = MONTH_STEPS.get(template.frequency, 0) or template.interval
    return quantize(template.subtotal / Decimal(months), template.currency)


def upcoming(template: BillTemplate, until: date, limit: int = 12) -> list[date]:
    """The periods still to be made, up to ``until`` (an active schedule only; paused and ended make none)."""
    if template.status != TemplateStatus.ACTIVE or template.next_date is None:
        return []
    out: list[date] = []
    index, count = template.next_index, template.created_count
    while len(out) < limit:
        day = occurrence(template.anchor_date, template.frequency, template.interval, index)
        if day > until or (template.end_date is not None and day > template.end_date):
            break
        if template.max_count is not None and count >= template.max_count:
            break
        out.append(day)  # a period already due is made by the next run of the daily job
        index += 1
        count += 1
    return out


__all__ = ["finished", "first_index_on_or_after", "occurrence", "per_month", "upcoming"]
