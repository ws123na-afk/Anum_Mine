"""The voice provider's webhooks (docs/calls.md#webhooks): ``/api/v1/calls/webhooks/twilio/{route}/...``.

Public routes: the provider is not a signed-in user. Each request is handled only after

1. the route id names a workspace (found as ``anum_maintenance``, which sees only scope and
   route of provider accounts), and
2. its ``X-Twilio-Signature`` matches the HMAC of the exact public URL and the form
   parameters, keyed with that workspace's auth token (constant-time comparison).

Anything else answers 403 without saying why. ANUM dials only what it prepared itself: a
browser call names the call log entry ANUM created for the signed-in person, and the number
comes from that entry, never from the request.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from ..model_budget import ModelBudgetExceededError
from ..schemas import TenantContext, new_id, utc_now
from ..suppression import keyword_of, normalize_keyword
from . import ai, service
from .config import call_settings
from .phone import PhoneNumberError, normalized
from .provider import TwiML
from .store import (
    CallEntry,
    CallKind,
    CallStatus,
    Direction,
    OptOutSource,
    Outcome,
    ProviderAccount,
    action_lock,
    open_calls_store,
    route_workspace,
)

logger = logging.getLogger("anum.calls.webhooks")
router = APIRouter(prefix=service.WEBHOOK_PREFIX, tags=["calls"])

MAX_BODY = 64 * 1024
# Browser and provider calls end at the latest after four hours.
HUMAN_CALL_LIMIT = 4 * 3600
_ROUTE = re.compile(r"^[0-9a-f]{32}$")
_STOP_PHRASES = tuple(normalize_keyword(text) for text in (
    "do not call", "don't call", "dont call", "stop calling", "remove me", "no more calls",
    "لا تتصل", "لا تتصلوا", "لا تتصل بي", "توقف عن الاتصال",
))


def _xml(twiml: TwiML) -> Response:
    return Response(content=twiml.render(), media_type="application/xml")


async def _verified(request: Request, route_id: str) -> tuple[TenantContext, ProviderAccount, dict[str, str]]:
    if not _ROUTE.match(route_id) or not call_settings.webhooks_ready:
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(status_code=413, detail="Too large")
    params = dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
    scope = await asyncio.to_thread(route_workspace, route_id)
    if scope is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    context = service.system_context(*scope)
    with open_calls_store(context) as store:
        account = store.get_provider()
    if account is None or account.route_id != route_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    url = call_settings.public_base_url.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not service.voice(account).verify_signature(url, params, request.headers.get("x-twilio-signature", "")):
        logger.warning("call_webhook_bad_signature")
        raise HTTPException(status_code=403, detail="Forbidden")
    return context, account, params


def _person(context: TenantContext, user_id: str) -> TenantContext:
    return TenantContext(tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=user_id, roles=["member"])


def _refuse(text: str = "This call could not be connected.") -> Response:
    return _xml(TwiML().say(text).hangup())


# -- voice: browser calls out, and calls in -------------------------------------------------


@router.post("/{route_id}/voice")
async def voice(route_id: str, request: Request) -> Response:
    context, account, params = await _verified(request, route_id)
    caller = params.get("From", "")
    if caller.startswith("client:"):
        return _browser_call(context, account, params, caller.removeprefix("client:"))
    return _inbound(context, account, params)


def _browser_call(context: TenantContext, account: ProviderAccount, params: dict[str, str], identity: str) -> Response:
    call_id = params.get("anum_call_id", "")
    with action_lock, open_calls_store(context) as store:
        entry = store.get_call(call_id, lock=True)
        if (
            entry is None or entry.kind != CallKind.PROVIDER or entry.status != CallStatus.DIALING or entry.provider_call_id
            or service.dial_expired(entry) or service.identity_for(_person(context, entry.user_id)) != identity
            or not entry.phone_normalized
        ):
            return _refuse()
        record = entry.recording_consent is not None and account.recording_enabled
        store.save_call(entry.model_copy(update={"provider_call_id": params.get("CallSid") or None, "status": CallStatus.RINGING, "updated_at": utc_now()}))
    twiml = TwiML()
    if record and entry.recording_consent is not None:
        language = "ar" if any("؀" <= ch <= "ۿ" for ch in entry.recording_consent.notice) else "en"
        twiml.say(entry.recording_consent.notice, language)
    twiml.dial(
        entry.phone_normalized, caller_id=account.from_number, time_limit=HUMAN_CALL_LIMIT,
        action=service.webhook_url(account.route_id, "dial", entry.id), record=record,
        recording_url=service.webhook_url(account.route_id, "recording", entry.id),
        status_url=service.webhook_url(account.route_id, "status", entry.id),
    )
    return _xml(twiml)


def _inbound(context: TenantContext, account: ProviderAccount, params: dict[str, str]) -> Response:
    try:
        number = normalized(params.get("From", ""))
    except PhoneNumberError:
        number = None
    now = utc_now()
    gateway = ai.call_gateway(context)
    with open_calls_store(context) as store:
        sid = params.get("CallSid") or None
        if sid and store.call_by_provider_id(sid) is not None:
            return _refuse("Please call again.")
        contacts = store.contacts_by_number(number) if number else []
        contact = contacts[0] if len(contacts) == 1 else None
        opted = bool(number and service.opted_out(store, context, number))
        ai_on = account.inbound_ai_enabled and ai.model_ready(gateway) and not opted
        entry = CallEntry(
            id=new_id("call"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=service.SYSTEM_ACTOR,
            contact_id=contact.id if contact else None, contact_name=contact.name if contact else None,
            phone=params.get("From") or None, phone_normalized=number, direction=Direction.INBOUND,
            kind=CallKind.AI_INBOUND if ai_on else CallKind.PROVIDER,
            status=CallStatus.IN_PROGRESS if ai_on else CallStatus.COMPLETED, outcome=None if ai_on else Outcome.MISSED,
            ended_at=None if ai_on else now, started_at=now, provider=account.provider, provider_call_id=sid, created_at=now, updated_at=now,
        )
        language = contact.language if contact and contact.language in {"en", "ar"} else "en"
        if ai_on:
            disclosure = service.ai_disclosure(account.business_name, contact.name if contact else None, language=language, inbound=True)
            opening = "How can I help you? Please tell me your name and why you are calling." if language == "en" else "كيف يمكنني مساعدتك؟ من فضلك أخبرني باسمك وسبب اتصالك."
            entry = entry.model_copy(update={"transcript": service.add_line(service.add_line(None, "ai", disclosure), "ai", opening)})
        store.add_call(entry)
    service.audit(context, "call.inbound", entry.id, metadata={"ai": ai_on, "known_contact": contact is not None})
    if not ai_on:
        text = f"Thank you for calling {account.business_name}. Nobody can take your call right now; we will see that you called."
        return _xml(TwiML().say(text).hangup())
    twiml = TwiML()
    if account.greeting:
        twiml.say(account.greeting, language)
    twiml.say(disclosure, language)
    twiml.gather(service.webhook_url(account.route_id, "turn", entry.id), language=language, prompt=opening)
    twiml.redirect(service.webhook_url(account.route_id, "turn", entry.id))
    return _xml(twiml)


# -- status, dial result and recording -----------------------------------------------------


@router.post("/{route_id}/dial/{call_id}")
async def dial_result(route_id: str, call_id: str, request: Request, background: BackgroundTasks) -> Response:
    context, account, params = await _verified(request, route_id)
    _record_status(context, account, call_id, None, params.get("DialCallStatus", ""), params.get("DialCallDuration"), background)
    return _xml(TwiML().hangup())


@router.post("/{route_id}/status")
async def status_by_sid(route_id: str, request: Request, background: BackgroundTasks) -> Response:
    """The provider number's call status URL (calls in): the call is found by its provider id."""
    context, account, params = await _verified(request, route_id)
    _record_status(context, account, None, params.get("CallSid", ""), params.get("CallStatus", ""), params.get("CallDuration"), background)
    return Response(status_code=204)


@router.post("/{route_id}/status/{call_id}")
async def status_by_id(route_id: str, call_id: str, request: Request, background: BackgroundTasks) -> Response:
    context, account, params = await _verified(request, route_id)
    _record_status(context, account, call_id, None, params.get("CallStatus", ""), params.get("CallDuration"), background)
    return Response(status_code=204)


def _record_status(context: TenantContext, account: ProviderAccount, call_id: str | None, sid: str | None,
                   call_status: str, duration: str | None, background: BackgroundTasks) -> None:
    with action_lock, open_calls_store(context) as store:
        entry = store.get_call(call_id, lock=True) if call_id else (store.call_by_provider_id(sid) if sid else None)
        if entry is None:
            return
        updated = service.provider_update(entry, account, call_status, duration)
        if updated == entry:
            return
        store.save_call(updated)
        finished = updated.status in {CallStatus.COMPLETED, CallStatus.FAILED}
        if finished:
            service.finish_request(store, updated)
    if finished:
        service.audit(context, "call.ended", updated.id, metadata={"kind": updated.kind.value, "outcome": updated.outcome.value if updated.outcome else None, "seconds": updated.duration_seconds})
        if updated.transcript and updated.kind in {CallKind.AI_OUTBOUND, CallKind.AI_INBOUND}:
            background.add_task(service.write_summary, context, updated.id)


@router.post("/{route_id}/recording/{call_id}")
async def recording(route_id: str, call_id: str, request: Request, background: BackgroundTasks) -> Response:
    context, account, params = await _verified(request, route_id)
    if params.get("RecordingStatus") != "completed" or not params.get("RecordingUrl"):
        return Response(status_code=204)
    with open_calls_store(context) as store:
        entry = store.get_call(call_id)
    if entry is None or entry.recording_consent is None or entry.recording_file_id:
        return Response(status_code=204)
    background.add_task(store_recording, context, account, call_id, params["RecordingUrl"])
    return Response(status_code=204)


async def store_recording(context: TenantContext, account: ProviderAccount, call_id: str, url: str) -> None:
    """Download a consented recording into the workspace files, with its expiry; then transcribe
    and summarise when the owner turned transcription on. Never raises."""
    from ..files import remove_workspace_file, store_workspace_file
    from ..settings import settings
    from ..transcription import TranscriptionError, build_transcriber

    try:
        content, content_type = await service.voice(account).fetch_recording(url, call_settings.recording_max_bytes)
        with open_calls_store(context) as store:
            entry = store.get_call(call_id)
        if entry is None:
            return
        person = _person(context, entry.user_id)
        record = await asyncio.to_thread(store_workspace_file, person, f"call-{entry.started_at:%Y%m%d-%H%M}-{entry.id[-6:]}.mp3", content, content_type)
        with open_calls_store(context) as store:
            current = store.get_call(call_id, lock=True)
            if current is None:
                remove_workspace_file(person, record.id)
                return
            current = current.model_copy(update={"recording_file_id": record.id, "recording_expires_at": utc_now() + timedelta(days=account.recording_retention_days), "updated_at": utc_now()})
            store.save_call(current)
        service.audit(context, "call.recording.stored", call_id, metadata={"bytes": len(content), "retention_days": account.recording_retention_days})
        transcriber = build_transcriber(settings) if account.transcription_enabled and current.recording_consent and current.recording_consent.transcription else None
        if transcriber is None:
            return
        try:
            result = await transcriber.transcribe(content, file_name=record.name, content_type=content_type, language=None)
        except TranscriptionError as exc:
            logger.warning("call_transcription_failed", extra={"anum_detail": exc.detail[:120]})
            return
        text = "\n".join(f"{segment.speaker + ': ' if segment.speaker else ''}{segment.text}" for segment in result.segments)[:60_000]
        with open_calls_store(context) as store:
            current = store.get_call(call_id, lock=True)
            if current is not None:
                store.save_call(current.model_copy(update={"transcript": text or None, "updated_at": utc_now()}))
        if text:
            await service.write_summary(context, call_id)
    except Exception as exc:
        logger.warning("call_recording_failed", extra={"anum_error_class": type(exc).__name__})


# -- the AI on the line --------------------------------------------------------------------


def _goodbye(language: str) -> str:
    return "Thank you for your time. Goodbye." if language == "en" else "شكرا لوقتك. مع السلامة."


def _sorry(language: str, business: str) -> str:
    if language == "ar":
        return f"عذرا، لا أستطيع متابعة هذه المكالمة الآن. سيتواصل معك شخص من {business}. مع السلامة."
    return f"Sorry, I cannot continue this call now. Someone from {business} will be in touch. Goodbye."


def _opted_out_reply(language: str) -> str:
    return "Understood. You will not be called by this assistant again. Goodbye." if language == "en" else "حسنا. لن يتصل بك هذا المساعد مرة أخرى. مع السلامة."


def _is_stop(digits: str, speech: str) -> OptOutSource | None:
    if digits.strip() == "9":
        return OptOutSource.KEYPAD
    if speech:
        folded = normalize_keyword(speech)
        if keyword_of(speech) == "stop" or any(phrase in folded for phrase in _STOP_PHRASES):
            return OptOutSource.SPEECH
    return None


def _request_for(store: Any, entry: CallEntry) -> Any:
    return store.get_request(entry.ai_request_id) if entry.ai_request_id else None


@router.post("/{route_id}/ai/{call_id}")
async def ai_answered(route_id: str, call_id: str, request: Request) -> Response:
    """The other side answered an AI call: the disclosure first, then the first sentence of the script."""
    context, account, _params = await _verified(request, route_id)
    with open_calls_store(context) as store:
        entry = store.get_call(call_id)
        ai_request = _request_for(store, entry) if entry is not None else None
    if entry is None or entry.kind != CallKind.AI_OUTBOUND or ai_request is None or entry.status in {CallStatus.COMPLETED, CallStatus.FAILED}:
        return _xml(TwiML().hangup())
    language = ai_request.language
    disclosure = service.ai_disclosure(account.business_name, entry.contact_name, language=language, inbound=False)
    lines = [("ai", disclosure)]
    try:
        turn = await ai.next_turn(
            ai.call_gateway(context), inbound=False, business=account.business_name, purpose=ai_request.purpose,
            script=ai_request.script, language=language, lines=lines, max_tokens=call_settings.ai_turn_max_tokens,
        )
    except Exception as exc:
        _note_model_failure(exc)
        return _end(context, call_id, [("ai", disclosure)], _sorry(language, account.business_name), language)
    transcript = service.add_line(service.add_line(None, "ai", disclosure), "ai", turn.say)
    with open_calls_store(context) as store:
        current = store.get_call(call_id, lock=True)
        if current is not None:
            store.save_call(current.model_copy(update={"transcript": transcript, "status": CallStatus.IN_PROGRESS, "updated_at": utc_now()}))
    twiml = TwiML().say(disclosure, language)
    if turn.done:
        return _xml(twiml.say(turn.say, language).hangup())
    twiml.gather(service.webhook_url(account.route_id, "turn", call_id), language=language, prompt=turn.say)
    twiml.redirect(service.webhook_url(account.route_id, "turn", call_id))
    return _xml(twiml)


def _note_model_failure(exc: Exception) -> None:
    if not isinstance(exc, (ModelBudgetExceededError, ai.CallAiParseError)):
        logger.warning("call_ai_turn_failed", extra={"anum_error_class": type(exc).__name__})


def _end(context: TenantContext, call_id: str, extra: list[tuple[str, str]], text: str, language: str) -> Response:
    """Say ``text``, hang up and mark the call finished (the provider's status report may refine it)."""
    with open_calls_store(context) as store:
        current = store.get_call(call_id, lock=True)
        if current is not None:
            transcript = current.transcript
            for speaker, said in [*extra, ("ai", text)]:
                transcript = service.add_line(transcript, speaker, said)
            store.save_call(current.model_copy(update={"transcript": transcript[:60_000] if transcript else None, "updated_at": utc_now()}))
    return _xml(TwiML().say(text, language).hangup())


@router.post("/{route_id}/turn/{call_id}")
async def ai_turn(route_id: str, call_id: str, request: Request, background: BackgroundTasks) -> Response:
    """What the other side said (speech) or pressed (keypad); the AI's next sentence."""
    context, account, params = await _verified(request, route_id)
    with open_calls_store(context) as store:
        entry = store.get_call(call_id)
        ai_request = _request_for(store, entry) if entry is not None else None
    if entry is None or entry.kind not in {CallKind.AI_OUTBOUND, CallKind.AI_INBOUND} or entry.status in {CallStatus.COMPLETED, CallStatus.FAILED}:
        return _xml(TwiML().hangup())
    inbound = entry.kind == CallKind.AI_INBOUND
    if not inbound and ai_request is None:
        return _xml(TwiML().hangup())
    language = ai_request.language if ai_request else "en"
    speech, digits = params.get("SpeechResult", "").strip()[:1000], params.get("Digits", "")
    heard = speech or (f"(pressed {digits})" if digits else "")

    def close(extra: list[tuple[str, str]], text: str, lang: str) -> Response:
        response = _end(context, call_id, extra, text, lang)
        if inbound:
            # Calls in have no status report unless the owner set the number's status URL; finish here.
            seconds = int((utc_now() - entry.started_at).total_seconds())
            _record_status(context, account, call_id, None, "completed", str(max(seconds, 1)), background)
        return response

    stop = _is_stop(digits, speech)
    if stop is not None and entry.phone_normalized:
        with open_calls_store(context) as store:
            opt = service.record_opt_out(store, context, entry.phone_normalized, OptOutSource.INBOUND if inbound else stop, None, service.SYSTEM_ACTOR)
            for contact in store.contacts_by_number(entry.phone_normalized):
                if contact.ai_consent is not None:
                    store.save_contact(contact.model_copy(update={"ai_consent": None, "updated_at": utc_now()}))
        service.audit(context, "call.opt_out.added", opt.id, metadata={"source": stop.value, "during_call": True})
        return close([("caller", heard or "(asked to stop)")], _opted_out_reply(language), language)

    lines = service.transcript_lines(entry.transcript)
    longest, dearest = service.ai_caps(account)
    limit = min(ai_request.max_seconds, longest) if ai_request else longest
    elapsed = int((utc_now() - entry.started_at).total_seconds())
    ai_turns = sum(1 for speaker, _ in lines if speaker == "ai")
    silent = not heard
    recent_silence = len(lines) >= 1 and lines[-1] == ("caller", "(silence)")
    if (
        elapsed >= limit - 10 or ai_turns >= call_settings.ai_max_turns
        or service.estimated_cost_cents(elapsed + 60, account) > (ai_request.max_cost_cents if ai_request else dearest)
        or (silent and recent_silence)
    ):
        return close([("caller", heard or "(silence)")], _goodbye(language), language)
    lines.append(("caller", heard or "(silence)"))
    try:
        turn = await ai.next_turn(
            ai.call_gateway(context), inbound=inbound, business=account.business_name,
            purpose=ai_request.purpose if ai_request else "Take a message", script=ai_request.script if ai_request else "",
            language=language, lines=lines, max_tokens=call_settings.ai_turn_max_tokens,
        )
    except Exception as exc:
        _note_model_failure(exc)
        return close([("caller", heard or "(silence)")], _sorry(language, account.business_name), language)
    if turn.done:
        return close([("caller", heard or "(silence)")], turn.say, language)
    with open_calls_store(context) as store:
        current = store.get_call(call_id, lock=True)
        if current is not None:
            transcript = service.add_line(service.add_line(current.transcript, "caller", heard or "(silence)"), "ai", turn.say)
            store.save_call(current.model_copy(update={"transcript": transcript[:60_000], "updated_at": utc_now()}))
    twiml = TwiML().gather(service.webhook_url(account.route_id, "turn", call_id), language=language, prompt=turn.say)
    twiml.redirect(service.webhook_url(account.route_id, "turn", call_id))
    return _xml(twiml)
