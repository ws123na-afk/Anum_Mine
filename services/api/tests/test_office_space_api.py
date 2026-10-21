"""Office API must not silently accept temporary in-memory writes in production paths."""
from fastapi.testclient import TestClient
from anum_api.main import app
from anum_api.settings import settings


def test_office_write_is_not_faked_on_memory_backend(monkeypatch):
    monkeypatch.setattr(settings, "repository_backend", "memory")
    h = {"x-tenant-id": "tenant_office_unit", "x-workspace-id": "workspace_office_unit", "x-user-id": "owner_office_unit", "x-user-roles": "owner"}
    response = TestClient(app).post("/api/v1/office-spaces/buildings", headers=h, json={"id": "hq", "name": "HQ", "timezone": "Asia/Riyadh"})
    assert response.status_code == 503, response.text
    assert "PostgreSQL" in str(response.json())


def test_office_models_require_strict_fields():
    from pydantic import ValidationError
    from anum_api.office_space_api import BuildingCreate, RoomCreate, AssignDesk
    try:
        BuildingCreate.model_validate({"id":"hq", "name":"HQ", "timezone":"Asia/Riyadh", "tenant_id":"other"})
    except ValidationError:
        pass
    else:
        raise AssertionError("Caller-controlled tenant ID must be rejected")
    try:
        RoomCreate.model_validate({"id":"x", "floor_id":"f", "name":"X", "kind":"office", "capacity":0})
    except ValidationError:
        pass
    else:
        raise AssertionError("Zero-capacity rooms must be rejected")
    try:
        AssignDesk.model_validate({"agent_id":"a", "expected_revision":0})
    except ValidationError:
        pass
    else:
        raise AssertionError("Revision 0 must be rejected")
