"""In-app voice and video calls between teammates (docs/in-app-calls.md).

Media flows peer to peer (WebRTC); the API only rings, signals and keeps the record.

Who may call whom. Both people must be active members of the request's workspace, so of
the same tenant: the callee is looked up among the workspace's active memberships inside
the caller's RLS context, and an id from another tenant or workspace answers 404 exactly
like an unknown id. With PostgreSQL ``app_calls`` and ``app_call_signals`` are under forced
RLS on the tenant and the workspace, plus restrictive policies that admit a row only to
its two participants (``anum.user_id``) and let only the caller insert a call and only the
sender insert a signal (migration ``0073_app_calls``).

Lifecycle. ``POST /app-calls`` rings (status ``ringing``; ``app_call.ringing`` is the phone push
trigger). The callee's device that answers (``POST /app-calls/{id}/accept`` with its
``client_id``) is bound to the call; other devices stop ringing. Then the caller sends the
offer, the callee the answer and both their ICE candidates (``POST /app-calls/{id}/signals``),
each addressed to the other side's bound client. ``POST /app-calls/{id}/end`` ends it; a ring
that nobody answers within ``ANUM_CALLS_RING_TIMEOUT_SECONDS`` becomes ``missed`` and a call
whose parties stop sending heartbeats for ``HEARTBEAT_TIMEOUT`` seconds becomes
``dropped``, both settled lazily by the next read of either participant. Missed, cancelled
and busy rings leave the callee an in-app notification (kind ``missed_call``).

Signaling stream. ``GET /app-calls/stream?client_id=`` is a per-device SSE stream of the
caller's call states and the signals addressed to that device. Like the workspace event
stream it never reads through the request's session (CLAUDE.md, "Streaming responses
outlive request dependencies"): every read opens its own short session in a worker thread.
It reads when a call event wakes it (``app_call.ringing``, ``app_call.updated``,
``app_call.signaled``, recorded in the same transaction as the change and published
through the outbox; ``app_call_wake.py``) and polls as a fallback.

ICE servers. ``GET /app-calls/ice-servers`` returns the STUN URLs and, in ``turn`` mode,
short-lived TURN credentials in coturn's REST scheme (``use-auth-secret``): the username
is ``<expiry unix time>:<opaque id>`` and the credential the base64 HMAC-SHA1 of it under
``ANUM_CALLS_TURN_SECRET``. The opaque id is random per issue, so coturn's logs name no user.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
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

from .app_call_wake import CallWakeHub, CallWakeListener, call_wake_hub
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import repository_context, require_permission, tenant_context
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
# Group calls are a mesh: every device sends its media to every other device, so each
# person's upload grows with the group. Four people (three streams up per device) still
# fits a phone on mobile data with video; a bigger group would need a media server (SFU).
ROOM_MAX_PEOPLE = 4
# Everyone ever invited to one group call (declined and left people included).
ROOM_MAX_MEMBERS = 12
GROUP_UPDATE_DETAIL = "This is a group call. Update ANUM on this device to join it."


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


class MemberState(StrEnum):
    INVITED = "invited"
    JOINED = "joined"
    LEFT = "left"
    DECLINED = "declined"
    MISSED = "missed"
    BUSY = "busy"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RoomStatus(StrEnum):
    LIVE = "live"
    ENDED = "ended"


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
    # The group call this pair belongs to (docs/in-app-calls.md#group-calls), or None for a
    # call between two people. Each pair of people in a group call has its own row.
    room_id: str | None = None

    def involves(self, user_id: str) -> bool:
        return user_id in (self.caller_user_id, self.callee_user_id)


class RoomMember(BaseModel):
    """One person of a group call. ``client_id`` is the device that joined; never shown."""

    user_id: str
    name: str
    state: MemberState
    client_id: str | None = None
    invited_by: str | None = None
    joined_at: datetime | None = None
    left_at: datetime | None = None
    seen_at: datetime | None = None


class AppCallRoom(BaseModel):
    """A group call: who is in it. The media flows over one pair call per two people."""

    id: str
    host_user_id: str
    media: CallMedia
    status: RoomStatus
    members: list[RoomMember]
    created_at: datetime
    ended_at: datetime | None = None

    def member(self, user_id: str) -> RoomMember | None:
        return next((m for m in self.members if m.user_id == user_id), None)

    @property
    def member_user_ids(self) -> list[str]:
        return [m.user_id for m in self.members]

    def involves(self, user_id: str) -> bool:
        return user_id in self.member_user_ids

    def count(self, *states: MemberState) -> int:
        return sum(1 for m in self.members if m.state in states)


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
    room_id: str | None = None


class RoomMemberView(BaseModel):
    user_id: str
    name: str
    state: MemberState
    is_you: bool


class AppCallRoomView(BaseModel):
    """A group call as one of its people sees it."""

    id: str
    media: CallMedia
    status: RoomStatus
    host_user_id: str
    members: list[RoomMemberView]
    max_people: int
    created_at: datetime
    ended_at: datetime | None


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
    # Sent by apps that can hold a group call (one connection per person). An app without
    # it cannot join one, so answering a group call without it answers 409.
    group: bool = False


class PlaceGroupCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[str] = Field(min_length=2, max_length=ROOM_MAX_PEOPLE - 1)
    media: CallMedia = CallMedia.AUDIO
    client_id: str = Field(pattern=CLIENT_ID_PATTERN)

    @field_validator("user_ids")
    @classmethod
    def _distinct(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 160 for item in value):
            raise ValueError("user ids are 1 to 160 characters")
        if len(set(value)) != len(value):
            raise ValueError("each person once")
        return value


class InvitePerson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=160)
    client_id: str = Field(pattern=CLIENT_ID_PATTERN)


class LeaveRoom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["hangup", "failed"] = "hangup"


class MediaChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A voice call becomes a video call: the camera (phone, web, desktop) or a shared
    # screen (web and desktop) is sent over the connection that is already up.
    media: Literal["video"] = "video"
    source: Literal["camera", "screen"] = "camera"


class EndCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # hangup: the normal end (a decline while ringing for the callee, a cancel for the
    # caller); decline: the callee refuses; failed: the device could not connect or had no
    # microphone; busy: the callee's device is already in another call.
    reason: Literal["hangup", "decline", "failed", "busy"] = "hangup"


class IceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: str = Field(max_length=MAX_CANDIDATE_CHARS)
    sdpMid: str | None = Field(default=None, max_length=64)
    sdpMLineIndex: int | None = Field(default=None, ge=0, le=64)


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
    # Random per issue and never read back: coturn's logs and the TURN username name nobody.
    opaque = secrets.token_hex(12)
    turn_label = f"{expires}:{opaque}"
    digest = hmac.new(secret.encode(), turn_label.encode(), hashlib.sha1).digest()
    return turn_label, base64.b64encode(digest).decode(), datetime.fromtimestamp(expires, tz=issued.tzinfo)


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
    def all_mine(self, context: TenantContext) -> list[AppCall]: ...
    def count_signals(self, context: TenantContext) -> int: ...
    def delete_my_signals(self, context: TenantContext) -> int: ...
    def delete_call(self, context: TenantContext, call_id: str) -> bool: ...
    def forget_side(self, context: TenantContext, call_id: str, user_id: str, tombstone: str) -> bool: ...
    def insert_room(self, context: TenantContext, room: AppCallRoom) -> AppCallRoom: ...
    def get_room(self, context: TenantContext, room_id: str, *, for_update: bool = False) -> AppCallRoom | None: ...
    def save_room(self, context: TenantContext, room: AppCallRoom) -> AppCallRoom: ...
    def rooms(self, context: TenantContext, room_ids: list[str]) -> list[AppCallRoom]: ...
    def my_rooms(self, context: TenantContext) -> list[AppCallRoom]: ...
    def delete_room(self, context: TenantContext, room_id: str) -> bool: ...


class InMemoryCallStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests). Filters like the row policies."""

    def __init__(self) -> None:
        self._calls: dict[str, tuple[str, str, AppCall]] = {}
        self._signals: dict[str, tuple[str, str, CallSignal]] = {}
        self._rooms: dict[str, tuple[str, str, AppCallRoom]] = {}
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
            doomed_calls = [c for c in self._scoped(context) if c.created_at < before and c.status == CallStatus.ENDED]
            doomed = [c.id for c in doomed_calls]
            for room_id in {c.room_id for c in doomed_calls if c.room_id}:
                room = self.get_room(context, room_id)
                if room is not None and room.status == RoomStatus.ENDED:
                    self._drop_room(room_id)
            for call_id in doomed:
                self._calls.pop(call_id, None)
                for signal_id in [k for k, (_, _, s) in self._signals.items() if s.call_id == call_id]:
                    self._signals.pop(signal_id, None)
        return len(doomed)

    def all_mine(self, context: TenantContext) -> list[AppCall]:
        with self.lock:
            return sorted((c.model_copy() for c in self._scoped(context)), key=lambda c: (c.created_at, c.id))

    def count_signals(self, context: TenantContext) -> int:
        with self.lock:
            return len(self._visible_signals(context))

    def delete_my_signals(self, context: TenantContext) -> int:
        with self.lock:
            doomed = [s.id for s in self._visible_signals(context)]
            for signal_id in doomed:
                self._signals.pop(signal_id, None)
        return len(doomed)

    def delete_call(self, context: TenantContext, call_id: str) -> bool:
        with self.lock:
            if self.get(context, call_id) is None:
                return False
            self._calls.pop(call_id, None)
            for signal_id in [k for k, (_, _, s) in self._signals.items() if s.call_id == call_id]:
                self._signals.pop(signal_id, None)
        return True

    def forget_side(self, context: TenantContext, call_id: str, user_id: str, tombstone: str) -> bool:
        with self.lock:
            call = self.get(context, call_id)
            if call is None:
                return False
            self._calls[call_id] = (context.tenant_id, context.workspace_id, forget(call, user_id, tombstone))
        return True

    def insert_room(self, context: TenantContext, room: AppCallRoom) -> AppCallRoom:
        if room.host_user_id != context.user_id:
            raise PermissionError("only the host inserts a group call")
        with self.lock:
            self._rooms[room.id] = (context.tenant_id, context.workspace_id, room.model_copy(deep=True))
        return room

    def get_room(self, context: TenantContext, room_id: str, *, for_update: bool = False) -> AppCallRoom | None:
        with self.lock:
            entry = self._rooms.get(room_id)
            if entry is None:
                return None
            tenant, workspace, room = entry
            if tenant != context.tenant_id or workspace != context.workspace_id or not room.involves(context.user_id):
                return None
            return room.model_copy(deep=True)

    def save_room(self, context: TenantContext, room: AppCallRoom) -> AppCallRoom:
        with self.lock:
            if self.get_room(context, room.id) is None:
                raise LookupError(room.id)
            self._rooms[room.id] = (context.tenant_id, context.workspace_id, room.model_copy(deep=True))
        return room

    def rooms(self, context: TenantContext, room_ids: list[str]) -> list[AppCallRoom]:
        with self.lock:
            found = [self.get_room(context, room_id) for room_id in dict.fromkeys(room_ids)]
        return [room for room in found if room is not None]

    def my_rooms(self, context: TenantContext) -> list[AppCallRoom]:
        with self.lock:
            return self.rooms(context, list(self._rooms))

    def delete_room(self, context: TenantContext, room_id: str) -> bool:
        with self.lock:
            if self.get_room(context, room_id) is None:
                return False
            self._drop_room(room_id)
        return True

    def _drop_room(self, room_id: str) -> None:
        # Like the foreign key's ON DELETE SET NULL.
        self._rooms.pop(room_id, None)
        for key, (tenant, workspace, call) in list(self._calls.items()):
            if call.room_id == room_id:
                self._calls[key] = (tenant, workspace, call.model_copy(update={"room_id": None}))

    def clear(self) -> None:
        with self.lock:
            self._calls.clear()
            self._signals.clear()
            self._rooms.clear()


memory_call_store = InMemoryCallStore()

DELETED_PREFIX = "deleted:"
DELETED_NAME = "Deleted user"


def forget(call: AppCall, user_id: str, tombstone: str) -> AppCall:
    """``call`` with ``user_id``'s side replaced by ``tombstone`` (Delete my data).

    The other participant keeps the call in their history, shown as "Deleted user". A call
    still ringing or in progress is ended as failed.
    """
    forgotten = call.model_copy()
    if forgotten.caller_user_id == user_id:
        forgotten.caller_user_id, forgotten.caller_name, forgotten.caller_client_id = tombstone, DELETED_NAME, "deleted"
    if forgotten.callee_user_id == user_id:
        forgotten.callee_user_id, forgotten.callee_name = tombstone, DELETED_NAME
        forgotten.callee_client_id = "deleted" if forgotten.callee_client_id else None
    if forgotten.ended_by_user_id == user_id:
        forgotten.ended_by_user_id = tombstone
    if forgotten.status != CallStatus.ENDED:
        forgotten.status, forgotten.outcome, forgotten.ended_at = CallStatus.ENDED, CallOutcome.FAILED, utc_now()
        forgotten.callee_client_id = forgotten.callee_client_id or "deleted"
    return forgotten


def forget_member(room: AppCallRoom, user_id: str, tombstone: str) -> AppCallRoom:
    """``room`` with ``user_id`` replaced by ``tombstone`` (Delete my data); one still in
    the call leaves it."""
    forgotten = room.model_copy(deep=True)
    if forgotten.host_user_id == user_id:
        forgotten.host_user_id = tombstone
    for member in forgotten.members:
        if member.user_id == user_id:
            member.user_id, member.name, member.client_id = tombstone, DELETED_NAME, None
            if member.state in (MemberState.INVITED, MemberState.JOINED):
                member.state, member.left_at = MemberState.LEFT, utc_now()
        if member.invited_by == user_id:
            member.invited_by = tombstone
    return forgotten


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
def open_call_unit(context: TenantContext, *, require_membership: bool = True) -> Iterator[CallUnit]:
    from .team_work import open_team_unit

    with open_team_unit(context, require_membership=require_membership) as team:
        session = team.session
        calls: CallStore = _sql_store(session) if session is not None else memory_call_store
        if session is None:
            with memory_call_store.lock:
                yield CallUnit(context, team, calls)
        else:
            yield CallUnit(context, team, calls)


# ----------------------------------------------------------------------------- privacy


def app_calls_privacy_counts(context: TenantContext) -> dict[str, int]:
    """Download/Delete my data (docs/in-app-calls.md#privacy): the caller's calls and signals."""
    with open_call_store(context) as store:
        return {"app_calls": len(store.all_mine(context)), "app_call_signals": store.count_signals(context)}


def app_calls_privacy_export(context: TenantContext) -> list[dict[str, Any]]:
    """The caller's own view of each call: direction, the other person's display name at the
    time, audio or video, the times and the outcome. Never device ids or the other person's id."""
    with open_call_store(context) as store:
        calls = store.all_mine(context)
    hidden = {"peer_user_id", "on_this_device", "answered_elsewhere", "ring_expires_at", "room_id"}
    return [view_of(call, context).model_dump(mode="json", exclude=hidden) for call in calls]


def delete_my_app_calls(context: TenantContext) -> int:
    """Delete my data: the caller's signals go; each call keeps the other person's side and
    shows the caller as "Deleted user". The caller's side is rewritten inside the other
    participant's RLS context (the caller's own context could not write a row they are no
    longer part of). A call whose other side was already deleted is removed."""
    forgotten = 0
    # One tombstone per call: a group call's pair calls and its room share one, so the
    # retention job (acting as the pair's caller) can still remove the room with them.
    room_tombstones: dict[str, str] = {}

    def tombstone(room_id: str | None) -> str:
        if room_id is None:
            return f"{DELETED_PREFIX}{secrets.token_hex(8)}"
        return room_tombstones.setdefault(room_id, f"{DELETED_PREFIX}{secrets.token_hex(8)}")

    with open_call_store(context) as store:
        store.delete_my_signals(context)
        peers: list[tuple[str, str, str | None]] = []
        for call in store.all_mine(context):
            peer = call.callee_user_id if call.caller_user_id == context.user_id else call.caller_user_id
            if peer.startswith(DELETED_PREFIX):
                store.delete_call(context, call.id)
                forgotten += 1
            else:
                peers.append((call.id, peer, call.room_id))
        # Group calls: the person's place in each is rewritten inside another member's
        # context; a group with nobody else left is removed.
        room_peers: list[tuple[str, str]] = []
        for room in store.my_rooms(context):
            others = [m.user_id for m in room.members if m.user_id != context.user_id and not m.user_id.startswith(DELETED_PREFIX)]
            if others:
                room_peers.append((room.id, others[0]))
            else:
                store.delete_room(context, room.id)
    for room_id, peer in room_peers:
        peer_context = context.model_copy(update={"user_id": peer})
        with open_call_store(peer_context) as store:
            room = store.get_room(peer_context, room_id, for_update=True)
            if room is not None:
                store.save_room(peer_context, forget_member(room, context.user_id, tombstone(room_id)))
    for call_id, peer, room_id in peers:
        peer_context = context.model_copy(update={"user_id": peer})
        # A call unit, so a call that was still ringing or in progress (now ended as
        # failed) wakes the other person's devices in the same transaction.
        with open_call_unit(peer_context, require_membership=False) as unit:
            before = unit.calls.get(peer_context, call_id)
            if unit.calls.forget_side(peer_context, call_id, context.user_id, tombstone(room_id)):
                forgotten += 1
                if before is not None and before.status != CallStatus.ENDED:
                    _wake(unit, CanonicalEventName.APP_CALL_UPDATED, before)
    return forgotten


def purge_retention(
    *,
    session_factory: Any = None,
    maintenance_session_factory: Any = None,
    retention_days: int | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict[str, object]:
    """The daily retention job's call purge (``python -m anum_api.voice_retention``)."""
    from dataclasses import asdict

    from .db.app_calls_repository import purge_old_calls
    from .maintenance import default_session_factory

    result = purge_old_calls(
        session_factory or default_session_factory,
        retention_days=settings.calls_retention_days if retention_days is None else retention_days,
        maintenance_session_factory=maintenance_session_factory,
        dry_run=dry_run,
        batch_size=batch_size,
    )
    return asdict(result)


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
        room_id=call.room_id,
    )


def room_view_of(room: AppCallRoom, context: TenantContext) -> AppCallRoomView:
    return AppCallRoomView(
        id=room.id,
        media=room.media,
        status=room.status,
        host_user_id=room.host_user_id,
        members=[
            RoomMemberView(user_id=m.user_id, name=m.name, state=m.state, is_you=m.user_id == context.user_id) for m in room.members
        ],
        max_people=ROOM_MAX_PEOPLE,
        created_at=room.created_at,
        ended_at=room.ended_at,
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


def _wake(unit: CallUnit, name: CanonicalEventName, call: AppCall) -> None:
    """Records a call stream wake-up in the change's own transaction (an outbox row with
    PostgreSQL). The call id only: who called whom and the setup messages stay in the
    participants-only tables."""
    unit.team.repository.record_event(create_event(name, unit.context, call.id, {"call_id": call.id}).event)


_RING_END_STATE = {
    CallOutcome.DECLINED: MemberState.DECLINED,
    CallOutcome.MISSED: MemberState.MISSED,
    CallOutcome.BUSY: MemberState.BUSY,
    CallOutcome.CANCELLED: MemberState.CANCELLED,
    CallOutcome.FAILED: MemberState.FAILED,
}


def _settle_room(room: AppCallRoom, *, now: datetime) -> None:
    """Members whose devices went silent leave; with at most one person left in the call
    and nobody still being rung, the group call is over."""
    if room.status == RoomStatus.ENDED:
        return
    for member in room.members:
        last = member.seen_at or member.joined_at
        if member.state == MemberState.JOINED and last is not None and now - last >= HEARTBEAT_TIMEOUT:
            member.state, member.left_at = MemberState.LEFT, last
    if room.count(MemberState.JOINED) <= 1 and room.count(MemberState.INVITED) == 0:
        room.status, room.ended_at = RoomStatus.ENDED, now
        for member in room.members:
            if member.state == MemberState.JOINED:
                member.state, member.left_at = MemberState.LEFT, now


def _room_after_pair_end(unit: CallUnit, call: AppCall, *, was_ringing: bool, outcome: CallOutcome, at: datetime) -> None:
    """A ring of a group call that ends settles that person's place in the group."""
    if call.room_id is None:
        return
    room = unit.calls.get_room(unit.context, call.room_id, for_update=True)
    if room is None:
        return
    if was_ringing:
        member = room.member(call.callee_user_id)
        if member is not None and member.state == MemberState.INVITED:
            member.state, member.left_at = _RING_END_STATE.get(outcome, MemberState.MISSED), at
    _settle_room(room, now=utc_now())
    unit.calls.save_room(unit.context, room)


def _finish(unit: CallUnit, call: AppCall, outcome: CallOutcome, *, at: datetime, by: str | None) -> AppCall:
    was_ringing = call.status == CallStatus.RINGING
    call.status = CallStatus.ENDED
    call.outcome = outcome
    call.ended_at = at
    call.ended_by_user_id = by
    if call.answered_at is not None:
        call.duration_seconds = max(0, int((at - call.answered_at).total_seconds()))
    unit.calls.save(unit.context, call)
    unit.calls.delete_signals(unit.context, call.id)
    _audit(unit, "app_call.ended", call, outcome=outcome.value, duration_seconds=call.duration_seconds)
    _wake(unit, CanonicalEventName.APP_CALL_UPDATED, call)
    if outcome in MISSED_OUTCOMES:
        _notify_missed(unit, call)
    _room_after_pair_end(unit, call, was_ringing=was_ringing, outcome=outcome, at=at)
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
    # Group calls this person is in: members whose devices went silent leave (after the
    # pairs above, the lock order every route keeps).
    for room_id in sorted({c.room_id for c in [*unit.calls.live(unit.context), *settled] if c.room_id}):
        room = unit.calls.get_room(unit.context, room_id)
        if room is None or room.status == RoomStatus.ENDED:
            continue
        before = (room.status, [m.state for m in room.members])
        _settle_room(room, now=at)
        if (room.status, [m.state for m in room.members]) != before:
            locked_room = unit.calls.get_room(unit.context, room_id, for_update=True)
            if locked_room is not None:
                _settle_room(locked_room, now=at)
                unit.calls.save_room(unit.context, locked_room)
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


@router.get("/app-calls/config", response_model=CallConfig)
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


@router.get("/app-calls/ice-servers", response_model=IceServers)
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
        turn_label, credential, expires_at = turn_credentials(context)
        ttl = settings.calls_turn_ttl_seconds
        servers.append(IceServer(urls=list(mode.turn_urls), username=turn_label, credential=credential))
    return IceServers(mode=mode.mode, ice_servers=servers, expires_at=expires_at, ttl_seconds=ttl)


@router.post("/app-calls", response_model=AppCallView, status_code=status.HTTP_201_CREATED)
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


@router.get("/app-calls", response_model=AppCallPage)
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


@router.post("/app-calls/{call_id}/accept", response_model=AppCallView)
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
        if call.room_id is not None and not body.group:
            raise HTTPException(status_code=409, detail=GROUP_UPDATE_DETAIL)
        now = utc_now()
        call.status = CallStatus.ACCEPTED
        call.answered_at = now
        call.heartbeat_at = now
        call.callee_client_id = body.client_id
        unit.calls.save(unit.context, call)
        _audit(unit, "app_call.answered", call)
        _wake(unit, CanonicalEventName.APP_CALL_UPDATED, call)
        if call.room_id is not None:
            _join_room(unit, call, body.client_id, now=now)
        return view_of(call, context, body.client_id)


def _join_room(unit: CallUnit, call: AppCall, client_id: str, *, now: datetime) -> None:
    """The person who answered a group call joins it: one connected pair call from them to
    each other person already in the call (the person who rang them has one already).
    They send the offers; the room row lock makes two people answering at once see each
    other."""
    room = unit.calls.get_room(unit.context, call.room_id or "", for_update=True)
    if room is None or room.status == RoomStatus.ENDED:
        raise HTTPException(status_code=409, detail="This group call has ended")
    me = room.member(unit.context.user_id)
    if me is None:  # pragma: no cover - the ring itself made them a member
        raise HTTPException(status_code=409, detail="You are not invited to this group call")
    me.state, me.client_id, me.joined_at, me.seen_at, me.left_at = MemberState.JOINED, client_id, now, now, None
    for other in room.members:
        if other.state != MemberState.JOINED or other.user_id in (me.user_id, call.caller_user_id) or not other.client_id:
            continue
        pair = AppCall(
            id=new_id("call"),
            caller_user_id=me.user_id,
            callee_user_id=other.user_id,
            caller_name=me.name[:120],
            callee_name=other.name[:120],
            caller_client_id=client_id,
            callee_client_id=other.client_id,
            media=room.media,
            status=CallStatus.ACCEPTED,
            created_at=now,
            answered_at=now,
            heartbeat_at=now,
            room_id=room.id,
        )
        unit.calls.insert(unit.context, pair)
        _audit(unit, "app_call.joined", pair, group=True)
        _wake(unit, CanonicalEventName.APP_CALL_UPDATED, pair)
    unit.calls.save_room(unit.context, room)


@router.post("/app-calls/{call_id}/end", response_model=AppCallView)
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


@router.post("/app-calls/{call_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def call_heartbeat(call_id: str, context: TenantContext = Depends(tenant_context)) -> None:
    """Sent every 15 s by a connected device; a call without one for a minute is dropped."""
    require_permission(context, Permission.CALL_USE)
    with open_call_store(context) as store:
        call = store.get(context, call_id, for_update=True)
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        if call.status != CallStatus.ACCEPTED:
            raise HTTPException(status_code=409, detail="This call is not in progress")
        now = utc_now()
        call.heartbeat_at = now
        store.save(context, call)
        if call.room_id is not None:
            room = store.get_room(context, call.room_id, for_update=True)
            me = room.member(context.user_id) if room is not None else None
            if room is not None and me is not None and me.state == MemberState.JOINED:
                me.seen_at = now
                store.save_room(context, room)


@router.post("/app-calls/{call_id}/signals", status_code=status.HTTP_202_ACCEPTED)
def send_signal(call_id: str, body: SignalBody, context: TenantContext = Depends(tenant_context)) -> dict[str, str]:
    """An offer (caller), an answer (callee) or ICE candidates, for the other side's device."""
    require_permission(context, Permission.CALL_USE)
    payload = body.payload()
    # A call unit, not a bare store: the wake-up event is stored with the signal. Membership
    # is not re-checked here; the row policies admit only the call's two participants.
    with open_call_unit(context, require_membership=False) as unit:
        store = unit.calls
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
        _wake(unit, CanonicalEventName.APP_CALL_SIGNALED, call)
    return {"id": signal.id}


# ----------------------------------------------------------------------------- group calls


def _ring_in_room(unit: CallUnit, room: AppCallRoom, person: Any, client_id: str, *, now: datetime) -> AppCall:
    """Rings ``person`` into ``room`` from the caller's device; a busy person is recorded so."""
    context = unit.context
    me = room.member(context.user_id)
    existing = room.member(person.user_id)
    if existing is None:
        room.members.append(RoomMember(user_id=person.user_id, name=person.display_name[:120], state=MemberState.INVITED, invited_by=context.user_id))
    else:
        existing.state, existing.invited_by, existing.client_id = MemberState.INVITED, context.user_id, None
        existing.joined_at = existing.left_at = existing.seen_at = None
    call = AppCall(
        id=new_id("call"),
        caller_user_id=context.user_id,
        callee_user_id=person.user_id,
        caller_name=(me.name if me else context.user_id)[:120],
        callee_name=person.display_name[:120],
        caller_client_id=client_id,
        media=room.media,
        status=CallStatus.RINGING,
        created_at=now,
        room_id=room.id,
    )
    busy = _callee_is_busy(context, person.user_id)
    unit.calls.insert(context, call)
    _audit(unit, "app_call.placed", call, group=True)
    unit.calls.save_room(context, room)
    if busy:
        _finish(unit, call, CallOutcome.BUSY, at=now, by=None)
    else:
        unit.team.repository.record_event(
            create_event(
                CanonicalEventName.APP_CALL_RINGING, context, call.id, {"call_id": call.id, "recipient": person.user_id}, created_at=now
            ).event
        )
    return call


def _people(unit: CallUnit, user_ids: list[str]) -> list[Any]:
    people = {person.user_id: person for person in unit.team.people()}
    found = []
    for user_id in user_ids:
        if user_id == unit.context.user_id:
            raise HTTPException(status_code=422, detail="You cannot call yourself")
        person = people.get(user_id)
        if person is None:
            raise HTTPException(status_code=404, detail="This person is not an active member of this workspace")
        found.append(person)
    return found


@router.post("/app-calls/rooms", response_model=AppCallRoomView, status_code=status.HTTP_201_CREATED)
def place_group_call(body: PlaceGroupCall, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)) -> AppCallRoomView:
    """Ring two or three teammates at once (a group call of up to four people)."""
    require_permission(context, Permission.CALL_USE)
    _require_enabled()
    if body.media == CallMedia.VIDEO and not settings.calls_video_enabled:
        raise HTTPException(status_code=422, detail="Video calls are turned off on this server")
    with open_call_unit(context) as unit:
        unit.team.require_workspace()
        invitees = _people(unit, body.user_ids)
        me = {person.user_id: person for person in unit.team.people()}.get(context.user_id)
        settle_stale(unit)
        if unit.calls.live(unit.context):
            raise HTTPException(status_code=409, detail="You are already in a call")
        now = utc_now()
        room = AppCallRoom(
            id=new_id("room"),
            host_user_id=context.user_id,
            media=body.media,
            status=RoomStatus.LIVE,
            members=[
                RoomMember(
                    user_id=context.user_id,
                    name=(me.display_name if me else context.user_id)[:120],
                    state=MemberState.JOINED,
                    client_id=body.client_id,
                    joined_at=now,
                    seen_at=now,
                )
            ],
            created_at=now,
        )
        unit.calls.insert_room(unit.context, room)
        for person in invitees:
            _ring_in_room(unit, room, person, body.client_id, now=now)
            room = unit.calls.get_room(unit.context, room.id) or room
        view = room_view_of(room, context)
    _deliver_after_commit(background, unit)
    return view


@router.post("/app-calls/{call_id}/invite", response_model=AppCallRoomView)
def invite_to_call(call_id: str, body: InvitePerson, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)) -> AppCallRoomView:
    """Add a teammate to a call in progress; a call between two people becomes a group call."""
    require_permission(context, Permission.CALL_USE)
    _require_enabled()
    with open_call_unit(context) as unit:
        unit.team.require_workspace()
        (person,) = _people(unit, [body.user_id])
        settle_stale(unit)
        call = _load(unit, call_id)
        if call.status != CallStatus.ACCEPTED:
            raise HTTPException(status_code=409, detail="People can be added once the call is answered")
        outgoing = call.caller_user_id == context.user_id
        if (call.caller_client_id if outgoing else call.callee_client_id) != body.client_id:
            raise HTTPException(status_code=409, detail="Add people from the device that is in the call")
        now = utc_now()
        if call.room_id is None:
            room = AppCallRoom(
                id=new_id("room"),
                host_user_id=context.user_id,
                media=call.media,
                status=RoomStatus.LIVE,
                members=[
                    RoomMember(
                        user_id=call.caller_user_id, name=call.caller_name, state=MemberState.JOINED,
                        client_id=call.caller_client_id, joined_at=call.answered_at or now, seen_at=now,
                    ),
                    RoomMember(
                        user_id=call.callee_user_id, name=call.callee_name, state=MemberState.JOINED,
                        client_id=call.callee_client_id, invited_by=call.caller_user_id,
                        joined_at=call.answered_at or now, seen_at=now,
                    ),
                ],
                created_at=call.created_at,
            )
            unit.calls.insert_room(unit.context, room)
            call.room_id = room.id
            unit.calls.save(unit.context, call)
            _wake(unit, CanonicalEventName.APP_CALL_UPDATED, call)
        else:
            found = unit.calls.get_room(unit.context, call.room_id, for_update=True)
            if found is None or found.status == RoomStatus.ENDED:
                raise HTTPException(status_code=409, detail="This group call has ended")
            room = found
        existing = room.member(person.user_id)
        if existing is not None and existing.state in (MemberState.INVITED, MemberState.JOINED):
            raise HTTPException(status_code=409, detail="This person is already in the call or being rung")
        if room.count(MemberState.INVITED, MemberState.JOINED) >= ROOM_MAX_PEOPLE:
            raise HTTPException(status_code=409, detail=f"A group call has at most {ROOM_MAX_PEOPLE} people")
        if existing is None and len(room.members) >= ROOM_MAX_MEMBERS:
            raise HTTPException(status_code=409, detail="Too many people were invited to this call. Start a new call.")
        _ring_in_room(unit, room, person, body.client_id, now=now)
        view = room_view_of(unit.calls.get_room(unit.context, room.id) or room, context)
    _deliver_after_commit(background, unit)
    return view


@router.post("/app-calls/rooms/{room_id}/leave", response_model=AppCallRoomView)
def leave_group_call(room_id: str, body: LeaveRoom, background: BackgroundTasks, context: TenantContext = Depends(tenant_context)) -> AppCallRoomView:
    """Leave a group call: every connection of this person in it ends, their rings stop; the
    others stay connected. Leaving a group call that is over returns it unchanged."""
    require_permission(context, Permission.CALL_USE)
    with open_call_unit(context) as unit:
        # Pairs before the room, in id order: the same lock order as answering and ending.
        mine = sorted(c.id for c in unit.calls.live(unit.context) if c.room_id == room_id)
        if unit.calls.get_room(unit.context, room_id) is None:
            raise HTTPException(status_code=404, detail="Call not found")
        now = utc_now()
        for pair_id in mine:
            call = unit.calls.get(unit.context, pair_id, for_update=True)
            if call is None or call.status == CallStatus.ENDED:
                continue
            caller = call.caller_user_id == context.user_id
            if body.reason == "failed":
                outcome = CallOutcome.FAILED
            elif call.status == CallStatus.ACCEPTED:
                outcome = CallOutcome.COMPLETED
            else:
                outcome = CallOutcome.CANCELLED if caller else CallOutcome.DECLINED
            _finish(unit, call, outcome, at=now, by=context.user_id)
        room = unit.calls.get_room(unit.context, room_id, for_update=True)
        if room is None:  # pragma: no cover - read above in the same transaction
            raise HTTPException(status_code=404, detail="Call not found")
        me = room.member(context.user_id)
        if me is not None and me.state in (MemberState.INVITED, MemberState.JOINED):
            me.state = MemberState.LEFT if me.state == MemberState.JOINED else MemberState.DECLINED
            me.left_at = now
            _settle_room(room, now=now)
            unit.calls.save_room(unit.context, room)
            _audit_room(unit, "app_call.left", room)
        view = room_view_of(room, context)
    _deliver_after_commit(background, unit)
    return view


def _audit_room(unit: CallUnit, action: str, room: AppCallRoom) -> None:
    unit.team.repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=unit.context.tenant_id,
            workspace_id=unit.context.workspace_id,
            actor=unit.context.user_id,
            action=action,
            target=room.id,
            outcome="success",
            correlation_id=room.id,
            created_at=utc_now(),
            metadata={"media": room.media.value, "group": True, "status": room.status.value},
        )
    )


@router.get("/app-calls/rooms/{room_id}", response_model=AppCallRoomView)
def get_group_call(room_id: str, context: TenantContext = Depends(tenant_context)) -> AppCallRoomView:
    """Who is in a group call (its people only)."""
    require_permission(context, Permission.CALL_USE)
    with open_call_store(context) as store:
        room = store.get_room(context, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return room_view_of(room, context)


@router.post("/app-calls/{call_id}/media", response_model=AppCallView)
def change_call_media(call_id: str, body: MediaChange, context: TenantContext = Depends(tenant_context)) -> AppCallView:
    """A voice call becomes a video call (camera or shared screen) without hanging up.

    The picture travels over the connection that is already up; this records it and checks
    that video is allowed here. In a group call it covers this person's connections."""
    require_permission(context, Permission.CALL_USE)
    _require_enabled()
    if not settings.calls_video_enabled:
        detail = "Screen sharing needs video calls, which are turned off on this server" if body.source == "screen" else (
            "Video calls are turned off on this server"
        )
        raise HTTPException(status_code=422, detail=detail)
    with open_call_unit(context) as unit:
        found = unit.calls.get(unit.context, call_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Call not found")
        ids = {found.id}
        if found.room_id is not None:
            ids |= {c.id for c in unit.calls.live(unit.context) if c.room_id == found.room_id}
        # Locked in id order, like leaving a group call, so the two never wait on each other.
        locked = {i: unit.calls.get(unit.context, i, for_update=True) for i in sorted(ids)}
        call = locked[found.id]
        if call is None or call.status != CallStatus.ACCEPTED:
            raise HTTPException(status_code=409, detail="This call is not in progress")
        targets = [c for c in locked.values() if c is not None and c.status == CallStatus.ACCEPTED]
        for target in targets:
            if target.media != CallMedia.VIDEO:
                target.media = CallMedia.VIDEO
                unit.calls.save(unit.context, target)
                _wake(unit, CanonicalEventName.APP_CALL_UPDATED, target)
        if call.room_id is not None:
            room = unit.calls.get_room(unit.context, call.room_id, for_update=True)
            if room is not None and room.media != CallMedia.VIDEO:
                room.media = CallMedia.VIDEO
                unit.calls.save_room(unit.context, room)
        _audit(unit, "app_call.video_on", call, source=body.source)
        return view_of(call, context)


# ----------------------------------------------------------------------------- stream


@dataclass
class Snapshot:
    calls: list[AppCall]
    signals: list[CallSignal]
    rooms: list[AppCallRoom] = field(default_factory=list)

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

    def seconds_to_deadline(self, now: datetime) -> float | None:
        """Until the next ring times out or a connected call goes silent, so the stream
        settles it on time even when it polls slowly."""
        deadlines: list[datetime] = []
        for call in self.calls:
            if call.status == CallStatus.RINGING:
                deadlines.append(ring_expires_at(call))
            elif call.status == CallStatus.ACCEPTED:
                deadlines.append((call.heartbeat_at or call.created_at) + HEARTBEAT_TIMEOUT)
        if not deadlines:
            return None
        return max(0.0, (min(deadlines) - now).total_seconds())


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
        room_ids = [c.room_id for c in calls if c.room_id]
        rooms = store.rooms(context, room_ids) if room_ids else []
    return Snapshot(calls, signals, rooms)


def settle_for_stream(context: TenantContext) -> None:
    with open_call_unit(context) as unit:
        settle_stale(unit)


def _frame(event: str, event_id: str, data: str) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {data}\n\n"


def poll_interval(snapshot: Snapshot, *, bus_live: bool) -> float:
    """The fallback poll: slow while NATS delivers the wake-ups, the old pace otherwise."""
    if bus_live:
        return settings.calls_bus_fallback_poll_seconds
    return settings.calls_active_poll_seconds if snapshot.live else settings.calls_poll_seconds


async def _wait_for_wake(listener: CallWakeListener, timeout: float, known: set[str]) -> bool:
    """Whether a wake-up that concerns this stream arrived within ``timeout``."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while (remaining := end - loop.time()) > 0:
        wake = await listener.wait(remaining)
        if wake is None:
            return False
        if wake.concerns(known):
            return True
    return False


async def call_event_source(
    context: TenantContext,
    client_id: str,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    read: Callable[[TenantContext, str], Snapshot] = read_snapshot,
    settle: Callable[[TenantContext], None] = settle_for_stream,
    follow: bool = True,
    wakes: CallWakeHub | None = None,
) -> AsyncIterator[str]:
    """``call`` events (a call's state changed) and ``signal`` events (for this device).

    Every frame comes from ``read`` in a worker thread (its own short session). The stream
    reads when a call event wakes it and otherwise polls (``poll_interval``). The listener
    is registered before the first read, so a change stored during a read wakes the next
    wait at once.
    """
    hub = wakes or call_wake_hub
    yield "retry: 1000\n\n"
    known: dict[str, tuple[Any, ...]] = {}
    seen: set[str] = set()
    last_settle = 0.0
    last_sent = time.monotonic()
    async with hub.listen(context) as listener:
        while True:
            snapshot = await asyncio.to_thread(read, context, client_id)
            if snapshot.stale(utc_now()) and time.monotonic() - last_settle >= 2.0:
                last_settle = time.monotonic()
                try:
                    await asyncio.to_thread(settle, context)
                except Exception:  # a failed settle never ends the stream; the next read retries
                    logger.exception("app_call_settle_failed")
                snapshot = await asyncio.to_thread(read, context, client_id)
            frames: list[str] = []
            for call in snapshot.calls:
                key = (call.status, call.outcome, call.callee_client_id, call.answered_at, call.ended_at, call.media, call.room_id)
                if known.get(call.id) != key:
                    known[call.id] = key
                    view = view_of(call, context, client_id)
                    frames.append(_frame("call", f"{call.id}:{call.status.value}", view.model_dump_json()))
            for room in snapshot.rooms:
                room_key = ("room", room.status, room.media, tuple((m.user_id, m.state) for m in room.members))
                if known.get(room.id) != room_key:
                    known[room.id] = room_key
                    frames.append(_frame("room", f"{room.id}:{room.status.value}", room_view_of(room, context).model_dump_json()))
            for signal in snapshot.signals:
                if signal.id not in seen:
                    seen.add(signal.id)
                    data = {"id": signal.id, "call_id": signal.call_id, "kind": signal.kind.value, "payload": signal.payload}
                    frames.append(_frame("signal", signal.id, _json(data)))
            live_ids = {call.id for call in snapshot.calls} | {room.id for room in snapshot.rooms}
            for call_id in [k for k in known if k not in live_ids]:
                known.pop(call_id, None)
            if len(seen) > 5000:
                seen = {signal.id for signal in snapshot.signals}
            for frame in frames:
                yield frame
            if frames:
                last_sent = time.monotonic()
            if not follow or await is_disconnected():
                return
            wait = poll_interval(snapshot, bus_live=hub.live)
            deadline = snapshot.seconds_to_deadline(utc_now())
            if deadline is not None:
                wait = min(wait, deadline + 0.05)
            wait = max(0.05, min(wait, KEEPALIVE_SECONDS - (time.monotonic() - last_sent)))
            await _wait_for_wake(listener, wait, set(known))
            if time.monotonic() - last_sent >= KEEPALIVE_SECONDS:
                last_sent = time.monotonic()
                yield ": keep-alive\n\n"


def _json(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


@router.get("/app-calls/stream")
async def call_stream(
    request: Request,
    client_id: str = Query(pattern=CLIENT_ID_PATTERN),
    follow: bool = True,
    context: TenantContext = Depends(tenant_context),
    # The membership check, in a short unit of work closed before the body starts
    # (scope="function"; docs/realtime.md). The body reads in its own short sessions.
    _membership: Any = Depends(repository_context, scope="function"),
) -> StreamingResponse:
    """This device's call states and signals as server-sent events (``call``, ``signal``)."""
    require_permission(context, Permission.CALL_USE)
    return StreamingResponse(
        call_event_source(context, client_id, request.is_disconnected, follow=follow),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# Declared after /app-calls/stream, /app-calls/config and /app-calls/ice-servers so those paths win.
@router.get("/app-calls/{call_id}", response_model=AppCallView)
def get_call(call_id: str, context: TenantContext = Depends(tenant_context)) -> AppCallView:
    require_permission(context, Permission.CALL_USE)
    with open_call_store(context) as store:
        call = store.get(context, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return view_of(call, context)


__all__ = [
    "AppCall",
    "AppCallRoom",
    "AppCallRoomView",
    "AppCallView",
    "CallMode",
    "CallOutcome",
    "CallSignal",
    "CallStatus",
    "call_event_source",
    "call_mode",
    "memory_call_store",
    "router",
    "turn_credentials",
]
