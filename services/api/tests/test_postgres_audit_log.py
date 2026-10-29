"""PostgreSQL, as the non-owner app role under forced RLS: the audit log's filters and
keyset pages, the streamed export (its own short sessions, batch by batch), invitation
resend, and the workspace lifecycle (create, rename, archive, restore, the archived
flag in the caller's workspace list). See docs/governance-and-scale.md#audit-log and
docs/identity.md#workspace-lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from anum_api import audit_log
from anum_api.audit import AuditRecord
from anum_api.main import app
from anum_api.settings import settings

from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.database


def headers(user: str = "owner_pg", *, roles: str = "owner", tenant: str = TENANT_A,
            workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


OWNER = headers()


@pytest.fixture
def client(postgres_backend: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:  # noqa: F811
    monkeypatch.setattr(settings, "auth_mode", "headers")
    return TestClient(app)


def _seed(repository_factory, tenant: str, workspace: str, count: int, *, action: str = "task.create",
          actor: str = "owner_pg") -> None:
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with repository_factory(tenant_context(tenant, workspace), commit=True) as repository:
        for index in range(count):
            repository.record_audit(AuditRecord(
                id=f"audit_{workspace}_{action}_{index:03d}", tenant_id=tenant, workspace_id=workspace,
                actor=actor, action=action, target=f"target_{index}", outcome="success",
                correlation_id=f"corr_{index}", created_at=base + timedelta(minutes=index),
                metadata={"index": index},
            ))


def test_audit_pages_filters_and_detail_stay_inside_the_workspace(client: TestClient, repository_factory) -> None:
    _seed(repository_factory, TENANT_A, WORKSPACE_A, 5)
    _seed(repository_factory, TENANT_A, WORKSPACE_A, 2, action="workspace_member.role_change", actor="owner_two")
    _seed(repository_factory, TENANT_A, WORKSPACE_A2, 3)
    _seed(repository_factory, TENANT_B, WORKSPACE_B, 3)

    seen, cursor = [], None
    while True:
        query = "/api/v1/audit-records?limit=3&action=task.create" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(query, headers=OWNER)
        assert page.status_code == 200, page.text
        seen += [item["target"] for item in page.json()["items"]]
        cursor = page.json()["next_cursor"]
        if not cursor:
            break
    assert seen == [f"target_{index}" for index in (4, 3, 2, 1, 0)]

    def ids(query: str) -> set[str]:
        return {item["id"] for item in client.get(f"/api/v1/audit-records?{query}", headers=OWNER).json()["items"]}

    assert all(WORKSPACE_A in record_id for record_id in ids("limit=200"))
    assert len(ids("actor=owner_two")) == 2
    assert len(ids("action=workspace_member.*")) == 2
    # LIKE wildcards in the prefix are literal.
    assert ids("action=workspace%25member.*") == set()
    assert len(ids("q=ROLE_change")) == 2
    assert len(ids("since=2026-09-01T00:02:00%2B00:00&until=2026-09-01T00:04:00Z&action=task.create")) == 2
    actions = client.get("/api/v1/audit-records/actions", headers=OWNER).json()["actions"]
    assert actions == ["task.create", "workspace_member.role_change"]

    other = f"audit_{WORKSPACE_A2}_task.create_000"
    assert client.get(f"/api/v1/audit-records/{other}", headers=OWNER).status_code == 404
    assert client.get(f"/api/v1/audit-records/{other}", headers=headers(workspace=WORKSPACE_A2)).status_code == 200


def test_export_streams_in_batches_through_its_own_sessions(
    client: TestClient, repository_factory, monkeypatch: pytest.MonkeyPatch, database_engine: Engine
) -> None:
    monkeypatch.setattr(audit_log, "EXPORT_BATCH", 2)
    _seed(repository_factory, TENANT_A, WORKSPACE_A, 5)
    _seed(repository_factory, TENANT_A, WORKSPACE_A2, 2)
    rows = client.get("/api/v1/audit/export?action=task.create", headers=OWNER).json()
    assert [row["target"] for row in rows] == [f"target_{index}" for index in (4, 3, 2, 1, 0)]
    csv_text = client.get("/api/v1/audit/export?format=csv&action=task.create", headers=OWNER).text
    assert csv_text.count("\n") == 6 and WORKSPACE_A2 not in csv_text
    with database_engine.connect() as connection:
        exports = connection.execute(
            text("select count(*) from audit_records where action = 'audit.export' and workspace_id = :w"),
            {"w": WORKSPACE_A},
        ).scalar_one()
    assert exports == 2
    refused = client.get("/api/v1/audit/export", headers=headers("viewer_pg", roles="viewer"))
    assert refused.status_code == 403


def test_export_mention_filters_in_the_database_and_stays_inside_the_tenant(
    client: TestClient, repository_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_log, "EXPORT_BATCH", 2)
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)

    def put(tenant: str, workspace: str, record_id: str, minute: int, **fields) -> None:
        values = {"actor": "owner_pg", "action": "office.room.rename", "target": "room_other", "metadata": {}} | fields
        with repository_factory(tenant_context(tenant, workspace), commit=True) as repository:
            repository.record_audit(AuditRecord(
                id=record_id, tenant_id=tenant, workspace_id=workspace, outcome="success",
                correlation_id=f"corr_{record_id}", created_at=base + timedelta(minutes=minute), **values,
            ))

    put(TENANT_A, WORKSPACE_A, "m_target", 1, target="room_alpha")
    put(TENANT_A, WORKSPACE_A, "m_desk", 2, action="office.desk.assignment", target="desk_alpha_1",
        metadata={"after": {"agent_id": "agent_fleet", "room_id": "room_alpha"}})
    put(TENANT_A, WORKSPACE_A, "m_actor", 3, actor="agent_fleet", action="task.updated", target="task_1")
    put(TENANT_A, WORKSPACE_A, "m_list", 4, action="agent_meeting.create", target="meeting_1",
        metadata={"participants": [{"id": "agent_fleet"}], "depth": {"a": {"b": ["room_alpha"]}}})
    put(TENANT_A, WORKSPACE_A, "m_text", 5, metadata={"note": "room_alpha next door", "n": 7})
    put(TENANT_A, WORKSPACE_A2, "m_other_ws", 6, target="room_alpha")
    put(TENANT_B, WORKSPACE_B, "m_other_tenant", 7, target="room_alpha", actor="agent_fleet")

    def ids(query: str, who: dict[str, str] = OWNER) -> list[str]:
        response = client.get(f"/api/v1/audit/export?{query}", headers=who)
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json() if row["action"] != "audit.export"]

    assert ids("mention=room_alpha&mention=desk_alpha_1") == ["m_list", "m_desk", "m_target"]
    assert ids("mention=agent_fleet") == ["m_list", "m_actor", "m_desk"]
    assert ids("mention=agent_fleet&action=office.*") == ["m_desk"]
    assert ids("mention=room_al") == []
    # Ids that look like SQL/JSON path or LIKE syntax are plain values.
    assert ids('mention=%24ids&mention=%25&mention=%22%29') == []
    # Tenant B, naming tenant A's room and agent, reads only its own record (RLS and the scope).
    tenant_b = headers(tenant=TENANT_B, workspace=WORKSPACE_B)
    assert ids("mention=room_alpha&mention=agent_fleet", tenant_b) == ["m_other_tenant"]
    # The filter adds no permission.
    assert client.get("/api/v1/audit/export?mention=room_alpha", headers=headers("viewer_pg", roles="viewer")).status_code == 403


def test_resend_rotates_the_stored_hash(client: TestClient, database_engine: Engine) -> None:
    created = client.post("/api/v1/workspace-invitations", headers=OWNER,
                          json={"role": "member", "invitee_user_id": "resend_pg"}).json()
    resent = client.post(f"/api/v1/workspace-invitations/{created['invitation']['id']}/resend", headers=OWNER)
    assert resent.status_code == 200, resent.text
    from anum_api.workspace_members import hash_invitation_token

    with database_engine.connect() as connection:
        stored = connection.execute(
            text("select token_hash from workspace_invitations where id = :id"), {"id": created["invitation"]["id"]}
        ).scalar_one()
    assert stored == hash_invitation_token(resent.json()["token"])
    accept = client.post("/api/v1/workspace-invitations/accept", headers=headers("resend_pg", roles="viewer"),
                         json={"token": created["token"]})
    assert accept.status_code == 404


def test_workspace_lifecycle_under_rls(client: TestClient, database_engine: Engine, repository_factory) -> None:
    from anum_api.schemas import WorkspaceMembership

    with repository_factory(tenant_context(TENANT_A, WORKSPACE_A), commit=True) as repository:
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        repository.save_membership(WorkspaceMembership(
            tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="owner_pg", role="owner",
            created_at=now, updated_at=now,
        ))

    renamed = client.patch("/api/v1/workspaces/current", headers=OWNER, json={"name": "Renamed A"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed A"
    assert client.post("/api/v1/workspaces/current/archive", headers=OWNER,
                       json={"confirm_name": "Renamed A"}).status_code == 200
    refused = client.post("/api/v1/workspace-invitations", headers=OWNER, json={"role": "viewer", "invitee_user_id": "x_pg"})
    assert refused.status_code == 409
    listed = client.get("/api/v1/me/workspace-memberships", headers=OWNER).json()
    assert [(item["workspace_id"], item["archived"]) for item in listed] == [(WORKSPACE_A, True)]
    assert client.post("/api/v1/workspaces/current/restore", headers=OWNER).json()["archived_at"] is None
    assert client.post("/api/v1/workspace-invitations", headers=OWNER,
                       json={"role": "viewer", "invitee_user_id": "x_pg"}).status_code == 201

    created = client.post("/api/v1/organization/workspaces", headers=OWNER,
                          json={"name": "Second", "workspace_id": "workspace_pg_new"})
    assert created.status_code == 201, created.text
    duplicate = client.post("/api/v1/organization/workspaces", headers=OWNER,
                            json={"name": "Again", "workspace_id": WORKSPACE_B})
    assert duplicate.status_code == 409
    with database_engine.begin() as connection:
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
        connection.execute(text("select set_config('anum.workspace_id', 'workspace_pg_new', true)"))
        role = connection.execute(text("select role from workspace_memberships where user_id = 'owner_pg'")).scalar_one()
        actions = connection.execute(text("select action from audit_records")).scalars().all()
    assert role == "owner" and actions == ["workspace.create"]
    names = {item["workspace_id"]: item["workspace_name"] for item in client.get("/api/v1/me/workspace-memberships", headers=OWNER).json()}
    assert names == {WORKSPACE_A: "Renamed A", "workspace_pg_new": "Second"}
    here = client.get("/api/v1/audit-records?action=workspace.*", headers=OWNER).json()["items"]
    assert [item["action"] for item in here] == ["workspace.create", "workspace.restore", "workspace.archive", "workspace.rename"]
