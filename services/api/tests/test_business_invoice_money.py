"""Credit notes, customer payment notices and the server-made PDF of a business invoice
(docs/business.md#credit-notes, docs/business-portal.md#reporting-a-payment, docs/business.md#printing-and-pdf),
against the in-memory backend. The portal side of the notices is in ``test_business_portal.py``.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from anum_api import main
from anum_api.business.invoice_models import Invoice
from anum_api.business.invoice_service import credit_split
from anum_api.business.store import business_store
from anum_api.schemas import utc_now
from anum_api.suppression import suppression_store

from test_business import BASE, MEMBER, OTHER_TENANT, OTHER_WORKSPACE, OWNER, VIEWER, ok
from test_business_change_orders import project as make_project
from test_business_invoices import create, send, sent_invoice, set_profile
from test_business_invoices import portal_report as notice_for

client = TestClient(main.app)
TODAY = utc_now().date().isoformat()


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    suppression_store.clear()
    yield
    business_store.clear()
    suppression_store.clear()


def manual_invoice(unit_price: float = 1000, tax: float = 15) -> dict[str, Any]:
    """A sent invoice of one manual line: 1000 net + 15% = 1150 by default."""
    set_profile()
    project = make_project(total=1000, tax=0)
    drafted = ok(create(account_id=project["account_id"], lines=[{"description": "Consulting", "quantity": 1, "unit_price": unit_price, "tax_rate_pct": tax}]), 201)
    return send(drafted)


def credit(invoice_id: str, amount: float, reason: str = "Price correction", headers: dict[str, str] = OWNER) -> Any:
    return client.post(f"{BASE}/invoices/{invoice_id}/credit-notes", headers=headers, json={"amount": amount, "reason": reason})


def pay(invoice_id: str, amount: float, headers: dict[str, str] = MEMBER, **extra: Any) -> Any:
    return client.post(f"{BASE}/invoices/{invoice_id}/payments", headers=headers, json={"amount": amount, "paid_on": TODAY, **extra})


# -- credit notes ----------------------------------------------------------------------------------


def test_credit_split_adds_up_to_the_invoice_tax_exactly() -> None:
    from anum_api.business.invoice_models import CreditNote

    now = utc_now()
    invoice = Invoice(id="i", tenant_id="t", workspace_id="w", account_id="a", currency="SAR", subtotal=Decimal("1000.00"), tax=Decimal("150.00"), total=Decimal("1150.00"),
                      created_by="u", created_at=now, updated_at=now)
    notes: list[CreditNote] = []
    for index, amount in enumerate((Decimal("333.33"), Decimal("333.33"), Decimal("483.34")), start=1):
        subtotal, tax = credit_split(invoice, notes, amount)
        assert subtotal + tax == amount
        notes.append(CreditNote(id=f"c{index}", tenant_id="t", workspace_id="w", invoice_id="i", account_id="a", seq=index, number=f"N-CN{index}", currency="SAR", amount=amount,
                                subtotal=subtotal, tax=tax, reason="Correction", issue_date=now.date(), issued_by="u", created_at=now, updated_at=now))
    assert sum(item.tax for item in notes) == Decimal("150.00") and sum(item.subtotal for item in notes) == Decimal("1000.00")


def test_a_credit_note_lowers_the_balance_keeps_the_invoice_and_splits_the_tax() -> None:
    sent = manual_invoice()
    assert sent["total"] == 1150 and sent["can"]["credit"] is True and sent["can"]["void_credit_note"] is True
    view = ok(credit(sent["id"], 115), 201)
    note = view["credit_notes"][0]
    assert note["number"] == f"{sent['number']}-CN1" and note["status"] == "issued" and note["issue_date"] == TODAY
    assert (note["amount"], note["subtotal"], note["tax"]) == (115, 100, 15)
    # The invoice itself did not change; its balance did.
    assert (view["total"], view["tax"], view["amount_credited"], view["balance"], view["status"], view["payment_state"]) == (1150, 150, 115, 1035, "sent", "unpaid")
    assert view["content_hash"] == sent["content_hash"] and view["can"]["void"] is False
    second = ok(credit(sent["id"], 35, "Goodwill"), 201)
    assert [item["number"] for item in second["credit_notes"]] == [f"{sent['number']}-CN1", f"{sent['number']}-CN2"] and second["amount_credited"] == 150
    listed = ok(client.get(f"{BASE}/invoices/{sent['id']}/credit-notes", headers=MEMBER))
    assert [item["amount"] for item in listed] == [115, 35]
    row = next(item for item in ok(client.get(f"{BASE}/invoices", headers=MEMBER))["items"] if item["id"] == sent["id"])
    assert (row["amount_credited"], row["balance"]) == (150, 1000)
    summary = ok(client.get(f"{BASE}/invoices/summary", headers=MEMBER))
    assert summary["currencies"][0]["outstanding"] == 1000


def test_a_credit_note_is_an_owner_decision_and_never_more_than_is_owed() -> None:
    sent = manual_invoice()
    assert credit(sent["id"], 10, headers=MEMBER).status_code == 403
    assert credit(sent["id"], 10, headers=VIEWER).status_code == 403
    assert client.get(f"{BASE}/invoices/{sent['id']}/credit-notes", headers=VIEWER).status_code == 403
    assert credit(sent["id"], 0).status_code == 422 and credit(sent["id"], -5).status_code == 422
    assert credit(sent["id"], 10, reason="ab").status_code == 422
    too_much = credit(sent["id"], 1150.01)
    assert too_much.status_code == 422 and "balance" in too_much.text
    ok(pay(sent["id"], 500), 201)
    assert credit(sent["id"], 650.01).status_code == 422
    assert credit(sent["id"], 650).status_code == 201
    # Everything is covered now: nothing left to credit.
    refused = credit(sent["id"], 1)
    assert refused.status_code == 409 and "nothing left" in refused.text
    draft = ok(create(account_id=sent["account_id"], lines=[{"description": "Work", "unit_price": 100}]), 201)
    assert credit(draft["id"], 10).status_code == 409


def test_payments_and_credits_settle_an_invoice_together() -> None:
    project = make_project(total=2000, tax=0)
    sent = sent_invoice(project, count=1)
    total = sent["total"]
    ok(pay(sent["id"], 400), 201)
    settled = ok(credit(sent["id"], total - 400, "Agreed discount"), 201)
    assert (settled["status"], settled["balance"], settled["payment_state"], settled["can"]["credit"]) == ("paid", 0, "paid", False) and settled["paid_at"]
    # Credits stand in for cash that never came: the milestone is not marked as collected.
    assert ok(client.get(f"{BASE}/projects/{project['id']}", headers=OWNER))["milestones"][0]["paid_amount"] is None
    # Taking the credit back owes the money again.
    reopened = ok(client.post(f"{BASE}/invoices/{sent['id']}/credit-notes/{settled['credit_notes'][0]['id']}/void", headers=OWNER, json={"reason": "Entered twice"}))
    assert (reopened["status"], reopened["amount_credited"], reopened["balance"], reopened["paid_at"]) == ("sent", 0, total - 400, None)
    assert reopened["credit_notes"][0]["status"] == "void" and reopened["credit_notes"][0]["void_reason"] == "Entered twice"
    # Paying the rest afterwards is ordinary cash: now the milestone is marked.
    done = ok(pay(sent["id"], total - 400), 201)
    assert done["status"] == "paid" and ok(client.get(f"{BASE}/projects/{project['id']}", headers=OWNER))["milestones"][0]["paid_amount"] == total


def test_an_invoice_settled_by_credit_alone_is_credited_not_paid() -> None:
    sent = manual_invoice()
    done = ok(credit(sent["id"], 1150, "Cancelled by agreement"), 201)
    assert (done["status"], done["payment_state"], done["amount_paid"], done["balance"]) == ("paid", "credited", 0, 0)
    assert pay(sent["id"], 1).status_code == 409


def test_a_voided_credit_note_keeps_its_number_and_the_next_one_gets_a_new_one() -> None:
    sent = manual_invoice()
    first = ok(credit(sent["id"], 100), 201)
    note_id = first["credit_notes"][0]["id"]
    assert client.post(f"{BASE}/invoices/{sent['id']}/credit-notes/{note_id}/void", headers=MEMBER, json={"reason": "Mistake"}).status_code == 403
    assert client.post(f"{BASE}/invoices/{sent['id']}/credit-notes/{note_id}/void", headers=OWNER, json={"reason": "ab"}).status_code == 422
    assert client.post(f"{BASE}/invoices/{sent['id']}/credit-notes/missing/void", headers=OWNER, json={"reason": "Mistake"}).status_code == 404
    ok(client.post(f"{BASE}/invoices/{sent['id']}/credit-notes/{note_id}/void", headers=OWNER, json={"reason": "Mistake"}))
    again = client.post(f"{BASE}/invoices/{sent['id']}/credit-notes/{note_id}/void", headers=OWNER, json={"reason": "Mistake"})
    assert again.status_code == 409
    second = ok(credit(sent["id"], 50), 201)
    assert [item["number"] for item in second["credit_notes"]] == [f"{sent['number']}-CN1", f"{sent['number']}-CN2"]
    assert second["amount_credited"] == 50 and second["balance"] == 1100


def test_an_invoice_with_a_credit_note_cannot_be_voided_until_it_is_taken_back() -> None:
    sent = manual_invoice()
    view = ok(credit(sent["id"], 100), 201)
    refused = client.post(f"{BASE}/invoices/{sent['id']}/void", headers=OWNER, json={"reason": "Reissue"})
    assert refused.status_code == 409 and "credit notes" in refused.text
    ok(client.post(f"{BASE}/invoices/{sent['id']}/credit-notes/{view['credit_notes'][0]['id']}/void", headers=OWNER, json={"reason": "Mistake"}))
    assert ok(client.post(f"{BASE}/invoices/{sent['id']}/void", headers=OWNER, json={"reason": "Reissue"}))["status"] == "void"


def test_credit_notes_are_scoped_to_their_workspace() -> None:
    sent = manual_invoice()
    for headers in (OTHER_WORKSPACE, OTHER_TENANT):
        assert credit(sent["id"], 10, headers=headers).status_code == 404
        assert client.get(f"{BASE}/invoices/{sent['id']}/credit-notes", headers=headers).status_code == 404


def test_credit_notes_show_on_the_printable_copy() -> None:
    sent = manual_invoice()
    ok(credit(sent["id"], 100, "Price <b>correction</b>"), 201)
    page = client.get(f"{BASE}/invoices/{sent['id']}/print", headers=OWNER).text
    assert f"{sent['number']}-CN1" in page and "Credit notes" in page and "&lt;b&gt;correction" in page and "<b>correction" not in page and "1,050.00" in page


# -- PDF ---------------------------------------------------------------------------------------------


def pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_the_pdf_is_made_on_the_server_with_the_stored_figures() -> None:
    sent = manual_invoice()
    ok(pay(sent["id"], 200, reference="TRX-77"), 201)
    ok(credit(sent["id"], 100, "Goodwill"), 201)
    response = client.get(f"{BASE}/invoices/{sent['id']}/pdf", headers=MEMBER)
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf" and response.content.startswith(b"%PDF")
    assert response.headers["content-disposition"] == f'attachment; filename="{sent["number"]}.pdf"'
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
    text = pdf_text(response.content)
    for needle in (sent["number"], "Tax invoice", "Anum Technologies LLC", "Gulf Mixers LLC", "Consulting", "1,150.00", "850.00", "TRX-77", f"{sent['number']}-CN1", "Goodwill"):
        assert needle in text, needle
    assert "DRAFT" not in text


def test_a_draft_pdf_says_it_is_not_an_invoice_and_a_void_one_says_void() -> None:
    set_profile()
    project = make_project(total=1000, tax=0)
    draft = ok(create(account_id=project["account_id"], lines=[{"description": "Work", "unit_price": 100}]), 201)
    response = client.get(f"{BASE}/invoices/{draft['id']}/pdf", headers=MEMBER)
    assert response.status_code == 200 and response.headers["content-disposition"] == f'attachment; filename="draft-{draft["id"]}.pdf"'
    assert "DRAFT" in pdf_text(response.content)
    sent = send(draft)
    ok(client.post(f"{BASE}/invoices/{sent['id']}/void", headers=OWNER, json={"reason": "Reissue"}))
    assert "VOID" in pdf_text(client.get(f"{BASE}/invoices/{sent['id']}/pdf", headers=OWNER).content)


def test_the_pdf_follows_the_same_permissions_and_scopes_as_the_invoice() -> None:
    sent = manual_invoice()
    assert client.get(f"{BASE}/invoices/{sent['id']}/pdf", headers=VIEWER).status_code == 403
    assert client.get(f"{BASE}/invoices/missing/pdf", headers=OWNER).status_code == 404
    assert client.get(f"{BASE}/invoices/{sent['id']}/pdf", headers=OTHER_WORKSPACE).status_code == 404
    assert client.get(f"{BASE}/invoices/{sent['id']}/pdf", headers=OTHER_TENANT).status_code == 404


def test_pdf_text_outside_latin_is_replaced_and_never_breaks_the_file() -> None:
    set_profile()
    project = make_project(total=1000, tax=0)
    drafted = ok(create(account_id=project["account_id"], bill_to_name="شركة الخليج (Gulf) <script>", lines=[{"description": "استشارة (a) \\ b", "unit_price": 100}]), 201)
    sent = send(drafted)
    response = client.get(f"{BASE}/invoices/{sent['id']}/pdf", headers=OWNER)
    assert response.status_code == 200
    text = pdf_text(response.content)
    assert "(Gulf)" in text and "(a)" in text and sent["number"] in text


# -- payment notices (owner side; the portal side is in test_business_portal.py) -----------------------


def test_confirming_a_customer_notice_records_an_ordinary_payment() -> None:
    sent = manual_invoice()
    notice = notice_for(sent, 400)
    assert notice["status"] == "pending" and notice["payer_name"] == "Sara Khan"
    # Nothing changed until a person checks their bank.
    waiting = ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=OWNER))
    assert (waiting["amount_paid"], waiting["balance"], len(waiting["payment_notices"]), waiting["can"]["decide_notice"]) == (0, 1150, 1, True)
    row = next(item for item in ok(client.get(f"{BASE}/invoices", headers=MEMBER))["items"] if item["id"] == sent["id"])
    assert row["notices_pending"] == 1
    assert ok(client.get(f"{BASE}/invoices/summary", headers=MEMBER))["notices_pending"] == 1
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/confirm", headers=VIEWER, json={}).status_code == 403
    confirmed = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/confirm", headers=MEMBER, json={"note": "Seen on the statement"}))
    assert (confirmed["amount_paid"], confirmed["balance"], confirmed["payment_state"]) == (400, 750, "partially_paid")
    payment = confirmed["payments"][0]
    assert (payment["amount"], payment["reference"], payment["method"], payment["recorded_by"]) == (400, "TRX-9", "bank_transfer", "member_biz")
    assert "Sara Khan" in payment["note"] and "Seen on the statement" in payment["note"]
    done = confirmed["payment_notices"][0]
    assert (done["status"], done["decided_by"], done["payment_id"]) == ("confirmed", "member_biz", payment["id"])
    again = client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/confirm", headers=MEMBER, json={})
    assert again.status_code == 409 and "already confirmed" in again.text
    assert len(ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=OWNER))["payments"]) == 1
    assert ok(client.get(f"{BASE}/invoices/summary", headers=MEMBER))["notices_pending"] == 0


def test_confirming_the_last_notice_pays_the_invoice() -> None:
    project = make_project(total=2000, tax=0)
    sent = sent_invoice(project, count=1)
    notice = notice_for(sent, sent["total"])
    paid = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/confirm", headers=OWNER, json={}))
    assert paid["status"] == "paid" and ok(client.get(f"{BASE}/projects/{project['id']}", headers=OWNER))["milestones"][0]["paid_amount"] == sent["total"]


def test_rejecting_a_notice_needs_a_reason_changes_nothing_and_shows_to_the_customer() -> None:
    sent = manual_invoice()
    notice = notice_for(sent, 300)
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/reject", headers=MEMBER, json={"reason": "no"}).status_code == 422
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/reject", headers=VIEWER, json={"reason": "Not in our bank"}).status_code == 403
    rejected = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/reject", headers=MEMBER, json={"reason": "Nothing arrived on this date"}))
    assert rejected["amount_paid"] == 0 and rejected["payment_notices"][0]["status"] == "rejected" and rejected["payment_notices"][0]["decision_note"] == "Nothing arrived on this date"
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/confirm", headers=MEMBER, json={}).status_code == 409
    assert client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/missing/reject", headers=MEMBER, json={"reason": "Not in our bank"}).status_code == 404


def test_a_notice_that_no_longer_fits_the_balance_is_refused_with_advice() -> None:
    sent = manual_invoice()
    notice = notice_for(sent, 900)
    ok(credit(sent["id"], 500), 201)
    refused = client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/confirm", headers=OWNER, json={})
    assert refused.status_code == 422 and "balance" in refused.text and "Reject this notice" in refused.text
    assert ok(client.get(f"{BASE}/invoices/{sent['id']}", headers=OWNER))["payment_notices"][0]["status"] == "pending"


def test_notices_are_scoped_to_their_workspace() -> None:
    sent = manual_invoice()
    notice = notice_for(sent, 100)
    for headers in (OTHER_WORKSPACE, OTHER_TENANT):
        assert client.get(f"{BASE}/invoices/{sent['id']}/payment-notices", headers=headers).status_code == 404
        assert client.post(f"{BASE}/invoices/{sent['id']}/payment-notices/{notice['id']}/confirm", headers=headers, json={}).status_code == 404
