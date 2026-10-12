"""The audit log, members search and paging, invitation resend, and the workspace
lifecycle (create, rename, archive, restore) against the in-memory repository."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from anum_api.audit import AuditRecord
from anum_api.dependencies import memory_repository
from anum_api.main import app, store
from anum_api.settings import settings

TENANT = "tenant_audit"
WORKSPACE = "workspace_audit"


def headers_for(user_id: str, *, roles: str = "viewer", tenant: str = TENANT,
                workspace: str = WORKSPACE, email: str | None = None) -> dict[str, str]:
    value = {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user_id, "x-user-roles": roles}
    if email is not None:
        value["x-user-email"] = email
    return value


OWNER = headers_for("owner_1", roles="owner")
MEMBER = headers_for("member_1", roles="member")
ORG_ADMIN_MEMBER = headers_for("admin_1", roles="member,org_admin")


def _clear() -> None:
    for collection in (store.tasks, store.runs, store.approvals, store.events, store.tenants,
                       store.workspaces, store.memberships, store.invitations, store.audit_records):
        collection.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    _clear()
    test_client = TestClient(app)
    for tenant, workspace in ((TENANT, WORKSPACE), ("tenant_elsewhere", "workspace_elsewhere")):
        owner = headers_for("owner_1", roles="owner", tenant=tenant, workspace=workspace)
        assert test_client.post("/api/v1/tenants", headers=owner, json={"name": tenant}).status_code == 201
        assert test_client.post("/api/v1/workspaces", headers=owner, json={"name": "Audit desk"}).status_code == 201
        assert test_client.post("/api/v1/workspace-memberships/current", headers=owner).status_code == 201
    try:
        yield test_client
    finally:
        _clear()


def seed(count: int, *, tenant: str = TENANT, workspace: str = WORKSPACE, action: str = "task.create",
         actor: str = "owner_1", start: datetime | None = None) -> list[AuditRecord]:
    base = start or datetime(2026, 9, 1, tzinfo=timezone.utc)
    records = []
    for index in range(count):
        record = AuditRecord(
            id=f"audit_{tenant}_{action}_{index:04d}", tenant_id=tenant, workspace_id=workspace,
            actor=actor, action=action, target=f"task_{index}", outcome="success",
            correlation_id=f"correlation_{index}", created_at=base + timedelta(minutes=index),
            metadata={"index": index, "api_key": "example"},
        )
        records.append(memory_repository.record_audit(record))
    return records


# --------------------------------------------------------------------------- audit log


def test_owner_pages_newest_first_with_a_keyset_cursor(client: TestClient) -> None:
    seed(7)
    first = client.get("/api/v1/audit-records?limit=3", headers=OWNER)
    assert first.status_code == 200, first.text
    body = first.json()
    assert [item["target"] for item in body["items"]] == ["task_6", "task_5", "task_4"]
    assert body["next_cursor"] and "append-only" in body["retention"]
    # Secret-looking metadata keys are redacted when recorded.
    assert body["items"][0]["metadata"] == {"index": 6, "api_key": "[REDACTED]"}

    targets, cursor = [], body["next_cursor"]
    while cursor:
        page = client.get(f"/api/v1/audit-records?limit=3&cursor={cursor}", headers=OWNER).json()
        targets += [item["target"] for item in page["items"]]
        cursor = page["next_cursor"]
    assert targets == ["task_3", "task_2", "task_1", "task_0"]


def test_filters_by_actor_action_prefix_target_text_and_date_range(client: TestClient) -> None:
    seed(3, action="task.create", actor="owner_1")
    seed(2, action="workspace_member.role_change", actor="owner_2",
         start=datetime(2026, 9, 10, tzinfo=timezone.utc))

    def items(query: str) -> list[dict]:
        response = client.get(f"/api/v1/audit-records?{query}", headers=OWNER)
        assert response.status_code == 200, response.text
        return response.json()["items"]

    assert {item["actor"] for item in items("actor=owner_2")} == {"owner_2"}
    assert len(items("action=workspace_member.*")) == 2
    assert len(items("action=task.create")) == 3
    assert [item["target"] for item in items("target=task_1&action=task.create")] == ["task_1"]
    assert len(items("q=ROLE_CHANGE")) == 2
    assert len(items("since=2026-09-10T00:00:00Z")) == 2
    assert len(items("until=2026-09-10T00:00:00Z")) == 3
    assert len(items("since=2026-09-01T00:01:00Z&until=2026-09-01T00:02:00Z")) == 1
    refused = client.get("/api/v1/audit-records?since=2026-09-10T00:00:00Z&until=2026-09-01T00:00:00Z", headers=OWNER)
    assert refused.status_code == 422
    assert client.get("/api/v1/audit-records?cursor=not-a-cursor", headers=OWNER).status_code == 400

    actions = client.get("/api/v1/audit-records/actions", headers=OWNER).json()["actions"]
    assert "task.create" in actions and "workspace_member.role_change" in actions


def test_detail_is_scoped_to_the_workspace(client: TestClient) -> None:
    mine = seed(1)[0]
    theirs = seed(1, tenant="tenant_elsewhere", workspace="workspace_elsewhere")[0]
    detail = client.get(f"/api/v1/audit-records/{mine.id}", headers=OWNER)
    assert detail.status_code == 200 and detail.json()["correlation_id"] == mine.correlation_id
    assert client.get(f"/api/v1/audit-records/{theirs.id}", headers=OWNER).status_code == 404
    listed = client.get("/api/v1/audit-records?limit=200", headers=OWNER).json()["items"]
    assert theirs.id not in {item["id"] for item in listed}


def test_members_are_refused_and_org_admins_with_a_membership_are_allowed(client: TestClient) -> None:
    seed(2)
    refused = client.get("/api/v1/audit-records", headers=MEMBER)
    assert refused.status_code == 403
    assert "organization administrator" in refused.json()["error"]["message"]
    assert client.get("/api/v1/audit/export", headers=MEMBER).status_code == 403
    assert client.get("/api/v1/audit-records", headers=ORG_ADMIN_MEMBER).status_code == 200
    assert client.get("/api/v1/audit/export?format=csv", headers=ORG_ADMIN_MEMBER).status_code == 200
    # org_admin alone is not a membership.
    assert client.get("/api/v1/audit-records", headers=headers_for("admin_1", roles="org_admin")).status_code == 403


def test_export_streams_the_filtered_range_and_is_itself_audited(client: TestClient) -> None:
    seed(4, action="task.create")
    seed(2, action="workspace_member.deactivate", start=datetime(2026, 9, 5, tzinfo=timezone.utc))
    response = client.get("/api/v1/audit/export?format=json&action=task.create", headers=OWNER)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="audit-workspace_audit-')
    rows = response.json()
    assert [row["target"] for row in rows] == ["task_3", "task_2", "task_1", "task_0"]
    assert rows[0]["tenant_id"] == TENANT and rows[0]["metadata"]["api_key"] == "[REDACTED]"

    exported = client.get("/api/v1/audit-records?action=audit.export", headers=OWNER).json()["items"]
    assert len(exported) == 1
    assert exported[0]["metadata"] == {"format": "json", "filters": {"action": "task.create"}}

    csv_response = client.get("/api/v1/audit/export?format=csv&since=2026-09-05T00:00:00Z", headers=OWNER)
    reader = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert {row["action"] for row in reader} == {"workspace_member.deactivate", "audit.export"}


def test_csv_export_neutralises_spreadsheet_formulas(client: TestClient) -> None:
    memory_repository.record_audit(AuditRecord(
        id="audit_formula", tenant_id=TENANT, workspace_id=WORKSPACE, actor="=HYPERLINK(1)",
        action="task.create", target="+cmd", outcome="success", correlation_id="c1",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    ))
    text = client.get("/api/v1/audit/export?format=csv&action=task.create", headers=OWNER).text
    row = next(csv.DictReader(io.StringIO(text)))
    assert row["actor"] == "'=HYPERLINK(1)" and row["target"] == "'+cmd"


def test_export_spans_more_than_one_batch(client: TestClient) -> None:
    seed(1203)
    rows = client.get("/api/v1/audit/export?action=task.create", headers=OWNER).json()
    assert len(rows) == 1203
    assert len({row["id"] for row in rows}) == 1203


# --------------------------------------------------------------------------- members


def _join(client: TestClient, user_id: str, role: str, email: str | None = None) -> None:
    body = {"role": role, "invitee_user_id": user_id}
    if email:
        body["invitee_email"] = email
    created = client.post("/api/v1/workspace-invitations", headers=OWNER, json=body).json()
    accepted = client.post("/api/v1/workspace-invitations/accept",
                           headers=headers_for(user_id, email=email), json={"token": created["token"]})
    assert accepted.status_code == 200, accepted.text


def test_members_search_filter_page_and_summary(client: TestClient) -> None:
    _join(client, "ada", "member", "ada@example.com")
    _join(client, "bob", "viewer")
    _join(client, "cy", "owner")
    assert client.post("/api/v1/workspace-members/bob/deactivate", headers=OWNER).status_code == 200

    everyone = client.get("/api/v1/workspace-members", headers=OWNER).json()
    assert [m["user_id"] for m in everyone] == ["cy", "owner_1", "ada", "bob"]
    assert next(m for m in everyone if m["user_id"] == "ada")["email"] == "ada@example.com"

    assert [m["user_id"] for m in client.get("/api/v1/workspace-members?q=EXAMPLE.com", headers=OWNER).json()] == ["ada"]
    assert [m["user_id"] for m in client.get("/api/v1/workspace-members?status=deactivated", headers=OWNER).json()] == ["bob"]
    assert [m["user_id"] for m in client.get("/api/v1/workspace-members?role=owner", headers=OWNER).json()] == ["cy", "owner_1"]

    first = client.get("/api/v1/workspace-members?limit=2", headers=OWNER)
    assert [m["user_id"] for m in first.json()] == ["cy", "owner_1"]
    rest = client.get(f"/api/v1/workspace-members?limit=2&cursor={first.headers['x-next-cursor']}", headers=OWNER)
    assert [m["user_id"] for m in rest.json()] == ["ada", "bob"] and "x-next-cursor" not in rest.headers

    summary = client.get("/api/v1/workspace-members/summary", headers=OWNER).json()
    assert summary == {"total": 4, "active": 3, "deactivated": 1, "active_owners": 2, "pending_invitations": 0}
    assert client.get("/api/v1/workspace-members/summary", headers=MEMBER).status_code == 403


def test_resend_rotates_the_token_and_restarts_the_expiry(client: TestClient) -> None:
    created = client.post("/api/v1/workspace-invitations", headers=OWNER,
                          json={"role": "member", "invitee_user_id": "dee", "ttl_hours": 1}).json()
    invitation_id = created["invitation"]["id"]
    resent = client.post(f"/api/v1/workspace-invitations/{invitation_id}/resend", headers=OWNER, json={"ttl_hours": 48})
    assert resent.status_code == 200, resent.text
    body = resent.json()
    assert body["token"] != created["token"] and body["invitation"]["status"] == "pending"
    assert body["invitation"]["expires_at"] > created["invitation"]["expires_at"]
    # The old token no longer works; the new one does.
    old = client.post("/api/v1/workspace-invitations/accept", headers=headers_for("dee"), json={"token": created["token"]})
    assert old.status_code == 404
    assert client.post("/api/v1/workspace-invitations/accept", headers=headers_for("dee"), json={"token": body["token"]}).status_code == 200
    # Accepted (or revoked) invitations cannot be resent.
    assert client.post(f"/api/v1/workspace-invitations/{invitation_id}/resend", headers=OWNER).status_code == 409
    assert client.post(f"/api/v1/workspace-invitations/{invitation_id}/resend", headers=MEMBER).status_code == 403
    actions = [item["action"] for item in client.get("/api/v1/audit-records?action=workspace_invitation.*", headers=OWNER).json()["items"]]
    assert actions == ["workspace_invitation.accept", "workspace_invitation.resend", "workspace_invitation.create"]
    assert client.get("/api/v1/workspace-invitations?status=accepted", headers=OWNER).json()[0]["id"] == invitation_id
    assert client.get("/api/v1/workspace-invitations?status=pending", headers=OWNER).json() == []


def test_an_expired_invitation_can_be_resent(client: TestClient) -> None:
    created = client.post("/api/v1/workspace-invitations", headers=OWNER,
                          json={"role": "viewer", "invitee_user_id": "eve"}).json()
    invitation = store.invitations[created["invitation"]["id"]]
    store.invitations[invitation.id] = invitation.model_copy(update={"expires_at": invitation.created_at - timedelta(hours=1)})
    assert client.get("/api/v1/workspace-invitations?status=expired", headers=OWNER).json()[0]["id"] == invitation.id
    resent = client.post(f"/api/v1/workspace-invitations/{invitation.id}/resend", headers=OWNER)
    assert resent.status_code == 200 and resent.json()["invitation"]["status"] == "pending"


# --------------------------------------------------------------------------- workspaces


def test_permissions_report_what_the_caller_may_do(client: TestClient) -> None:
    _join(client, "member_1", "member")
    owner = client.get("/api/v1/workspaces/current/permissions", headers=OWNER).json()
    assert owner["can_manage_workspace"] and owner["can_read_audit"] and owner["can_manage_members"]
    member = client.get("/api/v1/workspaces/current/permissions", headers=MEMBER).json()
    assert member["role"] == "member"
    assert not member["can_manage_workspace"] and not member["can_read_audit"] and not member["can_manage_members"]
    admin = client.get("/api/v1/workspaces/current/permissions", headers=ORG_ADMIN_MEMBER).json()
    assert admin["organization_admin"] and admin["can_read_audit"] and admin["can_manage_workspace"]
    assert not admin["can_manage_members"]


def test_rename_archive_restore_with_permission_checks(client: TestClient) -> None:
    assert client.patch("/api/v1/workspaces/current", headers=MEMBER, json={"name": "Nope"}).status_code == 403
    renamed = client.patch("/api/v1/workspaces/current", headers=OWNER, json={"name": "  Audit   room "})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Audit room"

    wrong = client.post("/api/v1/workspaces/current/archive", headers=OWNER, json={"confirm_name": "audit room"})
    assert wrong.status_code == 422
    archived = client.post("/api/v1/workspaces/current/archive", headers=OWNER, json={"confirm_name": "Audit room"})
    assert archived.status_code == 200 and archived.json()["archived_at"] and archived.json()["archived_by"] == "owner_1"

    # Read-only now: reads work, writes are refused with the way out.
    assert client.get("/api/v1/workspaces/current", headers=OWNER).status_code == 200
    refused = client.post("/api/v1/workspace-invitations", headers=OWNER, json={"role": "viewer", "invitee_user_id": "zed"})
    assert refused.status_code == 409 and "archived" in refused.json()["error"]["message"]
    assert client.patch("/api/v1/workspaces/current", headers=OWNER, json={"name": "Again"}).status_code == 409
    listed = client.get("/api/v1/me/workspace-memberships", headers=OWNER).json()
    assert next(item for item in listed if item["workspace_id"] == WORKSPACE)["archived"] is True

    assert client.post("/api/v1/workspaces/current/restore", headers=MEMBER).status_code == 403
    restored = client.post("/api/v1/workspaces/current/restore", headers=OWNER)
    assert restored.status_code == 200 and restored.json()["archived_at"] is None
    assert client.post("/api/v1/workspace-invitations", headers=OWNER,
                       json={"role": "viewer", "invitee_user_id": "zed"}).status_code == 201
    actions = [item["action"] for item in client.get("/api/v1/audit-records?action=workspace.*", headers=OWNER).json()["items"]]
    assert actions == ["workspace.restore", "workspace.archive", "workspace.rename"]


def test_an_invitation_cannot_be_accepted_into_an_archived_workspace(client: TestClient) -> None:
    created = client.post("/api/v1/workspace-invitations", headers=OWNER, json={"role": "viewer", "invitee_user_id": "fay"}).json()
    client.post("/api/v1/workspaces/current/archive", headers=OWNER, json={"confirm_name": "Audit desk"})
    refused = client.post("/api/v1/workspace-invitations/accept", headers=headers_for("fay"), json={"token": created["token"]})
    assert refused.status_code == 409


def test_browsers_can_rename_and_read_the_paging_and_download_headers(client: TestClient) -> None:
    preflight = client.options("/api/v1/workspaces/current", headers={
        "origin": "http://127.0.0.1:5173", "access-control-request-method": "PATCH",
        "access-control-request-headers": "authorization,content-type,x-workspace-id",
    })
    assert preflight.status_code == 200 and "PATCH" in preflight.headers["access-control-allow-methods"]
    page = client.get("/api/v1/workspace-members?limit=1", headers={**OWNER, "origin": "http://127.0.0.1:5173"})
    exposed = page.headers["access-control-expose-headers"].lower()
    assert "x-next-cursor" in exposed and "content-disposition" in exposed


def test_create_workspace_makes_the_caller_its_owner_and_audits_both_sides(client: TestClient) -> None:
    refused = client.post("/api/v1/organization/workspaces", headers=MEMBER, json={"name": "Finance"})
    assert refused.status_code == 403
    created = client.post("/api/v1/organization/workspaces", headers=OWNER, json={"name": "Finance team"})
    assert created.status_code == 201, created.text
    workspace = created.json()
    assert workspace["id"].startswith("ws_finance-team_") and workspace["tenant_id"] == TENANT

    new_owner = headers_for("owner_1", roles="owner", workspace=workspace["id"])
    assert client.get("/api/v1/workspace-memberships/current", headers=new_owner).json()["role"] == "owner"
    there = client.get("/api/v1/audit-records", headers=new_owner).json()["items"]
    assert [item["action"] for item in there] == ["workspace.create"]
    assert there[0]["metadata"]["created_from"] == WORKSPACE
    here = client.get("/api/v1/audit-records?action=workspace.create", headers=OWNER).json()["items"]
    assert here[0]["target"] == workspace["id"]
    listed = {item["workspace_id"]: item for item in client.get("/api/v1/me/workspace-memberships", headers=OWNER).json()}
    assert listed[workspace["id"]]["workspace_name"] == "Finance team"

    explicit = client.post("/api/v1/organization/workspaces", headers=ORG_ADMIN_MEMBER,
                           json={"name": "Legal", "workspace_id": "workspace_legal"})
    assert explicit.status_code == 201 and explicit.json()["id"] == "workspace_legal"
    duplicate = client.post("/api/v1/organization/workspaces", headers=OWNER,
                            json={"name": "Legal 2", "workspace_id": "workspace_legal"})
    assert duplicate.status_code == 409
    bad = client.post("/api/v1/organization/workspaces", headers=OWNER, json={"name": "x", "workspace_id": "a b"})
    assert bad.status_code == 422
