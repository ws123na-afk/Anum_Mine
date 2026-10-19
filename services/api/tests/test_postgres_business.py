"""Business in PostgreSQL (migrations ``0070_business``, ``0080_business_change_orders`` and ``0087_business_invoices_portal``), as
the non-owner app role.

Every table is under forced RLS: tenant A's rows are invisible to tenant B and to A's other
workspace, guessed ids read as missing, writes into another scope are refused by the row
policy, references between business tables cannot point into another scope (composite
foreign keys), duplicates are refused by unique indexes, and an accepted proposal version
cannot be changed even by a direct UPDATE. The main path (product, lead, qualify, draft,
proposal accepted through an approval, won, project) runs end to end.
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
TABLES = (
    "business_settings", "business_products", "business_offers", "business_accounts", "business_contacts", "business_leads", "business_evidence",
    "business_inferences", "business_activities", "business_opportunities", "business_proposals", "business_proposal_versions", "business_projects",
    "business_milestones", "business_drafts", "business_research_runs", "business_change_orders", "business_usage_ledger",
    "business_invoices", "business_invoice_payments", "business_invoice_counters", "business_portal_access", "business_portal_decisions",
)


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user: str = "owner_test", roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": roles}


A, A2, B = headers(), headers(workspace_id=WORKSPACE_A2), headers(TENANT_B, WORKSPACE_B)
MEMBER_A = headers(user="member_test", roles="member")
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


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def _scoped(connection: Any, tenant_id: str, workspace_id: str) -> None:
    connection.execute(text(f"set local role {APP_ROLE}"))
    connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": tenant_id})
    connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": workspace_id})


def main_path(h: dict[str, str]) -> dict[str, Any]:
    product = ok(client.post(f"{BASE}/products", headers=h, json={"kind": "service_package", "name": "Website Starter", "maturity": "pilot"}), 201)
    offer = ok(client.post(f"{BASE}/products/{product['id']}/versions", headers=h, json={"price_type": "fixed", "price_min": 1800, "deliverables": ["Five pages"],
                                                                                      "cost": {"labor_hours": 10, "hourly_cost": 60}}), 201)["offers"][0]
    ok(client.post(f"{BASE}/offers/{offer['id']}/activate", headers=h))
    lead = ok(client.post(f"{BASE}/leads", headers=h, json={
        "title": "Bakery website", "product_id": product["id"], "account": {"name": "Sunrise Bakery", "website": "https://sunrise-bakery.example", "city": "Sharjah"},
        "contact": {"name": "Huda", "email": "huda@sunrise-bakery.example", "consent_basis": "inbound_inquiry"},
    }), 201)
    lead_id = lead["lead"]["id"]
    source = ok(client.post(f"{BASE}/leads/{lead_id}/evidence", headers=h, json={"url": "https://sunrise-bakery.example/contact", "title": "Contact page"}), 201)["evidence"][0]["id"]
    ok(client.put(f"{BASE}/leads/{lead_id}/score", headers=h, json={"criteria": {
        "industry_fit": {"score": 3, "reason": "Local retail"}, "problem_evidence": {"score": 4, "reason": "No online menu", "evidence_ids": [source]}}}))
    ok(client.post(f"{BASE}/leads/{lead_id}/transition", headers=h, json={"to": "qualified"}))
    draft = ok(client.post(f"{BASE}/leads/{lead_id}/drafts", headers=h, json={"channel": "email"}), 201)["draft"]
    opportunity = ok(client.post(f"{BASE}/opportunities", headers=h, json={"lead_id": lead_id, "value_amount": 1800, "probability_pct": 50}), 201)
    proposal = ok(client.post(f"{BASE}/proposals", headers=h, json={"opportunity_id": opportunity["id"], "title": "Bakery website", "content": {
        "currency": "AED", "deliverables": ["Five pages", "Contact form"], "acceptance_criteria": ["Pages live on the client's domain"],
        "line_items": [{"description": "Website Starter", "unit_price": 1800}], "milestones": [{"title": "Deposit", "amount": 900}, {"title": "Launch", "amount": 900}],
    }}), 201)
    return {"product": product, "lead": lead, "lead_id": lead_id, "evidence_id": source, "draft": draft, "opportunity": opportunity, "proposal": proposal}


def test_main_path_end_to_end_under_rls(db: Engine) -> None:
    path = main_path(A)
    pending = ok(client.post(f"{BASE}/proposals/{path['proposal']['id']}/request-acceptance", headers=MEMBER_A))
    approval = pending["approval"]
    assert ok(client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]}))
    accepted = ok(client.get(f"{BASE}/proposals/{path['proposal']['id']}", headers=A))
    assert accepted["status"] == "accepted" and accepted["accepted_version"] == 1
    assert ok(client.get(f"{BASE}/opportunities/{path['opportunity']['id']}", headers=A))["stage"] == "won"
    assert ok(client.get(f"{BASE}/leads/{path['lead_id']}", headers=A))["lead"]["state"] == "won"
    project = ok(client.post(f"{BASE}/projects", headers=A, json={"proposal_id": path["proposal"]["id"], "start_on": "2026-11-01"}), 201)
    assert project["contract_total"] == 1800.0 and len(project["milestones"]) == 2
    ok(client.patch(f"{BASE}/milestones/{project['milestones'][0]['id']}", headers=A, json={"status": "accepted", "paid_amount": 900}))
    kpi = ok(client.get(f"{BASE}/kpi", headers=A))
    assert kpi["leads_created"] == 1 and kpi["contracted"] == [{"currency": "AED", "amount": 1800.0}] and kpi["cash_collected"] == [{"currency": "AED", "amount": 900.0}]
    # The accepted version is immutable in the database too.
    with db.connect() as connection:
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text("update business_proposal_versions set summary = 'changed' where proposal_id = :p"), {"p": path["proposal"]["id"]})


def test_tenant_b_and_the_other_workspace_cannot_read_or_guess(db: Engine) -> None:
    path = main_path(A)
    ids = {
        "leads": path["lead_id"], "accounts": path["lead"]["account"]["id"], "products": path["product"]["id"],
        "opportunities": path["opportunity"]["id"], "proposals": path["proposal"]["id"],
    }
    for other in (A2, B):
        assert ok(client.get(f"{BASE}/leads", headers=other))["total"] == 0
        assert ok(client.get(f"{BASE}/products", headers=other))["total"] == 0
        assert ok(client.get(f"{BASE}/accounts", headers=other))["total"] == 0
        assert ok(client.get(f"{BASE}/opportunities", headers=other))["total"] == 0
        assert ok(client.get(f"{BASE}/proposals", headers=other))["total"] == 0
        for kind, item_id in ids.items():
            assert client.get(f"{BASE}/{kind}/{item_id}", headers=other).status_code == 404, kind
        assert client.patch(f"{BASE}/contacts/{path['lead']['contact']['id']}", headers=other, json={"name": "x"}).status_code == 404
        assert client.delete(f"{BASE}/evidence/{path['evidence_id']}", headers=other).status_code == 404
        assert client.patch(f"{BASE}/drafts/{path['draft']['id']}", headers=other, json={"body": "x"}).status_code == 404
        assert client.post(f"{BASE}/leads", headers=other, json={"title": "Steal it", "account_id": ids["accounts"]}).status_code == 404
        assert client.post(f"{BASE}/opportunities", headers=other, json={"lead_id": ids["leads"]}).status_code == 404
        assert client.post(f"{BASE}/projects", headers=other, json={"proposal_id": ids["proposals"]}).status_code == 404
        assert ok(client.get(f"{BASE}/kpi", headers=other))["leads_created"] == 0
        assert ok(client.get(f"{BASE}/duplicates", headers=other, params={"website": "https://sunrise-bakery.example"})) == []
    # Without a scope, the app role sees nothing in any business table.
    with db.connect() as connection:
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            for table in TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            for table in TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table


def test_rls_refuses_writes_into_another_scope_and_cross_scope_references(db: Engine) -> None:
    path = main_path(A)
    with db.connect() as connection:
        # A row claiming tenant B, written in tenant A's context: the row policy refuses it.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text(
                    "insert into business_accounts (id, tenant_id, workspace_id, name, name_key, source, owner_id, created_by) "
                    "values ('bacct_x', :t, :w, 'X', 'x', 'manual', 'u', 'u')"), {"t": TENANT_B, "w": WORKSPACE_B})
        # Tenant B referencing tenant A's account by its (guessed) id: the scoped foreign key refuses it.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_B, WORKSPACE_B)
                connection.execute(text(
                    "insert into business_leads (id, tenant_id, workspace_id, account_id, title, state, owner_id, source, created_by) "
                    "values ('blead_x', :t, :w, :a, 'Steal', 'discovered', 'u', 'manual', 'u')"),
                    {"t": TENANT_B, "w": WORKSPACE_B, "a": path["lead"]["account"]["id"]})
        # Updates from tenant B touch nothing of tenant A.
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            changed = connection.execute(text("update business_leads set title = 'pwned' where id = :id"), {"id": path["lead_id"]})
            assert changed.rowcount == 0
            deleted = connection.execute(text("delete from business_evidence where id = :id"), {"id": path["evidence_id"]})
            assert deleted.rowcount == 0
    assert ok(client.get(f"{BASE}/leads/{path['lead_id']}", headers=A))["lead"]["title"] == "Bakery website"


def test_unique_indexes_back_duplicate_detection(db: Engine) -> None:
    main_path(A)
    # The same website in another tenant is fine; in the same workspace it is refused.
    ok(client.post(f"{BASE}/accounts", headers=B, json={"name": "Sunrise Bakery", "website": "https://sunrise-bakery.example"}), 201)
    assert client.post(f"{BASE}/accounts", headers=A, json={"name": "Sunrise", "website": "https://www.sunrise-bakery.example"}).status_code == 409
    with db.connect() as connection:
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text(
                    "insert into business_accounts (id, tenant_id, workspace_id, name, name_key, domain, source, owner_id, created_by) "
                    "values ('bacct_dup', :t, :w, 'Dup', 'dup', 'sunrise-bakery.example', 'manual', 'u', 'u')"), {"t": TENANT_A, "w": WORKSPACE_A})


def test_settings_and_activities_are_scoped(db: Engine) -> None:
    ok(client.put(f"{BASE}/settings", headers=A, json={"research_runs_per_day": 3, "research_searches_per_day": 9}))
    assert ok(client.get(f"{BASE}/setup", headers=A))["settings"]["research_runs_per_day"] == 3
    assert ok(client.get(f"{BASE}/setup", headers=B))["settings"]["research_runs_per_day"] == 10
    lead_id = main_path(A)["lead_id"]
    key = {"idempotency-key": "visit-1"}
    first = client.post(f"{BASE}/leads/{lead_id}/activities", headers={**A, **key}, json={"kind": "meeting_logged", "summary": "Visited the shop"})
    again = client.post(f"{BASE}/leads/{lead_id}/activities", headers={**A, **key}, json={"kind": "meeting_logged", "summary": "Visited the shop"})
    assert first.status_code == 201 and again.status_code == 200 and first.json()["id"] == again.json()["id"]
    assert client.post(f"{BASE}/leads/{lead_id}/activities", headers={**B, **key}, json={"kind": "note", "summary": "x y"}).status_code == 404


SUPPORT_TABLES = ("business_support_tickets", "business_ticket_replies")


def test_support_tickets_under_rls(db: Engine) -> None:
    """Migration ``0076_business_support``: tickets, their trail and the policy stay in their scope."""
    path = main_path(A)
    account_id = path["lead"]["account"]["id"]
    contact_id = path["lead"]["contact"]["id"]
    policy = ok(client.get(f"{BASE}/support/setup", headers=A))["policy"]
    ok(client.put(f"{BASE}/support/policy", headers=A, json={**policy, "start_hour": 8}))
    assert ok(client.get(f"{BASE}/support/setup", headers=A))["policy"]["start_hour"] == 8
    assert ok(client.get(f"{BASE}/support/setup", headers=B))["policy"]["start_hour"] == 9
    created = ok(client.post(f"{BASE}/support/tickets", headers=MEMBER_A, json={
        "title": "Contact form sends nothing", "severity": "high", "requester_email": "huda@sunrise-bakery.example"}), 201)
    assert created["contact_id"] == contact_id and created["account_id"] == account_id and created["requester_verified"]
    ticket_id = created["id"]
    ok(client.post(f"{BASE}/support/tickets/{ticket_id}/status", headers=MEMBER_A, json={"to": "waiting_on_customer"}))
    resolved = ok(client.post(f"{BASE}/support/tickets/{ticket_id}/status", headers=MEMBER_A, json={
        "to": "resolved", "resolution_code": "fixed", "resolution_summary": "The form's mail relay was restored and tested."}))
    assert resolved["sla_paused_at"] is None and len(resolved["activities"]) >= 3
    for other in (A2, B):
        assert ok(client.get(f"{BASE}/support/tickets", headers=other))["total"] == 0
        assert client.get(f"{BASE}/support/tickets/{ticket_id}", headers=other).status_code == 404
        assert client.post(f"{BASE}/support/tickets", headers=other, json={"title": "Steal", "account_id": account_id}).status_code == 404
    with db.connect() as connection:
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            for table in SUPPORT_TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table
        # Tenant B pointing a ticket at tenant A's account by its id: the scoped foreign key refuses it.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_B, WORKSPACE_B)
                connection.execute(text(
                    "insert into business_support_tickets (id, tenant_id, workspace_id, title, severity, status, reported_at, created_by, account_id) "
                    "values ('btkt_x', :t, :w, 'Steal', 'low', 'new', now(), 'u', :a)"), {"t": TENANT_B, "w": WORKSPACE_B, "a": account_id})
        # A resolved ticket always carries its resolution audit.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text("update business_support_tickets set resolution_code = null where id = :id"), {"id": ticket_id})
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            assert connection.execute(text("update business_support_tickets set title = 'pwned' where id = :id"), {"id": ticket_id}).rowcount == 0
        # One open ticket per linked message (the partial unique index).
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                for number in (1, 2):
                    connection.execute(text(
                        "insert into business_support_tickets (id, tenant_id, workspace_id, title, severity, status, reported_at, created_by, source, source_ref) "
                        f"values ('btkt_dup{number}', :t, :w, 'Dup', 'low', 'new', now(), 'u', 'email', 'emsg_same')"), {"t": TENANT_A, "w": WORKSPACE_A})



def test_change_orders_and_the_ledger_under_rls(db: Engine) -> None:
    """An approved change order adds a locked version (version 1 stays immutable), and the
    change orders and ledger rows of tenant A are invisible to tenant B and A's other workspace."""
    path = main_path(A)
    pending = ok(client.post(f"{BASE}/proposals/{path['proposal']['id']}/request-acceptance", headers=MEMBER_A))
    assert ok(client.post(f"/api/v1/approvals/{pending['approval']['id']}/approve", headers=A, json={"payload_hash": pending["approval"]["payload_hash"]}))
    project = ok(client.post(f"{BASE}/projects", headers=A, json={"proposal_id": path["proposal"]["id"]}), 201)
    change = ok(client.post(f"{BASE}/projects/{project['id']}/change-orders", headers=MEMBER_A, json={
        "title": "Menu page", "lines": [{"description": "Menu page", "unit_price": 450}], "timeline_days": 7}), 201)
    sent = ok(client.post(f"{BASE}/change-orders/{change['id']}/submit", headers=MEMBER_A))
    assert client.post(f"{BASE}/change-orders/{change['id']}/approve", headers=MEMBER_A, json={"content_hash": sent["content_hash"]}).status_code == 403
    approved = ok(client.post(f"{BASE}/change-orders/{change['id']}/approve", headers=A, json={"content_hash": sent["content_hash"]}))
    assert approved["status"] == "approved" and approved["result_version"] == 2
    assert ok(client.get(f"{BASE}/projects/{project['id']}", headers=A))["contract_total"] == 2250.0
    entry = ok(client.post(f"{BASE}/usage", headers=A, json={"project_id": project["id"], "kind": "labour", "description": "Build pages",
                                                               "occurred_on": "2026-11-02", "quantity": 4, "unit_cost": 60, "currency": "AED"}), 201)
    money = ok(client.get(f"{BASE}/projects/{project['id']}/profitability", headers=A))
    assert money["revenue"] == 2250.0 and money["labour_cost"] == 240.0 and money["gross_margin"] == 2010.0 and money["reconciled"]
    for other in (A2, B):
        assert client.get(f"{BASE}/change-orders/{change['id']}", headers=other).status_code == 404
        assert client.patch(f"{BASE}/usage/{entry['id']}", headers=other, json={"billable": True}).status_code == 404
        assert ok(client.get(f"{BASE}/usage", headers=other))["total"] == 0
    with db.connect() as connection:
        # Both the accepted and the amended version refuse a direct update.
        for version in (1, 2):
            with pytest.raises(DBAPIError):
                with connection.begin():
                    _scoped(connection, TENANT_A, WORKSPACE_A)
                    connection.execute(text("update business_proposal_versions set summary = 'changed' where proposal_id = :p and version = :v"),
                                       {"p": path["proposal"]["id"], "v": version})
        # Tenant B cannot point a ledger row at tenant A's project (scoped foreign key).
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_B, WORKSPACE_B)
                connection.execute(text(
                    "insert into business_usage_ledger (id, tenant_id, workspace_id, account_id, project_id, kind, description, occurred_on, currency, source, created_by) "
                    "values ('busg_x', :t, :w, :a, :p, 'other', 'Steal', '2026-11-01', 'AED', 'manual', 'u')"),
                    {"t": TENANT_B, "w": WORKSPACE_B, "a": project["account_id"], "p": project["id"]})


def test_invoices_and_the_customer_portal_under_rls(db: Engine) -> None:
    """An invoice is numbered and sent through an owner's approval; a sent invoice cannot be rewritten
    even by a direct UPDATE; a portal link only ever finds its own tenant, workspace and account."""
    path = main_path(A)
    pending = ok(client.post(f"{BASE}/proposals/{path['proposal']['id']}/request-acceptance", headers=MEMBER_A))
    assert ok(client.post(f"/api/v1/approvals/{pending['approval']['id']}/approve", headers=A, json={"payload_hash": pending["approval"]["payload_hash"]}))
    project = ok(client.post(f"{BASE}/projects", headers=A, json={"proposal_id": path["proposal"]["id"]}), 201)
    ok(client.put(f"{BASE}/billing-profile", headers=A, json={"legal_name": "Anum Technologies LLC", "tax_number": "300000000000003", "invoice_prefix": "INV"}))
    milestones = [item["id"] for item in project["milestones"]]
    draft = ok(client.post(f"{BASE}/invoices", headers=MEMBER_A, json={"project_id": project["id"], "milestone_ids": milestones[:1]}), 201)
    waiting = ok(client.post(f"{BASE}/invoices/{draft['id']}/request-approval", headers=MEMBER_A))
    assert ok(client.post(f"/api/v1/approvals/{waiting['approval']['id']}/approve", headers=A, json={"payload_hash": waiting["approval"]["payload_hash"]}))
    sent = ok(client.get(f"{BASE}/invoices/{draft['id']}", headers=A))
    assert sent["status"] == "sent" and sent["number"].startswith("INV-") and sent["number"].endswith("-0001") and sent["total"] == 900.0
    paid = ok(client.post(f"{BASE}/invoices/{draft['id']}/payments", headers=MEMBER_A, json={"amount": 900, "paid_on": sent["issue_date"]}), 201)
    assert paid["status"] == "paid" and ok(client.get(f"{BASE}/projects/{project['id']}", headers=A))["milestones"][0]["paid_amount"] == 900.0
    second = ok(client.post(f"{BASE}/invoices", headers=MEMBER_A, json={"project_id": project["id"], "milestone_ids": milestones[1:]}), 201)
    again = ok(client.post(f"{BASE}/invoices/{second['id']}/request-approval", headers=MEMBER_A))
    assert ok(client.post(f"/api/v1/approvals/{again['approval']['id']}/approve", headers=A, json={"payload_hash": again["approval"]["payload_hash"]}))
    assert ok(client.get(f"{BASE}/invoices/{second['id']}", headers=A))["number"].endswith("-0002")
    for other in (A2, B):
        assert client.get(f"{BASE}/invoices/{draft['id']}", headers=other).status_code == 404
        assert ok(client.get(f"{BASE}/invoices", headers=other))["total"] == 0
    # The portal: a link for the client's contact.
    contact = ok(client.get(f"{BASE}/accounts/{project['account_id']}", headers=A))["contacts"][0]
    access = ok(client.post(f"{BASE}/portal/access", headers=A, json={"account_id": project["account_id"], "contact_id": contact["id"]}), 201)
    token = access["token"]
    portal = {"authorization": f"Bearer {token}"}
    me = ok(client.get("/api/v1/portal/me", headers=portal))
    assert me["account_name"] == "Sunrise Bakery" and me["counts"]["invoices_open"] == 1
    assert {item["number"] for item in ok(client.get("/api/v1/portal/invoices", headers=portal))} == {sent["number"], ok(client.get(f"{BASE}/invoices/{second['id']}", headers=A))["number"]}
    parts = token.split(".")
    for tenant, workspace in ((TENANT_B, WORKSPACE_B), (TENANT_A, WORKSPACE_A2)):
        forged = ".".join([parts[0], tenant, workspace, parts[3], parts[4]])
        assert client.get("/api/v1/portal/me", headers={"authorization": f"Bearer {forged}"}).status_code == 401
    ok(client.post(f"{BASE}/portal/access/{access['id']}/revoke", headers=A))
    assert client.get("/api/v1/portal/me", headers=portal).status_code == 401
    with db.connect() as connection:
        # A sent invoice cannot be rewritten by a direct UPDATE, and a void one stays void.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text("update business_invoices set bill_to_name = 'Someone else' where id = :i"), {"i": draft["id"]})
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text("update business_invoices set total = 1, subtotal = 1, tax = 0, amount_paid = 0 where id = :i"), {"i": second["id"]})
        # Numbers are unique per workspace.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text("update business_invoices set number = :n where id = :i"), {"n": sent["number"], "i": second["id"]})
        # Tenant B cannot invoice tenant A's account (scoped foreign key) or read the link hashes.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_B, WORKSPACE_B)
                connection.execute(text("insert into business_invoices (id, tenant_id, workspace_id, account_id, status, currency, created_by) values ('binv_x', :t, :w, :a, 'draft', 'AED', 'u')"),
                                   {"t": TENANT_B, "w": WORKSPACE_B, "a": project["account_id"]})
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            assert connection.execute(text("select count(*) from business_portal_access")).scalar() == 0
