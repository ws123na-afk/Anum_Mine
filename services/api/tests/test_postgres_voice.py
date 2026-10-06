"""Voice sessions and transcripts in PostgreSQL (migration 0009).

The API runs as the non-owner ``anum_test_app`` role. Voice rows are private to one user,
so the RLS policies check ``anum.user_id`` too. "Restart" means the in-memory voice store
is cleared: everything must be read back from PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api import voice as voice_module
from anum_api.db import session as db_session
from anum_api.dependencies import memory_repository
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings
from anum_api.voice import open_voice_store, voice_store
from anum_api.voice_retention import main as retention_main
from anum_api.voice_retention import purge

pytestmark = pytest.mark.database


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "member"}


A = headers()


def _app_factory(database_engine: Engine) -> sessionmaker[Session]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    return factory


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = _app_factory(database_engine)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    voice_store.clear()
    memory_repository.store.tasks.clear()
    yield factory
    voice_store.clear()


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def _session(client: TestClient, retention: str = "session", hdrs: dict[str, str] = A) -> str:
    response = client.post("/api/v1/voice/sessions", headers=hdrs, json={"retention": retention, "locale": "en-US"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _say(client: TestClient, session_id: str, words: str, sequence: int, hdrs: dict[str, str] = A) -> dict:
    response = client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=hdrs,
        json={"text": words, "is_final": True, "client_sequence": sequence},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return connection.execute(text(f"select count(*) from {table}")).scalar_one()  # nosec B608 - fixed table names


def test_sessions_and_transcripts_survive_a_restart(client: TestClient, database_engine: Engine) -> None:
    session_id = _session(client, "permanent")
    _say(client, session_id, "Plan the quarterly review", 0)
    _say(client, session_id, "and book the room", 1)
    assert client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=A,
        json={"text": "duplicate", "client_sequence": 1},
    ).status_code == 409

    voice_store.clear()  # a restarted API process has nothing in memory
    assert voice_store.sessions == {}
    fetched = client.get(f"/api/v1/voice/sessions/{session_id}", headers=A)
    transcript = client.get(f"/api/v1/voice/sessions/{session_id}/transcript", headers=A)

    assert fetched.status_code == 200 and fetched.json()["retention"] == "permanent"
    assert [item["text"] for item in transcript.json()] == ["Plan the quarterly review", "and book the room"]
    assert (_count(database_engine, "voice_sessions"), _count(database_engine, "voice_transcript_segments")) == (1, 2)


def test_voice_rows_are_private_to_the_user_workspace_and_tenant(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    session_id = _session(client, "permanent")
    _say(client, session_id, "private words", 0)

    for other in (headers("user_other"), headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)):
        assert client.get(f"/api/v1/voice/sessions/{session_id}", headers=other).status_code == 404
        assert client.get(f"/api/v1/voice/sessions/{session_id}/transcript", headers=other).status_code == 404

    # RLS itself, not only the query filters: another user of the same workspace sees no rows.
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_other', true)"))
        assert session.execute(text("select count(*) from voice_sessions")).scalar_one() == 0
        assert session.execute(text("select count(*) from voice_transcript_segments")).scalar_one() == 0
    with app_session(tenant_context()) as session:  # no user context at all
        assert session.execute(text("select count(*) from voice_sessions")).scalar_one() == 0
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        assert session.execute(text("select count(*) from voice_transcript_segments")).scalar_one() == 1

    # Writing a row for another user is refused by the policy's WITH CHECK.
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(
                text(
                    """
                    insert into voice_sessions (id, tenant_id, workspace_id, user_id, locale, retention,
                        assistant_name, status)
                    values ('voice_forged', :tenant, :workspace, 'user_other', 'en-US', 'session', 'Anum', 'active')
                    """
                ),
                {"tenant": TENANT_A, "workspace": WORKSPACE_A},
            )


def test_session_only_transcript_is_deleted_when_completed(client: TestClient, database_engine: Engine) -> None:
    kept = _session(client, "permanent")
    _say(client, kept, "keep me", 0)
    erased = _session(client, "session")
    _say(client, erased, "sensitive note", 0)
    cancelled = _session(client, "session")
    _say(client, cancelled, "also sensitive", 0)

    assert client.post(f"/api/v1/voice/sessions/{erased}/complete", headers=A).json()["status"] == "completed"
    assert client.delete(f"/api/v1/voice/sessions/{cancelled}", headers=A).json()["status"] == "cancelled"

    with database_engine.connect() as connection:
        remaining = connection.execute(text("select session_id, text from voice_transcript_segments")).all()
        purged = connection.execute(
            text("select id from voice_sessions where transcript_purged_at is not null order by id")
        ).scalars().all()
    assert [(row.session_id, row.text) for row in remaining] == [(kept, "keep me")]
    assert sorted(purged) == sorted([erased, cancelled])
    # A closed session takes no more transcript.
    assert client.post(
        f"/api/v1/voice/sessions/{erased}/transcript", headers=A, json={"text": "late", "client_sequence": 1}
    ).status_code == 409


def test_a_transcript_becomes_at_most_one_task(client: TestClient) -> None:
    session_id = _session(client)
    segment = _say(client, session_id, "Summarize the release notes", 0)
    command = {"transcript_segment_id": segment["id"]}

    first = client.post(f"/api/v1/voice/sessions/{session_id}/commands", headers=A, json=command)
    voice_store.clear()
    second = client.post(f"/api/v1/voice/sessions/{session_id}/commands", headers=A, json=command)

    assert first.status_code == 200 and first.json()["task"]["prompt"] == "Summarize the release notes"
    assert second.status_code == 409


def test_question_limit_is_shared_by_every_replica(
    client: TestClient, app_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_module, "MAX_ASKS_PER_SESSION", 5)
    session_id = _session(client)
    context = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test", roles=["member"])

    def ask_once(_: int) -> int:
        # Each call is its own store and transaction, as on separate API replicas.
        with open_voice_store(context) as store:
            return store.count_ask(session_id)

    with ThreadPoolExecutor(max_workers=6) as pool:
        counts = sorted(pool.map(ask_once, range(12)))
    assert counts == list(range(1, 13))  # atomic: no two replicas got the same number

    segment = _say(client, session_id, "What's the status of my workspace?", 0)
    response = client.post(f"/api/v1/voice/sessions/{session_id}/ask", headers=A, json={"transcript_segment_id": segment["id"]})
    assert response.status_code == 429


def test_ask_stores_the_reply_in_the_transcript(client: TestClient) -> None:
    session_id = _session(client, "30_days")
    segment = _say(client, session_id, "What's the status of my workspace?", 0)

    response = client.post(f"/api/v1/voice/sessions/{session_id}/ask", headers=A, json={"transcript_segment_id": segment["id"]})
    voice_store.clear()
    transcript = client.get(f"/api/v1/voice/sessions/{session_id}/transcript", headers=A).json()

    assert response.status_code == 200, response.text
    assert [item["role"] for item in transcript] == ["user", "assistant"]
    assert transcript[1]["text"] == response.json()["reply"]


def test_voice_writes_need_an_onboarded_workspace(client: TestClient) -> None:
    response = client.post(
        "/api/v1/voice/sessions", headers=headers(workspace_id="workspace_not_onboarded"), json={}
    )
    assert response.status_code == 409


def _expire(database_engine: Engine, session_id: str) -> None:
    with database_engine.begin() as connection:
        connection.execute(
            text("update voice_sessions set expires_at = now() - interval '1 minute' where id = :id"),
            {"id": session_id},
        )


def test_thirty_day_transcripts_are_hidden_on_expiry_and_purged_per_scope(
    client: TestClient, database_engine: Engine, app_factory: sessionmaker[Session]
) -> None:
    sessions = {}
    for name, hdrs in (
        ("a", A),
        ("a_other_user", headers("user_other")),
        ("b", headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)),
    ):
        sessions[name] = _session(client, "30_days", hdrs)
        _say(client, sessions[name], f"note {name}", 0, hdrs)
        _say(client, sessions[name], f"more {name}", 1, hdrs)
    fresh = _session(client, "30_days")
    _say(client, fresh, "not expired yet", 0)
    permanent = _session(client, "permanent")
    _say(client, permanent, "forever", 0)
    for name in ("a", "a_other_user", "b"):
        _expire(database_engine, sessions[name])

    # Expired transcripts are unreadable before any purge has run.
    assert client.get(f"/api/v1/voice/sessions/{sessions['a']}/transcript", headers=A).json() == []

    dry = purge(session_factory=app_factory, dry_run=True)
    assert dry == {"sessions": 3, "segments": 6, "dry_run": True}
    assert _count(database_engine, "voice_transcript_segments") == 8

    done = purge(session_factory=app_factory, batch_size=2)
    assert done == {"sessions": 3, "segments": 6, "dry_run": False}
    with database_engine.connect() as connection:
        left = sorted(connection.execute(text("select text from voice_transcript_segments")).scalars())
    assert left == ["forever", "not expired yet"]
    # Idempotent: nothing left to purge, and the sessions themselves remain.
    assert purge(session_factory=app_factory) == {"sessions": 0, "segments": 0, "dry_run": False}
    assert _count(database_engine, "voice_sessions") == 5


def test_retention_command_refuses_the_memory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    assert retention_main([]) == 2


def test_maintenance_role_sees_only_ids_of_expired_sessions_and_cannot_write(
    client: TestClient, database_engine: Engine
) -> None:
    expired = _session(client, "30_days")
    _say(client, expired, "secret words", 0)
    _session(client, "30_days")
    _expire(database_engine, expired)

    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            visible = connection.execute(text("select id from voice_sessions")).scalars().all()
        assert visible == [expired]
        for statement in (
            "select text from voice_transcript_segments",
            "select status from voice_sessions",
            "delete from voice_transcript_segments where session_id = :id",
            "update voice_sessions set transcript_purged_at = now() where id = :id",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement), {"id": expired})
