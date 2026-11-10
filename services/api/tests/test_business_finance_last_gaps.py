"""Dismissing a receipt proposed for a bill, and VAT return deadlines for any country
(docs/business-books.md#receipts-to-bills, docs/business-tax.md#filing-deadlines).

In-memory stores. Vendors, receipts and amounts are SYNTHETIC.
"""

from __future__ import annotations

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
from test_business_finance_depth import accountant, receipt, review, vault  # noqa: F401  (``vault`` resets the Document vault)

from anum_api.business import tax_rules as TR
from anum_api.dependencies import memory_repository

MATCHES = f"{API}/evidence/receipt-matches"


def _audits(action: str) -> list[Any]:
    return [r for r in memory_repository.store.audit_records if r.action == f"business.books.{action}"]


# -- dismissing a proposed receipt ---------------------------------------------------------------------------------------


def test_a_dismissed_receipt_is_never_proposed_again_for_that_bill_and_can_be_restored() -> None:
    setup_books()
    landlord = vendor("Synthetic Landlord")
    made = bill(landlord["id"], subtotal="1000", tax="150").json()
    doc = receipt("rent.pdf", merchant="SYNTHETIC LANDLORD LLC", total="1150", on=today() - timedelta(days=1))
    pair = {"bill_id": made["id"], "document_id": doc}
    assert [m["document_id"] for m in client.get(MATCHES, headers=OWNER).json()["items"]] == [doc]

    dismissed = client.post(f"{MATCHES}/dismiss", headers=OWNER, json=pair)
    assert dismissed.status_code == 201, dismissed.text
    row = dismissed.json()
    assert (row["bill_id"], row["document_id"], row["dismissed_by"]) == (made["id"], doc, "owner_books")
    assert [a.target for a in _audits("receipt_match_dismissed")] == [made["id"]]
    # Gone from the endpoint and from the Accountant review; accepting it now is refused.
    assert client.get(MATCHES, headers=OWNER).json()["items"] == []
    assert not [i for i in review()["items"] if i["kind"] == "receipt_match"]
    gone = client.post(f"{MATCHES}/accept", headers=OWNER, json=pair)
    assert gone.status_code == 409 and gone.json()["code"] == "match_gone"
    # Dismissing it again answers the first dismissal (not audited twice).
    again = client.post(f"{MATCHES}/dismiss", headers=OWNER, json=pair)
    assert again.status_code == 200 and again.json()["id"] == row["id"]
    assert len(_audits("receipt_match_dismissed")) == 1

    # Show dismissed: the bill's reference, vendor and amount and the document's title.
    listed = client.get(f"{MATCHES}/dismissed", headers=OWNER).json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert (item["id"], item["vendor"], item["amount"], item["currency"], item["document_title"]) == (row["id"], "Synthetic Landlord", "1150.00", "SAR",
                                                                                                    "Synthetic rent.pdf")
    assert item["bill_found"] is True and item["document_found"] is True

    # Restore: proposed again, audited; restoring twice is a plain 404.
    restored = client.delete(f"{MATCHES}/dismissed/{row['id']}", headers=OWNER)
    assert restored.status_code == 204, restored.text
    assert [a.target for a in _audits("receipt_match_restored")] == [made["id"]]
    assert [m["document_id"] for m in client.get(MATCHES, headers=OWNER).json()["items"]] == [doc]
    assert client.get(f"{MATCHES}/dismissed", headers=OWNER).json()["items"] == []
    twice = client.delete(f"{MATCHES}/dismissed/{row['id']}", headers=OWNER)
    assert twice.status_code == 404 and twice.json()["code"] == "not_found"


def test_a_dismissal_is_per_pair_so_the_document_can_still_fit_another_bill() -> None:
    setup_books()
    landlord = vendor("Synthetic Landlord")
    first = bill(landlord["id"], subtotal="1000", tax="150", bill_date=(today() - timedelta(days=1)).isoformat()).json()
    second = bill(landlord["id"], subtotal="1000", tax="150", bill_date=(today() - timedelta(days=4)).isoformat()).json()
    doc = receipt("rent.pdf", merchant="Synthetic Landlord", total="1150", on=today() - timedelta(days=1))
    assert [m["bill_id"] for m in client.get(MATCHES, headers=OWNER).json()["items"]] == [first["id"]]
    assert client.post(f"{MATCHES}/dismiss", headers=OWNER, json={"bill_id": first["id"], "document_id": doc}).status_code == 201
    # The same receipt is now proposed for the other bill it fits.
    assert [(m["bill_id"], m["document_id"]) for m in client.get(MATCHES, headers=OWNER).json()["items"]] == [(second["id"], doc)]


def test_only_owners_and_accountants_dismiss_or_restore_and_a_pair_not_proposed_is_refused() -> None:
    setup_books()
    landlord = vendor("Synthetic Landlord")
    made = bill(landlord["id"], subtotal="1000", tax="150").json()
    doc = receipt("rent.pdf", merchant="Synthetic Landlord", total="1150", on=today())
    pair = {"bill_id": made["id"], "document_id": doc}
    # The maker of the books (a member who is not an accountant) and a viewer are refused everything.
    for headers in (MEMBER, VIEWER):
        assert client.post(f"{MATCHES}/dismiss", headers=headers, json=pair).status_code == 403
        assert client.get(f"{MATCHES}/dismissed", headers=headers).status_code == 403
    assert client.get(MATCHES, headers=OWNER).json()["total"] == 1
    # An accountant may dismiss; then the member still cannot restore it, the owner can.
    accountant()
    by_accountant = client.post(f"{MATCHES}/dismiss", headers=MEMBER, json=pair)
    assert by_accountant.status_code == 201, by_accountant.text
    assert client.delete(f"{MATCHES}/dismissed/{by_accountant.json()['id']}", headers=VIEWER).status_code == 403
    assert client.delete(f"{MATCHES}/dismissed/{by_accountant.json()['id']}", headers=OWNER).status_code == 204
    # A pair that is not proposed (another amount) cannot be dismissed; a bad id is a 404.
    other = receipt("other.pdf", merchant="Synthetic Landlord", total="999", on=today())
    refused = client.post(f"{MATCHES}/dismiss", headers=OWNER, json={"bill_id": made["id"], "document_id": other})
    assert refused.status_code == 409 and refused.json()["code"] == "match_gone"
    assert client.delete(f"{MATCHES}/dismissed/brdis_missing", headers=OWNER).status_code == 404


# -- VAT return deadlines -------------------------------------------------------------------------------------------------


def save_tax(headers: dict = OWNER, **body: Any) -> Any:
    current = client.get(f"{API}/tax", headers=OWNER).json()["settings"]
    payload = {"jurisdiction": "OTHER", "registered": True, "registration_number": "SYNTHETIC-123", "filing_frequency": "quarterly",
               "version": current["version"] if current else None, **body}
    return client.put(f"{API}/tax/settings", headers=headers, json=payload)


def vat_codes() -> dict[str, dict]:
    return {i["params"]["code"]: i for i in review()["items"] if i["kind"] == "vat"}


def test_built_in_deadlines_for_the_gcc_and_the_owners_due_day_wins() -> None:
    end = date(2026, 9, 30)
    assert TR.return_deadline("SA", end) == date(2026, 10, 31)
    assert TR.return_deadline("AE", end) == date(2026, 10, 28)
    assert TR.return_deadline("OTHER", end, country="BH") == date(2026, 10, 31)
    assert TR.return_deadline("OTHER", date(2026, 12, 31), country="BH") == date(2027, 1, 31)
    assert TR.return_deadline("OTHER", end, country="OM") == date(2026, 10, 30)
    # Kuwait and Qatar have no VAT, another country has no preset: no deadline, never a guess.
    for place in ("KW", "QA", "FR", None):
        assert TR.return_deadline("OTHER", end, country=place) is None
    assert TR.deadline_source("OTHER", "KW") == "no_vat" and TR.deadline_source("OTHER", "FR") == "none"
    assert TR.deadline_source("OTHER", "OM") == "preset" and TR.deadline_source("SA") == "preset"
    # The owner's own due day always wins over a preset.
    assert TR.return_deadline("SA", end, due_days=20) == date(2026, 10, 20)
    assert TR.return_deadline("OTHER", end, country="FR", due_days=45) == date(2026, 11, 14)
    assert TR.deadline_source("AE", None, 15) == "owner"
    for preset in TR.FILING_PRESETS.values():
        assert preset.source.startswith("https://")


def test_without_a_preset_or_owner_values_the_review_asks_for_them_and_never_guesses() -> None:
    setup_books()
    assert save_tax(country="FR").status_code == 200
    overview = client.get(f"{API}/tax", headers=OWNER).json()
    assert overview["filing_deadline"]["source"] == "none" and overview["filing_deadline"]["country"] == "FR"
    assert {"BH", "OM", "SA", "AE"} <= set(overview["filing_presets"]) and overview["no_vat_countries"] == ["KW", "QA"]
    codes = vat_codes()
    assert "filing_deadline_missing" in codes and "return_due_soon" not in codes and "return_overdue" not in codes
    item = codes["filing_deadline_missing"]
    assert "Set your VAT filing period and due day in Tax settings" in item["detected"]
    assert "never files" in item["detected"] and "does not check compliance with ZATCA or FTA rules" in item["detected"]
    # The owner enters their own period and due day: the request disappears and the deadline follows their numbers.
    saved = save_tax(country="FR", filing_frequency="monthly", filing_due_days=10)
    assert saved.status_code == 200, saved.text
    assert (saved.json()["settings"]["filing_due_days"], saved.json()["filing_deadline"]["source"]) == (10, "owner")
    codes = vat_codes()
    assert "filing_deadline_missing" not in codes
    previous_end = today().replace(day=1) - timedelta(days=1)
    due = previous_end + timedelta(days=10)
    days = (due - today()).days
    expected = "return_overdue" if days < 0 else "return_due_soon" if days <= 14 else None
    if expected:
        assert codes[expected]["params"]["due"] == due.isoformat()


def test_kuwait_and_qatar_say_there_is_no_vat_and_presets_apply_to_bahrain_and_oman() -> None:
    setup_books()
    assert save_tax(country="kw").status_code == 200
    codes = vat_codes()
    assert codes["no_vat_country"]["params"]["country"] == "KW" and "has no VAT" in codes["no_vat_country"]["detected"]
    assert "return_due_soon" not in codes and "return_overdue" not in codes
    assert client.get(f"{API}/tax", headers=OWNER).json()["filing_deadline"]["source"] == "no_vat"
    # Oman: the 30 day preset, and no request for owner values.
    assert save_tax(country="OM").status_code == 200
    deadline = client.get(f"{API}/tax", headers=OWNER).json()["filing_deadline"]
    assert (deadline["source"], deadline["preset"]["days_after"], deadline["preset"]["end_of_next_month"]) == ("preset", 30, False)
    codes = vat_codes()
    assert "filing_deadline_missing" not in codes and "no_vat_country" not in codes
    # Bahrain: the end of the next month. A KSA business never keeps a country.
    assert save_tax(country="BH").json()["filing_deadline"]["preset"]["end_of_next_month"] is True
    ksa = save_tax(jurisdiction="SA", registration_number="300000000000003", country="BH")
    assert ksa.status_code == 200 and ksa.json()["settings"]["country"] is None


@pytest.mark.parametrize("body,code", [({"country": "SA"}, "country_has_jurisdiction"), ({"filing_due_days": 0}, None),
                                       ({"filing_due_days": 121}, None), ({"country": "BHR"}, None)])
def test_invalid_filing_settings_are_refused(body: dict, code: str | None) -> None:
    setup_books()
    response = save_tax(**body)
    assert response.status_code == 422
    if code:
        assert response.json()["code"] == code


def test_only_owners_and_accountants_change_the_filing_settings() -> None:
    setup_books()
    assert save_tax(country="OM").status_code == 200
    for headers in (MEMBER, VIEWER):
        assert save_tax(headers, country="OM", filing_due_days=5).status_code == 403
    assert client.get(f"{API}/tax", headers=OWNER).json()["settings"]["filing_due_days"] is None
    accountant()
    assert save_tax(MEMBER, country="OM", filing_due_days=5).json()["settings"]["filing_due_days"] == 5
