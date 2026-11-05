"""ANUM's tyre model and the owner-editable mapping onto it (docs/tyre-planning.md).

The owner's data lives in their own Supabase or Firebase project with their own table
and column names. A :class:`TyreMapping` says, per ANUM entity (vehicles, tyres,
tyre readings from inspections, pressure readings, removals, costs, suppliers, and the
fleet's operations data: inspection visits and schedules, workshop checklists, work
orders, odometer and engine-hour readings, sites), which table or collection to read and
which source column fills each ANUM field. Sync applies it and keeps the
result as a :class:`TyreDataset`; every report, forecast, plan and alert is computed from
that dataset, never from the source directly.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceKind(StrEnum):
    # A dedicated TyrePulse user signed in through Supabase Auth; reads PostgREST with its JWT.
    SUPABASE_AUTH = "supabase_auth"
    SUPABASE_POSTGRES = "supabase_postgres"
    SUPABASE_REST = "supabase_rest"
    FIRESTORE = "firestore"


class Entity(StrEnum):
    VEHICLES = "vehicles"
    TYRES = "tyres"
    INSPECTIONS = "inspections"
    PRESSURE_READINGS = "pressure_readings"
    REMOVALS = "removals"
    COSTS = "costs"
    SUPPLIERS = "suppliers"
    # Fleet operations (docs/tyre-planning.md#fleet-operations-data).
    INSPECTION_VISITS = "inspection_visits"
    INSPECTION_SCHEDULES = "inspection_schedules"
    CHECKLISTS = "checklists"
    WORK_ORDERS = "work_orders"
    ODOMETER_READINGS = "odometer_readings"
    HOUR_READINGS = "hour_readings"
    SITES = "sites"


class FieldKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"


class FieldSpec(BaseModel):
    name: str
    kind: FieldKind
    required: bool = False
    description: str


def _f(name: str, kind: FieldKind, description: str, required: bool = False) -> FieldSpec:
    return FieldSpec(name=name, kind=kind, description=description, required=required)


T, N, D, B = FieldKind.TEXT, FieldKind.NUMBER, FieldKind.DATE, FieldKind.BOOLEAN

# What each ANUM entity holds. "One of" requirements (a tyre id or a serial) are checked
# in ``TyreMapping.problems``.
ENTITY_FIELDS: dict[Entity, list[FieldSpec]] = {
    Entity.VEHICLES: [
        _f("id", T, "Vehicle or asset number that other tables refer to", True),
        _f("label", T, "Registration or display name"),
        _f("fleet_number", T, "Fleet or unit number"),
        _f("vehicle_type", T, "Vehicle type (truck, trailer, loader...)"),
        _f("site", T, "Site, depot or branch"),
        _f("odometer_km", N, "Current odometer"),
        _f("hours", N, "Current engine hours"),
        _f("status", T, "Vehicle status"),
    ],
    Entity.TYRES: [
        _f("id", T, "Tyre record id", True),
        _f("serial", T, "Tyre serial number"),
        _f("brand", T, "Brand"),
        _f("pattern", T, "Pattern or model"),
        _f("size", T, "Size, for example 315/80R22.5"),
        _f("dot", T, "DOT code (week and year of manufacture)"),
        _f("vehicle_id", T, "Vehicle the tyre is (or was) fitted to"),
        _f("position", T, "Wheel position"),
        _f("status", T, "Fitted, in stock, removed or scrapped"),
        _f("fitted_at", D, "Fitment date"),
        _f("fitted_km", N, "Odometer at fitment"),
        _f("fitted_hours", N, "Engine hours at fitment"),
        _f("removed_at", D, "Removal date"),
        _f("removed_km", N, "Odometer at removal"),
        _f("removed_hours", N, "Engine hours at removal"),
        _f("removal_reason", T, "Removal or scrap reason"),
        _f("total_km", N, "Distance run by the tyre"),
        _f("total_hours", N, "Hours run by the tyre"),
        _f("purchase_cost", N, "Purchase cost of the tyre"),
        _f("supplier", T, "Supplier name or id"),
        _f("original_tread", N, "Tread depth when new"),
    ],
    Entity.INSPECTIONS: [
        _f("tyre_id", T, "Tyre record id (or map the serial)"),
        _f("tyre_serial", T, "Tyre serial (used when there is no tyre id)"),
        _f("vehicle_id", T, "Vehicle (with the position, finds the tyre fitted there on the day)"),
        _f("position", T, "Wheel position"),
        _f("inspected_at", D, "Inspection date", True),
        _f("tread_depth", N, "Tread depth (the lowest groove)"),
        _f("pressure", N, "Measured pressure"),
        _f("target_pressure", N, "Recommended pressure"),
        _f("odometer_km", N, "Odometer at inspection"),
        _f("hours", N, "Engine hours at inspection"),
    ],
    Entity.PRESSURE_READINGS: [
        _f("tyre_id", T, "Tyre record id (or map the serial or the vehicle)"),
        _f("tyre_serial", T, "Tyre serial"),
        _f("vehicle_id", T, "Vehicle"),
        _f("position", T, "Wheel position"),
        _f("recorded_at", D, "Reading time", True),
        _f("pressure", N, "Measured pressure", True),
        _f("target_pressure", N, "Recommended pressure"),
    ],
    Entity.REMOVALS: [
        _f("tyre_id", T, "Tyre record id (or map the serial)"),
        _f("tyre_serial", T, "Tyre serial"),
        _f("vehicle_id", T, "Vehicle"),
        _f("position", T, "Wheel position"),
        _f("removed_at", D, "Removal date", True),
        _f("reason", T, "Removal or scrap reason"),
        _f("odometer_km", N, "Odometer at removal"),
        _f("hours", N, "Engine hours at removal"),
        _f("tread_depth", N, "Tread depth at removal"),
    ],
    Entity.COSTS: [
        _f("tyre_id", T, "Tyre record id"),
        _f("tyre_serial", T, "Tyre serial"),
        _f("vehicle_id", T, "Vehicle"),
        _f("date", D, "Cost date", True),
        _f("amount", N, "Amount", True),
        _f("category", T, "Purchase, repair, retread, fitting..."),
        _f("supplier", T, "Supplier"),
    ],
    Entity.SUPPLIERS: [
        _f("id", T, "Supplier id or code", True),
        _f("name", T, "Supplier name"),
    ],
    Entity.INSPECTION_VISITS: [
        _f("id", T, "Inspection id", True),
        _f("vehicle_id", T, "Vehicle inspected", True),
        _f("inspected_at", D, "Inspection date", True),
        _f("site", T, "Site"),
        _f("inspection_type", T, "Inspection type"),
        _f("status", T, "Status (done, in progress...)"),
        _f("severity", T, "Severity of the findings"),
        _f("approval_status", T, "Approval status"),
        _f("odometer_km", N, "Odometer at inspection"),
        _f("hours", N, "Engine hours at inspection"),
    ],
    Entity.INSPECTION_SCHEDULES: [
        _f("id", T, "Schedule id", True),
        _f("scheduled_for", D, "Scheduled date", True),
        _f("vehicle_id", T, "Vehicle"),
        _f("site", T, "Site"),
        _f("inspection_type", T, "Inspection type"),
        _f("priority", T, "Priority"),
        _f("status", T, "Status (scheduled, completed...)"),
        _f("completed_inspection_id", T, "The inspection that completed it"),
    ],
    Entity.CHECKLISTS: [
        _f("id", T, "Submission id", True),
        _f("submitted_at", D, "Submission date", True),
        _f("template", T, "Checklist template name"),
        _f("vehicle_id", T, "Vehicle"),
        _f("site", T, "Site"),
        _f("title", T, "Title"),
        _f("status", T, "Status"),
        _f("score_pct", N, "Score in percent"),
        _f("passed", B, "Passed (true or false)"),
        _f("approval_status", T, "Approval status"),
    ],
    Entity.WORK_ORDERS: [
        _f("id", T, "Work order number", True),
        _f("opened_at", D, "Opened date", True),
        _f("vehicle_id", T, "Vehicle"),
        _f("site", T, "Site or workshop"),
        _f("status", T, "Status (open, completed, closed...)"),
        _f("priority", T, "Priority"),
        _f("work_type", T, "Work type"),
        _f("completed_at", D, "Completion date"),
        _f("tyre_serial", T, "Tyre serial, for tyre work"),
        _f("labour_hours", N, "Labour hours"),
        _f("labour_cost", N, "Labour cost"),
        _f("parts_cost", N, "Parts cost"),
        _f("tyre_cost", N, "Tyre cost"),
        _f("total_cost", N, "Total cost"),
    ],
    Entity.ODOMETER_READINGS: [
        _f("vehicle_id", T, "Vehicle", True),
        _f("read_at", D, "Reading date", True),
        _f("odometer_km", N, "Odometer", True),
        _f("site", T, "Site"),
    ],
    Entity.HOUR_READINGS: [
        _f("vehicle_id", T, "Vehicle", True),
        _f("read_at", D, "Reading date", True),
        _f("hours", N, "Engine hours", True),
        _f("site", T, "Site"),
    ],
    Entity.SITES: [
        _f("id", T, "Site id or name that other tables use", True),
        _f("name", T, "Site name"),
        _f("region", T, "Region"),
        _f("city", T, "City"),
        _f("active", B, "Active"),
    ],
}

# Entities whose rows attach to a tyre. Removals need the tyre (id or serial); readings may
# also attach to a vehicle (and position), as TyrePulse's inspections do.
TYRE_LINKED = (Entity.INSPECTIONS, Entity.PRESSURE_READINGS, Entity.REMOVALS)
VEHICLE_LINK_ALLOWED = (Entity.INSPECTIONS, Entity.PRESSURE_READINGS)

# Columns ANUM never reads, whatever the mapping says: people's contact details, names of
# staff and drivers, signatures, photos and locations (docs/tyre-planning.md#what-anum-reads).
PERSONAL_COLUMN = re.compile(
    r"e-?mail|phone|mobile|whatsapp|password|passcode|token|secret|signature|licen[cs]e|national_id|iqama|passport"
    r"|(^|[_.])(driver|inspector|technician|approver|reporter|operator|supervisor|assessor|actor|entry|washed_by)([_.]|$)"
    r"|printed_name|full_name|first_name|last_name|username|user\d*_(name|code)|latitude|longitude|(^|[_.])gps"
    r"|photo|image|selfie|attendees",
    re.IGNORECASE,
)


class TreadUnit(StrEnum):
    MM = "mm"
    THIRTY_SECONDS = "32nds"


class PressureUnit(StrEnum):
    PSI = "psi"
    BAR = "bar"
    KPA = "kpa"


class DistanceUnit(StrEnum):
    KM = "km"
    MILES = "miles"


class Units(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tread: TreadUnit = TreadUnit.MM
    pressure: PressureUnit = PressureUnit.PSI
    distance: DistanceUnit = DistanceUnit.KM
    currency: str = Field(default="SAR", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


IDENTIFIER = r"^[A-Za-z0-9_][A-Za-z0-9_.\-/ ]{0,199}$"


class EntityMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Table (Supabase, optionally "schema.table") or collection path (Firestore).
    source: str = Field(min_length=1, max_length=200, pattern=IDENTIFIER)
    # ANUM field -> source column (Firestore: field path such as "specs.size").
    fields: dict[str, str] = Field(default_factory=dict)
    # A column holding one entry per tyre (a JSON object keyed by position, or a list), as
    # TyrePulse's ``inspections.tyre_conditions``: each entry becomes its own row, and fields
    # name the entry's keys as "<column>.<key>".
    expand: str | None = Field(default=None, max_length=200, pattern=IDENTIFIER)
    # Large tables: sync only rows whose ``window_column`` (a date) is within the last
    # ``window_days`` days, newest first.
    window_days: int | None = Field(default=None, ge=1, le=3650)
    window_column: str | None = Field(default=None, max_length=200, pattern=IDENTIFIER)

    @field_validator("fields")
    @classmethod
    def _columns(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, column in value.items():
            column = column.strip()
            if not column:
                continue
            if len(column) > 200 or not _valid_identifier(column):
                raise ValueError(f"{key}: source column names are letters, digits, '_', '.', '-', '/' or spaces")
            if PERSONAL_COLUMN.search(column):
                raise ValueError(
                    f"{key}: ANUM does not read '{column}': names, contact details, signatures, photos and "
                    "locations of people stay in the source."
                )
            cleaned[key] = column
        return cleaned

    @model_validator(mode="after")
    def _window(self) -> EntityMapping:
        if (self.window_days is None) != (self.window_column is None):
            raise ValueError("window_days and window_column go together")
        return self


def _valid_identifier(value: str) -> bool:
    import re

    return bool(re.fullmatch(IDENTIFIER, value))


class TyreMapping(BaseModel):
    """The owner's mapping. ``preset`` names a ready-made mapping it started from."""

    model_config = ConfigDict(extra="forbid")

    preset: str | None = Field(default=None, max_length=40)
    entities: dict[Entity, EntityMapping] = Field(default_factory=dict)
    units: Units = Field(default_factory=Units)
    # Raw status text (lowercase) -> fitted | stock | removed | scrapped.
    status_values: dict[str, str] = Field(default_factory=dict)
    # Wheel position codes of readings -> the tyres' codes, when the two use different
    # systems (TyrePulse inspections say "F1L", its tyre records "LHF1").
    position_aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("position_aliases")
    @classmethod
    def _aliases(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 200:
            raise ValueError("position_aliases: at most 200 entries")
        cleaned = {}
        for raw, canonical in value.items():
            key, target = normalise_position(raw), normalise_position(canonical)
            if not key or not target or len(key) > 40 or len(target) > 40:
                raise ValueError("position_aliases: position codes of 1 to 40 characters")
            cleaned[key] = target
        return cleaned

    @field_validator("status_values")
    @classmethod
    def _statuses(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {item.value for item in TyreStatus}
        cleaned = {}
        for raw, canonical in value.items():
            if canonical not in allowed:
                raise ValueError(f"status_values: {canonical!r} is not one of {sorted(allowed)}")
            cleaned[raw.strip().lower()] = canonical
        return cleaned

    @model_validator(mode="after")
    def _known_fields(self) -> TyreMapping:
        for entity, mapping in self.entities.items():
            known = {spec.name for spec in ENTITY_FIELDS[entity]}
            unknown = sorted(set(mapping.fields) - known)
            if unknown:
                raise ValueError(f"{entity.value}: unknown ANUM fields {unknown}")
        return self

    def problems(self) -> list[str]:
        """What stops a sync: missing entities or required fields. Empty when usable."""
        found: list[str] = []
        if Entity.TYRES not in self.entities:
            found.append("Map a table to Tyres: everything else hangs off it.")
        for entity, mapping in self.entities.items():
            for spec in ENTITY_FIELDS[entity]:
                if spec.required and spec.name not in mapping.fields:
                    found.append(f"{entity.value}: map a column to '{spec.name}' ({spec.description}).")
            if entity in VEHICLE_LINK_ALLOWED:
                if not {"tyre_id", "tyre_serial", "vehicle_id"} & set(mapping.fields):
                    found.append(f"{entity.value}: map 'tyre_id', 'tyre_serial' or 'vehicle_id' so rows attach to a tyre or vehicle.")
            elif entity in TYRE_LINKED and not {"tyre_id", "tyre_serial"} & set(mapping.fields):
                found.append(f"{entity.value}: map either 'tyre_id' or 'tyre_serial' so rows attach to a tyre.")
            if mapping.expand and not any(column.startswith(mapping.expand + ".") for column in mapping.fields.values()):
                found.append(f"{entity.value}: no field reads the entries of '{mapping.expand}'.")
        return found


def normalise_position(value: str | None) -> str:
    """Position codes compare without case or spaces ("lhf1 " is "LHF1")."""
    return re.sub(r"\s+", "", value or "").upper()


class Assumptions(BaseModel):
    """Planning assumptions the owner can edit; the method notes explain each one."""

    model_config = ConfigDict(extra="forbid")

    # Remove a tyre when its tread reaches this depth (mm).
    min_tread_mm: float = Field(default=3.0, ge=0.5, le=20)
    # Warn this many mm above the minimum.
    warning_band_mm: float = Field(default=2.0, ge=0, le=10)
    # Pressure within +/- this percent of the target is compliant.
    pressure_tolerance_pct: float = Field(default=10.0, ge=1, le=50)
    # Target used when a reading carries none (in the mapping's pressure unit, converted).
    default_target_pressure_psi: float | None = Field(default=None, ge=10, le=200)
    # A fitted tyre not inspected for this many days is overdue.
    inspection_interval_days: int = Field(default=30, ge=1, le=365)
    # A tyre wearing faster than this multiple of its brand+size median is abnormal.
    abnormal_wear_factor: float = Field(default=1.5, ge=1.1, le=5)
    # A cost entry above this multiple of its category's median is an anomaly.
    cost_anomaly_factor: float = Field(default=3.0, ge=1.5, le=20)
    # New tread depth when the data has none (mm).
    default_new_tread_mm: float = Field(default=16.0, ge=4, le=40)
    # Purchase plan.
    horizon_months: int = Field(default=12, ge=1, le=36)
    safety_stock_pct: float = Field(default=10.0, ge=0, le=100)
    include_low_confidence: bool = True
    subtract_stock: bool = True
    # "size|brand" -> unit price; "size" -> unit price for any brand.
    unit_prices: dict[str, float] = Field(default_factory=dict)
    # size -> brand to buy instead of the brand being replaced.
    preferred_brands: dict[str, str] = Field(default_factory=dict)

    @field_validator("unit_prices")
    @classmethod
    def _prices(cls, value: dict[str, float]) -> dict[str, float]:
        for key, price in value.items():
            if not key.strip() or price < 0 or price > 1_000_000:
                raise ValueError("unit_prices: a size or 'size|brand' key and a price between 0 and 1,000,000")
        if len(value) > 500:
            raise ValueError("unit_prices: at most 500 entries")
        return {key.strip(): float(price) for key, price in value.items()}

    @field_validator("preferred_brands")
    @classmethod
    def _brands(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 500:
            raise ValueError("preferred_brands: at most 500 entries")
        return {key.strip(): brand.strip() for key, brand in value.items() if key.strip() and brand.strip()}


# --- the dataset sync produces --------------------------------------------------------


class TyreStatus(StrEnum):
    FITTED = "fitted"
    STOCK = "stock"
    REMOVED = "removed"
    SCRAPPED = "scrapped"


class Vehicle(BaseModel):
    id: str
    label: str | None = None
    fleet_number: str | None = None
    vehicle_type: str | None = None
    site: str | None = None
    odometer_km: float | None = None
    hours: float | None = None
    status: str | None = None


class Tyre(BaseModel):
    id: str
    serial: str | None = None
    brand: str | None = None
    pattern: str | None = None
    size: str | None = None
    dot: str | None = None
    vehicle_id: str | None = None
    position: str | None = None
    status: TyreStatus = TyreStatus.FITTED
    fitted_at: date | None = None
    fitted_km: float | None = None
    fitted_hours: float | None = None
    removed_at: date | None = None
    removed_km: float | None = None
    removed_hours: float | None = None
    removal_reason: str | None = None
    total_km: float | None = None
    total_hours: float | None = None
    purchase_cost: float | None = None
    supplier: str | None = None
    original_tread_mm: float | None = None


class Inspection(BaseModel):
    # None: a reading of a vehicle position that no tyre record matches (TyrePulse
    # inspections name the position, rarely the serial).
    tyre_id: str | None = None
    vehicle_id: str | None = None
    position: str | None = None
    inspected_at: date
    # The time of the reading when the source stores one (UTC); None for a date-only record.
    inspected_time: datetime | None = None
    tread_mm: float | None = None
    pressure_psi: float | None = None
    target_psi: float | None = None
    odometer_km: float | None = None
    hours: float | None = None


class PressureReading(BaseModel):
    tyre_id: str | None = None
    vehicle_id: str | None = None
    position: str | None = None
    recorded_at: date
    pressure_psi: float
    target_psi: float | None = None


class Removal(BaseModel):
    tyre_id: str
    vehicle_id: str | None = None
    position: str | None = None
    removed_at: date
    reason: str | None = None
    odometer_km: float | None = None
    hours: float | None = None
    tread_mm: float | None = None


class Cost(BaseModel):
    tyre_id: str | None = None
    vehicle_id: str | None = None
    date: date
    amount: float
    category: str | None = None
    supplier: str | None = None


class Supplier(BaseModel):
    id: str
    name: str | None = None


class InspectionVisit(BaseModel):
    id: str
    vehicle_id: str
    inspected_at: date
    # The time of the inspection when the source stores one (UTC); None for a date-only record.
    inspected_time: datetime | None = None
    site: str | None = None
    inspection_type: str | None = None
    status: str | None = None
    severity: str | None = None
    approval_status: str | None = None
    odometer_km: float | None = None
    hours: float | None = None


class InspectionSchedule(BaseModel):
    id: str
    scheduled_for: date
    vehicle_id: str | None = None
    site: str | None = None
    inspection_type: str | None = None
    priority: str | None = None
    status: str | None = None
    completed_inspection_id: str | None = None


class Checklist(BaseModel):
    id: str
    submitted_at: date
    template: str | None = None
    vehicle_id: str | None = None
    site: str | None = None
    title: str | None = None
    status: str | None = None
    score_pct: float | None = None
    passed: bool | None = None
    approval_status: str | None = None


class WorkOrder(BaseModel):
    id: str
    opened_at: date
    vehicle_id: str | None = None
    site: str | None = None
    status: str | None = None
    priority: str | None = None
    work_type: str | None = None
    completed_at: date | None = None
    tyre_serial: str | None = None
    labour_hours: float | None = None
    labour_cost: float | None = None
    parts_cost: float | None = None
    tyre_cost: float | None = None
    total_cost: float | None = None


class OdometerReading(BaseModel):
    vehicle_id: str
    read_at: date
    odometer_km: float
    site: str | None = None


class HourReading(BaseModel):
    vehicle_id: str
    read_at: date
    hours: float
    site: str | None = None


class Site(BaseModel):
    id: str
    name: str | None = None
    region: str | None = None
    city: str | None = None
    active: bool | None = None


class TyreDataset(BaseModel):
    vehicles: list[Vehicle] = Field(default_factory=list)
    tyres: list[Tyre] = Field(default_factory=list)
    inspections: list[Inspection] = Field(default_factory=list)
    pressure_readings: list[PressureReading] = Field(default_factory=list)
    removals: list[Removal] = Field(default_factory=list)
    costs: list[Cost] = Field(default_factory=list)
    suppliers: list[Supplier] = Field(default_factory=list)
    inspection_visits: list[InspectionVisit] = Field(default_factory=list)
    inspection_schedules: list[InspectionSchedule] = Field(default_factory=list)
    checklists: list[Checklist] = Field(default_factory=list)
    work_orders: list[WorkOrder] = Field(default_factory=list)
    odometer_readings: list[OdometerReading] = Field(default_factory=list)
    hour_readings: list[HourReading] = Field(default_factory=list)
    sites: list[Site] = Field(default_factory=list)
    currency: str = "SAR"


class EntityStats(BaseModel):
    read: int = 0
    kept: int = 0
    skipped: int = 0
    cut: bool = False
    # Up to a few reasons, with counts: never row contents.
    issues: dict[str, int] = Field(default_factory=dict)


class SyncStats(BaseModel):
    synced_at: datetime
    entities: dict[str, EntityStats] = Field(default_factory=dict)
    duration_ms: int = 0


def dataset_counts(dataset: TyreDataset) -> dict[str, int]:
    return {entity.value: len(getattr(dataset, entity.value)) for entity in Entity}


JsonRow = dict[str, Any]
