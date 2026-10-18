"""Business in PostgreSQL (migration ``0069_business``), as the non-owner app role.

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
    "business_milestones", "business_drafts", "business_research_runs",
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
