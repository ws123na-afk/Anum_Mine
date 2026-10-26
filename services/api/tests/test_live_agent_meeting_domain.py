"""Deterministic tests for governed agent meetings; no provider calls or storage."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from anum_api.live_agent_meeting_domain import (
    LiveMeeting, LiveMeetingLimits, LiveParticipant, LiveTurn, MeetingConflict,
    MeetingScopeError, ParticipantKind, SessionStatus, SpeakerRole,
    add_participant, append_turn, transition,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def meeting(**kwargs):
    return LiveMeeting(id="live-1", tenant_id="t1", workspace_id="w1",
                       title="Architecture review", created_by="owner", **kwargs)


def agent():
    return LiveParticipant(id="agent-a", kind=ParticipantKind.AGENT,
                           tenant_id="t1", workspace_id="w1", added_by="owner")


def prepare(limits=None):
    m = meeting(**({"limits": limits} if limits else {}))
    m = add_participant(m, agent(), tenant_id="t1", workspace_id="w1", expected_revision=1)
    m = transition(m, SessionStatus.ACTIVE, tenant_id="t1", workspace_id="w1",
                   expected_revision=2, now=NOW)
    return m


def turn(seq=1, key="k1", *, cost=0.1, tokens=5, body="Evidence-backed analysis"):
    return LiveTurn(sequence=seq, speaker_id="agent-a", role=SpeakerRole.AGENT,
                    request_key=key, body=body, created_at=NOW,
                    input_tokens=tokens, output_tokens=tokens,
                    estimated_cost_usd=cost)


def append(m, t, now=NOW):
    return append_turn(m, t, tenant_id="t1", workspace_id="w1",
                       expected_revision=m.revision, now=now)


def test_meeting_is_separate_and_must_have_participants_before_start():
    m = meeting()
    with pytest.raises(MeetingConflict, match="participants"):
        transition(m, SessionStatus.ACTIVE, tenant_id="t1", workspace_id="w1",
                   expected_revision=1, now=NOW)
    assert m.status == SessionStatus.SCHEDULED
    assert prepare().status == SessionStatus.ACTIVE


def test_turns_are_real_ordered_scoped_and_accounted_for():
    m = prepare()
    written = append(m, turn())
    assert written.revision == m.revision + 1
    assert [t.sequence for t in written.turns] == [1]
    assert written.total_input_tokens == 5
    assert written.total_output_tokens == 5
    assert written.total_cost_usd == pytest.approx(0.1)
    assert m.turns == ()


def test_request_keys_are_idempotent_but_cannot_change_content():
    m = prepare()
    first = append(m, turn())
    same = append_turn(first, turn(), tenant_id="t1", workspace_id="w1",
                       expected_revision=m.revision, now=NOW)
    assert same is first
    with pytest.raises(MeetingConflict, match="request key"):
        append_turn(first, turn(body="different"), tenant_id="t1", workspace_id="w1",
                    expected_revision=first.revision, now=NOW)


def test_exhausted_turn_budget_blocks_more_messages():
    m = prepare(LiveMeetingLimits(max_turns=1))
    m = append(m, turn())
    with pytest.raises(MeetingConflict, match="turn budget"):
        append(m, turn(2, "k2"))


def test_tokens_cost_and_time_are_hard_limits():
    m = prepare(LiveMeetingLimits(max_tokens=100, max_cost_usd=0.15, max_duration_seconds=60))
    with pytest.raises(MeetingConflict, match="token or cost"):
        append(m, turn(cost=0.2))
    with pytest.raises(MeetingConflict, match="token or cost"):
        append(m, turn(tokens=60))
    with pytest.raises(MeetingConflict, match="time budget"):
        append(m, turn(), NOW + timedelta(seconds=61))


def test_permission_and_tenant_isolation():
    m = meeting()
    stranger = agent().model_copy(update={"workspace_id": "other"})
    with pytest.raises(MeetingScopeError):
        add_participant(m, stranger, tenant_id="t1", workspace_id="w1",
                        expected_revision=1)
    with pytest.raises(MeetingScopeError):
        add_participant(m, agent(), tenant_id="t1", workspace_id="other",
                        expected_revision=1)
    active = prepare()
    with pytest.raises(MeetingScopeError):
        append_turn(active, turn(), tenant_id="t2", workspace_id="w1",
                    expected_revision=active.revision, now=NOW)


def test_only_authorized_role_and_participant_may_speak():
    m = prepare()
    wrong = turn().model_copy(update={"speaker_id": "unknown"})
    with pytest.raises(MeetingScopeError):
        append(m, wrong)
    wrong_role = turn().model_copy(update={"role": SpeakerRole.HUMAN})
    with pytest.raises(MeetingScopeError):
        append(m, wrong_role)


def test_wrong_revision_sequence_pause_cancel_and_finish():
    m = prepare()
    with pytest.raises(MeetingConflict, match="meeting changed"):
        append_turn(m, turn(), tenant_id="t1", workspace_id="w1",
                    expected_revision=1, now=NOW)
    with pytest.raises(MeetingConflict, match="sequence"):
        append(m, turn(2, "k2"))
    m = transition(m, SessionStatus.PAUSED, tenant_id="t1", workspace_id="w1",
                   expected_revision=m.revision, now=NOW)
    with pytest.raises(MeetingConflict, match="not active"):
        append(m, turn())
    m = transition(m, SessionStatus.ACTIVE, tenant_id="t1", workspace_id="w1",
                   expected_revision=m.revision, now=NOW)
    m = transition(m, SessionStatus.FINISHED, tenant_id="t1", workspace_id="w1",
                   expected_revision=m.revision, now=NOW)
    with pytest.raises(MeetingConflict):
        transition(m, SessionStatus.ACTIVE, tenant_id="t1", workspace_id="w1",
                   expected_revision=m.revision, now=NOW)


def test_data_model_rejects_fabricated_history_and_invalid_limits():
    with pytest.raises(ValidationError):
        meeting(turns=(turn(seq=3),))
    with pytest.raises(ValidationError):
        meeting(participants=(agent(), agent()))
    with pytest.raises(ValidationError):
        LiveMeetingLimits(max_turns=0)
    with pytest.raises(ValidationError):
        meeting(total_cost_usd=1.0)
    with pytest.raises(ValidationError):
        meeting(participants=(agent().model_copy(update={"workspace_id": "another"}),))
