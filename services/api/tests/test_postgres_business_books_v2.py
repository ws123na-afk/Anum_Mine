"""Business books, second release, in PostgreSQL (migration ``0138_books_v2``, docs/business-books.md).

The API runs as the non-owner ``anum_test_app`` role under forced RLS; the daily job discovers work as
``anum_maintenance`` and acts as the application role in each business. Recurring bills are made once
per period even when two runs overlap; the maintenance role reads only scope and schedule columns; a
consolidation reads only businesses the caller manages; fetched rates land in the business's own
books. Vendors and rates are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.business import books_jobs
from anum_api.db import session as db_session
from anum_api.finance import connectors
from anum_api.main import app
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/business/books"
OTHER_BIZ = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
ME = {**OTHER_BIZ, "x-workspace-id": WORKSPACE_A2}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("business_book_bill_templates", "business_book_rate_feeds")


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
          tenant: str = TENANT_A) -> list:
    with app_session(TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="user_test")) as session:
        return list(session.execute(text(sql), params or {}).all())


def _setup(api: TestClient, headers: dict = ME, currency: str = "SAR") -> None:
    response = api.put(f"{API}/settings", headers=headers, json={"base_currency": currency, "version": None})
    assert response.status_code == 200, response.text


def _vendor(api: TestClient, headers: dict = ME) -> str:
    response = api.post(f"{API}/vendors", headers=headers, json={"name": f"Synthetic Landlord {utc_now().timestamp()}", "default_category": "rent"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_new_tables_have_forced_rls_purge_and_narrow_maintenance_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, (table, policies)
        maintenance = set(connection.execute(text("select policyname from pg_policies where 'anum_maintenance' = any(roles)")).scalars())
        assert {"maintenance_business_book_templates_due", "maintenance_business_book_rate_feeds_on"} <= maintenance
        origin = connection.execute(text("select column_default from information_schema.columns where table_name = 'business_book_rates' "
                                         "and column_name = 'origin'")).scalar_one()
        assert "manual" in origin


def test_recurring_bills_are_made_once_per_period_when_two_runs_overlap(client: TestClient, database_engine: Engine,
                                                                         app_session: Callable[..., Iterator[Session]]) -> None:
    _setup(client)
    tomorrow = utc_now().date() + timedelta(days=1)
    made = client.post(f"{API}/recurring", headers=ME, json={"vendor_id": _vendor(client), "description": "Synthetic rent", "currency": "SAR",
                                                            "subtotal": "1000", "tax": "150", "category": "rent", "frequency": "weeks", "interval": 1,
                                                            "start_date": tomorrow.isoformat(), "due_days": 7})
    assert made.status_code == 201 and made.json()["bills"] == [], made.text
    later = utc_now() + timedelta(days=16)
    assert (TENANT_A, WORKSPACE_A2) in books_jobs.discover_templates(later.date())
    assert (TENANT_A, WORKSPACE_A2) not in books_jobs.discover_templates(utc_now().date())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: books_jobs.run(now=later), range(2)))
    assert sum(r["bills_made"] for r in results) == 3 and all(r["failed"] == 0 for r in results), results
    periods = _rows(app_session, "select period_date, count(*) from business_book_bills where template_id = :t group by 1 order by 1", {"t": made.json()["id"]})
    assert [count for _, count in periods] == [1, 1, 1]
    assert books_jobs.run(now=later)["bills_made"] == 0
    detail = client.get(f"{API}/recurring/{made.json()['id']}", headers=COLLEAGUE).json()
    assert detail["created_count"] == 3 and detail["bills_total"] == 3
    # The partial unique index refuses a second bill for the same template and period.
    with pytest.raises(DBAPIError):
        with database_engine.begin() as connection:
            connection.execute(text("insert into business_book_bills (id, tenant_id, workspace_id, vendor_id, description, bill_date, due_date, currency, "
                                    "subtotal, tax, total, category, section, status, created_by, template_id, period_date) select 'bill_dup', tenant_id, "
                                    "workspace_id, vendor_id, description, bill_date, due_date, currency, subtotal, tax, total, category, section, status, "
                                    "created_by, template_id, period_date from business_book_bills where template_id = :t limit 1"), {"t": made.json()["id"]})
    # Another business and another tenant see none of it.
    assert _rows(app_session, "select count(*) from business_book_bill_templates", workspace=WORKSPACE_A) == [(0,)]
    assert _rows(app_session, "select count(*) from business_book_bills", workspace=WORKSPACE_B, tenant=TENANT_B) == [(0,)]


def test_the_maintenance_role_reads_only_scope_and_schedule_columns(client: TestClient, database_engine: Engine) -> None:
    _setup(client)
    assert client.post(f"{API}/recurring", headers=ME, json={"vendor_id": _vendor(client), "description": "Synthetic rent", "currency": "SAR",
                                                             "subtotal": "10", "category": "rent", "frequency": "monthly",
                                                             "start_date": (utc_now().date() + timedelta(days=3)).isoformat()}).status_code == 201
    assert client.put(f"{API}/rates/auto", headers=ME, json={"enabled": True, "currencies": ["USD"], "version": None}).status_code == 200
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert connection.execute(text("select count(*) from business_book_bill_templates where next_date is not null")).scalar_one() == 1
            assert connection.execute(text("select count(*) from business_book_rate_feeds where enabled")).scalar_one() == 1
        for sql in ("select description from business_book_bill_templates", "select subtotal from business_book_bill_templates",
                    "select currencies from business_book_rate_feeds", "select id from business_book_bills",
                    "update business_book_bill_templates set status = 'ended'"):
            with pytest.raises(DBAPIError):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(sql))


def test_fetched_rates_land_in_the_business_books_and_a_typed_rate_replaces_one(client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                                                               app_session: Callable[..., Iterator[Session]]) -> None:
    today = utc_now().date()
    xml = (f"<Cube><Cube time='{today.isoformat()}'><Cube currency='USD' rate='1.1000'/><Cube currency='JPY' rate='160.00'/>"
           "<Cube currency='GBP' rate='0.8500'/><Cube currency='INR' rate='92.000'/><Cube currency='CHF' rate='0.9500'/></Cube></Cube>")
    monkeypatch.setattr(connectors, "finance_transport", httpx.MockTransport(lambda request: httpx.Response(200, text=xml)))
    _setup(client, currency="AED")
    assert client.put(f"{API}/rates/auto", headers=ME, json={"enabled": True, "currencies": ["USD", "SAR"], "version": None}).status_code == 200
    result = books_jobs.run()
    assert result["rates_added"] == 2 and result["failed"] == 0, result
    rows = _rows(app_session, "select base, quote, origin, source, created_by from business_book_rates order by quote")
    assert [(r[0], r[1], r[2]) for r in rows] == [("EUR", "AED", "ecb"), ("EUR", "USD", "ecb")] and rows[1][3] == "ECB" and rows[0][4] == "system:books"
    feed = client.get(f"{API}/rates/auto", headers=COLLEAGUE).json()
    assert feed["unavailable"] == ["SAR"] and feed["last_as_of"] == today.isoformat()
    typed = client.post(f"{API}/rates", headers=ME, json={"base": "EUR", "quote": "USD", "rate": "1.2", "as_of": today.isoformat(), "source": "Synthetic"})
    assert typed.status_code == 201, typed.text
    assert [(r[0], float(r[1])) for r in _rows(app_session, "select origin, rate from business_book_rates where quote = 'USD'")] == [("manual", 1.2)]
    assert _rows(app_session, "select count(*) from business_book_rates", workspace=WORKSPACE_A) == [(0,)]


def test_the_group_reads_only_businesses_the_caller_owns(client: TestClient) -> None:
    _setup(client)
    _setup(client, OTHER_BIZ, currency="SAR")
    entry = client.post(f"{API}/intercompany", headers=ME, json={"workspace_id": WORKSPACE_A, "kind": "recharge", "direction": "receivable",
                                                                "entry_date": utc_now().date().isoformat(), "currency": "SAR", "amount": "700",
                                                                "description": "Synthetic shared office"})
    assert entry.status_code == 201, entry.text
    data = client.get(f"{API}/group", headers=ME)
    assert data.status_code == 200 and data.json()["enough"] is True, data.text
    other_income = next(row for row in data.json()["pnl"] if row["key"] == "other_income")
    costs = next(row for row in data.json()["pnl"] if row["key"] == "operating_expenses")
    assert other_income["businesses"][WORKSPACE_A2] == "700.00" and other_income["total"] == "0.00"
    assert costs["businesses"][WORKSPACE_A] == "700.00" and costs["total"] == "0.00"
    assert client.get(f"{API}/group", headers=COLLEAGUE).status_code == 403
    stranger = {**ME, "x-tenant-id": TENANT_B, "x-workspace-id": WORKSPACE_B}
    asked = client.get(f"{API}/group", headers=stranger, params={"workspace": [WORKSPACE_B, WORKSPACE_A2]})
    assert asked.status_code in (403, 409) and "Synthetic" not in asked.text and "700" not in asked.text


def test_draft_status_is_admitted_and_anything_else_refused(client: TestClient, database_engine: Engine) -> None:
    _setup(client)
    made = client.post(f"{API}/recurring", headers=ME, json={"vendor_id": _vendor(client), "description": "Synthetic rent", "currency": "SAR",
                                                            "subtotal": "10", "category": "rent", "frequency": "monthly", "create_as": "draft",
                                                            "start_date": utc_now().date().isoformat()})
    draft = made.json()["bills"][0]
    assert draft["status"] == "draft"
    approved = client.post(f"{API}/bills/{draft['id']}/approve", headers=COLLEAGUE, json={"version": draft["version"]})
    assert approved.status_code == 200 and approved.json()["status"] == "open"
    with pytest.raises(DBAPIError):
        with database_engine.begin() as connection:
            connection.execute(text("update business_book_bills set status = 'pending' where id = :id"), {"id": draft["id"]})
