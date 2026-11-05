"""Vehicle analytics as pure functions (docs/vehicles.md#analytics).

Rules:

* **Money comes only from linked payments** (``payment.status == "active"``): the pump total on a
  fuel entry is a detail used for price trends and checks, never counted as spend.
* **Never across currencies.** Every money figure is per currency; nothing adds SAR to AED.
* **Distance** is the sum of increases between consecutive odometer readings; a correction starts
  a new run (the drop to it is not distance).
* **L/100 km only from full-to-full chains**: from one full fill with an odometer value to the next,
  counting the litres of every fill after the first (partial fills included). A fill without an
  odometer value, a full fill missing in between, or an odometer correction breaks the chain.
* **A capital purchase is not an operating cost**: depreciation (straight line) is shown next to
  operating spend for the total cost of ownership, never inside it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any, Iterable

from .models import FuelEntry, OdometerReading, ServiceRecord, Vehicle

CENT = Decimal("0.01")
TENTH = Decimal("0.1")
PRICE_OUTLIER = Decimal("0.25")  # 25 % from the vehicle's median price per litre
CONSUMPTION_OUTLIER = Decimal("1.5")  # 1.5 times the vehicle's median L/100 km
TANK_SLACK = Decimal("1.05")
PAYMENT_GAP = Decimal("0.05")


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _day(moment: datetime | date) -> date:
    return moment.date() if isinstance(moment, datetime) else moment


def _in(moment: datetime | date, start: date | None, end: date | None) -> bool:
    day = _day(moment)
    return (start is None or day >= start) and (end is None or day <= end)


def month_of(moment: datetime | date) -> str:
    return _day(moment).strftime("%Y-%m")


# -- distance --------------------------------------------------------------------------------------


def distance_km(readings: Iterable[OdometerReading], start: date | None = None, end: date | None = None) -> Decimal:
    """Kilometres driven between readings inside the period (both readings inside it)."""
    ordered = sorted((r for r in readings if _in(r.read_at, start, end)), key=lambda r: (r.read_at, r.created_at, r.id))
    total = Decimal(0)
    for before, after in zip(ordered, ordered[1:]):
        if after.correction:
            continue  # a correction starts a new run: the jump to it is not distance
        if after.reading_km > before.reading_km:
            total += after.reading_km - before.reading_km
    return total


def current_odometer(readings: Iterable[OdometerReading]) -> Decimal | None:
    ordered = sorted(readings, key=lambda r: (r.read_at, r.created_at, r.id))
    return ordered[-1].reading_km if ordered else None


# -- fuel consumption --------------------------------------------------------------------------------


@dataclass
class Segment:
    start_id: str
    end_id: str
    start_km: Decimal
    end_km: Decimal
    litres: Decimal
    ended_on: date

    @property
    def km(self) -> Decimal:
        return self.end_km - self.start_km

    @property
    def per_100(self) -> Decimal:
        return (self.litres / self.km * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def consumption(fuel: Iterable[FuelEntry], corrections: Iterable[datetime] = ()) -> list[Segment]:
    """Full-to-full segments, oldest first."""
    breaks = sorted(corrections)
    ordered = sorted(fuel, key=lambda f: (f.filled_at, f.created_at, f.id))
    segments: list[Segment] = []
    anchor: FuelEntry | None = None
    litres = Decimal(0)
    for fill in ordered:
        if anchor is not None and any(anchor.filled_at < moment <= fill.filled_at for moment in breaks):
            anchor, litres = None, Decimal(0)  # an odometer correction in between: the chain restarts
        if not fill.full_tank:
            if anchor is not None:
                litres += fill.litres  # a partial fill inside a chain counts towards the next full fill
            continue
        if fill.odometer_km is None:
            anchor, litres = None, Decimal(0)  # a full fill without an odometer value breaks the chain
            continue
        if anchor is not None and anchor.odometer_km is not None and fill.odometer_km > anchor.odometer_km:
            segments.append(Segment(start_id=anchor.id, end_id=fill.id, start_km=anchor.odometer_km, end_km=fill.odometer_km,
                                    litres=litres + fill.litres, ended_on=_day(fill.filled_at)))
        anchor, litres = fill, Decimal(0)
    return segments


def average_per_100(segments: list[Segment]) -> Decimal | None:
    km = sum((s.km for s in segments), Decimal(0))
    if km <= 0:
        return None
    litres = sum((s.litres for s in segments), Decimal(0))
    return (litres / km * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# -- money -----------------------------------------------------------------------------------------


def price_per_litre(fill: FuelEntry) -> Decimal | None:
    if fill.price_per_litre is not None:
        return fill.price_per_litre
    if fill.total is not None and fill.litres > 0:
        return (fill.total / fill.litres).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return None


@dataclass
class Cost:
    record_id: str
    vehicle_id: str
    category: str  # "fuel" or a service kind
    day: date
    amount: Decimal
    currency: str


def costs(fuel: Iterable[FuelEntry], service: Iterable[ServiceRecord]) -> list[Cost]:
    """Operating costs from active linked payments only (a capital purchase is never one)."""
    out: list[Cost] = []
    for fill in fuel:
        if fill.payment is not None and fill.payment.status == "active":
            out.append(Cost(fill.id, fill.vehicle_id, "fuel", _day(fill.filled_at), fill.payment.amount, fill.payment.currency))
    for record in service:
        if record.payment is not None and record.payment.status == "active":
            out.append(Cost(record.id, record.vehicle_id, record.kind, record.service_on, record.payment.amount, record.payment.currency))
    return out


def by_currency(items: Iterable[Cost]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for item in items:
        totals[item.currency] += item.amount
    return dict(totals)


def monthly(items: Iterable[Cost]) -> list[dict[str, Any]]:
    """``[{month, currency, amount}]`` sorted by month then currency."""
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for item in items:
        totals[(item.day.strftime("%Y-%m"), item.currency)] += item.amount
    return [{"month": m, "currency": c, "amount": money(v)} for (m, c), v in sorted(totals.items())]


def by_category(items: Iterable[Cost]) -> list[dict[str, Any]]:
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for item in items:
        totals[(item.currency, item.category)] += item.amount
    return [{"currency": c, "category": k, "amount": money(v)} for (c, k), v in sorted(totals.items(), key=lambda kv: (kv[0][0], -kv[1]))]


def price_trend(fuel: Iterable[FuelEntry]) -> list[dict[str, Any]]:
    """Litres-weighted price per litre per month, currency and fuel type."""
    sums: dict[tuple[str, str, str], tuple[Decimal, Decimal]] = {}
    for fill in fuel:
        price = price_per_litre(fill)
        if price is None:
            continue
        key = (month_of(fill.filled_at), fill.currency, fill.fuel_type)
        spent, litres = sums.get(key, (Decimal(0), Decimal(0)))
        sums[key] = (spent + price * fill.litres, litres + fill.litres)
    return [{"month": m, "currency": c, "fuel_type": t, "price_per_litre": str((s / l).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))}
            for (m, c, t), (s, l) in sorted(sums.items()) if l > 0]


# -- depreciation and total cost of ownership ------------------------------------------------------


def months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def depreciation(vehicle: Vehicle, as_of: date) -> dict[str, Any] | None:
    """Straight-line depreciation in the vehicle's currency, or None without a price, a date and a useful life."""
    if vehicle.purchase_price is None or vehicle.acquisition_date is None or not vehicle.useful_life_months:
        return None
    residual = vehicle.residual_value or Decimal(0)
    base = max(vehicle.purchase_price - residual, Decimal(0))
    monthly_amount = base / vehicle.useful_life_months
    elapsed = min(months_between(vehicle.acquisition_date, as_of), vehicle.useful_life_months)
    accumulated = monthly_amount * elapsed
    return {"currency": vehicle.currency, "purchase_price": money(vehicle.purchase_price), "residual_value": money(residual),
            "monthly": money(monthly_amount), "months": elapsed, "accumulated": money(accumulated),
            "book_value": money(vehicle.purchase_price - accumulated)}


# -- anomalies --------------------------------------------------------------------------------------


@dataclass
class Anomaly:
    kind: str
    record_id: str
    detail: dict[str, str] = field(default_factory=dict)


def anomalies(vehicle: Vehicle, fuel: list[FuelEntry], service: list[ServiceRecord], segments: list[Segment]) -> list[Anomaly]:
    found: list[Anomaly] = []
    if vehicle.tank_litres:
        for fill in fuel:
            if fill.litres > vehicle.tank_litres * TANK_SLACK:
                found.append(Anomaly("over_tank", fill.id, {"litres": str(fill.litres), "tank_litres": str(vehicle.tank_litres)}))
    if len(segments) >= 3:
        typical = Decimal(str(median([s.per_100 for s in segments])))
        for segment in segments:
            if typical > 0 and segment.per_100 > typical * CONSUMPTION_OUTLIER:
                found.append(Anomaly("high_consumption", segment.end_id, {"per_100": str(segment.per_100), "typical": str(typical)}))
    prices: dict[tuple[str, str], list[tuple[FuelEntry, Decimal]]] = defaultdict(list)
    for fill in fuel:
        price = price_per_litre(fill)
        if price is not None:
            prices[(fill.currency, fill.fuel_type)].append((fill, price))
    for rows in prices.values():
        if len(rows) < 3:
            continue
        typical = Decimal(str(median([p for _f, p in rows])))
        for fill, price in rows:
            if typical > 0 and abs(price - typical) / typical > PRICE_OUTLIER:
                found.append(Anomaly("price_outlier", fill.id, {"price_per_litre": str(price), "typical": str(typical), "currency": fill.currency}))
    for fill in fuel:
        paid = fill.payment
        if paid is None:
            continue
        if paid.status == "needs_review":
            found.append(Anomaly("payment_changed", fill.id))
        elif paid.status == "void":
            found.append(Anomaly("payment_undone", fill.id))
        elif fill.total is not None and paid.currency == fill.currency and fill.total > 0:
            gap = abs(paid.amount - fill.total)
            if gap / fill.total > PAYMENT_GAP and gap > 1:
                found.append(Anomaly("payment_mismatch", fill.id, {"paid": money(paid.amount), "pump": money(fill.total), "currency": fill.currency}))
    for record in service:
        if record.payment is not None and record.payment.status in ("needs_review", "void"):
            found.append(Anomaly("payment_changed" if record.payment.status == "needs_review" else "payment_undone", record.id))
    return found


# -- one vehicle --------------------------------------------------------------------------------------


def vehicle_report(vehicle: Vehicle, readings: list[OdometerReading], fuel: list[FuelEntry], service: list[ServiceRecord], *,
                   start: date | None, end: date | None, today: date) -> dict[str, Any]:
    in_fuel = [f for f in fuel if _in(f.filled_at, start, end)]
    in_service = [s for s in service if _in(s.service_on, start, end)]
    period_costs = costs(in_fuel, in_service)
    km = distance_km(readings, start, end)
    spend = by_currency(period_costs)
    corrections = [r.read_at for r in readings if r.correction]
    all_segments = consumption(fuel, corrections)
    segments = [s for s in all_segments if _in(s.ended_on, start, end)]
    lifetime = by_currency(costs(fuel, service))
    unlinked = sum(1 for f in in_fuel if f.payment is None or f.payment.status == "void") + sum(
        1 for s in in_service if s.payment is None or s.payment.status == "void")
    return {
        "vehicle_id": vehicle.id,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "odometer_km": str(current_odometer(readings)) if readings else None,
        "distance_km": str(km.quantize(TENTH)),
        "spend": [{"currency": c, "amount": money(v)} for c, v in sorted(spend.items())],
        "cost_per_km": [{"currency": c, "amount": str((v / km).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))}
                        for c, v in sorted(spend.items()) if km > 0],
        "monthly": monthly(period_costs),
        "by_category": by_category(period_costs),
        "fuel_litres": str(sum((f.litres for f in in_fuel), Decimal(0))),
        "fuel_price_trend": price_trend(in_fuel),
        "consumption": {
            "average_per_100": str(avg) if (avg := average_per_100(segments)) is not None else None,
            "segments": [{"start_id": s.start_id, "end_id": s.end_id, "km": str(s.km), "litres": str(s.litres), "per_100": str(s.per_100),
                          "ended_on": s.ended_on.isoformat()} for s in segments],
        },
        "anomalies": [{"kind": a.kind, "record_id": a.record_id, "detail": a.detail}
                      for a in anomalies(vehicle, in_fuel, in_service, all_segments)],
        "unlinked_records": unlinked,
        "ownership_cost": {
            "operating": [{"currency": c, "amount": money(v)} for c, v in sorted(lifetime.items())],
            "depreciation": depreciation(vehicle, today),
            "capital_paid": ({"currency": vehicle.purchase_payment.currency, "amount": money(vehicle.purchase_payment.amount)}
                             if vehicle.purchase_payment is not None and vehicle.purchase_payment.status == "active" else None),
        },
    }


def overview(vehicles: list[Vehicle], fuel: list[FuelEntry], service: list[ServiceRecord], *, start: date | None, end: date | None) -> dict[str, Any]:
    """Spend across vehicles: per month and currency, and per vehicle and currency."""
    period = costs([f for f in fuel if _in(f.filled_at, start, end)], [s for s in service if _in(s.service_on, start, end)])
    per_vehicle: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for item in period:
        per_vehicle[(item.vehicle_id, item.currency)] += item.amount
    names = {v.id: v.registration for v in vehicles}
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "spend": [{"currency": c, "amount": money(v)} for c, v in sorted(by_currency(period).items())],
        "monthly": monthly(period),
        "by_vehicle": [{"vehicle_id": vid, "registration": names.get(vid, ""), "currency": c, "amount": money(v)}
                       for (vid, c), v in sorted(per_vehicle.items(), key=lambda kv: (kv[0][1], -kv[1]))],
    }


__all__ = [
    "Anomaly",
    "Cost",
    "Segment",
    "anomalies",
    "average_per_100",
    "by_category",
    "by_currency",
    "consumption",
    "costs",
    "current_odometer",
    "depreciation",
    "distance_km",
    "monthly",
    "overview",
    "price_per_litre",
    "price_trend",
    "vehicle_report",
]
