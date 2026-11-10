"""Private tasks (docs/tasks.md#private-tasks), memory backend.

Security audit, November 2026: an owner's finance answer was readable by every workspace
member, viewers included, through the task a private chat question becomes and through any
task whose run called a tool that reads the caller's own data. A task can now be private to
its creator; every read path returns it, its runs, approvals and events to that person only.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from anum_api.dependencies import memory_repository
from anum_api.event_bus import EventBusRuntime
from anum_api.model_gateway import MockModelGateway
from anum_api.push import intent_for, resolve_recipients
from anum_api.repository import InMemoryRepository
from anum_api.runtime import AgentRuntime
from anum_api.schemas import (
    DomainEvent,
    RiskLevel,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from anum_api.store import InMemoryStore
from anum_api.tenant_lifecycle import MemoryTenantData
from anum_api.tenant_lifecycle_tables import BY_NAME

BALANCE = "98,765.43"
QUESTION = "What is my account balance and what did I spend this month?"

client = TestClient(main.app)


def _headers(tenant: str, user: str, role: str) -> dict[str, str]:
    return {"x-tenant-id": f"tenant_{tenant}", "x-workspace-id": f"workspace_{tenant}", "x-user-id": user, "x-user-roles": role}


def _people(tenant: str) -> dict[str, dict[str, str]]:
    return {
        "alice": _headers(tenant, "alice_owner", "owner"),
        "omar": _headers(tenant, "omar_owner", "owner"),
        "mona": _headers(tenant, "mona_member", "member"),
        "vic": _headers(tenant, "vic_viewer", "viewer"),
    }


def _finance_account(owner: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/finance/accounts", headers=owner,
        json={"name": "Salary", "kind": "bank", "currency": "AED", "opening_balance": "98765.43"},
    )
    assert created.status_code == 201, created.text


def _asked_as_task(owner: dict[str, str]) -> str:
    task = client.post("/api/v1/tasks", headers=owner, json={"title": "Money", "prompt": QUESTION})
    assert task.status_code == 201, task.text
    assert task.json()["private_to"] is None  # an ordinary workspace task until a run reads private data
    run = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=owner)
    assert run.status_code == 200, run.text
    assert BALANCE in json.dumps(run.json())
    return str(task.json()["id"])


def _asked_in_chat(owner: dict[str, str]) -> str:
    conversation = client.post("/api/v1/conversations", headers=owner, json={})
    assert conversation.status_code == 201, conversation.text
    ask = client.post(f"/api/v1/conversations/{conversation.json()['id']}/ask", headers=owner, json={"content": QUESTION})
    assert ask.status_code == 200 and BALANCE in ask.text
    tasks = client.get("/api/v1/tasks", headers=owner).json()
    assert len(tasks) == 1
    return str(tasks[0]["id"])


def _assert_hidden_from(person: dict[str, str], task_id: str) -> None:
    """Nothing of the task reaches ``person``: not its title, its runs, its events or a count."""
    for path in ("", "/runs", "/events", "/latest-run", "/export"):
        answer = client.get(f"/api/v1/tasks/{task_id}{path}", headers=person)
        assert answer.status_code == 404, (path, answer.status_code, answer.text[:200])
    listed = client.get("/api/v1/tasks", headers=person)
    assert listed.status_code == 200 and listed.json() == []
    searched = client.get("/api/v1/tasks", headers=person, params={"q": "balance"})
    assert searched.status_code == 200 and searched.json() == []
    summary = client.get("/api/v1/tasks/summary", headers=person).json()
    assert summary["total"] == 0 and summary["by_status"] == {} and summary["creators"] == []
    for path in ("/api/v1/events", "/api/v1/events/stream?follow=false", "/api/v1/approvals", "/api/v1/home/summary", "/api/v1/home/insights"):
        answer = client.get(path, headers=person)
        assert answer.status_code in {200, 403}, (path, answer.status_code)
        assert task_id not in answer.text and BALANCE not in answer.text and "account balance" not in answer.text, path
    # Writes answer like a task that does not exist.
    assert client.post(f"/api/v1/tasks/{task_id}/run", headers=person).status_code in {403, 404}
    assert client.post(f"/api/v1/tasks/{task_id}/retry", headers=person).status_code in {403, 404}
    assert client.post(f"/api/v1/tasks/{task_id}/archive", headers=person).status_code in {403, 404}
    assert client.put(f"/api/v1/tasks/{task_id}", headers=person, json={"title": "Mine now"}).status_code in {403, 404}


def _assert_creator_sees(owner: dict[str, str], task_id: str) -> None:
    task = client.get(f"/api/v1/tasks/{task_id}", headers=owner)
    assert task.status_code == 200 and task.json()["private_to"] == owner["x-user-id"]
    assert [item["id"] for item in client.get("/api/v1/tasks", headers=owner).json()] == [task_id]
    assert client.get("/api/v1/tasks/summary", headers=owner).json()["total"] == 1
    runs = client.get(f"/api/v1/tasks/{task_id}/runs", headers=owner)
    assert runs.status_code == 200 and BALANCE in runs.text
    events = client.get(f"/api/v1/tasks/{task_id}/events", headers=owner)
    assert events.status_code == 200 and {"task.created", "agent_run.completed"} <= {event["type"] for event in events.json()}
    assert task_id in client.get("/api/v1/events", headers=owner).text
    assert "private_to" not in client.get("/api/v1/events", headers=owner).text  # never on the wire


@pytest.mark.parametrize("asked", [_asked_as_task, _asked_in_chat], ids=["task", "chat"])
def test_a_finance_answer_is_read_by_its_creator_only(asked: Any) -> None:
    people = _people(f"private_{asked.__name__}")
    _finance_account(people["alice"])
    assert client.get("/api/v1/finance/accounts", headers=people["vic"]).status_code == 403
    task_id = asked(people["alice"])
    for name in ("vic", "mona", "omar"):
        _assert_hidden_from(people[name], task_id)
    _assert_creator_sees(people["alice"], task_id)


def test_a_chat_task_is_private_from_its_first_event() -> None:
    """The question is the task's title and rides in ``task.created``: nobody else may read it,
    whatever the answer turns out to need."""
    people = _people("private_chat_first")
    conversation = client.post("/api/v1/conversations", headers=people["alice"], json={}).json()
    client.post(f"/api/v1/conversations/{conversation['id']}/ask", headers=people["alice"], json={"content": "Search my email for the Riyadh tender"})
    mine = client.get("/api/v1/tasks", headers=people["alice"]).json()
    assert mine and all(task["private_to"] == "alice_owner" for task in mine)
    for name in ("vic", "mona", "omar"):
        assert client.get("/api/v1/tasks", headers=people[name]).json() == []
        assert "Riyadh tender" not in client.get("/api/v1/events", headers=people[name]).text


def test_another_persons_run_of_the_task_is_refused_and_stores_nothing_of_theirs() -> None:
    """A second owner who runs the creator's task would read their own ledger into a task the
    creator owns: the call is refused and the task stays a workspace task."""
    people = _people("private_other_runner")
    _finance_account(people["omar"])
    task = client.post("/api/v1/tasks", headers=people["alice"], json={"title": "Money", "prompt": QUESTION}).json()
    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=people["omar"])
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["task"]["status"] == "failed" and body["task"]["private_to"] is None
    assert BALANCE not in json.dumps(body) and "only the person who created the task" in json.dumps(body)
    assert BALANCE not in client.get(f"/api/v1/tasks/{task['id']}/runs", headers=people["alice"]).text


# -- the runtime and the repository ------------------------------------------------------------


def _task(context: TenantContext, **changes: Any) -> Task:
    now = utc_now()
    return Task(
        id=new_id("task"), title="Private mail", prompt="Read my private mailbox", status=TaskStatus.CREATED,
        tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, updated_at=now,
        created_by=context.user_id, **changes,
    )


def _context(user: str, role: str = "owner") -> TenantContext:
    return TenantContext(tenant_id="tenant_private_unit", workspace_id="workspace_private_unit", user_id=user, roles=[role])


def _registry(*, caller_scoped: bool = False, caller_private: bool = False) -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
        return ToolResult(status="succeeded", summary=f"SECRET of {context.user_id}", caller_private=caller_private)

    registry.register(
        ToolDefinition(name="anum.respond", description="test", risk_level=RiskLevel.LOW, idempotent=True, caller_scoped=caller_scoped),
        handler,
    )
    return registry


def test_a_caller_scoped_tool_makes_the_task_private_before_it_runs() -> None:
    repository = InMemoryRepository(InMemoryStore())
    alice, omar = _context("alice"), _context("omar")
    runtime = AgentRuntime(MockModelGateway(), repository, tools=_registry(caller_scoped=True))
    task = repository.create_task(_task(alice))
    decision = runtime.evaluate(ToolCall(name="anum.respond"), alice, task)
    assert decision.outcome.value == "allow"
    assert repository.store.tasks[task.id].private_to == "alice"  # saved at once, before any result exists
    assert repository.get_task(task.id, omar) is None and repository.get_task(task.id, alice) is not None

    # Not the creator: blocked, and the task is not handed to the runner.
    shared = repository.create_task(_task(alice))
    refused = runtime.evaluate(ToolCall(name="anum.respond"), omar, shared)
    assert refused.outcome.value == "block" and "only the person who created the task" in refused.reason
    assert repository.store.tasks[shared.id].private_to is None


def test_a_result_flagged_private_makes_the_task_private_in_the_unit_that_stores_it() -> None:
    repository = InMemoryRepository(InMemoryStore())
    alice, omar = _context("alice"), _context("omar")
    runtime = AgentRuntime(MockModelGateway(), repository, tools=_registry(caller_private=True))
    task = repository.create_task(_task(alice))
    run, _ = asyncio.run(runtime.run_task(task, alice))
    assert run.result == "SECRET of alice" and repository.store.tasks[task.id].private_to == "alice"
    assert repository.list_runs_for_task(task.id, omar) == [] and repository.get_run(run.id, omar) is None
    assert repository.list_task_events(task.id, omar) == [] and repository.list_events(omar) == []
    assert repository.list_events(alice)

    # Another person's run: their private result is dropped, the run fails, the task stays shared.
    shared = repository.create_task(_task(alice))
    run, _ = asyncio.run(runtime.run_task(shared, omar))
    assert run.status == TaskStatus.FAILED and "SECRET" not in run.model_dump_json()
    assert repository.store.tasks[shared.id].private_to is None


def test_the_marker_is_sticky_and_counts_leave_private_tasks_out() -> None:
    from anum_api.tasks import TaskFilters

    repository = InMemoryRepository(InMemoryStore())
    alice, omar = _context("alice"), _context("omar")
    private = repository.create_task(_task(alice, private_to="alice"))
    repository.create_task(_task(omar))
    stale = private.model_copy(update={"private_to": None, "title": "Edited"})
    assert repository.save_task(stale).private_to == "alice"  # a stale copy never clears it
    handed = private.model_copy(update={"private_to": "omar"})
    assert repository.save_task(handed).private_to == "alice"  # and it never moves to someone else
    assert [task.created_by for task in repository.list_tasks(omar)] == ["omar"]
    mine, theirs = repository.summarize_tasks(alice, TaskFilters()), repository.summarize_tasks(omar, TaskFilters())
    assert (mine.total, mine.creators) == (2, ["alice", "omar"])
    assert (theirs.total, theirs.creators) == (1, ["omar"])


def test_private_approvals_are_listed_and_decided_by_their_person_only() -> None:
    from anum_api.approval_paging import ApprovalFilter
    from anum_api.schemas import Approval, ApprovalStatus

    repository = InMemoryRepository(InMemoryStore())
    alice, omar = _context("alice"), _context("omar")
    task = repository.create_task(_task(alice, private_to="alice"))
    approval = repository.save_approval(Approval(
        id=new_id("approval"), task_id=task.id, action="finance.summary", risk_level=RiskLevel.HIGH, status=ApprovalStatus.PENDING,
        reason="Proposed action: read my balance", created_at=utc_now(), arguments={"month": "2026-11"},
    ))
    now = utc_now()
    for reader in (repository.get_approval, repository.get_approval_for_update):
        assert reader(approval.id, omar) is None and reader(approval.id, alice) is not None
    assert repository.list_approvals(omar) == [] and repository.list_task_approvals(task.id, omar) == []
    assert repository.summarize_approvals(omar, now).pending == 0 and repository.summarize_approvals(alice, now).pending == 1
    page, _ = repository.list_approvals_page(omar, now=now, status=ApprovalFilter.ALL, query="balance", task_id=None, cursor=None, limit=10)
    assert page == []


def test_private_events_stay_off_the_bus_and_only_their_person_is_notified() -> None:
    repository = InMemoryRepository(InMemoryStore())
    alice = _context("alice")
    task = repository.create_task(_task(alice, private_to="alice"))
    event = repository.record_event(DomainEvent(
        id=new_id("evt"), type="agent_run.completed", tenant_id=alice.tenant_id, workspace_id=alice.workspace_id,
        subject="run_1", correlation_id=task.id, created_at=utc_now(), payload={"task_id": task.id},
    ))
    assert event.private_to == "alice" and "private_to" not in event.model_dump_json()

    published: list[DomainEvent] = []
    heard: list[DomainEvent] = []

    class Outbox:
        def enqueue(self, events: Any) -> None:
            published.extend(events)

    runtime = EventBusRuntime("memory")
    runtime.outbox = Outbox()  # type: ignore[assignment]
    runtime.add_listener(heard.extend)
    public = event.model_copy(update={"id": new_id("evt"), "private_to": None})
    runtime.after_commit([event, public])
    assert [item.id for item in published] == [public.id]
    assert [item.id for item in heard] == [event.id, public.id]

    intent = intent_for(event)
    assert intent is not None and intent.private_to == "alice"


def test_push_for_a_private_task_goes_to_its_person_only() -> None:
    people = _people("private_push")
    _finance_account(people["alice"])
    task_id = _asked_as_task(people["alice"])
    completed = [
        event for event in memory_repository.store.events
        if event.correlation_id == task_id and event.type == "agent_run.completed"
    ]
    assert completed
    intent = intent_for(completed[-1].model_copy(update={"private_to": "alice_owner"}))
    assert intent is not None
    assert resolve_recipients(intent) in ([], ["alice_owner"])  # never another member
    assert resolve_recipients(intent_for(completed[-1].model_copy(update={"private_to": "mona_member"}))) == []  # type: ignore[arg-type]


def test_the_organisation_export_leaves_private_tasks_out() -> None:
    people = _people("private_export")
    _finance_account(people["alice"])
    private_id = _asked_as_task(people["alice"])
    shared = client.post("/api/v1/tasks", headers=people["mona"], json={"title": "Shared", "prompt": "Say hello"}).json()
    data = MemoryTenantData()
    tenant, workspace = people["alice"]["x-tenant-id"], people["alice"]["x-workspace-id"]
    exported = {
        name: [row for batch in data.export_batches(tenant, workspace, BY_NAME[name]) for row in batch]
        for name in ("tasks", "agent_runs", "approvals", "domain_events")
    }
    text = json.dumps(exported, default=str)
    assert private_id not in text and BALANCE not in text and "account balance" not in text
    assert [row["id"] for row in exported["tasks"]] == [shared["id"]]
    assert any(row["correlation_id"] == shared["id"] for row in exported["domain_events"])


def test_a_workflow_run_does_not_copy_a_private_tasks_answer() -> None:
    from anum_api.automation import RepositoryEffects

    repository = InMemoryRepository(InMemoryStore())
    alice = _context("alice")
    runtime = AgentRuntime(MockModelGateway(), repository, tools=_registry(caller_private=True))
    task = repository.create_task(_task(alice))
    asyncio.run(runtime.run_task(task, alice))
    outcome = RepositoryEffects(repository, lambda context: None).task_outcome(alice, task.id)
    assert outcome is not None and outcome.status == TaskStatus.COMPLETED
    assert "SECRET" not in (outcome.result or "") and "private" in (outcome.result or "")
    # Its events never start a workspace workflow either.
    effects = RepositoryEffects(repository, lambda context: None)
    assert effects.events_since(alice, "agent_run.completed", utc_now().replace(year=2020), 10) == []


def test_every_finance_and_own_mailbox_reading_tool_is_caller_scoped() -> None:
    """The property lives on the tool definition, so a new finance tool that forgets it fails here."""
    definitions = main.tool_registry._definitions
    assert all(definition.caller_scoped for name, definition in definitions.items() if name.startswith("finance."))
    assert sum(1 for name in definitions if name.startswith("finance.")) >= 20
    for name in ("summarize_mail", "classify_mail", "classify_mail_page", "request_mail_action", "draft_mail_reply"):
        assert definitions[name].caller_scoped, name
    # Approval-bound mailbox actions show the exact call to the approver on purpose.
    for name in ("send_mail", "delete_mail", "archive_mail_bulk", "export_mail"):
        assert not definitions[name].caller_scoped, name
