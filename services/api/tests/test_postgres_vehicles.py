"""Vehicles in PostgreSQL (migration ``0135_vehicles``, docs/vehicles.md#storage).

The API runs as the non-owner ``anum_test_app`` role. A personal vehicle and its records are visible
only to their owner; a business vehicle to the workspace; the odometer trigger refuses a decrease
that is not a correction; a linked payment adds no posting and its read copy follows corrections
and undo in the same transaction. Bank messages are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.db import session as db_session
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.main import app
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings
from anum_api.vehicles import jobs as J
from anum_api.vehicles.service import plug_finance_links

pytestmark = pytest.mark.database
API = "/api/v1/vehicles"
FIN = "/api/v1/finance"
FUEL_SMS = "SYNTHETIC. Purchase of SAR 150.00 at PETROMIN FUEL with card **1234"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("vehicles", "vehicle_odometer_readings", "vehicle_fuel_entries", "vehicle_trips", "vehicle_service_records", "vehicle_reminders",
          "vehicle_preferences")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        for user_id, role in (("user_test", "owner"), ("colleague", "member")):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": WORKSPACE_A})
    for store in (finance_store, sms_memory_store, links_memory_store):
        store.clear()
    svc._last_notified.clear()
    plug_finance_links()
    yield TestClient(app)
    for store in (finance_store, sms_memory_store, links_memory_store):
        store.clear()


def _rows(app_session: Callable[..., Iterator[Session]], user: str, sql: str, params: dict | None = None) -> list:
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id=user)) as session:
        session.execute(text("select set_config('anum.user_id', :u, true)"), {"u": user})
        return list(session.execute(text(sql), params or {}).all())


def _vehicle(api: TestClient, headers: dict = ME, **body: object) -> dict:
    response = api.post(API, headers=headers, json={"registration": "ABC 1234", "country": "SA", "fuel_type": "petrol_91", **body})
    assert response.status_code == 201, response.text
    return response.json()


def _fuel_payment(api: TestClient) -> tuple[str, str]:
    card = api.post(f"{FIN}/accounts", headers=ME, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    assert api.post(f"{FIN}/sms/self-test", headers=ME).json()["passed"]
    version = api.get(f"{FIN}/sms/settings", headers=ME).json()["version"]
    assert api.put(f"{FIN}/sms/settings", headers=ME, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": "TestBank", "bank": "test"}], "verified_senders": ["TestBank"],
        "account_map": [{"last4": "1234", "account_id": card["id"]}], "wallet_cards": [{"card": "My Visa", "account_id": card["id"]}]}).status_code == 200
    token = api.post(f"{FIN}/sms/devices", headers=ME, json={"name": "iPhone"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    assert api.post(f"{FIN}/wallet/ingest", headers=bearer, json={"source": "apple_wallet", "amount": "SAR 150.00", "merchant": "Petromin Fuel",
                                                                  "card": "My Visa", "name": "Purchase"}).json()["status"] == "posted"
    assert api.post(f"{FIN}/sms/ingest", headers=bearer, json={"message": FUEL_SMS, "sender": "TestBank"}).json()["status"] == "attached"
    entries = api.get(f"{FIN}/ledger/entries", headers=ME).json()["items"]
    assert len(entries) == 1
    return entries[0]["canonical_id"], card["id"]


def test_vehicle_tables_have_forced_rls_and_visibility_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}", f"vehicle_visible_{table}"} <= policies, (table, policies)


def test_personal_vehicles_are_private_and_business_ones_shared(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    mine = _vehicle(client, odometer_km="100")
    shared = _vehicle(client, COLLEAGUE, registration="BIZ 1", ownership="business", odometer_km="5")
    assert client.post(f"{API}/{shared['id']}/fuel", headers=ME, json={"filled_at": utc_now().isoformat(), "litres": "20",
                                                                        "odometer_km": "50"}).status_code == 201
    assert {r[0] for r in _rows(app_session, "colleague", "select id from vehicles")} == {shared["id"]}
    assert _rows(app_session, "colleague", "select count(*) from vehicle_odometer_readings where vehicle_id = :v", {"v": mine["id"]}) == [(0,)]
    assert _rows(app_session, "colleague", "select count(*) from vehicle_fuel_entries") == [(1,)]
    assert client.get(f"{API}/{mine['id']}", headers=COLLEAGUE).status_code == 404
    # A colleague cannot write a record that claims another person's personal vehicle.
    with pytest.raises(DBAPIError):
        with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="colleague")) as session:
            session.execute(text("select set_config('anum.user_id', 'colleague', true)"))
            session.execute(text(
                "insert into vehicle_odometer_readings (id, tenant_id, workspace_id, owner_user_id, vehicle_id, visibility, vehicle_owner_id, "
                "reading_km, read_at, source) values ('x', :t, :w, 'colleague', :v, 'business', 'user_test', 1, now(), 'manual')"),
                {"t": TENANT_A, "w": WORKSPACE_A, "v": mine["id"]})


def test_the_database_refuses_an_odometer_decrease_without_a_correction(client: TestClient,
                                                                         app_session: Callable[..., Iterator[Session]]) -> None:
    car = _vehicle(client, odometer_km="1000")
    insert = ("insert into vehicle_odometer_readings (id, tenant_id, workspace_id, owner_user_id, vehicle_id, visibility, vehicle_owner_id, "
              "reading_km, read_at, source, correction, reason) values (:id, :t, :w, 'user_test', :v, 'personal', 'user_test', :km, now(), :s, :c, :r)")
    params = {"t": TENANT_A, "w": WORKSPACE_A, "v": car["id"]}
    with pytest.raises(DBAPIError, match="odometer_decrease"):
        _rows(app_session, "user_test", insert, {**params, "id": "lower", "km": 900, "s": "manual", "c": False, "r": None})
    with pytest.raises(DBAPIError, match="ck_vehicle_odometer_readings_reason"):
        _rows(app_session, "user_test", insert, {**params, "id": "noreason", "km": 900, "s": "correction", "c": True, "r": None})
    with app_session(TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="user_test"), commit=True) as session:
        session.execute(text("select set_config('anum.user_id', 'user_test', true)"))
        session.execute(text(insert), {**params, "id": "fix", "km": 900, "s": "correction", "c": True, "r": "cluster replaced"})
    with pytest.raises(DBAPIError, match="never changed"):
        _rows(app_session, "user_test", "update vehicle_odometer_readings set reading_km = 5 where id = 'fix'")
    # The API answers the same rule with the reading it would break.
    lower = client.post(f"{API}/{car['id']}/odometer", headers=ME, json={"reading_km": "800"})
    assert lower.status_code == 409 and lower.json()["reading_km"] == "900.0"


def test_concurrent_readings_keep_the_odometer_monotonic(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    car = _vehicle(client, odometer_km="100")
    moment = utc_now() + timedelta(seconds=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda km: client.post(f"{API}/{car['id']}/odometer", headers=ME,
                                                     json={"reading_km": str(km), "read_at": moment.isoformat()}).status_code, (500, 300, 400, 200)))
    assert codes.count(201) >= 1 and set(codes) <= {201, 409}
    rows = _rows(app_session, "user_test", "select reading_km from vehicle_odometer_readings where vehicle_id = :v order by read_at, created_at, id",
                 {"v": car["id"]})
    values = [r[0] for r in rows]
    assert values == sorted(values)


def test_a_linked_fuel_payment_adds_no_posting_and_follows_correction_and_undo(client: TestClient,
                                                                              app_session: Callable[..., Iterator[Session]]) -> None:
    canonical, card = _fuel_payment(client)
    car = _vehicle(client, odometer_km="10000")
    entry = client.post(f"{API}/{car['id']}/fuel", headers=ME, json={"filled_at": utc_now().isoformat(), "litres": "40", "total": "150",
                                                                     "odometer_km": "10400"}).json()
    journal = "select e.id, e.status, p.ledger_account, p.amount from finance_journal_entries e join finance_postings p on p.entry_id = e.id order by 1, 3"
    before = _rows(app_session, "user_test", journal)
    transactions = _rows(app_session, "user_test", "select count(*) from finance_transactions")
    linked = client.post(f"{API}/costs/fuel_entry/{entry['id']}/payment", headers=ME, json={"canonical_id": canonical})
    assert linked.status_code == 201, linked.text
    assert client.post(f"{API}/costs/fuel_entry/{entry['id']}/payment", headers=ME, json={"canonical_id": canonical}).status_code == 200
    assert _rows(app_session, "user_test", journal) == before
    assert _rows(app_session, "user_test", "select count(*) from finance_transactions") == transactions
    payment_sql = "select payment->>'status', payment->>'amount' from vehicle_fuel_entries where id = :id"
    assert _rows(app_session, "user_test", payment_sql, {"id": entry["id"]}) == [("active", "150.00")]
    # A colleague reads the vehicle's cost from the read copy, never the payer's ledger or links.
    assert _rows(app_session, "colleague", "select count(*) from finance_links") == [(0,)]
    message_id = client.get(f"{FIN}/sms/messages", headers=ME, params={"status": "posted"}).json()["items"][0]["id"]
    assert client.post(f"{FIN}/sms/messages/{message_id}/post", headers=ME, json={"amount": "100.00", "account_id": card}).status_code == 200
    assert _rows(app_session, "user_test", payment_sql, {"id": entry["id"]})[0][0] == "needs_review"
    assert client.post(f"{FIN}/sms/messages/{message_id}/reverse", headers=ME).status_code == 200
    assert _rows(app_session, "user_test", payment_sql, {"id": entry["id"]})[0][0] == "void"
    assert client.get(f"{API}/{car['id']}/report", headers=ME).json()["spend"] == []


def test_cash_is_one_finance_transaction_under_rls(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    cash = client.post(f"{FIN}/accounts", headers=ME, json={"name": "Wallet", "kind": "cash", "currency": "SAR"}).json()
    car = _vehicle(client)
    record = client.post(f"{API}/{car['id']}/service", headers=ME, json={"kind": "parking", "service_on": utc_now().date().isoformat()}).json()
    body = {"account_id": cash["id"], "amount": "10", "posted_on": utc_now().date().isoformat()}
    for expected in (201, 200):
        assert client.post(f"{API}/costs/service_record/{record['id']}/cash", headers={**ME, "Idempotency-Key": "k1"}, json=body).status_code == expected
    assert _rows(app_session, "user_test", "select count(*) from finance_transactions") == [(1,)]
    assert _rows(app_session, "user_test", "select count(*) from finance_journal_entries") == [(1,)]
    assert _rows(app_session, "user_test", "select payment->>'recorded_as_cash' from vehicle_service_records") == [("true",)]


def test_reminder_discovery_and_privacy_delete(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:
    soon = (utc_now().date() + timedelta(days=5)).isoformat()
    _vehicle(client, insurance_expires_on=soon)
    business = _vehicle(client, COLLEAGUE, registration="BIZ 7", ownership="business")
    assert (TENANT_A, WORKSPACE_A, "user_test") in J.discover_people()
    first = J.run()
    assert first["reminders"] == 1, first
    assert J.run()["reminders"] == 0
    assert _rows(app_session, "user_test", "select count(*) from user_notifications where kind = 'vehicle_reminder'") == [(1,)]
    assert client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"}).status_code == 200
    assert {r[0] for r in _rows(app_session, "colleague", "select id from vehicles")} == {business["id"]}
    assert _rows(app_session, "user_test", "select count(*) from vehicles where owner_user_id = 'user_test'") == [(0,)]
