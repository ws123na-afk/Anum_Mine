"""The command sweep in PostgreSQL (migration 0182_command_sweep, docs/command.md#the-sweep).

The API runs as the non-owner ``anum_test_app`` role. Discovery runs as ``anum_maintenance`` and sees
ids, status and times only (never a step's text or a task's prompt); the work runs as the application
role in the creator's scope. A waiting step whose completion never reached the continuer is started
once, however many sweeps race; every tried step is recorded under forced RLS.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from anum_api.command_continuation import command_continuer
from anum_api.command_sweep import discover_waiting, run_sweep_pass
from anum_api.commands import memory_command_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database

TEXT = "Check my emails, schedule a meeting with the team tomorrow at 10 a.m, send the summary to the team"


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
    command_continuer.clear()
    with database_engine.begin() as connection:
        for tenant_id, workspace_id in ((TENANT_A, WORKSPACE_A), (TENANT_B, WORKSPACE_B)):
            connection.execute(
                text("insert into workspace_memberships (tenant_id, workspace_id, user_id, role) values (:t, :w, 'user_test', 'owner')"),
                {"t": tenant_id, "w": workspace_id},
            )
    yield TestClient(app)
    memory_command_store.clear()
    command_continuer.clear()


def _command(client: TestClient, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> tuple[str, list[str]]:
    h = headers(tenant_id=tenant_id, workspace_id=workspace_id)
    created = client.post("/api/v1/commands", headers=h, json={"text": TEXT, "steps": [
        {"instruction": "Check my emails", "depends_on": []},
        {"instruction": "Schedule a meeting with the team tomorrow at 10 a.m", "depends_on": []},
        {"instruction": "send the summary to the team", "depends_on": [1, 2]},
    ]})
    assert created.status_code == 201, created.text
    body = created.json()
    return body["id"], [step["task_id"] for step in body["steps"]]


def _complete_first_two(client: TestClient, ids: list[str], tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> None:
    for task_id in ids[:2]:
        ran = client.post(f"/api/v1/tasks/{task_id}/run", headers=headers(tenant_id=tenant_id, workspace_id=workspace_id))
        assert ran.status_code == 200 and ran.json()["task"]["status"] == "completed", ran.text
    command_continuer.clear()  # the completions never reach the continuer (no NATS, a restart)


def test_the_generated_column_holds_only_waiting_step_ids(client: TestClient, database_engine: Engine) -> None:
    command_id, ids = _command(client)
    with database_engine.connect() as connection:
        waiting = connection.execute(text("select waiting_task_ids from commands where id = :id"), {"id": command_id}).scalar_one()
        forced = connection.execute(text(
            "select relrowsecurity and relforcerowsecurity from pg_class where relname = 'command_step_continuations'")).scalar_one()
    assert waiting == [ids[2]] and forced


def test_the_sweep_starts_a_lost_continuation_once_in_each_tenant(client: TestClient, database_engine: Engine) -> None:
    command_a, ids_a = _command(client)
    command_b, ids_b = _command(client, TENANT_B, WORKSPACE_B)
    _complete_first_two(client, ids_a)
    _complete_first_two(client, ids_b, TENANT_B, WORKSPACE_B)
    found = discover_waiting()
    assert {key[3]: value for key, value in found.items()} == {command_a: [ids_a[2]], command_b: [ids_b[2]]}

    async def race() -> list[dict[str, int]]:
        # Two replicas sweep at the same moment: each step starts once, the loser gets 409.
        return list(await asyncio.gather(run_sweep_pass(), run_sweep_pass()))

    first, second = asyncio.run(race())
    assert first["started"] + second["started"] == 2
    with database_engine.connect() as connection:
        for task_id in (ids_a[2], ids_b[2]):
            runs = connection.execute(text("select count(*) from agent_runs where task_id = :id"), {"id": task_id}).scalar_one()
            status = connection.execute(text("select status from tasks where id = :id"), {"id": task_id}).scalar_one()
            assert runs == 1 and status == "waiting_approval"
        recorded = connection.execute(text(
            "select tenant_id, task_id, source from command_step_continuations order by tenant_id")).all()
    assert [(row.tenant_id, row.task_id, row.source) for row in recorded] == [
        (TENANT_A, ids_a[2], "sweep"), (TENANT_B, ids_b[2], "sweep")]
    assert discover_waiting() == {}
    assert asyncio.run(run_sweep_pass())["started"] == 0
    view = client.get(f"/api/v1/commands/{command_a}", headers=headers()).json()
    assert [step["status"] for step in view["steps"]] == ["completed", "completed", "waiting_approval"]


def test_discovery_reads_ids_only_and_tried_rows_stay_in_their_tenant(client: TestClient, database_engine: Engine) -> None:
    _, ids = _command(client)
    _complete_first_two(client, ids)
    asyncio.run(run_sweep_pass())
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            rows = connection.execute(text("select id, created_by, waiting_task_ids from commands")).all()
            assert len(rows) == 1
        for statement in ("select steps from commands", "select text from commands", "select prompt from tasks",
                          "select title from tasks", "select outcome from command_step_continuations"):
            with pytest.raises(ProgrammingError), connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text(statement))
        with connection.begin():
            # Only never-started tasks are visible to the role: the completed steps are not.
            connection.execute(text("set local role anum_maintenance"))
            visible = connection.execute(text("select id from tasks where id = any(:ids)"), {"ids": ids}).scalars().all()
            assert visible == []
    # The application role sees the tried step only in its own tenant and workspace.
    for tenant_id, workspace_id, expected in ((TENANT_A, WORKSPACE_A, 1), (TENANT_B, WORKSPACE_B, 0)):
        with database_engine.connect() as connection, connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"),
                               {"t": tenant_id, "w": workspace_id})
            assert connection.execute(text("select count(*) from command_step_continuations")).scalar_one() == expected
    with pytest.raises(DBAPIError):
        with database_engine.connect() as connection, connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"),
                               {"t": TENANT_B, "w": WORKSPACE_B})
            connection.execute(text(
                "insert into command_step_continuations (tenant_id, workspace_id, task_id, command_id, outcome, source) "
                "values (:t, :w, 'task_x', 'command_x', 'started', 'sweep')"), {"t": TENANT_A, "w": WORKSPACE_A})


def test_the_continuer_records_its_tries_so_the_sweep_skips_them(client: TestClient, database_engine: Engine) -> None:
    from anum_api.command_continuation import Finished

    _, ids = _command(client)
    for task_id in ids[:2]:
        assert client.post(f"/api/v1/tasks/{task_id}/run", headers=headers()).status_code == 200
    command_continuer.clear()
    assert asyncio.run(command_continuer.handle(Finished(TENANT_A, WORKSPACE_A, ids[0]))) == [ids[2]]
    with database_engine.connect() as connection:
        row = connection.execute(text("select outcome, source from command_step_continuations where task_id = :id"),
                                 {"id": ids[2]}).one()
    assert tuple(row) == ("started", "event")
    assert discover_waiting() == {}
