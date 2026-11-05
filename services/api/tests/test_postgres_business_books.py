"""Business books in PostgreSQL (migration ``0137_business_books``, docs/business-books.md#storage).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. Another business and another
tenant read nothing; an allocation pays one bill even when two requests race; an adjustment never
changes after it is written (only voided); an intercompany entry lands in both businesses; and
allocating a payment and paying a bill with it add no journal entry, posting or transaction.
Bank messages are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/business/books"
FIN = "/api/v1/finance"
FUEL_SMS = "SYNTHETIC. Purchase of SAR 150.00 at PETROMIN FUEL with card **1234"
PERSONAL = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
ME = {**PERSONAL, "x-workspace-id": WORKSPACE_A2}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("business_book_settings", "business_book_cost_centres", "business_book_vendors", "business_book_bills", "business_book_bill_payments",
          "business_book_allocation_tags", "business_book_budgets", "business_book_rates", "business_book_adjustments", "business_book_intercompany")
JOURNAL = "select e.id, e.status, p.ledger_account, p.amount from finance_journal_entries e join finance_postings p on p.entry_id = e.id order by 1, 3"


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
    for store in (finance_store, sms_memory_store, links_memory_store):
        store.clear()
    svc._last_notified.clear()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store, links_memory_store):
        store.clear()


def _rows(app_session: Callable[..., Iterator[Session]], sql: str, params: dict | None = None, *, workspace: str = WORKSPACE_A2,
          tenant: str = TENANT_A, user: str = "user_test") -> list:
    with app_session(TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        return list(session.execute(text(sql), params or {}).all())


def _setup(api: TestClient, headers: dict = ME) -> None:
    response = api.put(f"{API}/settings", headers=headers, json={"base_currency": "SAR", "country": "SA", "version": None})
    assert response.status_code == 200, response.text


def _bill(api: TestClient, **body: object) -> dict:
    vendor = api.post(f"{API}/vendors", headers=ME, json={"name": f"Petromin {utc_now().timestamp()}", "default_category": "fuel"})
    assert vendor.status_code == 201, vendor.text
    created = api.post(f"{API}/bills", headers=ME, json={"vendor_id": vendor.json()["id"], "description": "Fuel", "bill_date": utc_now().date().isoformat(),
                                                         "currency": "SAR", "subtotal": "150", "tax": "0", **body})
    assert created.status_code == 201, created.text
    return created.json()


def _allocated_payment(api: TestClient) -> str:
    card = api.post(f"{FIN}/accounts", headers=PERSONAL, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    assert api.post(f"{FIN}/sms/self-test", headers=PERSONAL).json()["passed"]
    version = api.get(f"{FIN}/sms/settings", headers=PERSONAL).json()["version"]
    assert api.put(f"{FIN}/sms/settings", headers=PERSONAL, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": "TestBank", "bank": "test"}], "verified_senders": ["TestBank"],
        "account_map": [{"last4": "1234", "account_id": card["id"]}], "wallet_cards": [{"card": "My Visa", "account_id": card["id"]}]}).status_code == 200
    token = api.post(f"{FIN}/sms/devices", headers=PERSONAL, json={"name": "iPhone"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    assert api.post(f"{FIN}/wallet/ingest", headers=bearer, json={"source": "apple_wallet", "amount": "SAR 150.00", "merchant": "Petromin Fuel",
                                                                  "card": "My Visa", "name": "Purchase"}).json()["status"] == "posted"
    assert api.post(f"{FIN}/sms/ingest", headers=bearer, json={"message": FUEL_SMS, "sender": "TestBank"}).json()["status"] == "attached"
    canonical = api.get(f"{FIN}/ledger/entries", headers=PERSONAL).json()["items"][0]["canonical_id"]
    allocated = api.post(f"{FIN}/allocations", headers=PERSONAL, json={"canonical_id": canonical, "workspace_id": WORKSPACE_A2, "amount": "150",
                                                                      "currency": "SAR", "category": "fuel", "treatment": "owner_contribution"})
    assert allocated.status_code == 201, allocated.text
    rows = api.get(f"{API}/allocations", headers=ME).json()["items"]
    assert len(rows) == 1
    return rows[0]["id"]


def test_book_tables_have_forced_rls_and_purge_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, (table, policies)


def test_every_record_kind_round_trips_and_other_scopes_read_nothing(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    centre = client.post(f"{API}/cost-centres", headers=ME, json={"code": "OPS", "name": "Operations"}).json()
    bill = _bill(client, cost_centre_id=centre["id"], recurrence="monthly")
    paid = client.post(f"{API}/bills/{bill['id']}/payments", headers={**ME, "Idempotency-Key": "pg-pay-0001"}, json={"amount": "50", "method": "cash"})
    assert paid.status_code == 201 and paid.json()["payment_state"] == "partly_paid", paid.text
    assert client.post(f"{API}/bills/{bill['id']}/payments", headers={**ME, "Idempotency-Key": "pg-pay-0001"},
                       json={"amount": "50", "method": "cash"}).status_code == 200
    today = utc_now().date()
    assert client.post(f"{API}/budgets", headers=ME, json={"name": "Fuel", "period_start": today.replace(day=1).isoformat(), "amount": "500",
                                                           "category": "fuel"}).status_code == 201
    assert client.post(f"{API}/rates", headers=ME, json={"base": "USD", "quote": "SAR", "rate": "3.75", "as_of": today.isoformat(),
                                                         "source": "Synthetic reference"}).status_code == 201
    assert client.post(f"{API}/adjustments", headers=ME, json={"kind": "capital_item", "entry_date": today.isoformat(), "currency": "SAR", "amount": "1200",
                                                               "description": "Synthetic laptop", "start_month": today.replace(day=1).isoformat(),
                                                               "months": 12}).status_code == 201
    month = {"from": today.replace(day=1).isoformat(), "to": today.isoformat()}
    data = client.get(f"{API}/report", headers=ME, params=month).json()
    assert data["pnl"]["operating_expenses"]["total"] == "250.00", data["pnl"]  # the bill and one month of depreciation
    assert data["recurring"]["per_month"] == [{"currency": "SAR", "amount": "150.00"}]
    for path in ("cost-centres", "vendors", "bills", "budgets", "rates", "adjustments", "intercompany", "allocations", "projects"):
        assert client.get(f"{API}/{path}", headers=COLLEAGUE).status_code == 200, path
    counts = {table: _rows(app_session, f"select count(*) from {table}")[0][0] for table in TABLES}  # nosec B608: fixed names
    assert counts["business_book_bills"] == 1 and counts["business_book_bill_payments"] == 1 and counts["business_book_settings"] == 1
    for workspace, tenant in ((WORKSPACE_A, TENANT_A), (WORKSPACE_B, TENANT_B)):
        for table in TABLES:
            assert _rows(app_session, f"select count(*) from {table}", workspace=workspace, tenant=tenant) == [(0,)], (table, workspace)  # nosec B608
    assert client.get(f"{API}/bills/{bill['id']}", headers=PERSONAL).status_code == 404
    # A row written under another scope is refused by the row policy itself.
    with pytest.raises(DBAPIError):
        _rows(app_session, "insert into business_book_cost_centres (id, tenant_id, workspace_id, code, name, created_by) "
                           "values ('x', :t, :w, 'X', 'X', 'u')", {"t": TENANT_A, "w": WORKSPACE_A})


def test_an_allocation_pays_one_bill_and_adds_no_posting_even_when_requests_race(client: TestClient,
                                                                                  app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    allocation = _allocated_payment(client)
    before = _rows(app_session, JOURNAL, workspace=WORKSPACE_A)
    transactions = _rows(app_session, "select count(*) from finance_transactions", workspace=WORKSPACE_A)
    first, second = _bill(client), _bill(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda bill_id: client.post(f"{API}/bills/{bill_id}/payments", headers=ME,
                                                          json={"kind": "allocation", "allocation_id": allocation}).status_code, (first["id"], second["id"])))
    assert sorted(codes) == [201, 409], codes
    assert _rows(app_session, "select count(*) from business_book_bill_payments where voided_at is null") == [(1,)]
    assert _rows(app_session, JOURNAL, workspace=WORKSPACE_A) == before
    assert _rows(app_session, "select count(*) from finance_transactions", workspace=WORKSPACE_A) == transactions
    data = client.get(f"{API}/report", headers=ME).json()
    assert data["pnl"]["operating_expenses"]["total"] == "300.00"  # two bills of 150; the allocation is one bill's payment, not a third cost
    assert data["cash_flow"]["paid_by_members"] == {"owner_contribution": [{"currency": "SAR", "amount": "150.00"}]}
    # The colleague reads the books but never the payer's ledger or links.
    assert client.get(f"{API}/report", headers=COLLEAGUE).status_code == 200
    assert _rows(app_session, "select count(*) from finance_links", workspace=WORKSPACE_A, user="colleague") == [(0,)]


def test_adjustments_only_void_and_intercompany_lands_in_both_businesses(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    _setup(client, PERSONAL)
    today = utc_now().date().isoformat()
    made = client.post(f"{API}/adjustments", headers=ME, json={"kind": "accrual", "entry_date": today, "currency": "SAR", "amount": "300",
                                                              "description": "Synthetic utilities"}).json()
    with pytest.raises(DBAPIError, match="never changes"):
        _rows(app_session, "update business_book_adjustments set amount = 1 where id = :i", {"i": made["id"]})
    voided = client.post(f"{API}/adjustments/{made['id']}/void", headers=ME, json={"reason": "estimate was wrong"})
    assert voided.status_code == 200 and voided.json()["status"] == "void"
    with pytest.raises(DBAPIError, match="never changes"):
        _rows(app_session, "update business_book_adjustments set status = 'active', voided_at = null, voided_by = null where id = :i", {"i": made["id"]})
    entry = client.post(f"{API}/intercompany", headers=ME, json={"workspace_id": WORKSPACE_A, "kind": "loan", "direction": "receivable", "entry_date": today,
                                                                 "currency": "SAR", "amount": "5000", "description": "Synthetic loan"})
    assert entry.status_code == 201, entry.text
    assert _rows(app_session, "select direction, pair_id from business_book_intercompany", workspace=WORKSPACE_A) == [("payable", entry.json()["pair_id"])]
    refused = client.post(f"{API}/intercompany", headers={**COLLEAGUE, "x-user-roles": "owner"},
                          json={"workspace_id": WORKSPACE_A, "kind": "loan", "direction": "receivable", "entry_date": today, "currency": "SAR",
                                "amount": "1", "description": "Synthetic loan"})
    assert refused.status_code == 403
    assert client.post(f"{API}/intercompany/{entry.json()['id']}/void", headers=ME, json={"reason": "typed in error"}).status_code == 200
    assert _rows(app_session, "select status from business_book_intercompany", workspace=WORKSPACE_A) == [("void",)]
    assert _rows(app_session, "select status from business_book_intercompany") == [("void",)]
