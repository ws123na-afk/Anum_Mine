"""Tyre planning tools for tasks, chat and voice (docs/tyre-planning.md#assistant).

Read-only, low-risk tools answer from the workspace's synced tyre data, with the
numbers in the summary so an answer can show them:

* ``tyre_fleet_summary`` - fleet health: tread, pressure compliance, overdue, due, cost,
  and the operations counts (vehicles inspected, checklists, open work orders).
* ``tyres_due`` - tyres projected for removal in a month (``month``: ``YYYY-MM``).
* ``purchase_plan`` - quantities and estimated cost by size and brand (``months`` or ``month``).
* ``tyre_alerts`` - open alerts, most severe first.
* the fleet operations tools in ``fleet_tools.py``: vehicles, one vehicle's tyres and
  readings, inspections and schedules, workshop checklists, work orders and maintenance
  costs.

Tasks reach them through the ``anum.tyre-planning`` skill and the planner. Chat and
voice ground their answers with :func:`tyre_grounding`, which picks the tool from the
user's own words, asks ``ToolPolicy`` (governance can deny it), runs it through the
registry, audits it as ``tool.call`` and hands the result to the model as a labeled,
untrusted data block. None of the tools writes anything or reaches outside ANUM.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult, tool_output_prompt_block
from ..authorization import Permission
from ..dependencies import has_permission
from ..schemas import RiskLevel, TenantContext, utc_now
from . import fleet, fleet_tools, service
from .analytics import fleet_summary, purchase_plan
from .model import Entity
from .store import open_tyre_store

FLEET_SUMMARY = "tyre_fleet_summary"
TYRES_DUE = "tyres_due"
PURCHASE_PLAN = "purchase_plan"
TYRE_ALERTS = "tyre_alerts"
TOOL_NAMES = (FLEET_SUMMARY, TYRES_DUE, PURCHASE_PLAN, TYRE_ALERTS, *fleet_tools.FLEET_TOOL_NAMES)
SKILL_ID = "anum.tyre-planning"
MAX_LISTED = 15

_DEFINITIONS = {
    FLEET_SUMMARY: "Summarise fleet tyre health from the synced tyre data; read-only.",
    TYRES_DUE: "List tyres projected for removal in a month (month: YYYY-MM); read-only.",
    PURCHASE_PLAN: "Tyre purchase plan by month, size and brand with estimated cost (months: 1-36); read-only.",
    TYRE_ALERTS: "List open tyre alerts (low tread, pressure, overdue inspections, wear, cost); read-only.",
    **fleet_tools.DEFINITIONS,
}

_registry: ToolRegistry | None = None


def _failed(summary: str) -> ToolResult:
    return ToolResult(status="failed", summary=summary, audit_metadata={"outcome": "unavailable"})


def _guard(context: TenantContext) -> ToolResult | None:
    if not has_permission(context, Permission.TYRE_READ):
        return _failed("Your role cannot read tyre planning in this workspace.")
    return None


def _loaded(context: TenantContext):  # type: ignore[no-untyped-def]
    try:
        return service.forecasts_for(context), None
    except service.NotConnectedError:
        return None, _failed("Tyre data is not connected in this workspace yet: an owner can connect it in Tyre planning.")
    except service.NotSyncedError:
        return None, _failed("Tyre data is connected but has not been synced yet: sync it in Tyre planning.")


def _fmt(value: float | None, unit: str = "", digits: int = 1) -> str:
    if value is None:
        return "unknown"
    return f"{value:,.{digits}f}{unit}"


async def fleet_summary_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    if (denied := _guard(context)) is not None:
        return denied
    loaded_forecasts, problem = _loaded(context)
    if problem:
        return problem
    loaded, forecasts = loaded_forecasts
    today = utc_now().date()
    summary = fleet_summary(loaded.snapshot.dataset, loaded.connection.assumptions, today, forecasts)
    with open_tyre_store(context) as store:
        counts = store.alert_counts(context)
    mapping = loaded.connection.mapping
    work_orders = mapping.entities.get(Entity.WORK_ORDERS) if mapping else None
    operations = fleet.operations_summary(loaded.snapshot.dataset, today,
                                          work_orders.window_days if work_orders else None)
    text = (
        f"Fleet tyre health on {today.isoformat()} (data synced {loaded.snapshot.stats.synced_at:%Y-%m-%d %H:%M} UTC): "
        f"{summary.vehicles} vehicles, {summary.tyres_fitted} tyres fitted, {summary.tyres_in_stock} in stock. "
        f"Average tread {_fmt(summary.average_tread_mm, ' mm')} across {summary.tyres_with_readings} measured tyres; "
        f"{summary.below_minimum} at or below the {summary.min_tread_mm:g} mm minimum and {summary.in_warning_band} close to it. "
        f"Pressure compliance {_fmt(summary.pressure_compliant_pct, '%')} of {summary.pressure_checked} tyres checked "
        f"({summary.under_inflated} under, {summary.over_inflated} over). "
        f"{summary.overdue_inspections} tyres are overdue for inspection. "
        f"{summary.due_next_30_days} tyres are due for removal in 30 days and {summary.due_next_90_days} in 90 days. "
        f"Cost per 1000 km {_fmt(summary.cost_per_1000_km, ' ' + summary.currency, 2)}. "
        f"Open alerts: {counts.get('critical', 0)} critical, {counts.get('warning', 0)} warnings, {counts.get('info', 0)} info. "
        f"Inspections: {operations.inspections_last_30_days} in the last 30 days covering "
        f"{operations.vehicles_inspected_last_30_days} vehicles ({operations.vehicles_inspected_last_90_days} in 90 days), "
        f"{operations.tyre_readings} tyre readings ({operations.readings_on_tyre_records} matched to a tyre record); "
        f"{operations.schedules_overdue} scheduled inspections overdue. "
        f"Workshop: {operations.checklists_last_30_days} checklists in 30 days "
        f"({operations.checklists_failed_last_30_days} failed), {operations.work_orders_open} open work orders."
    )
    return ToolResult(
        status="succeeded", summary=text,
        output={"summary": summary.model_dump(mode="json"), "open_alerts": counts,
                "operations": operations.model_dump(mode="json")},
        audit_metadata={"outcome": "answered", "tyres_fitted": summary.tyres_fitted},
    )


_MONTHS = {name: index for index, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
_MONTHS.update({name[:3]: index for name, index in list(_MONTHS.items())})
_MONTHS.update({"sept": 9})
_ARABIC_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
    "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}


def parse_month(text: str, today: date) -> str:
    """``YYYY-MM`` named in the text ("March", "next month", "2026-11"); this month otherwise."""
    lowered = text.lower()
    found = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", lowered)
    if found:
        return f"{found.group(1)}-{found.group(2)}"
    if "next month" in lowered or "الشهر القادم" in text or "الشهر المقبل" in text:
        month = today.month % 12 + 1
        return f"{today.year + (today.month == 12):04d}-{month:02d}"
    year_match = re.search(r"\b(20\d{2})\b", lowered)
    for word in re.findall(r"[a-z]+", lowered):
        if word in _MONTHS:
            month = _MONTHS[word]
            year = int(year_match.group(1)) if year_match else today.year + (month < today.month)
            return f"{year:04d}-{month:02d}"
    for word, month in _ARABIC_MONTHS.items():
        if word in text:
            year = int(year_match.group(1)) if year_match else today.year + (month < today.month)
            return f"{year:04d}-{month:02d}"
    return f"{today.year:04d}-{today.month:02d}"


def parse_months(text: str, default: int | None = None) -> int | None:
    match = re.search(r"(\d{1,2})\s*(months|month|شهر|أشهر|اشهر)", text.lower())
    if match:
        return max(1, min(36, int(match.group(1))))
    if "next year" in text.lower() or "السنة القادمة" in text or "12 months" in text:
        return 12
    if "quarter" in text.lower() or "ربع" in text:
        return 3
    return default


async def tyres_due_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    from .api import filter_forecasts, month_bounds

    if (denied := _guard(context)) is not None:
        return denied
    loaded_forecasts, problem = _loaded(context)
    if problem:
        return problem
    loaded, forecasts = loaded_forecasts
    today = utc_now().date()
    month = str(call.arguments.get("month") or f"{today.year:04d}-{today.month:02d}")
    try:
        month_bounds(month)
    except Exception:
        return _failed("month must be YYYY-MM.")
    items = filter_forecasts(forecasts, month=month, today=today)
    by_size: dict[str, int] = {}
    for item in items:
        by_size[item.size or "unknown size"] = by_size.get(item.size or "unknown size", 0) + 1
    listed = "; ".join(
        f"{item.serial or item.tyre_id} on {item.vehicle_label or item.vehicle_id or 'unassigned'}"
        f"{' ' + item.position if item.position else ''}: {_fmt(item.current_tread_mm, ' mm')}, "
        f"removal {item.projected_removal.isoformat() if item.projected_removal else '?'} ({item.confidence.value} confidence)"
        for item in items[:MAX_LISTED]
    )
    sizes = ", ".join(f"{count} x {size}" for size, count in sorted(by_size.items(), key=lambda kv: -kv[1]))
    text = (
        f"{len(items)} tyres are projected for removal in {month} (minimum tread "
        f"{loaded.connection.assumptions.min_tread_mm:g} mm)"
        + (f": {sizes}. " if items else ". ")
        + (listed + ("." if len(items) <= MAX_LISTED else f"; and {len(items) - MAX_LISTED} more.") if items else "")
    ).strip()
    return ToolResult(
        status="succeeded", summary=text,
        output={"month": month, "total": len(items), "by_size": by_size,
                "tyres": [item.model_dump(mode="json") for item in items[:50]]},
        audit_metadata={"outcome": "answered", "total": len(items)},
    )


async def purchase_plan_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    if (denied := _guard(context)) is not None:
        return denied
    loaded_forecasts, problem = _loaded(context)
    if problem:
        return problem
    loaded, forecasts = loaded_forecasts
    raw = call.arguments.get("months")
    months = int(raw) if isinstance(raw, int) and 1 <= raw <= 36 else None
    month = call.arguments.get("month")
    plan = purchase_plan(loaded.snapshot.dataset, loaded.connection.assumptions, utc_now().date(), forecasts,
                         months=months)
    rows = [row for row in plan.rows if not month or row.month == month]
    total_cost = sum(row.estimated_cost or 0 for row in rows)
    unpriced = sum(1 for row in rows if row.estimated_cost is None and row.to_buy)
    span = f"{month}" if month else f"the next {months or loaded.connection.assumptions.horizon_months} months"
    listed = "; ".join(
        f"{row.month}: {row.to_buy} x {row.size} {row.brand}"
        + (f" ({row.estimated_cost:,.0f} {plan.currency})" if row.estimated_cost is not None else " (no price)")
        for row in rows[:MAX_LISTED]
    )
    text = (
        f"Purchase plan for {span}: buy {sum(row.to_buy for row in rows)} tyres, estimated "
        f"{total_cost:,.0f} {plan.currency}"
        + (f" ({unpriced} lines have no price)" if unpriced else "")
        + (f", after {sum(row.from_stock for row in rows)} from stock and "
           f"{loaded.connection.assumptions.safety_stock_pct:g}% safety stock. " if rows else ". ")
        + listed
    ).strip()
    return ToolResult(
        status="succeeded", summary=text,
        output={"months": plan.months, "currency": plan.currency, "rows": [row.model_dump() for row in rows],
                "total_tyres": sum(row.to_buy for row in rows), "total_cost": round(total_cost, 2)},
        audit_metadata={"outcome": "answered", "rows": len(rows)},
    )


async def tyre_alerts_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    if (denied := _guard(context)) is not None:
        return denied
    with open_tyre_store(context) as store:
        if store.get_connection(context) is None:
            return _failed("Tyre data is not connected in this workspace yet: an owner can connect it in Tyre planning.")
        items, total = store.list_alerts(context, status="open", kind=None, query=None, limit=MAX_LISTED, offset=0)
        counts = store.alert_counts(context)
    listed = "; ".join(f"[{alert.severity.value}] {alert.subject_label}: {alert.message}" for alert in items)
    text = (
        f"{total} open tyre alerts ({counts.get('critical', 0)} critical, {counts.get('warning', 0)} warnings, "
        f"{counts.get('info', 0)} info)" + (f": {listed}" if items else ".")
        + (f" And {total - len(items)} more in Tyre planning > Alerts." if total > len(items) else "")
    )
    return ToolResult(
        status="succeeded", summary=text,
        output={"total": total, "counts": counts, "alerts": [alert.model_dump(mode="json") for alert in items]},
        audit_metadata={"outcome": "answered", "total": total},
    )


def _guarded(handler):  # type: ignore[no-untyped-def]
    async def run(call: ToolCall, context: TenantContext) -> ToolResult:
        if (denied := _guard(context)) is not None:
            return denied
        return await handler(call, context)

    return run


_HANDLERS = {
    FLEET_SUMMARY: fleet_summary_tool,
    TYRES_DUE: tyres_due_tool,
    PURCHASE_PLAN: purchase_plan_tool,
    TYRE_ALERTS: tyre_alerts_tool,
    **{name: _guarded(handler) for name, handler in fleet_tools.HANDLERS.items()},
}


def register_tyre_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the read-only tyre and fleet tools (the API's and the worker's registries)."""
    global _registry
    for name, handler in _HANDLERS.items():
        if name not in registry.names:
            registry.register(
                # Autonomy (docs/autonomy.md): read-only reports over synced data. Declared,
                # not guessed: the name ``purchase_plan`` would otherwise read as a payment.
                ToolDefinition(name=name, description=_DEFINITIONS[name], risk_level=RiskLevel.LOW,
                               timeout_seconds=30, idempotent=True, category="general", action_kind="read"),
                handler,
            )
    _registry = registry
    return registry


# --- choosing a tool from the user's words ------------------------------------------------

_TYRE_WORDS = re.compile(
    r"\b(tyres?|tires?|tread|treads|inflation|puncture|retread|inspections?|inspected|check\s*lists?|"
    r"work\s*orders?|job\s*cards?|workshop|maintenance|odometer|mileage|fleet|vehicles?|trucks?)\b"
    r"|(إطار|إطارات|اطار|اطارات|كفر|كفرات|التآكل|المداس|فحص|الفحوصات|المركبات|مركبة|الشاحنات|ورشة|صيانة)",
    re.IGNORECASE,
)


def mentions_tyres(text: str) -> bool:
    return bool(_TYRE_WORDS.search(text))


def choose_call(text: str, today: date | None = None) -> ToolCall:
    """The tool and arguments for a tyre question, from the user's own words only."""
    today = today or utc_now().date()
    lowered = text.lower()
    operations = fleet_tools.choose_fleet_call(text, today)
    if operations is not None:
        return operations
    if re.search(r"\b(alerts?|warnings?|alarms?|pressure|inflat\w*|overdue)\b", lowered) or re.search(
        r"تنبيه|تنبيهات|ضغط|إنذار", text
    ):
        return ToolCall(name=TYRE_ALERTS, arguments={})
    if re.search(r"\b(buy|purchase|order|procure\w*|budget|plan|stock up)\b", lowered) or re.search(
        r"شراء|اشتري|خطة|ميزانية|طلبية", text
    ):
        arguments: dict[str, Any] = {}
        months = parse_months(text)
        if months:
            arguments["months"] = months
        elif re.search(r"\b(in|for|during)\s+[a-z]+\b|\b20\d{2}-\d{2}\b", lowered) and parse_month(text, today) != f"{today.year:04d}-{today.month:02d}":
            arguments["month"] = parse_month(text, today)
        return ToolCall(name=PURCHASE_PLAN, arguments=arguments)
    if re.search(r"\b(due|remov\w*|replac\w*|change|swap|forecast|wear out|worn|when)\b", lowered) or re.search(
        r"مستحق|استبدال|تغيير|تبديل|متى", text
    ):
        return ToolCall(name=TYRES_DUE, arguments={"month": parse_month(text, today)})
    return ToolCall(name=FLEET_SUMMARY, arguments={})


@dataclass(frozen=True)
class TyreGrounding:
    call: ToolCall
    result: ToolResult
    block: str


TYRE_PREFACE = (
    "Tyre data for this workspace follows, read just now through the governed read-only tool named in the "
    "block. Answer tyre questions from it, show the numbers it gives (with units and dates) and say when the "
    "data was synced. Never invent figures that are not in it; if it says the data is not connected or synced, "
    "tell the user how to fix that."
)


async def tyre_grounding(context: TenantContext, question: str) -> TyreGrounding | None:
    """Run the tyre tool the question asks for, under tool policy, for chat and voice."""
    if _registry is None or not mentions_tyres(question) or not has_permission(context, Permission.TYRE_READ):
        return None
    from ..dependencies import _unit_of_work
    from ..model_gateway import MockModelGateway
    from ..runtime import AgentRuntime
    from ..agent_tools import ToolPolicyOutcome

    call = choose_call(question)
    registry = _registry

    # Policy and audit are short units of work in worker threads; the tool runs with no
    # transaction open, never holding a connection (docs/realtime.md#event-loop).
    def allowed() -> bool:
        with _unit_of_work(context, require_membership=False) as repository:
            runtime = AgentRuntime(MockModelGateway(), repository, tools=registry)
            decision = runtime.evaluate(call, context)
            if decision.outcome != ToolPolicyOutcome.ALLOW:
                runtime._audit(context, "tool.call", call.name, "blocked", call.name,
                               {"tool": call.name, "surface": "assistant", "reason": decision.reason})
                return False
            return True

    if not await asyncio.to_thread(allowed):
        return None
    result = await registry.execute(call, context)

    def audit() -> None:
        with _unit_of_work(context, require_membership=False) as repository:
            AgentRuntime(MockModelGateway(), repository, tools=registry)._audit(
                context, "tool.call", call.name, result.status, call.name,
                {**result.audit_metadata, "tool": call.name, "surface": "assistant"},
            )

    await asyncio.to_thread(audit)
    return TyreGrounding(call=call, result=result, block=tool_output_prompt_block(call, result))


def spoken(grounding: TyreGrounding) -> str:
    """A plain answer from the tool itself (the mock provider has no model to phrase it)."""
    return grounding.result.summary
