"""Personal finance in PostgreSQL (migration 0056_finance_ledger, docs/finance.md).

The API runs as the non-owner ``anum_test_app`` role. Every finance row is visible and
writable only to its owner (``owner_user_id = anum.user_id``, a restrictive policy) inside
its own tenant and workspace. Counts below run with no predicate, so only RLS decides.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B, tenant_context
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
TABLES = (
    "finance_settings", "finance_accounts", "finance_imports", "finance_transactions",
    "finance_bills", "finance_debts", "finance_goals", "finance_holdings",
)


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


ME = headers()
OWNER = headers("user_owner", role="owner")
A2 = headers(workspace_id=WORKSPACE_A2)
B = headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)
STATEMENT = "Date,Description,Amount\n2026-10-01,CARREFOUR,-120.50\n2026-10-02,SALARY,15000\n2026-10-02,SALARY,15000\n"


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    finance_store.clear()
    yield TestClient(app)
    finance_store.clear()


def _count(app_session: Callable[..., Iterator[Session]], table: str, user_id: str | None,
           tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> int:
    with app_session(tenant_context(tenant_id, workspace_id)) as session:
        if user_id is not None:
            session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user_id})
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608


def test_every_finance_table_has_forced_rls_and_an_owner_only_restrictive_policy(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(
                text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}
            ).scalar_one()
            assert forced, table
            policies = dict(
                connection.execute(text("select policyname, permissive from pg_policies where tablename = :t"), {"t": table}).all()
            )
            assert policies.get(f"tenant_isolation_{table}") == "PERMISSIVE"
            assert policies.get(f"owner_only_{table}") == "RESTRICTIVE"


def test_ledger_persists_and_only_its_owner_sees_it(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    account = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "opening_balance": "100.10"})
    assert account.status_code == 201, account.text
    account_id = account.json()["id"]
    imported = client.post(f"{API}/imports", headers=ME, json={"account_id": account_id, "content": STATEMENT})
    assert imported.status_code == 201, imported.text
    assert imported.json()["imported_count"] == 3
    again = client.post(f"{API}/imports", headers=ME, json={"account_id": account_id, "content": STATEMENT}).json()
    assert (again["imported_count"], again["duplicate_count"]) == (0, 3)
    client.post(f"{API}/bills", headers=ME, json={"name": "DEWA", "amount": "450", "next_due_on": "2026-10-20"})
    client.put(f"{API}/settings", headers=ME, json={"base_currency": "AED", "timezone": "Asia/Dubai", "category_rules": []})

    summary = client.get(f"{API}/summary", headers=ME, params={"month": "2026-10"}).json()
    assert summary["accounts"][0]["balance"] == "29979.60"  # exact numeric, no float drift
    assert summary["spend"] == [{"currency": "AED", "amount": "120.50"}]
    page = client.get(f"{API}/transactions", headers=ME, params={"q": "carre"}).json()
    assert page["total"] == 1 and page["items"][0]["amount"] == "-120.50"

    # Workspace owners, other workspaces and other tenants see nothing.
    assert client.get(f"{API}/accounts", headers=OWNER).json() == []
    assert client.get(f"{API}/accounts/{account_id}", headers=OWNER).status_code == 404
    assert client.get(f"{API}/accounts", headers=A2).json() == []
    assert client.get(f"{API}/accounts", headers=B).json() == []

    assert _count(app_session, "finance_transactions", "user_test") == 3
    assert _count(app_session, "finance_transactions", "user_owner") == 0
    assert _count(app_session, "finance_transactions", None) == 0
    assert _count(app_session, "finance_accounts", "user_test", TENANT_B, WORKSPACE_B) == 0
    assert _count(app_session, "finance_settings", "user_test") == 1

    # Deleting the account takes its transactions and imports; the bill stays.
    assert client.delete(f"{API}/accounts/{account_id}", headers=ME).status_code == 204
    assert _count(app_session, "finance_transactions", "user_test") == 0
    assert _count(app_session, "finance_imports", "user_test") == 0
    assert _count(app_session, "finance_bills", "user_test") == 1


def test_rows_cannot_be_written_for_someone_else(app_session: Callable[..., Iterator[Session]], seed_scopes: None) -> None:
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(
                text(
                    "insert into finance_accounts (id, tenant_id, workspace_id, owner_user_id, name, kind, currency) "
                    "values ('a1', :t, :w, 'user_other', 'x', 'bank', 'AED')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(
                text(
                    "insert into finance_accounts (id, tenant_id, workspace_id, owner_user_id, name, kind, currency) "
                    "values ('a2', :t, :w, 'user_test', 'x', 'bank', 'AED')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(
                text(
                    "insert into finance_accounts (id, tenant_id, workspace_id, owner_user_id, name, kind, currency, last4) "
                    "values ('a3', :t, :w, 'user_test', 'x', 'card', 'AED', '41111111')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )


def test_privacy_counts_exports_and_deletes_the_ledger(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    account_id = client.post(f"{API}/accounts", headers=ME, json={"name": "Cash", "kind": "cash"}).json()["id"]
    client.post(f"{API}/transactions", headers=ME, json={"account_id": account_id, "posted_on": "2026-10-01", "amount": "-5", "description": "KIOSK"})
    assert client.get("/api/v1/privacy", headers=ME).json()["private"]["finance_records"] == 2
    exported = client.get("/api/v1/privacy/export", headers=ME).json()["finance"]
    assert [t["description"] for t in exported["transactions"]] == ["KIOSK"]
    client.post(f"{API}/accounts", headers=OWNER, json={"name": "Owner's", "kind": "cash"})
    deleted = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"})
    assert deleted.status_code == 200, deleted.text
    assert _count(app_session, "finance_accounts", "user_test") == 0
    assert _count(app_session, "finance_accounts", "user_owner") == 1
