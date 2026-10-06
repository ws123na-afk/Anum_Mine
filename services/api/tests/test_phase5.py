from fastapi.testclient import TestClient

from anum_api.main import app
from anum_api.phase5 import store
from anum_api.schemas import TenantContext


OWNER = {"x-tenant-id": "tenant_scale", "x-workspace-id": "workspace_scale", "x-user-id": "owner_scale", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_scale", "x-user-roles": "member"}
PACKAGE = {
    "id": "skill.research-core", "name": "Research Core", "kind": "skill", "version": "1.0.0",
    "publisher": "ANUM", "verified": True, "permissions": ["memory:read"], "regions": ["us-east"],
}


def _target(target_id: str, region: str, cost: float) -> dict:
    return {
        "id": target_id, "region": region, "provider": "openai-compatible", "model": "primary",
        "status": "healthy", "cost_per_1k_tokens": cost, "latency_ms": 200,
    }


def test_marketplace_starts_empty_with_no_invented_listings() -> None:
    client = TestClient(app)
    fresh = {**OWNER, "x-tenant-id": "tenant_fresh"}
    ids = {item["id"] for item in client.get("/api/v1/marketplace/packages", headers=fresh).json()}
    assert "integration.crm-sync" not in ids


def test_default_routing_reports_only_the_configured_model_as_unmeasured() -> None:
    client = TestClient(app)
    fresh = {**OWNER, "x-tenant-id": "tenant_fresh_routes"}
    targets = client.get("/api/v1/routing/targets", headers=fresh).json()
    assert [target["id"] for target in targets] == ["configured-model"]
    assert targets[0]["region"] == "local"
    operations = client.get("/api/v1/enterprise/operations", headers=fresh).json()
    assert operations["failover_ready"] is False


def test_marketplace_install_is_scoped_and_owner_managed() -> None:
    client = TestClient(app)
    assert client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=OWNER, json=PACKAGE).status_code == 200
    response = client.post("/api/v1/marketplace/packages/skill.research-core/install", headers=OWNER, json={})
    assert response.status_code == 201
    assert response.json()["package_id"] == "skill.research-core"
    assert client.get("/api/v1/marketplace/installs", headers=OWNER).json()[0]["tenant_id"] == "tenant_scale"
    assert client.post("/api/v1/marketplace/packages/skill.research-core/install", headers=MEMBER, json={}).status_code == 403


def test_routing_honors_region_and_cost_with_failover() -> None:
    client = TestClient(app)
    for target in (_target("us-primary", "us-east", 0.01), _target("eu-primary", "eu-west", 0.012)):
        assert client.put(f"/api/v1/routing/targets/{target['id']}", headers=OWNER, json=target).status_code == 200
    response = client.post("/api/v1/routing/decisions", headers=OWNER, json={"preferred_region": "eu-west", "max_cost_per_1k_tokens": 0.02})
    assert response.status_code == 200
    assert response.json()["target"]["region"] == "eu-west"
    assert response.json()["failover_target_ids"]


def test_routing_rejects_unsatisfied_policy() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/routing/decisions", headers=OWNER, json={"preferred_region": "ap-south", "sensitivity": "restricted"})
    assert response.status_code == 503


def test_enterprise_snapshot_reports_multi_region_readiness_from_real_targets() -> None:
    client = TestClient(app)
    headers = {**OWNER, "x-tenant-id": "tenant_regions"}
    for target in (_target("us-a", "us-east", 0.01), _target("eu-a", "eu-west", 0.01)):
        client.put(f"/api/v1/routing/targets/{target['id']}", headers=headers, json=target)
    payload = client.get("/api/v1/enterprise/operations", headers=headers).json()
    assert payload["active_regions"] >= 2
    assert payload["failover_ready"] is True


def test_member_can_read_catalog_but_cannot_configure_routes() -> None:
    client = TestClient(app)
    assert client.get("/api/v1/marketplace/packages", headers=MEMBER).status_code == 200
    context = TenantContext(tenant_id="tenant_scale", workspace_id="workspace_scale", user_id="member_scale", roles=["member"])
    target = store.targets(context)[0].model_dump(mode="json")
    assert client.put(f"/api/v1/routing/targets/{target['id']}", headers=MEMBER, json=target).status_code == 403
