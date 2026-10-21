"""Safe single-turn agent-meeting runtime gate.

The scheduler is pure policy. This service adds authenticated scope checks,
approval/emergency-stop checks, deadline/budget reservations, and a durable
single-flight lease interface. No tool execution or fake messages are allowed.

An adapter implementing MeetingTurnStore MUST persist reservations and commits
atomically under PostgreSQL RLS, using request-key and sequence uniqueness.
A model call occurs only after reservation and governance checks. Model text
is never considered committed until finish_turn succeeds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .live_agent_meeting_domain import (
    LiveMeeting, LiveTurn, SessionStatus, SpeakerRole,
)
from .live_agent_meeting_coordinator import (
    CoordinatorInput, CoordinatorAction, CoordinatorDecision, decide_next,
)


@dataclass(frozen=True)
class TurnReservation:
    meeting_id: str
    tenant_id: str
    workspace_id: str
    agent_id: str
    request_key: str
    sequence: int
    expected_revision: int
    expires_at: datetime


@dataclass(frozen=True)
class GeneratedTurn:
    text: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    provenance: str


class MeetingTurnStore(Protocol):
    """Backing store must enforce unique scope/session/sequence/request_key.

    reserve_turn must fail on concurrently held reservations and invalid revisions.
    finish_turn atomically validates reservation and writes turn + updated ledger +
    append-only audit event/outbox. abandon_turn releases an expired/failed lease.
    """
    async def reserve_turn(self, reservation: TurnReservation) -> bool: ...
    async def finish_turn(self, reservation: TurnReservation, turn: LiveTurn) -> LiveMeeting: ...
    async def abandon_turn(self, reservation: TurnReservation, reason: str) -> None: ...


class MeetingSafetyGate(Protocol):
    async def can_agent_speak(self, *, tenant_id: str, workspace_id: str,
                              agent_id: str, meeting_id: str) -> bool: ...


class AgentTextGenerator(Protocol):
    async def generate(self, *, meeting: LiveMeeting, agent_id: str,
                       token_limit: int, cost_limit_usd: float) -> GeneratedTurn: ...


class RuntimeBlocked(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RuntimeResult:
    decision: CoordinatorDecision
    committed: LiveMeeting | None = None


async def run_one_turn(
    *,
    meeting: LiveMeeting,
    state: CoordinatorInput,
    tenant_id: str,
    workspace_id: str,
    request_key: str,
    now: datetime,
    store: MeetingTurnStore,
    gate: MeetingSafetyGate,
    generator: AgentTextGenerator,
) -> RuntimeResult:
    """Perform at most one budgeted agent turn; no autonomous tool permissions.

    IMPORTANT: trusted runtime derives scope, clock, actual participants, usage,
    approvals and emergency-stop state. Do not build CoordinatorInput from browser
    input. The gate must check current authorization and approvals again immediately
    before starting external model work. The persistence adapter must perform its
    own compare-and-swap and enforce usage limits at commit.
    """
    if meeting.tenant_id != tenant_id or meeting.workspace_id != workspace_id:
        raise RuntimeBlocked("scope_mismatch")
    if not request_key or len(request_key) > 120:
        raise RuntimeBlocked("invalid_request_key")
    if now.tzinfo is None or now.utcoffset() is None:
        raise RuntimeBlocked("clock_not_timezone_aware")
    if meeting.status != SessionStatus.ACTIVE:
        return RuntimeResult(decision=decide_next(meeting, state))
    decision = decide_next(meeting, state)
    if decision.action != CoordinatorAction.SPEAK or decision.agent_id is None:
        return RuntimeResult(decision=decision)
    if not await gate.can_agent_speak(
        tenant_id=tenant_id, workspace_id=workspace_id,
        agent_id=decision.agent_id, meeting_id=meeting.id
    ):
        raise RuntimeBlocked("agent_not_authorized")
    ttl_seconds = min(120, max(1, meeting.limits.max_duration_seconds))
    reservation = TurnReservation(
        meeting_id=meeting.id, tenant_id=tenant_id, workspace_id=workspace_id,
        agent_id=decision.agent_id, request_key=request_key,
        sequence=decision.next_sequence, expected_revision=decision.expected_revision,
        expires_at=now.replace(microsecond=0) + timedelta(seconds=ttl_seconds)
    )
    if not await store.reserve_turn(reservation):
        raise RuntimeBlocked("turn_already_claimed")
    try:
        # Recheck governance after reservation: an approval or stop may have
        # arrived while the reservation was acquired.
        if not await gate.can_agent_speak(
            tenant_id=tenant_id, workspace_id=workspace_id,
            agent_id=decision.agent_id, meeting_id=meeting.id
        ):
            raise RuntimeBlocked("agent_not_authorized")
        response = await generator.generate(
            meeting=meeting, agent_id=decision.agent_id,
            token_limit=state.reserved_output_tokens,
            cost_limit_usd=state.reserved_cost_usd
        )
        if not response.text.strip() or len(response.text) > 30000:
            raise RuntimeBlocked("invalid_model_response")
        if min(response.input_tokens, response.output_tokens) < 0 or response.estimated_cost_usd < 0:
            raise RuntimeBlocked("negative_model_usage")
        if (response.input_tokens > state.reserved_input_tokens
                or response.output_tokens > state.reserved_output_tokens
                or response.estimated_cost_usd > state.reserved_cost_usd):
            raise RuntimeBlocked("model_usage_exceeded_reservation")
        if not response.provenance.strip():
            raise RuntimeBlocked("missing_provenance")
        turn = LiveTurn(
            sequence=decision.next_sequence, speaker_id=decision.agent_id,
            role=SpeakerRole.AGENT, body=response.text.strip(), created_at=now,
            request_key=request_key, input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost_usd=response.estimated_cost_usd,
            provenance=response.provenance
        )
        committed = await store.finish_turn(reservation, turn)
        if (committed.tenant_id != meeting.tenant_id
                or committed.workspace_id != meeting.workspace_id
                or not committed.turns or committed.turns[-1].request_key != request_key):
            raise RuntimeBlocked("invalid_persistence_receipt")
        return RuntimeResult(decision=decision, committed=committed)
    except BaseException:
        await store.abandon_turn(reservation, "generation_or_commit_failed")
        raise
