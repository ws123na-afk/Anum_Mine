"""Supporting documents and the evidence pack of the business books (docs/business-books.md#supporting-documents-and-evidence).

In-memory stores. Documents, vendors and the bank messages are SYNTHETIC (``test_business_books`` fixtures). The vault's
own rules (workspace documents, who may edit) apply; the evidence pack never carries the member's personal bank details.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    BUSINESS,
    FIN,
    MEMBER,
    OWNER,
    PERSONAL_OWNER,
    TENANT,
    VIEWER,
    allocate,
    bill,
    clean,
    client,
    report,
    setup_books,
    today,
    vendor,
)

from anum_api import files
from anum_api.dependencies import memory_repository
from anum_api.files import file_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.schemas import TenantContext
from anum_api.vault.store import memory_vault_store

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def vault(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(file_store, "storage", files.InMemoryObjectStorage())
    file_store.clear()
    memory_vault_store.clear()
    memory_repository.store.audit_records.clear()  # these tests count audit rows
    yield
    file_store.clear()
    memory_vault_store.clear()


def document(headers: dict = OWNER, name: str = "receipt.pdf", access: str = "workspace", content: bytes | None = None) -> str:
    body = content if content is not None else PDF + f"%{name}\n".encode()  # different bytes per name: the vault refuses duplicates
    response = client.post("/api/v1/documents", params={"name": name, "title": f"Synthetic {name}", "category": "receipt", "access_class": access},
                           headers=headers, content=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def attach(source_type: str, source_id: str, document_id: str, headers: dict = OWNER) -> Any:
    return client.post(f"{API}/evidence", headers=headers, json={"source_type": source_type, "source_id": source_id, "document_id": document_id})


def evidence(source_type: str, ids: list[str], headers: dict = OWNER) -> dict:
    response = client.get(f"{API}/evidence", headers=headers, params={"source_type": source_type, "ids": ",".join(ids)})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_receipt_is_attached_to_a_bill_and_the_missing_flag_follows_the_threshold() -> None:
    setup_books(evidence_threshold="500")
    landlord = vendor()
    big = bill(landlord["id"]).json()
    small = bill(landlord["id"], description="Synthetic stationery", subtotal="40", tax="6").json()
    found = evidence("bill", [big["id"], small["id"]])
    assert found["items"][big["id"]] == {"documents": [], "evidence_missing": True}
    assert found["items"][small["id"]]["evidence_missing"] is False and found["can_attach"] is True
    # The P&L and the statements warn while a bill above the threshold has nothing attached.
    pnl = report()
    assert pnl["evidence_missing"]["count"] == 1 and "evidence_missing" in pnl["notes"]
    missing = client.get(f"{API}/evidence/missing", headers=OWNER).json()
    assert [(i["type"], i["id"]) for i in missing["items"]] == [("bill", big["id"])] and missing["threshold"] == "500.00"

    receipt = document()
    made = attach("bill", big["id"], receipt)
    assert made.status_code == 201, made.text
    assert attach("bill", big["id"], receipt).status_code == 200  # the same document again is the same attachment
    after = evidence("bill", [big["id"]])["items"][big["id"]]
    assert after["evidence_missing"] is False and [d["document_id"] for d in after["documents"]] == [receipt]
    assert report()["evidence_missing"]["count"] == 0 and "evidence_missing" not in report()["notes"]
    # The vault shows the link on the document, so removing it there removes the evidence here too.
    detail = client.get(f"/api/v1/documents/{receipt}", headers=OWNER).json()
    assert [(l["target_type"], l["target_id"]) for l in detail["links"]] == [("business_bill", big["id"])]
    removed = client.delete(f"{API}/evidence/bill/{big['id']}/{made.json()['link_id']}", headers=OWNER)
    assert removed.status_code == 204
    assert evidence("bill", [big["id"]])["items"][big["id"]]["evidence_missing"] is True
    audit = [r.action for r in memory_repository.store.audit_records if r.action.startswith("business.books.evidence")]
    assert audit == ["business.books.evidence_attached", "business.books.evidence_detached"]


def test_private_documents_other_businesses_and_roles_are_refused() -> None:
    setup_books(evidence_threshold="100")
    landlord = vendor()
    target = bill(landlord["id"]).json()
    private = document(name="mine.pdf", access="private")
    refused = attach("bill", target["id"], private)
    assert refused.status_code == 422 and refused.json()["code"] == "private_document"
    assert attach("bill", "bill_missing", document(name="other.pdf")).json()["code"] == "bill_not_found"
    assert attach("bill", target["id"], "doc_missing").status_code == 404
    # Adjustments and manual entries are attached by owners and accountants, never by a plain member.
    adjustment = client.post(f"{API}/adjustments", headers=OWNER, json={"kind": "accrual", "entry_date": today().isoformat(), "currency": "SAR",
                                                                        "amount": "900", "section": "operating_expense", "category": "rent",
                                                                        "description": "Synthetic accrual"}).json()
    member_doc = document(headers=MEMBER, name="member.pdf")
    assert attach("adjustment", adjustment["id"], member_doc, headers=MEMBER).status_code == 403
    assert attach("bill", target["id"], member_doc, headers=MEMBER).status_code == 201
    assert client.get(f"{API}/evidence", headers=VIEWER, params={"source_type": "bill", "ids": target["id"]}).status_code == 403
    assert attach("adjustment", adjustment["id"], document(name="accrual.pdf")).status_code == 201
    assert evidence("adjustment", [adjustment["id"]])["items"][adjustment["id"]]["evidence_missing"] is False


def test_the_journal_drill_down_shows_each_entrys_documents() -> None:
    setup_books()
    landlord = vendor()
    target = bill(landlord["id"]).json()
    receipt = document()
    assert attach("bill", target["id"], receipt).status_code == 201
    client.post(f"{API}/ledger/post", headers=OWNER)
    entries = client.get(f"{API}/ledger/journal", headers=OWNER, params={"limit": 50}).json()["items"]
    entry = next(e for e in entries if e["source_kind"] == "bill")
    assert entry["evidence_source"] == {"type": "bill", "id": target["id"]} and [d["document_id"] for d in entry["evidence"]] == [receipt]
    # A manual journal entry takes its own evidence.
    accounts = {row["system_key"]: row for row in client.get(f"{API}/ledger/accounts", headers=OWNER).json()["items"] if row["system_key"]}
    manual = client.post(f"{API}/ledger/journal", headers={**OWNER, "Idempotency-Key": "synthetic-opening"}, json={
        "entry_date": today().isoformat(), "description": "Synthetic opening balance",
        "lines": [{"account_id": accounts["bank"]["id"], "debit": "100"}, {"account_id": accounts["capital"]["id"], "credit": "100"}]})
    assert manual.status_code in (200, 201), manual.text
    entry_id = manual.json()["id"]
    assert attach("journal", entry_id, document(name="opening.pdf")).status_code == 201
    entries = client.get(f"{API}/ledger/journal", headers=OWNER, params={"limit": 50, "source_kind": "manual"}).json()["items"]
    assert entries[0]["evidence_source"] == {"type": "journal", "id": entry_id} and len(entries[0]["evidence"]) == 1


def test_the_evidence_pack_lists_every_entry_and_never_a_members_bank_details() -> None:
    setup_books()
    # A member's card payment from yesterday (a payment dated today in the Finance time zone may be tomorrow in UTC, and the
    # journal never posts ahead), allocated to the business.
    card = client.post(f"{FIN}/accounts", headers=PERSONAL_OWNER, json={"name": "My Visa Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    paid = client.post(f"{FIN}/transactions", headers=PERSONAL_OWNER, json={
        "account_id": card["id"], "amount": "-150.00", "description": "SYNTHETIC. Purchase PETROMIN FUEL card **1234",
        "posted_on": (today() - timedelta(days=1)).isoformat()})
    assert paid.status_code == 201, paid.text
    context = TenantContext(tenant_id=TENANT, workspace_id=PERSONAL_OWNER["x-workspace-id"], user_id=PERSONAL_OWNER["x-user-id"])
    entry = sms_memory_store.entry_for_transaction(context, paid.json()["id"])
    allocate(entry.canonical_id or entry.id)
    landlord = vendor()
    target = bill(landlord["id"]).json()
    receipt = document(content=PDF + b"%synthetic receipt\n")
    attach("bill", target["id"], receipt)
    posted = client.post(f"{API}/ledger/post", headers=OWNER)
    assert posted.status_code == 200, posted.text
    span = {"from": (today() - timedelta(days=40)).isoformat(), "to": today().isoformat()}

    index = client.get(f"{API}/evidence-pack.csv", headers=OWNER, params=span)
    assert index.status_code == 200, index.text
    rows = list(csv.reader(io.StringIO(index.text)))
    header, body = rows[0], rows[1:]
    assert header[:5] == ["entry_id", "entry_date", "event_date", "status", "source_kind"]
    kinds = {row[4] for row in body}
    assert {"bill", "allocation"} <= kinds, (posted.json(), client.get(f"{API}/allocations", headers=OWNER).json())
    bill_row = next(row for row in body if row[4] == "bill")
    assert bill_row[header.index("evidence_document_ids")] == receipt and bill_row[header.index("evidence_count")] == "1"
    # The allocation row carries the business's own reference only: no card, account, merchant or message text.
    allocation_row = next(row for row in body if row[4] == "allocation")
    text = index.text.casefold()
    for personal in ("1234", card["name"].casefold(), "petromin", "my visa", "synthetic. purchase", card["id"].casefold()):
        assert personal not in text, personal
    assert allocation_row[header.index("source_reference")].startswith("#") and allocation_row[header.index("description")] == "Paid by a member"

    pack = client.get(f"{API}/evidence-pack.zip", headers=OWNER, params=span)
    assert pack.status_code == 200 and pack.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(pack.content))
    names = archive.namelist()
    assert "index.csv" in names and "files.csv" in names
    stored = [n for n in names if n.startswith(f"documents/{receipt}-")]
    assert len(stored) == 1 and archive.read(stored[0]).endswith(b"%synthetic receipt\n")
    for name in names:
        assert b"petromin" not in archive.read(name).lower()
    # Owners and accountants only; every export is audited with counts.
    assert client.get(f"{API}/evidence-pack.zip", headers=MEMBER, params=span).status_code == 403
    assert client.get(f"{API}/evidence-pack.csv", headers=VIEWER, params=span).status_code == 403
    exports = [r for r in memory_repository.store.audit_records if r.action == "business.books.evidence_pack"]
    assert [r.metadata["format"] for r in exports] == ["csv", "zip"] and exports[1].metadata["files"] == 1


def test_other_tenants_and_businesses_see_no_evidence() -> None:
    setup_books()
    landlord = vendor()
    target = bill(landlord["id"]).json()
    attach("bill", target["id"], document())
    other = {**OWNER, "x-tenant-id": f"{TENANT}_other"}
    seen = client.get(f"{API}/evidence", headers=other, params={"source_type": "bill", "ids": target["id"]})
    assert seen.status_code in (403, 404, 409) or seen.json()["items"][target["id"]]["documents"] == []
    elsewhere = {**OWNER, "x-workspace-id": f"{BUSINESS}_elsewhere"}
    seen = client.get(f"{API}/evidence", headers=elsewhere, params={"source_type": "bill", "ids": target["id"]})
    assert seen.status_code in (403, 404, 409) or seen.json()["items"][target["id"]]["documents"] == []
