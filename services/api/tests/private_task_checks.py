"""Checks every repository backend must pass for private tasks (docs/tasks.md#private-tasks).

``test_private_tasks.py`` runs them against the in-memory repository and
``test_postgres_private_tasks.py`` against PostgreSQL, so the two backends cannot drift apart:
an event is stamped ``private_to`` when it is recorded, the stamp follows the task when its marker
changes, and the activity list, the event stream cursor reads, automation triggers
(``events_since``) and the event-bus filter all read that one stamp.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import Any

from anum_api.schemas import DomainEvent, Task, TaskStatus, TenantContext, utc_now

AsUser = Callable[[str], AbstractContextManager[Any]]
EventsSince = Callable[[str, str], list[DomainEvent]]


def _event(task: Task, event_id: str, event_type: str = "task.created") -> DomainEvent:
    return DomainEvent(
        id=event_id, type=event_type, tenant_id=task.tenant_id, workspace_id=task.workspace_id, subject=task.id,
        correlation_id=task.id, created_at=utc_now(), payload={"title": task.title},
    )


def check_events_follow_the_task(
    as_user: AsUser,
    events_since: EventsSince,
    context_for: Callable[[str], TenantContext],
    task_id: str,
) -> None:
    """``as_user(user)`` yields a repository whose session (if any) runs as ``user``;
    ``events_since(user, event_type)`` reads through the automation effects of that backend."""
    alice, omar = context_for("alice"), context_for("omar")
    now = utc_now()
    with as_user("alice") as repository:
        task = repository.create_task(Task(
            id=task_id, title="Balance question", prompt="What is my balance?", status=TaskStatus.CREATED,
            tenant_id=alice.tenant_id, workspace_id=alice.workspace_id, created_at=now, updated_at=now, created_by="alice",
        ))
        first = repository.record_event(_event(task, f"evt_{task_id}_1"))
        assert first.private_to is None  # a workspace task so far: on the bus, visible to everyone
    with as_user("omar") as repository:
        assert [event.id for event in repository.list_events(omar)] == [first.id]
        assert repository.latest_event_id(omar) == first.id
    assert [event.id for event in events_since("omar", "task.created")] == [first.id]

    # The task turns private: its earlier event follows it, and the next one is stamped at record time.
    with as_user("alice") as repository:
        task = repository.get_task(task_id, alice)
        assert task is not None
        task.private_to, task.private_kind = "alice", "data"
        repository.save_task(task)
        second = repository.record_event(_event(task, f"evt_{task_id}_2", "agent_run.completed"))
        assert second.private_to == "alice"
    with as_user("omar") as repository:
        assert repository.list_events(omar) == [] and repository.list_events_after(omar, first.id) == []
        assert repository.latest_event_id(omar) is None and repository.list_recent_events(omar, 10) == []
    # A private task's events never start a workspace automation, not even for its person.
    assert events_since("omar", "task.created") == [] and events_since("alice", "task.created") == []
    assert events_since("alice", "agent_run.completed") == []
    with as_user("alice") as repository:
        events = repository.list_events(alice)
        assert [event.id for event in events] == [first.id, second.id]
        assert {event.private_to for event in events} == {"alice"}  # the stamp the bus filter reads
        assert [event.id for event in repository.list_events_after(alice, first.id)] == [second.id]


def since(after_days: int = 1) -> Any:
    """The ``after`` cursor ``events_since`` callers pass: a little while ago."""
    return utc_now() - timedelta(days=after_days)


def check_the_tasks_person_decides_a_private_approval(client: Any, headers_for: Callable[[str], dict[str, str]], balance: str) -> None:
    """Finding of the November 2026 review: a caller-scoped tool whose call needs an approval left the
    task data-private, so the approval was visible to its creator only and a non-owner creator could
    never decide it. The task's own person now decides it (the one allowed self-approval), the data
    stays with them, and the run proceeds. ``headers_for`` names: ``org_admin`` (an owner who is also an
    organization administrator), ``alice`` (owner), ``mona`` (member), ``omar`` (owner), ``vic`` (viewer)."""
    rule = client.post("/api/v1/organization/approval-rules", headers=headers_for("org_admin"),
                       json={"name": "Finance needs a look", "action_pattern": "finance.*"})
    assert rule.status_code == 201, rule.text
    mona, others = headers_for("mona"), [headers_for(name) for name in ("alice", "omar", "vic")]
    account = client.post("/api/v1/finance/accounts", headers=mona,
                          json={"name": "Salary", "kind": "bank", "currency": "AED", "opening_balance": "98765.43"})
    assert account.status_code == 201, account.text
    task = client.post("/api/v1/tasks", headers=mona, json={"title": "Money", "prompt": "What is my account balance and what did I spend this month?"})
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]
    paused = client.post(f"/api/v1/tasks/{task_id}/run", headers=mona)
    assert paused.status_code == 200, paused.text
    body = paused.json()
    assert body["run"]["status"] == "waiting_approval" and body["task"]["private_to"] == mona["x-user-id"], body["task"]
    approval = body["approval"]
    assert approval is not None and approval["status"] == "pending" and approval["action"].startswith("finance.")
    assert balance not in paused.text

    # Nobody else sees the approval or the task: owners, viewers, the approvals list, the task routes.
    for person in others:
        assert client.get(f"/api/v1/approvals/{approval['id']}", headers=person).status_code == 404, person["x-user-id"]
        assert approval["id"] not in client.get("/api/v1/approvals", headers=person).text
        assert client.get(f"/api/v1/tasks/{task_id}", headers=person).status_code == 404
        decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=person, json={"payload_hash": approval["payload_hash"]})
        assert decided.status_code == 404, (person["x-user-id"], decided.text)

    # The person: listed as the one approver, then the decision runs the tool.
    listed = client.get("/api/v1/approvals", headers=mona)
    assert listed.status_code == 200, listed.text
    mine = next(item for item in listed.json() if item["id"] == approval["id"])
    assert mine["routing"]["can_approve"] is True and mine["routing"]["can_reject"] is True and mine["routing"]["refusal"] is None
    assert mine["routing"]["steps"] == [{"approvers": [[f"user:{mona['x-user-id']}"]], "approved_by": None}]
    assert "your own data" in mine["routing"]["summary"]
    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=mona, json={"payload_hash": approval["payload_hash"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["status"] == "approved" and approved.json()["approval"]["decided_by"] == mona["x-user-id"]
    assert approved.json()["run"]["status"] == "completed" and balance in approved.text
    assert client.get(f"/api/v1/tasks/{task_id}", headers=mona).json()["private_to"] == mona["x-user-id"]
    for person in others:
        assert client.get(f"/api/v1/tasks/{task_id}/runs", headers=person).status_code == 404
        assert balance not in client.get("/api/v1/approvals", headers=person).text
    # The shared audit log says the person decided their own request, and carries no content.
    exported = client.get("/api/v1/audit/export", headers=headers_for("alice"))
    assert exported.status_code == 200, exported.text
    decisions = [row for row in exported.json() if row["action"] == "approval.approved" and row["target"] == approval["id"]]
    assert len(decisions) == 1 and decisions[0]["metadata"]["private_self_decision"] is True and decisions[0]["actor"] == mona["x-user-id"]
    assert balance not in exported.text

    # An owner under the two-person rule: the rule asks for another owner, but nobody else can see a
    # private approval, so the person decides this one too (every other approval still needs two people).
    alice = headers_for("alice")
    policy = client.put("/api/v1/approval-policy", headers=alice, json={"two_person_rule": True, "medium_risk_requires_approval": False})
    assert policy.status_code == 200, policy.text
    account = client.post("/api/v1/finance/accounts", headers=alice,
                          json={"name": "Salary", "kind": "bank", "currency": "AED", "opening_balance": "98765.43"})
    assert account.status_code == 201, account.text
    own = client.post("/api/v1/tasks", headers=alice, json={"title": "Money", "prompt": "What is my account balance and what did I spend this month?"}).json()
    paused = client.post(f"/api/v1/tasks/{own['id']}/run", headers=alice).json()
    assert paused["run"]["status"] == "waiting_approval"
    approved = client.post(f"/api/v1/approvals/{paused['approval']['id']}/approve", headers=alice, json={"payload_hash": paused["approval"]["payload_hash"]})
    assert approved.status_code == 200 and approved.json()["run"]["status"] == "completed" and balance in approved.text, approved.text
    assert client.get(f"/api/v1/tasks/{own['id']}", headers=headers_for("omar")).status_code == 404
