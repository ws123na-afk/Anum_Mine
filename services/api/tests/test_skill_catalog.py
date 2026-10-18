"""Skill details, search, rollback and audit; marketplace search, validation and audit.

docs/skills.md and docs/governance-and-scale.md. Memory backend.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from skill_signing_support import signed

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.governance import governance_store
from anum_api.phase5 import store as scale_store
from anum_api.schemas import TenantContext
from anum_api.skills_api import skill_store

OWNER = {"x-tenant-id": "tenant_sk", "x-workspace-id": "workspace_sk", "x-user-id": "owner_sk", "x-user-roles": "owner"}
ORG_ADMIN = {**OWNER, "x-user-roles": "owner,org_admin"}
MEMBER = {**OWNER, "x-user-id": "member_sk", "x-user-roles": "member"}
OTHER_TENANT = {**OWNER, "x-tenant-id": "tenant_sk_other", "x-workspace-id": "workspace_sk_other"}
CONTEXT = TenantContext(tenant_id="tenant_sk", workspace_id="workspace_sk", user_id="owner_sk", roles=["owner"])


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    skill_store.clear()
    scale_store.clear()
    governance_store.clear()
    # Governance audit records live in the shared in-memory audit log (docs/governance-and-scale.md#audit-log).
    memory_repository.store.audit_records.clear()
    yield
    skill_store.clear()
    scale_store.clear()
    governance_store.clear()
    # Governance audit records live in the shared in-memory audit log (docs/governance-and-scale.md#audit-log).
    memory_repository.store.audit_records.clear()


def _publish(client: TestClient, version: str, tools: list[str], name: str = "Release notes", risk: str = "medium") -> None:
    response = client.post("/api/v1/skills/versions", headers=ORG_ADMIN, json=signed(client, ORG_ADMIN, {
        "skill_id": "acme.release-notes", "version": version, "name": name,
        "description": "Drafts release notes from merged work", "instructions": "Summarize changes.",
        "required_tools": tools, "risk_level": risk,
    }))
    assert response.status_code == 201, response.text


def test_detail_lists_versions_tools_with_risk_and_approval_and_history() -> None:
    client = TestClient(main.app)
    _publish(client, "1.0.0", ["anum.respond"])
    _publish(client, "1.2.0", ["anum.respond", "external.action", "unknown.tool"])
    installed = client.post("/api/v1/skills/installations", headers=OWNER, json={
        "skill_id": "acme.release-notes", "version": "1.2.0", "approved_tools": ["anum.respond", "external.action"]})
    assert installed.status_code == 201

    detail = client.get("/api/v1/skills/acme.release-notes", headers=MEMBER)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [v["version"] for v in body["versions"]] == ["1.2.0", "1.0.0"]
    assert body["latest_version"] == "1.2.0" and body["tools_version"] == "1.2.0"
    assert body["installation"]["version"] == "1.2.0"
    tools = {t["name"]: t for t in body["tools"]}
    assert tools["external.action"]["risk_level"] == "high"
    assert tools["external.action"]["approval"] == "always" and tools["external.action"]["granted"] is True
    assert tools["anum.respond"]["approval"] == "rule"
    assert tools["unknown.tool"]["registered"] is False and tools["unknown.tool"]["approval"] == "unavailable"
    assert tools["unknown.tool"]["granted"] is False
    assert [h["action"] for h in body["history"]] == ["skill.installed", "skill.published", "skill.published"]
    # A member reads but cannot manage or roll back.
    assert (body["can_manage"], body["can_rollback"]) == (False, False)
    admin = client.get("/api/v1/skills/acme.release-notes", headers=ORG_ADMIN).json()
    assert (admin["can_manage"], admin["can_rollback"]) == (True, True)
    assert client.get("/api/v1/skills/acme.missing", headers=OWNER).status_code == 404
    assert client.get("/api/v1/skills/acme.release-notes", headers=OTHER_TENANT).status_code == 404
    # The list routes are not shadowed by the detail route.
    assert client.get("/api/v1/skills/installations", headers=OWNER).json()[0]["skill_id"] == "acme.release-notes"


def test_versions_search_and_paging() -> None:
    client = TestClient(main.app)
    _publish(client, "1.0.0", [])
    _publish(client, "1.1.0", [])
    assert len(client.get("/api/v1/skills/versions?q=RELEASE", headers=OWNER).json()) == 2
    assert client.get("/api/v1/skills/versions?q=merged work", headers=OWNER).json()[0]["version"] == "1.0.0"
    assert client.get("/api/v1/skills/versions?q=nothing-matches", headers=OWNER).json() == []
    page = client.get("/api/v1/skills/versions?limit=1&offset=1", headers=OWNER).json()
    assert [v["version"] for v in page] == ["1.1.0"]
    assert client.get("/api/v1/skills/versions?limit=0", headers=OWNER).status_code == 422


def test_rollback_is_for_org_admins_never_widens_grants_and_is_audited() -> None:
    client = TestClient(main.app)
    _publish(client, "1.0.0", ["anum.respond", "web_search"])
    _publish(client, "2.0.0", ["anum.respond", "external.action"])
    client.post("/api/v1/skills/installations", headers=OWNER, json={
        "skill_id": "acme.release-notes", "version": "2.0.0", "approved_tools": ["anum.respond", "external.action"]})
    client.patch("/api/v1/skills/installations/acme.release-notes", headers=OWNER, json={"enabled": False})

    body = {"version": "1.0.0"}
    owner_try = client.post("/api/v1/skills/installations/acme.release-notes/rollback", headers=OWNER, json=body)
    assert owner_try.status_code == 403
    assert owner_try.json()["error"]["message"].startswith("Organization administrator role required")

    rolled = client.post("/api/v1/skills/installations/acme.release-notes/rollback", headers=ORG_ADMIN, json=body)
    assert rolled.status_code == 200, rolled.text
    result = rolled.json()
    assert result["version"] == "1.0.0"
    # web_search was never approved, external.action is not in 1.0.0: only anum.respond stays.
    assert result["approved_tools"] == ["anum.respond"]
    assert result["enabled"] is False

    newer = client.post("/api/v1/skills/installations/acme.release-notes/rollback", headers=ORG_ADMIN, json={"version": "2.0.0"})
    assert newer.status_code == 422
    assert client.post("/api/v1/skills/installations/acme.release-notes/rollback", headers=ORG_ADMIN,
                       json={"version": "0.9.0"}).status_code == 404
    assert client.post("/api/v1/skills/installations/acme.other/rollback", headers=ORG_ADMIN,
                       json=body).status_code == 404

    # Trusting the test publisher key is audited on its own target (skill_publisher_key:...).
    records = [r for r in governance_store.query_audit(CONTEXT) if r.target.startswith("skill:")]
    actions = [r.action for r in records]
    assert actions == ["skill.published", "skill.published", "skill.installed", "skill.disabled", "skill.rolled_back"]
    rollback = records[-1]
    assert dict(rollback.metadata)["from_version"] == "2.0.0" and rollback.actor == "owner_sk"


def test_uninstall_is_audited_with_the_removed_version() -> None:
    client = TestClient(main.app)
    _publish(client, "1.0.0", [])
    client.post("/api/v1/skills/installations", headers=OWNER, json={"skill_id": "acme.release-notes", "version": "1.0.0"})
    assert client.delete("/api/v1/skills/installations/acme.release-notes", headers=MEMBER).status_code == 403
    assert client.delete("/api/v1/skills/installations/acme.release-notes", headers=OWNER).status_code == 204
    last = governance_store.query_audit(CONTEXT)[-1]
    assert (last.action, dict(last.metadata)["version"]) == ("skill.uninstalled", "1.0.0")


PACKAGE = {"id": "acme.crm-sync", "name": "CRM sync", "kind": "integration", "version": "1.0.0",
           "publisher": "Acme", "verified": True, "permissions": ["integration:read"], "regions": ["eu-west"]}


def test_marketplace_search_filters_validation_and_audit() -> None:
    client = TestClient(main.app)
    assert client.put("/api/v1/marketplace/packages/acme.crm-sync", headers=ORG_ADMIN, json=PACKAGE).status_code == 200
    second = {**PACKAGE, "id": "beta.notes", "name": "Notes", "kind": "skill", "publisher": "Beta", "verified": False}
    assert client.put("/api/v1/marketplace/packages/beta.notes", headers=ORG_ADMIN, json=second).status_code == 200
    bad = {**PACKAGE, "id": "Bad Id", "version": "one"}
    assert client.put("/api/v1/marketplace/packages/Bad Id", headers=ORG_ADMIN, json=bad).status_code == 422

    def ids(query: str) -> list[str]:
        return sorted(p["id"] for p in client.get(f"/api/v1/marketplace/packages{query}", headers=MEMBER).json())

    assert ids("") == ["acme.crm-sync", "beta.notes"]
    assert ids("?q=acme") == ["acme.crm-sync"]
    assert ids("?q=BETA") == ["beta.notes"]
    assert ids("?verified=true") == ["acme.crm-sync"]
    assert ids("?kind=skill") == ["beta.notes"]

    assert client.post("/api/v1/marketplace/packages/acme.crm-sync/install", headers=MEMBER, json={}).status_code == 403
    assert client.post("/api/v1/marketplace/packages/acme.crm-sync/install", headers=OWNER, json={}).status_code == 201
    assert client.delete("/api/v1/marketplace/packages/acme.crm-sync/install", headers=OWNER).status_code == 204
    assert client.put("/api/v1/marketplace/packages/acme.crm-sync", headers=ORG_ADMIN,
                      json={**PACKAGE, "version": "1.1.0"}).status_code == 200
    assert client.delete("/api/v1/marketplace/packages/beta.notes", headers=ORG_ADMIN).status_code == 204

    actions = [(r.action, r.target) for r in governance_store.query_audit(CONTEXT)]
    assert actions == [
        ("marketplace.package_published", "marketplace_package:acme.crm-sync"),
        ("marketplace.package_published", "marketplace_package:beta.notes"),
        ("marketplace.package_installed", "marketplace_package:acme.crm-sync"),
        ("marketplace.package_uninstalled", "marketplace_package:acme.crm-sync"),
        ("marketplace.package_updated", "marketplace_package:acme.crm-sync"),
        ("marketplace.package_deleted", "marketplace_package:beta.notes"),
    ]


def test_detail_describes_a_requested_version_for_the_install_review() -> None:
    client = TestClient(main.app)
    _publish(client, "1.0.0", ["anum.respond"])
    _publish(client, "2.0.0", ["anum.respond", "external.action"])
    client.post("/api/v1/skills/installations", headers=OWNER, json={
        "skill_id": "acme.release-notes", "version": "1.0.0", "approved_tools": ["anum.respond"]})
    review = client.get("/api/v1/skills/acme.release-notes?version=2.0.0", headers=OWNER).json()
    assert review["tools_version"] == "2.0.0"
    assert {t["name"]: t["granted"] for t in review["tools"]} == {"anum.respond": False, "external.action": False}
    assert client.get("/api/v1/skills/acme.release-notes?version=3.0.0", headers=OWNER).status_code == 404
    installed = client.get("/api/v1/skills/acme.release-notes", headers=OWNER).json()
    assert installed["tools_version"] == "1.0.0" and installed["tools"][0]["granted"] is True
