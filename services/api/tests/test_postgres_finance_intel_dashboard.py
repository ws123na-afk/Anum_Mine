"""The read-only trading intelligence dashboard in PostgreSQL (docs/finance-trading-intel.md#dashboard).

The API runs as the non-owner ``anum_test_app`` role, so forced RLS decides what the overview
reads: the owner's SIMULATED portfolio, nobody else's in the workspace, the tenant or another one.
"""

from __future__ import annotations

from collections.abc import Iterator

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


def test_overview_reads_only_the_owners_simulated_book_under_rls(client: TestClient) -> None:
    today = client.get(f"{API}/summary", headers=ME).json()["today"]
    portfolio = client.post(f"{API}/paper/portfolios", headers=ME, json={"name": "Practice", "starting_cash": [{"currency": "USD", "amount": "5000"}]}).json()
    assert client.post(f"{API}/prices", headers=ME, json={"symbol": "EXMPL", "currency": "USD", "price": "100", "as_of": today}).status_code == 201
    intent = client.post(f"{API}/execution/intents", headers=ME, json={
        "mode": "paper", "portfolio_id": portfolio["id"], "symbol": "EXMPL", "currency": "USD", "side": "buy", "quantity": "10",
    }).json()
    assert client.post(f"{API}/execution/intents/{intent['id']}/approve", headers=ME, json={"payload_hash": intent["payload_hash"]}).status_code == 200

    mine = client.get(f"{API}/intel/overview", headers=ME)
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["simulation"]["live_enabled"] is False
    [item] = body["portfolios"]
    assert item["simulated"] is True and item["holdings"][0]["symbol"] == "EXMPL" and item["holdings"][0]["quantity"] == "10.00000000"
    for other in (OWNER, A2, B):
        assert client.get(f"{API}/intel/overview", headers=other).json()["portfolios"] == []
    assert client.get(f"{API}/intel/strategies", headers=ME).json() == {"items": [], "total": 0, "limit": 20, "offset": 0}
    assert {c["state"]: c["count"] for c in body["strategies"]}["PAPER_ACTIVE"] == 0
    for other in (OWNER, B):
        assert client.get(f"{API}/intel/decisions", headers=other).json()["total"] == 0
