"""The email and WhatsApp channels of the generic delivery path (docs/tools-and-integrations.md#delivery).

Two tools take the delivery arguments ``{"to", "subject", "text", "attachments"}`` that
``delivery.request_delivery`` plans, so a tyre report (or anything else that registers an
attachment resolver) can leave ANUM by email or WhatsApp. Both are high risk and
``always_ask``: every send waits for a person's approval bound to the payload hash, and
workspace autonomy never lowers that. What is sent is what was approved: each attachment is
resolved again in the same tenant context and refused when its SHA-256 changed.

* ``deliver_by_email`` sends one new email through the server's mail settings
  (``ANUM_SMTP_*``, the same sender as notifications) with the files attached. It is
  available when the server has a mail server set up.
* ``deliver_by_whatsapp`` uploads each file to Meta and sends it as a document to a number
  that wrote to the workspace's WhatsApp number in the last 24 hours (WhatsApp refuses
  business-initiated documents outside that window). It is available only when the
  workspace has its own WhatsApp number connected and checked.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from . import delivery
from .agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from .schemas import RiskLevel, TenantContext, utc_now

EMAIL_TOOL = "deliver_by_email"
WHATSAPP_TOOL = "deliver_by_whatsapp"
MAX_ATTACHMENTS = 3
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT = 20_000
# WhatsApp's document caption limit.
MAX_CAPTION = 1024
EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")
PEOPLE = frozenset({"owner", "member"})


def email_unavailable(_context: TenantContext) -> str | None:
    from .notifications import email_configured

    if email_configured():
        return None
    return (
        "No email integration is connected: this ANUM server has no mail server set up (ANUM_SMTP_HOST and "
        "ANUM_SMTP_FROM). Ask the server's operator to set one up; until then, download the report and send it yourself."
    )


def whatsapp_unavailable(context: TenantContext) -> str | None:
    from .whatsapp import setup_problems
    from .whatsapp_store import AccountStatus, open_whatsapp_store

    if setup_problems():
        return "No whatsapp integration is connected: WhatsApp is not set up on this ANUM server."
    with open_whatsapp_store(context) as store:
        account = store.get_account()
    if account is None:
        return (
            "No whatsapp integration is connected for this workspace. An owner connects the workspace's number under "
            "WhatsApp, Settings; until then, send the report by email or download it."
        )
    if account.status != AccountStatus.CONNECTED:
        return "The workspace's WhatsApp number is not working yet. An owner checks it under WhatsApp, Settings."
    return None


def _failed(summary: str, **output: Any) -> ToolResult:
    return ToolResult(status="failed", summary=summary, output=output, audit_metadata={"outcome": "not_sent"})


def _references(call: ToolCall) -> list[dict[str, Any]] | str:
    raw = call.arguments.get("attachments") or []
    if not isinstance(raw, list) or len(raw) > MAX_ATTACHMENTS or not all(isinstance(item, dict) for item in raw):
        return f"Not sent: at most {MAX_ATTACHMENTS} attachments, each a reference."
    return raw


def _resolve_all(references: list[dict[str, Any]], context: TenantContext) -> list[tuple[str, str, bytes]]:
    files = []
    for reference in references:
        attachment = delivery.resolve_attachment(reference, context)
        files.append((attachment.filename, attachment.content_type, attachment.content))
    if sum(len(content) for _, _, content in files) > MAX_ATTACHMENT_BYTES:
        raise ValueError("The attachments are larger than 10 MB together.")
    return files


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


async def email_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    """Send one approved email with the approved attachments through the server's mail settings."""
    from .notifications import send_with_retries

    to = str(call.arguments.get("to") or "").strip()
    if not EMAIL.fullmatch(to):
        return _failed("Not sent: the recipient is not one email address.")
    subject = _one_line(call.arguments.get("subject"), 200) or "From ANUM"
    text = str(call.arguments.get("text") or "")[:MAX_TEXT]
    references = _references(call)
    if isinstance(references, str):
        return _failed(references)
    reason = await asyncio.to_thread(email_unavailable, context)
    if reason:
        return _failed(f"Not sent: {reason}")
    try:
        files = await asyncio.to_thread(_resolve_all, references, context)
    except (LookupError, ValueError) as exc:
        return _failed(f"Not sent: {exc}")
    outcome = await asyncio.to_thread(send_with_retries, to, subject, text, attachments=files)
    if outcome.status != "sent":
        return _failed(f"Not sent: {outcome.error or 'the mail server refused the message'}.", attempts=outcome.attempts)
    return ToolResult(
        status="succeeded",
        summary=f"Emailed {subject} to {to}.",
        output={"to": to, "attachments": [name for name, _, _ in files], "attempts": outcome.attempts},
        audit_metadata={"outcome": "sent", "channel": "email", "attachments": len(files)},
    )


def _whatsapp_target(context: TenantContext, digits: str) -> tuple[Any, Any]:
    from .whatsapp_store import open_whatsapp_store

    with open_whatsapp_store(context) as store:
        return store.get_account(include_secrets=True), store.conversation_for(digits)


async def whatsapp_tool(call: ToolCall, context: TenantContext) -> ToolResult:
    """Send the approved files as WhatsApp documents to a number inside its 24-hour window."""
    from .whatsapp import _outbound, _settle_message, graph_client
    from .whatsapp_graph import WhatsAppGraphError
    from .whatsapp_store import MessageStatus

    digits = re.sub(r"\D", "", str(call.arguments.get("to") or ""))
    if not 7 <= len(digits) <= 20:
        return _failed("Not sent: the recipient is not one phone number in international format.")
    references = _references(call)
    if isinstance(references, str):
        return _failed(references)
    if not references:
        return _failed("Not sent: WhatsApp delivery sends files, and there is none.")
    reason = await asyncio.to_thread(whatsapp_unavailable, context)
    if reason:
        return _failed(f"Not sent: {reason}")
    account, conversation = await asyncio.to_thread(_whatsapp_target, context, digits)
    if account is None or account.access_token is None:
        return _failed("Not sent: WhatsApp is not connected for this workspace.")
    if conversation is None or not conversation.is_window_open(utc_now()):
        return _failed(
            "Not sent: WhatsApp lets a business send a document only within 24 hours after the person last wrote to "
            "the workspace's number. Ask them to send any message first, or send the report by email."
        )
    try:
        files = await asyncio.to_thread(_resolve_all, references, context)
    except (LookupError, ValueError) as exc:
        return _failed(f"Not sent: {exc}")
    caption = (_one_line(call.arguments.get("subject"), 200) + "\n\n" + str(call.arguments.get("text") or "")).strip()[:MAX_CAPTION]
    client = graph_client(account)
    sent: list[str] = []
    for index, (filename, content_type, content) in enumerate(files):
        fallback = _outbound(context, conversation, body=f"{filename}\n{caption}" if index == 0 else filename, kind="document",
                             status_=MessageStatus.SENDING)
        try:
            media_id = await client.upload_media(account.phone_number_id, filename, content_type, content)
            wamid = await client.send_document(account.phone_number_id, conversation.wa_id, media_id, filename,
                                               caption if index == 0 else "")
        except WhatsAppGraphError as exc:
            await asyncio.to_thread(_settle_message, context, None, conversation, wamid=None, error="Not sent: " + exc.message, fallback=fallback)
            return _failed("Not sent: " + exc.message, sent=sent, meta_code=exc.code)
        await asyncio.to_thread(_settle_message, context, None, conversation, wamid=wamid, error=None, fallback=fallback)
        sent.append(wamid)
    return ToolResult(
        status="succeeded",
        summary=f"Sent {len(sent)} file(s) on WhatsApp to {conversation.contact_name or conversation.wa_id}.",
        output={"conversation_id": conversation.id, "wamids": sent},
        audit_metadata={"outcome": "sent", "channel": "whatsapp", "attachments": len(sent)},
    )


def tool_definitions() -> list[ToolDefinition]:
    from .settings import settings
    from .whatsapp import graph_host

    smtp_host = (settings.smtp_host or "").strip() or None
    return [
        ToolDefinition(
            name=EMAIL_TOOL, description="Send a file ANUM produced (a report) to one email address, with the file attached.",
            risk_level=RiskLevel.HIGH, required_roles=PEOPLE, timeout_seconds=120, target=smtp_host, category="email",
            action_kind="send", delivery_channel="email", always_ask=True,
        ),
        ToolDefinition(
            name=WHATSAPP_TOOL, description="Send a file ANUM produced (a report) as a WhatsApp document to one number.",
            risk_level=RiskLevel.HIGH, required_roles=PEOPLE, timeout_seconds=120, target=graph_host(), category="whatsapp",
            action_kind="send", delivery_channel="whatsapp", always_ask=True,
        ),
    ]


HANDLERS = {EMAIL_TOOL: email_tool, WHATSAPP_TOOL: whatsapp_tool}
AVAILABILITY = {EMAIL_TOOL: email_unavailable, WHATSAPP_TOOL: whatsapp_unavailable}


def register_delivery_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the two delivery tools to a runtime's registry (the API's and the worker's)."""
    for definition in tool_definitions():
        if definition.name not in registry.names:
            registry.register(definition, HANDLERS[definition.name])
        delivery.register_availability(definition.name, AVAILABILITY[definition.name])
    return registry
