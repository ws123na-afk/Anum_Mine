"""Business accounting completion (docs/business-books.md#fixed-asset-register, #loan-schedules, #period-end-revaluation,
#group-balance-sheet): the fixed asset register, loan schedules, the period-end revaluation and realised exchange
differences on settlements, and the group balance sheet. In-memory stores.

``test_postgres_business_assets.py`` repeats the database rules (forced RLS, the closed-period triggers, one asset per
source, maker and checker) under PostgreSQL. Lenders, vendors, customers and rates are SYNTHETIC.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import pytest
from test_business_books import (  # noqa: F401  (``clean`` is the autouse fixture that resets every store)
    API,
    MEMBER,
    OTHER,
    OTHER_OWNER,
    OWNER,
    TENANT,
    bill,
    clean,
    client,
    seed_invoice,
    setup_books,
    today,
    vendor,
)
from test_business_ledger import (
    adjust,
    balance,
    chart,
    entries,
    last_month_end,
    lines_of,
    post,
    statements,
)

from anum_api.business import books_assets as A
from anum_api.business import books_loans as LN
from anum_api.business import books_register
from anum_api.business.books_models import Asset, AssetImpairment, Loan, LoanRepayment
from anum_api.business.books_report import add_months, month_start
from anum_api.schemas import utc_now

CENT = Decimal("0.01")


def q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def months_ago(count: int) -> date:
    return add_months(month_start(today()), -count)


def rate(currency: str, value: str, day: date, headers: dict = OWNER, quote: str = "SAR") -> None:
    response = client.post(f"{API}/rates", headers=headers, json={"base": currency, "quote": quote, "rate": value, "as_of": day.isoformat(),
                                                                  "source": "Synthetic reference"})
    assert response.status_code == 201, response.text


def capital_bill(cost: str, life: int, day: date, **body: Any) -> dict:
    made = bill(vendor(f"Synthetic Supplier {cost} {day.isoformat()} {life}")["id"], subtotal=cost, tax="0", category="equipment", section="capital",
                useful_life_months=life, bill_date=day.isoformat(), **body)
    assert made.status_code == 201, made.text
    return made.json()


def assets() -> list[dict]:
    response = client.get(f"{API}/assets", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def asset_of(source_id: str) -> dict:
    found = next(item for item in assets() if item["source_id"] == source_id)
    response = client.get(f"{API}/assets/{found['id']}", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()


def account_total(key: str, **params: Any) -> Decimal:
    return Decimal(balance(statements(**params), key))


# -- the fixed asset register -------------------------------------------------------------------------------------------------


def test_existing_capital_purchases_are_registered_once_and_their_depreciation_is_never_counted_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    setup_books()
    start = months_ago(4).replace(day=15)  # a purchase in the middle of a month, part way through the financial year
    with monkeypatch.context() as patched:
        # Books from before the register: the posting run did not register anything, the bill posted its own depreciation.
        patched.setattr(books_register, "sync", lambda *args, **kwargs: 0)
        made = capital_bill("1200", 24, start)
        item = adjust(kind="capital_item", amount="600", entry_date=months_ago(2).isoformat(), start_month=months_ago(2).isoformat(), months=12,
                      description="Synthetic printer brought in").json()
        post()
        before = len(entries(source_kind="bill")) + len(entries(source_kind="adjustment"))
        assert client.get(f"{API}/assets", headers=OWNER).status_code == 200  # opening the register is the first registration
    assert len(assets()) == 2 and len(assets()) == 2  # registered once, however often it is opened
    rerun = post()
    assert rerun["posted"] == 0 and rerun["reversed"] == 0, rerun  # the same keys and facts: nothing re-posted, nothing twice
    assert len(entries(source_kind="bill")) + len(entries(source_kind="adjustment")) == before
    # Five months of the bill (50.00 each) and three of the item (50.00 each) since their start months.
    assert account_total("accumulated_depreciation") == Decimal("-400.00")
    bill_asset, item_asset = asset_of(made["id"]), asset_of(item["id"])
    assert bill_asset["method"] == "straight_line" and bill_asset["useful_life_months"] == 24 and bill_asset["depreciation_start"] == months_ago(4).isoformat()
    assert bill_asset["accumulated_depreciation"] == "250.00" and bill_asset["net_book_value"] == "950.00"
    assert item_asset["net_book_value"] == "450.00" and len(item_asset["schedule"]) == 12
    # The register depreciates them from now on: a new capital bill is registered by the posting run itself.
    newer = capital_bill("240", 12, today())
    assert any(row["source_id"] == newer["id"] for row in assets())


def test_reducing_balance_with_a_residual_value_from_a_mid_year_month() -> None:
    setup_books()
    made = capital_bill("1200", 12, months_ago(5).replace(day=20))
    found = asset_of(made["id"])
    start = months_ago(3)
    updated = client.patch(f"{API}/assets/{found['id']}", headers=OWNER, json={
        "name": "Synthetic forklift", "category": "vehicles", "useful_life_months": 12, "residual_value": "200", "method": "reducing_balance",
        "rate_pct": "30", "depreciation_start": start.isoformat(), "version": found["version"]})
    assert updated.status_code == 200, updated.text
    expected, value = [], Decimal(1200)
    for index in range(12):
        part = value - 200 if index == 11 else min(q(value * Decimal("0.30") / 12), value - 200)
        expected.append(part)
        value -= part
    schedule = updated.json()["schedule"]
    assert [Decimal(row["amount"]) for row in schedule] == expected and sum(expected) == Decimal(1000)
    assert schedule[0]["month"] == start.isoformat() and schedule[0]["amount"] == "30.00" and schedule[1]["amount"] == "29.25"
    # The straight-line months posted before are reversed; only the reducing-balance months up to this one stay.
    assert account_total("accumulated_depreciation") == -sum(expected[:4])
    assert client.patch(f"{API}/assets/{found['id']}", headers=OWNER, json={
        "name": "Synthetic forklift", "useful_life_months": 12, "method": "reducing_balance", "depreciation_start": start.isoformat(),
        "version": updated.json()["version"]}).json()["code"] == "rate_required"
    assert client.patch(f"{API}/assets/{found['id']}", headers=MEMBER, json={
        "name": "Synthetic forklift", "depreciation_start": start.isoformat(), "version": updated.json()["version"]}).status_code == 403
    assert post()["posted"] == 0


def test_a_sale_posts_its_gain_and_a_write_off_its_loss() -> None:
    setup_books()
    sold, scrapped = capital_bill("1200", 24, months_ago(2)), capital_bill("600", 12, months_ago(2))
    for made, body in ((sold, {"kind": "sale", "proceeds": "1500"}), (scrapped, {"kind": "write_off"})):
        found = asset_of(made["id"])
        response = client.post(f"{API}/assets/{found['id']}/dispose", headers=OWNER, json={"disposed_on": today().isoformat(), "version": found["version"],
                                                                                            **body})
        assert response.status_code == 200, response.text
    sale = asset_of(sold["id"])
    # Two months depreciated (none in the disposal month): net book value 1,100.00, sold for 1,500.00.
    assert sale["status"] == "disposed" and sale["gain_or_loss"] == "400.00" and sale["net_book_value"] == "0.00"
    disposal = [entry for entry in entries(source_kind="asset") if entry["source_id"] == sale["id"]][0]
    assert lines_of(disposal) == {("fixed_assets", "0.00", "1200.00"), ("accumulated_depreciation", "100.00", "0.00"), ("bank", "1500.00", "0.00"),
                                  ("disposal_gain", "0.00", "400.00")}
    bank_line = next(line for line in disposal["lines"] if line["debit"] == "1500.00")
    assert bank_line["flow"] == "asset_sales"
    loss = [entry for entry in entries(source_kind="asset") if entry["source_id"] == asset_of(scrapped["id"])["id"]][0]
    assert ("disposal_loss", "500.00", "0.00") in lines_of(loss)
    data = statements()
    assert balance(data, "fixed_assets") == "0.00" and balance(data, "accumulated_depreciation") == "0.00"
    assert data["balance_sheet"]["balanced"] and data["trial_balance"]["balanced"]
    assert data["reconciliation"]["agrees"], data["reconciliation"]
    again = client.post(f"{API}/assets/{sale['id']}/dispose", headers=OWNER, json={"disposed_on": today().isoformat(), "kind": "write_off",
                                                                                  "version": sale["version"]})
    assert again.status_code == 409 and again.json()["code"] == "asset_disposed"
    assert post()["posted"] == 0


def test_impairment_needs_a_second_person_and_lowers_later_depreciation() -> None:
    setup_books()
    made = capital_bill("1200", 12, months_ago(2))
    found = asset_of(made["id"])
    asked = client.post(f"{API}/assets/{found['id']}/impairments", headers=OWNER, json={"entry_date": today().isoformat(), "amount": "300",
                                                                                        "reason": "Synthetic water damage"})
    assert asked.status_code == 201, asked.text
    too_much = client.post(f"{API}/assets/{found['id']}/impairments", headers=OWNER, json={"entry_date": today().isoformat(), "amount": "5000",
                                                                                           "reason": "Synthetic estimate"})
    assert too_much.status_code == 422 and too_much.json()["code"] == "more_than_value"
    impairment = asked.json()
    own = client.post(f"{API}/assets/{found['id']}/impairments/{impairment['id']}/approve", headers=OWNER, json={"version": impairment["version"]})
    assert own.status_code == 403 and own.json()["code"] == "same_person"
    assert client.post(f"{API}/assets/{found['id']}/impairments/{impairment['id']}/approve", headers=MEMBER,
                       json={"version": impairment["version"]}).status_code == 403  # not an accountant yet
    assert client.post(f"{API}/accountants", headers=OWNER, json={"user_id": "member_books"}).status_code == 201
    approved = client.post(f"{API}/assets/{found['id']}/impairments/{impairment['id']}/approve", headers=MEMBER, json={"version": impairment["version"]})
    assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
    posted = [entry for entry in entries(source_kind="asset") if entry["source_id"] == found["id"]]
    assert len(posted) == 1 and lines_of(posted[0]) == {("impairment", "300.00", "0.00"), ("accumulated_depreciation", "0.00", "300.00")}
    detail = asset_of(made["id"])
    # Three months (100.00 each) and the impairment: 600.00 left, spread over the nine months that remain.
    assert detail["net_book_value"] == "600.00"
    later = [Decimal(row["amount"]) for row in detail["schedule"][3:]]
    assert len(later) == 9 and sum(later) == Decimal(600) and later[0] == Decimal("66.67")
    decided = client.post(f"{API}/assets/{found['id']}/impairments/{impairment['id']}/reject", headers=OWNER, json={"version": approved.json()["version"]})
    assert decided.status_code == 409 and decided.json()["code"] == "already_decided"


def test_a_closed_period_keeps_its_depreciation_disposals_and_impairments() -> None:
    setup_books()
    made = capital_bill("1200", 12, months_ago(3))
    closed = client.post(f"{API}/close", headers=OWNER, json={"through": last_month_end().isoformat()})
    assert closed.status_code == 200, closed.text
    found = asset_of(made["id"])
    changed = client.patch(f"{API}/assets/{found['id']}", headers=OWNER, json={
        "name": "Synthetic van", "useful_life_months": 24, "depreciation_start": found["depreciation_start"], "version": found["version"]})
    assert changed.status_code == 409 and changed.json()["code"] == "period_locked"
    # A change that leaves the closed months alone is fine: only later months change.
    assert [row["state"] for row in found["schedule"][:3]] == ["closed"] * 3
    in_closed = last_month_end() - timedelta(days=3)
    disposed = client.post(f"{API}/assets/{found['id']}/dispose", headers=OWNER, json={"disposed_on": in_closed.isoformat(), "kind": "write_off",
                                                                                       "version": found["version"]})
    assert disposed.status_code == 409 and disposed.json()["code"] == "period_locked"
    impaired = client.post(f"{API}/assets/{found['id']}/impairments", headers=OWNER, json={"entry_date": in_closed.isoformat(), "amount": "10",
                                                                                           "reason": "Synthetic damage"})
    assert impaired.status_code == 409 and impaired.json()["code"] == "period_locked"
    ok = client.post(f"{API}/assets/{found['id']}/dispose", headers=OWNER, json={"disposed_on": today().isoformat(), "kind": "write_off",
                                                                                 "version": found["version"]})
    assert ok.status_code == 200, ok.text
    assert all(not entry["late"] for entry in entries(source_kind="asset"))


def test_the_roll_forward_explains_cost_and_depreciation() -> None:
    setup_books()
    old = capital_bill("1200", 12, months_ago(14))
    capital_bill("600", 12, today())
    first, last = month_start(today()).replace(month=1), today()
    data = client.get(f"{API}/assets", headers=OWNER, params={"from": first.isoformat(), "to": last.isoformat()}).json()
    row = next(item for item in data["roll_forward"] if item["currency"] == "SAR")
    assert Decimal(row["closing_cost"]) == Decimal(row["opening_cost"]) + Decimal(row["additions"]) - Decimal(row["disposals_cost"])
    assert Decimal(row["closing_depreciation"]) == (Decimal(row["opening_depreciation"]) + Decimal(row["depreciation"]) + Decimal(row["impairment"])
                                                     - Decimal(row["disposals_depreciation"]))
    assert row["additions"] == "600.00" and asset_of(old["id"])["accumulated_depreciation"] == "1200.00"
    csv_text = client.get(f"{API}/assets.csv", headers=OWNER).text
    assert "roll_forward" in csv_text and "register" in csv_text


# -- loans ----------------------------------------------------------------------------------------------------------------------


def loan(headers: dict = OWNER, **body: Any) -> dict:
    payload = {"lender": "Synthetic Bank", "principal": "12000", "currency": "SAR", "annual_rate": "12", "start_date": months_ago(2).isoformat(),
               "term_months": 12, **body}
    response = client.post(f"{API}/loans", headers=headers, json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()


def detail(loan_id: str) -> dict:
    response = client.get(f"{API}/loans/{loan_id}", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()


def repay(loan_id: str, amount: str, paid_on: date, **body: Any) -> Any:
    return client.post(f"{API}/loans/{loan_id}/repayments", headers=OWNER, json={"paid_on": paid_on.isoformat(), "amount": amount, **body})


def annuity(principal: Decimal, yearly_pct: Decimal, count: int) -> list[tuple[Decimal, Decimal, Decimal]]:
    """The standard annuity formula, worked out here: (payment, interest, principal) per period."""
    rate = yearly_pct / 100 / 12
    payment = q(principal * rate / (1 - (1 + rate) ** -count))
    out, balance_left = [], principal
    for number in range(count):
        interest = q(balance_left * rate)
        part = balance_left if number == count - 1 else payment - interest
        out.append((part + interest, interest, part))
        balance_left -= part
    return out


def test_the_annuity_schedule_matches_the_standard_formula_and_equal_principal_its_own() -> None:
    setup_books()
    made = loan()
    rows = detail(made["id"])["schedule"]
    expected = annuity(Decimal(12000), Decimal(12), 12)
    assert [(Decimal(r["payment"]), Decimal(r["interest"]), Decimal(r["principal"])) for r in rows] == expected
    assert rows[0]["payment"] == "1066.19" and rows[0]["interest"] == "120.00" and rows[-1]["balance"] == "0.00"
    assert rows[0]["due"] == months_ago(1).isoformat() and made["maturity"] == add_months(months_ago(2), 12).isoformat()
    flat = loan(method="equal_principal", frequency="quarterly")
    parts = detail(flat["id"])["schedule"]
    assert [r["principal"] for r in parts] == ["3000.00"] * 4 and parts[0]["interest"] == "360.00" and parts[1]["interest"] == "270.00"
    refused = client.post(f"{API}/loans", headers=OWNER, json={"lender": "Synthetic Bank", "principal": "100", "currency": "SAR", "annual_rate": "5",
                                                                "start_date": today().isoformat(), "term_months": 5, "frequency": "quarterly"})
    assert refused.status_code == 422 and refused.json()["code"] == "invalid_term"
    assert client.post(f"{API}/loans", headers=MEMBER, json={"lender": "Synthetic Bank", "principal": "100", "currency": "SAR", "annual_rate": "5",
                                                              "start_date": today().isoformat(), "term_months": 12}).status_code == 403


def test_a_repayment_splits_into_principal_and_interest_and_unpaid_interest_is_accrued_at_month_ends() -> None:
    setup_books()
    made = loan()
    start, due_one = months_ago(2), months_ago(1)
    payment = Decimal("1066.19")
    paid = repay(made["id"], "1066.19", due_one)
    assert paid.status_code == 201, paid.text
    assert paid.json()["interest"] == "120.00" and paid.json()["principal"] == "946.19"
    received = entries(source_kind="loan")
    assert any(lines_of(entry) == {("bank", "12000.00", "0.00"), ("loans", "0.00", "12000.00")} for entry in received)
    repayment = entries(source_kind="loan_repayment")[0]
    assert lines_of(repayment) == {("loans", "946.19", "0.00"), ("finance_costs", "120.00", "0.00"), ("bank", "0.00", "946.19"),
                                   ("bank", "0.00", "120.00")}
    assert {line["flow"] for line in repayment["lines"] if line["credit"] != "0.00"} == {"loans_repaid", "interest_paid"}
    # Interest accrued and unpaid on each month end, in proportion to the days of the schedule period.
    first_days = (due_one - start).days
    accrual_one = q(Decimal(12000) * Decimal("0.01") * (first_days - 1) / first_days)
    second_days = (month_start(today()) - due_one).days
    balance_after = Decimal(12000) - (payment - 120)
    accrual_two = q(balance_after * Decimal("0.01") * (second_days - 1) / second_days)
    accruals = {row["month_end"]: row["amount"] for row in detail(made["id"])["accruals"]}
    assert accruals[(due_one - timedelta(days=1)).isoformat()] == str(accrual_one)
    assert accruals[last_month_end().isoformat()] == str(accrual_two)
    data = statements(**{"from": start.isoformat(), "to": last_month_end().isoformat()})
    assert balance(data, "interest_payable") == str(-accrual_two)
    # The month's accrual is reversed the next day; the repayment carries its whole interest.
    assert Decimal(data["pnl"]["finance_costs"]["total"]) == 120 + accrual_two
    assert data["balance_sheet"]["balanced"] and data["reconciliation"]["agrees"], data["reconciliation"]
    too_much = repay(made["id"], "50000", today())
    assert too_much.status_code == 422 and too_much.json()["code"] == "more_than_owed"
    earlier = repay(made["id"], "10", start + timedelta(days=3))
    assert earlier.status_code == 422 and earlier.json()["code"] == "out_of_order"
    assert post()["posted"] == 0 and post()["reversed"] == 0


def test_an_early_repayment_shortens_the_term_or_lowers_the_payment() -> None:
    setup_books()
    shorter, lower = loan(), loan(early_repayment="reduce_payment")
    for made in (shorter, lower):
        assert repay(made["id"], "4066.19", months_ago(1)).status_code == 201  # the first payment and 3,000.00 more
    term = detail(shorter["id"])
    assert all(row["payment"] == "1066.19" for row in term["revised"][:-1]) and len(term["revised"]) < 11
    payment = detail(lower["id"])
    assert len(payment["revised"]) == 11 and Decimal(payment["revised"][0]["payment"]) < Decimal("1066.19")
    assert payment["revised"][-1]["balance"] == "0.00" and term["revised"][-1]["balance"] == "0.00"
    actual = payment["actual"][0]
    assert actual["state"] == "paid" and actual["paid"] == "4066.19" and actual["paid_interest"] == "120.00"


def test_repayments_matched_to_a_bill_or_an_adjustment_are_not_posted_twice() -> None:
    setup_books()
    received = adjust(kind="loan_received", amount="12000", entry_date=months_ago(2).isoformat(), description="Synthetic loan in").json()
    made = loan(received_adjustment_id=received["id"])
    assert not [entry for entry in entries(source_kind="loan") if lines_of(entry) == {("bank", "12000.00", "0.00"), ("loans", "0.00", "12000.00")}]
    paid = adjust(kind="loan_repayment", amount="1066.19", entry_date=months_ago(1).isoformat(), description="Synthetic repayment").json()
    matched = repay(made["id"], "1066.19", months_ago(1), adjustment_id=paid["id"])
    assert matched.status_code == 201, matched.text
    moved = entries(source_kind="loan_repayment")[0]
    assert lines_of(moved) == {("finance_costs", "120.00", "0.00"), ("loans", "0.00", "120.00")}
    data = statements()
    assert balance(data, "loans") == "-11053.81" and balance(data, "bank") == str(Decimal(12000) - Decimal("1066.19"))
    again = repay(made["id"], "10", months_ago(1), adjustment_id=paid["id"])
    assert again.status_code in (409, 422)
    supplier = vendor("Synthetic Lender Billing")["id"]
    invoice = bill(supplier, subtotal="1066.19", tax="0", category="loan", bill_date=today().isoformat()).json()
    owed = Decimal(detail(made["id"])["outstanding"]) + Decimal(detail(made["id"])["interest_accrued"])
    assert owed > Decimal("1066.19")
    split = repay(made["id"], "1066.19", today(), bill_id=invoice["id"])
    assert split.status_code == 201, split.text
    bill_split = next(entry for entry in entries(source_kind="loan_repayment") if entry["source_id"] == split.json()["id"])
    codes = lines_of(bill_split)
    assert ("6000", "0.00", "1066.19") in {(chart_key if chart_key != "operating" else "6000", d, c) for chart_key, d, c in codes}
    assert sum(Decimal(d) for _, d, _ in codes) == Decimal("1066.19")


# -- exchange differences ----------------------------------------------------------------------------------------------------------


def test_month_end_revaluation_books_gains_and_losses_reverses_next_month_and_never_guesses_a_rate() -> None:
    setup_books(rate_max_age_days=5)
    end = last_month_end()
    issued = end - timedelta(days=20)
    rate("USD", "3.75", issued)
    rate("AED", "1.02", issued)
    rate("USD", "3.80", end)
    seed_invoice(subtotal="100", tax="0", currency="USD", issued=issued)
    seed_invoice(subtotal="100", tax="0", currency="AED", issued=issued)
    post()
    done = client.post(f"{API}/ledger/revaluation", headers=OWNER, json={"period_end": end.isoformat()})
    assert done.status_code == 200, done.text
    result = {row["currency"]: row for row in done.json()["currencies"]}
    assert result["USD"]["status"] == "revalued" and result["USD"]["difference"] == "5.00" and result["USD"]["rate"] == "3.8"
    assert result["AED"]["status"] == "missing_rate" and done.json()["missing"] == ["AED"]  # only AED waits; USD is revalued
    revaluations = entries(source_kind="revaluation")
    entry = next(item for item in revaluations if item["entry_date"] == end.isoformat())
    back = next(item for item in revaluations if item["entry_date"] == (end + timedelta(days=1)).isoformat())
    assert lines_of(entry) == {("ar", "5.00", "0.00"), ("fx_unrealised", "0.00", "5.00")}
    assert lines_of(back) == {("ar", "0.00", "5.00"), ("fx_unrealised", "5.00", "0.00")}
    month = statements(**{"from": month_start(end).isoformat(), "to": end.isoformat()})
    assert month["unrealised_fx"] == "5.00" and balance(month, "ar") == "482.00"
    assert month["reconciliation"]["agrees"] and any(row["key"] == "unrealised_fx" and row.get("explained") for row in month["reconciliation"]["rows"])
    assert month["cash_flow"]["by_currency"] == [] or all(item["reconciles"] for item in month["cash_flow"]["by_currency"])
    following = statements(**{"from": (end + timedelta(days=1)).isoformat(), "to": today().isoformat()})
    assert following["unrealised_fx"] == "-5.00"
    issues = client.get(f"{API}/ledger/issues", headers=OWNER).json()["items"]
    assert [(row["reason"], row["currency"]) for row in issues] == [("missing_closing_rate", "AED")]
    unchanged = client.post(f"{API}/ledger/revaluation", headers=OWNER, json={"period_end": end.isoformat()}).json()
    assert unchanged["posted"] == 0 and unchanged["reversed"] == 0
    refused = client.post(f"{API}/close", headers=OWNER, json={"through": end.isoformat()})
    assert refused.status_code == 409 and refused.json()["code"] == "period_not_ready" and refused.json()["issues"] == 1
    # A payable in USD makes the month a net loss: the earlier revaluation is replaced, not added to.
    supplier = vendor("Synthetic USD supplier")["id"]
    assert bill(supplier, subtotal="200", tax="0", currency="USD", bill_date=issued.isoformat()).status_code == 201
    rate("AED", "1.03", end)
    replaced = client.post(f"{API}/ledger/revaluation", headers=OWNER, json={"period_end": end.isoformat()}).json()
    assert replaced["reversed"] == 2 and replaced["posted"] == 2 and replaced["missing"] == []
    usd = next(item for item in entries(source_kind="revaluation") if item["entry_date"] == end.isoformat() and item["status"] == "posted"
               and any(line["account_id"] == chart()["ap"]["id"] for line in item["lines"]))
    assert lines_of(usd) == {("ar", "5.00", "0.00"), ("ap", "0.00", "10.00"), ("fx_unrealised", "5.00", "0.00")}
    closed = client.post(f"{API}/close", headers=OWNER, json={"through": end.isoformat()})
    assert closed.status_code == 200, closed.text
    assert client.post(f"{API}/ledger/revaluation", headers=OWNER, json={"period_end": end.isoformat()}).json()["code"] == "period_locked"
    assert client.post(f"{API}/ledger/revaluation", headers=MEMBER, json={"period_end": end.isoformat()}).status_code == 403


def test_settling_a_loan_in_another_currency_realises_the_exchange_difference() -> None:
    setup_books()
    drawn, settled = today() - timedelta(days=10), today() - timedelta(days=2)
    rate("USD", "3.60", drawn)
    rate("USD", "3.70", settled)
    made = loan(principal="1000", currency="USD", annual_rate="0", start_date=drawn.isoformat())
    assert repay(made["id"], "1000", settled).status_code == 201
    adjust(kind="loan_received", amount="500", currency="USD", entry_date=drawn.isoformat(), description="Synthetic USD loan in")
    adjust(kind="loan_repayment", amount="500", currency="USD", entry_date=settled.isoformat(), description="Synthetic USD loan out")
    module = entries(source_kind="loan_repayment")[0]
    assert lines_of(module) == {("loans", "3600.00", "0.00"), ("bank", "0.00", "3700.00"), ("fx", "100.00", "0.00")}
    carried = next(line for line in module["lines"] if line["debit"] == "3600.00")
    assert carried["rate"] == "3.6" and carried["rate_source"].startswith("Carrying rate")
    legacy = next(item for item in entries(source_kind="adjustment") if any(line["credit"] == "1850.00" for line in item["lines"]))
    assert lines_of(legacy) == {("loans", "1800.00", "0.00"), ("bank", "0.00", "1850.00"), ("fx", "50.00", "0.00")}
    data = statements()
    assert balance(data, "loans") == "0.00" and data["pnl"]["fx_differences"] == "-150.00" and data["balance_sheet"]["balanced"]
    assert post()["posted"] == 0


# -- the group balance sheet ---------------------------------------------------------------------------------------------------------


def test_the_group_balance_sheet_eliminates_intercompany_and_shows_a_translation_reserve() -> None:
    setup_books()
    setup_books(OTHER_OWNER, base_currency="USD")
    earlier = today() - timedelta(days=3)
    rate("USD", "3.75", earlier, headers=OTHER_OWNER)
    rate("USD", "3.80", today(), headers=OTHER_OWNER)
    adjust(kind="owner_contribution", amount="1000", entry_date=earlier.isoformat(), description="Synthetic capital")
    adjust(OTHER_OWNER, kind="owner_contribution", amount="1000", currency="USD", entry_date=earlier.isoformat(), description="Synthetic capital")
    lent = client.post(f"{API}/intercompany", headers=OWNER, json={"workspace_id": OTHER, "kind": "loan", "direction": "receivable",
                                                                    "entry_date": (today() - timedelta(days=1)).isoformat(), "currency": "SAR", "amount": "375",
                                                                    "description": "Synthetic group loan"})
    assert lent.status_code == 201, lent.text
    post()
    post(OTHER_OWNER)
    response = client.get(f"{API}/group/balance-sheet", headers=OWNER, params={"currency": "SAR"})
    assert response.status_code == 200, response.text
    data = response.json()
    rows = {row["key"]: row for row in data["rows"]}
    assert data["balanced"] and data["complete"] and data["included"] == [data["businesses"][0]["workspace_id"], OTHER]
    assert rows["translation_reserve"]["businesses"][OTHER] == "50.00"
    assert rows["current_assets"]["eliminations"] == "-375.00" and rows["current_liabilities"]["eliminations"] == "-380.00"
    assert rows["intercompany_difference"]["total"] == "5.00"
    assert rows["total_assets"]["total"] == "4805.00" and rows["liabilities_and_equity"]["total"] == "4805.00"
    assert data["rates"][0]["workspace_id"] == OTHER and data["rates"][0]["rate"] == "3.8"
    # In US dollars this business has no rate of its own: it is listed as not converted, never summed in part.
    usd = client.get(f"{API}/group/balance-sheet", headers=OWNER, params={"currency": "USD"}).json()
    assert usd["included"] == [OTHER] and not usd["complete"] and usd["not_converted"][0]["currency"] == "SAR"
    assert client.get(f"{API}/group/balance-sheet", headers=MEMBER).status_code == 403
    refused = client.get(f"{API}/group/balance-sheet", headers=OWNER, params={"workspace": ["ws_someone_else"]})
    assert refused.status_code == 403 and refused.json()["code"] == "workspace_not_allowed"


# -- isolation -------------------------------------------------------------------------------------------------------------------------


def test_another_business_or_tenant_never_reads_assets_or_loans() -> None:
    setup_books()
    setup_books(OTHER_OWNER)
    made = capital_bill("1200", 12, today())
    asset = asset_of(made["id"])
    borrowed = loan()
    assert client.get(f"{API}/assets/{asset['id']}", headers=OTHER_OWNER).status_code == 404
    assert client.get(f"{API}/loans/{borrowed['id']}", headers=OTHER_OWNER).status_code == 404
    assert client.get(f"{API}/assets", headers=OTHER_OWNER).json()["items"] == []
    stranger = {**OWNER, "x-tenant-id": "tenant_elsewhere"}
    assert client.get(f"{API}/loans/{borrowed['id']}", headers=stranger).status_code in (403, 404, 409)  # no books there: nothing read
    assert client.get(f"{API}/assets", headers={**OWNER, "x-user-id": "viewer_books", "x-user-roles": "viewer"}).status_code == 403


# -- the pure rules ----------------------------------------------------------------------------------------------------------------------


def _asset(**fields: Any) -> Asset:
    now = utc_now()
    base = {"id": "basset_x", "tenant_id": TENANT, "workspace_id": "ws", "created_at": now, "source_kind": "bill", "source_id": "b1", "name": "Synthetic",
            "acquired_on": date(2026, 6, 15), "currency": "SAR", "cost": Decimal(1000), "useful_life_months": 10, "depreciation_start": date(2026, 6, 1),
            "created_by": "u", "updated_at": now}
    return Asset(**{**base, **fields})


def test_pure_straight_line_spreads_exactly_and_stops_at_a_disposal() -> None:
    asset = _asset(cost=Decimal(1000), residual_value=Decimal(100), useful_life_months=7)
    parts = [row.amount for row in A.schedule(asset)]
    assert sum(parts) == Decimal(900) and parts[:6] == [Decimal("128.57")] * 6 and parts[-1] == Decimal("128.58")
    sold = asset.model_copy(update={"status": "disposed", "disposed_on": date(2026, 9, 10), "disposal_kind": "sale", "proceeds": Decimal(700)})
    assert [row.month for row in A.schedule(sold)] == [date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)]
    figures = A.disposal(sold, [])
    assert figures is not None and figures["net_book_value"] == Decimal("614.29") and figures["gain"] == Decimal("85.71")
    loss = AssetImpairment(id="bimp", tenant_id=TENANT, workspace_id="ws", created_at=utc_now(), asset_id=asset.id, entry_date=date(2026, 7, 20),
                           amount=Decimal(200), currency="SAR", reason="Synthetic", status="approved", requested_by="a", decided_by="b")
    assert A.net_book_value(asset, [loss], date(2026, 7, 31)) == Decimal(1000) - Decimal("257.14") - 200


def test_pure_loan_interest_follows_the_days_of_a_period() -> None:
    now = utc_now()
    item = Loan(id="bloan", tenant_id=TENANT, workspace_id="ws", created_at=now, lender="Synthetic", principal=Decimal(1200), currency="SAR",
                annual_rate=Decimal(12), start_date=date(2026, 1, 31), term_months=3, created_by="u", updated_at=now)
    assert [LN.due_date(item, n) for n in (1, 2, 3)] == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert LN.accrued(item, Decimal(1200), date(2026, 1, 31), date(2026, 2, 28)) == Decimal("12.00")
    halfway = LN.accrued(item, Decimal(1200), date(2026, 2, 28), date(2026, 3, 14))
    assert halfway == q(Decimal(1200) * Decimal("0.01") * 14 / 31)
    paid = LoanRepayment(id="r1", tenant_id=TENANT, workspace_id="ws", created_at=now, loan_id="bloan", paid_on=date(2026, 2, 28), amount=Decimal(5),
                         currency="SAR", interest=Decimal(5), principal=Decimal(0), created_by="u")
    state, splits = LN.replay(item, [paid])
    assert splits[0].interest == Decimal(5) and state.unpaid_interest == Decimal(7) and state.balance == Decimal(1200)
    assert LN.owed_on(item, [paid], date(2026, 3, 31)) == (Decimal(1200), Decimal(19))
