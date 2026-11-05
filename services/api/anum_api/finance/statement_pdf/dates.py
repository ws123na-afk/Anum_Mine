"""Dates in statements (docs/finance-statements.md#dates).

A statement uses one date layout throughout, so the order (day first, month first or year first) is
decided once from all of its dates, never row by row: a single "13/01" anywhere settles it. When every
date fits both, day first wins (the Gulf convention) and the answer is marked ambiguous for the owner.

Read: numbers with ``/``, ``-``, ``.`` or spaces (two or four digit years), English and Arabic month
names (``05 Nov 2026``, ``Nov 5, 2026``, ``5 نوفمبر 2026``), dates without a year (the statement
period gives it) and Hijri dates (a year from 1300 to 1500, or a Hijri month name), converted with the
tabular Islamic calendar. Saudi banks use Umm al-Qura, which can differ by a day, so such rows are
marked for the owner to check.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11,
    "november": 11, "dec": 12, "december": 12,
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "إبريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8,
    "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    "كانون الثاني": 1, "شباط": 2, "آذار": 3, "نيسان": 4, "أيار": 5, "حزيران": 6, "تموز": 7, "آب": 8, "أيلول": 9, "تشرين الأول": 10,
    "تشرين الثاني": 11, "كانون الأول": 12,
}
_HIJRI_MONTHS = {
    "محرم": 1, "صفر": 2, "ربيع الأول": 3, "ربيع الاول": 3, "ربيع الآخر": 4, "ربيع الثاني": 4, "جمادى الأولى": 5, "جمادى الاولى": 5,
    "جمادى الآخرة": 6, "جمادى الثانية": 6, "رجب": 7, "شعبان": 8, "رمضان": 9, "شوال": 10, "ذو القعدة": 11, "ذو الحجة": 12,
}
_NAMES = sorted(list(_MONTHS) + list(_HIJRI_MONTHS), key=len, reverse=True)
_NAME = "(?:" + "|".join(re.escape(n) for n in _NAMES) + r")\.?"

NUMERIC = r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"
NAMED = r"\d{1,2}[ \-]?" + _NAME + r"[ \-,]*\d{2,4}|" + _NAME + r" \d{1,2},? \d{4}"
SHORT = r"\d{1,2}[/\-.]\d{1,2}(?![/\-.]?\d)"
DATE = "(?:" + NUMERIC + "|" + NAMED + "|" + SHORT + ")"
DATE_AT_START = re.compile(r"^\s*(" + DATE + r")(?![\w])", re.IGNORECASE)
ANY_DATE = re.compile(r"(?<![\w/.\-])(" + DATE + r")(?![\w])", re.IGNORECASE)
WHOLE_DATE = re.compile(r"^(" + DATE + r")$", re.IGNORECASE)

# Orders and the strftime-like codes the preview shows and the owner can pick.
FORMATS = {"dmy": "%d/%m/%Y", "mdy": "%m/%d/%Y", "ymd": "%Y-%m-%d"}


def order_of(fmt: str | None) -> str | None:
    """The order a format like ``%d/%m/%Y`` stands for."""
    if not fmt:
        return None
    positions = {key: fmt.find(code) for key, code in (("d", "%d"), ("m", "%m"), ("y", "%Y"))}
    if positions["y"] < 0:
        positions["y"] = fmt.find("%y")
    if min(positions.values()) < 0:
        if fmt.find("%d") >= 0 and fmt.find("%b") >= 0:
            return "dmy"
        return None
    return "".join(sorted(positions, key=lambda k: positions[k]))


@dataclass
class Parts:
    a: int
    b: int
    c: int | None
    kind: str  # "numeric", "ymd", "named", "short"
    hijri: bool = False


def split(text: str) -> Parts | None:
    value = " ".join(text.strip().split()).rstrip(",")
    lower = value.lower()
    for name in _NAMES:
        found = re.search(r"(?<!\w)" + re.escape(name) + r"\.?(?!\w)", lower)
        if not found:
            continue
        numbers = [int(n) for n in re.findall(r"\d+", lower[: found.start()] + " " + lower[found.end():])]
        if len(numbers) != 2:
            return None
        day, year = numbers  # "05 Nov 2026" and "Nov 5, 2026" alike
        hijri = name in _HIJRI_MONTHS
        month = _HIJRI_MONTHS[name] if hijri else _MONTHS[name]
        return Parts(day, month, year, "named", hijri or 1300 <= year <= 1500)
    numbers = re.split(r"[/\-. ]", value)
    if not all(n.isdigit() for n in numbers):
        return None
    ints = [int(n) for n in numbers]
    if len(ints) == 2:
        return Parts(ints[0], ints[1], None, "short")
    if len(ints) != 3:
        return None
    if len(numbers[0]) == 4:
        return Parts(ints[2], ints[1], ints[0], "ymd", 1300 <= ints[0] <= 1500)  # day, month, year
    return Parts(ints[0], ints[1], ints[2], "numeric", 1300 <= ints[2] <= 1500)


def plausible(text: str) -> bool:
    """A real calendar date in some order ("0.00" and "45.13" are not)."""
    parts = split(text)
    if parts is None:
        return False
    if parts.c is not None and not (1300 <= parts.c <= 1500 or 1900 <= parts.c <= 2100 or parts.c < 100):
        return False
    if parts.kind in ("named", "ymd"):
        return 1 <= parts.a <= 31 and 1 <= parts.b <= 12
    return (1 <= parts.a <= 31 and 1 <= parts.b <= 12) or (1 <= parts.b <= 31 and 1 <= parts.a <= 12)


def decide_order(values: list[str]) -> tuple[str, bool]:
    """Day first or month first for the whole statement, and whether it was a guess."""
    kinds = {p.kind for v in values if (p := split(v)) is not None}
    if kinds == {"ymd"}:
        return "ymd", False
    day_first = month_first = True
    for value in values:
        parts = split(value)
        if parts is None or parts.kind not in ("numeric", "short"):
            continue
        if parts.a > 12:
            month_first = False
        if parts.b > 12:
            day_first = False
    if day_first and not month_first:
        return "dmy", False
    if month_first and not day_first:
        return "mdy", False
    return "dmy", any((p := split(v)) is not None and p.kind in ("numeric", "short") for v in values)


def hijri_to_gregorian(year: int, month: int, day: int) -> date:
    """The tabular (arithmetical) Islamic calendar; Umm al-Qura can differ by a day."""
    jd = day + math.ceil(29.5 * (month - 1)) + (year - 1) * 354 + (3 + 11 * year) // 30 + 1948440 - 1
    return date.fromordinal(jd - 1721425)


def to_date(text: str, order: str, year_hint: int | None) -> tuple[date | None, bool]:
    """The date and whether it was a Hijri date."""
    parts = split(text)
    if parts is None:
        return None, False
    if parts.kind == "named":
        day, month, year = parts.a, parts.b, parts.c
    elif parts.kind == "ymd":
        day, month, year = parts.a, parts.b, parts.c
    else:
        day, month = (parts.a, parts.b) if order != "mdy" else (parts.b, parts.a)
        year = parts.c if parts.c is not None else year_hint
    if year is None:
        return None, False
    if year < 100:
        year += 2000
    try:
        if parts.hijri:
            if not 1 <= month <= 12 or not 1 <= day <= 30:
                return None, True
            return hijri_to_gregorian(year, month, day), True
        found = date(year, month, day)
    except ValueError:
        return None, False
    return (found if 1990 <= found.year <= 2100 else None), False


__all__ = ["ANY_DATE", "DATE_AT_START", "FORMATS", "WHOLE_DATE", "decide_order", "plausible", "hijri_to_gregorian", "order_of", "split", "to_date"]
