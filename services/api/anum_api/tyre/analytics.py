"""Tyre health, wear-rate forecasts, purchase plans and alerts (docs/tyre-planning.md#method).

Everything here is a pure function of a :class:`TyreDataset`, the owner's
:class:`Assumptions` and an ``as_of`` date, so the same numbers come out of the web
dashboard, a scheduled report and an agent tool.

Wear rate (per tyre): a Theil-Sen line through the tyre's tread readings (the median of
the slopes between every pair of readings), which a single mis-typed reading cannot
drag. Distance is the x axis when readings carry odometer values and the tyre's
fitment odometer is known; otherwise calendar days. The 95% confidence interval of the
slope uses Sen's rank method (Kendall's S variance), and turns into the earliest and
latest likely removal dates. A tyre with one reading uses the wear from fitment to that
reading; a tyre with none uses the median rate of its brand and size (or size, or the
fleet), marked low confidence.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from .model import Assumptions, Cost, Inspection, PressureReading, Tyre, TyreDataset, TyreStatus, Vehicle

Z_95 = 1.96
MAX_POINTS = 60
# Forecasts beyond this many days are reported as "beyond horizon" rather than a date.
MAX_FORECAST_DAYS = 3650


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Basis(StrEnum):
    DISTANCE = "distance"
    TIME = "time"
    FITMENT = "fitment"
    GROUP = "group"
    NONE = "none"


class ForecastState(StrEnum):
    DUE = "due"  # at or below the minimum already
    PROJECTED = "projected"
    NO_WEAR = "no_wear"  # readings do not show wear
    NO_DATA = "no_data"


# --- robust regression ----------------------------------------------------------------


class Fit(BaseModel):
    slope: float
    intercept: float
    slope_low: float | None = None
    slope_high: float | None = None
    points: int


def theil_sen(xs: Sequence[float], ys: Sequence[float]) -> Fit | None:
    """Median pairwise slope with Sen's 95% interval; None without two distinct x."""
    n = len(xs)
    slopes = sorted(
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(n)
        for j in range(i + 1, n)
        if xs[j] != xs[i]
    )
    if not slopes:
        return None
    slope = statistics.median(slopes)
    intercept = statistics.median(y - slope * x for x, y in zip(xs, ys))
    low = high = None
    count = len(slopes)
    if n >= 3:
        variance = n * (n - 1) * (2 * n + 5) / 18
        c = Z_95 * math.sqrt(variance)
        lower_rank = max(0, math.floor((count - c) / 2))
        upper_rank = min(count - 1, math.ceil((count + c) / 2))
        low, high = slopes[lower_rank], slopes[upper_rank]
    return Fit(slope=slope, intercept=intercept, slope_low=low, slope_high=high, points=n)


# --- per-tyre forecast ----------------------------------------------------------------


class TyreForecast(BaseModel):
    tyre_id: str
    serial: str | None = None
    brand: str | None = None
    size: str | None = None
    vehicle_id: str | None = None
    vehicle_label: str | None = None
    position: str | None = None
    state: ForecastState
    basis: Basis
    confidence: Confidence
    readings: int = 0
    last_inspected_at: date | None = None
    current_tread_mm: float | None = None
    # Positive: mm lost per 1000 km (distance basis) and per 30 days (all bases).
    wear_mm_per_1000km: float | None = None
    wear_mm_per_30_days: float | None = None
    remaining_mm: float | None = None
    projected_removal: date | None = None
    earliest_removal: date | None = None
    latest_removal: date | None = None
    note: str = ""


def _latest_tread(points: list[Inspection]) -> Inspection | None:
    with_tread = [item for item in points if item.tread_mm is not None]
    return max(with_tread, key=lambda item: item.inspected_at) if with_tread else None


def _confidence(fit: Fit | None, slope: float) -> Confidence:
    if fit is None or fit.slope_low is None or fit.slope_high is None or slope == 0:
        return Confidence.LOW
    half_width = abs(fit.slope_high - fit.slope_low) / 2 / abs(slope)
    if fit.points >= 5 and half_width <= 0.25:
        return Confidence.HIGH
    if fit.points >= 3 and half_width <= 0.5:
        return Confidence.MEDIUM
    return Confidence.LOW


def _date_after(start: date, days: float) -> date | None:
    if not math.isfinite(days) or days > MAX_FORECAST_DAYS:
        return None
    return start + timedelta(days=max(0.0, days))


class _Context:
    def __init__(self, dataset: TyreDataset, assumptions: Assumptions, as_of: date) -> None:
        self.dataset = dataset
        self.assumptions = assumptions
        self.as_of = as_of
        self.vehicles: dict[str, Vehicle] = {vehicle.id: vehicle for vehicle in dataset.vehicles}
        self.inspections: dict[str, list[Inspection]] = defaultdict(list)
        for inspection in dataset.inspections:
            self.inspections[inspection.tyre_id].append(inspection)
        for items in self.inspections.values():
            items.sort(key=lambda item: item.inspected_at)
        self.vehicle_km_per_day = self._vehicle_usage()

    def _vehicle_usage(self) -> dict[str, float]:
        """km/day per vehicle from odometer readings over time (Theil-Sen)."""
        readings: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for inspection in self.dataset.inspections:
            if inspection.vehicle_id and inspection.odometer_km:
                readings[inspection.vehicle_id].append(
                    (float(inspection.inspected_at.toordinal()), inspection.odometer_km)
                )
        for tyre in self.dataset.tyres:
            if tyre.vehicle_id and tyre.fitted_at and tyre.fitted_km:
                readings[tyre.vehicle_id].append((float(tyre.fitted_at.toordinal()), tyre.fitted_km))
            if tyre.vehicle_id and tyre.removed_at and tyre.removed_km:
                readings[tyre.vehicle_id].append((float(tyre.removed_at.toordinal()), tyre.removed_km))
        usage: dict[str, float] = {}
        for vehicle_id, points in readings.items():
            unique = sorted(set(points))[-MAX_POINTS:]
            fit = theil_sen([x for x, _ in unique], [y for _, y in unique])
            if fit is not None and 0 < fit.slope < 3000:
                usage[vehicle_id] = fit.slope
        return usage

    def new_tread(self, tyre: Tyre) -> float:
        return tyre.original_tread_mm or self.assumptions.default_new_tread_mm


def _forecast_one(tyre: Tyre, ctx: _Context) -> TyreForecast:
    minimum = ctx.assumptions.min_tread_mm
    vehicle = ctx.vehicles.get(tyre.vehicle_id or "")
    base = TyreForecast(
        tyre_id=tyre.id,
        serial=tyre.serial,
        brand=tyre.brand,
        size=tyre.size,
        vehicle_id=tyre.vehicle_id,
        vehicle_label=(vehicle.label or vehicle.fleet_number) if vehicle else None,
        position=tyre.position,
        state=ForecastState.NO_DATA,
        basis=Basis.NONE,
        confidence=Confidence.LOW,
    )
    points = [item for item in ctx.inspections.get(tyre.id, []) if item.tread_mm is not None][-MAX_POINTS:]
    latest = _latest_tread(points)
    base.readings = len(points)
    if latest is not None:
        base.last_inspected_at = latest.inspected_at
        base.current_tread_mm = latest.tread_mm

    # Distance basis: readings with odometer and a known fitment odometer.
    distance_points = [
        (item.odometer_km - tyre.fitted_km, item.tread_mm)
        for item in points
        if item.odometer_km is not None and tyre.fitted_km is not None and item.odometer_km >= tyre.fitted_km
    ]
    fit: Fit | None = None
    if len({x for x, _ in distance_points}) >= 2:
        fit = theil_sen([x for x, _ in distance_points], [y for _, y in distance_points])  # type: ignore[misc]
        if fit is not None:
            base.basis = Basis.DISTANCE
    if fit is None and len({item.inspected_at for item in points}) >= 2:
        origin = points[0].inspected_at.toordinal()
        fit = theil_sen(
            [float(item.inspected_at.toordinal() - origin) for item in points],
            [item.tread_mm for item in points],  # type: ignore[misc]
        )
        if fit is not None:
            base.basis = Basis.TIME
    if fit is None and latest is not None and tyre.fitted_at and latest.inspected_at > tyre.fitted_at:
        days = (latest.inspected_at - tyre.fitted_at).days
        worn = ctx.new_tread(tyre) - (latest.tread_mm or 0)
        fit = Fit(slope=-worn / days, intercept=ctx.new_tread(tyre), points=1)
        base.basis = Basis.FITMENT

    if latest is None:
        return base
    current = latest.tread_mm or 0.0
    if base.basis in (Basis.DISTANCE, Basis.TIME) and fit is not None:
        # The fitted line at the latest reading, never above what was last measured + 1 mm.
        if base.basis == Basis.DISTANCE:
            x_last = max(x for x, _ in distance_points)
        else:
            x_last = float(latest.inspected_at.toordinal() - points[0].inspected_at.toordinal())
        current = min(fit.intercept + fit.slope * x_last, current + 1.0)
    current = round(max(current, 0.0), 2)
    base.current_tread_mm = current
    base.remaining_mm = round(current - minimum, 2)

    if current <= minimum:
        base.state = ForecastState.DUE
        base.projected_removal = base.earliest_removal = base.latest_removal = latest.inspected_at
        base.confidence = Confidence.HIGH
        base.note = "At or below the minimum tread."
        if fit is not None and fit.slope < 0:
            _set_rates(base, fit, ctx, tyre)
        return base
    if fit is None:
        return base
    if fit.slope >= 0:
        base.state = ForecastState.NO_WEAR
        base.note = "The readings do not show wear yet."
        return base

    _set_rates(base, fit, ctx, tyre)
    per_day = _per_day(fit.slope, base.basis, ctx, tyre)
    if per_day is None:
        # Distance basis without a usage rate for the vehicle: fall back to calendar time.
        base.basis = Basis.TIME
        origin = points[0].inspected_at.toordinal()
        time_fit = theil_sen(
            [float(item.inspected_at.toordinal() - origin) for item in points],
            [item.tread_mm for item in points],  # type: ignore[misc]
        )
        if time_fit is None or time_fit.slope >= 0:
            base.state = ForecastState.NO_WEAR
            base.note = "No usage rate for the vehicle and no wear over time."
            return base
        fit, per_day = time_fit, -time_fit.slope
        _set_rates(base, fit, ctx, tyre)
    remaining = current - minimum
    base.state = ForecastState.PROJECTED
    base.confidence = _confidence(fit, fit.slope) if base.basis != Basis.FITMENT else Confidence.LOW
    base.projected_removal = _date_after(latest.inspected_at, remaining / per_day)
    fast, slow = _bounds(fit, base, ctx, tyre, per_day)
    base.earliest_removal = _date_after(latest.inspected_at, remaining / fast)
    base.latest_removal = _date_after(latest.inspected_at, remaining / slow) if slow > 0 else None
    if base.projected_removal is None:
        base.note = "More than ten years away at the current wear rate."
    return base


def _per_day(slope: float, basis: Basis, ctx: _Context, tyre: Tyre) -> float | None:
    """mm lost per day (positive)."""
    if basis == Basis.DISTANCE:
        usage = ctx.vehicle_km_per_day.get(tyre.vehicle_id or "")
        return -slope * usage if usage else None
    return -slope


def _bounds(fit: Fit, forecast: TyreForecast, ctx: _Context, tyre: Tyre, per_day: float) -> tuple[float, float]:
    """(fastest, slowest) mm/day: the slope interval, or +/-50% without one."""
    if fit.slope_low is None or fit.slope_high is None or forecast.confidence == Confidence.LOW and fit.points < 3:
        return per_day * 1.5, per_day / 1.5
    fast = _per_day(min(fit.slope_low, fit.slope), forecast.basis, ctx, tyre) or per_day * 1.5
    slow = _per_day(max(fit.slope_high, fit.slope), forecast.basis, ctx, tyre) or 0.0
    return max(fast, per_day), max(slow, 0.0)


def _set_rates(forecast: TyreForecast, fit: Fit, ctx: _Context, tyre: Tyre) -> None:
    if forecast.basis == Basis.DISTANCE:
        forecast.wear_mm_per_1000km = round(-fit.slope * 1000, 3)
        usage = ctx.vehicle_km_per_day.get(tyre.vehicle_id or "")
        if usage:
            forecast.wear_mm_per_30_days = round(-fit.slope * usage * 30, 3)
    else:
        forecast.wear_mm_per_30_days = round(-fit.slope * 30, 3)
        usage = ctx.vehicle_km_per_day.get(tyre.vehicle_id or "")
        if usage:
            forecast.wear_mm_per_1000km = round(-fit.slope / usage * 1000, 3)


def _group_fill(forecasts: list[TyreForecast], ctx: _Context) -> None:
    """Tyres without their own wear rate borrow their group's median (low confidence)."""
    rates: dict[tuple[str | None, str | None], list[float]] = defaultdict(list)
    for item in forecasts:
        if item.wear_mm_per_30_days and item.basis in (Basis.DISTANCE, Basis.TIME, Basis.FITMENT):
            rates[(item.size, item.brand)].append(item.wear_mm_per_30_days)
            rates[(item.size, None)].append(item.wear_mm_per_30_days)
            rates[(None, None)].append(item.wear_mm_per_30_days)
    for item in forecasts:
        if item.state not in (ForecastState.NO_DATA, ForecastState.NO_WEAR) or item.wear_mm_per_30_days:
            continue
        for key, label in (((item.size, item.brand), "brand and size"), ((item.size, None), "size"), ((None, None), "fleet")):
            sample = rates.get(key, [])
            if len(sample) >= 3:
                rate = statistics.median(sample)
                break
        else:
            continue
        tyre_reading = item.current_tread_mm
        start = item.last_inspected_at
        if tyre_reading is None:
            tyre = next((t for t in ctx.dataset.tyres if t.id == item.tyre_id), None)
            if tyre is None or tyre.fitted_at is None or tyre.status != TyreStatus.FITTED:
                continue
            start = tyre.fitted_at
            tyre_reading = ctx.new_tread(tyre)
        if start is None or rate <= 0:
            continue
        remaining = tyre_reading - ctx.assumptions.min_tread_mm
        per_day = rate / 30
        item.basis = Basis.GROUP
        item.confidence = Confidence.LOW
        item.wear_mm_per_30_days = round(rate, 3)
        item.remaining_mm = round(remaining, 2)
        if item.current_tread_mm is None:
            # Estimated from fitment: wear since then at the group rate.
            elapsed = (ctx.as_of - start).days
            estimate = max(0.0, tyre_reading - per_day * elapsed)
            item.current_tread_mm = round(estimate, 2)
            item.remaining_mm = round(estimate - ctx.assumptions.min_tread_mm, 2)
            remaining = item.remaining_mm
            start = ctx.as_of
        item.state = ForecastState.DUE if remaining <= 0 else ForecastState.PROJECTED
        item.projected_removal = _date_after(start, max(remaining, 0) / per_day)
        item.earliest_removal = _date_after(start, max(remaining, 0) / (per_day * 1.5))
        item.latest_removal = _date_after(start, max(remaining, 0) / (per_day / 1.5))
        item.note = f"No usable readings: uses the median wear of the {label} ({len(sample)} tyres)."


def forecast_tyres(dataset: TyreDataset, assumptions: Assumptions, as_of: date) -> list[TyreForecast]:
    """Forecasts for every fitted tyre, soonest removal first."""
    ctx = _Context(dataset, assumptions, as_of)
    fitted = [tyre for tyre in dataset.tyres if tyre.status == TyreStatus.FITTED]
    forecasts = [_forecast_one(tyre, ctx) for tyre in fitted]
    _group_fill(forecasts, ctx)
    forecasts.sort(key=lambda item: (item.projected_removal or date.max, item.tyre_id))
    return forecasts


# --- costs ------------------------------------------------------------------------------


def tyre_costs(dataset: TyreDataset) -> dict[str, float]:
    """Purchase cost plus every cost entry linked to the tyre."""
    totals: dict[str, float] = defaultdict(float)
    for tyre in dataset.tyres:
        if tyre.purchase_cost:
            totals[tyre.id] += tyre.purchase_cost
    for cost in dataset.costs:
        if cost.tyre_id:
            totals[cost.tyre_id] += cost.amount
    return totals


def tyre_distance(tyre: Tyre, vehicles: dict[str, Vehicle]) -> float | None:
    """km the tyre has run: total, removal - fitment, or the vehicle's odometer now - fitment."""
    if tyre.total_km and tyre.total_km > 0:
        return tyre.total_km
    if tyre.fitted_km is not None and tyre.removed_km is not None and tyre.removed_km > tyre.fitted_km:
        return tyre.removed_km - tyre.fitted_km
    vehicle = vehicles.get(tyre.vehicle_id or "")
    if (
        tyre.status == TyreStatus.FITTED
        and tyre.fitted_km is not None
        and vehicle is not None
        and vehicle.odometer_km is not None
        and vehicle.odometer_km > tyre.fitted_km
    ):
        return vehicle.odometer_km - tyre.fitted_km
    return None


def tyre_hours(tyre: Tyre, vehicles: dict[str, Vehicle]) -> float | None:
    if tyre.total_hours and tyre.total_hours > 0:
        return tyre.total_hours
    if tyre.fitted_hours is not None and tyre.removed_hours is not None and tyre.removed_hours > tyre.fitted_hours:
        return tyre.removed_hours - tyre.fitted_hours
    vehicle = vehicles.get(tyre.vehicle_id or "")
    if tyre.status == TyreStatus.FITTED and tyre.fitted_hours is not None and vehicle and vehicle.hours and vehicle.hours > tyre.fitted_hours:
        return vehicle.hours - tyre.fitted_hours
    return None


def _median(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.median(items) if items else None


# --- pressure -----------------------------------------------------------------------------


class PressureCheck(BaseModel):
    tyre_id: str | None
    vehicle_id: str | None
    position: str | None
    recorded_at: date
    pressure_psi: float
    target_psi: float | None
    # under, over, ok or no_target
    verdict: str
    deviation_pct: float | None = None


def pressure_checks(dataset: TyreDataset, assumptions: Assumptions) -> list[PressureCheck]:
    tolerance = assumptions.pressure_tolerance_pct / 100
    checks: list[PressureCheck] = []
    readings: list[tuple[str | None, str | None, str | None, date, float, float | None]] = [
        (item.tyre_id, item.vehicle_id, item.position, item.inspected_at, item.pressure_psi, item.target_psi)
        for item in dataset.inspections
        if item.pressure_psi is not None
    ]
    readings += [
        (item.tyre_id, item.vehicle_id, item.position, item.recorded_at, item.pressure_psi, item.target_psi)
        for item in dataset.pressure_readings
    ]
    for tyre_id, vehicle_id, position, recorded_at, pressure, target in readings:
        target = target or assumptions.default_target_pressure_psi
        if not target:
            checks.append(PressureCheck(tyre_id=tyre_id, vehicle_id=vehicle_id, position=position,
                                        recorded_at=recorded_at, pressure_psi=pressure, target_psi=None, verdict="no_target"))
            continue
        deviation = (pressure - target) / target
        verdict = "under" if deviation < -tolerance else "over" if deviation > tolerance else "ok"
        checks.append(
            PressureCheck(tyre_id=tyre_id, vehicle_id=vehicle_id, position=position, recorded_at=recorded_at,
                          pressure_psi=pressure, target_psi=target, verdict=verdict, deviation_pct=round(deviation * 100, 1))
        )
    return checks


def latest_pressure(checks: list[PressureCheck]) -> dict[str, PressureCheck]:
    """The latest check per tyre (or per vehicle and position when no tyre)."""
    latest: dict[str, PressureCheck] = {}
    for check in sorted(checks, key=lambda item: item.recorded_at):
        key = check.tyre_id or f"{check.vehicle_id}|{check.position}"
        latest[key] = check
    return latest


# --- fleet summary --------------------------------------------------------------------------


class FleetSummary(BaseModel):
    as_of: date
    currency: str
    vehicles: int
    tyres_fitted: int
    tyres_in_stock: int
    tyres_removed_last_30_days: int
    tyres_with_readings: int
    average_tread_mm: float | None
    below_minimum: int
    in_warning_band: int
    pressure_checked: int
    pressure_compliant_pct: float | None
    under_inflated: int
    over_inflated: int
    overdue_inspections: int
    due_next_30_days: int
    due_next_90_days: int
    cost_per_1000_km: float | None
    cost_per_hour: float | None
    min_tread_mm: float


def overdue_tyres(dataset: TyreDataset, assumptions: Assumptions, as_of: date) -> list[tuple[Tyre, date | None, int]]:
    """(tyre, last inspection, days since) for fitted tyres overdue for inspection."""
    last: dict[str, date] = {}
    for inspection in dataset.inspections:
        if inspection.tyre_id not in last or inspection.inspected_at > last[inspection.tyre_id]:
            last[inspection.tyre_id] = inspection.inspected_at
    found = []
    for tyre in dataset.tyres:
        if tyre.status != TyreStatus.FITTED:
            continue
        seen = last.get(tyre.id) or tyre.fitted_at
        if seen is None:
            continue
        days = (as_of - seen).days
        if days > assumptions.inspection_interval_days:
            found.append((tyre, last.get(tyre.id), days))
    return found


def fleet_summary(
    dataset: TyreDataset, assumptions: Assumptions, as_of: date, forecasts: list[TyreForecast] | None = None
) -> FleetSummary:
    forecasts = forecasts if forecasts is not None else forecast_tyres(dataset, assumptions, as_of)
    vehicles = {vehicle.id: vehicle for vehicle in dataset.vehicles}
    fitted = [tyre for tyre in dataset.tyres if tyre.status == TyreStatus.FITTED]
    treads = [item.current_tread_mm for item in forecasts if item.current_tread_mm is not None and item.readings]
    minimum = assumptions.min_tread_mm
    recent = as_of - timedelta(days=90)
    latest = latest_pressure([check for check in pressure_checks(dataset, assumptions) if check.recorded_at >= recent])
    judged = [check for check in latest.values() if check.verdict != "no_target"]
    costs = tyre_costs(dataset)
    cost_km = [(costs.get(tyre.id, 0.0), tyre_distance(tyre, vehicles)) for tyre in dataset.tyres]
    cost_km = [(cost, km) for cost, km in cost_km if km and cost]
    cost_hours = [(costs.get(tyre.id, 0.0), tyre_hours(tyre, vehicles)) for tyre in dataset.tyres]
    cost_hours = [(cost, hours) for cost, hours in cost_hours if hours and cost]
    removed_recently = sum(
        1 for removal in dataset.removals if as_of - timedelta(days=30) <= removal.removed_at <= as_of
    )
    return FleetSummary(
        as_of=as_of,
        currency=dataset.currency,
        vehicles=len(dataset.vehicles) or len({tyre.vehicle_id for tyre in fitted if tyre.vehicle_id}),
        tyres_fitted=len(fitted),
        tyres_in_stock=sum(tyre.status == TyreStatus.STOCK for tyre in dataset.tyres),
        tyres_removed_last_30_days=removed_recently,
        tyres_with_readings=len(treads),
        average_tread_mm=round(statistics.fmean(treads), 2) if treads else None,
        below_minimum=sum(value <= minimum for value in treads),
        in_warning_band=sum(minimum < value <= minimum + assumptions.warning_band_mm for value in treads),
        pressure_checked=len(judged),
        pressure_compliant_pct=round(100 * sum(c.verdict == "ok" for c in judged) / len(judged), 1) if judged else None,
        under_inflated=sum(c.verdict == "under" for c in judged),
        over_inflated=sum(c.verdict == "over" for c in judged),
        overdue_inspections=len(overdue_tyres(dataset, assumptions, as_of)),
        due_next_30_days=sum(1 for f in forecasts if f.projected_removal and f.projected_removal <= as_of + timedelta(days=30)),
        due_next_90_days=sum(1 for f in forecasts if f.projected_removal and f.projected_removal <= as_of + timedelta(days=90)),
        cost_per_1000_km=round(1000 * sum(c for c, _ in cost_km) / sum(k for _, k in cost_km), 2) if cost_km else None,
        cost_per_hour=round(sum(c for c, _ in cost_hours) / sum(h for _, h in cost_hours), 2) if cost_hours else None,
        min_tread_mm=minimum,
    )


# --- purchase plan ------------------------------------------------------------------------


class PlanRow(BaseModel):
    month: str  # YYYY-MM
    size: str
    brand: str
    due: int
    safety: int
    from_stock: int
    to_buy: int
    unit_price: float | None
    price_source: str  # assumption, data_brand_size, data_size, unknown
    estimated_cost: float | None
    low_confidence: int = 0


class PurchasePlan(BaseModel):
    as_of: date
    currency: str
    months: list[str]
    rows: list[PlanRow]
    total_tyres: int
    total_cost: float | None
    unpriced_rows: int
    skipped_no_size: int
    skipped_beyond_horizon: int
    assumptions: Assumptions


def _month(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    return date(value.year + month // 12, month % 12 + 1, 1)


def price_lookup(dataset: TyreDataset, assumptions: Assumptions):  # type: ignore[no-untyped-def]
    by_pair: dict[tuple[str, str], list[float]] = defaultdict(list)
    by_size: dict[str, list[float]] = defaultdict(list)
    for tyre in dataset.tyres:
        if tyre.purchase_cost and tyre.size:
            by_pair[(tyre.size.lower(), (tyre.brand or "").lower())].append(tyre.purchase_cost)
            by_size[tyre.size.lower()].append(tyre.purchase_cost)
    prices = {key.lower(): value for key, value in assumptions.unit_prices.items()}

    def lookup(size: str, brand: str) -> tuple[float | None, str]:
        pair = f"{size}|{brand}".lower()
        if pair in prices:
            return prices[pair], "assumption"
        if size.lower() in prices:
            return prices[size.lower()], "assumption"
        sample = by_pair.get((size.lower(), brand.lower()))
        if sample:
            return round(statistics.median(sample), 2), "data_brand_size"
        sample = by_size.get(size.lower())
        if sample:
            return round(statistics.median(sample), 2), "data_size"
        return None, "unknown"

    return lookup


def purchase_plan(
    dataset: TyreDataset,
    assumptions: Assumptions,
    as_of: date,
    forecasts: list[TyreForecast] | None = None,
    *,
    months: int | None = None,
) -> PurchasePlan:
    forecasts = forecasts if forecasts is not None else forecast_tyres(dataset, assumptions, as_of)
    horizon = months or assumptions.horizon_months
    first = date(as_of.year, as_of.month, 1)
    month_list = [_month(add_months(first, offset)) for offset in range(horizon)]
    end = add_months(first, horizon)
    due: dict[tuple[str, str, str], int] = defaultdict(int)
    low: dict[tuple[str, str, str], int] = defaultdict(int)
    skipped_size = skipped_horizon = 0
    for forecast in forecasts:
        when = forecast.projected_removal
        if forecast.state not in (ForecastState.DUE, ForecastState.PROJECTED) or when is None:
            continue
        if forecast.confidence == Confidence.LOW and not assumptions.include_low_confidence:
            continue
        if when >= end:
            skipped_horizon += 1
            continue
        if not forecast.size:
            skipped_size += 1
            continue
        month = _month(max(when, first))
        brand = assumptions.preferred_brands.get(forecast.size) or forecast.brand or "any brand"
        key = (month, forecast.size, brand)
        due[key] += 1
        if forecast.confidence == Confidence.LOW:
            low[key] += 1
    stock: dict[tuple[str, str], int] = defaultdict(int)
    if assumptions.subtract_stock:
        for tyre in dataset.tyres:
            if tyre.status == TyreStatus.STOCK and tyre.size:
                stock[(tyre.size.lower(), (tyre.brand or "any brand").lower())] += 1
    lookup = price_lookup(dataset, assumptions)
    rows: list[PlanRow] = []
    for (month, size, brand), count in sorted(due.items()):
        safety = math.ceil(count * assumptions.safety_stock_pct / 100)
        need = count + safety
        stock_key = (size.lower(), brand.lower())
        from_stock = min(stock.get(stock_key, 0), need)
        stock[stock_key] = stock.get(stock_key, 0) - from_stock
        to_buy = need - from_stock
        price, source = lookup(size, brand)
        rows.append(
            PlanRow(month=month, size=size, brand=brand, due=count, safety=safety, from_stock=from_stock,
                    to_buy=to_buy, unit_price=price, price_source=source,
                    estimated_cost=round(price * to_buy, 2) if price is not None else None,
                    low_confidence=low.get((month, size, brand), 0))
        )
    priced = [row.estimated_cost for row in rows if row.estimated_cost is not None]
    return PurchasePlan(
        as_of=as_of,
        currency=dataset.currency,
        months=month_list,
        rows=rows,
        total_tyres=sum(row.to_buy for row in rows),
        total_cost=round(sum(priced), 2) if priced else None,
        unpriced_rows=sum(row.estimated_cost is None and row.to_buy > 0 for row in rows),
        skipped_no_size=skipped_size,
        skipped_beyond_horizon=skipped_horizon,
        assumptions=assumptions,
    )


# --- breakdowns for reports ------------------------------------------------------------------


class BrandPerformance(BaseModel):
    brand: str
    size: str
    tyres: int
    removed: int
    median_wear_mm_per_1000km: float | None
    median_wear_mm_per_30_days: float | None
    projected_life_km: float | None
    median_km_at_removal: float | None
    cost_per_1000_km: float | None


def brand_performance(dataset: TyreDataset, assumptions: Assumptions, forecasts: list[TyreForecast]) -> list[BrandPerformance]:
    vehicles = {vehicle.id: vehicle for vehicle in dataset.vehicles}
    costs = tyre_costs(dataset)
    by_tyre = {item.tyre_id: item for item in forecasts}
    groups: dict[tuple[str, str], list[Tyre]] = defaultdict(list)
    for tyre in dataset.tyres:
        groups[(tyre.brand or "unknown", tyre.size or "unknown")].append(tyre)
    rows = []
    for (brand, size), tyres in groups.items():
        own = [by_tyre[t.id] for t in tyres if t.id in by_tyre and by_tyre[t.id].basis in (Basis.DISTANCE, Basis.TIME, Basis.FITMENT)]
        per_km = _median(item.wear_mm_per_1000km for item in own if item.wear_mm_per_1000km)
        per_30 = _median(item.wear_mm_per_30_days for item in own if item.wear_mm_per_30_days)
        usable = statistics.median([t.original_tread_mm or assumptions.default_new_tread_mm for t in tyres]) - assumptions.min_tread_mm
        removed = [t for t in tyres if t.status in (TyreStatus.REMOVED, TyreStatus.SCRAPPED)]
        km_removed = _median(km for km in (tyre_distance(t, vehicles) for t in removed) if km)
        pairs = [(costs.get(t.id, 0.0), tyre_distance(t, vehicles)) for t in tyres]
        pairs = [(c, k) for c, k in pairs if c and k]
        rows.append(
            BrandPerformance(
                brand=brand, size=size, tyres=len(tyres), removed=len(removed),
                median_wear_mm_per_1000km=round(per_km, 3) if per_km else None,
                median_wear_mm_per_30_days=round(per_30, 3) if per_30 else None,
                projected_life_km=round(usable / per_km * 1000) if per_km else None,
                median_km_at_removal=round(km_removed) if km_removed else None,
                cost_per_1000_km=round(1000 * sum(c for c, _ in pairs) / sum(k for _, k in pairs), 2) if pairs else None,
            )
        )
    rows.sort(key=lambda row: (row.cost_per_1000_km is None, row.cost_per_1000_km or 0, -row.tyres))
    return rows


class ScrapReason(BaseModel):
    reason: str
    removals: int
    share_pct: float
    median_km: float | None
    median_tread_mm: float | None


def scrap_reasons(dataset: TyreDataset, start: date | None = None, end: date | None = None) -> list[ScrapReason]:
    tyres = {tyre.id: tyre for tyre in dataset.tyres}
    vehicles = {vehicle.id: vehicle for vehicle in dataset.vehicles}
    removals = [r for r in dataset.removals if (start is None or r.removed_at >= start) and (end is None or r.removed_at <= end)]
    groups: dict[str, list] = defaultdict(list)
    for removal in removals:
        groups[(removal.reason or "not recorded").strip().lower()[:80]].append(removal)
    total = len(removals)
    rows = []
    for reason, items in groups.items():
        km = [tyre_distance(tyres[r.tyre_id], vehicles) for r in items if r.tyre_id in tyres]
        rows.append(
            ScrapReason(
                reason=reason,
                removals=len(items),
                share_pct=round(100 * len(items) / total, 1) if total else 0.0,
                median_km=_median(k for k in km if k),
                median_tread_mm=_median(r.tread_mm for r in items if r.tread_mm is not None),
            )
        )
    rows.sort(key=lambda row: -row.removals)
    return rows


class CostLine(BaseModel):
    group: str
    tyres: int
    cost: float
    km: float | None
    hours: float | None
    cost_per_1000_km: float | None
    cost_per_hour: float | None


def cost_per_distance(dataset: TyreDataset, by: str = "brand") -> list[CostLine]:
    """Cost per 1000 km and per hour grouped by brand, size or vehicle."""
    vehicles = {vehicle.id: vehicle for vehicle in dataset.vehicles}
    costs = tyre_costs(dataset)
    groups: dict[str, list[Tyre]] = defaultdict(list)
    for tyre in dataset.tyres:
        key = {"brand": tyre.brand, "size": tyre.size, "vehicle": tyre.vehicle_id}.get(by) or "unknown"
        groups[key].append(tyre)
    lines = []
    for key, tyres in groups.items():
        cost = sum(costs.get(t.id, 0.0) for t in tyres)
        km_pairs = [(costs.get(t.id, 0.0), tyre_distance(t, vehicles)) for t in tyres]
        km_pairs = [(c, k) for c, k in km_pairs if c and k]
        hour_pairs = [(costs.get(t.id, 0.0), tyre_hours(t, vehicles)) for t in tyres]
        hour_pairs = [(c, h) for c, h in hour_pairs if c and h]
        km = sum(k for _, k in km_pairs) or None
        hours = sum(h for _, h in hour_pairs) or None
        lines.append(
            CostLine(
                group=key, tyres=len(tyres), cost=round(cost, 2), km=round(km) if km else None,
                hours=round(hours, 1) if hours else None,
                cost_per_1000_km=round(1000 * sum(c for c, _ in km_pairs) / km, 2) if km else None,
                cost_per_hour=round(sum(c for c, _ in hour_pairs) / hours, 2) if hours else None,
            )
        )
    lines.sort(key=lambda line: -line.cost)
    return lines


# --- alerts ----------------------------------------------------------------------------------


class AlertKind(StrEnum):
    LOW_TREAD = "low_tread"
    UNDER_INFLATION = "under_inflation"
    OVER_INFLATION = "over_inflation"
    OVERDUE_INSPECTION = "overdue_inspection"
    ABNORMAL_WEAR = "abnormal_wear"
    COST_ANOMALY = "cost_anomaly"


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertCandidate(BaseModel):
    key: str = Field(max_length=300)
    kind: AlertKind
    severity: Severity
    subject_type: str  # tyre, vehicle, cost
    subject_id: str
    subject_label: str
    message: str
    value: dict[str, float | str | None] = Field(default_factory=dict)


def _tyre_label(tyre: Tyre | None, fallback: str, vehicles: dict[str, Vehicle]) -> str:
    if tyre is None:
        return fallback
    vehicle = vehicles.get(tyre.vehicle_id or "")
    where = (vehicle.label or vehicle.id) if vehicle else tyre.vehicle_id
    parts = [tyre.serial or tyre.id]
    if where:
        parts.append(f"on {where}")
    if tyre.position:
        parts.append(f"at {tyre.position}")
    return " ".join(parts)


def evaluate_alerts(
    dataset: TyreDataset, assumptions: Assumptions, as_of: date, forecasts: list[TyreForecast] | None = None
) -> list[AlertCandidate]:
    forecasts = forecasts if forecasts is not None else forecast_tyres(dataset, assumptions, as_of)
    tyres = {tyre.id: tyre for tyre in dataset.tyres}
    vehicles = {vehicle.id: vehicle for vehicle in dataset.vehicles}
    alerts: list[AlertCandidate] = []
    minimum, band = assumptions.min_tread_mm, assumptions.warning_band_mm

    for item in forecasts:
        if item.current_tread_mm is None or not item.readings:
            continue
        label = _tyre_label(tyres.get(item.tyre_id), item.tyre_id, vehicles)
        if item.current_tread_mm <= minimum:
            alerts.append(AlertCandidate(
                key=f"low_tread:{item.tyre_id}", kind=AlertKind.LOW_TREAD, severity=Severity.CRITICAL,
                subject_type="tyre", subject_id=item.tyre_id, subject_label=label,
                message=f"Tread {item.current_tread_mm:.1f} mm is at or below the {minimum:.1f} mm minimum: remove now.",
                value={"tread_mm": item.current_tread_mm, "minimum_mm": minimum},
            ))
        elif item.current_tread_mm <= minimum + band:
            alerts.append(AlertCandidate(
                key=f"low_tread:{item.tyre_id}", kind=AlertKind.LOW_TREAD, severity=Severity.WARNING,
                subject_type="tyre", subject_id=item.tyre_id, subject_label=label,
                message=(f"Tread {item.current_tread_mm:.1f} mm is within {band:.1f} mm of the {minimum:.1f} mm minimum"
                         + (f"; projected removal {item.projected_removal.isoformat()}." if item.projected_removal else ".")),
                value={"tread_mm": item.current_tread_mm, "minimum_mm": minimum},
            ))

    stale = as_of - timedelta(days=assumptions.inspection_interval_days)
    for key, check in latest_pressure(pressure_checks(dataset, assumptions)).items():
        if check.verdict not in ("under", "over") or check.recorded_at < stale:
            continue
        tyre = tyres.get(check.tyre_id or "")
        if tyre is not None and tyre.status != TyreStatus.FITTED:
            continue
        label = _tyre_label(tyre, f"{check.vehicle_id or 'vehicle'} {check.position or ''}".strip(), vehicles)
        deviation = abs(check.deviation_pct or 0)
        severe = deviation >= 2 * assumptions.pressure_tolerance_pct
        kind = AlertKind.UNDER_INFLATION if check.verdict == "under" else AlertKind.OVER_INFLATION
        alerts.append(AlertCandidate(
            key=f"{kind.value}:{key}", kind=kind,
            severity=Severity.CRITICAL if severe else Severity.WARNING,
            subject_type="tyre" if tyre else "vehicle", subject_id=check.tyre_id or key, subject_label=label,
            message=(f"{check.pressure_psi:.0f} psi against a {check.target_psi:.0f} psi target "
                     f"({check.deviation_pct:+.0f}%) on {check.recorded_at.isoformat()}."),
            value={"pressure_psi": check.pressure_psi, "target_psi": check.target_psi, "deviation_pct": check.deviation_pct},
        ))

    for tyre, last, days in overdue_tyres(dataset, assumptions, as_of):
        alerts.append(AlertCandidate(
            key=f"overdue_inspection:{tyre.id}", kind=AlertKind.OVERDUE_INSPECTION,
            severity=Severity.WARNING if days > 2 * assumptions.inspection_interval_days else Severity.INFO,
            subject_type="tyre", subject_id=tyre.id, subject_label=_tyre_label(tyre, tyre.id, vehicles),
            message=(f"Not inspected for {days} days (last {last.isoformat()})." if last
                     else f"Never inspected since fitment {days} days ago."),
            value={"days": float(days), "interval_days": float(assumptions.inspection_interval_days)},
        ))

    group_rates: dict[tuple[str | None, str | None], list[float]] = defaultdict(list)
    for item in forecasts:
        if item.basis in (Basis.DISTANCE, Basis.TIME) and item.wear_mm_per_30_days:
            group_rates[(item.brand, item.size)].append(item.wear_mm_per_30_days)
    for item in forecasts:
        sample = group_rates.get((item.brand, item.size), [])
        if item.basis not in (Basis.DISTANCE, Basis.TIME) or not item.wear_mm_per_30_days or len(sample) < 5:
            continue
        median = statistics.median(sample)
        if median > 0 and item.wear_mm_per_30_days > assumptions.abnormal_wear_factor * median:
            alerts.append(AlertCandidate(
                key=f"abnormal_wear:{item.tyre_id}", kind=AlertKind.ABNORMAL_WEAR, severity=Severity.WARNING,
                subject_type="tyre", subject_id=item.tyre_id,
                subject_label=_tyre_label(tyres.get(item.tyre_id), item.tyre_id, vehicles),
                message=(f"Wearing {item.wear_mm_per_30_days:.2f} mm per 30 days, "
                         f"{item.wear_mm_per_30_days / median:.1f}x the {item.brand or 'unknown'} {item.size or ''} median "
                         f"of {median:.2f} mm: check alignment, pressure and load."),
                value={"wear_mm_per_30_days": item.wear_mm_per_30_days, "group_median": round(median, 3)},
            ))

    alerts.extend(_cost_anomalies(dataset.costs, dataset.tyres, assumptions, as_of, vehicles, tyres))
    return alerts


def _cost_anomalies(costs: list[Cost], tyre_list: list[Tyre], assumptions: Assumptions, as_of: date,
                    vehicles: dict[str, Vehicle], tyres: dict[str, Tyre]) -> list[AlertCandidate]:
    alerts: list[AlertCandidate] = []
    window = as_of - timedelta(days=90)
    by_category: dict[str, list[float]] = defaultdict(list)
    for cost in costs:
        by_category[cost.category or "other"].append(cost.amount)
    for cost in costs:
        sample = by_category[cost.category or "other"]
        if cost.date < window or len(sample) < 5:
            continue
        median = statistics.median(sample)
        if median > 0 and cost.amount > assumptions.cost_anomaly_factor * median:
            subject = cost.tyre_id or cost.vehicle_id or "fleet"
            alerts.append(AlertCandidate(
                key=f"cost_anomaly:{subject}:{cost.date.isoformat()}:{cost.amount:.2f}", kind=AlertKind.COST_ANOMALY,
                severity=Severity.WARNING, subject_type="cost", subject_id=subject,
                subject_label=_tyre_label(tyres.get(cost.tyre_id or ""), subject, vehicles),
                message=(f"{cost.category or 'Cost'} of {cost.amount:,.2f} on {cost.date.isoformat()} is "
                         f"{cost.amount / median:.1f}x the usual {median:,.2f}."),
                value={"amount": cost.amount, "median": round(median, 2)},
            ))
    by_size: dict[str, list[float]] = defaultdict(list)
    for tyre in tyre_list:
        if tyre.purchase_cost and tyre.size:
            by_size[tyre.size].append(tyre.purchase_cost)
    for tyre in tyre_list:
        sample = by_size.get(tyre.size or "", [])
        if not tyre.purchase_cost or len(sample) < 5 or (tyre.fitted_at and tyre.fitted_at < window):
            continue
        median = statistics.median(sample)
        if median > 0 and tyre.purchase_cost > assumptions.cost_anomaly_factor * median:
            alerts.append(AlertCandidate(
                key=f"cost_anomaly:purchase:{tyre.id}", kind=AlertKind.COST_ANOMALY, severity=Severity.WARNING,
                subject_type="tyre", subject_id=tyre.id, subject_label=_tyre_label(tyre, tyre.id, vehicles),
                message=(f"Bought for {tyre.purchase_cost:,.2f}, {tyre.purchase_cost / median:.1f}x the "
                         f"{tyre.size} median of {median:,.2f}."),
                value={"amount": tyre.purchase_cost, "median": round(median, 2)},
            ))
    return alerts
