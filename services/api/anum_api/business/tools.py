"""Business tools for the agent runtime (docs/business.md#proposal-acceptance).

* ``business.accept_proposal`` (high risk, always asks; members ask, owners decide): marks one exact proposal
  version accepted. It runs only after an owner approves it in the ordinary approvals flow
  with the payload hash they were shown, and only if the proposal still has that version
  and content hash; anything changed after the request is refused and nothing is accepted.

* ``business.draft_support_reply`` (low risk; owners and members): writes a reply draft on a
  support ticket that came from an email or WhatsApp conversation. It only drafts: the reply
  is delivered when a person asks for it and an owner approves it in the inbox's own approval
  path (docs/business.md#replies-and-approval).

Accepting is an internal record (the client's acceptance confirmed by the owner); it sends
nothing.

* ``business.send_outreach`` (high risk, always asks; ``outreach.py``): sends one approved outbox
  message after the send-time checks. Sending is off by default (docs/business.md#outreach).
* ``web.fetch_page`` (low risk, read only; ``fetch.py``): reads one public https page for the
  prospecting pipeline, obeying robots.txt behind the SSRF guard.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..events import CanonicalEventName, create_event
from ..model_gateway import MockModelGateway
from ..repository import AnumRepository
from ..runtime import AgentRuntime
from ..schemas import AgentRunStep, Approval, RiskLevel, RunPhase, Task, TaskStatus, TenantContext, new_id, utc_now
from . import research
from .models import OPEN_LEAD_STATES, LeadState, Proposal, ProposalStatus, ProposalVersion, Stage
from .service import ACCEPT_TOOL, audit, http_error, proposal_totals, record_activity, stamp_state
from .store import open_business_store


def accept_definition() -> ToolDefinition:
    return ToolDefinition(
        name=ACCEPT_TOOL,
        description="Record that the client accepted one exact proposal version (internal record; sends nothing).",
        risk_level=RiskLevel.HIGH,
        # Members ask; only an owner decides the approval (approval:decide).
        required_roles=frozenset({"owner", "member"}),
        timeout_seconds=30,
        idempotent=True,
        category="general",
        action_kind="other",
        always_ask=True,
    )


def acceptance_arguments(proposal: Proposal, version: ProposalVersion, account_name: str) -> dict[str, Any]:
    """What the approver sees and the payload hash binds: the exact version and money."""
    totals = proposal_totals(version)
    return {
        "proposal_id": proposal.id,
        "title": proposal.title,
        "client": account_name,
        "version": version.version,
        "content_hash": version.content_hash,
        "currency": version.currency.value,
        "subtotal": str(totals["subtotal"]),
        "tax": str(totals["tax"]),
        "tax_note": version.tax_note,
        "total": str(totals["total"]),
        "milestones": [f"{item.title}: {version.currency.value} {item.amount}" for item in version.milestones],
        "deliverables": list(version.deliverables),
        "exclusions": list(version.exclusions),
    }


def _failed(summary: str) -> ToolResult:
    return ToolResult(status="failed", summary=summary)


async def accept_proposal(call: ToolCall, context: TenantContext) -> ToolResult:
    args = call.arguments
    proposal_id = str(args.get("proposal_id", ""))
    approved_hash = str(args.get("content_hash", ""))
    try:
        approved_version = int(args.get("version", 0))
    except (TypeError, ValueError):
        return _failed("The approval names no proposal version; nothing was accepted.")
    now = utc_now()
    with open_business_store(context) as store:
        proposal = store.get("proposals", proposal_id, for_update=True)
        if proposal is None:
            return _failed("The proposal was deleted; nothing was accepted.")
        if proposal.status == ProposalStatus.ACCEPTED:
            same = proposal.accepted_version == approved_version and proposal.accepted_hash == approved_hash
            return ToolResult(status="succeeded" if same else "failed",
                              summary="This version was already accepted." if same else "Another version was already accepted; nothing changed.")
        if proposal.status != ProposalStatus.PENDING_APPROVAL:
            return _failed(f"The proposal is {proposal.status.value.replace('_', ' ')}, not waiting for acceptance; nothing was accepted.")
        version = store.first("proposal_versions", proposal_id=proposal.id, version=approved_version)
        if (
            version is None
            or proposal.current_version != approved_version
            or proposal.requested_version != approved_version
            or proposal.requested_hash != approved_hash
            or version.content_hash != approved_hash
            or not version.locked
        ):
            return _failed("The proposal changed after it was sent for approval; nothing was accepted. Ask for approval again.")
        totals = proposal_totals(version)
        if str(totals["total"]) != str(args.get("total")) or version.currency.value != args.get("currency"):
            return _failed("The proposal's total no longer matches the approval; nothing was accepted.")
        proposal.status = ProposalStatus.ACCEPTED
        proposal.accepted_version = version.version
        proposal.accepted_hash = version.content_hash
        proposal.accepted_by = context.user_id
        proposal.accepted_at = now
        proposal.decision_note = ""
        proposal.updated_at = now
        store.save("proposals", proposal)
        opportunity = store.get("opportunities", proposal.opportunity_id, for_update=True)
        lead_id = None
        if opportunity is not None:
            lead_id = opportunity.lead_id
            opportunity.stage = Stage.WON
            opportunity.won_at = opportunity.won_at or now
            opportunity.lost_at = None
            opportunity.value_amount = totals["subtotal"] if opportunity.currency == version.currency else opportunity.value_amount
            opportunity.probability_pct = Decimal("100")
            opportunity.last_interaction_at = now
            opportunity.updated_at = now
            store.save("opportunities", opportunity)
            if opportunity.lead_id:
                lead = store.get("leads", opportunity.lead_id, for_update=True)
                if lead is not None and lead.state in OPEN_LEAD_STATES:
                    if lead.offered_at is None:
                        lead.offered_at = now
                    stamp_state(lead, LeadState.WON, now)
                    lead.updated_at = now
                    store.save("leads", lead)
        record_activity(
            store, context, "proposal_accepted",
            f"Proposal \"{proposal.title}\" version {version.version} accepted ({version.currency.value} {totals['total']}).",
            proposal_id=proposal.id, opportunity_id=proposal.opportunity_id, account_id=proposal.account_id, lead_id=lead_id,
            metadata={"version": version.version, "content_hash": version.content_hash}, idempotency_key=f"accept:{proposal.id}:{version.version}",
        )
    audit(None, context, "business.proposal.accepted", proposal.id, metadata={"version": version.version, "content_hash": approved_hash})
    return ToolResult(
        status="succeeded",
        summary=f"Accepted: \"{proposal.title}\" version {version.version}, {version.currency.value} {totals['total']}. The opportunity is won; start the project from the proposal.",
        output={"proposal_id": proposal.id, "version": version.version},
        audit_metadata={"proposal_id": proposal.id, "version": version.version},
    )


SUPPORT_REPLY_TOOL = "business.draft_support_reply"


def support_reply_definition() -> ToolDefinition:
    return ToolDefinition(
        name=SUPPORT_REPLY_TOOL,
        description=(
            "Draft a reply to the requester of a Business support ticket (arguments: ticket_id, body). "
            "The reply stays a draft until a person asks to deliver it and an owner approves it; this tool sends nothing."
        ),
        risk_level=RiskLevel.LOW,
        required_roles=frozenset({"owner", "member"}),
        timeout_seconds=30,
        category="general",
        action_kind="draft",
    )


async def draft_support_reply(call: ToolCall, context: TenantContext) -> ToolResult:
    from .api_support import create_reply, reference

    ticket_id = str(call.arguments.get("ticket_id") or "")
    body = str(call.arguments.get("body") or "").strip()
    if not body:
        return _failed("Write the reply text (body); nothing was drafted.")
    try:
        with open_business_store(context) as store:
            ticket = store.get("tickets", ticket_id, for_update=True)
            if ticket is None:
                return _failed("That ticket does not exist in this workspace; nothing was drafted.")
            reply = create_reply(store, context, ticket, body[:4000], "agent")
    except HTTPException as exc:
        return _failed(f"Not drafted: {exc.detail}")
    audit(None, context, "business.ticket.reply_drafted", ticket_id, metadata={"reply_id": reply.id, "generator": "agent", "chars": len(reply.body)})
    return ToolResult(
        status="succeeded",
        summary=f"Reply drafted on ticket {reference(ticket)}. It is not sent: open the ticket, check it and ask for approval.",
        output={"ticket_id": ticket_id, "reply_id": reply.id, "status": reply.status.value},
        audit_metadata={"ticket_id": ticket_id, "reply_id": reply.id},
    )


def register_business_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the business tools to a runtime's registry (the API's and the Temporal worker's).

    The research skill runs its web searches through this same registry."""
    from .fetch import FETCH_TOOL, fetch_definition, page_fetcher
    from .outreach import SEND_TOOL, send_definition, send_outreach

    if ACCEPT_TOOL not in registry.names:
        registry.register(accept_definition(), accept_proposal)
    if SEND_TOOL not in registry.names:
        registry.register(send_definition(), send_outreach)
    if FETCH_TOOL not in registry.names:
        registry.register(fetch_definition(), page_fetcher)
    if SUPPORT_REPLY_TOOL not in registry.names:
        registry.register(support_reply_definition(), draft_support_reply)
    research.configure(registry)
    return registry


def _approval_registry() -> ToolRegistry:
    """The business tools' definitions for planning an approval, without re-pointing the
    research skill at a registry that has no ``web_search`` (``register_business_tools`` does)."""
    from .fetch import fetch_definition, page_fetcher
    from .outreach import send_definition, send_outreach

    registry = ToolRegistry()
    registry.register(accept_definition(), accept_proposal)
    registry.register(send_definition(), send_outreach)
    registry.register(fetch_definition(), page_fetcher)
    return registry


def request_approval(repository: AnumRepository, context: TenantContext, arguments: dict[str, Any], title: str, *,
                     tool: str = ACCEPT_TOOL, prompt: str | None = None) -> tuple[Task, Approval]:
    """A paused call of a business tool (``business.accept_proposal`` or ``business.send_outreach``)
    waiting for an owner (as marketing does)."""
    call = ToolCall(name=tool, arguments=arguments)
    now = utc_now()
    task = Task(
        id=new_id("task"), title=title[:160], prompt=prompt or f"Record the client's acceptance of {title}.", status=TaskStatus.CREATED,
        tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, updated_at=now, created_by=context.user_id,
    )
    repository.create_task(task)
    repository.record_event(create_event(CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now).event)
    runtime = AgentRuntime(MockModelGateway(), repository, tools=_approval_registry())
    run = runtime.new_run(task)
    decision = runtime.evaluate(call, context)
    proposal_step = AgentRunStep(
        id=new_id("step"), type="tool_proposal", summary=f"Proposed tool call: {tool}", created_at=utc_now(),
        metadata={"tool": tool, "risk_level": decision.risk_level.value, "policy_outcome": decision.outcome.value,
                  "governance_rules": list(decision.governance_rules),
                  **({"proposal_id": arguments["proposal_id"]} if "proposal_id" in arguments else {"outbox_id": arguments.get("outbox_id")})},
    )
    run.steps.append(proposal_step)
    run.checkpoint.phase = RunPhase.TOOL_READY
    run.checkpoint.version += 1
    run.checkpoint.tool_call = call.model_dump(mode="json")
    run.checkpoint.last_step_id = proposal_step.id
    task.status = run.status = TaskStatus.RUNNING
    repository.save_task(task)
    repository.save_run(run)
    # The tool always asks; a governance deny (or a missing role) fails the run instead.
    if runtime.begin_execution(task, run, context) is not None:  # pragma: no cover - always_ask never runs unapproved
        raise RuntimeError("proposal acceptance was not paused for approval")
    repository.save_task(task)
    repository.save_run(run)
    if run.checkpoint.phase != RunPhase.WAITING_APPROVAL or not run.checkpoint.approval_id:
        reason = run.steps[-1].summary if run.steps else "Blocked by policy."
        raise http_error(403, f"{'Acceptance' if tool == ACCEPT_TOOL else 'Sending'} is blocked: {reason}")
    approval = repository.get_approval(run.checkpoint.approval_id, context)
    if approval is None:  # pragma: no cover - saved just above
        raise http_error(500, "Approval was not stored")
    return task, approval


def retire_approval(repository: AnumRepository, context: TenantContext, task_id: str | None, approval_id: str | None) -> None:
    from ..marketing.service import retire_approval as retire

    retire(repository, context, task_id, approval_id)
