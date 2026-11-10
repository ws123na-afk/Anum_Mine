from fastapi.testclient import TestClient

from anum_api.governance import governance_store
from anum_api.main import app
from anum_api.store import store


client = TestClient(app)
# OWNER administers the organization (workspace owner + org_admin); WORKSPACE_OWNER owns a
# workspace of the same tenant but is not an organization administrator.
OWNER = {
    "x-tenant-id": "tenant_a", "x-workspace-id": "workspace_a",
    "x-user-id": "owner_a", "x-user-roles": "owner,org_admin",
}
WORKSPACE_OWNER = {**OWNER, "x-user-id": "owner_b", "x-workspace-id": "workspace_a2", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_a", "x-user-roles": "member"}
OTHER = {**OWNER, "x-tenant-id": "tenant_b", "x-workspace-id": "workspace_b"}


def setup_function() -> None:
    governance_store.clear()
    # Governance audit records share the in-memory audit store with every other change.
    store.audit_records.clear()


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


APPROVAL_RULE = {
    "name": "Finance dual control", "action_pattern": "finance.*",
    "minimum_approvers": 2, "required_roles": ["owner"],
}
MEMORY_GOVERNANCE = {
    "default_retention_days": 30, "allow_permanent_retention": True,
    "require_provenance": False, "allowed_source_types": [],
}


def test_a_workspace_owner_cannot_change_tenant_wide_policy() -> None:
    pack = client.post("/api/v1/policy-packs", headers=OWNER, json=policy()).json()

    refused = [
        client.post("/api/v1/policy-packs", headers=WORKSPACE_OWNER, json=policy("Weaker")),
        client.post(f"/api/v1/policy-packs/{pack['id']}/archive", headers=WORKSPACE_OWNER),
        client.post(f"/api/v1/policy-packs/{pack['id']}/activate", headers=WORKSPACE_OWNER),
        client.post("/api/v1/role-templates", headers=WORKSPACE_OWNER,
                    json={"name": "Escalated", "permissions": ["task:read"]}),
        client.post("/api/v1/organization/approval-rules", headers=WORKSPACE_OWNER, json=APPROVAL_RULE),
        client.put("/api/v1/organization/memory-governance", headers=WORKSPACE_OWNER, json=MEMORY_GOVERNANCE),
    ]

    for response in refused:
        assert response.status_code == 403
        assert response.json()["error"]["message"].startswith("Organization administrator role required")
    # Nothing changed and nothing was audited as a change.
    assert [p["active"] for p in client.get("/api/v1/policy-packs", headers=OWNER).json()] == [True]
    assert client.get("/api/v1/role-templates", headers=OWNER).json() == []
    assert client.get("/api/v1/organization/approval-rules", headers=OWNER).json() == []
    assert client.get("/api/v1/organization/memory-governance", headers=OWNER).status_code == 404
    actions = [record["action"] for record in client.get("/api/v1/audit/export", headers=OWNER).json()]
    assert actions == ["policy_pack.created"]


def test_a_workspace_owner_keeps_read_access_and_workspace_scoped_controls() -> None:
    summary = client.get("/api/v1/organization/governance", headers=WORKSPACE_OWNER)
    assert summary.status_code == 200
    assert summary.json()["can_manage_organization_policy"] is False
    assert client.get("/api/v1/policy-packs", headers=WORKSPACE_OWNER).status_code == 200
    assert client.get("/api/v1/organization/approval-rules", headers=WORKSPACE_OWNER).status_code == 200
    # The workspace's own audit trail stays with its owner.
    assert client.get("/api/v1/audit/export", headers=WORKSPACE_OWNER).status_code == 200


def test_org_admin_still_needs_a_workspace_role() -> None:
    # org_admin alone is not a workspace membership: a request without one is refused.
    bare = {**OWNER, "x-user-roles": "org_admin"}
    assert client.post("/api/v1/policy-packs", headers=bare, json=policy()).status_code == 403
    assert client.get("/api/v1/organization/governance", headers=bare).status_code == 403


def test_org_admin_of_one_tenant_cannot_change_another_tenants_policy() -> None:
    created = client.post("/api/v1/policy-packs", headers=OWNER, json=policy()).json()

    other = client.post(f"/api/v1/policy-packs/{created['id']}/archive", headers=OTHER)

    assert other.status_code == 404
    assert client.get("/api/v1/policy-packs", headers=OWNER).json()[0]["active"] is True


def test_summary_reports_that_an_org_admin_can_manage() -> None:
    assert client.get("/api/v1/organization/governance", headers=OWNER).json()["can_manage_organization_policy"] is True
    assert client.get("/api/v1/organization/governance", headers=MEMBER).json()["can_manage_organization_policy"] is False


# --------------------------------------------------------------- pack versions: search, paging, delete


def test_policy_pack_list_searches_and_pages() -> None:
    for name in ("External actions", "Finance guard", "Mail policy"):
        assert client.post("/api/v1/policy-packs", headers=OWNER, json=policy(name)).status_code == 201
    assert [p["name"] for p in client.get("/api/v1/policy-packs?q=finance", headers=OWNER).json()] == ["Finance guard"]
    # A rule's action matches too.
    assert len(client.get("/api/v1/policy-packs?q=integration", headers=OWNER).json()) == 3
    assert [p["name"] for p in client.get("/api/v1/policy-packs?limit=1&offset=1", headers=OWNER).json()] == ["Finance guard"]
    assert client.get("/api/v1/policy-packs?limit=0", headers=OWNER).status_code == 422


def test_only_an_archived_pack_version_can_be_deleted_and_the_delete_is_audited() -> None:
    first = client.post("/api/v1/policy-packs", headers=OWNER, json=policy()).json()
    second = client.post("/api/v1/policy-packs", headers=OWNER, json=policy()).json()
    # Publishing v2 archived v1; v2 is active and stays.
    active = client.delete(f"/api/v1/policy-packs/{second['id']}", headers=OWNER)
    assert active.status_code == 409
    assert "Archive it first" in active.json()["error"]["message"]
    assert client.delete(f"/api/v1/policy-packs/{first['id']}", headers=WORKSPACE_OWNER).status_code == 403
    assert client.delete(f"/api/v1/policy-packs/{first['id']}", headers=OTHER).status_code == 404
    assert client.delete(f"/api/v1/policy-packs/{first['id']}", headers=OWNER).status_code == 204
    assert client.delete(f"/api/v1/policy-packs/{first['id']}", headers=OWNER).status_code == 404
    packs = client.get("/api/v1/policy-packs", headers=OWNER).json()
    assert [(p["version"], p["active"]) for p in packs] == [(2, True)]
    # The next version continues the numbering of the versions that stayed.
    assert client.post("/api/v1/policy-packs", headers=OWNER, json=policy()).json()["version"] == 3
    deleted = [r for r in store.audit_records if r.action == "policy_pack.deleted"]
    assert len(deleted) == 1 and deleted[0].metadata == {"name": "External actions", "version": 1, "rules": 1}


def test_role_template_list_searches_and_pages() -> None:
    for name, permissions in (("Operator", ["task:read", "task:run"]), ("Reader", ["task:read"]), ("Approver", ["approval:decide"])):
        assert client.post("/api/v1/role-templates", headers=OWNER, json={"name": name, "permissions": permissions}).status_code == 201
    assert [t["name"] for t in client.get("/api/v1/role-templates?q=approval", headers=OWNER).json()] == ["Approver"]
    assert [t["name"] for t in client.get("/api/v1/role-templates?q=task:run", headers=OWNER).json()] == ["Operator"]
    assert [t["name"] for t in client.get("/api/v1/role-templates?limit=2&offset=1", headers=OWNER).json()] == ["Reader", "Approver"]


# --------------------------------------------------------------- role templates on memberships


def _persist_membership(user_id: str, role: str) -> None:
    from anum_api.dependencies import memory_repository
    from anum_api.schemas import WorkspaceMembership, utc_now

    now = utc_now()
    memory_repository.save_membership(WorkspaceMembership(
        tenant_id="tenant_a", workspace_id="workspace_a", user_id=user_id, role=role, created_at=now, updated_at=now,
    ))


def _template(name: str = "Reader", permissions: list[str] | None = None) -> dict:
    created = client.post("/api/v1/role-templates", headers=OWNER, json={"name": name, "permissions": permissions or ["task:read", "memory:read", "organization:read"]})
    assert created.status_code == 201, created.text
    return created.json()


def test_a_role_template_narrows_a_members_permissions_until_it_is_removed() -> None:
    store.memberships.clear()
    _persist_membership("member_a", "member")
    template = _template()
    # Before: a member may create a task.
    assert client.post("/api/v1/tasks", headers=MEMBER, json={"title": "t", "prompt": "p"}).status_code == 201

    assigned = client.put("/api/v1/workspace-members/member_a/role-template", headers=OWNER, json={"role_template_id": template["id"]})
    assert assigned.status_code == 200, assigned.text
    body = assigned.json()
    assert body["role_template_id"] == template["id"] and body["role_template_name"] == "Reader"
    assert body["effective_permissions"] == ["memory:read", "organization:read", "task:read"]

    # Reads inside the template still work; a write the template does not name is refused with the reason.
    assert client.get("/api/v1/tasks", headers=MEMBER).status_code == 200
    refused = client.post("/api/v1/tasks", headers=MEMBER, json={"title": "t", "prompt": "p"})
    assert refused.status_code == 403
    assert "role template does not include this permission" in refused.json()["error"]["message"]
    # The member directory shows the template and the effective permissions.
    listed = {m["user_id"]: m for m in client.get("/api/v1/workspace-members", headers=OWNER).json()}
    assert listed["member_a"]["role_template_name"] == "Reader"
    assert listed["member_a"]["effective_permissions"] == ["memory:read", "organization:read", "task:read"]
    actions = [r.action for r in store.audit_records if r.action.startswith("workspace_member.")]
    assert "workspace_member.role_template" in actions

    removed = client.put("/api/v1/workspace-members/member_a/role-template", headers=OWNER, json={"role_template_id": None})
    assert removed.status_code == 200 and removed.json()["role_template_id"] is None and removed.json()["effective_permissions"] is None
    assert client.post("/api/v1/tasks", headers=MEMBER, json={"title": "t", "prompt": "p"}).status_code == 201


def test_role_templates_are_refused_for_owners_unknown_templates_and_non_owners() -> None:
    store.memberships.clear()
    _persist_membership("owner_a", "owner")
    _persist_membership("member_a", "member")
    template = _template()
    assert client.put("/api/v1/workspace-members/member_a/role-template", headers=OWNER, json={"role_template_id": "role_missing"}).status_code == 404
    refused = client.put("/api/v1/workspace-members/owner_a/role-template", headers=OWNER, json={"role_template_id": template["id"]})
    assert refused.status_code == 409 and "Owners keep the whole owner role" in refused.json()["error"]["message"]
    assert client.put("/api/v1/workspace-members/member_a/role-template", headers=MEMBER, json={"role_template_id": template["id"]}).status_code == 403
    assert client.put("/api/v1/workspace-members/member_a/role-template", headers=OTHER, json={"role_template_id": template["id"]}).status_code == 404


def test_promoting_a_templated_member_to_owner_drops_the_template() -> None:
    store.memberships.clear()
    _persist_membership("owner_a", "owner")
    _persist_membership("member_a", "member")
    template = _template()
    assert client.put("/api/v1/workspace-members/member_a/role-template", headers=OWNER, json={"role_template_id": template["id"]}).status_code == 200
    promoted = client.put("/api/v1/workspace-members/member_a/role", headers=OWNER, json={"role": "owner"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "owner" and promoted.json()["role_template_id"] is None and promoted.json()["permission_limit"] is None
    # The template's permissions that the role does not grant never apply: a viewer with task:run stays read only.
    _persist_membership("viewer_a", "viewer")
    wide = _template("Wide", ["task:read", "task:run"])
    view = client.put("/api/v1/workspace-members/viewer_a/role-template", headers=OWNER, json={"role_template_id": wide["id"]}).json()
    assert view["effective_permissions"] == ["task:read"]
