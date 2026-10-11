"""Per-user preferences in PostgreSQL (migration 0025_user_preferences).

The API runs as the non-owner ``anum_test_app`` role. A preference row is private to one
user in one tenant: the forced RLS policy checks ``anum.user_id`` and ``anum.tenant_id``.
"Restart" clears the in-memory store, so everything is read back from PostgreSQL.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, FIXED_NOW, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.conversation_memory import extraction_enabled, recall_memories, remember_facts
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.retrieval import LocalEmbedder
from anum_api.schemas import TenantContext
from anum_api.settings import settings
from anum_api.user_preferences import user_preference_store

pytestmark = pytest.mark.database


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "member"}


def ctx(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=["member"])


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    user_preference_store.clear()
    yield factory
    user_preference_store.clear()


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def _count(app_session: Callable[..., Iterator[Session]], tenant_id: str, user_id: str | None) -> int:
    context = tenant_context(tenant_id, WORKSPACE_A if tenant_id == TENANT_A else WORKSPACE_B)
    with app_session(context) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": user_id})
        # No predicate: only RLS decides what is visible.
        return int(session.execute(text("select count(*) from user_preferences")).scalar_one())


def test_the_switch_persists_per_user_and_tenant(client: TestClient, database_engine: Engine) -> None:
    assert client.get("/api/v1/me/preferences", headers=headers()).json()["remember_conversations"] is True
    saved = client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": False})
    assert saved.status_code == 200, saved.text
    assert saved.json()["conversation_memory"] == "off_by_user"

    user_preference_store.clear()  # restart: read back from PostgreSQL
    mine = client.get("/api/v1/me/preferences", headers=headers()).json()
    assert mine["remember_conversations"] is False and mine["updated_at"]
    assert client.get("/api/v1/me/preferences", headers=headers("user_other")).json()["remember_conversations"] is True
    assert (
        client.get("/api/v1/me/preferences", headers=headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)).json()[
            "remember_conversations"
        ]
        is True
    )
    # Saving again updates the one row.
    assert client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": True}).status_code == 200
    with database_engine.connect() as connection:
        rows = connection.execute(text("select tenant_id, user_id, remember_conversations from user_preferences")).all()
    assert [tuple(row) for row in rows] == [(TENANT_A, "user_test", True)]


def test_rows_are_private_to_their_user_and_tenant_under_rls(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": False})
    assert _count(app_session, TENANT_A, "user_test") == 1
    assert _count(app_session, TENANT_A, "user_other") == 0
    assert _count(app_session, TENANT_A, None) == 0
    assert _count(app_session, TENANT_B, "user_test") == 0

    # Writing a row for another user, or for another tenant, is refused by WITH CHECK.
    for tenant_id, user_id in ((TENANT_A, "user_other"), (TENANT_B, "user_test")):
        with pytest.raises(DBAPIError):
            with app_session(tenant_context()) as session:
                session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
                session.execute(
                    text("insert into user_preferences (tenant_id, user_id, remember_conversations) values (:t, :u, false)"),
                    {"t": tenant_id, "u": user_id},
                )
    # Another user's statement cannot change the row either.
    with app_session(tenant_context(), commit=True) as session:
        session.execute(text("select set_config('anum.user_id', 'user_other', true)"))
        changed = session.execute(text("update user_preferences set remember_conversations = true")).rowcount
    assert changed == 0
    assert client.get("/api/v1/me/preferences", headers=headers()).json()["remember_conversations"] is False


def test_saving_needs_an_onboarded_tenant(client: TestClient) -> None:
    response = client.put(
        "/api/v1/me/preferences",
        headers=headers(tenant_id="tenant_not_onboarded", workspace_id="workspace_not_onboarded"),
        json={"remember_conversations": False},
    )
    assert response.status_code in {403, 409}


def test_off_stops_extraction_and_recall_and_chat_memories_can_be_deleted_together(client: TestClient) -> None:
    asyncio.run(
        remember_facts(ctx(), "conv_pg", "Tea chat", ["The user prefers green tea."], embedder=LocalEmbedder(), clock=lambda: FIXED_NOW)
    )
    asyncio.run(
        remember_facts(
            ctx("user_other"), "conv_pg_other", "Other chat", ["The user prefers black tea."],
            embedder=LocalEmbedder(), clock=lambda: FIXED_NOW,
        )
    )
    assert extraction_enabled(ctx())

    client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": False})
    assert not extraction_enabled(ctx())
    assert asyncio.run(recall_memories(ctx(), "Which tea?", embedder=LocalEmbedder())).blocks == []
    assert extraction_enabled(ctx("user_other"))

    deleted = client.delete("/api/v1/memories", params={"source_type": "conversation"}, headers=headers())
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": 1}
    assert client.get("/api/v1/memories", params={"source_type": "conversation"}, headers=headers()).json() == []
    others = client.get("/api/v1/memories", params={"source_type": "conversation"}, headers=headers("user_other")).json()
    assert [memory["content"] for memory in others] == ["The user prefers black tea."]
