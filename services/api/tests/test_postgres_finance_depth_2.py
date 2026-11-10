"""Personal trips and projects and a repayment split in PostgreSQL (migration ``0160_finance_depth_2``).

``finance_personal_tags`` has forced RLS with the tenant, owner and purge policies; a tag is stored, read back and invisible
to another member; a split across two loans is two ``person_loan`` links whose total the database keeps within the payment.
Every row is SYNTHETIC.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

pytestmark = pytest.mark.database
OWNER = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "owner_1", "x-user-roles": "owner"}
FIN = "/api/v1/finance"


def _members(engine: Engine) -> None:
    with engine.begin() as connection:
        for user_id, role in (("owner_1", "owner"), ("alice", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})


def test_the_tags_table_has_forced_rls_and_its_checks(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        table = "finance_personal_tags"
        forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
        policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
        assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies
        checks = set(connection.execute(text("select conname from pg_constraint where conrelid = cast(:t as regclass) and contype = 'c'"), {"t": table}).scalars())
        assert {"ck_finance_personal_tags_kind", "ck_finance_personal_tags_dates"} <= checks


def test_a_tag_and_a_split_are_the_owners_own(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    made = client.post(f"{FIN}/tags", headers=OWNER, json={"kind": "trip", "name": "Synthetic Muscat trip", "starts_on": date.today().isoformat(),
                                                           "ends_on": (date.today() + timedelta(days=4)).isoformat(), "keywords": "Muscat"})
    assert made.status_code == 201, made.text
    assert client.get(f"{FIN}/tags/{made.json()['id']}", headers=OWNER).json()["keywords"] == "Muscat"
    bank = client.post(f"{FIN}/accounts", headers=OWNER, json={"name": "Synthetic bank", "kind": "bank", "currency": "SAR", "opening_balance": "0"})
    assert bank.status_code == 201, bank.text
    loans = []
    for amount in ("100", "50"):
        loan = client.post(f"{FIN}/people-loans", headers=OWNER, json={
            "direction": "lent", "person": "Synthetic Friend", "amount": amount, "currency": "SAR",
            "lent_on": (date.today() - timedelta(days=30)).isoformat(), "due_on": (date.today() + timedelta(days=5)).isoformat()})
        assert loan.status_code == 201, loan.text
        loans.append(loan.json()["id"])
    txn = client.post(f"{FIN}/transactions", headers=OWNER, json={"account_id": bank.json()["id"], "posted_on": date.today().isoformat(), "amount": "150",
                                                                  "description": "Synthetic Friend", "category": "other"})
    assert txn.status_code == 201, txn.text
    entries = client.get(f"{FIN}/ledger/entries", headers=OWNER).json()["items"]
    canonical = next(e["canonical_id"] for e in entries if e["source_ref"] == txn.json()["id"])
    split = client.post(f"{FIN}/people-loans/split", headers=OWNER, json={"canonical_id": canonical, "parts": [
        {"loan_id": loans[0], "amount": "100"}, {"loan_id": loans[1], "amount": "50"}]})
    assert split.status_code == 201, split.text
    over = client.post(f"{FIN}/people-loans/split", headers={**OWNER, "Idempotency-Key": "again-example"}, json={"canonical_id": canonical, "parts": [
        {"loan_id": loans[0], "amount": "1"}, {"loan_id": loans[1], "amount": "1"}]})
    assert over.status_code == 409
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'alice', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        assert connection.execute(text("select count(*) from finance_personal_tags")).scalar_one() == 0
        assert connection.execute(text("select count(*) from finance_links where dimension = 'person_loan'")).scalar_one() == 0
    with database_engine.connect() as connection:
        rows = connection.execute(text("select amount from finance_links where dimension = 'person_loan' and canonical_id = :c and status = 'active' "
                                       "order by amount"), {"c": canonical}).scalars().all()
        assert [str(r) for r in rows] == ["50.0000", "100.0000"]
