"""Lent and borrowed, payslips and remittances in PostgreSQL (migration ``0147_finance_personal_money``,
docs/finance.md#lent-and-borrowed).

The API runs as the non-owner ``anum_test_app`` role. The three tables have forced RLS with the tenant, owner
and purge policies; a record is visible only to its owner; linking a loan makes its transactions transfers in
the summary; the reminder job claims a due date once; "Delete my data" removes the records. SYNTHETIC data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance import personal_jobs as J
from anum_api.finance.personal_store import open_personal_store, personal_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store, open_finance_store
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("finance_person_loans", "finance_payslips", "finance_remittances", "finance_salary_profiles")


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
    for store in (finance_store, sms_memory_store, personal_memory_store):
        store.clear()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store, personal_memory_store):
        store.clear()


def _rows(app_session: Callable[..., Iterator[Session]], user: str, sql: str) -> list:
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        return list(session.execute(text(sql)).all())


def _record(api: TestClient, account_id: str, amount: str, on: str, description: str, category: str) -> str:
    made = api.post(f"{API}/transactions", headers=ME, json={"account_id": account_id, "posted_on": on, "amount": amount,
                                                             "description": description, "category": category})
    assert made.status_code == 201, made.text
    entries = api.get(f"{API}/ledger/entries", headers=ME).json()["items"]
    return next(e["canonical_id"] for e in entries if e["source_ref"] == made.json()["id"])


def test_the_tables_have_forced_rls_owner_tenant_and_purge_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies
        check = connection.execute(text("select pg_get_constraintdef(oid) from pg_constraint where conname = 'ck_finance_links_dimension'")).scalar_one()
        assert "person_loan" in check and "payslip" in check and "remittance" in check


def test_records_are_the_owners_alone_and_lending_is_a_transfer(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "AED", "opening_balance": "5000"}).json()
    loan = client.post(f"{API}/people-loans", headers=ME, json={"direction": "lent", "person": "Friend A", "amount": "1000", "lent_on": "2026-11-02",
                                                                "due_on": "2026-11-10"}).json()
    slip = client.post(f"{API}/payslips", headers=ME, json={"employer": "Example Co", "month": "2026-10", "gross": "10000",
                                                            "deductions": [{"kind": "social_insurance", "amount": "975"}]}).json()
    remit = client.post(f"{API}/remittances", headers=ME, json={"sent_on": "2026-11-03", "amount": "500", "fee": "15", "fx_rate": "22.5",
                                                                "received_currency": "INR", "recipient": "Family", "provider": "Exchange A"}).json()
    assert slip["net"] == "9025.00" and remit["received_amount"] == "11250.00"
    # The salary profile (migration 0148) sits next to the payslips; its rules and decisions are JSON.
    salary = client.put(f"{API}/salary/profile", headers=ME, json={"employer": "Example Co", "joined_on": "2026-01-05", "currency": "SAR",
                                                                  "expected_gross": "10000", "pay_day": 27, "rules": [
                                                                      {"kind": "advance", "amount": "500", "total": "1500", "starts": "2026-02"}]})
    assert salary.status_code == 200, salary.text
    assert client.get(f"{API}/salary/profile", headers=ME).json()["profile"]["rules"][0]["total"] == "1500.00"
    assert client.get(f"{API}/salary/profile", headers=COLLEAGUE).json() == {"profile": None}
    handed = _record(client, bank["id"], "-1000", "2026-11-02", "To Friend A", "other")
    assert client.post(f"{API}/people-loans/{loan['id']}/links", headers=ME, json={"canonical_id": handed, "kind": "handed"}).status_code == 201
    month = client.get(f"{API}/summary", headers=ME, params={"month": "2026-11"}).json()
    assert month["spend"] == []
    # The Guardian reads the same links on its own finance unit of work: the lent payment is no spend against a cap.
    change = client.post(f"{API}/guardian/changes", headers=ME, json={"rules": {
        "category_caps": [{"category": "other", "currency": "AED", "amount": "500"}]}}).json()
    assert client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=ME, json={"payload_hash": change["payload_hash"]}).status_code == 200
    assert client.get(f"{API}/guardian/alerts", headers=ME, params={"month": "2026-11"}).json()["alerts"] == []
    assert client.get(f"{API}/guardian/review", headers=ME, params={"month": "2026-11"}).json()["counts"]["flagged"] == 0
    for table in TABLES:
        assert len(_rows(app_session, "user_test", f"select id from {table}")) == 1
        assert _rows(app_session, "colleague", f"select id from {table}") == []
    assert client.get(f"{API}/people-loans/{loan['id']}", headers=COLLEAGUE).status_code == 404
    with pytest.raises(DBAPIError):
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="colleague")) as session:
            session.execute(text("select set_config('anum.user_id', 'colleague', true)"))
            session.execute(text("insert into finance_person_loans (id, tenant_id, workspace_id, owner_user_id, direction, person, amount, currency, "
                                 "lent_on, remind, remind_days_before, created_at, updated_at) values ('x', :t, :w, 'user_test', 'lent', 'X', 1, 'AED', "
                                 "now(), true, 3, now(), now())"), {"t": TENANT_A, "w": WORKSPACE_A})
    ctx = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test")
    # The reminder is claimed once per due date.
    assert J.run_person(TENANT_A, WORKSPACE_A, "user_test", now=datetime(2026, 11, 8, 8, tzinfo=UTC)).reminders == 1
    with open_personal_store(ctx) as store:
        assert store.list(ctx, "person_loan")[0].reminded_for is not None
        assert store.claim_reminder(ctx, loan["id"], store.list(ctx, "person_loan")[0].due_on, False) is False
    with open_finance_store(ctx) as store:
        from anum_api.privacy import finance_export

        exported = finance_export(store, ctx)
        assert [len(exported[k]) for k in ("person_loans", "payslips", "remittances", "salary_profiles")] == [1, 1, 1, 1]
        store.delete_everything(ctx)
    with open_personal_store(ctx) as store:
        assert store.count_everything(ctx) == 0
