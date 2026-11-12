"""The generic path for sending something ANUM produced to someone outside it.

A connector that can deliver (an email or WhatsApp integration) registers a tool whose
``ToolDefinition.delivery_channel`` names the channel (``email``, ``whatsapp``). Features
that want to send something, such as tyre reports, never call a connector directly:
:func:`request_delivery` plans a call to that channel's tool as an ordinary task run, so
``ToolPolicy`` decides it like any other tool call (sending externally is high risk, so
it waits for an approval bound to the payload hash) and the runtime executes it.

The call's arguments are ``{"to", "subject", "text", "attachments"}``. Attachments are
references, not bytes: ``{"kind", "id", "format", "filename", "content_type",
"sha256", "size_bytes"}``. The tool turns a reference into bytes with
:func:`resolve_attachment` in the same tenant context, and refuses one whose SHA-256
differs from the approved reference. See docs/tools-and-integrations.md#delivery.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .agent_tools import ToolCall, ToolRegistry, ToolResult
from .schemas import TenantContext

AttachmentResolver = Callable[[TenantContext, str, str], tuple[str, str, bytes] | None]
# Whether a delivery tool can send for a workspace now: None when it can, else a sentence
# saying what to connect (the email tool needs the server's mail settings, the WhatsApp tool
# the workspace's own WhatsApp connection). A tool without a check is always available.
AvailabilityCheck = Callable[[TenantContext], str | None]

_resolvers: dict[str, AttachmentResolver] = {}
_availability: dict[str, AvailabilityCheck] = {}
_registry: ToolRegistry | None = None


class DeliveryUnavailableError(LookupError):
    """No tool can deliver on the channel; the message says what to connect."""


def configure(registry: ToolRegistry) -> None:
    """Remember the process's tool registry (the API's and the worker's)."""
    global _registry
    _registry = registry


def registry() -> ToolRegistry | None:
    return _registry


def register_availability(tool_name: str, check: AvailabilityCheck) -> None:
    """Say when ``tool_name`` can deliver for a workspace (see ``AvailabilityCheck``)."""
    _availability[tool_name] = check


def unavailable_reason(tool_name: str, context: TenantContext) -> str | None:
    check = _availability.get(tool_name)
    return check(context) if check is not None else None


def channels(tools: ToolRegistry | None = None, context: TenantContext | None = None) -> dict[str, str]:
    """channel -> tool name, for every registered delivery tool. With ``context``, only the
    tools that can deliver for that workspace now (their availability check may read the
    database: call it in a worker thread from async code)."""
    tools = tools or _registry
    if tools is None:
        return {}
    found: dict[str, str] = {}
    for name in sorted(tools.names):
        definition = tools.definition(name)
        if definition is None or not definition.delivery_channel or definition.delivery_channel in found:
            continue
        if context is not None and unavailable_reason(name, context) is not None:
            continue
        found[definition.delivery_channel] = name
    return found


def missing_reason(channel: str, context: TenantContext, tools: ToolRegistry | None = None) -> str:
    """Why nothing can deliver on ``channel`` for this workspace: the first registered tool's
    own reason, else that no integration is connected."""
    tools = tools or _registry
    generic = (
        f"No {channel} integration is connected, so nothing can be sent. Connect one in Integrations; "
        "until then, download the report and send it yourself."
    )
    if tools is None:
        return generic
    for name in sorted(tools.names):
        definition = tools.definition(name)
        if definition is not None and definition.delivery_channel == channel:
            reason = unavailable_reason(name, context)
            if reason:
                return reason
    return generic


def register_attachment_resolver(kind: str, resolver: AttachmentResolver) -> None:
    _resolvers[kind] = resolver


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    content: bytes


def attachment_reference(kind: str, item_id: str, fmt: str, context: TenantContext) -> dict[str, Any]:
    """A reference to attach, with the hash and size of the bytes it resolves to now."""
    resolved = _resolve(kind, item_id, fmt, context)
    if resolved is None:
        raise LookupError(f"{kind} {item_id} not found")
    filename, content_type, content = resolved
    return {
        "kind": kind,
        "id": item_id,
        "format": fmt,
        "filename": filename,
        "content_type": content_type,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _resolve(kind: str, item_id: str, fmt: str, context: TenantContext) -> tuple[str, str, bytes] | None:
    resolver = _resolvers.get(kind)
    return resolver(context, item_id, fmt) if resolver else None


def resolve_attachment(reference: dict[str, Any], context: TenantContext) -> Attachment:
    """Bytes for an approved reference; raises if missing or changed since approval."""
    resolved = _resolve(str(reference.get("kind")), str(reference.get("id")), str(reference.get("format")), context)
    if resolved is None:
        raise LookupError("The attachment no longer exists.")
    filename, content_type, content = resolved
    if hashlib.sha256(content).hexdigest() != reference.get("sha256"):
        raise ValueError("The attachment changed after it was approved; nothing was sent.")
    return Attachment(filename=filename, content_type=content_type, content=content)


@dataclass(frozen=True)
class DeliveryOutcome:
    """``status``: waiting_approval, sent, failed or blocked."""

    status: str
    task_id: str
    approval_id: str | None
    payload_hash: str | None
    summary: str


async def request_delivery(
    context: TenantContext,
    units: Any,
    *,
    channel: str,
    to: str,
    subject: str,
    text: str,
    attachments: list[dict[str, Any]],
    title: str,
) -> DeliveryOutcome:
    """Plan the channel's tool call as a task run and let policy decide it.

    ``units`` opens a short unit of work for a tenant context (``dependencies.units_of_work``).
    The plan and policy decision commit in one unit, the tool runs with no transaction open,
    and its outcome is written in another; every store step runs in a worker thread
    (docs/realtime.md#event-loop)."""
    from .dependencies import run_in_unit
    from .events import CanonicalEventName, create_event
    from .model_gateway import MockModelGateway
    from .runtime import AgentRuntime, finish_execution_in_units
    from .schemas import AgentRunStep, RunPhase, Task, TaskStatus, new_id, utc_now

    tools = _registry
    tool_name = (await asyncio.to_thread(channels, tools, context)).get(channel) if tools is not None else None
    if tools is None or tool_name is None:
        raise DeliveryUnavailableError(await asyncio.to_thread(missing_reason, channel, context, tools))
    call = ToolCall(
        name=tool_name,
        arguments={"to": to, "subject": subject, "text": text, "attachments": attachments},
    )

    def runtime_for(repository: Any) -> AgentRuntime:
        return AgentRuntime(MockModelGateway(), repository, tools=tools)

    def propose(repository: Any) -> tuple[Any, Any, ToolCall | None, DeliveryOutcome | None]:
        now = utc_now()
        task = Task(
            id=new_id("task"),
            title=title[:160],
            prompt=f"Send '{subject}' to {to} by {channel}.",
            status=TaskStatus.CREATED,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            created_at=now,
            updated_at=now,
            created_by=context.user_id,
        )
        repository.create_task(task)
        repository.record_event(
            create_event(CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title},
                         correlation_id=task.id, created_at=now).event
        )
        runtime = runtime_for(repository)
        run = runtime.new_run(task)
        decision = runtime.evaluate(call, context)
        proposal = AgentRunStep(
            id=new_id("step"),
            type="tool_proposal",
            summary=f"Proposed tool call: {tool_name}",
            created_at=utc_now(),
            metadata={
                "tool": tool_name,
                "risk_level": decision.risk_level.value,
                "policy_outcome": decision.outcome.value,
                "governance_rules": list(decision.governance_rules),
                "delivery_channel": channel,
            },
        )
        run.steps.append(proposal)
        run.checkpoint.phase = RunPhase.TOOL_READY
        run.checkpoint.version += 1
        run.checkpoint.tool_call = call.model_dump(mode="json")
        run.checkpoint.last_step_id = proposal.id
        task.status = run.status = TaskStatus.RUNNING
        repository.save_task(task)
        repository.save_run(run)
        ready = runtime.begin_execution(task, run, context)
        repository.save_task(task)
        repository.save_run(run)
        if ready is not None:
            return task, run, ready, None
        if run.checkpoint.phase == RunPhase.WAITING_APPROVAL and run.checkpoint.approval_id:
            approval = repository.get_approval(run.checkpoint.approval_id, context)
            return task, run, None, DeliveryOutcome(
                "waiting_approval", task.id, run.checkpoint.approval_id,
                getattr(approval, "payload_hash", None), "Waiting for approval.",
            )
        reason = run.steps[-1].summary if run.steps else "Blocked by policy."
        return task, run, None, DeliveryOutcome("blocked", task.id, None, None, reason)

    task, run, ready, outcome = await run_in_unit(units, context, propose)
    if outcome is not None or ready is None:
        return outcome or DeliveryOutcome("blocked", task.id, None, None, "Blocked by policy.")
    # The executing checkpoint is committed; the tool runs with no transaction open.
    run = await finish_execution_in_units(runtime_for, units, task, run, context, ready)
    ok = run.status == TaskStatus.COMPLETED
    return DeliveryOutcome("sent" if ok else "failed", task.id, None, None, run.result or "")


def result_summary(result: ToolResult) -> str:
    return result.summary
