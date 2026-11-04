"""Vehicles, fuel and mileage (docs/vehicles.md), in-memory stores.

Every bank message here is SYNTHETIC: written for these tests, labelled, and not the wording of any
real bank. ``test_postgres_vehicles.py`` repeats the database rules under RLS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance import devices as D
from anum_api.finance import links as K
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import WorkspaceMembership, utc_now
from anum_api.vehicles import devices as VD
from anum_api.vehicles import jobs as J
from anum_api.vehicles.service import plug_finance_links
from anum_api.vehicles.store import vehicle_memory_store

TENANT, WORKSPACE, BUSINESS = "tenant_veh", "ws_veh", "ws_veh_business"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_veh", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_veh", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_veh", "x-user-roles": "viewer"}
API = "/api/v1/vehicles"
FIN = "/api/v1/finance"
SENDER = "TestBank"  # SYNTHETIC sender
FUEL_SMS = "SYNTHETIC. Purchase of SAR 150.00 at PETROMIN FUEL with card **1234"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    vehicle_memory_store.clear()
    svc._last_notified.clear()
    plug_finance_links()  # other suites register a stand-in vehicle target and remove it afterwards
    VD.register()
    memberships = dict(memory_repository.store.memberships)
    now = utc_now()
    for workspace, user, role in ((BUSINESS, "owner_veh", "owner"), (WORKSPACE, "owner_veh", "owner"), (WORKSPACE, "member_veh", "member")):
        memory_repository.store.memberships[(TENANT, workspace, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=workspace, user_id=user, role=role, active=True, created_at=now, updated_at=now)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    for store in (finance_store, sms_memory_store, links_memory_store, vehicle_memory_store):
        store.clear()


def vehicle(headers: dict = OWNER, **body: Any) -> dict:
    payload = {"registration": "ABC 1234", "country": "SA", "fuel_type": "petrol_91", "make": "Toyota", "model": "Camry", "year": 2022,
               "vin": "JTDBR32E720123456", "tank_litres": "60", **body}
    response = client.post(API, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def fuel(vehicle_id: str, headers: dict = OWNER, key: str | None = None, **body: Any):  # type: ignore[no-untyped-def]
    payload = {"filled_at": utc_now().isoformat(), "litres": "40", "total": "150", "odometer_km": "10400", **body}
    return client.post(f"{API}/{vehicle_id}/fuel", headers={**headers, **({"Idempotency-Key": key} if key else {})}, json=payload)


# -- the finance side: one SAR 150 fuel payment from Apple Pay and the bank SMS ------------------------


def fuel_payment() -> tuple[str, dict]:
    card = client.post(f"{FIN}/accounts", headers=OWNER, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    assert client.post(f"{FIN}/sms/self-test", headers=OWNER).json()["passed"]
    version = client.get(f"{FIN}/sms/settings", headers=OWNER).json()["version"]
    put = client.put(f"{FIN}/sms/settings", headers=OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "1234", "account_id": card["id"]}], "wallet_cards": [{"card": "My Visa", "account_id": card["id"]}]})
    assert put.status_code == 200, put.text
    token = client.post(f"{FIN}/sms/devices", headers=OWNER, json={"name": "iPhone"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    paid = client.post(f"{FIN}/wallet/ingest", headers=bearer, json={"source": "apple_wallet", "amount": "SAR 150.00", "merchant": "Petromin Fuel",
                                                                     "card": "My Visa", "name": "Purchase"})
    assert paid.json()["status"] == "posted", paid.text
    sms = client.post(f"{FIN}/sms/ingest", headers=bearer, json={"message": FUEL_SMS, "sender": SENDER})
    assert sms.json()["status"] == "attached", sms.text
    entries = list(sms_memory_store.entries.values())
    assert len(entries) == 1
    return entries[0].canonical_id or entries[0].id, card


def journal() -> tuple[dict, int]:
    return ({e.id: (e.status, sorted((p.ledger_account, p.amount, p.currency) for p in e.postings)) for e in sms_memory_store.entries.values()},
            len(finance_store.transactions))


def link(kind: str, record_id: str, canonical: str, amount: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(f"{API}/costs/{kind}/{record_id}/payment", headers=OWNER, json={"canonical_id": canonical, **({"amount": amount} if amount else {})})


# -- vehicles ----------------------------------------------------------------------------------------


def test_create_list_mask_and_permissions() -> None:
    car = vehicle(odometer_km="10000", insurance_expires_on=(utc_now().date() + timedelta(days=10)).isoformat())
    assert car["currency"] == "SAR" and car["registration"] == "ABC 1234" and car["vin"] == "JTDBR32E720123456" and car["odometer_km"] == "10000.0"
    listed = client.get(API, headers=OWNER).json()
    assert listed["total"] == 1 and listed["items"][0]["vin"].endswith("3456") and "JTDBR" not in listed["items"][0]["vin"]
    assert listed["items"][0]["due"][0]["kind"] == "insurance"
    assert client.get(API, headers=OWNER, params={"q": "camry"}).json()["total"] == 1
    assert client.get(API, headers=OWNER, params={"q": "nothing"}).json()["total"] == 0
    duplicate = client.post(API, headers=OWNER, json={"registration": "abc  1234", "country": "SA", "fuel_type": "diesel"})
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "duplicate_registration"
    # The same plate in the other country is another vehicle.
    assert client.post(API, headers=OWNER, json={"registration": "ABC 1234", "country": "AE", "fuel_type": "diesel"}).json()["currency"] == "AED"
    viewer = client.get(API, headers=VIEWER)
    assert viewer.status_code == 403 and "viewer" in viewer.json()["detail"]
    assert client.post(API, headers=OWNER, json={"registration": "X", "country": "SA", "fuel_type": "diesel", "vin": "IOQ12345678"}).json()["code"] == "invalid_vin"


def test_a_personal_vehicle_is_private_and_a_business_one_is_shared() -> None:
    mine = vehicle()
    shared = vehicle(MEMBER, registration="BIZ 1", ownership="business")
    assert {v["id"] for v in client.get(API, headers=MEMBER).json()["items"]} == {shared["id"]}
    assert client.get(f"{API}/{mine['id']}", headers=MEMBER).status_code == 404
    assert fuel(mine["id"], MEMBER).status_code == 404
    # Every member logs on a business vehicle; only its creator or a workspace owner changes the vehicle itself.
    assert fuel(shared["id"], OWNER).status_code == 201
    other = vehicle(OWNER, registration="BIZ 2", ownership="business")
    refused = client.patch(f"{API}/{other['id']}", headers=MEMBER, json={"version": 1, "nickname": "Van"})
    assert refused.status_code == 403 and refused.json()["code"] == "not_vehicle_owner"
    assert client.patch(f"{API}/{shared['id']}", headers=OWNER, json={"version": 1, "nickname": "Pool car"}).json()["nickname"] == "Pool car"
    detail = client.get(f"{API}/{other['id']}", headers=MEMBER).json()
    assert detail["can_edit"] is False and detail["vin_masked"] is True


def test_stale_edit_archive_and_delete() -> None:
    car = vehicle()
    assert client.patch(f"{API}/{car['id']}", headers=OWNER, json={"version": 1, "nickname": "Mine"}).status_code == 200
    stale = client.patch(f"{API}/{car['id']}", headers=OWNER, json={"version": 1, "nickname": "Again"})
    assert stale.status_code == 409 and stale.json()["code"] == "stale"
    archived = client.post(f"{API}/{car['id']}/archive", headers=OWNER, json={"version": 2})
    assert archived.json()["archived_at"] is not None
    assert client.get(API, headers=OWNER).json()["total"] == 0 and client.get(API, headers=OWNER, params={"archived": True}).json()["total"] == 1
    assert fuel(car["id"]).json()["code"] == "archived"
    assert client.delete(f"{API}/{car['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/{car['id']}", headers=OWNER).status_code == 404


# -- odometer ------------------------------------------------------------------------------------------


def test_the_odometer_never_goes_down_without_a_correction() -> None:
    car = vehicle(odometer_km="10000")
    url = f"{API}/{car['id']}/odometer"
    lower = client.post(url, headers=OWNER, json={"reading_km": "9990"})
    assert lower.status_code == 409 and lower.json()["code"] == "odometer_decrease" and lower.json()["reading_km"] == "10000.0"
    no_reason = client.post(url, headers=OWNER, json={"reading_km": "9990", "correction": True})
    assert no_reason.status_code == 422 and no_reason.json()["code"] == "reason_required"
    fixed = client.post(url, headers=OWNER, json={"reading_km": "9990", "correction": True, "reason": "typo when adding the car"})
    assert fixed.status_code == 201 and fixed.json()["source"] == "correction"
    assert client.post(url, headers=OWNER, json={"reading_km": "9995"}).status_code == 201
    # A back-dated reading must fit between its neighbours.
    yesterday = (utc_now() - timedelta(days=1)).isoformat()
    assert client.post(url, headers=OWNER, json={"reading_km": "10050", "read_at": yesterday}).status_code == 409
    readings = client.get(url, headers=OWNER).json()
    assert readings["total"] == 3 and readings["items"][0]["reading_km"] == "9995.0"
    # Fuel with a lower odometer is refused the same way, and nothing is saved.
    refused = fuel(car["id"], odometer_km="9000")  # now: after the 9995 reading
    assert refused.status_code == 409 and client.get(f"{API}/{car['id']}/fuel", headers=OWNER).json()["total"] == 0


def test_only_the_vehicle_owner_corrects_a_business_vehicle() -> None:
    van = vehicle(registration="VAN 1", ownership="business", odometer_km="500")
    refused = client.post(f"{API}/{van['id']}/odometer", headers=MEMBER, json={"reading_km": "100", "correction": True, "reason": "cluster swap"})
    assert refused.status_code == 403
    assert client.post(f"{API}/{van['id']}/odometer", headers=MEMBER, json={"reading_km": "600"}).status_code == 201


# -- records -------------------------------------------------------------------------------------------


def test_fuel_entry_is_idempotent_and_editable() -> None:
    car = vehicle(odometer_km="10000")
    first = fuel(car["id"], key="pump-1")
    again = fuel(car["id"], key="pump-1")
    assert first.status_code == 201 and again.status_code == 200 and again.json()["replayed"] and again.json()["id"] == first.json()["id"]
    entry = first.json()
    assert entry["price_per_litre"] == "3.750" and entry["currency"] == "SAR" and entry["fuel_type"] == "petrol_91" and entry["payment"] is None
    edited = client.patch(f"{API}/fuel/{entry['id']}", headers=OWNER, json={"version": 1, "odometer_km": "10450", "station": "Aldrees"})
    assert edited.status_code == 200 and edited.json()["odometer_km"] == "10450.0"
    assert [r["reading_km"] for r in client.get(f"{API}/{car['id']}/odometer", headers=OWNER).json()["items"]] == ["10450.0", "10000.0"]
    assert client.get(f"{API}/{car['id']}/fuel", headers=OWNER, params={"q": "aldrees"}).json()["total"] == 1
    other = client.patch(f"{API}/fuel/{entry['id']}", headers=MEMBER, json={"version": 2, "station": "x"})
    assert other.status_code == 404  # a personal vehicle's record does not exist for others
    assert client.delete(f"{API}/fuel/{entry['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/{car['id']}/odometer", headers=OWNER).json()["total"] == 1


def test_trips_distance_places_and_one_open_trip() -> None:
    car = vehicle()
    start = utc_now() - timedelta(hours=3)
    trip = client.post(f"{API}/{car['id']}/trips", headers=OWNER, json={
        "started_at": start.isoformat(), "ended_at": (start + timedelta(hours=1)).isoformat(), "start_odometer_km": "1000",
        "end_odometer_km": "1042.5", "purpose": "business", "project_id": "proj_1"}).json()
    assert trip["distance_km"] == "42.5" and trip["distance_basis"] == "typed" and trip["status"] == "closed"
    places = client.post(f"{API}/{car['id']}/trips", headers=OWNER, json={"started_at": start.isoformat(), "origin": "Home"})
    assert places.status_code == 422 and places.json()["code"] == "places_off"
    assert client.put(f"{API}/preferences", headers=OWNER, json={"version": 0, "trip_places": True}).json()["trip_places"] is True
    opened = client.post(f"{API}/{car['id']}/trips", headers=OWNER, json={"started_at": utc_now().isoformat(), "origin": "Home"})
    assert opened.status_code == 201 and opened.json()["status"] == "open"
    second = client.post(f"{API}/{car['id']}/trips", headers=OWNER, json={"started_at": utc_now().isoformat()})
    assert second.status_code == 409 and second.json()["code"] == "trip_open"
    closed = client.patch(f"{API}/trips/{opened.json()['id']}", headers=OWNER, json={"version": 1, "ended_at": utc_now().isoformat(),
                                                                                    "end_odometer_km": "1050"})
    assert closed.json()["distance_km"] == "7.5" and closed.json()["distance_basis"] == "previous_reading"
    backwards = client.post(f"{API}/{car['id']}/trips", headers=OWNER, json={"started_at": start.isoformat(), "ended_at": start.isoformat(),
                                                                             "start_odometer_km": "50", "end_odometer_km": "40"})
    assert backwards.status_code == 422


def test_service_records_and_reminders() -> None:
    car = vehicle(odometer_km="20000", registration_expires_on=(utc_now().date() + timedelta(days=3)).isoformat())
    oil = client.post(f"{API}/{car['id']}/service", headers=OWNER, json={
        "kind": "oil", "service_on": utc_now().date().isoformat(), "odometer_km": "20000", "workshop": "Petromin Express",
        "next_due_km": "20400", "receipt_total": "180"})
    assert oil.status_code == 201, oil.text
    due = client.get(f"{API}/reminders", headers=OWNER).json()["items"]
    assert {(d["kind"], d.get("service_kind")) for d in due} == {("registration", None), ("service", "oil")}
    result = J.run(now=utc_now())
    assert result["reminders"] == 2 and result["failed"] == 0
    assert J.run(now=utc_now())["reminders"] == 0  # each step once
    off = client.put(f"{API}/preferences", headers=OWNER, json={"version": 0, "reminders": False})
    assert off.status_code == 200
    assert J.step_for(30, 30) == 30 and J.step_for(5, 30) == 7 and J.step_for(-1, 30) is None


# -- payments: the acceptance cases ---------------------------------------------------------------------


def test_an_apple_pay_and_sms_fuel_payment_stays_one_expense_after_linking() -> None:
    canonical, _card = fuel_payment()
    car = vehicle(odometer_km="10000")
    entry = fuel(car["id"]).json()
    before = journal()
    linked = link("fuel_entry", entry["id"], canonical)
    assert linked.status_code == 201, linked.text
    payment = linked.json()["payment"]
    assert payment["status"] == "active" and payment["amount"] == "150.00" and payment["currency"] == "SAR" and payment["canonical_id"] == canonical
    assert journal() == before  # no entry, posting or transaction was added
    # Allocating the same payment to a business is a second view of the one posting.
    allocated = client.post(f"{FIN}/allocations", headers=OWNER, json={"canonical_id": canonical, "workspace_id": BUSINESS, "amount": "150",
                                                                      "currency": "SAR", "category": "fuel", "treatment": "owner_contribution"})
    assert allocated.status_code == 201, allocated.text
    assert journal() == before
    report = client.get(f"{API}/{car['id']}/report", headers=OWNER).json()
    assert report["spend"] == [{"currency": "SAR", "amount": "150.00"}]
    assert client.get(f"{API}/overview", headers=OWNER).json()["spend"] == [{"currency": "SAR", "amount": "150.00"}]
    # Relinking is idempotent; a second vehicle record cannot take more than the payment.
    again = link("fuel_entry", entry["id"], canonical)
    assert again.status_code == 200 and again.json()["replayed"] and len(links_memory_store.links) == 2
    other = fuel(car["id"], odometer_km="10500").json()
    over = link("fuel_entry", other["id"], canonical)
    assert over.status_code == 409 and over.json()["code"] == "over_allocated"
    # Unlinking twice is the same as once; the payment stays in Finance.
    assert client.delete(f"{API}/costs/fuel_entry/{entry['id']}/payment", headers=OWNER).status_code == 204
    assert client.delete(f"{API}/costs/fuel_entry/{entry['id']}/payment", headers=OWNER).status_code == 204
    assert client.get(f"{API}/{car['id']}/fuel", headers=OWNER).json()["items"][1]["payment"] is None
    assert journal() == before
    assert link("fuel_entry", entry["id"], canonical).status_code == 201


def test_a_correction_keeps_the_link_and_an_undo_voids_it() -> None:
    canonical, card = fuel_payment()
    car = vehicle(odometer_km="10000")
    entry = fuel(car["id"]).json()
    assert link("fuel_entry", entry["id"], canonical, "120").status_code == 201
    message_id = client.get(f"{FIN}/sms/messages", headers=OWNER, params={"status": "posted"}).json()["items"][0]["id"]
    # Same figures: the link stays active on the same canonical id.
    same = client.post(f"{FIN}/sms/messages/{message_id}/post", headers=OWNER, json={"amount": "150.00", "account_id": card["id"]})
    assert same.status_code == 200 and same.json()["canonical_id"] == canonical
    record = client.get(f"{API}/{car['id']}/fuel", headers=OWNER).json()["items"][0]
    assert record["payment"]["status"] == "active" and record["payment"]["canonical_id"] == canonical
    # Corrected below what is linked: the vehicle record waits for review and stops counting.
    client.post(f"{FIN}/sms/messages/{message_id}/post", headers=OWNER, json={"amount": "100.00", "account_id": card["id"]})
    record = client.get(f"{API}/{car['id']}/fuel", headers=OWNER).json()["items"][0]
    assert record["payment"]["status"] == "needs_review"
    report = client.get(f"{API}/{car['id']}/report", headers=OWNER).json()
    assert report["spend"] == [] and {a["kind"] for a in report["anomalies"]} >= {"payment_changed"}
    # Undone in Finance: the link is void and the record says so.
    assert client.post(f"{FIN}/sms/messages/{message_id}/reverse", headers=OWNER).status_code == 200
    record = client.get(f"{API}/{car['id']}/fuel", headers=OWNER).json()["items"][0]
    assert record["payment"]["status"] == "void" and record["payment"]["status_reason"] == "transaction_undone"
    assert client.get(f"{API}/{car['id']}/report", headers=OWNER).json()["unlinked_records"] == 1


def test_cash_is_recorded_once_in_finance_and_linked() -> None:
    cash = client.post(f"{FIN}/accounts", headers=OWNER, json={"name": "Wallet", "kind": "cash", "currency": "SAR"}).json()
    car = vehicle(odometer_km="10000")
    entry = fuel(car["id"]).json()
    body = {"account_id": cash["id"], "amount": "95.50", "posted_on": utc_now().date().isoformat()}
    paid = client.post(f"{API}/costs/fuel_entry/{entry['id']}/cash", headers={**OWNER, "Idempotency-Key": "cash-1"}, json=body)
    assert paid.status_code == 201, paid.text
    assert paid.json()["payment"]["recorded_as_cash"] is True and paid.json()["payment"]["amount"] == "95.50"
    assert len(finance_store.transactions) == 1 and len(sms_memory_store.entries) == 1
    again = client.post(f"{API}/costs/fuel_entry/{entry['id']}/cash", headers={**OWNER, "Idempotency-Key": "cash-1"}, json=body)
    assert again.status_code == 200 and again.json()["replayed"]
    assert len(finance_store.transactions) == 1 and len(sms_memory_store.entries) == 1
    (txn,) = finance_store.transactions.values()
    assert txn.amount == Decimal("-95.50") and txn.category.value == "fuel"
    # A vehicle with a linked payment is archived, not deleted.
    refused = client.delete(f"{API}/{car['id']}", headers=OWNER)
    assert refused.status_code == 409 and refused.json()["code"] == "has_payments"


def test_payment_needs_finance_access_and_the_records_own_payer() -> None:
    canonical, _card = fuel_payment()
    van = vehicle(registration="VAN 9", ownership="business")
    entry = fuel(van["id"], MEMBER).json()
    refused = link("fuel_entry", entry["id"], canonical)
    assert refused.status_code == 403 and refused.json()["code"] == "not_record_owner"
    stranger = client.post(f"{API}/costs/fuel_entry/{entry['id']}/payment", headers=MEMBER, json={"canonical_id": canonical})
    assert stranger.status_code == 404 and stranger.json()["code"] == "transaction_not_found"  # never another person's ledger
    picker = client.get(f"{API}/payments", headers=OWNER, params={"amount": "150"}).json()["items"]
    assert picker[0]["canonical_id"] == canonical and picker[0]["remaining"] == "150.00"
    assert client.get(f"{API}/payments", headers=MEMBER).json()["items"] == []
    assert client.get(f"{API}/payments", headers=VIEWER).status_code == 403


def test_another_members_payment_shows_the_amount_but_not_their_ledger_id() -> None:
    canonical, _card = fuel_payment()
    van = vehicle(registration="VAN 7", ownership="business")
    entry = fuel(van["id"]).json()
    assert link("fuel_entry", entry["id"], canonical).status_code == 201
    seen = client.get(f"{API}/{van['id']}/fuel", headers=MEMBER).json()["items"][0]["payment"]
    assert seen["amount"] == "150.00" and seen["canonical_id"] is None and seen["link_id"] is None
    assert client.get(f"{API}/{van['id']}/report", headers=MEMBER).json()["spend"] == [{"currency": "SAR", "amount": "150.00"}]


def test_capital_purchase_is_depreciated_never_operating_cost() -> None:
    car = vehicle(purchase_price="120000", residual_value="30000", useful_life_months=60,
                  acquisition_date=(utc_now().date() - timedelta(days=400)).isoformat())
    report = client.get(f"{API}/{car['id']}/report", headers=OWNER).json()
    dep = report["ownership_cost"]["depreciation"]
    assert dep["monthly"] == "1500.00" and dep["months"] == 13 and dep["accumulated"] == "19500.00" and report["spend"] == []
    assert client.post(API, headers=OWNER, json={"registration": "Z 1", "country": "SA", "fuel_type": "diesel", "purchase_price": "10",
                                                 "residual_value": "20"}).json()["code"] == "invalid_residual"


def test_export_csv_and_privacy() -> None:
    car = vehicle(registration="CSV 1", odometer_km="100")
    fuel(car["id"], odometer_km="200", station="=cmd")
    csv = client.get(f"{API}/{car['id']}/export.csv", headers=OWNER)
    assert csv.status_code == 200 and "'=cmd" in csv.text and "not_linked" in csv.text
    van = vehicle(MEMBER, registration="BIZ 9", ownership="business")
    fuel(van["id"], OWNER, odometer_km="50")
    summary = client.get("/api/v1/privacy", headers=OWNER).json()
    assert summary["private"]["vehicle_records"] == 6  # car, its 2 readings and fuel entry; the fuel entry and reading on the business van
    assert client.post("/api/v1/privacy/delete", headers=OWNER, json={"confirm": "DELETE MY DATA"}).status_code == 200
    assert vehicle_memory_store.vehicles.keys() == {van["id"]}
    assert client.get(f"{API}/{van['id']}/fuel", headers=MEMBER).json()["total"] == 0


# -- the iPhone ------------------------------------------------------------------------------------------


def test_driving_session_from_the_iphone() -> None:
    car = vehicle(odometer_km="5000")
    url = VD.INGEST_PATH
    started = client.post(url, headers=OWNER, json={"action": "start", "idempotency_key": "s1"}).json()
    assert started["status"] == "started"
    assert client.post(url, headers=OWNER, json={"action": "start", "idempotency_key": "s2"}).json()["status"] == "already_started"
    ended = client.post(url, headers=OWNER, json={"action": "end", "idempotency_key": "e1", "odometer_km": "5032", "fuel_litres": "30",
                                                  "fuel_total": "70", "parking_total": "10"}).json()
    assert ended["status"] == "ended" and ended["trip"]["distance_km"] == "32.0" and ended["trip"]["distance_basis"] == "previous_reading"
    assert "link its payment" in ended["message"]
    assert client.post(url, headers=OWNER, json={"action": "end", "idempotency_key": "e1", "odometer_km": "5032"}).json()["status"] == "replayed"
    assert client.get(f"{API}/{car['id']}/fuel", headers=OWNER).json()["items"][0]["payment"] is None  # waits for its payment
    parking = client.get(f"{API}/{car['id']}/service", headers=OWNER).json()["items"][0]
    assert parking["kind"] == "parking" and parking["receipt_total"] == "10.00" and parking["trip_id"] == ended["trip"]["id"]
    lower = client.post(url, headers=OWNER, json={"action": "end", "odometer_km": "10"})
    assert lower.status_code == 409 and lower.json()["code"] == "odometer_decrease"


def test_driving_session_with_a_scoped_device_token() -> None:
    vehicle(odometer_km="100")
    created = client.post("/api/v1/integrations/iphone/devices", headers=OWNER, json={"name": "Phone", "scopes": ["vehicle"]})
    assert created.status_code == 201, created.text
    token = {"Authorization": f"Bearer {created.json()['token']}"}
    off = client.post(VD.INGEST_PATH, headers=token, json={"action": "test"})
    assert off.status_code == 409 and "is off" in off.json()["error"]["message"]
    assert client.put("/api/v1/integrations/iphone/integrations/vehicle.driving", headers=OWNER, json={"enabled": True}).status_code == 200
    tested = client.post(VD.INGEST_PATH, headers=token, json={"action": "test"})
    assert tested.status_code == 200 and tested.json()["status"] == "test_ok"
    assert client.post(VD.INGEST_PATH, headers=token, json={"action": "start"}).json()["status"] == "started"
    two = vehicle(registration="TWO 2")
    unknown = client.post(VD.INGEST_PATH, headers=token, json={"action": "end", "vehicle": "NOPE"})
    assert unknown.status_code == 404
    assert two["id"]
    events = client.get("/api/v1/integrations/iphone/integrations/vehicle.driving/events", headers=OWNER).json()
    assert any(e["code"] == "vehicle_not_found" for e in events["items"])
    assert "token" not in str(events).lower().replace("tokens", "")


def test_the_vehicle_target_refuses_records_of_other_people() -> None:
    canonical, _card = fuel_payment()
    van = vehicle(MEMBER, registration="VAN 3", ownership="business")
    entry = fuel(van["id"], MEMBER).json()
    direct = client.post(f"{FIN}/links", headers=OWNER, json={"canonical_id": canonical, "dimension": "vehicle", "role": "payment",
                                                             "target_type": "vehicle_fuel_entry", "target_id": entry["id"], "amount": "10",
                                                             "currency": "SAR"})
    assert direct.status_code == 404 and direct.json()["code"] == "target_not_found"
    assert "vehicle" in K.available_dimensions()
    assert D.integration("vehicle.driving") is not None
