"""The Call Agent's tools (docs/calls.md#agent-tools, docs/agents.md).

* ``call.search_log``, ``call.get`` and ``call.search_contacts`` read the workspace's call
  log and contacts (low risk, read only, for owners and members). Their output is untrusted
  data with provenance before it reaches a prompt (the runtime labels tool output).
* ``telephony.ai_call`` asks the AI assistant to phone a consenting contact. It is high risk
  and ``always_ask``: every call waits for an owner's approval of the exact arguments, and
  ``service.place_ai_call`` checks them again against the records when it runs.
"""

from __future__ import annotations

from typing import Any

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..authorization import Permission
from ..dependencies import has_permission
from ..schemas import RiskLevel, TenantContext
from .phone import masked
from .service import AI_CALL_DEFINITION, opted_out, place_ai_call
from .store import CallFilter, open_calls_store

_PEOPLE = frozenset({"owner", "member"})
MAX_RESULTS = 20
MAX_TEXT = 4000


def _denied() -> ToolResult:
    return ToolResult(status="blocked", summary="Phone calls are not part of this role.", audit_metadata={"outcome": "denied"})


def _limit(args: dict[str, Any]) -> int:
    try:
        return max(1, min(int(args.get("limit") or 10), MAX_RESULTS))
    except (TypeError, ValueError):
        return 10


def _call_row(entry: Any, *, full: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {
        "call_id": entry.id, "contact": entry.contact_name, "number": masked(entry.phone_normalized or "") if entry.phone_normalized else None,
        "direction": entry.direction.value, "kind": entry.kind.value, "outcome": entry.outcome.value if entry.outcome else None,
        "started_at": entry.started_at.isoformat(), "duration_seconds": entry.duration_seconds,
        "summary": entry.summary, "follow_up": entry.follow_up.text if entry.follow_up else None,
    }
    if full:
        row["notes"] = (entry.notes or "")[:MAX_TEXT] or None
        row["transcript"] = (entry.transcript or "")[:MAX_TEXT] or None
    else:
        row["notes"] = (entry.notes or "")[:300] or None
    return row


async def search_log(call: ToolCall, context: TenantContext) -> ToolResult:
    if not has_permission(context, Permission.CALL_READ):
        return _denied()
    args = call.arguments
    query = str(args.get("query") or "").strip()[:200] or None
    with open_calls_store(context) as store:
        items, total = store.list_calls(CallFilter(query=query, contact_id=str(args.get("contact_id") or "") or None), offset=0, limit=_limit(args))
    return ToolResult(
        status="succeeded",
        summary=f"{total} call(s) in the log" + (" match" if query else "") + f"; showing {len(items)}.",
        output={"calls": [_call_row(item) for item in items], "total": total},
        audit_metadata={"tool": "call.search_log", "results": len(items)},
    )


async def get_call(call: ToolCall, context: TenantContext) -> ToolResult:
    if not has_permission(context, Permission.CALL_READ):
        return _denied()
    with open_calls_store(context) as store:
        entry = store.get_call(str(call.arguments.get("call_id") or ""))
    if entry is None:
        return ToolResult(status="failed", summary="No such call in this workspace's log.", audit_metadata={"tool": "call.get", "found": False})
    return ToolResult(status="succeeded", summary=f"Call with {entry.contact_name or 'an unknown number'}.",
                      output={"call": _call_row(entry, full=True)}, audit_metadata={"tool": "call.get", "found": True})


async def search_contacts(call: ToolCall, context: TenantContext) -> ToolResult:
    if not has_permission(context, Permission.CALL_READ):
        return _denied()
    args = call.arguments
    query = str(args.get("query") or "").strip()[:200] or None
    with open_calls_store(context) as store:
        items, total = store.list_contacts(query=query, favourites=False, offset=0, limit=_limit(args))
        rows = [
            {
                "contact_id": item.id, "name": item.name, "company": item.company, "phone_number": item.phone_normalized,
                "ai_call_consent": item.ai_consent is not None,
                "opted_out_of_ai_calls": opted_out(store, context, item.phone_normalized) is not None,
            }
            for item in items
        ]
    return ToolResult(status="succeeded", summary=f"{total} contact(s); showing {len(rows)}.", output={"contacts": rows, "total": total},
                      audit_metadata={"tool": "call.search_contacts", "results": len(rows)})


def _read(name: str, description: str, properties: dict[str, Any]) -> ToolDefinition:
    return ToolDefinition(
        name=name, description=description, risk_level=RiskLevel.LOW, required_roles=_PEOPLE, timeout_seconds=20,
        idempotent=True, category="calls", action_kind="read",
        input_schema={"type": "object", "properties": properties},
    )


TOOL_DEFINITIONS: list[tuple[ToolDefinition, Any]] = [
    (_read("call.search_log", "Search the workspace's phone call log (who, when, outcome, notes, AI summary). Read only.",
           {"query": {"type": "string"}, "contact_id": {"type": "string"}, "limit": {"type": "integer", "maximum": MAX_RESULTS}}), search_log),
    (_read("call.get", "Read one call from the log: notes, summary, follow-up and transcript. Read only.",
           {"call_id": {"type": "string"}}), get_call),
    (_read("call.search_contacts", "Search the workspace's phone contacts, with whether each consented to AI calls. Read only.",
           {"query": {"type": "string"}, "limit": {"type": "integer", "maximum": MAX_RESULTS}}), search_contacts),
    (AI_CALL_DEFINITION, place_ai_call),
]


def register_call_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the call tools to a runtime's registry (the API's and the Temporal worker's)."""
    for definition, handler in TOOL_DEFINITIONS:
        if definition.name not in registry.names:
            registry.register(definition, handler)
    return registry


def call_tool_registry() -> ToolRegistry:
    return register_call_tools(ToolRegistry())
