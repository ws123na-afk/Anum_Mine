"""Migration 0129_setup_plan_gaps in PostgreSQL, as the non-owner app role: consent records,
loop limits, approval reminders (discovered as ``anum_maintenance``) and the Home order, each
under forced RLS; and the whole pilot of the setup plan setting them through the SQL stores."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from anum_api.approval_reminders import run_approval_reminder_pass
from anum_api.contact_consent import consent_refusals_for_call
from anum_api.agent_tools import ToolDefinition
from anum_api.main import app
from anum_api.schemas import RiskLevel, TenantContext, utc_now
from anum_api.setup_plans import load_blueprints

from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)
from test_postgres_team_work import _member, _rows, _scoped

pytestmark = pytest.mark.database

PLAN = "shahzeb-business-group"


def h(user: str = "owner_a", roles: str = "owner,org_admin", tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


@pytest.fixture
def client(postgres_backend: None, database_engine: Engine) -> TestClient:  # noqa: F811
    _member(database_engine, TENANT_A, WORKSPACE_A, "owner_a", "owner")
    _member(database_engine, TENANT_A, WORKSPACE_A, "member_a", "member")
    _member(database_engine, TENANT_B, WORKSPACE_B, "owner_b", "owner")
    return TestClient(app)


def _count(engine: Engine, sql: str, tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> int:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            _scoped(connection, tenant, workspace)
            return int(connection.execute(text(sql)).scalar_one())
        finally:
            transaction.rollback()


def test_consents_are_per_tenant_and_checked_on_marketing_sends(client: TestClient, database_engine: Engine) -> None:
    body = {"recipient": "+971501234567", "channel": "whatsapp", "lawful_basis": "consent", "source": "Website form",
            "consented_at": (utc_now() - timedelta(days=1)).isoformat()}
    first = client.post("/api/v1/contact-consents", headers=h(), json=body)
    assert first.status_code == 201, first.text
    again = client.post("/api/v1/contact-consents", headers=h(), json={**body, "source": "Counter"})
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert _count(database_engine, "select count(*) from contact_consents") == 1
    assert _count(database_engine, "select count(*) from contact_consents", TENANT_B, WORKSPACE_B) == 0
    assert _count(database_engine, "select count(*) from contact_consents where masked like '%501234567%'") == 0
    withdrawn = client.post(f"/api/v1/contact-consents/{first.json()['id']}/withdraw", headers=h(), json={"reason": "Asked by phone"})
    assert withdrawn.status_code == 200
    assert client.put("/api/v1/contact-consents/settings", headers=h(), json={"require_for_marketing": True}).status_code == 200
    tool = ToolDefinition(name="send_whatsapp_template", description="", risk_level=RiskLevel.HIGH, category="whatsapp", action_kind="send")
    ctx = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="owner_a", roles=["owner"])
    assert consent_refusals_for_call(ctx, tool.name, {"to": "971501234567", "purpose": "marketing"}, tool)[0].why == "withdrawn"
    other = TenantContext(tenant_id=TENANT_B, workspace_id=WORKSPACE_B, user_id="owner_b", roles=["owner"])
    assert consent_refusals_for_call(other, tool.name, {"to": "971501234567", "purpose": "marketing"}, tool) == []
    # RLS refuses a row for another tenant.
    with pytest.raises(DBAPIError):
        with database_engine.begin() as connection:
            connection.execute(text(f"set local role {APP_ROLE}"))
            _scoped(connection, TENANT_A, WORKSPACE_A)
            connection.execute(text(
                "insert into contact_consents (id, tenant_id, recipient_hash, masked, kind, channel, lawful_basis, source, consented_at, created_by) "
                "values ('c1', :t, repeat('a', 64), 'x', 'email', 'email', 'consent', 'src', now(), 'u')"), {"t": TENANT_B})


def test_loop_limits_and_home_order_live_in_their_scope(client: TestClient, database_engine: Engine) -> None:
    agent = client.post("/api/v1/agents", headers=h(), json={"name": "PG agent", "category": "work", "sharing": "workspace"}).json()
    assert client.put(f"/api/v1/agent-controls/loop-limits/{agent['id']}", headers=h(), json={"max_runs_per_task": 3}).status_code == 200
    assert _count(database_engine, "select count(*) from agent_loop_limits") == 1
    assert _count(database_engine, "select count(*) from agent_loop_limits", TENANT_A, WORKSPACE_A2) == 0
    listed = client.get("/api/v1/agent-controls/loop-limits", headers=h()).json()
    assert listed["items"][0]["max_runs_per_task"] == 3
    assert client.put("/api/v1/home/layout", headers=h(), json={"sections": ["decisions", "focus"]}).status_code == 200
    assert client.get("/api/v1/home/layout", headers=h()).json()["sections"] == ["decisions", "focus"]
    assert client.get("/api/v1/home/layout", headers=h("member_a", "member")).json()["customized"] is False


def test_reminders_are_discovered_as_maintenance_and_sent_once(client: TestClient, database_engine: Engine) -> None:
    assert client.put("/api/v1/approval-reminders/settings", headers=h(), json={"reminder_hours": 2}).status_code == 200
    with database_engine.begin() as connection:
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A)
        connection.execute(text(
            "insert into tasks (id, title, prompt, status, tenant_id, workspace_id, created_at, updated_at, created_by_user_id) "
            "values ('task_rem', 'Refund', 'Refund', 'waiting_approval', :t, :w, now(), now(), 'owner_a')"), {"t": TENANT_A, "w": WORKSPACE_A})
        connection.execute(text(
            "insert into approvals (id, task_id, action, risk_level, status, reason, tenant_id, workspace_id, created_at, updated_at, expires_at) "
            "values ('appr_old', 'task_rem', 'commerce.refund', 'high', 'pending', 'Refund', :t, :w, now() - interval '3 hours', now(), now() + interval '20 hours')"),
            {"t": TENANT_A, "w": WORKSPACE_A})
    due = _rows(database_engine, "anum_maintenance", "select tenant_id, id from approvals")
    assert [r.id for r in due] == ["appr_old"]
    assert run_approval_reminder_pass() == 1
    assert run_approval_reminder_pass() == 0
    assert _count(database_engine, "select count(*) from approval_reminders") == 1
    assert _count(database_engine, "select count(*) from approval_reminders", TENANT_B, WORKSPACE_B) == 0
    kinds = [n["kind"] for n in client.get("/api/v1/notifications", headers=h()).json()["items"]]
    assert "approval_reminder" in kinds
    # The maintenance role reads no content of the approval.
    with pytest.raises(DBAPIError):
        _rows(database_engine, "anum_maintenance", "select reason from approvals")


def test_pilot_sets_the_new_parts_through_postgres(client: TestClient, database_engine: Engine) -> None:
    response = client.post(f"/api/v1/setup-plans/{PLAN}/apply", headers=h(), json={"phase": "pilot", "plan_version": load_blueprints()[PLAN].version})
    assert response.status_code == 200, response.text
    failed = [(i["key"], i["error"]) for i in response.json()["items"] if i["outcome"] == "failed"]
    assert not failed, failed
    assert _count(database_engine, "select count(*) from agent_cost_caps") == 12
    assert _count(database_engine, "select count(*) from agent_loop_limits") == 12
    assert _count(database_engine, "select reminder_hours from approval_reminder_settings") == 4
    assert _count(database_engine, "select count(*) from contact_consent_settings where require_for_marketing") == 1
    again = client.post(f"/api/v1/setup-plans/{PLAN}/apply", headers=h(), json={"phase": "pilot", "plan_version": load_blueprints()[PLAN].version}).json()
    assert not again["outcomes"].get("created") and not again["outcomes"].get("failed")
    checklist = client.get(f"/api/v1/setup-plans/{PLAN}/checklist", headers=h())
    assert checklist.status_code == 200 and checklist.json()["total"] >= 10
    offices = [i for i in response.json()["items"] if i["section"] == "offices"]
    assert offices and offices[0]["reason_code"] == "no_offices"
