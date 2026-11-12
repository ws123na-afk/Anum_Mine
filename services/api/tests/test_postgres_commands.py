"""ANUM Command in PostgreSQL (migration 0044_commands, docs/command.md).

The API runs as the non-owner ``anum_test_app`` role. Commands and quick commands are
private to one user inside one tenant and workspace: the forced RLS policies check
``anum.tenant_id``, ``anum.workspace_id`` and ``anum.user_id``. The command record is
written in the same transaction as its tasks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.commands import memory_command_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "owner"}


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    memory_command_store.clear()
    with database_engine.begin() as connection:
        for tenant_id, workspace_id in ((TENANT_A, WORKSPACE_A), (TENANT_B, WORKSPACE_B)):
            for user_id in ("user_test", "user_other"):
                connection.execute(
                    text(
                        "insert into workspace_memberships (tenant_id, workspace_id, user_id, role) "
                        "values (:tenant_id, :workspace_id, :user_id, 'owner')"
                    ),
                    {"tenant_id": tenant_id, "workspace_id": workspace_id, "user_id": user_id},
                )
    yield TestClient(app)
    memory_command_store.clear()


def _count(app_session: Callable[..., Iterator[Session]], table: str, tenant_id: str, user_id: str | None) -> int:
    context = tenant_context(tenant_id, WORKSPACE_A if tenant_id == TENANT_A else WORKSPACE_B)
    with app_session(context) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": user_id})
        # No predicate: only RLS decides what is visible.
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())


def _create(client: TestClient, text_value: str = "Check my emails, then send the summary to the team") -> dict:
    plan = client.post("/api/v1/commands/preview", headers=headers(), json={"text": text_value})
    assert plan.status_code == 200, plan.text
    steps = [{"instruction": step["instruction"]} for step in plan.json()["steps"]]
    created = client.post("/api/v1/commands", headers=headers(), json={"text": text_value, "steps": steps})
    assert created.status_code == 201, created.text
    return created.json()


def test_command_and_its_tasks_persist_and_run(client: TestClient, database_engine: Engine) -> None:
    command = _create(client)
    assert [step["agent"]["id"] for step in command["steps"]] == ["email", "action"]
    memory_command_store.clear()  # restart: read back from PostgreSQL
    for step in command["steps"]:
        assert client.post(f"/api/v1/tasks/{step['task_id']}/run", headers=headers()).status_code == 200
    view = client.get(f"/api/v1/commands/{command['id']}", headers=headers()).json()
    assert view["status"] == "waiting_approval" and view["timeline"]
    (pending,) = view["pending"]
    approved = client.post(
        f"/api/v1/approvals/{pending['id']}/approve", headers=headers("user_other"), json={"payload_hash": pending["payload_hash"]}
    )
    assert approved.status_code == 200, approved.text
    assert client.get(f"/api/v1/commands/{command['id']}", headers=headers()).json()["status"] == "completed"
    listed = client.get("/api/v1/commands", headers=headers()).json()
    assert [item["id"] for item in listed] == [command["id"]]
    with database_engine.connect() as connection:
        stored = connection.execute(text("select created_by, jsonb_array_length(steps) from commands")).one()
        audited = connection.execute(text("select count(*) from audit_records where action = 'command.created'")).scalar_one()
    assert tuple(stored) == ("user_test", 2) and audited == 1


def test_commands_and_quick_commands_are_private_under_rls(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    command = _create(client)
    quick = client.post(
        "/api/v1/quick-commands", headers=headers(), json={"title": "Mail", "text": "Check my emails", "category": "work"}
    )
    assert quick.status_code == 201, quick.text
    for table in ("commands", "quick_commands"):
        assert _count(app_session, table, TENANT_A, "user_test") == 1
        assert _count(app_session, table, TENANT_A, "user_other") == 0
        assert _count(app_session, table, TENANT_A, None) == 0
        assert _count(app_session, table, TENANT_B, "user_test") == 0
    # The API agrees: another user of the workspace gets a 404 and an empty list.
    assert client.get(f"/api/v1/commands/{command['id']}", headers=headers("user_other")).status_code == 404
    assert client.get("/api/v1/quick-commands", headers=headers("user_other")).json() == []
    # Writing a row for another user is refused by WITH CHECK.
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(
                text(
                    "insert into quick_commands (id, tenant_id, workspace_id, user_id, title, text, category) "
                    "values ('quick_x', :t, :w, 'user_other', 'x', 'y', 'work')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )


def test_quick_command_crud_and_use_count_persist(client: TestClient) -> None:
    created = client.post("/api/v1/quick-commands", headers=headers(), json={"title": "Mail", "text": "Check my emails"})
    quick_id = created.json()["id"]
    renamed = client.put(f"/api/v1/quick-commands/{quick_id}", headers=headers(), json={"title": "Morning mail"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Morning mail"
    plan = client.post("/api/v1/commands/preview", headers=headers(), json={"text": "Check my emails"}).json()
    used = client.post(
        "/api/v1/commands",
        headers=headers(),
        json={
            "text": "Check my emails",
            "steps": [{"instruction": step["instruction"]} for step in plan["steps"]],
            "source": "quick_command",
            "quick_command_id": quick_id,
        },
    )
    assert used.status_code == 201, used.text
    (saved,) = client.get("/api/v1/quick-commands", headers=headers()).json()
    assert saved["use_count"] == 1 and saved["title"] == "Morning mail"
    suggestions = client.get("/api/v1/commands/suggestions", headers=headers()).json()
    assert suggestions[0]["kind"] == "quick" and suggestions[0]["quick_command_id"] == quick_id
    assert client.delete(f"/api/v1/quick-commands/{quick_id}", headers=headers()).status_code == 204
    assert client.get("/api/v1/quick-commands", headers=headers()).json() == []


def test_steps_start_in_dependency_order_under_rls(client: TestClient) -> None:
    """The dependency gate finds the step's command by its task id (JSONB containment) as the
    application role, and only for the command's own user."""
    command = _create(client)
    send = command["steps"][1]
    assert send["depends_on"] == [1]
    early = client.post(f"/api/v1/tasks/{send['task_id']}/run", headers=headers())
    assert early.status_code == 409 and "Step 2 waits for step 1" in early.json()["error"]["message"]
    started = client.post(f"/api/v1/commands/{command['id']}/start", headers=headers())
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["started"] == [command["steps"][0]["task_id"]] and body["refused"] == {}
    assert [step["status"] for step in body["command"]["steps"]] == ["completed", "created"]
    # The server continues on its own once step 1 completed (docs/command.md#the-server-continues-a-command).
    import asyncio

    from anum_api.command_continuation import command_continuer

    continued: list[str] = []
    while (item := command_continuer._take()) is not None:
        continued.extend(asyncio.run(command_continuer.handle(item)))
    assert continued == [send["task_id"]]
    view = client.get(f"/api/v1/commands/{command['id']}", headers=headers()).json()
    assert [step["status"] for step in view["steps"]] == ["completed", "waiting_approval"]
    # Another user of the workspace cannot start this user's command.
    assert client.post(f"/api/v1/commands/{command['id']}/start", headers=headers("user_other")).status_code == 404


def test_two_concurrent_finishers_start_the_waiting_step_once(client: TestClient, database_engine: Engine) -> None:
    """Steps 1 and 2 complete; step 3 waits for both. Two API replicas each receive both
    completions (as from NATS) and race to continue the command: the claim in a short
    transaction lets exactly one start step 3 (docs/command.md#the-server-continues-a-command)."""
    import asyncio

    from anum_api.command_continuation import CommandContinuer, Finished, command_continuer
    from anum_api.main import _membership_units, start_task_run

    text_value = "Check my emails, schedule a meeting with the team tomorrow at 10 a.m, send the summary to the team"
    created = client.post("/api/v1/commands", headers=headers(), json={"text": text_value, "steps": [
        {"instruction": "Check my emails", "depends_on": []},
        {"instruction": "Schedule a meeting with the team tomorrow at 10 a.m", "depends_on": []},
        {"instruction": "send the summary to the team", "depends_on": [1, 2]},
    ]})
    assert created.status_code == 201, created.text
    ids = [step["task_id"] for step in created.json()["steps"]]
    for task_id in ids[:2]:
        ran = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers())
        assert ran.status_code == 200 and ran.json()["task"]["status"] == "completed", ran.text
    command_continuer.clear()  # this process's own queue is not part of the race

    async def race() -> list[list[str]]:
        # Both replicas read step 3 as ready before either starts it, then both try.
        barrier = asyncio.Barrier(2)

        async def runner(context, task_id):  # type: ignore[no-untyped-def]
            try:
                await asyncio.wait_for(barrier.wait(), timeout=5)
            except (TimeoutError, asyncio.BrokenBarrierError):
                pass
            return await start_task_run(task_id, context, _membership_units)

        replicas = [CommandContinuer(), CommandContinuer()]
        for replica in replicas:
            replica.configure(runner=runner)
        finished = [Finished(TENANT_A, WORKSPACE_A, task_id) for task_id in ids[:2]]
        return list(await asyncio.gather(replicas[0].handle(finished[0]), replicas[1].handle(finished[1]))) + [
            [str(replica.started), str(replica.already_started), str(replica.refused)] for replica in replicas
        ]

    first, second, counts_a, counts_b = asyncio.run(race())
    assert sorted(first + second) == [ids[2]]
    assert sorted([counts_a, counts_b]) == [["0", "1", "0"], ["1", "0", "0"]]
    with database_engine.connect() as connection:
        runs = connection.execute(text("select count(*) from agent_runs where task_id = :id"), {"id": ids[2]}).scalar_one()
        approvals = connection.execute(text("select count(*) from approvals where task_id = :id"), {"id": ids[2]}).scalar_one()
        status = connection.execute(text("select status from tasks where id = :id"), {"id": ids[2]}).scalar_one()
    assert runs == 1 and approvals == 1 and status == "waiting_approval"
    # The live view agrees, as the command's creator.
    view = client.get(f"/api/v1/commands/{created.json()['id']}", headers=headers()).json()
    assert [step["status"] for step in view["steps"]] == ["completed", "completed", "waiting_approval"]
