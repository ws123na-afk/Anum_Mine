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


# -- stages 3 to 5 (migration 0057_finance_plan_guard_conn) ------------------------------------

NEW_TABLES = ("finance_plans", "finance_guardian_versions", "finance_guardian_changes", "finance_connections", "finance_fx_rates")


def test_every_new_finance_table_has_forced_rls_and_an_owner_only_restrictive_policy(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in NEW_TABLES:
            forced = connection.execute(
                text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}
            ).scalar_one()
            assert forced, table
            policies = dict(
                connection.execute(text("select policyname, permissive from pg_policies where tablename = :t"), {"t": table}).all()
            )
            assert policies.get(f"tenant_isolation_{table}") == "PERMISSIVE"
            assert policies.get(f"owner_only_{table}") == "RESTRICTIVE"


def test_rules_safe_to_spend_and_rates_persist_and_stay_private(
    client: TestClient, app_session: Callable[..., Iterator[Session]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from anum_api.finance import connectors

    client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "opening_balance": "1000"})
    client.post(f"{API}/goals", headers=ME, json={"name": "Car", "target_amount": "900", "monthly_contribution": "300"})
    client.put(f"{API}/plan", headers=ME, json={"buffers": [{"currency": "AED", "amount": "200"}], "combine_currencies": True})
    safe = client.get(f"{API}/safe-to-spend", headers=ME).json()
    assert safe["currencies"][0]["safe_to_spend"] == "500.00"
    assert client.get(f"{API}/safe-to-spend", headers=OWNER).json()["currencies"] == []

    change = client.post(f"{API}/guardian/changes", headers=ME, json={"rules": {"blocked_merchants": ["talabat"]}}).json()
    assert client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=OWNER, json={"payload_hash": change["payload_hash"]}).status_code == 404
    confirmed = client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=ME, json={"payload_hash": change["payload_hash"]})
    assert confirmed.status_code == 200 and confirmed.json()["version"] == 1
    assert client.get(f"{API}/guardian", headers=OWNER).json()["version"] == 0
    assert _count(app_session, "finance_guardian_versions", "user_test") == 1
    assert _count(app_session, "finance_guardian_versions", "user_owner") == 0

    today = client.get(f"{API}/summary", headers=ME).json()["today"]
    xml = (
        f"<Cube><Cube time='{today}'><Cube currency='USD' rate='1.1000'/><Cube currency='JPY' rate='160.00'/>"
        "<Cube currency='GBP' rate='0.8500'/><Cube currency='INR' rate='92.000'/><Cube currency='CHF' rate='0.9500'/></Cube></Cube>"
    )
    monkeypatch.setattr(connectors, "finance_transport", httpx.MockTransport(lambda request: httpx.Response(200, text=xml)))
    connection = client.post(f"{API}/connections", headers=ME, json={"provider": "ecb_fx"}).json()
    report = client.post(f"{API}/connections/{connection['id']}/sync", headers=ME).json()
    assert report["status"] == "ok" and report["counts"]["rates_new"] == 6
    assert _count(app_session, "finance_fx_rates", "user_test") == 6
    assert _count(app_session, "finance_fx_rates", None) == 0
    assert client.get(f"{API}/fx-rates", headers=A2).json() == []
    assert client.delete(f"{API}/connections/{connection['id']}", headers=ME).status_code == 204
    assert _count(app_session, "finance_fx_rates", "user_test") == 0


def test_the_database_refuses_write_scopes_kept_credentials_and_loosening_agents(
    app_session: Callable[..., Iterator[Session]], seed_scopes: None
) -> None:
    insert = (
        "insert into finance_connections (id, tenant_id, workspace_id, owner_user_id, provider, kind, label, status, scopes, credentials_encrypted) "
        "values (:id, :t, :w, 'user_test', 'x', 'bank', 'x', :status, cast(:scopes as jsonb), :creds)"
    )
    for scopes, status, creds in (('["orders:write"]', "active", None), ('["balances:read", "transfers"]', "active", None),
                                  ("[]", "active", None), ('["balances:read"]', "disconnected", "ciphertext")):
        with pytest.raises(DBAPIError):
            with app_session(tenant_context()) as session:
                session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
                session.execute(text(insert), {"id": "c1", "t": TENANT_A, "w": WORKSPACE_A, "status": status, "scopes": scopes, "creds": creds})
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(
                text(
                    "insert into finance_guardian_changes (id, tenant_id, workspace_id, owner_user_id, base_version, rules, proposed_by, "
                    "proposer, payload_hash, loosens, status, expires_at) values ('g1', :t, :w, 'user_test', 0, '{}', 'agent', 'assistant', "
                    ":h, '[\"max_single\"]', 'pending', now())"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A, "h": "a" * 64},
            )


def test_maintenance_role_finds_only_due_connections_and_the_scheduler_syncs_on_postgres(
    client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import httpx

    from anum_api.finance import connectors
    from anum_api.finance.sync import _DUE, run_once

    due = client.post(f"{API}/connections", headers=ME, json={"provider": "ecb_fx"}).json()
    later = client.post(f"{API}/connections", headers=headers("user_other"), json={"provider": "ecb_fx"}).json()
    with database_engine.begin() as connection:
        connection.execute(text("update finance_connections set next_sync_at = now() + interval '1 hour' where id = :id"), {"id": later["id"]})
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            found = [tuple(row) for row in connection.execute(_DUE, {"limit": 10})]
        assert found == [(TENANT_A, WORKSPACE_A, "user_test", due["id"])]
        for statement in (
            "select credentials_encrypted from finance_connections",
            "select label from finance_connections",
            "select id from finance_fx_rates",
            "update finance_connections set next_sync_at = now()",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with connection.begin():
                    connection.execute(text("set local role anum_maintenance"))
                    connection.execute(text(statement))
    today = client.get(f"{API}/summary", headers=ME).json()["today"]
    xml = (
        f"<Cube><Cube time='{today}'><Cube currency='USD' rate='1.1000'/><Cube currency='JPY' rate='160.00'/>"
        "<Cube currency='GBP' rate='0.8500'/><Cube currency='INR' rate='92.000'/><Cube currency='CHF' rate='0.9500'/></Cube></Cube>"
    )
    monkeypatch.setattr(connectors, "finance_transport", httpx.MockTransport(lambda request: httpx.Response(200, text=xml)))
    with database_engine.begin() as connection:
        connection.execute(text(f"grant anum_maintenance to {APP_ROLE} with inherit false, set true"))
    try:
        assert asyncio.run(run_once()) == 1
        assert asyncio.run(run_once()) == 0
    finally:
        with database_engine.begin() as connection:
            connection.execute(text(f"revoke anum_maintenance from {APP_ROLE}"))
    assert len(client.get(f"{API}/fx-rates", headers=ME).json()) == 6
    assert client.get(f"{API}/fx-rates", headers=headers("user_other")).json() == []
