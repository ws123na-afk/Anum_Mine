"""Maker and checker for adjustments, VAT return warnings and receipts proposed for bills in the Accountant review
(docs/business-books.md#adjustment-approval, #receipts-to-bills, docs/business-tax.md#warnings-in-the-accountant-review).

In-memory stores. Vendors, receipts and amounts are SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    MEMBER,
    OWNER,
    VIEWER,
    bill,
    clean,
    client,
    setup_books,
    today,
    vendor,
)

from anum_api import files
from anum_api.business import tax_review as TXR
from anum_api.business.tax_rules import return_deadline
from anum_api.files import file_store
from anum_api.schemas import TenantContext
from anum_api.vault.store import memory_vault_store

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
SECOND_OWNER = {**OWNER, "x-user-id": "owner_two"}


@pytest.fixture(autouse=True)
def vault(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(file_store, "storage", files.InMemoryObjectStorage())
    file_store.clear()
    memory_vault_store.clear()
    yield
    file_store.clear()
    memory_vault_store.clear()


def review(headers: dict = OWNER, **params: Any) -> dict:
    response = client.get(f"{API}/review", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def adjustment(headers: dict, amount: str = "5000", **body: Any) -> Any:
    payload = {"kind": "owner_contribution", "entry_date": today().isoformat(), "currency": "SAR", "amount": amount,
               "description": "Synthetic owner money in", **body}
    return client.post(f"{API}/adjustments", headers=headers, json=payload)


def accountant() -> None:
    assert client.post(f"{API}/accountants", headers=OWNER, json={"user_id": "member_books"}).status_code == 201


# -- maker and checker ------------------------------------------------------------------------------------------------


def test_an_accountants_adjustment_above_the_threshold_waits_and_its_maker_cannot_approve_it() -> None:
    setup_books(adjustment_approval_threshold="1000")
    accountant()
    small = adjustment(MEMBER, "500")
    assert small.status_code == 201 and small.json()["status"] == "active"
    made = adjustment(MEMBER, "5000")
    assert made.status_code == 201 and made.json()["status"] == "pending"
    pending = made.json()
    # Listed in the Accountant review, blocking the month, and not posted.
    item = next(i for i in review(MEMBER)["items"] if i["kind"] == "adjustment_approval")
    assert (item["ref_id"], item["can_act"], item["why_not"], item["action"], item["severity"]) == (pending["id"], False, "own_adjustment", "approve", "blocking")
    assert review(MEMBER)["close_months"][1]["adjustments_pending"] == 1
    journal = client.get(f"{API}/ledger/journal", headers=OWNER).json()
    assert not any(e.get("source_id") == pending["id"] for e in journal["items"])
    # The maker is refused; the owner approves; then it posts.
    refused = client.post(f"{API}/adjustments/{pending['id']}/approve", headers=MEMBER)
    assert refused.status_code == 403 and refused.json()["code"] == "own_adjustment"
    owner_item = next(i for i in review()["items"] if i["kind"] == "adjustment_approval")
    assert owner_item["can_act"] is True
    approved = client.post(f"{API}/adjustments/{pending['id']}/approve", headers=OWNER)
    assert approved.status_code == 200, approved.text
    assert (approved.json()["status"], approved.json()["approved_by"]) == ("active", "owner_books")
    again = client.post(f"{API}/adjustments/{pending['id']}/approve", headers=OWNER)
    assert again.status_code == 409 and again.json()["code"] == "not_pending"
    assert "adjustment_approval" not in review()["counts"]
    journal = client.get(f"{API}/ledger/journal", headers=OWNER).json()
    assert any(e.get("source_id") == pending["id"] for e in journal["items"])


def test_owners_post_directly_no_threshold_means_no_step_and_a_pending_one_can_be_rejected() -> None:
    setup_books()
    accountant()
    assert adjustment(MEMBER, "999999").json()["status"] == "active"  # no threshold: no approval step
    setup_books(adjustment_approval_threshold="1000")
    assert adjustment(OWNER, "999999").json()["status"] == "active"  # an owner's own adjustment is posted at once
    pending = adjustment(MEMBER, "2000").json()
    listed = client.get(f"{API}/adjustments", headers=OWNER, params={"status": "pending"}).json()
    assert [row["id"] for row in listed["items"]] == [pending["id"]]
    voided = client.post(f"{API}/adjustments/{pending['id']}/void", headers=OWNER, json={"reason": "Not agreed"})
    assert voided.status_code == 200 and voided.json()["status"] == "void"
    assert client.post(f"{API}/adjustments/{pending['id']}/approve", headers=OWNER).status_code == 409
    # A viewer never approves.
    other = adjustment(MEMBER, "3000").json()
    assert client.post(f"{API}/adjustments/{other['id']}/approve", headers=VIEWER).status_code == 403


def test_another_owner_can_approve_an_accountants_adjustment_in_another_currency_without_a_rate() -> None:
    setup_books(adjustment_approval_threshold="1000000")
    accountant()
    # Even a tiny amount in a currency with no rate waits: it cannot be shown to be below the threshold.
    pending = adjustment(MEMBER, "1", currency="USD").json()
    assert pending["status"] == "pending"
    assert client.post(f"{API}/adjustments/{pending['id']}/approve", headers=SECOND_OWNER).status_code == 200


# -- VAT return warnings ------------------------------------------------------------------------------------------------


def tax_settings(**body: Any) -> None:
    current = client.get(f"{API}/tax", headers=OWNER).json()["settings"]
    payload = {"jurisdiction": "SA", "registered": True, "registration_number": "300000000000003", "filing_frequency": "monthly",
               "version": current["version"] if current else None, **body}
    response = client.put(f"{API}/tax/settings", headers=OWNER, json=payload)
    assert response.status_code == 200, response.text


def test_vat_warnings_name_the_deadline_bills_without_a_tax_number_and_never_claim_compliance() -> None:
    setup_books()
    tax_settings()
    unnumbered = vendor("Synthetic Supplier")
    assert bill(unnumbered["id"], tax_code="standard", subtotal="1000", tax="150").status_code == 201
    items = [i for i in review()["items"] if i["kind"] == "vat"]
    codes = {i["params"]["code"] for i in items}
    assert "missing_tax_number" in codes
    previous_end = today().replace(day=1) - timedelta(days=1)
    due = return_deadline("SA", previous_end)
    assert due is not None
    days = (due - today()).days
    expected = "return_overdue" if days < 0 else "return_due_soon" if days <= TXR.DUE_SOON_DAYS else None
    if expected:
        found = next(i for i in items if i["params"]["code"] == expected)
        assert found["params"]["due"] == due.isoformat() and found["section"] == "tax"
    for item in items:
        assert "never files" in item["detected"] and "does not check compliance" in item["detected"]
        assert "compliant" not in item["detected"].lower()
    # A vendor with a tax number raises no such warning.
    numbered = vendor("Numbered Synthetic Supplier", tax_number="300000000000003")
    assert bill(numbered["id"], tax_code="standard", subtotal="500", tax="75").status_code == 201
    warning = next(i for i in review()["items"] if i["kind"] == "vat" and i["params"]["code"] == "missing_tax_number" and i["params"]["to"] >= today().isoformat())
    assert warning["params"]["count"] == "1"


def test_no_vat_warnings_for_a_business_that_is_not_registered_and_the_deadlines() -> None:
    setup_books()
    assert not [i for i in review()["items"] if i["kind"] == "vat"]
    tax_settings(registered=False, registration_number="")
    assert not [i for i in review()["items"] if i["kind"] == "vat"]
    assert return_deadline("SA", date(2026, 9, 30)) == date(2026, 10, 31)
    assert return_deadline("SA", date(2026, 12, 31)) == date(2027, 1, 31)
    assert return_deadline("AE", date(2026, 9, 30)) == date(2026, 10, 28)
    assert return_deadline("OTHER", date(2026, 9, 30)) is None


# -- receipts to bills ---------------------------------------------------------------------------------------------------


def receipt(name: str, *, merchant: str | None, total: str, on: date, currency: str = "SAR", access: str = "workspace") -> str:
    response = client.post("/api/v1/documents", params={"name": name, "title": f"Synthetic {name}", "category": "receipt", "access_class": access},
                           headers=OWNER, content=PDF + f"%{name}\n".encode())
    assert response.status_code == 201, response.text
    doc_id = response.json()["id"]
    context = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id=OWNER["x-user-id"])
    record = memory_vault_store.get_document(context, doc_id)
    assert record is not None
    memory_vault_store.save_document(context, record.model_copy(update={"receipt": {
        "merchant": merchant, "date": on.isoformat(), "total": total, "currency": currency, "tax": None, "reference": None, "items": []}}),
        record.row_version)
    return doc_id


def test_a_receipt_is_proposed_for_its_bill_and_attached_only_when_accepted() -> None:
    setup_books()
    landlord = vendor("Synthetic Landlord")
    made = bill(landlord["id"], subtotal="1000", tax="150").json()
    match = receipt("rent.pdf", merchant="SYNTHETIC LANDLORD LLC", total="1150", on=today() - timedelta(days=2))
    receipt("wrong-shop.pdf", merchant="Another Shop", total="1150", on=today())  # names someone else: never proposed
    receipt("private.pdf", merchant="Synthetic Landlord", total="1150", on=today(), access="private")  # private: never proposed
    listed = client.get(f"{API}/evidence/receipt-matches", headers=OWNER).json()
    assert [(m["bill_id"], m["document_id"], m["vendor_agrees"]) for m in listed["items"]] == [(made["id"], match, True)]
    item = next(i for i in review()["items"] if i["kind"] == "receipt_match")
    assert (item["ref_id"], item["action"], item["params"]["document_id"]) == (made["id"], "link", match)
    # Nothing was attached on its own.
    evidence = client.get(f"{API}/evidence", headers=OWNER, params={"source_type": "bill", "ids": made["id"]}).json()
    assert evidence["items"][made["id"]]["documents"] == []
    assert client.post(f"{API}/evidence/receipt-matches/accept", headers=VIEWER, json={"bill_id": made["id"], "document_id": match}).status_code == 403
    accepted = client.post(f"{API}/evidence/receipt-matches/accept", headers=OWNER, json={"bill_id": made["id"], "document_id": match})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["document_id"] == match
    attached = client.get(f"{API}/evidence", headers=OWNER, params={"source_type": "bill", "ids": made["id"]}).json()
    assert [d["document_id"] for d in attached["items"][made["id"]]["documents"]] == [match]
    assert client.get(f"{API}/evidence/receipt-matches", headers=OWNER).json()["items"] == []
    gone = client.post(f"{API}/evidence/receipt-matches/accept", headers=OWNER, json={"bill_id": made["id"], "document_id": match})
    assert gone.status_code == 409 and gone.json()["code"] == "match_gone"


def test_amount_and_date_window_decide_and_a_receipt_without_a_merchant_needs_one_bill() -> None:
    setup_books()
    landlord = vendor("Synthetic Landlord")
    made = bill(landlord["id"], subtotal="1000", tax="150").json()
    receipt("late.pdf", merchant="Synthetic Landlord", total="1150", on=today() - timedelta(days=30))  # outside the window
    receipt("other-amount.pdf", merchant="Synthetic Landlord", total="1149.99", on=today())  # another amount
    assert client.get(f"{API}/evidence/receipt-matches", headers=OWNER).json()["items"] == []
    plain = receipt("no-merchant.pdf", merchant=None, total="1150", on=today())
    found = client.get(f"{API}/evidence/receipt-matches", headers=OWNER).json()["items"]
    assert [(m["bill_id"], m["document_id"], m["vendor_agrees"]) for m in found] == [(made["id"], plain, False)]
    bill(vendor("Second Synthetic Vendor")["id"], subtotal="1000", tax="150")  # now two bills fit: no proposal without a merchant
    assert client.get(f"{API}/evidence/receipt-matches", headers=OWNER).json()["items"] == []
