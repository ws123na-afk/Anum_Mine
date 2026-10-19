"""Brevo tools for the agent runtime, chat and voice (docs/brevo.md#agent-tools).

* ``brevo.list_lists``, ``brevo.list_campaigns``, ``brevo.campaign_report`` (low risk,
  read-only): the workspace's contact lists, recent campaigns with their numbers, and one
  campaign's report. Everything Brevo returns is untrusted data: the output names its
  provenance and reaches a model only through ``tool_output_prompt_block``.
* ``brevo.draft_campaign`` (low risk, draft): writes a campaign draft in ANUM for a person
  to review. Nothing is created in Brevo and nothing is sent.
* ``brevo.send_campaign`` (high risk, ``always_ask``, protected ``publish`` kind): sends or
  schedules one draft through Brevo. It runs only after an owner approved the exact draft
  with the payload hash they were shown, and only if the draft, its lists, its sender and
  its content still match the approval. One Brevo campaign per draft, whatever happens.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..schemas import RiskLevel, TenantContext, new_id, utc_now
from .client import HOST, BrevoClient, BrevoError
from .models import (
    MAX_LISTS,
    BrevoConnection,
    CampaignDraft,
    ConnectionStatus,
    DraftInput,
    DraftStatus,
    SendError,
)
from .service import (
    CAMPAIGNS_TOOL,
    DRAFT_TOOL,
    LISTS_TOOL,
    REPORT_TOOL,
    SEND_TOOL,
    audit,
    campaign_tag,
    client_for,
    content_hash,
    draft_hash,
    load_connection,
)
from .compose import describe
from .store import open_brevo_store

PROVENANCE = {"source": "tool_output", "origin": HOST, "trust": "untrusted"}
_PEOPLE = frozenset({"owner", "member"})


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def send_definition() -> ToolDefinition:
    return ToolDefinition(
        name=SEND_TOOL,
        description="Send or schedule one approved Brevo email campaign draft to its contact lists.",
        risk_level=RiskLevel.HIGH,
        # Members ask; only an owner decides the approval (approval:decide).
        required_roles=_PEOPLE,
        timeout_seconds=120,
        target=HOST,
        category="marketing",
        # A protected kind: autonomy always asks; always_ask keeps every call waiting for a person.
        action_kind="publish",
        always_ask=True,
    )


def definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=LISTS_TOOL, description="List the workspace's Brevo contact lists with their subscriber counts (read-only).",
            risk_level=RiskLevel.LOW, timeout_seconds=30, idempotent=True, target=HOST, category="marketing", action_kind="read",
            input_schema=_object({"limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
        ),
        ToolDefinition(
            name=CAMPAIGNS_TOOL, description="List recent Brevo email campaigns with sent, open and click numbers (read-only).",
            risk_level=RiskLevel.LOW, timeout_seconds=30, idempotent=True, target=HOST, category="marketing", action_kind="read",
            input_schema=_object({
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "status": {"type": "string", "enum": ["draft", "sent", "queued", "suspended", "in_process", "scheduled", "archive"]},
            }),
        ),
        ToolDefinition(
            name=REPORT_TOOL, description="Read one Brevo email campaign's report: delivered, opens, clicks, bounces, unsubscribes (read-only).",
            risk_level=RiskLevel.LOW, timeout_seconds=30, idempotent=True, target=HOST, category="marketing", action_kind="read",
            input_schema=_object({"campaign_id": {"type": "integer", "minimum": 1}}, ["campaign_id"]),
        ),
        ToolDefinition(
            name=DRAFT_TOOL, description="Write a Brevo email campaign draft in ANUM for a person to review; nothing is sent.",
            risk_level=RiskLevel.LOW, required_roles=_PEOPLE, timeout_seconds=60, target=HOST, category="marketing", action_kind="draft",
            input_schema=_object({
                "name": {"type": "string", "maxLength": 200}, "subject": {"type": "string", "maxLength": 200},
                "preview_text": {"type": "string", "maxLength": 200}, "html": {"type": "string"},
                "list_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}, "maxItems": MAX_LISTS},
                # The planner names lists and gives an outline (docs/brevo.md#planner); ids and HTML win when both are given.
                "list_names": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": MAX_LISTS},
                "outline": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 12},
                "schedule": {"type": "string", "maxLength": 40},
                "brief": {"type": "string", "maxLength": 2000},
                "about": {"type": "string", "maxLength": 60},
                "language": {"type": "string", "enum": ["en", "ar"]},
                "sender_id": {"type": "integer", "minimum": 1},
            }, ["name", "subject"]),
        ),
        send_definition(),
    ]


# -- helpers ------------------------------------------------------------------------------------


def _failed(summary: str, **output: Any) -> ToolResult:
    return ToolResult(status="failed", summary=summary, output=dict(output))


async def _client(context: TenantContext) -> tuple[BrevoConnection | None, BrevoClient | None, ToolResult | None]:
    connection = await asyncio.to_thread(load_connection, context)
    if connection is None:
        return None, None, _failed("Brevo is not connected. An owner pastes the Brevo API key on the Email campaigns page first.")
    if connection.status == ConnectionStatus.NEEDS_KEY:
        return connection, None, _failed("Brevo refused the saved API key. An owner saves a new key on the Email campaigns page.")
    try:
        return connection, client_for(connection), None
    except Exception:  # noqa: BLE001 - the HTTP error's detail is shown, never the key
        return connection, None, _failed("The saved Brevo key cannot be read. An owner pastes the API key again.")


def _limit(value: Any, default: int = 20) -> int:
    try:
        return max(1, min(int(value), 50))
    except (TypeError, ValueError):
        return default


def _read_result(summary: str, output: dict[str, Any], count: int) -> ToolResult:
    return ToolResult(status="succeeded", summary=summary, output={**output, "provenance": PROVENANCE},
                      audit_metadata={"result_count": count})


# -- read tools ---------------------------------------------------------------------------------


async def list_lists(call: ToolCall, context: TenantContext) -> ToolResult:
    _, client, refused = await _client(context)
    if refused is not None or client is None:
        return refused or _failed("Brevo is not available.")
    try:
        lists, total = await client.lists(limit=_limit(call.arguments.get("limit"), 50))
    except BrevoError as error:
        return _failed(f"Brevo did not list the contact lists: {error.message}")
    rows = [{"id": item.id, "name": item.name, "subscribers": item.subscribers} for item in lists]
    summary = f"{total} contact list{'s' if total != 1 else ''} in Brevo" + (": " + ", ".join(f"{row['name']} ({row['subscribers']})" for row in rows[:10]) if rows else ".")
    return _read_result(summary, {"lists": rows, "total": total}, len(rows))


async def list_campaigns(call: ToolCall, context: TenantContext) -> ToolResult:
    _, client, refused = await _client(context)
    if refused is not None or client is None:
        return refused or _failed("Brevo is not available.")
    status = call.arguments.get("status")
    try:
        campaigns, total = await client.campaigns(limit=_limit(call.arguments.get("limit")), status=str(status) if status else None)
    except BrevoError as error:
        return _failed(f"Brevo did not list the campaigns: {error.message}")
    rows = [{"id": item.id, "name": item.name, "subject": item.subject, "status": item.status, "sent_at": item.sent_at,
             "scheduled_at": item.scheduled_at, "stats": item.stats.as_dict()} for item in campaigns]
    lines = [f"{row['name']} ({row['status']}, opens {row['stats']['open_rate'] if row['stats']['open_rate'] is not None else '-'}%)" for row in rows[:10]]
    summary = f"{total} email campaign{'s' if total != 1 else ''} in Brevo" + (": " + "; ".join(lines) if lines else ".")
    return _read_result(summary, {"campaigns": rows, "total": total}, len(rows))


async def campaign_report(call: ToolCall, context: TenantContext) -> ToolResult:
    try:
        campaign_id = int(call.arguments.get("campaign_id") or 0)
    except (TypeError, ValueError):
        campaign_id = 0
    if campaign_id < 1:
        return _failed("Name the Brevo campaign by its number.")
    _, client, refused = await _client(context)
    if refused is not None or client is None:
        return refused or _failed("Brevo is not available.")
    try:
        campaign = await client.campaign(campaign_id)
    except BrevoError as error:
        return _failed(f"Brevo did not return the report: {error.message}")
    stats = campaign.stats
    summary = (f"{campaign.name} ({campaign.status}): {stats.delivered} delivered of {stats.sent} sent, "
               f"{stats.unique_views} opened, {stats.unique_clicks} clicked, {stats.unsubscriptions} unsubscribed, "
               f"{stats.hard_bounces + stats.soft_bounces} bounced.")
    return _read_result(summary, {"campaign": campaign.as_dict()}, 1)


# -- draft tool ---------------------------------------------------------------------------------


def _list_key(name: str) -> str:
    # Case, a trailing "s" and the Arabic article aside: "customers" is "Customers", "العملاء" is "عملاء".
    words = [word.removeprefix("ال") if len(word) > 4 else word for word in name.casefold().split()]
    return " ".join(words).removesuffix("s")


async def _list_ids_by_name(client: BrevoClient, wanted: list[str]) -> tuple[list[int], list[str]]:
    """Resolve list names from the user's prompt against the account's lists: one exact
    match each (case and a trailing "s" aside), never a guess."""
    lists, _ = await client.lists(limit=50)
    ids: list[int] = []
    missing: list[str] = []
    for name in wanted:
        matches = [item.id for item in lists if _list_key(item.name) == _list_key(name)]
        if len(matches) == 1:
            ids.append(matches[0])
        else:
            missing.append(name[:100])
    return ids, missing


def _requested_time(value: Any) -> datetime | None:
    """A planned send time the send request will accept: 10 minutes to a year away, else None."""
    when = _parse_when(value)
    now = utc_now()
    if when is None or when < now + timedelta(minutes=10) or when > now + timedelta(days=365):
        return None
    return when.astimezone(UTC).replace(microsecond=0)


async def draft_campaign(call: ToolCall, context: TenantContext) -> ToolResult:
    args = call.arguments
    connection, client, refused = await _client(context)
    if refused is not None or client is None or connection is None:
        return refused or _failed("Brevo is not available.")
    try:
        senders = [item for item in await client.senders() if item.active]
    except BrevoError as error:
        return _failed(f"Brevo did not list the senders: {error.message}")
    wanted = args.get("sender_id")
    sender = next((item for item in senders if str(item.id) == str(wanted)), None) if wanted else (senders[0] if senders else None)
    if sender is None:
        return _failed("No verified, active sender matches. Add and verify a sender in Brevo (Senders, domains and IPs) first.")
    list_ids = args.get("list_ids") or []
    if not list_ids and args.get("list_names"):
        try:
            list_ids, missing = await _list_ids_by_name(client, [str(item) for item in args.get("list_names") or []])
        except BrevoError as error:
            return _failed(f"Brevo did not list the contact lists: {error.message}")
        if missing:
            return _failed(f"No single Brevo contact list is named {', '.join(repr(name) for name in missing)}. "
                           "Check the list names on the Email campaigns page and ask again with the exact name.")
    html_text = str(args.get("html") or "")
    composed = None
    if not html_text and args.get("outline"):
        # The planner's draft: the full email in the workspace's brand and Brevo template (docs/brevo.md#planner).
        from .compose import compose

        composed = await compose(
            context, client, subject=str(args.get("subject") or ""), preview_text=str(args.get("preview_text") or ""),
            outline=[str(item) for item in args.get("outline") or []][:12], brief=str(args.get("brief") or "")[:2000],
            about=str(args.get("about") or "")[:60], language="ar" if args.get("language") == "ar" else "en",
        )
        html_text = composed.html
    try:
        payload = DraftInput(
            name=str(args.get("name") or ""), subject=str(args.get("subject") or ""), preview_text=str(args.get("preview_text") or ""),
            sender_id=sender.id, html=html_text, list_ids=[int(item) for item in list_ids],
        )
    except (ValueError, TypeError) as exc:
        return _failed(f"The draft is incomplete: {str(exc).splitlines()[0][:200]}")
    names: list[str] = []
    try:
        for list_id in payload.list_ids:
            names.append((await client.contact_list(list_id)).name)
    except BrevoError as error:
        return _failed(f"A contact list was not found in Brevo: {error.message}")
    now = utc_now()
    draft = CampaignDraft(
        id=new_id("brevodraft"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=payload.name, subject=payload.subject,
        preview_text=payload.preview_text, sender_id=sender.id, sender_name=sender.name, sender_email=sender.email, html=payload.html,
        content_hash=content_hash(name=payload.name, subject=payload.subject, preview_text=payload.preview_text, sender_id=sender.id,
                                  sender_email=sender.email, html=payload.html, list_ids=payload.list_ids),
        list_ids=payload.list_ids, list_names=names, source="assistant", created_by=context.user_id, created_at=now, updated_at=now,
        # The requested time is kept on the draft; asking to send reuses it (the approval binds it).
        scheduled_at=_requested_time(args.get("schedule")),
    )

    def save() -> None:
        with open_brevo_store(context) as store:
            store.add_draft(draft)

    await asyncio.to_thread(save)
    await asyncio.to_thread(audit, context, "brevo.draft.created", draft.id, metadata={"by": "assistant", "lists": len(draft.list_ids)})
    requested = draft.scheduled_at.strftime("%Y-%m-%d %H:%M UTC") if draft.scheduled_at else None
    when = f" It is set to go out at {requested} once an owner approves it." if requested else ""
    if composed is not None and composed.note:
        when += f" {composed.note}"
    return ToolResult(
        status="succeeded",
        summary=f"Drafted the email campaign \"{draft.name}\" (draft {draft.id}) for review on the Email campaigns page. Nothing was sent.{when}",
        output={"draft_id": draft.id, "lists": names, "scheduled_at": draft.scheduled_at.isoformat() if draft.scheduled_at else "as soon as approved",
                **({"email": describe(composed)} if composed is not None else {})},
        audit_metadata={"draft_id": draft.id, "lists": len(draft.list_ids)},
    )


# -- send tool ----------------------------------------------------------------------------------


def _parse_when(value: Any) -> datetime | None:
    if not value or value == "as soon as approved":
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _claim(context: TenantContext, args: dict[str, Any], approver: str) -> tuple[CampaignDraft | None, BrevoConnection | None, ToolResult | None]:
    """One short transaction: check the draft against the approval and claim the send."""
    draft_id = str(args.get("draft_id", ""))
    approved = str(args.get("content_hash", ""))
    with open_brevo_store(context) as store:
        draft = store.get_draft(draft_id, for_update=True)
        if draft is None:
            return None, None, _failed("The draft was deleted; nothing was sent.")
        if draft.status in {DraftStatus.SENT, DraftStatus.SCHEDULED} and draft.approved_hash == approved:
            done = "sent" if draft.status == DraftStatus.SENT else "scheduled"
            return None, None, ToolResult(status="succeeded", summary=f"This campaign was already {done}; nothing was sent again.",
                                          output={"draft_id": draft.id, "brevo_campaign_id": draft.brevo_campaign_id})
        if draft.status not in {DraftStatus.PENDING_APPROVAL, DraftStatus.DRAFT, DraftStatus.FAILED}:
            return None, None, _failed(f"The campaign is {draft.status.value.replace('_', ' ')}; nothing was sent.")
        if (
            draft_hash(draft) != approved
            or draft.content_hash != approved
            or sorted(draft.list_ids) != sorted(int(item) for item in args.get("list_ids") or [])
            or str(draft.sender_id) != str(args.get("sender_id"))
            or draft.sender_email.lower() != str(args.get("sender_email", "")).lower()
            or (draft.scheduled_at.isoformat() if draft.scheduled_at else "as soon as approved") != args.get("scheduled_at")
        ):
            return None, None, _failed("The campaign changed after it was sent for approval; nothing was sent. Ask for approval again.")
        connection = store.get_connection()
        if connection is None or connection.status == ConnectionStatus.NEEDS_KEY:
            return None, None, _failed("Brevo is not connected with a working key; nothing was sent. An owner saves the key and asks again.")
        now = utc_now()
        draft.status = DraftStatus.SENDING
        draft.send_attempts += 1
        draft.approved_hash = approved
        draft.approved_by = approver
        draft.last_error = None
        draft.updated_at = now
        store.save_draft(draft)
        return draft, connection, None


def _finish(context: TenantContext, draft_id: str, *, status: DraftStatus, error: SendError | None = None,
            campaign_id: int | None = None, sent: bool = False) -> None:
    with open_brevo_store(context) as store:
        draft = store.get_draft(draft_id, for_update=True)
        if draft is None:
            return
        now = utc_now()
        draft.status = status
        draft.last_error = error
        if campaign_id is not None and draft.brevo_campaign_id is None:
            draft.brevo_campaign_id = campaign_id
        if sent:
            draft.sent_at = now
        draft.updated_at = now
        store.save_draft(draft)


def _error(code: str, message: str, fix: str) -> SendError:
    return SendError(code=code, message=message[:500], fix=fix)


async def _existing_campaign(client: BrevoClient, draft: CampaignDraft) -> int | None:
    """After an interrupted earlier attempt, the Brevo campaign carrying this draft's tag."""
    tag = campaign_tag(draft.id)
    campaigns, _ = await client.campaigns(limit=50)
    return next((item.id for item in campaigns if item.tag == tag), None)


async def send_campaign(call: ToolCall, context: TenantContext) -> ToolResult:
    """Runs only after approval: checks again, then sends or schedules through Brevo."""
    args = call.arguments
    draft, connection, refused = await asyncio.to_thread(_claim, context, args, context.user_id)
    if refused is not None or draft is None or connection is None:
        return refused or _failed("Nothing was sent.")
    when = _parse_when(args.get("scheduled_at"))
    now = utc_now()

    async def fail(code: str, message: str, fix: str, *, status: DraftStatus = DraftStatus.FAILED, campaign_id: int | None = None) -> ToolResult:
        await asyncio.to_thread(_finish, context, draft.id, status=status, error=_error(code, message, fix), campaign_id=campaign_id)
        await asyncio.to_thread(audit, context, "brevo.campaign.send_failed", draft.id, outcome="failure",
                                metadata={"code": code, "content_hash": draft.content_hash, "brevo_campaign_id": campaign_id or draft.brevo_campaign_id})
        return _failed(f"Not sent: {message} {fix}", draft_id=draft.id, code=code)

    if when is not None and when <= now + timedelta(minutes=1):
        return await fail("schedule_passed", "The scheduled time passed before the approval.", "Pick a new time and ask for approval again.")
    try:
        client = client_for(connection)
    except Exception:  # noqa: BLE001
        return await fail("key_unreadable", "The saved Brevo key cannot be read.", "An owner pastes the API key again, then asks again.")
    # Checked again at execution: the sender is still verified and active, and every list still exists.
    try:
        senders = await client.senders()
        sender = next((item for item in senders if item.id == draft.sender_id), None)
        if sender is None or not sender.active or sender.email.lower() != draft.sender_email.lower():
            return await fail("sender_changed", "The sender is no longer a verified, active sender in Brevo.",
                              "Verify the sender in Brevo or pick another, then ask again.")
        for list_id in draft.list_ids:
            await client.contact_list(list_id)
    except BrevoError as error:
        return await fail("auth" if error.auth_failed else "check_failed", error.message, "Fix it in Brevo or on the Connection tab, then ask again.")
    campaign_id = draft.brevo_campaign_id
    content = {"name": draft.name, "subject": draft.subject, "sender_id": draft.sender_id, "html": draft.html,
               "list_ids": draft.list_ids, "tag": campaign_tag(draft.id), "scheduled_at": when, "preview_text": draft.preview_text}
    try:
        if campaign_id is None and draft.send_attempts > 1:
            campaign_id = await _existing_campaign(client, draft)
        if campaign_id is None:
            campaign_id = await client.create_campaign(**content)
            # Recorded at once: whatever happens next, this draft never gets a second Brevo campaign.
            await asyncio.to_thread(_finish, context, draft.id, status=DraftStatus.SENDING, campaign_id=campaign_id)
        else:
            await client.update_campaign(campaign_id, **content)
    except BrevoError as error:
        # A timed-out create may have made the campaign; a timed-out update that sets
        # scheduledAt may have scheduled it. Both need a look in Brevo, never a blind retry.
        unknown = error.status == 0 and (campaign_id is None or when is not None)
        return await fail(
            "unknown" if unknown else ("auth" if error.auth_failed else "rejected"), error.message,
            "Check Email campaigns in Brevo before asking again." if unknown else "Fix the campaign and ask for approval again.",
            status=DraftStatus.UNKNOWN if unknown else DraftStatus.FAILED, campaign_id=campaign_id,
        )
    if when is None:
        try:
            await client.send_now(campaign_id)
        except BrevoError as error:
            # A timeout on sendNow may still have started the send: never retried blindly.
            unknown = error.status == 0
            return await fail("unknown" if unknown else "send_refused", error.message,
                              "Check this campaign in Brevo before asking again." if unknown else "Fix it in Brevo and ask for approval again.",
                              status=DraftStatus.UNKNOWN if unknown else DraftStatus.FAILED, campaign_id=campaign_id)
    final = DraftStatus.SENT if when is None else DraftStatus.SCHEDULED
    await asyncio.to_thread(_finish, context, draft.id, status=final, campaign_id=campaign_id, sent=when is None)
    await asyncio.to_thread(audit, context, "brevo.campaign.sent" if when is None else "brevo.campaign.scheduled", draft.id,
                            metadata={"content_hash": draft.content_hash, "brevo_campaign_id": campaign_id, "lists": len(draft.list_ids),
                                      "scheduled_at": when.isoformat() if when else None})
    told = "is being sent now" if when is None else f"is scheduled for {when.strftime('%Y-%m-%d %H:%M')} UTC"
    return ToolResult(
        status="succeeded",
        summary=f"Approved. \"{draft.name}\" {told} through Brevo (campaign {campaign_id}).",
        output={"draft_id": draft.id, "brevo_campaign_id": campaign_id, "status": final.value},
        audit_metadata={"draft_id": draft.id, "brevo_campaign_id": campaign_id},
    )


def register_brevo_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the Brevo tools to a runtime's registry (the API's and the Temporal worker's)."""
    handlers = {LISTS_TOOL: list_lists, CAMPAIGNS_TOOL: list_campaigns, REPORT_TOOL: campaign_report,
                DRAFT_TOOL: draft_campaign, SEND_TOOL: send_campaign}
    for definition in definitions():
        if definition.name not in registry.names:
            registry.register(definition, handlers[definition.name])
    return registry
