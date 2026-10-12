"""PostgreSQL: team work, notifications, assistants and organisations under forced RLS.

The API runs as the non-owner app role (``postgres_backend``). Below the API, these tests
check the policies of migration ``0039_team_work`` directly: workspace isolation, the
recipient-only notification policies, the append-only activity feed, and the three narrow
roles (``anum_platform_reader``, ``anum_tenant_directory`` and the ``anum_maintenance``
additions): which rows and columns they see, that they cannot write, and that granting
them ``WITH INHERIT FALSE`` never widens the app role's own queries.
See docs/team-work.md and docs/customer-organisations.md.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from anum_api.main import app
from anum_api.schemas import utc_now
from anum_api.team_work import run_reminder_pass

from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.database


def h(user: str, roles: str = "member", tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


OWNER = h("owner_a", "owner")
AHMED = h("user_ahmed")


def _scoped(connection: Connection, tenant: str | None, workspace: str | None = None, user: str | None = None) -> None:
    for name, value in (("tenant_id", tenant), ("workspace_id", workspace), ("user_id", user)):
        if value:
            connection.execute(text(f"select set_config('anum.{name}', :value, true)"), {"value": value})


def _member(engine: Engine, tenant: str, workspace: str, user: str, role: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, tenant, workspace)
        connection.execute(
            text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values (:u, :t, :w, :r, true)"),
            {"u": user, "t": tenant, "w": workspace, "r": role},
        )


@pytest.fixture
def client(postgres_backend: None, database_engine: Engine) -> TestClient:  # noqa: F811
    _member(database_engine, TENANT_A, WORKSPACE_A, "owner_a", "owner")
    _member(database_engine, TENANT_A, WORKSPACE_A, "user_ahmed", "member")
    _member(database_engine, TENANT_B, WORKSPACE_B, "owner_b", "owner")
    test_client = TestClient(app)
    assert test_client.put("/api/v1/team/people/user_ahmed", headers=OWNER, json={"display_name": "Ahmed Saleh"}).status_code == 200
    return test_client


def _rows(engine: Engine, role: str, statement: str, tenant: str | None = None, workspace: str | None = None,
          user: str | None = None) -> list:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {role}"))
            _scoped(connection, tenant, workspace, user)
            return list(connection.execute(text(statement)).all())
        finally:
            transaction.rollback()


# --------------------------------------------------------------------------- through the API


def test_work_item_lifecycle_persists_and_stays_in_its_workspace(client: TestClient, database_engine: Engine) -> None:
    item = client.post("/api/v1/work-items", headers=OWNER, json={
        "title": "Call the supplier", "assignee": {"type": "user", "id": "user_ahmed"}, "checklist": ["Get quote"]})
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    comment = client.post(f"/api/v1/work-items/{item_id}/comments", headers=AHMED, json={"body": "On it"})
    assert comment.status_code == 201
    patched = client.patch(f"/api/v1/work-items/{item_id}", headers=AHMED, json={"version": 2, "status": "done"})
    assert patched.status_code == 200, patched.text
    detail = client.get(f"/api/v1/work-items/{item_id}", headers=OWNER).json()
    assert detail["status"] == "done" and detail["comments"][0]["author_name"] == "Ahmed Saleh"
    assert detail["checklist"][0]["label"] == "Get quote"
    assert client.get("/api/v1/work-items?status=all", headers=OWNER).json()["total"] == 1
    # Same tenant, other workspace; other tenant: nothing.
    assert client.get(f"/api/v1/work-items/{item_id}", headers=h("owner_a", "owner", workspace=WORKSPACE_A2)).status_code == 404
    assert client.get("/api/v1/work-items?status=all", headers=h("owner_b", "owner", TENANT_B, WORKSPACE_B)).json()["total"] == 0
    assert client.get("/api/v1/work-items/workload", headers=OWNER).status_code == 200
    audits = _rows(database_engine, APP_ROLE, "select action from audit_records where action like 'work_item.%'", TENANT_A, WORKSPACE_A)
    assert {"work_item.create", "work_item.comment", "work_item.complete"} <= {r.action for r in audits}
    outbox = _rows(database_engine, APP_ROLE, "select type from domain_events where type like 'work_item.%'", TENANT_A, WORKSPACE_A)
    assert {"work_item.created", "work_item.completed"} <= {r.type for r in outbox}


def test_notifications_are_readable_only_by_their_recipient(client: TestClient, database_engine: Engine) -> None:
    client.post("/api/v1/work-items", headers=OWNER, json={"title": "Count", "assignee": {"type": "user", "id": "user_ahmed"}})
    mine = client.get("/api/v1/notifications", headers=AHMED).json()
    assert mine["unread"] == 1 and mine["items"][0]["kind"] == "work_assigned"
    assert client.get("/api/v1/notifications", headers=OWNER).json()["items"] == []
    # Below the API: the owner of the same workspace sees no row of Ahmed's, cannot mark it read or delete it.
    stmt = "select id from user_notifications"
    assert len(_rows(database_engine, APP_ROLE, stmt, TENANT_A, WORKSPACE_A, "user_ahmed")) == 1
    assert _rows(database_engine, APP_ROLE, stmt, TENANT_A, WORKSPACE_A, "owner_a") == []
    assert _rows(database_engine, APP_ROLE, stmt, TENANT_A, WORKSPACE_A) == []
    with database_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            _scoped(connection, TENANT_A, WORKSPACE_A, "owner_a")
            assert connection.execute(text("update user_notifications set read_at = now()")).rowcount == 0
            assert connection.execute(text("delete from user_notifications")).rowcount == 0
        finally:
            transaction.rollback()
    assert client.post("/api/v1/notifications/read-all", headers=AHMED).json() == {"updated": 1}


def test_activity_feed_is_append_only(client: TestClient, database_engine: Engine) -> None:
    client.post("/api/v1/work-items", headers=OWNER, json={"title": "Feed"})
    with database_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            _scoped(connection, TENANT_A, WORKSPACE_A)
            assert connection.execute(text("select count(*) from team_activity")).scalar_one() >= 1
            assert connection.execute(text("update team_activity set summary = 'edited'")).rowcount == 0
            assert connection.execute(text("delete from team_activity")).rowcount == 0
        finally:
            transaction.rollback()


def test_reminder_sweep_discovers_as_maintenance_and_acts_in_the_tenant(client: TestClient, database_engine: Engine) -> None:
    past = (utc_now() - timedelta(minutes=10)).isoformat()
    client.post("/api/v1/work-items", headers=OWNER, json={"title": "Late", "assignee": {"type": "user", "id": "user_ahmed"}, "due_at": past})
    client.post("/api/v1/work-items", headers=h("owner_b", "owner", TENANT_B, WORKSPACE_B), json={"title": "Late B", "due_at": past})
    due = _rows(database_engine, "anum_maintenance", "select tenant_id from work_items")
    assert sorted(r.tenant_id for r in due) == [TENANT_A, TENANT_B]
    counts = run_reminder_pass()
    assert counts["reminded"] == 2
    assert run_reminder_pass()["reminded"] == 0
    kinds = [n["kind"] for n in client.get("/api/v1/notifications", headers=AHMED).json()["items"]]
    assert "work_overdue" in kinds
    # The maintenance role sees ids and timestamps of due rows only.
    assert _rows(database_engine, "anum_maintenance", "select id, tenant_id from work_items") == []  # both reminded
    with pytest.raises(DBAPIError, match="permission denied"):
        _rows(database_engine, "anum_maintenance", "select title from work_items")
    with pytest.raises(DBAPIError, match="permission denied"):
        _rows(database_engine, "anum_maintenance", "update work_items set status = 'done'")


def test_assistants_and_routing_persist(client: TestClient) -> None:
    created = client.post("/api/v1/assistants", headers=OWNER, json={
        "department": "support", "name": "Support assistant", "allowed_tools": ["anum.respond"], "routing_keywords": ["refund"]})
    assert created.status_code == 201, created.text
    routed = client.post("/api/v1/assistants/route", headers=AHMED, json={"channel": "email", "sender": "c@example.com", "body": "I want a refund"})
    assert routed.status_code == 201 and routed.json()["assistant"]["id"] == created.json()["id"]
    assert client.get("/api/v1/assistants", headers=h("owner_b", "owner", TENANT_B, WORKSPACE_B)).json() == []
    # Auto-routing on arrival runs as ANUM in the workspace's RLS context, only after opting in.
    from anum_api.assistants import ArrivedMessage, auto_route_inbound

    arrived = [ArrivedMessage(sender="+971500000000", body="refund for my order", external_ref="whatsapp:wacv_pg")]
    assert auto_route_inbound(TENANT_A, WORKSPACE_A, "whatsapp", arrived) == 0
    opted = client.put(f"/api/v1/assistants/{created.json()['id']}", headers=OWNER, json={
        "department": "support", "name": "Support assistant", "allowed_tools": ["anum.respond"], "routing_keywords": ["refund"],
        "inbound_channels": ["whatsapp"]})
    assert opted.json()["inbound_channels"] == ["whatsapp"]
    assert auto_route_inbound(TENANT_A, WORKSPACE_A, "whatsapp", arrived) == 1
    assert auto_route_inbound(TENANT_A, WORKSPACE_A, "whatsapp", arrived) == 0  # same conversation, still open
    assert auto_route_inbound(TENANT_B, WORKSPACE_B, "whatsapp", arrived) == 0  # another tenant has no such assistant


# --------------------------------------------------------------------------- organisations


OPERATOR = h("owner_a", "owner,platform_operator")


def _onboard(client: TestClient, tenant_id: str = "acme_tyres") -> dict:
    response = client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": tenant_id, "display_name": "Acme Tyres", "first_admin_email": "admin@acme.example"})
    assert response.status_code == 201, response.text
    return response.json()


def test_onboarding_writes_inside_the_new_tenant_and_counts_through_the_platform_reader(
    client: TestClient, database_engine: Engine
) -> None:
    created = _onboard(client)
    assert created["organisation"]["pending_invitations"] == 1
    assert client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": "acme_tyres", "display_name": "Again", "first_admin_email": "x@example.com"}).status_code == 409
    assert client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": TENANT_B, "display_name": "Taken", "first_admin_email": "x@example.com"}).status_code == 409
    admin = {**h("acme_admin", "member", "acme_tyres", "acme_tyres_main"), "x-user-email": "admin@acme.example"}
    accepted = client.post("/api/v1/workspace-invitations/accept", headers=admin, json={"token": created["invitation_token"]})
    assert accepted.status_code == 200, accepted.text
    item = client.post("/api/v1/work-items", headers=h("acme_admin", "owner", "acme_tyres", "acme_tyres_main"),
                       json={"title": "Secret customer plan", "due_at": (utc_now() - timedelta(hours=1)).isoformat()})
    assert item.status_code == 201
    summary = client.get("/api/v1/platform/organizations", headers=OPERATOR).json()[0]
    assert summary["active_members"] == 1 and summary["open_work_items"] == 1 and summary["overdue_work_items"] == 1
    assert summary["health"] == "attention"
    # The platform reader: the operator's organisation only, counted columns only, no writes.
    seen = _rows(database_engine, "anum_platform_reader", "select tenant_id from work_items", user="owner_a")
    assert [r.tenant_id for r in seen] == ["acme_tyres"]
    assert _rows(database_engine, "anum_platform_reader", "select tenant_id from work_items", user="someone_else") == []
    assert _rows(database_engine, "anum_platform_reader", "select tenant_id from platform_organizations") == []
    for statement in (
        "select title from work_items",
        "select prompt from tasks",
        "select user_id from workspace_memberships",
        "select name from workspaces",
        "select invitee_email from workspace_invitations",
        "select first_admin_email from platform_organizations",
        "select id from memories",
        "update work_items set status = 'done'",
        "delete from platform_organizations",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            _rows(database_engine, "anum_platform_reader", statement, user="owner_a")


def test_tenant_directory_lists_only_the_callers_organisations(client: TestClient, database_engine: Engine) -> None:
    created = _onboard(client)
    admin = {**h("owner_a", "member", "acme_tyres", "acme_tyres_main"), "x-user-email": "admin@acme.example"}
    assert client.post("/api/v1/workspace-invitations/accept", headers=admin, json={"token": created["invitation_token"]}).status_code == 200
    orgs = client.get("/api/v1/me/organizations", headers=OWNER).json()
    assert [o["tenant_id"] for o in orgs] == [TENANT_A, "acme_tyres"]
    assert orgs[1]["display_name"] == "Acme Tyres" and orgs[1]["workspaces"][0]["role"] == "owner"
    assert [o["tenant_id"] for o in client.get("/api/v1/me/organizations", headers=AHMED).json()] == [TENANT_A]
    rows = _rows(database_engine, "anum_tenant_directory", "select tenant_id, user_id from workspace_memberships", user="user_ahmed")
    assert rows == [(TENANT_A, "user_ahmed")]
    assert _rows(database_engine, "anum_tenant_directory", "select id from tenants") == []
    for statement in ("select created_at from workspace_memberships", "select logo from tenant_branding",
                      "select id from work_items", "update tenants set name = 'x'"):
        with pytest.raises(DBAPIError, match="permission denied"):
            _rows(database_engine, "anum_tenant_directory", statement, user="user_ahmed")


def test_branding_logo_round_trip(client: TestClient) -> None:
    admin = h("owner_a", "owner,org_admin")
    assert client.put("/api/v1/organization/branding", headers=admin, json={"display_name": "Tyre House"}).status_code == 200
    png = b"\x89PNG\r\n\x1a\n" + b"\x01" * 16
    assert client.put("/api/v1/organization/branding/logo", headers={**admin, "content-type": "image/png"}, content=png).status_code == 200
    assert client.get("/api/v1/organization/branding/logo", headers=AHMED).content == png
    other = client.get("/api/v1/organization/branding", headers=h("owner_b", "owner", TENANT_B, WORKSPACE_B)).json()
    assert other["display_name"] == "Tenant B" and other["has_logo"] is False


@pytest.mark.parametrize("role", ["anum_platform_reader", "anum_tenant_directory"])
def test_new_roles_exist_without_login_and_never_widen_the_app_role(database_engine: Engine, client: TestClient, role: str) -> None:
    with database_engine.connect() as connection:
        found = connection.execute(
            text("select rolcanlogin, rolbypassrls, rolsuper from pg_roles where rolname = :r"), {"r": role}
        ).one()
    assert tuple(found) == (False, False, False)
    _onboard(client)
    with database_engine.begin() as connection:
        connection.execute(text(f"grant {role} to {APP_ROLE} with inherit false"))
    try:
        rows = _rows(database_engine, APP_ROLE, "select distinct tenant_id from workspace_memberships", TENANT_A, WORKSPACE_A, "owner_a")
        assert {r.tenant_id for r in rows} == {TENANT_A}
        tenants = _rows(database_engine, APP_ROLE, "select id from tenants", TENANT_A, WORKSPACE_A, "owner_a")
        assert [r.id for r in tenants] == [TENANT_A]
    finally:
        with database_engine.begin() as connection:
            connection.execute(text(f"revoke {role} from {APP_ROLE}"))
