"""Integration package signing and turning off what a revoked key signed (docs/skills.md#signing).

Integration packages verify like skill versions before install. When a key is revoked and the
tenant's "disable flagged installs" setting is on (the default), every installation it signed
is turned off in every workspace of the tenant, audited there, and the owners are notified; an
owner turns one back on only explicitly. With the setting off, installations stay on and flagged.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from skill_signing_support import KEYS, private_key, public_key, sign, sign_package, signed_package, trust

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.governance import governance_store
from anum_api.phase5 import store as scale_store
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.skills_api import skill_store
from anum_api.team_store import memory_team_store

TENANT = "tenant_ad"
WS1, WS2, OTHER_WS = "workspace_ad_one", "workspace_ad_two", "workspace_other"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WS1, "x-user-id": "owner_ad", "x-user-roles": "owner"}
OWNER2 = {**OWNER, "x-workspace-id": WS2, "x-user-id": "owner_two"}
ADMIN = {**OWNER, "x-user-roles": "owner,org_admin"}
MEMBER = {**OWNER, "x-user-id": "member_ad", "x-user-roles": "member"}
OTHER_TENANT = {**OWNER, "x-tenant-id": "tenant_ad_other", "x-workspace-id": OTHER_WS, "x-user-roles": "owner,org_admin"}
PACKAGE = {"id": "acme.crm-sync", "name": "CRM sync", "kind": "integration", "version": "1.0.0", "publisher": "Acme",
           "verified": True, "permissions": ["integration:read"], "regions": ["eu-west"]}
SKILL = {"skill_id": "acme.notes", "version": "1.0.0", "name": "Notes", "description": "Writes notes",
         "instructions": "One line per change.", "required_tools": [], "risk_level": "low"}


def context(workspace: str = WS1, user: str = "owner_ad", tenant: str = TENANT) -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=["owner"])


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def reset() -> None:
        skill_store.clear()
        scale_store.clear()
        governance_store.clear()
        memory_team_store.clear()
        memory_repository.store.audit_records.clear()
        for key in [k for k in memory_repository.store.memberships if k[0] in (TENANT, "tenant_ad_other")]:
            memory_repository.store.memberships.pop(key)

    reset()
    now = utc_now()
    for tenant, workspace, user, role in ((TENANT, WS1, "owner_ad", "owner"), (TENANT, WS1, "member_ad", "member"),
                                          (TENANT, WS2, "owner_two", "owner"), ("tenant_ad_other", OTHER_WS, "owner_ad", "owner")):
        memory_repository.store.memberships[(tenant, workspace, user)] = WorkspaceMembership(
            tenant_id=tenant, workspace_id=workspace, user_id=user, role=role, created_at=now, updated_at=now)
    yield
    reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def actions(workspace: str = WS1) -> list[tuple[str, str, str]]:
    return [(r.action, r.target, r.outcome) for r in governance_store.query_audit(context(workspace))]


def publish_and_install(client: TestClient, owners: tuple[dict[str, str], ...] = (OWNER, OWNER2)) -> None:
    trust(client, ADMIN)
    assert client.post("/api/v1/skills/versions", headers=ADMIN, json={**SKILL, "signature": sign(SKILL)}).status_code == 201
    assert client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=ADMIN, json={**PACKAGE, "signature": sign_package(PACKAGE)}).status_code == 200
    for owner in owners:
        assert client.post("/api/v1/skills/installations", headers=owner, json={"skill_id": "acme.notes", "version": "1.0.0"}).status_code == 201
        assert client.post(f"/api/v1/marketplace/packages/{PACKAGE['id']}/install", headers=owner, json={}).status_code == 201


# Integration package signing --------------------------------------------------------------------


def test_unsigned_integration_publishes_but_cannot_be_installed(client: TestClient) -> None:
    assert client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=ADMIN, json=PACKAGE).status_code == 200
    [listed] = client.get("/api/v1/marketplace/packages", headers=MEMBER).json()
    assert listed["verification"]["status"] == "unsigned" and listed["verification"]["installable"] is False
    refused = client.post(f"/api/v1/marketplace/packages/{PACKAGE['id']}/install", headers=OWNER, json={})
    assert refused.status_code == 409
    assert refused.json()["error"]["message"].startswith("Package acme.crm-sync 1.0.0 cannot be installed: This package is not signed")
    assert ("marketplace.install_refused", "marketplace_package:acme.crm-sync", "denied") in actions()
    assert client.get("/api/v1/marketplace/installs", headers=OWNER).json() == []


def test_skill_kind_packages_need_no_signature_here(client: TestClient) -> None:
    skill_package = {**PACKAGE, "id": "skill.notes", "kind": "skill"}
    assert client.put("/api/v1/marketplace/packages/skill.notes", headers=ADMIN, json=skill_package).status_code == 200
    assert client.get("/api/v1/marketplace/packages", headers=MEMBER).json()[0]["verification"] is None
    assert client.post("/api/v1/marketplace/packages/skill.notes/install", headers=OWNER, json={}).status_code == 201
    signing = client.put("/api/v1/marketplace/packages/skill.notes/signature", headers=ADMIN, json=sign_package(skill_package))
    assert signing.status_code == 422 and "skill registry" in signing.json()["error"]["message"]


def test_bad_package_signatures_are_refused_and_good_ones_install(client: TestClient) -> None:
    trust(client, ADMIN)
    tampered = {**PACKAGE, "signature": sign_package({**PACKAGE, "permissions": ["integration:write"]})}
    refused = client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=ADMIN, json=tampered)
    assert refused.status_code == 422 and "does not match this package" in refused.json()["error"]["message"]
    assert client.get("/api/v1/marketplace/packages", headers=MEMBER).json() == []
    # A skill signature can never be replayed as a package signature (different context and digest).
    replay = {**PACKAGE, "signature": sign(SKILL)}
    assert client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=ADMIN, json=replay).status_code == 422

    saved = client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=ADMIN, json=signed_package(client, ADMIN, PACKAGE))
    assert saved.status_code == 200 and saved.json()["verification"]["signer"] == "Test publisher"
    installed = client.post(f"/api/v1/marketplace/packages/{PACKAGE['id']}/install", headers=OWNER, json={})
    assert installed.status_code == 201 and installed.json()["verification"]["status"] == "verified"
    # Widening permissions while keeping the old signature makes it invalid at once.
    widened = {**saved.json(), "permissions": ["integration:read", "integration:write"]}
    assert client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=ADMIN, json=widened).status_code == 200
    [install] = client.get("/api/v1/marketplace/installs", headers=OWNER).json()
    assert install["verification"]["status"] == "invalid"


def test_an_admin_signs_an_unsigned_integration_later(client: TestClient) -> None:
    client.put(f"/api/v1/marketplace/packages/{PACKAGE['id']}", headers=ADMIN, json=PACKAGE)
    trust(client, ADMIN)
    path = f"/api/v1/marketplace/packages/{PACKAGE['id']}/signature"
    assert client.put(path, headers=OWNER, json=sign_package(PACKAGE)).status_code == 403
    assert client.put(path, headers=ADMIN, json=sign_package({**PACKAGE, "version": "9.9.9"})).status_code == 422
    signed = client.put(path, headers=ADMIN, json=sign_package(PACKAGE))
    assert signed.status_code == 200 and signed.json()["signed_by"] == "owner_ad"
    assert client.put(path, headers=ADMIN, json=sign_package(PACKAGE)).status_code == 409
    assert client.post(f"/api/v1/marketplace/packages/{PACKAGE['id']}/install", headers=OWNER, json={}).status_code == 201
    assert ("marketplace.package_signed", "marketplace_package:acme.crm-sync", "success") in actions()


# Settings ----------------------------------------------------------------------------------


def test_disable_flagged_is_on_by_default_and_only_org_admins_change_it(client: TestClient) -> None:
    view = client.get("/api/v1/skills/signing-settings", headers=MEMBER).json()
    assert view["disable_flagged"] is True and view["can_manage"] is False
    assert client.put("/api/v1/skills/signing-settings", headers=OWNER, json={"disable_flagged": False}).status_code == 403
    changed = client.put("/api/v1/skills/signing-settings", headers=ADMIN, json={"disable_flagged": False})
    assert changed.status_code == 200 and changed.json()["disable_flagged"] is False and changed.json()["updated_by"] == "owner_ad"
    assert client.get("/api/v1/skills/signing-settings", headers=OWNER2).json()["disable_flagged"] is False  # tenant-wide
    assert client.get("/api/v1/skills/signing-settings", headers=OTHER_TENANT).json()["disable_flagged"] is True
    assert ("skill.signing_settings_updated", "skill_signing_settings", "success") in actions()


# Revocation turns off what the key signed ----------------------------------------------------


def test_revocation_disables_every_signed_install_in_every_workspace_and_notifies_owners(client: TestClient) -> None:
    publish_and_install(client)
    # Another tenant's identical install is never touched.
    trust(client, OTHER_TENANT)
    client.post("/api/v1/skills/versions", headers=OTHER_TENANT, json={**SKILL, "signature": sign(SKILL)})
    client.post("/api/v1/skills/installations", headers=OTHER_TENANT, json={"skill_id": "acme.notes", "version": "1.0.0"})

    revoked = client.post(f"{KEYS}/test-publisher/revoke", headers=ADMIN, json={"reason": "Leaked"})
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["auto_disable"] is True and body["affected_packages"] == 1 and body["disabled_installations"] == 4

    for owner, workspace, user in ((OWNER, WS1, "owner_ad"), (OWNER2, WS2, "owner_two")):
        [skill] = client.get("/api/v1/skills/installations", headers=owner).json()
        assert skill["enabled"] is False and "signing key test-publisher was revoked" in skill["disabled_reason"]
        [package] = client.get("/api/v1/marketplace/installs", headers=owner).json()
        assert package["enabled"] is False and package["verification"]["status"] == "revoked"
        # The agent can no longer use it.
        resolved = client.post("/api/v1/skills/resolve", headers=owner, json={"skill_id": "acme.notes"})
        assert resolved.status_code == 404
        audit = actions(workspace)
        assert ("skill.installation_disabled", "skill:acme.notes", "flagged") in audit
        assert ("marketplace.package_disabled", "marketplace_package:acme.crm-sync", "flagged") in audit
        [notice] = memory_team_store.list_notifications(context(workspace, user), limit=10, before=None, unread_only=False)
        assert notice.kind == "skill_signing" and "test-publisher" in notice.body and "acme.notes" in notice.body
    # Members are not notified; owners are.
    assert memory_team_store.list_notifications(context(WS1, "member_ad"), limit=10, before=None, unread_only=False) == []
    other = client.get("/api/v1/skills/installations", headers=OTHER_TENANT).json()
    assert [i["enabled"] for i in other] == [True]


def test_with_the_setting_off_installs_stay_on_and_flagged(client: TestClient) -> None:
    publish_and_install(client, owners=(OWNER,))
    client.put("/api/v1/skills/signing-settings", headers=ADMIN, json={"disable_flagged": False})
    body = client.post(f"{KEYS}/test-publisher/revoke", headers=ADMIN, json={"reason": "Leaked"}).json()
    assert body["auto_disable"] is False and body["disabled_installations"] == 0
    [skill] = client.get("/api/v1/skills/installations", headers=OWNER).json()
    assert skill["enabled"] is True and skill["verification"]["status"] == "revoked"
    assert memory_team_store.list_notifications(context(), limit=10, before=None, unread_only=False) == []


def test_an_owner_turns_a_flagged_install_back_on_only_explicitly(client: TestClient) -> None:
    publish_and_install(client, owners=(OWNER,))
    client.post(f"{KEYS}/test-publisher/revoke", headers=ADMIN, json={"reason": "Leaked"})

    implicit = client.patch("/api/v1/skills/installations/acme.notes", headers=OWNER, json={"enabled": True})
    assert implicit.status_code == 409 and "Confirm that you want to turn it on anyway" in implicit.json()["error"]["message"]
    assert client.patch("/api/v1/skills/installations/acme.notes", headers=MEMBER, json={"enabled": True, "acknowledge_flagged": True}).status_code == 403
    explicit = client.patch("/api/v1/skills/installations/acme.notes", headers=OWNER, json={"enabled": True, "acknowledge_flagged": True})
    assert explicit.status_code == 200 and explicit.json()["enabled"] is True and explicit.json()["disabled_reason"] is None

    path = f"/api/v1/marketplace/packages/{PACKAGE['id']}/install"
    assert client.patch(path, headers=OWNER, json={"enabled": True}).status_code == 409
    back = client.patch(path, headers=OWNER, json={"enabled": True, "acknowledge_flagged": True})
    assert back.status_code == 200 and back.json()["enabled"] is True
    assert client.patch(path, headers=OWNER, json={"enabled": False}).json()["disabled_reason"] == "Turned off by a workspace owner."
    audit = actions()
    assert ("skill.flagged_reenabled", "skill:acme.notes", "success") in audit
    assert ("marketplace.flagged_reenabled", "marketplace_package:acme.crm-sync", "success") in audit


def test_rotation_never_disables_anything(client: TestClient) -> None:
    publish_and_install(client, owners=(OWNER,))
    rotated = client.post(f"{KEYS}/test-publisher/rotate", headers=ADMIN, json={"new_id": "test-publisher-2", "public_key": public_key(private_key("next"))})
    assert rotated.status_code == 201
    assert [i["enabled"] for i in client.get("/api/v1/skills/installations", headers=OWNER).json()] == [True]
    assert [i["enabled"] for i in client.get("/api/v1/marketplace/installs", headers=OWNER).json()] == [True]
