"""HTTP API of phone calls: ``/api/v1/calls`` (docs/calls.md#api).

Contacts and the call log belong to the workspace (forced RLS on tenant and workspace).
Members read them, add contacts and log calls; the person who added an entry or a workspace
owner changes or deletes it. Owners connect the voice provider, record AI call consent and
lift opt-outs. Every state change is audited with ids and counts only.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent_tools import ToolCall
from ..authorization import Permission
from ..dependencies import has_permission, tenant_context
from ..model_budget import ModelBudgetExceededError
from ..schemas import TenantContext, new_id, utc_now
from . import ai, service
from .config import call_settings
from .phone import PhoneNumberError, dialable, masked, normalized
from .provider import ProviderError
from .service import require, require_edit
from .store import (
    DEFAULT_PAGE,
    MAX_PAGE,
    AiCallRequest,
    AiConsent,
    AiRequestStatus,
    CallContact,
    CallEntry,
    CallFilter,
    CallKind,
    CallStatus,
    ConsentMethod,
    Direction,
    FollowUp,
    OptOut,
    OptOutSource,
    Outcome,
    ProviderAccount,
    RecordingConsent,
    action_lock,
    open_calls_store,
)

logger = logging.getLogger("anum.calls")
router = APIRouter(prefix="/api/v1/calls", tags=["calls"])

_EMAIL = re.compile(r"^[^@\s<>,;]{1,64}@[^@\s<>,;]{1,255}\.[^@\s<>,;]{2,63}$")
_SID = r"^[A-Za-z0-9]{10,64}$"


def _phone(value: str) -> str:
    try:
        return dialable(value)
    except PhoneNumberError as exc:
        raise ValueError(str(exc)) from None


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


# -- schemas -----------------------------------------------------------------------------


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=3, max_length=40)
    company: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    notes: str | None = Field(default=None, max_length=4000)
    favourite: bool = False
    # "device": picked from the phone's address book (only this one contact is stored).
    source: Literal["manual", "device"] = "manual"
    language: Literal["auto", "en", "ar"] = "auto"

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, value: str) -> str:
        return _phone(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("enter a name")
        return value.strip()

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = _text(value)
        if value is not None and not _EMAIL.match(value):
            raise ValueError("not an email address")
        return value


class ContactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=3, max_length=40)
    company: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    notes: str | None = Field(default=None, max_length=4000)
    favourite: bool | None = None
    language: Literal["auto", "en", "ar"] | None = None

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, value: str | None) -> str | None:
        return _phone(value) if value is not None else None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = _text(value)
        if value is not None and not _EMAIL.match(value):
            raise ValueError("not an email address")
        return value


class ContactView(CallContact):
    opted_out: bool = False
    can_edit: bool = False


class ContactPage(BaseModel):
    items: list[ContactView]
    total: int
    offset: int
    limit: int


class ConsentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: ConsentMethod
    note: str | None = Field(default=None, max_length=500)
    # The owner confirms the person agreed to calls from the AI assistant.
    confirmed: bool

    @field_validator("confirmed")
    @classmethod
    def _confirmed(cls, value: bool) -> bool:
        if not value:
            raise ValueError("confirm that the person agreed to calls from the AI assistant")
        return value


class CallCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["phone", "in_app"] = "phone"
    contact_id: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    # The other ANUM user of an in-app call (the in-app calling module writes these).
    peer_user_id: str | None = Field(default=None, max_length=160)
    direction: Direction = Direction.OUTBOUND
    outcome: Outcome
    started_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    notes: str | None = Field(default=None, max_length=8000)

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, value: str | None) -> str | None:
        return _phone(value) if value else None

    @field_validator("started_at")
    @classmethod
    def _zone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("give the time with a time zone")
        return value


class CallUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Direction | None = None
    outcome: Outcome | None = None
    started_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    notes: str | None = Field(default=None, max_length=8000)
    contact_id: str | None = Field(default=None, max_length=80)


class FollowUpUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    due_date: date | None = None


class CallView(CallEntry):
    can_edit: bool = False


class CallPage(BaseModel):
    items: list[CallView]
    total: int
    offset: int
    limit: int


class OptOutCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=3, max_length=40)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, value: str) -> str:
        return _phone(value)


class OptOutRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=500)


class OptOutPage(BaseModel):
    items: list[OptOut]
    total: int
    offset: int
    limit: int


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_sid: str = Field(pattern=_SID)
    # Required on the first save; empty keeps the stored token.
    auth_token: str | None = Field(default=None, max_length=200, pattern=r"^[\x21-\x7e]{16,200}$")
    api_key_sid: str | None = Field(default=None, pattern=_SID)
    api_key_secret: str | None = Field(default=None, max_length=200, pattern=r"^[\x21-\x7e]{16,200}$")
    twiml_app_sid: str | None = Field(default=None, pattern=_SID)
    from_number: str
    business_name: str = Field(min_length=1, max_length=120)
    greeting: str | None = Field(default=None, max_length=500)
    recording_enabled: bool = False
    transcription_enabled: bool = False
    recording_retention_days: int = Field(default=30, ge=1, le=365)
    ai_calls_enabled: bool = False
    inbound_ai_enabled: bool = False
    ai_max_seconds: int = Field(default=180, ge=30, le=900)
    ai_max_cost_cents: int = Field(default=100, ge=1, le=10000)
    cost_per_minute_cents: int = Field(default=5, ge=0, le=1000)

    @field_validator("from_number")
    @classmethod
    def _number(cls, value: str) -> str:
        from .phone import international

        found = international(value)
        if found is None:
            raise ValueError("enter the provider number in international form, for example +971501234567")
        return found


class ProviderView(BaseModel):
    connected: bool
    provider: str = "twilio"
    account_sid: str | None = None
    api_key_sid: str | None = None
    twiml_app_sid: str | None = None
    from_number: str | None = None
    credential_hint: str | None = None
    has_api_key_secret: bool = False
    business_name: str | None = None
    greeting: str | None = None
    recording_enabled: bool = False
    transcription_enabled: bool = False
    recording_retention_days: int = 30
    ai_calls_enabled: bool = False
    inbound_ai_enabled: bool = False
    ai_max_seconds: int = 180
    ai_max_cost_cents: int = 100
    cost_per_minute_cents: int = 5
    last_check_status: str | None = None
    last_check_detail: str | None = None
    last_checked_at: datetime | None = None
    # Where the owner points the provider (Twilio console): the TwiML app's and the number's
    # voice URL, and the number's call status URL. Empty until the server has a public address.
    voice_url: str | None = None
    status_url: str | None = None
    webhooks_ready: bool
    problem: str | None = None
    limits: dict[str, int]


class Capabilities(BaseModel):
    browser_calls: bool
    recording: bool
    transcription: bool
    ai_calls: bool
    inbound_ai: bool
    problem: str | None = None
    ai_problem: str | None = None
    cost_per_minute_cents: int | None = None
    # What ANUM says before a recorded call connects, in English and Arabic (shown in the consent step).
    recording_notice: dict[str, str] | None = None


class CallsSetup(BaseModel):
    can_read: bool
    can_log: bool
    can_manage: bool
    can_create_tasks: bool
    model_ready: bool
    provider_connected: bool
    capabilities: Capabilities
    transcription_configured: bool


class AiCallCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=5, max_length=500)
    script: str = Field(min_length=10, max_length=4000)
    language: Literal["en", "ar"] = "en"
    max_seconds: int = Field(default=120, ge=30, le=900)


class AiCallView(AiCallRequest):
    approval: Any = None
    phone_masked: str


class AiCallPage(BaseModel):
    items: list[AiCallView]
    total: int
    offset: int
    limit: int


class BrowserCallCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    record: bool = False
    # The person confirms the other side will hear the recording notice (spoken by ANUM
    # before the call connects) and that recording this call is lawful for them.
    recording_consent: bool = False
    language: Literal["en", "ar"] = "en"


class BrowserCall(BaseModel):
    call: CallView
    token: str
    identity: str
    expires_in: int


# -- helpers -------------------------------------------------------------------------------


def _page(offset: int, limit: int) -> tuple[int, int]:
    return max(offset, 0), max(1, min(limit, MAX_PAGE))


def _contact_view(store: Any, context: TenantContext, contact: CallContact) -> ContactView:
    return ContactView(
        **contact.model_dump(), opted_out=service.opted_out(store, context, contact.phone_normalized) is not None,
        can_edit=service.can_edit(context, contact.created_by),
    )


def _stale(entry: CallEntry) -> bool:
    """A browser call that never reached the provider (the tab closed, the microphone was refused)."""
    return entry.kind == CallKind.PROVIDER and entry.status == CallStatus.DIALING and not entry.provider_call_id and service.dial_expired(entry)


def _call_view(context: TenantContext, entry: CallEntry) -> CallView:
    if _stale(entry):
        entry = entry.model_copy(update={"status": CallStatus.FAILED, "outcome": Outcome.CANCELLED})
    return CallView(**entry.model_dump(), can_edit=service.can_edit(context, entry.user_id))


def _contact_or_404(store: Any, contact_id: str) -> CallContact:
    contact = store.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found in this workspace")
    return contact


def _call_or_404(store: Any, call_id: str, *, lock: bool = False) -> CallEntry:
    entry = store.get_call(call_id, lock=lock)
    if entry is None:
        raise HTTPException(status_code=404, detail="Call not found in this workspace's log")
    return entry


def _provider_view(account: ProviderAccount | None) -> ProviderView:
    limits = {"ai_max_seconds": call_settings.ai_max_seconds, "ai_max_cost_cents": call_settings.ai_max_cost_cents}
    if account is None:
        return ProviderView(connected=False, webhooks_ready=call_settings.webhooks_ready, problem=service.provider_problem(None), limits=limits)
    secret_known = False
    try:
        secret_known = bool(service.credentials_of(account).api_key_secret)
    except Exception:  # an undecryptable secret shows as a failed check, never as an error page
        secret_known = False
    ready = call_settings.webhooks_ready
    return ProviderView(
        connected=True, **account.model_dump(include={
            "provider", "account_sid", "api_key_sid", "twiml_app_sid", "from_number", "credential_hint", "business_name", "greeting",
            "recording_enabled", "transcription_enabled", "recording_retention_days", "ai_calls_enabled", "inbound_ai_enabled",
            "ai_max_seconds", "ai_max_cost_cents", "cost_per_minute_cents", "last_check_status", "last_check_detail", "last_checked_at",
        }),
        has_api_key_secret=secret_known,
        voice_url=service.webhook_url(account.route_id, "voice") if ready else None,
        status_url=service.webhook_url(account.route_id, "status") if ready else None,
        webhooks_ready=ready, problem=service.provider_problem(account), limits=limits,
    )


def _ai_view(request: AiCallRequest, approval: Any = None) -> AiCallView:
    return AiCallView(**request.model_dump(), approval=approval, phone_masked=masked(request.phone_normalized))


def _model_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ModelBudgetExceededError):
        return HTTPException(status_code=429, detail=f"{exc.message} The notes are saved; summarise again after the reset or once the budget is raised.")
    if isinstance(exc, ai.CallAiParseError):
        return HTTPException(status_code=502, detail="The model's answer could not be read as a call summary. Try again, or choose a more capable model in Settings > Model.")
    return HTTPException(status_code=502, detail="The model could not be reached. Check Settings > Model, then try again.")


# -- setup -----------------------------------------------------------------------------------


@router.get("/setup", response_model=CallsSetup)
def setup(context: TenantContext = Depends(tenant_context)) -> CallsSetup:
    """What the caller may do and what works in this workspace now."""
    from ..transcription import build_transcriber
    from ..settings import settings

    require(context, Permission.CALL_READ)
    with open_calls_store(context) as store:
        account = store.get_provider()
    gateway = ai.call_gateway(context)
    problem = service.provider_problem(account)
    browser = service.browser_problem(account)
    ai_problem = service.ai_problem(account, gateway)
    return CallsSetup(
        can_read=True, can_log=has_permission(context, Permission.CALL_LOG), can_manage=has_permission(context, Permission.CALL_MANAGE),
        can_create_tasks=has_permission(context, Permission.TASK_CREATE), model_ready=ai.model_ready(gateway),
        provider_connected=account is not None,
        capabilities=Capabilities(
            browser_calls=browser is None, recording=bool(account and problem is None and account.recording_enabled),
            transcription=bool(account and problem is None and account.transcription_enabled),
            ai_calls=ai_problem is None, inbound_ai=bool(account and problem is None and account.inbound_ai_enabled and ai.model_ready(gateway)),
            problem=browser, ai_problem=ai_problem, cost_per_minute_cents=account.cost_per_minute_cents if account else None,
            recording_notice={
                language: service.recording_notice(account.business_name, transcription=account.transcription_enabled, language=language)
                for language in ("en", "ar")
            } if account and account.recording_enabled else None,
        ),
        transcription_configured=build_transcriber(settings) is not None,
    )


# -- contacts --------------------------------------------------------------------------------


@router.get("/contacts", response_model=ContactPage)
def list_contacts(
    q: str | None = Query(default=None, max_length=200),
    favourites: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    context: TenantContext = Depends(tenant_context),
) -> ContactPage:
    require(context, Permission.CALL_READ)
    offset, limit = _page(offset, limit)
    with open_calls_store(context) as store:
        items, total = store.list_contacts(query=_text(q), favourites=favourites, offset=offset, limit=limit)
        views = [_contact_view(store, context, item) for item in items]
    return ContactPage(items=views, total=total, offset=offset, limit=limit)


@router.post("/contacts", response_model=ContactView, status_code=status.HTTP_201_CREATED)
def create_contact(payload: ContactCreate, context: TenantContext = Depends(tenant_context)) -> ContactView:
    require(context, Permission.CALL_LOG)
    now = utc_now()
    contact = CallContact(
        id=new_id("contact"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_by=context.user_id,
        name=payload.name, phone=payload.phone, phone_normalized=normalized(payload.phone), company=_text(payload.company),
        email=payload.email, notes=_text(payload.notes), favourite=payload.favourite, source=payload.source,
        language=payload.language, created_at=now, updated_at=now,
    )
    with open_calls_store(context) as store:
        store.add_contact(contact)
        view = _contact_view(store, context, contact)
    service.audit(context, "call.contact.created", contact.id, metadata={"source": payload.source})
    return view


@router.get("/contacts/{contact_id}", response_model=ContactView)
def get_contact(contact_id: str, context: TenantContext = Depends(tenant_context)) -> ContactView:
    require(context, Permission.CALL_READ)
    with open_calls_store(context) as store:
        return _contact_view(store, context, _contact_or_404(store, contact_id))


@router.patch("/contacts/{contact_id}", response_model=ContactView)
def update_contact(contact_id: str, payload: ContactUpdate, context: TenantContext = Depends(tenant_context)) -> ContactView:
    """Change a contact. Anyone who logs calls may star or unstar it; other fields need the
    person who added it or an owner. A new number withdraws the AI call consent (it was given
    for the old number)."""
    require(context, Permission.CALL_LOG)
    changes = payload.model_dump(exclude_unset=True)
    with open_calls_store(context) as store:
        contact = _contact_or_404(store, contact_id)
        if set(changes) - {"favourite"}:
            require_edit(context, contact.created_by)
        update: dict[str, Any] = {"updated_at": utc_now()}
        for name in ("name", "company", "notes"):
            if name in changes:
                update[name] = _text(changes[name]) if name != "name" else str(changes[name]).strip()
        for name in ("email", "favourite", "language"):
            if name in changes and changes[name] is not None:
                update[name] = changes[name]
        if "email" in changes and changes["email"] is None:
            update["email"] = None
        consent_dropped = False
        if changes.get("phone"):
            update["phone"] = changes["phone"]
            update["phone_normalized"] = normalized(changes["phone"])
            if update["phone_normalized"] != contact.phone_normalized and contact.ai_consent is not None:
                update["ai_consent"] = None
                consent_dropped = True
        saved = contact.model_copy(update=update)
        store.save_contact(saved)
        view = _contact_view(store, context, saved)
    service.audit(context, "call.contact.updated", contact_id, metadata={"fields": sorted(changes), "consent_withdrawn": consent_dropped})
    return view


@router.delete("/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(contact_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Delete a contact. Calls already logged keep the name they were logged with."""
    require(context, Permission.CALL_LOG)
    with open_calls_store(context) as store:
        contact = _contact_or_404(store, contact_id)
        require_edit(context, contact.created_by)
        store.delete_contact(contact_id)
    service.audit(context, "call.contact.deleted", contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/contacts/{contact_id}/ai-consent", response_model=ContactView)
def record_consent(contact_id: str, payload: ConsentUpdate, context: TenantContext = Depends(tenant_context)) -> ContactView:
    """Record that this person agreed to calls from the AI assistant (owners only)."""
    require(context, Permission.CALL_MANAGE)
    with open_calls_store(context) as store:
        contact = _contact_or_404(store, contact_id)
        if service.opted_out(store, context, contact.phone_normalized) is not None:
            raise HTTPException(status_code=409, detail="This number opted out of AI calls. Remove the opt-out first, only if the person asked to be called again.")
        consent = AiConsent(granted_at=utc_now(), recorded_by=context.user_id, method=payload.method, note=_text(payload.note))
        saved = contact.model_copy(update={"ai_consent": consent, "updated_at": utc_now()})
        store.save_contact(saved)
        view = _contact_view(store, context, saved)
    service.audit(context, "call.consent.recorded", contact_id, metadata={"method": payload.method.value})
    return view


@router.delete("/contacts/{contact_id}/ai-consent", response_model=ContactView)
def withdraw_consent(contact_id: str, context: TenantContext = Depends(tenant_context)) -> ContactView:
    """Withdraw the AI call consent. Anyone who logs calls may do it (it only stops calls)."""
    require(context, Permission.CALL_LOG)
    with open_calls_store(context) as store:
        contact = _contact_or_404(store, contact_id)
        saved = contact.model_copy(update={"ai_consent": None, "updated_at": utc_now()})
        store.save_contact(saved)
        view = _contact_view(store, context, saved)
    service.audit(context, "call.consent.withdrawn", contact_id)
    return view


# -- call log --------------------------------------------------------------------------------


@router.get("/log", response_model=CallPage)
def list_calls(
    q: str | None = Query(default=None, max_length=200),
    direction: Direction | None = None,
    outcome: Outcome | None = None,
    kind: CallKind | None = None,
    contact_id: str | None = Query(default=None, max_length=80),
    mine: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    context: TenantContext = Depends(tenant_context),
) -> CallPage:
    require(context, Permission.CALL_READ)
    offset, limit = _page(offset, limit)
    flt = CallFilter(query=_text(q), direction=direction, outcome=outcome, kind=kind, contact_id=contact_id, user_id=context.user_id if mine else None)
    with open_calls_store(context) as store:
        items, total = store.list_calls(flt, offset=offset, limit=limit)
    return CallPage(items=[_call_view(context, item) for item in items], total=total, offset=offset, limit=limit)


@router.post("/log", response_model=CallView, status_code=status.HTTP_201_CREATED)
def log_call(payload: CallCreate, context: TenantContext = Depends(tenant_context)) -> CallView:
    """Log a call made on the phone's own dialer (``kind: phone``) or an in-app call (``kind: in_app``)."""
    require(context, Permission.CALL_LOG)
    now = utc_now()
    with open_calls_store(context) as store:
        contact = _contact_or_404(store, payload.contact_id) if payload.contact_id else None
        phone = payload.phone or (contact.phone if contact else None)
        if payload.kind == "phone" and not phone:
            raise HTTPException(status_code=422, detail="Give the number that was called, or pick a contact.")
        if payload.kind == "in_app" and not payload.peer_user_id:
            raise HTTPException(status_code=422, detail="An in-app call names the other ANUM user (peer_user_id).")
        started = payload.started_at.astimezone(timezone.utc) if payload.started_at else now
        if started > now:
            raise HTTPException(status_code=422, detail="A call cannot start in the future.")
        entry = CallEntry(
            id=new_id("call"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id,
            contact_id=contact.id if contact else None, contact_name=contact.name if contact else None,
            phone=phone, phone_normalized=normalized(phone) if phone else None, peer_user_id=payload.peer_user_id,
            direction=payload.direction, kind=CallKind(payload.kind), status=CallStatus.LOGGED, outcome=payload.outcome,
            started_at=started, duration_seconds=payload.duration_seconds, notes=_text(payload.notes), created_at=now, updated_at=now,
        )
        if contact is None and entry.phone_normalized:
            matches = store.contacts_by_number(entry.phone_normalized)
            if len(matches) == 1:
                entry = entry.model_copy(update={"contact_id": matches[0].id, "contact_name": matches[0].name})
        store.add_call(entry)
    service.audit(context, "call.logged", entry.id, metadata={"kind": entry.kind.value, "direction": entry.direction.value, "outcome": entry.outcome.value if entry.outcome else None})
    return _call_view(context, entry)


@router.get("/log/{call_id}", response_model=CallView)
def get_call(call_id: str, context: TenantContext = Depends(tenant_context)) -> CallView:
    require(context, Permission.CALL_READ)
    with open_calls_store(context) as store:
        return _call_view(context, _call_or_404(store, call_id))


@router.patch("/log/{call_id}", response_model=CallView)
def update_call(call_id: str, payload: CallUpdate, context: TenantContext = Depends(tenant_context)) -> CallView:
    require(context, Permission.CALL_LOG)
    changes = payload.model_dump(exclude_unset=True)
    with open_calls_store(context) as store:
        entry = _call_or_404(store, call_id, lock=True)
        require_edit(context, entry.user_id)
        update: dict[str, Any] = {"updated_at": utc_now()}
        for name in ("direction", "outcome", "duration_seconds"):
            if name in changes and changes[name] is not None:
                update[name] = changes[name]
        if "notes" in changes:
            update["notes"] = _text(changes["notes"])
        if changes.get("started_at"):
            started = changes["started_at"]
            if started.tzinfo is None:
                raise HTTPException(status_code=422, detail="Give the time with a time zone.")
            update["started_at"] = started.astimezone(timezone.utc)
        if "contact_id" in changes:
            contact = _contact_or_404(store, changes["contact_id"]) if changes["contact_id"] else None
            update["contact_id"] = contact.id if contact else None
            update["contact_name"] = contact.name if contact else None
        saved = entry.model_copy(update=update)
        store.save_call(saved)
    service.audit(context, "call.updated", call_id, metadata={"fields": sorted(changes)})
    return _call_view(context, saved)


@router.delete("/log/{call_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_call(call_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """Delete a call from the log, with its recording."""
    require(context, Permission.CALL_LOG)
    with open_calls_store(context) as store:
        entry = _call_or_404(store, call_id)
        require_edit(context, entry.user_id)
        if entry.status in {CallStatus.DIALING, CallStatus.RINGING, CallStatus.IN_PROGRESS} and not _stale(entry):
            raise HTTPException(status_code=409, detail="This call is still going on; delete it once it has ended.")
        store.delete_call(call_id)
    if entry.recording_file_id:
        from ..files import remove_workspace_file

        try:
            remove_workspace_file(context, entry.recording_file_id)
        except Exception as exc:  # the log row is gone; the file is removed by the retention purge
            logger.warning("call_recording_delete_failed", extra={"anum_error_class": type(exc).__name__})
    service.audit(context, "call.deleted", call_id, metadata={"had_recording": bool(entry.recording_file_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/log/{call_id}/summarize", response_model=CallView)
async def summarize_call(call_id: str, context: TenantContext = Depends(tenant_context)) -> CallView:
    """Write the AI summary and a suggested follow-up from the notes or transcript (budgeted)."""
    require(context, Permission.CALL_LOG)
    with open_calls_store(context) as store:
        entry = _call_or_404(store, call_id)
    require_edit(context, entry.user_id)
    if not (entry.notes or entry.transcript):
        raise HTTPException(status_code=422, detail="Add notes about the call first; the summary is written from them.")
    gateway = ai.call_gateway(context)
    if not ai.model_ready(gateway):
        raise HTTPException(status_code=409, detail="Summaries need a real model. Connect one in Settings > Model; the notes are saved.")
    try:
        result = await service.summarize_entry(gateway, entry)
    except Exception as exc:
        if not isinstance(exc, (ModelBudgetExceededError, ai.CallAiParseError)):
            logger.warning("call_summary_failed", extra={"anum_error_class": type(exc).__name__})
        raise _model_error(exc) from None
    with open_calls_store(context) as store:
        current = _call_or_404(store, call_id, lock=True)
        saved = service.apply_summary(current, result)
        store.save_call(saved)
    service.audit(context, "call.summarized", call_id, metadata={"follow_up": saved.follow_up is not None, "model": result.provenance.get("model")})
    return _call_view(context, saved)


@router.put("/log/{call_id}/follow-up", response_model=CallView)
def update_follow_up(call_id: str, payload: FollowUpUpdate, context: TenantContext = Depends(tenant_context)) -> CallView:
    require(context, Permission.CALL_LOG)
    with open_calls_store(context) as store:
        entry = _call_or_404(store, call_id, lock=True)
        require_edit(context, entry.user_id)
        task_id = entry.follow_up.task_id if entry.follow_up else None
        saved = entry.model_copy(update={"follow_up": FollowUp(text=payload.text.strip(), due_date=payload.due_date, task_id=task_id, edited=True), "updated_at": utc_now()})
        store.save_call(saved)
    service.audit(context, "call.follow_up.updated", call_id)
    return _call_view(context, saved)


@router.delete("/log/{call_id}/follow-up", response_model=CallView)
def clear_follow_up(call_id: str, context: TenantContext = Depends(tenant_context)) -> CallView:
    require(context, Permission.CALL_LOG)
    with open_calls_store(context) as store:
        entry = _call_or_404(store, call_id, lock=True)
        require_edit(context, entry.user_id)
        saved = entry.model_copy(update={"follow_up": None, "updated_at": utc_now()})
        store.save_call(saved)
    service.audit(context, "call.follow_up.cleared", call_id)
    return _call_view(context, saved)


@router.post("/log/{call_id}/follow-up/task", response_model=CallView)
def follow_up_task(call_id: str, context: TenantContext = Depends(tenant_context)) -> CallView:
    """Save the follow-up as a workspace task, once."""
    require(context, Permission.CALL_LOG)
    if not has_permission(context, Permission.TASK_CREATE):
        raise HTTPException(status_code=403, detail="Creating tasks needs the owner or member role in this workspace.")
    from ..meetings import _create_task

    with action_lock, open_calls_store(context) as store:
        entry = _call_or_404(store, call_id, lock=True)
        require_edit(context, entry.user_id)
        if entry.follow_up is None:
            raise HTTPException(status_code=422, detail="This call has no follow-up to save. Write one or summarise the notes first.")
        if entry.follow_up.task_id:
            raise HTTPException(status_code=409, detail="This follow-up is already a task.")
        who = entry.contact_name or (masked(entry.phone_normalized) if entry.phone_normalized else "a caller")
        prompt = "\n".join(line for line in (
            f"Follow-up from a phone call with {who} on {entry.started_at.date().isoformat()}.",
            f"Due: {entry.follow_up.due_date.isoformat()}" if entry.follow_up.due_date else "Due: no date",
            "", entry.follow_up.text,
            "", f"Call summary: {entry.summary}" if entry.summary else "",
        ) if line is not None)
        task_id = _create_task(context, entry.follow_up.text, prompt.strip())
        saved = entry.model_copy(update={"follow_up": entry.follow_up.model_copy(update={"task_id": task_id}), "updated_at": utc_now()})
        store.save_call(saved)
    service.audit(context, "call.follow_up.task_created", call_id, metadata={"task_id": task_id})
    return _call_view(context, saved)


# -- opt-outs ----------------------------------------------------------------------------------


@router.get("/opt-outs", response_model=OptOutPage)
def list_opt_outs(
    offset: int = Query(default=0, ge=0), limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    context: TenantContext = Depends(tenant_context),
) -> OptOutPage:
    require(context, Permission.CALL_READ)
    offset, limit = _page(offset, limit)
    with open_calls_store(context) as store:
        items, total = store.list_opt_outs(offset=offset, limit=limit)
    return OptOutPage(items=items, total=total, offset=offset, limit=limit)


@router.post("/opt-outs", response_model=OptOut, status_code=status.HTTP_201_CREATED)
def add_opt_out(payload: OptOutCreate, context: TenantContext = Depends(tenant_context)) -> OptOut:
    """Record that a number asked not to be called by the AI. It also withdraws the consent of
    every contact with that number."""
    require(context, Permission.CALL_LOG)
    number = normalized(payload.phone)
    with open_calls_store(context) as store:
        entry = service.record_opt_out(store, context, number, OptOutSource.OWNER, _text(payload.note), context.user_id)
        withdrawn = 0
        for contact in store.contacts_by_number(number):
            if contact.ai_consent is not None:
                store.save_contact(contact.model_copy(update={"ai_consent": None, "updated_at": utc_now()}))
                withdrawn += 1
    service.audit(context, "call.opt_out.added", entry.id, metadata={"consents_withdrawn": withdrawn})
    return entry


@router.delete("/opt-outs/{opt_out_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_opt_out(opt_out_id: str, payload: OptOutRemove, context: TenantContext = Depends(tenant_context)) -> Response:
    """Lift an opt-out (owners only, with a reason): only when the person asked to be called again."""
    require(context, Permission.CALL_MANAGE)
    with open_calls_store(context) as store:
        removed = store.delete_opt_out(opt_out_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Opt-out not found in this workspace")
    service.audit(context, "call.opt_out.removed", opt_out_id, metadata={"reason_chars": len(payload.reason)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- provider (owners) ----------------------------------------------------------------------


@router.get("/provider", response_model=ProviderView)
def get_provider(context: TenantContext = Depends(tenant_context)) -> ProviderView:
    require(context, Permission.CALL_MANAGE)
    with open_calls_store(context) as store:
        return _provider_view(store.get_provider())


@router.put("/provider", response_model=ProviderView)
async def save_provider(payload: ProviderUpdate, context: TenantContext = Depends(tenant_context)) -> ProviderView:
    """Connect or change the Twilio account. Secrets are stored encrypted and never returned;
    the account is checked with Twilio before anything is saved."""
    require(context, Permission.CALL_MANAGE)
    if payload.ai_max_seconds > call_settings.ai_max_seconds or payload.ai_max_cost_cents > call_settings.ai_max_cost_cents:
        raise HTTPException(status_code=422, detail=(
            f"This server allows AI calls of at most {call_settings.ai_max_seconds} seconds and {call_settings.ai_max_cost_cents} cents."
        ))
    if (payload.api_key_sid is None) != (payload.twiml_app_sid is None):
        raise HTTPException(status_code=422, detail="Browser calls need both the API key SID and the TwiML app SID (or neither).")
    with open_calls_store(context) as store:
        existing = store.get_provider()
    old = service.credentials_of(existing) if existing is not None else None
    auth_token = payload.auth_token or (old.auth_token if old and existing and existing.account_sid == payload.account_sid else None)
    if not auth_token:
        raise HTTPException(status_code=422, detail="Enter the Twilio auth token.")
    key_secret = payload.api_key_secret or (old.api_key_secret if old and existing and existing.api_key_sid == payload.api_key_sid else None)
    if payload.api_key_sid and not key_secret:
        raise HTTPException(status_code=422, detail="Enter the API key secret for this API key SID.")
    from .provider import Credentials

    checker = service.provider_factory(Credentials(account_sid=payload.account_sid, auth_token=auth_token))
    try:
        detail = await checker.check()
    except ProviderError as exc:
        service.audit(context, "call.provider.check", "twilio", outcome="failure", metadata={"stage": "save"})
        raise HTTPException(status_code=422, detail=f"Not saved: {exc.detail}") from None
    now = utc_now()
    account = ProviderAccount(
        tenant_id=context.tenant_id, workspace_id=context.workspace_id, route_id=existing.route_id if existing else service.new_route_id(),
        account_sid=payload.account_sid, api_key_sid=payload.api_key_sid if key_secret else None,
        twiml_app_sid=payload.twiml_app_sid if key_secret else None, from_number=payload.from_number,
        credentials_ciphertext=service.encrypt_credentials(auth_token, key_secret if payload.api_key_sid else None),
        credential_hint=f"…{auth_token[-4:]}", business_name=payload.business_name.strip(), greeting=_text(payload.greeting),
        recording_enabled=payload.recording_enabled, transcription_enabled=payload.transcription_enabled and payload.recording_enabled,
        recording_retention_days=payload.recording_retention_days, ai_calls_enabled=payload.ai_calls_enabled,
        inbound_ai_enabled=payload.inbound_ai_enabled, ai_max_seconds=payload.ai_max_seconds, ai_max_cost_cents=payload.ai_max_cost_cents,
        cost_per_minute_cents=payload.cost_per_minute_cents, last_check_status="ok", last_check_detail=detail[:500], last_checked_at=now,
        updated_by=context.user_id, created_at=existing.created_at if existing else now, updated_at=now,
    )
    with open_calls_store(context) as store:
        store.save_provider(account)
    service.audit(context, "call.provider.saved", "twilio", metadata={
        "replaced": existing is not None, "browser_calls": bool(account.api_key_sid), "recording": account.recording_enabled,
        "ai_calls": account.ai_calls_enabled, "inbound_ai": account.inbound_ai_enabled,
    })
    return _provider_view(account)


@router.post("/provider/check", response_model=ProviderView)
async def check_provider(context: TenantContext = Depends(tenant_context)) -> ProviderView:
    require(context, Permission.CALL_MANAGE)
    with open_calls_store(context) as store:
        account = store.get_provider()
    if account is None:
        raise HTTPException(status_code=404, detail="No voice provider is connected.")
    try:
        detail, result = await service.voice(account).check(), "ok"
    except ProviderError as exc:
        detail, result = exc.detail, "failed"
    with open_calls_store(context) as store:
        current = store.get_provider()
        if current is None:
            raise HTTPException(status_code=404, detail="No voice provider is connected.")
        current = current.model_copy(update={"last_check_status": result, "last_check_detail": detail[:500], "last_checked_at": utc_now()})
        store.save_provider(current)
    service.audit(context, "call.provider.check", "twilio", outcome="success" if result == "ok" else "failure")
    return _provider_view(current)


@router.delete("/provider", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(context: TenantContext = Depends(tenant_context)) -> Response:
    """Disconnect the provider: the stored credentials are deleted. The call log stays."""
    require(context, Permission.CALL_MANAGE)
    with open_calls_store(context) as store:
        removed = store.delete_provider()
    if not removed:
        raise HTTPException(status_code=404, detail="No voice provider is connected.")
    service.audit(context, "call.provider.removed", "twilio")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/provider/calls", response_model=BrowserCall, status_code=status.HTTP_201_CREATED)
def prepare_browser_call(payload: BrowserCallCreate, context: TenantContext = Depends(tenant_context)) -> BrowserCall:
    """Prepare a call from the browser through the provider: a call log entry and a short-lived
    access token for the provider's browser SDK. The provider then asks ANUM (signed webhook)
    what to dial, and ANUM dials only the number prepared here."""
    require(context, Permission.CALL_LOG)
    if payload.record and not payload.recording_consent:
        raise HTTPException(status_code=422, detail="Recording needs your confirmation that the other side will hear the recording notice and that recording this call is lawful.")
    now = utc_now()
    with open_calls_store(context) as store:
        account = store.get_provider()
        problem = service.browser_problem(account)
        if problem:
            raise HTTPException(status_code=409, detail=problem)
        if account is None:  # checked just above
            raise RuntimeError("the provider was checked")
        if payload.record and not account.recording_enabled:
            raise HTTPException(status_code=409, detail="Recording is off in this workspace. A workspace owner turns it on under Calls > Provider.")
        contact = _contact_or_404(store, payload.contact_id) if payload.contact_id else None
        raw = payload.phone or (contact.phone if contact else None)
        from .phone import international

        number = international(raw or "")
        if number is None:
            raise HTTPException(status_code=422, detail="Provider calls need a full international number, for example +971501234567.")
        consent = None
        if payload.record:
            consent = RecordingConsent(
                confirmed_by=context.user_id, confirmed_at=now, transcription=account.transcription_enabled,
                notice=service.recording_notice(account.business_name, transcription=account.transcription_enabled, language=payload.language),
            )
        entry = CallEntry(
            id=new_id("call"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, user_id=context.user_id,
            contact_id=contact.id if contact else None, contact_name=contact.name if contact else None, phone=raw, phone_normalized=number,
            direction=Direction.OUTBOUND, kind=CallKind.PROVIDER, status=CallStatus.DIALING, started_at=now,
            provider=account.provider, recording_consent=consent, created_at=now, updated_at=now,
        )
        if contact is None:
            matches = store.contacts_by_number(number)
            if len(matches) == 1:
                entry = entry.model_copy(update={"contact_id": matches[0].id, "contact_name": matches[0].name})
        store.add_call(entry)
        provider = service.voice(account)
    identity = service.identity_for(context)
    try:
        token = provider.browser_token(identity, ttl_seconds=600)
    except ProviderError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from None
    service.audit(context, "call.provider.prepared", entry.id, metadata={"record": payload.record})
    return BrowserCall(call=_call_view(context, entry), token=token, identity=identity, expires_in=600)


# -- AI calls --------------------------------------------------------------------------------


@router.get("/ai-calls", response_model=AiCallPage)
def list_ai_calls(
    offset: int = Query(default=0, ge=0), limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    context: TenantContext = Depends(tenant_context),
) -> AiCallPage:
    require(context, Permission.CALL_READ)
    offset, limit = _page(offset, limit)
    with open_calls_store(context) as store:
        items, total = store.list_requests(offset=offset, limit=limit)
    approvals = _approvals(context, [item.approval_id for item in items if item.approval_id and item.status == AiRequestStatus.PENDING_APPROVAL])
    return AiCallPage(items=[_ai_view(item, approvals.get(item.approval_id or "")) for item in items], total=total, offset=offset, limit=limit)


def _approvals(context: TenantContext, ids: list[str]) -> dict[str, Any]:
    if not ids:
        return {}
    from ..dependencies import _unit_of_work

    found: dict[str, Any] = {}
    with _unit_of_work(context, require_membership=False) as repository:
        for approval_id in ids:
            approval = repository.get_approval(approval_id, context)
            if approval is not None:
                found[approval_id] = approval
    return found


@router.post("/ai-calls", response_model=AiCallView, status_code=status.HTTP_201_CREATED)
def request_ai_call(payload: AiCallCreate, context: TenantContext = Depends(tenant_context)) -> AiCallView:
    """Ask for an AI call to a consenting contact. Nothing is dialled: the call waits for a
    workspace owner's approval of the exact number, purpose and script (payload hash)."""
    require(context, Permission.CALL_LOG)
    from ..dependencies import _unit_of_work
    from ..email_api import _pause_for_approval
    from .tools import call_tool_registry

    gateway = ai.call_gateway(context)
    now = utc_now()
    with open_calls_store(context) as store:
        account = store.get_provider()
        contact = store.get_contact(payload.contact_id)
        problem = service.check_ai_call(store, context, contact, account, gateway, max_seconds=payload.max_seconds)
        if problem:
            raise HTTPException(status_code=409, detail=problem)
        if contact is None or account is None:  # checked just above
            raise RuntimeError("the contact and provider were checked")
        request = AiCallRequest(
            id=new_id("aicall"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, requested_by=context.user_id,
            contact_id=contact.id, contact_name=contact.name, phone_normalized=contact.phone_normalized,
            purpose=payload.purpose.strip(), script=payload.script.strip(), language=payload.language, max_seconds=payload.max_seconds,
            max_cost_cents=service.estimated_cost_cents(payload.max_seconds, account),
            status=AiRequestStatus.PENDING_APPROVAL, created_at=now, updated_at=now,
        )
        arguments = service.ai_call_arguments(
            contact, purpose=request.purpose, script=request.script, language=request.language, max_seconds=request.max_seconds, request_id=request.id,
        )
    hits = service.suppressed(context, arguments)
    if hits:
        raise HTTPException(status_code=409, detail="This number is on the workspace's contact suppression list; it is never called.")
    call = ToolCall(name=service.AI_CALL_TOOL, arguments=arguments)
    with _unit_of_work(context, require_membership=True) as repository:
        task, approval = _pause_for_approval(
            repository, context, call, f"AI call to {contact.name}",
            f"AI call to {contact.name} ({masked(contact.phone_normalized)}). Purpose: {request.purpose}", registry=call_tool_registry(),
        )
    request = request.model_copy(update={"approval_id": approval.id, "task_id": task.id})
    with open_calls_store(context) as store:
        store.add_request(request)
    service.audit(context, "call.ai.requested", request.id, metadata={"approval_id": approval.id, "payload_hash": approval.payload_hash, "max_seconds": request.max_seconds})
    return _ai_view(request, approval)


@router.get("/ai-calls/{request_id}", response_model=AiCallView)
def get_ai_call(request_id: str, context: TenantContext = Depends(tenant_context)) -> AiCallView:
    require(context, Permission.CALL_READ)
    with open_calls_store(context) as store:
        request = store.get_request(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="AI call request not found in this workspace")
    approvals = _approvals(context, [request.approval_id] if request.approval_id else [])
    return _ai_view(request, approvals.get(request.approval_id or ""))


@router.post("/ai-calls/{request_id}/cancel", response_model=AiCallView)
def cancel_ai_call(request_id: str, context: TenantContext = Depends(tenant_context)) -> AiCallView:
    """Cancel an AI call still waiting for approval (its approval is withdrawn)."""
    require(context, Permission.CALL_LOG)
    from ..dependencies import _unit_of_work
    from ..email_api import _retire

    with action_lock, open_calls_store(context) as store:
        request = store.get_request(request_id, lock=True)
        if request is None:
            raise HTTPException(status_code=404, detail="AI call request not found in this workspace")
        require_edit(context, request.requested_by)
        if request.status != AiRequestStatus.PENDING_APPROVAL:
            raise HTTPException(status_code=409, detail="Only a call still waiting for approval can be cancelled.")
        saved = request.model_copy(update={"status": AiRequestStatus.CANCELLED, "updated_at": utc_now()})
        store.save_request(saved)
    with _unit_of_work(context, require_membership=True) as repository:
        _retire(repository, context, request)  # type: ignore[arg-type]  (task_id and approval_id are all it reads)
    service.audit(context, "call.ai.cancelled", request_id)
    return _ai_view(saved)

