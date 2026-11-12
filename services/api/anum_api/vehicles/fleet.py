"""Fleet cost analytics for a workspace's business vehicles (docs/vehicles.md#fleet-costs).

Pure functions over vehicles, odometer readings, fuel entries and service records, plus one loader that reads
them in the caller's scope. The fleet costs page, the monthly client report and the phone all call these, so
the figures agree.

Rules:

* **Business vehicles only.** A person's own car never appears in a client's fleet figures.
* **One cost per record.** A record's cost is its active linked payment or business allocation when it has
  one (``linked``); otherwise the figure written on the record itself, the pump total or the receipt total
  (``recorded``), which is how an imported client fleet carries its costs. Nothing is posted to Finance and
  nothing is counted twice. The share of each basis is reported.
* **Categories:** fuel; service (maintenance, oil, battery, repair, inspection, warranty); tyres (a service
  record of kind tyres); other (insurance, registration, fines, tolls, parking, washes and anything else).
* **Never across currencies.** Every money figure is per currency; cost per km divides a currency's cost by
  the kilometres of the vehicles kept in that currency.
* **Distance** is the module's rule (increases between consecutive readings, a correction starts a new run),
  counted in the month of the later reading.
* **Tyre planning** (the read-only Tyre Pulse snapshot, docs/tyre-planning.md) is shown beside the figures for
  vehicles whose label or fleet number matches a registration or nickname, never added into them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from ..authorization import Permission
from ..dependencies import has_permission
from ..schemas import TenantContext, utc_now
from . import analytics as A
from .models import FuelEntry, OdometerReading, Ownership, ServiceRecord, Vehicle

CATEGORIES = ("fuel", "service", "tyres", "other")
SERVICE_KINDS = frozenset({"maintenance", "oil", "battery", "repair", "inspection", "warranty"})
RENEWALS = (("insurance", "insurance_expires_on"), ("registration", "registration_expires_on"), ("inspection", "inspection_expires_on"))
TREND_MONTHS = 12
WORST_MIN_KM = Decimal(100)  # a vehicle needs this many kilometres in the month to be ranked by cost per km
WORST_COUNT = 5
TYRE_DUE_DAYS = 60
RENEWAL_DAYS = 60
CENT = Decimal("0.01")
MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


@dataclass
class FleetCost:
    record_id: str
    vehicle_id: str
    category: str
    day: date
    amount: Decimal
    currency: str
    basis: Literal["linked", "recorded"]


def category_of(kind: str) -> str:
    if kind == "fuel":
        return "fuel"
    if kind == "tyres":
        return "tyres"
    return "service" if kind in SERVICE_KINDS else "other"


def record_costs(fuel: list[FuelEntry], service: list[ServiceRecord]) -> list[FleetCost]:
    """Each record's one cost: its active payment or allocation, else its own figure."""
    out: list[FleetCost] = []
    for fill in fuel:
        day = fill.filled_at.date()
        if fill.payment is not None and fill.payment.status == "active":
            out.append(FleetCost(fill.id, fill.vehicle_id, "fuel", day, fill.payment.amount, fill.payment.currency, "linked"))
        elif fill.total is not None:
            out.append(FleetCost(fill.id, fill.vehicle_id, "fuel", day, fill.total, fill.currency, "recorded"))
    for record in service:
        category = category_of(record.kind)
        if record.payment is not None and record.payment.status == "active":
            out.append(FleetCost(record.id, record.vehicle_id, category, record.service_on, record.payment.amount, record.payment.currency, "linked"))
        elif record.receipt_total is not None:
            out.append(FleetCost(record.id, record.vehicle_id, category, record.service_on, record.receipt_total, record.currency, "recorded"))
    return out


def parse_month(value: str | None, today: date) -> date:
    """The first day of ``YYYY-MM`` (this month when empty); never after this month."""
    if not value:
        return today.replace(day=1)
    found = MONTH.match(value)
    if not found:
        raise ValueError("month")
    first = date(int(found.group(1)), int(found.group(2)), 1)
    if first > today.replace(day=1):
        raise ValueError("future")
    return first


def month_end(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def months_ending(first: date, count: int) -> list[date]:
    out = [first]
    while len(out) < count:
        previous = out[0] - timedelta(days=1)
        out.insert(0, previous.replace(day=1))
    return out


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _per_km(amount: Decimal, km: Decimal) -> str | None:
    return str((amount / km).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)) if km > 0 else None


def _key(text: str | None) -> str:
    return re.sub(r"[\s\-_.]+", "", (text or "")).upper()


def fleet_summary(vehicles: list[Vehicle], readings: dict[str, list[OdometerReading]], fuel: list[FuelEntry], service: list[ServiceRecord], *,
                  month: date, today: date) -> dict[str, Any]:
    """The month's figures per vehicle and for the fleet, the twelve months to it, the worst vehicles and the renewals due."""
    months = months_ending(month, TREND_MONTHS)
    window_start, window_end = months[0], month_end(month)
    labels = [m.strftime("%Y-%m") for m in months]
    current = month.strftime("%Y-%m")
    previous = months[-2].strftime("%Y-%m")
    costs = [c for c in record_costs(fuel, service) if window_start <= c.day <= window_end]
    distance = {v.id: A.distance_by_month(readings.get(v.id, []), window_start, window_end) for v in vehicles}
    litres: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for fill in fuel:
        if window_start <= fill.filled_at.date() <= window_end:
            litres[(fill.vehicle_id, A.month_of(fill.filled_at))] += fill.litres
    corrections = {v.id: [r.read_at for r in readings.get(v.id, []) if r.correction] for v in vehicles}
    fuel_by_vehicle: dict[str, list[FuelEntry]] = defaultdict(list)
    for fill in fuel:
        fuel_by_vehicle[fill.vehicle_id].append(fill)

    # vehicle, month, currency -> category -> amount
    cells: dict[tuple[str, str, str], dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    basis: dict[tuple[str, str], Decimal] = defaultdict(Decimal)  # (currency, basis) in the month
    for cost in costs:
        label = cost.day.strftime("%Y-%m")
        cells[(cost.vehicle_id, label, cost.currency)][cost.category] += cost.amount
        if label == current:
            basis[(cost.currency, cost.basis)] += cost.amount

    rows: list[dict[str, Any]] = []
    worst_pool: list[dict[str, Any]] = []
    for vehicle in vehicles:
        km = distance[vehicle.id].get(current, Decimal(0))
        km_before = distance[vehicle.id].get(previous, Decimal(0))
        segments = [s for s in A.consumption(fuel_by_vehicle.get(vehicle.id, []), corrections[vehicle.id]) if s.ended_on.strftime("%Y-%m") == current]
        currencies = sorted({c for (vid, m, c) in cells if vid == vehicle.id and m in (current, previous)} | {vehicle.currency})
        money_rows = []
        for currency in currencies:
            this = cells.get((vehicle.id, current, currency), {})
            before = cells.get((vehicle.id, previous, currency), {})
            total = sum(this.values(), Decimal(0))
            total_before = sum(before.values(), Decimal(0))
            if not total and not total_before and currency != vehicle.currency:
                continue
            km_for = km if currency == vehicle.currency else Decimal(0)
            money_rows.append({"currency": currency, **{c: _money(this.get(c, Decimal(0))) for c in CATEGORIES}, "total": _money(total),
                               "previous_total": _money(total_before), "cost_per_km": _per_km(total, km_for)})
            if currency == vehicle.currency and km >= WORST_MIN_KM and total > 0:
                worst_pool.append({"vehicle_id": vehicle.id, "registration": vehicle.registration, "nickname": vehicle.nickname, "currency": currency,
                                   "cost_per_km": _per_km(total, km), "total": _money(total), "distance_km": str(km.quantize(A.TENTH)),
                                   "_rank": total / km})
        active = bool(km or km_before or litres.get((vehicle.id, current)) or any(Decimal(r["total"]) or Decimal(r["previous_total"]) for r in money_rows))
        if vehicle.archived_at is not None and not active:
            continue  # an archived vehicle shows only for a month it was used in
        rows.append({
            "vehicle_id": vehicle.id, "registration": vehicle.registration, "nickname": vehicle.nickname, "make": vehicle.make, "model": vehicle.model,
            "currency": vehicle.currency, "archived": vehicle.archived_at is not None,
            "distance_km": str(km.quantize(A.TENTH)), "previous_distance_km": str(km_before.quantize(A.TENTH)),
            "fuel_litres": str(litres.get((vehicle.id, current), Decimal(0)).quantize(A.LITRE)),
            "litres_per_100km": str(A.average_per_100(segments)) if A.average_per_100(segments) is not None else None,
            "costs": money_rows,
        })

    worst: list[dict[str, Any]] = []
    for currency in sorted({w["currency"] for w in worst_pool}):
        ranked = sorted((w for w in worst_pool if w["currency"] == currency), key=lambda w: (-w["_rank"], w["registration"]))[:WORST_COUNT]
        worst.extend({k: v for k, v in w.items() if k != "_rank"} for w in ranked)

    shown = {r["vehicle_id"] for r in rows}
    by_currency_vehicles: dict[str, set[str]] = defaultdict(set)
    for vehicle in vehicles:
        if vehicle.id in shown:
            by_currency_vehicles[vehicle.currency].add(vehicle.id)
    trend: list[dict[str, Any]] = []
    currencies = sorted({c for (_v, _m, c) in cells} | {v.currency for v in vehicles if v.id in shown})
    for label in labels:
        for currency in currencies:
            sums = {c: Decimal(0) for c in CATEGORIES}
            for (vid, m, c), values in cells.items():
                if m == label and c == currency:
                    for category, amount in values.items():
                        sums[category] += amount
            km = sum((distance[v].get(label, Decimal(0)) for v in by_currency_vehicles.get(currency, set())), Decimal(0))
            total = sum(sums.values(), Decimal(0))
            trend.append({"month": label, "currency": currency, **{c: _money(sums[c]) for c in CATEGORIES}, "total": _money(total),
                          "distance_km": str(km.quantize(A.TENTH)), "cost_per_km": _per_km(total, km)})
    totals = [t for t in trend if t["month"] == current]
    previous_totals = {t["currency"]: t for t in trend if t["month"] == previous}
    for item in totals:
        before = previous_totals.get(item["currency"])
        item["previous_total"] = before["total"] if before else _money(Decimal(0))
        item["linked"] = _money(basis.get((item["currency"], "linked"), Decimal(0)))
        item["recorded"] = _money(basis.get((item["currency"], "recorded"), Decimal(0)))

    return {
        "month": current, "start": month.isoformat(), "end": window_end.isoformat(), "months": labels,
        "vehicles": len(rows), "distance_km": str(sum((distance[r["vehicle_id"]].get(current, Decimal(0)) for r in rows), Decimal(0)).quantize(A.TENTH)),
        "fuel_litres": str(sum((litres.get((r["vehicle_id"], current), Decimal(0)) for r in rows), Decimal(0)).quantize(A.LITRE)),
        "totals": totals, "trend": trend, "rows": sorted(rows, key=lambda r: r["registration"].upper()), "worst": worst,
        "renewals": renewals(vehicles, month=month, today=today),
    }


def renewals(vehicles: list[Vehicle], *, month: date, today: date) -> list[dict[str, Any]]:
    """Insurance, registration and inspection that have expired or expire within 60 days of the later of the month's end and today
    (a report for October prepared in November lists what falls due in November and December)."""
    horizon = max(month_end(month), today) + timedelta(days=RENEWAL_DAYS)
    out = []
    for vehicle in vehicles:
        if vehicle.archived_at is not None:
            continue
        for kind, attribute in RENEWALS:
            due: date | None = getattr(vehicle, attribute)
            if due is not None and due <= horizon:
                out.append({"vehicle_id": vehicle.id, "registration": vehicle.registration, "nickname": vehicle.nickname, "kind": kind,
                            "due_on": due.isoformat(), "days_left": (due - today).days})
    return sorted(out, key=lambda r: (r["due_on"], r["registration"], r["kind"]))


def tyre_section(context: TenantContext, vehicles: list[Vehicle], *, month: date, today: date) -> dict[str, Any]:
    """The tyre planning snapshot for the matched vehicles, read only (docs/tyre-planning.md): tyre costs in the month and
    tyres due for removal within 60 days. ``status`` says why there is nothing: no access, not connected, not synced."""
    if not has_permission(context, Permission.TYRE_READ):
        return {"status": "denied", "vehicles": [], "matched": 0}
    from ..tyre.service import NotConnectedError, NotSyncedError, forecasts_cached

    try:
        loaded, forecasts = forecasts_cached(context)
    except NotConnectedError:
        return {"status": "not_connected", "vehicles": [], "matched": 0}
    except NotSyncedError:
        return {"status": "not_synced", "vehicles": [], "matched": 0}
    dataset = loaded.snapshot.dataset
    ours: dict[str, Vehicle] = {}
    for vehicle in vehicles:
        for text in (vehicle.registration, vehicle.nickname):
            if _key(text):
                ours.setdefault(_key(text), vehicle)
    start, end = month, month_end(month)
    horizon = today + timedelta(days=TYRE_DUE_DAYS)
    out: list[dict[str, Any]] = []
    for tyre_vehicle in dataset.vehicles:
        match = next((ours[_key(t)] for t in (tyre_vehicle.label, tyre_vehicle.fleet_number, tyre_vehicle.id) if _key(t) in ours), None)
        if match is None:
            continue
        spent = sum((Decimal(str(c.amount)) for c in dataset.costs if c.vehicle_id == tyre_vehicle.id and start <= c.date <= end), Decimal(0))
        due = [f for f in forecasts if f.vehicle_id == tyre_vehicle.id
               and (f.state.value == "due" or (f.projected_removal is not None and f.projected_removal <= horizon))]
        removal = min((f.projected_removal for f in due if f.projected_removal is not None), default=None)
        out.append({"vehicle_id": match.id, "registration": match.registration, "tyre_vehicle": tyre_vehicle.label or tyre_vehicle.fleet_number or tyre_vehicle.id,
                    "tyre_cost": _money(spent), "tyres_due": len(due), "next_removal": removal.isoformat() if removal else None})
    out.sort(key=lambda r: r["registration"].upper())
    return {"status": "ok", "currency": dataset.currency, "synced_at": loaded.snapshot.stats.synced_at.isoformat() if getattr(loaded.snapshot, "stats", None) else None,
            "matched": len(out), "vehicles": out, "tyre_cost": _money(sum((Decimal(r["tyre_cost"]) for r in out), Decimal(0))),
            "tyres_due": sum(r["tyres_due"] for r in out)}


def load_summary(context: TenantContext, month: date) -> dict[str, Any]:
    """The fleet summary for ``month`` in the caller's scope: business vehicles (active, and archived ones used in the window)."""
    from . import service as S
    from .store import MAX_VEHICLES, open_vehicle_store

    today = utc_now().date()
    with open_vehicle_store(context) as store:
        active, _ = store.list_vehicles(context, query=None, archived=False, limit=MAX_VEHICLES, offset=0)
        archived, _ = store.list_vehicles(context, query=None, archived=True, limit=10_000, offset=0)
        vehicles = [v for v in [*active, *archived] if v.ownership == Ownership.BUSINESS]
        ids = [v.id for v in vehicles]
        fuel = store.records_for(context, "fuel", ids)
        service = store.records_for(context, "service", ids)
        readings = {v.id: store.readings(context, v.id) for v in vehicles}
    fuel, service = S.refresh_allocations(context, fuel), S.refresh_allocations(context, service)
    summary = fleet_summary(vehicles, readings, fuel, service, month=month, today=today)
    summary["tyres"] = tyre_section(context, [v for v in vehicles if v.archived_at is None], month=month, today=today)
    summary["generated_at"] = utc_now().isoformat()
    return summary


__all__ = ["CATEGORIES", "FleetCost", "category_of", "fleet_summary", "load_summary", "month_end", "months_ending", "parse_month", "record_costs",
           "renewals", "tyre_section"]
