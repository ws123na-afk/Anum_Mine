"""Importing a client's fleet from a spreadsheet (docs/vehicles.md#fleet-import).

One file holds one kind of row: **vehicles**, **fuel** entries, **service** records (maintenance, tyres,
fees and fines, every service kind) or **odometer** readings. Two calls, both for people who may manage
the workspace's vehicles (``vehicle:manage``, workspace owners), because a file brings many records into
the workspace at once:

* **Preview** reads the file (CSV text, or an Excel workbook through the contacts import's checked
  reader: bounded zip, no macros, formulas never evaluated, ``contacts/import_files.py``), suggests which
  column holds which field from English or Arabic header words (or uses the mapping the caller chose),
  checks every row like the matching form does (the same input models and rules as the API), and says per
  row what applying would do: add, update a vehicle that already has the registration, or skip. A row
  that repeats a record already in the workspace is ``exists``; a row repeating an earlier row of the file
  is ``duplicate_in_file``; a row this same import already added (its key is stored on the record) is
  ``imported``. Odometer values are checked against the vehicle's readings and the file's other rows in
  time order: the odometer never goes down. Nothing is written.
* **Apply** takes the same file, mapping and the caller's decision per row, checks everything again
  against the workspace as it is now and writes all of it in one unit of work, or nothing: every row that
  is not skipped must be valid (409 otherwise, preview again).

Imported vehicles are business vehicles added by the importer. Fuel and service figures are the record's
own details (the pump total, the receipt total): nothing is posted to Finance. Fleet cost analytics count
such a figure as the record's cost when no payment is linked (docs/vehicles.md#fleet-costs). The file is not
stored; one audit record per apply (``vehicle.fleet_import``) holds the kind and counts only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..schemas import TenantContext, new_id, utc_now
from . import service as S
from .models import OdometerReading, Ownership, Vehicle
from .store import MAX_VEHICLES, DuplicateError, OdometerRefused, VehicleStore, check_odometer

MAX_ROWS = 5000
MAX_COLUMNS = 60
MAX_HEADER = 120
MAX_TEXT_CHARS = 6_000_000
MAX_WORKBOOK_BYTES = 5_000_000
MAX_WORKBOOK_BASE64 = 6_700_000  # a 5 MB workbook in base64
SHOWN_PROBLEMS = 5

ImportKind = Literal["vehicles", "fuel", "service", "odometer"]
RECORD_KIND = {"fuel": "fuel", "service": "service"}

# The fields of each kind, in the order a form shows them; the first ones are required.
FIELDS: dict[str, tuple[str, ...]] = {
    "vehicles": ("registration", "country", "nickname", "make", "model", "year", "vin", "vehicle_type", "fuel_type", "tank_litres", "currency",
                 "odometer_km", "odometer_on", "acquisition_date", "purchase_price", "insurance_expires_on", "registration_expires_on",
                 "inspection_expires_on", "notes"),
    "fuel": ("registration", "date", "litres", "total", "price_per_litre", "odometer_km", "station", "fuel_type", "full_tank", "currency", "notes"),
    "service": ("registration", "date", "kind", "total", "odometer_km", "workshop", "description", "reference", "next_due_on", "next_due_km",
                "currency"),
    "odometer": ("registration", "date", "odometer_km"),
}
REQUIRED: dict[str, tuple[str, ...]] = {"vehicles": ("registration",), "fuel": ("registration", "date", "litres"),
                                        "service": ("registration", "date"), "odometer": ("registration", "date", "odometer_km")}

# Header words that suggest each field (English and Arabic), compared after folding case, spaces, dots and dashes.
_HINTS: dict[str, tuple[str, ...]] = {
    "registration": ("registration", "registration number", "registration no", "reg", "reg no", "plate", "plate number", "plate no", "number plate",
                     "vehicle", "vehicle no", "vehicle number", "رقم اللوحة", "اللوحة", "لوحة", "رقم المركبة", "المركبة", "رقم السيارة"),
    "country": ("country", "الدولة", "البلد"),
    "nickname": ("fleet number", "fleet no", "nickname", "name", "unit", "unit no", "asset", "asset no", "door number", "رقم الأسطول", "الاسم", "رقم الوحدة"),
    "make": ("make", "brand", "manufacturer", "الماركة", "الشركة المصنعة", "الصانع"),
    "model": ("model", "الموديل", "الطراز"),
    "year": ("year", "model year", "manufacture year", "سنة الصنع", "السنة"),
    "vin": ("vin", "chassis", "chassis number", "chassis no", "رقم الشاصي", "رقم الهيكل", "الشاصي"),
    "vehicle_type": ("vehicle type", "type", "category", "body type", "نوع المركبة", "النوع"),
    "fuel_type": ("fuel type", "fuel", "نوع الوقود", "الوقود"),
    "tank_litres": ("tank", "tank size", "tank litres", "tank liters", "tank capacity", "سعة الخزان"),
    "currency": ("currency", "العملة"),
    "odometer_km": ("odometer", "odometer km", "km", "kms", "mileage", "kilometres", "kilometers", "odometer reading", "reading", "meter",
                    "العداد", "عداد", "قراءة العداد", "الكيلومترات", "كم"),
    "odometer_on": ("odometer date", "reading date", "mileage date", "تاريخ القراءة"),
    "acquisition_date": ("acquisition date", "purchase date", "bought on", "date acquired", "تاريخ الشراء"),
    "purchase_price": ("purchase price", "cost price", "سعر الشراء"),
    "insurance_expires_on": ("insurance expiry", "insurance expires", "insurance expiry date", "insurance", "انتهاء التأمين", "تاريخ انتهاء التأمين", "التأمين"),
    "registration_expires_on": ("registration expiry", "registration expires", "registration expiry date", "istimara expiry", "mulkiya expiry",
                                "licence expiry", "license expiry", "انتهاء الاستمارة", "انتهاء الملكية", "انتهاء التسجيل", "انتهاء الرخصة"),
    "inspection_expires_on": ("inspection expiry", "inspection expires", "fahas expiry", "mvpi expiry", "انتهاء الفحص", "الفحص الدوري"),
    "notes": ("notes", "note", "remarks", "comments", "ملاحظات"),
    "date": ("date", "fill date", "fuel date", "service date", "reading date", "transaction date", "التاريخ", "تاريخ"),
    "litres": ("litres", "liters", "ltr", "ltrs", "qty", "quantity", "volume", "لتر", "اللترات", "الكمية"),
    "total": ("total", "amount", "cost", "total amount", "total cost", "value", "net amount", "المبلغ", "الإجمالي", "التكلفة", "القيمة"),
    "price_per_litre": ("price per litre", "price per liter", "unit price", "rate", "price", "سعر اللتر"),
    "station": ("station", "fuel station", "petrol station", "location", "المحطة"),
    "full_tank": ("full", "full tank", "ممتلئ", "تعبئة كاملة"),
    "kind": ("service type", "kind", "work type", "job type", "type", "category", "نوع الخدمة", "نوع العمل", "النوع"),
    "workshop": ("workshop", "garage", "vendor", "supplier", "الورشة", "المورد"),
    "description": ("description", "details", "work done", "job description", "الوصف", "التفاصيل"),
    "reference": ("reference", "invoice", "invoice no", "invoice number", "job card", "ref", "المرجع", "رقم الفاتورة"),
    "next_due_on": ("next due", "next due date", "next service date", "next service", "الموعد القادم", "تاريخ الصيانة القادمة"),
    "next_due_km": ("next due km", "next service km", "next service odometer", "الكيلومترات القادمة"),
}

_YES = {"yes", "y", "true", "1", "x", "full", "نعم", "كامل", "ممتلئ"}
_NO = {"no", "n", "false", "0", "partial", "لا", "جزئي"}
_COUNTRY_WORDS = {"sa": "SA", "ksa": "SA", "saudi": "SA", "saudi arabia": "SA", "السعودية": "SA", "المملكة العربية السعودية": "SA",
                  "ae": "AE", "uae": "AE", "emirates": "AE", "united arab emirates": "AE", "الإمارات": "AE", "الامارات": "AE"}
# Word fragments, tried in order (folded text contains the fragment).
_FUEL_WORDS = (("diesel", "diesel"), ("ديزل", "diesel"), ("سولار", "diesel"), ("electric", "electric"), ("ev", "electric"), ("كهرب", "electric"),
               ("hybrid", "hybrid"), ("هجين", "hybrid"), ("lpg", "lpg"), ("gas", "lpg"), ("غاز", "lpg"), ("98", "petrol_98"), ("super", "petrol_98"),
               ("95", "petrol_95"), ("special", "petrol_95"), ("91", "petrol_91"), ("petrol", "petrol_95"), ("gasoline", "petrol_95"),
               ("بنزين", "petrol_95"))
_TYPE_WORDS = (("pick", "pickup"), ("بيك", "pickup"), ("suv", "suv"), ("jeep", "suv"), ("van", "van"), ("فان", "van"), ("bus", "bus"),
               ("حافلة", "bus"), ("باص", "bus"), ("truck", "truck"), ("lorry", "truck"), ("trailer", "truck"), ("tanker", "truck"),
               ("mixer", "truck"), ("tipper", "truck"), ("شاحنة", "truck"), ("قلاب", "truck"), ("خلاطة", "truck"), ("motor", "motorcycle"),
               ("bike", "motorcycle"), ("دراجة", "motorcycle"), ("car", "car"), ("sedan", "car"), ("saloon", "car"), ("سيارة", "car"),
               ("صالون", "car"))
_SERVICE_WORDS = (("oil", "oil"), ("زيت", "oil"), ("tyre", "tyres"), ("tire", "tyres"), ("إطار", "tyres"), ("اطار", "tyres"), ("كفر", "tyres"),
                  ("battery", "battery"), ("بطارية", "battery"), ("inspection", "inspection"), ("fahas", "inspection"), ("mvpi", "inspection"),
                  ("فحص", "inspection"), ("insurance", "insurance"), ("تأمين", "insurance"), ("registration", "registration"),
                  ("istimara", "registration"), ("mulkiya", "registration"), ("استمارة", "registration"), ("ملكية", "registration"),
                  ("fine", "fine"), ("violation", "fine"), ("مخالفة", "fine"), ("toll", "toll"), ("salik", "toll"), ("darb", "toll"),
                  ("سالك", "toll"), ("parking", "parking"), ("مواقف", "parking"), ("warranty", "warranty"), ("ضمان", "warranty"),
                  ("wash", "wash"), ("غسيل", "wash"), ("repair", "repair"), ("breakdown", "repair"), ("إصلاح", "repair"), ("تصليح", "repair"),
                  ("service", "maintenance"), ("maintenance", "maintenance"), ("صيانة", "maintenance"))
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_SLASH_DATE = re.compile(r"^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$")
_EXCEL_EPOCH = date(1899, 12, 30)


class ImportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class RowAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"


class RowStatus(StrEnum):
    NEW = "new"
    EXISTS = "exists"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    IMPORTED = "imported"
    INVALID = "invalid"


class IssueCode(StrEnum):
    REQUIRED = "required"
    NUMBER = "number"
    DATE = "date"
    FUTURE = "future"
    RANGE = "range"
    CHOICE = "choice"
    UNKNOWN_VEHICLE = "unknown_vehicle"
    AMBIGUOUS_VEHICLE = "ambiguous_vehicle"
    ARCHIVED_VEHICLE = "archived_vehicle"
    ODOMETER = "odometer"
    REGISTRATION = "registration"
    VIN = "vin"
    CURRENCY = "currency"
    OTHER = "other"


class FleetImportFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ImportKind
    format: ImportFormat = ImportFormat.CSV
    csv: str = Field(default="", max_length=MAX_TEXT_CHARS, description="The CSV file's text")
    file_base64: str = Field(default="", max_length=MAX_WORKBOOK_BASE64, description="An Excel workbook (.xlsx), base64")
    file_name: str = Field(default="", max_length=200)
    mapping: dict[str, str] | None = Field(default=None, description="Field to column; omitted: ANUM suggests one from the headers")
    country: Literal["SA", "AE"] = Field(default="AE", description="For rows without a country column")
    date_order: Literal["dmy", "mdy"] = Field(default="dmy", description="How 03/04/2026 reads: 3 April (dmy) or 4 March (mdy)")

    @field_validator("file_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return " ".join(value.split())[:200]

    @field_validator("mapping")
    @classmethod
    def _mapping(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if len(value) > 40 or any(len(k) > 40 or len(v) > MAX_HEADER for k, v in value.items()):
            raise ValueError("The mapping is too long.")
        return {k: " ".join(v.split()) for k, v in value.items() if v and v.strip()}

    @model_validator(mode="after")
    def _one_file(self) -> FleetImportFile:
        if self.format == ImportFormat.XLSX:
            if not self.file_base64 or self.csv:
                raise ValueError("An Excel file is sent in file_base64 (and csv is left empty).")
        elif not self.csv or self.file_base64:
            raise ValueError("A CSV file is sent as text in csv (and file_base64 is left empty).")
        if self.mapping is not None:
            unknown = sorted(set(self.mapping) - set(FIELDS[self.kind]))
            if unknown:
                raise ValueError(f"Unknown fields for {self.kind}: {', '.join(unknown)}.")
        return self

    def content(self) -> str:
        return self.file_base64 if self.format == ImportFormat.XLSX else self.csv


class RowDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=2)
    action: RowAction


class FleetImportApply(FleetImportFile):
    mapping: dict[str, str]  # type: ignore[assignment]
    decisions: list[RowDecision] = Field(default_factory=list, max_length=MAX_ROWS, description="Rows that differ from the suggested action")
    fingerprint: str = Field(min_length=64, max_length=64, description="The preview's fingerprint: the same file, kind, mapping and defaults")


class RowIssue(BaseModel):
    code: IssueCode
    field: str | None = None
    value: str = ""
    text: str


class RowView(BaseModel):
    row: int
    registration: str
    summary: dict[str, str | None]
    status: RowStatus
    action: RowAction
    actions: list[RowAction]
    vehicle_id: str | None = None
    duplicate_of: int | None = None
    issues: list[RowIssue] = Field(default_factory=list)


class ImportCounts(BaseModel):
    rows: int = 0
    new: int = 0
    exists: int = 0
    duplicate_in_file: int = 0
    imported: int = 0
    invalid: int = 0


class FleetPreview(BaseModel):
    kind: ImportKind
    columns: list[str]
    fields: list[dict[str, Any]]
    mapping: dict[str, str]
    suggested: bool
    rows: list[RowView]
    counts: ImportCounts
    fingerprint: str


class FleetImportResult(BaseModel):
    kind: ImportKind
    rows: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    readings: int = 0


def _refuse(code: int, detail: str, error: str = "invalid_file", extra: dict[str, Any] | None = None) -> S.VehicleError:
    return S.VehicleError(code, detail, error, extra)


# -- reading the file ----------------------------------------------------------------------------------


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[_\-.:#/()]+", " ", text).split()).casefold()


def _check_columns(header: list[str] | None) -> list[str]:
    if not header or not any(cell.strip() for cell in header):
        raise _refuse(422, "The file is empty. The first row must name the columns (Registration, Date, Litres...).")
    columns = [" ".join(cell.split())[:MAX_HEADER] for cell in header]
    if len(columns) > MAX_COLUMNS:
        raise _refuse(422, f"The file has {len(columns)} columns; ANUM reads at most {MAX_COLUMNS}.")
    named = [c.casefold() for c in columns if c]
    if len(set(named)) != len(named):
        raise _refuse(422, "Two columns have the same name. Rename one in the first row and choose the file again.")
    return columns


def _read_csv(text: str) -> tuple[list[str], list[tuple[int, list[str]]]]:
    text = text.lstrip("﻿")
    if "\x00" in text:
        raise _refuse(422, "This is not a text CSV file. Save the sheet as CSV (UTF-8) and choose it again.")
    first = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=first.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        columns = _check_columns(next(reader, None))
        rows: list[tuple[int, list[str]]] = []
        for number, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue
            rows.append((number, cells))
            if len(rows) > MAX_ROWS:
                raise _refuse(413, f"The file has more than {MAX_ROWS} rows. Split it into files of at most {MAX_ROWS} rows.", "too_large")
    except csv.Error as exc:
        raise _refuse(422, f"The file could not be read as CSV ({exc}). Save the sheet as CSV (UTF-8) and choose it again.") from None
    return columns, rows


def load_table(payload: FleetImportFile) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """The header and the rows (each with its row number in the sheet, the header being 1)."""
    from ..contacts import import_files

    if payload.format == ImportFormat.XLSX:
        try:
            data = import_files.decode_workbook(payload.content(), max_bytes=MAX_WORKBOOK_BYTES)
            header, rows, _ = import_files.read_workbook(data, max_rows=MAX_ROWS, max_columns=MAX_COLUMNS, max_bytes=MAX_WORKBOOK_BYTES, noun="rows")
        except HTTPException as exc:
            raise _refuse(exc.status_code, str(exc.detail), "too_large" if exc.status_code == 413 else "invalid_file") from None
        columns = _check_columns(header)
    else:
        columns, rows = _read_csv(payload.content())
    if not rows:
        raise _refuse(422, "The file has a header row but no rows under it.")
    return columns, rows


def suggest_mapping(kind: str, columns: list[str]) -> dict[str, str]:
    """A first mapping from the header words (English and Arabic): an exact word beats a contained one, a longer one a shorter."""
    fields = FIELDS[kind]
    scored: list[tuple[int, int, int, str, str]] = []
    for position, column in enumerate(columns):
        folded = _fold(column)
        if not folded:
            continue
        for field in fields:
            for hint in _HINTS[field]:
                if folded == hint:
                    scored.append((2, len(hint), -position, field, column))
                elif len(hint) > 3 and re.search(rf"(^|\s){re.escape(hint)}($|\s)", folded):
                    scored.append((1, len(hint), -position, field, column))
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for _exact, _length, _position, field, column in sorted(scored, reverse=True):
        if field not in mapping and column not in taken:
            mapping[field] = column
            taken.add(column)
    return {field: mapping[field] for field in fields if field in mapping}


def _check_mapping(kind: str, mapping: dict[str, str], columns: list[str]) -> None:
    missing = [c for c in mapping.values() if c not in set(columns)]
    if missing:
        raise _refuse(422, f"The file has no column named {missing[0]!r}. Choose the columns again.", "invalid_mapping")
    needed = [f for f in REQUIRED[kind] if f not in mapping]
    if needed:
        raise _refuse(422, f"Choose the column for {', '.join(needed)}.", "invalid_mapping", {"fields": needed})
    if len(set(mapping.values())) != len(mapping):
        raise _refuse(422, "One column is chosen for two fields. Choose a different column for each.", "invalid_mapping")


def fingerprint(payload: FleetImportFile, mapping: dict[str, str]) -> str:
    """The same file with the same kind, mapping and defaults (apply refuses a preview of another file)."""
    ordered = sorted(mapping.items())
    text = "\x1e".join([payload.kind, payload.format.value, payload.country, payload.date_order, repr(ordered), payload.content().lstrip("﻿")])
    return hashlib.sha256(text.encode()).hexdigest()


# -- reading values ------------------------------------------------------------------------------------


def _plain(text: str) -> str:
    return " ".join(text.translate(_ARABIC_DIGITS).split())


def parse_number(text: str) -> Decimal | None:
    """1,234.50 / 1234,5 / AED 1,234 / ١٬٢٣٤٫٥: a plain decimal. None when it is not a number."""
    value = re.sub(r"[^\d.,\-]", "", _plain(text))
    if not value or value in {"-", ".", ","}:
        return None
    if "," in value and "." in value:
        value = value.replace(",", "")
    elif value.count(",") == 1 and len(value.split(",")[1]) != 3:
        value = value.replace(",", ".")  # a decimal comma
    else:
        value = value.replace(",", "")
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def parse_moment(text: str, date_order: str) -> datetime | None:
    """A date (and time, when the cell has one) in UTC: ISO, day/month/year (or month/day/year), or an Excel day number."""
    value = _plain(text)
    if not value:
        return None
    clock: tuple[int, int, int] | None = None
    found = _ISO_DATE.match(value)
    try:
        if found:
            year, month, day = int(found.group(1)), int(found.group(2)), int(found.group(3))
            if found.group(4):
                clock = (int(found.group(4)), int(found.group(5)), int(found.group(6) or 0))
        elif (found := _SLASH_DATE.match(value)) is not None:
            a, b, c = int(found.group(1)), int(found.group(2)), int(found.group(3))
            if found.group(1) and len(found.group(1)) == 4:
                year, month, day = a, b, c
            else:
                year = c + 2000 if c < 100 else c
                day, month = (a, b) if date_order == "dmy" else (b, a)
            if found.group(4):
                clock = (int(found.group(4)), int(found.group(5)), int(found.group(6) or 0))
        elif re.fullmatch(r"\d{4,5}(\.\d+)?", value) and 20_000 <= float(value) <= 80_000:
            serial = Decimal(value)
            day_value = _EXCEL_EPOCH + timedelta(days=int(serial))
            seconds = int((serial - int(serial)) * 86400)
            return datetime.combine(day_value, time(seconds // 3600, (seconds // 60) % 60), tzinfo=UTC)
        else:
            return None
        day_value = date(year, month, day)
        if clock is None:
            return datetime.combine(day_value, time(12, 0), tzinfo=UTC)
        return datetime.combine(day_value, time(*clock), tzinfo=UTC)
    except ValueError:
        return None


def _word(text: str, words: tuple[tuple[str, str], ...]) -> str | None:
    folded = _fold(_plain(text))
    for fragment, value in words:
        if (len(fragment) <= 3 and re.search(rf"(^|\s){re.escape(fragment)}($|\s)", folded)) or (len(fragment) > 3 and fragment in folded):
            return value
    return None


def registration_key(value: str) -> str:
    return re.sub(r"[\s\-]+", "", _plain(value)).upper()


# -- one row -------------------------------------------------------------------------------------------


class _Row:
    def __init__(self, number: int) -> None:
        self.number = number
        self.values: dict[str, Any] = {}
        self.registration = ""
        self.country: str | None = None
        self.issues: list[RowIssue] = []
        self.vehicle: Vehicle | None = None  # the vehicle a record row belongs to, or the existing vehicle of a vehicle row
        self.record: Any = None  # the record a fuel or service row would add
        self.readings: list[tuple[Decimal, datetime, str]] = []
        self.key = ""  # what makes the row the same as another (duplicates)
        self.idem = ""  # the record's idempotency key (fuel and service rows)

    def issue(self, code: IssueCode, text: str, field: str | None = None, value: str = "") -> None:
        self.issues.append(RowIssue(code=code, field=field, value=value[:60], text=text))


def _cell(cells: list[str], index: dict[str, int], column: str | None) -> str:
    if not column:
        return ""
    position = index[column]
    return cells[position].strip() if position < len(cells) else ""


def _read_values(kind: str, row: _Row, cells: list[str], index: dict[str, int], mapping: dict[str, str], payload: FleetImportFile) -> None:
    """The row's cells as typed values; a value that cannot be read is an issue, an empty one stays None."""
    today = utc_now().date()
    for field in FIELDS[kind]:
        raw = _cell(cells, index, mapping.get(field))
        if not raw:
            if field in REQUIRED[kind]:
                row.issue(IssueCode.REQUIRED, f"{field.replace('_', ' ').capitalize()} is empty.", field)
            continue
        if any(ord(ch) < 32 for ch in raw if ch not in "\n\t"):
            row.issue(IssueCode.OTHER, "The row has control characters.", field)
            continue
        if field in ("date", "odometer_on", "acquisition_date", "insurance_expires_on", "registration_expires_on", "inspection_expires_on", "next_due_on"):
            moment = parse_moment(raw, payload.date_order)
            if moment is None:
                row.issue(IssueCode.DATE, f"{raw[:40]!r} is not a date (use 2026-03-31 or 31/03/2026).", field, raw)
            elif field in ("date", "odometer_on", "acquisition_date") and moment.date() > today:
                row.issue(IssueCode.FUTURE, f"The date {moment.date().isoformat()} is in the future.", field, raw)
            else:
                row.values[field] = moment if field == "date" else moment.date()
        elif field in ("litres", "total", "price_per_litre", "odometer_km", "next_due_km", "tank_litres", "purchase_price", "year"):
            number = parse_number(raw)
            if number is None:
                row.issue(IssueCode.NUMBER, f"{raw[:40]!r} is not a number.", field, raw)
            else:
                row.values[field] = int(number) if field == "year" else number
        elif field == "country":
            country = _COUNTRY_WORDS.get(_fold(raw))
            if country is None:
                row.issue(IssueCode.CHOICE, f"{raw[:40]!r} is not a country ANUM keeps vehicles for (SA or AE).", field, raw)
            else:
                row.country = country
        elif field == "fuel_type":
            fuel = _word(raw, _FUEL_WORDS)
            if fuel is None:
                row.issue(IssueCode.CHOICE, f"{raw[:40]!r} is not a fuel type (diesel, petrol 91, 95 or 98, electric, hybrid, LPG).", field, raw)
            else:
                row.values[field] = fuel
        elif field == "vehicle_type":
            row.values[field] = _word(raw, _TYPE_WORDS) or "other"
        elif field == "kind":
            row.values[field] = _word(raw, _SERVICE_WORDS) or "other"
        elif field == "full_tank":
            folded = _fold(raw)
            if folded in _YES:
                row.values[field] = True
            elif folded in _NO:
                row.values[field] = False
            else:
                row.issue(IssueCode.CHOICE, f"{raw[:40]!r} is not yes or no.", field, raw)
        elif field == "currency":
            code = _plain(raw).upper()
            if not re.fullmatch(r"[A-Z]{3}", code):
                row.issue(IssueCode.CURRENCY, f"{raw[:40]!r} is not a currency code such as SAR or AED.", field, raw)
            else:
                row.values[field] = code
        elif field == "registration":
            row.registration = _plain(raw)
        else:
            row.values[field] = " ".join(raw.split())


def _issue_from(row: _Row, exc: Exception) -> None:
    if isinstance(exc, S.VehicleError):
        code = {"invalid_registration": IssueCode.REGISTRATION, "invalid_vin": IssueCode.VIN, "invalid_currency": IssueCode.CURRENCY,
                "in_future": IssueCode.FUTURE, "invalid_odometer": IssueCode.RANGE}.get(exc.code, IssueCode.OTHER)
        row.issue(code, exc.message)
    elif isinstance(exc, ValidationError):
        for error in exc.errors()[:3]:
            field = str(error["loc"][0]) if error.get("loc") else None
            kind = error.get("type", "")
            code = IssueCode.RANGE if kind.startswith(("greater", "less", "too_", "string_too")) or "decimal" in kind else IssueCode.OTHER
            row.issue(code, f"{(field or 'value').replace('_', ' ').capitalize()}: {error['msg']}.", field)
    else:  # pragma: no cover - every expected refusal is one of the two above
        row.issue(IssueCode.OTHER, "This row could not be read.")


# -- the workspace as it is ----------------------------------------------------------------------------


class _Fleet:
    """The caller's visible vehicles, their readings and records, read once per preview or apply."""

    def __init__(self, store: VehicleStore, context: TenantContext, kind: str) -> None:
        active, _ = store.list_vehicles(context, query=None, archived=False, limit=MAX_VEHICLES, offset=0)
        archived, _ = store.list_vehicles(context, query=None, archived=True, limit=10_000, offset=0)
        self.active = active
        self.by_registration: dict[str, list[Vehicle]] = defaultdict(list)
        self.archived: dict[str, Vehicle] = {}
        for vehicle in active:
            self.by_registration[registration_key(vehicle.registration)].append(vehicle)
        for vehicle in archived:
            self.archived.setdefault(registration_key(vehicle.registration), vehicle)
        self.readings: dict[str, list[OdometerReading]] = {v.id: store.readings(context, v.id) for v in active}
        self.records: list[Any] = store.records_for(context, RECORD_KIND[kind], [v.id for v in active]) if kind in RECORD_KIND else []
        self.idem = {r.idem_key for r in self.records if r.owner_user_id == context.user_id}


def _vehicle_for(fleet: _Fleet, row: _Row, context: TenantContext) -> Vehicle | None:
    key = registration_key(row.registration)
    candidates = fleet.by_registration.get(key, [])
    if row.country:
        candidates = [v for v in candidates if v.country == row.country]
    if len(candidates) == 1:
        return candidates[0]  # a business vehicle of the workspace, or the caller's own personal one (the store shows no other)
    if len(candidates) > 1:
        row.issue(IssueCode.AMBIGUOUS_VEHICLE, f"Two vehicles have the registration {row.registration}. Add a Country column to tell them apart.",
                  "registration", row.registration)
    elif key in fleet.archived:
        row.issue(IssueCode.ARCHIVED_VEHICLE, f"The vehicle {row.registration} is archived. Restore it first, or skip the row.", "registration", row.registration)
    else:
        row.issue(IssueCode.UNKNOWN_VEHICLE, f"No vehicle with the registration {row.registration} in this workspace. Import the vehicles first.",
                  "registration", row.registration)
    return None


def _same_figure(a: Decimal | None, b: Decimal | None) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and a.quantize(Decimal("0.01")) == b.quantize(Decimal("0.01")))


def _exists(kind: str, fleet: _Fleet, row: _Row) -> bool:
    """The same record is already in the workspace (typed by hand, or imported from another file)."""
    vehicle = row.vehicle
    if vehicle is None:
        return False
    if kind == "odometer":
        km, moment, _source = row.readings[0]
        return any(r.reading_km == km and r.read_at.date() == moment.date() for r in fleet.readings.get(vehicle.id, []))
    record = row.record
    for other in fleet.records:
        if other.vehicle_id != vehicle.id:
            continue
        if kind == "fuel" and other.filled_at.date() == record.filled_at.date() and other.litres == record.litres and _same_figure(other.total, record.total):
            return True
        if kind == "service" and other.service_on == record.service_on and other.kind == record.kind and _same_figure(other.receipt_total, record.receipt_total):
            return True
    return False


# -- building rows -------------------------------------------------------------------------------------


def _build_vehicle(context: TenantContext, row: _Row, payload: FleetImportFile, fleet: _Fleet, now: datetime) -> None:
    from .api import VehicleInput

    country = row.country or payload.country
    data = {k: v for k, v in row.values.items() if k not in ("odometer_km", "odometer_on")}
    data.update({"ownership": "business", "registration": row.registration[:20], "country": country})
    data.setdefault("fuel_type", "diesel" if data.get("vehicle_type") in ("truck", "bus", "pickup") else "petrol_95")
    for key in ("nickname", "make", "model"):
        if data.get(key):
            data[key] = str(data[key])[:60]
    for key, places in (("tank_litres", 2), ("purchase_price", 2)):
        if data.get(key) is not None:
            data[key] = data[key].quantize(Decimal(1).scaleb(-places))
    if row.values.get("odometer_km") is not None:
        row.values["odometer_km"] = row.values["odometer_km"].quantize(Decimal("0.1"))
    if data.get("notes"):
        data["notes"] = str(data["notes"])[:1000]
    try:
        body = VehicleInput.model_validate({**data, "odometer_km": row.values.get("odometer_km")})
        vehicle = S.new_vehicle(context, body.model_dump(exclude={"odometer_km"}), now)
    except (S.VehicleError, ValidationError) as exc:
        _issue_from(row, exc)
        return
    row.registration = vehicle.registration
    row.country = country
    row.key = f"{country}:{registration_key(vehicle.registration)}"
    existing = [v for v in fleet.by_registration.get(registration_key(vehicle.registration), []) if v.country == country]
    row.vehicle = existing[0] if existing else None
    row.record = vehicle
    if body.odometer_km is not None:
        on = row.values.get("odometer_on")
        moment = S.at_noon(on) if on else now
        row.readings = [(body.odometer_km, moment, "manual")]


def _build_record(context: TenantContext, kind: str, row: _Row, store: VehicleStore, fleet: _Fleet, now: datetime) -> None:
    from .api import INPUTS, _build, _planned_readings

    vehicle = _vehicle_for(fleet, row, context)
    if vehicle is None or row.issues:
        return
    row.vehicle = vehicle
    values = row.values
    if kind == "odometer":
        km = values["odometer_km"]
        if km < 0 or km >= Decimal(10_000_000):
            row.issue(IssueCode.RANGE, "Odometer: enter the kilometres shown, from 0 to 9,999,999.", "odometer_km")
            return
        moment = values["date"] if values["date"].time() != time(12, 0) else S.at_noon(values["date"].date())
        row.readings = [(km.quantize(Decimal("0.1")), moment, "manual")]
        row.key = f"{vehicle.id}:{moment.date().isoformat()}:{km}"
        return
    if kind == "fuel":
        data = {"filled_at": values["date"] if values["date"].time() != time(12, 0) else S.at_noon(values["date"].date()),
                **{k: values.get(k) for k in ("station", "fuel_type", "litres", "price_per_litre", "total", "currency", "odometer_km", "notes")}}
        data["full_tank"] = values.get("full_tank", True)
        for key, places in (("litres", 3), ("price_per_litre", 3), ("total", 2), ("odometer_km", 1)):
            if data.get(key) is not None:
                data[key] = data[key].quantize(Decimal(1).scaleb(-places))
        if data.get("station"):
            data["station"] = data["station"][:120]
        if data.get("notes"):
            data["notes"] = data["notes"][:500]
    else:
        data = {"service_on": values["date"].date(), "kind": values.get("kind") or "maintenance", "receipt_total": values.get("total"),
                **{k: values.get(k) for k in ("odometer_km", "workshop", "description", "reference", "next_due_on", "next_due_km", "currency")}}
        for key, places in (("receipt_total", 2), ("odometer_km", 1), ("next_due_km", 1)):
            if data.get(key) is not None:
                data[key] = data[key].quantize(Decimal(1).scaleb(-places))
        for key, limit in (("workshop", 120), ("description", 500), ("reference", 60)):
            if data.get(key):
                data[key] = data[key][:limit]
    try:
        body = INPUTS[kind].model_validate(data)
        record = _build(store, context, kind, vehicle, body.model_dump(exclude_unset=False), None, now, "~")  # type: ignore[arg-type]
    except (S.VehicleError, ValidationError) as exc:
        _issue_from(row, exc)
        return
    row.record = record
    row.readings = _planned_readings(kind, record)  # type: ignore[arg-type]
    if kind == "fuel":
        row.key = f"{vehicle.id}:{record.filled_at.date().isoformat()}:{record.litres}:{record.total}"
    else:
        row.key = f"{vehicle.id}:{record.service_on.isoformat()}:{record.kind}:{record.receipt_total}"


def _parse(store: VehicleStore, context: TenantContext, payload: FleetImportFile, mapping: dict[str, str], columns: list[str],
           table: list[tuple[int, list[str]]], fleet: _Fleet, now: datetime) -> list[_Row]:
    index = {column: i for i, column in enumerate(columns)}
    rows: list[_Row] = []
    occurrences: dict[str, int] = defaultdict(int)
    for number, cells in table:
        row = _Row(number)
        _read_values(payload.kind, row, cells, index, mapping, payload)
        if not row.issues:
            if payload.kind == "vehicles":
                _build_vehicle(context, row, payload, fleet, now)
            else:
                _build_record(context, payload.kind, row, store, fleet, now)
        if row.key:
            occurrences[row.key] += 1
            # The record's idempotency key: the same row of the same content (its n-th repeat) is stored once, whatever the file.
            row.idem = S.record_key(context, "fleet-import", payload.kind, row.key, occurrences[row.key])
        rows.append(row)
    return rows


def _classify(kind: str, rows: list[_Row], fleet: _Fleet) -> dict[int, tuple[RowStatus, RowAction, list[RowAction], int | None]]:
    seen: dict[str, int] = {}
    out: dict[int, tuple[RowStatus, RowAction, list[RowAction], int | None]] = {}
    for row in rows:
        earlier = seen.get(row.key) if row.key else None
        if row.key:
            seen.setdefault(row.key, row.number)
        if row.issues:
            out[row.number] = (RowStatus.INVALID, RowAction.SKIP, [RowAction.SKIP], None)
        elif kind == "vehicles":
            if earlier is not None:
                out[row.number] = (RowStatus.DUPLICATE_IN_FILE, RowAction.SKIP, [RowAction.SKIP], earlier)
            elif row.vehicle is not None:
                out[row.number] = (RowStatus.EXISTS, RowAction.SKIP, [RowAction.SKIP, RowAction.UPDATE], None)
            else:
                out[row.number] = (RowStatus.NEW, RowAction.CREATE, [RowAction.CREATE, RowAction.SKIP], None)
        elif kind != "odometer" and row.idem in fleet.idem:
            out[row.number] = (RowStatus.IMPORTED, RowAction.SKIP, [RowAction.SKIP], None)
        elif kind == "odometer" and _exists(kind, fleet, row):
            out[row.number] = (RowStatus.EXISTS, RowAction.SKIP, [RowAction.SKIP], None)
        elif earlier is not None:
            out[row.number] = (RowStatus.DUPLICATE_IN_FILE, RowAction.SKIP, [RowAction.SKIP, RowAction.CREATE], earlier)
        elif _exists(kind, fleet, row):
            out[row.number] = (RowStatus.EXISTS, RowAction.SKIP, [RowAction.SKIP, RowAction.CREATE], None)
        else:
            out[row.number] = (RowStatus.NEW, RowAction.CREATE, [RowAction.CREATE, RowAction.SKIP], None)
    return out


def _odometer_order(context: TenantContext, rows: list[_Row], actions: dict[int, RowAction], fleet: _Fleet) -> dict[int, RowIssue]:
    """Per vehicle, the readings of the rows that would be written, in time order against the vehicle's readings: a row whose
    reading would make the odometer go down gets an issue. The first row in time wins; the later one is refused."""
    problems: dict[int, RowIssue] = {}
    per_vehicle: dict[str, list[tuple[datetime, int, Decimal, _Row]]] = defaultdict(list)
    for row in rows:
        if actions.get(row.number) == RowAction.SKIP or not row.readings:
            continue
        vehicle_id = row.vehicle.id if row.vehicle is not None else f"new:{row.key}"
        for km, moment, _source in row.readings:
            per_vehicle[vehicle_id].append((moment, row.number, km, row))
    now = utc_now()
    for vehicle_id, planned in per_vehicle.items():
        known = list(fleet.readings.get(vehicle_id, []))
        for moment, number, km, row in sorted(planned, key=lambda item: (item[0], item[1])):
            if number in problems:
                continue
            probe = OdometerReading(id=f"~row{number}", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                    vehicle_id=vehicle_id, visibility=Ownership.BUSINESS, vehicle_owner_id=context.user_id, reading_km=km,
                                    read_at=moment, source="manual", created_at=now)
            before, after = check_odometer(known, probe)
            other = before or after
            if other is not None:
                where = f"row {other.id[4:]}" if other.id.startswith("~row") else other.read_at.date().isoformat()
                problems[number] = RowIssue(code=IssueCode.ODOMETER, field="odometer_km", value=str(km),
                                            text=f"The odometer cannot go down: {other.reading_km} km is recorded on {where}.")
            else:
                known.append(probe)
    return problems


def _summary(kind: str, row: _Row) -> dict[str, str | None]:
    v = row.values

    def text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    if kind == "vehicles":
        return {"nickname": text(v.get("nickname")), "make": text(v.get("make")), "model": text(v.get("model")), "country": row.country,
                "insurance_expires_on": text(v.get("insurance_expires_on")), "registration_expires_on": text(v.get("registration_expires_on")),
                "odometer_km": text(v.get("odometer_km"))}
    if kind == "fuel":
        return {"date": text(v.get("date")), "litres": text(v.get("litres")), "total": text(v.get("total")), "odometer_km": text(v.get("odometer_km")),
                "currency": text(getattr(row.record, "currency", None))}
    if kind == "service":
        return {"date": text(v.get("date")), "kind": text(v.get("kind") or "maintenance"), "total": text(v.get("total")),
                "odometer_km": text(v.get("odometer_km")), "currency": text(getattr(row.record, "currency", None))}
    return {"date": text(v.get("date")), "odometer_km": text(v.get("odometer_km"))}


def _evaluate(store: VehicleStore, context: TenantContext, payload: FleetImportFile, mapping: dict[str, str],
              decisions: dict[int, RowAction] | None) -> tuple[list[_Row], list[RowView], _Fleet]:
    columns, table = load_table(payload)
    _check_mapping(payload.kind, mapping, columns)
    fleet = _Fleet(store, context, payload.kind)
    now = utc_now()
    rows = _parse(store, context, payload, mapping, columns, table, fleet, now)
    classes = _classify(payload.kind, rows, fleet)
    actions = {number: (decisions or {}).get(number, suggested) for number, (_s, suggested, _a, _d) in classes.items()}
    odometer = _odometer_order(context, rows, actions, fleet)
    views: list[RowView] = []
    for row in rows:
        status, action, allowed, earlier = classes[row.number]
        if row.number in odometer:
            row.issues.append(odometer[row.number])
            status, action, allowed = RowStatus.INVALID, RowAction.SKIP, [RowAction.SKIP]
        views.append(RowView(row=row.number, registration=row.registration, summary=_summary(payload.kind, row), status=status, action=action,
                             actions=allowed, vehicle_id=row.vehicle.id if row.vehicle is not None else None, duplicate_of=earlier, issues=row.issues))
    return rows, views, fleet


def _fields(kind: str) -> list[dict[str, Any]]:
    return [{"field": field, "required": field in REQUIRED[kind]} for field in FIELDS[kind]]


def preview(store: VehicleStore, context: TenantContext, payload: FleetImportFile) -> FleetPreview:
    columns, table = load_table(payload)
    suggested = payload.mapping is None
    mapping = payload.mapping if payload.mapping is not None else suggest_mapping(payload.kind, columns)
    if suggested and any(f not in mapping for f in REQUIRED[payload.kind]):
        # Not every required column was recognised: the caller chooses (the preview still lists the columns).
        return FleetPreview(kind=payload.kind, columns=columns, fields=_fields(payload.kind), mapping=mapping, suggested=True, rows=[],
                            counts=ImportCounts(rows=len(table)), fingerprint=fingerprint(payload, mapping))
    _rows, views, _fleet = _evaluate(store, context, payload, mapping, None)
    counts = ImportCounts(rows=len(views))
    for view in views:
        setattr(counts, view.status.value, getattr(counts, view.status.value) + 1)
    return FleetPreview(kind=payload.kind, columns=columns, fields=_fields(payload.kind), mapping=mapping, suggested=suggested, rows=views,
                        counts=counts, fingerprint=fingerprint(payload, mapping))


def apply(store: VehicleStore, context: TenantContext, payload: FleetImportApply) -> FleetImportResult:
    """Check every decision against the workspace as it is now, then write all of it in the caller's unit of work."""
    if payload.fingerprint != fingerprint(payload, payload.mapping):
        raise _refuse(409, "The file or the columns changed since the preview. Preview the file again, then import.", "stale_preview")
    chosen = {d.row: d.action for d in payload.decisions}
    rows, views, fleet = _evaluate(store, context, payload, payload.mapping, chosen)
    by_number = {row.number: row for row in rows}
    unknown = sorted(set(chosen) - set(by_number))
    if unknown:
        raise _refuse(422, f"Row {unknown[0]} is not in the file.", "invalid_decision")
    problems: list[str] = []
    plan: list[tuple[_Row, RowAction]] = []
    for view in views:
        action = chosen.get(view.row, view.action)
        if action not in view.actions:
            reason = view.issues[0].text if view.issues else "that action is not possible for this row"
            problems.append(f"Row {view.row}: {reason}")
            continue
        plan.append((by_number[view.row], action))
    if problems:
        shown = " ".join(problems[:SHOWN_PROBLEMS]) + (f" And {len(problems) - SHOWN_PROBLEMS} more." if len(problems) > SHOWN_PROBLEMS else "")
        raise _refuse(409, f"Nothing was imported. {shown} Fix the file or skip those rows, then preview again.", "rows_refused",
                      {"rows": len(problems)})
    result = FleetImportResult(kind=payload.kind, rows=len(views))
    if payload.kind == "vehicles":
        creating = sum(1 for _row, action in plan if action == RowAction.CREATE)
        if len(fleet.active) + creating > MAX_VEHICLES:
            raise _refuse(409, f"A workspace keeps up to {MAX_VEHICLES} active vehicles; this file would make {len(fleet.active) + creating}.",
                          "too_many")
    now = utc_now()
    lock = getattr(store, "lock_vehicle", None)
    locked: set[str] = set()
    for row, action in sorted(plan, key=lambda item: (min((m for _k, m, _s in item[0].readings), default=now), item[0].number)):
        if action == RowAction.SKIP:
            result.skipped += 1
            continue
        if payload.kind == "vehicles":
            vehicle = _write_vehicle(store, context, row, action, now)
            result.created += action == RowAction.CREATE
            result.updated += action == RowAction.UPDATE
        else:
            vehicle = row.vehicle
            assert vehicle is not None  # nosec B101: a record row that is not skipped has its vehicle
            if callable(lock) and vehicle.id not in locked:
                lock(context, vehicle.id)
                locked.add(vehicle.id)
            if row.record is not None:
                record = row.record.model_copy(update={"idem_key": row.idem, "created_at": now, "updated_at": now})
                try:
                    store.add_record(RECORD_KIND[payload.kind], record)  # type: ignore[arg-type]
                except DuplicateError:
                    raise _refuse(409, f"Row {row.number} was imported a moment ago. Preview the file again.", "stale_preview") from None
                row.record = record
            result.created += 1
        source_id = row.record.id if payload.kind in RECORD_KIND else None
        for km, moment, source in row.readings:
            # Checked in time order against the vehicle's readings above (``_odometer_order``); the store (and in PostgreSQL the
            # trigger ``vehicle_odometer_monotonic``) refuses a reading that would still make the odometer go down.
            reading = OdometerReading(id=new_id("vodo"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                                      vehicle_id=vehicle.id, visibility=vehicle.ownership, vehicle_owner_id=vehicle.owner_user_id, reading_km=km,
                                      read_at=moment, source=source, source_id=source_id, created_at=now)
            try:
                store.add_reading(reading)
            except OdometerRefused:
                raise _refuse(409, f"Nothing was imported. Row {row.number}: the odometer cannot go down. Preview the file again.",
                              "odometer_decrease") from None
            result.readings += 1
    return result


def _write_vehicle(store: VehicleStore, context: TenantContext, row: _Row, action: RowAction, now: datetime) -> Vehicle:
    new: Vehicle = row.record
    if action == RowAction.CREATE:
        try:
            store.add_vehicle(new)
        except DuplicateError:
            raise _refuse(409, f"Nothing was imported. Row {row.number}: a vehicle with the registration {new.registration} was added a moment ago. "
                               "Preview the file again.", "duplicate_registration") from None
        return new
    current = row.vehicle
    assert current is not None  # nosec B101: an update row names its vehicle
    if not S.can_edit_vehicle(context, current):
        raise _refuse(403, f"Row {row.number}: only the person who added {current.registration} or a workspace owner can change it.", "not_vehicle_owner")
    changes = {k: v for k, v in row.values.items() if k not in ("odometer_km", "odometer_on", "country") and v is not None}
    for key in ("nickname", "make", "model"):
        if key in changes:
            changes[key] = str(changes[key])[:60]
    updated = S.apply_vehicle_changes(current, changes, now)
    if not store.save_vehicle(updated, current.version):
        raise _refuse(409, f"Nothing was imported. Row {row.number}: {current.registration} changed since the preview. Preview the file again.", "stale")
    return updated


__all__ = [
    "FIELDS",
    "REQUIRED",
    "FleetImportApply",
    "FleetImportFile",
    "FleetImportResult",
    "FleetPreview",
    "apply",
    "parse_moment",
    "parse_number",
    "preview",
    "suggest_mapping",
]
