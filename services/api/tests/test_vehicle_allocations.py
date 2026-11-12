"""A business vehicle paid from a personal card (docs/vehicles.md#business-allocations). In-memory stores; SYNTHETIC data.

The owner allocates a personal payment to the business in Finance; on the business vehicle's fuel entry the person who
logged it picks that allocation, and the vehicle's cost shows it. The business never sees the payer's ledger. One
allocation is one record's cost; taking the allocation back in Finance takes it out of the vehicle's cost.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    FIN,
    MEMBER,
    OWNER,
    PERSONAL_OWNER,
    VIEWER,
    allocate,
    clean,
    client,
    fuel_payment,
    today,
)

VEH = "/api/v1/vehicles"


def business_car(headers: dict = OWNER, ownership: str = "business") -> dict:
    car = client.post(VEH, headers=headers, json={"registration": f"SYN {ownership[:3].upper()} 77", "country": "SA", "fuel_type": "petrol_91",
                                                  "ownership": ownership})
    assert car.status_code == 201, car.text
    return car.json()


def fuel(car_id: str, headers: dict = OWNER, total: str = "150") -> dict:
    made = client.post(f"{VEH}/{car_id}/fuel", headers=headers, json={
        "filled_at": (today() - timedelta(days=1)).isoformat() + "T08:00:00+00:00", "litres": "40", "total": total, "odometer_km": "10400"})
    assert made.status_code == 201, made.text
    return made.json()


def test_a_personal_payment_allocated_to_the_business_becomes_the_business_vehicles_cost() -> None:
    canonical, _card = fuel_payment()
    allocated = allocate(canonical)
    car = business_car()
    entry = fuel(car["id"])
    # The picker lists this business's allocations the caller made, nearest to the figure first; never the ledger.
    found = client.get(f"{VEH}/allocations", headers=OWNER, params={"amount": "150", "around": today().isoformat()})
    assert found.status_code == 200, found.text
    items = found.json()["items"]
    assert len(items) == 1 and items[0]["amount"] == "150.00" and items[0]["currency"] == "SAR" and len(items[0]["source_ref"]) == 6
    assert "canonical_id" not in items[0]
    alloc_id = items[0]["allocation_id"]
    # Viewers cannot; another member does not see the owner's allocations as theirs to use.
    assert client.get(f"{VEH}/allocations", headers=VIEWER).status_code == 403
    assert client.get(f"{VEH}/allocations", headers=MEMBER).json()["items"] == []
    used = client.post(f"{VEH}/costs/fuel_entry/{entry['id']}/allocation", headers=OWNER, json={"allocation_id": alloc_id})
    assert used.status_code == 201, used.text
    payment = used.json()["payment"]
    assert (payment["source"], payment["status"], payment["amount"], payment["canonical_id"]) == ("allocation", "active", "150.00", "")
    again = client.post(f"{VEH}/costs/fuel_entry/{entry['id']}/allocation", headers=OWNER, json={"allocation_id": alloc_id})
    assert again.status_code == 200 and again.json()["replayed"] is True
    # The vehicle's cost and the overview count it; the picker no longer offers it.
    report = client.get(f"{VEH}/{car['id']}/report", headers=OWNER).json()
    assert report["spend"] == [{"currency": "SAR", "amount": "150.00"}] and report["unlinked_records"] == 0
    overview = client.get(f"{VEH}/overview", headers=OWNER).json()
    assert overview["spend"] == [{"currency": "SAR", "amount": "150.00"}]
    assert client.get(f"{VEH}/allocations", headers=OWNER).json()["items"] == []
    # A second record cannot count the same allocation.
    other = fuel(car["id"], total="150")
    refused = client.post(f"{VEH}/costs/fuel_entry/{other['id']}/allocation", headers=OWNER, json={"allocation_id": alloc_id})
    assert refused.status_code == 409 and refused.json()["code"] == "allocation_used"
    # Taking the allocation back in Finance takes it out of the vehicle's cost (read as it is now).
    assert client.delete(f"{FIN}/allocations/{allocated['link']['id']}", headers=PERSONAL_OWNER).status_code == 200
    rows = client.get(f"{VEH}/{car['id']}/fuel", headers=OWNER).json()["items"]
    mine = next(r for r in rows if r["id"] == entry["id"])
    assert (mine["payment"]["status"], mine["payment"]["status_reason"]) == ("void", "allocation_removed")
    assert client.get(f"{VEH}/overview", headers=OWNER).json()["spend"] == []
    assert client.get(f"{VEH}/{car['id']}/report", headers=OWNER).json()["unlinked_records"] == 2
    # Releasing it clears the record; repeating changes nothing.
    assert client.delete(f"{VEH}/costs/fuel_entry/{entry['id']}/allocation", headers=OWNER).status_code == 204
    assert client.delete(f"{VEH}/costs/fuel_entry/{entry['id']}/allocation", headers=OWNER).status_code == 204
    rows = client.get(f"{VEH}/{car['id']}/fuel", headers=OWNER).json()["items"]
    assert next(r for r in rows if r["id"] == entry["id"])["payment"] is None


def test_only_the_records_author_and_only_a_business_vehicle_use_an_allocation() -> None:
    canonical, _card = fuel_payment()
    allocate(canonical)
    alloc_id = client.get(f"{VEH}/allocations", headers=OWNER).json()["items"][0]["allocation_id"]
    car = business_car()
    members_entry = fuel(car["id"], headers=MEMBER)
    refused = client.post(f"{VEH}/costs/fuel_entry/{members_entry['id']}/allocation", headers=OWNER, json={"allocation_id": alloc_id})
    assert refused.status_code == 403 and refused.json()["code"] == "not_record_owner"
    personal = business_car(ownership="personal")
    own = fuel(personal["id"])
    refused = client.post(f"{VEH}/costs/fuel_entry/{own['id']}/allocation", headers=OWNER, json={"allocation_id": alloc_id})
    assert refused.status_code == 422 and refused.json()["code"] == "not_business_vehicle"
    entry = fuel(car["id"])
    missing = client.post(f"{VEH}/costs/fuel_entry/{entry['id']}/allocation", headers=OWNER, json={"allocation_id": "alloc_missing"})
    assert missing.status_code == 404 and missing.json()["code"] == "allocation_not_found"
    # The vehicle's purchase is linked as a payment, never an allocation.
    assert client.post(f"{VEH}/costs/purchase/{car['id']}/allocation", headers=OWNER, json={"allocation_id": alloc_id}).status_code == 422
    # A record whose cost is an allocation refuses a payment link until it is released.
    assert client.post(f"{VEH}/costs/fuel_entry/{entry['id']}/allocation", headers=OWNER, json={"allocation_id": alloc_id}).status_code == 201
    linked = client.post(f"{VEH}/costs/fuel_entry/{entry['id']}/payment", headers=OWNER, json={"canonical_id": canonical})
    assert linked.status_code == 409 and linked.json()["code"] == "already_linked"
    assert Decimal(client.get(f"{VEH}/{car['id']}/fuel", headers=OWNER).json()["items"][0]["payment"]["amount"]) == Decimal("150.00")


def test_the_export_names_a_business_allocation() -> None:
    canonical, _card = fuel_payment()
    allocate(canonical)
    alloc_id = client.get(f"{VEH}/allocations", headers=OWNER).json()["items"][0]["allocation_id"]
    car = business_car()
    entry = fuel(car["id"])
    assert client.post(f"{VEH}/costs/fuel_entry/{entry['id']}/allocation", headers=OWNER, json={"allocation_id": alloc_id}).status_code == 201
    csv_text = client.get(f"{VEH}/{car['id']}/export.csv", headers=OWNER).text
    assert "allocation_active" in csv_text and canonical not in csv_text


def test_a_fitting_allocation_is_proposed_on_the_record_and_confirmed_by_the_person() -> None:
    canonical, _card = fuel_payment()
    allocate(canonical)
    car = business_car()
    entry = fuel(car["id"])
    odd = fuel(car["id"], total="99")  # no allocation of this figure: nothing proposed
    rows = {r["id"]: r for r in client.get(f"{VEH}/{car['id']}/fuel", headers=OWNER).json()["items"]}
    proposal = rows[entry["id"]]["allocation_proposal"]
    assert proposal is not None and proposal["amount"] == "150.00" and proposal["currency"] == "SAR" and proposal["days_apart"] <= 7
    assert "canonical_id" not in proposal and len(proposal["source_ref"]) == 6
    assert rows[odd["id"]]["allocation_proposal"] is None
    # Only to the person who may use it: a member who logged none of these records sees no proposal (viewers see no records).
    assert all(r["allocation_proposal"] is None for r in client.get(f"{VEH}/{car['id']}/fuel", headers=MEMBER).json()["items"])
    # Confirming uses the ordinary route; then the record has its cost and nothing is proposed again.
    used = client.post(f"{VEH}/costs/fuel_entry/{entry['id']}/allocation", headers=OWNER, json={"allocation_id": proposal["allocation_id"]})
    assert used.status_code == 201, used.text
    later = fuel(car["id"])  # the same figure again: the allocation is used, so nothing is proposed
    rows = {r["id"]: r for r in client.get(f"{VEH}/{car['id']}/fuel", headers=OWNER).json()["items"]}
    assert rows[entry["id"]]["allocation_proposal"] is None and rows[later["id"]]["allocation_proposal"] is None
    # A personal vehicle never gets a business allocation proposed.
    personal = business_car(ownership="personal")
    fuel(personal["id"])
    assert all(r["allocation_proposal"] is None for r in client.get(f"{VEH}/{personal['id']}/fuel", headers=OWNER).json()["items"])
