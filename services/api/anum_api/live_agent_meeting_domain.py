"""Governed live agent meeting state machine (F06/F07 foundation).

This domain is deliberately separate from imported call recordings in meetings.py.
It makes no LLM calls, emits no synthetic conversation, and authorizes no tool use.
Persistence, participant authorization, event outbox and runtime orchestration are
separate layers. Methods return new immutable sessions for atomic repository writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionStatus(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ParticipantKind(StrEnum):
    AGENT = "agent"
    HUMAN = "human"


class SpeakerRole(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


class MeetingConflict(ValueError):
    pass


class MeetingScopeError(PermissionError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


MeetingId = Annotated[str, Field(min_length=1, max_length=80)]
ActorId = Annotated[str, Field(min_length=1, max_length=160)]


class LiveMeetingLimits(StrictModel):
    max_turns: int = Field(default=24, ge=1, le=200)
    max_duration_seconds: int = Field(default=1800, ge=60, le=14400)
    max_tokens: int = Field(default=50000, ge=100, le=1000000)
    max_cost_usd: float = Field(default=2, ge=0, le=1000)
    max_participants: int = Field(default=8, ge=1, le=50)


class LiveParticipant(StrictModel):
    id: ActorId
    kind: ParticipantKind
    tenant_id: MeetingId
    workspace_id: MeetingId
    added_by: ActorId


class LiveTurn(StrictModel):
    sequence: int = Field(ge=1)
    speaker_id: ActorId
    role: SpeakerRole
    body: str = Field(min_length=1, max_length=30000)
    created_at: datetime
    request_key: str = Field(min_length=1, max_length=120)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)
    provenance: str | None = Field(default=None, max_length=400)


class LiveMeeting(StrictModel):
    id: MeetingId
    tenant_id: MeetingId
    workspace_id: MeetingId
    title: str = Field(min_length=1, max_length=200)
    created_by: ActorId
    status: SessionStatus = SessionStatus.SCHEDULED
    revision: int = Field(default=1, ge=1)
    limits: LiveMeetingLimits = Field(default_factory=LiveMeetingLimits)
    participants: tuple[LiveParticipant, ...] = ()
    turns: tuple[LiveTurn, ...] = ()
    started_at: datetime | None = None
    ended_at: datetime | None = None
    total_input_tokens: int = Field(default=0, ge=0)
    total_output_tokens: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def invariant(self) -> "LiveMeeting":
        ids = [p.id for p in self.participants]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate participant")
        if len(ids) > self.limits.max_participants:
            raise ValueError("participant limit exceeded")
        if any(p.tenant_id != self.tenant_id or p.workspace_id != self.workspace_id for p in self.participants):
            raise ValueError("cross-workspace participant")
        seq = [turn.sequence for turn in self.turns]
        if seq != list(range(1, len(self.turns) + 1)):
            raise ValueError("turns must be consecutive and ordered")
        keys = [turn.request_key for turn in self.turns]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate turn request key")
        if len(seq) > self.limits.max_turns:
            raise ValueError("turn limit exceeded")
        if (self.total_input_tokens, self.total_output_tokens) != (
            sum(t.input_tokens for t in self.turns),
            sum(t.output_tokens for t in self.turns),
        ):
            raise ValueError("inconsistent token ledger")
        if abs(self.total_cost_usd - sum(t.estimated_cost_usd for t in self.turns)) > 0.000001:
            raise ValueError("inconsistent cost ledger")
        return self


def _scope(meeting: LiveMeeting, tenant_id: str, workspace_id: str) -> None:
    if meeting.tenant_id != tenant_id or meeting.workspace_id != workspace_id:
        raise MeetingScopeError("meeting is not accessible in this workspace")


def _revision(meeting: LiveMeeting, revision: int) -> None:
    if meeting.revision != revision:
        raise MeetingConflict("meeting changed; reload before retrying")


def _changed(meeting: LiveMeeting, **changes: object) -> LiveMeeting:
    return meeting.model_copy(update={"revision": meeting.revision + 1, **changes})


def add_participant(
    meeting: LiveMeeting, participant: LiveParticipant, *,
    tenant_id: str, workspace_id: str, expected_revision: int,
) -> LiveMeeting:
    _scope(meeting, tenant_id, workspace_id)
    _revision(meeting, expected_revision)
    if meeting.status != SessionStatus.SCHEDULED:
        raise MeetingConflict("participants may be added only before starting")
    if participant.tenant_id != meeting.tenant_id or participant.workspace_id != meeting.workspace_id:
        raise MeetingScopeError("cross-workspace participants are not allowed")
    if any(p.id == participant.id for p in meeting.participants):
        return meeting
    if len(meeting.participants) >= meeting.limits.max_participants:
        raise MeetingConflict("participant limit reached")
    return _changed(meeting, participants=meeting.participants + (participant,))


def transition(
    meeting: LiveMeeting, target: SessionStatus, *,
    tenant_id: str, workspace_id: str, expected_revision: int,
    now: datetime,
) -> LiveMeeting:
    _scope(meeting, tenant_id, workspace_id)
    _revision(meeting, expected_revision)
    transitions = {
        SessionStatus.SCHEDULED: {SessionStatus.ACTIVE, SessionStatus.CANCELLED},
        SessionStatus.ACTIVE: {SessionStatus.PAUSED, SessionStatus.FINISHED, SessionStatus.CANCELLED, SessionStatus.FAILED},
        SessionStatus.PAUSED: {SessionStatus.ACTIVE, SessionStatus.CANCELLED, SessionStatus.FINISHED},
        SessionStatus.FINISHED: set(),
        SessionStatus.CANCELLED: set(),
        SessionStatus.FAILED: set(),
    }
    if target not in transitions[meeting.status]:
        raise MeetingConflict(f"cannot transition {meeting.status} to {target}")
    if target == SessionStatus.ACTIVE and meeting.status == SessionStatus.SCHEDULED and not meeting.participants:
        raise MeetingConflict("meeting cannot start without participants")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    updates: dict[str, object] = {"status": target}
    if target == SessionStatus.ACTIVE and meeting.started_at is None:
        updates["started_at"] = now
    if target in (SessionStatus.FINISHED, SessionStatus.CANCELLED, SessionStatus.FAILED):
        updates["ended_at"] = now
    return _changed(meeting, **updates)


def append_turn(
    meeting: LiveMeeting, turn: LiveTurn, *,
    tenant_id: str, workspace_id: str, expected_revision: int, now: datetime,
) -> LiveMeeting:
    _scope(meeting, tenant_id, workspace_id)
    # Idempotent retry returns same immutable state; repository must still use an
    # atomic unique (scope, session, request_key) insert to prevent races.
    old = next((t for t in meeting.turns if t.request_key == turn.request_key), None)
    if old is not None:
        if old != turn:
            raise MeetingConflict("request key was reused for different turn content")
        return meeting
    _revision(meeting, expected_revision)
    if meeting.status != SessionStatus.ACTIVE or meeting.started_at is None:
        raise MeetingConflict("meeting is not active")
    if turn.sequence != len(meeting.turns) + 1:
        raise MeetingConflict("incorrect meeting turn sequence")
    if turn.role == SpeakerRole.AGENT and not any(
        p.id == turn.speaker_id and p.kind == ParticipantKind.AGENT for p in meeting.participants
    ):
        raise MeetingScopeError("agent is not a meeting participant")
    if turn.role == SpeakerRole.HUMAN and not any(
        p.id == turn.speaker_id and p.kind == ParticipantKind.HUMAN for p in meeting.participants
    ):
        raise MeetingScopeError("human is not a meeting participant")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    if (now - meeting.started_at).total_seconds() > meeting.limits.max_duration_seconds:
        raise MeetingConflict("meeting time budget exceeded")
    if len(meeting.turns) >= meeting.limits.max_turns:
        raise MeetingConflict("meeting turn budget exhausted")
    inp = meeting.total_input_tokens + turn.input_tokens
    out = meeting.total_output_tokens + turn.output_tokens
    cost = meeting.total_cost_usd + turn.estimated_cost_usd
    if inp + out > meeting.limits.max_tokens or cost > meeting.limits.max_cost_usd + 1e-9:
        raise MeetingConflict("meeting token or cost budget exceeded")
    return _changed(
        meeting, turns=meeting.turns + (turn,),
        total_input_tokens=inp, total_output_tokens=out, total_cost_usd=cost,
    )
