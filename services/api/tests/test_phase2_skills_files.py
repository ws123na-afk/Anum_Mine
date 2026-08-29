from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anum_api.files import FileStore, LocalObjectStorage, file_store, router as files_router
from anum_api.skills_api import router as skills_router, skill_store


app = FastAPI()
app.include_router(skills_router)
app.include_router(files_router)
client = TestClient(app)
OWNER = {"x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a",
         "x-user-id": "owner_a", "x-user-roles": "owner"}
OTHER_WORKSPACE = {**OWNER, "x-workspace-id": "workspace_b"}


def setup_function() -> None:
    skill_store.clear()
    file_store.clear()


def _skill(risk: str = "medium") -> dict:
    return {"skill_id": "acme.research", "version": "1.0.0", "name": "Research",
            "description": "Research with governed tools", "instructions": "Verify all sources.",
            "required_tools": ["web.search"], "risk_level": risk}


def test_skill_versions_installations_and_resolution_are_governed() -> None:
    assert client.post("/api/v1/skills/versions", headers=OWNER, json=_skill()).status_code == 201
    assert client.post("/api/v1/skills/versions", headers=OWNER, json=_skill()).status_code == 409
    installed = client.post("/api/v1/skills/installations", headers=OWNER, json={
        "skill_id": "acme.research", "version": "1.0.0", "approved_tools": ["web.search"]})
    assert installed.status_code == 201
    resolved = client.post("/api/v1/skills/resolve", headers=OWNER, json={
        "skill_id": "acme.research", "available_tools": ["web.search"], "maximum_risk": "medium"})
    assert resolved.status_code == 200
    assert resolved.json()["granted_tools"] == ["web.search"]


def test_skill_resolution_enforces_scope_tools_and_risk() -> None:
    client.post("/api/v1/skills/versions", headers=OWNER, json=_skill("high"))
    client.post("/api/v1/skills/installations", headers=OWNER, json={
        "skill_id": "acme.research", "version": "1.0.0", "approved_tools": ["web.search"]})
    assert client.post("/api/v1/skills/resolve", headers=OWNER, json={
        "skill_id": "acme.research", "available_tools": ["web.search"], "maximum_risk": "low"}).status_code == 403
    assert client.post("/api/v1/skills/resolve", headers=OWNER, json={
        "skill_id": "acme.research", "available_tools": [], "maximum_risk": "high"}).status_code == 409
    assert client.get("/api/v1/skills/installations", headers=OTHER_WORKSPACE).json() == []


def test_files_round_trip_checksum_and_workspace_isolation(tmp_path: Path) -> None:
    original = file_store.storage
    file_store.storage = LocalObjectStorage(tmp_path)
    try:
        uploaded = client.post("/api/v1/files", headers={**OWNER, "x-file-name": "report.txt",
                               "content-type": "text/plain"}, content=b"phase two")
        assert uploaded.status_code == 201
        record = uploaded.json()
        assert record["size_bytes"] == 9
        assert client.get(f"/api/v1/files/{record['id']}/content", headers=OWNER).content == b"phase two"
        assert client.get(f"/api/v1/files/{record['id']}", headers=OTHER_WORKSPACE).status_code == 404
        assert client.delete(f"/api/v1/files/{record['id']}", headers=OWNER).status_code == 204
    finally:
        file_store.clear()
        file_store.storage = original


def test_file_upload_rejects_traversal_and_bad_checksum() -> None:
    assert client.post("/api/v1/files", headers={**OWNER, "x-file-name": "../secret"}, content=b"x").status_code == 422
    assert client.post("/api/v1/files", headers={**OWNER, "x-file-name": "safe.txt",
                       "x-content-sha256": "0" * 64}, content=b"x").status_code == 422
