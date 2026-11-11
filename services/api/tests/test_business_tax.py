"""Business tax (docs/business-tax.md): settings and rates, tax codes, VAT posting, the VAT return, ZATCA readiness. In-memory stores.

``test_postgres_business_tax.py`` repeats the database rules (forced RLS, the counter only moving forward, frozen e-invoice data,
the counter under concurrency). Sellers, buyers, amounts and registration numbers are SYNTHETIC; nothing is a real invoice.
"""

from __future__ import annotations

import base64
import csv
import io
import threading
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from defusedxml import ElementTree as ET
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    BUSINESS,
    MEMBER,
    OTHER_OWNER,
    OWNER,
    TENANT,
    VIEWER,
    bill,
    clean,
    client,
    ctx,
    seed_credit,
    seed_invoice,
    setup_books,
    today,
    vendor,
)

from anum_api.business import zatca as Z
from anum_api.business.books_store import books_memory_store
from anum_api.business.invoice_models import InvoiceLine
from anum_api.business.invoices import invoice_hash
from anum_api.business.models import BillingProfile, LineKind
from anum_api.business.store import business_store
from anum_api.business.tax_rules import Jurisdiction, TaxRuleError, check_registration, rate_on, split
from anum_api.dependencies import memory_repository

TAX = f"{API}/tax"
# Synthetic registration numbers in the right shape (KSA: 15 digits starting and ending with 3; UAE: 15 digits).
SA_NUMBER = "300000000000003"
AE_NUMBER = "100000000000001"


def last_month_end() -> date:
    return today().replace(day=1) - timedelta(days=1)


def tax_settings(headers: dict = OWNER, **body: Any) -> dict:
    current = client.get(TAX, headers=headers).json()["settings"]
    payload = {"jurisdiction": "SA", "registered": True, "registration_number": SA_NUMBER, "filing_frequency": "quarterly",
               "version": current["version"] if current else None, **body}
    response = client.put(f"{TAX}/settings", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def post_now() -> None:
    response = client.post(f"{API}/ledger/post", headers=OWNER)
    assert response.status_code == 200, response.text


def vat_return(**params: Any) -> dict:
    response = client.get(f"{TAX}/return", headers=OWNER, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def boxes(data: dict) -> dict[str, dict]:
    return {row["box"]: row for row in data["boxes"]}


def coded_invoice(lines: list[tuple[str, str, str | None]], *, issued: date | None = None, buyer_vat: str = "", seller: bool = True,
                  currency: str = "SAR") -> Any:
    """An invoice of (net, tax, tax code) lines, sent, stored directly (the invoice routes are covered in test_business_invoices)."""
    invoice = seed_invoice(subtotal="1", tax="0", issued=issued, currency=currency)
    priced = [InvoiceLine(description=f"Synthetic line {index}", kind=LineKind.SERVICE, quantity=Decimal(1), unit_price=Decimal(net), net=Decimal(net),
                          tax=Decimal(tax), total=Decimal(net) + Decimal(tax), tax_code=code,
                          tax_rate_pct=Decimal(15) if Decimal(tax) > 0 else Decimal(0)) for index, (net, tax, code) in enumerate(lines)]
    subtotal = sum((line.net for line in priced), Decimal(0))
    total_tax = sum((line.tax for line in priced), Decimal(0))
    profile = BillingProfile(legal_name="شركة الاختبار التجريبية", tax_number=SA_NUMBER) if seller else None
    invoice = invoice.model_copy(update={"lines": priced, "subtotal": subtotal, "tax": total_tax, "total": subtotal + total_tax, "seller": profile,
                                         "sent_at": invoice.created_at, "bill_to_tax_number": buyer_vat, "bill_to_name": "Synthetic Buyer"})
    business_store.scoped(ctx()).save("invoices", invoice)
    return invoice


# -- rules ---------------------------------------------------------------------------------------------------------------


def test_registration_numbers_rates_by_day_and_exact_splits() -> None:
    assert check_registration(Jurisdiction.SA, " 3000 0000 0000 003 ") == SA_NUMBER
    for wrong in ("300000000000000", "100000000000003", "30000000000003", "3000000000000033"):
        with pytest.raises(TaxRuleError):
            check_registration(Jurisdiction.SA, wrong)
    assert check_registration(Jurisdiction.AE, AE_NUMBER) == AE_NUMBER
    with pytest.raises(TaxRuleError):
        check_registration(Jurisdiction.AE, "1234")
    rows = [("standard", Decimal(5), date(2018, 1, 1), date(2020, 6, 30)), ("standard", Decimal(15), date(2020, 7, 1), None)]
    assert rate_on(rows, "standard", date(2020, 6, 30)) == Decimal(5) and rate_on(rows, "standard", date(2020, 7, 1)) == Decimal(15)
    assert rate_on(rows, "standard", date(2017, 12, 31)) is None and rate_on(rows, "exempt", date(2024, 1, 1)) is None
    parts = split(Decimal("100.00"), [Decimal(1), Decimal(1), Decimal(1)])
    assert sum(parts) == Decimal("100.00") and sorted(parts) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]


# -- settings and rates --------------------------------------------------------------------------------------------------


def test_settings_are_for_owners_and_accountants_validate_the_number_and_seed_the_rate_table() -> None:
    setup_books()
    empty = client.get(TAX, headers=MEMBER).json()
    assert empty["settings"] is None and empty["can_edit"] is False and empty["rates"] == []
    assert client.get(TAX, headers=VIEWER).status_code == 403
    denied = client.put(f"{TAX}/settings", headers=MEMBER, json={"jurisdiction": "SA"})
    assert denied.status_code == 403 and "owner or an accountant" in denied.json()["detail"]
    bad = client.put(f"{TAX}/settings", headers=OWNER, json={"jurisdiction": "SA", "registered": True, "registration_number": "123456789012345"})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_vat_number"
    saved = tax_settings()
    assert saved["settings"]["zatca_enabled"] is False  # off by default
    rates = {(row["code"], row["effective_from"]): row["rate_pct"] for row in saved["rates"]}
    assert rates[("standard", "2020-07-01")] == "15" and rates[("standard", "2018-01-01")] == "5" and rates[("exempt", "2018-01-01")] == "0"
    # e-invoicing only for a registered Saudi business
    uae = client.put(f"{TAX}/settings", headers=OWNER, json={"jurisdiction": "AE", "registered": True, "registration_number": AE_NUMBER,
                                                             "zatca_enabled": True, "version": saved["settings"]["version"]})
    assert uae.status_code == 422 and uae.json()["code"] == "zatca_not_sa"
    stale = client.put(f"{TAX}/settings", headers=OWNER, json={"jurisdiction": "SA", "version": 99})
    assert stale.status_code == 409
    # a rate change is a new row; a rate in use cannot be removed
    added = client.post(f"{TAX}/rates", headers=OWNER, json={"code": "standard", "rate_pct": "16", "effective_from": (today() + timedelta(days=40)).isoformat()})
    assert added.status_code == 201, added.text
    in_use = next(row for row in saved["rates"] if row["code"] == "standard" and row["effective_from"] == "2020-07-01")
    assert client.delete(f"{TAX}/rates/{in_use['id']}", headers=OWNER).json()["code"] == "rate_in_use"
    assert client.delete(f"{TAX}/rates/{added.json()['id']}", headers=OWNER).status_code == 204
    actions = [row.action for row in memory_repository.store.audit_records if row.action.startswith("business.books.tax")]
    assert "business.books.tax_settings_saved" in actions and "business.books.tax_rate_added" in actions
    assert all(SA_NUMBER not in str(row.metadata) for row in memory_repository.store.audit_records)


def test_another_business_never_sees_the_tax_settings_or_e_invoice_data() -> None:
    setup_books()
    tax_settings(zatca_enabled=True)
    setup_books(OTHER_OWNER)
    assert client.get(TAX, headers=OTHER_OWNER).json()["settings"] is None
    invoice = coded_invoice([("100", "15", "standard")])
    made = client.post(f"{TAX}/zatca/generate", headers=OWNER, json={"source_id": invoice.id})
    assert made.status_code == 201, made.text
    assert client.get(f"{TAX}/zatca/documents/{made.json()['id']}/xml", headers=OTHER_OWNER).status_code == 404
    assert client.get(f"{TAX}/zatca/documents", headers=OTHER_OWNER).json()["total"] == 0


# -- posting ----------------------------------------------------------------------------------------------------------


def _lines(source_kind: str) -> list[tuple[str, str, str, str | None]]:
    """(system key, debit, credit, tax code) of the live entry of a source kind."""
    keys = {row.id: row.system_key for row in books_memory_store.rows["ledger_accounts"].values()}
    entries = [row for row in books_memory_store.rows["journal_entries"].values() if row.source_kind == source_kind and row.status == "posted"]
    ids = {row.id for row in entries}
    return sorted((keys[line.account_id], str(line.debit), str(line.credit), line.tax_code)
                  for line in books_memory_store.rows["journal_lines"].values() if line.entry_id in ids)


def test_vat_on_invoices_and_bills_posts_to_the_vat_accounts_by_tax_code() -> None:
    setup_books()
    tax_settings()
    coded_invoice([("1000", "150", "standard"), ("500", "0", "zero_rated"), ("200", "0", "exempt")])
    landlord = vendor()["id"]
    assert bill(landlord, tax_code="standard").status_code == 201
    zero = bill(vendor("Exporter")["id"], tax_code="zero_rated")
    assert zero.status_code == 422 and zero.json()["code"] == "tax_not_allowed"
    reverse = bill(vendor("Foreign Software")["id"], tax="0", subtotal="1000", tax_code="reverse_charge", category="software")
    assert reverse.status_code == 201 and reverse.json()["reverse_charge_tax"] == "150.00" and reverse.json()["tax_code"] == "reverse_charge"
    post_now()
    invoice_lines = _lines("invoice")
    assert [(k, Decimal(c), t) for k, _d, c, t in invoice_lines if k == "tax_payable"] == [("tax_payable", Decimal(150), "standard")]
    revenue = {t: Decimal(c) for k, _d, c, t in invoice_lines if k == "revenue_service"}
    assert revenue == {"standard": 1000, "zero_rated": 500, "exempt": 200}
    bills = _lines("bill")
    assert any(k == "tax_receivable" and Decimal(d) == 150 and t == "standard" for k, d, _c, t in bills)
    # reverse charge: input and output VAT of the same amount, nothing owed to the supplier for it
    assert any(k == "tax_receivable" and Decimal(d) == 150 and t == "reverse_charge" for k, d, _c, t in bills)
    assert any(k == "tax_payable" and Decimal(c) == 150 and t == "reverse_charge" for k, _d, c, t in bills)
    assert any(k == "ap" and Decimal(c) == 1000 for k, _d, c, _t in bills)


def test_an_uncoded_invoice_keeps_its_posting_and_hash_from_before_tax_codes() -> None:
    setup_books()
    seed_invoice(subtotal="1000", tax="150")
    post_now()
    before = {row.fingerprint for row in books_memory_store.rows["journal_entries"].values()}
    tax_settings()
    post_now()
    after = [row for row in books_memory_store.rows["journal_entries"].values()]
    assert {row.fingerprint for row in after} == before and all(row.status == "posted" for row in after)  # nothing re-posted
    line = InvoiceLine(description="Synthetic", unit_price=Decimal(10), net=Decimal(10), total=Decimal(10))
    invoice = seed_invoice(status="draft").model_copy(update={"lines": [line]})
    legacy = invoice_hash(invoice)
    assert invoice_hash(invoice.model_copy(update={"lines": [line.model_copy(update={"tax_code": None})]})) == legacy
    assert invoice_hash(invoice.model_copy(update={"lines": [line.model_copy(update={"tax_code": "standard"})]})) != legacy


def test_invoice_lines_take_the_rate_of_their_tax_code() -> None:
    setup_books()
    business_store.scoped(ctx()).add("accounts", __import__("anum_api.business.models", fromlist=["Account"]).Account(
        id="acc_tax", tenant_id=TENANT, workspace_id=BUSINESS, created_at=today_dt(), updated_at=today_dt(), name="Synthetic Buyer Co",
        name_key="synthetic buyer co", owner_id="owner_books", created_by="owner_books"))
    body = {"account_id": "acc_tax", "currency": "SAR", "lines": [{"description": "Synthetic design", "unit_price": "1000", "tax_code": "standard"}]}
    not_set_up = client.post("/api/v1/business/invoices", headers=OWNER, json=body)
    assert not_set_up.status_code == 422 and "Set up tax first" in not_set_up.json()["error"]["message"]
    tax_settings()
    made = client.post("/api/v1/business/invoices", headers=OWNER, json={**body, "lines": [
        {"description": "Synthetic design", "unit_price": "1000", "tax_rate_pct": "5", "tax_code": "standard"},
        {"description": "Synthetic export", "unit_price": "400", "tax_rate_pct": "15", "tax_code": "export"},
        {"description": "Synthetic legacy line", "unit_price": "100", "tax_rate_pct": "15"}]})
    assert made.status_code in (200, 201), made.text
    lines = made.json()["lines"]
    assert [(row["tax_code"], Decimal(row["tax_rate_pct"]), Decimal(row["tax"])) for row in lines] == [
        ("standard", Decimal(15), Decimal(150)), ("export", Decimal(0), Decimal(0)), (None, Decimal(15), Decimal(15))]
    # codes can be set on every line by position (the legacy line gets one)
    edited = client.patch(f"/api/v1/business/invoices/{made.json()['id']}", headers=OWNER, json={"tax_codes": ["standard", "export", "exempt"]})
    assert edited.status_code == 200, edited.text
    assert [row["tax_code"] for row in edited.json()["lines"]] == ["standard", "export", "exempt"] and Decimal(edited.json()["tax"]) == 150
    wrong = client.patch(f"/api/v1/business/invoices/{made.json()['id']}", headers=OWNER, json={"tax_codes": ["standard"]})
    assert wrong.status_code == 422


def today_dt():  # type: ignore[no-untyped-def]
    from anum_api.schemas import utc_now

    return utc_now()


# -- the VAT return ---------------------------------------------------------------------------------------------------


def test_the_ksa_return_fills_its_boxes_from_the_journal_and_flags_what_it_cannot_count() -> None:
    setup_books()
    tax_settings()
    invoice = coded_invoice([("1000", "150", "standard"), ("500", "0", "zero_rated"), ("300", "0", "export"), ("200", "0", "exempt"),
                             ("50", "0", "out_of_scope")])
    seed_credit(invoice, "100", "10")  # split in the invoice's proportion by code
    seed_invoice(subtotal="400", tax="60")  # written before tax codes, with VAT: inferred standard rated
    seed_invoice(subtotal="70", tax="0")  # before tax codes, no VAT: unknown, listed
    bill(vendor()["id"], subtotal="400", tax="60", tax_code="standard")
    bill(vendor("Foreign Software")["id"], subtotal="1000", tax="0", tax_code="reverse_charge", category="software")
    bill(vendor("Customs Broker")["id"], subtotal="2000", tax="300", tax_code="import_customs", category="goods")
    post_now()
    period = {"from": today().replace(day=1).isoformat(), "to": today().isoformat()}
    data = vat_return(**period)
    found = boxes(data)
    # the credit note (100 net, 10 VAT) is shared by net and by VAT in the invoice's proportion
    credit = split(Decimal(100), [Decimal(1000), Decimal(500), Decimal(300), Decimal(200), Decimal(50)])
    assert Decimal(found["1"]["amount"]) == 1000 + 400 - credit[0] and Decimal(found["1"]["vat"]) == 150 + 60 - 10
    assert Decimal(found["3"]["amount"]) == 500 - credit[1] and Decimal(found["4"]["amount"]) == 300 - credit[2]
    assert Decimal(found["5"]["amount"]) == 200 - credit[3] and Decimal(found["5"]["vat"]) == 0
    assert Decimal(found["6"]["vat"]) == 200
    assert Decimal(found["7"]["amount"]) == 400 and Decimal(found["7"]["vat"]) == 60
    assert Decimal(found["8"]["amount"]) == 2000 and Decimal(found["8"]["vat"]) == 300
    assert Decimal(found["9"]["amount"]) == 1000 and Decimal(found["9"]["vat"]) == 150
    assert Decimal(found["12"]["vat"]) == 510
    # due = sales VAT 200 + reverse charge 150 - recoverable 510
    assert Decimal(found["13"]["vat"]) == Decimal(-160) and found["16"]["vat"] == found["13"]["vat"]
    info = {row["key"]: row for row in data["info"]}
    assert Decimal(info["out_of_scope_sales"]["amount"]) == 50 - credit[4] and info["unknown_sales"]["documents"] == 1
    codes = {row["code"] for row in data["warnings"]}
    assert {"inferred", "unknown", "period_open"} <= codes and not data["complete"]
    assert data["journal"]["ties"] is True
    # drill down: the documents behind box 1, paged
    page = client.get(f"{TAX}/return/documents", headers=OWNER, params={**period, "box": "1", "limit": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    inferred = client.get(f"{TAX}/return/documents", headers=OWNER, params={**period, "box": "1", "limit": 10}).json()["items"]
    assert sum(1 for row in inferred if row["inferred"]) == 1
    # CSV with the boxes, the warnings and the notes
    exported = client.get(f"{TAX}/return.csv", headers=OWNER, params=period)
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert exported.status_code == 200 and rows[0][0].startswith("VAT return (working draft")
    assert any(row[:2] == ["warning", "unknown"] for row in rows) and any(row and row[0] == "note" for row in rows)


def test_the_uae_return_uses_vat201_boxes() -> None:
    setup_books(base_currency="AED", country="AE")
    tax_settings(jurisdiction="AE", registration_number=AE_NUMBER)
    coded_invoice([("1000", "50", "standard"), ("300", "0", "export")], currency="AED")
    bill(vendor()["id"], currency="AED", subtotal="200", tax="10", tax_code="standard")
    bill(vendor("Foreign Software")["id"], currency="AED", subtotal="1000", tax="0", tax_code="reverse_charge", category="software")
    post_now()
    found = boxes(vat_return(**{"from": today().replace(day=1).isoformat(), "to": today().isoformat()}))
    assert Decimal(found["1"]["vat"]) == 50 and Decimal(found["4"]["amount"]) == 300
    assert Decimal(found["3"]["vat"]) == 50 and Decimal(found["10"]["vat"]) == 50  # reverse charge 5 % of 1000, due and recoverable
    assert Decimal(found["12"]["vat"]) == 100 and Decimal(found["13"]["vat"]) == 60 and Decimal(found["14"]["vat"]) == 40


def test_a_closed_period_keeps_its_return_and_a_late_document_lands_in_the_adjustment_column() -> None:
    setup_books()
    tax_settings()
    earlier = last_month_end() - timedelta(days=2)
    coded_invoice([("1000", "150", "standard")], issued=earlier)
    post_now()
    closed_period = {"from": earlier.replace(day=1).isoformat(), "to": last_month_end().isoformat()}
    before = vat_return(**closed_period)
    assert client.post(f"{API}/close", headers=OWNER, json={"through": last_month_end().isoformat()}).status_code == 200
    coded_invoice([("200", "30", "standard")], issued=earlier)  # recorded after the close, dated in the closed month
    post_now()
    after = vat_return(**closed_period)
    assert after["closed"] and boxes(after)["1"] == boxes(before)["1"] and "period_open" not in {row["code"] for row in after["warnings"]}
    current = boxes(vat_return(**{"from": today().replace(day=1).isoformat(), "to": today().isoformat()}))
    assert Decimal(current["1"]["adjustment_amount"]) == 200 and Decimal(current["1"]["adjustment_vat"]) == 30 and Decimal(current["1"]["amount"]) == 0


def test_the_return_waits_for_tax_settings_and_says_what_is_not_posted() -> None:
    setup_books()
    seed_invoice(subtotal="100", tax="15")
    data = vat_return()
    codes = {row["code"] for row in data["warnings"]}
    assert {"not_set_up", "unposted"} <= codes and data["jurisdiction"] == "OTHER"
    assert client.get(f"{TAX}/return", headers=VIEWER).status_code == 403


# -- ZATCA ------------------------------------------------------------------------------------------------------------


def test_the_qr_tlv_round_trips_with_an_arabic_seller_name_in_utf8_bytes() -> None:
    name = "شركة الاختبار"
    moment = today_dt().replace(microsecond=0)
    text = Z.phase1_qr(name, SA_NUMBER, moment, Decimal("1150"), Decimal("150"))
    raw = base64.b64decode(text)
    assert raw[0] == 1 and raw[1] == len(name.encode("utf-8")) and raw[2:2 + raw[1]].decode("utf-8") == name
    fields = Z.tlv_decode(raw)
    assert [tag for tag, _ in fields] == [1, 2, 3, 4, 5]
    assert dict(fields) == {1: name, 2: SA_NUMBER, 3: moment.strftime("%Y-%m-%dT%H:%M:%SZ"), 4: "1150.00", 5: "150.00"}
    assert Z.tlv([(1, "ab")]) == bytes([1, 2]) + b"ab"
    with pytest.raises(ValueError):
        Z.tlv_decode(bytes([1, 5]) + b"ab")


def _xml_document(**changes: Any) -> Z.XmlDocument:
    base = dict(number="INV-2026-0001", uuid="00000000-0000-4000-8000-000000000001", counter=1, previous_hash=Z.INITIAL_PREVIOUS_HASH,
                type_code=Z.TYPE_INVOICE, subtype=Z.STANDARD, issued_at=today_dt().replace(microsecond=0), currency="SAR",
                seller_name="Synthetic Seller", seller_vat=SA_NUMBER, seller_crn="1010000000", seller_street="Synthetic Street",
                seller_building="1234", seller_district="Synthetic District", seller_city="Riyadh", seller_postal_code="12345",
                buyer_name="Synthetic Buyer", buyer_vat="399999999999993",
                lines=[Z.XmlLine("Synthetic service", Decimal(2), Decimal(500), Decimal(1000), Decimal(150), "S", Decimal(15))])
    return Z.XmlDocument(**{**base, **changes})


def test_the_ubl_xml_has_the_required_elements_and_a_deterministic_hash() -> None:
    xml = Z.build_xml(_xml_document())
    root = ET.fromstring(xml)
    ns = {"cbc": Z.NS["cbc"], "cac": Z.NS["cac"]}
    for path in ("cbc:ProfileID", "cbc:ID", "cbc:UUID", "cbc:IssueDate", "cbc:IssueTime", "cbc:InvoiceTypeCode", "cbc:DocumentCurrencyCode",
                 "cbc:TaxCurrencyCode", "cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID",
                 "cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:ID",
                 "cac:LegalMonetaryTotal/cbc:PayableAmount", "cac:InvoiceLine/cac:Item/cac:ClassifiedTaxCategory/cbc:Percent"):
        assert root.find(path, ns) is not None, path
    assert root.find("cbc:InvoiceTypeCode", ns).attrib["name"] == Z.STANDARD and root.find("cbc:InvoiceTypeCode", ns).text == "388"
    references = {item.find("cbc:ID", ns).text: item for item in root.findall("cac:AdditionalDocumentReference", ns)}
    assert references["ICV"].find("cbc:UUID", ns).text == "1"
    assert references["PIH"].find("cac:Attachment/cbc:EmbeddedDocumentBinaryObject", ns).text == Z.INITIAL_PREVIOUS_HASH
    assert root.find("cac:LegalMonetaryTotal/cbc:PayableAmount", ns).text == "1150.00"
    digest = Z.invoice_hash(xml)
    assert digest == Z.invoice_hash(xml) and len(base64.b64decode(digest)) == 32
    # canonical form: the same document written with other whitespace and attribute order hashes the same
    reordered = xml.replace('<cbc:InvoiceTypeCode name="0100000">', '<cbc:InvoiceTypeCode   name="0100000" >')
    assert Z.invoice_hash(reordered) == digest
    assert Z.invoice_hash(Z.build_xml(_xml_document(counter=2))) != digest
    missing = Z.missing_fields(_xml_document())
    assert "buyer_address_structured" in missing and "cryptographic_stamp" in missing and "seller_vat_number" not in missing
    simplified = Z.missing_fields(_xml_document(subtype=Z.SIMPLIFIED, seller_building="12", lines=[
        Z.XmlLine("Synthetic export", Decimal(1), Decimal(10), Decimal(10), Decimal(0), "Z", Decimal(0)),
        Z.XmlLine("Synthetic legacy", Decimal(1), Decimal(10), Decimal(10), Decimal(1), None, Decimal(10))]))
    assert {"seller_building_format", "exemption_reason_codes", "line_tax_codes"} <= set(simplified) and "buyer_address_structured" not in simplified


def test_e_invoicing_is_off_by_default_chains_hashes_and_never_submits() -> None:
    setup_books()
    tax_settings()
    first = coded_invoice([("1000", "150", "standard")], buyer_vat="399999999999993")
    off = client.post(f"{TAX}/zatca/generate", headers=OWNER, json={"source_id": first.id})
    assert off.status_code == 409 and off.json()["code"] == "zatca_off"
    panel = client.get(f"{TAX}/zatca", headers=MEMBER).json()
    assert panel["enabled"] is False and panel["notice"]["en"].startswith("Not verified with ZATCA") and "seller_crn" in panel["missing"]
    tax_settings(zatca_enabled=True, seller_crn="1010000000", seller_street="Synthetic Street", seller_building="1234",
                 seller_district="Synthetic District", seller_city="Riyadh", seller_postal_code="12345")
    assert client.post(f"{TAX}/zatca/generate", headers=MEMBER, json={"source_id": first.id}).status_code == 403
    one = client.post(f"{TAX}/zatca/generate", headers=OWNER, json={"source_id": first.id})
    assert one.status_code == 201, one.text
    assert one.json()["counter"] == 1 and one.json()["previous_hash"] == Z.INITIAL_PREVIOUS_HASH and one.json()["subtype"] == Z.STANDARD
    again = client.post(f"{TAX}/zatca/generate", headers=OWNER, json={"source_id": first.id})
    assert again.status_code == 200 and again.json()["replayed"] and again.json()["id"] == one.json()["id"]
    second = coded_invoice([("10", "1.50", "standard")])
    two = client.post(f"{TAX}/zatca/generate", headers=OWNER, json={"source_id": second.id}).json()
    assert two["counter"] == 2 and two["previous_hash"] == one.json()["invoice_hash"] and two["subtype"] == Z.SIMPLIFIED
    xml = client.get(f"{TAX}/zatca/documents/{two['id']}/xml", headers=MEMBER)
    assert xml.status_code == 200 and Z.invoice_hash(xml.text) == two["invoice_hash"]
    assert two["qr"] and dict(Z.tlv_decode(base64.b64decode(two["qr"])))[2] == SA_NUMBER
    refused = client.post(f"{TAX}/zatca/documents/{two['id']}/submit", headers=OWNER)
    assert refused.status_code == 409 and refused.json()["code"] == "zatca_onboarding_required" and "Fatoora" in refused.json()["detail"]
    panel = client.get(f"{TAX}/zatca", headers=OWNER).json()
    assert panel["counter"] == 2 and panel["generated"] == 2 and panel["invoices"]["total"] == 2
    # a credit note of the first invoice: type 381 with the invoice as its billing reference
    seed_credit(first, "100", "15")
    note_id = next(iter(business_store._rows["credit_notes"]))
    credit = client.post(f"{TAX}/zatca/generate", headers=OWNER, json={"source_kind": "credit_note", "source_id": note_id}).json()
    assert credit["type_code"] == "381" and credit["counter"] == 3 and credit["previous_hash"] == two["invoice_hash"]


def test_the_xml_date_and_time_are_the_sellers_local_day_not_utc() -> None:
    """ZATCA reads IssueDate and IssueTime as the seller's own date and time (Asia/Riyadh by default, as business invoices
    are dated, invoice_service.business_day). Sent at 22:30 UTC on 10 November is 01:30 on 11 November in Riyadh."""
    from datetime import UTC, datetime

    setup_books()
    tax_settings(zatca_enabled=True)
    late = datetime(2026, 11, 10, 22, 30, tzinfo=UTC)
    invoice = coded_invoice([("100", "15", "standard")])
    business_store.scoped(ctx()).save("invoices", invoice.model_copy(update={"sent_at": late}))
    ns = {"cbc": Z.NS["cbc"], "cac": Z.NS["cac"]}
    record, _ = Z.generate(ctx(), "invoice", invoice.id, actor="owner_books", now=late)
    root = ET.fromstring(record.xml)
    assert (root.find("cbc:IssueDate", ns).text, root.find("cbc:IssueTime", ns).text) == ("2026-11-11", "01:30:00")
    assert record.issue_date == date(2026, 11, 11)
    # One minute before the local midnight it is still the 10th.
    early = coded_invoice([("100", "15", "standard")])
    before = datetime(2026, 11, 10, 20, 59, tzinfo=UTC)
    business_store.scoped(ctx()).save("invoices", early.model_copy(update={"sent_at": before}))
    record, _ = Z.generate(ctx(), "invoice", early.id, actor="owner_books", now=before)
    root = ET.fromstring(record.xml)
    assert (root.find("cbc:IssueDate", ns).text, root.find("cbc:IssueTime", ns).text) == ("2026-11-10", "23:59:00")
    # A credit note keeps its own (business) issue date with the local time of generation.
    seed_credit(invoice, "10", "1.50")
    note_id = next(iter(business_store._rows["credit_notes"]))
    note = business_store.scoped(ctx()).get("credit_notes", note_id)
    business_store.scoped(ctx()).save("credit_notes", note.model_copy(update={"issue_date": date(2026, 11, 11)}))
    record, _ = Z.generate(ctx(), "credit_note", note_id, actor="owner_books", now=late)
    root = ET.fromstring(record.xml)
    assert (root.find("cbc:IssueDate", ns).text, root.find("cbc:IssueTime", ns).text) == ("2026-11-11", "01:30:00")
    assert record.issue_date == date(2026, 11, 11)


def test_the_counter_never_repeats_when_generations_race() -> None:
    setup_books()
    tax_settings(zatca_enabled=True)
    invoices = [coded_invoice([("100", "15", "standard")]) for _ in range(8)]
    results: list[Any] = []

    def run(invoice_id: str) -> None:
        results.append(Z.generate(ctx(), "invoice", invoice_id, actor="owner_books")[0])

    threads = [threading.Thread(target=run, args=(item.id,)) for item in invoices]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ordered = sorted(results, key=lambda row: row.counter)
    assert [row.counter for row in ordered] == list(range(1, 9))
    assert [row.previous_hash for row in ordered[1:]] == [row.invoice_hash for row in ordered[:-1]]


def test_the_invoice_pdf_prints_the_qr_only_when_e_invoicing_is_on() -> None:
    setup_books()
    tax_settings()
    invoice = coded_invoice([("1000", "150", "standard")])
    plain = client.get(f"/api/v1/business/invoices/{invoice.id}/pdf", headers=OWNER)
    assert plain.status_code == 200 and b"ZATCA QR code" not in plain.content
    tax_settings(zatca_enabled=True)
    printed = client.get(f"/api/v1/business/invoices/{invoice.id}/pdf", headers=OWNER)
    assert printed.status_code == 200 and b"ZATCA QR code" in printed.content and b"Not verified with ZATCA" in printed.content
