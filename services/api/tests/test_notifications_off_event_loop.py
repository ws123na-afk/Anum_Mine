"""The notification routes never open a database unit of work on the event loop (memory backend).

One event loop serves every user (docs/realtime.md#event-loop). A team store or repository
opened inside an ``async def`` route blocks every other request while it waits for a pooled
connection or a row lock. The Notifications page and the bell call these routes often (each
open, each filter, each change), so they run their unit of work in a worker thread: plain
``def`` routes, which FastAPI runs in its threadpool. This test wraps the store openers and
records any call made from a thread that is running an event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import dependencies, team_work
from anum_api.main import app, store
from anum_api.notifications import create_notification, reset_test_email_limits
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.settings import settings
from anum_api.team_store import memory_team_store

TENANT, WORKSPACE = "tenant_ntf_loop", "workspace_ntf_loop"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_loop", "x-user-roles": "owner"}
AHMED = {**OWNER, "x-user-id": "user_ahmed_loop", "x-user-roles": "member"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    monkeypatch.setattr(settings, "smtp_host", None)
    memory_team_store.clear()
    reset_test_email_limits()
    test_client = TestClient(app)
    assert test_client.post("/api/v1/tenants", headers=OWNER, json={"name": "Loop Co"}).status_code == 201
    assert test_client.post("/api/v1/workspaces", headers=OWNER, json={"name": "Main"}).status_code == 201
    assert test_client.post("/api/v1/workspace-memberships/current", headers=OWNER).status_code == 201
    now = utc_now()
    dependencies.memory_repository.save_membership(
        WorkspaceMembership(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="user_ahmed_loop", role="member", created_at=now, updated_at=now)
    )
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_loop", roles=["owner"])
    for n in range(2):
        create_notification(
            memory_team_store, context, user_id="user_ahmed_loop", kind="calendar_reminder",
            title=f"Reminder {n}", body="Supplier meeting at 3.", in_app_only=True,
        )
    yield test_client
    memory_team_store.clear()
    for name in ("tenants", "workspaces", "memberships"):
        getattr(store, name).clear()


@pytest.fixture
def on_loop(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Names of store openers called on the event loop."""
    calls: list[str] = []

    def guard(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        @contextmanager
        def wrapped(*args: Any, **kwargs: Any) -> Iterator[Any]:
            try:
                asyncio.get_running_loop()
                calls.append(name)
            except RuntimeError:
                pass  # a worker thread: allowed
            with original(*args, **kwargs) as unit:
                yield unit

        return wrapped

    # notifications._store_for_caller imports open_team_unit from team_work at call time.
    monkeypatch.setattr(team_work, "open_team_unit", guard("open_team_unit", team_work.open_team_unit))
    monkeypatch.setattr(dependencies, "_unit_of_work", guard("_unit_of_work", dependencies._unit_of_work))
    yield calls


def test_every_notification_route_works_off_the_event_loop(client: TestClient, on_loop: list[str]) -> None:
    page = client.get("/api/v1/notifications?state=unread&kind=calendar_reminder", headers=AHMED)
    assert page.status_code == 200, page.text
    assert page.json()["unread"] == 2
    first = page.json()["items"][0]["id"]

    kinds = client.get("/api/v1/notifications/kinds", headers=AHMED)
    assert kinds.json()["items"] == [{"kind": "calendar_reminder", "total": 2, "unread": 2}]
    assert client.post(f"/api/v1/notifications/{first}/read", headers=AHMED).json()["read_at"] is not None
    assert client.post(f"/api/v1/notifications/{first}/read", headers=OWNER).status_code == 404
    assert client.post(f"/api/v1/notifications/{first}/unread", headers=AHMED).json()["read_at"] is None
    assert client.post("/api/v1/notifications/read-all", headers=AHMED).json() == {"updated": 2}
    assert client.delete(f"/api/v1/notifications/{first}", headers=AHMED).status_code == 204
    assert client.get("/api/v1/notification-channels", headers=AHMED).status_code == 200
    test_email = client.post("/api/v1/notification-channels/email-test", headers=AHMED)
    assert test_email.json()["status"] == "not_configured", test_email.text

    assert on_loop == [], f"opened on the event loop: {on_loop}"
