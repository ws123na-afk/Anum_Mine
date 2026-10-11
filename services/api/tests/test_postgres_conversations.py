"""Assistant conversations in PostgreSQL (migration 0018).

The API runs as the non-owner ``anum_test_app`` role. Conversations are private to one
user, so the RLS policies check ``anum.user_id`` as well as the tenant and workspace.
"Restart" means the in-memory store is cleared: everything is read back from PostgreSQL.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.conversation_retention import main as conversation_retention_main
from anum_api.conversation_retention import purge as conversation_purge
from anum_api.conversations import conversation_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings
from anum_api.voice_retention import main as voice_retention_main

pytestmark = pytest.mark.database


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "member"}


A = headers()


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    conversation_store.clear()
    yield factory
    conversation_store.clear()


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def _frames(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    frames = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return int(connection.execute(text(f"select count(*) from {table}")).scalar_one())


def test_conversations_survive_a_restart_and_keep_their_order(client: TestClient, database_engine: Engine) -> None:
    conversation_id = client.post("/api/v1/conversations", headers=A, json={}).json()["id"]
    for question in ("first question", "second question"):
        assert _frames(client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=A, json={"content": question}))[-1][0] == "done"

    conversation_store.clear()
    assert conversation_store.conversations == {}
    detail = client.get(f"/api/v1/conversations/{conversation_id}", headers=A).json()
    assert detail["title"] == "first question"
    assert [(t["role"], t["content"][:15]) for t in detail["turns"]] == [
        ("user", "first question"),
        ("assistant", "Mock reply to: "),
        ("user", "second question"),
        ("assistant", "Mock reply to: "),
    ]
    # The second answer saw the first exchange (bounded history from PostgreSQL).
    assert "1 earlier question" in detail["turns"][3]["content"]
    assert (_count(database_engine, "conversations"), _count(database_engine, "conversation_turns")) == (1, 4)


def test_conversation_rows_are_private_to_the_user_workspace_and_tenant(
    client: TestClient, app_factory: sessionmaker[Session]
) -> None:
    conversation_id = client.post("/api/v1/conversations", headers=A, json={}).json()["id"]
    _frames(client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=A, json={"content": "private"}))

    for other in (headers("someone_else"), headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)):
        assert client.get(f"/api/v1/conversations/{conversation_id}", headers=other).status_code == 404
        assert client.get("/api/v1/conversations", headers=other).json() == []
        assert client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=other, json={"content": "x"}).status_code == 404

    # Below the API: RLS itself hides the rows from another user in the same workspace
    # and refuses a turn written into this user's conversation.
    with app_factory() as session:
        db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A, user_id="someone_else")
        assert session.execute(text("select count(*) from conversations")).scalar_one() == 0
        assert session.execute(text("select count(*) from conversation_turns")).scalar_one() == 0
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    """
                    insert into conversation_turns (id, tenant_id, workspace_id, user_id, conversation_id,
                                                    role, content, status)
                    values ('turn_forged', :tenant, :workspace, 'user_test', :conversation, 'user', 'x', 'complete')
                    """
                ),
                {"tenant": TENANT_A, "workspace": WORKSPACE_A, "conversation": conversation_id},
            )
        session.rollback()


def test_deleting_a_conversation_removes_its_turns(client: TestClient, database_engine: Engine) -> None:
    conversation_id = client.post("/api/v1/conversations", headers=A, json={}).json()["id"]
    _frames(client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=A, json={"content": "bye"}))
    assert client.delete(f"/api/v1/conversations/{conversation_id}", headers=A).status_code == 204
    assert (_count(database_engine, "conversations"), _count(database_engine, "conversation_turns")) == (0, 0)


def test_conversations_need_an_onboarded_workspace(client: TestClient) -> None:
    response = client.post("/api/v1/conversations", headers=headers(workspace_id="workspace_missing"), json={})
    assert response.status_code == 409


# Retention (migration 0019_conversation_retention) -------------------------------------


def _seed_conversation(
    factory: sessionmaker[Session],
    conversation_id: str,
    *,
    idle_days: float,
    turns: int = 2,
    tenant_id: str = TENANT_A,
    workspace_id: str = WORKSPACE_A,
    user_id: str = "user_test",
) -> None:
    updated_at = datetime.now(timezone.utc) - timedelta(days=idle_days)
    scope = {"tenant": tenant_id, "workspace": workspace_id, "user": user_id, "id": conversation_id}
    with factory() as session:
        db_session.set_tenant_context(session, tenant_id, workspace_id, user_id=user_id)
        session.execute(
            text(
                """
                insert into conversations (id, tenant_id, workspace_id, user_id, title, created_at, updated_at)
                values (:id, :tenant, :workspace, :user, 'secret title', :at, :at)
                """
            ),
            {**scope, "at": updated_at},
        )
        for index in range(turns):
            session.execute(
                text(
                    """
                    insert into conversation_turns (id, tenant_id, workspace_id, user_id, conversation_id,
                                                    role, content, status, created_at)
                    values (:turn, :tenant, :workspace, :user, :id, 'user', 'secret text', 'complete', :at)
                    """
                ),
                {**scope, "turn": f"{conversation_id}_turn{index}", "at": updated_at},
            )
        session.commit()


def _seed_retention(factory: sessionmaker[Session]) -> None:
    _seed_conversation(factory, "conv_a_idle", idle_days=45, turns=3)
    _seed_conversation(factory, "conv_a_idle_other_user", idle_days=31, turns=1, user_id="user_other")
    _seed_conversation(factory, "conv_a_recent", idle_days=5)
    _seed_conversation(factory, "conv_a_today", idle_days=0.1)
    _seed_conversation(factory, "conv_a2_idle", idle_days=60, turns=2, workspace_id=WORKSPACE_A2)
    _seed_conversation(factory, "conv_b_idle", idle_days=90, turns=4, tenant_id=TENANT_B, workspace_id=WORKSPACE_B)


def _conversation_ids(database_engine: Engine) -> set[str]:
    with database_engine.connect() as connection:
        return set(connection.execute(text("select id from conversations")).scalars())


IDLE = {"conv_a_idle", "conv_a_idle_other_user", "conv_a2_idle", "conv_b_idle"}
KEPT = {"conv_a_recent", "conv_a_today"}


def test_retention_purge_deletes_idle_conversations_and_their_turns_per_user(
    app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    _seed_retention(app_factory)
    assert _conversation_ids(database_engine) == IDLE | KEPT

    # A dry run walks every batch (keyset paging) and deletes nothing.
    dry = conversation_purge(session_factory=app_factory, retention_days=30, dry_run=True, batch_size=1)
    assert dry == {"retention_days": 30, "conversations": 4, "turns": 10, "dry_run": True}
    assert _conversation_ids(database_engine) == IDLE | KEPT

    done = conversation_purge(session_factory=app_factory, retention_days=30, batch_size=2)
    assert done == {"retention_days": 30, "conversations": 4, "turns": 10, "dry_run": False}
    assert _conversation_ids(database_engine) == KEPT
    assert _count(database_engine, "conversation_turns") == 4
    # Idempotent.
    assert conversation_purge(session_factory=app_factory, retention_days=30)["conversations"] == 0


def test_retention_follows_the_configured_days_and_zero_keeps_everything(
    app_factory: sessionmaker[Session], database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_retention(app_factory)
    monkeypatch.setattr(settings, "conversation_retention_days", 0)
    assert conversation_purge(session_factory=app_factory) == {
        "retention_days": 0, "conversations": 0, "turns": 0, "dry_run": False
    }
    assert _conversation_ids(database_engine) == IDLE | KEPT

    monkeypatch.setattr(settings, "conversation_retention_days", 50)
    assert conversation_purge(session_factory=app_factory)["conversations"] == 2
    assert _conversation_ids(database_engine) == KEPT | {"conv_a_idle", "conv_a_idle_other_user"}

    # The one-day floor of the discovery policy: a 1-day retention still never sees a
    # conversation active within the last day.
    assert conversation_purge(session_factory=app_factory, retention_days=1)["conversations"] == 3
    assert _conversation_ids(database_engine) == {"conv_a_today"}

    with pytest.raises(ValueError):
        conversation_purge(session_factory=app_factory, retention_days=-1)


def test_retention_commands_run_the_purge_and_refuse_the_memory_backend(
    app_factory: sessionmaker[Session],
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_retention(app_factory)
    monkeypatch.setattr(settings, "conversation_retention_days", 30)
    assert voice_retention_main(["--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["conversations"] == {"retention_days": 30, "conversations": 4, "turns": 10, "dry_run": True}
    assert conversation_retention_main([]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["conversations"] == 4
    assert "secret" not in output
    assert _conversation_ids(database_engine) == KEPT

    monkeypatch.setattr(settings, "repository_backend", "memory")
    assert conversation_retention_main([]) == 2


def test_maintenance_role_sees_only_ids_of_idle_conversations_and_cannot_write(
    app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    _seed_retention(app_factory)
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            visible = set(connection.execute(text("select id from conversations")).scalars())
        # Every tenant's idle conversations, never one active within the last day.
        assert visible == IDLE | {"conv_a_recent"}
        for statement in (
            "select title from conversations",
            "select created_at from conversations",
            "select content from conversation_turns",
            "select id from conversation_turns",
            "delete from conversations where id = 'conv_a_idle'",
            "delete from conversation_turns where conversation_id = 'conv_a_idle'",
            "update conversations set updated_at = now() where id = 'conv_a_idle'",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))


@pytest.mark.parametrize(
    ("grant_options", "sees_other_tenants"),
    [("with inherit false, set true", False), ("with inherit true", True)],
)
def test_maintenance_grant_without_inherit_keeps_conversation_reads_to_one_user(
    app_factory: sessionmaker[Session],
    database_engine: Engine,
    grant_options: str,
    sees_other_tenants: bool,
) -> None:
    _seed_retention(app_factory)
    with database_engine.begin() as connection:
        connection.execute(text(f"grant anum_maintenance to {APP_ROLE} {grant_options}"))
    try:
        with app_factory() as session:
            db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A, user_id="user_test")
            seen = set(session.execute(text("select id from conversations")).scalars())
            session.rollback()
    finally:
        with database_engine.begin() as connection:
            connection.execute(text(f"revoke anum_maintenance from {APP_ROLE}"))
    own = {"conv_a_idle", "conv_a_recent", "conv_a_today"}
    if sees_other_tenants:
        # Why the bootstrap must not use an inheriting grant: the discovery policy would
        # widen the application role's own reads to other users' and tenants' idle rows.
        assert seen == own | {"conv_a_idle_other_user", "conv_a2_idle", "conv_b_idle"}
    else:
        assert seen == own
