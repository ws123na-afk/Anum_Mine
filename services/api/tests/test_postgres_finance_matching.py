"""Matching decisions and history runs in PostgreSQL (migration ``0184_pf_matching``), as the non-owner app role.

Both tables have forced RLS with the tenant, owner and purge policies; a pair has one record; one history run runs per
person; a Tamara installment debit is linked through the API and a backfill rerun creates nothing new; another person
never reads or changes the records or runs. docs/finance-matching.md. Every row is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

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
TABLES = ("finance_match_records", "finance_backfill_runs")


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


def test_the_tables_have_forced_rls_and_the_notice_check_takes_match_review(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies
        check = connection.execute(text("select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_finance_notice_claims_kind'")).scalar_one()
        assert "match_review" in check and "uncategorized_money" in check
        unique = connection.execute(text("select indexdef from pg_indexes where indexname = 'uq_finance_backfill_runs_running'")).scalar_one()
        assert "WHERE" in unique and "running" in unique


def _as(connection, user: str) -> None:  # type: ignore[no-untyped-def]
    connection.execute(text(f"set local role {APP_ROLE}"))
    connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), set_config('anum.user_id', :u, true)"),
                       {"t": TENANT_A, "w": WORKSPACE_A, "u": user})


def _insert_run(connection, user: str, run_id: str) -> None:  # type: ignore[no-untyped-def]
    connection.execute(text("insert into finance_backfill_runs (id, tenant_id, workspace_id, owner_user_id, mode, status, policy_version, created_by, "
                            "started_at, updated_at) values (:i, :t, :w, :u, 'preview', 'running', 'pfm-1.r0', :u, now(), now())"),
                       {"i": run_id, "t": TENANT_A, "w": WORKSPACE_A, "u": user})


def test_one_running_run_per_person_and_nobody_reads_anothers(database_engine: Engine, seed_scopes: None) -> None:
    with database_engine.connect() as connection, connection.begin():
        _as(connection, "user_test")
        _insert_run(connection, "user_test", "fin_backfill_1")
        with pytest.raises(IntegrityError), connection.begin_nested():
            _insert_run(connection, "user_test", "fin_backfill_2")
        # Writing a row for someone else is refused by the owner policy.
        with pytest.raises(Exception), connection.begin_nested():  # noqa: B017 - the policy's own error class
            _insert_run(connection, "colleague", "fin_backfill_3")
    with database_engine.connect() as connection, connection.begin():
        _as(connection, "colleague")
        assert connection.execute(text("select count(*) from finance_backfill_runs")).scalar_one() == 0
        _insert_run(connection, "colleague", "fin_backfill_4")  # their own running run is allowed alongside


def test_a_tamara_debit_links_once_and_another_person_sees_none_of_it(client: TestClient, database_engine: Engine) -> None:
    day = today_in(FinanceSettings())
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR", "last4": "5678"}).json()
    assert client.post(f"{API}/sms/self-test", headers=ME).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=ME).json()["version"]
    assert client.put(f"{API}/sms/settings", headers=ME, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": "TestBank", "bank": "test"}], "verified_senders": ["TestBank"],
        "account_map": [{"last4": "5678", "account_id": bank["id"]}]}).status_code == 200
    token = client.post(f"{API}/sms/devices", headers=ME, json={"name": "iPhone"}).json()["token"]
    entry = {"provider": "Tamara", "order_id": "ORD10001", "merchant": "Synthetic Shop", "due_on": day.isoformat(), "installment": 2,
             "total_installments": 4, "amount": "125", "currency": "SAR", "source_status": "scheduled"}
    assert client.post(f"{API}/installment-statement/sync", headers=ME, json={"entries": [entry]}).status_code == 200
    bill = client.post(f"{API}/bills", headers=ME, json={"name": "Tamara: Synthetic Shop 2/4", "amount": "125", "currency": "SAR", "cadence": "once",
                                                         "next_due_on": day.isoformat(), "notes": "Order: ORD10001 | Installment: 2"}).json()
    sms = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"},
                      json={"message": "SYNTHETIC. Purchase of SAR 125.00 at TAMARA with card **5678", "sender": "TestBank"})
    assert sms.status_code == 200 and sms.json()["status"] == "posted", sms.text
    # Policy on after the debit: the history run finds it.
    policy = client.put(f"{API}/automation/matching", headers=ME, json={
        "match_transactions": True, "reconcile_installments": True, "reprocess_history": True, "source_account_ids": [bank["id"]], "approve": True})
    assert policy.status_code == 200, policy.text
    for _ in range(2):
        run = client.post(f"{API}/matching/backfill", headers=ME, json={"mode": "apply"})
        assert run.status_code == 201, run.text
        current = run.json()
        while current["status"] == "running":
            current = client.post(f"{API}/matching/backfill/{current['id']}/continue", headers=ME).json()
        assert current["status"] == "done"
    rows = client.get(f"{API}/bill-schedule?from={(day - timedelta(days=5)).isoformat()}&to={(day + timedelta(days=5)).isoformat()}", headers=ME).json()
    assert next(r["status"] for r in rows["items"] if r["bill_id"] == bill["id"]) == "paid"
    made = client.get(f"{API}/matching/records", headers=ME).json()
    assert made["total"] == 1 and made["items"][0]["outcome"] == "auto_linked"
    with database_engine.connect() as connection:
        links = connection.execute(text("select count(*) from finance_links where status <> 'void'")).scalar_one()
        assert links == 1  # the second apply created nothing
    # The colleague, in the same workspace, reads and changes nothing of it.
    assert client.get(f"{API}/matching/records", headers=OTHER).json()["total"] == 0
    assert client.get(f"{API}/matching/backfill", headers=OTHER).json()["total"] == 0
    assert client.post(f"{API}/matching/records/{made['items'][0]['id']}/undo", headers=OTHER, json={}).status_code == 404
    undone = client.post(f"{API}/matching/records/{made['items'][0]['id']}/undo", headers=ME, json={})
    assert undone.status_code == 200 and undone.json()["outcome"] == "undone"
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from finance_links where status <> 'void'")).scalar_one() == 0
        assert connection.execute(text("select count(*) from finance_journal_entries")).scalar_one() >= 1
