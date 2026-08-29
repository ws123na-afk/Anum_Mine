from fastapi.testclient import TestClient

from anum_api.main import app
from anum_api.phase5 import store
from anum_api.schemas import TenantContext


OWNER = {"x-tenant-id": "tenant_scale", "x-workspace-id": "workspace_scale", "x-user-id": "owner_scale", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_scale", "x-user-roles": "member"}


def test_marketplace_install_is_scoped_and_owner_managed() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/marketplace/packages/skill.research-core/install", headers=OWNER, json={})
    assert response.status_code == 201
    assert response.json()["package_id"] == "skill.research-core"
    assert client.get("/api/v1/marketplace/installs", headers=OWNER).json()[0]["tenant_id"] == "tenant_scale"
    assert client.post("/api/v1/marketplace/packages/skill.research-core/install", headers=MEMBER, json={}).status_code == 403


def test_routing_honors_region_and_cost_with_failover() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/routing/decisions", headers=OWNER, json={"preferred_region": "eu-west", "max_cost_per_1k_tokens": 0.02})
    assert response.status_code == 200
    assert response.json()["target"]["region"] == "eu-west"
    assert response.json()["failover_target_ids"]


def test_routing_rejects_unsatisfied_policy() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/routing/decisions", headers=OWNER, json={"max_cost_per_1k_tokens": 0.00001})
    assert response.status_code == 503


def test_enterprise_snapshot_reports_multi_region_readiness() -> None:
    client = TestClient(app)
    payload = client.get("/api/v1/enterprise/operations", headers=OWNER).json()
    assert payload["active_regions"] == 2
    assert payload["failover_ready"] is True


def test_member_can_read_catalog_but_cannot_configure_routes() -> None:
    client = TestClient(app)
    assert client.get("/api/v1/marketplace/packages", headers=MEMBER).status_code == 200
    context = TenantContext(tenant_id="tenant_scale", workspace_id="workspace_scale", user_id="member_scale", roles=["member"])
    target = store.targets(context)[0].model_dump(mode="json")
    assert client.put(f"/api/v1/routing/targets/{target['id']}", headers=MEMBER, json=target).status_code == 403
