"""The prospecting pipeline and outbox in PostgreSQL (migration ``0076_business_pipeline``), as the app role.

All six tables are under forced RLS: tenant B and tenant A's other workspace see none of A's rows,
guessed ids read as missing, a row claiming another scope is refused by the row policy, updates and
deletes from another scope touch nothing, scoped foreign keys refuse cross-scope references, and the
unique indexes keep one target profile per product and one live outbox row per sequence step.
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
    "business_outreach_settings", "business_target_profiles", "business_observations", "business_pipeline_runs", "business_outbox", "business_replies",
)


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


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def _scoped(connection: Any, tenant_id: str, workspace_id: str) -> None:
    connection.execute(text(f"set local role {APP_ROLE}"))
    connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": tenant_id})
    connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": workspace_id})


def pipeline_rows(h: dict[str, str]) -> dict[str, Any]:
    """One row in every new table, through the API (plus an observation written in scope)."""
    product = ok(client.post(f"{BASE}/products", headers=h, json={"kind": "product", "name": "Tyre Pulse", "summary": "Fleet tyre management."}), 201)
    ok(client.put(f"{BASE}/pipeline/products/{product['id']}/target", headers=h, json={"industries": ["fleet"], "trigger_events": ["New fleet contract"]}))
    ok(client.put(f"{BASE}/pipeline/signals", headers=h, json={"decisions": {"FOUND_IN_SEARCH": "confirmed"}}))
    run = ok(client.post(f"{BASE}/pipeline/runs/estimate", headers=h, json={"kind": "discover", "product_id": product["id"], "terms": ["fleet"]}), 201)
    lead = ok(client.post(f"{BASE}/leads", headers=h, json={
        "title": "Gulf fleet", "product_id": product["id"], "account": {"name": "Gulf Fleet", "website": "https://gulf-fleet.example"},
        "contact": {"name": "Sara", "email": "ops@gulf-fleet.example", "consent_basis": "public_business_channel"},
    }), 201)
    lead_id = lead["lead"]["id"]
    source = ok(client.post(f"{BASE}/leads/{lead_id}/evidence", headers=h, json={"url": "https://gulf-fleet.example/fleet", "title": "Fleet"}), 201)["evidence"][0]["id"]
    ok(client.put(f"{BASE}/leads/{lead_id}/score", headers=h, json={"criteria": {
        "industry_fit": {"score": 4, "reason": "Fleet operator"}, "problem_evidence": {"score": 2, "reason": "Manual logs", "evidence_ids": [source]}}}))
    ok(client.post(f"{BASE}/leads/{lead_id}/transition", headers=h, json={"to": "qualified"}))
    draft = ok(client.post(f"{BASE}/leads/{lead_id}/drafts", headers=h, json={"channel": "email"}), 201)["draft"]
    ok(client.post(f"{BASE}/drafts/{draft['id']}/approve", headers=h))
    item = ok(client.post(f"{BASE}/outreach/drafts/{draft['id']}/queue", headers=h, json={}), 201)
    reply = ok(client.post(f"{BASE}/outreach/leads/{lead_id}/replies", headers=h, json={"channel": "email", "text": "Call me next week"}), 201)
    return {"product": product, "run": run, "lead": lead, "lead_id": lead_id, "evidence_id": source, "item": item, "reply": reply}


def test_every_new_table_forces_row_level_security(db: Engine) -> None:
    with db.connect() as connection:
        rows = connection.execute(text(
            "select relname, relrowsecurity, relforcerowsecurity from pg_class where relname = any(:names)"), {"names": list(TABLES)}).all()
        assert {row[0] for row in rows} == set(TABLES)
        assert all(row[1] and row[2] for row in rows), rows
        policies = {row[0] for row in connection.execute(text("select tablename from pg_policies where policyname like 'tenant_isolation_business_%'"))}
        assert set(TABLES) <= policies


def test_tenant_b_and_the_other_workspace_cannot_read_or_guess(db: Engine) -> None:
    rows = pipeline_rows(A)
    with db.connect() as connection:
        with connection.begin():
            _scoped(connection, TENANT_A, WORKSPACE_A)
            connection.execute(text(
                "insert into business_observations (id, tenant_id, workspace_id, lead_id, account_id, signal_code, value_text, url, excerpt, observed_on, created_by) "
                "values ('bobs_pg', :t, :w, :l, :a, 'TECH_STACK', 'WordPress', 'https://gulf-fleet.example/', 'wp-content', current_date, 'u')"),
                {"t": TENANT_A, "w": WORKSPACE_A, "l": rows["lead_id"], "a": rows["lead"]["account"]["id"]})
            for table in TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() >= 1, table
    for other in (A2, B):
        assert ok(client.get(f"{BASE}/pipeline/runs", headers=other))["total"] == 0
        assert client.get(f"{BASE}/pipeline/runs/{rows['run']['id']}", headers=other).status_code == 404
        assert ok(client.get(f"{BASE}/outreach/items", headers=other))["total"] == 0
        assert client.get(f"{BASE}/outreach/items/{rows['item']['id']}", headers=other).status_code == 404
        assert client.get(f"{BASE}/pipeline/leads/{rows['lead_id']}/profile", headers=other).status_code == 404
        assert client.post(f"{BASE}/outreach/leads/{rows['lead_id']}/replies", headers=other, json={"text": "x"}).status_code == 404
        assert client.put(f"{BASE}/pipeline/products/{rows['product']['id']}/target", headers=other, json={"industries": ["x"]}).status_code == 404
        assert ok(client.get(f"{BASE}/pipeline/targets", headers=other)) == []
        assert all(item["status"] == "proposed" for item in ok(client.get(f"{BASE}/pipeline/signals", headers=other)))
    with db.connect() as connection:
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))  # no scope at all
            for table in TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table
        for tenant, workspace in ((TENANT_B, WORKSPACE_B), (TENANT_A, WORKSPACE_A2)):
            with connection.begin():
                _scoped(connection, tenant, workspace)
                for table in TABLES:
                    assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table


def test_rls_refuses_writes_into_another_scope_and_cross_scope_references(db: Engine) -> None:
    rows = pipeline_rows(A)
    with db.connect() as connection:
        # A row claiming tenant B, written in tenant A's context: the row policy refuses it.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text(
                    "insert into business_pipeline_runs (id, tenant_id, workspace_id, kind, status, created_by) values ('bpip_x', :t, :w, 'discover', 'estimated', 'u')"),
                    {"t": TENANT_B, "w": WORKSPACE_B})
        # Tenant B pointing an outbox row at tenant A's lead, contact and draft: the scoped foreign keys refuse it.
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_B, WORKSPACE_B)
                connection.execute(text(
                    "insert into business_outbox (id, tenant_id, workspace_id, lead_id, contact_id, draft_id, channel, sequence_id, recipient, body, content_hash, status, created_by) "
                    "values ('bout_x', :t, :w, :l, :c, :d, 'email', 'bout_x', 'a@b.example', 'x', 'h', 'held', 'u')"),
                    {"t": TENANT_B, "w": WORKSPACE_B, "l": rows["lead_id"], "c": rows["item"]["contact_id"], "d": rows["item"]["draft_id"]})
        # Updates and deletes from tenant B touch nothing of tenant A.
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            assert connection.execute(text("update business_outbox set status = 'sent' where id = :id"), {"id": rows["item"]["id"]}).rowcount == 0
            assert connection.execute(text("update business_outreach_settings set sending_enabled = true")).rowcount == 0
            assert connection.execute(text("delete from business_replies where id = :id"), {"id": rows["reply"]["id"]}).rowcount == 0
            assert connection.execute(text("delete from business_target_profiles")).rowcount == 0
    item = ok(client.get(f"{BASE}/outreach/items/{rows['item']['id']}", headers=A))
    assert item["status"] == "cancelled"  # the reply stopped it; tenant B's update did nothing
    assert ok(client.get(f"{BASE}/pipeline/settings", headers=A))["sending_enabled"] is False


def test_unique_indexes_keep_one_profile_per_product_and_one_live_row_per_step(db: Engine) -> None:
    rows = pipeline_rows(A)
    with db.connect() as connection:
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text(
                    "insert into business_target_profiles (id, tenant_id, workspace_id, product_id) values ('btgt_dup', :t, :w, :p)"),
                    {"t": TENANT_A, "w": WORKSPACE_A, "p": rows["product"]["id"]})
        item = rows["item"]
        with connection.begin():
            _scoped(connection, TENANT_A, WORKSPACE_A)
            connection.execute(text("update business_outbox set status = 'held' where id = :id"), {"id": item["id"]})
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(text(
                    "insert into business_outbox (id, tenant_id, workspace_id, lead_id, contact_id, draft_id, channel, step, sequence_id, recipient, body, content_hash, status, created_by) "
                    "values ('bout_dup', :t, :w, :l, :c, :d, 'email', 1, :s, 'a@b.example', 'x', 'h', 'held', 'u')"),
                    {"t": TENANT_A, "w": WORKSPACE_A, "l": item["lead_id"], "c": item["contact_id"], "d": item["draft_id"], "s": item["sequence_id"]})
