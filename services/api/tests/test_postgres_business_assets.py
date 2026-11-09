"""The fixed asset register, loan schedules and the period-end revaluation in PostgreSQL (migration ``0155_books_assets_fx``,
docs/business-books.md#fixed-asset-register).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. The new tables are isolated per tenant and business
and have purge policies; the database keeps one asset per source, refuses an impairment decided by the person who asked
for it, and refuses disposals, impairment decisions and loan repayments dated in a closed period; the posting run posts the
register's depreciation, a loan's entries and a revaluation once. Lenders, vendors and amounts are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_postgres_business_ledger import (  # noqa: F401  (``client`` is the fixture)
    COLLEAGUE,
    ME,
    _bill,
    _rows,
    _setup,
    client,
)

from anum_api.business.books_report import add_months, month_start
from anum_api.schemas import utc_now

pytestmark = pytest.mark.database
API = "/api/v1/business/books"
TABLES = ("business_book_assets", "business_book_asset_impairments", "business_book_loans", "business_book_loan_repayments")


def _today():  # type: ignore[no-untyped-def]
    return utc_now().date()


def test_new_tables_have_forced_rls_purge_policies_and_closed_period_triggers(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, (table, policies)
        triggers = set(connection.execute(text("select tgname from pg_trigger where not tgisinternal")).scalars())
        assert {"business_book_assets_open", "business_book_asset_impairments_open", "business_book_loan_repayments_open"} <= triggers
        definers = connection.execute(text("select count(*) from pg_proc where prosecdef and proname like 'business_book%'")).scalar_one()
        assert definers == 0


def test_assets_loans_and_revaluation_post_once_and_other_scopes_read_nothing(client: TestClient,
                                                                             app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    start = add_months(month_start(_today()), -2)
    made = _bill(client, section="capital", useful_life_months=12, subtotal="1200", tax="0", category="equipment", bill_date=start.isoformat())
    listed = client.get(f"{API}/assets", headers=ME)
    assert listed.status_code == 200, listed.text
    assert [item["source_id"] for item in listed.json()["items"]] == [made["id"]]
    assert client.get(f"{API}/assets", headers=COLLEAGUE).json()["total"] == 1  # opened again: still one asset
    with pytest.raises(DBAPIError, match="uq_business_book_assets_source|duplicate key"):
        _rows(app_session, "insert into business_book_assets (id, tenant_id, workspace_id, source_kind, source_id, name, acquired_on, currency, cost, "
              "depreciation_start, created_by) select 'basset_copy', tenant_id, workspace_id, source_kind, source_id, name, acquired_on, currency, cost, "
              "depreciation_start, created_by from business_book_assets", commit=True)
    loan = client.post(f"{API}/loans", headers=ME, json={"lender": "Synthetic Bank", "principal": "12000", "currency": "SAR", "annual_rate": "12",
                                                         "start_date": start.isoformat(), "term_months": 12})
    assert loan.status_code == 201, loan.text
    paid = client.post(f"{API}/loans/{loan.json()['id']}/repayments", headers=ME, json={"paid_on": add_months(start, 1).isoformat(), "amount": "1066.19"})
    assert paid.status_code == 201 and paid.json()["interest"] == "120.00", paid.text
    assert client.post(f"{API}/ledger/post", headers=ME).json()["posted"] == 0
    kinds = dict(_rows(app_session, "select source_kind, count(*) from business_book_journal_entries group by source_kind"))
    assert kinds["loan_repayment"] == 1 and kinds["loan"] >= 3 and kinds["bill"] == 4  # the bill and three months of depreciation
    unbalanced = _rows(app_session, "select entry_id from business_book_journal_lines group by entry_id having sum(debit) <> sum(credit)")
    assert unbalanced == []
    end = month_start(_today()) - timedelta(days=1)
    revalued = client.post(f"{API}/ledger/revaluation", headers=ME, json={"period_end": end.isoformat()})
    assert revalued.status_code == 200 and revalued.json()["currencies"] == [], revalued.text  # nothing in another currency
    for workspace, tenant in ((WORKSPACE_A, TENANT_A), (WORKSPACE_B, TENANT_B)):
        for table in TABLES:
            assert _rows(app_session, f"select count(*) from {table}", workspace=workspace, tenant=tenant) == [(0,)], (table, workspace)  # nosec B608
    sheet = client.get(f"{API}/ledger/statements", headers=ME).json()
    assert sheet["trial_balance"]["balanced"] and sheet["balance_sheet"]["balanced"]


def test_maker_and_checker_and_closed_periods_are_kept_by_the_database(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    start = add_months(month_start(_today()), -3)
    made = _bill(client, section="capital", useful_life_months=12, subtotal="1200", tax="0", category="equipment", bill_date=start.isoformat())
    asset = next(item for item in client.get(f"{API}/assets", headers=ME).json()["items"] if item["source_id"] == made["id"])
    asked = client.post(f"{API}/assets/{asset['id']}/impairments", headers=ME, json={"entry_date": _today().isoformat(), "amount": "50",
                                                                                     "reason": "Synthetic damage"})
    assert asked.status_code == 201, asked.text
    with pytest.raises(DBAPIError, match="ck_business_book_asset_impairments_decision"):
        _rows(app_session, "update business_book_asset_impairments set status = 'approved', decided_by = requested_by, decided_at = now()", commit=True)
    loan = client.post(f"{API}/loans", headers=ME, json={"lender": "Synthetic Bank", "principal": "1000", "currency": "SAR", "annual_rate": "0",
                                                         "start_date": start.isoformat(), "term_months": 10}).json()
    end = month_start(_today()) - timedelta(days=1)
    assert client.post(f"{API}/accountants", headers=ME, json={"user_id": "colleague"}).status_code == 201
    closed = client.post(f"{API}/close", headers=COLLEAGUE, json={"through": end.isoformat()})
    assert closed.status_code == 200, closed.text
    in_closed = (end - timedelta(days=2)).isoformat()
    scope = {"t": TENANT_A, "l": loan["id"], "d": in_closed}
    with pytest.raises(DBAPIError, match="closed period"):
        _rows(app_session, "insert into business_book_loan_repayments (id, tenant_id, workspace_id, loan_id, paid_on, amount, currency, interest, principal, "
              "created_by) select 'brepay_late', tenant_id, workspace_id, id, :d, 10, 'SAR', 0, 10, 'u' from business_book_loans where id = :l",
              scope, commit=True)
    with pytest.raises(DBAPIError, match="closed period"):
        _rows(app_session, "update business_book_assets set status = 'disposed', disposed_on = :d, disposal_kind = 'write_off' where id = :a",
              {"d": in_closed, "a": asset["id"]}, commit=True)
    refused = client.post(f"{API}/loans/{loan['id']}/repayments", headers=ME, json={"paid_on": in_closed, "amount": "10"})
    assert refused.status_code == 409 and refused.json()["code"] == "period_locked", refused.text
    approved = client.post(f"{API}/assets/{asset['id']}/impairments/{asked.json()['id']}/approve", headers=COLLEAGUE,
                           json={"version": asked.json()["version"]})
    assert approved.status_code == 200, approved.text
    assert _rows(app_session, "select count(*) from business_book_journal_entries where source_kind = 'asset'") == [(1,)]
