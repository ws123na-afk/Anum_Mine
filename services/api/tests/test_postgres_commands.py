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
