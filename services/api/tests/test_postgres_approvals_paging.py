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


def test_a_lapsed_pending_approval_counts_as_decided_and_expired(
    postgres_client: TestClient,  # noqa: F811
    repository_factory: Callable[..., Iterator[SqlAlchemyRepository]],
) -> None:
    from datetime import timedelta

    approval = _waiting(postgres_client, "Lapsing launch")
    context = tenant_context(TENANT_A, WORKSPACE_A)
    later = utc_now() + timedelta(days=30)  # past ANUM_APPROVAL_TTL_SECONDS
    with repository_factory(context) as repository:
        pending, _ = repository.list_approvals_page(
            context, now=later, status=ApprovalFilter.PENDING, query=None, task_id=None, cursor=None, limit=10
        )
        decided, _ = repository.list_approvals_page(
            context, now=later, status=ApprovalFilter.DECIDED, query=None, task_id=None, cursor=None, limit=10
        )
        summary = repository.summarize_approvals(context, later)
    assert pending == []
    assert [item.id for item in decided] == [approval["id"]]
    assert (summary.pending, summary.expired) == (0, 1)


def test_the_plain_list_accepts_status_and_limit(postgres_client: TestClient) -> None:  # noqa: F811
    first = _waiting(postgres_client, "First")
    second = _waiting(postgres_client, "Second")
    everything = postgres_client.get("/api/v1/approvals", headers=HEADERS).json()
    assert [item["id"] for item in everything] == [first["id"], second["id"]]
    newest = postgres_client.get("/api/v1/approvals", headers=HEADERS, params={"status": "pending", "limit": 1}).json()
    assert [item["id"] for item in newest] == [second["id"]]


def test_decisions_between_pages_neither_repeat_nor_skip_a_row(postgres_client: TestClient) -> None:  # noqa: F811
    client = postgres_client
    created = [_waiting(client, f"Launch {n}") for n in range(5)]
    expected = [approval["id"] for approval in reversed(created)]
    seen: list[str] = []
    cursor = None
    decided = False
    while True:
        params: dict[str, object] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/v1/approvals/page", headers=HEADERS, params=params).json()
        seen += [item["id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
        if not decided:
            decided = True
            # One row already shown and one not yet shown are decided between the pages.
            for target in (created[4], created[1]):
                sent = client.post(
                    f"/api/v1/approvals/{target['id']}/reject",
                    headers=HEADERS,
                    json={"payload_hash": target["payload_hash"]},
                )
                assert sent.status_code == 200, sent.text
    assert seen == expected
