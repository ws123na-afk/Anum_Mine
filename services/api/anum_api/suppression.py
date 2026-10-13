"""Contact suppression (opt-out) list: who ANUM must not message (docs/privacy.md#contact-suppression).

One list per tenant. An entry is a phone number or e-mail address, normalized, and stored
as a keyed SHA-256 hash plus a masked form for people to recognise it (``+971•••4567``,
``a•••@example.com``); the address itself is never stored. Each entry has a channel
(``email``, ``whatsapp``, ``sms`` or ``all``), a purpose (``marketing``: only marketing
sends are refused; ``all``: every business-initiated message), a source, the reason, who
added it and when.

* Inbound WhatsApp messages that are exactly an opt-out keyword (``STOP``,
  ``UNSUBSCRIBE``, ``إيقاف``, ``لا ترسلوا لي رسائل`` and a few variants) add a WhatsApp
  entry for every purpose; ``START`` or ``ابدأ`` removes the entries those keywords added
  (an entry a person added stays until a person removes it). An inbound e-mail whose
  subject is exactly ``unsubscribe`` (or ``stop``, ``إلغاء الاشتراك``) adds an e-mail
  marketing entry.
* Every tool call that sends (any action kind but read and draft) is checked by the
  runtime's tool mediation (``AgentRuntime.evaluate``) and again by the tool registry right
  before the handler runs (``ToolRegistry.execute``): a suppressed recipient blocks the
  call with the reason, and the refusal is audited (``suppression.send_refused``).
* Owners and organization administrators view, search, add and remove entries (a removal
  needs a reason); only owners export the list as CSV.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, require_permission, tenant_context
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store


class SuppressionChannel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    ALL = "all"


class SuppressionPurpose(StrEnum):
    MARKETING = "marketing"
    ALL = "all"


class SuppressionSource(StrEnum):
    MANUAL = "manual"
    WHATSAPP_KEYWORD = "whatsapp_keyword"
    EMAIL_UNSUBSCRIBE = "email_unsubscribe"


KEYWORD_SOURCES = frozenset({SuppressionSource.WHATSAPP_KEYWORD, SuppressionSource.EMAIL_UNSUBSCRIBE})
MAX_LIST = 200
EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
SYSTEM_ACTOR = "system:inbound"


class SuppressionEntry(BaseModel):
    id: str
    tenant_id: str
    recipient_hash: str
    masked: str
    kind: Literal["phone", "email"]
    channel: SuppressionChannel
    purpose: SuppressionPurpose
    source: SuppressionSource
    reason: str = ""
    created_by: str
    created_at: datetime


class SuppressionView(BaseModel):
    """An entry as listed: never the hash or the address."""

    id: str
    masked: str
    kind: Literal["phone", "email"]
    channel: SuppressionChannel
    purpose: SuppressionPurpose
    source: SuppressionSource
    reason: str
    created_by: str
    created_at: datetime


class SuppressionCreate(BaseModel):
    recipient: str = Field(min_length=3, max_length=320)
    channel: SuppressionChannel = SuppressionChannel.ALL
    purpose: SuppressionPurpose = SuppressionPurpose.ALL
    reason: str = Field(min_length=3, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean_reason(value)


class SuppressionRemove(BaseModel):
    reason: str = Field(min_length=3, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean_reason(value)


class SuppressionPage(BaseModel):
    items: list[SuppressionView]
    total: int
    can_manage: bool
    can_export: bool


class SuppressionHit(BaseModel):
    """A recipient of a call that is on the list (masked)."""

    masked: str
    channel: SuppressionChannel
    purpose: SuppressionPurpose
    source: SuppressionSource
    created_at: datetime


def _clean_reason(value: str) -> str:
    text = value.strip()
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in text):
        raise ValueError("the reason contains control characters")
    if len(text) < 3:
        raise ValueError("give a reason of at least 3 characters")
    return text


# -- normalization ---------------------------------------------------------------------


def normalize(value: str) -> tuple[Literal["phone", "email"], str] | None:
    """An e-mail address (lower case) or a phone number (``+`` and digits), or None.

    ``00`` is read as ``+``, and a UAE local mobile number (``05x xxx xxxx``) as ``+9715...``.
    """
    text = value.strip().strip("<>").strip()
    if "@" in text:
        text = text.lower()
        return ("email", text) if EMAIL.match(text) else None
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        digits = digits[2:]
    elif not text.startswith("+") and len(digits) == 10 and digits.startswith("05"):
        digits = "971" + digits[1:]
    if not 8 <= len(digits) <= 15:
        return None
    return "phone", "+" + digits


def recipient_hash(tenant_id: str, normalized: str) -> str:
    """What is stored instead of the address (per tenant, so lists cannot be joined)."""
    material = f"anum-suppression-v1\x1f{tenant_id}\x1f{normalized}".encode()
    return hashlib.sha256(material).hexdigest()


def mask(kind: str, normalized: str) -> str:
    if kind == "email":
        local, _, domain = normalized.partition("@")
        return f"{local[:1]}•••@{domain}"
    return f"{normalized[:4]}•••{normalized[-4:]}"


_ARABIC_MARKS = re.compile(r"[ؐ-ًؚ-ٰٟـ]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_keyword(text: str) -> str:
    folded = _ARABIC_MARKS.sub("", unicodedata.normalize("NFKC", text)).casefold()
    folded = folded.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").replace("ة", "ه")
    return " ".join(_PUNCT.sub(" ", folded).split())


STOP_WORDS = frozenset(
    normalize_keyword(word)
    for word in (
        "STOP", "STOP ALL", "STOPALL", "UNSUBSCRIBE", "OPT OUT", "OPTOUT", "CANCEL", "END", "QUIT",
        "إيقاف", "ايقاف", "توقف", "الغاء الاشتراك", "إلغاء الاشتراك",
        "لا ترسلوا لي رسائل", "لا ترسل لي رسائل", "لا ترسلوا رسائل",
    )
)
START_WORDS = frozenset(normalize_keyword(word) for word in ("START", "UNSTOP", "SUBSCRIBE", "ابدأ", "ابدا", "اشتراك"))
EMAIL_STOP_SUBJECTS = frozenset(normalize_keyword(word) for word in ("unsubscribe", "stop", "إلغاء الاشتراك", "الغاء الاشتراك"))


def keyword_of(text: str) -> Literal["stop", "start"] | None:
    """``stop`` or ``start`` when the whole message is an opt-out or opt-in keyword."""
    word = normalize_keyword(text or "")
    if word in STOP_WORDS:
        return "stop"
    if word in START_WORDS:
        return "start"
    return None


# -- store -----------------------------------------------------------------------------


class SuppressionRepository(Protocol):
    def list(self, context: TenantContext) -> list[SuppressionEntry]: ...
    def find(self, context: TenantContext, hashes: list[str]) -> list[SuppressionEntry]: ...
    def get(self, context: TenantContext, entry_id: str) -> SuppressionEntry | None: ...
    def add(self, context: TenantContext, entry: SuppressionEntry) -> tuple[SuppressionEntry, bool]: ...
    def remove(self, context: TenantContext, entry_id: str) -> bool: ...
    def record_audit(self, record: AuditRecord) -> AuditRecord: ...


class SuppressionStore:
    """In-memory list for ``ANUM_REPOSITORY_BACKEND=memory`` (local and tests)."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, SuppressionEntry]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.entries.clear()

    def list(self, context: TenantContext) -> list[SuppressionEntry]:
        with self._lock:
            return sorted(self.entries.get(context.tenant_id, {}).values(), key=lambda e: e.created_at, reverse=True)

    def find(self, context: TenantContext, hashes: list[str]) -> list[SuppressionEntry]:
        wanted = set(hashes)
        return [entry for entry in self.list(context) if entry.recipient_hash in wanted]

    def get(self, context: TenantContext, entry_id: str) -> SuppressionEntry | None:
        with self._lock:
            return self.entries.get(context.tenant_id, {}).get(entry_id)

    def add(self, context: TenantContext, entry: SuppressionEntry) -> tuple[SuppressionEntry, bool]:
        with self._lock:
            rows = self.entries.setdefault(context.tenant_id, {})
            for existing in rows.values():
                if existing.recipient_hash == entry.recipient_hash and existing.channel == entry.channel:
                    if existing.purpose == SuppressionPurpose.MARKETING and entry.purpose == SuppressionPurpose.ALL:
                        existing.purpose = SuppressionPurpose.ALL
                        return existing, True
                    return existing, False
            rows[entry.id] = entry
            return entry, True

    def remove(self, context: TenantContext, entry_id: str) -> bool:
        with self._lock:
            return self.entries.get(context.tenant_id, {}).pop(entry_id, None) is not None

    def record_audit(self, record: AuditRecord) -> AuditRecord:
        from .dependencies import memory_repository

        with self._lock:
            return memory_repository.record_audit(record)


suppression_store = SuppressionStore()


@contextmanager
def open_suppression_store(context: TenantContext) -> Iterator[SuppressionRepository]:
    """The list for one unit of work, chosen by ``ANUM_REPOSITORY_BACKEND`` (RLS-scoped)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.suppression_repository import SqlAlchemySuppressionStore

        return SqlAlchemySuppressionStore(session)

    with open_scoped_store(context, suppression_store, sql_store) as store:
        yield store


# -- checks (the mediated tool layer) --------------------------------------------------

_RECIPIENT_KEYS = ("to", "recipient", "recipients", "cc", "bcc", "phone", "phone_number", "to_phone", "address", "email")
_MARKETING_WORDS = ("marketing", "campaign", "newsletter", "promo", "broadcast")


def recipients_of(arguments: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(kind, normalized)`` for every recipient-looking argument of a call."""
    found: list[tuple[str, str]] = []
    for key in _RECIPIENT_KEYS:
        value = arguments.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, Mapping):
                item = item.get("email") or item.get("phone") or item.get("address")
            if not isinstance(item, (str, int)) or isinstance(item, bool):
                continue
            for part in str(item).split(",") if "@" in str(item) else [str(item)]:
                normalized = normalize(part)
                if normalized is not None and normalized not in found:
                    found.append(normalized)
    return found


def channel_of(tool: str, definition: Any) -> SuppressionChannel | None:
    """The channel a tool sends on, or None when it cannot be told (every channel applies)."""
    from .autonomy import ToolCategory, category_of

    declared = str(getattr(definition, "delivery_channel", None) or "")
    if declared in {"email", "whatsapp", "sms"}:
        return SuppressionChannel(declared)
    category = category_of(tool, definition)
    if category == ToolCategory.WHATSAPP:
        return SuppressionChannel.WHATSAPP
    if category == ToolCategory.EMAIL:
        return SuppressionChannel.EMAIL
    if "sms" in tool.lower():
        return SuppressionChannel.SMS
    return None


def is_marketing(tool: str, arguments: Mapping[str, Any], definition: Any) -> bool:
    from .autonomy import ToolCategory, category_of

    if category_of(tool, definition) == ToolCategory.MARKETING:
        return True
    if str(arguments.get("purpose") or "").strip().lower() == "marketing" or arguments.get("marketing") is True:
        return True
    lowered = tool.lower()
    return any(word in lowered for word in _MARKETING_WORDS)


def sends(tool: str, definition: Any) -> bool:
    """Whether the tool may deliver something to a person (anything but a read or a draft)."""
    from .autonomy import NO_EFFECT_KINDS, kind_of

    return kind_of(tool, definition) not in NO_EFFECT_KINDS


def _applies(entry: SuppressionEntry, kind: str, channel: SuppressionChannel | None, marketing: bool) -> bool:
    if entry.purpose == SuppressionPurpose.MARKETING and not marketing:
        return False
    if entry.channel == SuppressionChannel.ALL:
        return True
    if channel is not None:
        return entry.channel == channel
    # A tool whose channel is unknown: an e-mail entry for an address, any phone channel for a number.
    return (entry.channel == SuppressionChannel.EMAIL) == (kind == "email")


def suppressed_for_call(
    context: TenantContext, tool: str, arguments: Mapping[str, Any] | None, definition: Any
) -> list[SuppressionHit]:
    """The call's recipients that are on the tenant's list for its channel and purpose.

    Read in the caller's tenant scope. A failure to read the list propagates: a send never
    goes out on a guess.
    """
    arguments = arguments or {}
    if not sends(tool, definition):
        return []
    recipients = recipients_of(arguments)
    if not recipients:
        return []
    hashes = {recipient_hash(context.tenant_id, value): kind for kind, value in recipients}
    with open_suppression_store(context) as store:
        entries = store.find(context, list(hashes))
    channel = channel_of(tool, definition)
    marketing = is_marketing(tool, arguments, definition)
    hits: list[SuppressionHit] = []
    for entry in entries:
        if _applies(entry, hashes[entry.recipient_hash], channel, marketing):
            hits.append(SuppressionHit(masked=entry.masked, channel=entry.channel, purpose=entry.purpose,
                                       source=entry.source, created_at=entry.created_at))
    return hits


def refusal_reason(hits: list[SuppressionHit]) -> str:
    first = hits[0]
    scope = "marketing messages" if first.purpose == SuppressionPurpose.MARKETING else "messages"
    where = "any channel" if first.channel == SuppressionChannel.ALL else first.channel.value
    more = f" (and {len(hits) - 1} more)" if len(hits) > 1 else ""
    return (
        f"{first.masked}{more} opted out of {scope} on {where} "
        f"({first.created_at.date().isoformat()}), so ANUM did not send this. "
        "An owner can review the contact suppression list."
    )


def audit_refusal(context: TenantContext, tool: str, hits: list[SuppressionHit], stage: str) -> None:
    """Record a refused send in the tenant's audit trail (own unit of work)."""
    with open_suppression_store(context) as store:
        store.record_audit(
            AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                actor=context.user_id, action="suppression.send_refused", target=f"tool:{tool}",
                outcome="denied", correlation_id=new_id("correlation"), created_at=utc_now(),
                metadata={"tool": tool, "stage": stage, "recipients": [hit.masked for hit in hits],
                          "channels": sorted({hit.channel.value for hit in hits})},
            )
        )


# -- inbound keywords ------------------------------------------------------------------


def _audit(store: SuppressionRepository, context: TenantContext, action: str, entry: SuppressionEntry,
           extra: dict[str, Any] | None = None, actor: str | None = None) -> None:
    store.record_audit(
        AuditRecord(
            id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor=actor or context.user_id, action=action, target=f"suppression:{entry.id}", outcome="success",
            correlation_id=new_id("correlation"), created_at=utc_now(),
            metadata={"masked": entry.masked, "channel": entry.channel.value, "purpose": entry.purpose.value,
                      "source": entry.source.value, **(extra or {})},
        )
    )


def apply_inbound_keyword(
    context: TenantContext, channel: SuppressionChannel, sender: str, keyword: Literal["stop", "start"], ref: str
) -> bool:
    """Record an opt-out (``stop``) or remove a keyword opt-out (``start``). Returns whether anything changed."""
    normalized = normalize(sender if "@" in sender else ("+" + sender.lstrip("+")))
    if normalized is None:
        return False
    kind, value = normalized
    digest = recipient_hash(context.tenant_id, value)
    source = SuppressionSource.EMAIL_UNSUBSCRIBE if channel == SuppressionChannel.EMAIL else SuppressionSource.WHATSAPP_KEYWORD
    with open_suppression_store(context) as store:
        if keyword == "stop":
            entry = SuppressionEntry(
                id=new_id("supp"), tenant_id=context.tenant_id, recipient_hash=digest, masked=mask(kind, value),
                kind=kind, channel=channel,
                purpose=SuppressionPurpose.MARKETING if channel == SuppressionChannel.EMAIL else SuppressionPurpose.ALL,
                source=source, reason=f"The contact asked to stop ({ref}).", created_by=SYSTEM_ACTOR, created_at=utc_now(),
            )
            saved, changed = store.add(context, entry)
            if changed:
                _audit(store, context, "suppression.added", saved, {"keyword": True}, actor=SYSTEM_ACTOR)
            return changed
        removed = False
        for entry in store.find(context, [digest]):
            if entry.channel == channel and entry.source in KEYWORD_SOURCES and store.remove(context, entry.id):
                _audit(store, context, "suppression.removed", entry, {"keyword": True, "reason": "The contact asked to start again."},
                       actor=SYSTEM_ACTOR)
                removed = True
        return removed


# -- routes ----------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["contact-suppression"])


def _can_manage(context: TenantContext) -> bool:
    return has_permission(context, Permission.SUPPRESSION_MANAGE) or has_permission(context, Permission.ORG_POLICY_MANAGE)


def _require_manage(context: TenantContext) -> None:
    if not _can_manage(context):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace owners and organization administrators can see and change the contact suppression list.",
        )


def view(entry: SuppressionEntry) -> SuppressionView:
    return SuppressionView(**entry.model_dump(exclude={"tenant_id", "recipient_hash"}))


def _matches(entry: SuppressionEntry, query: str | None, hashed: str | None) -> bool:
    if not query:
        return True
    if hashed is not None:
        return entry.recipient_hash == hashed
    needle = query.casefold()
    return any(needle in field.casefold() for field in (entry.masked, entry.reason, entry.source.value, entry.created_by))


@router.get("/contact-suppressions", response_model=SuppressionPage)
def list_suppressions(
    q: str | None = Query(default=None, max_length=320, description="A full phone number or e-mail address, or text"),
    channel: SuppressionChannel | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_LIST),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(tenant_context),
) -> SuppressionPage:
    _require_manage(context)
    query = (q or "").strip() or None
    normalized = normalize(query) if query else None
    hashed = recipient_hash(context.tenant_id, normalized[1]) if normalized else None
    with open_suppression_store(context) as store:
        entries = [e for e in store.list(context) if _matches(e, query, hashed) and (channel is None or e.channel == channel)]
    return SuppressionPage(
        items=[view(entry) for entry in entries[offset : offset + limit]],
        total=len(entries),
        can_manage=True,
        can_export=has_permission(context, Permission.SUPPRESSION_EXPORT),
    )


@router.post("/contact-suppressions", response_model=SuppressionView, status_code=status.HTTP_201_CREATED)
def add_suppression(payload: SuppressionCreate, response: Response, context: TenantContext = Depends(tenant_context)) -> SuppressionView:
    _require_manage(context)
    normalized = normalize(payload.recipient)
    if normalized is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Enter a phone number with its country code (+971...) or an e-mail address.")
    kind, value = normalized
    if kind == "email" and payload.channel in {SuppressionChannel.WHATSAPP, SuppressionChannel.SMS}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="An e-mail address can only be suppressed for e-mail or all channels.")
    if kind == "phone" and payload.channel == SuppressionChannel.EMAIL:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A phone number can be suppressed for WhatsApp, SMS or all channels.")
    entry = SuppressionEntry(
        id=new_id("supp"), tenant_id=context.tenant_id, recipient_hash=recipient_hash(context.tenant_id, value),
        masked=mask(kind, value), kind=kind, channel=payload.channel, purpose=payload.purpose,
        source=SuppressionSource.MANUAL, reason=payload.reason, created_by=context.user_id, created_at=utc_now(),
    )
    with open_suppression_store(context) as store:
        saved, changed = store.add(context, entry)
        if changed:
            _audit(store, context, "suppression.added", saved, {"reason": payload.reason})
    if saved.id != entry.id:  # already on the list (perhaps widened to every purpose)
        response.status_code = status.HTTP_200_OK
    return view(saved)


@router.delete("/contact-suppressions/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_suppression(entry_id: str, payload: SuppressionRemove, context: TenantContext = Depends(tenant_context)) -> Response:
    _require_manage(context)
    with open_suppression_store(context) as store:
        entry = store.get(context, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suppression entry not found")
        store.remove(context, entry_id)
        _audit(store, context, "suppression.removed", entry, {"reason": payload.reason})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/contact-suppressions/export", response_class=Response)
def export_suppressions(context: TenantContext = Depends(tenant_context)) -> Response:
    """CSV of the list (owners only): masked contact, channel, purpose, source, reason, who, when."""
    if not has_permission(context, Permission.SUPPRESSION_EXPORT):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only workspace owners can export the contact suppression list.")
    with open_suppression_store(context) as store:
        entries = store.list(context)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["contact", "kind", "channel", "purpose", "source", "reason", "added_by", "added_at"])
        for entry in entries:
            writer.writerow([_csv_safe(entry.masked), entry.kind, entry.channel.value, entry.purpose.value, entry.source.value,
                             _csv_safe(entry.reason), _csv_safe(entry.created_by), entry.created_at.isoformat()])
        store.record_audit(
            AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id,
                action="suppression.exported", target=f"tenant:{context.tenant_id}", outcome="success",
                correlation_id=new_id("correlation"), created_at=utc_now(), metadata={"rows": len(entries)},
            )
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contact-suppressions.csv"', "Cache-Control": "no-store"},
    )


def _csv_safe(value: str) -> str:
    """Spreadsheet formula injection: a cell never starts with = + - @."""
    return "'" + value if value[:1] in {"=", "+", "-", "@", "\t", "\r"} else value
