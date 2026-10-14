"""Statement CSV import (docs/finance.md#csv-import).

The file is untrusted input. It is read as text only (never evaluated, never opened by a
spreadsheet program on the server), capped in size, rows, columns and cell length, and
every description is cleaned of control and direction-override characters before it is
stored. Nothing in a file can choose a tool, an account or a category rule.

Flow: ``POST /api/v1/finance/imports/preview`` parses the file and shows the detected
layout, the column mapping, the date format, the first rows (with their category and
whether they are already in the ledger) and the rows that could not be read. Nothing is
saved. ``POST /api/v1/finance/imports`` sends the same file and choices again; only then
are the new rows written, in one unit of work, and duplicates skipped.

Layouts: a generic one (a date, a description and either one signed amount column or a
debit and a credit column) and presets for common UAE bank exports. A preset is a set of
column names; when a bank's file uses different names the preview says so and the owner
picks the columns (``mapping``). Presets were written from typical column names and are
not certified against every bank's current export.

Dates: the format is detected from every date in the file. When a file fits both day
first and month first (all days 12 or below), ANUM takes day first, the UAE convention,
and flags it as ambiguous so the owner can override it (``date_format``).

Duplicates: each row gets a hash of its account, date, amount, currency, cleaned
description and its occurrence number among identical rows in the same file; the hash
is unique per account, so importing the same or an overlapping statement again adds
only the rows that are new.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field, model_validator

from .money import MoneyError, quantize

MAX_BYTES = 2_000_000
MAX_ROWS = 5_000
MAX_COLUMNS = 50
MAX_CELL = 1_000
MAX_DESCRIPTION = 500
HEADER_SEARCH_ROWS = 20
PREVIEW_ROWS = 20
MAX_ERRORS_SHOWN = 20

DATE_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%d.%m.%Y", "%Y/%m/%d",
    "%d/%m/%y", "%m/%d/%y", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y", "%d %B %Y", "%b %d, %Y",
)
# When a file fits several formats, the earlier one wins: day first (UAE) before month first.
_DAY_FIRST_TWIN = {"%m/%d/%Y": "%d/%m/%Y", "%m-%d-%Y": "%d-%m-%Y", "%m/%d/%y": "%d/%m/%y"}


class ImportError_(ValueError):
    """The file cannot be imported at all (too large, no header, no usable columns)."""


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    date: tuple[str, ...]
    description: tuple[str, ...]
    amount: tuple[str, ...] = ()
    debit: tuple[str, ...] = ()
    credit: tuple[str, ...] = ()


# Column names compared after ``_norm`` (lower case, punctuation and "aed" removed).
PRESETS: dict[str, Preset] = {
    preset.id: preset
    for preset in (
        Preset("emirates_nbd", "Emirates NBD", ("transaction date", "date"), ("narration", "description"),
               debit=("debit", "withdrawals"), credit=("credit", "deposits")),
        Preset("fab", "First Abu Dhabi Bank", ("value date", "transaction date"), ("transaction details", "details"),
               debit=("debit", "debit amount"), credit=("credit", "credit amount")),
        Preset("adcb", "ADCB", ("posting date", "transaction date"), ("description", "transaction description"),
               debit=("debit amount", "debit"), credit=("credit amount", "credit")),
        Preset("mashreq", "Mashreq", ("transaction date", "date"), ("transaction description", "description"),
               debit=("withdrawal", "withdrawals"), credit=("deposit", "deposits")),
        Preset("dib", "Dubai Islamic Bank", ("date", "transaction date"), ("narrative", "description"),
               debit=("debit",), credit=("credit",)),
        Preset("generic", "Generic (date, description, amount)",
               ("date", "transaction date", "posting date", "posted date", "value date", "booking date", "التاريخ"),
               ("description", "details", "narration", "narrative", "memo", "payee", "merchant", "transaction details",
                "الوصف", "البيان"),
               amount=("amount", "transaction amount", "value", "المبلغ"),
               debit=("debit", "debit amount", "withdrawal", "withdrawals", "money out", "paid out", "مدين"),
               credit=("credit", "credit amount", "deposit", "deposits", "money in", "paid in", "دائن")),
    )
}
# Tried in this order; a bank preset counts only when its distinctive columns are all there.
_DETECT_ORDER = ("adcb", "mashreq", "fab", "dib", "emirates_nbd")
_DISTINCTIVE = {
    "emirates_nbd": ("narration",),
    "fab": ("value date", "transaction details"),
    "adcb": ("posting date", "debit amount"),
    "mashreq": ("withdrawal", "deposit"),
    "dib": ("narrative",),
}


class ColumnMapping(BaseModel):
    """Which header names hold the date, the description and the money."""

    date: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=120)
    amount: str | None = Field(default=None, max_length=120)
    debit: str | None = Field(default=None, max_length=120)
    credit: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _money_columns(self) -> ColumnMapping:
        if not self.amount and not (self.debit or self.credit):
            raise ValueError("Pick an amount column, or a debit and a credit column.")
        if self.amount and (self.debit or self.credit):
            raise ValueError("Pick either an amount column or debit and credit columns, not both.")
        return self


@dataclass
class ParsedRow:
    line: int
    posted_on: date
    amount: Decimal
    description: str
    dedupe_hash: str = ""


@dataclass
class RowError:
    line: int
    message: str


@dataclass
class ParsedFile:
    headers: list[str]
    preset: str
    mapping: ColumnMapping
    date_format: str
    date_formats: list[str]
    date_format_ambiguous: bool
    row_count: int
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    file_sha256: str = ""


# -- cleaning ---------------------------------------------------------------------------------

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")


def _norm(header: str) -> str:
    text = clean_text(header).lower().replace("aed", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def clean_text(value: str, limit: int = MAX_DESCRIPTION) -> str:
    """Untrusted cell text made safe to store and show: no control, format or bidi
    override characters, single spaces, at most ``limit`` characters."""
    kept = []
    for char in value:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            kept.append(" ")
            continue
        kept.append(char)
    text = " ".join("".join(kept).split())
    return text[:limit].rstrip()


def safe_cell(value: str) -> str:
    """A text cell for a CSV export that a spreadsheet will not run as a formula."""
    text = value or ""
    if text[:1] in {"=", "+", "-", "@", "\t", "\r", "|", "%"}:
        return "'" + text
    return text


def parse_amount(value: str) -> Decimal | None:
    """A statement amount: "1,234.50", "-12.00", "(12.00)", "12.00 DR", "AED 9", "١٢٫٥٠"."""
    text = clean_text(value or "", 60).translate(_ARABIC_DIGITS).replace("−", "-")
    if not text:
        return None
    negative = False
    if re.search(r"\d\s*DR$", text, re.IGNORECASE):
        negative, text = True, re.sub(r"(?i)\s*DR$", "", text)
    elif re.search(r"\d\s*CR$", text, re.IGNORECASE):
        text = re.sub(r"(?i)\s*CR$", "", text)
    # A currency code or sign next to the number ("AED 9", "9 USD", "$9", "9 د.إ").
    text = re.sub(r"(?i)\b[a-z]{3}\b|د\.إ|[$\u20ac\u00a3]", "", text).strip()
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    if text.endswith("-"):
        negative, text = True, text[:-1].strip()
    if text.startswith("-"):
        negative, text = not negative, text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()
    text = text.replace(" ", "")
    if "," in text and "." in text:
        # The later separator is the decimal one: "1,234.50" and "1.234,50".
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", text) else text.replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", text):
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def _parse_date(value: str, fmt: str) -> date | None:
    text = clean_text(value or "", 40).translate(_ARABIC_DIGITS)
    # "15/01/2026 10:31" and "2026-01-15T10:31:00": the date part only.
    text = re.split(r"[T ](?=\d{1,2}:\d{2})", text, maxsplit=1)[0].strip()
    try:
        parsed = datetime.strptime(text, fmt).date()
    except ValueError:
        return None
    return parsed if 1990 <= parsed.year <= 2100 else None


def detect_date_formats(values: list[str]) -> list[str]:
    """Every format that reads all of ``values`` that look like dates (in ``DATE_FORMATS``
    order). A footer or a broken row that no format reads does not change the answer."""
    sample = [v for v in values if v.strip()][:500]
    sample = [v for v in sample if any(_parse_date(v, fmt) is not None for fmt in DATE_FORMATS)]
    if not sample:
        return []
    return [fmt for fmt in DATE_FORMATS if all(_parse_date(v, fmt) is not None for v in sample)]


# -- reading ----------------------------------------------------------------------------------


def decode(raw: bytes | str) -> str:
    if isinstance(raw, str):
        text = raw
    else:
        if len(raw) > MAX_BYTES:
            raise ImportError_(f"The file is larger than {MAX_BYTES // 1_000_000} MB. Split it by month and import each part.")
        for encoding in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ImportError_(f"The file is larger than {MAX_BYTES // 1_000_000} MB. Split it by month and import each part.")
    return text.lstrip("﻿")


def _delimiter(text: str) -> str:
    head = "\n".join(text.splitlines()[:HEADER_SEARCH_ROWS])
    counts = {d: head.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def read_rows(text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text), delimiter=_delimiter(text))
    rows: list[list[str]] = []
    try:
        for row in reader:
            if len(rows) > MAX_ROWS + HEADER_SEARCH_ROWS:
                raise ImportError_(f"The file has more than {MAX_ROWS} rows. Split it by month and import each part.")
            if len(row) > MAX_COLUMNS:
                raise ImportError_(f"A row has more than {MAX_COLUMNS} columns. This does not look like a statement.")
            rows.append([cell[:MAX_CELL] for cell in row])
    except csv.Error as exc:
        raise ImportError_(f"The file is not valid CSV: {exc}.") from None
    return rows


def _find(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {_norm(h): h for h in headers if h.strip()}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def mapping_for(preset: Preset, headers: list[str]) -> ColumnMapping | None:
    date_col, desc_col = _find(headers, preset.date), _find(headers, preset.description)
    amount_col = _find(headers, preset.amount) if preset.amount else None
    debit_col, credit_col = _find(headers, preset.debit), _find(headers, preset.credit)
    if not date_col or not desc_col:
        return None
    if amount_col and not (debit_col and credit_col):
        return ColumnMapping(date=date_col, description=desc_col, amount=amount_col)
    if debit_col or credit_col:
        return ColumnMapping(date=date_col, description=desc_col, debit=debit_col, credit=credit_col)
    return None


def detect_preset(headers: list[str]) -> str | None:
    normalized = {_norm(h) for h in headers}
    for preset_id in _DETECT_ORDER:
        if all(name in normalized for name in _DISTINCTIVE[preset_id]) and mapping_for(PRESETS[preset_id], headers):
            return preset_id
    return "generic" if mapping_for(PRESETS["generic"], headers) else None


def _locate_header(rows: list[list[str]], preset: str | None, mapping: ColumnMapping | None) -> int:
    """The header row: some exports start with account details before the table."""
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        headers = [clean_text(c, 120) for c in row]
        if mapping is not None:
            wanted = [mapping.date, mapping.description, mapping.amount, mapping.debit, mapping.credit]
            if all(name in headers for name in wanted if name):
                return index
        elif preset is not None and preset in PRESETS:
            if mapping_for(PRESETS[preset], headers):
                return index
        elif detect_preset(headers):
            return index
    first = next((i for i, row in enumerate(rows) if any(c.strip() for c in row)), None)
    if first is None:
        raise ImportError_("The file is empty.")
    return first


def dedupe_hash(account_id: str, posted_on: date, amount: Decimal, currency: str, description: str, occurrence: int) -> str:
    material = "\x1f".join(
        (account_id, posted_on.isoformat(), str(amount), currency, " ".join(description.lower().split()), str(occurrence))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def parse_file(
    raw: bytes | str,
    *,
    account_id: str,
    currency: str,
    preset: str | None = None,
    mapping: ColumnMapping | None = None,
    date_format: str | None = None,
) -> ParsedFile:
    """Read a statement. Raises ``ImportError_`` when nothing usable is there."""
    if preset is not None and preset not in PRESETS:
        raise ImportError_("Unknown layout. Pick one of the listed layouts or map the columns yourself.")
    if date_format is not None and date_format not in DATE_FORMATS:
        raise ImportError_("Unknown date format. Pick one of the listed formats.")
    text = decode(raw)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rows = read_rows(text)
    if not rows:
        raise ImportError_("The file is empty.")
    header_index = _locate_header(rows, preset, mapping)
    headers = [clean_text(c, 120) for c in rows[header_index]]
    chosen = preset
    if mapping is None:
        chosen = preset or detect_preset(headers)
        found = mapping_for(PRESETS[chosen], headers) if chosen else None
        if found is None:
            raise ImportError_(
                "ANUM could not tell which columns hold the date, the description and the amount. "
                "Pick them under Column mapping."
            )
        mapping = found
    else:
        missing = [n for n in (mapping.date, mapping.description, mapping.amount, mapping.debit, mapping.credit) if n and n not in headers]
        if missing:
            raise ImportError_(f"These columns are not in the file: {', '.join(missing)}.")
        chosen = "custom"
    body = rows[header_index + 1 :]
    if len(body) > MAX_ROWS:
        raise ImportError_(f"The file has more than {MAX_ROWS} rows. Split it by month and import each part.")
    position = {name: headers.index(name) for name in headers if name}

    def cell(row: list[str], name: str | None) -> str:
        if not name:
            return ""
        index = position.get(name)
        return row[index] if index is not None and index < len(row) else ""

    data_rows = [(header_index + 2 + i, row) for i, row in enumerate(body) if any(c.strip() for c in row)]
    formats = detect_date_formats([cell(row, mapping.date) for _, row in data_rows])
    if date_format is not None:
        fmt = date_format
    elif formats:
        fmt = formats[0]
    else:
        # Some rows are not dates (a footer, a total): take the format most rows fit.
        sample = [cell(row, mapping.date) for _, row in data_rows[:500]]
        fmt = max(DATE_FORMATS, key=lambda f: (sum(_parse_date(v, f) is not None for v in sample), -DATE_FORMATS.index(f)))
    ambiguous = date_format is None and any(_DAY_FIRST_TWIN.get(other) == fmt for other in formats)
    result = ParsedFile(
        headers=headers, preset=chosen or "custom", mapping=mapping, date_format=fmt, date_formats=formats,
        date_format_ambiguous=ambiguous, row_count=len(data_rows), file_sha256=sha,
    )
    seen: dict[tuple[date, Decimal, str], int] = {}
    for line, row in data_rows:
        posted = _parse_date(cell(row, mapping.date), fmt)
        if posted is None:
            result.errors.append(RowError(line, "The date could not be read with the chosen date format."))
            continue
        description = clean_text(cell(row, mapping.description))
        if not description:
            result.errors.append(RowError(line, "The description is empty."))
            continue
        try:
            if mapping.amount:
                value = parse_amount(cell(row, mapping.amount))
                if value is None:
                    raise MoneyError("The amount could not be read.")
            else:
                debit = parse_amount(cell(row, mapping.debit)) if mapping.debit else None
                credit = parse_amount(cell(row, mapping.credit)) if mapping.credit else None
                if debit is None and credit is None:
                    raise MoneyError("Neither a debit nor a credit amount is there.")
                value = abs(credit or Decimal(0)) - abs(debit or Decimal(0))
            amount = quantize(value, currency)
        except MoneyError as exc:
            result.errors.append(RowError(line, str(exc)))
            continue
        key = (posted, amount, " ".join(description.lower().split()))
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        result.rows.append(
            ParsedRow(line, posted, amount, description, dedupe_hash(account_id, posted, amount, currency, description, occurrence))
        )
    return result
