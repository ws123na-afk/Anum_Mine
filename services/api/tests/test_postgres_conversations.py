"""Assistant conversations in PostgreSQL (migration 0018).

The API runs as the non-owner ``anum_test_app`` role. Conversations are private to one
user, so the RLS policies check ``anum.user_id`` as well as the tenant and workspace.
"Restart" means the in-memory store is cleared: everything is read back from PostgreSQL.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.conversations import conversation_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

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
