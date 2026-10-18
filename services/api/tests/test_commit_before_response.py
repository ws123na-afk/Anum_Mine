"""A request's unit of work commits before the client sees the response.

FastAPI closes a ``yield`` dependency with the default ("request") scope only after the
response has been sent. The units of work commit in that teardown, so a client could read
a 200 and send its next request before the write was committed. The authenticated
journey hit exactly that: ``PUT /api/v1/onboarding`` answered 200, the SSE request that
followed checked the membership on another connection before the onboarding transaction
had committed, and was refused with 403 "Active workspace membership required"
(docs/realtime.md#commit-order). The units of work therefore run with
``scope="function"``: they commit, and publish their events, before the response starts.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from anum_api import dependencies
from anum_api.main import app, store
from anum_api.settings import settings

OWNER = {
    "x-tenant-id": "tenant_commit_order",
    "x-workspace-id": "workspace_commit_order",
    "x-user-id": "user_commit_order",
    "x-user-roles": "owner",
}


def _send(method: str, path: str, *, json: dict[str, Any] | None, order: list[str]) -> httpx.Response:
    """Call the ASGI app and note when the response starts, next to the commits."""

    async def recording_app(scope: dict, receive: Any, send: Any) -> None:
        async def recording_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                order.append("response.start")
            await send(message)

        await app(scope, receive, recording_send)

    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=recording_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=OWNER, json=json)

    return asyncio.run(call())


@pytest.fixture
def memory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    store.tenants.clear()
    store.workspaces.clear()
    store.memberships.clear()


def test_a_write_is_committed_before_the_response_starts(memory_backend: None, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    onboarded = _send(
        "PUT",
        "/api/v1/onboarding",
        json={"organization_name": "Commit Order Org", "workspace_name": "Commit Order Workspace"},
        order=[],
    )
    assert onboarded.status_code == 200, onboarded.text

    real_after_commit = dependencies.event_runtime.after_commit

    def recording_after_commit(events: Any) -> None:
        order.append("commit")
        real_after_commit(events)

    monkeypatch.setattr(dependencies.event_runtime, "after_commit", recording_after_commit)
    created = _send("POST", "/api/v1/tasks", json={"title": "Commit order", "prompt": "Check the order"}, order=order)

    assert created.status_code == 201, created.text
    assert order == ["commit", "response.start"]


@pytest.mark.database
def test_onboarding_commits_the_membership_before_the_response_starts(
    database_engine: Any, clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journey's case against PostgreSQL: the tenant, workspace and owner membership
    are committed before the 200, so the next request's membership check finds them."""
    from conftest import APP_ROLE
    from sqlalchemy import event, text
    from sqlalchemy.orm import sessionmaker

    from anum_api.db import session as db_session

    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)
    order: list[str] = []

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session: Any, transaction: Any, connection: Any) -> None:
        connection.execute(text(f"set local role {APP_ROLE}"))

    @event.listens_for(factory, "after_commit")
    def _note_commit(session: Any) -> None:
        order.append("commit")

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")

    onboarded = _send(
        "PUT",
        "/api/v1/onboarding",
        json={"organization_name": "Commit Order Org", "workspace_name": "Commit Order Workspace"},
        order=order,
    )
    assert onboarded.status_code == 200, onboarded.text
    assert onboarded.json()["membership"]["role"] == "owner"
    assert "commit" in order and order.index("commit") < order.index("response.start")

    # The very next request, on a new connection, is a member.
    tasks = _send("GET", "/api/v1/tasks", json=None, order=[])
    assert tasks.status_code == 200, tasks.text
