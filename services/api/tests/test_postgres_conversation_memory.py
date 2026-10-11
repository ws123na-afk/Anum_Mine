"""Conversation memory on PostgreSQL (migration 0021_memory_sources).

Runs as the non-owner application role with forced RLS: a memory remembered from a
user's chat is visible, searchable and deletable only under that user's ``anum.user_id``,
even to statements that leave the owner predicate out.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from anum_api.conversation_memory import remember_facts
from anum_api.db.memory_repository import SqlAlchemyMemoryRepository
from anum_api.db.retrieval_repository import SqlAlchemyRetrievalStore
from anum_api.model_gateway import LOCAL_EMBEDDING_MODEL, local_embedding
from anum_api.retrieval import LocalEmbedder, SourceType
from anum_api.retrieval_retention import purge as retrieval_purge
from anum_api.schemas import TenantContext

from conftest import FIXED_NOW, TENANT_A, WORKSPACE_A
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)
from test_postgres_retrieval import _app_factory, _expiring, _index_memory, _run

pytestmark = pytest.mark.database


def _ctx(user: str) -> TenantContext:
    return TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user, roles=["member"])


def _set_user(session: Session, user: str | None) -> None:
    if user:
        session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": user})


def _opener(app_session: Callable[..., Iterator[Session]]):  # type: ignore[no-untyped-def]
    @contextmanager
    def open_repository(context: TenantContext) -> Iterator[SqlAlchemyMemoryRepository]:
        with app_session(context, commit=True) as session:
            _set_user(session, context.user_id)
            yield SqlAlchemyMemoryRepository(session)

    return open_repository


def _count(app_session, context: TenantContext, user: str | None, table: str) -> int:  # type: ignore[no-untyped-def]
    with app_session(context) as session:
        _set_user(session, user)
        # No owner predicate here: only RLS decides what is visible.
        return session.execute(text(f"select count(*) from {table}")).scalar_one()  # nosec B608 - fixed names


def test_conversation_memories_are_private_under_rls_and_deduplicated(
    seed_scopes: None,
    database_engine: Engine,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    sam, kim = _ctx("user_sam"), _ctx("user_kim")
    opener = _opener(app_session)
    first = _run(
        remember_facts(
            sam, "conv_1", "Planning", ["The user prefers dark mode in the code editor."],
            embedder=LocalEmbedder(), clock=lambda: FIXED_NOW, open_repository=opener,
        )
    )
    assert len(first.created) == 1
    for table in ("memories", "retrieval_sources", "retrieval_chunks"):
        assert _count(app_session, sam, "user_sam", table) == 1
        assert _count(app_session, kim, "user_kim", table) == (1 if table == "retrieval_chunks" else 0)
        # Without a user context (retention purge, the previous release) owned rows are hidden too.
        if table != "retrieval_chunks":
            assert _count(app_session, sam, None, table) == 0

    # Chunks are only ever read through their source row: Kim's search finds nothing.
    with app_session(kim) as session:
        _set_user(session, "user_kim")
        store = SqlAlchemyRetrievalStore(session)
        vector = local_embedding("dark mode code editor")
        assert store.search(kim, vector, LOCAL_EMBEDDING_MODEL, 10, FIXED_NOW) == []
        assert not store.has_indexed(kim, LOCAL_EMBEDDING_MODEL)
        assert SqlAlchemyMemoryRepository(session).delete(first.created[0], kim) is False
    with app_session(sam) as session:
        _set_user(session, "user_sam")
        found = SqlAlchemyRetrievalStore(session).search(
            sam, local_embedding("dark mode code editor"), LOCAL_EMBEDDING_MODEL, 10, FIXED_NOW, source_type=SourceType.MEMORY
        )
        assert [chunk.source_id for chunk in found] == first.created
        assert SqlAlchemyRetrievalStore(session).search(
            sam, local_embedding("dark mode code editor"), LOCAL_EMBEDDING_MODEL, 10, FIXED_NOW, source_type=SourceType.FILE
        ) == []

    # A similar fact updates the same row; the index follows.
    again = _run(
        remember_facts(
            sam, "conv_2", "Later", ["The user prefers dark mode in their code editor."],
            embedder=LocalEmbedder(), clock=lambda: FIXED_NOW, open_repository=opener,
        )
    )
    assert again.updated == first.created and again.created == []
    with database_engine.connect() as connection:
        row = connection.execute(
            text("select content, owner_user_id, source_task_id, kind, provenance from memories")
        ).one()
        chunk = connection.execute(text("select content from retrieval_chunks")).scalar_one()
    assert (row.content, row.owner_user_id, row.source_task_id, row.kind) == (
        "The user prefers dark mode in their code editor.", "user_sam", None, "conversation_fact",
    )
    assert row.provenance["source_type"] == "conversation" and row.provenance["source_id"] == "conv_2"
    assert chunk == row.content


def test_rls_refuses_writing_a_memory_owned_by_someone_else(
    seed_scopes: None,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    with pytest.raises(DBAPIError):
        with app_session(_ctx("user_kim")) as session:
            _set_user(session, "user_kim")
            session.execute(
                text(
                    "insert into memories (id, tenant_id, workspace_id, kind, content, provenance, owner_user_id) "
                    "values ('memory_forged', :tenant_id, :workspace_id, 'conversation_fact', 'x', '{}'::jsonb, 'user_sam')"
                ),
                {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
            )


def test_the_retention_purge_leaves_owned_sources_alone(
    seed_scopes: None,
    database_engine: Engine,
    app_session: Callable[..., Iterator[Session]],
) -> None:
    sam = _ctx("user_sam")
    _run(
        remember_facts(
            sam, "conv_1", "Planning", ["The user lives in Porto."],
            embedder=LocalEmbedder(), clock=lambda: FIXED_NOW, open_repository=_opener(app_session),
        )
    )
    shared = _ctx("user_test")
    with app_session(shared, commit=True) as session:
        expired = _index_memory(session, shared, "task_cm_1", "release checklist expired", _expiring())
    result = retrieval_purge(session_factory=_app_factory(database_engine))
    assert (result["sources"], result["workspaces"]) == (1, 1)
    with database_engine.connect() as connection:
        remaining = set(connection.execute(text("select source_id from retrieval_sources")).scalars())
    assert expired.id not in remaining and len(remaining) == 1


def test_a_chat_is_remembered_and_recalled_through_the_api_on_postgres(
    postgres_backend: None,  # noqa: F811
    database_engine: Engine,
) -> None:
    from anum_api.main import app

    client = TestClient(app)
    mine = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_sam", "x-user-roles": "member"}
    theirs = {**mine, "x-user-id": "user_kim"}

    def ask(conversation_id: str, content: str) -> list[tuple[str, dict]]:
        response = client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=mine, json={"content": content})
        assert response.status_code == 200, response.text
        frames = []
        for frame in response.text.strip().split("\n\n"):
            lines = dict(line.split(": ", 1) for line in frame.splitlines())
            frames.append((lines["event"], json.loads(lines["data"])))
        return frames

    first_id = client.post("/api/v1/conversations", headers=mine, json={}).json()["id"]
    assert ask(first_id, "My name is Sam. I prefer short answers.")[-1][0] == "done"
    memories = client.get("/api/v1/memories", headers=mine).json()
    assert {m["content"] for m in memories} == {"The user's name is Sam.", "The user prefers short answers."}
    assert all(m["provenance"]["source_id"] == first_id and m["task_id"] is None for m in memories)
    assert client.get("/api/v1/memories", headers=theirs).json() == []
    status = client.get("/api/v1/retrieval/status", headers=mine).json()
    assert status["indexed"] == 2
    assert client.get("/api/v1/retrieval/status", headers=theirs).json()["indexed"] == 0
    # Kim's reindex must not treat Sam's private sources as orphans.
    assert client.post("/api/v1/retrieval/index", headers={**theirs, "x-user-roles": "owner"}).json()["removed"] == 0

    second_id = client.post("/api/v1/conversations", headers=mine, json={}).json()["id"]
    recalled = ask(second_id, "Do you know my name, Sam?")[0][1]["memories_used"]
    assert recalled and set(recalled) <= {m["id"] for m in memories}

    for memory in memories:
        assert client.delete(f"/api/v1/memories/{memory['id']}", headers=mine).status_code == 204
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from retrieval_sources")).scalar_one() == 0
