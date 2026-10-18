"""In-app voice and video calls between teammates (docs/in-app-calls.md).

Media flows peer to peer (WebRTC); the API only rings, signals and keeps the record.

Who may call whom. Both people must be active members of the request's workspace, so of
the same tenant: the callee is looked up among the workspace's active memberships inside
the caller's RLS context, and an id from another tenant or workspace answers 404 exactly
like an unknown id. With PostgreSQL ``app_calls`` and ``app_call_signals`` are under forced
RLS on the tenant and the workspace, plus restrictive policies that admit a row only to
its two participants (``anum.user_id``) and let only the caller insert a call and only the
sender insert a signal (migration ``0071_app_calls``).

Lifecycle. ``POST /calls`` rings (status ``ringing``; ``app_call.ringing`` is the phone push
trigger). The callee's device that answers (``POST /calls/{id}/accept`` with its
``client_id``) is bound to the call; other devices stop ringing. Then the caller sends the
offer, the callee the answer and both their ICE candidates (``POST /calls/{id}/signals``),
each addressed to the other side's bound client. ``POST /calls/{id}/end`` ends it; a ring
that nobody answers within ``ANUM_CALLS_RING_TIMEOUT_SECONDS`` becomes ``missed`` and a call
whose parties stop sending heartbeats for ``HEARTBEAT_TIMEOUT`` seconds becomes
``dropped``, both settled lazily by the next read of either participant. Missed, cancelled
and busy rings leave the callee an in-app notification (kind ``missed_call``).

Signaling stream. ``GET /calls/stream?client_id=`` is a per-device SSE stream of the
caller's call states and the signals addressed to that device. Like the workspace event
stream it never reads through the request's session (CLAUDE.md, "Streaming responses
outlive request dependencies"): every poll opens its own short session in a worker thread.

ICE servers. ``GET /calls/ice-servers`` returns the STUN URLs and, in ``turn`` mode,
short-lived TURN credentials in coturn's REST scheme (``use-auth-secret``): the username
is ``<expiry unix time>:<opaque id>`` and the credential the base64 HMAC-SHA1 of it under
``ANUM_CALLS_TURN_SECRET``. The opaque id is a digest, so coturn's logs name no user.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .events import CanonicalEventName, create_event
from .schemas import TenantContext, new_id, utc_now
from .settings import settings
from .sse import SSE_HEADERS

logger = logging.getLogger("anum.app_calls")

router = APIRouter(prefix="/api/v1", tags=["app-calls"])

CLIENT_ID_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"
# Either party sends a heartbeat every 15 s while connected; after this long without one
# the call is settled as dropped.
HEARTBEAT_TIMEOUT = timedelta(seconds=60)
# Ended calls stay in the stream's snapshot this long, so both sides see the end.
ENDED_VISIBLE = timedelta(seconds=120)
MAX_SIGNALS_PER_SENDER = 400
MAX_SDP_CHARS = 16_000
MAX_CANDIDATE_CHARS = 1_000
MAX_CANDIDATES = 20
KEEPALIVE_SECONDS = 15.0


class CallMedia(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"


class CallStatus(StrEnum):
    RINGING = "ringing"
    ACCEPTED = "accepted"
    ENDED = "ended"


class CallOutcome(StrEnum):
    COMPLETED = "completed"
    DECLINED = "declined"
    MISSED = "missed"
    CANCELLED = "cancelled"
    BUSY = "busy"
    FAILED = "failed"
    DROPPED = "dropped"


# Outcomes the callee sees as a missed call.
MISSED_OUTCOMES = frozenset({CallOutcome.MISSED, CallOutcome.CANCELLED, CallOutcome.BUSY})


class SignalKind(StrEnum):
    OFFER = "offer"
    ANSWER = "answer"
    ICE = "ice"


class CallMode(StrEnum):
    OFF = "off"
    STUN_ONLY = "stun_only"
    TURN = "turn"


class AppCall(BaseModel):
    """One call as stored. Client ids address signals and are never shown to the other side."""

    id: str
    caller_user_id: str
    callee_user_id: str
    caller_name: str
    callee_name: str
    caller_client_id: str
    callee_client_id: str | None = None
    media: CallMedia
    status: CallStatus
    outcome: CallOutcome | None = None
    ended_by_user_id: str | None = None
    created_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    heartbeat_at: datetime | None = None
    duration_seconds: int | None = None

    def involves(self, user_id: str) -> bool:
        return user_id in (self.caller_user_id, self.callee_user_id)


class CallSignal(BaseModel):
    id: str
    call_id: str
    sender_user_id: str
    recipient_user_id: str
    recipient_client_id: str
    kind: SignalKind
    payload: dict[str, Any]
    created_at: datetime


class AppCallView(BaseModel):
    """A call as one participant sees it."""

    id: str
    direction: Literal["outgoing", "incoming"]
    peer_user_id: str
    peer_name: str
    media: CallMedia
    status: CallStatus
    outcome: CallOutcome | None
    created_at: datetime
    answered_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    ring_expires_at: datetime
    # Stream only: whether the device reading the stream is the one bound to this call
    # (the caller's device, or the callee's device that answered).
    on_this_device: bool | None = None
    answered_elsewhere: bool = False


class AppCallPage(BaseModel):
    items: list[AppCallView]
    next_cursor: str | None = None


class CallConfig(BaseModel):
    enabled: bool
    mode: CallMode
    reason: str | None = Field(default=None, description="Why calls are off, or what limits them")
    video_enabled: bool
    ring_timeout_seconds: int


class IceServer(BaseModel):
    urls: list[str]
    username: str | None = None
    credential: str | None = None


class IceServers(BaseModel):
    mode: CallMode
    ice_servers: list[IceServer]
    expires_at: datetime | None
    ttl_seconds: int | None


class PlaceCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callee_user_id: str = Field(min_length=1, max_length=160)
    media: CallMedia = CallMedia.AUDIO
    client_id: str = Field(pattern=CLIENT_ID_PATTERN)


class AcceptCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(pattern=CLIENT_ID_PATTERN)


class EndCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # hangup: the normal end (a decline while ringing for the callee, a cancel for the
    # caller); decline: the callee refuses; failed: the device could not connect or had no
    # microphone; busy: the callee's device is already in another call.
    reason: Literal["hangup", "decline", "failed", "busy"] = "hangup"


class IceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: str = Field(max_length=MAX_CANDIDATE_CHARS)
    sdpMid: str | None = Field(default=None, max_length=64)  # noqa: N815 - WebRTC's own name
    sdpMLineIndex: int | None = Field(default=None, ge=0, le=64)  # noqa: N815


class SignalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SignalKind
    sdp: str | None = Field(default=None, max_length=MAX_SDP_CHARS)
    candidates: list[IceCandidate] | None = Field(default=None, max_length=MAX_CANDIDATES)

    @field_validator("sdp")
    @classmethod
    def _sdp_shape(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("v=0"):
            raise ValueError("sdp must be a session description (starts with v=0)")
        return value

    def payload(self) -> dict[str, Any]:
        if self.kind in (SignalKind.OFFER, SignalKind.ANSWER):
            if not self.sdp or self.candidates is not None:
                raise HTTPException(status_code=422, detail="An offer or answer carries an sdp and no candidates")
            return {"type": self.kind.value, "sdp": self.sdp}
        if not self.candidates or self.sdp is not None:
            raise HTTPException(status_code=422, detail="An ice signal carries candidates and no sdp")
        return {"candidates": [candidate.model_dump() for candidate in self.candidates]}


# ----------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class _Mode:
    mode: CallMode
    reason: str | None
    turn_urls: tuple[str, ...] = ()
    stun_urls: tuple[str, ...] = ()


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _host_of(url: str) -> str:
    rest = url.split(":", 1)[1]
    rest = rest.split("?", 1)[0]
    if rest.startswith("["):
        return rest[: rest.index("]") + 1]
    return rest.rsplit(":", 1)[0] if rest.count(":") == 1 else rest


def call_mode() -> _Mode:
    """The server's call mode from the settings; never raises."""
    turn = _split(settings.calls_turn_urls)
    stun = _split(settings.calls_stun_urls)
    if any(not url.startswith(("stun:", "stuns:")) for url in stun):
        return _Mode(CallMode.OFF, "ANUM_CALLS_STUN_URLS must list stun: or stuns: URLs")
    if turn:
        if any(not url.startswith(("turn:", "turns:")) for url in turn):
            return _Mode(CallMode.OFF, "ANUM_CALLS_TURN_URLS must list turn: or turns: URLs")
        secret = settings.calls_turn_secret.get_secret_value().strip() if settings.calls_turn_secret else ""
        if len(secret) < 32:
            return _Mode(CallMode.OFF, "ANUM_CALLS_TURN_SECRET must be set (32 or more characters) with ANUM_CALLS_TURN_URLS")
        if not stun:
            hosts: list[str] = []
            for url in turn:
                host = _host_of(url)
                if host and host not in hosts:
                    hosts.append(host)
            stun = tuple(f"stun:{host}:3478" for host in hosts)
        return _Mode(CallMode.TURN, None, turn, stun)
    if settings.calls_allow_stun_only:
        return _Mode(
            CallMode.STUN_ONLY,
            "No relay (TURN) server is configured: calls connect on many networks but can fail behind strict firewalls",
            (),
            stun,
        )
    return _Mode(CallMode.OFF, "Calls are off on this server until a relay (TURN) server is configured")


def turn_credentials(context: TenantContext, *, now: datetime | None = None, ttl: int | None = None) -> tuple[str, str, datetime]:
    """coturn REST credentials for one user, valid for ``ttl`` seconds."""
    secret = settings.calls_turn_secret.get_secret_value().strip() if settings.calls_turn_secret else ""
    if not secret:
        raise RuntimeError("ANUM_CALLS_TURN_SECRET is not set")
    issued = now or utc_now()
    lifetime = ttl or settings.calls_turn_ttl_seconds
    expires = int(issued.timestamp()) + lifetime
    opaque = hashlib.sha256(f"{context.tenant_id}\x1f{context.user_id}".encode()).hexdigest()[:24]
    username = f"{expires}:{opaque}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode(), datetime.fromtimestamp(expires, tz=issued.tzinfo)


def turn_credential_valid(username: str, credential: str, *, now: datetime | None = None) -> bool:
    """What coturn checks: the HMAC matches and the expiry in the username is in the future."""
    secret = settings.calls_turn_secret.get_secret_value().strip() if settings.calls_turn_secret else ""
    expiry, _, _ = username.partition(":")
    if not secret or not expiry.isdigit():
        return False
    expected = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
    current = (now or utc_now()).timestamp()
    return hmac.compare_digest(expected, credential) and int(expiry) > current


def _require_enabled() -> _Mode:
    mode = call_mode()
    if mode.mode == CallMode.OFF:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=mode.reason or "Calls are off on this server")
    return mode


# ----------------------------------------------------------------------------- stores


class CallStore(Protocol):
    def insert(self, context: TenantContext, call: AppCall) -> AppCall: ...
    def get(self, context: TenantContext, call_id: str, *, for_update: bool = False) -> AppCall | None: ...
    def save(self, context: TenantContext, call: AppCall) -> AppCall: ...
    def page(
        self, context: TenantContext, *, limit: int, before: tuple[datetime, str] | None, missed_only: bool
    ) -> list[AppCall]: ...
    def live(self, context: TenantContext) -> list[AppCall]: ...
    def recent(self, context: TenantContext, since: datetime) -> list[AppCall]: ...
    def add_signal(self, context: TenantContext, signal: CallSignal) -> CallSignal: ...
    def count_sent(self, context: TenantContext, call_id: str) -> int: ...
    def signals_for(self, context: TenantContext, client_id: str, call_ids: list[str]) -> list[CallSignal]: ...
    def delete_signals(self, context: TenantContext, call_id: str) -> int: ...
    def purge(self, context: TenantContext, before: datetime) -> int: ...


class InMemoryCallStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests). Filters like the row policies."""

    def __init__(self) -> None:
        self._calls: dict[str, tuple[str, str, AppCall]] = {}
        self._signals: dict[str, tuple[str, str, CallSignal]] = {}
        self.lock = threading.RLock()

    def _scoped(self, context: TenantContext) -> list[AppCall]:
        return [
            call
            for tenant, workspace, call in self._calls.values()
            if tenant == context.tenant_id and workspace == context.workspace_id and call.involves(context.user_id)
        ]

    def insert(self, context: TenantContext, call: AppCall) -> AppCall:
        if call.caller_user_id != context.user_id:
            raise PermissionError("only the caller inserts a call")
        with self.lock:
            self._calls[call.id] = (context.tenant_id, context.workspace_id, call.model_copy())
        return call

    def get(self, context: TenantContext, call_id: str, *, for_update: bool = False) -> AppCall | None:
        with self.lock:
            entry = self._calls.get(call_id)
            if entry is None:
                return None
            tenant, workspace, call = entry
            if tenant != context.tenant_id or workspace != context.workspace_id or not call.involves(context.user_id):
                return None
            return call.model_copy()

    def save(self, context: TenantContext, call: AppCall) -> AppCall:
        with self.lock:
            if self.get(context, call.id) is None:
                raise LookupError(call.id)
            self._calls[call.id] = (context.tenant_id, context.workspace_id, call.model_copy())
        return call

    def page(
        self, context: TenantContext, *, limit: int, before: tuple[datetime, str] | None, missed_only: bool
    ) -> list[AppCall]:
        with self.lock:
            calls = self._scoped(context)
        if missed_only:
            calls = [c for c in calls if c.callee_user_id == context.user_id and c.outcome in MISSED_OUTCOMES]
        calls.sort(key=lambda c: (c.created_at, c.id), reverse=True)
        if before is not None:
            calls = [c for c in calls if (c.created_at, c.id) < before]
        return [c.model_copy() for c in calls[:limit]]

    def live(self, context: TenantContext) -> list[AppCall]:
        with self.lock:
            return [c.model_copy() for c in self._scoped(context) if c.status != CallStatus.ENDED]

    def recent(self, context: TenantContext, since: datetime) -> list[AppCall]:
        with self.lock:
            return sorted(
                (
                    c.model_copy()
                    for c in self._scoped(context)
                    if c.status != CallStatus.ENDED or (c.ended_at is not None and c.ended_at >= since)
                ),
                key=lambda c: (c.created_at, c.id),
            )

    def add_signal(self, context: TenantContext, signal: CallSignal) -> CallSignal:
        if signal.sender_user_id != context.user_id:
            raise PermissionError("only the sender inserts a signal")
        with self.lock:
            self._signals[signal.id] = (context.tenant_id, context.workspace_id, signal.model_copy())
        return signal

    def _visible_signals(self, context: TenantContext) -> list[CallSignal]:
        return [
            s
            for tenant, workspace, s in self._signals.values()
            if tenant == context.tenant_id
            and workspace == context.workspace_id
            and context.user_id in (s.sender_user_id, s.recipient_user_id)
        ]

    def count_sent(self, context: TenantContext, call_id: str) -> int:
        with self.lock:
            return sum(1 for s in self._visible_signals(context) if s.call_id == call_id and s.sender_user_id == context.user_id)

    def signals_for(self, context: TenantContext, client_id: str, call_ids: list[str]) -> list[CallSignal]:
        wanted = set(call_ids)
        with self.lock:
            found = [
                s
                for s in self._visible_signals(context)
                if s.call_id in wanted and s.recipient_user_id == context.user_id and s.recipient_client_id == client_id
            ]
        return sorted((s.model_copy() for s in found), key=lambda s: (s.created_at, s.id))

    def delete_signals(self, context: TenantContext, call_id: str) -> int:
        with self.lock:
            doomed = [s.id for s in self._visible_signals(context) if s.call_id == call_id]
            for signal_id in doomed:
                self._signals.pop(signal_id, None)
        return len(doomed)

    def purge(self, context: TenantContext, before: datetime) -> int:
        with self.lock:
            doomed = [c.id for c in self._scoped(context) if c.created_at < before and c.status == CallStatus.ENDED]
            for call_id in doomed:
                self._calls.pop(call_id, None)
                for signal_id in [k for k, (_, _, s) in self._signals.items() if s.call_id == call_id]:
                    self._signals.pop(signal_id, None)
        return len(doomed)

    def clear(self) -> None:
        with self.lock:
            self._calls.clear()
            self._signals.clear()


memory_call_store = InMemoryCallStore()


def _sql_store(session: Any) -> CallStore:
    from .db.app_calls_repository import SqlAlchemyCallStore

    return SqlAlchemyCallStore(session)


@contextmanager
def open_call_store(context: TenantContext) -> Iterator[CallStore]:
    """A short unit of work in the participant's RLS context (``anum.user_id`` set)."""
    from .scoped_store import open_scoped_store

    with open_scoped_store(context, memory_call_store, _sql_store, user_scoped=True) as store:
        yield store


@dataclass
class CallUnit:
    """One transaction: memberships, audit, events and notifications (team unit) and calls."""

    context: TenantContext
    team: Any
    calls: CallStore
    pending_email: list[tuple[str, str]] = field(default_factory=list)


@contextmanager
def open_call_unit(context: TenantContext) -> Iterator[CallUnit]:
    from .team_work import open_team_unit

    with open_team_unit(context, require_membership=True) as team:
        session = team.session
        calls: CallStore = _sql_store(session) if session is not None else memory_call_store
        if session is None:
            with memory_call_store.lock:
                yield CallUnit(context, team, calls)
        else:
            yield CallUnit(context, team, calls)


# ----------------------------------------------------------------------------- views


def ring_expires_at(call: AppCall) -> datetime:
    return call.created_at + timedelta(seconds=settings.calls_ring_timeout_seconds)


def view_of(call: AppCall, context: TenantContext, client_id: str | None = None) -> AppCallView:
    outgoing = call.caller_user_id == context.user_id
    bound = call.caller_client_id if outgoing else call.callee_client_id
    return AppCallView(
        id=call.id,
        direction="outgoing" if outgoing else "incoming",
        peer_user_id=call.callee_user_id if outgoing else call.caller_user_id,
        peer_name=call.callee_name if outgoing else call.caller_name,
        media=call.media,
        status=call.status,
        outcome=call.outcome,
        created_at=call.created_at,
        answered_at=call.answered_at,
        ended_at=call.ended_at,
        duration_seconds=call.duration_seconds,
        ring_expires_at=ring_expires_at(call),
        on_this_device=None if client_id is None else bound == client_id,
        answered_elsewhere=(
            client_id is not None and not outgoing and call.callee_client_id is not None and call.callee_client_id != client_id
        ),
    )


# ----------------------------------------------------------------------------- transitions


def _audit(unit: CallUnit, action: str, call: AppCall, **metadata: Any) -> None:
    # The actor and the call id only: the audit log never says whom someone called.
    now = utc_now()
    unit.team.repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=unit.context.tenant_id,
            workspace_id=unit.context.workspace_id,
            actor=unit.context.user_id,
            action=action,
            target=call.id,
            outcome="success",
            correlation_id=call.id,
            created_at=now,
            metadata={"media": call.media.value, **metadata},
        )
    )


def _finish(unit: CallUnit, call: AppCall, outcome: CallOutcome, *, at: datetime, by: str | None) -> AppCall:
    call.status = CallStatus.ENDED
    call.outcome = outcome
    call.ended_at = at
    call.ended_by_user_id = by
    if call.answered_at is not None:
        call.duration_seconds = max(0, int((at - call.answered_at).total_seconds()))
    unit.calls.save(unit.context, call)
    unit.calls.delete_signals(unit.context, call.id)
    _audit(unit, "app_call.ended", call, outcome=outcome.value, duration_seconds=call.duration_seconds)
    if outcome in MISSED_OUTCOMES:
        _notify_missed(unit, call)
    return call


def _notify_missed(unit: CallUnit, call: AppCall) -> None:
    from .notifications import create_notification
    from .team_models import EmailStatus

    kind = "video" if call.media == CallMedia.VIDEO else "voice"
    notification = create_notification(
        unit.team.store,
        unit.context,
        user_id=call.callee_user_id,
        kind="missed_call",
        title="Missed call",
        body=f"{call.caller_name} tried to reach you with a {kind} call.",
        email=None,
    )
    if notification.email_status == EmailStatus.PENDING:  # pragma: no cover - email=None never queues
        unit.pending_email.append((call.callee_user_id, notification.id))
    unit.team.repository.record_event(
        create_event(
            CanonicalEventName.NOTIFICATION_CREATED,
            unit.context,
            notification.id,
            {"recipient": call.callee_user_id, "kind": "missed_call", "work_item_id": None},
        ).event
    )


def settle_stale(unit: CallUnit, *, now: datetime | None = None) -> list[AppCall]:
    """Ends the participant's rings past their timeout and connected calls gone silent."""
    at = now or utc_now()
    settled: list[AppCall] = []
    for call in unit.calls.live(unit.context):
        if call.status == CallStatus.RINGING and at >= ring_expires_at(call):
            locked = unit.calls.get(unit.context, call.id, for_update=True)
            if locked is not None and locked.status == CallStatus.RINGING:
                settled.append(_finish(unit, locked, CallOutcome.MISSED, at=ring_expires_at(locked), by=None))
        elif call.status == CallStatus.ACCEPTED:
            last = call.heartbeat_at or call.answered_at or call.created_at
            if at - last >= HEARTBEAT_TIMEOUT:
                locked = unit.calls.get(unit.context, call.id, for_update=True)
                if locked is not None and locked.status == CallStatus.ACCEPTED:
                    settled.append(_finish(unit, locked, CallOutcome.DROPPED, at=last, by=None))
    return settled


def _callee_is_busy(context: TenantContext, callee_user_id: str) -> bool:
    """Whether the callee has a call ringing or in progress (read in the callee's own context).

    The caller's row policies cannot see the callee's calls with other people, so this one
    read runs as the callee, like the push sender reads a recipient's devices.
    """
    callee = context.model_copy(update={"user_id": callee_user_id})
    with open_call_store(callee) as store:
        live = store.live(callee)
    now = utc_now()
    return any(c.status == CallStatus.ACCEPTED or now < ring_expires_at(c) for c in live)


def _load(unit: CallUnit, call_id: str) -> AppCall:
    call = unit.calls.get(unit.context, call_id, for_update=True)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


def _deliver_after_commit(background: BackgroundTasks, unit: CallUnit) -> None:
    if unit.pending_email:  # pragma: no cover - see _notify_missed
        from .notifications import deliver_pending_email

        background.add_task(deliver_pending_email, unit.context, list(unit.pending_email))


# ----------------------------------------------------------------------------- routes


@router.get("/calls/config", response_model=CallConfig)
def get_call_config(context: TenantContext = Depends(tenant_context)) -> CallConfig:
    """Whether calls work on this server and how (every member may read it)."""
    require_permission(context, Permission.CALL_USE)
    mode = call_mode()
    return CallConfig(
        enabled=mode.mode != CallMode.OFF,
        mode=mode.mode,
        reason=mode.reason,
        video_enabled=settings.calls_video_enabled,
        ring_timeout_seconds=settings.calls_ring_timeout_seconds,
    )


@router.get("/calls/ice-servers", response_model=IceServers)
def get_ice_servers(context: TenantContext = Depends(tenant_context)) -> IceServers:
    """STUN URLs and, with a relay configured, TURN credentials that expire."""
    require_permission(context, Permission.CALL_USE)
    mode = _require_enabled()
    servers: list[IceServer] = []
    if mode.stun_urls:
        servers.append(IceServer(urls=list(mode.stun_urls)))
    expires_at: datetime | None = None
    ttl: int | None = None
    if mode.mode == CallMode.TURN:
        username, credential, expires_at = turn_credentials(context)
        ttl = settings.calls_turn_ttl_seconds
        servers.append(IceServer(urls=list(mode.turn_urls), username=username, credential=credential))
    return IceServers(mode=mode.mode, ice_servers=servers, expires_at=expires_at, ttl_seconds=ttl)


@router.post("/calls", response_model=AppCallView, status_code=status.HTTP_201_CREATED)
def place_call(body: PlaceCall, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)) -> AppCallView:
    """Ring a teammate. A busy callee gets the call recorded as ``busy`` (201, not an error)."""
    require_permission(context, Permission.CALL_USE)
    _require_enabled()
    if body.media == CallMedia.VIDEO and not settings.calls_video_enabled:
        raise HTTPException(status_code=422, detail="Video calls are turned off on this server")
    if body.callee_user_id == context.user_id:
        raise HTTPException(status_code=422, detail="You cannot call yourself")
    with open_call_unit(context) as unit:
        unit.team.require_workspace()
        people = {person.user_id: person for person in unit.team.people()}
        callee = people.get(body.callee_user_id)
        if callee is None:
            # The same answer for an unknown id and for someone of another workspace or tenant.
            raise HTTPException(status_code=404, detail="This person is not an active member of this workspace")
        me = people.get(context.user_id)
        settle_stale(unit)
        if unit.calls.live(unit.context):
            raise HTTPException(status_code=409, detail="You are already in a call")
        unit.calls.purge(unit.context, utc_now() - timedelta(days=settings.calls_retention_days))
        now = utc_now()
        call = AppCall(
            id=new_id("call"),
            caller_user_id=context.user_id,
            callee_user_id=callee.user_id,
            caller_name=(me.display_name if me else context.user_id)[:120],
            callee_name=callee.display_name[:120],
            caller_client_id=body.client_id,
            media=body.media,
            status=CallStatus.RINGING,
            created_at=now,
        )
        busy = _callee_is_busy(context, callee.user_id)
        unit.calls.insert(unit.context, call)
        _audit(unit, "app_call.placed", call)
        if busy:
            _finish(unit, call, CallOutcome.BUSY, at=now, by=None)
        else:
            unit.team.repository.record_event(
                create_event(
                    CanonicalEventName.APP_CALL_RINGING,
                    context,
                    call.id,
                    {"call_id": call.id, "recipient": callee.user_id},
                    created_at=now,
                ).event
            )
        view = view_of(call, context, body.client_id)
    _deliver_after_commit(background, unit)
    return view


@router.get("/calls", response_model=AppCallPage)
def list_calls(
    background: BackgroundTasks,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=200),
    missed: bool = False,
    context: TenantContext = Depends(tenant_context),
) -> AppCallPage:
    """The caller's own calls, newest first; ``missed=true`` keeps the missed incoming ones."""
    require_permission(context, Permission.CALL_USE)
    before: tuple[datetime, str] | None = None
    if cursor:
        stamp, _, call_id = cursor.partition("|")
        try:
            before = (datetime.fromisoformat(stamp), call_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid cursor") from exc
    with open_call_unit(context) as unit:
        settle_stale(unit)
        calls = unit.calls.page(unit.context, limit=limit + 1, before=before, missed_only=missed)
    _deliver_after_commit(background, unit)
    more = len(calls) > limit
    calls = calls[:limit]
    next_cursor = f"{calls[-1].created_at.isoformat()}|{calls[-1].id}" if more and calls else None
    return AppCallPage(items=[view_of(call, context) for call in calls], next_cursor=next_cursor)


@router.post("/calls/{call_id}/accept", response_model=AppCallView)
def accept_call(call_id: str, body: AcceptCall, context: TenantContext = Depends(tenant_context)) -> AppCallView:
    """The callee answers on this device; the caller then sends the offer."""
    require_permission(context, Permission.CALL_USE)
    _require_enabled()
    with open_call_unit(context) as unit:
        settle_stale(unit)
        call = _load(unit, call_id)
        if call.callee_user_id != context.user_id:
            raise HTTPException(status_code=403, detail="Only the person being called can answer")
        if call.status != CallStatus.RINGING:
            raise HTTPException(status_code=409, detail="This call is no longer ringing")
        now = utc_now()
        call.status = CallStatus.ACCEPTED
        call.answered_at = now
        call.heartbeat_at = now
        call.callee_client_id = body.client_id
        unit.calls.save(unit.context, call)
        _audit(unit, "app_call.answered", call)
        return view_of(call, context, body.client_id)


@router.post("/calls/{call_id}/end", response_model=AppCallView)
def end_call(call_id: str, body: EndCall, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)) -> AppCallView:
    """Hang up, decline or report a failure; ending an ended call returns it unchanged."""
    require_permission(context, Permission.CALL_USE)
    with open_call_unit(context) as unit:
        call = _load(unit, call_id)
        if call.status != CallStatus.ENDED:
            caller = call.caller_user_id == context.user_id
            if body.reason == "failed":
                outcome = CallOutcome.FAILED
            elif call.status == CallStatus.ACCEPTED:
                outcome = CallOutcome.COMPLETED
            elif caller:
                outcome = CallOutcome.CANCELLED
            elif body.reason == "busy":
                outcome = CallOutcome.BUSY
            else:
                outcome = CallOutcome.DECLINED
            _finish(unit, call, outcome, at=utc_now(), by=context.user_id)
        view = view_of(call, context)
    _deliver_after_commit(background, unit)
    return view


@router.post("/calls/{call_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def call_heartbeat(call_id: str, context: TenantContext = Depends(tenant_context)) -> None:
    """Sent every 15 s by a connected device; a call without one for a minute is dropped."""
    require_permission(context, Permission.CALL_USE)
    with open_call_store(context) as store:
        call = store.get(context, call_id, for_update=True)
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        if call.status != CallStatus.ACCEPTED:
            raise HTTPException(status_code=409, detail="This call is not in progress")
        call.heartbeat_at = utc_now()
        store.save(context, call)


@router.post("/calls/{call_id}/signals", status_code=status.HTTP_202_ACCEPTED)
def send_signal(call_id: str, body: SignalBody, context: TenantContext = Depends(tenant_context)) -> dict[str, str]:
    """An offer (caller), an answer (callee) or ICE candidates, for the other side's device."""
    require_permission(context, Permission.CALL_USE)
    payload = body.payload()
    with open_call_store(context) as store:
        call = store.get(context, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        if call.status != CallStatus.ACCEPTED or call.callee_client_id is None:
            raise HTTPException(status_code=409, detail="This call is not in progress")
        caller = call.caller_user_id == context.user_id
        if body.kind == SignalKind.OFFER and not caller:
            raise HTTPException(status_code=409, detail="Only the caller sends the offer")
        if body.kind == SignalKind.ANSWER and caller:
            raise HTTPException(status_code=409, detail="Only the person called sends the answer")
        if store.count_sent(context, call_id) >= MAX_SIGNALS_PER_SENDER:
            raise HTTPException(status_code=429, detail="Too many signals for this call")
        signal = CallSignal(
            id=new_id("sig"),
            call_id=call.id,
            sender_user_id=context.user_id,
            recipient_user_id=call.callee_user_id if caller else call.caller_user_id,
            recipient_client_id=call.callee_client_id if caller else call.caller_client_id,
            kind=body.kind,
            payload=payload,
            created_at=utc_now(),
        )
        store.add_signal(context, signal)
    return {"id": signal.id}


# ----------------------------------------------------------------------------- stream


@dataclass
class Snapshot:
    calls: list[AppCall]
    signals: list[CallSignal]

    def stale(self, now: datetime) -> bool:
        for call in self.calls:
            if call.status == CallStatus.RINGING and now >= ring_expires_at(call):
                return True
            if call.status == CallStatus.ACCEPTED and now - (call.heartbeat_at or call.created_at) >= HEARTBEAT_TIMEOUT:
                return True
        return False

    @property
    def live(self) -> bool:
        return any(call.status != CallStatus.ENDED for call in self.calls)


def read_snapshot(context: TenantContext, client_id: str) -> Snapshot:
    """The participant's live and just-ended calls and this device's signals (own session)."""
    with open_call_store(context) as store:
        calls = store.recent(context, utc_now() - ENDED_VISIBLE)
        bound = [
            c.id
            for c in calls
            if c.status == CallStatus.ACCEPTED
            and client_id == (c.caller_client_id if c.caller_user_id == context.user_id else c.callee_client_id)
        ]
        signals = store.signals_for(context, client_id, bound) if bound else []
    return Snapshot(calls, signals)


def settle_for_stream(context: TenantContext) -> None:
    with open_call_unit(context) as unit:
        settle_stale(unit)


def _frame(event: str, event_id: str, data: str) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {data}\n\n"


async def call_event_source(
    context: TenantContext,
    client_id: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    read: Callable[[TenantContext, str], Snapshot] = read_snapshot,
    settle: Callable[[TenantContext], None] = settle_for_stream,
    follow: bool = True,
) -> AsyncIterator[str]:
    """``call`` events (a call's state changed) and ``signal`` events (for this device)."""
    yield "retry: 1000\n\n"
    known: dict[str, tuple[Any, ...]] = {}
    seen: set[str] = set()
    idle = 0.0
    last_settle = 0.0
    while True:
        snapshot = await asyncio.to_thread(read, context, client_id)
        if snapshot.stale(utc_now()) and time.monotonic() - last_settle >= 2.0:
            last_settle = time.monotonic()
            try:
                await asyncio.to_thread(settle, context)
            except Exception:  # a failed settle never ends the stream; the next poll retries
                logger.exception("app_call_settle_failed")
            snapshot = await asyncio.to_thread(read, context, client_id)
        frames: list[str] = []
        for call in snapshot.calls:
            key = (call.status, call.outcome, call.callee_client_id, call.answered_at, call.ended_at)
            if known.get(call.id) != key:
                known[call.id] = key
                view = view_of(call, context, client_id)
                frames.append(_frame("call", f"{call.id}:{call.status.value}", view.model_dump_json()))
        for signal in snapshot.signals:
            if signal.id not in seen:
                seen.add(signal.id)
                data = {"id": signal.id, "call_id": signal.call_id, "kind": signal.kind.value, "payload": signal.payload}
                frames.append(_frame("signal", signal.id, _json(data)))
        live_ids = {call.id for call in snapshot.calls}
        for call_id in [k for k in known if k not in live_ids]:
            known.pop(call_id, None)
        if len(seen) > 5000:
            seen = {signal.id for signal in snapshot.signals}
        for frame in frames:
            yield frame
        if not follow or await is_disconnected():
            return
        interval = settings.calls_active_poll_seconds if snapshot.live else settings.calls_poll_seconds
        await asyncio.sleep(interval)
        idle = 0.0 if frames else idle + interval
        if idle >= KEEPALIVE_SECONDS:
            idle = 0.0
            yield ": keep-alive\n\n"


def _json(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _check_member(context: TenantContext) -> None:
    from .dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=True):
        pass


@router.get("/calls/stream")
async def call_stream(
    request: Request,
    client_id: str = Query(pattern=CLIENT_ID_PATTERN),
    follow: bool = True,
    context: TenantContext = Depends(tenant_context),
) -> StreamingResponse:
    """This device's call states and signals as server-sent events (``call``, ``signal``)."""
    require_permission(context, Permission.CALL_USE)
    # Checked once, in a short unit of work that ends before the body starts.
    await asyncio.to_thread(_check_member, context)
    return StreamingResponse(
        call_event_source(context, client_id, request.is_disconnected, follow=follow),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# Declared after /calls/stream, /calls/config and /calls/ice-servers so those paths win.
@router.get("/calls/{call_id}", response_model=AppCallView)
def get_call(call_id: str, context: TenantContext = Depends(tenant_context)) -> AppCallView:
    require_permission(context, Permission.CALL_USE)
    with open_call_store(context) as store:
        call = store.get(context, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return view_of(call, context)


__all__ = [
    "AppCall",
    "AppCallView",
    "CallMode",
    "CallOutcome",
    "CallSignal",
    "CallStatus",
    "call_event_source",
    "call_mode",
    "memory_call_store",
    "router",
    "turn_credential_valid",
    "turn_credentials",
]
