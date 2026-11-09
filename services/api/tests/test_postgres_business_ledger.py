"""The business journal in PostgreSQL (migration ``0151_business_ledger``, docs/business-books.md#the-journal).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. The database itself refuses an
entry that does not balance or has no lines, any change to a journal line, any change to an entry but
posted to reversed, and anything dated in a closed period; other businesses and tenants read nothing;
the daily job finds businesses as ``anum_maintenance`` through the scope columns of book settings only.
Vendors and amounts are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.business import books_jobs
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/business/books"
OTHER_BIZ = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
ME = {**OTHER_BIZ, "x-workspace-id": WORKSPACE_A2}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("business_book_ledger_accounts", "business_book_journal_entries", "business_book_journal_lines", "business_book_posting_issues",
          "business_book_closes", "business_book_accountants")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        for user_id, role, workspace in (("user_test", "owner", WORKSPACE_A), ("user_test", "owner", WORKSPACE_A2), ("colleague", "member", WORKSPACE_A2)):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": workspace})
    yield TestClient(app)


def _rows(app_session: Callable[..., Iterator[Session]], sql: str, params: dict | None = None, *, workspace: str = WORKSPACE_A2,
          tenant: str = TENANT_A, commit: bool = False) -> list:
    with app_session(TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="user_test"), commit=commit) as session:
        result = None
        for statement in sql.split(";;"):  # several statements in one unit of work
            result = session.execute(text(statement), params or {})
        return list(result.all()) if result is not None and result.returns_rows else []


def _setup(api: TestClient, headers: dict = ME) -> None:
    response = api.put(f"{API}/settings", headers=headers, json={"base_currency": "SAR", "version": None})
    assert response.status_code == 200, response.text


def _bill(api: TestClient, **body: object) -> dict:
    vendor = api.post(f"{API}/vendors", headers=ME, json={"name": f"Synthetic Landlord {utc_now().timestamp()}", "default_category": "rent"})
    assert vendor.status_code == 201, vendor.text
    created = api.post(f"{API}/bills", headers=ME, json={"vendor_id": vendor.json()["id"], "description": "Synthetic rent", "currency": "SAR",
                                                         "bill_date": utc_now().date().isoformat(), "subtotal": "100", "tax": "15", **body})
    assert created.status_code == 201, created.text
    return created.json()


def test_journal_tables_have_forced_rls_purge_policies_and_the_rules_in_triggers(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, (table, policies)
        triggers = set(connection.execute(text("select tgname from pg_trigger where not tgisinternal")).scalars())
        assert {"business_book_lines_balanced", "business_book_entries_balanced", "business_book_lines_frozen", "business_book_entries_frozen",
                "business_book_closes_frozen", "business_book_lines_open", "business_book_bills_open", "business_book_bill_payments_open",
                "business_book_adjustments_open", "business_book_intercompany_open"} <= triggers
        # Invoker functions only: nothing here runs with its owner's rights over forced-RLS tables.
        definers = connection.execute(text("select count(*) from pg_proc where prosecdef and proname like 'business_book%'")).scalar_one()
        assert definers == 0


def test_posting_writes_balanced_lines_once_and_other_scopes_read_nothing(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    _bill(client)
    posted = _rows(app_session, "select count(*), sum(debit), sum(credit) from business_book_journal_lines")
    assert posted[0][0] == 3 and posted[0][1] == posted[0][2] == 115
    assert client.post(f"{API}/ledger/post", headers=COLLEAGUE).json()["posted"] == 0
    assert _rows(app_session, "select count(*) from business_book_journal_entries") == [(1,)]
    for workspace, tenant in ((WORKSPACE_A, TENANT_A), (WORKSPACE_B, TENANT_B)):
        for table in TABLES:
            assert _rows(app_session, f"select count(*) from {table}", workspace=workspace, tenant=tenant) == [(0,)], (table, workspace)  # nosec B608
    data = client.get(f"{API}/ledger/statements", headers=COLLEAGUE).json()
    assert data["trial_balance"]["balanced"] and data["balance_sheet"]["balanced"] and data["complete"]


def test_the_database_refuses_unbalanced_or_empty_entries_and_any_edit_of_the_journal(client: TestClient,
                                                                                      app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    _bill(client)
    bank = _rows(app_session, "select id from business_book_ledger_accounts where system_key = 'bank'")[0][0]
    insert_entry = ("insert into business_book_journal_entries (id, tenant_id, workspace_id, entry_date, event_date, source_kind, event_key, fingerprint, "
                    "description, created_by) values (:id, :t, :w, current_date, current_date, 'manual', :id, 'x', 'Synthetic', 'u')")
    scope = {"t": TENANT_A, "w": WORKSPACE_A2}
    with pytest.raises(DBAPIError, match="needs at least two lines"):
        _rows(app_session, insert_entry, {"id": "bje_empty", **scope}, commit=True)
    with pytest.raises(DBAPIError, match="needs at least two lines|does not balance"):
        _rows(app_session, insert_entry + ";; insert into business_book_journal_lines (id, tenant_id, workspace_id, entry_id, line_no, entry_date, "
              "source_kind, account_id, debit, credit, currency, amount) values ('bjl_x', :t, :w, :id, 1, current_date, 'manual', :a, 10, 0, 'SAR', 10)",
              {"id": "bje_single", "a": bank, **scope}, commit=True)
    line = _rows(app_session, "select id, entry_id from business_book_journal_lines limit 1")[0]
    with pytest.raises(DBAPIError, match="never change"):
        _rows(app_session, "update business_book_journal_lines set debit = debit + 1 where id = :i", {"i": line[0]})
    with pytest.raises(DBAPIError, match="never change"):
        _rows(app_session, "delete from business_book_journal_lines where id = :i", {"i": line[0]})
    with pytest.raises(DBAPIError, match="only changes from posted to reversed"):
        _rows(app_session, "update business_book_journal_entries set description = 'edited' where id = :i", {"i": line[1]})
    with pytest.raises(DBAPIError, match="never deleted"):
        _rows(app_session, "delete from business_book_journal_entries where id = :i", {"i": line[1]})


def test_a_closed_period_refuses_postings_and_source_changes_until_reopened(client: TestClient,
                                                                            app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    last_month_end = utc_now().date().replace(day=1) - timedelta(days=1)
    made = _bill(client, bill_date=(last_month_end - timedelta(days=2)).isoformat())
    assert client.post(f"{API}/accountants", headers=ME, json={"user_id": "colleague"}).status_code == 201
    closed = client.post(f"{API}/close", headers=COLLEAGUE, json={"through": last_month_end.isoformat()})
    assert closed.status_code == 200, closed.text
    with pytest.raises(DBAPIError, match="closed period"):
        _rows(app_session, "update business_book_bills set subtotal = 1, total = 16 where id = :i", {"i": made["id"]})
    assert _rows(app_session, "update business_book_bills set description = 'Synthetic wording' where id = :i", {"i": made["id"]}, commit=True) == []
    account = _rows(app_session, "select id from business_book_ledger_accounts where system_key = 'bank'")[0][0]
    with pytest.raises(DBAPIError, match="closed period"):
        _rows(app_session, "insert into business_book_journal_lines (id, tenant_id, workspace_id, entry_id, line_no, entry_date, source_kind, account_id, "
              "debit, credit, currency, amount) select 'bjl_late', tenant_id, workspace_id, id, 9, :d, 'manual', :a, 1, 0, 'SAR', 1 "
              "from business_book_journal_entries limit 1", {"d": last_month_end, "a": account})
    with pytest.raises(DBAPIError, match="reopened with a reason"):
        _rows(app_session, "delete from business_book_closes")
    refused = client.patch(f"{API}/bills/{made['id']}", headers=ME, json={**{k: made[k] for k in ("vendor_id", "description", "bill_date", "currency", "category")},
                                                                         "subtotal": "200", "tax": "0",
                                                                         "version": client.get(f"{API}/bills/{made['id']}", headers=ME).json()["version"]})
    assert refused.status_code == 409 and refused.json()["code"] == "period_locked", refused.text
    assert client.post(f"{API}/reopen", headers=COLLEAGUE, json={"month": last_month_end.isoformat(), "reason": "late credit note"}).status_code == 403
    reopened = client.post(f"{API}/reopen", headers=ME, json={"month": last_month_end.isoformat(), "reason": "late credit note"})
    assert reopened.status_code == 200, reopened.text
    assert _rows(app_session, "select status, reopen_reason from business_book_closes") == [("reopened", "late credit note")]


def test_the_daily_job_finds_businesses_through_scope_columns_only(client: TestClient, database_engine: Engine,
                                                                   app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    _bill(client)
    _rows(app_session, "select 1")
    result = books_jobs.run()
    assert result["ledgers"] == 1 and result["entries_posted"] == 0 and result["failed"] == 0, result
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert connection.execute(text("select count(*) from business_book_settings")).scalar_one() == 1
        for sql in ("select base_currency from business_book_settings", "select id from business_book_journal_entries",
                    "update business_book_settings set basis = 'cash'"):
            with pytest.raises(DBAPIError):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(sql))
