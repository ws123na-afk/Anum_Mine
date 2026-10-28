"""Fleet operations answers from the synced snapshot (docs/tyre-planning.md#fleet-operations-data).

Pure functions over a :class:`TyreDataset`: which vehicles were inspected and when, a
vehicle's tyres with their latest readings, inspection schedules, workshop checklists, work
orders and their costs. The dashboard, the HTTP API and the assistant tools all call these,
so the numbers agree. Nothing here reads the source; ``live.py`` does the bounded reads of
the tables a sync never copies.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from .analytics import TyreForecast
from .model import (
    Checklist,
    Inspection,
    InspectionSchedule,
    InspectionVisit,
    Tyre,
    TyreDataset,
    TyreStatus,
    Vehicle,
    WorkOrder,
    normalise_position,
)

CLOSED_WORDS = ("complete", "closed", "close", "done", "cancel", "finished", "resolved")


def _key(value: str | None) -> str:
    return (value or "").strip().lower()


def _in(value: date | None, date_from: date | None, date_to: date | None) -> bool:
    if value is None:
        return date_from is None and date_to is None
    return (date_from is None or value >= date_from) and (date_to is None or value <= date_to)


def _site_match(value: str | None, site: str | None) -> bool:
    return not site or _key(site) in _key(value)


def is_open(order: WorkOrder) -> bool:
    status = _key(order.status)
    return order.completed_at is None and not any(word in status for word in CLOSED_WORDS)


def schedule_state(schedule: InspectionSchedule, as_of: date) -> str:
    """completed, overdue or upcoming."""
    status = _key(schedule.status)
    if schedule.completed_inspection_id or any(word in status for word in CLOSED_WORDS):
        return "completed"
    return "overdue" if schedule.scheduled_for < as_of else "upcoming"


def failed(checklist: Checklist) -> bool:
    status = _key(checklist.status) + " " + _key(checklist.approval_status)
    return checklist.passed is False or "fail" in status or "reject" in status


def order_cost(order: WorkOrder) -> float | None:
    if order.total_cost is not None:
        return order.total_cost
    parts = [value for value in (order.labour_cost, order.parts_cost, order.tyre_cost) if value is not None]
    return sum(parts) if parts else None


# --- finding a vehicle --------------------------------------------------------------------


def find_vehicles(dataset: TyreDataset, text: str | None) -> list[Vehicle]:
    """Vehicles whose id, registration or fleet number is ``text`` (exact first, then contains)."""
    needle = _key(text)
    if not needle:
        return []
    vehicles = list(dataset.vehicles)
    known = {_key(vehicle.id) for vehicle in vehicles}
    # Vehicles named only in tyres, inspections or work orders still count.
    for vehicle_id in [t.vehicle_id for t in dataset.tyres] + [v.vehicle_id for v in dataset.inspection_visits] \
            + [w.vehicle_id for w in dataset.work_orders]:
        if vehicle_id and _key(vehicle_id) not in known:
            known.add(_key(vehicle_id))
            vehicles.append(Vehicle(id=vehicle_id))
    exact = [v for v in vehicles if needle in {_key(v.id), _key(v.label), _key(v.fleet_number)}]
    if exact:
        return exact
    return [v for v in vehicles if any(needle in _key(value) for value in (v.id, v.label, v.fleet_number))]


def vehicle_label(vehicle: Vehicle | None, vehicle_id: str | None) -> str:
    if vehicle is None:
        return vehicle_id or "unassigned"
    extra = vehicle.label or vehicle.fleet_number
    return f"{vehicle.id} ({extra})" if extra and extra != vehicle.id else vehicle.id


# --- the dashboard's operations counts ------------------------------------------------------


class OperationsSummary(BaseModel):
    as_of: date
    vehicles: int
    vehicles_inspected_last_30_days: int
    vehicles_inspected_last_90_days: int
    inspections_last_30_days: int
    inspections_total: int
    last_inspection: date | None
    # The time of the last inspection (UTC), only when every inspection of that day stores
    # one; None when the source keeps dates only. Never estimated.
    last_inspection_at: datetime | None = None
    tyre_readings: int
    readings_on_tyre_records: int
    schedules_upcoming_14_days: int
    schedules_overdue: int
    checklists_last_30_days: int
    checklists_failed_last_30_days: int
    checklists_total: int
    work_orders_open: int
    work_orders_opened_last_30_days: int
    work_orders_synced: int
    work_order_cost_last_30_days: float | None
    work_order_window_days: int | None
    odometer_readings: int
    hour_readings: int
    sites: int
    currency: str


def _visits(dataset: TyreDataset) -> list[InspectionVisit]:
    """Inspection visits; for a source without a visits table, one per vehicle and day of readings."""
    if dataset.inspection_visits:
        return dataset.inspection_visits
    seen: dict[tuple[str, date], InspectionVisit] = {}
    timed: dict[tuple[str, date], list[datetime | None]] = defaultdict(list)
    for item in dataset.inspections:
        if not item.vehicle_id:
            continue
        key = (_key(item.vehicle_id), item.inspected_at)
        timed[key].append(item.inspected_time)
        if key not in seen:
            seen[key] = InspectionVisit(
                id=f"{item.vehicle_id}@{item.inspected_at.isoformat()}", vehicle_id=item.vehicle_id,
                inspected_at=item.inspected_at, odometer_km=item.odometer_km, hours=item.hours)
    for key, visit in seen.items():
        # A visit made of readings has a time only when every reading of it has one: the latest.
        times = timed[key]
        if all(times):
            visit.inspected_time = max(t for t in times if t)
    return list(seen.values())


def last_inspection_time(visits: list[InspectionVisit], day: date | None) -> datetime | None:
    """The latest time on the last inspection day, when every inspection of that day has one."""
    if day is None:
        return None
    times = [visit.inspected_time for visit in visits if visit.inspected_at == day]
    if not times or not all(times):
        return None
    return max(t for t in times if t)


def operations_summary(dataset: TyreDataset, as_of: date, work_order_window_days: int | None = None) -> OperationsSummary:
    visits = _visits(dataset)
    last30, last90 = as_of - timedelta(days=30), as_of - timedelta(days=90)
    recent_checklists = [c for c in dataset.checklists if c.submitted_at >= last30]
    recent_orders = [o for o in dataset.work_orders if o.opened_at >= last30]
    costs = [cost for cost in (order_cost(o) for o in recent_orders) if cost is not None]
    vehicle_ids = {_key(v.id) for v in dataset.vehicles} or {_key(t.vehicle_id) for t in dataset.tyres if t.vehicle_id}
    last_day = max((v.inspected_at for v in visits), default=None)
    return OperationsSummary(
        as_of=as_of,
        vehicles=len(vehicle_ids),
        vehicles_inspected_last_30_days=len({_key(v.vehicle_id) for v in visits if v.inspected_at >= last30}),
        vehicles_inspected_last_90_days=len({_key(v.vehicle_id) for v in visits if v.inspected_at >= last90}),
        inspections_last_30_days=sum(1 for v in visits if v.inspected_at >= last30),
        inspections_total=len(visits),
        last_inspection=last_day,
        last_inspection_at=last_inspection_time(visits, last_day),
        tyre_readings=len(dataset.inspections),
        readings_on_tyre_records=sum(1 for item in dataset.inspections if item.tyre_id),
        schedules_upcoming_14_days=sum(
            1 for s in dataset.inspection_schedules
            if schedule_state(s, as_of) == "upcoming" and s.scheduled_for <= as_of + timedelta(days=14)),
        schedules_overdue=sum(1 for s in dataset.inspection_schedules if schedule_state(s, as_of) == "overdue"),
        checklists_last_30_days=len(recent_checklists),
        checklists_failed_last_30_days=sum(1 for c in recent_checklists if failed(c)),
        checklists_total=len(dataset.checklists),
        work_orders_open=sum(1 for o in dataset.work_orders if is_open(o)),
        work_orders_opened_last_30_days=len(recent_orders),
        work_orders_synced=len(dataset.work_orders),
        work_order_cost_last_30_days=round(sum(costs), 2) if costs else None,
        work_order_window_days=work_order_window_days,
        odometer_readings=len(dataset.odometer_readings),
        hour_readings=len(dataset.hour_readings),
        sites=len(dataset.sites),
        currency=dataset.currency,
    )


# --- per vehicle ----------------------------------------------------------------------------


class VehicleRow(BaseModel):
    vehicle_id: str
    label: str
    site: str | None = None
    vehicle_type: str | None = None
    tyres_fitted: int = 0
    last_inspected: date | None = None
    inspections_last_90_days: int = 0
    low_tread_tyres: int = 0
    tyres_due_90_days: int = 0
    open_work_orders: int = 0
    odometer_km: float | None = None


def vehicle_rows(dataset: TyreDataset, forecasts: list[TyreForecast], as_of: date, min_tread_mm: float,
                 *, site: str | None = None, inspected: str | None = None) -> list[VehicleRow]:
    """One row per vehicle, the least recently inspected first. ``inspected``: "yes" (in the
    last 90 days), "no", or None for all."""
    vehicles = {_key(v.id): v for v in dataset.vehicles}
    rows: dict[str, VehicleRow] = {}

    def row(vehicle_id: str) -> VehicleRow:
        key = _key(vehicle_id)
        if key not in rows:
            vehicle = vehicles.get(key)
            rows[key] = VehicleRow(
                vehicle_id=vehicle.id if vehicle else vehicle_id, label=vehicle_label(vehicle, vehicle_id),
                site=vehicle.site if vehicle else None, vehicle_type=vehicle.vehicle_type if vehicle else None,
                odometer_km=vehicle.odometer_km if vehicle else None)
        return rows[key]

    for vehicle in dataset.vehicles:
        row(vehicle.id)
    for tyre in dataset.tyres:
        if tyre.vehicle_id and tyre.status == TyreStatus.FITTED:
            row(tyre.vehicle_id).tyres_fitted += 1
    last90 = as_of - timedelta(days=90)
    for visit in _visits(dataset):
        item = row(visit.vehicle_id)
        if item.last_inspected is None or visit.inspected_at > item.last_inspected:
            item.last_inspected = visit.inspected_at
        if visit.inspected_at >= last90:
            item.inspections_last_90_days += 1
        if item.site is None:
            item.site = visit.site
    for forecast in forecasts:
        if not forecast.vehicle_id:
            continue
        item = row(forecast.vehicle_id)
        if forecast.current_tread_mm is not None and forecast.current_tread_mm <= min_tread_mm:
            item.low_tread_tyres += 1
        if forecast.projected_removal and forecast.projected_removal <= as_of + timedelta(days=90):
            item.tyres_due_90_days += 1
    for order in dataset.work_orders:
        if order.vehicle_id and is_open(order):
            row(order.vehicle_id).open_work_orders += 1
    found = [r for r in rows.values() if _site_match(r.site, site)]
    if inspected == "yes":
        found = [r for r in found if r.last_inspected and r.last_inspected >= last90]
    elif inspected == "no":
        found = [r for r in found if not r.last_inspected or r.last_inspected < last90]
    found.sort(key=lambda r: (r.last_inspected or date.min, r.vehicle_id))
    return found


class PositionReading(BaseModel):
    position: str | None
    inspected_at: date
    tyre_id: str | None = None
    serial: str | None = None
    tread_mm: float | None = None
    pressure_psi: float | None = None


class TyreLine(BaseModel):
    tyre_id: str
    serial: str | None = None
    brand: str | None = None
    size: str | None = None
    position: str | None = None
    fitted_at: date | None = None
    current_tread_mm: float | None = None
    projected_removal: date | None = None


class VehicleDetail(BaseModel):
    vehicle_id: str
    label: str
    site: str | None = None
    vehicle_type: str | None = None
    status: str | None = None
    odometer_km: float | None = None
    hours: float | None = None
    last_odometer_reading: date | None = None
    tyres: list[TyreLine] = Field(default_factory=list)
    last_inspection: InspectionVisit | None = None
    inspections_last_90_days: int = 0
    latest_readings: list[PositionReading] = Field(default_factory=list)
    schedules: list[InspectionSchedule] = Field(default_factory=list)
    open_work_orders: list[WorkOrder] = Field(default_factory=list)
    recent_checklists: list[Checklist] = Field(default_factory=list)


def vehicle_detail(dataset: TyreDataset, vehicle: Vehicle, forecasts: list[TyreForecast], as_of: date) -> VehicleDetail:
    key = _key(vehicle.id)
    by_tyre = {item.tyre_id: item for item in forecasts}
    tyres: list[Tyre] = [t for t in dataset.tyres if _key(t.vehicle_id) == key and t.status == TyreStatus.FITTED]
    lines = [
        TyreLine(tyre_id=t.id, serial=t.serial, brand=t.brand, size=t.size, position=t.position, fitted_at=t.fitted_at,
                 current_tread_mm=by_tyre[t.id].current_tread_mm if t.id in by_tyre else None,
                 projected_removal=by_tyre[t.id].projected_removal if t.id in by_tyre else None)
        for t in sorted(tyres, key=lambda t: normalise_position(t.position))
    ]
    visits = sorted((v for v in _visits(dataset) if _key(v.vehicle_id) == key), key=lambda v: v.inspected_at)
    readings: list[Inspection] = [i for i in dataset.inspections if _key(i.vehicle_id) == key]
    latest_day = max((i.inspected_at for i in readings), default=None)
    serials = {t.id: t.serial for t in dataset.tyres}
    latest = [
        PositionReading(position=i.position, inspected_at=i.inspected_at, tyre_id=i.tyre_id,
                        serial=serials.get(i.tyre_id or ""), tread_mm=i.tread_mm, pressure_psi=i.pressure_psi)
        for i in sorted(readings, key=lambda i: normalise_position(i.position)) if i.inspected_at == latest_day
    ]
    meters = [r for r in dataset.odometer_readings if _key(r.vehicle_id) == key]
    last_meter = max(meters, key=lambda r: r.read_at, default=None)
    return VehicleDetail(
        vehicle_id=vehicle.id, label=vehicle_label(vehicle, vehicle.id), site=vehicle.site,
        vehicle_type=vehicle.vehicle_type, status=vehicle.status,
        odometer_km=last_meter.odometer_km if last_meter and (vehicle.odometer_km or 0) < last_meter.odometer_km
        else vehicle.odometer_km,
        hours=vehicle.hours, last_odometer_reading=last_meter.read_at if last_meter else None,
        tyres=lines, last_inspection=visits[-1] if visits else None,
        inspections_last_90_days=sum(1 for v in visits if v.inspected_at >= as_of - timedelta(days=90)),
        latest_readings=latest,
        schedules=sorted((s for s in dataset.inspection_schedules
                          if _key(s.vehicle_id) == key and schedule_state(s, as_of) != "completed"),
                         key=lambda s: s.scheduled_for)[:10],
        open_work_orders=sorted((o for o in dataset.work_orders if _key(o.vehicle_id) == key and is_open(o)),
                                key=lambda o: o.opened_at, reverse=True)[:20],
        recent_checklists=sorted((c for c in dataset.checklists if _key(c.vehicle_id) == key),
                                 key=lambda c: c.submitted_at, reverse=True)[:10],
    )


# --- lists with filters ---------------------------------------------------------------------


class InspectionLine(BaseModel):
    id: str
    vehicle_id: str
    inspected_at: date
    site: str | None = None
    status: str | None = None
    inspection_type: str | None = None
    severity: str | None = None
    positions_read: int = 0
    lowest_pressure_psi: float | None = None
    lowest_tread_mm: float | None = None


class InspectionList(BaseModel):
    total: int
    vehicles: int
    by_site: dict[str, int]
    by_status: dict[str, int]
    items: list[InspectionLine]
    vehicles_listed: list[str]


def inspections_list(dataset: TyreDataset, *, date_from: date | None = None, date_to: date | None = None,
                     site: str | None = None, vehicle_ids: set[str] | None = None, status: str | None = None,
                     limit: int = 50) -> InspectionList:
    readings: dict[tuple[str, date], list[Inspection]] = defaultdict(list)
    for item in dataset.inspections:
        if item.vehicle_id:
            readings[(_key(item.vehicle_id), item.inspected_at)].append(item)
    picked = [
        v for v in _visits(dataset)
        if _in(v.inspected_at, date_from, date_to) and _site_match(v.site, site)
        and (vehicle_ids is None or _key(v.vehicle_id) in vehicle_ids)
        and (not status or _key(status) in _key(v.status))
    ]
    picked.sort(key=lambda v: (v.inspected_at, v.vehicle_id), reverse=True)
    lines = []
    for visit in picked[:limit]:
        found = readings.get((_key(visit.vehicle_id), visit.inspected_at), [])
        pressures = [i.pressure_psi for i in found if i.pressure_psi is not None]
        treads = [i.tread_mm for i in found if i.tread_mm is not None]
        lines.append(InspectionLine(
            id=visit.id, vehicle_id=visit.vehicle_id, inspected_at=visit.inspected_at, site=visit.site,
            status=visit.status, inspection_type=visit.inspection_type, severity=visit.severity,
            positions_read=len(found), lowest_pressure_psi=min(pressures) if pressures else None,
            lowest_tread_mm=min(treads) if treads else None))
    vehicles = sorted({v.vehicle_id for v in picked}, key=str.lower)
    return InspectionList(
        total=len(picked), vehicles=len({_key(v) for v in vehicles}),
        by_site=dict(Counter(v.site or "unknown" for v in picked).most_common()),
        by_status=dict(Counter(v.status or "unknown" for v in picked).most_common()),
        items=lines, vehicles_listed=vehicles[:200],
    )


def schedules_list(dataset: TyreDataset, as_of: date, *, state: str | None = None, site: str | None = None,
                   vehicle_ids: set[str] | None = None, date_from: date | None = None,
                   date_to: date | None = None) -> tuple[list[InspectionSchedule], dict[str, int]]:
    picked = [
        s for s in dataset.inspection_schedules
        if _site_match(s.site, site) and (vehicle_ids is None or _key(s.vehicle_id) in vehicle_ids)
        and (date_from is None and date_to is None or _in(s.scheduled_for, date_from, date_to))
    ]
    counts = dict(Counter(schedule_state(s, as_of) for s in picked))
    if state:
        picked = [s for s in picked if schedule_state(s, as_of) == state]
    picked.sort(key=lambda s: s.scheduled_for)
    return picked, counts


def checklists_list(dataset: TyreDataset, *, date_from: date | None = None, date_to: date | None = None,
                    site: str | None = None, vehicle_ids: set[str] | None = None,
                    only_failed: bool = False, template: str | None = None) -> list[Checklist]:
    picked = [
        c for c in dataset.checklists
        if _in(c.submitted_at, date_from, date_to) and _site_match(c.site, site)
        and (vehicle_ids is None or _key(c.vehicle_id) in vehicle_ids)
        and (not only_failed or failed(c)) and (not template or _key(template) in _key(c.template))
    ]
    picked.sort(key=lambda c: c.submitted_at, reverse=True)
    return picked


def work_orders_list(dataset: TyreDataset, *, open_only: bool = False, date_from: date | None = None,
                     date_to: date | None = None, site: str | None = None, vehicle_ids: set[str] | None = None,
                     work_type: str | None = None) -> list[WorkOrder]:
    picked = [
        o for o in dataset.work_orders
        if (not open_only or is_open(o)) and _in(o.opened_at, date_from, date_to) and _site_match(o.site, site)
        and (vehicle_ids is None or _key(o.vehicle_id) in vehicle_ids)
        and (not work_type or _key(work_type) in _key(o.work_type))
    ]
    picked.sort(key=lambda o: (o.opened_at, o.id), reverse=True)
    return picked


class CostGroup(BaseModel):
    key: str
    work_orders: int
    labour_cost: float
    parts_cost: float
    tyre_cost: float
    total_cost: float


def work_order_costs(orders: list[WorkOrder], group_by: str) -> list[CostGroup]:
    """Totals per vehicle, site or work type, highest total first."""
    groups: dict[str, dict[str, Any]] = {}
    for order in orders:
        key = (order.vehicle_id if group_by == "vehicle" else order.site if group_by == "site"
               else order.work_type) or "unknown"
        group = groups.setdefault(key, {"work_orders": 0, "labour_cost": 0.0, "parts_cost": 0.0,
                                        "tyre_cost": 0.0, "total_cost": 0.0})
        group["work_orders"] += 1
        group["labour_cost"] += order.labour_cost or 0
        group["parts_cost"] += order.parts_cost or 0
        group["tyre_cost"] += order.tyre_cost or 0
        group["total_cost"] += order_cost(order) or 0
    rows = [CostGroup(key=key, **{name: round(value, 2) if isinstance(value, float) else value
                                  for name, value in values.items()}) for key, values in groups.items()]
    rows.sort(key=lambda row: (-row.total_cost, row.key))
    return rows
