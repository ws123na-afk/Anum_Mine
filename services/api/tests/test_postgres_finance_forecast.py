"""The emergency reserve and the forecast in PostgreSQL (migration ``0156_finance_reserve``, docs/finance.md#emergency-reserve,
#forecast). The API runs as the non-owner ``anum_test_app`` role under forced RLS. Every record is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import IntegrityError
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
    for store in (finance_store, sms_memory_store, links_memory_store):
        store.clear()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store, links_memory_store):
        store.clear()


def test_a_reserve_in_months_is_stored_read_back_and_private_under_rls(client: TestClient, database_engine: Engine) -> None:
    account = client.post(f"{API}/accounts", headers=ME, json={"name": "Savings", "kind": "bank", "currency": "SAR", "opening_balance": "4000"}).json()
    goal = client.post(f"{API}/goals", headers=ME, json={"name": "Emergency", "target_amount": "9000", "currency": "SAR", "purpose": "emergency",
                                                       "reserve_months": 3, "account_id": account["id"]})
    assert goal.status_code == 201, goal.text
    assert client.get(f"{API}/goals/{goal.json()['id']}", headers=ME).json()["reserve_months"] == 3
    reserve = client.get(f"{API}/reserve", headers=ME).json()["reserves"][0]
    assert (reserve["mode"], reserve["months"], reserve["saved"], reserve["progress_percent"], reserve["held_in"]) == ("months", 3, "4000.00", 44, "account")
    row = client.get(f"{API}/forecast", headers=ME).json()["currencies"][0]
    assert (row["actual"], row["reserve_target"]) == ("4000.00", "9000.00")
    assert row["warnings"][0]["kind"] == "below_reserve"
    # Someone else in the same workspace reads none of it.
    assert client.get(f"{API}/reserve", headers=SOMEONE).json()["reserves"] == []
    assert client.get(f"{API}/forecast", headers=SOMEONE).json()["currencies"] == []
    with database_engine.connect() as connection:
        assert connection.execute(text("select reserve_months from finance_goals where id = :id"), {"id": goal.json()["id"]}).scalar_one() == 3
        # The database refuses months on a goal that is not an emergency reserve, whatever the API does.
        with pytest.raises(IntegrityError):
            connection.execute(text("update finance_goals set purpose = 'travel' where id = :id"), {"id": goal.json()["id"]})
        connection.rollback()
