"""Matching across currencies in PostgreSQL (migration ``0188_pf_fx_matching``), as the non-owner app role.

A USD installment paid from a SAR account: the link keeps the bank side in SAR (the amount check of ``0134`` still
holds), settles the installment in USD with the bank's figures, and another person in the same workspace reads none of
it. The new columns refuse a settled amount in the payment's own currency or without its currency. docs/finance-matching.md.
Every row is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.models import FinanceSettings
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.summary import today_in
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
OTHER = {**ME, "x-user-id": "colleague", "x-user-roles": "owner"}


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                "('colleague', :t, :w, 'owner', true) on conflict do nothing"), {"t": TENANT_A, "w": WORKSPACE_A})
    for store in (finance_store, sms_memory_store, links_memory_store, rules_memory_store):
        store.clear()
    svc._last_notified.clear()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store, links_memory_store, rules_memory_store):
        store.clear()


def test_the_new_columns_and_their_checks(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        columns = set(connection.execute(text("select table_name || '.' || column_name from information_schema.columns "
                                              "where table_name in ('finance_links', 'finance_match_records')")).scalars())
        assert {"finance_links.settled_amount", "finance_links.settled_currency", "finance_links.fx", "finance_match_records.fx"} <= columns
        checks = set(connection.execute(text("select conname from pg_constraint where conrelid = 'finance_links'::regclass")).scalars())
        assert {"ck_finance_links_settled_pair", "ck_finance_links_settled_currency", "ck_finance_links_settled_amount"} <= checks


def test_a_usd_installment_paid_from_a_sar_account_settles_in_usd_and_stays_the_owners(client: TestClient, database_engine: Engine) -> None:
    day = today_in(FinanceSettings())
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR", "last4": "5678"}).json()
    assert client.post(f"{API}/sms/self-test", headers=ME).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=ME).json()["version"]
    assert client.put(f"{API}/sms/settings", headers=ME, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": "TestBank", "bank": "test"}], "verified_senders": ["TestBank"],
        "account_map": [{"last4": "5678", "account_id": bank["id"]}]}).status_code == 200
    token = client.post(f"{API}/sms/devices", headers=ME, json={"name": "iPhone"}).json()["token"]
    entry = {"provider": "Tamara", "order_id": "ORD10001", "merchant": "Synthetic Shop", "due_on": day.isoformat(), "installment": 2,
             "total_installments": 4, "amount": "100", "currency": "USD", "source_status": "scheduled"}
    assert client.post(f"{API}/installment-statement/sync", headers=ME, json={"entries": [entry]}).status_code == 200
    bill = client.post(f"{API}/bills", headers=ME, json={"name": "Tamara: Synthetic Shop 2/4", "amount": "100", "currency": "USD", "cadence": "once",
                                                         "next_due_on": day.isoformat(), "notes": "Order: ORD10001 | Installment: 2"}).json()
    policy = client.put(f"{API}/automation/matching", headers=ME, json={
        "match_transactions": True, "reconcile_installments": True, "source_account_ids": [bank["id"]], "approve": True})
    assert policy.status_code == 200, policy.text
    sms = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"},
                      json={"message": "SYNTHETIC. Purchase USD 100.00 (SAR 375.00) at TAMARA card **5678", "sender": "TestBank"})
    assert sms.status_code == 200 and sms.json()["status"] == "posted", sms.text
    made = client.get(f"{API}/matching/records", headers=ME).json()
    assert made["total"] == 1 and made["items"][0]["outcome"] == "auto_linked" and made["items"][0]["fx"]["original_currency"] == "USD"
    rows = client.get(f"{API}/bill-schedule?from={(day - timedelta(days=5)).isoformat()}&to={(day + timedelta(days=5)).isoformat()}", headers=ME).json()
    row = next(r for r in rows["items"] if r["bill_id"] == bill["id"])
    assert row["status"] == "paid" and Decimal(row["paid_amount"]) == Decimal("100")
    with database_engine.connect() as connection:
        link = connection.execute(text("select amount, currency, settled_amount, settled_currency, fx->>'bank_currency' from finance_links "
                                       "where status <> 'void'")).one()
        assert (link[0], link[1], link[2], link[3], link[4]) == (Decimal("375.0000"), "SAR", Decimal("100.0000"), "USD", "SAR")
        canonical = connection.execute(text("select canonical_id from finance_links where status <> 'void'")).scalar_one()
    # The colleague in the same workspace sees nothing of it.
    assert client.get(f"{API}/matching/records", headers=OTHER).json()["total"] == 0
    assert client.get(f"{API}/links", headers=OTHER).json()["total"] == 0
    # A settled amount in the payment's own currency, or without its currency, is refused by the table itself.
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'user_test', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        for settled, currency in (("100", "SAR"), ("100", None), ("0", "USD")):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text(
                    "insert into finance_links (id, tenant_id, workspace_id, owner_user_id, canonical_id, dimension, role, target_type, target_id, "
                    "amount, currency, settled_amount, settled_currency, status, source_event_ids, idem_key, created_by, created_at, updated_at, version) "
                    "values (:i, :t, :w, 'user_test', :c, 'bill', 'payment', 'finance_bill_due', 'fin_bill_x:2026-11-15', 1, 'SAR', :s, :sc, 'active', "
                    "'[]'::jsonb, :k, 'user_test', now(), now(), 1)"),
                    {"i": f"fin_link_bad_{currency}_{settled}", "t": TENANT_A, "w": WORKSPACE_A, "c": canonical, "s": settled, "sc": currency,
                     "k": f"bad{currency}{settled}".ljust(64, "0")})
