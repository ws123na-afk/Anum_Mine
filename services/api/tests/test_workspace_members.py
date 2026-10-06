"""Workspace invitations and membership management (in-memory repository)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from anum_api import dependencies
from anum_api.dependencies import memory_repository
from anum_api.main import app, store
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings
from anum_api.workspace_members import hash_invitation_token

from test_identity import FakeJwksEndpoint, SigningKey, bearer, make_validator

TENANT = "tenant_inv"
WORKSPACE = "workspace_inv"
OWNER = {
    "x-tenant-id": TENANT,
    "x-workspace-id": WORKSPACE,
    "x-user-id": "owner_1",
    "x-user-roles": "owner",
}


def headers_for(user_id: str, *, roles: str = "viewer", tenant: str = TENANT,
                workspace: str = WORKSPACE, email: str | None = None) -> dict[str, str]:
    value = {
        "x-tenant-id": tenant,
        "x-workspace-id": workspace,
        "x-user-id": user_id,
        "x-user-roles": roles,
    }
    if email is not None:
        value["x-user-email"] = email
    return value


def _clear() -> None:
    store.tasks.clear()
    store.runs.clear()
    store.approvals.clear()
    store.events.clear()
    store.tenants.clear()
    store.workspaces.clear()
    store.memberships.clear()
    store.invitations.clear()
    store.audit_records.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    _clear()
    test_client = TestClient(app)
    for tenant, workspace in ((TENANT, WORKSPACE), ("tenant_other", "workspace_other")):
        owner = headers_for("owner_1", roles="owner", tenant=tenant, workspace=workspace)
        assert test_client.post("/api/v1/tenants", headers=owner, json={"name": tenant}).status_code == 201
        assert test_client.post("/api/v1/workspaces", headers=owner, json={"name": workspace}).status_code == 201
        assert test_client.post("/api/v1/workspace-memberships/current", headers=owner).status_code == 201
    try:
        yield test_client
    finally:
        _clear()


def invite(client: TestClient, **body) -> dict:
    response = client.post("/api/v1/workspace-invitations", headers=OWNER, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def accept(client: TestClient, token: str, headers: dict[str, str]):
    return client.post("/api/v1/workspace-invitations/accept", headers=headers, json={"token": token})


def scope() -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_1", roles=["owner"])


def test_invitation_round_trip_stores_only_a_hash_and_is_single_use(client: TestClient) -> None:
    created = invite(client, role="member", invitee_user_id="user_new")
    token = created["token"]
    invitation = created["invitation"]

    assert token.startswith("anum_inv_")
    assert invitation["status"] == "pending" and invitation["role"] == "member"
    assert "token_hash" not in invitation
    stored = store.invitations[invitation["id"]]
    assert stored.token_hash == hash_invitation_token(token)
    assert token not in stored.model_dump_json()

    accepted = accept(client, token, headers_for("user_new"))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["membership"]["role"] == "member"
    assert accepted.json()["invitation"]["status"] == "accepted"

    reused = accept(client, token, headers_for("user_new"))
    assert reused.status_code == 409
    assert reused.json()["error"]["message"] == "Invitation was already used"


def test_accept_writes_events_and_audit_records(client: TestClient) -> None:
    created = invite(client, role="viewer", invitee_user_id="user_ev")
    assert accept(client, created["token"], headers_for("user_ev")).status_code == 200

    types = [event.type for event in store.events if event.tenant_id == TENANT]
    assert "workspace_invitation.created" in types
    assert "workspace_invitation.accepted" in types
    assert "workspace_member.added" in types
    assert all(created["token"] not in event.model_dump_json() for event in store.events)

    actions = {record.action for record in memory_repository.list_audit_records(scope())}
    assert actions == {
        "workspace_invitation.create",
        "workspace_invitation.accept",
        "workspace_member.add",
    }
    accepted = [r for r in memory_repository.list_audit_records(scope()) if r.action.endswith("accept")]
    assert accepted[0].actor == "user_ev"


def test_expired_invitation_is_rejected_and_shown_as_expired(client: TestClient) -> None:
    created = invite(client, role="member", invitee_user_id="user_late")
    stored = store.invitations[created["invitation"]["id"]]
    store.invitations[stored.id] = stored.model_copy(
        update={"expires_at": utc_now() - timedelta(seconds=1)}
    )

    response = accept(client, created["token"], headers_for("user_late"))

    assert response.status_code == 410
    assert response.json()["error"]["message"] == "Invitation has expired"
    listed = client.get("/api/v1/workspace-invitations", headers=OWNER).json()
    assert listed[0]["status"] == "expired"
    assert (TENANT, WORKSPACE, "user_late") not in store.memberships


def test_invitation_bound_to_another_user_is_not_consumed(client: TestClient) -> None:
    created = invite(client, role="member", invitee_user_id="user_right")

    wrong = accept(client, created["token"], headers_for("user_wrong"))
    assert wrong.status_code == 403
    assert (TENANT, WORKSPACE, "user_wrong") not in store.memberships

    right = accept(client, created["token"], headers_for("user_right"))
    assert right.status_code == 200


def test_email_bound_invitation_needs_matching_email(client: TestClient) -> None:
    created = invite(client, role="viewer", invitee_email="Invitee@Example.com")

    assert accept(client, created["token"], headers_for("user_mail")).status_code == 403
    assert accept(
        client, created["token"], headers_for("user_mail", email="other@example.com")
    ).status_code == 403
    ok = accept(client, created["token"], headers_for("user_mail", email="invitee@example.COM"))
    assert ok.status_code == 200
    assert ok.json()["membership"]["user_id"] == "user_mail"


def test_token_from_another_tenant_or_workspace_is_not_found(client: TestClient) -> None:
    created = invite(client, role="member", invitee_user_id="user_x")

    other_tenant = accept(
        client, created["token"],
        headers_for("user_x", tenant="tenant_other", workspace="workspace_other"),
    )
    other_workspace = accept(
        client, created["token"], headers_for("user_x", workspace="workspace_other")
    )
    garbage = accept(client, "anum_inv_not-a-real-token", headers_for("user_x"))

    assert other_tenant.status_code == 404
    assert other_workspace.status_code == 404
    assert garbage.status_code == 404
    other_owner = headers_for("owner_1", roles="owner", tenant="tenant_other", workspace="workspace_other")
    assert client.get("/api/v1/workspace-invitations", headers=other_owner).json() == []
    # Still usable in the right scope.
    assert accept(client, created["token"], headers_for("user_x")).status_code == 200


def test_revoked_invitation_cannot_be_accepted(client: TestClient) -> None:
    created = invite(client, role="member", invitee_user_id="user_rev")
    revoked = client.post(
        f"/api/v1/workspace-invitations/{created['invitation']['id']}/revoke", headers=OWNER
    )
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"

    response = accept(client, created["token"], headers_for("user_rev"))
    assert response.status_code == 410


def test_already_active_member_cannot_reuse_an_invitation(client: TestClient) -> None:
    created = invite(client, role="viewer", invitee_user_id="owner_1")
    response = accept(client, created["token"], OWNER)
    assert response.status_code == 409
    assert store.invitations[created["invitation"]["id"]].status == "pending"


def test_only_owners_manage_invitations_and_members(client: TestClient) -> None:
    member = headers_for("someone", roles="member")
    assert client.post(
        "/api/v1/workspace-invitations", headers=member,
        json={"role": "owner", "invitee_user_id": "x"},
    ).status_code == 403
    assert client.get("/api/v1/workspace-invitations", headers=member).status_code == 403
    assert client.get("/api/v1/workspace-members", headers=member).status_code == 403
    assert client.put(
        "/api/v1/workspace-members/owner_1/role", headers=member, json={"role": "viewer"}
    ).status_code == 403
    assert client.post("/api/v1/workspace-members/owner_1/deactivate", headers=member).status_code == 403


def test_invitation_payload_validation(client: TestClient) -> None:
    for body in (
        {"role": "member"},
        {"role": "admin", "invitee_user_id": "x"},
        {"role": "member", "invitee_email": "not-an-email"},
        {"role": "member", "invitee_user_id": "x", "ttl_hours": 0},
        {"role": "member", "invitee_user_id": "x", "ttl_hours": 10_000},
    ):
        response = client.post("/api/v1/workspace-invitations", headers=OWNER, json=body)
        assert response.status_code == 422, body


def test_last_active_owner_cannot_be_demoted_or_deactivated(client: TestClient) -> None:
    demote = client.put("/api/v1/workspace-members/owner_1/role", headers=OWNER, json={"role": "member"})
    deactivate = client.post("/api/v1/workspace-members/owner_1/deactivate", headers=OWNER)

    assert demote.status_code == 409
    assert deactivate.status_code == 409
    assert store.memberships[(TENANT, WORKSPACE, "owner_1")].role == "owner"
    assert store.memberships[(TENANT, WORKSPACE, "owner_1")].active is True

    created = invite(client, role="owner", invitee_user_id="owner_2")
    assert accept(client, created["token"], headers_for("owner_2")).status_code == 200

    demoted = client.put("/api/v1/workspace-members/owner_1/role", headers=OWNER, json={"role": "member"})
    assert demoted.status_code == 200 and demoted.json()["role"] == "member"
    owner_2 = headers_for("owner_2", roles="owner")
    assert client.post("/api/v1/workspace-members/owner_2/deactivate", headers=owner_2).status_code == 409


def test_role_change_deactivate_and_reactivate_are_audited(client: TestClient) -> None:
    created = invite(client, role="member", invitee_user_id="user_m")
    assert accept(client, created["token"], headers_for("user_m")).status_code == 200

    changed = client.put("/api/v1/workspace-members/user_m/role", headers=OWNER, json={"role": "viewer"})
    deactivated = client.post("/api/v1/workspace-members/user_m/deactivate", headers=OWNER)
    reactivated = client.post("/api/v1/workspace-members/user_m/reactivate", headers=OWNER)
    missing = client.post("/api/v1/workspace-members/nobody/deactivate", headers=OWNER)

    assert changed.json()["role"] == "viewer"
    assert deactivated.json()["active"] is False
    assert reactivated.json()["active"] is True
    assert missing.status_code == 404
    members = client.get("/api/v1/workspace-members", headers=OWNER).json()
    assert {member["user_id"]: member["role"] for member in members} == {
        "owner_1": "owner",
        "user_m": "viewer",
    }
    actions = [record.action for record in memory_repository.list_audit_records(scope())]
    assert actions[-3:] == [
        "workspace_member.role_change",
        "workspace_member.deactivate",
        "workspace_member.reactivate",
    ]
    types = [event.type for event in store.events if event.workspace_id == WORKSPACE]
    assert types[-3:] == [
        "workspace_member.role_changed",
        "workspace_member.deactivated",
        "workspace_member.reactivated",
    ]


def test_deactivated_member_can_be_reinvited(client: TestClient) -> None:
    first = invite(client, role="member", invitee_user_id="user_back")
    assert accept(client, first["token"], headers_for("user_back")).status_code == 200
    assert client.post("/api/v1/workspace-members/user_back/deactivate", headers=OWNER).status_code == 200

    second = invite(client, role="viewer", invitee_user_id="user_back")
    response = accept(client, second["token"], headers_for("user_back"))

    assert response.status_code == 200
    membership = store.memberships[(TENANT, WORKSPACE, "user_back")]
    assert membership.active is True and membership.role == "viewer"


# --- OIDC ------------------------------------------------------------------------------------

OIDC_TENANT = "tenant_oidc"
OIDC_WORKSPACE = "workspace_oidc"


@pytest.fixture
def oidc_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, SigningKey]]:
    key = SigningKey("kid-inv")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "repository_backend", "memory")
    dependencies.set_oidc_validator(make_validator(FakeJwksEndpoint(key)))
    _clear()
    try:
        yield TestClient(app), key
    finally:
        dependencies.set_oidc_validator(None)
        _clear()


def test_oidc_invitee_without_membership_accepts_and_gets_the_invited_role(oidc_client) -> None:
    client, key = oidc_client
    owner_token = key.sign(sub="oidc-owner")
    assert client.post("/api/v1/tenants", headers=bearer(owner_token), json={"name": "T"}).status_code == 201
    assert client.post("/api/v1/workspaces", headers=bearer(owner_token), json={"name": "W"}).status_code == 201
    assert client.post("/api/v1/workspace-memberships/current", headers=bearer(owner_token)).status_code == 201

    created = client.post(
        "/api/v1/workspace-invitations",
        headers=bearer(owner_token),
        json={"role": "viewer", "invitee_email": "new@example.com"},
    ).json()

    # Realm role `owner` in the invitee's token grants nothing: the invitation decides.
    unverified = key.sign(sub="oidc-new", email="new@example.com", email_verified=False)
    assert accept(client, created["token"], bearer(unverified)).status_code == 403
    verified = key.sign(sub="oidc-new", email="new@example.com", email_verified=True)
    response = accept(client, created["token"], bearer(verified))

    assert response.status_code == 200, response.text
    assert response.json()["membership"]["role"] == "viewer"
    me = client.get("/api/v1/workspace-memberships/current", headers=bearer(verified))
    assert me.status_code == 200 and me.json()["role"] == "viewer"
    # A viewer cannot manage members even though the token carries realm role owner.
    assert client.get("/api/v1/workspace-members", headers=bearer(verified)).status_code == 403
