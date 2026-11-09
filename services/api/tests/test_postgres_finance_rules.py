"""Rules, aliases, subcategories, suggestion decisions, rule batches and automation in PostgreSQL (migration
``0152_finance_rules_learning``, docs/finance.md#rules-and-learning).

The API runs as the non-owner ``anum_test_app`` role. The six tables have forced RLS with the tenant, owner and purge
policies; a rule and its rows are visible only to their owner; a retroactive apply reposts the plain journal entries
and undo puts them back; of two accepts of one suggestion one wins; "Delete my data" removes everything. SYNTHETIC data.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import date, timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.rules_store import open_rule_stores, rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store, open_finance_store
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("finance_rules", "finance_merchant_aliases", "finance_subcategories", "finance_suggestion_decisions", "finance_rule_batches",
          "finance_automation_settings")


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


def _rows(app_session: Callable[..., Iterator[Session]], user: str, sql: str) -> list:
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        return list(session.execute(text(sql)).all())


def _txn(api: TestClient, account_id: str, amount: str, description: str, days: int = 5) -> dict:
    made = api.post(f"{API}/transactions", headers=ME, json={"account_id": account_id, "posted_on": (date.today() - timedelta(days=days)).isoformat(),
                                                             "amount": amount, "description": description})
    assert made.status_code == 201, made.text
    return made.json()


def test_the_tables_have_forced_rls_owner_tenant_and_purge_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies
        column = connection.execute(text("select is_nullable from information_schema.columns where table_name = 'finance_transactions' "
                                         "and column_name = 'subcategory_id'")).scalar_one()
        assert column == "YES"


def test_rules_apply_undo_and_isolation(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "AED"}).json()
    rows = [_txn(client, bank["id"], "-40.00", f"ZEBRAMART TESTCITY {i}", days=5 + i) for i in range(2)]
    assert client.post(f"{API}/merchants/aliases", headers=ME, json={"alias": "زيبرا", "merchant": "Zebra Mart"}).status_code == 201
    assert client.post(f"{API}/subcategories", headers=ME, json={"parent": "groceries", "name": "Fruit"}).status_code == 201
    made = client.post(f"{API}/rules", headers=ME, json={"content": {"match": {"words": ["zebramart"]}, "category": "groceries"}}).json()
    approved = client.post(f"{API}/rules/{made['rule_id']}/approve", headers=ME, json={"version": 1, "payload_hash": made["payload_hash"]})
    assert approved.status_code == 200, approved.text
    preview = client.post(f"{API}/rules/{made['rule_id']}/preview", headers=ME).json()
    assert preview["changes"] == 2
    batch = client.post(f"{API}/rules/{made['rule_id']}/apply", headers=ME, json={"preview_hash": preview["preview_hash"]}).json()
    assert batch["changed"] == 2
    entries = client.get(f"{API}/ledger/entries", headers=ME).json()["items"]
    assert any("expense:groceries" in str(e) for e in entries)  # the plain journal entries were reposted
    assert client.post(f"{API}/rule-batches/{batch['id']}/undo", headers=ME).json()["restored"] == 2
    assert client.get(f"{API}/transactions/{rows[0]['id']}", headers=ME).json()["transaction_type"] == "uncategorized"
    client.put(f"{API}/automation", headers=ME, json={"notify_unresolved": True})
    waiting = next(s for s in client.get(f"{API}/suggestions", headers=ME).json()["items"] if s["kind"] == "uncategorized")
    assert client.post(f"{API}/suggestions/reject", headers=ME, json={"key": waiting["key"]}).status_code == 200
    for table in TABLES:
        assert len(_rows(app_session, "user_test", f"select id from {table}")) >= 1, table
        assert _rows(app_session, "colleague", f"select id from {table}") == []
    assert client.get(f"{API}/rules/{made['rule_id']}", headers=COLLEAGUE).status_code == 404
    with pytest.raises(DBAPIError):
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="colleague")) as session:
            session.execute(text("select set_config('anum.user_id', 'colleague', true)"))
            session.execute(text("insert into finance_merchant_aliases (id, tenant_id, workspace_id, owner_user_id, alias, alias_key, merchant, "
                                 "created_at, updated_at) values ('x', :t, :w, 'user_test', 'a', 'a', 'b', now(), now())"), {"t": TENANT_A, "w": WORKSPACE_A})
    ctx = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test")
    with open_finance_store(ctx) as store:
        from anum_api.privacy import finance_export

        exported = finance_export(store, ctx)
        assert len(exported["finance_rules"]) == 1 and len(exported["merchant_aliases"]) == 1 and len(exported["rule_batches"]) == 1
        store.delete_everything(ctx)
    with open_rule_stores(ctx) as (_finance, rules):
        assert rules.count_everything(ctx) == 0


def test_two_accepts_of_one_suggestion_one_wins(client: TestClient) -> None:
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "AED"}).json()
    for i in range(2):
        row = _txn(client, bank["id"], "-25.00", "POS 3381 ZEBRAMART TESTCITY", days=30 + i)
        client.patch(f"{API}/transactions/{row['id']}", headers=ME, json={"category": "groceries"})
    _txn(client, bank["id"], "-27.00", "POS 9902 ZEBRAMART TESTCITY", days=2)
    item = next(s for s in client.get(f"{API}/suggestions", headers=ME).json()["items"] if s["kind"] == "classification")
    results: list[int] = []
    barrier = threading.Barrier(2)

    def accept() -> None:
        barrier.wait()
        results.append(client.post(f"{API}/suggestions/accept", headers=ME, json={"key": item["key"], "payload_hash": item["payload_hash"]}).status_code)

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409]
    assert client.get(f"{API}/rule-batches", headers=ME).json()["total"] == 1
