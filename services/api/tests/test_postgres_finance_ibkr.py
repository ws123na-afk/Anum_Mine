"""IBKR trading in PostgreSQL (migration ``0122_finance_ibkr_live``, docs/finance-live-trading.md#storage).

The API runs as the non-owner ``anum_test_app`` role. Every IBKR row is visible only to its owner
inside its tenant and workspace; the database refuses live intents outside the IBKR shape, a
second active connection, kept secrets on a revoked connection and impossible fills.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A, tenant_context
from fastapi.testclient import TestClient
from ibkr_fake import FakeIbkr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from test_finance_ibkr import NOW, Clock

from anum_api.db import session as db_session
from anum_api.finance import ibkr_api
from anum_api.finance import ibkr_service as svc
from anum_api.finance.ibkr_store import ibkr_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance/ibkr"
TABLES = ("finance_broker_settings", "finance_broker_connections", "finance_broker_orders", "finance_broker_events")
PIN = "ab" * 32


def headers(user_id: str = "user_test", role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": user_id, "x-user-roles": role}


ME = headers()
SOMEONE = headers("user_owner")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, FakeIbkr]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    fake = FakeIbkr(now_ts=NOW.timestamp())
    monkeypatch.setattr(svc, "TRANSPORT", fake)
    monkeypatch.setattr(ibkr_api, "utc_now", Clock())
    finance_store.clear()
    ibkr_memory_store.clear()
    yield TestClient(app), fake
    finance_store.clear()
    ibkr_memory_store.clear()


def _count(app_session: Callable[..., Iterator[Session]], table: str, user_id: str) -> int:
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user_id})
        return int(session.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608 - fixed table names


def test_every_ibkr_table_has_forced_rls_an_owner_policy_and_a_purge_policy(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"owner_only_{table}", f"lifecycle_purge_{table}"} <= policies


def test_the_paper_order_path_persists_and_stays_with_its_owner(client: tuple[TestClient, FakeIbkr],
                                                                app_session: Callable[..., Iterator[Session]]) -> None:
    api, fake = client
    body = {"environment": "paper", "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN, "account_id": "DU1234567", "cash_account_confirmed": True}
    assert api.post(f"{API}/connections", json=body, headers=ME).status_code == 201
    assert api.post(f"{API}/symbols", json={"symbol": "SPY", "confirm_not_screened": True}, headers=ME).status_code == 200
    assert api.post(f"{API}/sync?environment=paper", headers=ME).json()["reconciliation"] == "ok"
    intent = api.post(f"{API}/orders", json={"symbol": "SPY", "side": "buy", "quantity": "1", "limit_price": "9.5"}, headers=ME).json()
    assert api.post(f"{API}/intents/{intent['id']}/approve", json={"payload_hash": "f" * 64}, headers=ME).status_code == 409
    sent = api.post(f"{API}/intents/{intent['id']}/approve", json={"payload_hash": intent["payload_hash"]}, headers=ME)
    assert sent.status_code == 200, sent.text
    assert api.post(f"{API}/intents/{intent['id']}/approve", json={"payload_hash": intent["payload_hash"]}, headers=ME).status_code == 409
    assert len(fake.placed) == 1
    for table in TABLES:
        assert _count(app_session, table, "user_test") >= 1 and _count(app_session, table, "user_owner") == 0
    assert api.get(f"{API}/orders", headers=SOMEONE).json()["total"] == 0
    assert api.post(f"{API}/intents/{intent['id']}/approve", json={"payload_hash": intent["payload_hash"]}, headers=SOMEONE).status_code == 404
    # The secret is ciphertext in the database and is erased on disconnect.
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        secret = session.execute(text("select secret from finance_broker_connections")).scalar_one()
    assert secret and "DU1234567" not in secret
    connection_id = api.get(f"{API}/overview", headers=ME).json()["connections"][0]["id"]
    assert api.delete(f"{API}/connections/{connection_id}", headers=ME).status_code == 200
    with app_session(tenant_context()) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        assert session.execute(text("select secret from finance_broker_connections")).scalar_one() == ""
    # Deleting my data deletes my IBKR rows too.
    assert api.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"}).status_code == 200
    for table in TABLES:
        assert _count(app_session, table, "user_test") == 0


INTENT = (
    "insert into finance_exec_intents (id, tenant_id, workspace_id, owner_user_id, mode, kind, requested_by, proposer, idempotency_key, payload_hash, "
    "status, expires_at, approved_at, approved_by, order_type, instrument_kind, currency, side, account_id, amount) values (:id, :t, :w, 'user_test', "
    "'live', 'trade', :by, 'x', :id, :h, 'executed', now(), now(), :approver, :ot, 'security', :cur, 'buy', :acct, :amount)"
)
GOOD = {"by": "person", "approver": "user_test", "ot": "limit", "cur": "USD", "acct": "ibkr:conn", "amount": 10}


@pytest.mark.parametrize("change", [
    {},  # the IBKR shape runs
    {"ot": "market"}, {"cur": "AED"}, {"acct": "alpaca:conn"}, {"amount": 1001}, {"amount": None}, {"by": "agent"}, {"approver": "someone_else"},
])
def test_the_database_lets_only_a_bounded_ibkr_limit_order_run_live(app_session: Callable[..., Iterator[Session]], seed_scopes: None,
                                                                    change: dict) -> None:
    values = {**GOOD, **change, "id": "live1", "t": TENANT_A, "w": WORKSPACE_A, "h": "a" * 64}
    if not change:
        with app_session(tenant_context(), commit=True) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(text(INTENT), values)
        return
    with pytest.raises(DBAPIError):
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(text(INTENT), values)


def test_the_database_keeps_one_active_connection_and_sane_orders(app_session: Callable[..., Iterator[Session]], seed_scopes: None) -> None:
    connection = ("insert into finance_broker_connections (id, tenant_id, workspace_id, owner_user_id, environment, gateway_host, secret, hint, status) "
                  "values (:id, :t, :w, 'user_test', 'paper', :host, :secret, 'DU...567', :status)")
    with app_session(tenant_context(), commit=True) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        session.execute(text(connection), {"id": "c1", "t": TENANT_A, "w": WORKSPACE_A, "host": "ibkr-gateway", "secret": "x", "status": "active"})
    bad = (
        {"id": "c2", "host": "ibkr-gateway", "secret": "x", "status": "active"},  # a second active paper connection
        {"id": "c3", "host": "evil.example.com", "secret": "x", "status": "revoked"},  # host not allowed
        {"id": "c4", "host": "ibkr-gateway", "secret": "x", "status": "revoked"},  # a revoked one keeps no secret
    )
    for row in bad:
        with pytest.raises(DBAPIError):
            with app_session(tenant_context()) as session:
                session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
                session.execute(text(connection), {**row, "t": TENANT_A, "w": WORKSPACE_A})
    order = ("insert into finance_broker_orders (id, tenant_id, workspace_id, owner_user_id, connection_id, environment, symbol, conid, side, quantity, "
             "limit_price, state, filled_quantity) values ('o1', :t, :w, 'user_test', 'c1', 'paper', 'SPY', 1, :side, 1, 9.5, 'submitted', :filled)")
    for side, filled in (("short", 0), ("buy", 2)):
        with pytest.raises(DBAPIError):
            with app_session(tenant_context()) as session:
                session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
                session.execute(text(order), {"t": TENANT_A, "w": WORKSPACE_A, "side": side, "filled": filled})
    with pytest.raises(DBAPIError):  # someone else's row cannot be written under my session
        with app_session(tenant_context()) as session:
            session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
            session.execute(text(connection.replace("'user_test'", "'user_owner'")),
                            {"id": "c5", "t": TENANT_A, "w": WORKSPACE_A, "host": "ibkr-gateway", "secret": "x", "status": "active"})
