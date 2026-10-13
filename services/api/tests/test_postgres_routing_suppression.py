"""Approval routing rules and the contact suppression list in PostgreSQL (migration
0053_routing_suppression), as the non-owner app role.

Rules with AED bands and approver steps round-trip through ``approval_rules`` and route a
decision; the suppression list is tenant-scoped by RLS alone, its adds are idempotent, its
changes are audited in the same transaction, and a send to a suppressed recipient is refused.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings
from anum_api.suppression import SuppressionChannel, apply_inbound_keyword, suppressed_for_call

pytestmark = pytest.mark.database

URL = "/api/v1/contact-suppressions"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, roles: str = "owner,org_admin", user: str = "user_test") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": roles}


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    yield TestClient(app)


def _count(app_session: Callable[..., Iterator[Session]], sql: str, tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> int:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        return int(session.execute(text(sql)).scalar_one())


def test_routed_rules_round_trip_and_update(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    body = {"name": "Refunds up to AED 250", "action_pattern": "*refund*",
            "conditions": {"amount_at_most": 250, "disputed": "no"},
            "approver_steps": [{"approvers": ["owner", "user:care_lead"]}, {"approvers": ["owner"]}]}
    created = client.post("/api/v1/organization/approval-rules", headers=headers(), json=body)
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    listed = client.get("/api/v1/organization/approval-rules", headers=headers()).json()
    assert listed[0]["conditions"]["amount_at_most"] == 250 and listed[0]["approver_steps"][1]["approvers"] == ["owner"]
    updated = client.put(f"/api/v1/organization/approval-rules/{rule_id}", headers=headers(),
                         json={**body, "conditions": {"amount_above": 250}, "approver_steps": [{"approvers": ["owner"]}]})
    assert updated.status_code == 200, updated.text
    with app_session(tenant_context()) as session:
        row = session.execute(text("select conditions, approver_steps, updated_by from approval_rules")).one()
    assert row.conditions["amount_above"] == 250 and row.approver_steps == [{"approvers": ["owner"]}] and row.updated_by == "user_test"
    # The runtime reads it in the tenant's scope; another tenant never sees it.
    from anum_api.db.repository import SqlAlchemyRepository

    with app_session(tenant_context()) as session:
        governance = SqlAlchemyRepository(session).get_tool_governance(tenant_context())
    assert governance.approval_rules[0].conditions["amount_above"] == 250
    with app_session(tenant_context(TENANT_B, WORKSPACE_B)) as session:
        assert SqlAlchemyRepository(session).get_tool_governance(tenant_context(TENANT_B, WORKSPACE_B)).approval_rules == []
    assert client.delete(f"/api/v1/organization/approval-rules/{rule_id}", headers=headers(TENANT_B, WORKSPACE_B)).status_code == 404
    assert client.delete(f"/api/v1/organization/approval-rules/{rule_id}", headers=headers()).status_code == 204
    assert _count(app_session, "select count(*) from approval_rules") == 0
    assert _count(app_session, "select count(*) from audit_records where action like 'approval_rule.%'") == 3


def test_suppression_list_in_postgres(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    added = client.post(URL, headers=headers(), json={"recipient": "+971 50 123 4567", "channel": "whatsapp", "reason": "Asked by phone"})
    assert added.status_code == 201, added.text
    again = client.post(URL, headers=headers(), json={"recipient": "0501234567", "channel": "whatsapp", "reason": "Asked again"})
    assert again.status_code == 200 and again.json()["id"] == added.json()["id"]
    with app_session(tenant_context()) as session:
        stored = session.execute(text("select recipient_hash, masked from contact_suppressions")).all()
    assert len(stored) == 1 and stored[0].masked == "+971•••4567" and "501234567" not in stored[0].recipient_hash
    # RLS alone: the other tenant sees nothing.
    assert _count(app_session, "select count(*) from contact_suppressions", TENANT_B, WORKSPACE_B) == 0
    assert client.get(URL, headers=headers(TENANT_B, WORKSPACE_B)).json()["total"] == 0

    context = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test", roles=["owner"])
    other = TenantContext(tenant_id=TENANT_B, workspace_id=WORKSPACE_B, user_id="user_test", roles=["owner"])
    call = {"to": "971501234567", "text": "Hello"}
    assert suppressed_for_call(context, "send_whatsapp", call, None)
    assert not suppressed_for_call(other, "send_whatsapp", call, None)

    # Inbound keywords: STOP adds once (concurrent-safe insert), START removes only keyword rows.
    assert apply_inbound_keyword(context, SuppressionChannel.WHATSAPP, "971509999999", "stop", "m1")
    assert not apply_inbound_keyword(context, SuppressionChannel.WHATSAPP, "971509999999", "stop", "m2")
    assert apply_inbound_keyword(context, SuppressionChannel.WHATSAPP, "971509999999", "start", "m3")
    assert not apply_inbound_keyword(context, SuppressionChannel.WHATSAPP, "971501234567", "start", "m4")
    assert _count(app_session, "select count(*) from contact_suppressions") == 1

    entry = client.get(URL, headers=headers()).json()["items"][0]
    removed = client.request("DELETE", f"{URL}/{entry['id']}", headers=headers(), json={"reason": "Customer asked to start"})
    assert removed.status_code == 204
    assert _count(app_session, "select count(*) from contact_suppressions") == 0
    assert _count(app_session, "select count(*) from audit_records where action like 'suppression.%'") == 4
    export = client.get(f"{URL}/export", headers=headers())
    assert export.status_code == 200 and export.text.startswith("contact,kind,channel")


def test_rls_refuses_suppression_rows_for_another_tenant(app_session: Callable[..., Iterator[Session]], seed_scopes: None) -> None:
    row = (
        "insert into contact_suppressions (tenant_id, id, recipient_hash, masked, kind, channel, purpose, source, reason, "
        "created_by, created_at) values (:tenant, :id, :hash, 'x', 'phone', 'all', 'all', 'manual', '', 'u', now())"
    )
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text(row), {"tenant": TENANT_B, "id": "s1", "hash": "a" * 64})
    with app_session(tenant_context(TENANT_A, WORKSPACE_A), commit=True) as session:
        session.execute(text(row), {"tenant": TENANT_A, "id": "s2", "hash": "b" * 64})
    with pytest.raises(DBAPIError):
        with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
            session.execute(text(row), {"tenant": TENANT_A, "id": "s3", "hash": "b" * 64})  # same contact and channel
    assert _count(app_session, "select count(*) from contact_suppressions", TENANT_B, WORKSPACE_B) == 0
    with app_session(tenant_context(TENANT_A, WORKSPACE_A)) as session:
        forced = session.execute(text(
            "select relforcerowsecurity from pg_class where relname = 'contact_suppressions'"
        )).scalar_one()
    assert forced is True
