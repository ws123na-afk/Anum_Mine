"""Private tasks in PostgreSQL (migration ``0163_private_tasks``, docs/tasks.md#private-tasks).

The repository hides a private task, its runs, approvals and events from everyone but its
person, and the restrictive row policies say the same for a query that goes round the
repository: under the app role another user's session, and a session with no user at all, see
none of those rows. Every row is SYNTHETIC.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from conftest import APP_ROLE, FIXED_NOW, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api.approval_paging import ApprovalFilter
from anum_api.db.repository import SqlAlchemyRepository
from anum_api.schemas import (
    AgentRun,
    AgentRunStep,
    Approval,
    ApprovalStatus,
    DomainEvent,
    RiskLevel,
    Task,
    TaskStatus,
    TenantContext,
)
from anum_api.tasks import TaskFilters

pytestmark = pytest.mark.database

TABLES = ("tasks", "agent_runs", "agent_run_steps", "approvals", "domain_events")
BALANCE = "98,765.43"
QUESTION = "What is my account balance and what did I spend this month?"


def _context(user: str, role: str = "owner") -> TenantContext:
    return TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user, roles=[role])


def _headers(user: str, role: str) -> dict[str, str]:
    return {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": user, "x-user-roles": role}


@pytest.fixture
def as_user(database_engine: Engine) -> Callable[..., Any]:
    """An app-role session with the tenant, the workspace and (unless ``None``) the user set,
    as ``dependencies._unit_of_work`` sets them."""

    @contextmanager
    def factory(user: str | None, *, commit: bool = True) -> Iterator[tuple[Session, SqlAlchemyRepository]]:
        connection = database_engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(text("select set_config('anum.tenant_id', :v, true)"), {"v": TENANT_A})
            connection.execute(text("select set_config('anum.workspace_id', :v, true)"), {"v": WORKSPACE_A})
            if user is not None:
                connection.execute(text("select set_config('anum.user_id', :v, true)"), {"v": user})
            yield session, SqlAlchemyRepository(session, created_by_user_id=user or "system")
            session.flush()
            transaction.commit() if commit else transaction.rollback()
        except Exception:
            if transaction.is_active:
                transaction.rollback()
            raise
        finally:
            session.close()
            connection.close()

    return factory


def _task(task_id: str, user: str, **changes: Any) -> Task:
    return Task(id=task_id, title="Balance question", prompt=QUESTION, status=TaskStatus.COMPLETED, tenant_id=TENANT_A,
                workspace_id=WORKSPACE_A, created_at=FIXED_NOW, updated_at=FIXED_NOW, created_by=user, **changes)


def _seed_private(as_user: Callable[..., Any], task_id: str = "task_private", user: str = "alice") -> None:
    with as_user(user) as (_, repository):
        repository.create_task(_task(task_id, user, private_to=user, private_kind="data"))
        repository.save_run(AgentRun(
            id=f"run_{task_id}", task_id=task_id, status=TaskStatus.COMPLETED, created_at=FIXED_NOW, updated_at=FIXED_NOW,
            result=f"Balances: AED {BALANCE}",
            steps=[AgentRunStep(id=f"step_{task_id}", type="tool_result", summary=f"Balances: AED {BALANCE}", created_at=FIXED_NOW)],
        ))
        repository.save_approval(Approval(
            id=f"approval_{task_id}", task_id=task_id, action="finance.summary", risk_level=RiskLevel.HIGH,
            status=ApprovalStatus.PENDING, reason=f"Proposed action: {QUESTION}", created_at=FIXED_NOW,
            run_id=f"run_{task_id}", expires_at=FIXED_NOW + timedelta(days=3650),
        ))
        repository.record_event(DomainEvent(
            id=f"evt_{task_id}", type="task.created", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, subject=task_id,
            correlation_id=task_id, created_at=FIXED_NOW, payload={"title": "Balance question"},
        ))


def _counts(session: Session) -> dict[str, int]:
    return {table: int(session.execute(text(f"select count(*) from {table}")).scalar_one()) for table in TABLES}  # nosec B608 - fixed names


def test_the_columns_are_nullable_and_the_policies_are_restrictive(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table, column in (("tasks", "private_to"), ("tasks", "private_kind"), ("domain_events", "private_to"),
                              ("automation_schedules", "accepted_version"), ("automation_triggers", "accepted_version")):
            nullable = connection.execute(text(
                "select is_nullable from information_schema.columns where table_name = :t and column_name = :c"), {"t": table, "c": column}).scalar_one()
            assert nullable == "YES", (table, column)
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            kind = connection.execute(text(
                "select permissive from pg_policies where tablename = :t and policyname = :p"), {"t": table, "p": f"private_task_{table}"}).scalar_one()
            assert forced and kind == "RESTRICTIVE", table
        # The relay is not admitted to private events; none of the roles involved bypasses RLS.
        relay = connection.execute(text("select qual from pg_policies where policyname = 'private_task_domain_events'")).scalar_one()
        assert "anum_outbox_relay" not in relay and "anum_tenant_purger" in relay
        bypass = connection.execute(text(
            "select count(*) from pg_roles where rolbypassrls and rolname in "
            "('anum_tenant_purger', 'anum_maintenance', 'anum_platform_reader', 'anum_outbox_relay', :app)"), {"app": APP_ROLE}).scalar_one()
        assert bypass == 0


def test_the_database_shows_private_rows_to_their_person_only(seed_scopes: None, as_user: Callable[..., Any]) -> None:
    _seed_private(as_user)
    with as_user("alice") as (session, _):
        assert _counts(session) == dict.fromkeys(TABLES, 1)
        assert session.execute(text("select private_to from domain_events")).scalar_one() == "alice"
    # Raw SQL, no repository filter: the row policies alone.
    for other in ("omar", "vic", None):
        with as_user(other) as (session, _):
            assert _counts(session) == dict.fromkeys(TABLES, 0), other
            leaked = session.execute(text(
                "select count(*) from agent_run_steps where summary like '%98,765%'")).scalar_one()
            assert leaked == 0


def test_the_database_refuses_writes_into_another_persons_private_task(seed_scopes: None, as_user: Callable[..., Any]) -> None:
    _seed_private(as_user)
    statements = (
        # Hand a new task to someone else, or attach rows to a task the session cannot see.
        ("insert into tasks (id, tenant_id, workspace_id, created_by_user_id, title, prompt, status, private_to, private_kind) "
        "values ('task_forged', :t, :w, 'omar', 'x', 'x', 'created', 'alice', 'data')"),
        "insert into agent_runs (id, tenant_id, workspace_id, task_id, status) values ('run_forged', :t, :w, 'task_private', 'completed')",
        ("insert into approvals (id, tenant_id, workspace_id, task_id, action, risk_level, status, reason) "
        "values ('approval_forged', :t, :w, 'task_private', 'x', 'high', 'pending', 'x')"),
        "insert into agent_run_steps (id, tenant_id, workspace_id, run_id, type, summary) values ('step_forged', :t, :w, 'run_task_private', 'final', 'x')",
        ("insert into domain_events (id, tenant_id, workspace_id, type, subject, correlation_id, private_to) "
        "values ('evt_forged', :t, :w, 'task.created', 'x', 'task_private', 'alice')"),
    )
    for statement in statements:
        with pytest.raises(DBAPIError), as_user("omar") as (session, _):
            session.execute(text(statement), {"t": TENANT_A, "w": WORKSPACE_A})
    for statement in ("update tasks set private_to = null, private_kind = null", "update tasks set private_to = 'omar'",
                      "delete from agent_run_steps", "delete from agent_runs", "delete from approvals", "delete from domain_events", "delete from tasks"):
        with as_user("omar") as (session, _):
            assert session.execute(text(statement)).rowcount == 0, statement
    with as_user("alice") as (session, _):
        assert _counts(session) == dict.fromkeys(TABLES, 1)
        # Her own task cannot be handed to someone else either.
    with pytest.raises(DBAPIError), as_user("alice") as (session, _):
        session.execute(text("update tasks set private_to = 'omar'"))
    # The two columns go together, and the kind is one of two.
    for statement in ("update tasks set private_kind = null", "update tasks set private_kind = 'other'"):
        with pytest.raises(DBAPIError), as_user("alice") as (session, _):
            session.execute(text(statement))


def test_the_repository_returns_a_private_task_to_its_person_only(seed_scopes: None, as_user: Callable[..., Any]) -> None:
    _seed_private(as_user)
    alice, omar = _context("alice"), _context("omar")
    with as_user("alice") as (_, repository):
        repository.create_task(_task("task_shared", "alice").model_copy(update={"title": "Shared", "prompt": "Say hello"}))
    # The repository filter is tested apart from the policy: a session whose anum.user_id is
    # the person's own, asked on behalf of someone else, still answers nothing.
    for user in ("omar", "alice"):
        with as_user(user) as (_, repository):
            assert repository.get_task("task_private", omar) is None and repository.get_task_for_update("task_private", omar) is None
            assert [task.id for task in repository.list_tasks(omar)] == ["task_shared"]
            assert [task.id for task in repository.list_tasks(omar, filters=TaskFilters(query="balance"))] == []
            summary = repository.summarize_tasks(omar, TaskFilters())
            assert summary.total == 1 and summary.by_status == {"completed": 1}
            assert repository.list_runs_for_task("task_private", omar) == [] and repository.find_run_for_task("task_private", omar) is None
            assert repository.get_run("run_task_private", omar) is None
            assert repository.list_task_events("task_private", omar) == [] and repository.list_events(omar) == []
            assert repository.list_events_after(omar, "evt_task_private") == []
            assert repository.get_approval("approval_task_private", omar) is None
            assert repository.get_approval_for_update("approval_task_private", omar) is None
            assert repository.list_approvals(omar) == [] and repository.list_task_approvals("task_private", omar) == []
            assert repository.summarize_approvals(omar, FIXED_NOW).pending == 0
            page, more = repository.list_approvals_page(omar, now=FIXED_NOW, status=ApprovalFilter.ALL, query=None, task_id=None, cursor=None, limit=10)
            assert page == [] and more is False
    with as_user("alice") as (_, repository):
        task = repository.get_task("task_private", alice)
        assert task is not None and (task.private_to, task.private_kind) == ("alice", "data")
        assert {item.id for item in repository.list_tasks(alice)} == {"task_private", "task_shared"}
        assert repository.summarize_tasks(alice, TaskFilters()).total == 2
        assert BALANCE in (repository.list_runs_for_task("task_private", alice)[0].result or "")
        events = repository.list_events(alice)
        assert [event.id for event in events] == ["evt_task_private"] and events[0].private_to == "alice"
        assert repository.summarize_approvals(alice, FIXED_NOW).pending == 1


def test_the_marker_is_permanent_and_events_follow_the_task(seed_scopes: None, as_user: Callable[..., Any]) -> None:
    alice = _context("alice")
    with as_user("alice") as (_, repository):
        repository.create_task(_task("task_late", "alice"))
        repository.record_event(DomainEvent(
            id="evt_late_1", type="task.created", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, subject="task_late",
            correlation_id="task_late", created_at=FIXED_NOW, payload={"title": "Balance question"},
        ))
    with as_user("omar") as (_, repository):
        assert [event.id for event in repository.list_events(_context("omar"))] == ["evt_late_1"]  # a workspace task so far
    with as_user("alice") as (_, repository):
        task = repository.get_task("task_late", alice)
        assert task is not None
        task.private_to, task.private_kind = "alice", "data"
        repository.save_task(task)
    with as_user("omar") as (session, repository):
        assert repository.list_events(_context("omar")) == [] and _counts(session)["domain_events"] == 0
    with as_user("alice") as (_, repository):
        stale = _task("task_late", "alice")  # a copy read before the task turned private
        saved = repository.save_task(stale)
        assert (saved.private_to, saved.private_kind) == ("alice", "data")

    # A chat task can be shared (its run waits for an approver), and its events with it.
    with as_user("alice") as (_, repository):
        repository.create_task(_task("task_chat", "alice", private_to="alice", private_kind="chat"))
        repository.record_event(DomainEvent(
            id="evt_chat_1", type="task.created", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, subject="task_chat",
            correlation_id="task_chat", created_at=FIXED_NOW, payload={"title": "Send the offer"},
        ))
    with as_user("omar") as (session, _):
        assert session.execute(text("select count(*) from tasks where id = 'task_chat'")).scalar_one() == 0
    with as_user("alice") as (_, repository):
        task = repository.get_task("task_chat", alice)
        assert task is not None
        task.private_to = task.private_kind = None
        repository.save_task(task)
    with as_user("omar") as (session, repository):
        assert repository.get_task("task_chat", _context("omar")) is not None
        assert session.execute(text("select count(*) from domain_events where id = 'evt_chat_1'")).scalar_one() == 1


def test_other_roles_see_what_they_did_before_and_the_relay_sees_no_private_event(
    seed_scopes: None, as_user: Callable[..., Any], database_engine: Engine
) -> None:
    _seed_private(as_user)
    with as_user("alice") as (_, repository):
        repository.create_task(_task("task_shared", "alice"))
        repository.record_event(DomainEvent(
            id="evt_shared", type="task.created", tenant_id=TENANT_A, workspace_id=WORKSPACE_A, subject="task_shared",
            correlation_id="task_shared", created_at=FIXED_NOW, payload={},
        ))
    with database_engine.begin() as connection:
        connection.execute(text("set local role anum_outbox_relay"))
        assert set(connection.execute(text("select id from domain_events where published_at is null")).scalars()) == {"evt_shared"}
    with database_engine.begin() as connection:
        # Approval reminders discover pending approvals by id, status and time only.
        connection.execute(text("set local role anum_maintenance"))
        assert set(connection.execute(text("select id from approvals where status = 'pending'")).scalars()) == {"approval_task_private"}
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(text("select reason from approvals"))
    with database_engine.begin() as connection:
        # Billing counts every task of a tenant (private ones too), never its text.
        connection.execute(text("set local role anum_maintenance"))
        connection.execute(text("select set_config('anum.billing_tenant', :t, true)"), {"t": TENANT_A})
        assert connection.execute(text("select count(*) from tasks where tenant_id = :t"), {"t": TENANT_A}).scalar_one() == 2
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(text("select title from tasks"))


def test_a_finance_answer_over_http_is_read_by_its_creator_only(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    with database_engine.begin() as connection:
        for user, role in (("alice_owner", "owner"), ("omar_owner", "owner"), ("mona_member", "member"), ("vic_viewer", "viewer")):
            connection.execute(text(
                "insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values (:u, :t, :w, :r, true) "
                "on conflict do nothing"), {"u": user, "r": role, "t": TENANT_A, "w": WORKSPACE_A})
    alice = _headers("alice_owner", "owner")
    others = (_headers("vic_viewer", "viewer"), _headers("mona_member", "member"), _headers("omar_owner", "owner"))
    created = client.post("/api/v1/finance/accounts", headers=alice,
                          json={"name": "Salary", "kind": "bank", "currency": "AED", "opening_balance": "98765.43"})
    assert created.status_code == 201, created.text
    task = client.post("/api/v1/tasks", headers=alice, json={"title": "Money", "prompt": QUESTION})
    assert task.status_code == 201, task.text
    task_id = task.json()["id"]
    ran = client.post(f"/api/v1/tasks/{task_id}/run", headers=alice)
    assert ran.status_code == 200 and BALANCE in json.dumps(ran.json()), ran.text
    assert ran.json()["task"]["private_to"] == "alice_owner"

    for person in others:
        for path in ("", "/runs", "/events", "/latest-run"):
            assert client.get(f"/api/v1/tasks/{task_id}{path}", headers=person).status_code == 404, (person["x-user-id"], path)
        assert client.get("/api/v1/tasks", headers=person).json() == []
        assert client.get("/api/v1/tasks/summary", headers=person).json()["total"] == 0
        for path in ("/api/v1/events", "/api/v1/events/stream?follow=false", "/api/v1/approvals"):
            answer = client.get(path, headers=person)
            assert task_id not in answer.text and BALANCE not in answer.text, (person["x-user-id"], path)
    assert BALANCE in client.get(f"/api/v1/tasks/{task_id}/runs", headers=alice).text
    assert task_id in client.get("/api/v1/events", headers=alice).text
    assert task_id in client.get("/api/v1/events/stream?follow=false", headers=alice).text

    # The chat question: private from its first event.
    conversation = client.post("/api/v1/conversations", headers=alice, json={})
    assert conversation.status_code == 201, conversation.text
    asked = client.post(f"/api/v1/conversations/{conversation.json()['id']}/ask", headers=alice, json={"content": QUESTION})
    assert asked.status_code == 200 and BALANCE in asked.text
    assert len(client.get("/api/v1/tasks", headers=alice).json()) == 2
    for person in others:
        assert client.get("/api/v1/tasks", headers=person).json() == []
        assert "account balance" not in client.get("/api/v1/events", headers=person).text
    with database_engine.connect() as connection:
        # Owner view (no RLS for the superuser): both tasks carry the marker, as do their events.
        rows = connection.execute(text("select private_to, private_kind from tasks order by created_at")).all()
        assert [tuple(row) for row in rows] == [("alice_owner", "data"), ("alice_owner", "data")]
        assert connection.execute(text("select count(*) from domain_events where correlation_id in (select id from tasks) and private_to is null")).scalar_one() == 0
