"""The Money advisor in PostgreSQL (migration ``0143_finance_money_advisor``, docs/finance.md#money-advisor).

The API runs as the non-owner ``anum_test_app`` role. A weekly note and the switches are visible only to
their owner in their tenant and workspace; the daily job finds people through ``anum_maintenance``, which
reads the scope columns of active finance accounts and nothing else; the job writes one note and one
notification per person per week, never an email, even when it runs twice. The ledger is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance import advisor_jobs as J
from anum_api.finance.advisor_store import advisor_memory_store, open_advisor_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store, open_finance_store
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
ADVISOR = f"{API}/advisor"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("finance_advisor_reports", "finance_advisor_settings")
SUNDAY = datetime(2026, 11, 8, 6, 0, tzinfo=UTC)  # 10:00 on Sunday in Asia/Dubai


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
    for store in (finance_store, sms_memory_store, advisor_memory_store):
        store.clear()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store, advisor_memory_store):
        store.clear()


def _rows(app_session: Callable[..., Iterator[Session]], user: str, sql: str) -> list:
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        return list(session.execute(text(sql)).all())


def _ledger(api: TestClient, headers: dict = ME) -> None:
    account = api.post(f"{API}/accounts", headers=headers, json={"name": "Current", "kind": "bank", "currency": "AED", "opening_balance": "20000"})
    assert account.status_code == 201, account.text
    day = date(2026, 7, 2)
    while day < date(2026, 11, 8):
        for amount, text_, category in (("-1500", "CARREFOUR", "groceries"), ("-4000", "RENT EJARI", "housing"), ("12000", "SALARY ACME", "income")):
            created = api.post(f"{API}/transactions", headers=headers, json={"account_id": account.json()["id"], "posted_on": day.isoformat(),
                                                                             "amount": amount, "description": text_, "category": category})
            assert created.status_code == 201, created.text
        day = (day.replace(day=1) + timedelta(days=32)).replace(day=2)


def test_the_advisor_tables_have_forced_rls_owner_tenant_and_purge_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies
        rolbypass = connection.execute(text("select rolbypassrls from pg_roles where rolname = 'anum_maintenance'")).scalar_one()
        assert rolbypass is False


def test_notes_and_switches_are_the_owners_alone(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    _ledger(client)
    made = client.post(f"{ADVISOR}/generate", headers=ME)
    assert made.status_code == 201, made.text
    assert made.json()["note"]["currencies"][0]["runway"]["status"] == "ok"
    assert client.post(f"{ADVISOR}/generate", headers=ME).json()["id"] == made.json()["id"]  # this week's note, refreshed
    assert client.put(f"{ADVISOR}/settings", headers=ME, json={"enabled": True, "weekday": 2}).status_code == 200
    assert len(_rows(app_session, "user_test", "select id from finance_advisor_reports")) >= 1
    assert _rows(app_session, "colleague", "select id from finance_advisor_reports") == []
    assert _rows(app_session, "colleague", "select weekday from finance_advisor_settings") == []
    assert client.get(f"{ADVISOR}/notes/{made.json()['id']}", headers=COLLEAGUE).status_code == 404
    assert client.get(ADVISOR, headers=COLLEAGUE).json()["latest"] is None
    # Another person cannot write a row in someone else's name.
    with pytest.raises(DBAPIError):
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="colleague")) as session:
            session.execute(text("select set_config('anum.user_id', 'colleague', true)"))
            session.execute(text("insert into finance_advisor_settings (tenant_id, workspace_id, owner_user_id, enabled, weekday) "
                                 "values (:t, :w, 'user_test', false, 1)"), {"t": TENANT_A, "w": WORKSPACE_A})
    # Delete my data removes the notes and the switches.
    ctx = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test")
    with open_finance_store(ctx) as store:
        assert store.count_everything(ctx) > 0
        from anum_api.privacy import finance_export

        exported = finance_export(store, ctx)
        assert len(exported["advisor_notes"]) == 1 and exported["advisor_settings"]["weekday"] == 2
        store.delete_everything(ctx)
    with open_advisor_store(ctx) as advisor:
        assert advisor.count_everything(ctx) == 0


def test_the_maintenance_role_sees_only_who_has_an_account(client: TestClient, database_engine: Engine) -> None:
    _ledger(client)
    assert (TENANT_A, WORKSPACE_A, "user_test") in J.discover_people()
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(text("select name from finance_accounts"))
            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(text("select note from finance_advisor_reports"))
            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(text("select amount from finance_transactions"))


def test_the_job_writes_one_note_and_one_notification_a_week(client: TestClient, database_engine: Engine) -> None:
    _ledger(client)
    with database_engine.begin() as connection:
        connection.execute(text("delete from user_notifications where kind = 'finance_weekly_advice'"))
    assert J.run(now=SUNDAY - timedelta(days=1))["notes"] == 0
    first = J.run(now=SUNDAY)
    assert first["notes"] == 1 and first["failed"] == 0, first
    assert J.run(now=SUNDAY + timedelta(hours=3))["notes"] == 0
    assert J.run(now=SUNDAY + timedelta(days=2))["notes"] == 0
    with database_engine.connect() as connection:
        notices = connection.execute(text("select email_status, body from user_notifications where kind = 'finance_weekly_advice'")).all()
        reports = connection.execute(text("select week_start, trigger, delivered_at is not null from finance_advisor_reports")).all()
    assert len(notices) == 1 and notices[0][0] == "not_requested" and "never moves money" in notices[0][1]
    assert reports == [(date(2026, 11, 8), "schedule", True)]
    assert J.run(now=SUNDAY + timedelta(days=7))["notes"] == 1
