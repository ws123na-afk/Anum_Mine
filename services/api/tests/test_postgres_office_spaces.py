"""Office registry RLS and transaction tests against the non-owner PostgreSQL app role.

Requires migrated test DB (pytest -m database). Covers same identifiers in separate
workspaces, owner-only writes, revision conflicts and referential integrity.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
client = TestClient(app)


@pytest.fixture
def db(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield


def _headers(tenant: str = TENANT_A, workspace: str = WORKSPACE_A, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": "owner_test", "x-user-roles": role}


def _building() -> dict[str, object]:
    return {"id": "headquarters", "name": "HQ", "timezone": "Asia/Riyadh", "latitude": 24.7136, "longitude": 46.6753}


def _create(client_headers: dict[str, str]) -> None:
    r = client.post("/api/v1/office-spaces/buildings", headers=client_headers, json=_building())
    assert r.status_code == 201, r.text


def test_office_layout_isolated_and_identifiers_reusable_between_tenants(db: None) -> None:
    a = _headers()
    b = _headers(TENANT_B, WORKSPACE_B)
    _create(a)
    assert client.get("/api/v1/office-spaces/buildings", headers=a).json()[0]["id"] == "headquarters"
    assert client.get("/api/v1/office-spaces/buildings", headers=b).json() == []
    assert client.get("/api/v1/office-spaces/buildings/headquarters", headers=b).status_code == 404
    _create(b)
    assert len(client.get("/api/v1/office-spaces/buildings", headers=b).json()) == 1


def test_room_floor_desk_creation_and_revision_conflicts(db: None) -> None:
    headers = _headers()
    _create(headers)
    floor = client.post("/api/v1/office-spaces/floors", headers=headers, json={"id": "g", "building_id": "headquarters", "level": 0, "name": "Ground"})
    assert floor.status_code == 201, floor.text
    room = client.post("/api/v1/office-spaces/rooms", headers=headers, json={"id": "operations", "floor_id": "g", "name": "Operations", "kind": "office", "capacity": 1})
    assert room.status_code == 201, room.text
    desk = client.post("/api/v1/office-spaces/desks", headers=headers, json={"id": "a", "room_id": "operations", "label": "A"})
    assert desk.status_code == 201, desk.text
    full = client.post("/api/v1/office-spaces/desks", headers=headers, json={"id": "b", "room_id": "operations", "label": "B"})
    assert full.status_code == 409, full.text
    assert client.patch("/api/v1/office-spaces/rooms/operations", headers=headers, json={"name": "Operations II", "expected_revision": 1}).status_code == 200
    assert client.patch("/api/v1/office-spaces/rooms/operations", headers=headers, json={"name": "Too late", "expected_revision": 1}).status_code == 409
    assert client.delete("/api/v1/office-spaces/rooms/operations?expected_revision=2", headers=headers).status_code == 409


def test_member_cannot_change_registered_space(db: None) -> None:
    headers = _headers()
    _create(headers)
    member = _headers(role="member")
    answer = client.post("/api/v1/office-spaces/rooms", headers=member, json={"id": "x", "floor_id": "not_here", "name": "Room", "kind": "office", "capacity": 1})
    assert answer.status_code == 403, answer.text


def test_cross_workspace_parent_rejected(db: None) -> None:
    _create(_headers())
    other = _headers(TENANT_B, WORKSPACE_B)
    attempt = client.post("/api/v1/office-spaces/floors", headers=other, json={"id": "g", "building_id": "headquarters", "level": 0, "name": "Should not work"})
    assert attempt.status_code == 409, attempt.text


def test_bad_zone_and_unexpected_fields_rejected(db: None) -> None:
    h = _headers()
    assert client.post("/api/v1/office-spaces/buildings", headers=h, json={**_building(), "timezone": "Not/AZone"}).status_code == 422
    assert client.post("/api/v1/office-spaces/buildings", headers=h, json={**_building(), "tenant_id": TENANT_B}).status_code == 422
