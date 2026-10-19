"""Business invoices (docs/business.md#invoices), against the in-memory backend.

Covers the billing profile, invoices built from milestones (change order milestones
included), a contract without milestones, billable ledger entries and catalog offers; tax
reconciliation; the approval that sends an invoice and the hash it binds; numbering; payments
recorded by hand and their effect on milestones; voiding; the printable copy; permissions and
cross-workspace and cross-tenant isolation; and the audit walk.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business.invoice_models import InvoiceLine, price_line
from anum_api.business.invoices import apply_lines, invoice_hash, problems
from anum_api.business.models import BillingProfile
from anum_api.business.store import business_store
from anum_api.schemas import utc_now
from anum_api.suppression import suppression_store

from test_business import BASE, MEMBER, OTHER_TENANT, OTHER_WORKSPACE, OWNER, VIEWER, approve, lead, ok, proposal_body, qualify
from test_business_change_orders import decide, draft as draft_change, project as make_project, submit as submit_change

client = TestClient(main.app)
YEAR = utc_now().year

PROFILE = {
    "legal_name": "Anum Technologies LLC", "address": "Office 12, Riyadh\nSaudi Arabia", "tax_label": "VAT number", "tax_number": "300000000000003",
    "email": "billing@anum.example", "phone": "+966500000000", "bank_details": "Bank: Example Bank\nIBAN: SA00 0000 0000 0000 0000 0000",
    "footer_note": "Thank you for your business.", "invoice_prefix": "INV", "default_due_days": 14, "default_tax_rate_pct": 15,
}


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    business_store.clear()
    suppression_store.clear()
    yield
    business_store.clear()
    suppression_store.clear()


def set_profile(**changes: Any) -> dict[str, Any]:
    return ok(client.put(f"{BASE}/billing-profile", headers=OWNER, json={**PROFILE, **changes}))


def create(headers: dict[str, str] = MEMBER, **body: Any) -> Any:
    return client.post(f"{BASE}/invoices", headers=headers, json=body)


def milestone_ids(project: dict[str, Any]) -> list[str]:
    return [item["id"] for item in project["milestones"]]


def request_send(invoice_id: str, headers: dict[str, str] = MEMBER) -> dict[str, Any]:
    return ok(client.post(f"{BASE}/invoices/{invoice_id}/request-approval", headers=headers))


def send(invoice: dict[str, Any]) -> dict[str, Any]:
    """Draft to sent: request, then an owner approves with the hash they were shown."""
    pending = request_send(invoice["id"])
    assert pending["status"] == "pending_approval" and pending["approval_state"] == "pending"
    assert approve(pending["approval"]).status_code == 200
    return ok(client.get(f"{BASE}/invoices/{invoice['id']}", headers=OWNER))


def sent_invoice(project: dict[str, Any], count: int = 1) -> dict[str, Any]:
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:count]), 201)
    return send(drafted)


# -- pure rules ------------------------------------------------------------------------------------


def test_lines_are_priced_in_decimal_and_a_gross_line_reconciles_to_the_contract() -> None:
    plain = price_line(InvoiceLine(description="Setup", quantity=Decimal("3"), unit_price=Decimal("33.33"), tax_rate_pct=Decimal("15")))
    assert (plain.net, plain.tax, plain.total) == (Decimal("99.99"), Decimal("15.00"), Decimal("114.99"))
    gross = price_line(InvoiceLine(description="Deposit", tax_rate_pct=Decimal("15"), gross_total=Decimal("1437.50")))
    assert (gross.net, gross.tax, gross.total) == (Decimal("1250.00"), Decimal("187.50"), Decimal("1437.50"))
    odd = price_line(InvoiceLine(description="Odd", tax_rate_pct=Decimal("15"), gross_total=Decimal("100.00")))
    assert odd.net + odd.tax == odd.total == Decimal("100.00")


def test_the_hash_binds_the_lines_the_buyer_and_the_seller() -> None:
    from anum_api.business.invoice_models import Invoice

    now = utc_now()
    base = Invoice(id="i", tenant_id="t", workspace_id="w", account_id="a", bill_to_name="Acme", created_by="u", created_at=now, updated_at=now,
                   seller=BillingProfile(legal_name="Anum"))
    invoice = apply_lines(base, [InvoiceLine(description="Work", unit_price=Decimal("100"))])
    first = invoice_hash(invoice)
    assert invoice_hash(invoice) == first
    assert invoice_hash(apply_lines(base, [InvoiceLine(description="Work", unit_price=Decimal("101"))])) != first
    assert invoice_hash(invoice.model_copy(update={"bill_to_name": "Other"})) != first
    assert invoice_hash(invoice.model_copy(update={"seller": BillingProfile(legal_name="Someone else")})) != first
    assert problems(invoice) == []
    assert "Name the customer" in " ".join(problems(invoice.model_copy(update={"bill_to_name": ""})))


# -- billing profile -------------------------------------------------------------------------------


def test_billing_profile_is_owner_managed_and_validated() -> None:
    assert ok(client.get(f"{BASE}/billing-profile", headers=MEMBER))["invoice_prefix"] == "INV"
    assert client.put(f"{BASE}/billing-profile", headers=MEMBER, json=PROFILE).status_code == 403
    assert client.put(f"{BASE}/billing-profile", headers=OWNER, json={**PROFILE, "invoice_prefix": "A B"}).status_code == 422
    saved = set_profile(legal_name="  Anum   Technologies – LLC ")
    assert saved["legal_name"] == "Anum Technologies, LLC" or "–" not in saved["legal_name"]
    assert ok(client.get(f"{BASE}/billing-profile", headers=VIEWER if False else MEMBER))["tax_number"] == PROFILE["tax_number"]
    # The research caps and the profile are saved apart and neither wipes the other.
    ok(client.put(f"{BASE}/settings", headers=OWNER, json={"research_runs_per_day": 5, "research_searches_per_day": 20}))
    assert ok(client.get(f"{BASE}/billing-profile", headers=OWNER))["tax_number"] == PROFILE["tax_number"]


# -- from milestones ---------------------------------------------------------------------------------


def test_a_milestone_invoice_reconciles_with_the_contract_and_waits_for_an_owner() -> None:
    project = make_project(total=2500, tax=15)
    set_profile()
    candidates = ok(client.get(f"{BASE}/invoices/billable?project_id={project['id']}", headers=MEMBER))
    assert [item["billable"] for item in candidates["milestones"]] == [True, True] and candidates["tax_rate_pct"] == 15
    invoice = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)), 201)
    assert (invoice["status"], invoice["subtotal"], invoice["tax"], invoice["total"]) == ("draft", 2500, 375, 2875)
    assert invoice["bill_to_name"] == "Gulf Mixers LLC" and invoice["bill_to_email"] == "ops@gulfmixers.example" and invoice["number"] is None
    assert invoice["can"]["request_send"] is True and invoice["issues"] == []
    pending = request_send(invoice["id"])
    assert pending["approval"]["payload_hash"]
    assert pending["can"]["edit"] is True and pending["number"] is None
    # A member cannot decide; the owner can, with the hash they were shown.
    assert approve(pending["approval"], MEMBER).status_code == 403
    assert ok(client.get(f"{BASE}/invoices/{invoice['id']}", headers=OWNER))["status"] == "pending_approval"
    assert approve(pending["approval"]).status_code == 200
    final = ok(client.get(f"{BASE}/invoices/{invoice['id']}", headers=OWNER))
    assert final["status"] == "sent" and final["number"] == f"INV-{YEAR}-0001" and final["issue_date"] == utc_now().date().isoformat()
    assert final["due_date"] and final["sent_by"] == "owner_biz" and final["seller"]["legal_name"] == "Anum Technologies LLC"
    assert final["can"]["edit"] is False and final["balance"] == 2875 and final["overdue"] is False and final["payment_state"] == "unpaid"
    # A sent invoice never changes.
    assert client.patch(f"{BASE}/invoices/{invoice['id']}", headers=MEMBER, json={"notes": "late edit"}).status_code == 409
    assert client.delete(f"{BASE}/invoices/{invoice['id']}", headers=MEMBER).status_code == 409


def test_sending_needs_a_billing_profile_a_customer_and_a_price() -> None:
    project = make_project(total=1000, tax=0)
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    assert any("billing profile" in item for item in drafted["issues"])
    blocked = client.post(f"{BASE}/invoices/{drafted['id']}/request-approval", headers=MEMBER)
    assert blocked.status_code == 409 and "billing profile" in blocked.text
    set_profile()
    assert client.patch(f"{BASE}/invoices/{drafted['id']}", headers=MEMBER, json={"bill_to_name": ""}).status_code == 422
    # Tax without a tax number is refused.
    taxed = ok(create(account_id=drafted["account_id"], lines=[{"description": "Extra", "unit_price": 100, "tax_rate_pct": 15}]), 201)
    set_profile(tax_number="")
    assert client.post(f"{BASE}/invoices/{taxed['id']}/request-approval", headers=MEMBER).status_code == 409


def test_editing_a_waiting_invoice_lapses_the_request_and_the_approval_cannot_be_used() -> None:
    project = make_project(total=2000, tax=0)
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    pending = request_send(drafted["id"])
    edited = ok(client.patch(f"{BASE}/invoices/{drafted['id']}", headers=MEMBER, json={"notes": "PO 1234", "lines": [{"description": "Extra day", "unit_price": 50}]}))
    assert edited["status"] == "draft" and edited["approval_state"] in {"none", "expired"} and edited["total"] == 1050
    stale = approve(pending["approval"])
    assert stale.status_code >= 400 or ok(client.get(f"{BASE}/invoices/{drafted['id']}", headers=OWNER))["status"] == "draft"
    assert ok(client.get(f"{BASE}/invoices/{drafted['id']}", headers=OWNER))["number"] is None


def test_a_rejected_request_returns_to_a_draft_with_the_reason() -> None:
    project = make_project(total=1000, tax=0)
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    pending = request_send(drafted["id"])
    rejected = client.post(f"/api/v1/approvals/{pending['approval']['id']}/reject", headers=OWNER, json={"reason": "Wrong amount"})
    assert rejected.status_code == 200, rejected.text
    again = ok(client.get(f"{BASE}/invoices/{drafted['id']}", headers=MEMBER))
    assert again["status"] == "draft" and "Wrong amount" in again["decision_note"]
    # It can be requested again.
    assert request_send(drafted["id"])["status"] == "pending_approval"


def test_cancelling_a_request_returns_the_invoice_to_a_draft() -> None:
    project = make_project(total=1000, tax=0)
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    pending = request_send(drafted["id"])
    cancelled = ok(client.post(f"{BASE}/invoices/{drafted['id']}/cancel-request", headers=MEMBER))
    assert cancelled["status"] == "draft"
    assert client.post(f"{BASE}/invoices/{drafted['id']}/cancel-request", headers=MEMBER).status_code == 409
    assert approve(pending["approval"]).status_code >= 400 or ok(client.get(f"{BASE}/invoices/{drafted['id']}", headers=OWNER))["status"] == "draft"


# -- numbering and double billing ----------------------------------------------------------------------


def test_numbers_are_sequential_and_never_reused() -> None:
    project = make_project(total=2000, tax=0)
    first = sent_invoice(project)
    second_draft = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[1:2]), 201)
    second = send(second_draft)
    assert (first["number"], second["number"]) == (f"INV-{YEAR}-0001", f"INV-{YEAR}-0002")
    voided = ok(client.post(f"{BASE}/invoices/{second['id']}/void", headers=OWNER, json={"reason": "Wrong customer"}))
    assert voided["status"] == "void" and voided["number"] == f"INV-{YEAR}-0002" and voided["void_reason"] == "Wrong customer"
    # The next invoice takes 0003: a voided number stays used.
    again = send(ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[1:2]), 201))
    assert again["number"] == f"INV-{YEAR}-0003"
    set_profile(invoice_prefix="ACME")
    third = send(ok(create(account_id=project["account_id"], lines=[{"description": "Call-out", "unit_price": 10}]), 201))
    assert third["number"] == f"ACME-{YEAR}-0004"


def test_a_milestone_is_billed_once_until_the_invoice_is_voided() -> None:
    project = make_project(total=2000, tax=0)
    set_profile()
    first = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    clash = create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1])
    assert clash.status_code == 409 and "already on an invoice" in clash.text
    listed = ok(client.get(f"{BASE}/invoices/billable?project_id={project['id']}", headers=MEMBER))
    assert [item["billable"] for item in listed["milestones"]] == [False, True] and "draft invoice" in listed["milestones"][0]["reason"]
    sent = send(first)
    ok(client.post(f"{BASE}/invoices/{sent['id']}/void", headers=OWNER, json={"reason": "Reissue"}))
    assert create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]).status_code == 201


def test_an_account_with_invoices_is_not_deleted() -> None:
    project = make_project(total=1000, tax=0)
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    refused = client.delete(f"{BASE}/accounts/{project['account_id']}", headers=OWNER)
    assert refused.status_code == 409
    assert drafted["id"]


def test_a_draft_can_be_deleted_and_frees_its_milestones() -> None:
    project = make_project(total=2000, tax=0)
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    assert client.delete(f"{BASE}/invoices/{drafted['id']}", headers=MEMBER).status_code == 204
    assert client.get(f"{BASE}/invoices/{drafted['id']}", headers=MEMBER).status_code == 404
    assert create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]).status_code == 201


# -- change orders, the ledger, offers and the contract ---------------------------------------------------


def test_an_approved_change_order_milestone_can_be_invoiced() -> None:
    project = make_project(total=2500, tax=0)
    change = submit_change(draft_change(project["id"])["id"])
    assert decide(change).status_code == 200
    candidates = ok(client.get(f"{BASE}/invoices/billable?project_id={project['id']}", headers=MEMBER))
    added = [item for item in candidates["milestones"] if item["change_order_number"] == 1]
    assert len(added) == 1 and added[0]["amount"] == 800
    set_profile()
    invoice = ok(create(project_id=project["id"], milestone_ids=[added[0]["id"]]), 201)
    assert invoice["total"] == 800 and invoice["lines"][0]["source"] == "milestone" and "Change order 1" in invoice["lines"][0]["description"]


def test_billable_ledger_entries_are_invoiced_at_cost_or_with_a_markup() -> None:
    project = make_project(total=1000, tax=0)
    set_profile()
    billable = ok(client.post(f"{BASE}/usage", headers=MEMBER, json={"project_id": project["id"], "kind": "labour", "description": "Extra training", "occurred_on": "2026-10-01",
                                                                      "quantity": 3, "unit_cost": 100, "billable": True}), 201)
    internal = ok(client.post(f"{BASE}/usage", headers=MEMBER, json={"project_id": project["id"], "kind": "hosting", "description": "Staging server", "occurred_on": "2026-10-01",
                                                                      "quantity": 1, "unit_cost": 40}), 201)
    dollars = ok(client.post(f"{BASE}/usage", headers=MEMBER, json={"project_id": project["id"], "kind": "ai_usage", "description": "AI month", "occurred_on": "2026-09-01",
                                                                     "quantity": 1, "unit_cost": 9, "currency": "USD", "billable": True}), 201)
    listed = ok(client.get(f"{BASE}/invoices/billable?project_id={project['id']}", headers=MEMBER))
    assert {item["id"] for item in listed["usage"]} == {billable["id"], dollars["id"]}
    assert [item["billable"] for item in listed["usage"] if item["id"] == dollars["id"]] == [False]
    assert create(project_id=project["id"], usage_ids=[internal["id"]]).status_code == 409
    assert create(project_id=project["id"], usage_ids=[dollars["id"]]).status_code == 409
    invoice = ok(create(project_id=project["id"], usage_ids=[billable["id"]], usage_markup_pct=10, tax_rate_pct=15), 201)
    assert invoice["lines"][0]["unit_price"] == 110 and invoice["lines"][0]["net"] == 330 and invoice["total"] == 379.5 and invoice["lines"][0]["kind"] == "labor"
    assert create(project_id=project["id"], usage_ids=[billable["id"]]).status_code == 409


def test_a_contract_without_milestones_is_invoiced_in_shares_up_to_the_total() -> None:
    created = lead()
    qualify(created["lead"]["id"])
    opportunity = ok(client.post(f"{BASE}/opportunities", headers=MEMBER, json={"lead_id": created["lead"]["id"]}), 201)
    body = proposal_body(opportunity["id"], 3000)
    body["content"]["milestones"] = []
    body["content"]["tax_rate_pct"] = 15
    proposal = ok(client.post(f"{BASE}/proposals", headers=MEMBER, json=body), 201)
    assert approve(ok(client.post(f"{BASE}/proposals/{proposal['id']}/request-acceptance", headers=MEMBER))["approval"]).status_code == 200
    project = ok(client.post(f"{BASE}/projects", headers=MEMBER, json={"proposal_id": proposal["id"]}), 201)
    set_profile()
    listed = ok(client.get(f"{BASE}/invoices/billable?project_id={project['id']}", headers=MEMBER))
    assert listed["milestones"] == [] and listed["contract"]["remaining"] == 3450
    assert create(project_id=project["id"], contract_amount=3451).status_code == 422
    first = ok(create(project_id=project["id"], contract_amount=1725), 201)
    assert (first["subtotal"], first["tax"], first["total"]) == (1500, 225, 1725)
    assert ok(client.get(f"{BASE}/invoices/billable?project_id={project['id']}", headers=MEMBER))["contract"]["remaining"] == 1725
    assert create(project_id=project["id"], contract_amount=1725.01).status_code == 422
    assert create(project_id=project["id"], contract_amount=1725).status_code == 201
    assert create(project_id=project["id"], contract_amount=0.01).status_code == 422


def test_an_offer_becomes_a_line_and_a_range_needs_a_price() -> None:
    from test_business import product

    detail = product(price=2500)
    offer = next(item for item in detail["offers"] if item["status"] == "active")
    created = lead()
    set_profile()
    account_id = created["account"]["id"]
    invoice = ok(create(account_id=account_id, offer_id=offer["id"], offer_quantity=2, tax_rate_pct=0), 201)
    assert invoice["total"] == 5000 and invoice["lines"][0]["source"] == "offer" and "Reporting Quick Win" in invoice["lines"][0]["description"]
    assert create(account_id=account_id, offer_id="missing").status_code == 404
    listed = ok(client.get(f"{BASE}/invoices/billable?account_id={account_id}", headers=MEMBER))
    assert listed["offers"] and listed["offers"][0]["price"] == 2500


def test_something_to_invoice_is_required_and_scope_errors_are_plain() -> None:
    created = lead(company="Other Co", website="https://other.example", email="a@other.example")
    assert create(account_id=created["account"]["id"]).status_code == 422
    assert create().status_code == 422
    project = make_project(total=1000, tax=0)
    assert create(account_id="other", project_id=project["id"]).status_code == 422
    assert create(project_id=project["id"], milestone_ids=["missing"]).status_code == 404
    assert create(project_id=project["id"], currency="USD", lines=[{"description": "x", "unit_price": 1}]).status_code == 422
    assert create(account_id=created["account"]["id"], milestone_ids=milestone_ids(project)).status_code == 422


# -- payments --------------------------------------------------------------------------------------------


def test_payments_mark_the_milestones_paid_and_removing_one_undoes_it() -> None:
    project = make_project(total=2000, tax=0)
    sent = sent_invoice(project, count=1)
    pay = lambda amount, **extra: client.post(f"{BASE}/invoices/{sent['id']}/payments", headers=MEMBER,  # noqa: E731
                                              json={"amount": amount, "paid_on": utc_now().date().isoformat(), "method": "bank_transfer", "reference": "TRX-1", **extra})
    assert pay(0).status_code == 422 and pay(1000.01).status_code == 422
    assert client.post(f"{BASE}/invoices/{sent['id']}/payments", headers=MEMBER, json={"amount": 10, "paid_on": "2999-01-01"}).status_code == 422
    assert client.post(f"{BASE}/invoices/{sent['id']}/payments", headers=MEMBER, json={"amount": 10, "paid_on": "2000-01-01"}).status_code == 422
    part = ok(pay(400), 201)
    assert (part["status"], part["amount_paid"], part["balance"], part["payment_state"]) == ("sent", 400, 600, "partially_paid") and len(part["payments"]) == 1
    before = ok(client.get(f"{BASE}/projects/{project['id']}", headers=OWNER))
    assert before["milestones"][0]["paid_amount"] is None
    full = ok(pay(600), 201)
    assert (full["status"], full["balance"], full["payment_state"]) == ("paid", 0, "paid") and full["paid_at"] and full["can"]["record_payment"] is False
    paid = ok(client.get(f"{BASE}/projects/{project['id']}", headers=OWNER))
    assert paid["milestones"][0]["paid_amount"] == 1000 and paid["milestones"][1]["paid_amount"] is None
    assert pay(1).status_code == 409
    assert client.post(f"{BASE}/invoices/{sent['id']}/void", headers=OWNER, json={"reason": "Mistake"}).status_code == 409
    # Only an owner takes a payment back; the invoice is sent again and the milestone is unpaid.
    last = next(item for item in full["payments"] if item["amount"] == 600)
    assert client.delete(f"{BASE}/invoices/{sent['id']}/payments/{last['id']}", headers=MEMBER).status_code == 403
    undone = ok(client.delete(f"{BASE}/invoices/{sent['id']}/payments/{last['id']}", headers=OWNER))
    assert (undone["status"], undone["amount_paid"], undone["paid_at"]) == ("sent", 400, None)
    assert ok(client.get(f"{BASE}/projects/{project['id']}", headers=OWNER))["milestones"][0]["paid_amount"] is None
    assert client.delete(f"{BASE}/invoices/{sent['id']}/payments/missing", headers=OWNER).status_code == 404


def test_a_milestone_a_person_already_marked_paid_is_left_alone() -> None:
    project = make_project(total=2000, tax=0)
    sent = sent_invoice(project, count=1)
    ok(client.patch(f"{BASE}/milestones/{project['milestones'][0]['id']}", headers=MEMBER, json={"paid_amount": 1000, "paid_on": "2026-10-02"}))
    full = ok(client.post(f"{BASE}/invoices/{sent['id']}/payments", headers=MEMBER, json={"amount": 1000, "paid_on": utc_now().date().isoformat()}), 201)
    assert full["status"] == "paid" and full["marked_milestones"] == []
    ok(client.delete(f"{BASE}/invoices/{sent['id']}/payments/{full['payments'][0]['id']}", headers=OWNER))
    assert ok(client.get(f"{BASE}/projects/{project['id']}", headers=OWNER))["milestones"][0]["paid_amount"] == 1000


def test_an_invoice_with_a_payment_cannot_be_voided_and_an_unpaid_one_can() -> None:
    project = make_project(total=2000, tax=0)
    sent = sent_invoice(project)
    ok(client.post(f"{BASE}/invoices/{sent['id']}/payments", headers=MEMBER, json={"amount": 100, "paid_on": utc_now().date().isoformat()}), 201)
    refused = client.post(f"{BASE}/invoices/{sent['id']}/void", headers=OWNER, json={"reason": "Mistake"})
    assert refused.status_code == 409 and "payments" in refused.text
    assert client.post(f"{BASE}/invoices/{sent['id']}/void", headers=MEMBER, json={"reason": "Mistake"}).status_code == 403
    assert client.post(f"{BASE}/invoices/{sent['id']}/void", headers=OWNER, json={"reason": "ab"}).status_code == 422


def test_overdue_invoices_are_listed_and_summarised() -> None:
    project = make_project(total=4000, tax=0)
    sent = sent_invoice(project)
    late_id = sent["id"]
    # Move the due date into the past the way time would.
    stored = business_store._rows["invoices"][late_id]
    stored.due_date = utc_now().date().replace(year=utc_now().year - 1)
    stored.issue_date = stored.due_date
    listed = ok(client.get(f"{BASE}/invoices?overdue=true", headers=MEMBER))
    assert listed["total"] == 1 and listed["items"][0]["overdue"] is True and listed["items"][0]["days_overdue"] > 300
    summary = ok(client.get(f"{BASE}/invoices/summary", headers=MEMBER))
    figures = summary["currencies"][0]
    assert (figures["currency"], figures["outstanding"], figures["overdue"], figures["overdue_count"]) == ("SAR", 2000, 2000, 1) and summary["counts"]["sent"] == 1
    ok(client.post(f"{BASE}/invoices/{late_id}/payments", headers=MEMBER, json={"amount": 500, "paid_on": utc_now().date().isoformat()}), 201)
    after = ok(client.get(f"{BASE}/invoices/summary", headers=MEMBER))["currencies"][0]
    assert after["outstanding"] == 1500 and after["collected_this_month"] == 500


def test_the_list_filters_and_pages() -> None:
    project = make_project(total=2000, tax=0)
    set_profile()
    first = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1], customer_reference="PO-77"), 201)
    send(ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[1:2]), 201))
    everything = ok(client.get(f"{BASE}/invoices", headers=MEMBER))
    assert everything["total"] == 2 and {item["status"] for item in everything["items"]} == {"draft", "sent"}
    assert [item["id"] for item in ok(client.get(f"{BASE}/invoices?status=draft", headers=MEMBER))["items"]] == [first["id"]]
    assert ok(client.get(f"{BASE}/invoices?q=PO-77", headers=MEMBER))["total"] == 1
    assert ok(client.get(f"{BASE}/invoices?q=INV-", headers=MEMBER))["total"] == 1
    assert ok(client.get(f"{BASE}/invoices?limit=1&offset=1", headers=MEMBER))["items"].__len__() == 1
    assert ok(client.get(f"{BASE}/invoices?project_id={project['id']}", headers=MEMBER))["total"] == 2


# -- printable copy ---------------------------------------------------------------------------------------


def test_the_printable_copy_escapes_text_marks_drafts_and_follows_the_language() -> None:
    project = make_project(total=2000, tax=15)
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1], bill_to_name="Acme <script>alert(1)</script>", notes="Pay by <b>Friday</b>"), 201)
    page = client.get(f"{BASE}/invoices/{drafted['id']}/print", headers=MEMBER)
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in page.headers["content-security-policy"] and page.headers["x-content-type-options"] == "nosniff"
    html = page.text
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;Friday" in html and "Draft: not sent" in html and 'dir="ltr"' in html
    sent = send(drafted)
    html = client.get(f"{BASE}/invoices/{sent['id']}/print?lang=ar", headers=MEMBER).text
    assert 'dir="rtl"' in html and sent["number"] in html and "فاتورة ضريبية" in html and "Draft" not in html and "300000000000003" in html
    assert "1,150.00" in client.get(f"{BASE}/invoices/{sent['id']}/print", headers=MEMBER).text
    assert client.get(f"{BASE}/invoices/{sent['id']}/print?lang=xx", headers=MEMBER).status_code == 422
    # A later change to the profile does not rewrite the sent copy.
    set_profile(legal_name="Renamed Co")
    assert "Anum Technologies LLC" in client.get(f"{BASE}/invoices/{sent['id']}/print", headers=MEMBER).text


# -- permissions and isolation ---------------------------------------------------------------------------


def test_permissions_and_isolation() -> None:
    project = make_project(total=2000, tax=0)
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    assert create(VIEWER, project_id=project["id"], milestone_ids=milestone_ids(project)[1:]).status_code == 403
    assert client.get(f"{BASE}/invoices", headers=VIEWER).status_code == 403
    for headers in (OTHER_WORKSPACE, OTHER_TENANT):
        assert client.get(f"{BASE}/invoices/{drafted['id']}", headers=headers).status_code == 404
        assert ok(client.get(f"{BASE}/invoices", headers=headers))["total"] == 0
        assert client.post(f"{BASE}/invoices/{drafted['id']}/request-approval", headers=headers).status_code == 404
        assert client.get(f"{BASE}/invoices/{drafted['id']}/print", headers=headers).status_code == 404
        assert create(headers, project_id=project["id"], milestone_ids=milestone_ids(project)).status_code == 404
    assert ok(client.get(f"{BASE}/billing-profile", headers=OTHER_TENANT))["legal_name"] == ""
    assert ok(client.get(f"{BASE}/invoices/summary", headers=OTHER_TENANT))["currencies"] == []


def test_the_send_tool_refuses_a_changed_invoice() -> None:
    import asyncio

    from anum_api.agent_tools import ToolCall
    from anum_api.business.tools import send_invoice
    from anum_api.schemas import TenantContext

    project = make_project(total=2000, tax=0)
    set_profile()
    drafted = ok(create(project_id=project["id"], milestone_ids=milestone_ids(project)[:1]), 201)
    pending = request_send(drafted["id"])
    context = TenantContext(tenant_id="tenant_biz", workspace_id="workspace_biz", user_id="owner_biz", roles=["owner"])
    arguments = {"invoice_id": drafted["id"], "content_hash": pending["requested_hash"], "total": "1000.00", "currency": "SAR"}
    wrong_hash = asyncio.run(send_invoice(ToolCall(name="business.send_invoice", arguments={**arguments, "content_hash": "0" * 64}), context))
    assert wrong_hash.status == "failed" and "changed" in wrong_hash.summary
    wrong_total = asyncio.run(send_invoice(ToolCall(name="business.send_invoice", arguments={**arguments, "total": "999.00"}), context))
    assert wrong_total.status == "failed"
    missing = asyncio.run(send_invoice(ToolCall(name="business.send_invoice", arguments={**arguments, "invoice_id": "missing"}), context))
    assert missing.status == "failed" and "deleted" in missing.summary
    done = asyncio.run(send_invoice(ToolCall(name="business.send_invoice", arguments=arguments), context))
    assert done.status == "succeeded" and "Nothing was e-mailed" in done.summary
    again = asyncio.run(send_invoice(ToolCall(name="business.send_invoice", arguments=arguments), context))
    assert again.status == "succeeded" and "already sent" in again.summary
    other = asyncio.run(send_invoice(ToolCall(name="business.send_invoice", arguments={**arguments, "content_hash": "1" * 64}), context))
    assert other.status == "failed"


# -- audit walk -------------------------------------------------------------------------------------------


def test_every_state_changing_invoice_route_is_audited_without_content() -> None:
    from audit_probe import AuditProbe

    project = make_project(total=3000, tax=15)
    sent_text = ["Pay by Friday", "PO-AUDIT-1", "TRX-AUDIT-9", "billing@anum.example", "IBAN: SA00 0000 0000 0000 0000 0000", "Office 12, Riyadh"]
    probe = AuditProbe(client, "business_invoices")
    call = lambda method, path, **kwargs: probe.call(method, f"{BASE}{path}", OWNER, sent=sent_text, **kwargs)  # noqa: E731
    set_profile()
    first = call("POST", "/invoices", json={"project_id": project["id"], "milestone_ids": milestone_ids(project)[:1], "notes": "Pay by Friday", "customer_reference": "PO-AUDIT-1"}).json()
    call("PATCH", f"/invoices/{first['id']}", json={"due_in_days": 30})
    pending = call("POST", f"/invoices/{first['id']}/request-approval").json()
    call("POST", f"/invoices/{first['id']}/cancel-request")
    pending = call("POST", f"/invoices/{first['id']}/request-approval").json()
    assert approve(pending["approval"]).status_code == 200
    payment = call("POST", f"/invoices/{first['id']}/payments", json={"amount": 500, "paid_on": utc_now().date().isoformat(), "reference": "TRX-AUDIT-9"}).json()
    call("DELETE", f"/invoices/{first['id']}/payments/{payment['payments'][0]['id']}")
    call("POST", f"/invoices/{first['id']}/void", json={"reason": "Reissue"})
    spare = call("POST", "/invoices", json={"project_id": project["id"], "milestone_ids": milestone_ids(project)[:1]}).json()
    call("DELETE", f"/invoices/{spare['id']}")
    probe.assert_covered()
