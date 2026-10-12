"""Department assistants (docs/team-work.md#department-assistants).

An assistant is a configured agent profile for one department (Sales, Support,
Operations/Warehouse, Finance, Tyre engineering, or custom): instructions, the tools it
may use, an autonomy level and routing keywords. Work items can be assigned to it, and
inbound messages are routed to it by keyword (or explicitly). When its work is handed
off, the agent task carries ``assistant_id`` and :func:`narrow_for_assistant` narrows the
runtime's tool policy: a tool outside the profile is blocked, ``observe`` blocks anything
that is not low risk, ``suggest`` turns every allowed call into an approval. It never
loosens policy: workspace approval rules, governance and approvals still apply.

Only workspace owners create, change or delete assistants (``assistant:manage``);
every member can read them and route messages to them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from .agent_tools import ToolCall, ToolPolicyDecision, ToolPolicyOutcome
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .events import CanonicalEventName
from .schemas import RiskLevel, TenantContext, new_id, utc_now
from .team_models import (
    OPEN_STATUSES,
    ActorType,
    Assignee,
    AssigneeType,
    AssistantProfile,
    AssistantTemplate,
    AssistantWrite,
    Autonomy,
    Department,
    InboundMessage,
    RouteResult,
    WorkItem,
    WorkItemCreate,
    WorkSource,
)
from .team_store import WorkFilter
from .team_work import TeamUnit, _view, create_item, open_team_unit

logger = logging.getLogger(__name__)

# Most messages auto-routed from one sync pass or webhook delivery.
AUTO_ROUTE_BATCH = 50

router = APIRouter(prefix="/api/v1", tags=["assistants"])

_TEMPLATES: tuple[AssistantTemplate, ...] = (
    AssistantTemplate(
        department=Department.SALES, name="Sales assistant",
        instructions=(
            "You help the sales team sell tyres on Amazon.sa, Amazon.ae, Noon and the company's own "
            "app and website. Draft quotes and replies, summarise leads and orders, and propose "
            "follow-ups. Never change a price, listing or stock level yourself: propose it for approval."
        ),
        allowed_tools=["anum.respond", "team.assign_task", "external.action"],
        routing_keywords=["price", "quote", "order", "buy", "discount", "offer", "bulk", "سعر", "عرض", "طلب"],
        autonomy=Autonomy.SUGGEST,
    ),
    AssistantTemplate(
        department=Department.SUPPORT, name="Support assistant",
        instructions=(
            "You help customers after a sale: delivery status, returns, warranty and complaints. "
            "Be polite and specific, quote the order number, and hand anything you cannot resolve "
            "to a person with a work item."
        ),
        allowed_tools=["anum.respond", "team.assign_task"],
        routing_keywords=["return", "refund", "complaint", "warranty", "damaged", "late", "delivery", "استرجاع", "ضمان", "شكوى", "توصيل"],
        autonomy=Autonomy.SUGGEST,
    ),
    AssistantTemplate(
        department=Department.OPERATIONS, name="Operations and warehouse assistant",
        instructions=(
            "You help the warehouse: stock counts, inbound shipments from suppliers, picking and "
            "dispatch to marketplace fulfilment centres. Flag low stock and late shipments and "
            "assign follow-ups to the right person."
        ),
        allowed_tools=["anum.respond", "team.assign_task"],
        routing_keywords=["stock", "inventory", "warehouse", "shipment", "supplier", "pickup", "container", "مخزون", "شحنة", "مورد", "مستودع"],
        autonomy=Autonomy.ACT_LOW_RISK,
    ),
    AssistantTemplate(
        department=Department.FINANCE, name="Finance assistant",
        instructions=(
            "You help finance: invoices, supplier payments, marketplace settlements and VAT. "
            "Summarise and reconcile; never pay or move money yourself."
        ),
        allowed_tools=["anum.respond", "team.assign_task"],
        routing_keywords=["invoice", "payment", "vat", "settlement", "bank", "receipt", "فاتورة", "دفع", "ضريبة"],
        autonomy=Autonomy.SUGGEST,
    ),
    AssistantTemplate(
        department=Department.TYRE_ENGINEERING, name="Tyre engineering assistant",
        instructions=(
            "You support tyre planning engineering: sizes, load index, speed rating, fitment, "
            "tread and pressure questions, and planning notes from TyrePulse. Explain your "
            "reasoning and cite the specification you rely on. Read and advise only."
        ),
        allowed_tools=["anum.respond"],
        routing_keywords=["size", "load index", "speed rating", "fitment", "tread", "pressure", "tyrepulse", "مقاس", "ضغط"],
        autonomy=Autonomy.OBSERVE,
    ),
)


def registered_tools() -> set[str]:
    from .main import tool_registry

    return set(tool_registry.names)


def templates() -> list[AssistantTemplate]:
    tools = registered_tools()
    return [t.model_copy(update={"allowed_tools": [x for x in t.allowed_tools if x in tools]}) for t in _TEMPLATES]


def _check_tools(payload: AssistantWrite) -> None:
    unknown = sorted(set(payload.allowed_tools) - registered_tools())
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown tools for this deployment: {', '.join(unknown)}")


def narrow_for_assistant(
    decision: ToolPolicyDecision, call: ToolCall, context: TenantContext, assistant_id: str
) -> ToolPolicyDecision:
    """Apply the assistant's profile on top of the runtime's decision; only ever stricter."""
    if decision.outcome == ToolPolicyOutcome.BLOCK:
        return decision
    with open_team_unit(context) as unit:
        assistant = unit.store.get_assistant(context, assistant_id)
    if assistant is None or not assistant.enabled:
        return ToolPolicyDecision(
            outcome=ToolPolicyOutcome.BLOCK, reason="The department assistant for this task is missing or turned off.",
            risk_level=RiskLevel.BLOCKED,
        )
    if call.name not in assistant.allowed_tools:
        return ToolPolicyDecision(
            outcome=ToolPolicyOutcome.BLOCK,
            reason=f"{assistant.name} is not allowed to use {call.name}. An owner can add it under Assistants.",
            risk_level=RiskLevel.BLOCKED,
        )
    if assistant.autonomy == Autonomy.OBSERVE and decision.risk_level != RiskLevel.LOW:
        return ToolPolicyDecision(
            outcome=ToolPolicyOutcome.BLOCK,
            reason=f"{assistant.name} is set to observe only and may use low-risk tools only.",
            risk_level=RiskLevel.BLOCKED,
        )
    if assistant.autonomy == Autonomy.SUGGEST and decision.outcome == ToolPolicyOutcome.ALLOW and call.name != "anum.respond":
        return decision.model_copy(
            update={
                "outcome": ToolPolicyOutcome.REQUIRE_APPROVAL,
                "reason": f"{assistant.name} suggests actions only; a person approves each one.",
            }
        )
    return decision


def _require_assistant_admin(context: TenantContext) -> None:
    if not has_permission(context, Permission.ASSISTANT_MANAGE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace owners can create or change department assistants.",
        )


@router.get("/assistants/templates", response_model=list[AssistantTemplate])
def list_templates(context: TenantContext = Depends(tenant_context)) -> list[AssistantTemplate]:
    require_permission(context, Permission.WORK_READ)
    return templates()


@router.get("/assistants", response_model=list[AssistantProfile])
def list_assistants(context: TenantContext = Depends(tenant_context)) -> list[AssistantProfile]:
    require_permission(context, Permission.WORK_READ)
    with open_team_unit(context, require_membership=True) as unit:
        return unit.store.list_assistants(context)


@router.post("/assistants", response_model=AssistantProfile, status_code=status.HTTP_201_CREATED)
def create_assistant(payload: AssistantWrite, context: TenantContext = Depends(tenant_context)) -> AssistantProfile:
    _require_assistant_admin(context)
    _check_tools(payload)
    now = utc_now()
    with open_team_unit(context, require_membership=True) as unit:
        unit.require_workspace()
        if any(a.name.casefold() == payload.name.casefold() for a in unit.store.list_assistants(context)):
            raise HTTPException(status_code=409, detail=f"An assistant called {payload.name} already exists")
        assistant = AssistantProfile(
            id=new_id("asst"), **payload.model_dump(exclude={"inbound_channels"}), inbound_channels=payload.inbound_channels or [],
            created_by=context.user_id, created_at=now, updated_at=now,
        )
        unit.store.save_assistant(context, assistant)
        unit.log("assistant.create", f"{assistant.name} was set up ({assistant.autonomy.value.replace('_', ' ')})",
                 item_id=None, event=CanonicalEventName.ASSISTANT_CHANGED,
                 payload={"assistant_id": assistant.id, "change": "created", "tools": assistant.allowed_tools})
        return assistant


@router.get("/assistants/{assistant_id}", response_model=AssistantProfile)
def get_assistant(assistant_id: str, context: TenantContext = Depends(tenant_context)) -> AssistantProfile:
    require_permission(context, Permission.WORK_READ)
    with open_team_unit(context, require_membership=True) as unit:
        found = unit.store.get_assistant(context, assistant_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    return found


@router.put("/assistants/{assistant_id}", response_model=AssistantProfile)
def update_assistant(assistant_id: str, payload: AssistantWrite, context: TenantContext = Depends(tenant_context)) -> AssistantProfile:
    _require_assistant_admin(context)
    _check_tools(payload)
    with open_team_unit(context, require_membership=True) as unit:
        found = unit.store.get_assistant(context, assistant_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Assistant not found")
        if any(a.id != assistant_id and a.name.casefold() == payload.name.casefold() for a in unit.store.list_assistants(context)):
            raise HTTPException(status_code=409, detail=f"An assistant called {payload.name} already exists")
        changes = payload.model_dump(exclude={"inbound_channels"})
        if payload.inbound_channels is not None:
            changes["inbound_channels"] = payload.inbound_channels
        updated = found.model_copy(update={**changes, "updated_at": utc_now()})
        unit.store.save_assistant(context, updated)
        unit.log("assistant.update", f"{updated.name} was updated", item_id=None,
                 event=CanonicalEventName.ASSISTANT_CHANGED,
                 payload={"assistant_id": assistant_id, "change": "updated", "enabled": updated.enabled,
                          "autonomy": updated.autonomy.value, "tools": updated.allowed_tools,
                          "inbound_channels": list(updated.inbound_channels)})
        return updated


@router.delete("/assistants/{assistant_id}", status_code=204, response_model=None)
def delete_assistant(assistant_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Delete an assistant. Refused while open work is assigned to it (turn it off instead)."""
    _require_assistant_admin(context)
    with open_team_unit(context, require_membership=True) as unit:
        found = unit.store.get_assistant(context, assistant_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Assistant not found")
        from .team_models import OPEN_STATUSES
        from .team_store import WorkFilter

        _, open_count = unit.store.query_items(
            context, WorkFilter(assignee_type=AssigneeType.ASSISTANT, assignee_id=assistant_id, statuses=frozenset(OPEN_STATUSES)), 1, 0
        )
        if open_count:
            raise HTTPException(
                status_code=409,
                detail=f"{found.name} still has {open_count} open work item(s). Reassign them or turn the assistant off instead.",
            )
        unit.store.delete_assistant(context, assistant_id)
        unit.log("assistant.delete", f"{found.name} was deleted", item_id=None, event=CanonicalEventName.ASSISTANT_CHANGED,
                 payload={"assistant_id": assistant_id, "change": "deleted"})
    return Response(status_code=204)


def _score(assistant: AssistantProfile, text: str) -> list[str]:
    lowered = text.casefold()
    hits = []
    for keyword in assistant.routing_keywords:
        if keyword.isascii():
            if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                hits.append(keyword)
        elif keyword in lowered:
            hits.append(keyword)
    return hits


def _best_match(assistants: list[AssistantProfile], text: str) -> tuple[AssistantProfile | None, list[str]]:
    scored = sorted(((len(h), a, h) for a in assistants if (h := _score(a, text))), key=lambda x: (-x[0], x[1].name.casefold()))
    return (scored[0][1], scored[0][2]) if scored else (None, [])


def route_message(
    unit: TeamUnit, payload: InboundMessage, *, external_ref: str | None = None, automatic: bool = False
) -> tuple[AssistantProfile | None, list[str], WorkItem] | None:
    """Create the work item for one inbound message; shared by the endpoint and auto-routing.

    By hand: the named assistant, else the best keyword match, else an unassigned item for
    triage. Automatic (``automatic=True``, on arrival): only enabled assistants that list the
    channel in ``inbound_channels`` and whose keywords match; no match, or an open item
    already tracking the same conversation (``external_ref``) for that assistant, creates
    nothing and returns ``None``. The message is stored as the item's description: customer
    data, never instructions, and it never chooses tools.
    """
    context = unit.context
    assistants = [a for a in unit.store.list_assistants(context) if a.enabled]
    chosen: AssistantProfile | None = None
    hits: list[str] = []
    text = f"{payload.subject}\n{payload.body}"
    if automatic:
        chosen, hits = _best_match([a for a in assistants if payload.channel in a.inbound_channels], text)
        if chosen is None:
            return None
        if external_ref:
            open_items, _ = unit.store.query_items(
                context,
                WorkFilter(assignee_type=AssigneeType.ASSISTANT, assignee_id=chosen.id, statuses=frozenset(OPEN_STATUSES), sort="updated"),
                200, 0,
            )
            if any(
                i.source == WorkSource.INBOUND
                and i.source_detail.get("channel") == payload.channel
                and i.source_detail.get("external_ref") == external_ref
                for i in open_items
            ):
                return None
    elif payload.assistant_id:
        chosen = next((a for a in assistants if a.id == payload.assistant_id), None)
        if chosen is None:
            raise HTTPException(status_code=422, detail="That assistant is missing or turned off")
    else:
        chosen, hits = _best_match(assistants, text)
    title = payload.subject.strip() or payload.body.strip().splitlines()[0][:120]
    detail: dict[str, Any] = {"channel": payload.channel, "sender": payload.sender[:200], "matched_keywords": hits}
    if automatic:
        detail["automatic"] = True
    if external_ref:
        detail["external_ref"] = external_ref[:200]
    actor_type = ActorType.ASSISTANT if chosen else ActorType.ANUM
    actor_id = chosen.id if chosen else "anum"
    item = create_item(
        unit,
        WorkItemCreate(
            title=f"{payload.channel.title()} from {payload.sender}: {title}"[:200],
            description=payload.body,
            assignee=Assignee(type=AssigneeType.ASSISTANT, id=chosen.id) if chosen else None,
        ),
        source=WorkSource.INBOUND,
        source_detail=detail,
        actor_type=actor_type,
        actor_id=actor_id,
    )
    unit.log(
        "assistant.route",
        f"Inbound {payload.channel} message routed to {chosen.name}" if chosen else f"Inbound {payload.channel} message needs triage",
        item_id=item.id, event=CanonicalEventName.ASSISTANT_ROUTED,
        payload={"assistant_id": chosen.id if chosen else None, "channel": payload.channel, "automatic": automatic},
        actor_type=actor_type, actor_id=actor_id,
    )
    return chosen, hits, item


@router.post("/assistants/route", response_model=RouteResult, status_code=201)
def route_inbound(payload: InboundMessage, context: TenantContext = Depends(tenant_context)) -> RouteResult:
    """Turn an inbound message into a work item for the best-matching assistant.

    The assistant is the one named in ``assistant_id``, else the enabled assistant whose
    routing keywords match the subject and body most often (ties: alphabetical). With no
    match the work item stays unassigned for a person to triage.
    """
    require_permission(context, Permission.WORK_MANAGE)
    with open_team_unit(context, require_membership=True) as unit:
        routed = route_message(unit, payload)
        if routed is None:  # only automatic routing returns None
            raise HTTPException(status_code=500, detail="The message could not be routed")
        chosen, hits, item = routed
        view = _view(unit, item)
    return RouteResult(assistant=chosen, matched_keywords=hits, work_item=view)


@dataclass(frozen=True)
class ArrivedMessage:
    """One new inbound message handed over by a channel (email sync, WhatsApp webhook)."""

    sender: str
    body: str
    subject: str = ""
    external_ref: str | None = None


def auto_route_inbound(tenant_id: str, workspace_id: str, channel: str, messages: list[ArrivedMessage]) -> int:
    """Route newly arrived messages to assistants that opted into the channel. Best effort.

    Runs after the channel has stored the messages (a background task or the end of a
    sync): any failure is logged without message content and never reaches ingestion.
    Each routed message commits in its own transaction as ANUM in the workspace's RLS
    context. Returns how many work items were created.
    """
    if not messages:
        return 0
    created = 0
    try:
        from .notifications import deliver_pending_email
        from .team_work import _system_context

        context = _system_context(tenant_id, workspace_id)
        with open_team_unit(context) as unit:
            if not any(a.enabled and channel in a.inbound_channels for a in unit.store.list_assistants(context)):
                return 0
        for message in messages[:AUTO_ROUTE_BATCH]:
            body = message.body.strip()[:8000]
            if not body:
                continue
            try:
                payload = InboundMessage(channel=channel, sender=(message.sender.strip() or "unknown")[:200],
                                         subject=message.subject.strip()[:200], body=body)
                with open_team_unit(context) as unit:
                    routed = route_message(unit, payload, external_ref=message.external_ref, automatic=True)
                    pending = list(unit.pending_email)
                if routed is None:
                    continue
                created += 1
                if pending:
                    deliver_pending_email(context, pending)
            except Exception as exc:  # one bad message never stops the rest
                logger.warning("assistant_auto_route_failed channel=%s error_class=%s", channel, type(exc).__name__,
                               extra={"anum_assistant_route": {"event": "failed", "channel": channel}})
    except Exception as exc:
        logger.warning("assistant_auto_route_unavailable channel=%s error_class=%s", channel, type(exc).__name__,
                       extra={"anum_assistant_route": {"event": "unavailable", "channel": channel}})
    return created
