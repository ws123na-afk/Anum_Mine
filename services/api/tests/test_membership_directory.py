"""The caller's own memberships across the workspaces of their tenant (in-memory backend).

``GET /api/v1/me/workspace-memberships``; the PostgreSQL path and its role are covered by
``test_postgres_membership_directory.py``. See docs/identity.md#my-workspaces.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anum_api import dependencies
from anum_api.dependencies import memory_repository
from anum_api.main import app, store
from anum_api.schemas import Tenant, Workspace, WorkspaceMembership, utc_now
from anum_api.settings import settings

from test_identity import FakeJwksEndpoint, SigningKey, bearer, make_validator

TENANT = "tenant_dir"
OTHER_TENANT = "tenant_dir_other"
ROUTE = "/api/v1/me/workspace-memberships"


def headers(user: str = "user_dir", workspace: str = "workspace_dir_a", tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": "member"}


def _clear() -> None:
    store.tenants.clear()
    store.workspaces.clear()
    store.memberships.clear()


def _member(tenant: str, workspace: str, user: str, role: str = "member", active: bool = True) -> None:
    now = utc_now()
    memory_repository.save_membership(
        WorkspaceMembership(
            tenant_id=tenant, workspace_id=workspace, user_id=user, role=role, active=active,
            created_at=now, updated_at=now,
        )
    )


def _seed() -> None:
    now = utc_now()
    for tenant in (TENANT, OTHER_TENANT):
        store.tenants[tenant] = Tenant(id=tenant, name=tenant, created_at=now, updated_at=now)
    for tenant, workspace, name in (
        (TENANT, "workspace_dir_a", "Operations"),
        (TENANT, "workspace_dir_b", "Finance"),
        (TENANT, "workspace_dir_c", "Legal"),
        (OTHER_TENANT, "workspace_dir_x", "Elsewhere"),
    ):
        store.workspaces[workspace] = Workspace(
            id=workspace, tenant_id=tenant, name=name, created_at=now, updated_at=now
        )
    _member(TENANT, "workspace_dir_a", "user_dir", "owner")
    _member(TENANT, "workspace_dir_b", "user_dir", "viewer")
    _member(TENANT, "workspace_dir_c", "user_dir", "member", active=False)  # deactivated
    _member(TENANT, "workspace_dir_c", "someone_else", "owner")
    _member(OTHER_TENANT, "workspace_dir_x", "user_dir", "owner")  # another tenant
    _member(TENANT, "workspace_dir_unnamed", "user_dir", "member")  # no workspace record


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    _clear()
    _seed()
    try:
        yield TestClient(app)
    finally:
        _clear()


def test_lists_only_the_callers_active_memberships_in_their_tenant(client: TestClient) -> None:
    response = client.get(ROUTE, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json() == [
        {"tenant_id": TENANT, "workspace_id": "workspace_dir_unnamed", "workspace_name": None,
         "role": "member", "status": "active"},
        {"tenant_id": TENANT, "workspace_id": "workspace_dir_b", "workspace_name": "Finance",
         "role": "viewer", "status": "active"},
        {"tenant_id": TENANT, "workspace_id": "workspace_dir_a", "workspace_name": "Operations",
         "role": "owner", "status": "active"},
    ]


def test_needs_no_membership_in_the_selected_workspace(client: TestClient) -> None:
    # Deactivated in workspace C: the caller can still find where they may switch to.
    response = client.get(ROUTE, headers=headers(workspace="workspace_dir_c"))
    assert response.status_code == 200
    assert {item["workspace_id"] for item in response.json()} == {
        "workspace_dir_a", "workspace_dir_b", "workspace_dir_unnamed",
    }


def test_another_user_and_another_tenant_see_their_own_lists(client: TestClient) -> None:
    assert [item["workspace_id"] for item in client.get(ROUTE, headers=headers("someone_else")).json()] == [
        "workspace_dir_c"
    ]
    other = client.get(ROUTE, headers=headers(workspace="workspace_dir_x", tenant=OTHER_TENANT)).json()
    assert [(item["tenant_id"], item["workspace_id"]) for item in other] == [(OTHER_TENANT, "workspace_dir_x")]
    assert client.get(ROUTE, headers=headers("nobody")).json() == []


def test_requires_an_authenticated_caller(client: TestClient) -> None:
    assert client.get(ROUTE).status_code == 401


def test_oidc_takes_the_tenant_and_user_from_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    key = SigningKey("kid-dir")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "repository_backend", "memory")
    dependencies.set_oidc_validator(make_validator(FakeJwksEndpoint(key)))
    _clear()
    _seed()
    try:
        client = TestClient(app)
        token = key.sign(sub="user_dir", tenant_id=TENANT, workspace_id="workspace_dir_a")
        response = client.get(ROUTE, headers=bearer(token))
        assert response.status_code == 200, response.text
        assert [item["workspace_id"] for item in response.json()] == [
            "workspace_dir_unnamed", "workspace_dir_b", "workspace_dir_a",
        ]
        # A header naming another tenant is refused, never used.
        assert client.get(ROUTE, headers=bearer(token, **{"x-tenant-id": OTHER_TENANT})).status_code == 403
        assert client.get(ROUTE, headers={"authorization": "Bearer not-a-jwt"}).status_code == 401
    finally:
        dependencies.set_oidc_validator(None)
        _clear()
