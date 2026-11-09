"""The statement matching engine in PostgreSQL (migration ``0145_finance_statement_match``, docs/finance.md#statement-matching).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. A statement import stores its balance check
(``finance_imports.reconciliation``) and the accounts overview reads it; an own transfer between two accounts is
paired on import as a ``transfer`` link; another person finds none of it. Every row is SYNTHETIC ("EXAMPLE").
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"


def headers(user_id: str = "user_test", role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": user_id, "x-user-roles": role}


ME = headers()
SOMEONE = headers("user_owner")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    yield TestClient(app)


def _account(client: TestClient, **body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=ME, json={"currency": "SAR", "kind": "bank", **body})
    assert response.status_code == 201, response.text
    return response.json()


def _import(client: TestClient, account_id: str, content: str, **extra: object) -> dict:
    response = client.post(f"{API}/imports", headers=ME, json={"account_id": account_id, "content": content, "filename": "nov.csv", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_balance_check_and_transfer_pair_are_stored_and_private(client: TestClient) -> None:
    bank = _account(client, name="Salary account", last4="5678")
    card = _account(client, name="Card", kind="card", last4="5377")
    done = _import(client, bank["id"], "Date,Description,Amount,Balance\n2026-11-01,EXAMPLE SALARY,2000.00,2000.00\n2026-11-03,TRANSFER TO CARD 5377,-1000.00,1000.00\n",
                   opening_balance="0.00", closing_balance="1000.00")
    assert done["imported_count"] == 2
    paired = _import(client, card["id"], "Date,Description,Amount\n2026-11-05,PAYMENT RECEIVED THANK YOU,1000.00\n")
    assert paired["transfers"] == 1
    rows = client.get(f"{API}/transactions", headers=ME, params={"limit": 50}).json()["items"]
    assert sorted(r["category"] for r in rows if abs(Decimal(r["amount"])) == 1000) == ["transfer", "transfer"]
    overview = client.get(f"{API}/accounts/overview", headers=ME).json()
    mine = next(a for a in overview["accounts"] if a["account_id"] == bank["id"])
    assert mine["last_statement"]["status"] == "tied" and mine["last_reconciled_at"] is not None
    # The same statement again adds nothing.
    again = _import(client, bank["id"], "Date,Description,Amount,Balance\n2026-11-01,EXAMPLE SALARY,2000.00,2000.00\n2026-11-03,TRANSFER TO CARD 5377,-1000.00,1000.00\n")
    assert (again["imported_count"], again["duplicate_count"]) == (0, 2)
    # Another person in the same workspace sees no candidates and no preview match.
    other = client.get(f"{API}/transfers/candidates", headers=SOMEONE, params={"start": "2026-11-01", "end": "2026-11-30"})
    assert other.status_code == 200 and other.json()["pairs"] == []
