import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api.main import app
from anum_api.phase5 import store
from anum_api.schemas import TenantContext


OWNER = {"x-tenant-id": "tenant_scale", "x-workspace-id": "workspace_scale", "x-user-id": "owner_scale", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_scale", "x-user-roles": "member"}
# The catalog and routing targets are tenant-wide: changing them needs org_admin.
ORG_ADMIN = {**OWNER, "x-user-roles": "owner,org_admin"}
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
    assert client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=ORG_ADMIN, json=PACKAGE).status_code == 200
    response = client.post("/api/v1/marketplace/packages/skill.research-core/install", headers=OWNER, json={})
    assert response.status_code == 201
    assert response.json()["package_id"] == "skill.research-core"
    assert client.get("/api/v1/marketplace/installs", headers=OWNER).json()[0]["tenant_id"] == "tenant_scale"
    assert client.post("/api/v1/marketplace/packages/skill.research-core/install", headers=MEMBER, json={}).status_code == 403


def test_routing_honors_region_and_cost_with_failover() -> None:
    client = TestClient(app)
    for target in (_target("us-primary", "us-east", 0.01), _target("eu-primary", "eu-west", 0.012)):
        assert client.put(f"/api/v1/routing/targets/{target['id']}", headers=ORG_ADMIN, json=target).status_code == 200
    response = client.post("/api/v1/routing/decisions", headers=OWNER, json={"preferred_region": "eu-west", "max_cost_per_1k_tokens": 0.02})
    assert response.status_code == 200
    assert response.json()["target"]["region"] == "eu-west"
    assert response.json()["failover_target_ids"]


def test_routing_rejects_unsatisfied_policy() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/routing/decisions", headers=OWNER, json={"preferred_region": "ap-south", "sensitivity": "restricted"})
    assert response.status_code == 503


def test_enterprise_snapshot_reports_multi_region_readiness_from_probed_targets_only(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    headers = {**ORG_ADMIN, "x-tenant-id": "tenant_regions", "x-workspace-id": "workspace_regions"}
    for target in (_target("us-a", "us-east", 0.01), _target("eu-a", "eu-west", 0.01)):
        assert client.put(f"/api/v1/routing/targets/{target['id']}", headers=headers, json=target).status_code == 200
    # Typed "healthy" counts for nothing until a probe measured it.
    payload = client.get("/api/v1/enterprise/operations", headers=headers).json()
    assert (payload["healthy_targets"], payload["unprobed_targets"], payload["failover_ready"]) == (0, 3, False)

    _answering_provider(monkeypatch)
    client.put("/api/v1/model-config", headers=headers, json=OPENAI_CONFIG)
    for target_id in ("us-a", "eu-a"):
        probed = client.post(f"/api/v1/routing/targets/{target_id}/probe", headers=headers)
        assert probed.status_code == 200, probed.text
        assert probed.json()["status"] == "healthy" and probed.json()["last_checked_at"] and probed.json()["last_error"] is None
        assert probed.json()["probe_workspace_id"] == "workspace_regions"
    payload = client.get("/api/v1/enterprise/operations", headers=headers).json()
    assert payload["active_regions"] == 2 and payload["healthy_targets"] == 2 and payload["unprobed_targets"] == 1
    assert payload["failover_ready"] is True


OPENAI_CONFIG = {"provider": "openai_compatible", "model": "primary", "base_url": "http://localhost:18080/v1", "api_key": "sk-example"}


def _answering_provider(monkeypatch: pytest.MonkeyPatch, handler=None) -> list[httpx.Request]:
    import anum_api.onboarding as onboarding

    seen: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if handler is not None:
            return handler(request)
        return httpx.Response(200, json={"model": "primary", "choices": [{"message": {"content": "ANUM_OK"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(onboarding, "_test_client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    return seen


def test_member_can_read_catalog_but_cannot_configure_routes() -> None:
    client = TestClient(app)
    assert client.get("/api/v1/marketplace/packages", headers=MEMBER).status_code == 200
    context = TenantContext(tenant_id="tenant_scale", workspace_id="workspace_scale", user_id="member_scale", roles=["member"])
    target = store.targets(context)[0].model_dump(mode="json")
    assert client.put(f"/api/v1/routing/targets/{target['id']}", headers=MEMBER, json=target).status_code == 403


def test_a_workspace_owner_cannot_change_the_tenant_catalog_or_routing() -> None:
    client = TestClient(app)
    tenant = {"x-tenant-id": "tenant_scale_deny"}
    owner, admin = {**OWNER, **tenant}, {**ORG_ADMIN, **tenant}
    package = {**PACKAGE, "id": "skill.deny-check"}
    assert client.put(f"/api/v1/marketplace/packages/{package['id']}", headers=admin, json=package).status_code == 200
    target = _target("deny-check", "us-east", 0.01)

    refused = [
        client.put("/api/v1/marketplace/packages/skill.other", headers=owner, json={**PACKAGE, "id": "skill.other"}),
        client.delete(f"/api/v1/marketplace/packages/{package['id']}", headers=owner),
        client.put(f"/api/v1/routing/targets/{target['id']}", headers=owner, json=target),
    ]

    for response in refused:
        assert response.status_code == 403
        assert response.json()["error"]["message"].startswith("Organization administrator role required")
    # Nothing changed: the catalog still holds only the admin's package, no target was added.
    assert [item["id"] for item in client.get("/api/v1/marketplace/packages", headers=owner).json()] == [package["id"]]
    assert "deny-check" not in {t["id"] for t in client.get("/api/v1/routing/targets", headers=owner).json()}
    # Installing into this workspace stays a workspace-owner action.
    assert client.post(f"/api/v1/marketplace/packages/{package['id']}/install", headers=owner, json={}).status_code == 201
    assert client.delete(f"/api/v1/marketplace/packages/{package['id']}/install", headers=owner).status_code == 204
    # The admin may remove the package once nothing installs it.
    assert client.delete(f"/api/v1/marketplace/packages/{package['id']}", headers=admin).status_code == 204


# ------------------------------------------------------------------- probes, delete, in routing


def _fresh(name: str) -> dict:
    return {**ORG_ADMIN, "x-tenant-id": f"tenant_{name}", "x-workspace-id": f"workspace_{name}"}


def test_probe_needs_the_target_provider_configured_and_reports_why_not(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    headers = _fresh("probe_cfg")
    assert client.put("/api/v1/routing/targets/eu-a", headers=headers, json=_target("eu-a", "eu-west", 0.01)).status_code == 200
    # No model configuration in this workspace at all.
    probed = client.post("/api/v1/routing/targets/eu-a/probe", headers=headers).json()
    assert probed["status"] == "unverified" and "no model configuration" in probed["last_error"]
    assert probed["last_checked_at"] is not None and probed["measured_latency_ms"] is None
    # A configuration for another provider.
    client.put("/api/v1/model-config", headers=headers, json={"provider": "mock", "model": "anum-mock", "base_url": "http://localhost:8000"})
    probed = client.post("/api/v1/routing/targets/eu-a/probe", headers=headers).json()
    assert probed["status"] == "unverified" and "is for mock, not openai-compatible" in probed["last_error"]
    # The built-in configured model is the mock here: always answers, and probing stores it as a tenant target.
    built_in = client.post("/api/v1/routing/targets/configured-model/probe", headers=headers).json()
    assert built_in["status"] == "healthy" and built_in["measured_latency_ms"] is not None
    assert client.post("/api/v1/routing/targets/missing/probe", headers=headers).status_code == 404
    assert client.post("/api/v1/routing/targets/eu-a/probe", headers={**headers, "x-user-roles": "owner"}).status_code == 403


def test_probe_marks_provider_errors_degraded_and_unreachable_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    headers = _fresh("probe_err")
    client.put("/api/v1/routing/targets/eu-a", headers=headers, json=_target("eu-a", "eu-west", 0.01))
    client.put("/api/v1/model-config", headers=headers, json=OPENAI_CONFIG)
    _answering_provider(monkeypatch, lambda request: httpx.Response(404, json={"error": "unknown model"}))
    degraded = client.post("/api/v1/routing/targets/eu-a/probe", headers=headers).json()
    assert degraded["status"] == "degraded" and degraded["last_error"]

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _answering_provider(monkeypatch, unreachable)
    offline = client.post("/api/v1/routing/targets/eu-a/probe", headers=headers).json()
    assert offline["status"] == "offline" and offline["measured_latency_ms"] is None and "Could not reach" in offline["last_error"]
    actions = [r.action for r in _audit(headers)]
    assert actions.count("routing_target.probed") == 2


def _audit(headers: dict) -> list:
    from anum_api.store import store as memory_store

    return [r for r in memory_store.audit_records if r.tenant_id == headers["x-tenant-id"]]


def test_put_keeps_the_probe_result_unless_provider_or_model_change(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    headers = _fresh("probe_keep")
    target = _target("eu-a", "eu-west", 0.01)
    client.put("/api/v1/routing/targets/eu-a", headers=headers, json=target)
    client.put("/api/v1/model-config", headers=headers, json=OPENAI_CONFIG)
    _answering_provider(monkeypatch)
    probed = client.post("/api/v1/routing/targets/eu-a/probe", headers=headers).json()
    # Probe fields sent by a client are ignored; the stored result and status stay while provider and model do.
    forged = {**target, "status": "offline", "region": "eu-central", "last_checked_at": None, "last_error": "forged"}
    saved = client.put("/api/v1/routing/targets/eu-a", headers=headers, json=forged).json()
    assert saved["region"] == "eu-central" and saved["status"] == "healthy"
    assert saved["last_checked_at"] == probed["last_checked_at"] and saved["last_error"] is None
    # A new model starts over: not yet probed.
    changed = client.put("/api/v1/routing/targets/eu-a", headers=headers, json={**target, "model": "secondary", "status": "unverified"}).json()
    assert changed["last_checked_at"] is None and changed["status"] == "unverified"


def test_delete_refuses_a_target_in_use_and_the_built_in_model(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    headers = _fresh("probe_del")
    target = _target("eu-a", "eu-west", 0.01)
    client.put("/api/v1/routing/targets/eu-a", headers=headers, json=target)
    client.put("/api/v1/model-config", headers=headers, json=OPENAI_CONFIG)
    _answering_provider(monkeypatch)
    assert client.post("/api/v1/routing/targets/eu-a/probe", headers=headers).json()["status"] == "healthy"
    in_use = client.delete("/api/v1/routing/targets/eu-a", headers=headers)
    assert in_use.status_code == 409 and "Take it out of routing first" in in_use.json()["error"]["message"]
    # Out of routing: decisions skip it, the figures count it as disabled, and it can go.
    disabled = client.put("/api/v1/routing/targets/eu-a", headers=headers, json={**target, "enabled": False}).json()
    assert disabled["enabled"] is False and disabled["status"] == "healthy"
    decision = client.post("/api/v1/routing/decisions", headers=headers, json={})
    assert decision.json()["target"]["id"] != "eu-a"
    assert client.get("/api/v1/enterprise/operations", headers=headers).json()["disabled_targets"] == 1
    assert client.delete("/api/v1/routing/targets/eu-a", headers={**headers, "x-user-roles": "owner"}).status_code == 403
    assert client.delete("/api/v1/routing/targets/eu-a", headers=headers).status_code == 204
    assert client.delete("/api/v1/routing/targets/eu-a", headers=headers).status_code == 404
    built_in = client.delete("/api/v1/routing/targets/configured-model", headers=headers)
    assert built_in.status_code == 409 and "configured model" in built_in.json()["error"]["message"]
    assert [r.action for r in _audit(headers) if r.action == "routing_target.deleted"] == ["routing_target.deleted"]
    # An unprobed target is not in use: it goes at once.
    client.put("/api/v1/routing/targets/us-a", headers=headers, json=_target("us-a", "us-east", 0.01))
    assert client.delete("/api/v1/routing/targets/us-a", headers=headers).status_code == 204


def test_scheduler_pass_probes_every_target_with_a_reachable_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta

    from anum_api import routing_probe
    from anum_api.model_config_store import memory_model_config_store
    from anum_api.schemas import TenantContext as Context
    from anum_api.settings import settings

    # The pass covers every tenant in the memory store: start from an empty one.
    store.clear()
    memory_model_config_store.clear()
    client = TestClient(app)
    headers = _fresh("probe_sched")
    other = {**headers, "x-workspace-id": "workspace_probe_sched_b"}
    for target_id in ("eu-a", "us-a"):
        client.put(f"/api/v1/routing/targets/{target_id}", headers=headers, json=_target(target_id, "eu-west", 0.01))
    # A provider no workspace of the organisation is configured for: never probed by the schedule.
    client.put("/api/v1/routing/targets/an-a", headers=headers, json={**_target("an-a", "eu-west", 0.01), "provider": "anthropic"})
    seen = _answering_provider(monkeypatch)
    # No configuration anywhere yet: nothing is reachable, nothing is probed.
    assert routing_probe.run_due_routing_probes() == 0 and seen == []
    client.put("/api/v1/model-config", headers=headers, json=OPENAI_CONFIG)
    client.post("/api/v1/routing/targets/eu-a/probe", headers=headers)
    assert len(seen) == 1
    # us-a was never probed and its provider is configured in this workspace: the schedule probes it.
    assert routing_probe.run_due_routing_probes() == 1
    assert len(seen) == 2
    context = Context(tenant_id=headers["x-tenant-id"], workspace_id=headers["x-workspace-id"], user_id="x", roles=["owner"])
    targets = {t.id: t for t in store.targets(context)}
    assert targets["us-a"].last_checked_at is not None and targets["us-a"].probe_workspace_id == headers["x-workspace-id"]
    assert targets["an-a"].last_checked_at is None, "a target nobody can reach stays not yet probed"
    # Nothing is due again until the interval passes.
    assert routing_probe.run_due_routing_probes() == 0
    probed = targets["eu-a"]
    aged = probed.model_copy(update={"last_checked_at": probed.last_checked_at - timedelta(seconds=settings.routing_probe_interval_seconds + 5)})
    store.upsert_target(aged, context)
    assert routing_probe.run_due_routing_probes() == 1
    assert len(seen) == 3
    reprobed = next(t for t in store.targets(context) if t.id == "eu-a")
    assert reprobed.last_checked_at > aged.last_checked_at and reprobed.status.value == "healthy"
    # The probing workspace moved to another provider: the next due probe uses a workspace that still reaches it.
    assert client.put("/api/v1/model-config", headers=other, json=OPENAI_CONFIG).status_code == 200
    switched = client.put("/api/v1/model-config", headers=headers, json={"provider": "mock", "model": "mock", "base_url": "http://localhost:9"})
    assert switched.status_code == 200, switched.text
    store.upsert_target(reprobed.model_copy(update={"last_checked_at": aged.last_checked_at}), context)
    assert routing_probe.run_due_routing_probes() == 1
    moved = next(t for t in store.targets(context) if t.id == "eu-a")
    assert moved.probe_workspace_id == "workspace_probe_sched_b" and moved.status.value == "healthy"
    # A scheduled probe with the same outcome writes no audit row; only changes do.
    assert [r for r in _audit(headers) if r.action == "routing_target.probed" and r.metadata.get("scheduled")] == []
