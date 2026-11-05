"""Presence projection for the ANUM MINE office (master plan F03).

An agent is never shown busy without a persisted record that says so. ``load_evidence``
reads the records of ONE workspace inside that workspace's RLS context (the caller opens
the session); ``derive_presence`` is a pure function from evidence to per-agent state, so
duplicate or out-of-order events cannot change the answer: the stream only uses events as
a hint to re-read, never as the state itself.

States: working, queued, meeting, waiting_approval, blocked, idle, paused, unknown.
Every item says why (``provenance``), when the evidence was last verified and whether it
is stale. See docs/ANUM_MINE_PRESENCE_AND_CAMPUS.md.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from .schemas import TenantContext

PRESENCE_STATES = (
    "working", "queued", "meeting", "waiting_approval", "blocked", "idle", "paused", "unknown",
)
PresenceState = Literal[
    "working", "queued", "meeting", "waiting_approval", "blocked", "idle", "paused", "unknown"
]
# Evidence older than this is flagged stale (the client greys it out).
STALE_AFTER = timedelta(minutes=5)
# A "running" record silent for this long no longer proves work: the state becomes unknown.
RUNNING_MAX_SILENCE = timedelta(hours=1)
# A failed task blocks its agent for this long; after that the agent reads as idle again.
BLOCKED_WINDOW = timedelta(hours=24)
MAX_EVIDENCE_ROWS = 2000
RELEVANT_EVENT_PREFIXES = ("task.", "agent_run.", "approval.", "agent_meeting.", "meeting.", "office.")


@dataclass(frozen=True)
class AgentEvidence:
    agent_id: str
    name: str
    status: str  # agent_profiles.status: active | paused
    updated_at: datetime


@dataclass(frozen=True)
class DeskEvidence:
    desk_id: str
    room_id: str
    agent_id: str | None  # None when empty or when the agent is not visible to the viewer


@dataclass(frozen=True)
class TaskEvidence:
    task_id: str
    agent_id: str
    title: str
    status: str
    updated_at: datetime


@dataclass(frozen=True)
class RunEvidence:
    run_id: str
    task_id: str
    status: str
    phase: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ApprovalEvidence:
    approval_id: str
    task_id: str
    run_id: str | None
    expires_at: datetime | None
    created_at: datetime
    status: str = "pending"


@dataclass(frozen=True)
class MeetingEvidence:
    meeting_id: str
    agent_id: str
    updated_at: datetime


@dataclass
class Evidence:
    as_of: datetime
    agents: list[AgentEvidence] = field(default_factory=list)
    desks: list[DeskEvidence] = field(default_factory=list)
    tasks: list[TaskEvidence] = field(default_factory=list)
    runs: list[RunEvidence] = field(default_factory=list)
    approvals: list[ApprovalEvidence] = field(default_factory=list)
    meetings: list[MeetingEvidence] = field(default_factory=list)
    cursor: str | None = None


def _iso(value: datetime) -> str:
    return value.isoformat()


def _item(
    key: str, agent_id: str | None, desk_id: str | None, state: str, kind: str, *,
    verified: datetime, now: datetime, name: str | None = None, reason: str | None = None,
    task: TaskEvidence | None = None, run_id: str | None = None,
    approval_id: str | None = None, meeting_id: str | None = None,
    stale_exempt: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "agent_id": agent_id,
        "agent_name": name,
        "desk_id": desk_id,
        "state": state,
        "provenance": {
            "kind": kind,
            "run_id": run_id,
            "task_id": task.task_id if task else None,
            "approval_id": approval_id,
            "meeting_id": meeting_id,
            "reason": reason,
        },
        "task": {"id": task.task_id, "title": task.title[:160]} if task else None,
        "last_verified_at": _iso(verified),
        "stale": (not stale_exempt) and (now - verified) > STALE_AFTER,
    }


def derive_presence(evidence: Evidence) -> list[dict[str, Any]]:
    """One item per visible agent, plus one ``unknown`` item per desk whose agent is hidden.

    Priority: paused, meeting, waiting_approval, working, blocked, queued, idle.
    """
    now = evidence.as_of
    desk_of = {d.agent_id: d.desk_id for d in evidence.desks if d.agent_id}
    tasks_by_agent: dict[str, list[TaskEvidence]] = {}
    for task in evidence.tasks:
        tasks_by_agent.setdefault(task.agent_id, []).append(task)
    run_of = {r.task_id: r for r in evidence.runs}
    pending: dict[str, ApprovalEvidence] = {}
    for a in evidence.approvals:  # newest first: keep the newest per task
        pending.setdefault(a.task_id, a)
    meeting_of = {m.agent_id: m for m in evidence.meetings}

    items: list[dict[str, Any]] = []
    for agent in evidence.agents:
        desk = desk_of.get(agent.agent_id)
        common = {"name": agent.name, "now": now}
        if agent.status == "paused":
            items.append(_item(agent.agent_id, agent.agent_id, desk, "paused", "agent",
                               verified=agent.updated_at, reason="agent_paused", **common))
            continue
        meeting = meeting_of.get(agent.agent_id)
        if meeting:
            items.append(_item(agent.agent_id, agent.agent_id, desk, "meeting", "meeting",
                               verified=meeting.updated_at, meeting_id=meeting.meeting_id, **common))
            continue
        tasks = sorted(tasks_by_agent.get(agent.agent_id, []), key=lambda t: (t.updated_at, t.task_id), reverse=True)
        chosen = _from_tasks(agent, desk, tasks, run_of, pending, now)
        items.append(chosen or _item(
            agent.agent_id, agent.agent_id, desk, "idle", "none", verified=now,
            reason="no_active_record", stale_exempt=True, **common,
        ))
    visible = {a.agent_id for a in evidence.agents}
    for desk in evidence.desks:
        if desk.agent_id is None or desk.agent_id in visible:
            continue
        items.append(_item(f"desk:{desk.desk_id}", None, desk.desk_id, "unknown", "none", verified=now,
                           now=now, reason="agent_not_visible", stale_exempt=True))
    items.sort(key=lambda i: i["key"])
    return items


def _from_tasks(agent: AgentEvidence, desk: str | None, tasks: list[TaskEvidence],
                run_of: dict[str, RunEvidence], pending: dict[str, ApprovalEvidence],
                now: datetime) -> dict[str, Any] | None:
    common = {"name": agent.name, "now": now}
    for task in tasks:
        if task.status != "waiting_approval":
            continue
        approval = pending.get(task.task_id)
        run = run_of.get(task.task_id)
        live = approval is not None and approval.status == "pending" and (
            approval.expires_at is None or approval.expires_at > now)
        if approval and live:
            return _item(agent.agent_id, agent.agent_id, desk, "waiting_approval", "approval",
                         verified=approval.created_at, task=task, run_id=approval.run_id or (run.run_id if run else None),
                         approval_id=approval.approval_id, **common)
        # Waiting with no live approval (expired or missing): the agent cannot continue.
        return _item(agent.agent_id, agent.agent_id, desk, "blocked", "approval",
                     verified=approval.expires_at if approval and approval.expires_at else task.updated_at,
                     task=task, run_id=run.run_id if run else None,
                     approval_id=approval.approval_id if approval else None,
                     reason="approval_expired" if approval else "approval_missing", **common)
    for task in tasks:
        if task.status != "running":
            continue
        run = run_of.get(task.task_id)
        if run is None or run.status != "running":
            continue  # a running task without a running run proves nothing
        if now - run.updated_at > RUNNING_MAX_SILENCE:
            return _item(agent.agent_id, agent.agent_id, desk, "unknown", "run", verified=run.updated_at,
                         task=task, run_id=run.run_id, reason="run_silent", **common)
        return _item(agent.agent_id, agent.agent_id, desk, "working", "run", verified=run.updated_at,
                     task=task, run_id=run.run_id, **common)
    for task in tasks:
        if task.status == "failed" and now - task.updated_at <= BLOCKED_WINDOW:
            run = run_of.get(task.task_id)
            return _item(agent.agent_id, agent.agent_id, desk, "blocked", "run", verified=task.updated_at,
                         task=task, run_id=run.run_id if run else None, reason="run_failed", **common)
    for task in tasks:
        if task.status == "queued":
            return _item(agent.agent_id, agent.agent_id, desk, "queued", "task", verified=task.updated_at,
                         task=task, **common)
    return None


def totals(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(i["state"] for i in items)
    return {state: counts.get(state, 0) for state in PRESENCE_STATES}


def diff_presence(previous: dict[str, dict[str, Any]], current: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Changed or new items, and keys that vanished. ``last_verified_at`` of idle items moves
    with every read, so it is ignored when comparing."""
    def signature(item: dict[str, Any]) -> tuple:
        verified = None if item["state"] == "idle" else item["last_verified_at"]
        return (item["state"], item["stale"], item["agent_name"], item["desk_id"],
                tuple(sorted(item["provenance"].items(), key=lambda kv: kv[0])), verified,
                item["task"]["id"] if item["task"] else None)

    changed = [i for i in current if i["key"] not in previous or signature(previous[i["key"]]) != signature(i)]
    gone = sorted(set(previous) - {i["key"] for i in current})
    return changed, gone


def is_relevant_event(event_type: str) -> bool:
    return event_type.startswith(RELEVANT_EVENT_PREFIXES)


def load_evidence(session: Session, context: TenantContext, now: datetime) -> Evidence:
    """Read presence evidence for the context's workspace. The session must already carry
    the tenant, workspace and user RLS settings; every query repeats the scope predicate."""
    p = {"t": context.tenant_id, "w": context.workspace_id}
    evidence = Evidence(as_of=now)
    for row in session.execute(text(
        "select id, name, status, updated_at from agent_profiles "
        "where tenant_id = :t and workspace_id = :w order by id limit :n"), {**p, "n": MAX_EVIDENCE_ROWS}):
        evidence.agents.append(AgentEvidence(row.id, row.name, row.status, row.updated_at))
    visible = {a.agent_id for a in evidence.agents}
    for row in session.execute(text(
        "select id, room_id, agent_id from office_desks "
        "where tenant_id = :t and workspace_id = :w order by id limit :n"), {**p, "n": MAX_EVIDENCE_ROWS}):
        evidence.desks.append(DeskEvidence(row.id, row.room_id, row.agent_id))
    # tasks of any agent: pending work, running work and recent failures
    task_rows = session.execute(text(
        "select id, agent_id, title, status, updated_at from tasks "
        "where tenant_id = :t and workspace_id = :w and agent_id is not null and archived_at is null "
        "and (status in ('queued', 'running', 'waiting_approval') "
        "     or (status = 'failed' and updated_at > :since)) "
        "order by updated_at desc, id desc limit :n"),
        {**p, "since": now - BLOCKED_WINDOW, "n": MAX_EVIDENCE_ROWS}).all()
    evidence.tasks = [TaskEvidence(r.id, r.agent_id, r.title, r.status, r.updated_at)
                      for r in task_rows if r.agent_id in visible]
    task_ids = [t.task_id for t in evidence.tasks]
    if task_ids:
        for row in session.execute(text(
            "select distinct on (task_id) id, task_id, status, updated_at, checkpoint->>'phase' as phase "
            "from agent_runs where tenant_id = :t and workspace_id = :w and task_id = any(:ids) "
            "order by task_id, created_at desc, id desc"), {**p, "ids": task_ids}):
            evidence.runs.append(RunEvidence(row.id, row.task_id, row.status, row.phase, row.updated_at))
        for row in session.execute(text(
            "select id, task_id, run_id, expires_at, created_at, status from approvals "
            "where tenant_id = :t and workspace_id = :w and status in ('pending', 'expired') "
            "and task_id = any(:ids) order by created_at desc, id desc"), {**p, "ids": task_ids}):
            evidence.approvals.append(
                ApprovalEvidence(row.id, row.task_id, row.run_id, row.expires_at, row.created_at, row.status))
    for row in session.execute(text(
        "select s.id, pa.actor_id, s.updated_at from agent_meeting_sessions s "
        "join agent_meeting_participants pa on pa.tenant_id = s.tenant_id and pa.workspace_id = s.workspace_id "
        "and pa.session_id = s.id and pa.left_at is null "
        "where s.tenant_id = :t and s.workspace_id = :w and s.status = 'active' and pa.kind = 'agent' "
        "order by s.id limit :n"), {**p, "n": MAX_EVIDENCE_ROWS}):
        if row.actor_id in visible:
            evidence.meetings.append(MeetingEvidence(row.id, row.actor_id, row.updated_at))
    cursor = session.execute(text(
        "select id from domain_events where tenant_id = :t and workspace_id = :w "
        "order by created_at desc, id desc limit 1"), p).scalar()
    evidence.cursor = cursor
    return evidence
