"""ANUM's tyre model and the owner-editable mapping onto it (docs/tyre-planning.md).

The owner's data lives in their own Supabase or Firebase project with their own table
and column names. A :class:`TyreMapping` says, per ANUM entity (vehicles, tyres,
inspections, pressure readings, removals, costs, suppliers), which table or collection
to read and which source column fills each ANUM field. Sync applies it and keeps the
result as a :class:`TyreDataset`; every report, forecast, plan and alert is computed from
that dataset, never from the source directly.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceKind(StrEnum):
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


class FieldKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"


class FieldSpec(BaseModel):
    name: str
    kind: FieldKind
    required: bool = False
    description: str


def _f(name: str, kind: FieldKind, description: str, required: bool = False) -> FieldSpec:
    return FieldSpec(name=name, kind=kind, description=description, required=required)


T, N, D = FieldKind.TEXT, FieldKind.NUMBER, FieldKind.DATE

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
        _f("vehicle_id", T, "Vehicle"),
        _f("position", T, "Wheel position"),
        _f("inspected_at", D, "Inspection date", True),
        _f("tread_depth", N, "Tread depth (the lowest groove)"),
        _f("pressure", N, "Measured pressure"),
        _f("target_pressure", N, "Recommended pressure"),
        _f("odometer_km", N, "Odometer at inspection"),
        _f("hours", N, "Engine hours at inspection"),
    ],
    Entity.PRESSURE_READINGS: [
        _f("tyre_id", T, "Tyre record id (or map the serial)"),
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
}

# Entities whose rows attach to a tyre by id or serial: one of the two must be mapped.
TYRE_LINKED = (Entity.INSPECTIONS, Entity.PRESSURE_READINGS, Entity.REMOVALS)


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
            cleaned[key] = column
        return cleaned


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
            if entity in TYRE_LINKED and not {"tyre_id", "tyre_serial"} & set(mapping.fields):
                found.append(f"{entity.value}: map either 'tyre_id' or 'tyre_serial' so rows attach to a tyre.")
        return found


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
    tyre_id: str
    vehicle_id: str | None = None
    position: str | None = None
    inspected_at: date
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


class TyreDataset(BaseModel):
    vehicles: list[Vehicle] = Field(default_factory=list)
    tyres: list[Tyre] = Field(default_factory=list)
    inspections: list[Inspection] = Field(default_factory=list)
    pressure_readings: list[PressureReading] = Field(default_factory=list)
    removals: list[Removal] = Field(default_factory=list)
    costs: list[Cost] = Field(default_factory=list)
    suppliers: list[Supplier] = Field(default_factory=list)
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
