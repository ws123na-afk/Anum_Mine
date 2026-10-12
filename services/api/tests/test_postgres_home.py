"""Home in PostgreSQL (migration 0046_home, docs/home.md).

The API runs as the non-owner ``anum_test_app`` role. Pins and insight
states are private to one user in one workspace, the greeting name to one user in one
tenant: the forced RLS policies check ``anum.user_id`` as well as the scope. "Restart"
clears the in-memory store, so everything is read back from PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.home import home_store
from anum_api.main import app
from anum_api.schemas import Task, TaskStatus, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database

TABLES = ("home_profiles", "home_pins", "home_insight_states")


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "member"}


@pytest.fixture
def app_factory(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    home_store.clear()
    yield factory
    home_store.clear()


@pytest.fixture
def client(app_factory: sessionmaker[Session]) -> TestClient:
    return TestClient(app)


def _visible(app_session: Callable[..., Iterator[Session]], table: str, user_id: str | None, workspace_id: str = WORKSPACE_A) -> int:
    tenant_id = TENANT_A if workspace_id != WORKSPACE_B else TENANT_B
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": user_id})
        # No predicate: only RLS decides what is visible.
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608


def test_pins_states_and_name_persist_per_user(client: TestClient, app_factory: sessionmaker[Session]) -> None:
    pin = client.post("/api/v1/home/pins", headers=headers(), json={"kind": "link", "label": "TyrePulse", "target": "https://tyrepulse.example"})
    assert pin.status_code == 201, pin.text
    second = client.post("/api/v1/home/pins", headers=headers(), json={"kind": "feature", "label": "Reports", "target": "reports"})
    assert second.status_code == 201, second.text
    assert client.put("/api/v1/home/profile", headers=headers(), json={"display_name": "Shahzeb"}).status_code == 200

    # A failed task gives an insight to dismiss.
    with app_factory() as session:
        db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A)
        from anum_api.db.repository import SqlAlchemyRepository

        now = utc_now()
        SqlAlchemyRepository(session, created_by_user_id="user_test").create_task(
            Task(id="task_home_failed", title="Quotation", prompt="compare", status=TaskStatus.FAILED,
                 tenant_id=TENANT_A, workspace_id=WORKSPACE_A, created_at=now, updated_at=now, created_by="user_test")
        )
        session.commit()
    dismissed = client.post("/api/v1/home/insights/tasks:failed:task_home_failed/dismiss", headers=headers())
    assert dismissed.status_code == 200, dismissed.text

    home_store.clear()  # restart: read back from PostgreSQL
    ids = [p["id"] for p in client.get("/api/v1/home/pins", headers=headers()).json()]
    assert ids == [pin.json()["id"], second.json()["id"]]
    reordered = client.put("/api/v1/home/pins/order", headers=headers(), json={"ids": list(reversed(ids))})
    assert [p["id"] for p in reordered.json()] == list(reversed(ids))
    summary = client.get("/api/v1/home/summary", headers=headers())
    assert summary.status_code == 200, summary.text
    assert summary.json()["profile"]["display_name"] == "Shahzeb"
    insights = client.get("/api/v1/home/insights", headers=headers()).json()
    assert insights["hidden"] == 1 and all(i["key"] != "tasks:failed:task_home_failed" for i in insights["items"])

    # Another user, workspace or tenant sees none of it.
    assert client.get("/api/v1/home/pins", headers=headers("user_other")).json() == []
    assert client.get("/api/v1/home/pins", headers=headers(workspace_id=WORKSPACE_A2)).json() == []
    assert client.get("/api/v1/home/summary", headers=headers("user_other")).json()["profile"]["display_name"] is None
    assert client.delete(f"/api/v1/home/pins/{ids[0]}", headers=headers("user_other")).status_code == 404
    assert client.delete(f"/api/v1/home/pins/{ids[0]}", headers=headers()).status_code == 204
    audit = client.get("/api/v1/audit-records", headers={**headers(), "x-user-roles": "owner"}, params={"action": "home.*"})
    assert audit.status_code == 200, audit.text
    assert {item["action"] for item in audit.json()["items"]} >= {"home.pin_added", "home.pin_removed", "home.insight_dismissed"}


def test_rows_are_private_to_their_user_under_rls(
    client: TestClient, app_session: Callable[..., Iterator[Session]], database_engine: Engine
) -> None:
    client.post("/api/v1/home/pins", headers=headers(), json={"kind": "feature", "label": "Tasks", "target": "tasks"})
    client.put("/api/v1/home/profile", headers=headers(), json={"display_name": "Shahzeb"})
    for table in ("home_pins", "home_profiles"):
        assert _visible(app_session, table, "user_test") == 1, table
        assert _visible(app_session, table, "user_other") == 0, table
        assert _visible(app_session, table, None) == 0, table
        assert _visible(app_session, table, "user_test", WORKSPACE_B) == 0, table
    assert _visible(app_session, "home_pins", "user_test", WORKSPACE_A2) == 0

    # WITH CHECK refuses writing a row for another user or workspace.
    inserts = (
        ("insert into home_pins (id, tenant_id, workspace_id, user_id, kind, label, target) "
         "values ('pin_x', :t, :w, :u, 'feature', 'x', 'tasks')"),
        ("insert into home_insight_states (tenant_id, workspace_id, user_id, insight_key, state) "
         "values (:t, :w, :u, 'tasks:x', 'dismissed')"),
    )
    for statement in inserts:
        for workspace_id, user_id in ((WORKSPACE_A, "user_other"), (WORKSPACE_A2, "user_test")):
            with pytest.raises(DBAPIError):
                with app_session(tenant_context()) as session:
                    session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
                    session.execute(text(statement), {"t": TENANT_A, "w": workspace_id, "u": user_id})
    with app_session(tenant_context(), commit=True) as session:
        session.execute(text("select set_config('anum.user_id', 'user_other', true)"))
        assert session.execute(text("update home_pins set label = 'stolen'")).rowcount == 0
        assert session.execute(text("delete from home_profiles")).rowcount == 0

    with database_engine.connect() as connection:
        rows = connection.execute(
            text("select relname, relrowsecurity, relforcerowsecurity from pg_class where relname = any(:names)"),
            {"names": list(TABLES)},
        ).all()
    assert {row.relname for row in rows} == set(TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)


def test_saving_needs_an_onboarded_workspace(client: TestClient) -> None:
    response = client.post(
        "/api/v1/home/pins",
        headers=headers(tenant_id="tenant_not_onboarded", workspace_id="workspace_not_onboarded"),
        json={"kind": "feature", "label": "Tasks", "target": "tasks"},
    )
    assert response.status_code in {403, 409}


def test_delete_my_data_removes_home_rows_of_the_caller_only(client: TestClient) -> None:
    client.post("/api/v1/home/pins", headers=headers(), json={"kind": "feature", "label": "Tasks", "target": "tasks"})
    client.put("/api/v1/home/profile", headers=headers(), json={"display_name": "Shahzeb"})
    client.post("/api/v1/home/pins", headers=headers("user_other"), json={"kind": "feature", "label": "Tasks", "target": "tasks"})
    exported = client.get("/api/v1/privacy/export", headers=headers())
    assert exported.status_code == 200, exported.text
    assert [pin["label"] for pin in exported.json()["home"]["pins"]] == ["Tasks"]
    deleted = client.post("/api/v1/privacy/delete", headers=headers(), json={"confirm": "DELETE MY DATA"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"]["home_items"] == 2
    home_store.clear()
    assert client.get("/api/v1/home/pins", headers=headers()).json() == []
    assert client.get("/api/v1/home/summary", headers=headers()).json()["profile"]["display_name"] is None
    assert len(client.get("/api/v1/home/pins", headers=headers("user_other")).json()) == 1
