"""The fleet import and fleet costs in PostgreSQL (docs/vehicles.md#fleet), as the non-owner ``anum_test_app`` role under RLS.

An import writes every row in one unit of work or nothing: a row the database refuses rolls back the vehicles and
records written before it. Imported records carry the importer and the vehicle's visibility, so the row policies
apply as for typed records; the fleet costs read them back.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.schemas import utc_now
from anum_api.settings import settings
from anum_api.vehicles import fleet_import

pytestmark = pytest.mark.database
API = "/api/v1/vehicles"
FLEET = "/api/v1/vehicles/fleet"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}


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
    yield TestClient(app)


def _import(api: TestClient, kind: str, text_: str) -> dict:
    shown = api.post(f"{FLEET}/import/preview", headers=ME, json={"kind": kind, "csv": text_})
    assert shown.status_code == 200, shown.text
    done = api.post(f"{FLEET}/import/apply", headers=ME, json={"kind": kind, "csv": text_, "mapping": shown.json()["mapping"],
                                                                "fingerprint": shown.json()["fingerprint"]})
    assert done.status_code == 200, done.text
    return done.json()


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text("select count(*) from " + {"vehicles": "vehicles", "fuel": "vehicle_fuel_entries",
                                                                     "odometer": "vehicle_odometer_readings"}[table])).scalar_one()


def test_an_import_writes_under_rls_and_the_costs_read_it_back(client: TestClient, database_engine: Engine) -> None:
    today = utc_now().date()
    first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    done = _import(client, "vehicles", "Plate,Fleet No,Type,Odometer,Odometer date\nA 1001,T-01,Truck,1000," + (first - timedelta(days=40)).isoformat()
                   + "\nB 2002,T-02,Van,,\n")
    assert done["created"] == 2 and done["readings"] == 1
    fuel = (f"Plate,Date,Litres,Amount,Odometer\nA 1001,{first.replace(day=3).isoformat()},100,300,1500\n"
            f"A 1001,{first.replace(day=20).isoformat()},120,360,2100\nB 2002,{first.replace(day=5).isoformat()},40,120,\n")
    assert _import(client, "fuel", fuel)["created"] == 3
    # The colleague (a member) sees the business vehicles' imported records; their owner is the importer.
    truck = next(v for v in client.get(API, headers=COLLEAGUE).json()["items"] if v["registration"] == "A 1001")
    entries = client.get(f"{API}/{truck['id']}/fuel", headers=COLLEAGUE).json()["items"]
    assert len(entries) == 2 and all(e["mine"] is False and e["can_edit"] is False for e in entries)
    summary = client.get(f"{FLEET}/costs", headers=COLLEAGUE, params={"month": first.strftime("%Y-%m")}).json()
    row = next(r for r in summary["rows"] if r["registration"] == "A 1001")
    assert row["distance_km"] == "1100.0" and row["costs"][0]["fuel"] == "660.00" and row["costs"][0]["cost_per_km"] == "0.600"
    assert summary["totals"][0]["total"] == "780.00" and summary["totals"][0]["recorded"] == "780.00"
    report = client.post(f"{FLEET}/report", headers=ME, json={"month": first.strftime("%Y-%m"), "company_name": "Client", "format": "xlsx", "language": "ar"})
    assert report.status_code == 200 and report.content[:2] == b"PK"


def test_a_row_the_database_refuses_rolls_the_whole_import_back(client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _import(client, "vehicles", "Plate\nA 1001\n")
    before = (_count(database_engine, "vehicles"), _count(database_engine, "fuel"), _count(database_engine, "odometer"))
    day, later = (utc_now().date() - timedelta(days=10)).isoformat(), (utc_now().date() - timedelta(days=9)).isoformat()
    text_ = f"Plate,Date,Litres,Odometer\nA 1001,{day},50,5000\nA 1001,{later},60,4000\n"
    shown = client.post(f"{FLEET}/import/preview", headers=ME, json={"kind": "fuel", "csv": text_}).json()
    assert [r["status"] for r in shown["rows"]] == ["new", "invalid"]
    # The preview's own odometer check is switched off here, so the second reading reaches the database trigger.
    monkeypatch.setattr(fleet_import, "_odometer_order", lambda *args, **kwargs: {})
    refused = client.post(f"{FLEET}/import/apply", headers=ME, json={"kind": "fuel", "csv": text_, "mapping": shown["mapping"],
                                                                      "fingerprint": shown["fingerprint"], "decisions": [{"row": 3, "action": "create"}]})
    assert refused.status_code == 409 and refused.json()["code"] == "odometer_decrease"
    assert (_count(database_engine, "vehicles"), _count(database_engine, "fuel"), _count(database_engine, "odometer")) == before
