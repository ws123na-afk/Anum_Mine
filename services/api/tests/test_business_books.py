"""Business books: expenses, payables, budgets, intercompany and the P&L (docs/business-books.md), in-memory stores.

Every bank message here is SYNTHETIC: written for these tests, labelled, and not the wording of any
real bank. Invoices are seeded straight into the Business store with synthetic customers.
``test_postgres_business_books.py`` repeats the storage rules under forced RLS.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.business.books_store import books_memory_store
from anum_api.business.invoice_models import CreditNote, Invoice, InvoiceLine, InvoicePayment
from anum_api.business.models import Account, LineKind, Project
from anum_api.business.store import business_store
from anum_api.dependencies import memory_repository
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.purchases.service import plug_finance_links as plug_purchases
from anum_api.purchases.store import purchase_memory_store
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.vehicles.service import plug_finance_links as plug_vehicles
from anum_api.vehicles.store import vehicle_memory_store

TENANT, PERSONAL, BUSINESS, OTHER = "tenant_books", "ws_books_personal", "ws_books_anum", "ws_books_tyre"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": BUSINESS, "x-user-id": "owner_books", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_books", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_books", "x-user-roles": "viewer"}
PERSONAL_OWNER = {**OWNER, "x-workspace-id": PERSONAL}
OTHER_OWNER = {**OWNER, "x-workspace-id": OTHER}
API = "/api/v1/business/books"
FIN = "/api/v1/finance"
SENDER = "TestBank"  # SYNTHETIC sender
FUEL_SMS = "SYNTHETIC. Purchase of SAR 150.00 at PETROMIN FUEL with card **1234"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    stores = (finance_store, sms_memory_store, links_memory_store, books_memory_store, business_store, purchase_memory_store, vehicle_memory_store)
    for store in stores:
        store.clear()
    svc._last_notified.clear()
    plug_purchases()
    plug_vehicles()
    memberships = dict(memory_repository.store.memberships)
    now = utc_now()
    for workspace, user, role in ((BUSINESS, "owner_books", "owner"), (BUSINESS, "member_books", "member"), (BUSINESS, "viewer_books", "viewer"),
                                  (PERSONAL, "owner_books", "owner"), (OTHER, "owner_books", "owner"), (OTHER, "member_books", "member")):
        memory_repository.store.memberships[(TENANT, workspace, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=workspace, user_id=user, role=role, active=True, created_at=now, updated_at=now)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    for store in stores:
        store.clear()


def today() -> date:
    return utc_now().date()


def setup_books(headers: dict = OWNER, **body: Any) -> dict:
    current = client.get(f"{API}/setup", headers=headers).json()["settings"]
    payload = {"base_currency": "SAR", "country": "SA", "fiscal_year_start_month": 1, "basis": "accrual", "tax_label": "VAT",
               "version": current["version"] if current else None, **body}
    response = client.put(f"{API}/settings", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def vendor(name: str = "Office Landlord", **body: Any) -> dict:
    response = client.post(f"{API}/vendors", headers=OWNER, json={"name": name, "default_category": "rent", **body})
    assert response.status_code == 201, response.text
    return response.json()


def bill(vendor_id: str, key: str | None = None, headers: dict = OWNER, **body: Any):  # type: ignore[no-untyped-def]
    payload = {"vendor_id": vendor_id, "description": "October rent", "bill_date": today().isoformat(), "currency": "SAR", "subtotal": "1000",
               "tax": "150", "category": "rent", **body}
    return client.post(f"{API}/bills", headers={**headers, **({"Idempotency-Key": key} if key else {})}, json=payload)


def report(headers: dict = OWNER, **params: Any) -> dict:
    response = client.get(f"{API}/report", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def line(statement: dict, section: str, key: str) -> str | None:
    return next((item["amount"] for item in statement[section]["lines"] if item["key"] == key), None)


# -- the business's own invoices (seeded: SYNTHETIC customers) ------------------------------------------


def ctx(workspace: str = BUSINESS) -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=workspace, user_id="owner_books", roles=["owner"])


def seed_invoice(*, subtotal: str = "1000", tax: str = "150", currency: str = "SAR", issued: date | None = None, project_id: str | None = None,
                 account_id: str = "acc_synthetic", status: str = "sent", due_in: int = 14) -> Invoice:
    now = utc_now()
    issued = issued or today()
    total = Decimal(subtotal) + Decimal(tax)
    invoice = Invoice(id=f"inv_{len(business_store._rows['invoices'])}", tenant_id=TENANT, workspace_id=BUSINESS, created_at=now, updated_at=now,
                      account_id=account_id, project_id=project_id, status=status, currency=currency,
                      lines=[InvoiceLine(description="Synthetic service", kind=LineKind.SERVICE, quantity=Decimal(1), unit_price=Decimal(subtotal),
                                         net=Decimal(subtotal), tax=Decimal(tax), total=total)],
                      subtotal=Decimal(subtotal), tax=Decimal(tax), total=total, number=f"INV-2026-{len(business_store._rows['invoices']) + 1:04d}",
                      issue_date=issued, due_date=issued + timedelta(days=due_in), created_by="owner_books")
    business_store.scoped(ctx()).add("invoices", invoice)
    return invoice


def seed_payment(invoice: Invoice, amount: str, paid_on: date | None = None) -> None:
    now = utc_now()
    business_store.scoped(ctx()).add("invoice_payments", InvoicePayment(
        id=f"ipay_{len(business_store._rows['invoice_payments'])}", tenant_id=TENANT, workspace_id=BUSINESS, created_at=now, updated_at=now,
        invoice_id=invoice.id, amount=Decimal(amount), paid_on=paid_on or today(), recorded_by="owner_books"))


def seed_credit(invoice: Invoice, subtotal: str, tax: str) -> None:
    now = utc_now()
    business_store.scoped(ctx()).add("credit_notes", CreditNote(
        id=f"cn_{len(business_store._rows['credit_notes'])}", tenant_id=TENANT, workspace_id=BUSINESS, created_at=now, updated_at=now,
        invoice_id=invoice.id, account_id=invoice.account_id, seq=1, number=f"{invoice.number}-CN1", currency=invoice.currency,
        amount=Decimal(subtotal) + Decimal(tax), subtotal=Decimal(subtotal), tax=Decimal(tax), reason="Synthetic correction", issue_date=today(),
        issued_by="owner_books"))


def seed_project(name: str = "Synthetic fit-out") -> Project:
    now = utc_now()
    scoped = business_store.scoped(ctx())
    scoped.add("accounts", Account(id="acc_synthetic", tenant_id=TENANT, workspace_id=BUSINESS, created_at=now, updated_at=now, name="Synthetic Co",
                                   name_key="synthetic co", owner_id="owner_books", created_by="owner_books"))
    project = Project(id="prj_synthetic", tenant_id=TENANT, workspace_id=BUSINESS, created_at=now, updated_at=now, proposal_id="prop_missing",
                      proposal_version=1, opportunity_id="opp_synthetic", account_id="acc_synthetic", name=name, owner_id="owner_books",
                      created_by="owner_books")
    scoped.add("projects", project)
    return project


# -- the personal ledger: one SAR 150 fuel payment from Apple Pay and the bank SMS ---------------------------


def fuel_payment() -> tuple[str, dict]:
    card = client.post(f"{FIN}/accounts", headers=PERSONAL_OWNER, json={"name": "Card", "kind": "card", "currency": "SAR", "last4": "1234"}).json()
    assert client.post(f"{FIN}/sms/self-test", headers=PERSONAL_OWNER).json()["passed"]
    version = client.get(f"{FIN}/sms/settings", headers=PERSONAL_OWNER).json()["version"]
    put = client.put(f"{FIN}/sms/settings", headers=PERSONAL_OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "1234", "account_id": card["id"]}], "wallet_cards": [{"card": "My Visa", "account_id": card["id"]}]})
    assert put.status_code == 200, put.text
    token = client.post(f"{FIN}/sms/devices", headers=PERSONAL_OWNER, json={"name": "iPhone"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    paid = client.post(f"{FIN}/wallet/ingest", headers=bearer, json={"source": "apple_wallet", "amount": "SAR 150.00", "merchant": "Petromin Fuel",
                                                                     "card": "My Visa", "name": "Purchase"})
    assert paid.json()["status"] == "posted", paid.text
    sms = client.post(f"{FIN}/sms/ingest", headers=bearer, json={"message": FUEL_SMS, "sender": SENDER})
    assert sms.json()["status"] == "attached", sms.text
    entries = list(sms_memory_store.entries.values())
    assert len(entries) == 1
    return entries[0].canonical_id or entries[0].id, card


def journal() -> tuple[dict, int]:
    return ({e.id: (e.status, sorted((p.ledger_account, p.amount, p.currency) for p in e.postings)) for e in sms_memory_store.entries.values()},
            len(finance_store.transactions))


def allocate(canonical: str, amount: str = "150", category: str = "fuel", treatment: str = "owner_contribution", project_id: str | None = None) -> dict:
    response = client.post(f"{FIN}/allocations", headers=PERSONAL_OWNER, json={
        "canonical_id": canonical, "workspace_id": BUSINESS, "amount": amount, "currency": "SAR", "category": category, "treatment": treatment,
        **({"project_id": project_id} if project_id else {})})
    assert response.status_code in (200, 201), response.text
    return response.json()


def allocation_id() -> str:
    rows = client.get(f"{API}/allocations", headers=OWNER).json()["items"]
    assert len(rows) == 1
    return rows[0]["id"]


# -- permissions and setup ------------------------------------------------------------------------------------


def test_viewers_are_refused_members_cannot_change_settings_and_reports_wait_for_setup() -> None:
    denied = client.get(f"{API}/setup", headers=VIEWER)
    assert denied.status_code == 403 and denied.json()["code"] == "forbidden" and "owner" in denied.json()["detail"]
    first = client.get(f"{API}/setup", headers=MEMBER).json()
    assert first["settings"] is None and first["permissions"] == {"read": True, "write": True, "manage": False, "accountant": False,
                                                                                 "account": False}
    assert client.get(f"{API}/report", headers=MEMBER).json()["code"] == "books_not_set_up"
    refused = client.put(f"{API}/settings", headers=MEMBER, json={"base_currency": "SAR", "version": None})
    assert refused.status_code == 403
    assert client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "S1R", "version": None}).json()["code"] == "invalid_currency"
    saved = setup_books()
    assert saved["base_currency"] == "SAR" and saved["version"] == 1
    stale = client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "AED", "version": None})
    assert stale.status_code == 409 and stale.json()["code"] == "stale"
    bad = client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "SAR", "version": 1, "category_sections": {"Bad Cat": "operating_expense"}})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_category"
    for path in ("/vendors", "/bills", "/allocations", "/budgets", "/rates", "/adjustments", "/intercompany", "/cost-centres"):
        assert client.get(f"{API}{path}", headers=VIEWER).status_code == 403, path
        assert client.get(f"{API}{path}", headers=MEMBER).status_code == 200, path
    assert client.post(f"{API}/rates", headers=MEMBER, json={"base": "USD", "quote": "SAR", "rate": "3.75", "as_of": today().isoformat(),
                                                             "source": "SAMA"}).status_code == 403
    assert client.get(f"{API}/intercompany/counterparties", headers=MEMBER).status_code == 403


def test_vendors_cost_centres_search_duplicates_and_archive() -> None:
    setup_books()
    landlord = vendor()
    assert client.post(f"{API}/vendors", headers=MEMBER, json={"name": "office landlord"}).json()["code"] == "duplicate"
    vendor("Fuel Station", default_category="fuel", country="sa")
    found = client.get(f"{API}/vendors", headers=MEMBER, params={"q": "fuel"}).json()
    assert found["total"] == 1 and found["items"][0]["country"] == "SA" and "name_key" not in found["items"][0]
    archived = client.patch(f"{API}/vendors/{landlord['id']}", headers=MEMBER, json={"name": "Office Landlord", "archived": True, "version": 1})
    assert archived.status_code == 200 and archived.json()["archived"]
    assert client.patch(f"{API}/vendors/{landlord['id']}", headers=MEMBER, json={"name": "X Y", "version": 1}).json()["code"] == "stale"
    assert bill(landlord["id"]).json()["code"] == "vendor_archived"
    centre = client.post(f"{API}/cost-centres", headers=MEMBER, json={"code": "OPS", "name": "Operations"})
    assert centre.status_code == 201
    assert client.post(f"{API}/cost-centres", headers=MEMBER, json={"code": "ops", "name": "Again"}).json()["code"] == "duplicate"
    assert client.get(f"{API}/setup", headers=MEMBER).json()["cost_centres"][0]["code"] == "OPS"


# -- bills and payables ---------------------------------------------------------------------------------------------


def test_bills_are_idempotent_validated_and_keep_tax_apart() -> None:
    setup_books()
    landlord = vendor()
    first = bill(landlord["id"], key="bill-key-0001")
    assert first.status_code == 201
    created = first.json()
    assert (created["subtotal"], created["tax"], created["total"], created["payment_state"], created["balance"]) == ("1000.00", "150.00", "1150.00", "unpaid", "1150.00")
    again = bill(landlord["id"], key="bill-key-0001")
    assert again.status_code == 200 and again.json()["replayed"] and again.json()["id"] == created["id"]
    assert bill(landlord["id"], key="bill-key-0001", subtotal="900").json()["code"] == "idempotency_mismatch"
    assert bill(landlord["id"], due_date=(today() - timedelta(days=1)).isoformat()).json()["code"] == "invalid_due_date"
    assert bill(landlord["id"], useful_life_months=36).json()["code"] == "life_needs_capital"
    assert bill(landlord["id"], subtotal="0", tax="0").json()["code"] == "invalid_amount"
    assert bill(landlord["id"], subtotal="12.345").json()["subtotal"] == "12.34"  # rounded once, half to even
    assert bill(landlord["id"], project_id="prj_nowhere").json()["code"] == "project_not_found"
    assert bill("bven_missing").json()["code"] == "vendor_not_found"
    listed = client.get(f"{API}/bills", headers=MEMBER, params={"q": "rent", "limit": 1})
    assert listed.json()["total"] == 2 and len(listed.json()["items"]) == 1
    assert listed.json()["summary"]["open"] == [{"currency": "SAR", "amount": "1312.34"}]


def test_recorded_payments_settle_a_bill_and_void_needs_an_owner() -> None:
    setup_books()
    created = bill(vendor()["id"]).json()
    path = f"{API}/bills/{created['id']}/payments"
    over = client.post(path, headers=MEMBER, json={"amount": "2000", "method": "bank_transfer"})
    assert over.status_code == 409 and over.json()["code"] == "over_balance" and over.json()["remaining"] == "1150.00"
    future = client.post(path, headers=MEMBER, json={"amount": "100", "paid_on": (today() + timedelta(days=2)).isoformat()})
    assert future.json()["code"] == "date_in_future"
    part = client.post(path, headers={**MEMBER, "Idempotency-Key": "pay-key-0001"}, json={"amount": "150", "method": "cash"})
    assert part.status_code == 201 and part.json()["payment_state"] == "partly_paid" and part.json()["balance"] == "1000.00"
    replay = client.post(path, headers={**MEMBER, "Idempotency-Key": "pay-key-0001"}, json={"amount": "150", "method": "cash"})
    assert replay.status_code == 200 and replay.json()["replayed"] and len(replay.json()["payments"]) == 1
    assert client.post(path, headers={**MEMBER, "Idempotency-Key": "pay-key-0001"}, json={"amount": "151"}).json()["code"] == "idempotency_mismatch"
    # A bill with a payment keeps its money; describing it may still change.
    edit = {"vendor_id": created["vendor_id"], "description": "Rent, October", "bill_date": created["bill_date"], "currency": "SAR", "subtotal": "1000",
            "tax": "150", "category": "rent", "version": part.json()["version"]}
    renamed = client.patch(f"{API}/bills/{created['id']}", headers=MEMBER, json=edit)
    assert renamed.status_code == 200 and renamed.json()["description"] == "Rent, October"
    money = client.patch(f"{API}/bills/{created['id']}", headers=MEMBER, json={**edit, "subtotal": "900", "version": renamed.json()["version"]})
    assert money.json()["code"] == "has_payments"
    assert client.post(f"{API}/bills/{created['id']}/void", headers=OWNER, json={"reason": "duplicate bill"}).json()["code"] == "has_payments"
    paid = client.post(path, headers=MEMBER, json={"amount": "1000", "method": "bank_transfer", "reference": "=HYPERLINK(1)"})
    assert paid.json()["payment_state"] == "paid" and not paid.json()["can"]["pay"]
    payments = paid.json()["payments"]
    assert client.post(f"{path}/{payments[0]['id']}/void", headers=MEMBER, json={"reason": "typed twice"}).status_code == 403
    undone = client.post(f"{path}/{payments[0]['id']}/void", headers=OWNER, json={"reason": "typed twice"})
    assert undone.json()["payment_state"] == "partly_paid" and undone.json()["payments"][0]["counts"] is False
    exported = client.get(f"{API}/bills.csv", headers=MEMBER)
    assert exported.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert rows[0][0] == "bill_id" and rows[1][2] == "" and rows[1][12] == "partly_paid"
    other = bill(created["vendor_id"]).json()
    assert client.post(f"{API}/bills/{other['id']}/void", headers=MEMBER, json={"reason": "not ours"}).status_code == 403
    voided = client.post(f"{API}/bills/{other['id']}/void", headers=OWNER, json={"reason": "not ours"})
    assert voided.json()["status"] == "void" and voided.json()["payment_state"] == "void"
    assert client.get(f"{API}/bills", headers=MEMBER, params={"status": "void"}).json()["total"] == 1


# -- the acceptance cases: one payment, counted once ----------------------------------------------------------------


def test_a_fuel_payment_that_is_a_vehicle_cost_a_purchase_and_a_business_allocation_counts_once() -> None:
    setup_books(category_sections={"fuel": "cost_of_sales"})
    canonical, _card = fuel_payment()
    before = journal()
    # The same canonical transaction is linked to a vehicle and a purchase in the personal workspace ...
    car = client.post("/api/v1/vehicles", headers=PERSONAL_OWNER, json={"registration": "ABC 1234", "country": "SA", "fuel_type": "petrol_91",
                                                                       "odometer_km": "10000"}).json()
    fuel = client.post(f"/api/v1/vehicles/{car['id']}/fuel", headers=PERSONAL_OWNER, json={"filled_at": utc_now().isoformat(), "litres": "40",
                                                                                         "total": "150", "odometer_km": "10400"}).json()
    assert client.post(f"/api/v1/vehicles/costs/fuel_entry/{fuel['id']}/payment", headers=PERSONAL_OWNER,
                       json={"canonical_id": canonical}).status_code == 201
    order = client.post("/api/v1/purchases", headers=PERSONAL_OWNER, json={"merchant": "Petromin", "ordered_on": today().isoformat(), "currency": "SAR",
                                                                          "total": "150", "items": [{"description": "Fuel", "quantity": "1", "unit_price": "150"}]}).json()
    assert client.post(f"/api/v1/purchases/{order['id']}/payment", headers=PERSONAL_OWNER, json={"canonical_id": canonical}).status_code == 201
    # ... and allocated to the business: one more view of the one posting.
    allocate(canonical)
    assert journal() == before
    statement = report()["pnl"]
    assert statement["cost_of_sales"]["total"] == "150.00" and line(statement, "cost_of_sales", "fuel") == "150.00"
    assert statement["operating_expenses"]["total"] == "0.00" and statement["net_profit"] == "-150.00"
    # The same payment paying a vendor bill is the bill's payment, never a second cost.
    station = vendor("Petromin", default_category="fuel", default_section="cost_of_sales")
    fuel_bill = bill(station["id"], subtotal="150", tax="0", category="fuel").json()
    paid = client.post(f"{API}/bills/{fuel_bill['id']}/payments", headers=MEMBER, json={"kind": "allocation", "allocation_id": allocation_id()})
    assert paid.status_code == 201 and paid.json()["payment_state"] == "paid", paid.text
    posted_on = client.get(f"{API}/allocations", headers=OWNER).json()["items"][0]["posted_on"]
    assert paid.json()["payments"][0]["paid_on"] == posted_on and paid.json()["payments"][0]["reference"].startswith("#")
    for basis in ("accrual", "cash"):
        assert report(basis=basis)["pnl"]["cost_of_sales"]["total"] == "150.00", basis
    used = client.post(f"{API}/bills/{bill(station['id'], subtotal='150', tax='0').json()['id']}/payments", headers=MEMBER,
                       json={"kind": "allocation", "allocation_id": allocation_id()})
    assert used.status_code == 409 and used.json()["code"] == "allocation_used"
    flow = report()["cash_flow"]
    assert flow["paid_by_members"] == {"owner_contribution": [{"currency": "SAR", "amount": "150.00"}]}
    assert "suppliers_paid" not in flow["lines"]  # no business cash moved: the owner paid from their own card
    assert report()["equity"]["contributions"] == [{"currency": "SAR", "amount": "150.00"}]
    assert journal() == before


def test_undoing_the_payment_in_finance_takes_it_out_of_the_books() -> None:
    setup_books()
    canonical, _card = fuel_payment()
    allocate(canonical, treatment="reimbursable")
    station = vendor("Petromin", default_category="fuel")
    fuel_bill = bill(station["id"], subtotal="150", tax="0").json()
    assert client.post(f"{API}/bills/{fuel_bill['id']}/payments", headers=MEMBER, json={"kind": "allocation", "allocation_id": allocation_id()}).status_code == 201
    assert report()["balances"]["owed_to_members"] == [{"currency": "SAR", "amount": "150.00"}]
    assert report(basis="cash")["pnl"]["operating_expenses"]["total"] == "150.00"
    message_id = client.get(f"{FIN}/sms/messages", headers=PERSONAL_OWNER, params={"status": "posted"}).json()["items"][0]["id"]
    assert client.post(f"{FIN}/sms/messages/{message_id}/reverse", headers=PERSONAL_OWNER).status_code == 200
    detail = client.get(f"{API}/bills/{fuel_bill['id']}", headers=MEMBER).json()
    assert detail["payment_state"] == "unpaid" and detail["payments"][0]["allocation_removed"] is True
    after = report(basis="cash")
    assert after["pnl"]["operating_expenses"]["total"] == "0.00" and after["balances"]["owed_to_members"] == []
    assert report()["pnl"]["operating_expenses"]["total"] == "150.00"  # the bill itself is still owed (accrual)


def test_invoices_are_the_revenue_accrual_and_cash_kept_apart_and_tax_separate() -> None:
    setup_books()
    invoice = seed_invoice()
    seed_payment(invoice, "500")
    seed_credit(invoice, "100", "15")
    seed_invoice(status="void")
    seed_invoice(status="draft")
    accrual = report()
    assert accrual["pnl"]["revenue"]["total"] == "900.00" and line(accrual["pnl"], "revenue", "credit_notes") == "-100.00"
    assert accrual["tax"]["by_currency"] == [{"currency": "SAR", "output": "135.00", "input": "0.00", "net": "135.00"}]
    assert accrual["balances"]["receivables"][0]["total"] == "535.00"
    assert accrual["balances"]["outstanding_invoices"][0]["balance"] == "535.00"
    cash = report(basis="cash")
    assert cash["pnl"]["revenue"]["total"] == "434.78" and cash["tax"]["by_currency"][0]["output"] == "65.22"
    # A cash surplus is not profit: a loan is cash in, never revenue.
    loan = client.post(f"{API}/adjustments", headers=OWNER, json={"kind": "loan_received", "entry_date": today().isoformat(), "currency": "SAR",
                                                                "amount": "10000", "description": "Synthetic bank loan"})
    assert loan.status_code == 201, loan.text
    after = report(basis="cash")
    assert after["pnl"]["net_profit"] == cash["pnl"]["net_profit"]
    assert after["cash_flow"]["lines"]["loans_received"] == [{"currency": "SAR", "amount": "10000.00"}]
    assert after["cash_flow"]["net"] == [{"currency": "SAR", "amount": "10500.00"}]
    assert after["balances"]["debt"] == [{"currency": "SAR", "amount": "10000.00"}]


def test_other_currencies_convert_only_with_a_dated_rate_and_its_source() -> None:
    setup_books(rate_max_age_days=10)
    supplier = vendor("Cloud Host", default_category="hosting")
    bill(supplier["id"], currency="USD", subtotal="100", tax="0", category="hosting", bill_date=(today() - timedelta(days=3)).isoformat())
    first = report()
    assert first["pnl"]["operating_expenses"]["total"] == "0.00" and not first["pnl"]["complete"]
    assert first["pnl"]["not_converted"] == [{"currency": "USD", "groups": {"operating_expense": "100.00"}}] and "not_converted" in first["notes"]
    # A rate dated after the bill is not used for it; one on or before it is, and it is listed.
    later = client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": "3.80", "as_of": today().isoformat(), "source": "Synthetic later"})
    assert later.status_code == 201
    assert not report()["pnl"]["complete"]
    assert client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": "3.75", "as_of": (today() - timedelta(days=5)).isoformat(),
                                                            "source": "Synthetic SAMA reference"}).status_code == 201
    converted = report()
    assert converted["pnl"]["operating_expenses"]["total"] == "375.00" and converted["pnl"]["complete"]
    assert converted["conversions"] == [{"from_currency": "USD", "to_currency": "SAR", "rate": "3.75", "as_of": (today() - timedelta(days=5)).isoformat(),
                                         "source": "Synthetic SAMA reference", "via": None, "figures": 1}]
    assert client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": "3.75", "as_of": (today() - timedelta(days=5)).isoformat(),
                                                            "source": "again"}).json()["code"] == "duplicate"
    assert client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": "-1", "as_of": today().isoformat(),
                                                            "source": "bad"}).json()["code"] == "invalid_rate"
    assert client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "USD", "rate": "1", "as_of": today().isoformat(),
                                                            "source": "same"}).json()["code"] == "same_currency"
    # The payables stay in the bill's own currency.
    assert converted["balances"]["payables"][0]["currency"] == "USD"


def test_adjustments_spread_depreciate_reverse_and_only_void() -> None:
    setup_books()
    start = today().replace(day=1)
    year_start = date(today().year, 1, 1)
    body = {"entry_date": start.isoformat(), "currency": "SAR", "description": "Synthetic"}
    capital = client.post(f"{API}/adjustments", headers=OWNER, json={**body, "kind": "capital_item", "amount": "1200", "start_month": start.isoformat(), "months": 12})
    assert capital.status_code == 201
    assert client.post(f"{API}/adjustments", headers=MEMBER, json={**body, "kind": "accrual", "amount": "10"}).status_code == 403
    assert client.post(f"{API}/adjustments", headers=OWNER, json={**body, "kind": "prepayment", "amount": "10"}).json()["code"] == "spread_required"
    assert client.post(f"{API}/adjustments", headers=OWNER, json={**body, "kind": "loan_interest", "amount": "10",
                                                                 "reverse_on": today().isoformat()}).json()["code"] == "invalid_reverse_on"
    statement = report(**{"from": year_start.isoformat(), "to": start.isoformat()})["pnl"]
    assert line(statement, "operating_expenses", "depreciation") == "100.00"
    accrual = client.post(f"{API}/adjustments", headers={**OWNER, "Idempotency-Key": "adj-key-0001"},
                          json={**body, "kind": "accrual", "amount": "300", "category": "utilities", "reverse_on": (start + timedelta(days=40)).isoformat()})
    assert accrual.status_code == 201
    replay = client.post(f"{API}/adjustments", headers={**OWNER, "Idempotency-Key": "adj-key-0001"},
                         json={**body, "kind": "accrual", "amount": "300", "category": "utilities", "reverse_on": (start + timedelta(days=40)).isoformat()})
    assert replay.status_code == 200 and replay.json()["replayed"]
    month = report(**{"from": start.isoformat(), "to": start.isoformat()})
    assert line(month["pnl"], "operating_expenses", "utilities") == "300.00"
    assert line(report(basis="cash", **{"from": start.isoformat(), "to": start.isoformat()})["pnl"], "operating_expenses", "utilities") is None
    both = report(**{"from": start.isoformat(), "to": (start + timedelta(days=45)).isoformat()})
    assert line(both["pnl"], "operating_expenses", "utilities") is None  # booked and reversed inside the period
    voided = client.post(f"{API}/adjustments/{accrual.json()['id']}/void", headers=OWNER, json={"reason": "estimate was wrong"})
    assert voided.json()["status"] == "void" and voided.json()["void_reason"] == "estimate was wrong"
    assert client.post(f"{API}/adjustments/{accrual.json()['id']}/void", headers=OWNER, json={"reason": "again"}).json()["code"] == "already_void"
    assert client.get(f"{API}/adjustments", headers=MEMBER).json()["total"] == 1
    assert client.get(f"{API}/adjustments", headers=MEMBER, params={"include_void": True}).json()["total"] == 2
    drawing = client.post(f"{API}/adjustments", headers=OWNER, json={**body, "kind": "owner_drawing", "amount": "50",
                                                                    "entry_date": (today() + timedelta(days=3)).isoformat()})
    assert drawing.json()["code"] == "date_in_future"


def test_intercompany_entries_are_mirrored_and_need_an_owner_of_both_businesses() -> None:
    setup_books()
    setup_books(OTHER_OWNER)
    parties = client.get(f"{API}/intercompany/counterparties", headers=OWNER).json()["items"]
    assert [p["workspace_id"] for p in parties] == sorted([PERSONAL, OTHER])
    body = {"workspace_id": OTHER, "kind": "recharge", "direction": "receivable", "entry_date": today().isoformat(), "currency": "SAR",
            "amount": "2000", "description": "Synthetic shared office recharge"}
    entry = client.post(f"{API}/intercompany", headers=OWNER, json=body)
    assert entry.status_code == 201, entry.text
    mirror = client.get(f"{API}/intercompany", headers=OTHER_OWNER).json()["items"]
    assert len(mirror) == 1 and mirror[0]["direction"] == "payable" and mirror[0]["pair_id"] == entry.json()["pair_id"]
    assert line(report()["pnl"], "other_income", "intercompany") == "2000.00"
    assert line(report(OTHER_OWNER)["pnl"], "operating_expenses", "intercompany") == "2000.00"
    assert report()["balances"]["intercompany"][0]["amount"] == "2000.00"
    assert report(OTHER_OWNER)["balances"]["intercompany"][0]["amount"] == "-2000.00"
    # A member of this business is no owner of the other; a workspace in another tenant or unknown is refused the same way.
    member_other = {**MEMBER, "x-user-roles": "owner"}
    refused = client.post(f"{API}/intercompany", headers=member_other, json=body)
    assert refused.status_code == 403 and refused.json()["code"] == "workspace_not_allowed"
    assert client.post(f"{API}/intercompany", headers=OWNER, json={**body, "workspace_id": "ws_unknown"}).status_code == 403
    assert client.post(f"{API}/intercompany", headers=OWNER, json={**body, "workspace_id": BUSINESS}).status_code == 403
    voided = client.post(f"{API}/intercompany/{entry.json()['id']}/void", headers=OWNER, json={"reason": "wrong month"})
    assert voided.json()["status"] == "void"
    assert client.get(f"{API}/intercompany", headers=OTHER_OWNER).json()["total"] == 0
    assert report(OTHER_OWNER)["balances"]["intercompany"] == []


def test_budgets_tags_cost_centres_projects_and_recurring_costs() -> None:
    setup_books()
    project = seed_project()
    centre = client.post(f"{API}/cost-centres", headers=MEMBER, json={"code": "OPS", "name": "Operations"}).json()
    landlord = vendor()
    bill(landlord["id"], recurrence="monthly", cost_centre_id=centre["id"], project_id=project.id)
    seed_invoice(subtotal="3000", tax="0", project_id=project.id)
    canonical, _card = fuel_payment()
    allocate(canonical, project_id=project.id)
    budget = client.post(f"{API}/budgets", headers=MEMBER, json={"name": "Rent", "period_start": today().replace(day=1).isoformat(), "months": 1,
                                                                 "amount": "1200", "category": "rent"})
    assert budget.status_code == 201 and budget.json()["currency"] == "SAR"
    data = report()
    row = next(b for b in data["budgets"] if b["id"] == budget.json()["id"])
    assert (row["actual"], row["variance"], row["used_pct"]) == ("1000.00", "200.00", "83.3")
    assert data["cost_centres"][0]["name"] == "OPS Operations" and data["cost_centres"][0]["costs"] == "1000.00"
    assert data["projects"][0] == {"id": project.id, "name": project.name, "revenue": "3000.00", "costs": "1150.00", "margin": "1850.00", "margin_pct": "61.7",
                                   "complete": True, "not_converted": []}
    assert data["customers"][0]["name"] == "Synthetic Co"
    assert data["recurring"]["per_month"] == [{"currency": "SAR", "amount": "1000.00"}]
    # Leaving the allocation out of the books removes it from the P&L only.
    rows = client.get(f"{API}/allocations", headers=MEMBER, params={"status": "untagged"}).json()["items"]
    tagged = client.put(f"{API}/allocations/{rows[0]['id']}/tag", headers=MEMBER, json={"section": "excluded", "note": "deposit held"})
    assert tagged.status_code == 200
    assert report()["projects"][0]["costs"] == "1000.00"
    assert client.put(f"{API}/allocations/{rows[0]['id']}/tag", headers=MEMBER, json={"section": "capital"}).json()["code"] == "stale"
    assert client.put(f"{API}/allocations/alloc_missing/tag", headers=MEMBER, json={}).json()["code"] == "allocation_not_found"
    projects = client.get(f"{API}/projects", headers=MEMBER).json()
    assert projects["items"][0]["books"]["revenue"] == "3000.00" and projects["items"][0]["contract"] is None
    trend = data["trend"]
    assert len(trend) == 12 and trend[-1]["revenue"] == "3000.00"
    csv_rows = list(csv.reader(io.StringIO(client.get(f"{API}/report.csv", headers=MEMBER).text)))
    assert ["net_profit", "", "SAR", report()["pnl"]["net_profit"]] in csv_rows
    assert client.delete(f"{API}/budgets/{budget.json()['id']}", headers=MEMBER).status_code == 204


def test_another_business_or_tenant_cannot_read_the_books() -> None:
    setup_books()
    created = bill(vendor()["id"]).json()
    assert client.get(f"{API}/bills/{created['id']}", headers=OTHER_OWNER).status_code == 404
    assert client.get(f"{API}/bills", headers=OTHER_OWNER).json()["total"] == 0
    stranger = {**OWNER, "x-tenant-id": "tenant_other"}
    assert client.get(f"{API}/bills/{created['id']}", headers=stranger).status_code in (403, 404)
    assert client.post(f"{API}/bills/{created['id']}/payments", headers=OTHER_OWNER, json={"amount": "1"}).status_code == 404


def test_report_period_rules() -> None:
    setup_books(fiscal_year_start_month=4)
    data = report()
    first = date(today().year if today().month >= 4 else today().year - 1, 4, 1)
    last = date(first.year + 1, 3, 31)
    assert data["period"] == {"from": first.isoformat(), "to": last.isoformat(), "basis": "accrual"}
    assert data["balances"]["aged_on"] == today().isoformat()
    assert client.get(f"{API}/report", headers=OWNER, params={"from": "2026-05-01", "to": "2026-04-01"}).json()["code"] == "invalid_period"
    assert client.get(f"{API}/report", headers=OWNER, params={"from": "2020-01-01", "to": "2026-04-01"}).json()["code"] == "period_too_long"


# -- review fixes: an allocation waiting for review still holds its bill -------------------------------------


def _set_allocation_status(status: str) -> None:
    for row in links_memory_store.allocations.values():
        row.status = status


def test_an_allocation_in_review_still_holds_its_bill_against_void_overpayment_and_money_edits() -> None:
    setup_books()
    canonical, _card = fuel_payment()
    allocate(canonical)
    station = vendor("Petromin", default_category="fuel")
    fuel_bill = bill(station["id"], subtotal="150", tax="0").json()
    assert client.post(f"{API}/bills/{fuel_bill['id']}/payments", headers=MEMBER, json={"kind": "allocation", "allocation_id": allocation_id()}).status_code == 201
    _set_allocation_status("needs_review")
    assert client.post(f"{API}/bills/{fuel_bill['id']}/void", headers=OWNER, json={"reason": "duplicate"}).json()["code"] == "has_payments"
    over = client.post(f"{API}/bills/{fuel_bill['id']}/payments", headers=MEMBER, json={"amount": "150", "method": "cash"})
    assert over.status_code == 409 and over.json()["code"] in ("bill_paid", "over_balance")
    current = client.get(f"{API}/bills/{fuel_bill['id']}", headers=MEMBER).json()
    edit = {"vendor_id": station["id"], "description": "Fuel", "bill_date": current["bill_date"], "currency": "SAR", "subtotal": "100", "tax": "0",
            "category": "fuel", "version": current["version"]}
    assert client.patch(f"{API}/bills/{fuel_bill['id']}", headers=MEMBER, json=edit).json()["code"] == "has_payments"
    _set_allocation_status("active")
    assert report()["pnl"]["operating_expenses"]["total"] == "150.00"


def test_a_record_keeps_its_archived_cost_centre_and_a_tag_race_names_the_tag() -> None:
    setup_books()
    centre = client.post(f"{API}/cost-centres", headers=MEMBER, json={"code": "OLD", "name": "Old site"}).json()
    created = bill(vendor()["id"], cost_centre_id=centre["id"]).json()
    assert client.patch(f"{API}/cost-centres/{centre['id']}", headers=MEMBER, json={"archived": True, "version": 1}).status_code == 200
    edit = {"vendor_id": created["vendor_id"], "description": "Renamed", "bill_date": created["bill_date"], "currency": "SAR", "subtotal": "1000",
            "tax": "150", "category": "rent", "cost_centre_id": centre["id"], "version": created["version"]}
    assert client.patch(f"{API}/bills/{created['id']}", headers=MEMBER, json=edit).status_code == 200
    assert bill(created["vendor_id"], cost_centre_id=centre["id"]).json()["code"] == "cost_centre_archived"
    from anum_api.business.api_books import _duplicate

    assert "classified" in _duplicate("allocation_tags: allocation")
    assert "pays another bill" in _duplicate("uq_business_book_bill_payments_allocation")


def test_every_state_changing_books_route_is_audited_without_amounts_or_text() -> None:
    from audit_probe import AuditProbe

    setup_books(OTHER_OWNER)
    probe = AuditProbe(client, "business_books")
    secret = "Synthetic secret memo 9931"
    call = lambda method, path, headers=OWNER, **kwargs: probe.call(method, f"{API}{path}", headers, sent=[secret], **kwargs)  # noqa: E731
    call("PUT", "/settings", json={"base_currency": "SAR", "version": None, "tax_label": "VAT"})
    centre = call("POST", "/cost-centres", json={"code": "OPS", "name": "Operations"}).json()
    call("PATCH", f"/cost-centres/{centre['id']}", json={"name": "Operations team", "version": 1})
    seller = call("POST", "/vendors", json={"name": "Synthetic Supplier", "notes": secret}).json()
    call("PATCH", f"/vendors/{seller['id']}", json={"name": "Synthetic Supplier", "notes": secret, "version": 1})
    made = call("POST", "/bills", json={"vendor_id": seller["id"], "description": secret, "bill_date": today().isoformat(), "currency": "SAR",
                                        "subtotal": "100", "tax": "15", "category": "rent"}).json()
    call("PATCH", f"/bills/{made['id']}", json={"vendor_id": seller["id"], "description": secret, "bill_date": today().isoformat(), "currency": "SAR",
                                                "subtotal": "100", "tax": "15", "category": "rent", "version": made["version"]})
    paid = call("POST", f"/bills/{made['id']}/payments", json={"amount": "50", "method": "cash", "note": secret}).json()
    call("POST", f"/bills/{made['id']}/payments/{paid['payment_id']}/void", json={"reason": secret})
    call("POST", f"/bills/{made['id']}/void", json={"reason": secret})
    canonical, _card = fuel_payment()
    allocate(canonical)
    call("PUT", f"/allocations/{allocation_id()}/tag", json={"section": "excluded", "note": secret})
    budget = call("POST", "/budgets", json={"name": "Rent", "period_start": today().replace(day=1).isoformat(), "amount": "500"}).json()
    call("PATCH", f"/budgets/{budget['id']}", json={"name": "Rent", "period_start": today().replace(day=1).isoformat(), "amount": "600", "version": 1})
    call("DELETE", f"/budgets/{budget['id']}")
    rate = call("POST", "/rates", json={"base": "USD", "quote": "SAR", "rate": "3.75", "as_of": today().isoformat(), "source": "Synthetic source"}).json()
    call("DELETE", f"/rates/{rate['id']}")
    adjustment = call("POST", "/adjustments", json={"kind": "accrual", "entry_date": today().isoformat(), "currency": "SAR", "amount": "10", "description": secret}).json()
    call("POST", f"/adjustments/{adjustment['id']}/void", json={"reason": secret})
    entry = call("POST", "/intercompany", json={"workspace_id": OTHER, "kind": "loan", "direction": "receivable", "entry_date": today().isoformat(),
                                                "currency": "SAR", "amount": "10", "description": secret}).json()
    call("POST", f"/intercompany/{entry['id']}/void", json={"reason": secret})
    # Recurring bills (and the draft bills they make), automatic ECB rates (docs/business-books.md, second release).
    template = call("POST", "/recurring", json={"vendor_id": seller["id"], "description": secret, "currency": "SAR", "subtotal": "100", "tax": "0",
                                                "category": "rent", "frequency": "monthly", "start_date": today().isoformat(), "create_as": "draft"}).json()
    draft = template["bills"][0]
    # The owner made the recurring bill, so an accountant approves its draft (maker and checker).
    call("POST", "/accountants", json={"user_id": "member_books"})
    call("POST", f"/bills/{draft['id']}/approve", MEMBER, json={"version": draft["version"]})
    template = call("PATCH", f"/recurring/{template['id']}", json={"vendor_id": seller["id"], "description": secret, "currency": "SAR", "subtotal": "120",
                                                                    "tax": "0", "category": "rent", "frequency": "monthly", "start_date": today().isoformat(),
                                                                    "create_as": "draft", "version": template["version"]}).json()
    template = call("POST", f"/recurring/{template['id']}/pause", json={"version": template["version"]}).json()
    template = call("POST", f"/recurring/{template['id']}/resume", json={"version": template["version"]}).json()
    call("POST", f"/recurring/{template['id']}/end", json={"version": template["version"]})
    call("PUT", "/rates/auto", json={"enabled": True, "currencies": ["USD"], "version": None})
    import httpx

    from anum_api.finance import connectors

    xml = (f"<Cube><Cube time='{today().isoformat()}'><Cube currency='USD' rate='1.1000'/><Cube currency='JPY' rate='160.00'/>"
           "<Cube currency='GBP' rate='0.8500'/><Cube currency='INR' rate='92.000'/><Cube currency='CHF' rate='0.9500'/></Cube></Cube>")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(connectors, "finance_transport", httpx.MockTransport(lambda request: httpx.Response(200, text=xml)))
        call("POST", "/rates/auto/refresh")
    # The journal: accounts, Post now, a manual entry and its reversal, closing and reopening a period, accountants.
    call("POST", "/ledger/post")
    chart = client.get(f"{API}/ledger/accounts", headers=OWNER).json()["items"]
    bank, capital = (next(a for a in chart if a["system_key"] == key) for key in ("bank", "capital"))
    account = call("POST", "/ledger/accounts", json={"code": "6200", "name": "Synthetic rent", "subtype": "operating_expense", "category": "rent"}).json()
    call("PATCH", f"/ledger/accounts/{account['id']}", json={"code": "6210", "name": "Synthetic rent", "category": "rent", "version": account["version"]})
    entry = call("POST", "/ledger/journal", json={"entry_date": today().isoformat(), "description": secret, "lines": [
        {"account_id": bank["id"], "debit": "10"}, {"account_id": capital["id"], "credit": "10"}]}).json()
    call("POST", f"/ledger/journal/{entry['id']}/reverse", json={"reason": secret})
    last_month = today().replace(day=1) - timedelta(days=1)
    call("POST", "/close", json={"through": last_month.isoformat()})
    call("POST", "/reopen", json={"month": last_month.replace(day=1).isoformat(), "reason": secret})
    call("DELETE", "/accountants/member_books")
    # Tax (docs/business-tax.md): settings, a rate added and removed, e-invoice data generated for a sent invoice.
    call("PUT", "/tax/settings", json={"jurisdiction": "SA", "registered": True, "registration_number": "300000000000003", "zatca_enabled": True,
                                       "seller_street": secret[:20], "version": None})
    future = call("POST", "/tax/rates", json={"code": "standard", "rate_pct": "16", "effective_from": (today() + timedelta(days=60)).isoformat(),
                                              "note": secret}).json()
    call("DELETE", f"/tax/rates/{future['id']}")
    invoice = seed_invoice(subtotal="100", tax="15")
    call("POST", "/tax/zatca/generate", json={"source_id": invoice.id})
    # The fixed asset register, loan schedules and the period-end revaluation (docs/business-books.md#fixed-asset-register).
    call("POST", "/accountants", json={"user_id": "member_books"})
    capital = call("POST", "/bills", json={"vendor_id": seller["id"], "description": secret, "bill_date": today().isoformat(), "currency": "SAR",
                                           "subtotal": "1200", "tax": "0", "category": "equipment", "section": "capital", "useful_life_months": 24}).json()
    asset = next(item for item in client.get(f"{API}/assets", headers=OWNER).json()["items"] if item["source_id"] == capital["id"])
    asset = call("PATCH", f"/assets/{asset['id']}", json={"name": "Synthetic laptop", "category": "equipment", "useful_life_months": 36,
                                                          "residual_value": "0", "method": "straight_line",
                                                          "depreciation_start": today().replace(day=1).isoformat(), "version": asset["version"]}).json()
    first = call("POST", f"/assets/{asset['id']}/impairments", json={"entry_date": today().isoformat(), "amount": "10", "reason": secret}).json()
    call("POST", f"/assets/{asset['id']}/impairments/{first['id']}/approve", MEMBER, json={"version": first["version"]})
    second = call("POST", f"/assets/{asset['id']}/impairments", json={"entry_date": today().isoformat(), "amount": "10", "reason": secret}).json()
    call("POST", f"/assets/{asset['id']}/impairments/{second['id']}/reject", MEMBER, json={"version": second["version"]})
    asset = client.get(f"{API}/assets/{asset['id']}", headers=OWNER).json()
    call("POST", f"/assets/{asset['id']}/dispose", json={"disposed_on": today().isoformat(), "kind": "write_off", "version": asset["version"]})
    loan = call("POST", "/loans", json={"lender": secret, "principal": "1000", "currency": "SAR", "annual_rate": "6", "start_date": today().isoformat(),
                                        "term_months": 12}).json()
    repaid = call("POST", f"/loans/{loan['id']}/repayments", json={"paid_on": today().isoformat(), "amount": "100"}).json()
    call("POST", f"/loans/{loan['id']}/repayments/{repaid['id']}/void", json={"reason": secret})
    call("POST", f"/loans/{loan['id']}/void", json={"reason": secret})
    call("POST", "/ledger/revaluation", json={"period_end": last_month.isoformat()})
    # Supporting documents (docs/business-books.md#supporting-documents-and-evidence): a vault document attached and removed.
    from anum_api import files

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(files.file_store, "storage", files.InMemoryObjectStorage())
        receipt = client.post("/api/v1/documents", params={"name": "receipt.pdf", "title": "Synthetic receipt", "category": "receipt",
                                                           "access_class": "workspace"}, headers=OWNER,
                              content=b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n").json()
        kept = bill(seller["id"], description="Synthetic evidence bill").json()
        attached = call("POST", "/evidence", json={"source_type": "bill", "source_id": kept["id"], "document_id": receipt["id"]}).json()
        call("DELETE", f"/evidence/bill/{kept['id']}/{attached['link_id']}")
        files.file_store.clear()
        from anum_api.vault.store import memory_vault_store

        memory_vault_store.clear()
    probe.assert_covered()


def test_a_paid_bill_stays_paid_however_many_voided_payments_came_before() -> None:
    setup_books()
    made = bill(vendor()["id"], subtotal="100", tax="0").json()
    path = f"{API}/bills/{made['id']}/payments"
    from anum_api.business.books_models import BillPayment

    scoped = books_memory_store.scoped(ctx())
    for index in range(201):  # earlier payments typed by mistake and voided (seeded: the API limits writes per minute)
        scoped.add("bill_payments", BillPayment(id=f"bpay_void_{index:03d}", tenant_id=TENANT, workspace_id=BUSINESS, created_at=utc_now(), bill_id=made["id"],
                                                kind="recorded", amount=Decimal("1"), currency="SAR", paid_on=today(), method="cash", recorded_by="owner_books",
                                                voided_at=utc_now(), voided_by="owner_books", void_reason="typed twice"))
    assert client.post(path, headers=MEMBER, json={"amount": "100", "method": "cash"}).json()["payment_state"] == "paid"
    again = client.post(path, headers=MEMBER, json={"amount": "100", "method": "cash"})
    assert again.status_code == 409 and again.json()["code"] == "bill_paid"
    assert client.post(f"{API}/bills/{made['id']}/void", headers=OWNER, json={"reason": "duplicate"}).json()["code"] == "has_payments"


def test_an_allocation_paying_a_bill_counts_on_its_current_date() -> None:
    setup_books()
    canonical, _card = fuel_payment()
    allocate(canonical)
    station = vendor("Petromin", default_category="fuel")
    fuel_bill = bill(station["id"], subtotal="150", tax="0").json()
    assert client.post(f"{API}/bills/{fuel_bill['id']}/payments", headers=MEMBER, json={"kind": "allocation", "allocation_id": allocation_id()}).status_code == 201
    moved = date(today().year - 1, 6, 15)
    for row in links_memory_store.allocations.values():
        row.posted_on = moved  # the payment's date was corrected in Finance
    this_year = report(basis="cash")
    assert this_year["pnl"]["operating_expenses"]["total"] == "0.00"
    last_year = report(basis="cash", **{"from": moved.replace(day=1).isoformat(), "to": moved.replace(day=28).isoformat()})
    assert last_year["pnl"]["operating_expenses"]["total"] == "150.00"
