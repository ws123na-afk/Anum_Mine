"""Developer-mode requests in PostgreSQL (migration 0022_developer_requests).

The API runs as the non-owner ``anum_test_app`` role against a fake GitHub. Requests are
scoped to their tenant and workspace by forced RLS; "restart" clears the in-memory store
so everything is read back from PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from test_developer import PAT, REPO, FakeGitHub

from anum_api import developer
from anum_api.db import session as db_session
from anum_api.developer import developer_settings, developer_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user_id: str = "owner_test") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "owner"}


A = headers()


@pytest.fixture
def github(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    fake = FakeGitHub()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(developer, "github_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(developer_settings, "github_repo", REPO)
    monkeypatch.setattr(developer_settings, "github_token", SecretStr(PAT))
    monkeypatch.setattr(developer_settings, "tenant_id", TENANT_A)
    monkeypatch.setattr(developer_settings, "workspace_id", "")
    monkeypatch.setattr(developer_settings, "status_cache_seconds", 0.0)
    developer_store.clear()
    yield fake
    developer_store.clear()


@pytest.fixture
def client(github: FakeGitHub) -> TestClient:
    return TestClient(app)


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return int(connection.execute(text(f"select count(*) from {table}")).scalar_one())


def test_requests_persist_and_merge_through_an_approval(client: TestClient, github: FakeGitHub, database_engine: Engine) -> None:
    created = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"})
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert _count(database_engine, "developer_requests") == 1
    developer_store.clear()  # restart: read back from PostgreSQL

    github.start_run(request_id, status="completed", conclusion="success")
    github.open_pull(request_id)
    github.set_ci("a" * 40, "success")
    listed = client.get("/api/v1/developer/requests", headers=A).json()
    assert [(item["id"], item["status"], item["pr_number"]) for item in listed] == [(request_id, "waiting_approval", 7)]
    assert listed[0]["checks"]["passed"] == 1

    approval = client.post(f"/api/v1/developer/requests/{request_id}/merge-approval", headers=A).json()["approval"]
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert len(github.merges) == 1
    merged = client.get(f"/api/v1/developer/requests/{request_id}", headers=A).json()
    assert merged["status"] == "merged" and merged["merge_commit_sha"] == "m" * 40
    with database_engine.connect() as connection:
        actions = set(connection.execute(text("select action from audit_records where correlation_id = :id"), {"id": request_id}).scalars())
    assert {"developer.request.created", "developer.merge.requested", "developer.merge.completed"} <= actions


def test_requests_are_isolated_by_tenant_and_workspace(client: TestClient, github: FakeGitHub) -> None:
    request_id = client.post("/api/v1/developer/requests", headers=A, json={"text": "Add a dark/light toggle to settings"}).json()["id"]
    for other in (headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)):
        assert client.get("/api/v1/developer/requests", headers=other).json() == []
        assert client.get(f"/api/v1/developer/requests/{request_id}", headers=other).status_code == 404


def test_forced_rls_refuses_rows_outside_the_session_scope(github: FakeGitHub, database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_A})
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "insert into developer_requests (id, tenant_id, workspace_id, created_by, text, source, status, branch) "
                    "values ('dev_x', :t, :w, 'u', 'text', 'text', 'queued', 'anum-dev/dev_x')"
                ),
                {"t": TENANT_B, "w": WORKSPACE_B},
            )
        transaction.rollback()

