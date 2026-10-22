"""Deterministic coordinator policy for persisted live-agent meetings.

No network or model calls occur here. The executor must atomically reserve the
turn under an optimistic meeting revision, then call ANUM's governed model
gateway outside the DB transaction. This policy never approves tool calls.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .live_agent_meeting_domain import (
    LiveMeeting,
    ParticipantKind,
    SessionStatus,
)


class CoordinatorAction(StrEnum):
    SPEAK = "speak"
    WAIT = "wait"
    STOP = "stop"


class CoordinatorReason(StrEnum):
    READY = "ready"
    SCHEDULED = "scheduled"
    PAUSED = "paused"
    FINISHED = "finished"
    NO_AGENTS = "no_agents"
    APPROVAL_PENDING = "approval_pending"
    TASK_BLOCKED = "task_blocked"
    MAX_TURNS = "max_turns"
    MAX_COST = "max_cost"
    MAX_TOKENS = "max_tokens"
    DEADLINE = "deadline"
    EMERGENCY_STOP = "emergency_stop"
    NO_BUDGET = "no_budget"


class CoordinatorInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    observed_at_epoch: int = Field(ge=1)
    active_agent_ids: frozenset[str] = frozenset()
    blocked_agent_ids: frozenset[str] = frozenset()
    approval_pending: bool = False
    emergency_stop: bool = False
    # Conservative upper bound on one *additional* agent call, not actual cost.
    reserved_input_tokens: int = Field(default=1000, ge=1, le=200000)
    reserved_output_tokens: int = Field(default=1000, ge=1, le=200000)
    reserved_cost_usd: float = Field(default=0.05, ge=0, le=1000)


class CoordinatorDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: CoordinatorAction
    reason: CoordinatorReason
    agent_id: str | None = None
    expected_revision: int = Field(ge=1)
    next_sequence: int = Field(ge=1)


def decide_next(meeting: LiveMeeting, state: CoordinatorInput) -> CoordinatorDecision:
    """Choose one eligible speaker without inventing agent activity.

    Fair rotation is deterministic from the last *persisted* agent utterance.
    User-supplied "active" claims must originate from verified runtime states;
    the coordinator must not infer that an agent is available from a desk
    assignment or avatar animation.
    """
    base = {"expected_revision": meeting.revision, "next_sequence": len(meeting.turns) + 1}

    def result(action: CoordinatorAction, reason: CoordinatorReason, agent: str | None = None) -> CoordinatorDecision:
        return CoordinatorDecision(action=action, reason=reason, agent_id=agent, **base)

    if state.emergency_stop:
        return result(CoordinatorAction.STOP, CoordinatorReason.EMERGENCY_STOP)
    if meeting.status in {SessionStatus.FINISHED, SessionStatus.CANCELLED, SessionStatus.FAILED}:
        return result(CoordinatorAction.STOP, CoordinatorReason.FINISHED)
    if meeting.status == SessionStatus.SCHEDULED:
        return result(CoordinatorAction.WAIT, CoordinatorReason.SCHEDULED)
    if meeting.status == SessionStatus.PAUSED:
        return result(CoordinatorAction.WAIT, CoordinatorReason.PAUSED)
    if state.approval_pending:
        return result(CoordinatorAction.WAIT, CoordinatorReason.APPROVAL_PENDING)
    if meeting.started_at is None:
        return result(CoordinatorAction.STOP, CoordinatorReason.NO_BUDGET)
    if state.observed_at_epoch >= int(meeting.started_at.timestamp()) + meeting.limits.max_duration_seconds:
        return result(CoordinatorAction.STOP, CoordinatorReason.DEADLINE)
    if len(meeting.turns) >= meeting.limits.max_turns:
        return result(CoordinatorAction.STOP, CoordinatorReason.MAX_TURNS)
    if meeting.total_cost_usd + state.reserved_cost_usd > meeting.limits.max_cost_usd:
        return result(CoordinatorAction.STOP, CoordinatorReason.MAX_COST)
    if (
        meeting.total_input_tokens + meeting.total_output_tokens
        + state.reserved_input_tokens + state.reserved_output_tokens
        > meeting.limits.max_tokens
    ):
        return result(CoordinatorAction.STOP, CoordinatorReason.MAX_TOKENS)

    agents = [
        participant.id for participant in meeting.participants
        if participant.kind == ParticipantKind.AGENT
    ]
    if not agents:
        return result(CoordinatorAction.WAIT, CoordinatorReason.NO_AGENTS)
    runnable = [
        agent for agent in agents
        if agent in state.active_agent_ids and agent not in state.blocked_agent_ids
    ]
    if not runnable:
        return result(CoordinatorAction.WAIT, CoordinatorReason.TASK_BLOCKED)
    previous = next(
        (turn.speaker_id for turn in reversed(meeting.turns) if turn.speaker_id in agents),
        None,
    )
    if previous in agents:
        # Skip blocked/unavailable agents while retaining fixed participant order.
        start = (agents.index(previous) + 1) % len(agents)
        order = agents[start:] + agents[:start]
    else:
        order = agents
    chosen = next(agent for agent in order if agent in runnable)
    return result(CoordinatorAction.SPEAK, CoordinatorReason.READY, chosen)
