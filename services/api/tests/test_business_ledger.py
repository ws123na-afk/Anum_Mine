"""The business journal (docs/business-books.md#the-journal): posting, statements, close and lock, accountants. In-memory stores.

``test_postgres_business_ledger.py`` repeats the database rules (the balance trigger, frozen lines, closed
periods refused, forced RLS) under PostgreSQL. Customers, vendors, rates and bank messages are SYNTHETIC.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    BUSINESS,
    MEMBER,
    OTHER,
    OTHER_OWNER,
    OWNER,
    TENANT,
    VIEWER,
    allocate,
    allocation_id,
    bill,
    clean,
    client,
    ctx,
    fuel_payment,
    report,
    seed_credit,
    seed_invoice,
    seed_payment,
    setup_books,
    today,
    vendor,
)

from anum_api.business import books_jobs
from anum_api.business.books_models import JournalEntry, JournalLine
from anum_api.business.books_store import Unbalanced, books_memory_store
from anum_api.dependencies import memory_repository
from anum_api.finance.links_store import links_memory_store
from anum_api.schemas import WorkspaceMembership, utc_now

OWNER_TWO = {**OWNER, "x-user-id": "owner_two"}


@pytest.fixture(autouse=True)
def second_owner() -> None:
    now = utc_now()
    memory_repository.store.memberships[(TENANT, BUSINESS, "owner_two")] = WorkspaceMembership(
        tenant_id=TENANT, workspace_id=BUSINESS, user_id="owner_two", role="owner", active=True, created_at=now, updated_at=now)


def post(headers: dict = OWNER) -> dict:
    response = client.post(f"{API}/ledger/post", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def statements(headers: dict = OWNER, **params: Any) -> dict:
    response = client.get(f"{API}/ledger/statements", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def chart(headers: dict = OWNER) -> dict[str, dict]:
    return {row["system_key"] or row["code"]: row for row in client.get(f"{API}/ledger/accounts", headers=headers).json()["items"]}


def balance(data: dict, key: str) -> str:
    """The closing balance of a system account in the trial balance, debit positive."""
    account = chart()[key]
    row = next((row for row in data["trial_balance"]["rows"] if row["account_id"] == account["id"]), None)
    if row is None:
        return "0.00"
    return str(Decimal(row["closing_debit"]) - Decimal(row["closing_credit"]))


def entries(**params: Any) -> list[dict]:
    response = client.get(f"{API}/ledger/journal", headers=OWNER, params={"limit": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def lines_of(entry: dict) -> set[tuple[str, str, str]]:
    codes = {row["id"]: key for key, row in chart().items()}
    return {(codes[line["account_id"]], line["debit"], line["credit"]) for line in entry["lines"]}


def adjust(headers: dict = OWNER, **body: Any) -> Any:
    payload = {"entry_date": today().isoformat(), "currency": "SAR", "description": "Synthetic adjustment", **body}
    return client.post(f"{API}/adjustments", headers=headers, json=payload)


def last_month_end() -> date:
    return today().replace(day=1) - timedelta(days=1)


# -- posting ----------------------------------------------------------------------------------------------------------


def test_the_first_settings_save_seeds_the_chart_with_unique_structured_codes() -> None:
    setup_books()
    accounts = chart()
    assert accounts["bank"]["code"] == "1000" and accounts["ar"]["code"] == "1100" and accounts["ap"]["code"] == "2000"
    assert accounts["retained_earnings"]["code"] == "3200" and accounts["fx"]["code"] == "7000" and accounts["finance_costs"]["code"] == "7100"
    created = client.post(f"{API}/ledger/accounts", headers=OWNER, json={"code": "6200", "name": "Rent", "subtype": "operating_expense", "category": "rent"})
    assert created.status_code == 201, created.text
    assert client.post(f"{API}/ledger/accounts", headers=OWNER, json={"code": "6200", "name": "Again", "subtype": "operating_expense"}).json()["code"] == "duplicate"
    assert client.post(f"{API}/ledger/accounts", headers=OWNER, json={"code": "1300", "name": "Wrong range", "subtype": "operating_expense"}).json()["code"] == "code_range"
    assert client.post(f"{API}/ledger/accounts", headers=MEMBER, json={"code": "6300", "name": "Member", "subtype": "operating_expense"}).status_code == 403
    system = accounts["bank"]
    refused = client.patch(f"{API}/ledger/accounts/{system['id']}", headers=OWNER, json={"code": "1000", "name": "Bank", "archived": True, "version": system["version"]})
    assert refused.status_code == 409 and refused.json()["code"] == "system_account"
    renamed = client.patch(f"{API}/ledger/accounts/{system['id']}", headers=OWNER, json={"code": "1010", "name": "Main bank", "version": system["version"]})
    assert renamed.status_code == 200 and renamed.json()["code"] == "1010"
    # A bill of the bound category posts to that account, not the default operating expenses.
    made = bill(vendor()["id"], subtotal="100", tax="0").json()
    entry = next(item for item in entries(source_kind="bill") if item["source_id"] == made["id"])
    assert ("6200", "100.00", "0.00") in lines_of(entry)


def test_an_invoice_paid_in_part_posts_receivable_revenue_tax_and_cash() -> None:
    setup_books()
    invoice = seed_invoice(subtotal="1000", tax="150")
    seed_payment(invoice, "500")
    result = post()
    assert result["posted"] == 2 and result["issues"] == 0
    issued = entries(source_kind="invoice")[0]
    assert lines_of(issued) == {("ar", "1150.00", "0.00"), ("revenue_service", "0.00", "1000.00"), ("tax_payable", "0.00", "150.00")}
    paid = entries(source_kind="invoice_payment")[0]
    assert lines_of(paid) == {("bank", "500.00", "0.00"), ("ar", "0.00", "500.00")}
    assert paid["lines"][0]["flow"] == "customers_paid"
    data = statements()
    assert balance(data, "ar") == "650.00" and balance(data, "bank") == "500.00"
    assert data["trial_balance"]["balanced"] and data["balance_sheet"]["balanced"] and data["complete"]
    assert data["pnl"]["revenue"]["total"] == "1000.00" and data["reconciliation"]["agrees"]
    # Partial payments and a credit note: each its own event.
    seed_payment(invoice, "300")
    seed_credit(invoice, "100", "15")
    assert post()["posted"] == 2
    data = statements()
    assert balance(data, "ar") == "235.00" and data["pnl"]["revenue"]["total"] == "900.00" and data["reconciliation"]["agrees"]


def test_a_payment_in_another_currency_books_the_exchange_difference() -> None:
    setup_books()
    issued = today() - timedelta(days=10)
    for day, rate in ((issued, "3.70"), (today(), "3.75")):
        assert client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": rate, "as_of": day.isoformat(),
                                                                "source": "Synthetic reference"}).status_code == 201
    invoice = seed_invoice(subtotal="100", tax="0", currency="USD", issued=issued)
    seed_payment(invoice, "100")
    post()
    paid = entries(source_kind="invoice_payment")[0]
    # 100 USD received at 3.75 clears a receivable booked at 3.70: a 5.00 SAR gain, the rate kept on each line.
    assert lines_of(paid) == {("bank", "375.00", "0.00"), ("ar", "0.00", "370.00"), ("fx", "0.00", "5.00")}
    usd = next(line for line in paid["lines"] if line["currency"] == "USD" and line["debit"] != "0.00")
    assert (usd["amount"], usd["rate"], usd["rate_date"], usd["rate_source"]) == ("100.00", "3.75", today().isoformat(), "Synthetic reference")
    data = statements()
    assert data["pnl"]["fx_differences"] == "5.00" and balance(data, "ar") == "0.00"
    assert data["cash_flow"]["by_currency"] == [{"currency": "USD", "opening": "0.00", "operating": {"lines": [{"key": "customers_paid", "amount": "100.00"}],
                                                 "total": "100.00"}, "investing": {"lines": [], "total": "0.00"},
                                                 "financing": {"lines": [], "total": "0.00"}, "net": "100.00", "closing": "100.00", "reconciles": True}]


def test_a_missing_rate_leaves_the_event_unposted_with_its_reason_never_a_guess() -> None:
    setup_books()
    made = bill(vendor("Cloud Host", default_category="hosting")["id"], currency="USD", subtotal="100", tax="0", category="hosting").json()
    state = client.get(f"{API}/ledger/status", headers=MEMBER).json()
    assert state["issues"] == 1 and not state["complete"]
    issue = state["issue_rows"][0]
    assert (issue["event_key"], issue["reason"], issue["currency"], issue["rate_date"]) == (f"bill:{made['id']}", "missing_rate", "USD", today().isoformat())
    assert client.get(f"{API}/ledger/issues", headers=MEMBER).json()["items"][0]["reason"] == "missing_rate"
    assert entries(source_kind="bill") == []
    data = statements()
    assert not data["complete"] and "not_posted" in data["notes"] and not data["balance_sheet"]["complete"]
    assert client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": "3.75", "as_of": today().isoformat(),
                                                            "source": "Synthetic reference"}).status_code == 201
    posted = entries(source_kind="bill")[0]
    assert lines_of(posted) == {("operating", "375.00", "0.00"), ("ap", "0.00", "375.00")}
    assert client.get(f"{API}/ledger/issues", headers=MEMBER).json()["total"] == 0 and statements()["complete"]


def test_posting_is_idempotent_and_an_edit_reverses_and_reposts() -> None:
    setup_books()
    made = bill(vendor()["id"], subtotal="1000", tax="150").json()
    count = len(entries())
    again = post()
    assert again["posted"] == 0 and again["reversed"] == 0 and len(entries()) == count
    assert books_jobs.run()["entries_posted"] == 0
    edited = client.patch(f"{API}/bills/{made['id']}", headers=MEMBER, json={"vendor_id": made["vendor_id"], "description": "October rent",
                                                                             "bill_date": today().isoformat(), "currency": "SAR", "subtotal": "1200", "tax": "180",
                                                                             "category": "rent", "version": made["version"]})
    assert edited.status_code == 200, edited.text
    bills = entries(source_kind="bill")
    assert len(bills) == 3 and sum(1 for item in bills if item["status"] == "reversed") == 1 and sum(1 for item in bills if item["reverses_id"]) == 1
    assert balance(statements(), "ap") == "-1380.00"
    assert post()["posted"] == 0
    # Voiding the bill reverses its entry; nothing is deleted.
    client.post(f"{API}/bills/{made['id']}/void", headers=OWNER, json={"reason": "duplicate"})
    assert balance(statements(), "ap") == "0.00" and len(entries(source_kind="bill")) == 4


def test_an_owner_paid_business_expense_is_owed_to_the_member_and_reimbursement_is_capped() -> None:
    setup_books()
    canonical, _card = fuel_payment()
    allocate(canonical, treatment="reimbursable")
    post()
    expense = entries(source_kind="allocation")[0]
    assert lines_of(expense) == {("operating", "150.00", "0.00"), ("owed_to_members", "0.00", "150.00")}
    # Repaid on the day the expense is dated (the owner's local date, which can be a day ahead of UTC), never before it.
    on = expense["entry_date"]
    paid = adjust(kind="owner_reimbursement", amount="100", entry_date=on)
    assert paid.status_code == 201, paid.text
    assert lines_of(entries(source_kind="adjustment")[0]) == {("owed_to_members", "100.00", "0.00"), ("bank", "0.00", "100.00")}
    # Defect B4: never more than is owed.
    over = adjust(kind="owner_reimbursement", amount="60", entry_date=on)
    assert over.status_code == 422 and over.json()["code"] == "more_than_owed" and over.json()["remaining"] == "50.00"
    assert balance(statements(to=on), "owed_to_members") == "-50.00"
    repay = adjust(kind="loan_repayment", amount="1", entry_date=on)
    assert repay.status_code == 422 and repay.json()["remaining"] == "0"


def test_interest_owed_and_interest_paid_are_separate_events() -> None:
    setup_books()
    assert adjust(kind="loan_received", amount="10000").status_code == 201
    assert adjust(kind="loan_interest_accrued", amount="100").status_code == 201
    accrued = report()
    assert accrued["pnl"]["finance_costs"]["total"] == "100.00" and "interest_paid" not in accrued["cash_flow"]["lines"]
    assert accrued["balances"]["interest_payable"] == [{"currency": "SAR", "amount": "100.00"}]
    assert adjust(kind="loan_interest_paid", amount="150").json()["code"] == "more_than_owed"
    assert adjust(kind="loan_interest_paid", amount="100").status_code == 201
    paid = report()
    assert paid["pnl"]["finance_costs"]["total"] == "100.00"  # paying it is not a second cost (defect B5)
    assert paid["cash_flow"]["lines"]["interest_paid"] == [{"currency": "SAR", "amount": "100.00"}] and paid["balances"]["interest_payable"] == []
    data = statements()
    assert balance(data, "interest_payable") == "0.00" and balance(data, "finance_costs") == "100.00" and data["reconciliation"]["agrees"]


def test_depreciation_posts_month_by_month_up_to_today_at_the_purchase_rate() -> None:
    setup_books()
    bought = (today().replace(day=1) - timedelta(days=60)).replace(day=10)  # two months back
    made = bill(vendor("Synthetic Tools", default_category="equipment")["id"], subtotal="1200", tax="0", category="equipment", section="capital",
                useful_life_months=12, bill_date=bought.isoformat())
    assert made.status_code == 201, made.text
    state = client.get(f"{API}/ledger/status", headers=OWNER).json()
    assert state["scheduled"] == 9  # the months still to come are not posted ahead
    data = statements(**{"from": date(bought.year, 1, 1).isoformat(), "to": today().isoformat()})
    assert balance(data, "fixed_assets") == "1200.00" and balance(data, "accumulated_depreciation") == "-300.00"
    assert balance(data, "depreciation") == "300.00"
    assert len([item for item in entries(source_kind="bill") if ":dep:" not in item["description"]]) == 4


def test_an_accrual_reverses_on_its_day_and_voiding_it_reverses_both_entries() -> None:
    setup_books()
    made = adjust(kind="accrual", amount="300", category="utilities", entry_date=(today() - timedelta(days=10)).isoformat(),
                  reverse_on=(today() - timedelta(days=1)).isoformat())
    assert made.status_code == 201, made.text
    posted = entries(source_kind="adjustment")
    assert len(posted) == 2
    data = statements()
    assert balance(data, "accruals") == "0.00" and balance(data, "operating") == "0.00"
    accrual = client.post(f"{API}/adjustments/{made.json()['id']}/void", headers=OWNER, json={"reason": "estimate was wrong"})
    assert accrual.status_code == 200
    assert len(entries(source_kind="adjustment")) == 4
    assert all(row["closing_debit"] == row["closing_credit"] == "0.00" for row in statements()["trial_balance"]["rows"])


def test_intercompany_entries_post_on_both_sides_with_the_pair_and_eliminate_in_the_group() -> None:
    setup_books()
    setup_books(OTHER_OWNER)
    made = client.post(f"{API}/intercompany", headers=OWNER, json={"workspace_id": OTHER, "kind": "recharge", "direction": "receivable",
                                                                   "entry_date": today().isoformat(), "currency": "SAR", "amount": "1000",
                                                                   "description": "Synthetic shared office"})
    assert made.status_code == 201, made.text
    mine = entries(source_kind="intercompany")[0]
    assert lines_of(mine) == {("ic_receivable", "1000.00", "0.00"), ("other_income", "0.00", "1000.00")}
    theirs = client.get(f"{API}/ledger/journal", headers=OTHER_OWNER, params={"source_kind": "intercompany"}).json()["items"][0]
    assert {line["pair_id"] for line in mine["lines"]} == {line["pair_id"] for line in theirs["lines"]} == {made.json()["pair_id"]}
    group = client.get(f"{API}/group", headers=OWNER, params={"workspace": [BUSINESS, OTHER]}).json()
    rows = {row["key"]: row for row in group["pnl"]}
    # Each side counts it once in its own books; the group takes both out: no double counting.
    assert rows["other_income"]["businesses"][BUSINESS] == "1000.00" and rows["operating_expenses"]["businesses"][OTHER] == "1000.00"
    assert rows["other_income"]["total"] == "0.00" and rows["operating_expenses"]["total"] == "0.00" and rows["net_profit"]["total"] == "0.00"


def test_a_completed_balance_sheet_balances_and_equity_explains_the_change() -> None:
    setup_books()
    assert adjust(kind="owner_contribution", amount="5000").status_code == 201
    assert adjust(kind="loan_received", amount="2000").status_code == 201
    invoice = seed_invoice(subtotal="1000", tax="150")
    seed_payment(invoice, "1150")
    made = bill(vendor()["id"], subtotal="400", tax="60").json()
    assert client.post(f"{API}/bills/{made['id']}/payments", headers=MEMBER, json={"amount": "460", "method": "bank_transfer"}).status_code == 201
    assert adjust(kind="owner_drawing", amount="300").status_code == 201
    post()
    data = statements(compare=True)
    sheet = data["balance_sheet"]
    assert data["complete"] and sheet["complete"] and sheet["balanced"] and sheet["assets"]["total"] == sheet["liabilities_and_equity"]
    assert balance(data, "bank") == "7390.00"  # 5000 + 2000 + 1150 - 460 - 300
    assert sheet["assets"]["total"] == "7450.00"  # and the input tax of the bill, 60
    assert data["pnl"]["net_profit"] == "600.00" and data["reconciliation"]["agrees"]
    equity = {row["key"]: row for row in data["equity"]["rows"]}
    assert data["equity"]["balanced"] and equity["contributions"]["capital"] == "5000.00" and equity["drawings"]["drawings"] == "-300.00"
    assert equity["result"]["result"] == "600.00" and equity["closing"]["total"] == "5300.00"
    flows = data["cash_flow"]["by_currency"][0]
    assert flows["closing"] == "7390.00" and flows["reconciles"]
    assert {line["key"] for line in flows["financing"]["lines"]} == {"loans_received", "owner_contributions", "owner_drawings"}
    assert set(data["comparatives"]) == {"previous", "last_year"} and data["comparatives"]["previous"]["pnl"]["net_profit"] == "0.00"
    assert {item["key"] for item in data["policies"]} >= {"basis", "functional_currency", "rates", "depreciation"}
    exported = client.get(f"{API}/ledger/statements.csv", headers=MEMBER)
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert ["complete", "yes", "", "", ""] in rows and any(row[:2] == ["balance_sheet", "balanced"] for row in rows)


# -- manual entries and the database rule -----------------------------------------------------------------------------


def test_manual_entries_balance_are_idempotent_and_only_they_are_reversed_by_hand() -> None:
    setup_books()
    accounts = chart()
    body = {"entry_date": today().isoformat(), "description": "Synthetic opening balance",
            "lines": [{"account_id": accounts["bank"]["id"], "debit": "2500"}, {"account_id": accounts["capital"]["id"], "credit": "2000"}]}
    unbalanced = client.post(f"{API}/ledger/journal", headers=OWNER, json=body)
    assert unbalanced.status_code == 422 and unbalanced.json()["code"] == "unbalanced"
    body["lines"][1]["credit"] = "2500"
    assert client.post(f"{API}/ledger/journal", headers=MEMBER, json=body).status_code == 403
    keyed = {**OWNER, "Idempotency-Key": "manual-key-0001"}
    first = client.post(f"{API}/ledger/journal", headers=keyed, json=body)
    assert first.status_code == 201, first.text
    replay = client.post(f"{API}/ledger/journal", headers=keyed, json=body)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    reversed_ = client.post(f"{API}/ledger/journal/{first.json()['id']}/reverse", headers=OWNER, json={"reason": "typed twice"})
    assert reversed_.status_code == 200 and reversed_.json()["reverses_id"] == first.json()["id"]
    assert client.post(f"{API}/ledger/journal/{first.json()['id']}/reverse", headers=OWNER, json={"reason": "again please"}).json()["code"] == "already_reversed"
    made = bill(vendor()["id"]).json()
    source = next(item for item in entries(source_kind="bill") if item["source_id"] == made["id"])
    assert client.post(f"{API}/ledger/journal/{source['id']}/reverse", headers=OWNER, json={"reason": "not by hand"}).json()["code"] == "not_manual"


def test_the_store_refuses_an_unbalanced_entry_or_one_without_lines() -> None:
    setup_books()
    bank = chart()["bank"]["id"]
    now = utc_now()
    store = books_memory_store.scoped(ctx())
    entry = JournalEntry(id="bje_bad", tenant_id=TENANT, workspace_id=BUSINESS, created_at=now, entry_date=today(), event_date=today(),
                         source_kind="manual", event_key="manual:bad", fingerprint="x", description="Synthetic", created_by="owner_books")
    line = JournalLine(id="bjl_bad", tenant_id=TENANT, workspace_id=BUSINESS, created_at=now, entry_id="bje_bad", line_no=1, entry_date=today(),
                       source_kind="manual", account_id=bank, debit=Decimal(10), currency="SAR", amount=Decimal(10))
    with pytest.raises(Unbalanced):
        store.add_entry(entry, [line])
    with pytest.raises(Unbalanced):
        store.add_entry(entry, [])
    assert store.get("journal_entries", "bje_bad") is None


# -- close and lock ---------------------------------------------------------------------------------------------------


def test_a_closed_month_refuses_bill_edits_until_an_owner_reopens_it_with_a_reason() -> None:
    setup_books()
    in_month = last_month_end() - timedelta(days=5)
    made = bill(vendor()["id"], subtotal="100", tax="0", bill_date=in_month.isoformat()).json()
    status = client.get(f"{API}/close", headers=MEMBER).json()
    assert status["next"]["through"] == last_month_end().isoformat() and status["next"]["ready"] and not status["can_close"]
    assert client.post(f"{API}/close", headers=MEMBER, json={"through": last_month_end().isoformat()}).status_code == 403
    closed = client.post(f"{API}/close", headers=OWNER, json={"through": last_month_end().isoformat()})
    assert closed.status_code == 200, closed.text
    assert closed.json()["closed_through"] == last_month_end().isoformat()
    payload = {"vendor_id": made["vendor_id"], "description": "October rent", "bill_date": in_month.isoformat(), "currency": "SAR", "subtotal": "150",
               "tax": "0", "category": "rent", "version": made["version"]}
    refused = client.patch(f"{API}/bills/{made['id']}", headers=OWNER, json=payload)
    assert refused.status_code == 409 and refused.json()["code"] == "period_locked" and refused.json()["locked_through"] == last_month_end().isoformat()
    # Its words may change; its date, money and category may not; nothing new is dated in the closed month.
    words = client.patch(f"{API}/bills/{made['id']}", headers=OWNER, json={**payload, "subtotal": "100", "description": "Rent, corrected wording"})
    assert words.status_code == 200, words.text
    assert bill(made["vendor_id"], bill_date=in_month.isoformat()).json()["code"] == "period_locked"
    assert adjust(kind="accrual", amount="10", entry_date=in_month.isoformat()).json()["code"] == "period_locked"
    assert client.post(f"{API}/bills/{made['id']}/void", headers=OWNER, json={"reason": "duplicate"}).json()["code"] == "period_locked"
    # A payment today of a bill from the closed month is fine: the payment is in an open period.
    assert client.post(f"{API}/bills/{made['id']}/payments", headers=MEMBER, json={"amount": "100", "method": "cash"}).status_code == 201
    # Settings that would restate a closed period stay (defect B6).
    frozen = client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "SAR", "fiscal_year_start_month": 4, "version": 1})
    assert frozen.status_code == 409 and frozen.json()["code"] == "period_closed"
    # Only an owner reopens, with a reason; the reopen is audited with the lock before and after.
    assert client.post(f"{API}/reopen", headers=MEMBER, json={"month": in_month.isoformat(), "reason": "late supplier credit"}).status_code == 403
    assert client.post(f"{API}/reopen", headers=OWNER, json={"month": in_month.isoformat(), "reason": "no"}).status_code == 422
    reopened = client.post(f"{API}/reopen", headers=OWNER, json={"month": in_month.isoformat(), "reason": "late supplier credit"})
    assert reopened.status_code == 200 and reopened.json()["closed_through"] is None
    audit = [record for record in memory_repository.store.audit_records if record.action == "business.books.period_reopened"][-1]
    assert audit.metadata["before"] == {"closed_through": last_month_end().isoformat()} and audit.metadata["after"] == {"closed_through": ""}
    history = client.get(f"{API}/close", headers=OWNER).json()["history"]
    assert history[0]["status"] == "reopened" and history[0]["reopen_reason"] == "late supplier credit"
    edited = client.patch(f"{API}/bills/{made['id']}", headers=OWNER, json={**payload, "version": words.json()["version"] + 1, "subtotal": "100",
                                                                            "bill_date": (in_month - timedelta(days=1)).isoformat()})
    assert edited.status_code == 200, edited.text
    trail = [record for record in memory_repository.store.audit_records if record.action == "business.books.bill_updated"][-1]
    moved = (in_month - timedelta(days=1)).isoformat()
    assert trail.metadata["before"] == {"bill_date": in_month.isoformat(), "due_date": in_month.isoformat()}
    assert trail.metadata["after"] == {"bill_date": moved, "due_date": moved}


def test_close_waits_for_unposted_items_and_late_events_post_on_the_first_open_day() -> None:
    setup_books()
    in_month = last_month_end() - timedelta(days=3)
    bill(vendor("Cloud Host")["id"], currency="USD", subtotal="10", tax="0", bill_date=in_month.isoformat())
    waiting = client.post(f"{API}/close", headers=OWNER, json={"through": last_month_end().isoformat()})
    assert waiting.status_code == 409 and waiting.json()["code"] == "period_not_ready" and waiting.json()["issues"] == 1
    assert client.post(f"{API}/close", headers=OWNER, json={"through": today().isoformat()}).json()["code"] in ("not_month_end", "month_not_ended")
    client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": "3.75", "as_of": in_month.isoformat(), "source": "Synthetic"})
    assert client.post(f"{API}/close", headers=OWNER, json={"through": last_month_end().isoformat()}).status_code == 200
    # A member allocates an old payment after the close: it is posted on the first open day, marked late.
    canonical, _card = fuel_payment()
    allocate(canonical)
    for row in links_memory_store.allocations.values():
        row.posted_on = in_month
    post()
    late = entries(source_kind="allocation")[0]
    assert late["late"] and late["entry_date"] == today().replace(day=1).isoformat() and late["event_date"] == in_month.isoformat()
    assert "late_postings" in statements()["notes"]


def test_a_year_end_close_moves_the_result_to_retained_earnings_and_reopening_reverses_it() -> None:
    setup_books(fiscal_year_start_month=today().month)  # the financial year ended last month
    in_year = last_month_end() - timedelta(days=2)
    invoice = seed_invoice(subtotal="1000", tax="0", issued=in_year)
    seed_payment(invoice, "1000", paid_on=in_year)
    closed = client.post(f"{API}/close", headers=OWNER, json={"through": last_month_end().isoformat()})
    assert closed.status_code == 200, closed.text
    assert closed.json()["closed"][-1]["kind"] == "year" and closed.json()["closed"][-1]["closing_entry_id"]
    closing = entries(source_kind="closing")[0]
    assert lines_of(closing) == {("revenue_service", "1000.00", "0.00"), ("retained_earnings", "0.00", "1000.00")}
    data = statements(**{"from": today().replace(day=1).isoformat(), "to": today().isoformat()})
    assert balance(data, "retained_earnings") == "-1000.00" and data["pnl"]["net_profit"] == "0.00" and data["balance_sheet"]["balanced"]
    opening = {row["key"]: row for row in data["equity"]["rows"]}["opening"]
    assert opening["retained_earnings"] == "1000.00" and opening["result"] == "0.00"  # carried forward
    year = statements(**{"from": (in_year.replace(day=1)).isoformat(), "to": last_month_end().isoformat()})
    moved = {row["key"]: row for row in year["equity"]["rows"]}
    assert moved["result"]["result"] == "1000.00" and moved["transfer_to_retained_earnings"]["retained_earnings"] == "1000.00" and year["equity"]["balanced"]
    assert year["pnl"]["net_profit"] == "1000.00"  # the closed year's own report is reproduced after the close
    reopened = client.post(f"{API}/reopen", headers=OWNER, json={"month": last_month_end().isoformat(), "reason": "audit adjustment"})
    assert reopened.status_code == 200
    assert next(item for item in entries(source_kind="closing") if item["id"] == closing["id"])["status"] == "reversed"
    assert balance(statements(), "retained_earnings") == "0.00"


def test_an_accountant_adjusts_approves_others_drafts_and_closes_but_never_reopens() -> None:
    setup_books()
    assert adjust(MEMBER, kind="accrual", amount="10").status_code == 403
    assert client.post(f"{API}/accountants", headers=MEMBER, json={"user_id": "member_books"}).status_code == 403
    assert client.post(f"{API}/accountants", headers=OWNER, json={"user_id": "viewer_books"}).json()["code"] == "not_a_member"
    assert client.post(f"{API}/accountants", headers=OWNER, json={"user_id": "member_books"}).status_code == 201
    assert client.get(f"{API}/setup", headers=MEMBER).json()["permissions"]["account"] is True
    assert adjust(MEMBER, kind="accrual", amount="10").status_code == 201
    template = client.post(f"{API}/recurring", headers=OWNER, json={"vendor_id": vendor()["id"], "description": "Synthetic rent", "currency": "SAR",
                                                                     "subtotal": "100", "tax": "0", "category": "rent", "frequency": "monthly",
                                                                     "start_date": today().isoformat(), "create_as": "draft"}).json()
    draft = template["bills"][0]
    approved = client.post(f"{API}/bills/{draft['id']}/approve", headers=MEMBER, json={"version": draft["version"]})
    assert approved.status_code == 200
    assert client.post(f"{API}/close", headers=MEMBER, json={"through": last_month_end().isoformat()}).status_code == 200
    assert client.post(f"{API}/reopen", headers=MEMBER, json={"month": last_month_end().isoformat(), "reason": "member try"}).status_code == 403
    assert client.delete(f"{API}/accountants/member_books", headers=OWNER).status_code == 204
    assert adjust(MEMBER, kind="accrual", amount="10").status_code == 403


def test_another_business_or_tenant_reads_none_of_the_journal() -> None:
    setup_books()
    bill(vendor()["id"])
    assert entries()
    setup_books(OTHER_OWNER)
    assert client.get(f"{API}/ledger/journal", headers=OTHER_OWNER).json()["total"] == 0
    assert client.get(f"{API}/ledger/journal", headers=VIEWER).status_code == 403
    stranger = client.get(f"{API}/ledger/journal", headers={**OWNER, "x-tenant-id": "tenant_books_other"})
    assert stranger.status_code == 409 and stranger.json()["code"] == "books_not_set_up"  # another tenant sees no books at all


def test_the_base_currency_stays_once_the_journal_has_entries() -> None:
    setup_books()
    bill(vendor()["id"])
    moved = client.put(f"{API}/settings", headers=OWNER, json={"base_currency": "AED", "version": 1})
    assert moved.status_code == 409 and moved.json()["code"] == "journal_exists"


# -- defects of the derived figures -------------------------------------------------------------------------------------


def test_breakdowns_flag_unconverted_rows_and_say_how_many_rows_there_are() -> None:
    setup_books()
    seed_invoice(subtotal="100", tax="0", currency="USD", account_id="acc_usd")
    data = report()
    row = next(item for item in data["customers"] if item["id"] == "acc_usd")
    assert row["revenue"] == "0.00" and not row["complete"] and row["not_converted"] == [{"currency": "USD", "net": "100.00"}]
    assert data["customers_total"] == 1 and data["projects_total"] == 0


def test_the_trend_keeps_exchange_differences_out_of_revenue() -> None:
    setup_books()
    issued = today() - timedelta(days=10)
    for day, rate in ((issued, "3.70"), (today(), "3.75")):
        client.post(f"{API}/rates", headers=OWNER, json={"base": "USD", "quote": "SAR", "rate": rate, "as_of": day.isoformat(), "source": "Synthetic"})
    invoice = seed_invoice(subtotal="100", tax="0", currency="USD", issued=issued)
    seed_payment(invoice, "100")
    month = report()["trend"][-1]
    assert month["fx_differences"] == "5.00" and month["revenue"] == ("370.00" if issued.month == today().month else "0.00")
    assert Decimal(month["profit"]) == Decimal(month["revenue"]) - Decimal(month["costs"]) + Decimal("5.00")


def test_the_pnl_export_says_whether_it_is_complete_and_carries_balances_and_equity() -> None:
    setup_books()
    seed_invoice()
    adjust(kind="owner_contribution", amount="500")
    bill(vendor("Cloud Host")["id"], currency="USD", subtotal="10", tax="0")
    rows = list(csv.reader(io.StringIO(client.get(f"{API}/report.csv", headers=MEMBER).text)))
    assert ["complete", "no", "", ""] in rows and ["note", "not_converted", "", ""] in rows
    assert any(row[0] == "owed_receivables" and row[3] == "1150.00" for row in rows)
    assert ["equity", "contributions", "SAR", "500.00"] in rows


def test_the_daily_job_backfills_a_business_that_had_books_before_the_journal() -> None:
    setup_books()
    bill(vendor()["id"])
    # As if the books existed before the journal: no chart, no entries.
    for kind in ("journal_lines", "journal_entries", "ledger_accounts"):
        books_memory_store.rows[kind].clear()
    seed_invoice()
    first = books_jobs.run()
    assert first["ledgers"] == 1 and first["entries_posted"] == 2 and first["not_posted"] == 0
    assert books_jobs.run()["entries_posted"] == 0
    assert len(chart()) >= 29
