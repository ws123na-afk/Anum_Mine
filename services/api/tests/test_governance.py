from fastapi.testclient import TestClient

from anum_api.governance import governance_store
from anum_api.main import app


client = TestClient(app)
OWNER = {
    "x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a",
    "x-user-id": "owner_a", "x-user-roles": "owner",
}
MEMBER = {**OWNER, "x-user-id": "member_a", "x-user-roles": "member"}
OTHER = {**OWNER, "x-tenant-id": "tenant_b", "x-workspace-id": "workspace_b"}


def setup_function() -> None:
    governance_store.clear()


def policy(name: str = "External actions") -> dict:
    return {
        "name": name,
        "description": "Protect consequential operations",
        "rules": [{"action": "integration.*.write", "effect": "require_approval"}],
    }


def test_policy_packs_are_versioned_and_tenant_isolated() -> None:
    first = client.post("/api/v1/policy-packs", headers=OWNER, json=policy())
    second = client.post("/api/v1/policy-packs", headers=OWNER, json=policy())

    assert first.status_code == 201 and first.json()["version"] == 1
    assert second.status_code == 201 and second.json()["version"] == 2
    packs = client.get("/api/v1/policy-packs", headers=OWNER).json()
    assert [item["active"] for item in packs] == [False, True]
    assert client.get("/api/v1/policy-packs", headers=OTHER).json() == []


def test_members_can_read_but_cannot_administer_governance() -> None:
    assert client.get("/api/v1/organization/governance", headers=MEMBER).status_code == 200
    assert client.post("/api/v1/policy-packs", headers=MEMBER, json=policy()).status_code == 403
    assert client.get("/api/v1/audit/export", headers=MEMBER).status_code == 403


def test_role_templates_are_unique_and_permissions_normalized() -> None:
    payload = {"name": "Operator", "permissions": ["task:read", "task:run", "task:read"]}
    created = client.post("/api/v1/role-templates", headers=OWNER, json=payload)
    duplicate = client.post("/api/v1/role-templates", headers=OWNER, json=payload)

    assert created.status_code == 201
    assert created.json()["permissions"] == ["task:read", "task:run"]
    assert duplicate.status_code == 409


def test_approval_rules_memory_governance_and_summary() -> None:
    approval = client.post("/api/v1/organization/approval-rules", headers=OWNER, json={
        "name": "Finance dual control", "action_pattern": "finance.*",
        "minimum_approvers": 2, "required_roles": ["owner"],
    })
    memory = client.put("/api/v1/organization/memory-governance", headers=OWNER, json={
        "default_retention_days": 90, "allow_permanent_retention": False,
        "require_provenance": True, "allowed_source_types": ["task", "document"],
    })
    summary = client.get("/api/v1/organization/governance", headers=OWNER)

    assert approval.status_code == 201 and approval.json()["minimum_approvers"] == 2
    assert memory.status_code == 200 and memory.json()["default_retention_days"] == 90
    assert summary.json()["approval_rules"] == 1
    assert summary.json()["memory_governance_configured"] is True


def test_audit_exports_are_scoped_and_available_as_json_and_csv() -> None:
    client.post("/api/v1/policy-packs", headers=OWNER, json=policy())
    client.post("/api/v1/policy-packs", headers=OTHER, json=policy("Other policy"))

    json_export = client.get("/api/v1/audit/export", headers=OWNER)
    csv_export = client.get("/api/v1/audit/export?format=csv", headers=OWNER)

    assert json_export.status_code == 200
    assert len(json_export.json()) == 1
    assert json_export.json()[0]["tenant_id"] == "tenant_a"
    assert csv_export.status_code == 200
    assert "policy_pack.created" in csv_export.text
    assert "tenant_b" not in csv_export.text
