"""Favorites, knowledge base and reports in PostgreSQL (migration 0042_library).

The API runs as the non-owner ``anum_test_app`` role. Favorites are private to one user;
private collections, their items and private reports are visible only to their owner,
shared ones to the workspace, and nothing crosses tenants or workspaces. Counts below run
with no predicate, so only RLS decides what is visible.
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
from anum_api.favorites import favorite_store
from anum_api.knowledge_base import knowledge_store
from anum_api.main import app
from anum_api.reports import report_store
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


ME = headers()
OTHER = headers("user_other")
A2 = headers(workspace_id=WORKSPACE_A2)
B = headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    for store in (favorite_store, knowledge_store, report_store):
        store.clear()
    yield TestClient(app)
    for store in (favorite_store, knowledge_store, report_store):
        store.clear()


def _count(app_session: Callable[..., Iterator[Session]], table: str, user_id: str | None,
           tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> int:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user_id})
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608


def _report(client: TestClient, hdrs: dict[str, str] = ME, **body: object) -> dict:
    response = client.post(
        "/api/v1/reports",
        headers=hdrs,
        json={"type": "tyre", "title": "Tyre cost", "metrics": [{"label": "Total", "value": "12,400"}],
              "data": {"columns": ["a"], "rows": [["x"]]}, **body},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_reports_persist_and_private_ones_stay_private(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    shared = _report(client)
    private = _report(client, visibility="private", title="Mine only")
    report_store.clear()  # restart: read back from PostgreSQL
    mine = client.get("/api/v1/reports", headers=ME).json()
    assert mine["total"] == 2 and {t["type"] for t in mine["types"]} == {"tyre"}
    full = client.get(f"/api/v1/reports/{shared['id']}", headers=ME).json()
    assert full["data"] == {"columns": ["a"], "rows": [["x"]]} and full["metrics"][0]["value"] == "12,400"
    assert [r["id"] for r in client.get("/api/v1/reports", headers=OTHER).json()["items"]] == [shared["id"]]
    assert client.get(f"/api/v1/reports/{private['id']}", headers=OTHER).status_code == 404
    assert client.get("/api/v1/reports", headers=A2).json()["total"] == 0
    assert client.get("/api/v1/reports", headers=B).json()["total"] == 0
    assert client.get("/api/v1/reports", headers=ME, params={"q": "mine"}).json()["total"] == 1

    assert _count(app_session, "reports", "user_test") == 2
    assert _count(app_session, "reports", "user_other") == 1
    assert _count(app_session, "reports", None) == 1
    assert _count(app_session, "reports", "user_test", TENANT_B, WORKSPACE_B) == 0

    # Making it private again, renaming and deleting go through the same rules.
    assert client.patch(f"/api/v1/reports/{shared['id']}", headers=ME, json={"visibility": "private"}).status_code == 200
    assert _count(app_session, "reports", "user_other") == 0
    assert client.delete(f"/api/v1/reports/{private['id']}", headers=ME).status_code == 204
    assert _count(app_session, "reports", "user_test") == 1


def test_private_report_rows_cannot_be_written_for_someone_else(app_session: Callable[..., Iterator[Session]], seed_scopes: None) -> None:
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(
                text(
                    "insert into reports (id, tenant_id, workspace_id, report_type, title, owner_user_id, created_by, created_by_kind) "
                    "values ('r1', :t, :w, 'custom', 'x', 'user_other', 'user_other', 'user')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(
                text(
                    "insert into reports (id, tenant_id, workspace_id, report_type, title, created_by, created_by_kind) "
                    "values ('r2', :t, :w, 'custom', 'x', 'u', 'user')"
                ),
                {"t": TENANT_B, "w": WORKSPACE_B},
            )


def test_favorites_are_private_to_their_user(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    report = _report(client)
    assert client.post("/api/v1/favorites", headers=ME, json={"item_type": "report", "item_id": report["id"]}).status_code == 201
    assert client.post("/api/v1/favorites", headers=ME, json={"item_type": "report", "item_id": report["id"]}).status_code == 200
    favorite_store.clear()
    assert client.get("/api/v1/favorites", headers=ME).json()["total"] == 1
    assert client.get("/api/v1/favorites", headers=OTHER).json()["total"] == 0
    assert _count(app_session, "favorites", "user_test") == 1
    assert _count(app_session, "favorites", "user_other") == 0
    assert _count(app_session, "favorites", None) == 0
    with app_session(tenant_context(), commit=True) as session:
        session.execute(text("select set_config('anum.user_id', 'user_other', true)"))
        assert session.execute(text("delete from favorites")).rowcount == 0
    assert client.delete("/api/v1/favorites", headers=ME, params={"item_type": "report", "item_id": report["id"]}).status_code == 204
    assert _count(app_session, "favorites", "user_test") == 0


def test_automation_step_stores_a_report_in_postgres(client: TestClient) -> None:
    from uuid import uuid4

    assert client.post("/api/v1/tasks", headers=ME, json={"title": "Check stock", "prompt": "Check stock"}).status_code == 201
    workflow = client.post(
        "/api/v1/automation/workflows",
        headers=ME,
        json={"name": "Weekly", "steps": [{"id": "r", "name": "R", "action": "report.generate",
                                           "input": {"type": "task_summary", "period_days": 7}}]},
    ).json()
    run = client.post(f"/api/v1/automation/workflows/{workflow['id']}/runs", headers={**ME, "Idempotency-Key": uuid4().hex})
    assert run.status_code in {200, 201}, run.text
    report_id = run.json()["steps"][0]["output"]["report_id"]
    stored = client.get(f"/api/v1/reports/{report_id}", headers=OTHER).json()
    assert stored["created_by_kind"] == "automation" and int(stored["metrics"][0]["value"]) >= 1


def test_knowledge_base_rows_and_notes_follow_collection_visibility(
    client: TestClient, app_session: Callable[..., Iterator[Session]]
) -> None:
    shared = client.post("/api/v1/knowledge/collections", headers=ME, json={"name": "Team"}).json()
    private = client.post("/api/v1/knowledge/collections", headers=ME, json={"name": "Drafts", "visibility": "private"}).json()
    for collection, content in ((shared, "Winter tyres from November."), (private, "Supplier margin is 14 percent.")):
        note = client.post(
            f"/api/v1/knowledge/collections/{collection['id']}/notes",
            headers=ME,
            json={"title": "Note", "content": content, "tags": ["policy"]},
        )
        assert note.status_code == 201, note.text
    knowledge_store.clear()
    assert [c["name"] for c in client.get("/api/v1/knowledge/collections", headers=ME).json()] == ["Drafts", "Team"]
    assert [c["name"] for c in client.get("/api/v1/knowledge/collections", headers=OTHER).json()] == ["Team"]
    assert client.get("/api/v1/knowledge/collections", headers=B).json() == []
    assert _count(app_session, "kb_collections", "user_test") == 2
    assert _count(app_session, "kb_items", "user_other") == 1
    assert _count(app_session, "kb_items", None) == 1

    items = client.get(f"/api/v1/knowledge/collections/{private['id']}/items", headers=ME).json()
    assert items["total"] == 1 and items["items"][0]["index"]["state"] == "indexed"
    assert client.get("/api/v1/knowledge/search", headers=OTHER, params={"q": "supplier margin"}).json()["hits"] == []
    mine = client.get("/api/v1/knowledge/search", headers=ME, params={"q": "supplier margin"}).json()
    assert mine["hits"] and mine["hits"][0]["collection_name"] == "Drafts"
    answer = client.post("/api/v1/knowledge/ask", headers=ME, json={"question": "supplier margin"}).json()
    assert answer["status"] == "answered" and answer["citations"][0]["provenance"] == "memory"

    # Deleting the collection removes its items and its notes' memories.
    assert client.delete(f"/api/v1/knowledge/collections/{private['id']}", headers=ME).status_code == 204
    assert _count(app_session, "kb_items", "user_test") == 1
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        remaining = session.execute(
            text("select count(*) from memories where provenance->>'source_type' = 'knowledge_base'")
        ).scalar_one()
    assert remaining == 1


def test_privacy_delete_removes_only_my_library_rows(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    shared = _report(client)
    _report(client, visibility="private", title="Mine")
    _report(client, OTHER, visibility="private", title="Theirs")
    client.post("/api/v1/favorites", headers=ME, json={"item_type": "report", "item_id": shared["id"]})
    client.post("/api/v1/favorites", headers=OTHER, json={"item_type": "report", "item_id": shared["id"]})
    client.post("/api/v1/knowledge/collections", headers=ME, json={"name": "Drafts", "visibility": "private"})
    client.post("/api/v1/knowledge/collections", headers=ME, json={"name": "Team"})
    summary = client.get("/api/v1/privacy", headers=ME).json()["private"]
    assert (summary["favorites"], summary["private_collections"], summary["private_reports"]) == (1, 1, 1)
    assert client.get("/api/v1/privacy/export", headers=ME).json()["library"]["private_reports"][0]["title"] == "Mine"
    done = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"})
    assert done.status_code == 200, done.text
    assert _count(app_session, "reports", "user_test") == 1  # the shared one stays
    assert _count(app_session, "reports", "user_other") == 2
    assert _count(app_session, "favorites", "user_test") == 0
    assert _count(app_session, "favorites", "user_other") == 1
    assert _count(app_session, "kb_collections", "user_test") == 1
