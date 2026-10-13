"""Apply an owner's mapping to rows read from their source, and suggest mappings.

``apply_mapping`` turns raw rows (one list per ANUM entity, keyed by source column) into
a :class:`TyreDataset`: values are parsed (numbers, dates), units converted to mm, psi
and km, statuses normalised, and inspections, pressure readings and removals attached
to a tyre by id, else by serial, else by vehicle and wheel position on the day (readings
that match no tyre stay readings of that vehicle position). A column of per-tyre entries
(``EntityMapping.expand``, TyrePulse's ``inspections.tyre_conditions``) is unpacked into
one row per entry first. Rows that cannot be used are skipped and
counted by reason in :class:`SyncStats`; row contents are never recorded.

``suggest_mapping`` proposes a mapping from a discovered schema: the TyrePulse preset
when the source has TyrePulse's tables, otherwise a best guess by table and column names.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .model import (
    ENTITY_FIELDS,
    Cost,
    DistanceUnit,
    Entity,
    EntityMapping,
    EntityStats,
    FieldKind,
    HourReading,
    Inspection,
    InspectionSchedule,
    InspectionVisit,
    OdometerReading,
    PressureReading,
    PressureUnit,
    Checklist,
    Removal,
    Site,
    Supplier,
    TreadUnit,
    Tyre,
    TyreDataset,
    TyreMapping,
    TyreStatus,
    Units,
    Vehicle,
    WorkOrder,
    normalise_position,
)

# --- value parsing --------------------------------------------------------------------

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text[:200] or None


def parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _NUMBER.search(str(value).replace(",", ""))
        if not match:
            return None
        number = float(match.group())
    return number if math.isfinite(number) else None


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "t", "yes", "y", "1", "pass", "passed"}:
        return True
    if text in {"false", "f", "no", "n", "0", "fail", "failed"}:
        return False
    return None


_DAY_FIRST = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")


def parse_date(value: Any) -> date | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        seconds = float(value) / (1000 if value > 1e11 else 1)
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    match = _DAY_FIRST.match(text)
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def tread_to_mm(value: float | None, unit: TreadUnit) -> float | None:
    if value is None:
        return None
    return round(value * 25.4 / 32, 2) if unit == TreadUnit.THIRTY_SECONDS else value


def pressure_to_psi(value: float | None, unit: PressureUnit) -> float | None:
    if value is None:
        return None
    if unit == PressureUnit.BAR:
        return round(value * 14.5038, 1)
    if unit == PressureUnit.KPA:
        return round(value * 0.145038, 1)
    return value


def distance_to_km(value: float | None, unit: DistanceUnit) -> float | None:
    if value is None:
        return None
    return round(value * 1.609344, 1) if unit == DistanceUnit.MILES else value


_STATUS_WORDS: tuple[tuple[TyreStatus, tuple[str, ...]], ...] = (
    (TyreStatus.SCRAPPED, ("scrap", "written off", "write off", "disposed", "condemn")),
    (TyreStatus.REMOVED, ("removed", "dismount", "retread", "returned", "repair")),
    (TyreStatus.STOCK, ("stock", "store", "spare", "new", "warehouse", "inventory")),
    (TyreStatus.FITTED, ("active", "fitted", "mounted", "in service", "on vehicle", "installed", "running")),
)


def normalise_status(raw: str | None, overrides: Mapping[str, str], tyre: Tyre) -> TyreStatus:
    if raw:
        key = raw.strip().lower()
        if key in overrides:
            return TyreStatus(overrides[key])
        for status, words in _STATUS_WORDS:
            if any(word in key for word in words):
                return status
    if tyre.removed_at is not None:
        return TyreStatus.REMOVED
    return TyreStatus.FITTED if tyre.vehicle_id else TyreStatus.STOCK


# --- applying a mapping --------------------------------------------------------------


def _read(row: Mapping[str, Any], fields: Mapping[str, str], name: str, kind: FieldKind) -> Any:
    column = fields.get(name)
    if column is None:
        return None
    value = _lookup(row, column)
    if kind == FieldKind.NUMBER:
        return parse_number(value)
    if kind == FieldKind.DATE:
        return parse_date(value)
    if kind == FieldKind.BOOLEAN:
        return parse_bool(value)
    return parse_text(value)


def _lookup(row: Mapping[str, Any], column: str) -> Any:
    if column in row:
        return row[column]
    # Nested Firestore maps are flattened with dots; accept either form.
    current: Any = row
    for part in column.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _values(row: Mapping[str, Any], entity: Entity, mapping: EntityMapping) -> dict[str, Any]:
    return {spec.name: _read(row, mapping.fields, spec.name, spec.kind) for spec in ENTITY_FIELDS[entity]}


# Per-tyre entries unpacked from one row, at most (a truck has well under 64 wheels).
MAX_ENTRIES_PER_ROW = 64


def expand_rows(rows: Iterable[Mapping[str, Any]], column: str) -> list[dict[str, Any]]:
    """One row per entry of ``column`` (an object keyed by position, or a list of objects).

    The entry replaces the column's value, so "<column>.<key>" reads it; the object key is
    kept as "<column>.__key__". Rows without entries give nothing.
    """
    expanded: list[dict[str, Any]] = []
    for row in rows:
        value = row.get(column)
        if isinstance(value, Mapping):
            entries = list(value.items())
        elif isinstance(value, list):
            entries = [(str(index), item) for index, item in enumerate(value)]
        else:
            continue
        for key, entry in entries[:MAX_ENTRIES_PER_ROW]:
            if not isinstance(entry, Mapping):
                continue
            expanded.append({**row, column: {**entry, "__key__": key}})
    return expanded


def _vehicle_key(value: str | None) -> str:
    return (value or "").strip().lower()


def _covering(candidates: list[Tyre], on: date) -> Tyre | None:
    for tyre in reversed(candidates):
        started = tyre.fitted_at is None or tyre.fitted_at <= on
        ended = tyre.removed_at is not None and tyre.removed_at < on
        if started and not ended:
            return tyre
    return None


class _TyreIndex:
    """Find the tyre a reading belongs to: by id, else by serial and date, else by the
    vehicle and wheel position it was fitted to on that date."""

    def __init__(self, tyres: Iterable[Tyre], aliases: Mapping[str, str] | None = None) -> None:
        self.aliases = dict(aliases or {})
        self.by_id: dict[str, Tyre] = {}
        self.by_serial: dict[str, list[Tyre]] = defaultdict(list)
        self.by_position: dict[tuple[str, str], list[Tyre]] = defaultdict(list)
        for tyre in tyres:
            self.by_id[tyre.id] = tyre
            if tyre.serial:
                self.by_serial[tyre.serial.strip().lower()].append(tyre)
            if tyre.vehicle_id and tyre.position:
                self.by_position[(_vehicle_key(tyre.vehicle_id), normalise_position(tyre.position))].append(tyre)
        for items in (*self.by_serial.values(), *self.by_position.values()):
            items.sort(key=lambda item: item.fitted_at or date.min)

    def position(self, raw: str | None) -> str:
        code = normalise_position(raw)
        return self.aliases.get(code, code)

    def find(self, tyre_id: str | None, serial: str | None, on: date | None,
             vehicle_id: str | None = None, position: str | None = None) -> Tyre | None:
        if tyre_id and tyre_id in self.by_id:
            return self.by_id[tyre_id]
        candidates = self.by_serial.get(serial.strip().lower()) if serial else None
        if candidates:
            if on is not None:
                found = _covering(candidates, on)
                if found is not None:
                    return found
            return candidates[-1]
        if vehicle_id and position and on is not None:
            fitted = self.by_position.get((_vehicle_key(vehicle_id), self.position(position)))
            if fitted:
                # Only the tyre fitted there on the day; never a guess.
                return _covering(fitted, on)
        return None


def _skip(stats: EntityStats, reason: str) -> None:
    stats.skipped += 1
    stats.issues[reason] = stats.issues.get(reason, 0) + 1


def apply_mapping(
    mapping: TyreMapping,
    rows: Mapping[Entity, list[dict[str, Any]]],
    *,
    stats: dict[str, EntityStats] | None = None,
) -> tuple[TyreDataset, dict[str, EntityStats]]:
    units: Units = mapping.units
    stats = stats if stats is not None else {}
    for entity in Entity:
        if entity in mapping.entities:
            stats.setdefault(entity.value, EntityStats(read=len(rows.get(entity, []))))
    dataset = TyreDataset(currency=units.currency)

    def entity_rows(entity: Entity) -> list[dict[str, Any]]:
        if entity not in mapping.entities:
            return []
        found = rows.get(entity, [])
        column = mapping.entities[entity].expand
        if column:
            found = expand_rows(found, column)
            stats[entity.value].read = len(found)
        return found

    # Vehicles.
    seen_vehicles: set[str] = set()
    for row in entity_rows(Entity.VEHICLES):
        values = _values(row, Entity.VEHICLES, mapping.entities[Entity.VEHICLES])
        if not values["id"]:
            _skip(stats["vehicles"], "no vehicle id")
            continue
        if values["id"] in seen_vehicles:
            _skip(stats["vehicles"], "duplicate vehicle id")
            continue
        seen_vehicles.add(values["id"])
        values["odometer_km"] = distance_to_km(values["odometer_km"], units.distance)
        dataset.vehicles.append(Vehicle(**values))
        stats["vehicles"].kept += 1

    # Tyres.
    seen_tyres: set[str] = set()
    for row in entity_rows(Entity.TYRES):
        values = _values(row, Entity.TYRES, mapping.entities[Entity.TYRES])
        if not values["id"]:
            _skip(stats["tyres"], "no tyre id")
            continue
        if values["id"] in seen_tyres:
            _skip(stats["tyres"], "duplicate tyre id")
            continue
        seen_tyres.add(values["id"])
        raw_status = values.pop("status")
        # None when the source has no new-tread depth: analytics then uses the assumption.
        values["original_tread_mm"] = tread_to_mm(values.pop("original_tread"), units.tread)
        for key in ("fitted_km", "removed_km", "total_km"):
            values[key] = distance_to_km(values[key], units.distance)
        for key in ("fitted_km", "fitted_hours"):
            # TyrePulse and spreadsheets use 0 or 1 as "unknown" meter readings.
            if values[key] is not None and values[key] <= 1:
                values[key] = None
        tyre = Tyre(**values)
        tyre.status = normalise_status(raw_status, mapping.status_values, tyre)
        dataset.tyres.append(tyre)
        stats["tyres"].kept += 1

    index = _TyreIndex(dataset.tyres, mapping.position_aliases)

    # Inspections (tyre readings): attached to a tyre, else kept for the vehicle position.
    for row in entity_rows(Entity.INSPECTIONS):
        values = _values(row, Entity.INSPECTIONS, mapping.entities[Entity.INSPECTIONS])
        if values["inspected_at"] is None:
            _skip(stats["inspections"], "no inspection date")
            continue
        tread = tread_to_mm(values["tread_depth"], units.tread)
        pressure = pressure_to_psi(values["pressure"], units.pressure)
        if tread is None and (pressure is None or pressure <= 0):
            _skip(stats["inspections"], "no tread or pressure reading")
            continue
        if tread is not None and not 0 <= tread <= 40:
            _skip(stats["inspections"], "tread depth out of range")
            continue
        tyre = index.find(values["tyre_id"], values["tyre_serial"], values["inspected_at"],
                          values["vehicle_id"], values["position"])
        if tyre is None and not values["vehicle_id"]:
            _skip(stats["inspections"], "no matching tyre or vehicle")
            continue
        if tyre is None:
            stats["inspections"].issues["kept for the vehicle position (no tyre record matched)"] = (
                stats["inspections"].issues.get("kept for the vehicle position (no tyre record matched)", 0) + 1)
        dataset.inspections.append(
            Inspection(
                tyre_id=tyre.id if tyre else None,
                vehicle_id=values["vehicle_id"] or (tyre.vehicle_id if tyre else None),
                position=values["position"] or (tyre.position if tyre else None),
                inspected_at=values["inspected_at"],
                tread_mm=tread,
                pressure_psi=pressure if pressure and pressure > 0 else None,
                target_psi=pressure_to_psi(values["target_pressure"], units.pressure),
                odometer_km=distance_to_km(values["odometer_km"], units.distance),
                hours=values["hours"],
            )
        )
        stats["inspections"].kept += 1

    # Pressure readings (TPMS and manual checks).
    for row in entity_rows(Entity.PRESSURE_READINGS):
        values = _values(row, Entity.PRESSURE_READINGS, mapping.entities[Entity.PRESSURE_READINGS])
        pressure = pressure_to_psi(values["pressure"], units.pressure)
        if values["recorded_at"] is None or pressure is None or pressure <= 0:
            _skip(stats["pressure_readings"], "no date or pressure")
            continue
        tyre = index.find(values["tyre_id"], values["tyre_serial"], values["recorded_at"],
                          values["vehicle_id"], values["position"])
        if tyre is None and not values["vehicle_id"]:
            _skip(stats["pressure_readings"], "no matching tyre or vehicle")
            continue
        dataset.pressure_readings.append(
            PressureReading(
                tyre_id=tyre.id if tyre else None,
                vehicle_id=values["vehicle_id"] or (tyre.vehicle_id if tyre else None),
                position=values["position"] or (tyre.position if tyre else None),
                recorded_at=values["recorded_at"],
                pressure_psi=pressure,
                target_psi=pressure_to_psi(values["target_pressure"], units.pressure),
            )
        )
        stats["pressure_readings"].kept += 1

    # Removals: a mapped table, plus removal fields on the tyre itself.
    removed_ids: set[str] = set()
    for row in entity_rows(Entity.REMOVALS):
        values = _values(row, Entity.REMOVALS, mapping.entities[Entity.REMOVALS])
        if values["removed_at"] is None:
            _skip(stats["removals"], "no removal date")
            continue
        tyre = index.find(values["tyre_id"], values["tyre_serial"], values["removed_at"])
        if tyre is None:
            _skip(stats["removals"], "no matching tyre")
            continue
        removed_ids.add(tyre.id)
        dataset.removals.append(
            Removal(
                tyre_id=tyre.id,
                vehicle_id=values["vehicle_id"] or tyre.vehicle_id,
                position=values["position"] or tyre.position,
                removed_at=values["removed_at"],
                reason=values["reason"],
                odometer_km=distance_to_km(values["odometer_km"], units.distance),
                hours=values["hours"],
                tread_mm=tread_to_mm(values["tread_depth"], units.tread),
            )
        )
        stats["removals"].kept += 1
    for tyre in dataset.tyres:
        if tyre.removed_at is not None and tyre.id not in removed_ids:
            dataset.removals.append(
                Removal(
                    tyre_id=tyre.id,
                    vehicle_id=tyre.vehicle_id,
                    position=tyre.position,
                    removed_at=tyre.removed_at,
                    reason=tyre.removal_reason,
                    odometer_km=tyre.removed_km,
                    hours=tyre.removed_hours,
                )
            )

    # Costs.
    for row in entity_rows(Entity.COSTS):
        values = _values(row, Entity.COSTS, mapping.entities[Entity.COSTS])
        if values["date"] is None or values["amount"] is None:
            _skip(stats["costs"], "no date or amount")
            continue
        if values["amount"] <= 0:
            _skip(stats["costs"], "zero or negative amount")
            continue
        tyre = index.find(values["tyre_id"], values["tyre_serial"], values["date"])
        dataset.costs.append(
            Cost(
                tyre_id=tyre.id if tyre else None,
                vehicle_id=values["vehicle_id"] or (tyre.vehicle_id if tyre else None),
                date=values["date"],
                amount=values["amount"],
                category=(values["category"] or "").lower() or None,
                supplier=values["supplier"],
            )
        )
        stats["costs"].kept += 1

    # Suppliers.
    seen_suppliers: set[str] = set()
    for row in entity_rows(Entity.SUPPLIERS):
        values = _values(row, Entity.SUPPLIERS, mapping.entities[Entity.SUPPLIERS])
        if not values["id"] or values["id"] in seen_suppliers:
            _skip(stats["suppliers"], "no or duplicate supplier id")
            continue
        seen_suppliers.add(values["id"])
        dataset.suppliers.append(Supplier(**values))
        stats["suppliers"].kept += 1

    _apply_operations(mapping, entity_rows, dataset, stats)
    return dataset, stats


def _apply_operations(mapping: TyreMapping, entity_rows: Any, dataset: TyreDataset,
                      stats: dict[str, EntityStats]) -> None:
    """Fleet operations data: inspection visits and schedules, checklists, work orders,
    meter readings and sites. Rows missing a required value are skipped and counted."""
    units = mapping.units
    simple: tuple[tuple[Entity, type[Any], tuple[str, ...], str], ...] = (
        (Entity.INSPECTION_VISITS, InspectionVisit, ("id", "vehicle_id", "inspected_at"), "id"),
        (Entity.INSPECTION_SCHEDULES, InspectionSchedule, ("id", "scheduled_for"), "id"),
        (Entity.CHECKLISTS, Checklist, ("id", "submitted_at"), "id"),
        (Entity.WORK_ORDERS, WorkOrder, ("id", "opened_at"), "id"),
        (Entity.SITES, Site, ("id",), "id"),
        (Entity.ODOMETER_READINGS, OdometerReading, ("vehicle_id", "read_at", "odometer_km"), ""),
        (Entity.HOUR_READINGS, HourReading, ("vehicle_id", "read_at", "hours"), ""),
    )
    for entity, model, required, unique in simple:
        seen: set[str] = set()
        target = getattr(dataset, entity.value)
        for row in entity_rows(entity):
            values = _values(row, entity, mapping.entities[entity])
            missing = [name for name in required if values.get(name) is None]
            if missing:
                _skip(stats[entity.value], f"no {missing[0].replace('_', ' ')}")
                continue
            if unique:
                if values[unique] in seen:
                    _skip(stats[entity.value], "duplicate id")
                    continue
                seen.add(values[unique])
            if "odometer_km" in values:
                values["odometer_km"] = distance_to_km(values["odometer_km"], units.distance)
            if entity == Entity.ODOMETER_READINGS and values["odometer_km"] <= 0:
                _skip(stats[entity.value], "zero or negative reading")
                continue
            if entity == Entity.HOUR_READINGS and values["hours"] <= 0:
                _skip(stats[entity.value], "zero or negative reading")
                continue
            target.append(model(**values))
            stats[entity.value].kept += 1


# --- discovery and suggestions --------------------------------------------------------


class SourceColumn(BaseModel):
    name: str
    type: str = "unknown"


class SourceTable(BaseModel):
    name: str
    columns: list[SourceColumn] = Field(default_factory=list)
    # Approximate rows (PostgreSQL statistics) or the sample size read (Firestore).
    rows: int | None = None
    # Supabase PostgreSQL only: the login can write here (it should be read-only).
    writable: bool = False


# TyrePulse (the owner's own app, Supabase). tyre_records holds one row per fitment with
# its removal. Tyre readings live in ``inspections``: one row per vehicle inspection whose
# ``tyre_conditions`` holds an entry per wheel position ({"F1L": {"position", "serial_number",
# "pressure_psi", "tread_depth_mm", "condition"}, ...}); tpms_readings and
# tyre_service_events are TyrePulse's sensor and service tables. Inspections name positions
# as F1L / R1Lo while tyre records say LHF1 / LHCO: only the front axles map without doubt,
# so the preset aliases those and the owner may add the rear ones. Column names as in
# TyrePulse's migrations. Never a column about a person (see model.PERSONAL_COLUMN).
TYREPULSE_POSITION_ALIASES = {
    "FL": "LHF1", "FR": "RHF1", "F1L": "LHF1", "F1R": "RHF1", "F2L": "LHF2", "F2R": "RHF2",
}
# Work orders are many (TyrePulse: ~94,000): sync the recent ones; older ones and the line
# items and parts issues are read on demand (``live.py``).
WORK_ORDER_WINDOW_DAYS = 90

TYREPULSE_PRESET = TyreMapping(
    preset="tyrepulse",
    entities={
        Entity.VEHICLES: EntityMapping(
            source="vehicle_fleet",
            fields={
                "id": "asset_no",
                "label": "registration_no",
                "fleet_number": "fleet_number",
                "vehicle_type": "vehicle_type",
                "site": "site",
                "odometer_km": "current_km",
                "hours": "current_engine_hours",
                "status": "status",
            },
        ),
        Entity.TYRES: EntityMapping(
            source="tyre_records",
            fields={
                "id": "id",
                "serial": "serial_no",
                "brand": "brand",
                "size": "size",
                "vehicle_id": "asset_no",
                "position": "tyre_position",
                "status": "status",
                "fitted_at": "issue_date",
                "fitted_km": "km_at_fitment",
                "fitted_hours": "hrs_at_fitment",
                "removed_at": "removal_date",
                "removed_km": "km_at_removal",
                "removed_hours": "hrs_at_removal",
                "removal_reason": "removal_reason",
                "total_km": "total_km",
                "total_hours": "total_hrs",
                "purchase_cost": "cost_per_tyre",
                "supplier": "supplier",
            },
        ),
        Entity.INSPECTIONS: EntityMapping(
            source="inspections",
            expand="tyre_conditions",
            fields={
                "tyre_serial": "tyre_conditions.serial_number",
                "vehicle_id": "asset_no",
                "position": "tyre_conditions.position",
                "inspected_at": "inspection_date",
                "tread_depth": "tyre_conditions.tread_depth_mm",
                "pressure": "tyre_conditions.pressure_psi",
                "odometer_km": "odometer_km",
                "hours": "hour_meter",
            },
        ),
        Entity.PRESSURE_READINGS: EntityMapping(
            source="tpms_readings",
            fields={
                "tyre_serial": "tyre_serial",
                "vehicle_id": "asset_no",
                "position": "tyre_position",
                "recorded_at": "recorded_at",
                "pressure": "pressure",
                "target_pressure": "target_pressure",
            },
        ),
        Entity.COSTS: EntityMapping(
            source="tyre_service_events",
            fields={
                "tyre_serial": "tyre_serial",
                "vehicle_id": "asset_no",
                "date": "event_date",
                "amount": "cost",
                "category": "event_type",
            },
        ),
        Entity.SUPPLIERS: EntityMapping(
            source="suppliers",
            fields={"id": "supplier_name", "name": "supplier_name"},
        ),
        Entity.INSPECTION_VISITS: EntityMapping(
            source="inspections",
            fields={
                "id": "id",
                "vehicle_id": "asset_no",
                "inspected_at": "inspection_date",
                "site": "site",
                "inspection_type": "inspection_type",
                "status": "status",
                "severity": "severity",
                "approval_status": "approval_status",
                "odometer_km": "odometer_km",
                "hours": "hour_meter",
            },
        ),
        Entity.INSPECTION_SCHEDULES: EntityMapping(
            source="inspection_schedules",
            fields={
                "id": "id",
                "scheduled_for": "scheduled_date",
                "vehicle_id": "asset_no",
                "site": "site",
                "inspection_type": "inspection_type",
                "priority": "priority",
                "status": "status",
                "completed_inspection_id": "completed_inspection_id",
            },
        ),
        Entity.CHECKLISTS: EntityMapping(
            source="checklist_submissions",
            fields={
                "id": "id",
                "submitted_at": "submitted_at",
                "template": "template_name",
                "vehicle_id": "asset_no",
                "site": "site",
                "title": "title",
                "status": "status",
                "score_pct": "score_pct",
                "passed": "score_passed",
                "approval_status": "approval_status",
            },
        ),
        Entity.WORK_ORDERS: EntityMapping(
            source="work_orders",
            window_days=WORK_ORDER_WINDOW_DAYS,
            window_column="opened_at",
            fields={
                "id": "work_order_no",
                "opened_at": "opened_at",
                "vehicle_id": "asset_no",
                "site": "site",
                "status": "status",
                "priority": "priority",
                "work_type": "work_type",
                "completed_at": "completed_at",
                "tyre_serial": "tyre_serial",
                "labour_hours": "labour_hours",
                "labour_cost": "labour_cost",
                "parts_cost": "parts_cost",
                "tyre_cost": "tyre_cost",
                "total_cost": "total_cost",
            },
        ),
        Entity.ODOMETER_READINGS: EntityMapping(
            source="odometer_logs",
            fields={"vehicle_id": "asset_no", "read_at": "reading_date", "odometer_km": "odometer_km", "site": "site"},
        ),
        Entity.HOUR_READINGS: EntityMapping(
            source="engine_hours_logs",
            fields={"vehicle_id": "asset_no", "read_at": "reading_date", "hours": "engine_hours", "site": "site"},
        ),
        Entity.SITES: EntityMapping(
            source="sites",
            fields={"id": "name", "name": "name", "region": "region", "city": "city", "active": "active"},
        ),
    },
    units=Units(),
    status_values={"active": "fitted", "removed": "removed", "scrapped": "scrapped"},
    position_aliases=TYREPULSE_POSITION_ALIASES,
)

PRESETS: dict[str, TyreMapping] = {"tyrepulse": TYREPULSE_PRESET}


def _table_key(name: str) -> str:
    return name.split(".")[-1].split("/")[-1].lower()


def fit_to_schema(mapping: TyreMapping, tables: Iterable[SourceTable]) -> TyreMapping:
    """Keep only the entities whose table exists and the columns that exist in it."""
    by_name = {_table_key(table.name): table for table in tables}
    entities: dict[Entity, EntityMapping] = {}
    for entity, entity_mapping in mapping.entities.items():
        table = by_name.get(_table_key(entity_mapping.source))
        if table is None:
            continue
        columns = {column.name for column in table.columns}
        expand = entity_mapping.expand if entity_mapping.expand in columns else None
        fields = {name: column for name, column in entity_mapping.fields.items()
                  if column in columns or (expand and column.startswith(expand + "."))}
        window = entity_mapping.window_column in columns
        entities[entity] = EntityMapping(
            source=table.name, fields=fields, expand=expand,
            window_days=entity_mapping.window_days if window else None,
            window_column=entity_mapping.window_column if window else None,
        )
    return mapping.model_copy(update={"entities": entities})


_TABLE_WORDS: dict[Entity, tuple[str, ...]] = {
    Entity.VEHICLES: ("vehicle", "fleet", "asset", "truck", "unit"),
    Entity.TYRES: ("tyre", "tire"),
    Entity.INSPECTIONS: ("inspection", "service_event", "tread", "check", "survey"),
    Entity.PRESSURE_READINGS: ("tpms", "pressure"),
    Entity.REMOVALS: ("removal", "scrap", "dismount"),
    Entity.COSTS: ("cost", "expense", "invoice", "purchase"),
    Entity.SUPPLIERS: ("supplier", "vendor"),
}

_FIELD_WORDS: dict[str, tuple[str, ...]] = {
    "id": ("id", "asset_no", "code", "uuid"),
    "label": ("registration", "plate", "name", "label"),
    "fleet_number": ("fleet_number", "fleet_no", "unit_no"),
    "vehicle_type": ("vehicle_type", "type", "category"),
    "site": ("site", "depot", "branch", "location"),
    "odometer_km": ("current_km", "odometer", "mileage", "km"),
    "hours": ("hours", "hour_meter", "engine_hours"),
    "status": ("status", "state"),
    "serial": ("serial_no", "serial_number", "serial", "tyre_serial"),
    "brand": ("brand", "make", "manufacturer"),
    "pattern": ("pattern", "model", "design"),
    "size": ("size", "tyre_size", "tire_size", "dimension"),
    "dot": ("dot", "dot_code", "manufacture"),
    "vehicle_id": ("asset_no", "vehicle_id", "vehicle", "asset_id", "truck"),
    "position": ("position", "tyre_position", "wheel_position", "pos"),
    "fitted_at": ("fitment_date", "fitted_at", "fitted_date", "issue_date", "install"),
    "fitted_km": ("km_at_fitment", "fitted_km", "fitment_km", "odometer_at_fitment"),
    "fitted_hours": ("hrs_at_fitment", "hours_at_fitment"),
    "removed_at": ("removal_date", "removed_at", "removed_date"),
    "removed_km": ("km_at_removal", "removed_km", "removal_km"),
    "removed_hours": ("hrs_at_removal", "hours_at_removal"),
    "removal_reason": ("removal_reason", "reason_for_removal", "scrap_reason", "reason"),
    "total_km": ("total_km", "km_run", "distance"),
    "total_hours": ("total_hrs", "total_hours"),
    "purchase_cost": ("cost_per_tyre", "purchase_cost", "price", "cost", "unit_cost"),
    "supplier": ("supplier", "vendor"),
    "original_tread": ("original_tread", "new_tread", "otd", "initial_tread"),
    "tyre_id": ("tyre_id", "tire_id", "tyre_record_id"),
    "tyre_serial": ("tyre_serial", "serial_no", "serial_number", "serial"),
    "inspected_at": ("inspection_date", "inspected_at", "event_date", "completed_date", "date", "created_at"),
    "tread_depth": ("tread_depth", "tread", "rtd", "depth"),
    "pressure": ("pressure", "pressure_reading", "psi", "inflation"),
    "target_pressure": ("target_pressure", "recommended_pressure", "cold_pressure"),
    "recorded_at": ("recorded_at", "reading_date", "measured_at", "created_at"),
    "reason": ("reason", "removal_reason", "scrap_reason"),
    "date": ("date", "cost_date", "invoice_date", "event_date", "created_at"),
    "amount": ("amount", "cost", "total", "value"),
    "category": ("category", "cost_type", "event_type", "type"),
    "name": ("supplier_name", "name", "vendor_name"),
}


def _best_column(field: str, columns: list[str], taken: set[str]) -> str | None:
    words = _FIELD_WORDS.get(field, (field,))
    lowered = {column.lower(): column for column in columns if column not in taken}
    for word in words:
        if word in lowered:
            return lowered[word]
    for word in words:
        if len(word) < 4:
            continue
        for low, column in lowered.items():
            if word in low:
                return column
    return None


def suggest_mapping(tables: list[SourceTable]) -> TyreMapping:
    """The TyrePulse preset when its tables are present; otherwise a best guess."""
    names = {_table_key(table.name) for table in tables}
    if {"tyre_records", "vehicle_fleet"} <= names:
        return fit_to_schema(TYREPULSE_PRESET, tables)
    entities: dict[Entity, EntityMapping] = {}
    used_tables: set[str] = set()
    for entity in (Entity.TYRES, Entity.VEHICLES, Entity.INSPECTIONS, Entity.PRESSURE_READINGS,
                   Entity.REMOVALS, Entity.COSTS, Entity.SUPPLIERS):
        candidates = [
            table for table in tables
            if any(word in _table_key(table.name) for word in _TABLE_WORDS[entity])
            and (entity == Entity.TYRES or table.name not in used_tables)
        ]
        if not candidates:
            continue
        table = max(candidates, key=lambda item: len(item.columns))
        columns = [column.name for column in table.columns]
        taken: set[str] = set()
        fields: dict[str, str] = {}
        for spec in ENTITY_FIELDS[entity]:
            column = _best_column(spec.name, columns, taken)
            if column is not None:
                fields[spec.name] = column
                if spec.name not in {"tyre_serial", "vehicle_id"}:
                    taken.add(column)
        entities[entity] = EntityMapping(source=table.name, fields=fields)
        if entity != Entity.TYRES:
            used_tables.add(table.name)
    return TyreMapping(entities=entities)


def source_column(entity_mapping: EntityMapping, column: str) -> str:
    """The column a field is read from: an entry key of an expanded column reads that column."""
    if entity_mapping.expand and column.startswith(entity_mapping.expand + "."):
        return entity_mapping.expand
    return column


def read_key(entity_mapping: EntityMapping) -> tuple[str, str | None, int | None]:
    """Entities that share a table and a window are read together."""
    return entity_mapping.source, entity_mapping.window_column, entity_mapping.window_days


def mapped_columns(mapping: TyreMapping) -> dict[tuple[str, str | None, int | None], list[str]]:
    """(table, window column, window days) -> the columns any entity reads from it (what
    sync selects)."""
    columns: dict[tuple[str, str | None, int | None], set[str]] = defaultdict(set)
    for entity_mapping in mapping.entities.values():
        columns[read_key(entity_mapping)].update(
            source_column(entity_mapping, column) for column in entity_mapping.fields.values()
        )
    return {key: sorted(values) for key, values in columns.items()}
