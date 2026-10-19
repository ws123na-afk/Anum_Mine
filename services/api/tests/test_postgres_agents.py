"""Agent profiles and agent memories in PostgreSQL (migration 0041_agent_profiles).

The API runs as the non-owner ``anum_test_app`` role. A private agent (and its
memories) is visible only under its owner's ``anum.user_id``; a shared agent to the
whole workspace; nothing crosses workspaces or tenants. "Restart" clears the in-memory
store, so everything is read back from PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timezone

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from skill_signing_support import signed
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.agent_profiles import agent_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def headers(user_id: str = "user_test", role: str = "member", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    agent_store.clear()
    yield factory
    agent_store.clear()


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def _visible(app_session: Callable[..., Iterator[Session]], table: str, user_id: str | None, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> int:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": user_id})
        # No predicate: only RLS decides what is visible.
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())


def _create(client: TestClient, hdrs: dict[str, str] | None = None, **fields) -> dict:  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/agents", headers=hdrs or headers(), json={"name": "Agent"} | fields)
    assert response.status_code == 201, response.text
    return response.json()


def test_agents_persist_and_private_ones_stay_private(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    private = _create(client, name="Mine", instructions="secret sauce", allowed_skills=["anum.document-drafting"])
    shared = _create(client, name="Team", sharing="workspace")
    client.post(f"/api/v1/agents/{private['id']}/memories", headers=headers(), json={"content": "private fact"})
    client.post(f"/api/v1/agents/{shared['id']}/memories", headers=headers(), json={"content": "shared fact"})

    agent_store.clear()  # restart: read back from PostgreSQL
    mine = client.get(f"/api/v1/agents/{private['id']}", headers=headers()).json()
    assert mine["instructions"] == "secret sauce" and mine["allowed_skills"] == ["anum.document-drafting"]
    assert client.get("/api/v1/agents", headers=headers()).json()["counts"]["all"] == 2

    other = headers("user_other")
    assert [a["name"] for a in client.get("/api/v1/agents", headers=other).json()["items"]] == ["Team"]
    assert client.get(f"/api/v1/agents/{private['id']}", headers=other).status_code == 404
    assert client.get(f"/api/v1/agents/{private['id']}/memories", headers=other).status_code == 404
    assert client.get(f"/api/v1/agents/{shared['id']}/memories", headers=other).json()["total"] == 1

    # RLS alone, without any predicate in the query.
    assert _visible(app_session, "agent_profiles", "user_test") == 2
    assert _visible(app_session, "agent_profiles", "user_other") == 1
    assert _visible(app_session, "agent_profiles", None) == 1
    assert _visible(app_session, "agent_memories", "user_test") == 2
    assert _visible(app_session, "agent_memories", "user_other") == 1
    assert _visible(app_session, "agent_profiles", "user_test", workspace_id=WORKSPACE_A2) == 0
    assert _visible(app_session, "agent_profiles", "user_test", TENANT_B, WORKSPACE_B) == 0
    assert client.get("/api/v1/agents", headers=headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)).json()["items"] == []

    # Every change was audited in the same transaction, without instruction text.
    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        actions = session.execute(
            text("select action, metadata::text as record_metadata from audit_records where action like 'agent%' order by created_at")
        ).all()
    assert [row.action for row in actions] == ["agent.created", "agent.created", "agent_memory.created", "agent_memory.created"]
    assert "secret sauce" not in " ".join(row.record_metadata for row in actions)


def test_rls_refuses_writing_another_users_private_agent(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    private = _create(client, name="Mine")
    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        session.execute(text("select set_config('anum.user_id', 'user_other', true)"))
        updated = session.execute(
            text("update agent_profiles set name = 'stolen' where id = :id"), {"id": private["id"]}
        ).rowcount
        assert updated == 0
    # Inserting a private agent for someone else is refused by the policy's WITH CHECK.
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text("select set_config('anum.user_id', 'user_other', true)"))
            session.execute(
                text(
                    """
                    insert into agent_profiles (tenant_id, workspace_id, id, owner_user_id, name, icon, color,
                        category, model_route, knowledge_mode, status, sharing, created_at, updated_at)
                    values (:tenant, :workspace, 'agent_forged', 'user_test', 'x', 'bot', '#000000',
                        'custom', 'fast', 'none', 'active', 'private', now(), now())
                    """
                ),
                {"tenant": TENANT_A, "workspace": WORKSPACE_A},
            )
    # A memory cannot be attached to an agent the writer cannot see.
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text("select set_config('anum.user_id', 'user_other', true)"))
            session.execute(
                text(
                    """
                    insert into agent_memories (tenant_id, workspace_id, id, agent_id, content, source, created_by, created_at)
                    values (:tenant, :workspace, 'agent_memory_x', :agent, 'x', 'manual', 'user_other', now())
                    """
                ),
                {"tenant": TENANT_A, "workspace": WORKSPACE_A, "agent": private["id"]},
            )


def test_deleting_an_agent_deletes_its_memories_but_keeps_its_tasks(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    agent = _create(client, sharing="workspace")
    client.post(f"/api/v1/agents/{agent['id']}/memories", headers=headers(), json={"content": "fact"})
    task = client.post(
        "/api/v1/tasks", headers=headers(), json={"title": "t", "prompt": "summarize", "agent_id": agent["id"]}
    ).json()
    assert client.delete(f"/api/v1/agents/{agent['id']}", headers=headers()).status_code == 204
    assert _visible(app_session, "agent_memories", "user_test") == 0
    kept = client.get(f"/api/v1/tasks/{task['id']}", headers=headers()).json()
    assert kept["agent_id"] == agent["id"]


def test_run_as_an_agent_on_postgresql(client: TestClient) -> None:
    agent = _create(client, instructions="One line answers.", sharing="workspace")
    client.post(f"/api/v1/agents/{agent['id']}/memories", headers=headers(), json={"content": "Depot is in Jeddah"})
    task = client.post(
        "/api/v1/tasks", headers=headers("user_other"), json={"title": "Plan", "prompt": "Summarize", "agent_id": agent["id"]}
    ).json()
    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers("user_other"))
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["task"]["status"] == "completed" and body["run"]["checkpoint"]["agent"]["id"] == agent["id"]
    assert any(step["metadata"].get("source") == "agent_memory" for step in body["run"]["steps"])
    client.post("/api/v1/tasks", headers=headers(), json={"title": "Plain", "prompt": "no agent"})
    recent = client.get(f"/api/v1/tasks?agent_id={agent['id']}", headers=headers()).json()
    assert [t["id"] for t in recent] == [task["id"]]
    latest = client.get(f"/api/v1/tasks/{task['id']}/latest-run", headers=headers()).json()
    assert latest["checkpoint"]["agent"]["version"] == 1


def test_agents_load_installed_skills_on_postgresql(client: TestClient) -> None:
    """docs/agents.md#skills: the loadout, the usage and a run read skills and agents under RLS."""
    org_admin = headers("user_admin", "owner,org_admin")
    published = client.post(
        "/api/v1/skills/versions", headers=org_admin,
        json=signed(client, org_admin, {"skill_id": "acme.pg-notes", "version": "1.0.0", "name": "Notes", "description": "House style",
                                        "instructions": "Write one line per change.", "required_tools": ["anum.respond"]}),
    )
    assert published.status_code == 201, published.text
    installed = client.post(
        "/api/v1/skills/installations", headers=headers("user_boss", "owner"),
        json={"skill_id": "acme.pg-notes", "version": "1.0.0", "approved_tools": ["anum.respond"]},
    )
    assert installed.status_code == 201, installed.text
    shared = _create(client, name="Team", sharing="workspace", allowed_skills=["acme.pg-notes"])
    _create(client, name="Mine", allowed_skills=["acme.pg-notes"])
    agent_store.clear()  # restart: read back from PostgreSQL

    loadout = client.get(f"/api/v1/agents/{shared['id']}/skills", headers=headers("user_other")).json()
    assert [(i["id"], i["state"], i["version"]) for i in loadout["items"]] == [("acme.pg-notes", "ready", "1.0.0")]
    usage = client.get("/api/v1/agents/skills/usage", headers=headers("user_other")).json()["items"]
    assert [a["name"] for a in usage[0]["agents"]] == ["Team"]  # the private agent stays private under RLS
    assert client.get("/api/v1/agents/skills/usage", headers=headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)).json()["items"] == []

    task = client.post(
        "/api/v1/tasks", headers=headers("user_other"), json={"title": "Notes", "prompt": "Summarize", "agent_id": shared["id"]}
    ).json()
    run = client.post(f"/api/v1/tasks/{task['id']}/run", headers=headers("user_other")).json()
    assert run["run"]["checkpoint"]["agent"]["loaded_skills"][0]["instructions"] == "Write one line per change."
    model_step = next(step for step in run["run"]["steps"] if step["type"] == "model_call")
    assert model_step["metadata"]["loaded_skills"] == [{"id": "acme.pg-notes", "version": "1.0.0"}]


def test_agent_chat_threads_on_postgresql(client: TestClient) -> None:
    agent = _create(client, name="Chatty")
    general = client.post("/api/v1/conversations", headers=headers(), json={}).json()
    thread = client.post("/api/v1/conversations", headers=headers(), json={"agent_id": agent["id"]}).json()
    assert [c["id"] for c in client.get("/api/v1/conversations", headers=headers()).json()] == [general["id"]]
    listed = client.get(f"/api/v1/conversations?agent_id={agent['id']}", headers=headers()).json()
    assert [c["id"] for c in listed] == [thread["id"]] and listed[0]["agent_id"] == agent["id"]
    reply = client.post(f"/api/v1/conversations/{thread['id']}/ask", headers=headers(), json={"content": "Hi"})
    assert reply.status_code == 200 and "event: done" in reply.text


def test_updates_are_versioned_on_postgresql(client: TestClient) -> None:
    agent = _create(client)
    first = client.patch(f"/api/v1/agents/{agent['id']}", headers=headers(), json={"status": "paused", "version": 1})
    assert first.status_code == 200 and first.json()["version"] == 2
    agent_store.clear()
    stale = client.patch(f"/api/v1/agents/{agent['id']}", headers=headers(), json={"name": "x", "version": 1})
    assert stale.status_code == 409
    assert client.get(f"/api/v1/agents/{agent['id']}", headers=headers()).json()["status"] == "paused"
