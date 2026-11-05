"""Bills matched to payments, debt payments, the debt plan, paused goals and budget alerts in PostgreSQL
(migration ``0142_finance_debts_bills``, docs/finance.md#bills-and-budgets). The API runs as the non-owner
``anum_test_app`` role under forced RLS. Every record is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator

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
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
SOMEONE = {**ME, "x-user-id": "someone_else"}


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
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


def test_bill_and_debt_matches_plan_and_alerts_under_rls(client: TestClient, database_engine: Engine) -> None:
    current = client.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR"}).json()
    bill = client.post(f"{API}/bills", headers=ME, json={"name": "Rent", "amount": "2500", "currency": "SAR", "next_due_on": "2026-11-15"}).json()
    paid = client.post(f"{API}/transactions", headers=ME, json={"account_id": current["id"], "posted_on": "2026-11-14", "amount": "-2500",
                                                            "description": "Rent", "category": "housing"})
    assert paid.status_code == 201, paid.text
    canonical = next(e["canonical_id"] for e in client.get(f"{API}/ledger/entries", headers=ME).json()["items"] if e["source_ref"] == paid.json()["id"])
    made = client.post(f"{API}/bills/{bill['id']}/matches", headers=ME, json={"canonical_id": canonical, "due_on": "2026-11-15"})
    assert made.status_code == 201, made.text
    item = client.get(f"{API}/bill-schedule?from=2026-11-01&to=2026-11-30", headers=ME).json()["items"][0]
    assert (item["status"], item["paid_amount"]) == ("paid", "2500.00")

    debt = client.post(f"{API}/debts", headers=ME, json={"name": "Car", "currency": "SAR", "balance": "15500", "original_balance": "40000"}).json()
    assert debt["original_balance"] == "40000.00"
    # Dimensions are separate views of one payment (docs/finance-links.md): a debt match does not use up the bill's.
    assert client.post(f"{API}/debts/{debt['id']}/payments", headers=ME, json={"canonical_id": canonical, "amount": "1000"}).status_code == 201
    assert client.get(f"{API}/debt-payments", headers=ME).json()["paid"] == [{"debt_id": debt["id"], "currency": "SAR", "amount": "1000.00"}]

    goal = client.post(f"{API}/goals", headers=ME, json={"name": "Reserve", "currency": "SAR", "target_amount": "20000", "paused": True}).json()
    assert client.get(f"{API}/goals/{goal['id']}", headers=ME).json()["paused"] is True

    client.put(f"{API}/plan", headers=ME, json={"buffers": [{"currency": "SAR", "amount": "300"}]})
    assert client.put(f"{API}/debt-plan", headers=ME, json={"plans": [{"currency": "SAR", "strategy": "snowball", "monthly_payment": "2450"}]}).status_code == 200
    assert client.put(f"{API}/budgets/alerts", headers=ME, json={"alerts": [{"category": "fuel", "currency": "SAR", "alert_percent": 85}]}).status_code == 200
    assert client.get(f"{API}/plan", headers=ME).json()["buffers"] == [{"currency": "SAR", "amount": "300.00"}]
    assert client.get(f"{API}/debt-plan", headers=ME).json()["plans"][0]["monthly_payment"] == "2450.00"

    # Another person in the same workspace reads none of it.
    assert client.get(f"{API}/bill-schedule?from=2026-11-01&to=2026-11-30", headers=SOMEONE).json()["items"] == []
    assert client.get(f"{API}/debt-payments", headers=SOMEONE).json()["total"] == 0
    assert client.get(f"{API}/debt-plan", headers=SOMEONE).json()["plans"] == []
    with database_engine.begin() as connection:
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true), set_config('anum.workspace_id', :w, true), "
                                "set_config('anum.user_id', 'someone_else', true)"), {"t": TENANT_A, "w": WORKSPACE_A})
        assert connection.execute(text("select count(*) from finance_links where dimension in ('bill', 'debt')")).scalar() == 0
        assert connection.execute(text("select count(*) from finance_plans")).scalar() == 0
    with database_engine.begin() as connection:
        dims = connection.execute(text("select dimension from finance_links order by dimension")).scalars().all()
        assert dims == ["bill", "debt"]
