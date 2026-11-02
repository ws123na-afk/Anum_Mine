"""Fund research in PostgreSQL (docs/finance-research.md): no new table. AI summaries are owner-only rows
in ``finance_agent_decisions`` and a paper run is a paper-only plan in ``finance_broker_settings``; both
run as the non-owner ``anum_test_app`` role under forced RLS, and another person sees neither.
Outside sources and the model are fakes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from ibkr_fake import PIN, FakeIbkr
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker
from test_finance_research import NOW, Sources, _answer

from anum_api.db import session as db_session
from anum_api.finance import ibkr_api, research_data, research_service, research_summary
from anum_api.finance import ibkr_service as svc
from anum_api.finance.config import finance_config
from anum_api.finance.ibkr_store import ibkr_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance/research"


def headers(user_id: str = "user_test", role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": user_id, "x-user-roles": role}


ME, SOMEONE = headers(), headers("user_owner")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(research_data, "TRANSPORT", httpx.MockTransport(Sources()))
    monkeypatch.setattr(finance_config, "research_contact", "research@example.com")
    monkeypatch.setattr(settings, "searxng_url", "http://searxng:8080")
    monkeypatch.setattr(svc, "TRANSPORT", FakeIbkr(now_ts=NOW.timestamp()))
    monkeypatch.setattr(ibkr_api, "utc_now", lambda: NOW)
    monkeypatch.setattr(research_summary, "unavailable_reason", lambda context: None)
    answer = _answer(history="No figures.", good=["Shariah-screened."], range="Past results do not predict the future.", reasons=["Wait."])
    monkeypatch.setattr(research_summary, "ask", lambda context, prompt: (answer, "fake/model"))
    finance_store.clear()
    ibkr_memory_store.clear()
    research_service.clear_cache()
    yield TestClient(app)
    research_service.clear_cache()


def test_summaries_and_paper_plans_stay_with_their_owner_under_rls(client: TestClient, database_engine: Engine) -> None:
    made = client.post(f"{API}/symbols/SPUS/summary", headers=ME)
    assert made.status_code == 200, made.text
    assert made.json()["status"] == "ok"
    assert client.get(f"{API}/symbols/SPUS", headers=ME).json()["summary"]["status"] == "ok"
    assert client.get(f"{API}/symbols/SPUS", headers=SOMEONE).json()["summary"] is None
    with database_engine.connect() as connection:
        row = connection.execute(text("select agent, action, owner_user_id, details from finance_agent_decisions where action = 'symbol_research_summary'")).one()
    assert (row.agent, row.owner_user_id) == ("market_research", "user_test")
    assert json.loads(json.dumps(row.details))["symbol"] == "SPUS"
    conn = {"environment": "paper", "gateway_host": "ibkr-gateway", "tls_fingerprint": PIN, "account_id": "DU1234567", "cash_account_confirmed": True}
    assert client.post("/api/v1/finance/ibkr/connections", json=conn, headers=ME).status_code == 201
    assert client.post("/api/v1/finance/ibkr/symbols", json={"symbol": "SPUS"}, headers=ME).status_code == 200
    plan = client.post(f"{API}/paper-run", json={"symbol": "SPUS", "amount": "10", "day_of_month": 5}, headers=ME)
    assert plan.status_code == 201, plan.text
    assert plan.json()["plan"]["paper_only"] is True
    assert client.get("/api/v1/finance/ibkr/overview", headers=ME).json()["settings"]["buy_plans"][0]["paper_only"] is True
    assert client.get("/api/v1/finance/ibkr/overview", headers=SOMEONE).json()["settings"]["buy_plans"] == []
