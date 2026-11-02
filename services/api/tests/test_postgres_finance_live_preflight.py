"""The live eligibility preflight in PostgreSQL (docs/finance.md#live-eligibility-preflight).

The API runs as the non-owner ``anum_test_app`` role, so forced RLS decides whose live
settings the preflight reads: only the caller's own. It writes nothing but an audit row
with ids only, and it always answers that live trading is off and no order was sent.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.broker_readiness import BrokerReadiness
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
URL = f"{API}/broker/readiness"


def headers(user_id: str = "user_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": role}


ME, NEIGHBOUR, B = headers(), headers("user_owner", role="owner"), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)
LIMITS = {"max_per_action": [{"currency": "AED", "amount": "100"}], "max_per_day": [{"currency": "AED", "amount": "200"}],
          "max_per_month": [{"currency": "AED", "amount": "1000"}], "currencies": ["AED"], "instruments": ["exmpl"], "cooling_off_hours": 24}


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


def _met(client: TestClient, who: dict[str, str]) -> dict[str, bool]:
    response = client.post(URL, headers=who, json={"facts": {f: True for f in BrokerReadiness.model_fields}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["live_trading_enabled"] is False and body["orders_sent"] == 0
    return {p["code"]: p["met"] for p in body["system_preconditions"]}


def test_preflight_reads_only_the_callers_live_settings_under_rls(client: TestClient, database_engine: Engine) -> None:
    change = client.post(f"{API}/execution/live/changes", headers=ME, json={"limits": LIMITS}).json()
    approved = client.post(f"{API}/execution/intents/{change['id']}/approve", headers=ME, json={"payload_hash": change["payload_hash"]})
    assert approved.status_code == 200, approved.text
    with database_engine.connect() as connection:
        intents_before = connection.execute(text("select count(*) from finance_exec_intents")).scalar_one()
    mine = _met(client, ME)
    assert mine["limits"] is True
    assert mine["deployment_flag"] is False and mine["live_executor"] is False and mine["write_connector"] is False
    # Another person in the same workspace and another tenant see only their own (unset) settings.
    assert _met(client, NEIGHBOUR)["limits"] is False
    assert _met(client, B)["limits"] is False
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from finance_exec_intents")).scalar_one() == intents_before
        rows = connection.execute(text(
            "select tenant_id, actor, target, metadata from audit_records where action = 'finance.broker.live_preflight'"
        )).all()
    assert sorted((r.tenant_id, r.actor, r.target) for r in rows) == sorted([
        (TENANT_A, "user_test", "user_test"), (TENANT_A, "user_owner", "user_owner"), (TENANT_B, "user_test", "user_test"),
    ])
    assert all(not r.metadata for r in rows)


def test_viewer_is_refused_and_nothing_is_recorded(client: TestClient, database_engine: Engine) -> None:
    denied = client.post(URL, headers=headers("user_viewer", role="viewer"), json={})
    assert denied.status_code == 403
    with database_engine.connect() as connection:
        assert connection.execute(text("select count(*) from audit_records where action = 'finance.broker.live_preflight'")).scalar_one() == 0
