"""Tyre attributes from free text (docs/commerce.md#tyre-attributes).

Marketplace titles carry the tyre's metric size (``205/55R16``, ``205/55 ZR16``,
``LT265/70R17``), its service description (load index and speed symbol, ``91V``) and
sometimes the DOT date code (``DOT 2324`` = week 23 of 2024). ``parse_tyre`` reads what
it can and leaves the rest empty; the owner corrects or completes it in the catalog.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field, field_validator

SPEED_SYMBOLS = "LMNPQRSTUHVWYZ"

_SIZE = re.compile(
    r"(?<![\d/])(?P<prefix>P|LT)?\s?(?P<width>\d{3})\s?/\s?(?P<aspect>\d{2})\s?"
    r"(?P<construction>Z?R|D|B)\s?(?P<rim>\d{2}(?:\.\d)?)"
    r"(?:\s*(?P<load>\d{2,3}(?:/\d{2,3})?)\s?(?P<speed>\(?[LMNPQRSTUHVWYZ]\)?)(?![A-Za-z]))?",
    re.IGNORECASE,
)
_DOT = re.compile(r"\bDOT\s*(?:[A-Z0-9]{2,4}\s+){0,3}(?P<week>\d{2})(?P<year>\d{2})\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(?:production|made|year|manufactured|سنة الصنع|صنع)\D{0,6}(?P<year>20\d{2})\b", re.IGNORECASE)


class TyreSpec(BaseModel):
    """What identifies a tyre. Every field is optional: not every product is a tyre."""

    size: str | None = Field(default=None, max_length=40, description="Normalized size, e.g. 205/55R16")
    width_mm: int | None = Field(default=None, ge=100, le=455)
    aspect_ratio: int | None = Field(default=None, ge=20, le=100)
    construction: str | None = Field(default=None, max_length=3)
    rim_inches: float | None = Field(default=None, ge=10, le=30)
    load_index: str | None = Field(default=None, max_length=8, description="e.g. 91 or 121/118")
    speed_index: str | None = Field(default=None, max_length=1)
    dot_week: int | None = Field(default=None, ge=1, le=53)
    dot_year: int | None = Field(default=None, ge=1990, le=2100)
    season: str | None = Field(default=None, max_length=20, description="summer, winter, all_season")

    @field_validator("speed_index")
    @classmethod
    def _speed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip("() ").upper()
        if value and value not in SPEED_SYMBOLS:
            raise ValueError(f"speed index must be one of {SPEED_SYMBOLS}")
        return value or None

    @property
    def is_tyre(self) -> bool:
        return self.width_mm is not None and self.rim_inches is not None

    def age_years(self, today: date | None = None) -> int | None:
        """Whole years since the DOT date (tyres older than about five years sell badly)."""
        if self.dot_year is None:
            return None
        today = today or date.today()
        week = self.dot_week or 1
        produced = date.fromisocalendar(self.dot_year, min(week, 52), 1)
        return max(0, (today - produced).days // 365)


def normalize_size(width: int, aspect: int, construction: str, rim: float) -> str:
    rim_text = f"{rim:g}"
    return f"{width}/{aspect}{construction.upper()}{rim_text}"


def parse_size(text: str) -> TyreSpec | None:
    match = _SIZE.search(text or "")
    if match is None:
        return None
    width, aspect = int(match["width"]), int(match["aspect"])
    rim = float(match["rim"])
    construction = match["construction"].upper()
    if not (100 <= width <= 455 and 20 <= aspect <= 100 and 10 <= rim <= 30):
        return None
    speed = (match["speed"] or "").strip("()").upper() or None
    return TyreSpec(
        size=normalize_size(width, aspect, construction, rim),
        width_mm=width,
        aspect_ratio=aspect,
        construction=construction,
        rim_inches=rim,
        load_index=match["load"] or None,
        speed_index=speed,
    )


def parse_dot(text: str) -> tuple[int, int] | None:
    """``DOT 2324`` (or a full DOT code ending in the date) -> ``(23, 2024)``."""
    match = _DOT.search(text or "")
    if match is not None:
        week, year = int(match["week"]), 2000 + int(match["year"])
        if 1 <= week <= 53:
            return week, year
    match = _YEAR.search(text or "")
    if match is not None:
        return 0, int(match["year"])
    return None


def parse_tyre(*texts: str | None) -> TyreSpec:
    """The tyre attributes found in any of ``texts`` (title, Arabic title, attributes)."""
    spec = TyreSpec()
    for text in texts:
        if not text:
            continue
        if spec.size is None:
            found = parse_size(text)
            if found is not None:
                spec = found.model_copy(update={"season": spec.season, "dot_week": spec.dot_week, "dot_year": spec.dot_year})
        if spec.dot_year is None:
            dot = parse_dot(text)
            if dot is not None:
                week, year = dot
                spec = spec.model_copy(update={"dot_week": week or None, "dot_year": year})
        if spec.season is None:
            lowered = text.lower()
            if "all season" in lowered or "all-season" in lowered or "4 season" in lowered:
                spec = spec.model_copy(update={"season": "all_season"})
            elif "winter" in lowered or "شتوي" in text:
                spec = spec.model_copy(update={"season": "winter"})
            elif "summer" in lowered or "صيفي" in text:
                spec = spec.model_copy(update={"season": "summer"})
    return spec


def size_query(text: str) -> str | None:
    """A size typed in search (``2055516``, ``205 55 16``, ``205/55r16``) as the normalized size."""
    compact = re.sub(r"[^0-9]", "", text or "")
    if len(compact) in (7, 8) and compact.isdigit():
        width, aspect, rim = int(compact[:3]), int(compact[3:5]), float(compact[5:])
        if 100 <= width <= 455 and 20 <= aspect <= 100 and 10 <= rim <= 30:
            return normalize_size(width, aspect, "R", rim)
    found = parse_size(text)
    return found.size if found else None
