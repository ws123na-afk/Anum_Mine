"""Reconciliation sessions in PostgreSQL (migration ``0152_finance_reconciliation``, docs/finance.md#reconciliation-sessions).

The API runs as the non-owner ``anum_test_app`` role. Both tables have forced RLS with the tenant, owner and purge
policies; a session is visible only to its owner; completing a tied statement records the snapshot; the partial
unique indexes let only one live resolution per statement row and ledger record exist; a correcting entry posts one
balanced journal entry; the journal refuses an entry with no postings at commit (audit B7); "Delete my data" removes the
sessions. Every row is SYNTHETIC ("EXAMPLE").
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.reconciliation_store import recon_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store, open_finance_store
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("finance_reconciliations", "finance_reconciliation_items")
STATEMENT = "Date,Description,Amount,Balance\n2026-10-03,EXAMPLE CAFE,-50.00,950.00\n2026-10-04,SAMPLE BOOKS,-20.00,930.00\n"


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
    for store in (finance_store, sms_memory_store, links_memory_store, recon_memory_store):
        store.clear()
    yield TestClient(app)


def _rows(app_session: Callable[..., Iterator[Session]], user: str, sql: str) -> list:
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        return list(session.execute(text(sql)).all())


def test_the_tables_have_forced_rls_owner_tenant_and_purge_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies
        definer = connection.execute(text("select prosecdef from pg_proc where proname = 'finance_journal_entry_has_postings'")).scalar_one()
        assert definer is False


def test_a_session_is_the_owners_and_completes_with_its_snapshot(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR", "opening_balance": "1000"}).json()
    imported = client.post(f"{API}/imports", headers=ME, json={"account_id": bank["id"], "content": STATEMENT, "filename": "oct.csv",
                                                               "opening_balance": "1000.00", "closing_balance": "930.00"})
    assert imported.status_code == 201, imported.text
    made = client.post(f"{API}/reconciliations", headers=ME, json={"account_id": bank["id"], "import_id": imported.json()["id"]})
    assert made.status_code == 201, made.text
    detail = made.json()
    assert detail["state"] == "balanced" and detail["figures"]["book_closing"] == "930.00"
    rows = client.get(f"{API}/transactions", headers=ME, params={"account_id": bank["id"]}).json()["items"]
    attached = client.post(f"{API}/reconciliations/{detail['id']}/resolutions", headers=ME, json={"kind": "pending", "transaction_id": rows[0]["id"]})
    assert attached.status_code == 201, attached.text
    assert client.post(f"{API}/reconciliations/{detail['id']}/resolutions", headers=ME,
                       json={"kind": "attach", "transaction_id": rows[0]["id"]}).status_code == 409
    assert client.delete(f"{API}/reconciliations/{detail['id']}/resolutions/{attached.json()['id']}", headers=ME).status_code == 204
    current = client.get(f"{API}/reconciliations/{detail['id']}", headers=ME).json()
    done = client.post(f"{API}/reconciliations/{detail['id']}/complete", headers=ME, json={"version": current["version"]})
    assert done.status_code == 200, done.text
    assert done.json()["snapshot"]["figures"]["statement_closing"] == "930.00"
    for table in TABLES:
        assert len(_rows(app_session, "user_test", f"select id from {table}")) == 1
        assert _rows(app_session, "colleague", f"select id from {table}") == []
    assert client.get(f"{API}/reconciliations/{detail['id']}", headers=COLLEAGUE).status_code in (403, 404)
    overview = client.get(f"{API}/accounts/overview", headers=ME).json()
    assert next(a for a in overview["accounts"] if a["account_id"] == bank["id"])["reconciliation"]["status"] == "completed"
    # Another person cannot write a session in the owner's name.
    with pytest.raises(DBAPIError):
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="colleague")) as session:
            session.execute(text("select set_config('anum.user_id', 'colleague', true)"))
            session.execute(text("insert into finance_reconciliations (id, tenant_id, workspace_id, owner_user_id, account_id, period_start, period_end, "
                                 "currency, statement_closing, source, status, created_by, created_at, updated_at) values ('x', :t, :w, 'user_test', 'a', "
                                 "'2026-10-01', '2026-10-31', 'SAR', 0, 'typed', 'open', 'colleague', now(), now())"), {"t": TENANT_A, "w": WORKSPACE_A})
    ctx = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test")
    with open_finance_store(ctx) as store:
        from anum_api.privacy import finance_export

        exported = finance_export(store, ctx)
        assert len(exported["reconciliations"]) == 1 and len(exported["reconciliation_items"]) == 1
        store.delete_everything(ctx)
    for table in TABLES:
        assert _rows(app_session, "user_test", f"select id from {table}") == []


def test_only_one_live_resolution_per_row_is_stored(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR", "opening_balance": "1000"}).json()
    detail = client.post(f"{API}/reconciliations", headers=ME, json={"account_id": bank["id"], "period_start": "2026-10-01",
                                                                     "period_end": "2026-10-31", "statement_closing": "1000"}).json()
    common = {"t": TENANT_A, "w": WORKSPACE_A, "u": "user_test", "r": detail["id"]}
    insert = ("insert into finance_reconciliation_items (id, tenant_id, workspace_id, owner_user_id, reconciliation_id, kind, statement_ref, "
              "transaction_id, status, created_by, created_at) values (:id, :t, :w, :u, :r, 'match', 'imp#1', :txn, 'active', :u, now())")
    with pytest.raises(IntegrityError):
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test")) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(text(insert), {**common, "id": "i1", "txn": "txn_a"})
            session.execute(text(insert), {**common, "id": "i2", "txn": "txn_b"})  # the same statement row a second time


def test_a_journal_entry_without_postings_is_refused_at_commit(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    common = {"t": TENANT_A, "w": WORKSPACE_A, "u": "user_test"}
    entry = ("insert into finance_journal_entries (id, tenant_id, workspace_id, owner_user_id, posted_on, kind, description, source, status) "
             "values (:id, :t, :w, :u, '2026-11-01', 'manual', 'x', 'manual', 'posted')")
    with pytest.raises(DBAPIError, match="needs at least two postings"):
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test")) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(text(entry), {**common, "id": "je_empty"})
            session.execute(text("set constraints all immediate"))  # what commit does
    # An entry with balanced postings still commits, and a correcting entry posts one.
    posting = ("insert into finance_postings (id, tenant_id, workspace_id, owner_user_id, entry_id, ledger_account, amount, currency) "
               "values (:id, :t, :w, :u, :e, :a, :amt, 'SAR')")
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test"), commit=True) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        session.execute(text(entry), {**common, "id": "je_full"})
        session.execute(text(posting), {**common, "id": "pa", "e": "je_full", "a": "expense:groceries", "amt": Decimal("5")})
        session.execute(text(posting), {**common, "id": "pb", "e": "je_full", "a": "asset:x", "amt": Decimal("-5")})
    bank = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR", "opening_balance": "1000"}).json()
    imported = client.post(f"{API}/imports", headers=ME, json={"account_id": bank["id"], "content": STATEMENT, "filename": "oct.csv",
                                                               "opening_balance": "1000.00", "closing_balance": "930.00",
                                                               "decisions": [{"line": 3, "action": "skip"}]}).json()
    detail = client.post(f"{API}/reconciliations", headers=ME, json={"account_id": bank["id"], "import_id": imported["id"]}).json()
    missing = next(i for i in detail["items"] if i["kind"] == "not_in_ledger")
    shown = client.post(f"{API}/reconciliations/{detail['id']}/corrections/preview", headers=ME, json={"statement_ref": missing["statement_ref"]}).json()
    posted = client.post(f"{API}/reconciliations/{detail['id']}/corrections", headers=ME,
                         json={"statement_ref": missing["statement_ref"], "payload_hash": shown["payload_hash"]})
    assert posted.status_code == 201, posted.text
    assert client.get(f"{API}/reconciliations/{detail['id']}", headers=ME).json()["state"] == "balanced"
    sums = _rows(app_session, "user_test", "select p.currency, sum(p.amount) from finance_postings p join finance_journal_entries e on e.id = p.entry_id "
                                           f"where e.source_ref = '{posted.json()['transaction_id']}' group by p.currency")
    assert sums == [("SAR", Decimal("0.0000"))]
