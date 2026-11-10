"""Loan schedules, expected income and the adjustment approval step in PostgreSQL (migration ``0158_finance_depth``).

``finance_expected_income`` has forced RLS with the tenant, owner and purge policies; a loan's schedule columns are stored
and read back; a pending adjustment is never edited, is approved only by someone other than its maker (a database check)
and is posted only once approved. Every row is SYNTHETIC.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_postgres_control_plane import client, postgres_backend  # noqa: F401 - pytest fixtures

from anum_api.business.books_store import books_memory_store

pytestmark = pytest.mark.database
OWNER = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "owner_1", "x-user-roles": "owner"}
ALICE = {**OWNER, "x-user-id": "alice", "x-user-roles": "member"}
BOOKS = "/api/v1/business/books"
FIN = "/api/v1/finance"


def _members(engine: Engine) -> None:
    with engine.begin() as connection:
        for user_id, role in (("owner_1", "owner"), ("alice", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})


def test_the_income_table_has_forced_rls_and_the_new_columns_are_nullable(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        table = "finance_expected_income"
        forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
        policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
        assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies
        for table_name, column in (("finance_person_loans", "interest_rate"), ("finance_person_loans", "installments"),
                                   ("finance_person_loans", "first_due_on"), ("business_book_settings", "adjustment_approval_threshold"),
                                   ("business_book_adjustments", "approved_by"), ("business_book_adjustments", "approved_at")):
            assert connection.execute(text("select is_nullable from information_schema.columns where table_name = :t and column_name = :c"),
                                      {"t": table_name, "c": column}).scalar_one() == "YES"


def test_expected_income_and_a_loan_schedule_are_the_owners_own(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    on = (date.today() + timedelta(days=5)).isoformat()
    made = client.post(f"{FIN}/expected-income", headers=OWNER, json={"label": "Synthetic bonus", "amount": "900", "currency": "SAR", "expected_on": on})
    assert made.status_code == 201, made.text
    lent_on = date.today() - timedelta(days=10)
    loan = client.post(f"{FIN}/people-loans", headers=OWNER, json={
        "direction": "borrowed", "person": "Synthetic Uncle", "amount": "1200", "currency": "SAR", "lent_on": lent_on.isoformat(),
        "installments": 12, "first_due_on": (lent_on + timedelta(days=30)).isoformat(), "interest_rate": "12"})
    assert loan.status_code == 201, loan.text
    detail = client.get(f"{FIN}/people-loans/{loan.json()['id']}", headers=OWNER).json()
    assert (detail["interest_rate"], detail["installments"], len(detail["schedule"])) == ("12.0000", 12, 12)
    assert client.get(f"{FIN}/expected-income", headers=OWNER).json()["total"] == 1
    with database_engine.connect() as connection, connection.begin():
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'alice', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        assert connection.execute(text("select count(*) from finance_expected_income")).scalar_one() == 0
        assert connection.execute(text("select count(*) from finance_person_loans")).scalar_one() == 0
    with database_engine.begin() as connection, pytest.raises(DBAPIError):  # a schedule needs both columns
        connection.execute(text("update finance_person_loans set first_due_on = null where id = :i"), {"i": loan.json()["id"]})


def test_a_pending_adjustment_waits_for_someone_else_and_never_changes(client: TestClient, database_engine: Engine) -> None:  # noqa: F811
    _members(database_engine)
    books_memory_store.clear()
    settings = client.put(f"{BOOKS}/settings", headers=OWNER, json={"base_currency": "SAR", "country": "SA", "fiscal_year_start_month": 1,
                                                                    "basis": "accrual", "adjustment_approval_threshold": "100", "version": None})
    assert settings.status_code == 200, settings.text
    assert client.post(f"{BOOKS}/accountants", headers=OWNER, json={"user_id": "alice"}).status_code == 201
    made = client.post(f"{BOOKS}/adjustments", headers=ALICE, json={"kind": "owner_contribution", "entry_date": date.today().isoformat(),
                                                                    "currency": "SAR", "amount": "500", "description": "Synthetic money in"})
    assert made.status_code == 201 and made.json()["status"] == "pending", made.text
    adjustment_id = made.json()["id"]
    with database_engine.begin() as connection:
        with pytest.raises(DBAPIError), connection.begin_nested():  # a pending row never changes its figures
            connection.execute(text("update business_book_adjustments set amount = 1 where id = :i"), {"i": adjustment_id})
        with pytest.raises(DBAPIError), connection.begin_nested():  # the maker never approves
            connection.execute(text("update business_book_adjustments set status = 'active', approved_by = 'alice', approved_at = now() where id = :i"),
                               {"i": adjustment_id})
    assert client.post(f"{BOOKS}/adjustments/{adjustment_id}/approve", headers=ALICE).status_code == 403
    approved = client.post(f"{BOOKS}/adjustments/{adjustment_id}/approve", headers=OWNER)
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"] == "owner_1"
    with database_engine.begin() as connection:
        with pytest.raises(DBAPIError), connection.begin_nested():  # an active row goes only to void, keeping its approval
            connection.execute(text("update business_book_adjustments set approved_by = null, approved_at = null, status = 'void', voided_by = 'owner_1', "
                                    "voided_at = now() where id = :i"), {"i": adjustment_id})
