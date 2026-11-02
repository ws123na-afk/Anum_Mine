"""Consent records for campaigns (docs/privacy.md#consent-records-for-campaigns).

One record per contact and channel in a tenant: the lawful basis ANUM may message the
contact for marketing on (``consent``, ``contract``, ``legitimate_interest`` or
``existing_customer``), where it came from (a form, an event, an import), when it was given
and, once withdrawn, when, why and by whom. The contact is stored like the contact
suppression list: a per-tenant SHA-256 hash plus a masked form, never the address itself.

Sending checks consent as well as suppression. Every marketing send (a tool of the
marketing category, a call with ``purpose: marketing`` or a campaign-like tool name) is
checked in the runtime's tool mediation and again right before the tool runs:

* a recipient whose record for the call's channel is **withdrawn** (and who has no other
  active record for it) is refused;
* with **Require consent for marketing** on (an organisation setting, off by default), a
  recipient with no active record for the channel is refused.

A refusal blocks the call with the reason and writes ``consent.send_refused``. Nothing
lowers it (no autonomy level, approval or agent setting). Service messages (not marketing)
are never checked here; the suppression list still applies to them.

Owners and organization administrators view, search, record and withdraw consents and
change the setting. Records are evidence, so there is no delete: organisation deletion
purges them, and "withdraw" keeps the history.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .suppression import (
    channel_of,
    is_marketing,
    mask,
    normalize,
    recipient_hash,
    recipients_of,
    sends,
)

MAX_LIST = 200


class ConsentChannel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    ALL = "all"


class LawfulBasis(StrEnum):
    CONSENT = "consent"
    CONTRACT = "contract"
    LEGITIMATE_INTEREST = "legitimate_interest"
    EXISTING_CUSTOMER = "existing_customer"


def _clean(value: str, low: int, high: int, what: str) -> str:
    text = " ".join(value.split())
    if any(ord(ch) < 32 for ch in value if ch not in "\t\n"):
        raise ValueError(f"The {what} has control characters.")
    if not low <= len(text) <= high:
        raise ValueError(f"The {what} must be {low} to {high} characters.")
    return text


class ConsentRecord(BaseModel):
    id: str
    tenant_id: str
    recipient_hash: str
    masked: str
    kind: Literal["phone", "email"]
    channel: ConsentChannel
    lawful_basis: LawfulBasis
    source: str
    consented_at: datetime
    note: str | None = None
    withdrawn_at: datetime | None = None
    withdrawn_reason: str | None = None
    withdrawn_by: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime

    @property
    def active(self) -> bool:
        return self.withdrawn_at is None


class ConsentView(BaseModel):
    id: str
    masked: str
    kind: Literal["phone", "email"]
    channel: ConsentChannel
    lawful_basis: LawfulBasis
    source: str
    consented_at: datetime
    note: str | None = None
    status: Literal["active", "withdrawn"]
    withdrawn_at: datetime | None = None
    withdrawn_reason: str | None = None
    withdrawn_by: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class ConsentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient: str = Field(min_length=3, max_length=320)
    channel: ConsentChannel
    lawful_basis: LawfulBasis
    source: str = Field(min_length=3, max_length=200, description="Where the consent came from: a form, an event, an import")
    consented_at: datetime
    note: str | None = Field(default=None, max_length=300)

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        return _clean(value, 3, 200, "source")

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _clean(value, 1, 300, "note")

    @field_validator("consented_at")
    @classmethod
    def _not_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Give the date with its time zone.")
        if value > utc_now() + timedelta(minutes=5):
            raise ValueError("The consent date cannot be in the future.")
        return value


class ConsentWithdraw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean(value, 3, 300, "reason")


class ConsentSettings(BaseModel):
    require_for_marketing: bool = False
    updated_by: str | None = None
    updated_at: datetime | None = None


class ConsentSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_for_marketing: bool


class ConsentPage(BaseModel):
    items: list[ConsentView]
    total: int
    active: int
    withdrawn: int
    settings: ConsentSettings
    can_manage: bool


def view(record: ConsentRecord) -> ConsentView:
    return ConsentView(
        **record.model_dump(exclude={"tenant_id", "recipient_hash"}),
        status="active" if record.active else "withdrawn",
    )


# -- store -----------------------------------------------------------------------------


class ConsentRepository(Protocol):
    def list(self, context: TenantContext) -> list[ConsentRecord]: ...
    def find(self, context: TenantContext, hashes: list[str]) -> list[ConsentRecord]: ...
    def get(self, context: TenantContext, record_id: str) -> ConsentRecord | None: ...
    def upsert(self, context: TenantContext, record: ConsentRecord) -> tuple[ConsentRecord, bool]: ...
    def withdraw(self, context: TenantContext, record_id: str, reason: str, by: str, at: datetime) -> ConsentRecord | None: ...
    def get_settings(self, context: TenantContext) -> ConsentSettings: ...
    def save_settings(self, context: TenantContext, settings: ConsentSettings) -> ConsentSettings: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


class InMemoryConsentStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests)."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, ConsentRecord]] = {}
        self.settings: dict[str, ConsentSettings] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.records.clear()
            self.settings.clear()

    def list(self, context: TenantContext) -> list[ConsentRecord]:
        with self._lock:
            rows = self.records.get(context.tenant_id, {}).values()
            return sorted((r.model_copy() for r in rows), key=lambda r: (r.updated_at, r.id), reverse=True)

    def find(self, context: TenantContext, hashes: list[str]) -> list[ConsentRecord]:
        wanted = set(hashes)
        return [r for r in self.list(context) if r.recipient_hash in wanted]

    def get(self, context: TenantContext, record_id: str) -> ConsentRecord | None:
        with self._lock:
            found = self.records.get(context.tenant_id, {}).get(record_id)
            return found.model_copy() if found else None

    def upsert(self, context: TenantContext, record: ConsentRecord) -> tuple[ConsentRecord, bool]:
        """Insert, or record again over the existing row for the same contact and channel.
        Returns the saved record and whether it was new."""
        with self._lock:
            rows = self.records.setdefault(context.tenant_id, {})
            for existing in rows.values():
                if existing.recipient_hash == record.recipient_hash and existing.channel == record.channel:
                    updated = record.model_copy(update={"id": existing.id, "created_by": existing.created_by,
                                                        "created_at": existing.created_at})
                    rows[existing.id] = updated
                    return updated.model_copy(), False
            rows[record.id] = record.model_copy()
            return record.model_copy(), True

    def withdraw(self, context: TenantContext, record_id: str, reason: str, by: str, at: datetime) -> ConsentRecord | None:
        with self._lock:
            found = self.records.get(context.tenant_id, {}).get(record_id)
            if found is None or found.withdrawn_at is not None:
                return None
            found.withdrawn_at, found.withdrawn_reason, found.withdrawn_by, found.updated_at = at, reason, by, at
            return found.model_copy()

    def get_settings(self, context: TenantContext) -> ConsentSettings:
        with self._lock:
            return (self.settings.get(context.tenant_id) or ConsentSettings()).model_copy()

    def save_settings(self, context: TenantContext, settings: ConsentSettings) -> ConsentSettings:
        with self._lock:
            self.settings[context.tenant_id] = settings.model_copy()
            return settings

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        from .dependencies import memory_repository

        return memory_repository.record_audit(record)


consent_store = InMemoryConsentStore()


@contextmanager
def open_consent_store(context: TenantContext) -> Iterator[ConsentRepository]:
    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.consent_repository import SqlAlchemyConsentStore

        return SqlAlchemyConsentStore(session)

    with open_scoped_store(context, consent_store, sql_store) as store:
        yield store


# -- the send check ----------------------------------------------------------------------


class ConsentRefusal(BaseModel):
    masked: str
    why: Literal["withdrawn", "missing"]
    at: datetime | None = None


def _applies(record: ConsentRecord, kind: str, channel: Any) -> bool:
    if record.channel == ConsentChannel.ALL:
        return True
    if channel is not None:
        return record.channel.value == channel.value
    return (record.channel == ConsentChannel.EMAIL) == (kind == "email")


def consent_refusals_for_call(
    context: TenantContext, tool: str, arguments: Mapping[str, Any] | None, definition: Any
) -> list[ConsentRefusal]:
    """Recipients of a marketing send that have withdrawn consent, or (with the setting on)
    have none for the call's channel. A failure to read propagates: nothing is sent on a guess."""
    arguments = arguments or {}
    if not sends(tool, definition) or not is_marketing(tool, arguments, definition):
        return []
    recipients = recipients_of(arguments)
    if not recipients:
        return []
    hashes = {recipient_hash(context.tenant_id, value): (kind, value) for kind, value in recipients}
    channel = channel_of(tool, definition)
    with open_consent_store(context) as store:
        records = store.find(context, list(hashes))
        required = store.get_settings(context).require_for_marketing
    refusals: list[ConsentRefusal] = []
    for digest, (kind, value) in hashes.items():
        mine = [r for r in records if r.recipient_hash == digest and _applies(r, kind, channel)]
        if any(r.active for r in mine):
            continue
        withdrawn = [r for r in mine if not r.active]
        if withdrawn:
            refusals.append(ConsentRefusal(masked=withdrawn[0].masked, why="withdrawn", at=withdrawn[0].withdrawn_at))
        elif required:
            refusals.append(ConsentRefusal(masked=mask(kind, value), why="missing"))
    return refusals


def consent_refusal_reason(refusals: list[ConsentRefusal]) -> str:
    first = refusals[0]
    more = f" (and {len(refusals) - 1} more)" if len(refusals) > 1 else ""
    if first.why == "withdrawn":
        when = f" on {first.at.date().isoformat()}" if first.at else ""
        return (f"{first.masked}{more} withdrew consent to marketing{when}, so ANUM did not send this. "
                "An owner can review the consent records under Contact opt-outs.")
    return (f"{first.masked}{more} has no consent record for marketing on this channel, and your organization "
            "requires one, so ANUM did not send this. Record the consent under Contact opt-outs first.")


def audit_consent_refusal(context: TenantContext, tool: str, refusals: list[ConsentRefusal], stage: str) -> None:
    with open_consent_store(context) as store:
        store.record_audit(AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
            action="consent.send_refused", target=f"tool:{tool}", outcome="denied", correlation_id=new_id("correlation"),
            created_at=utc_now(),
            metadata={"tool": tool, "stage": stage, "recipients": [r.masked for r in refusals], "why": sorted({r.why for r in refusals})},
        ))


# -- routes ----------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/contact-consents", tags=["contact-consent"])
MANAGE_DENIED = "Only workspace owners and organization administrators can see and change consent records."


def _can_manage(context: TenantContext) -> bool:
    return has_permission(context, Permission.SUPPRESSION_MANAGE) or has_permission(context, Permission.ORG_POLICY_MANAGE)


def _require_manage(context: TenantContext) -> None:
    if not _can_manage(context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MANAGE_DENIED)


def _audit(store: ConsentRepository, context: TenantContext, action: str, target: str, metadata: dict[str, Any]) -> None:
    store.record_audit(AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
        action=action, target=target, outcome="success", correlation_id=new_id("correlation"), created_at=utc_now(),
        metadata=metadata,
    ))


def _matches(record: ConsentRecord, query: str | None, hashed: str | None) -> bool:
    if not query:
        return True
    if hashed is not None:
        return record.recipient_hash == hashed
    needle = query.casefold()
    return any(needle in field.casefold() for field in (record.masked, record.source, record.note or "", record.lawful_basis.value))


@router.get("", response_model=ConsentPage)
def list_consents(
    q: str | None = Query(default=None, max_length=320, description="A full phone number or e-mail address, or text"),
    channel: ConsentChannel | None = None,
    state: Literal["active", "withdrawn"] | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_LIST),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> ConsentPage:
    _require_manage(context)
    query = (q or "").strip() or None
    normalized = normalize(query) if query else None
    hashed = recipient_hash(context.tenant_id, normalized[1]) if normalized else None
    with open_consent_store(context) as store:
        records = store.list(context)
        settings = store.get_settings(context)
    found = [r for r in records if _matches(r, query, hashed) and (channel is None or r.channel == channel)
             and (state is None or (state == "active") == r.active)]
    return ConsentPage(
        items=[view(r) for r in found[offset: offset + limit]], total=len(found),
        active=sum(1 for r in records if r.active), withdrawn=sum(1 for r in records if not r.active),
        settings=settings, can_manage=True,
    )


@router.post("", response_model=ConsentView, status_code=status.HTTP_201_CREATED)
def record_consent(payload: ConsentCreate, response: Response, context: TenantContext = Depends(tenant_context)) -> ConsentView:
    """Record a consent (201), or record it again over the contact's record for the channel (200)."""
    _require_manage(context)
    normalized = normalize(payload.recipient)
    if normalized is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Enter a phone number with its country code (+971...) or an e-mail address.")
    kind, value = normalized
    if kind == "email" and payload.channel in {ConsentChannel.WHATSAPP, ConsentChannel.SMS}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="An e-mail address can only consent to e-mail or all channels.")
    if kind == "phone" and payload.channel == ConsentChannel.EMAIL:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A phone number can consent to WhatsApp, SMS or all channels.")
    now = utc_now()
    record = ConsentRecord(
        id=new_id("consent"), tenant_id=context.tenant_id, recipient_hash=recipient_hash(context.tenant_id, value),
        masked=mask(kind, value), kind=kind, channel=payload.channel, lawful_basis=payload.lawful_basis, source=payload.source,
        consented_at=payload.consented_at, note=payload.note, created_by=context.user_id, created_at=now, updated_at=now,
    )
    with open_consent_store(context) as store:
        saved, created = store.upsert(context, record)
        _audit(store, context, "consent.recorded", f"consent:{saved.id}", {
            "masked": saved.masked, "channel": saved.channel.value, "lawful_basis": saved.lawful_basis.value,
            "consented_at": saved.consented_at.isoformat(), "renewed": not created,
        })
    if not created:
        response.status_code = status.HTTP_200_OK
    return view(saved)


@router.post("/{record_id}/withdraw", response_model=ConsentView)
def withdraw_consent(record_id: str, payload: ConsentWithdraw, context: TenantContext = Depends(tenant_context)) -> ConsentView:
    _require_manage(context)
    with open_consent_store(context) as store:
        current = store.get(context, record_id)
        if current is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consent record not found")
        if not current.active:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This consent is already withdrawn.")
        saved = store.withdraw(context, record_id, payload.reason, context.user_id, utc_now())
        if saved is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This consent is already withdrawn.")
        _audit(store, context, "consent.withdrawn", f"consent:{saved.id}",
               {"masked": saved.masked, "channel": saved.channel.value, "reason": payload.reason})
    return view(saved)


@router.get("/settings", response_model=ConsentSettings)
def get_consent_settings(context: TenantContext = Depends(tenant_context)) -> ConsentSettings:
    _require_manage(context)
    with open_consent_store(context) as store:
        return store.get_settings(context)


@router.put("/settings", response_model=ConsentSettings)
def set_consent_settings(payload: ConsentSettingsUpdate, context: TenantContext = Depends(tenant_context)) -> ConsentSettings:
    _require_manage(context)
    with open_consent_store(context) as store:
        previous = store.get_settings(context)
        saved = store.save_settings(context, ConsentSettings(require_for_marketing=payload.require_for_marketing,
                                                             updated_by=context.user_id, updated_at=utc_now()))
        _audit(store, context, "consent.settings_updated", f"tenant:{context.tenant_id}",
               {"require_for_marketing": payload.require_for_marketing, "previous": previous.require_for_marketing})
    return saved
