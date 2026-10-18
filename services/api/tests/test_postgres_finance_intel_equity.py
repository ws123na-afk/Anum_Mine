"""SIMULATED P&L and drawdown over time in PostgreSQL (docs/finance-trading-intel.md#pl-and-drawdown-over-time).

The API runs as the non-owner ``anum_test_app`` role, so forced RLS decides which orders and prices
the curve is rebuilt from: only the owner's, never another person's in the workspace or another
tenant's, even when they know the portfolio id.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


ME, OWNER = headers(), headers("user_owner", role="owner")
A2, B = headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)


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


def _book(client: TestClient, who: dict[str, str], price: str) -> str:
    today = client.get(f"{API}/summary", headers=who).json()["today"]
    portfolio = client.post(f"{API}/paper/portfolios", headers=who, json={"name": "Practice", "starting_cash": [{"currency": "USD", "amount": "5000"}]}).json()
    assert client.post(f"{API}/prices", headers=who, json={"symbol": "EXMPL", "currency": "USD", "price": price, "as_of": today}).status_code == 201
    intent = client.post(f"{API}/execution/intents", headers=who, json={
        "mode": "paper", "portfolio_id": portfolio["id"], "symbol": "EXMPL", "currency": "USD", "side": "buy", "quantity": "10",
    }).json()
    assert client.post(f"{API}/execution/intents/{intent['id']}/approve", headers=who, json={"payload_hash": intent["payload_hash"]}).status_code == 200
    return portfolio["id"]


def test_equity_curve_reads_only_the_owners_orders_and_prices_under_rls(client: TestClient) -> None:
    mine = _book(client, ME, "100")
    theirs = _book(client, B, "300")  # another tenant prices the same symbol differently
    response = client.get(f"{API}/intel/portfolios/{mine}/equity", headers=ME)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulated"] is True and body["orders"] == 1
    [usd] = body["series"]
    [point] = usd["points"]
    # 10 at 100 plus 5 bps slippage and the 10 bps fee: valued at ME's 100, not at tenant B's 300.
    overview = client.get(f"{API}/intel/overview", headers=ME).json()["portfolios"][0]
    assert Decimal(point["value"]) == Decimal(overview["cash"][0]["amount"]) + Decimal(overview["holdings"][0]["market_value"])
    assert Decimal(point["pnl"]) < 0
    for other in (OWNER, A2, B):
        assert client.get(f"{API}/intel/portfolios/{mine}/equity", headers=other).status_code == 404
    assert client.get(f"{API}/intel/portfolios/{theirs}/equity", headers=ME).status_code == 404
