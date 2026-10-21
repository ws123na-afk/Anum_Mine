"""Runtime safety tests with an explicit fake store, never a fake production DB."""
import asyncio
from datetime import datetime, timezone

import pytest

from anum_api.live_agent_meeting_domain import LiveMeeting, LiveMeetingLimits, LiveParticipant, LiveTurn, ParticipantKind, SessionStatus, append_turn
from anum_api.live_agent_meeting_coordinator import CoordinatorInput, CoordinatorAction
from anum_api.live_agent_meeting_runtime import GeneratedTurn, RuntimeBlocked, run_one_turn

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def meeting():
    return LiveMeeting(
        id="meet", tenant_id="tenant", workspace_id="workspace", title="Risk review",
        created_by="owner", status=SessionStatus.ACTIVE, started_at=NOW,
        participants=(LiveParticipant(id="agent", kind=ParticipantKind.AGENT, tenant_id="tenant", workspace_id="workspace", added_by="owner"),),
        limits=LiveMeetingLimits(max_tokens=3000, max_cost_usd=1.0)
    )


def state(**extra):
    return CoordinatorInput(
        observed_at_epoch=int(NOW.timestamp())+1,
        active_agent_ids=frozenset({"agent"}),
        reserved_input_tokens=100, reserved_output_tokens=100,
        reserved_cost_usd=0.15,
        **extra,
    )


class Store:
    def __init__(self, m):
        self.m=m
        self.reserved=False
        self.abandoned=0
        self.committed=0

    async def reserve_turn(self, reservation):
        if self.reserved or self.m.revision != reservation.expected_revision:
            return False
        self.reserved=True
        return True

    async def finish_turn(self, reservation, turn):
        self.m=append_turn(
            self.m, turn, tenant_id="tenant", workspace_id="workspace",
            expected_revision=reservation.expected_revision, now=NOW
        )
        self.committed+=1
        return self.m

    async def abandon_turn(self, reservation, reason):
        self.abandoned+=1
        self.reserved=False


class Gate:
    def __init__(self, answers=(True,True)):
        self.answers=iter(answers)
        self.calls=0

    async def can_agent_speak(self, **kwargs):
        self.calls+=1
        return next(self.answers,True)


class Generator:
    def __init__(self,response=None):
        self.calls=0
        self.response=response or GeneratedTurn("Verified result",10,12,0.01,"model:controlled")

    async def generate(self, **kwargs):
        self.calls+=1
        return self.response


async def execute(m,store,gate=None,generator=None,**extra):
    return await run_one_turn(
        meeting=m,state=state(**extra),tenant_id="tenant",
        workspace_id="workspace",request_key="request-123",
        now=NOW,store=store,gate=gate or Gate(),generator=generator or Generator()
    )


def test_success_commits_only_one_real_turn():
    async def run():
        m=meeting();store=Store(m);generator=Generator()
        result=await execute(m,store,generator=generator)
        assert result.decision.action == CoordinatorAction.SPEAK
        assert result.committed is not None
        assert result.committed.turns[-1].body == "Verified result"
        assert result.committed.total_cost_usd == pytest.approx(0.01)
        assert store.committed == 1
        assert generator.calls == 1
    asyncio.run(run())


def test_approval_and_emergency_stop_fail_closed_before_model_call():
    async def run():
        for extra in ({"approval_pending":True},{"emergency_stop":True}):
            m=meeting();store=Store(m);generator=Generator()
            result=await execute(m,store,generator=generator,**extra)
            assert result.decision.action != CoordinatorAction.SPEAK
            assert generator.calls==0
            assert store.committed==0
    asyncio.run(run())


def test_permission_revocation_after_reservation_cancels_call():
    async def run():
        m=meeting();store=Store(m);generator=Generator()
        with pytest.raises(RuntimeBlocked,match="agent_not_authorized"):
            await execute(m,store,gate=Gate((True,False)),generator=generator)
        assert generator.calls==0
        assert store.abandoned==1
    asyncio.run(run())


def test_excess_usage_or_missing_provenance_never_commits():
    async def run():
        values=(GeneratedTurn("text",101,0,0.01,"model:valid"),GeneratedTurn("text",2,2,0.16,"model:valid"),
                GeneratedTurn("text",2,2,0.01,""),GeneratedTurn("",2,2,0.01,"model:valid"))
        for value in values:
            m=meeting();store=Store(m)
            with pytest.raises(RuntimeBlocked):
                await execute(m,store,generator=Generator(value))
            assert store.committed==0
            assert store.abandoned==1
    asyncio.run(run())


def test_concurrent_reservations_accept_one_turn_only():
    async def run():
        m=meeting();store=Store(m)
        async def worker(key):
            try:
                return await run_one_turn(meeting=m,state=state(),tenant_id="tenant",
                    workspace_id="workspace",request_key=key,now=NOW,
                    store=store,gate=Gate(),generator=Generator())
            except RuntimeBlocked as exc:
                return exc.reason
        results=await asyncio.gather(worker("key-a"),worker("key-b"))
        assert sum(hasattr(x,"committed") for x in results)==1
        assert "turn_already_claimed" in results
        assert store.committed==1
    asyncio.run(run())


def test_cross_scope_rejected_before_storage():
    async def run():
        m=meeting();store=Store(m)
        with pytest.raises(RuntimeBlocked,match="scope_mismatch"):
            await run_one_turn(meeting=m,state=state(),tenant_id="other",workspace_id="workspace",
                request_key="key",now=NOW,store=store,gate=Gate(),generator=Generator())
        assert not store.reserved
    asyncio.run(run())
