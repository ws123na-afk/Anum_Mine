"""No model calls: verify coordinator decisions are safe and deterministic."""
from datetime import datetime, timezone

from anum_api.live_agent_meeting_domain import (
    LiveMeeting, LiveMeetingLimits, LiveParticipant, LiveTurn,
    ParticipantKind, SessionStatus, SpeakerRole,
)
from anum_api.live_agent_meeting_coordinator import (
    CoordinatorAction, CoordinatorInput, CoordinatorReason, decide_next,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
TS = int(NOW.timestamp())


def fixture(*, status=SessionStatus.ACTIVE, participants=("alpha", "beta", "gamma"),
            turns=(), limits=None):
    people=tuple(LiveParticipant(id=name, kind=ParticipantKind.AGENT,
                                 tenant_id="tenant", workspace_id="space", added_by="owner")
                 for name in participants)
    return LiveMeeting(
        id="session", tenant_id="tenant", workspace_id="space",
        title="Meeting", created_by="owner", participants=people,
        status=status, started_at=NOW, turns=turns,
        total_input_tokens=sum(x.input_tokens for x in turns),
        total_output_tokens=sum(x.output_tokens for x in turns),
        total_cost_usd=sum(x.estimated_cost_usd for x in turns),
        limits=limits or LiveMeetingLimits(),
    )


def state(**changes):
    return CoordinatorInput(observed_at_epoch=TS + 1,
                            active_agent_ids=frozenset({"alpha","beta","gamma"}),
                            **changes)


def record(seq, speaker):
    return LiveTurn(sequence=seq, speaker_id=speaker, role=SpeakerRole.AGENT,
                    request_key=f"key{seq}", body="Actual persisted turn", created_at=NOW)


def test_fair_rotation_from_persisted_history():
    assert decide_next(fixture(), state()).agent_id == "alpha"
    assert decide_next(fixture(turns=(record(1,"alpha"),)), state()).agent_id == "beta"
    assert decide_next(fixture(turns=(record(1,"alpha"),record(2,"beta"))), state()).agent_id == "gamma"
    assert decide_next(fixture(turns=(record(1,"alpha"),record(2,"beta"),record(3,"gamma"))), state()).agent_id == "alpha"


def test_skip_blocked_candidate_and_never_run_unavailable_agent():
    s=CoordinatorInput(observed_at_epoch=TS+1,active_agent_ids=frozenset({"alpha","gamma"}))
    m=fixture(turns=(record(1,"alpha"),))
    d=decide_next(m,s)
    assert d.action == CoordinatorAction.SPEAK
    assert d.agent_id == "gamma"
    assert d.next_sequence == 2
    assert d.expected_revision == m.revision


def test_approval_or_emergency_stop_prevents_next_turn():
    assert decide_next(fixture(),state(approval_pending=True)).reason == CoordinatorReason.APPROVAL_PENDING
    d=decide_next(fixture(),state(emergency_stop=True))
    assert d.action == CoordinatorAction.STOP
    assert d.reason == CoordinatorReason.EMERGENCY_STOP


def test_scheduled_paused_terminal_are_not_speaking():
    for status in (SessionStatus.SCHEDULED, SessionStatus.PAUSED, SessionStatus.FINISHED,SessionStatus.CANCELLED,SessionStatus.FAILED):
        assert decide_next(fixture(status=status),state()).action != CoordinatorAction.SPEAK


def test_budget_constraints_fail_closed_before_new_model_call():
    limits=LiveMeetingLimits(max_turns=1,max_duration_seconds=60,max_tokens=250,max_cost_usd=0.03)
    m=fixture(limits=limits)
    assert decide_next(m,state()).reason == CoordinatorReason.MAX_COST
    s=CoordinatorInput(observed_at_epoch=TS+1,active_agent_ids=frozenset({"alpha"}),
                       reserved_input_tokens=100,reserved_output_tokens=100,reserved_cost_usd=0.01)
    assert decide_next(m,s).action == CoordinatorAction.SPEAK
    late=s.model_copy(update={"observed_at_epoch":TS+61})
    assert decide_next(m,late).reason == CoordinatorReason.DEADLINE
    exceeded=s.model_copy(update={"reserved_output_tokens":200})
    assert decide_next(m,exceeded).reason == CoordinatorReason.MAX_TOKENS
    done=fixture(turns=(record(1,"alpha"),),limits=limits)
    assert decide_next(done,s).reason == CoordinatorReason.MAX_TURNS


def test_no_claimed_runtime_agents_means_wait_not_fake_dialogue():
    s=CoordinatorInput(observed_at_epoch=TS+1)
    d=decide_next(fixture(),s)
    assert d.action == CoordinatorAction.WAIT
    assert d.reason == CoordinatorReason.TASK_BLOCKED


def test_no_agents_waits_even_if_human_participates():
    d=decide_next(fixture(participants=()),state())
    assert d.reason == CoordinatorReason.NO_AGENTS
