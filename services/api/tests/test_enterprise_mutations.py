from pathlib import Path

from fastapi.testclient import TestClient

import anum_api.automation as automation_module
from anum_api.automation import LocalAutomationEngine
from anum_api.main import app


OWNER = {"x-tenant-id": "tenant_mutations", "x-workspace-id": "workspace_mutations", "x-user-id": "owner_mutations", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-tenant-id": "tenant_other", "x-workspace-id": "workspace_other"}


def test_schedule_full_lifecycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(automation_module, "engine", LocalAutomationEngine(str(tmp_path / "automation.db")))
    client = TestClient(app)
    workflow = client.post("/api/v1/automation/workflows", headers=OWNER, json={"name": "Daily", "steps": [{"id": "one", "name": "One", "action": "notify"}]}).json()
    created = client.post("/api/v1/automation/schedules", headers=OWNER, json={"workflow_id": workflow["id"], "name": "Morning", "cron": "0 8 * * *", "timezone": "UTC"})
    assert created.status_code == 201
    schedule_id = created.json()["id"]
    assert client.get(f"/api/v1/automation/schedules/{schedule_id}", headers=OWNER).status_code == 200
    assert client.get(f"/api/v1/automation/schedules/{schedule_id}", headers=OTHER).status_code == 404
    updated = client.put(f"/api/v1/automation/schedules/{schedule_id}", headers=OWNER, json={"cron": "0 9 * * *", "timezone": "Asia/Riyadh"})
    assert updated.json()["cron"] == "0 9 * * *"
    assert client.post(f"/api/v1/automation/schedules/{schedule_id}/disable", headers=OWNER).json()["enabled"] is False
    assert client.post(f"/api/v1/automation/schedules/{schedule_id}/enable", headers=OWNER).json()["enabled"] is True
    assert client.delete(f"/api/v1/automation/schedules/{schedule_id}", headers=OWNER).status_code == 204
    assert client.get(f"/api/v1/automation/schedules/{schedule_id}", headers=OWNER).status_code == 404


def test_skill_installation_disable_and_uninstall() -> None:
    client = TestClient(app)
    published = client.post("/api/v1/skills/versions", headers=OWNER, json={"skill_id": "operations.review", "version": "1.0.0", "name": "Operations review", "description": "Review operations", "instructions": "Review safely", "required_tools": ["memory.read"], "risk_level": "low"})
    assert published.status_code == 201
    installed = client.post("/api/v1/skills/installations", headers=OWNER, json={"skill_id": "operations.review", "version": "1.0.0", "approved_tools": ["memory.read"]})
    assert installed.status_code == 201
    disabled = client.patch("/api/v1/skills/installations/operations.review", headers=OWNER, json={"enabled": False})
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    assert client.delete("/api/v1/skills/installations/operations.review", headers=OWNER).status_code == 204
    assert client.delete("/api/v1/skills/installations/operations.review", headers=OWNER).status_code == 404


def test_integration_configuration_is_workspace_scoped() -> None:
    client = TestClient(app)
    configured = client.put("/api/v1/integrations/minio/configuration", headers=OWNER, json={"enabled": False, "endpoint": "http://minio.internal:9000"})
    assert configured.status_code == 200 and configured.json()["enabled"] is False
    assert client.get("/api/v1/integrations/minio/configuration", headers=OWNER).json()["endpoint"] == "http://minio.internal:9000"
    assert client.get("/api/v1/integrations/minio/configuration", headers=OTHER).json()["endpoint"] != "http://minio.internal:9000"
    health = client.get("/api/v1/integrations", headers=OWNER).json()
    assert next(item for item in health if item["id"] == "minio")["status"] == "disabled"


def test_policy_activation_archive_and_approval_detail() -> None:
    client = TestClient(app)
    payload = {"name": "Operations", "rules": [{"action": "integration.write", "effect": "require_approval"}]}
    first = client.post("/api/v1/policy-packs", headers=OWNER, json=payload).json()
    second = client.post("/api/v1/policy-packs", headers=OWNER, json=payload).json()
    assert client.post(f"/api/v1/policy-packs/{first['id']}/activate", headers=OWNER).json()["active"] is True
    assert client.post(f"/api/v1/policy-packs/{first['id']}/archive", headers=OWNER).json()["active"] is False
    task = client.post("/api/v1/tasks", headers=OWNER, json={"title": "Approval task", "prompt": "Send an external status update"}).json()
    result = client.post(f"/api/v1/tasks/{task['id']}/run", headers=OWNER).json()
    if result["approval"] is not None:
        approval_id = result["approval"]["id"]
        assert client.get(f"/api/v1/approvals/{approval_id}", headers=OWNER).status_code == 200
        assert client.get(f"/api/v1/approvals/{approval_id}", headers=OTHER).status_code == 404


def test_marketplace_catalog_mutation_and_install_protection() -> None:
    client = TestClient(app)
    package = {"id": "skill.custom-review", "name": "Custom Review", "kind": "skill", "version": "1.0.0", "publisher": "Test", "verified": True, "permissions": ["memory:read"], "regions": ["us-east"]}
    assert client.put("/api/v1/marketplace/packages/skill.custom-review", headers=OWNER, json=package).status_code == 200
    assert any(item["id"] == package["id"] for item in client.get("/api/v1/marketplace/packages", headers=OWNER).json())
    assert client.post("/api/v1/marketplace/packages/skill.custom-review/install", headers=OWNER, json={}).status_code == 201
    assert client.delete("/api/v1/marketplace/packages/skill.custom-review", headers=OWNER).status_code == 409
    assert client.delete("/api/v1/marketplace/packages/skill.custom-review/install", headers=OWNER).status_code == 204
    assert client.delete("/api/v1/marketplace/packages/skill.custom-review", headers=OWNER).status_code == 204
