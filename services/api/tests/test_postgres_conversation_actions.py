"""Message actions in PostgreSQL (migration 0051_chat_depth).

The API runs as the non-owner ``anum_test_app`` role under forced, user-scoped RLS:
rename, pin, search, keyset paging, delete one message, rate, regenerate and edit, and
none of them reaches another user's, workspace's or tenant's rows.
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
BASE = "/api/v1/conversations"


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "member"}


A = headers()
OTHERS = (headers("someone_else"), headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B))


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


def frames(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    out = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def new(client: TestClient, title: str | None = None, hdrs: dict[str, str] = A) -> str:
    response = client.post(BASE, headers=hdrs, json={"title": title} if title else {})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ask(client: TestClient, conversation_id: str, body: dict) -> list[tuple[str, dict]]:
    return frames(client.post(f"{BASE}/{conversation_id}/ask", headers=A, json=body))


def test_rename_pin_search_and_paging_read_back_from_postgres(client: TestClient) -> None:
    ids = [new(client, f"chat {index}") for index in range(5)]
    lantern = new(client)
    ask(client, lantern, {"content": "Which LANTERNS look 100% right?"})
    assert client.patch(f"{BASE}/{ids[0]}", headers=A, json={"title": "Renamed", "pinned": True}).json()["pinned"] is True

    conversation_store.clear()  # "restart": everything below is read from PostgreSQL
    listed = client.get(BASE, headers=A).json()
    assert listed[0]["id"] == ids[0] and listed[0]["title"] == "Renamed" and listed[0]["pinned_at"]
    assert [c["id"] for c in client.get(BASE, headers=A, params={"q": "lanterns"}).json()] == [lantern]
    assert [c["id"] for c in client.get(BASE, headers=A, params={"q": "mock REPLY"}).json()] == [lantern]
    assert [c["id"] for c in client.get(BASE, headers=A, params={"q": "100%"}).json()] == [lantern]
    assert client.get(BASE, headers=A, params={"q": "_"}).json() == []
    assert [c["id"] for c in client.get(BASE, headers=A, params={"q": "renamed"}).json()] == [ids[0]]

    first = client.get(BASE, headers=A, params={"limit": 2}).json()
    seen = [c["id"] for c in first]
    last = first[-1]
    while True:
        page = client.get(BASE, headers=A, params={"limit": 2, "before_updated_at": last["updated_at"], "before_id": last["id"]}).json()
        seen += [c["id"] for c in page]
        if len(page) < 2:
            break
        last = page[-1]
    assert sorted(seen) == sorted([*ids, lantern]) and len(seen) == len(set(seen))


def test_delete_rate_regenerate_and_edit_persist(client: TestClient, database_engine: Engine) -> None:
    conversation_id = new(client)
    one = ask(client, conversation_id, {"content": "first"})
    two = ask(client, conversation_id, {"content": "second"})
    answer = two[-1][1]["assistant_turn"]["id"]
    rated = client.put(f"{BASE}/{conversation_id}/turns/{answer}/feedback", headers=A, json={"rating": "up"})
    assert rated.status_code == 200, rated.text
    with database_engine.connect() as connection:
        assert connection.execute(text("select feedback from conversation_turns where id = :id"), {"id": answer}).scalar_one() == "up"

    regenerated = ask(client, conversation_id, {"retry_turn_id": two[0][1]["user_turn"]["id"]})
    stored = client.get(f"{BASE}/{conversation_id}", headers=A).json()["turns"]
    assert [t["id"] for t in stored][-1] == regenerated[-1][1]["assistant_turn"]["id"] and len(stored) == 4

    ask(client, conversation_id, {"edit_turn_id": one[0][1]["user_turn"]["id"], "content": "first, reworded"})
    stored = client.get(f"{BASE}/{conversation_id}", headers=A).json()["turns"]
    assert [t["content"][:15] for t in stored] == ["first, reworded", "Mock reply to: "]
    assert client.delete(f"{BASE}/{conversation_id}/turns/{stored[0]['id']}", headers=A).status_code == 204
    assert client.get(f"{BASE}/{conversation_id}", headers=A).json()["turns"] == []


def test_actions_never_reach_another_users_workspaces_or_tenants_rows(
    client: TestClient, app_factory: sessionmaker[Session]
) -> None:
    conversation_id = new(client, "mine")
    done = ask(client, conversation_id, {"content": "secret"})
    question, answer = done[0][1]["user_turn"]["id"], done[-1][1]["assistant_turn"]["id"]
    for other in OTHERS:
        assert client.patch(f"{BASE}/{conversation_id}", headers=other, json={"title": "x", "pinned": True}).status_code == 404
        assert client.delete(f"{BASE}/{conversation_id}/turns/{question}", headers=other).status_code == 404
        assert client.put(f"{BASE}/{conversation_id}/turns/{answer}/feedback", headers=other, json={"rating": "down"}).status_code == 404
        assert client.post(f"{BASE}/{conversation_id}/ask", headers=other, json={"edit_turn_id": question, "content": "x"}).status_code == 404
        assert client.get(BASE, headers=other, params={"q": "secret"}).json() == []
    detail = client.get(f"{BASE}/{conversation_id}", headers=A).json()
    assert (detail["title"], detail["pinned"], [t["feedback"] for t in detail["turns"]]) == ("mine", False, [None, None])

    # Below the API: RLS hides the rows from another user, so a forged update or rating changes nothing.
    with app_factory() as session:
        db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A, user_id="someone_else")
        assert session.execute(text("update conversations set pinned_at = now() where id = :id"), {"id": conversation_id}).rowcount == 0
        assert session.execute(text("update conversation_turns set feedback = 'down' where id = :id"), {"id": answer}).rowcount == 0
        assert session.execute(text("delete from conversation_turns where id = :id"), {"id": question}).rowcount == 0
        session.commit()
    # The check constraint refuses any rating but up, down or none.
    with app_factory() as session:
        db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A, user_id="user_test")
        with pytest.raises(DBAPIError):
            session.execute(text("update conversation_turns set feedback = 'meh' where id = :id"), {"id": answer})
