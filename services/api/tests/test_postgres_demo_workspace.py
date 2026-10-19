"""Sales demo workspace in PostgreSQL (migration ``0078_demo_records``), as the non-owner app role.

Seeding and reset run through the modules' own stores under forced RLS, inside the Demo
workspace's tenant context: tenant A's demo records and tags are invisible to tenant B, a
reset removes only tagged records, and a tag cannot be written into another scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.demo import demo_workspace_id
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
client = TestClient(app)


def headers(tenant_id: str, workspace_id: str) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": "owner_test", "x-user-roles": "owner"}


@pytest.fixture
def db(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield


def ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _count(engine: Engine, tenant_id: str, workspace_id: str, table: str) -> int:
    with engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": tenant_id})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": workspace_id})
        return connection.execute(text(f"select count(*) from {table}")).scalar_one()


def test_seed_and_reset_under_rls_are_isolated_per_tenant(db: None, database_engine: Engine) -> None:
    a = ok(client.post("/api/v1/demo/reset", headers=headers(TENANT_A, WORKSPACE_A)))
    demo_a = a["workspace_id"]
    assert demo_a == demo_workspace_id(TENANT_A) and a["records"] > 20
    for table in ("demo_records", "tasks", "business_accounts", "calendar_events", "marketing_posts", "workspace_files", "kb_collections"):
        assert _count(database_engine, TENANT_A, demo_a, table) > 0, table
        # Tenant B sees none of it, even naming A's demo workspace id.
        assert _count(database_engine, TENANT_B, demo_a, table) == 0, table
        assert _count(database_engine, TENANT_A, WORKSPACE_A, table if table != "tasks" else "demo_records") == 0, table

    b_status = ok(client.get("/api/v1/demo", headers=headers(TENANT_B, WORKSPACE_B)))
    assert b_status["exists"] is False

    # A hand-made record in the Demo workspace survives a reset; demo records are replaced.
    mine = client.post("/api/v1/calendar/events", headers=headers(TENANT_A, demo_a), json={
        "title": "My own meeting", "starts_at": "2030-01-02T09:00:00Z", "ends_at": "2030-01-02T10:00:00Z"})
    assert mine.status_code == 201, mine.text
    events_before = _count(database_engine, TENANT_A, demo_a, "calendar_events")
    again = ok(client.post("/api/v1/demo/reset", headers=headers(TENANT_A, WORKSPACE_A)))
    assert again["records"] == a["records"]
    assert _count(database_engine, TENANT_A, demo_a, "calendar_events") == events_before
    assert _count(database_engine, TENANT_A, demo_a, "business_accounts") == 3
    assert _count(database_engine, TENANT_A, demo_a, "demo_records") == a["records"]


def test_a_tag_cannot_be_written_into_another_scope(db: None, database_engine: Engine) -> None:
    ok(client.post("/api/v1/demo/reset", headers=headers(TENANT_A, WORKSPACE_A)))
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_B})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_B})
        with pytest.raises(DBAPIError):
            connection.execute(text(
                "insert into demo_records (id, tenant_id, workspace_id, kind, record_id) "
                "values ('demo_x', :t, :w, 'tasks', 'task_x')"), {"t": TENANT_A, "w": demo_workspace_id(TENANT_A)})
