"""Recurring invoices, the planned emails' switches and customer replies in the portal, in PostgreSQL (migration
``0175_business_depth``, docs/business.md#recurring-invoices, docs/business-portal.md#replying-inside-a-request).

The API runs as the non-owner ``anum_test_app`` role under forced RLS; the scheduler pass discovers work as
``anum_maintenance`` (scope, status and switches only) and acts as the application role in each workspace. Two passes
that overlap draft each period once; another workspace and tenant see nothing. Every name is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.business import invoice_jobs
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/business"
PORTAL = "/api/v1/portal"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A2, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("business_invoice_schedules", "business_invoice_automation", "business_ticket_messages")


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


def _ok(response, code: int = 200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json()


def _account(api: TestClient) -> dict:
    account = _ok(api.post(f"{API}/accounts", headers=ME, json={"name": f"Synthetic Client {uuid4().hex[:6]}"}), 201)
    with_contact = _ok(api.post(f"{API}/accounts/{account['id']}/contacts", headers=ME,
                                json={"name": "Sara Example", "email": "sara@client.example", "consent_basis": "inbound_inquiry"}), 201)
    return {**account, "contact_id": with_contact["contacts"][0]["id"]}


def test_new_tables_have_forced_rls_purge_and_narrow_maintenance_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, (table, policies)
        maintenance = set(connection.execute(text("select policyname from pg_policies where 'anum_maintenance' = any(roles)")).scalars())
        assert {"maintenance_business_invoice_schedules_due", "maintenance_business_invoice_automation_on"} <= maintenance


def test_a_schedule_drafts_each_period_once_when_two_passes_overlap_and_asks_the_owner(client: TestClient, database_engine: Engine,
                                                                                      app_session: Callable[..., Iterator[Session]]) -> None:
    _ok(client.put(f"{API}/billing-profile", headers=ME, json={"legal_name": "Synthetic Seller LLC", "invoice_prefix": "INV"}))
    account = _account(client)
    today = utc_now().date()
    made = _ok(client.post(f"{API}/invoice-schedules", headers=COLLEAGUE, json={
        "account_id": account["id"], "currency": "SAR", "frequency": "weeks", "interval": 1, "start_date": (today - timedelta(days=15)).isoformat(),
        "issue_mode": "request_approval", "lines": [{"description": "Synthetic retainer", "unit_price": 500}]}), 201)
    assert (TENANT_A, WORKSPACE_A2) in invoice_jobs.discover_schedules(today)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoice_jobs.run(), range(2)))
    assert sum(r["invoices_drafted"] for r in results) == 3 and all(r["failed"] == 0 for r in results), results
    assert sum(r["sent_for_approval"] for r in results) == 3
    periods = _rows(app_session, "select period_date, count(*), min(status) from business_invoices where schedule_id = :s group by 1 order by 1", {"s": made["id"]})
    assert [(count, state) for _, count, state in periods] == [(1, "pending_approval")] * 3
    assert invoice_jobs.run()["invoices_drafted"] == 0
    detail = _ok(client.get(f"{API}/invoice-schedules/{made['id']}", headers=COLLEAGUE))
    assert detail["created_count"] == 3 and detail["invoices_total"] == 3
    approvals = _rows(app_session, "select count(*) from approvals where status = 'pending'")
    assert approvals[0][0] >= 3
    with pytest.raises(DBAPIError):
        with database_engine.begin() as connection:
            connection.execute(text("insert into business_invoices (id, tenant_id, workspace_id, account_id, status, currency, lines, subtotal, tax, total, "
                                    "amount_paid, amount_credited, due_in_days, customer_reference, notes, payment_terms, bill_to_name, bill_to_address, "
                                    "bill_to_email, bill_to_tax_number, marked_milestones, decision_note, void_reason, created_by, created_at, updated_at, "
                                    "schedule_id, period_date) select 'binv_dup', tenant_id, workspace_id, account_id, 'draft', currency, lines, subtotal, tax, "
                                    "total, amount_paid, amount_credited, due_in_days, customer_reference, notes, payment_terms, bill_to_name, bill_to_address, "
                                    "bill_to_email, bill_to_tax_number, marked_milestones, decision_note, void_reason, created_by, created_at, updated_at, "
                                    "schedule_id, period_date from business_invoices where schedule_id = :s limit 1"), {"s": made["id"]})
    assert _rows(app_session, "select count(*) from business_invoice_schedules", workspace=WORKSPACE_A) == [(0,)]
    assert _rows(app_session, "select count(*) from business_invoice_schedules", workspace=WORKSPACE_B, tenant=TENANT_B) == [(0,)]


def test_the_maintenance_role_reads_only_scope_status_and_switches(client: TestClient, database_engine: Engine) -> None:
    account = _account(client)
    _ok(client.post(f"{API}/invoice-schedules", headers=ME, json={
        "account_id": account["id"], "currency": "SAR", "frequency": "monthly", "start_date": utc_now().date().isoformat(),
        "lines": [{"description": "Synthetic retainer", "unit_price": 500}]}), 201)
    _ok(client.put(f"{API}/invoice-automation", headers=ME, json={"auto_reminders": True, "email_scheduled": False}))
    assert client.put(f"{API}/invoice-automation", headers=COLLEAGUE, json={"auto_reminders": False, "email_scheduled": False}).status_code == 403
    assert (TENANT_A, WORKSPACE_A2) in invoice_jobs.discover_automation(utc_now().date())
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert connection.execute(text("select count(*) from business_invoice_schedules where next_date is not null")).scalar_one() == 1
            assert connection.execute(text("select count(*) from business_invoice_automation where auto_reminders")).scalar_one() == 1
        for sql in ("select lines from business_invoice_schedules", "select account_id from business_invoice_schedules",
                    "select language from business_invoice_automation", "select body from business_ticket_messages",
                    "update business_invoice_schedules set status = 'ended'"):
            with pytest.raises(DBAPIError):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(sql))


def test_customer_replies_and_answers_stay_in_their_workspace(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    account = _account(client)
    link = _ok(client.post(f"{API}/portal/access", headers=ME, json={"account_id": account["id"], "contact_id": account["contact_id"]}), 201)
    bearer = {"authorization": f"Bearer {link['token']}"}
    ticket = _ok(client.post(f"{PORTAL}/tickets", headers=bearer, json={"title": "Synthetic question", "description": "Totals differ."}), 201)
    key = uuid4().hex
    _ok(client.post(f"{PORTAL}/tickets/{ticket['id']}/messages", headers=bearer, json={"body": "Synthetic detail", "idempotency_key": key}), 201)
    again = client.post(f"{PORTAL}/tickets/{ticket['id']}/messages", headers=bearer, json={"body": "Synthetic detail", "idempotency_key": key})
    assert again.status_code == 200 and len(again.json()["messages"]) == 1
    answered = _ok(client.post(f"{API}/support/tickets/{ticket['id']}/portal-messages", headers=COLLEAGUE, json={"body": "Synthetic answer"}), 201)
    assert [m["author"] for m in answered["portal_messages"]] == ["customer", "staff"] and answered["portal_unread"] == 0
    shown = _ok(client.get(f"{PORTAL}/tickets/{ticket['id']}", headers=bearer))
    assert [m["author"] for m in shown["messages"]] == ["you", "business"]
    assert _rows(app_session, "select count(*) from business_ticket_messages") == [(2,)]
    assert _rows(app_session, "select count(*) from business_ticket_messages", workspace=WORKSPACE_A) == [(0,)]
    assert _rows(app_session, "select count(*) from business_ticket_messages", workspace=WORKSPACE_B, tenant=TENANT_B) == [(0,)]
    other = {**ME, "x-workspace-id": WORKSPACE_A}
    assert client.post(f"{API}/support/tickets/{ticket['id']}/portal-messages", headers=other, json={"body": "x"}).status_code == 404
