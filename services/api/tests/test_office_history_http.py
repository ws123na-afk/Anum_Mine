"""Office history over HTTP: the new read-only since, until and q filters, the permission gate and
workspace isolation, against the in-memory repository (no new storage, no new role)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from anum_api.audit import AuditRecord
from anum_api.dependencies import memory_repository
from anum_api.main import app, store
from anum_api.settings import settings

TENANT = "tenant_history"
WORKSPACE = "workspace_history"
BASE = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


def headers_for(user_id: str, roles: str, workspace: str = WORKSPACE) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": workspace, "x-user-id": user_id, "x-user-roles": roles}


OWNER = headers_for("owner_1", "owner")
MEMBER = headers_for("member_1", "member")


def _clear() -> None:
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.tenants,
                       store.workspaces, store.memberships, store.invitations, store.audit_records):
        collection.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    _clear()
    test_client = TestClient(app)
    owner = headers_for("owner_1", "owner")
    assert test_client.post("/api/v1/tenants", headers=owner, json={"name": TENANT}).status_code == 201
    assert test_client.post("/api/v1/workspaces", headers=owner, json={"name": "History desk"}).status_code == 201
    assert test_client.post("/api/v1/workspace-memberships/current", headers=owner).status_code == 201
    try:
        yield test_client
    finally:
        _clear()


def seed(count: int, action: str = "office.room.rename", workspace: str = WORKSPACE) -> None:
    for index in range(count):
        memory_repository.record_audit(AuditRecord(
            id=f"audit_{workspace}_{action}_{index:03d}", tenant_id=TENANT, workspace_id=workspace,
            actor="owner_1", action=action, target=f"room_{index}", outcome="success",
            correlation_id=f"correlation_{index}", created_at=BASE + timedelta(minutes=index),
            metadata={"before": {"name": f"Old {index}"}, "after": {"name": f"New {index}"}},
        ))


def test_owner_filters_by_text_and_date_range(client: TestClient) -> None:
    seed(5)
    seed(2, action="task.created")
    body = client.get("/api/v1/office-history?q=ROOM_3", headers=OWNER).json()
    assert [i["target"] for i in body["items"]] == ["room_3"]
    assert body["items"][0]["metadata"]["after"] == {"name": "New 3"}
    window = client.get(
        "/api/v1/office-history?category=office&since=2026-10-20T10:01:00Z&until=2026-10-20T10:04:00Z",
        headers=OWNER,
    ).json()
    assert [i["target"] for i in window["items"]] == ["room_3", "room_2", "room_1"]


def test_a_reversed_range_is_refused(client: TestClient) -> None:
    response = client.get(
        "/api/v1/office-history?since=2026-10-21T00:00:00Z&until=2026-10-20T00:00:00Z", headers=OWNER)
    assert response.status_code == 422


def test_members_are_denied_with_the_stated_reason(client: TestClient) -> None:
    seed(1)
    for path in ("", "/correlations/correlation_0", "/at?at=2026-10-21T00:00:00Z"):
        response = client.get(f"/api/v1/office-history{path}", headers=MEMBER)
        assert response.status_code == 403, path
        assert "owners" in response.text.lower()


def test_another_workspace_is_never_read(client: TestClient) -> None:
    seed(2)
    seed(3, workspace="workspace_elsewhere")
    body = client.get("/api/v1/office-history?q=room", headers=OWNER).json()
    assert len(body["items"]) == 2
    assert all(item["id"].startswith(f"audit_{WORKSPACE}") for item in body["items"])
