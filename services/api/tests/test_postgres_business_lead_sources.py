"""One lead list in PostgreSQL (migration ``0125_unify_whatsapp_leads``), as the non-owner app role.

WhatsApp Sales CRM rows (``sales_leads``) are mapped into ``business_leads`` inside the caller's
own tenant and workspace; the old rows stay. The new columns and the decisions table keep the
forced row policies: tenant B and A's other workspace see nothing, a second mapping of the same
row is refused by the partial unique index, and the decisions table refuses writes into another
scope. The unified email mailbox list reads provider mailboxes through their row policies too.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from anum_api.business.store import business_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
BASE = "/api/v1/business"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user: str = "owner_test", roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": roles}


A, A2, B = headers(), headers(workspace_id=WORKSPACE_A2), headers(TENANT_B, WORKSPACE_B)
client = TestClient(app)


@pytest.fixture
def db(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    business_store.clear()
    yield database_engine


def _scoped(connection: Any, tenant_id: str, workspace_id: str) -> None:
    connection.execute(text(f"set local role {APP_ROLE}"))
    connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": tenant_id})
    connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": workspace_id})


def sales_lead(engine: Engine, lead_id: str, phone: str | None, *, tenant: str = TENANT_A, workspace: str = WORKSPACE_A, status: str = "new") -> None:
    with engine.begin() as connection:
        _scoped(connection, tenant, workspace)
        connection.execute(text(
            "insert into sales_leads (id, tenant_id, workspace_id, contact_id, source_conversation_id, source_message_id, "
            "preferred_language, intent, status, contact_name, phone_e164, requirements) values "
            "(:id, :t, :w, :c, :conv, :msg, 'en', 'website', :status, 'Sara', :phone, 'Need a bilingual website')"
        ), {"id": lead_id, "t": tenant, "w": workspace, "c": f"contact_{lead_id}", "conv": f"conv_{lead_id}", "msg": f"wamid_{lead_id}",
            "status": status, "phone": phone})


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def test_whatsapp_leads_map_into_business_leads_inside_their_own_workspace(db: Engine) -> None:
    sales_lead(db, "wa_pg_1", "+971501112222")
    sales_lead(db, "wa_pg_2", "+971501112222", status="lost")
    sales_lead(db, "wa_pg_b", "+971501112222", tenant=TENANT_B, workspace=WORKSPACE_B)
    waiting = ok(client.get(f"{BASE}/lead-sources/whatsapp", headers=A))
    assert [item["id"] for item in waiting["items"]] == ["wa_pg_1", "wa_pg_2"]
    assert ok(client.get(f"{BASE}/lead-sources/whatsapp", headers=A2))["total"] == 0
    assert [item["id"] for item in ok(client.get(f"{BASE}/lead-sources/whatsapp", headers=B))["items"]] == ["wa_pg_b"]

    # Same number twice: one lead, one question for the owner.
    assert ok(client.post(f"{BASE}/lead-sources/whatsapp/map-all", headers=A)) == {"mapped": 1, "waiting_for_owner": 1}
    lead_id = ok(client.get(f"{BASE}/leads", headers=A, params={"channel": "whatsapp"}))["items"][0]["id"]
    left = ok(client.get(f"{BASE}/lead-sources/whatsapp", headers=A))["items"]
    assert [item["id"] for item in left] == ["wa_pg_2"] and left[0]["suggestions"][0]["lead_id"] == lead_id
    ok(client.post(f"{BASE}/lead-sources/whatsapp/wa_pg_2/map", headers=A, json={"link_to_lead_id": lead_id}))
    assert ok(client.get(f"{BASE}/lead-sources/whatsapp", headers=A))["total"] == 0
    # The other tenant's row is unknown here, and tenant B cannot link to A's lead.
    assert client.post(f"{BASE}/lead-sources/whatsapp/wa_pg_b/map", headers=A, json={}).status_code == 404
    assert client.post(f"{BASE}/lead-sources/whatsapp/wa_pg_b/map", headers=B, json={"link_to_lead_id": lead_id}).status_code == 404
    assert ok(client.post(f"{BASE}/lead-sources/whatsapp/wa_pg_b/map", headers=B, json={}))["linked"] is False

    with db.begin() as connection:
        _scoped(connection, TENANT_A, WORKSPACE_A)
        row = connection.execute(text("select channel, channel_conversation_id, channel_phone, sales_lead_id, state from business_leads")).one()
        assert tuple(row) == ("whatsapp", "conv_wa_pg_1", "+971501112222", "wa_pg_1", "discovered")
        decisions = connection.execute(text("select pair_key, decision from business_lead_pair_decisions")).all()
        assert [tuple(item) for item in decisions] == [(f"lead:{lead_id}|whatsapp:wa_pg_2", "merged")]
        # The old table is kept as it was (expand only).
        assert connection.execute(text("select count(*) from sales_leads")).scalar() == 2
    with db.begin() as connection:
        _scoped(connection, TENANT_A, WORKSPACE_A2)
        assert connection.execute(text("select count(*) from business_lead_pair_decisions")).scalar() == 0
        assert connection.execute(text("select count(*) from business_leads")).scalar() == 0


def test_one_business_lead_per_whatsapp_lead_and_scoped_decisions(db: Engine) -> None:
    sales_lead(db, "wa_pg_u", "+971503334444")
    lead_id = ok(client.post(f"{BASE}/lead-sources/whatsapp/wa_pg_u/map", headers=A, json={}))["lead_id"]
    assert client.post(f"{BASE}/lead-sources/whatsapp/wa_pg_u/map", headers=A, json={}).status_code == 409
    with pytest.raises(DBAPIError):
        with db.begin() as connection:
            _scoped(connection, TENANT_A, WORKSPACE_A)
            connection.execute(text(
                "insert into business_leads (id, tenant_id, workspace_id, account_id, title, state, owner_id, source, created_by, sales_lead_id) "
                "select 'blead_copy', tenant_id, workspace_id, account_id, 'Copy', 'discovered', owner_id, source, created_by, sales_lead_id "
                "from business_leads where id = :id"
            ), {"id": lead_id})
    with pytest.raises(DBAPIError):
        with db.begin() as connection:
            _scoped(connection, TENANT_A, WORKSPACE_A)
            connection.execute(text(
                "insert into business_lead_pair_decisions (id, tenant_id, workspace_id, pair_key, decision, decided_by) "
                "values ('bpair_x', :t, :w, 'lead:a|lead:b', 'separate', 'someone')"
            ), {"t": TENANT_B, "w": WORKSPACE_B})
    with pytest.raises(DBAPIError):
        with db.begin() as connection:
            _scoped(connection, TENANT_A, WORKSPACE_A)
            connection.execute(text("update business_leads set channel = 'sms' where id = :id"), {"id": lead_id})


def test_duplicate_suggestions_and_merge_in_postgres(db: Engine) -> None:
    sales_lead(db, "wa_pg_m", "+971505556666")
    first = ok(client.post(f"{BASE}/lead-sources/whatsapp/wa_pg_m/map", headers=A, json={}))["lead_id"]
    account = ok(client.post(f"{BASE}/accounts", headers=A, json={"name": "Other Co"}), 201)
    contact = ok(client.post(f"{BASE}/accounts/{account['id']}/contacts", headers=A,
                             json={"name": "Sara", "phone": "0505556666", "consent_basis": "inbound_inquiry"}), 201)["contacts"][0]
    second = ok(client.post(f"{BASE}/leads", headers=A, json={"title": "By hand", "account_id": account["id"], "contact_id": contact["id"]}), 201)["lead"]["id"]
    pairs = ok(client.get(f"{BASE}/lead-duplicates", headers=A))
    assert len(pairs) == 1 and {pairs[0]["first"]["id"], pairs[0]["second"]["id"]} == {first, second}
    assert ok(client.get(f"{BASE}/lead-duplicates", headers=A2)) == []
    merged = ok(client.post(f"{BASE}/lead-duplicates/merge", headers=A, json={"keep_id": second, "merge_id": first}))
    assert merged == {"kept_id": second, "closed_id": first}
    kept = ok(client.get(f"{BASE}/leads/{second}", headers=A))["lead"]
    assert kept["sales_lead_id"] == "wa_pg_m" and ok(client.get(f"{BASE}/leads/{first}", headers=A))["lead"]["state"] == "lost"
    assert client.post(f"{BASE}/lead-duplicates/merge", headers=B, json={"keep_id": second, "merge_id": first}).status_code == 404


def test_unified_mailboxes_read_provider_mailboxes_through_their_row_policies(db: Engine) -> None:
    from anum_api.email_inbox import EmailAccount, open_email_store
    from anum_api.email_providers import EmailProvider
    from anum_api.schemas import TenantContext, utc_now

    now = utc_now()
    for tenant, workspace, account_id in ((TENANT_A, WORKSPACE_A, "acct_pg_a"), (TENANT_B, WORKSPACE_B, "acct_pg_b")):
        context = TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="owner_test", roles=["owner"])
        with open_email_store(context) as store:
            store.add_account(EmailAccount(id=account_id, tenant_id=tenant, workspace_id=workspace, provider=EmailProvider.OUTLOOK,
                                           address=f"{account_id}@outlook.example", connected_by="owner_test", created_at=now, updated_at=now))
    found = ok(client.get("/api/v1/email/unified/mailboxes", headers=A))
    assert [item["ref"] for item in found["items"]] == ["provider:acct_pg_a"] and found["items"][0]["source"] == "outlook"
    assert ok(client.get("/api/v1/email/unified/mailboxes", headers=A2))["items"] == []
    assert [item["ref"] for item in ok(client.get("/api/v1/email/unified/mailboxes", headers=B))["items"]] == ["provider:acct_pg_b"]


def test_the_ingestion_writes_the_business_lead_under_row_policies_and_the_overview_counts_it(db: Engine) -> None:
    """The contract step (docs/business.md#whatsapp-leads) as the app role: the webhook's own
    context writes the Business lead in its workspace only; a number already on an open lead waits;
    the Sales overview reads Business leads; ``sales_leads`` is kept."""
    from anum_api import sales_whatsapp_ingestion as importer
    from anum_api.db.sales_repository import SalesCRMStore
    from anum_api.schemas import TenantContext

    webhook = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="system:whatsapp", roles=[])

    def crm_row(lead_id: str, phone: str) -> Any:
        sales_lead(db, lead_id, phone)
        with db_session.SessionLocal() as session, session.begin():
            db_session.set_tenant_context(session, TENANT_A, WORKSPACE_A)
            return SalesCRMStore(session, TENANT_A, WORKSPACE_A).get(lead_id)

    first = crm_row("wa_pg_in_1", "+971502223333")
    assert importer.join_lead_list(webhook, first, "owner_test") == "created"
    [lead] = ok(client.get(f"{BASE}/leads", headers=A, params={"channel": "whatsapp"}))["items"]
    assert lead["sales_lead_id"] == "wa_pg_in_1" and lead["channel_conversation_id"] == "conv_wa_pg_in_1"
    assert importer.join_lead_list(webhook, first, "owner_test") == "unchanged"
    second = crm_row("wa_pg_in_2", "+971502223333")
    assert importer.join_lead_list(webhook, second, "owner_test") == "waiting"
    assert ok(client.get(f"{BASE}/leads", headers=A, params={"channel": "whatsapp"}))["total"] == 1
    assert ok(client.get(f"{BASE}/leads", headers=A2, params={"channel": "whatsapp"}))["total"] == 0
    figures = ok(client.get("/api/v1/sales/overview", headers=A))
    assert figures["leads"] == 1 and figures["waiting"] == 1 and figures["source"] == "business_leads"
    assert ok(client.get("/api/v1/sales/overview", headers=B))["leads"] == 0
    with db.begin() as connection:
        _scoped(connection, TENANT_A, WORKSPACE_A)
        assert connection.execute(text("select count(*) from sales_leads where id like 'wa_pg_in_%'")).scalar() == 2
