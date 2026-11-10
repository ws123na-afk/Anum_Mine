"""The VAT return counts a capital bill once, when it is bought; its monthly depreciation is not a purchase
(docs/business-tax.md#the-vat-return). In-memory stores; the vendor is SYNTHETIC.
"""

from __future__ import annotations

from decimal import Decimal

from test_business_books import (  # noqa: F401  (``clean`` resets every store)
    API,
    OWNER,
    bill,
    clean,
    client,
    setup_books,
    today,
    vendor,
)
from test_business_tax import TAX, boxes, post_now, tax_settings, vat_return

from anum_api.business.books_store import books_memory_store


def test_depreciation_of_a_capital_bill_is_not_an_unknown_purchase_in_the_return() -> None:
    setup_books()
    tax_settings()
    first = today().replace(day=1)
    made = bill(vendor()["id"], subtotal="12000", tax="1800", tax_code="standard", section="capital", useful_life_months=12,
                bill_date=first.isoformat(), category="equipment")
    assert made.status_code == 201, made.text
    post_now()
    # The first month's depreciation is in the journal: 12,000.00 over 12 months = 1,000.00.
    keys = {row.id: row.system_key for row in books_memory_store.rows["ledger_accounts"].values()}
    depreciation = [line.debit for line in books_memory_store.rows["journal_lines"].values() if keys[line.account_id] == "depreciation"]
    assert depreciation == [Decimal("1000.00")]
    period = {"from": first.isoformat(), "to": today().isoformat()}
    data = vat_return(**period)
    box = boxes(data)["7"]
    assert (box["amount"], box["vat"], box["documents"]) == ("12000.00", "1800.00", 1)  # the purchase, once
    assert data["info"] == []  # no "purchases without a tax code" row for the 1,000.00
    assert "unknown" not in [warning["code"] for warning in data["warnings"]]
    documents = client.get(f"{TAX}/return/documents", headers=OWNER, params={**period, "limit": 50}).json()
    assert [(row["net"], row["vat"]) for row in documents["items"]] == [("12000.00", "1800.00")]
    # Voiding the bill reverses the purchase and its depreciation: the return is back to nothing, with no leftover row.
    voided = client.post(f"{API}/bills/{made.json()['id']}/void", headers=OWNER, json={"reason": "Synthetic: ordered twice"})
    assert voided.status_code == 200, voided.text
    post_now()
    after = vat_return(**period)
    assert (boxes(after)["7"]["amount"], boxes(after)["7"]["vat"], after["info"]) == ("0.00", "0.00", [])
