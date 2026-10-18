"""Phone calls storage: contacts, the call log, opt-outs, the provider account and AI call requests.

``ANUM_REPOSITORY_BACKEND=memory`` keeps everything in this process (local and tests);
PostgreSQL uses ``anum_api.db.calls_repository`` (migration ``0071_calls``, forced RLS on
tenant and workspace). A store is opened for one tenant and workspace and only ever sees
that scope. Field names match the table columns, so the SQL store maps rows directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from ..schemas import TenantContext
from ..scoped_store import open_scoped_store


class Direction(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class CallKind(StrEnum):
    # The phone's own dialer (a tel: link); logged by the person afterwards.
    PHONE = "phone"
    # Placed in ANUM through the workspace's voice provider.
    PROVIDER = "provider"
    AI_OUTBOUND = "ai_outbound"
    AI_INBOUND = "ai_inbound"
    # ANUM to ANUM (written by the in-app calling module, claude/calls-webrtc).
    IN_APP = "in_app"


class CallStatus(StrEnum):
    LOGGED = "logged"
    DIALING = "dialing"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


LIVE = {CallStatus.DIALING, CallStatus.RINGING, CallStatus.IN_PROGRESS}


class Outcome(StrEnum):
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    VOICEMAIL = "voicemail"
    MISSED = "missed"
    CANCELLED = "cancelled"


class ConsentMethod(StrEnum):
    VERBAL = "verbal"
    WRITTEN = "written"
    WEB_FORM = "web_form"
    OTHER = "other"


class AiConsent(BaseModel):
    granted_at: datetime
    recorded_by: str
    method: ConsentMethod
    note: str | None = None


class CallContact(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    workspace_id: str
    created_by: str
    name: str
    phone: str
    phone_normalized: str
    company: str | None = None
    email: str | None = None
    notes: str | None = None
    favourite: bool = False
    source: str = "manual"
    language: str = "auto"
    ai_consent: AiConsent | None = None
    created_at: datetime
    updated_at: datetime


class FollowUp(BaseModel):
    text: str
    due_date: date | None = None
    task_id: str | None = None
    edited: bool = False


class SummaryProvenance(BaseModel):
    label: str = "ai_generated"
    source: str
    provider: str
    model: str
    generated_at: datetime
    input_chars: int = 0
    truncated: bool = False


class RecordingConsent(BaseModel):
    # The person who confirmed that the other side was told, and when; the notice ANUM spoke.
    confirmed_by: str
    confirmed_at: datetime
    notice: str
    transcription: bool = False


class CallEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    contact_id: str | None = None
    contact_name: str | None = None
    phone: str | None = None
    phone_normalized: str | None = None
    peer_user_id: str | None = None
    direction: Direction
    kind: CallKind
    status: CallStatus
    outcome: Outcome | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    notes: str | None = None
    summary: str | None = None
    follow_up: FollowUp | None = None
    summary_provenance: SummaryProvenance | None = None
    provider: str | None = None
    provider_call_id: str | None = None
    recording_file_id: str | None = None
    recording_consent: RecordingConsent | None = None
    recording_expires_at: datetime | None = None
    transcript: str | None = None
    ai_request_id: str | None = None
    cost_cents: int | None = None
    created_at: datetime
    updated_at: datetime


class OptOutSource(StrEnum):
    KEYPAD = "keypad"
    SPEECH = "speech"
    OWNER = "owner"
    INBOUND = "inbound"


class OptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    workspace_id: str
    phone_hash: str
    masked: str
    source: OptOutSource
    note: str | None = None
    created_by: str
    created_at: datetime


class ProviderAccount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: str
    workspace_id: str
    provider: str = "twilio"
    route_id: str
    account_sid: str
    api_key_sid: str | None = None
    twiml_app_sid: str | None = None
    from_number: str
    credentials_ciphertext: str
    credential_hint: str
    recording_enabled: bool = False
    transcription_enabled: bool = False
    recording_retention_days: int = 30
    ai_calls_enabled: bool = False
    inbound_ai_enabled: bool = False
    ai_max_seconds: int = 180
    ai_max_cost_cents: int = 100
    cost_per_minute_cents: int = 5
    business_name: str
    greeting: str | None = None
    last_check_status: str | None = None
    last_check_detail: str | None = None
    last_checked_at: datetime | None = None
    updated_by: str
    created_at: datetime
    updated_at: datetime


class AiRequestStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    CALLING = "calling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AiCallRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    workspace_id: str
    requested_by: str
    contact_id: str
    contact_name: str
    phone_normalized: str
    purpose: str
    script: str
    language: str = "en"
    max_seconds: int
    max_cost_cents: int
    status: AiRequestStatus
    approval_id: str | None = None
    task_id: str | None = None
    call_id: str | None = None
    error: str | None = None
    decided_by: str | None = None
    created_at: datetime
    updated_at: datetime


class CallFilter(BaseModel):
    query: str | None = None
    direction: Direction | None = None
    outcome: Outcome | None = None
    kind: CallKind | None = None
    contact_id: str | None = None
    user_id: str | None = None


class CallsStore(Protocol):
    def add_contact(self, contact: CallContact) -> CallContact: ...
    def get_contact(self, contact_id: str) -> CallContact | None: ...
    def save_contact(self, contact: CallContact) -> CallContact: ...
    def delete_contact(self, contact_id: str) -> bool: ...
    def list_contacts(self, *, query: str | None, favourites: bool, offset: int, limit: int) -> tuple[list[CallContact], int]: ...
    def contacts_by_number(self, number: str) -> list[CallContact]: ...

    def add_call(self, call: CallEntry) -> CallEntry: ...
    def get_call(self, call_id: str, *, lock: bool = False) -> CallEntry | None: ...
    def save_call(self, call: CallEntry) -> CallEntry: ...
    def delete_call(self, call_id: str) -> bool: ...
    def list_calls(self, flt: CallFilter, *, offset: int, limit: int) -> tuple[list[CallEntry], int]: ...
    def call_by_provider_id(self, provider_call_id: str) -> CallEntry | None: ...

    def add_opt_out(self, opt_out: OptOut) -> OptOut: ...
    def find_opt_out(self, phone_hash: str) -> OptOut | None: ...
    def list_opt_outs(self, *, offset: int, limit: int) -> tuple[list[OptOut], int]: ...
    def delete_opt_out(self, opt_out_id: str) -> OptOut | None: ...

    def get_provider(self) -> ProviderAccount | None: ...
    def save_provider(self, account: ProviderAccount) -> ProviderAccount: ...
    def delete_provider(self) -> bool: ...

    def add_request(self, request: AiCallRequest) -> AiCallRequest: ...
    def get_request(self, request_id: str, *, lock: bool = False) -> AiCallRequest | None: ...
    def save_request(self, request: AiCallRequest) -> AiCallRequest: ...
    def list_requests(self, *, offset: int, limit: int) -> tuple[list[AiCallRequest], int]: ...


def _has(text: str | None, needle: str) -> bool:
    return bool(text) and needle in text.casefold()  # type: ignore[union-attr]


def contact_matches(contact: CallContact, needle: str) -> bool:
    return any(_has(value, needle) for value in (contact.name, contact.phone, contact.phone_normalized, contact.company, contact.email))


def call_matches(call: CallEntry, needle: str) -> bool:
    return any(_has(value, needle) for value in (call.contact_name, call.phone, call.phone_normalized, call.notes, call.summary))


class MemoryCallsData:
    """Every workspace's rows for the memory backend; lost on restart."""

    def __init__(self) -> None:
        self.contacts: dict[str, CallContact] = {}
        self.calls: dict[str, CallEntry] = {}
        self.opt_outs: dict[str, OptOut] = {}
        self.providers: dict[tuple[str, str], ProviderAccount] = {}
        self.requests: dict[str, AiCallRequest] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.contacts.clear()
            self.calls.clear()
            self.opt_outs.clear()
            self.providers.clear()
            self.requests.clear()

    def route(self, route_id: str) -> tuple[str, str] | None:
        with self.lock:
            for account in self.providers.values():
                if account.route_id == route_id:
                    return account.tenant_id, account.workspace_id
        return None


class MemoryCallsStore:
    def __init__(self, data: MemoryCallsData, context: TenantContext) -> None:
        self.data = data
        self.scope = (context.tenant_id, context.workspace_id)

    def _mine(self, row: Any) -> bool:
        return (row.tenant_id, row.workspace_id) == self.scope

    # -- contacts
    def add_contact(self, contact: CallContact) -> CallContact:
        with self.data.lock:
            self.data.contacts[contact.id] = contact.model_copy(deep=True)
        return contact

    def get_contact(self, contact_id: str) -> CallContact | None:
        with self.data.lock:
            found = self.data.contacts.get(contact_id)
            return found.model_copy(deep=True) if found is not None and self._mine(found) else None

    def save_contact(self, contact: CallContact) -> CallContact:
        with self.data.lock:
            if self.get_contact(contact.id) is not None:
                self.data.contacts[contact.id] = contact.model_copy(deep=True)
        return contact

    def delete_contact(self, contact_id: str) -> bool:
        with self.data.lock:
            if self.get_contact(contact_id) is None:
                return False
            del self.data.contacts[contact_id]
            return True

    def list_contacts(self, *, query: str | None, favourites: bool, offset: int, limit: int) -> tuple[list[CallContact], int]:
        needle = query.casefold() if query else None
        with self.data.lock:
            found = [
                item for item in self.data.contacts.values()
                if self._mine(item) and (not favourites or item.favourite) and (needle is None or contact_matches(item, needle))
            ]
        found.sort(key=lambda item: (not item.favourite, item.name.casefold(), item.id))
        return [item.model_copy(deep=True) for item in found[offset : offset + limit]], len(found)

    def contacts_by_number(self, number: str) -> list[CallContact]:
        with self.data.lock:
            return [item.model_copy(deep=True) for item in self.data.contacts.values() if self._mine(item) and item.phone_normalized == number]

    # -- call log
    def add_call(self, call: CallEntry) -> CallEntry:
        with self.data.lock:
            if call.provider_call_id and self.call_by_provider_id(call.provider_call_id) is not None:
                raise ValueError("duplicate provider call")
            self.data.calls[call.id] = call.model_copy(deep=True)
        return call

    def get_call(self, call_id: str, *, lock: bool = False) -> CallEntry | None:
        with self.data.lock:
            found = self.data.calls.get(call_id)
            return found.model_copy(deep=True) if found is not None and self._mine(found) else None

    def save_call(self, call: CallEntry) -> CallEntry:
        with self.data.lock:
            if self.get_call(call.id) is not None:
                self.data.calls[call.id] = call.model_copy(deep=True)
        return call

    def delete_call(self, call_id: str) -> bool:
        with self.data.lock:
            if self.get_call(call_id) is None:
                return False
            del self.data.calls[call_id]
            return True

    def list_calls(self, flt: CallFilter, *, offset: int, limit: int) -> tuple[list[CallEntry], int]:
        needle = flt.query.casefold() if flt.query else None
        with self.data.lock:
            found = [
                item for item in self.data.calls.values()
                if self._mine(item)
                and (flt.direction is None or item.direction == flt.direction)
                and (flt.outcome is None or item.outcome == flt.outcome)
                and (flt.kind is None or item.kind == flt.kind)
                and (flt.contact_id is None or item.contact_id == flt.contact_id)
                and (flt.user_id is None or item.user_id == flt.user_id)
                and (needle is None or call_matches(item, needle))
            ]
        found.sort(key=lambda item: (item.started_at, item.id), reverse=True)
        return [item.model_copy(deep=True) for item in found[offset : offset + limit]], len(found)

    def call_by_provider_id(self, provider_call_id: str) -> CallEntry | None:
        with self.data.lock:
            for item in self.data.calls.values():
                if self._mine(item) and item.provider_call_id == provider_call_id:
                    return item.model_copy(deep=True)
        return None

    # -- opt-outs
    def add_opt_out(self, opt_out: OptOut) -> OptOut:
        with self.data.lock:
            existing = self.find_opt_out(opt_out.phone_hash)
            if existing is not None:
                return existing
            self.data.opt_outs[opt_out.id] = opt_out.model_copy(deep=True)
        return opt_out

    def find_opt_out(self, phone_hash: str) -> OptOut | None:
        with self.data.lock:
            for item in self.data.opt_outs.values():
                if self._mine(item) and item.phone_hash == phone_hash:
                    return item.model_copy(deep=True)
        return None

    def list_opt_outs(self, *, offset: int, limit: int) -> tuple[list[OptOut], int]:
        with self.data.lock:
            found = [item for item in self.data.opt_outs.values() if self._mine(item)]
        found.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return [item.model_copy(deep=True) for item in found[offset : offset + limit]], len(found)

    def delete_opt_out(self, opt_out_id: str) -> OptOut | None:
        with self.data.lock:
            found = self.data.opt_outs.get(opt_out_id)
            if found is None or not self._mine(found):
                return None
            del self.data.opt_outs[opt_out_id]
            return found

    # -- provider account
    def get_provider(self) -> ProviderAccount | None:
        with self.data.lock:
            found = self.data.providers.get(self.scope)
            return found.model_copy(deep=True) if found is not None else None

    def save_provider(self, account: ProviderAccount) -> ProviderAccount:
        with self.data.lock:
            self.data.providers[self.scope] = account.model_copy(deep=True)
        return account

    def delete_provider(self) -> bool:
        with self.data.lock:
            return self.data.providers.pop(self.scope, None) is not None

    # -- AI call requests
    def add_request(self, request: AiCallRequest) -> AiCallRequest:
        with self.data.lock:
            self.data.requests[request.id] = request.model_copy(deep=True)
        return request

    def get_request(self, request_id: str, *, lock: bool = False) -> AiCallRequest | None:
        with self.data.lock:
            found = self.data.requests.get(request_id)
            return found.model_copy(deep=True) if found is not None and self._mine(found) else None

    def save_request(self, request: AiCallRequest) -> AiCallRequest:
        with self.data.lock:
            if self.get_request(request.id) is not None:
                self.data.requests[request.id] = request.model_copy(deep=True)
        return request

    def list_requests(self, *, offset: int, limit: int) -> tuple[list[AiCallRequest], int]:
        with self.data.lock:
            found = [item for item in self.data.requests.values() if self._mine(item)]
        found.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return [item.model_copy(deep=True) for item in found[offset : offset + limit]], len(found)


memory_data = MemoryCallsData()
# Serializes state changes of one AI request or provider call in this process (the memory
# backend); PostgreSQL also locks the row (SELECT ... FOR UPDATE).
action_lock = RLock()


@contextmanager
def open_calls_store(context: TenantContext) -> Iterator[CallsStore]:
    def sql_store(session):  # type: ignore[no-untyped-def]
        from ..db.calls_repository import SqlAlchemyCallsStore

        return SqlAlchemyCallsStore(session, context)

    with open_scoped_store(context, MemoryCallsStore(memory_data, context), sql_store) as store:
        yield store


def route_workspace(route_id: str) -> tuple[str, str] | None:
    """Which tenant and workspace a webhook route belongs to.

    PostgreSQL: one discovery query as ``anum_maintenance``, which may read only the scope
    and route of ``call_provider_accounts`` (migration 0071).
    """
    from ..settings import settings

    if settings.repository_backend == "memory":
        return memory_data.route(route_id)
    from ..db.calls_repository import discover_route

    return discover_route(route_id)


# The fields a person may see of an account; never the ciphertext.
PROVIDER_PRIVATE = {"credentials_ciphertext"}
MAX_NOTES = 8000
MAX_TRANSCRIPT = 60_000
DEFAULT_PAGE = 25
MAX_PAGE = 100
