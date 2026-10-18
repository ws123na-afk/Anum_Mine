"""Shared rules of phone calls: permissions, the provider account, opt-outs, AI calls and call completion.

Used by the HTTP routes (``calls.api``), the provider webhooks (``calls.webhooks``) and the
agent tools (``calls.tools``), so every path applies the same checks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, status

from ..agent_tools import ToolCall, ToolDefinition, ToolResult
from ..audit import AuditRecord
from ..authorization import Permission
from ..schemas import RiskLevel
from ..dependencies import has_permission
from ..schemas import TenantContext, new_id, utc_now
from . import ai
from .config import call_settings
from .phone import PhoneNumberError, masked, normalized, opt_out_hash
from .provider import Credentials, ProviderError, TwilioVoice, VoiceProvider
from .store import (
    AiCallRequest,
    AiRequestStatus,
    CallContact,
    CallEntry,
    CallKind,
    CallsStore,
    CallStatus,
    Direction,
    OptOut,
    OptOutSource,
    Outcome,
    ProviderAccount,
    action_lock,
    open_calls_store,
)

logger = logging.getLogger("anum.calls")

AI_CALL_TOOL = "telephony.ai_call"
WEBHOOK_PREFIX = "/api/v1/calls/webhooks/twilio"
SYSTEM_ACTOR = "system:calls"

READ_DENIED = "Phone calls are not part of the viewer role. Ask a workspace owner to make you a member."
LOG_DENIED = "Viewers cannot add contacts or log calls. Ask a workspace owner to make you a member."
MANAGE_DENIED = "Only a workspace owner can do this. Ask an owner of this workspace."
EDIT_DENIED = "Only the person who added this or a workspace owner can change or delete it."

# The voice provider for an account (tests replace it with a fake).
provider_factory: Callable[[Credentials], VoiceProvider] = lambda credentials: TwilioVoice(
    credentials, timeout=call_settings.provider_timeout_seconds
)


# -- permissions -----------------------------------------------------------------------


def require(context: TenantContext, permission: Permission) -> None:
    if not has_permission(context, permission):
        detail = {Permission.CALL_READ: READ_DENIED, Permission.CALL_LOG: LOG_DENIED}.get(permission, MANAGE_DENIED)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def can_edit(context: TenantContext, owner_user_id: str) -> bool:
    if has_permission(context, Permission.CALL_MANAGE):
        return True
    return has_permission(context, Permission.CALL_LOG) and owner_user_id == context.user_id


def require_edit(context: TenantContext, owner_user_id: str) -> None:
    if not can_edit(context, owner_user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=EDIT_DENIED)


def is_owner(context: TenantContext) -> bool:
    return "owner" in {role.lower() for role in context.roles}


# -- audit -----------------------------------------------------------------------------


def audit(context: TenantContext, action: str, target: str, outcome: str = "success", metadata: dict[str, Any] | None = None) -> None:
    """One audit record (counts and ids only: never a number, a note or a transcript)."""
    from ..dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=False) as repository:
        repository.record_audit(
            AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
                action=action, target=target, outcome=outcome, correlation_id=target, created_at=utc_now(), metadata=metadata or {},
            )
        )


# -- provider account ------------------------------------------------------------------


def encrypt_credentials(auth_token: str, api_key_secret: str | None) -> str:
    from ..secret_box import secret_cipher

    return secret_cipher().encrypt(json.dumps({"auth_token": auth_token, "api_key_secret": api_key_secret or None}))


def credentials_of(account: ProviderAccount) -> Credentials:
    from ..secret_box import secret_cipher

    secret = json.loads(secret_cipher().decrypt(account.credentials_ciphertext))
    return Credentials(
        account_sid=account.account_sid, auth_token=str(secret.get("auth_token") or ""),
        api_key_sid=account.api_key_sid, api_key_secret=secret.get("api_key_secret") or None,
        twiml_app_sid=account.twiml_app_sid,
    )


def voice(account: ProviderAccount) -> VoiceProvider:
    return provider_factory(credentials_of(account))


def webhook_url(route_id: str, *parts: str) -> str:
    base = call_settings.public_base_url.rstrip("/")
    return f"{base}{WEBHOOK_PREFIX}/{route_id}/" + "/".join(parts)


def new_route_id() -> str:
    return secrets.token_hex(16)


def provider_problem(account: ProviderAccount | None) -> str | None:
    """Why provider calls cannot run now, in words an owner can act on; None when they can."""
    if account is None:
        return "No voice provider is connected. A workspace owner connects a Twilio account under Calls > Provider."
    if not call_settings.webhooks_ready:
        return "The server has no public address for the provider to call back. The operator sets ANUM_CALLS_PUBLIC_BASE_URL."
    if account.last_check_status == "failed":
        return f"The last provider check failed: {account.last_check_detail or 'see Calls > Provider'}"
    return None


def browser_problem(account: ProviderAccount | None) -> str | None:
    problem = provider_problem(account)
    if problem:
        return problem
    if account is None:  # checked just above
        raise RuntimeError("the provider was checked")
    if not (account.api_key_sid and account.twiml_app_sid):
        return "Calling from the browser needs an API key and a TwiML app in the provider settings."
    return None


def ai_problem(account: ProviderAccount | None, gateway: Any) -> str | None:
    problem = provider_problem(account)
    if problem:
        return problem
    if account is None:  # checked just above
        raise RuntimeError("the provider was checked")
    if not account.ai_calls_enabled:
        return "AI calls are off. A workspace owner turns them on under Calls > Provider."
    if not ai.model_ready(gateway):
        return "AI calls need a real model. Connect one in Settings > Model."
    return None


def estimated_cost_cents(seconds: int, account: ProviderAccount) -> int:
    return math.ceil(max(seconds, 0) / 60) * account.cost_per_minute_cents


def ai_caps(account: ProviderAccount) -> tuple[int, int]:
    """(longest AI call in seconds, most it may cost in cents) for this workspace."""
    return min(account.ai_max_seconds, call_settings.ai_max_seconds), min(account.ai_max_cost_cents, call_settings.ai_max_cost_cents)


# -- opt-outs --------------------------------------------------------------------------


def opted_out(store: CallsStore, context: TenantContext, number: str) -> OptOut | None:
    return store.find_opt_out(opt_out_hash(context.tenant_id, context.workspace_id, number))


def record_opt_out(store: CallsStore, context: TenantContext, number: str, source: OptOutSource, note: str | None, actor: str) -> OptOut:
    entry = OptOut(
        id=new_id("optout"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
        phone_hash=opt_out_hash(context.tenant_id, context.workspace_id, number), masked=masked(number),
        source=source, note=note, created_by=actor, created_at=utc_now(),
    )
    return store.add_opt_out(entry)


def suppressed(context: TenantContext, arguments: dict[str, Any]) -> list[Any]:
    """Hits on the tenant's contact suppression list for an AI call (docs/privacy.md#contact-suppression)."""
    from ..suppression import suppressed_for_call

    return suppressed_for_call(context, AI_CALL_TOOL, arguments, AI_CALL_DEFINITION)


# -- AI calls --------------------------------------------------------------------------


def ai_call_arguments(contact: CallContact, *, purpose: str, script: str, language: str, max_seconds: int, request_id: str | None) -> dict[str, Any]:
    """What an AI call's approval binds: the exact number, purpose, script, language and length."""
    arguments: dict[str, Any] = {
        "contact_id": contact.id, "contact_name": contact.name, "phone_number": contact.phone_normalized,
        "purpose": purpose, "script": script, "language": language, "max_seconds": max_seconds,
    }
    if request_id:
        arguments["request_id"] = request_id
    return arguments


def check_ai_call(store: CallsStore, context: TenantContext, contact: CallContact | None, account: ProviderAccount | None,
                  gateway: Any, *, max_seconds: int) -> str | None:
    """Why this AI call may not be placed, or None. Applied when asked and again when approved."""
    problem = ai_problem(account, gateway)
    if problem:
        return problem
    if account is None:  # checked just above
        raise RuntimeError("the provider was checked")
    if contact is None:
        return "The contact no longer exists."
    if contact.ai_consent is None:
        return f"{contact.name} has no recorded consent to calls from the AI assistant. ANUM never makes AI cold calls."
    if opted_out(store, context, contact.phone_normalized) is not None:
        return f"{contact.name} asked not to be called by the AI assistant; the opt-out is honoured."
    longest, dearest = ai_caps(account)
    if max_seconds > longest:
        return f"An AI call may last at most {longest} seconds in this workspace."
    if estimated_cost_cents(max_seconds, account) > dearest:
        return f"A call of {max_seconds} seconds could cost more than this workspace's cap of {dearest} cents."
    return None


def _failed(summary: str, **metadata: Any) -> ToolResult:
    return ToolResult(status="failed", summary=summary, audit_metadata={"tool": AI_CALL_TOOL, "outcome": "refused", **metadata})


async def place_ai_call(call: ToolCall, context: TenantContext) -> ToolResult:
    """Place an approved AI call (``telephony.ai_call``). Runs only after an owner's approval.

    Everything the approval bound is checked against the records as they are now: a contact
    whose number changed, a request whose purpose or script changed, a consent withdrawn or an
    opt-out recorded since the approval means no call.
    """
    args = call.arguments
    if not is_owner(context):
        return _failed("Not called: an AI call needs the approval of a workspace owner.")
    try:
        number = normalized(str(args.get("phone_number") or ""))
    except PhoneNumberError:
        return _failed("Not called: the approved number is not a phone number.")
    purpose, script = str(args.get("purpose") or ""), str(args.get("script") or "")
    language = "ar" if args.get("language") == "ar" else "en"
    try:
        max_seconds = int(args.get("max_seconds") or 0)
    except (TypeError, ValueError):
        max_seconds = 0
    if not purpose or not script or max_seconds < 30:
        return _failed("Not called: the approved call has no purpose, script or length.")
    gateway = ai.call_gateway(context)
    now = utc_now()
    request_id = str(args.get("request_id") or "")
    with action_lock, open_calls_store(context) as store:
        request = store.get_request(request_id, lock=True) if request_id else None

        def refuse(message: str, **metadata: Any) -> ToolResult:
            # A request that was waiting for this approval is closed with the reason.
            if request is not None and request.status == AiRequestStatus.PENDING_APPROVAL:
                store.save_request(request.model_copy(update={"status": AiRequestStatus.FAILED, "error": message, "decided_by": context.user_id, "updated_at": utc_now()}))
            return _failed(f"Not called: {message}", **metadata)

        if request_id and (request is None or request.status != AiRequestStatus.PENDING_APPROVAL):
            return _failed("Not called: this AI call request was cancelled or has already run.")
        account = store.get_provider()
        contact = store.get_contact(str(args.get("contact_id") or ""))
        if contact is not None and contact.phone_normalized != number:
            return refuse("the contact's number changed after the approval. Ask for the call again.", changed="number")
        problem = check_ai_call(store, context, contact, account, gateway, max_seconds=max_seconds)
        if problem:
            return refuse(problem)
        if contact is None or account is None:  # checked just above
            raise RuntimeError("the contact and provider were checked")
        if request is not None:
            bound = (request.purpose, request.script, request.language, request.max_seconds, request.phone_normalized, request.contact_id)
            if bound != (purpose, script, language, max_seconds, number, contact.id):
                return refuse("the call's purpose or script changed after the approval. Ask for the call again.", changed="script")
        else:
            request = AiCallRequest(
                id=new_id("aicall"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, requested_by=context.user_id,
                contact_id=contact.id, contact_name=contact.name, phone_normalized=number, purpose=purpose, script=script,
                language=language, max_seconds=max_seconds, max_cost_cents=estimated_cost_cents(max_seconds, account),
                status=AiRequestStatus.PENDING_APPROVAL, created_at=now, updated_at=now,
            )
            store.add_request(request)
        entry = CallEntry(
            id=new_id("call"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=request.requested_by,
            contact_id=contact.id, contact_name=contact.name, phone=contact.phone, phone_normalized=number,
            direction=Direction.OUTBOUND, kind=CallKind.AI_OUTBOUND, status=CallStatus.DIALING, started_at=now,
            provider=account.provider, ai_request_id=request.id, created_at=now, updated_at=now,
        )
        store.add_call(entry)
        request = request.model_copy(update={"status": AiRequestStatus.CALLING, "call_id": entry.id, "decided_by": context.user_id, "updated_at": now})
        store.save_request(request)
        route, sender = account.route_id, account.from_number
        provider = voice(account)
    try:
        placed = await provider.place_call(
            to=number, from_=sender, answer_url=webhook_url(route, "ai", entry.id),
            status_url=webhook_url(route, "status", entry.id), time_limit=max_seconds,
        )
    except ProviderError as exc:
        with open_calls_store(context) as store:
            failed = store.get_call(entry.id)
            if failed is not None:
                store.save_call(failed.model_copy(update={"status": CallStatus.FAILED, "outcome": Outcome.FAILED, "updated_at": utc_now()}))
            current = store.get_request(request.id)
            if current is not None:
                store.save_request(current.model_copy(update={"status": AiRequestStatus.FAILED, "error": exc.detail, "updated_at": utc_now()}))
        return _failed(f"Not called: {exc.detail}", stage="provider")
    with open_calls_store(context) as store:
        current = store.get_call(entry.id)
        if current is not None:
            store.save_call(current.model_copy(update={"provider_call_id": placed.provider_call_id, "updated_at": utc_now()}))
    return ToolResult(
        status="succeeded",
        summary=f"Calling {contact.name} with the AI assistant (at most {max_seconds} seconds).",
        output={"call_id": entry.id, "request_id": request.id},
        audit_metadata={"tool": AI_CALL_TOOL, "call_id": entry.id, "request_id": request.id, "max_seconds": max_seconds},
    )


AI_CALL_DEFINITION = ToolDefinition(
    name=AI_CALL_TOOL,
    description=(
        "Have the AI assistant phone a contact who consented to AI calls, for one stated purpose with an approved script. "
        "Every call waits for a workspace owner's approval of the exact number, purpose and script; the assistant says "
        "it is an AI at the start, the call is capped in time and cost, and the person can opt out."
    ),
    risk_level=RiskLevel.HIGH,
    required_roles=frozenset({"owner", "member"}),
    timeout_seconds=60,
    category="calls",
    action_kind="other",
    always_ask=True,
    input_schema={
        "type": "object",
        "properties": {
            "contact_id": {"type": "string"}, "contact_name": {"type": "string"},
            "phone_number": {"type": "string", "description": "The contact's number, exactly as listed"},
            "purpose": {"type": "string", "maxLength": 500}, "script": {"type": "string", "maxLength": 4000},
            "language": {"type": "string", "enum": ["en", "ar"]},
            "max_seconds": {"type": "integer", "minimum": 30, "maximum": 900},
        },
        "required": ["contact_id", "phone_number", "purpose", "script", "max_seconds"],
    },
)


# -- call completion -------------------------------------------------------------------

_STATUS = {
    "queued": CallStatus.DIALING, "initiated": CallStatus.DIALING, "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS, "answered": CallStatus.IN_PROGRESS,
}
_ENDED = {
    "completed": Outcome.ANSWERED, "busy": Outcome.BUSY, "no-answer": Outcome.NO_ANSWER,
    "failed": Outcome.FAILED, "canceled": Outcome.CANCELLED,
}


def provider_update(entry: CallEntry, account: ProviderAccount, call_status: str, duration: str | None) -> CallEntry:
    """The entry after one provider status report (idempotent: a finished call stays finished)."""
    now = utc_now()
    if entry.status in {CallStatus.COMPLETED, CallStatus.FAILED}:
        return entry
    if call_status in _STATUS:
        update: dict[str, Any] = {"status": _STATUS[call_status], "updated_at": now}
        if _STATUS[call_status] == CallStatus.IN_PROGRESS:
            update["started_at"] = now
        return entry.model_copy(update=update)
    if call_status not in _ENDED:
        return entry
    seconds = int(duration) if duration and duration.isdigit() else None
    outcome = _ENDED[call_status]
    if outcome == Outcome.ANSWERED and not seconds:
        outcome = Outcome.NO_ANSWER
    if entry.direction == Direction.INBOUND and entry.kind == CallKind.PROVIDER:
        outcome = Outcome.MISSED
    return entry.model_copy(update={
        "status": CallStatus.FAILED if outcome == Outcome.FAILED else CallStatus.COMPLETED,
        "outcome": outcome, "duration_seconds": seconds, "ended_at": now, "updated_at": now,
        "cost_cents": estimated_cost_cents(seconds or 0, account),
    })


def transcript_lines(text: str | None) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        speaker, _, said = line.partition(": ")
        if said and speaker in {"AI", "Caller"}:
            lines.append(("ai" if speaker == "AI" else "caller", said))
    return lines


def add_line(text: str | None, speaker: str, said: str) -> str:
    line = f"{'AI' if speaker == 'ai' else 'Caller'}: {' '.join(said.split())}"
    return ((text + "\n") if text else "") + line


async def write_summary(context: TenantContext, call_id: str) -> None:
    """Summarise a finished call's notes or transcript (background; never raises)."""
    try:
        with open_calls_store(context) as store:
            entry = store.get_call(call_id)
        if entry is None or not (entry.notes or entry.transcript):
            return
        gateway = ai.call_gateway(context)
        if not ai.model_ready(gateway):
            return
        result = await summarize_entry(gateway, entry)
        with open_calls_store(context) as store:
            current = store.get_call(call_id, lock=True)
            if current is not None:
                store.save_call(apply_summary(current, result))
    except Exception as exc:  # the call record stands; the person can press Summarize
        logger.warning("call_summary_failed", extra={"anum_error_class": type(exc).__name__})


async def summarize_entry(gateway: Any, entry: CallEntry) -> ai.CallSummary:
    language = ai.language_of(entry.notes, entry.transcript)
    return await ai.summarize(
        gateway, notes=entry.notes, transcript=entry.transcript, who=entry.contact_name or masked(entry.phone_normalized or "unknown"),
        direction=entry.direction.value, outcome=entry.outcome.value if entry.outcome else None, language=language,
        call_date=entry.started_at.date(), max_chars=call_settings.summary_max_chars,
        max_tokens=call_settings.summary_max_tokens, now=utc_now(),
    )


def apply_summary(entry: CallEntry, result: ai.CallSummary) -> CallEntry:
    from .store import FollowUp, SummaryProvenance

    follow_up = entry.follow_up
    if follow_up is None or not (follow_up.edited or follow_up.task_id):
        follow_up = FollowUp(text=result.follow_up, due_date=result.due_date) if result.follow_up else None
    return entry.model_copy(update={
        "summary": result.summary, "follow_up": follow_up,
        "summary_provenance": SummaryProvenance.model_validate(result.provenance), "updated_at": utc_now(),
    })


def finish_request(store: CallsStore, entry: CallEntry) -> None:
    if not entry.ai_request_id:
        return
    request = store.get_request(entry.ai_request_id, lock=True)
    if request is None or request.status != AiRequestStatus.CALLING:
        return
    done = AiRequestStatus.COMPLETED if entry.outcome == Outcome.ANSWERED else AiRequestStatus.FAILED
    store.save_request(request.model_copy(update={
        "status": done, "error": None if done == AiRequestStatus.COMPLETED else f"The call ended: {entry.outcome.value if entry.outcome else 'unknown'}.",
        "updated_at": utc_now(),
    }))


def identity_for(context: TenantContext) -> str:
    """The browser SDK identity of a person: opaque, stable, no personal data."""
    digest = hashlib.sha256(f"{context.tenant_id}\x1f{context.workspace_id}\x1f{context.user_id}".encode()).hexdigest()
    return f"anum_{digest[:24]}"


def dial_expired(entry: CallEntry) -> bool:
    return utc_now() - entry.created_at > timedelta(seconds=call_settings.dial_window_seconds)


def system_context(tenant_id: str, workspace_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_ACTOR, roles=["system"])


def recording_notice(business: str, *, transcription: bool, language: str) -> str:
    if language == "ar":
        return f"تنبيه: هذه المكالمة مسجلة{' ومفرغة نصيا' if transcription else ''} لدى {business}."
    return f"Please note: this call is recorded{' and transcribed' if transcription else ''} by {business}."


def ai_disclosure(business: str, name: str | None, *, language: str, inbound: bool) -> str:
    if language == "ar":
        who = f"مرحبا {name}. " if name else "مرحبا. "
        what = "أنا مساعد ذكي يعمل بالذكاء الاصطناعي" + (f" أرد نيابة عن {business}." if inbound else f" أتصل نيابة عن {business}.")
        return who + what + " يتم تفريغ هذه المكالمة نصيا. إذا كنت لا تريد مكالمات من هذا المساعد، اضغط 9 أو قل توقف في أي وقت."
    who = f"Hello {name}. " if name else "Hello. "
    what = f"I am an AI assistant {'answering' if inbound else 'calling'} on behalf of {business}."
    return who + what + " This call is transcribed. If you do not want calls from this assistant, press 9 or say stop at any time."


def expires(days: int) -> datetime:
    return utc_now() + timedelta(days=days)
