"""An insurance or registration document in the vault, linked to its vehicle (docs/documents.md#vehicle-papers).

In-memory stores; every document and vehicle is SYNTHETIC. The person confirms the vehicle; the vehicle's expiry then
follows the document, and the daily reminder job sends its renewal reminder from that date.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from test_business_books import PERSONAL_OWNER, VIEWER, clean, client, today  # noqa: F401  (``clean`` resets every store)
from test_receipt_matches import DOCS, MEMBER_PERSONAL, PDF, VIEWER_PERSONAL, vault  # noqa: F401  (``vault`` resets the vault)

from anum_api.vault.store import memory_vault_store


def paper(title: str, category: str = "insurance", expires: date | None = None, seed: int = 0) -> str:
    made = client.post(DOCS, params={"name": f"{title}.pdf", "category": category}, headers=PERSONAL_OWNER, content=PDF + bytes([seed]))
    assert made.status_code == 201, made.text
    doc = memory_vault_store._documents[(PERSONAL_OWNER["x-tenant-id"], PERSONAL_OWNER["x-workspace-id"], made.json()["id"])]
    doc.title = title
    doc.expiry_date = expires
    return made.json()["id"]


def car(registration: str, nickname: str | None = None) -> dict[str, Any]:
    made = client.post("/api/v1/vehicles", headers=PERSONAL_OWNER, json={"registration": registration, "country": "SA", "fuel_type": "petrol_91",
                                                                         "nickname": nickname})
    assert made.status_code == 201, made.text
    return made.json()


def proposals(doc: str, headers: dict[str, str] = PERSONAL_OWNER) -> Any:
    return client.get(f"{DOCS}/{doc}/vehicle-proposals", headers=headers)


def confirm(doc: str, vehicle_id: str, headers: dict[str, str] = PERSONAL_OWNER) -> Any:
    return client.post(f"{DOCS}/{doc}/vehicle", headers=headers, json={"vehicle_id": vehicle_id})


def test_an_insurance_document_proposes_its_vehicle_and_confirm_sets_the_renewal_reminder() -> None:
    other = car("XYZ 9876", "Spare car")
    mine = car("ABC 1234", "Family car")
    expires = today() + timedelta(days=20)
    doc = paper("Motor insurance ABC-1234 2026", expires=expires)
    found = proposals(doc)
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["field"] == "insurance_expires_on" and body["expiry_date"] == expires.isoformat() and body["reason"] is None
    assert [v["vehicle_id"] for v in body["vehicles"]] == [mine["id"], other["id"]]  # the registration in the title comes first
    assert body["vehicles"][0]["matches_registration"] is True and body["vehicles"][1]["matches_registration"] is False
    assert body["vehicles"][0]["would_change"] is True and body["reminder_days"] == 30
    # Others: viewers see no documents; another member does not see a private one.
    assert proposals(doc, VIEWER_PERSONAL).status_code == 403
    assert proposals(doc, MEMBER_PERSONAL).status_code == 404
    assert confirm(doc, mine["id"], MEMBER_PERSONAL).status_code == 404
    assert confirm(doc, "veh_missing").status_code == 404
    done = confirm(doc, mine["id"])
    assert done.status_code == 200, done.text
    assert done.json()["linked_vehicles"] == [mine["id"]] and done.json()["vehicles"][0]["would_change"] is False
    vehicle = client.get(f"/api/v1/vehicles/{mine['id']}", headers=PERSONAL_OWNER).json()
    assert vehicle["insurance_expires_on"] == expires.isoformat()
    # The vehicle's renewal reminder is due now (20 days left, inside the 30-day window).
    due = client.get("/api/v1/vehicles/reminders", headers=PERSONAL_OWNER).json()["items"]
    assert [(item["kind"], item["days_left"]) for item in due if item["kind"] == "insurance"] == [("insurance", 20)]
    # The vault shows the link; confirming again changes nothing.
    links = client.get(f"{DOCS}/{doc}", headers=PERSONAL_OWNER).json()["links"]
    assert [(link["target_type"], link["target_id"]) for link in links] == [("vehicle", mine["id"])]
    assert confirm(doc, mine["id"]).status_code == 200
    assert len(client.get(f"{DOCS}/{doc}", headers=PERSONAL_OWNER).json()["links"]) == 1


def test_a_registration_document_sets_the_registration_expiry_and_papers_without_a_date_wait() -> None:
    mine = car("KSA 555")
    expires = today() + timedelta(days=200)
    reg = paper("Istimara", category="vehicle_registration", expires=expires, seed=1)
    assert proposals(reg).json()["field"] == "registration_expires_on"
    assert confirm(reg, mine["id"]).status_code == 200
    assert client.get(f"/api/v1/vehicles/{mine['id']}", headers=PERSONAL_OWNER).json()["registration_expires_on"] == expires.isoformat()
    undated = paper("Insurance card", seed=2)
    assert proposals(undated).json()["reason"] == "no_expiry"
    refused = confirm(undated, mine["id"])
    assert refused.status_code == 409 and "expiry" in refused.text
    contract = paper("Lease", category="contract", expires=expires, seed=3)
    assert proposals(contract).json()["reason"] == "not_a_vehicle_paper"
    assert confirm(contract, mine["id"]).status_code == 409
