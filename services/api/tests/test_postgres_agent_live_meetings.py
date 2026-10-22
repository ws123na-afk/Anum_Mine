"""Live-agent meeting row isolation and uniqueness under the PostgreSQL app role.

Run with pytest -m database after alembic head. These tests create durable
rows in transaction-scoped connections and roll back before returning.
"""
from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

pytestmark = pytest.mark.database


@pytest.fixture
def scoped_connection(database_engine: Engine, seed_scopes: None) -> Iterator[Connection]:
    with database_engine.connect() as connection:
        trans = connection.begin()
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(
                text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )
            yield connection
        finally:
            trans.rollback()


def create_meeting(conn: Connection, meeting_id: str) -> None:
    conn.execute(text("""
        insert into agent_meeting_sessions
        (id, tenant_id, workspace_id, title, created_by)
        values (:id, :tenant, :workspace, 'Test meeting', 'owner')
    """), {"id": meeting_id, "tenant": TENANT_A, "workspace": WORKSPACE_A})


def test_live_meeting_session_visible_only_to_own_scope(scoped_connection: Connection):
    db = scoped_connection
    key = "isolation-" + uuid4().hex
    create_meeting(db, key)
    assert db.execute(text(
        "select count(*) from agent_meeting_sessions where id=:id"
    ), {"id": key}).scalar_one() == 1
    db.execute(
        text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"),
        {"t": TENANT_B, "w": WORKSPACE_B}
    )
    assert db.execute(text(
        "select count(*) from agent_meeting_sessions where id=:id"
    ), {"id": key}).scalar_one() == 0


def test_turn_and_lease_sequence_uniqueness(scoped_connection: Connection):
    db = scoped_connection
    key = "unique-" + uuid4().hex
    create_meeting(db, key)
    parameters = {"tenant": TENANT_A, "workspace": WORKSPACE_A, "session": key}
    db.execute(text("""
        insert into agent_meeting_turn_leases
        (id, tenant_id, workspace_id, session_id, sequence, agent_id, request_key, expected_revision, expires_at)
        values ('lease-a', :tenant, :workspace, :session, 1, 'agent-a', 'req-a', 1, now() + interval '60 seconds')
    """), parameters)
    # A second worker cannot simultaneously claim the same sequence, even with
    # a different id and request_key. SAVEPOINT keeps the outer test valid.
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text("""
            insert into agent_meeting_turn_leases
            (id, tenant_id, workspace_id, session_id, sequence, agent_id, request_key, expected_revision, expires_at)
            values ('lease-b', :tenant, :workspace, :session, 1, 'agent-b', 'req-b', 1, now() + interval '60 seconds')
        """), parameters)
    db.execute(text("""
        insert into agent_meeting_turns
        (id, tenant_id, workspace_id, session_id, sequence, speaker_id, role, body, request_key)
        values ('turn-a', :tenant, :workspace, :session, 1, 'agent-a', 'agent', 'Persisted content', 'req-a')
    """), parameters)
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text("""
            insert into agent_meeting_turns
            (id, tenant_id, workspace_id, session_id, sequence, speaker_id, role, body, request_key)
            values ('turn-b', :tenant, :workspace, :session, 2, 'agent-a', 'agent', 'Different message', 'req-a')
        """), parameters)


def test_cross_tenant_session_cannot_be_used_as_child_parent(scoped_connection: Connection):
    db = scoped_connection
    key = "cross-" + uuid4().hex
    create_meeting(db, key)
    from sqlalchemy.exc import IntegrityError
    db.execute(
        text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true)"),
        {"t": TENANT_B, "w": WORKSPACE_B}
    )
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text("""
            insert into agent_meeting_participants
            (id, tenant_id, workspace_id, session_id, actor_id, kind, added_by)
            values ('cross-participant', :t, :w, :meeting, 'agent-a', 'agent', 'owner')
        """), {"t": TENANT_B, "w": WORKSPACE_B, "meeting": key})
