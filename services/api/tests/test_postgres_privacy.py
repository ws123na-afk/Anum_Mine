"""Download my data and Delete my data in PostgreSQL (docs/privacy.md).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. Each user gets a
conversation, a private memory with an index row and chunk, a voice transcript,
notification preferences and a task; the tests prove one user's export and deletion
see and touch only that user's rows in that tenant and workspace.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from anum_api.conversations import conversation_store
from anum_api.db import session as db_session
from anum_api.db.memory_repository import SqlAlchemyMemoryRepository
from anum_api.db.privacy_repository import SqlAlchemyPrivacyStore
from anum_api.db.retrieval_repository import SqlAlchemyRetrievalStore
from anum_api.main import app
from anum_api.memory import MemoryNote, MemoryProvenance, RetentionKind, RetentionPolicy
from anum_api.retrieval import ChunkInput, IndexStatus, RetrievalSource, SourceType
from anum_api.schemas import TenantContext, new_id, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(user_id: str = "user_me", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "member"}


ME = headers()
COLLEAGUE = headers("user_colleague")
OTHER_WORKSPACE = headers(workspace_id=WORKSPACE_A2)
OTHER_TENANT = headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)
EVERYONE = (ME, COLLEAGUE, OTHER_WORKSPACE, OTHER_TENANT)
PRIVATE_TABLES = (
    "conversations",
    "conversation_turns",
    "memories",
    "retrieval_sources",
    "retrieval_chunks",
    "voice_sessions",
    "voice_transcript_segments",
    "notification_preferences",
)


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


def _context(hdrs: dict[str, str]) -> TenantContext:
    return TenantContext(tenant_id=hdrs["x-tenant-id"], workspace_id=hdrs["x-workspace-id"], user_id=hdrs["x-user-id"], roles=["member"])


def _private_memory(factory: sessionmaker[Session], hdrs: dict[str, str], content: str) -> str:
    context = _context(hdrs)
    now = utc_now()
    with factory() as session:
        db_session.set_tenant_context(session, context.tenant_id, context.workspace_id, user_id=context.user_id)
        note = SqlAlchemyMemoryRepository(session).create(
            MemoryNote(
                id=new_id("memory"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                task_id=None,
                content=content,
                provenance=MemoryProvenance(source_type="conversation", source_id="conv_x", created_by_user_id=context.user_id, created_at=now),
                retention=RetentionPolicy(kind=RetentionKind.INDEFINITE),
                created_at=now,
                owner_user_id=context.user_id,
            )
        )
        SqlAlchemyRetrievalStore(session).save_source(
            context,
            RetrievalSource(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                source_type=SourceType.MEMORY,
                source_id=note.id,
                content_sha256="0" * 64,
                status=IndexStatus.INDEXED,
                embedding_model="anum-test",
                indexed_at=now,
                owner_user_id=context.user_id,
            ),
            [ChunkInput(id=new_id("chunk"), chunk_index=0, content=content, content_sha256="0" * 64, embedding=[0.1, 0.2, 0.3])],
        )
        session.commit()
    return note.id


def _seed(client: TestClient, factory: sessionmaker[Session], hdrs: dict[str, str], label: str) -> dict[str, str]:
    conversation_id = client.post("/api/v1/conversations", headers=hdrs, json={}).json()["id"]
    assert client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=hdrs, json={"content": f"{label} question"}).status_code == 200
    memory_id = _private_memory(factory, hdrs, f"{label} prefers tea")
    session_id = client.post("/api/v1/voice/sessions", headers=hdrs, json={"retention": "permanent"}).json()["id"]
    assert client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript", headers=hdrs, json={"text": f"{label} spoken", "client_sequence": 1}
    ).status_code == 201
    assert client.put("/api/v1/notification-preferences", headers=hdrs, json={"email_enabled": True}).status_code == 200
    assert client.put("/api/v1/me/preferences", headers=hdrs, json={"remember_conversations": False}).status_code == 200
    task = client.post("/api/v1/tasks", headers=hdrs, json={"title": f"{label} task", "prompt": f"{label} summarize notes"})
    assert task.status_code == 201, task.text
    return {"conversation": conversation_id, "memory": memory_id, "voice": session_id, "task": task.json()["id"]}


def _preference_rows(engine: Engine) -> set[tuple[str, str]]:
    """user_preferences rows (one per tenant and user, migration 0025), read below RLS."""
    with engine.connect() as connection:
        rows = connection.execute(text("select tenant_id, user_id from user_preferences"))
        return {(row.tenant_id, row.user_id) for row in rows}


def _rows_per_user(engine: Engine) -> dict[tuple[str, str, str], dict[str, int]]:
    """Row counts per (tenant, workspace, user) read as the table owner, below RLS."""
    found: dict[tuple[str, str, str], dict[str, int]] = {}
    owner_column = {"memories": "owner_user_id", "retrieval_sources": "owner_user_id"}
    with engine.connect() as connection:
        for table in PRIVATE_TABLES:
            if table == "retrieval_chunks":
                statement = (
                    "select s.tenant_id, s.workspace_id, s.owner_user_id as user_id, count(*) from retrieval_chunks c "
                    "join retrieval_sources s on (s.tenant_id, s.workspace_id, s.source_type, s.source_id) = "
                    "(c.tenant_id, c.workspace_id, c.source_type, c.source_id) group by 1, 2, 3"
                )
            else:
                column = owner_column.get(table, "user_id")
                statement = f"select tenant_id, workspace_id, {column} as user_id, count(*) from {table} group by 1, 2, 3"  # nosec B608 - fixed table names
            for tenant_id, workspace_id, user_id, count in connection.execute(text(statement)):
                found.setdefault((tenant_id, workspace_id, user_id), {})[table] = count
    return found


def _key(hdrs: dict[str, str]) -> tuple[str, str, str]:
    return (hdrs["x-tenant-id"], hdrs["x-workspace-id"], hdrs["x-user-id"])


def test_export_reads_only_my_rows_under_rls(client: TestClient, app_factory: sessionmaker[Session]) -> None:
    seeded = {label: _seed(client, app_factory, hdrs, label) for label, hdrs in zip(("mine", "colleague", "elsewhere", "foreign"), EVERYONE)}

    response = client.get("/api/v1/privacy/export", headers=ME)
    assert response.status_code == 200
    export = json.loads(response.content)
    assert [c["id"] for c in export["conversations"]] == [seeded["mine"]["conversation"]]
    assert [t["content"] for t in export["conversations"][0]["turns"]][0] == "mine question"
    assert [m["id"] for m in export["memories"]["private"]] == [seeded["mine"]["memory"]]
    assert export["voice_sessions"][0]["transcript"][0]["text"] == "mine spoken"
    assert [t["id"] for t in export["tasks_created"]] == [seeded["mine"]["task"]]
    assert export["notification_preferences"]["email_enabled"] is True
    assert export["user_preferences"]["remember_conversations"] is False
    for other in ("colleague", "elsewhere", "foreign"):
        assert other not in response.content.decode()

    summary = client.get("/api/v1/privacy", headers=ME).json()
    assert summary["private"] == {
        "conversations": 1,
        "conversation_turns": 2,
        "private_memories": 1,
        "retrieval_index_entries": 1,
        "voice_sessions": 1,
        "voice_transcript_segments": 1,
        "notification_preferences": 1,
        "user_preferences": 1,
    }
    assert summary["shared"]["tasks_created"] == 1


def test_the_store_needs_the_user_context_to_see_private_rows(client: TestClient, app_factory: sessionmaker[Session]) -> None:
    _seed(client, app_factory, ME, "mine")
    with app_factory() as session:
        # Tenant and workspace only: RLS hides every user-private row.
        db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A)
        counts, _ = SqlAlchemyPrivacyStore(session, _context(ME)).counts()
        assert counts.conversations == counts.private_memories == counts.voice_sessions == counts.user_preferences == 0
        session.rollback()
    with app_factory() as session:
        # Another user's context sees none of mine either.
        db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A, user_id="user_colleague")
        counts, _ = SqlAlchemyPrivacyStore(session, _context(ME)).counts()
        assert counts.conversations == counts.private_memories == counts.voice_sessions == counts.user_preferences == 0
        # notification_preferences is scoped by tenant and workspace in RLS; the store's
        # explicit user filter is what keeps it to the caller (it is only ever opened
        # with the caller's own context, privacy.open_privacy_store).
        session.rollback()


def test_delete_removes_my_private_rows_and_nobody_elses(
    client: TestClient, app_factory: sessionmaker[Session], database_engine: Engine
) -> None:
    seeded = {label: _seed(client, app_factory, hdrs, label) for label, hdrs in zip(("mine", "colleague", "elsewhere", "foreign"), EVERYONE)}
    before = _rows_per_user(database_engine)
    assert _preference_rows(database_engine) == {(TENANT_A, "user_me"), (TENANT_A, "user_colleague"), (TENANT_B, "user_me")}
    assert before[_key(ME)] == {table: (2 if table == "conversation_turns" else 1) for table in PRIVATE_TABLES}

    assert client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "nope"}).status_code == 422
    assert _rows_per_user(database_engine) == before

    response = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"]["retrieval_index_entries"] == 1
    assert response.json()["kept"]["tasks_created"] == 1

    after = _rows_per_user(database_engine)
    assert _key(ME) not in after
    # One preference row per user and tenant: mine in tenant A goes; the colleague's and
    # the same user id's row in tenant B stay.
    assert _preference_rows(database_engine) == {(TENANT_A, "user_colleague"), (TENANT_B, "user_me")}
    for hdrs in (COLLEAGUE, OTHER_WORKSPACE, OTHER_TENANT):
        assert after[_key(hdrs)] == before[_key(hdrs)]

    # Shared records stay: my task is still there for the workspace.
    assert client.get(f"/api/v1/tasks/{seeded['mine']['task']}", headers=COLLEAGUE).status_code == 200
    with database_engine.connect() as connection:
        audit = connection.execute(
            text("select actor, target, outcome, metadata from audit_records where action = 'privacy.delete'")
        ).one()
    assert (audit.actor, audit.target, audit.outcome) == ("user_me", "user_me", "success")
    assert audit.metadata["deleted"]["conversations"] == 1
    assert "mine" not in json.dumps(audit.metadata)


def test_export_audit_is_written_in_the_users_workspace(client: TestClient, app_factory: sessionmaker[Session], database_engine: Engine) -> None:
    _seed(client, app_factory, ME, "mine")
    assert client.get("/api/v1/privacy/export", headers=ME).status_code == 200
    with database_engine.connect() as connection:
        rows = connection.execute(
            text("select tenant_id, workspace_id, actor, outcome, metadata from audit_records where action = 'privacy.export'")
        ).all()
    assert [(r.tenant_id, r.workspace_id, r.actor, r.outcome) for r in rows] == [(TENANT_A, WORKSPACE_A, "user_me", "success")]
    assert rows[0].metadata["sections"]["conversations"] == 1
    assert "mine" not in json.dumps(rows[0].metadata)
