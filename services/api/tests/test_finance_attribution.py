"""Attribution suggestions in the one queue (docs/finance.md#attribution-suggestions). In-memory stores.

Payments, merchants, vehicles, purchases and debts are SYNTHETIC (``test_business_books`` fixtures). Accept makes the link
through ``finance.links``, so its checks apply; nothing is ever linked by itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    BUSINESS,
    FIN,
    PERSONAL_OWNER,
    clean,
    client,
    setup_books,
    today,
)

from anum_api.finance.links_store import links_memory_store
from anum_api.finance.rules import AutomationRefused, require_automatable
from anum_api.finance.rules_store import rules_memory_store

VIEWER_PERSONAL = {**PERSONAL_OWNER, "x-user-id": "viewer_books", "x-user-roles": "viewer"}


@pytest.fixture(autouse=True)
def rules() -> Iterator[None]:
    rules_memory_store.clear()
    yield
    rules_memory_store.clear()


def account(**body: Any) -> dict:
    response = client.post(f"{FIN}/accounts", headers=PERSONAL_OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def pay(account_id: str, amount: str, words: str, back: int = 2) -> dict:
    response = client.post(f"{FIN}/transactions", headers=PERSONAL_OWNER, json={
        "account_id": account_id, "amount": amount, "description": words, "posted_on": (today() - timedelta(days=back)).isoformat()})
    assert response.status_code == 201, response.text
    return response.json()


def canonical(transaction_id: str) -> str:
    from anum_api.finance.sms_store import sms_memory_store
    from anum_api.schemas import TenantContext

    context = TenantContext(tenant_id=PERSONAL_OWNER["x-tenant-id"], workspace_id=PERSONAL_OWNER["x-workspace-id"], user_id=PERSONAL_OWNER["x-user-id"])
    entry = sms_memory_store.entry_for_transaction(context, transaction_id)
    return entry.canonical_id or entry.id


def queue(kind: str | None = None) -> list[dict]:
    response = client.get(f"{FIN}/suggestions", headers=PERSONAL_OWNER, params={"limit": 100, **({"kind": kind} if kind else {})})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def accept(item: dict, headers: dict = PERSONAL_OWNER) -> Any:
    return client.post(f"{FIN}/suggestions/accept", headers=headers, json={"key": item["key"], "payload_hash": item["payload_hash"]})


def live_links(dimension: str) -> list[Any]:
    return [l for l in links_memory_store.links.values() if l.dimension == dimension and getattr(l.status, "value", l.status) != "void"]


def test_the_same_merchant_allocated_before_suggests_the_business_with_its_project_and_treatment() -> None:
    setup_books()
    bank = account()
    first = pay(bank["id"], "-120.00", "EXAMPLE PRINT SHOP", back=20)
    second = pay(bank["id"], "-80.00", "EXAMPLE PRINT SHOP", back=2)
    made = client.post(f"{FIN}/allocations", headers=PERSONAL_OWNER, json={
        "canonical_id": canonical(first["id"]), "workspace_id": BUSINESS, "amount": "120.00", "currency": "SAR", "category": "shopping",
        "project_id": "prj_synthetic", "treatment": "reimbursable"})
    assert made.status_code == 201, made.text
    items = [i for i in queue("attribution") if i["params"]["dimension"] == "business"]
    assert len(items) == 1
    item = items[0]
    assert item["evidence"][0]["ref_id"] == second["id"] and Decimal(item["confidence"]) >= Decimal("0.5")
    assert item["params"]["business"] == BUSINESS and item["params"]["treatment"] == "reimbursable" and item["params"]["project"] == "prj_synthetic"
    assert item["action"]["kind"] == "link" and item["action"]["link"]["amount"] == "80.00"
    # A viewer cannot accept; the owner's accept writes the allocation through the links API, once.
    assert accept(item, VIEWER_PERSONAL).status_code == 403
    done = accept(item)
    assert done.status_code == 200, done.text and done.json()["link_id"]
    rows = client.get(f"{API}/allocations", headers={**PERSONAL_OWNER, "x-workspace-id": BUSINESS}).json()["items"]
    assert sorted((r["amount"], r["treatment"], r["project_id"]) for r in rows) == [
        ("120.00", "reimbursable", "prj_synthetic"), ("80.00", "reimbursable", "prj_synthetic")]
    assert accept(item).status_code == 409
    assert not [i for i in queue("attribution") if i["params"]["dimension"] == "business"]


def test_a_purchase_and_a_debt_are_suggested_and_a_refused_link_releases_the_suggestion(monkeypatch: pytest.MonkeyPatch) -> None:
    bank = account()
    purchase = client.post("/api/v1/purchases", headers=PERSONAL_OWNER, json={
        "merchant": "Gadgethub Electronics", "ordered_on": (today() - timedelta(days=3)).isoformat(), "currency": "SAR", "total": "450"})
    assert purchase.status_code in (200, 201), purchase.text
    paid = pay(bank["id"], "-450.00", "POS GADGETHUB ELECTRONICS RIYADH")
    debt = client.post(f"{FIN}/debts", headers=PERSONAL_OWNER, json={"name": "Synthetic Auto Finance", "kind": "loan", "balance": "9000",
                                                                         "currency": "SAR", "minimum_payment": "750"})
    assert debt.status_code in (200, 201), debt.text
    instalment = pay(bank["id"], "-750.00", "SYNTHETIC AUTO FINANCE INSTALMENT")
    items = {i["params"]["dimension"]: i for i in queue("attribution")}
    assert items["purchase"]["evidence"][0]["ref_id"] == paid["id"] and Decimal(items["purchase"]["confidence"]) == Decimal("0.85")
    assert items["debt"]["evidence"][0]["ref_id"] == instalment["id"] and Decimal(items["debt"]["confidence"]) == Decimal("0.85")
    assert accept(items["purchase"]).status_code == 200
    assert len(live_links("purchase")) == 1 and live_links("purchase")[0].amount == Decimal("450.00")
    # The debt link goes through the links checks: a refusal there is the answer and the suggestion stays open (its
    # claim is released). Here the payment was linked in another tab between showing and accepting.
    from anum_api.finance import links as K
    from anum_api.schemas import TenantContext

    context = TenantContext(tenant_id=PERSONAL_OWNER["x-tenant-id"], workspace_id=PERSONAL_OWNER["x-workspace-id"], user_id=PERSONAL_OWNER["x-user-id"])
    real = K.create

    def raced(ctx: Any, request: Any, **kwargs: Any) -> Any:
        real(ctx, request, **kwargs)  # the other tab
        return real(ctx, K.LinkRequest(canonical_id=request.canonical_id, dimension=request.dimension, role=request.role,
                                       target_type=request.target_type, target_id=request.target_id, amount=request.amount,
                                       currency=request.currency, idempotency_key="another-tab"))

    monkeypatch.setattr(K, "create", raced)
    refused = accept(items["debt"])
    assert refused.status_code == 409 and refused.json()["code"] == "over_allocated"
    assert rules_memory_store.get_decision(context, items["debt"]["key"]) is None
    assert len(live_links("debt")) == 1


def test_a_bill_due_date_uses_the_one_payment_matcher_and_accept_marks_it_paid() -> None:
    bank = account()
    due = today() - timedelta(days=5)
    bill = client.post(f"{FIN}/bills", headers=PERSONAL_OWNER, json={"name": "Example Telecom", "amount": "230", "currency": "SAR",
                                                                         "cadence": "monthly", "next_due_on": due.isoformat(), "account_id": bank["id"]})
    assert bill.status_code in (200, 201), bill.text
    pay(bank["id"], "-230.00", "EXAMPLE TELECOM BILL PAYMENT", back=4)
    items = [i for i in queue("payment_match") if i["action"]["kind"] == "link"]
    assert len(items) == 1 and items[0]["action"]["link"]["dimension"] == "bill"
    assert accept(items[0]).status_code == 200
    schedule = client.get(f"{FIN}/bill-schedule", headers=PERSONAL_OWNER, params={"from": due.isoformat(), "to": due.isoformat()}).json()["items"]
    assert schedule[0]["status"] == "paid"


def test_fuel_with_a_recorded_fuel_entry_suggests_the_vehicle_cost() -> None:
    bank = account()
    car = client.post("/api/v1/vehicles", headers=PERSONAL_OWNER, json={"registration": "SYN 1234", "country": "SA", "fuel_type": "petrol_91"})
    assert car.status_code == 201, car.text
    filled = client.post(f"/api/v1/vehicles/{car.json()['id']}/fuel", headers=PERSONAL_OWNER, json={
        "filled_at": (today() - timedelta(days=1)).isoformat() + "T08:00:00+00:00", "litres": "40", "total": "150", "odometer_km": "10400"})
    assert filled.status_code == 201, filled.text
    pay(bank["id"], "-150.00", "EXAMPLE FUEL STATION", back=1)
    items = [i for i in queue("attribution") if i["params"]["dimension"] == "vehicle"]
    assert len(items) == 1 and items[0]["action"]["link"]["target_type"] == "vehicle_fuel_entry"
    assert accept(items[0]).status_code == 200
    assert len(live_links("vehicle")) == 1


def test_no_policy_can_link_on_its_own() -> None:
    with pytest.raises(AutomationRefused):
        require_automatable("link")
    refused = client.put(f"{FIN}/automation", headers=PERSONAL_OWNER, json={"auto_link": True})
    assert refused.status_code == 422
