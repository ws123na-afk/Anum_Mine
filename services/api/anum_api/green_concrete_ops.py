"""The Green Concrete read tools (docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md#tools).

Seven low risk, read only tools, one per role skill (``green_concrete_kit.TOOLS``). Each returns sections; every
section has a state, so an agent says "Not connected" instead of a figure it did not read:

* ``ok``: the data is there and the summary holds its figures.
* ``empty``: the source is connected and holds nothing for this question.
* ``not_connected`` / ``not_synced``: the source is not connected (or never synced) in this workspace.
* ``no_access``: the person the run acts for lacks the permission that module needs.
* ``failed``: the read failed; the summary says what failed.

The figures are computed by pure functions over the synced fleet data (``TyreDataset``), the tasks, the Green
Concrete seats (each person's own working hours) and the workspace calendar, read in the caller's tenant
context under RLS. The handlers run every database read in a worker thread, never on the event loop. Nothing is
written, sent or fetched from outside ANUM. Results go to the model as labeled, untrusted tool output and the
``tool.call`` audit records only the section states.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from .authorization import Permission
from .dependencies import has_permission
from .green_concrete_kit import TOOL_BY_NAME, TOOLS
from .schemas import RiskLevel, TaskStatus, TenantContext, utc_now

logger = logging.getLogger("anum.green_concrete")

GC_BUILDING = "gc-green-concrete"
OPEN_TASK = frozenset({TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL})
STALE_SYNC_HOURS = 24
LISTED = 10
BREAKDOWN_WORDS = ("breakdown", "break down", "عطل")

Section = dict[str, Any]


def section(section_id: str, state: str, summary: str, data: Mapping[str, Any] | None = None) -> Section:
    return {"id": section_id, "state": state, "summary": summary, "data": dict(data or {})}


NOT_CONNECTED_TYRE = "Fleet data is not connected: an owner connects Tyre Pulse in Tyre planning."
NOT_SYNCED_TYRE = "Fleet data is connected but never synced: sync it in Tyre planning."
NO_ACCESS = "The role of the person this runs for cannot read {what}."


# --------------------------------------------------------------------------- pure: fleet data


def _key(value: str | None) -> str:
    return (value or "").strip().lower()


def _in(day: date | None, start: date, end: date) -> bool:
    return day is not None and start <= day <= end


def period(arguments: Mapping[str, Any], today: date, default_days: int = 30) -> tuple[date, date]:
    def parse(name: str) -> date | None:
        raw = arguments.get(name)
        try:
            return date.fromisoformat(str(raw)[:10]) if raw else None
        except ValueError:
            return None

    start, end = parse("date_from"), parse("date_to")
    end = end or today
    start = start or end - timedelta(days=default_days - 1)
    if start > end:
        start, end = end, start
    return start, end


def use_section(dataset: Any, start: date, end: date) -> Section:
    """Distance and hours per vehicle in the period, only from odometer and hour readings."""
    if not dataset.odometer_readings and not dataset.hour_readings:
        return section("use", "not_connected", "No odometer or hour readings are in the fleet sync, so use per vehicle is Not connected.")
    km: dict[str, list[float]] = {}
    hours: dict[str, list[float]] = {}
    for r in dataset.odometer_readings:
        if _in(r.read_at, start, end):
            km.setdefault(r.vehicle_id, []).append(r.odometer_km)
    for r in dataset.hour_readings:
        if _in(r.read_at, start, end):
            hours.setdefault(r.vehicle_id, []).append(r.hours)
    dist = {v: round(max(x) - min(x), 1) for v, x in km.items() if len(x) > 1}
    run = {v: round(max(x) - min(x), 1) for v, x in hours.items() if len(x) > 1}
    vehicles = {_key(v.id) for v in dataset.vehicles}
    measured = {_key(v) for v in (*dist, *run)}
    without = sorted(vehicles - measured)
    if not dist and not run:
        return section("use", "empty", f"No vehicle has two readings between {start} and {end}, so use cannot be measured.",
                       {"vehicles_without_use": len(without)})
    top = sorted(dist.items(), key=lambda kv: -kv[1])[:5]
    return section("use", "ok",
                   f"{len(dist)} vehicles with distance ({sum(dist.values()):,.0f} km in all) and {len(run)} with hours "
                   f"({sum(run.values()):,.0f} h) between {start} and {end}; {len(without)} vehicles without two readings.",
                   {"km_by_vehicle": dict(top), "hours_vehicles": len(run), "total_km": round(sum(dist.values()), 1),
                    "total_hours": round(sum(run.values()), 1), "vehicles_without_use": len(without),
                    "examples_without_use": without[:LISTED]})


def fleet_health_sections(dataset: Any, today: date, start: date, end: date) -> list[Section]:
    from .tyre import fleet

    out: list[Section] = []
    statuses = Counter((v.status or "unknown") for v in dataset.vehicles)
    sites = Counter((v.site or "unknown") for v in dataset.vehicles)
    out.append(section("fleet", "ok" if dataset.vehicles else "empty",
                       f"{len(dataset.vehicles)} vehicles in the sync; by status: "
                       + (", ".join(f"{k} {n}" for k, n in statuses.most_common(6)) or "none") + ".",
                       {"vehicles": len(dataset.vehicles), "by_status": dict(statuses.most_common(10)),
                        "by_site": dict(sites.most_common(10))}))
    open_orders = [o for o in dataset.work_orders if fleet.is_open(o)]
    aged = [o for o in open_orders if (today - o.opened_at).days > 7]
    schedules_overdue = [s for s in dataset.inspection_schedules if fleet.schedule_state(s, today) == "overdue"]
    upcoming = [s for s in dataset.inspection_schedules
                if fleet.schedule_state(s, today) == "upcoming" and s.scheduled_for <= today + timedelta(days=14)]
    if not dataset.work_orders and not dataset.inspection_schedules:
        out.append(section("maintenance_due", "not_connected",
                           "No work orders or inspection schedules are in the fleet sync, so maintenance due is Not connected."))
    else:
        out.append(section("maintenance_due", "ok",
                           f"{len(open_orders)} open work orders ({len(aged)} open more than 7 days), {len(schedules_overdue)} "
                           f"inspections overdue and {len(upcoming)} due in the next 14 days.",
                           {"open_work_orders": len(open_orders), "open_over_7_days": len(aged),
                            "oldest_open": [{"id": o.id, "vehicle": o.vehicle_id, "opened": o.opened_at.isoformat()}
                                            for o in sorted(aged, key=lambda o: o.opened_at)[:LISTED]],
                            "inspections_overdue": len(schedules_overdue), "inspections_next_14_days": len(upcoming)}))
    typed = [o for o in dataset.work_orders if o.work_type]
    if not dataset.work_orders:
        out.append(section("breakdowns", "not_connected", "No work orders are in the fleet sync, so breakdowns are Not connected."))
    elif not typed:
        out.append(section("breakdowns", "not_connected",
                           "The synced work orders carry no work type, so breakdowns cannot be told apart from other work."))
    else:
        broke = [o for o in typed if any(w in _key(o.work_type) for w in BREAKDOWN_WORDS) and _in(o.opened_at, start, end)]
        by_vehicle = Counter(o.vehicle_id or "unknown" for o in broke)
        out.append(section("breakdowns", "ok" if broke else "empty",
                           f"{len(broke)} breakdown work orders between {start} and {end}"
                           + (f"; most on {', '.join(f'{v} ({n})' for v, n in by_vehicle.most_common(3))}." if broke else "."),
                           {"breakdowns": len(broke), "by_vehicle": dict(by_vehicle.most_common(LISTED))}))
    out.append(use_section(dataset, start, end))
    costs = [c for c in (fleet.order_cost(o) for o in dataset.work_orders if _in(o.opened_at, start, end)) if c is not None]
    out.append(section("cost", "ok" if costs else ("empty" if dataset.work_orders else "not_connected"),
                       f"Maintenance cost {sum(costs):,.2f} {dataset.currency} from {len(costs)} work orders between {start} and {end}."
                       if costs else ("No work order with a cost in the period." if dataset.work_orders
                                      else "No work orders are in the fleet sync, so cost is Not connected."),
                       {"total": round(sum(costs), 2), "currency": dataset.currency, "work_orders": len(costs)}))
    return out


def data_quality_sections(stats: Any, dataset: Any, now: datetime) -> list[Section]:
    today = now.date()
    age_hours = (now - stats.synced_at).total_seconds() / 3600
    stale = age_hours > STALE_SYNC_HOURS
    tables = {name: {"read": e.read, "kept": e.kept, "skipped": e.skipped, "cut": e.cut, "issues": dict(e.issues)}
              for name, e in sorted(stats.entities.items())}
    skipped = {name: t for name, t in tables.items() if t["skipped"] or t["issues"] or t["cut"]}
    out = [section("sync", "ok",
                   f"Last sync {stats.synced_at:%Y-%m-%d %H:%M} UTC, {age_hours:,.0f} hours ago"
                   + (" (stale: more than a day old)." if stale else ".")
                   + (f" Rows skipped in {', '.join(f'{k} ({v['skipped']})' for k, v in skipped.items())}." if skipped else " No rows skipped."),
                   {"synced_at": stats.synced_at.isoformat(), "age_hours": round(age_hours, 1), "stale": stale, "tables": tables})]
    since = today - timedelta(days=30)
    inspected = {_key(v.vehicle_id) for v in dataset.inspection_visits if v.inspected_at >= since}
    inspected |= {_key(i.vehicle_id) for i in dataset.inspections if i.inspected_at >= since and i.vehicle_id}
    vehicles = sorted(_key(v.id) for v in dataset.vehicles)
    not_inspected = [v for v in vehicles if v not in inspected]
    read_tyres = {i.tyre_id for i in dataset.inspections if i.tyre_id and i.tread_mm is not None}
    fitted = [t for t in dataset.tyres if str(getattr(t.status, "value", t.status)) == "fitted"]
    no_tread = [t.id for t in fitted if t.id not in read_tyres]
    with_reading = {_key(r.vehicle_id) for r in (*dataset.odometer_readings, *dataset.hour_readings) if r.read_at >= since}
    no_reading = [v for v in vehicles if v not in with_reading] if (dataset.odometer_readings or dataset.hour_readings) else None
    no_vehicle = [o.id for o in dataset.work_orders if not o.vehicle_id]
    from .tyre import fleet

    no_cost = [o.id for o in dataset.work_orders if not fleet.is_open(o) and fleet.order_cost(o) is None]
    missing = {
        "vehicles_not_inspected_30_days": {"count": len(not_inspected), "examples": not_inspected[:LISTED]},
        "fitted_tyres_without_tread": {"count": len(no_tread), "examples": no_tread[:LISTED]},
        "vehicles_without_readings_30_days": None if no_reading is None
        else {"count": len(no_reading), "examples": no_reading[:LISTED]},
        "work_orders_without_vehicle": {"count": len(no_vehicle), "examples": no_vehicle[:LISTED]},
        "closed_work_orders_without_cost": {"count": len(no_cost), "examples": no_cost[:LISTED]},
    }
    parts = [f"{len(not_inspected)} of {len(vehicles)} vehicles not inspected in 30 days",
             f"{len(no_tread)} of {len(fitted)} fitted tyres without a tread reading",
             ("odometer and hour readings are Not connected" if no_reading is None
              else f"{len(no_reading)} vehicles without an odometer or hour reading in 30 days"),
             f"{len(no_vehicle)} work orders without a vehicle", f"{len(no_cost)} closed work orders without a cost"]
    out.append(section("missing", "ok", "; ".join(parts) + ".", missing))
    return out


def maintenance_cost_sections(dataset: Any, start: date, end: date) -> list[Section]:
    from .tyre import fleet

    if not dataset.work_orders and not dataset.costs:
        out = [section("work_orders", "not_connected", "No work orders or tyre costs are in the fleet sync, so cost is Not connected.")]
    else:
        orders = [o for o in dataset.work_orders if _in(o.opened_at, start, end)]
        costed = [o for o in orders if fleet.order_cost(o) is not None]
        total = round(sum(fleet.order_cost(o) or 0 for o in costed), 2)
        groups = {g: [row.model_dump() for row in fleet.work_order_costs(costed, g)[:5]] for g in ("work_type", "site", "vehicle")}
        tyre_cost = round(sum(c.amount for c in dataset.costs if _in(c.date, start, end)), 2)
        out = [
            section("work_orders", "ok" if costed else "empty",
                    f"{total:,.2f} {dataset.currency} from {len(costed)} of {len(orders)} work orders between {start} and {end}; "
                    f"{len(orders) - len(costed)} without a cost.",
                    {"total": total, "currency": dataset.currency, "work_orders": len(orders), "without_cost": len(orders) - len(costed),
                     "largest": groups}),
            section("tyre_costs", "ok" if tyre_cost else "empty",
                    f"Tyre costs {tyre_cost:,.2f} {dataset.currency} between {start} and {end}.",
                    {"total": tyre_cost, "currency": dataset.currency}),
        ]
    out.append(section("fuel", "not_connected", "Fuel is Not connected: ANUM has no fuel data source yet."))
    return out


def fleet_watch_sections(dataset: Any, today: date) -> list[Section]:
    from .tyre import fleet

    overdue = sorted((s for s in dataset.inspection_schedules if fleet.schedule_state(s, today) == "overdue"), key=lambda s: s.scheduled_for)
    soon = sorted((s for s in dataset.inspection_schedules if fleet.schedule_state(s, today) == "upcoming"
                   and s.scheduled_for <= today + timedelta(days=14)), key=lambda s: s.scheduled_for)
    failed = [c for c in dataset.checklists if c.submitted_at >= today - timedelta(days=7) and fleet.failed(c)]

    def rows(items: Iterable[Any]) -> list[dict[str, Any]]:
        return [{"vehicle": s.vehicle_id, "date": s.scheduled_for.isoformat(), "type": s.inspection_type} for s in list(items)[:LISTED]]

    out = [section("inspections", "ok" if dataset.inspection_schedules else "not_connected",
                   f"{len(overdue)} inspections overdue and {len(soon)} due in the next 14 days." if dataset.inspection_schedules
                   else "No inspection schedules are in the fleet sync, so due inspections are Not connected.",
                   {"overdue": len(overdue), "overdue_list": rows(overdue), "next_14_days": len(soon), "next_list": rows(soon)})]
    out.append(section("checklists", "ok" if dataset.checklists else "not_connected",
                       f"{len(failed)} workshop checklists failed in the last 7 days." if dataset.checklists
                       else "No workshop checklists are in the fleet sync, so failed checklists are Not connected.",
                       {"failed_7_days": len(failed),
                        "failed_list": [{"id": c.id, "vehicle": c.vehicle_id, "date": c.submitted_at.isoformat()} for c in failed[:LISTED]]}))
    return out


# --------------------------------------------------------------------------- pure: seats and hours


def _minutes(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _until(now_min: int, target: str) -> int:
    return (_minutes(target) - now_min) % (24 * 60)


def team_section(seats: list[Mapping[str, Any]], now: datetime) -> tuple[Section, int | None]:
    """Who of Green Concrete is on shift now, who ends next and who starts next, from each seat's own hours.

    Returns the section and how many minutes ago the latest shift began (for the handover), or None.
    """
    from .office_green_concrete import in_window, local_minutes, seat_window

    if not seats:
        return section("team", "empty", "No one is seated in Green Concrete yet (Office tools, Add Green Concrete)."), None
    people = []
    began: list[int] = []
    for s in seats:
        window = seat_window(s)
        name = s.get("agent_name") or "A seated agent you cannot see"
        role = s.get("department") or ""
        if window is None:
            people.append({"name": name, "role": role, "seat": s.get("label"), "hours": None, "on_shift": True})
            continue
        start, end = window
        now_min = local_minutes(now, s.get("timezone"))
        on = in_window(now_min, start, end)
        if on:
            began.append((now_min - _minutes(start)) % (24 * 60))
        people.append({"name": name, "role": role, "seat": s.get("label"), "hours": f"{start} to {end}", "on_shift": on,
                       "ends_in_minutes": _until(now_min, end) if on else None,
                       "starts_in_minutes": None if on else _until(now_min, start)})
    on_now = [p for p in people if p["on_shift"]]
    ending = sorted((p for p in on_now if p.get("ends_in_minutes") is not None), key=lambda p: p["ends_in_minutes"])
    starting = sorted((p for p in people if p.get("starts_in_minutes") is not None), key=lambda p: p["starts_in_minutes"])
    next_end = [p for p in ending if p["ends_in_minutes"] == ending[0]["ends_in_minutes"]] if ending else []
    next_start = [p for p in starting if p["starts_in_minutes"] == starting[0]["starts_in_minutes"]] if starting else []
    tz = next((s.get("timezone") for s in seats if s.get("timezone")), None) or "UTC"
    summary = (f"{len(on_now)} of {len(people)} people on shift now ({tz}): "
               + (", ".join(f"{p['name']} ({p['hours'] or 'no hours set'})" for p in on_now) or "nobody") + ". "
               + (f"Ending next: {', '.join(p['name'] for p in next_end)} in {next_end[0]['ends_in_minutes']} minutes. " if next_end else "")
               + (f"Starting next: {', '.join(p['name'] for p in next_start)} in {next_start[0]['starts_in_minutes']} minutes." if next_start else ""))
    data = {"timezone": tz, "people": people, "on_shift": len(on_now), "total": len(people),
            "ending_next": [p["name"] for p in next_end], "starting_next": [p["name"] for p in next_start]}
    return section("team", "ok", summary.strip(), data), (min(began) if began else None)


def task_sections(by_status: Mapping[str, int], open_tasks: list[Any], now: datetime, since: datetime | None) -> list[Section]:
    overdue = sorted((t for t in open_tasks if t.due_at is not None and t.due_at < now), key=lambda t: t.due_at)
    waiting = by_status.get("waiting_approval", 0)
    out = [section("tasks", "ok" if open_tasks else "empty",
                   f"{len(open_tasks)} open tasks, {len(overdue)} overdue, {waiting} waiting for approval.",
                   {"by_status": dict(by_status), "open": len(open_tasks), "overdue": len(overdue), "waiting_approval": waiting,
                    "overdue_list": [{"id": t.id, "title": t.title[:120], "due_at": t.due_at.isoformat()} for t in overdue[:5]]})]
    if since is not None:
        changed = [t for t in open_tasks if t.updated_at >= since]
        out.append(section("tasks_this_shift", "ok" if changed else "empty",
                           f"{len(changed)} open tasks changed since the shift began at {since:%H:%M} UTC.",
                           {"since": since.isoformat(), "changed": [{"id": t.id, "title": t.title[:120], "status": t.status.value}
                                                                  for t in changed[:LISTED]]}))
    return out


# --------------------------------------------------------------------------- reads (worker thread)


def _tyre_loaded(context: TenantContext) -> tuple[Any, Section | None]:
    from .tyre import service

    if not has_permission(context, Permission.TYRE_READ):
        return None, section("fleet", "no_access", NO_ACCESS.format(what="tyre planning"))
    try:
        return service.load(context), None
    except service.NotConnectedError:
        return None, section("fleet", "not_connected", NOT_CONNECTED_TYRE)
    except service.NotSyncedError:
        return None, section("fleet", "not_synced", NOT_SYNCED_TYRE)


def _alerts(context: TenantContext) -> Section:
    from .tyre.store import open_tyre_store

    if not has_permission(context, Permission.TYRE_READ):
        return section("alerts", "no_access", NO_ACCESS.format(what="tyre alerts"))
    with open_tyre_store(context) as store:
        if store.get_connection(context) is None:
            return section("alerts", "not_connected", NOT_CONNECTED_TYRE)
        items, total = store.list_alerts(context, status="open", kind=None, query=None, limit=LISTED, offset=0)
        counts = store.alert_counts(context)
    return section("alerts", "ok" if total else "empty",
                   f"{total} open tyre alerts ({counts.get('critical', 0)} critical, {counts.get('warning', 0)} warnings).",
                   {"total": total, "counts": dict(counts),
                    "top": [{"severity": a.severity.value, "subject": a.subject_label, "message": a.message} for a in items]})


def read_seats(context: TenantContext) -> Section | list[dict[str, Any]]:
    """The Green Concrete seats with an agent: hours, label, the agent's name and role (RLS hides private agents' names)."""
    from sqlalchemy import text

    from .office_scope import workspace_session
    from .settings import settings

    if not has_permission(context, Permission.TASK_READ):
        return section("team", "no_access", NO_ACCESS.format(what="the office"))
    if settings.repository_backend != "postgresql":
        return section("team", "not_connected", "Seats and working hours are kept in the office, which needs the PostgreSQL backend.")
    p = {"t": context.tenant_id, "w": context.workspace_id, "b": GC_BUILDING}
    with workspace_session(context) as session:
        built = session.execute(text("select 1 from office_buildings where tenant_id = :t and workspace_id = :w and id = :b"), p).first()
        if built is None:
            return section("team", "not_connected", "Green Concrete is not built in this workspace: Office tools, Add Green Concrete.")
        rows = session.execute(text(
            "select d.id as desk_id, d.label, d.shift, d.work_start, d.work_end, b.timezone, b.shift_day_start, "
            "b.shift_night_start, a.name as agent_name, a.department from office_desks d "
            "join office_rooms r on r.tenant_id = d.tenant_id and r.workspace_id = d.workspace_id and r.id = d.room_id "
            "join office_floors f on f.tenant_id = r.tenant_id and f.workspace_id = r.workspace_id and f.id = r.floor_id "
            "join office_buildings b on b.tenant_id = f.tenant_id and b.workspace_id = f.workspace_id and b.id = f.building_id "
            "left join agent_profiles a on a.tenant_id = d.tenant_id and a.workspace_id = d.workspace_id and a.id = d.agent_id "
            "where d.tenant_id = :t and d.workspace_id = :w and b.id = :b and d.agent_id is not null order by d.id"), p).mappings().all()
    return [dict(r) for r in rows]


def _tasks(context: TenantContext, now: datetime, since: datetime | None) -> list[Section]:
    from .dependencies import _unit_of_work
    from .tasks import TaskFilters

    if not has_permission(context, Permission.TASK_READ):
        return [section("tasks", "no_access", NO_ACCESS.format(what="tasks"))]
    with _unit_of_work(context, require_membership=False) as repository:
        summary = repository.summarize_tasks(context, TaskFilters())
        open_tasks = repository.list_tasks(context, limit=200, filters=TaskFilters(statuses=OPEN_TASK))
    return task_sections(summary.by_status, open_tasks, now, since)


def _calendar(context: TenantContext, now: datetime, timezone: str) -> Section:
    from .calendar_api import open_calendar_store

    if not has_permission(context, Permission.CALENDAR_READ):
        return section("calendar", "no_access", NO_ACCESS.format(what="the calendar"))
    try:
        zone = ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 - an unknown zone reads UTC, as the seats do
        zone = ZoneInfo("UTC")
    local = now.astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    with open_calendar_store(context) as store:
        events = store.between(start, start + timedelta(days=1), include_cancelled=False, limit=LISTED + 1)
    return section("calendar", "ok" if events else "empty",
                   f"{len(events[:LISTED])} events on the workspace calendar today" + ("" if len(events) <= LISTED else ", and more") + ".",
                   {"events": [{"title": e.title[:120], "starts_at": e.starts_at.isoformat(), "ends_at": e.ends_at.isoformat()}
                               for e in events[:LISTED]]})


def _freshness(loaded: Any, now: datetime) -> Section:
    synced = loaded.snapshot.stats.synced_at
    hours = (now - synced).total_seconds() / 3600
    return section("data_freshness", "ok", f"Fleet data synced {synced:%Y-%m-%d %H:%M} UTC ({hours:,.0f} hours ago)"
                   + (", which is more than a day old." if hours > STALE_SYNC_HOURS else "."),
                   {"synced_at": synced.isoformat(), "age_hours": round(hours, 1)})


def _sub(handler: Callable[..., Any], name: str, arguments: dict[str, Any], context: TenantContext, section_id: str) -> Section:
    """An existing tyre tool (it checks its own permission and connection) as one section."""
    result = asyncio.run(handler(ToolCall(name=name, arguments=arguments), context))
    if result.status == "succeeded":
        return section(section_id, "ok", result.summary, result.output)
    lowered = result.summary.lower()
    state = ("no_access" if "cannot read" in lowered else "not_synced" if "not been synced" in lowered
             else "not_connected" if "not connected" in lowered else "failed")
    return section(section_id, state, result.summary)


def _with_fleet(context: TenantContext, build: Callable[[Any], list[Section]]) -> list[Section]:
    loaded, problem = _tyre_loaded(context)
    return [problem] if problem is not None else build(loaded)


def build(name: str, arguments: dict[str, Any], context: TenantContext, now: datetime | None = None) -> list[Section]:
    """The sections of one tool (blocking reads: call it in a worker thread)."""
    from .tyre import tools as tyre_tools

    now = now or utc_now()
    today = now.date()
    if name == "gc.fleet_health":
        start, end = period(arguments, today)
        return _with_fleet(context, lambda l: [*fleet_health_sections(l.snapshot.dataset, today, start, end), _alerts(context)])
    if name == "gc.tyre_plan":
        months = arguments.get("months") if isinstance(arguments.get("months"), int) else 3
        this_month = today.strftime("%Y-%m")
        next_month = (today.replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
        return [
            _sub(tyre_tools.fleet_summary_tool, tyre_tools.FLEET_SUMMARY, {}, context, "tyre_health"),
            _sub(tyre_tools.tyres_due_tool, tyre_tools.TYRES_DUE, {"month": this_month}, context, "due_this_month"),
            _sub(tyre_tools.tyres_due_tool, tyre_tools.TYRES_DUE, {"month": next_month}, context, "due_next_month"),
            _sub(tyre_tools.purchase_plan_tool, tyre_tools.PURCHASE_PLAN, {"months": max(1, min(int(months), 36))}, context, "purchase_plan"),
            _alerts(context),
        ]
    if name == "gc.data_quality":
        return _with_fleet(context, lambda l: data_quality_sections(l.snapshot.stats, l.snapshot.dataset, now))
    if name == "gc.maintenance_costs":
        start, end = period(arguments, today)
        return _with_fleet(context, lambda l: maintenance_cost_sections(l.snapshot.dataset, start, end))
    if name == "gc.fleet_watch":
        return [_alerts(context),
                *_with_fleet(context, lambda l: fleet_watch_sections(l.snapshot.dataset, today)),
                _sub(tyre_tools.tyres_due_tool, tyre_tools.TYRES_DUE, {"month": today.strftime("%Y-%m")}, context, "due_this_month")]
    seats = read_seats(context)
    team, began = team_section(seats, now) if isinstance(seats, list) else (seats, None)
    tz = team["data"].get("timezone") or "UTC"
    if name == "gc.ops_brief":
        loaded, problem = _tyre_loaded(context)
        fleet_part: list[Section]
        if problem is not None:
            fleet_part = [problem]
        else:
            from .tyre import fleet

            orders = [o for o in loaded.snapshot.dataset.work_orders if fleet.is_open(o)]
            fleet_part = [section("work_orders", "ok" if orders else "empty", f"{len(orders)} open work orders.",
                                  {"open": len(orders)}), _freshness(loaded, now)]
        return [_alerts(context), *_tasks(context, now, None), team, *fleet_part, _calendar(context, now, tz)]
    if name == "gc.shift_handover":
        since = now - timedelta(minutes=began) if began is not None else None
        return [team, *_tasks(context, now, since), _alerts(context)]
    raise ValueError(f"unknown Green Concrete tool {name}")


def result(name: str, sections: list[Section], now: datetime) -> ToolResult:
    label = TOOL_BY_NAME[name].label.en
    text = f"{label} on {now:%Y-%m-%d %H:%M} UTC. " + " ".join(
        f"[{s['id']}: {s['state']}] {s['summary']}" for s in sections)
    return ToolResult(status="succeeded", summary=text[:6000],
                      output={"tool": name, "as_of": now.isoformat(), "sections": sections},
                      audit_metadata={"outcome": "answered", "sections": {s["id"]: s["state"] for s in sections}})


def _handler(name: str):  # type: ignore[no-untyped-def]
    async def run(call: ToolCall, context: TenantContext) -> ToolResult:
        def work() -> ToolResult:
            now = utc_now()
            try:
                return result(name, build(name, dict(call.arguments or {}), context, now), now)
            except Exception as exc:  # noqa: BLE001 - one failed read is reported, never a guess
                logger.warning("green_concrete_tool_failed tool=%s error=%s", name, type(exc).__name__)
                return ToolResult(status="failed", summary=f"The {TOOL_BY_NAME[name].label.en} read failed ({type(exc).__name__}). Try again.",
                                  audit_metadata={"outcome": "failed"})

        return await asyncio.to_thread(work)

    return run


def register_green_concrete_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the Green Concrete read tools (the API's and the worker's registries)."""
    for tool in TOOLS:
        if tool.name not in registry.names:
            registry.register(
                ToolDefinition(name=tool.name, description=tool.description, risk_level=RiskLevel.LOW, timeout_seconds=60,
                               idempotent=True, category="general", action_kind="read",
                               input_schema={"type": "object", "properties": {
                                   "date_from": {"type": "string", "format": "date"},
                                   "date_to": {"type": "string", "format": "date"},
                                   "months": {"type": "integer", "minimum": 1, "maximum": 36}},
                                   "additionalProperties": False}),
                _handler(tool.name),
            )
    return registry
