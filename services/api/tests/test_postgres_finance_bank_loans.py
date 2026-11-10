"""Bank loan terms and accepted batches in PostgreSQL (migration ``0161_finance_bank_loans``, docs/finance.md#bank-loans).

The loan columns on ``finance_debts`` are nullable and checked by the database; ``finance_loan_batches`` has forced RLS with
the tenant, owner and purge policies; terms and an accepted batch round-trip through the API and another person sees
neither. Every row is SYNTHETIC.
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api.schemas import utc_now

pytestmark = pytest.mark.database
OWNER = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "owner_1", "x-user-roles": "owner"}
FIN = "/api/v1/finance"
COLUMNS = ("lender", "loan_start_on", "loan_amount", "loan_rate_type", "loan_rate", "loan_term_months", "loan_installment", "loan_payment_day",
           "loan_repayment", "loan_account_id", "loan_status", "loan_closed_on")


def _members(engine: Engine) -> None:
    with engine.begin() as connection:
        for user_id, role in (("owner_1", "owner"), ("alice", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})


def _months_ago(months: int) -> date:
    today = utc_now().date()
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, 15)


def test_the_loan_columns_are_nullable_and_the_batch_table_has_forced_rls(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for column in COLUMNS:
            assert connection.execute(text("select is_nullable from information_schema.columns where table_name = 'finance_debts' and column_name = :c"),
                                      {"c": column}).scalar_one() == "YES"
        table = "finance_loan_batches"
        forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
        policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
        assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies


def test_terms_and_an_accepted_batch_are_the_owners_own(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    debt = client.post(f"{FIN}/debts", headers=OWNER, json={"name": "Example car loan", "kind": "loan", "currency": "SAR", "balance": "99999"})
    assert debt.status_code == 201, debt.text
    debt_id = debt.json()["id"]
    start = _months_ago(4)
    view = client.put(f"{FIN}/debts/{debt_id}/loan", headers=OWNER, json={"lender": "Example Bank", "start_on": start.isoformat(), "amount": "24000",
                                                                          "installment": "1000", "term_months": 24, "payment_day": 27,
                                                                          "repayment": "salary_deduction"})
    assert view.status_code == 200, view.text
    first_month = view.json()["rows"][0]["due_on"][:7]
    slip = client.post(f"{FIN}/payslips", headers=OWNER, json={"employer": "Example Employer", "month": first_month, "currency": "SAR", "gross": "15000",
                                                               "deductions": [{"kind": "loan", "amount": "1000", "label": "Example Bank"}]})
    assert slip.status_code == 201, slip.text
    items = client.get(f"{FIN}/suggestions?kind=loan_payments&limit=100", headers=OWNER).json()["items"]
    item = next(s for s in items if s["params"]["debt_id"] == debt_id)
    accepted = client.post(f"{FIN}/suggestions/accept", headers=OWNER, json={"key": item["key"], "payload_hash": item["payload_hash"]})
    assert accepted.status_code == 200, accepted.text
    loan = client.get(f"{FIN}/debts/{debt_id}/loan", headers=OWNER).json()
    assert loan["terms"]["amount"] == "24000.00" and loan["totals"]["paid_count"] == 1 and loan["batches"][0]["status"] == "active"
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'alice', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        assert connection.execute(text("select count(*) from finance_loan_batches")).scalar_one() == 0
        assert connection.execute(text("select count(*) from finance_debts")).scalar_one() == 0
    undone = client.post(f"{FIN}/loan-batches/{accepted.json()['loan_batch_id']}/undo", headers=OWNER)
    assert undone.status_code == 200 and undone.json()["status"] == "undone"
    with database_engine.begin() as connection, pytest.raises(DBAPIError):  # a paid-off loan needs its date
        connection.execute(text("update finance_debts set loan_status = 'paid_off' where id = :i"), {"i": debt_id})
