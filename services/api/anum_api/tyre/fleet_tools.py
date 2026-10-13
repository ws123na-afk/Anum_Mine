"""Fleet operations tools for tasks, chat and voice (docs/tyre-planning.md#assistant).

Six read-only, low-risk tools over the workspace's synced TyrePulse data, with bounded
on-demand reads of the tables a sync never copies (``live.py``):

* ``tyre_vehicles`` - every vehicle with fitted tyres, last inspection, tyres due and open
  work orders; ``inspected`` "yes" / "no" for the last 90 days.
* ``tyre_vehicle_detail`` - one vehicle: its tyres, the latest reading per wheel position,
  schedules, open work orders and checklists.
* ``tyre_inspections`` - inspections with filters (dates, site, vehicle, status): how many,
  of how many vehicles, which vehicles; plus the inspection schedule (upcoming, overdue).
* ``tyre_checklists`` - workshop checklist submissions with filters; failed ones.
* ``tyre_work_orders`` - open (or all) work orders by vehicle, site, type or period; one
  work order's line items and parts read on demand.
* ``tyre_maintenance_costs`` - work order costs by vehicle, site or work type, plus parts
  issued (read on demand for one vehicle or up to 92 days).

The same checks as the other tyre tools apply: ``tyre:read``, tool policy, audit, and
answers carry the numbers and the sync time. Nothing is written anywhere.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from typing import Any

from ..agent_tools import ToolCall, ToolResult
from ..schemas import TenantContext, utc_now
from . import fleet, live, service
from .live_tables import LIVE_TABLES
from .model import Entity
from .sources import TyreSourceError

VEHICLES = "tyre_vehicles"
VEHICLE_DETAIL = "tyre_vehicle_detail"
INSPECTIONS = "tyre_inspections"
CHECKLISTS = "tyre_checklists"
WORK_ORDERS = "tyre_work_orders"
COSTS = "tyre_maintenance_costs"
FLEET_TOOL_NAMES = (VEHICLES, VEHICLE_DETAIL, INSPECTIONS, CHECKLISTS, WORK_ORDERS, COSTS)
LISTED = 15
MAX_PARTS_DAYS = 92

DEFINITIONS = {
    VEHICLES: "List fleet vehicles with fitted tyres, last inspection, tyres due and open work orders "
              "(site, inspected: yes|no); read-only.",
    VEHICLE_DETAIL: "One vehicle's tyres, latest inspection readings per wheel position, schedules, open work "
                    "orders and checklists (vehicle); read-only.",
    INSPECTIONS: "Inspections with filters (date_from, date_to, site, vehicle, status): count, vehicles inspected, "
                 "and the inspection schedule; read-only.",
    CHECKLISTS: "Workshop checklist submissions with filters (date_from, date_to, site, vehicle, failed); read-only.",
    WORK_ORDERS: "Work orders by vehicle, site, type or period (open: true for open only), or one work order's "
                 "line items and parts (work_order); read-only.",
    COSTS: "Maintenance cost totals from work orders by vehicle, site or work type (group_by), plus parts issued "
           "(date_from, date_to, site, vehicle); read-only.",
}


def _failed(summary: str) -> ToolResult:
    return ToolResult(status="failed", summary=summary, audit_metadata={"outcome": "unavailable"})


def _load(context: TenantContext) -> tuple[service.Loaded | None, ToolResult | None]:
    try:
        return service.load(context), None
    except service.NotConnectedError:
        return None, _failed("Tyre data is not connected in this workspace yet: an owner can connect it in Tyre planning.")
    except service.NotSyncedError:
        return None, _failed("Tyre data is connected but has not been synced yet: sync it in Tyre planning.")


def _text(arguments: dict[str, Any], name: str, limit: int = 100) -> str | None:
    value = arguments.get(name)
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = " ".join(str(value).split())[:limit]
    return text or None


def _day(arguments: dict[str, Any], name: str) -> date | None:
    value = _text(arguments, name, 10)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _flag(arguments: dict[str, Any], name: str) -> bool | None:
    value = arguments.get(name)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "yes", "1"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "no", "0"}:
        return False
    return None


def _synced(loaded: service.Loaded) -> str:
    return f"data synced {loaded.snapshot.stats.synced_at:%Y-%m-%d %H:%M} UTC"


def _period(date_from: date | None, date_to: date | None) -> str:
    if date_from and date_to:
        return f"between {date_from.isoformat()} and {date_to.isoformat()}"
    if date_from:
        return f"since {date_from.isoformat()}"
    if date_to:
        return f"up to {date_to.isoformat()}"
    return "in the synced data"


def _vehicles(loaded: service.Loaded, arguments: dict[str, Any]) -> tuple[set[str] | None, str | None, ToolResult | None]:
    """The vehicle ids a ``vehicle`` argument names, its display text, or a failure."""
    wanted = _text(arguments, "vehicle", 60)
    if not wanted:
        return None, None, None
    found = fleet.find_vehicles(loaded.snapshot.dataset, wanted)
    if not found:
        return None, wanted, _failed(f"No vehicle matches '{wanted}' in the synced tyre data ({_synced(loaded)}).")
    return {vehicle.id.strip().lower() for vehicle in found}, wanted, None


def _money(value: float | None, currency: str) -> str:
    return "unknown" if value is None else f"{value:,.0f} {currency}"


async def vehicles_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    try:
        loaded, forecasts = service.forecasts_for(context)
    except (service.NotConnectedError, service.NotSyncedError):
        _, problem = _load(context)
        return problem or _failed("Tyre data is not available.")
    args = call.arguments
    today = utc_now().date()
    inspected = _text(args, "inspected", 3)
    inspected = inspected.lower() if inspected and inspected.lower() in {"yes", "no"} else None
    rows = fleet.vehicle_rows(loaded.snapshot.dataset, forecasts, today, loaded.connection.assumptions.min_tread_mm,
                              site=_text(args, "site"), inspected=inspected)
    every = fleet.vehicle_rows(loaded.snapshot.dataset, forecasts, today, loaded.connection.assumptions.min_tread_mm,
                               site=_text(args, "site"))
    seen = sum(1 for row in every if row.last_inspected and row.last_inspected >= today - timedelta(days=90))
    shown = rows if inspected else sorted(rows, key=lambda r: (-r.open_work_orders - r.low_tread_tyres, r.vehicle_id))
    listed = "; ".join(
        f"{row.label}: {row.tyres_fitted} tyres, last inspected "
        f"{row.last_inspected.isoformat() if row.last_inspected else 'never'}"
        + (f", {row.low_tread_tyres} at minimum tread" if row.low_tread_tyres else "")
        + (f", {row.open_work_orders} open work orders" if row.open_work_orders else "")
        for row in shown[:LISTED]
    )
    scope = f" at {_text(args, 'site')}" if _text(args, "site") else ""
    which = {"yes": "inspected in the last 90 days", "no": "not inspected in the last 90 days"}.get(inspected or "", "")
    text = (
        f"{len(every)} vehicles{scope} ({_synced(loaded)}): {seen} inspected in the last 90 days, "
        f"{len(every) - seen} not. "
        + (f"{len(rows)} {which}" + (": " if rows else ".") if which else "")
        + (listed + (f"; and {len(shown) - LISTED} more." if len(shown) > LISTED else "."))
    ).strip()
    return ToolResult(
        status="succeeded", summary=text,
        output={"total": len(rows), "vehicles": len(every), "inspected_last_90_days": seen,
                "items": [row.model_dump(mode="json") for row in shown[:100]]},
        audit_metadata={"outcome": "answered", "total": len(rows)},
    )


async def vehicle_detail_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    try:
        loaded, forecasts = service.forecasts_for(context)
    except (service.NotConnectedError, service.NotSyncedError):
        _, problem = _load(context)
        return problem or _failed("Tyre data is not available.")
    wanted = _text(call.arguments, "vehicle", 60)
    if not wanted:
        return _failed("Name the vehicle (asset number, registration or fleet number).")
    found = fleet.find_vehicles(loaded.snapshot.dataset, wanted)
    if not found:
        return _failed(f"No vehicle matches '{wanted}' in the synced tyre data ({_synced(loaded)}).")
    if len(found) > 1:
        names = ", ".join(fleet.vehicle_label(v, v.id) for v in found[:10])
        return ToolResult(status="succeeded", summary=f"{len(found)} vehicles match '{wanted}': {names}. Which one?",
                          output={"matches": [v.model_dump() for v in found[:20]]},
                          audit_metadata={"outcome": "ambiguous", "total": len(found)})
    today = utc_now().date()
    detail = fleet.vehicle_detail(loaded.snapshot.dataset, found[0], forecasts, today)
    visit = detail.last_inspection
    readings = "; ".join(
        f"{r.position or '?'}: " + ", ".join(p for p in (
            f"{r.pressure_psi:g} psi" if r.pressure_psi is not None else None,
            f"{r.tread_mm:g} mm" if r.tread_mm is not None else None) if p)
        for r in detail.latest_readings[:24]
    )
    tyres = "; ".join(
        f"{t.position or '?'} {t.serial or t.tyre_id} {t.brand or ''} {t.size or ''}".strip()
        + (f" ({t.current_tread_mm:g} mm)" if t.current_tread_mm is not None else "")
        for t in detail.tyres[:24]
    )
    text = (
        f"{detail.label} ({_synced(loaded)}): {len(detail.tyres)} tyres fitted"
        + (f", site {detail.site}" if detail.site else "")
        + (f", odometer {detail.odometer_km:,.0f} km" if detail.odometer_km else "") + ". "
        + (f"Last inspection {visit.inspected_at.isoformat()}" + (f" ({visit.status})" if visit and visit.status else "")
           + f", {detail.inspections_last_90_days} in the last 90 days. " if visit else "No inspection recorded. ")
        + (f"Latest readings ({detail.latest_readings[0].inspected_at.isoformat()}): {readings}. " if readings else "")
        + (f"Tyres: {tyres}. " if tyres else "")
        + f"{len(detail.open_work_orders)} open work orders, {len(detail.schedules)} inspections scheduled, "
        f"{len(detail.recent_checklists)} recent checklists."
    )
    return ToolResult(status="succeeded", summary=text, output={"vehicle": detail.model_dump(mode="json")},
                      audit_metadata={"outcome": "answered", "tyres": len(detail.tyres)})


async def inspections_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    loaded, problem = _load(context)
    if problem or loaded is None:
        return problem or _failed("Tyre data is not available.")
    args = call.arguments
    vehicle_ids, _, missing = _vehicles(loaded, args)
    if missing:
        return missing
    date_from, date_to, site = _day(args, "date_from"), _day(args, "date_to"), _text(args, "site")
    today = utc_now().date()
    found = fleet.inspections_list(loaded.snapshot.dataset, date_from=date_from, date_to=date_to, site=site,
                                   vehicle_ids=vehicle_ids, status=_text(args, "status"))
    schedules, schedule_counts = fleet.schedules_list(loaded.snapshot.dataset, today, site=site, vehicle_ids=vehicle_ids)
    overdue = [s for s in schedules if fleet.schedule_state(s, today) == "overdue"]
    upcoming = [s for s in schedules if fleet.schedule_state(s, today) == "upcoming"]
    sites = ", ".join(f"{name} {count}" for name, count in list(found.by_site.items())[:8])
    vehicles = ", ".join(found.vehicles_listed[:30])
    text = (
        f"{found.total} inspections of {found.vehicles} vehicles {_period(date_from, date_to)}"
        + (f" at {site}" if site else "") + f" ({_synced(loaded)})"
        + (f". By site: {sites}" if sites and not site else "")
        + (f". Vehicles: {vehicles}" + (f" and {found.vehicles - 30} more" if found.vehicles > 30 else "") if vehicles else "")
        + f". Schedule: {len(upcoming)} upcoming"
        + (f" (next {upcoming[0].scheduled_for.isoformat()} for {upcoming[0].vehicle_id or 'a vehicle'})" if upcoming else "")
        + f", {len(overdue)} overdue, {schedule_counts.get('completed', 0)} completed."
    )
    return ToolResult(
        status="succeeded", summary=text,
        output={**found.model_dump(mode="json"), "schedule": schedule_counts,
                "overdue_schedules": [s.model_dump(mode="json") for s in overdue[:50]],
                "upcoming_schedules": [s.model_dump(mode="json") for s in upcoming[:50]]},
        audit_metadata={"outcome": "answered", "total": found.total},
    )


async def checklists_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    loaded, problem = _load(context)
    if problem or loaded is None:
        return problem or _failed("Tyre data is not available.")
    args = call.arguments
    vehicle_ids, _, missing = _vehicles(loaded, args)
    if missing:
        return missing
    date_from, date_to = _day(args, "date_from"), _day(args, "date_to")
    items = fleet.checklists_list(loaded.snapshot.dataset, date_from=date_from, date_to=date_to,
                                  site=_text(args, "site"), vehicle_ids=vehicle_ids,
                                  only_failed=bool(_flag(args, "failed")), template=_text(args, "template"))
    templates = Counter(item.template or "untitled" for item in items)
    failed_count = sum(1 for item in items if fleet.failed(item))
    scores = [item.score_pct for item in items if item.score_pct is not None]
    listed = "; ".join(
        f"{item.submitted_at.isoformat()} {item.template or item.title or 'checklist'}"
        + (f" for {item.vehicle_id}" if item.vehicle_id else "") + (f" at {item.site}" if item.site else "")
        + (f", score {item.score_pct:g}%" if item.score_pct is not None else "")
        + (", failed" if fleet.failed(item) else "")
        for item in items[:LISTED]
    )
    text = (
        f"{len(items)} {'failed ' if _flag(args, 'failed') else ''}workshop checklists {_period(date_from, date_to)} "
        f"({_synced(loaded)})"
        + (f": {failed_count} failed" if items and not _flag(args, "failed") else "")
        + (f", average score {sum(scores) / len(scores):.0f}%" if scores else "")
        + (". Templates: " + ", ".join(f"{name} {count}" for name, count in templates.most_common(6)) if items else "")
        + (". " + listed if listed else ".")
    )
    return ToolResult(
        status="succeeded", summary=text,
        output={"total": len(items), "failed": failed_count, "by_template": dict(templates.most_common()),
                "items": [item.model_dump(mode="json") for item in items[:100]]},
        audit_metadata={"outcome": "answered", "total": len(items)},
    )


def _window_note(loaded: service.Loaded) -> str:
    mapping = loaded.connection.mapping
    if mapping is None:
        return ""
    work_orders = mapping.entities.get(Entity.WORK_ORDERS)
    if work_orders and work_orders.window_days:
        return f" (work orders opened in the last {work_orders.window_days} days are synced)"
    return ""


async def work_orders_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    loaded, problem = _load(context)
    if problem or loaded is None:
        return problem or _failed("Tyre data is not available.")
    args = call.arguments
    number = _text(args, "work_order", 40)
    if number:
        return await _one_work_order(context, loaded, number)
    vehicle_ids, _, missing = _vehicles(loaded, args)
    if missing:
        return missing
    date_from, date_to = _day(args, "date_from"), _day(args, "date_to")
    open_only = _flag(args, "open")
    if open_only is None:
        open_only = date_from is None and date_to is None
    items = fleet.work_orders_list(loaded.snapshot.dataset, open_only=open_only, date_from=date_from, date_to=date_to,
                                   site=_text(args, "site"), vehicle_ids=vehicle_ids, work_type=_text(args, "work_type"))
    currency = loaded.snapshot.dataset.currency
    by_type = Counter(item.work_type or "unknown" for item in items)
    by_vehicle = Counter(item.vehicle_id or "unknown" for item in items)
    total = sum(fleet.order_cost(item) or 0 for item in items)
    listed = "; ".join(
        f"{item.id} {item.vehicle_id or ''} opened {item.opened_at.isoformat()}"
        + (f", {item.work_type}" if item.work_type else "") + (f", {item.status}" if item.status else "")
        for item in items[:LISTED]
    )
    text = (
        f"{len(items)} {'open ' if open_only else ''}work orders {_period(date_from, date_to)}"
        + (f" at {_text(args, 'site')}" if _text(args, "site") else "") + f" ({_synced(loaded)}{_window_note(loaded)})"
        + (f", recorded cost {_money(total, currency)}" if items and total else "")
        + (". Most for: " + ", ".join(f"{name} {count}" for name, count in by_vehicle.most_common(5)) if items else "")
        + (". By type: " + ", ".join(f"{name} {count}" for name, count in by_type.most_common(5)) if items else "")
        + (". " + listed if listed else ".")
    )
    return ToolResult(
        status="succeeded", summary=text,
        output={"total": len(items), "open_only": open_only, "currency": currency, "total_cost": round(total, 2),
                "by_type": dict(by_type.most_common(20)), "by_vehicle": dict(by_vehicle.most_common(20)),
                "items": [item.model_dump(mode="json") for item in items[:100]]},
        audit_metadata={"outcome": "answered", "total": len(items)},
    )


async def _one_work_order(context: TenantContext, loaded: service.Loaded, number: str) -> ToolResult:
    synced = next((item for item in loaded.snapshot.dataset.work_orders if item.id.lower() == number.lower()), None)
    lines: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    notes: list[str] = []
    for table, target in (("work_order_line_items", lines), ("parts_consumption", parts)):
        if not live.available(context, table):
            continue
        try:
            result = await live.read(context, table, work_order=synced.id if synced else number, limit=200)
        except TyreSourceError as exc:
            notes.append(exc.message)
            continue
        target.extend(result.rows)
    if synced is None and not lines and not parts:
        return _failed(f"No work order {number} in the synced data or in TyrePulse's line items and parts"
                       + (f" ({'; '.join(notes)})" if notes else "") + ".")
    currency = loaded.snapshot.dataset.currency
    part_cost = sum(float(row.get("line_cost") or 0) for row in parts if isinstance(row.get("line_cost"), (int, float)))
    tasks = "; ".join(" ".join(str(row.get(c)) for c in ("task", "action", "detail") if row.get(c)) for row in lines[:LISTED])
    head = (
        f"Work order {synced.id}: {synced.vehicle_id or 'no vehicle'}, opened {synced.opened_at.isoformat()}"
        + (f", {synced.status}" if synced.status else "") + (f", {synced.work_type}" if synced.work_type else "")
        + (f", total {_money(fleet.order_cost(synced), currency)}" if fleet.order_cost(synced) is not None else "")
        if synced else f"Work order {number} (not in the synced recent work orders)"
    )
    text = (
        f"{head}. {len(lines)} line items" + (f": {tasks}" if tasks else "")
        + f". {len(parts)} parts issued" + (f", {_money(part_cost, currency)}" if parts else "") + "."
        + (" " + " ".join(notes) if notes else "")
    )
    return ToolResult(
        status="succeeded", summary=text,
        output={"work_order": synced.model_dump(mode="json") if synced else {"id": number},
                "line_items": lines[:200], "parts": parts[:200], "currency": currency},
        audit_metadata={"outcome": "answered", "line_items": len(lines), "parts": len(parts)},
    )


async def costs_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    loaded, problem = _load(context)
    if problem or loaded is None:
        return problem or _failed("Tyre data is not available.")
    args = call.arguments
    vehicle_ids, _, missing = _vehicles(loaded, args)
    if missing:
        return missing
    today = utc_now().date()
    date_to = _day(args, "date_to") or today
    date_from = _day(args, "date_from") or date_to - timedelta(days=30)
    if date_from > date_to:
        return _failed("date_from is after date_to.")
    site = _text(args, "site")
    group_by = _text(args, "group_by", 10) or ("vehicle" if not vehicle_ids else "work_type")
    group_by = group_by if group_by in {"vehicle", "site", "work_type"} else "vehicle"
    orders = fleet.work_orders_list(loaded.snapshot.dataset, date_from=date_from, date_to=date_to, site=site,
                                    vehicle_ids=vehicle_ids)
    groups = fleet.work_order_costs(orders, group_by)
    currency = loaded.snapshot.dataset.currency
    total = sum(group.total_cost for group in groups)
    parts_note = ""
    parts_output: dict[str, Any] | None = None
    wants_parts = _flag(args, "include_parts")
    one_vehicle = vehicle_ids is not None and len(vehicle_ids) == 1
    if wants_parts is not False and "parts_consumption" in LIVE_TABLES and live.available(context, "parts_consumption") \
            and (one_vehicle or (date_to - date_from).days <= MAX_PARTS_DAYS):
        try:
            result = await live.read(context, "parts_consumption", vehicle_id=next(iter(_ids(loaded, vehicle_ids)), None)
                                     if one_vehicle else None, site=None, date_from=date_from, date_to=date_to,
                                     limit=live.MAX_LIVE_ROWS)
            categories: Counter[str] = Counter()
            for row in result.rows:
                amount = row.get("line_cost")
                if isinstance(amount, (int, float)):
                    categories[str(row.get("cost_category") or "other")] += float(amount)
            parts_total = sum(categories.values())
            parts_output = {"rows": len(result.rows), "cut": result.cut, "total": round(parts_total, 2),
                            "by_category": {k: round(v, 2) for k, v in categories.most_common()}}
            parts_note = (
                f" Parts issued in the same period: {len(result.rows)} lines, {_money(parts_total, currency)}"
                + (" (" + ", ".join(f"{k} {_money(v, currency)}" for k, v in categories.most_common(4)) + ")" if categories else "")
                + (f"; only the newest {len(result.rows)} lines were read" if result.cut else "") + "."
            )
        except TyreSourceError as exc:
            parts_note = f" Parts issued could not be read: {exc.message}"
    elif wants_parts is not False:
        parts_note = f" Parts issued are read for one vehicle or up to {MAX_PARTS_DAYS} days."
    listed = "; ".join(f"{group.key}: {_money(group.total_cost, currency)} over {group.work_orders} work orders"
                       for group in groups[:LISTED])
    text = (
        f"Work order costs {_period(date_from, date_to)}" + (f" at {site}" if site else "")
        + f" ({_synced(loaded)}{_window_note(loaded)}): {_money(total, currency)} over {len(orders)} work orders"
        + (f", by {group_by.replace('_', ' ')}: {listed}" if listed else "") + "." + parts_note
    )
    return ToolResult(
        status="succeeded", summary=text,
        output={"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "currency": currency,
                "group_by": group_by, "work_orders": len(orders), "total_cost": round(total, 2),
                "groups": [group.model_dump() for group in groups[:100]], "parts": parts_output},
        audit_metadata={"outcome": "answered", "work_orders": len(orders)},
    )


def _ids(loaded: service.Loaded, keys: set[str] | None) -> list[str]:
    """The source's own spelling of the vehicle ids (the live filter is exact)."""
    if not keys:
        return []
    found = [v.id for v in loaded.snapshot.dataset.vehicles if v.id.strip().lower() in keys]
    return found or sorted(keys)


HANDLERS = {
    VEHICLES: vehicles_tool,
    VEHICLE_DETAIL: vehicle_detail_tool,
    INSPECTIONS: inspections_tool,
    CHECKLISTS: checklists_tool,
    WORK_ORDERS: work_orders_tool,
    COSTS: costs_tool,
}


# --- arguments from the user's own words ------------------------------------------------------

_VEHICLE = re.compile(
    r"\b(?:vehicle|truck|asset|unit|trailer|tanker|bus|mixer|pump|plate|registration)\s*(?:no\.?|number|#|:)?\s*"
    r"([A-Za-z]*[-/]?\d[A-Za-z0-9\-/]*)", re.IGNORECASE)
_SITE = re.compile(r"(?<!by )\b(?:site|depot|branch)\s+(?:of\s+|is\s+|:\s*)?([A-Za-z][\w\-]{1,40})", re.IGNORECASE)
_NOT_A_SITE = {"the", "a", "an", "is", "in", "for", "and", "orders", "order", "this", "last", "next", "with", "today",
               "yesterday", "costs", "cost", "inspections", "vehicles", "checklists"}
_WORK_ORDER = re.compile(r"\b(?:work\s*order|job\s*card)\s*(?:no\.?|number|#|:)?\s*([A-Za-z]*[-/]?\d[A-Za-z0-9\-/]*)"
                         r"|\b(WO[-/]?\d[A-Za-z0-9\-/]*)", re.IGNORECASE)
_ISO = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def parse_period(text: str, today: date) -> tuple[date | None, date | None]:
    """A period named in the text: dates, "today", "yesterday", "this week / month", "last N days"."""
    lowered = text.lower()
    dates = sorted(date.fromisoformat(item) for item in _ISO.findall(text))
    if len(dates) >= 2:
        return dates[0], dates[-1]
    if len(dates) == 1:
        return dates[0], dates[0]
    match = re.search(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(day|days|week|weeks|month|months)\b", lowered)
    if match:
        count = int(match.group(1))
        days = count * {"d": 1, "w": 7, "m": 30}[match.group(2)[0]]
        return today - timedelta(days=days), today
    if "today" in lowered or "اليوم" in text:
        return today, today
    if "yesterday" in lowered or "أمس" in text or "امس" in text:
        return today - timedelta(days=1), today - timedelta(days=1)
    if re.search(r"\b(this|current) week\b", lowered) or "هذا الأسبوع" in text:
        return today - timedelta(days=today.weekday()), today
    if re.search(r"\blast week\b", lowered) or "الأسبوع الماضي" in text:
        return today - timedelta(days=7), today
    if re.search(r"\b(this|current) month\b", lowered) or "هذا الشهر" in text:
        return today.replace(day=1), today
    if re.search(r"\blast month\b", lowered) or "الشهر الماضي" in text:
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if re.search(r"\b(this|current) year\b", lowered):
        return today.replace(month=1, day=1), today
    return None, None


def _common(text: str, today: date) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    date_from, date_to = parse_period(text, today)
    if date_from:
        arguments["date_from"] = date_from.isoformat()
    if date_to:
        arguments["date_to"] = date_to.isoformat()
    vehicle = _VEHICLE.search(text)
    if vehicle:
        arguments["vehicle"] = vehicle.group(1)
    site = _SITE.search(text)
    if site and site.group(1).lower() not in _NOT_A_SITE:
        arguments["site"] = site.group(1)
    return arguments


_WORK_ORDER_WORDS = re.compile(r"\b(work\s*orders?|job\s*cards?|workshop|repairs?|breakdowns?|maintenance)\b"
                               r"|صيانة|ورشة|أمر عمل|أوامر العمل", re.IGNORECASE)
_COST_WORDS = re.compile(r"\b(cost|costs|spend|spent|spending|expense|expenses|parts)\b|تكلفة|تكاليف|مصاريف",
                         re.IGNORECASE)


def choose_fleet_call(text: str, today: date) -> ToolCall | None:
    """A fleet operations tool for the question, or None when it is about tyres themselves."""
    lowered = text.lower()
    number = _WORK_ORDER.search(text)
    if number:
        return ToolCall(name=WORK_ORDERS, arguments={"work_order": number.group(1) or number.group(2)})
    arguments = _common(text, today)
    if re.search(r"\b(vehicles|trucks|assets|units)\b|المركبات|الشاحنات", lowered) and re.search(
        r"\b(not|never|without|no)\b.{0,30}\binspect", lowered
    ):
        return ToolCall(name=VEHICLES, arguments={"inspected": "no", **{k: v for k, v in arguments.items() if k == "site"}})
    if _COST_WORDS.search(text) and _WORK_ORDER_WORDS.search(text):
        group = re.search(r"\bby\s+(vehicle|site|type|work type)\b", lowered)
        if group:
            arguments["group_by"] = "work_type" if "type" in group.group(1) else group.group(1)
        return ToolCall(name=COSTS, arguments=arguments)
    if _WORK_ORDER_WORDS.search(text):
        if re.search(r"\bopen|pending|outstanding\b|مفتوح", lowered):
            arguments["open"] = True
        return ToolCall(name=WORK_ORDERS, arguments=arguments)
    if re.search(r"\bcheck\s*lists?\b|قائمة الفحص|قوائم الفحص", lowered):
        if re.search(r"\bfail\w*|rejected\b|راسب|فشل", lowered):
            arguments["failed"] = True
        return ToolCall(name=CHECKLISTS, arguments=arguments)
    if re.search(r"\bschedul\w*", lowered) or (
        re.search(r"\binspect\w*|فحص|الفحوصات|تفتيش", lowered) and not re.search(r"\b(alerts?|overdue)\b", lowered)
    ):
        return ToolCall(name=INSPECTIONS, arguments=arguments)
    if re.search(r"\b(report|summary|overview|health)\b|تقرير|ملخص", lowered):
        return None  # the fleet summary answers report-style questions as a whole
    if _COST_WORDS.search(text) and re.search(r"\b(vehicles?|trucks?|assets?)\b|المركبات", lowered):
        return ToolCall(name=COSTS, arguments={**arguments, "group_by": "vehicle"})
    if "vehicle" in arguments and re.search(r"\b(vehicle|truck|asset|unit|trailer|tanker|bus|mixer|pump)\b", lowered):
        return ToolCall(name=VEHICLE_DETAIL, arguments={"vehicle": arguments["vehicle"]})
    if re.search(r"\b(vehicles|trucks|fleet list|assets|units)\b|المركبات|الشاحنات", lowered):
        call_arguments: dict[str, Any] = {key: arguments[key] for key in ("site",) if key in arguments}
        if re.search(r"\b(not|never|without)\b.*\binspect", lowered):
            call_arguments["inspected"] = "no"
        return ToolCall(name=VEHICLES, arguments=call_arguments)
    return None
