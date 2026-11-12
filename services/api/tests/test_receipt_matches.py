"""A scanned receipt and the records it belongs to (docs/documents.md#receipt-matches, docs/vehicles.md#payments).

In-memory stores; every receipt, payment, purchase and vehicle is SYNTHETIC. A receipt's values count only once the
person confirmed them in the vault (here written straight into the document, as the confirm route stores them).
Nothing is linked by itself: the vault proposes payments and purchases and the person accepts; the Finance queue
proposes the payment of a fuel entry from its scanned receipt and the person accepts there.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    FIN,
    PERSONAL_OWNER,
    VIEWER,
    clean,
    client,
    today,
)
from test_finance_attribution import accept, account, canonical, live_links, pay, queue

from anum_api import file_policy, files
from anum_api.files import file_store
from anum_api.vault.store import memory_vault_store

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
MEMBER_PERSONAL = {**PERSONAL_OWNER, "x-user-id": "member_books", "x-user-roles": "member"}
VIEWER_PERSONAL = {**VIEWER, "x-workspace-id": PERSONAL_OWNER["x-workspace-id"]}
DOCS = "/api/v1/documents"


@pytest.fixture(autouse=True)
def vault(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(file_store, "storage", files.InMemoryObjectStorage())
    file_store.clear()
    memory_vault_store.clear()
    file_policy.set_file_scanner(None)
    yield
    file_store.clear()
    memory_vault_store.clear()


def receipt(total: str, merchant: str, back: int = 1, currency: str = "SAR", category: str = "receipt", seed: int = 0) -> str:
    """A receipt in the vault whose scanned values the person confirmed."""
    made = client.post(DOCS, params={"name": f"receipt-{seed}.pdf", "category": category}, headers=PERSONAL_OWNER, content=PDF + bytes([seed]))
    assert made.status_code == 201, made.text
    doc = memory_vault_store._documents[(PERSONAL_OWNER["x-tenant-id"], PERSONAL_OWNER["x-workspace-id"], made.json()["id"])]
    doc.receipt = {"total": total, "currency": currency, "date": (today() - timedelta(days=back)).isoformat(), "merchant": merchant,
                   "confirmed_by": PERSONAL_OWNER["x-user-id"]}
    return made.json()["id"]


def matches(document_id: str, headers: dict[str, str] = PERSONAL_OWNER) -> Any:
    return client.get(f"{DOCS}/{document_id}/matches", headers=headers)


def take(document_id: str, kind: str, target_id: str, headers: dict[str, str] = PERSONAL_OWNER) -> Any:
    return client.post(f"{DOCS}/{document_id}/matches/accept", headers=headers, json={"kind": kind, "target_id": target_id})


# -- receipt to payment and purchase (the vault) ----------------------------------------------------------------------


def test_a_confirmed_receipt_proposes_its_payment_and_accept_links_it_as_evidence() -> None:
    bank = account()
    paid = pay(bank["id"], "-86.40", "POS ALNOOR PHARMACY RIYADH", back=1)
    pay(bank["id"], "-86.40", "CITYLIGHT BOOKSHOP", back=1)  # same amount, another merchant: never proposed
    pay(bank["id"], "-86.41", "POS ALNOOR PHARMACY RIYADH", back=1)  # one halala off: never proposed
    doc = receipt("86.40", "Alnoor Pharmacy")
    found = matches(doc)
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["receipt"] == {"total": "86.40", "currency": "SAR", "on": (today() - timedelta(days=1)).isoformat(), "merchant": "Alnoor Pharmacy"}
    assert [p["target_id"] for p in body["payments"]] == [canonical(paid["id"])]
    assert body["payments"][0]["merchant_agrees"] is True and body["payments"][0]["confidence"] == "0.9"
    # Viewers do not see documents; another member does not see a private one.
    assert matches(doc, VIEWER_PERSONAL).status_code == 403
    assert matches(doc, MEMBER_PERSONAL).status_code == 404
    assert take(doc, "payment", canonical(paid["id"]), MEMBER_PERSONAL).status_code == 404
    # A payment that was not proposed is refused; the proposed one becomes the document's evidence link.
    assert take(doc, "payment", "fin_jrn_missing").status_code == 409
    done = take(doc, "payment", canonical(paid["id"]))
    assert done.status_code == 200, done.text
    assert done.json()["linked_payments"] == [canonical(paid["id"])] and done.json()["payments"] == []
    evidence = live_links("document")
    assert len(evidence) == 1 and evidence[0].role == "evidence" and evidence[0].amount is None
    assert take(doc, "payment", canonical(paid["id"])).status_code == 409  # already linked: not proposed again


def test_a_receipt_without_confirmed_values_proposes_nothing() -> None:
    made = client.post(DOCS, params={"name": "contract.pdf", "category": "contract"}, headers=PERSONAL_OWNER, content=PDF)
    assert matches(made.json()["id"]).json()["reason"] == "not_a_receipt"
    raw = client.post(DOCS, params={"name": "receipt.pdf", "category": "receipt"}, headers=PERSONAL_OWNER, content=PDF + b"x")
    found = matches(raw.json()["id"]).json()
    assert (found["reason"], found["payments"], found["purchases"]) == ("not_confirmed", [], [])


def test_a_receipt_proposes_the_purchase_without_one_and_accept_sets_its_receipt() -> None:
    purchase = client.post("/api/v1/purchases", headers=PERSONAL_OWNER, json={
        "merchant": "Example Electronics", "ordered_on": (today() - timedelta(days=2)).isoformat(), "currency": "SAR", "total": "450"})
    assert purchase.status_code in (200, 201), purchase.text
    client.post("/api/v1/purchases", headers=PERSONAL_OWNER, json={  # another shop with the same total: never proposed
        "merchant": "Other Furniture", "ordered_on": (today() - timedelta(days=2)).isoformat(), "currency": "SAR", "total": "450"})
    doc = receipt("450.00", "Example Electronics LLC", back=2, category="invoice", seed=1)
    found = matches(doc).json()
    assert [p["target_id"] for p in found["purchases"]] == [purchase.json()["id"]]
    done = take(doc, "purchase", purchase.json()["id"])
    assert done.status_code == 200, done.text
    assert done.json()["linked_purchases"] == [purchase.json()["id"]] and done.json()["purchases"] == []
    assert client.get(f"/api/v1/purchases/{purchase.json()['id']}", headers=PERSONAL_OWNER).json()["document_id"] == doc
    # The purchase has its receipt now: a second receipt is not proposed for it.
    other = receipt("450.00", "Example Electronics", back=2, category="invoice", seed=2)
    assert matches(other).json()["purchases"] == []


def dismiss(document_id: str, kind: str, target_id: str, headers: dict[str, str] = PERSONAL_OWNER, action: str = "dismiss") -> Any:
    return client.post(f"{DOCS}/{document_id}/matches/{action}", headers=headers, json={"kind": kind, "target_id": target_id})


def test_a_wrong_match_is_dismissed_remembered_and_restored() -> None:
    bank = account()
    wrong = pay(bank["id"], "-86.40", "POS ALNOOR PHARMACY JEDDAH", back=1)
    right = pay(bank["id"], "-86.40", "POS ALNOOR PHARMACY RIYADH", back=2)
    purchase = client.post("/api/v1/purchases", headers=PERSONAL_OWNER, json={
        "merchant": "Alnoor Pharmacy", "ordered_on": (today() - timedelta(days=1)).isoformat(), "currency": "SAR", "total": "86.40"}).json()
    doc = receipt("86.40", "Alnoor Pharmacy", seed=7)
    first = matches(doc).json()
    assert [p["target_id"] for p in first["payments"]] == [canonical(wrong["id"]), canonical(right["id"])] and first["dismissed"] == []
    # Only someone who may change the document dismisses; a match that is not proposed cannot be dismissed.
    assert dismiss(doc, "payment", canonical(wrong["id"]), MEMBER_PERSONAL).status_code == 404
    assert dismiss(doc, "payment", canonical(wrong["id"]), VIEWER_PERSONAL).status_code == 403
    assert dismiss(doc, "payment", "fin_jrn_missing").status_code == 409
    assert dismiss(doc, "payment", "bad id with spaces").status_code == 422
    done = dismiss(doc, "payment", canonical(wrong["id"]))
    assert done.status_code == 200, done.text
    body = done.json()
    assert [p["target_id"] for p in body["payments"]] == [canonical(right["id"])]
    [gone] = body["dismissed"]
    assert gone["kind"] == "payment" and gone["target_id"] == canonical(wrong["id"]) and "86.40" in gone["label"]
    assert gone["dismissed_by"] == PERSONAL_OWNER["x-user-id"]
    # Remembered: asked again (or dismissed twice) it stays out; accepting it is refused.
    assert [p["target_id"] for p in matches(doc).json()["payments"]] == [canonical(right["id"])]
    assert dismiss(doc, "payment", canonical(wrong["id"])).status_code == 200
    assert len(matches(doc).json()["dismissed"]) == 1
    assert take(doc, "payment", canonical(wrong["id"])).status_code == 409
    # A purchase is dismissed the same way.
    after = dismiss(doc, "purchase", purchase["id"]).json()
    assert after["purchases"] == [] and {d["kind"] for d in after["dismissed"]} == {"payment", "purchase"}
    # Restore: proposed again while it still fits; restoring twice changes nothing.
    restored = dismiss(doc, "payment", canonical(wrong["id"]), action="restore").json()
    assert {p["target_id"] for p in restored["payments"]} == {canonical(wrong["id"]), canonical(right["id"])}
    assert dismiss(doc, "payment", canonical(wrong["id"]), action="restore").status_code == 200
    assert [d["kind"] for d in matches(doc).json()["dismissed"]] == ["purchase"]
    events = [e.action for _, _, e in memory_vault_store._events if e.document_id == doc]
    assert "match_dismissed" in events and "match_restored" in events


def test_without_finance_access_the_payments_section_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    import anum_api.dependencies as deps
    from anum_api.authorization import Permission

    doc = receipt("10.00", "Example Cafe", seed=3)
    real = deps.has_permission
    # A role without finance:read (no built-in role of a personal workspace lacks it, so it is simulated here).
    monkeypatch.setattr(deps, "has_permission", lambda context, permission: permission != Permission.FINANCE_READ and real(context, permission))
    found = matches(doc).json()
    assert found["payments_unavailable"] == "finance_access" and found["payments"] == []


# -- a fuel entry's scanned receipt (Finance suggestions) ------------------------------------------------------------


def _fuel_with_receipt(doc: str, total: str | None = None) -> dict:
    car = client.post("/api/v1/vehicles", headers=PERSONAL_OWNER, json={"registration": "SYN 4321", "country": "SA", "fuel_type": "petrol_91"})
    assert car.status_code == 201, car.text
    body: dict[str, Any] = {"filled_at": (today() - timedelta(days=3)).isoformat() + "T08:00:00+00:00", "litres": "40", "odometer_km": "10400",
                            "document_id": doc}
    if total is not None:
        body["total"] = total
    filled = client.post(f"/api/v1/vehicles/{car.json()['id']}/fuel", headers=PERSONAL_OWNER, json=body)
    assert filled.status_code == 201, filled.text
    return filled.json()


def test_a_fuel_entry_with_only_a_scanned_receipt_is_proposed_for_its_payment() -> None:
    bank = account()
    doc = receipt("152.30", "Sahara Petrol", back=1, seed=4)
    entry = _fuel_with_receipt(doc)  # no pump total typed: the receipt's total and date are used
    paid = pay(bank["id"], "-152.30", "POS SAHARA PETROL", back=1)
    pay(bank["id"], "-152.30", "CITYLIGHT GROCERY", back=1)  # same amount, no fuel words, another merchant
    items = [i for i in queue("attribution") if i["params"]["dimension"] == "vehicle"]
    assert len(items) == 1, items
    item = items[0]
    assert item["params"]["source"] == "receipt" and item["action"]["link"]["target_id"] == entry["id"]
    assert item["evidence"][0]["ref_id"] == paid["id"] and item["evidence"][-1]["ref_id"] == doc and item["confidence"] == "0.9"
    assert accept(item).status_code == 200
    assert [l.target_id for l in live_links("vehicle")] == [entry["id"]]
    assert [i for i in queue("attribution") if i["params"]["dimension"] == "vehicle"] == []


def test_a_receipt_naming_another_merchant_is_not_proposed() -> None:
    bank = account()
    doc = receipt("152.30", "Sahara Petrol", back=1, seed=5)
    _fuel_with_receipt(doc)
    pay(bank["id"], "-152.30", "BLUEWAVE FUEL", back=1)  # fuel words, but the receipt names another station
    assert [i for i in queue("attribution") if i["params"]["dimension"] == "vehicle" and i["params"].get("record") != "none"] == []


def test_a_typed_pump_total_that_disagrees_with_the_receipt_is_proposed_without_it() -> None:
    bank = account()
    doc = receipt("99.00", "Example Petrol Station", back=1, seed=6)
    _fuel_with_receipt(doc, total="150")
    pay(bank["id"], "-150.00", "EXAMPLE FUEL STATION", back=2)
    items = [i for i in queue("attribution") if i["params"]["dimension"] == "vehicle"]
    assert len(items) == 1 and "source" not in items[0]["params"] and items[0]["confidence"] == "0.85"
