"""Presence derivation and the delta stream, without a database.

The derivation is a pure function of persisted records: no record, no busy state.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from anum_api import office_presence as op
from anum_api.executive_api import GrantCreate, grant_payload_hash
from anum_api.office_presence_api import presence_frames

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def agent(agent_id: str = "a1", status: str = "active", name: str = "Ada") -> op.AgentEvidence:
    return op.AgentEvidence(agent_id, name, status, NOW - timedelta(days=1))


def task(task_id: str, status: str, agent_id: str = "a1", age: int = 30) -> op.TaskEvidence:
    return op.TaskEvidence(task_id, agent_id, f"Task {task_id}", status, NOW - timedelta(seconds=age))


def run(task_id: str, status: str, age: int = 30) -> op.RunEvidence:
    return op.RunEvidence(f"run_{task_id}", task_id, status, None, NOW - timedelta(seconds=age))


def derive(**parts) -> dict:
    evidence = op.Evidence(as_of=NOW, **parts)
    return {i["key"]: i for i in op.derive_presence(evidence)}


def test_agent_without_any_record_is_idle_and_never_busy() -> None:
    items = derive(agents=[agent()])
    item = items["a1"]
    assert item["state"] == "idle"
    assert item["provenance"]["kind"] == "none" and item["task"] is None
    assert item["stale"] is False


def test_running_task_with_running_run_is_working_with_provenance() -> None:
    items = derive(agents=[agent()], tasks=[task("t1", "running")], runs=[run("t1", "running")])
    item = items["a1"]
    assert item["state"] == "working"
    assert item["provenance"]["run_id"] == "run_t1" and item["provenance"]["task_id"] == "t1"
    assert item["task"] == {"id": "t1", "title": "Task t1"}
    assert item["stale"] is False


def test_running_task_without_a_running_run_proves_nothing() -> None:
    assert derive(agents=[agent()], tasks=[task("t1", "running")])["a1"]["state"] == "idle"
    assert derive(agents=[agent()], tasks=[task("t1", "running")], runs=[run("t1", "completed")])["a1"]["state"] == "idle"


def test_queued_task_is_queued() -> None:
    item = derive(agents=[agent()], tasks=[task("t1", "queued")])["a1"]
    assert item["state"] == "queued" and item["provenance"]["kind"] == "task"


def test_live_approval_wait_and_expired_approval_block() -> None:
    waiting = op.ApprovalEvidence("ap1", "t1", "run_t1", NOW + timedelta(minutes=5), NOW - timedelta(seconds=10))
    item = derive(agents=[agent()], tasks=[task("t1", "waiting_approval")], approvals=[waiting])["a1"]
    assert item["state"] == "waiting_approval" and item["provenance"]["approval_id"] == "ap1"
    expired = op.ApprovalEvidence("ap1", "t1", "run_t1", NOW - timedelta(minutes=1), NOW - timedelta(hours=1))
    item = derive(agents=[agent()], tasks=[task("t1", "waiting_approval")], approvals=[expired])["a1"]
    assert item["state"] == "blocked" and item["provenance"]["reason"] == "approval_expired"
    status_expired = op.ApprovalEvidence("ap1", "t1", None, None, NOW - timedelta(hours=1), "expired")
    assert derive(agents=[agent()], tasks=[task("t1", "waiting_approval")], approvals=[status_expired])["a1"]["state"] == "blocked"
    missing = derive(agents=[agent()], tasks=[task("t1", "waiting_approval")])["a1"]
    assert missing["state"] == "blocked" and missing["provenance"]["reason"] == "approval_missing"


def test_cancelled_and_completed_work_is_not_busy() -> None:
    # The loader never selects them, and even if present they prove nothing.
    items = derive(agents=[agent()], tasks=[task("t1", "cancelled"), task("t2", "completed")], runs=[run("t1", "cancelled")])
    assert items["a1"]["state"] == "idle"


def test_failed_run_blocks_for_a_day_then_idle() -> None:
    item = derive(agents=[agent()], tasks=[task("t1", "failed", age=60)], runs=[run("t1", "failed")])["a1"]
    assert item["state"] == "blocked" and item["provenance"]["reason"] == "run_failed"
    old = derive(agents=[agent()], tasks=[task("t1", "failed", age=60 * 60 * 25)])["a1"]
    assert old["state"] == "idle"


def test_paused_agent_and_meeting_win_over_task_records() -> None:
    items = derive(agents=[agent(status="paused"), agent("a2", name="Bo")],
                   tasks=[task("t1", "running"), task("t2", "running", "a2")],
                   runs=[run("t1", "running"), run("t2", "running")],
                   meetings=[op.MeetingEvidence("m1", "a2", NOW - timedelta(seconds=5))])
    assert items["a1"]["state"] == "paused"
    assert items["a2"]["state"] == "meeting" and items["a2"]["provenance"]["meeting_id"] == "m1"


def test_stale_flag_and_silent_run_becomes_unknown() -> None:
    item = derive(agents=[agent()], tasks=[task("t1", "running")], runs=[run("t1", "running", age=600)])["a1"]
    assert item["state"] == "working" and item["stale"] is True
    silent = derive(agents=[agent()], tasks=[task("t1", "running")], runs=[run("t1", "running", age=7200)])["a1"]
    assert silent["state"] == "unknown" and silent["provenance"]["reason"] == "run_silent"


def test_desk_with_hidden_agent_is_unknown_not_idle() -> None:
    items = derive(desks=[op.DeskEvidence("d1", "r1", "private_agent"), op.DeskEvidence("d2", "r1", None)])
    assert list(items) == ["desk:d1"]
    assert items["desk:d1"]["state"] == "unknown" and items["desk:d1"]["agent_id"] is None


def test_totals_cover_every_state() -> None:
    items = op.derive_presence(op.Evidence(as_of=NOW, agents=[agent(), agent("a2")], tasks=[task("t1", "queued")]))
    counts = op.totals(items)
    assert set(counts) == set(op.PRESENCE_STATES)
    assert counts["queued"] == 1 and counts["idle"] == 1 and sum(counts.values()) == 2


def test_diff_ignores_idle_clock_but_reports_real_changes() -> None:
    a = op.derive_presence(op.Evidence(as_of=NOW, agents=[agent()]))
    b = op.derive_presence(op.Evidence(as_of=NOW + timedelta(seconds=9), agents=[agent()]))
    assert op.diff_presence({i["key"]: i for i in a}, b) == ([], [])
    c = op.derive_presence(op.Evidence(as_of=NOW + timedelta(seconds=9), agents=[agent()], tasks=[task("t1", "queued")]))
    changed, gone = op.diff_presence({i["key"]: i for i in a}, c)
    assert [i["state"] for i in changed] == ["queued"] and gone == []
    changed, gone = op.diff_presence({i["key"]: i for i in c}, [])
    assert changed == [] and gone == ["a1"]


# ---- stream --------------------------------------------------------------------------


class Script:
    """Scripted snapshots and events for the stream."""

    def __init__(self, states: list[list[op.AgentEvidence]], events: list[list[SimpleNamespace]]):
        self.states, self.events = states, events
        self.loads = 0
        self.polls = 0
        self.disconnect_after = len(events)

    def load(self) -> dict:
        index = min(self.loads, len(self.states) - 1)
        self.loads += 1
        evidence = op.Evidence(as_of=NOW + timedelta(seconds=self.loads), agents=[agent()],
                               tasks=self.states[index], cursor="evt_0")
        items = op.derive_presence(evidence)
        return {"version": 1, "workspace_id": "w", "as_of": evidence.as_of.isoformat(),
                "cursor": evidence.cursor, "totals": op.totals(items), "items": items}

    def poll(self, cursor):
        batch = self.events[self.polls] if self.polls < len(self.events) else []
        self.polls += 1
        return batch

    async def disconnected(self) -> bool:
        return self.polls >= self.disconnect_after


def ev(event_id: str, event_type: str) -> SimpleNamespace:
    return SimpleNamespace(id=event_id, type=event_type)


async def collect(script: Script, **kwargs) -> list[str]:
    async def no_sleep(_: float) -> None:
        return None

    return [f async for f in presence_frames(
        load_snapshot=script.load, poll_events=script.poll, is_disconnected=script.disconnected,
        sleep=no_sleep, **kwargs)]


def parse(frame: str) -> tuple[str, dict]:
    lines = dict(line.split(": ", 1) for line in frame.strip().split("\n") if ": " in line)
    return lines["event"], json.loads(lines["data"])


def test_stream_sends_snapshot_then_only_real_changes() -> None:
    script = Script(
        states=[[], [task("t1", "queued")], [task("t1", "queued")]],
        events=[[ev("evt_1", "task.queued")], [ev("evt_1", "task.queued")], [ev("evt_2", "task.created")]],
    )
    frames = asyncio.run(collect(script))
    kinds = [parse(f)[0] for f in frames if not f.startswith(":")]
    assert kinds == ["snapshot", "delta"]  # duplicate and irrelevant events add nothing
    event, data = parse(frames[1])
    assert data["changed"][0]["state"] == "queued" and data["sequence"] == 1
    assert frames[0].startswith("id: evt_0\n")


def test_stream_out_of_order_events_still_converge_on_record_state() -> None:
    script = Script(
        states=[[], [task("t1", "queued")], [task("t1", "queued")]],
        events=[[ev("evt_9", "agent_run.started")], [ev("evt_3", "task.queued")]],
    )
    frames = asyncio.run(collect(script))
    deltas = [parse(f)[1] for f in frames if f.startswith("id:") and "event: delta" in f]
    assert len(deltas) == 1 and deltas[0]["changed"][0]["agent_id"] == "a1"


def test_stream_stops_when_the_client_disconnects_and_reloads_nothing_more() -> None:
    script = Script(states=[[]], events=[])
    frames = asyncio.run(collect(script))
    assert len(frames) == 1 and script.loads == 1 and script.polls == 0


def test_stream_resync_after_reconnect_starts_with_a_full_snapshot() -> None:
    script = Script(states=[[task("t1", "queued")]], events=[])
    frames = asyncio.run(collect(script, last_event_id="evt_7"))
    event, data = parse(frames[0])
    assert event == "snapshot" and data["items"][0]["state"] == "queued"


# ---- executive hash ------------------------------------------------------------------


def test_grant_hash_is_stable_and_binds_every_field() -> None:
    base = dict(tenant_id="t", workspace_id="w", grantee_user_id="u", scopes=["tasks", "counts"], expires_at=None)
    first = grant_payload_hash(**base)
    assert first == grant_payload_hash(**{**base, "scopes": ["counts", "tasks"]})
    assert len(first) == 64
    for change in ({"workspace_id": "w2"}, {"tenant_id": "t2"}, {"grantee_user_id": "u2"}, {"scopes": ["counts"]},
                   {"expires_at": NOW}):
        assert grant_payload_hash(**{**base, **change}) != first


def test_grant_body_is_strict() -> None:
    with pytest.raises(Exception):
        GrantCreate.model_validate({"grantee_user_id": "u", "scopes": ["salaries"]})
    with pytest.raises(Exception):
        GrantCreate.model_validate({"grantee_user_id": "u", "scopes": ["counts", "counts"]})
    with pytest.raises(Exception):
        GrantCreate.model_validate({"grantee_user_id": "u", "scopes": ["counts"], "tenant_id": "other"})
