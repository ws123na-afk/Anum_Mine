"""Tyre reports: content built from the synced dataset, exported as CSV or PDF.

A report's content is plain JSON (title, highlights with the numbers, tables, notes),
stored with the report so a later sync never changes a report already sent. Exports are
rendered from that stored content.
"""

from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .analytics import (
    PressureCheck,
    TyreForecast,
    brand_performance,
    cost_per_distance,
    fleet_summary,
    forecast_tyres,
    pressure_checks,
    purchase_plan,
    scrap_reasons,
)
from .model import Assumptions, TyreDataset
from .pdf import PdfDocument
from .store import ReportKind

TITLES: dict[ReportKind, str] = {
    ReportKind.FLEET_HEALTH: "Fleet tyre health",
    ReportKind.WEAR_RATES: "Tread wear rates",
    ReportKind.PRESSURE_COMPLIANCE: "Pressure compliance",
    ReportKind.COST_PER_DISTANCE: "Cost per km and per hour",
    ReportKind.SCRAP_REASONS: "Scrap and removal reasons",
    ReportKind.BRAND_PERFORMANCE: "Brand performance",
    ReportKind.REMOVAL_FORECAST: "Tyres due for removal",
    ReportKind.PURCHASE_PLAN: "Tyre purchase plan",
}
MAX_TABLE_ROWS = 500


def _num(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _table(title: str, columns: list[str], rows: list[list[Any]], notes: list[str]) -> dict[str, Any]:
    if len(rows) > MAX_TABLE_ROWS:
        notes.append(f"{title}: showing the first {MAX_TABLE_ROWS} of {len(rows)} rows; the CSV of the full list "
                     "is on the matching page.")
        rows = rows[:MAX_TABLE_ROWS]
    return {"title": title, "columns": columns, "rows": rows}


def default_period(kind: ReportKind, as_of: date) -> tuple[date, date]:
    if kind == ReportKind.REMOVAL_FORECAST:
        return as_of, as_of + timedelta(days=90)
    if kind == ReportKind.PURCHASE_PLAN:
        return date(as_of.year, as_of.month, 1), as_of + timedelta(days=365)
    return as_of - timedelta(days=30), as_of


def _forecast_rows(forecasts: list[TyreForecast]) -> list[list[Any]]:
    return [
        [f.serial or f.tyre_id, f.vehicle_label or f.vehicle_id, f.position, f.brand, f.size, f.current_tread_mm,
         f.wear_mm_per_30_days, f.projected_removal.isoformat() if f.projected_removal else None,
         f"{f.earliest_removal.isoformat() if f.earliest_removal else '?'} to "
         f"{f.latest_removal.isoformat() if f.latest_removal else 'later'}",
         f.confidence.value, f.basis.value]
        for f in forecasts
    ]


FORECAST_COLUMNS = ["Tyre", "Vehicle", "Position", "Brand", "Size", "Tread mm", "mm / 30 days",
                    "Projected removal", "Likely range", "Confidence", "Basis"]


def build_report(
    kind: ReportKind,
    dataset: TyreDataset,
    assumptions: Assumptions,
    *,
    as_of: date,
    period_from: date | None,
    period_to: date | None,
    data_as_of: datetime | None,
    open_alerts: dict[str, int] | None = None,
) -> dict[str, Any]:
    start, end = period_from, period_to
    if start is None or end is None:
        default_start, default_end = default_period(kind, as_of)
        start, end = start or default_start, end or default_end
    forecasts = forecast_tyres(dataset, assumptions, as_of)
    currency = dataset.currency
    highlights: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    notes: list[str] = []

    if kind == ReportKind.FLEET_HEALTH:
        summary = fleet_summary(dataset, assumptions, as_of, forecasts)
        highlights = [
            {"label": "Vehicles", "value": summary.vehicles},
            {"label": "Tyres fitted", "value": summary.tyres_fitted},
            {"label": "Average tread (mm)", "value": summary.average_tread_mm},
            {"label": f"At or below {summary.min_tread_mm:g} mm", "value": summary.below_minimum},
            {"label": "In the warning band", "value": summary.in_warning_band},
            {"label": "Pressure compliance (%)", "value": summary.pressure_compliant_pct},
            {"label": "Overdue inspections", "value": summary.overdue_inspections},
            {"label": "Due in the next 30 days", "value": summary.due_next_30_days},
            {"label": f"Cost per 1000 km ({currency})", "value": summary.cost_per_1000_km},
        ]
        if open_alerts:
            highlights.append({"label": "Open alerts", "value": sum(open_alerts.values())})
        worst = sorted((f for f in forecasts if f.current_tread_mm is not None and f.readings),
                       key=lambda f: f.current_tread_mm or 0)[:50]
        tables.append(_table("Lowest tread", FORECAST_COLUMNS, _forecast_rows(worst), notes))
    elif kind == ReportKind.WEAR_RATES:
        own = [f for f in forecasts if f.wear_mm_per_30_days and f.basis.value in ("distance", "time", "fitment")]
        rates = [f.wear_mm_per_30_days for f in own if f.wear_mm_per_30_days]
        per_km = [f.wear_mm_per_1000km for f in own if f.wear_mm_per_1000km]
        highlights = [
            {"label": "Tyres with a measured wear rate", "value": len(own)},
            {"label": "Median wear (mm / 30 days)", "value": _num(statistics.median(rates), 3) if rates else None},
            {"label": "Median wear (mm / 1000 km)", "value": _num(statistics.median(per_km), 3) if per_km else None},
        ]
        ranked = sorted(own, key=lambda f: -(f.wear_mm_per_30_days or 0))
        tables.append(_table("Wear rate by tyre (fastest first)", [
            "Tyre", "Vehicle", "Position", "Brand", "Size", "Readings", "mm / 1000 km", "mm / 30 days",
            "Tread mm", "Confidence"],
            [[f.serial or f.tyre_id, f.vehicle_label or f.vehicle_id, f.position, f.brand, f.size, f.readings,
              f.wear_mm_per_1000km, f.wear_mm_per_30_days, f.current_tread_mm, f.confidence.value] for f in ranked],
            notes))
    elif kind == ReportKind.PRESSURE_COMPLIANCE:
        checks = [c for c in pressure_checks(dataset, assumptions) if start <= c.recorded_at <= end]
        judged = [c for c in checks if c.verdict != "no_target"]
        ok = sum(c.verdict == "ok" for c in judged)
        highlights = [
            {"label": "Pressure readings", "value": len(checks)},
            {"label": "Readings with a target", "value": len(judged)},
            {"label": "Compliant (%)", "value": round(100 * ok / len(judged), 1) if judged else None},
            {"label": "Under-inflated", "value": sum(c.verdict == "under" for c in judged)},
            {"label": "Over-inflated", "value": sum(c.verdict == "over" for c in judged)},
        ]
        if len(judged) < len(checks):
            notes.append(f"{len(checks) - len(judged)} readings had no target pressure; set a default target in "
                         "the assumptions to judge them.")
        per_vehicle: dict[str, list[PressureCheck]] = defaultdict(list)
        for check in judged:
            per_vehicle[check.vehicle_id or "unknown"].append(check)
        vehicle_rows = []
        for vehicle_id, items in per_vehicle.items():
            good = sum(c.verdict == "ok" for c in items)
            vehicle_rows.append([vehicle_id, len(items), good, sum(c.verdict == "under" for c in items),
                                 sum(c.verdict == "over" for c in items), round(100 * good / len(items), 1)])
        vehicle_rows.sort(key=lambda row: row[5])
        tables.append(_table("Compliance by vehicle (worst first)",
                             ["Vehicle", "Readings", "OK", "Under", "Over", "Compliant %"], vehicle_rows, notes))
        worst = sorted((c for c in judged if c.verdict != "ok"), key=lambda c: -abs(c.deviation_pct or 0))
        tables.append(_table("Readings out of tolerance",
                             ["Date", "Vehicle", "Position", "Tyre", "psi", "Target psi", "Deviation %"],
                             [[c.recorded_at.isoformat(), c.vehicle_id, c.position, c.tyre_id, c.pressure_psi,
                               c.target_psi, c.deviation_pct] for c in worst], notes))
    elif kind == ReportKind.COST_PER_DISTANCE:
        summary = fleet_summary(dataset, assumptions, as_of, forecasts)
        highlights = [
            {"label": f"Fleet cost per 1000 km ({currency})", "value": summary.cost_per_1000_km},
            {"label": f"Fleet cost per hour ({currency})", "value": summary.cost_per_hour},
        ]
        columns = ["Group", "Tyres", f"Cost ({currency})", "km", "Hours", "Per 1000 km", "Per hour"]
        for by, title in (("brand", "By brand"), ("size", "By size"), ("vehicle", "By vehicle")):
            lines = cost_per_distance(dataset, by)
            tables.append(_table(title, columns, [[line.group, line.tyres, line.cost, line.km, line.hours,
                                                   line.cost_per_1000_km, line.cost_per_hour] for line in lines], notes))
        notes.append("Cost is each tyre's purchase cost plus the costs linked to it; distance is the km it ran "
                     "(total, removal minus fitment, or the vehicle's odometer now minus fitment).")
    elif kind == ReportKind.SCRAP_REASONS:
        rows = scrap_reasons(dataset, start, end)
        highlights = [{"label": "Removals in the period", "value": sum(row.removals for row in rows)}]
        if rows:
            highlights.append({"label": "Top reason", "value": f"{rows[0].reason} ({rows[0].share_pct:g}%)"})
        tables.append(_table("Reasons", ["Reason", "Removals", "Share %", "Median km", "Median tread mm"],
                             [[r.reason, r.removals, r.share_pct, r.median_km, r.median_tread_mm] for r in rows], notes))
    elif kind == ReportKind.BRAND_PERFORMANCE:
        rows = brand_performance(dataset, assumptions, forecasts)
        highlights = [{"label": "Brand and size groups", "value": len(rows)}]
        cheapest = next((row for row in rows if row.cost_per_1000_km is not None), None)
        if cheapest:
            highlights.append({"label": f"Lowest cost per 1000 km ({currency})",
                               "value": f"{cheapest.brand} {cheapest.size}: {cheapest.cost_per_1000_km:g}"})
        tables.append(_table("Brands (lowest cost per 1000 km first)",
                             ["Brand", "Size", "Tyres", "Removed", "mm / 1000 km", "mm / 30 days",
                              "Projected life km", "Median km at removal", "Per 1000 km"],
                             [[r.brand, r.size, r.tyres, r.removed, r.median_wear_mm_per_1000km,
                               r.median_wear_mm_per_30_days, r.projected_life_km, r.median_km_at_removal,
                               r.cost_per_1000_km] for r in rows], notes))
    elif kind == ReportKind.REMOVAL_FORECAST:
        due = [f for f in forecasts if f.projected_removal and f.projected_removal <= end]
        by_month: dict[str, int] = defaultdict(int)
        for item in due:
            when = max(item.projected_removal, start) if item.projected_removal else start
            by_month[f"{when.year:04d}-{when.month:02d}"] += 1
        highlights = [{"label": f"Due by {end.isoformat()}", "value": len(due)}] + [
            {"label": month, "value": count} for month, count in sorted(by_month.items())
        ]
        tables.append(_table("Tyres due", FORECAST_COLUMNS, _forecast_rows(due), notes))
    elif kind == ReportKind.PURCHASE_PLAN:
        months = max(1, min(36, (end.year - start.year) * 12 + end.month - start.month + 1))
        plan = purchase_plan(dataset, assumptions, as_of, forecasts, months=months)
        highlights = [
            {"label": "Tyres to buy", "value": plan.total_tyres},
            {"label": f"Estimated cost ({currency})", "value": plan.total_cost},
            {"label": "Rows without a price", "value": plan.unpriced_rows},
        ]
        tables.append(_table("Plan by month, size and brand",
                             ["Month", "Size", "Brand", "Due", "Safety", "From stock", "To buy",
                              f"Unit price ({currency})", "Price from", f"Cost ({currency})"],
                             [[r.month, r.size, r.brand, r.due, r.safety, r.from_stock, r.to_buy, r.unit_price,
                               r.price_source, r.estimated_cost] for r in plan.rows], notes))
        if plan.skipped_no_size:
            notes.append(f"{plan.skipped_no_size} tyres due have no size and are not in the plan.")
    notes.append(
        f"Minimum tread {assumptions.min_tread_mm:g} mm; pressure tolerance {assumptions.pressure_tolerance_pct:g}%; "
        f"inspections due every {assumptions.inspection_interval_days} days."
    )
    return {
        "kind": kind.value,
        "title": TITLES[kind],
        "as_of": as_of.isoformat(),
        "data_as_of": data_as_of.isoformat() if data_as_of else None,
        "period_from": start.isoformat(),
        "period_to": end.isoformat(),
        "currency": currency,
        "highlights": highlights,
        "tables": tables,
        "notes": notes,
    }


def summary_text(content: dict[str, Any]) -> str:
    """One paragraph with the report's numbers: for chat answers and delivery bodies."""
    parts = [f"{item['label']}: {_cell(item['value'])}" for item in content.get("highlights", [])]
    return f"{content.get('title', 'Report')} ({content.get('period_from')} to {content.get('period_to')}). " + "; ".join(parts) + "."


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: Any) -> str:
    text = _cell(value)
    # Values come from the owner's database: never let a spreadsheet run them as formulas.
    if text.startswith(_FORMULA_START) and not _is_number(text):
        return "'" + text
    return text


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}" if abs(value) < 1e15 else str(value)
    return str(value)


def to_csv(content: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_csv_cell(content.get("title"))])
    writer.writerow(["Period", content.get("period_from"), content.get("period_to")])
    writer.writerow(["Data as of", content.get("data_as_of") or ""])
    writer.writerow([])
    for item in content.get("highlights", []):
        writer.writerow([_csv_cell(item["label"]), _csv_cell(item["value"])])
    for table in content.get("tables", []):
        writer.writerow([])
        writer.writerow([_csv_cell(table["title"])])
        writer.writerow([_csv_cell(column) for column in table["columns"]])
        for row in table["rows"]:
            writer.writerow([_csv_cell(value) for value in row])
    if content.get("notes"):
        writer.writerow([])
        for note in content["notes"]:
            writer.writerow([_csv_cell(note)])
    return buffer.getvalue()


def to_pdf(content: dict[str, Any], workspace_label: str = "ANUM tyre planning") -> bytes:
    document = PdfDocument(footer=f"{workspace_label} - {content.get('title')} - generated {content.get('as_of')}")
    document.heading(str(content.get("title", "Report")))
    document.paragraph(
        f"Period {content.get('period_from')} to {content.get('period_to')}. Data as of "
        f"{content.get('data_as_of') or 'unknown'}. Amounts in {content.get('currency')}."
    )
    for item in content.get("highlights", []):
        document.paragraph(f"{item['label']}: {_cell(item['value']) or '-'}", size=10)
    for table in content.get("tables", []):
        document.table(str(table["title"]), table["columns"], table["rows"])
    for note in content.get("notes", []):
        document.paragraph(str(note), size=8)
    return document.render()
