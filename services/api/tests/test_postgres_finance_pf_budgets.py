"""Budgets, learned merchant categories, the wider category list and recurring bills in PostgreSQL (migration
``0185_pf_budgets``, docs/finance.md#budgets-and-planning, #learned-merchants, #recurring-payments).

The API runs as the non-owner ``anum_test_app`` role. Budgets and learned merchants live on the owner's forced-RLS
``finance_plans`` row and the recurring settings on ``finance_bills``: another person never reads or changes them, and a
subcategory can sit under a new category. SYNTHETIC data.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        for user_id, role in (("user_test", "owner"), ("colleague", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})
    for store in (finance_store, sms_memory_store, rules_memory_store):
        store.clear()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store, rules_memory_store):
        store.clear()


def test_the_new_columns_and_the_wider_check_exist(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        columns = {(r[0], r[1]): (r[2], r[3]) for r in connection.execute(text(
            "select table_name, column_name, is_nullable, column_default from information_schema.columns "
            "where (table_name = 'finance_plans' and column_name in ('budgets', 'category_mappings')) "
            "or (table_name = 'finance_bills' and column_name in ('merchant', 'recurring_kind', 'auto_link', 'usage'))"))}
        assert columns[("finance_plans", "budgets")][0] == "NO" and columns[("finance_plans", "category_mappings")][0] == "NO"
        assert columns[("finance_bills", "auto_link")][0] == "NO" and columns[("finance_bills", "merchant")][0] == "YES"
        check = connection.execute(text("select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_finance_subcategories_parent'")).scalar_one()
        assert "family_support" in check and "charity" in check
        forced = connection.execute(text("select relforcerowsecurity from pg_class where relname = 'finance_plans'")).scalar_one()
        assert forced


def test_budgets_mappings_and_recurring_settings_are_the_owners_only(client: TestClient) -> None:
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "AED"}).json()
    on = (date.today() - timedelta(days=1)).isoformat()
    first = client.post(f"{API}/transactions", headers=ME, json={"account_id": bank["id"], "posted_on": on, "amount": "-40.00",
                                                                 "description": "POS QUOKKA KITCHEN TESTCITY"}).json()
    assert client.patch(f"{API}/transactions/{first['id']}", headers=ME, json={"category": "dining", "learn_merchant": True}).status_code == 200
    nxt = client.post(f"{API}/transactions", headers=ME, json={"account_id": bank["id"], "posted_on": on, "amount": "-41.00",
                                                               "description": "POS QUOKKA KITCHEN TESTCITY"}).json()
    assert nxt["category"] == "dining" and nxt["type_reason"] == "learned_merchant"
    made = client.post(f"{API}/budgets/plans", headers=ME, json={"name": "Eating out", "currency": "AED", "amount": "500", "categories": ["dining"]})
    assert made.status_code == 201, made.text
    assert made.json()["spent"] == "81.00"
    sub = client.post(f"{API}/subcategories", headers=ME, json={"parent": "charity", "name": "Mosque fund"})
    assert sub.status_code == 201, sub.text
    bill = client.post(f"{API}/recurring/confirm", headers=ME, json={"name": "Sample stream", "amount": "30", "currency": "AED", "cadence": "monthly",
                                                                   "next_due_on": date.today().isoformat(), "category": "subscriptions",
                                                                   "merchant": "sample stream", "recurring_kind": "streaming", "auto_link": True,
                                                                   "usage": "rarely"})
    assert bill.status_code == 201, bill.text
    kept = client.get(f"{API}/recurring", headers=ME).json()["kept"]
    assert kept[0]["auto_link"] is True and kept[0]["usage"] == "rarely" and kept[0]["merchant"] == "sample stream"
    # The colleague in the same workspace sees and changes nothing of it.
    assert client.get(f"{API}/budgets/plans", headers=COLLEAGUE).status_code in (200, 403)
    for path in ("/budgets/plans", "/category-mappings", "/recurring"):
        seen = client.get(f"{API}{path}", headers=COLLEAGUE)
        if seen.status_code == 200:
            body = seen.json()
            assert body.get("budgets", []) == [] and body.get("items", []) == [] and body.get("kept", []) == []
    assert client.patch(f"{API}/recurring/bills/{bill.json()['id']}", headers=COLLEAGUE, json={"auto_link": False}).status_code in (403, 404)
    assert client.delete(f"{API}/budgets/plans/{made.json()['id']}", headers=COLLEAGUE).status_code in (403, 404)
    assert client.get(f"{API}/budgets/plans", headers=ME).json()["budgets"][0]["name"] == "Eating out"


def test_budget_rollover_terms_live_on_the_owners_row_only(client: TestClient, database_engine: Engine) -> None:
    """Migration ``0187_pf_budget_rollover``: ``finance_plans.budget_rollovers`` (not null, default ``[]``) holds each budget's
    terms period by period on the owner's forced-RLS row (docs/finance.md#budget-rollover)."""
    with database_engine.connect() as connection:
        nullable, default = connection.execute(text(
            "select is_nullable, column_default from information_schema.columns "
            "where table_name = 'finance_plans' and column_name = 'budget_rollovers'")).one()
        assert nullable == "NO" and "'[]'::jsonb" in default
    made = client.post(f"{API}/budgets/plans", headers=ME, json={"name": "Food", "currency": "AED", "amount": "900", "categories": ["groceries"],
                                                                 "rollover": "capped", "rollover_cap": "100", "overspend": "carry"})
    assert made.status_code == 201, made.text
    line = made.json()
    assert (line["rollover"], line["rollover_cap"], line["overspend"], line["available"]) == ("capped", "100.00", "carry", "900.00")
    changed = client.patch(f"{API}/budgets/plans/{line['id']}", headers=ME, json={"name": "Food", "currency": "AED", "amount": "950",
                                                                                  "categories": ["groceries"], "rollover": "surplus"})
    assert changed.status_code == 200 and changed.json()["rollover"] == "surplus" and changed.json()["rollover_cap"] is None
    with database_engine.connect() as connection:
        stored = connection.execute(text("select budget_rollovers from finance_plans where owner_user_id = 'user_test'")).scalar_one()
        assert len(stored) == 1 and stored[0]["rollover"] == "surplus" and stored[0]["amount"] == "950.00"
        assert stored[0]["effective_from"] == line["period_start"]
    # The colleague never reads or changes the owner's terms.
    assert client.patch(f"{API}/budgets/plans/{line['id']}", headers=COLLEAGUE,
                        json={"name": "Food", "currency": "AED", "amount": "1", "categories": ["groceries"], "rollover": "none"}).status_code in (403, 404)
    assert client.get(f"{API}/budgets/plans", headers=ME).json()["budgets"][0]["rollover"] == "surplus"
    assert client.delete(f"{API}/budgets/plans/{line['id']}", headers=ME).status_code == 204
    with database_engine.connect() as connection:
        assert connection.execute(text("select budget_rollovers from finance_plans where owner_user_id = 'user_test'")).scalar_one() == []
