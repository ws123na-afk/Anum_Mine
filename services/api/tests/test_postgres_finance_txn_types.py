"""Transaction types in PostgreSQL (migration ``0148_finance_txn_types``, docs/finance.md#transaction-types).

The API runs as the non-owner ``anum_test_app`` role under the table's forced RLS. A typed row round-trips through
the repository; a row stored before the migration (no type) is read with the type its category stands for and gets
it stored when it is next saved; the checks refuse a type or source outside the lists. SYNTHETIC data.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/finance"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                "(:u, :t, :w, 'owner', true) on conflict do nothing"), {"u": "user_test", "t": TENANT_A, "w": WORKSPACE_A})
    for store in (finance_store, sms_memory_store):
        store.clear()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store):
        store.clear()


def _scoped(connection) -> None:  # type: ignore[no-untyped-def]
    """The row's tenant, workspace and owner, so the forced RLS policies admit the write whoever the test login is."""
    for key, value in (("anum.tenant_id", TENANT_A), ("anum.workspace_id", WORKSPACE_A), ("anum.user_id", "user_test")):
        connection.execute(text("select set_config(:k, :v, true)"), {"k": key, "v": value})


def _account(api: TestClient) -> str:
    made = api.post(f"{API}/accounts", headers=ME, json={"name": "Current", "kind": "bank", "currency": "SAR"})
    assert made.status_code == 201, made.text
    return made.json()["id"]


def _post(api: TestClient, account_id: str, amount: str, description: str) -> dict:
    made = api.post(f"{API}/transactions", headers=ME, json={"account_id": account_id, "posted_on": "2026-11-03", "amount": amount,
                                                             "description": description})
    assert made.status_code == 201, made.text
    return made.json()


def test_types_round_trip_and_unknown_money_counts_in_neither_figure(client: TestClient) -> None:
    account_id = _account(client)
    salary = _post(client, account_id, "9000", "WPS SALARY ACME")
    unknown = _post(client, account_id, "750", "SAMPLE DEPOSIT 001")
    read = {r["id"]: r for r in client.get(f"{API}/transactions", headers=ME).json()["items"]}
    assert (read[salary["id"]]["transaction_type"], read[salary["id"]]["type_source"]) == ("income", "rule")
    assert Decimal(read[salary["id"]]["type_confidence"]) == Decimal("0.80")
    assert (read[unknown["id"]]["transaction_type"], read[unknown["id"]]["type_reason"]) == ("uncategorized", "no_rule_matched")
    summary = client.get(f"{API}/summary", headers=ME, params={"month": "2026-11"}).json()
    assert summary["income"] == [{"currency": "SAR", "amount": "9000.00"}] and summary["uncategorized_count"] == 1


def test_a_row_stored_before_the_migration_reads_its_type_and_stores_it_when_saved(client: TestClient, database_engine: Engine) -> None:
    account_id = _account(client)
    row = _post(client, account_id, "-45", "CARREFOUR CITY")
    with database_engine.begin() as connection:  # what a row from before the migration holds
        _scoped(connection)
        connection.execute(text("update finance_transactions set transaction_type = null, type_source = null, type_confidence = null, "
                                "type_reason = null where id = :i"), {"i": row["id"]})
    read = client.get(f"{API}/transactions/{row['id']}", headers=ME).json()
    assert (read["transaction_type"], read["type_reason"]) == ("expense", "legacy_category")
    assert client.patch(f"{API}/transactions/{row['id']}", headers=ME, json={"notes": "weekly"}).status_code == 200
    with database_engine.begin() as connection:
        _scoped(connection)
        stored = connection.execute(text("select transaction_type, type_source from finance_transactions where id = :i"), {"i": row["id"]}).one()
    assert tuple(stored) == ("expense", "rule")


def test_the_checks_refuse_unknown_types_and_sources(client: TestClient, database_engine: Engine) -> None:
    row = _post(client, _account(client), "-45", "CARREFOUR CITY")
    for column, value in (("transaction_type", "salary"), ("type_source", "guess"), ("type_confidence", "1.5")):
        with pytest.raises(DBAPIError), database_engine.begin() as connection:
            _scoped(connection)
            connection.execute(text(f"update finance_transactions set {column} = :v where id = :i"), {"v": value, "i": row["id"]})
