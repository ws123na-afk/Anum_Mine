"""PostgreSQL: paged approvals, filters, search and summary under RLS (migration
0086_approvals_paging, docs/approvals-and-risk.md#paging-filters-and-search)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from anum_api.approval_paging import ApprovalFilter
from anum_api.db.repository import SqlAlchemyRepository
from anum_api.schemas import utc_now

from conftest import TENANT_A, WORKSPACE_A, WORKSPACE_A2, tenant_context
from test_postgres_runtime import HEADERS, postgres_client  # noqa: F401 (fixture)

pytestmark = pytest.mark.database


def _waiting(client: TestClient, title: str) -> dict:
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": title, "prompt": "Publish the final update"})
    assert task.status_code == 201, task.text
    started = client.post(f"/api/v1/tasks/{task.json()['id']}/run", headers=HEADERS)
    assert started.status_code == 200, started.text
    return started.json()["approval"]


def test_pages_filters_search_and_summary(postgres_client: TestClient) -> None:  # noqa: F811
    client = postgres_client
    first = _waiting(client, "Spring launch")
    second = _waiting(client, "Autumn launch")
    third = _waiting(client, "Winter launch")
    rejected = client.post(
        f"/api/v1/approvals/{first['id']}/reject", headers=HEADERS, json={"payload_hash": first["payload_hash"]}
    )
    assert rejected.status_code == 200, rejected.text

    page = client.get("/api/v1/approvals/page", headers=HEADERS, params={"limit": 2})
    assert page.status_code == 200, page.text
    body = page.json()
    # Pending first, newest first; the rejected one comes after every pending one.
    assert [item["id"] for item in body["items"]] == [third["id"], second["id"]]
    assert body["next_cursor"]
    rest = client.get("/api/v1/approvals/page", headers=HEADERS, params={"limit": 2, "cursor": body["next_cursor"]})
    assert [item["id"] for item in rest.json()["items"]] == [first["id"]]
    assert rest.json()["next_cursor"] is None

    pending = client.get("/api/v1/approvals/page", headers=HEADERS, params={"status": "pending"}).json()
    assert {item["id"] for item in pending["items"]} == {second["id"], third["id"]}
    decided = client.get("/api/v1/approvals/page", headers=HEADERS, params={"status": "decided"}).json()
    assert [item["id"] for item in decided["items"]] == [first["id"]]
    assert decided["items"][0]["status"] == "rejected"

    searched = client.get("/api/v1/approvals/page", headers=HEADERS, params={"query": "autumn"}).json()
    assert [item["id"] for item in searched["items"]] == [second["id"]]
    literal = client.get("/api/v1/approvals/page", headers=HEADERS, params={"query": "%"}).json()
    assert literal["items"] == []
    by_task = client.get("/api/v1/approvals/page", headers=HEADERS, params={"task_id": third["task_id"]}).json()
    assert [item["id"] for item in by_task["items"]] == [third["id"]]

    summary = client.get("/api/v1/approvals/summary", headers=HEADERS)
    assert summary.status_code == 200
    assert summary.json() == {"pending": 2, "approved": 0, "rejected": 1, "expired": 0}


def test_another_workspace_sees_none_of_them(
    postgres_client: TestClient,  # noqa: F811
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    _waiting(postgres_client, "Private launch")
    now = utc_now()
    with repository_factory(tenant_context(TENANT_A, WORKSPACE_A)) as repository:
        own, _ = repository.list_approvals_page(
            tenant_context(TENANT_A, WORKSPACE_A), now=now, status=ApprovalFilter.ALL, query=None,
            task_id=None, cursor=None, limit=10,
        )
    with repository_factory(tenant_context(TENANT_A, WORKSPACE_A2)) as repository:
        other_context = tenant_context(TENANT_A, WORKSPACE_A2)
        foreign, more = repository.list_approvals_page(
            other_context, now=now, status=ApprovalFilter.ALL, query=None, task_id=None, cursor=None, limit=10,
        )
        summary = repository.summarize_approvals(other_context, now)
    assert len(own) == 1
    assert foreign == [] and more is False
    assert summary.pending == 0


def test_paging_indexes_exist(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        indexes = dict(
            connection.execute(
                text("select indexname, indexdef from pg_indexes where tablename = 'approvals'")
            ).all()
        )
    assert "ix_approvals_scope_created" in indexes
    assert "ix_approvals_scope_pending" in indexes
    assert "WHERE" in indexes["ix_approvals_scope_pending"]
