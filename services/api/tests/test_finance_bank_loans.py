"""Bank and finance loans on a Debt: the schedule, the payments found for it, accept and undo, salary deductions and the
balance every reader counts (docs/finance.md#bank-loans), in-memory stores. Every record is SYNTHETIC and low entropy.

``test_postgres_finance_bank_loans.py`` repeats the storage checks under RLS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import bank_loans as BL
from anum_api.finance import commitment_math as M
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.models import Debt, DebtKind
from anum_api.finance.personal_store import personal_memory_store
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import utc_now

API = "/api/v1/finance"
OWNER = {"x-tenant-id": "tenant_loans", "x-workspace-id": "ws_loans", "x-user-id": "owner_loans", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-user-id": "someone_else"}
VIEWER = {**OWNER, "x-user-roles": "viewer"}
client = TestClient(main.app)
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    for store in (finance_store, sms_memory_store, links_memory_store, personal_memory_store, rules_memory_store):
        store.clear()
    yield
    for store in (finance_store, sms_memory_store, links_memory_store, personal_memory_store, rules_memory_store):
        store.clear()


def _debt(**over: object) -> Debt:
    values: dict = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u", "id": "fin_debt_x", "name": "Car loan", "kind": DebtKind.LOAN,
                    "balance": Decimal(1), "currency": "SAR", "created_at": NOW, "updated_at": NOW, "loan_start_on": date(2024, 10, 15),
                    "loan_amount": Decimal(24000), "loan_term_months": 24, "loan_payment_day": 27}
    values.update(over)
    return Debt(**values)


# -- the schedule maths --------------------------------------------------------------------------------------------


def test_a_reducing_rate_loan_uses_the_annuity_and_the_last_instalment_takes_what_is_left() -> None:
    schedule = BL.build(_debt(loan_rate=Decimal(6), loan_rate_type="reducing"))
    assert schedule is not None and schedule.basis == "reducing" and schedule.installment_from == "computed"
    # 24,000 over 24 months at 6 percent: 1,063.70 a month (the annuity rounded up), 120.00 interest in the first month.
    assert schedule.installment == Decimal("1063.70") and len(schedule.rows) == 24
    first, last = schedule.rows[0], schedule.rows[-1]
    assert (first.due_on, first.interest, first.principal, first.balance_after) == (date(2024, 11, 27), Decimal("120.00"), Decimal("943.70"), Decimal("23056.30"))
    assert last.balance_after == Decimal("0.00") and sum(r.principal or 0 for r in schedule.rows) == Decimal("24000.00")
    assert schedule.total_interest == Decimal("1528.65") and schedule.effective_rate == Decimal("6.0000")


def test_a_flat_rate_loan_spreads_the_profit_evenly_like_the_planner() -> None:
    schedule = BL.build(_debt(loan_rate=Decimal("3.5"), loan_rate_type="flat"))
    assert schedule is not None and schedule.basis == "flat"
    # Profit 24,000 x 3.5 percent x 2 years = 1,680: 1,070 a month, 1,000 principal and 70 profit.
    assert {(r.payment, r.principal, r.interest) for r in schedule.rows} == {(Decimal("1070.00"), Decimal("1000.00"), Decimal("70.00"))}
    assert schedule.total_interest == Decimal("1680.00") and schedule.total_payable == Decimal("25680.00")
    # The reducing rate the same instalments give, for the payoff planner (higher than the flat rate).
    assert schedule.effective_rate is not None and Decimal("6.5") < schedule.effective_rate < Decimal("6.7")


def test_the_lenders_instalment_wins_and_no_rate_is_never_invented() -> None:
    lender = BL.build(_debt(loan_rate=Decimal(6), loan_installment=Decimal(1070)))
    assert lender is not None and lender.installment == Decimal("1070.00") and lender.installment_from == "lender"
    assert lender.rows[0].payment == Decimal("1070.00") and lender.rows[-1].payment < Decimal(1070)
    plain = BL.build(_debt())  # no rate, no instalment: the amount over the term, the split unknown
    assert plain is not None and plain.basis == "plain" and not plain.split_known and plain.effective_rate is None
    assert plain.installment == Decimal("1000.00") and plain.rows[0].interest is None and plain.total_interest is None
    known = BL.build(_debt(loan_installment=Decimal(1100)))  # the lender's instalments, no rate: what is left is the instalments
    assert known is not None and known.total_payable == Decimal("26400.00") and known.effective_rate is None
    assert BL.build(_debt(loan_term_months=None)) is None  # no term and no instalment: no schedule
    never = BL.build(_debt(loan_term_months=None, loan_rate=Decimal(12), loan_installment=Decimal(100)))
    assert never is not None and never.never_ends and never.rows == ()


def test_payments_cover_the_nearest_instalment_and_the_balance_follows_the_principal() -> None:
    debt = _debt(loan_rate=Decimal(6))
    schedule = BL.build(debt)
    assert schedule is not None
    paid = [BL.Paid(on=r.due_on, amount=r.payment, kind="link", ref=f"c{r.number}") for r in schedule.rows[:3]]
    view = BL.figures(debt, schedule, paid, date(2025, 3, 10))
    assert [r.status for r in view.rows[:5]] == ["paid", "paid", "paid", "missed", "upcoming"]
    assert view.balance == schedule.rows[2].balance_after and view.paid_count == 3 and view.due_count == 4 and view.months_ahead == -1
    assert view.interest_paid == sum(r.interest or 0 for r in schedule.rows[:3])
    closed = BL.figures(debt.model_copy(update={"loan_status": "paid_off", "loan_closed_on": date(2025, 3, 1)}), schedule, paid, date(2025, 3, 10))
    assert closed.balance == 0 and closed.payoff_on == date(2025, 3, 1) and closed.rows[5].status == "settled"


# -- the API ---------------------------------------------------------------------------------------------------------


def _months_ago(months: int, day: int = 15) -> date:
    today = utc_now().date()
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, day)


def _account(**body: object) -> dict:
    made = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert made.status_code == 201, made.text
    return made.json()


def _pay(account_id: str, amount: str, on: date, description: str) -> str:
    made = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": account_id, "posted_on": on.isoformat(), "amount": amount,
                                                                    "description": description, "category": "transfer"})
    assert made.status_code == 201, made.text
    entries = client.get(f"{API}/ledger/entries?limit=200", headers=OWNER).json()["items"]
    return next(e["canonical_id"] for e in entries if e["source_ref"] == made.json()["id"])


def _loan(account_id: str | None, **terms: object) -> tuple[dict, dict]:
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Example car loan", "kind": "loan", "currency": "SAR", "balance": "99999"}).json()
    body = {"lender": "Example Bank", "start_on": _months_ago(26).isoformat(), "amount": "24000", "term_months": 24, "payment_day": 27,
            "repayment": "bank_debit", "account_id": account_id, **terms}
    made = client.put(f"{API}/debts/{debt['id']}/loan", headers=OWNER, json=body)
    assert made.status_code == 200, made.text
    return debt, made.json()


def _loan_suggestion(debt_id: str) -> dict | None:
    items = client.get(f"{API}/suggestions?kind=loan_payments&limit=100", headers=OWNER).json()["items"]
    return next((s for s in items if s["params"]["debt_id"] == debt_id), None)


def test_a_paid_loan_is_found_month_by_month_accepted_at_once_and_undone() -> None:
    current = _account()
    debt, view = _loan(current["id"])
    rows = view["rows"]
    assert len(rows) == 24 and view["totals"]["installment"] == "1000.00" and view["totals"]["basis"] == "plain"
    # Typed balance 99,999 is not what is owed: with nothing matched yet the whole amount is.
    assert view["totals"]["balance"] == "24000.00" and view["totals"]["paid_count"] == 0
    canonical: dict[int, str] = {}
    for row in rows:
        on = date.fromisoformat(row["due_on"])
        if row["number"] == 5:
            continue  # a missed month
        if row["number"] == 9:  # two debits that both fit and name no one: ambiguous
            canonical[9] = _pay(current["id"], "-1000", on, "Standing order 1001")
            _pay(current["id"], "-1000", on.replace(day=on.day - 1), "Standing order 1002")
            continue
        canonical[row["number"]] = _pay(current["id"], "-1000", on, "Example Bank instalment")
    _pay(current["id"], "-1000", date.fromisoformat(rows[2]["due_on"]), "Other shop")  # names another merchant: never a candidate
    item = _loan_suggestion(debt["id"])
    assert item is not None and item["kind"] == "loan_payments" and item["action"]["kind"] == "link_batch"
    assert (item["params"]["found"], item["params"]["missing"], item["params"]["ambiguous"]) == ("22", "1", "1")
    states = {m["number"]: m["state"] for m in item["months"]}
    assert states[5] == "missing" and states[9] == "ambiguous" and states[1] == "found"
    # Nothing is linked before the accept.
    assert client.get(f"{API}/debt-payments", headers=OWNER).json()["total"] == 0
    assert client.post(f"{API}/suggestions/accept", headers=VIEWER, json={"key": item["key"], "payload_hash": item["payload_hash"]}).status_code == 403
    accepted = client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": item["key"], "payload_hash": item["payload_hash"]})
    assert accepted.status_code == 200, accepted.text
    batch_id = accepted.json()["loan_batch_id"]
    assert batch_id and client.get(f"{API}/debt-payments", headers=OWNER).json()["total"] == 22
    loan = client.get(f"{API}/debts/{debt['id']}/loan", headers=OWNER).json()
    assert loan["totals"]["paid_count"] == 22 and loan["totals"]["balance"] == "2000.00" and loan["totals"]["months_ahead"] == -2
    by_number = {r["number"]: r for r in loan["rows"]}
    assert by_number[1]["status"] == "paid" and by_number[1]["payments"][0]["ref"] == canonical[1]
    assert by_number[5]["status"] == "missed" and by_number[9]["status"] == "ambiguous" and len(by_number[9]["candidates"]) == 2
    # The ambiguous month waits for a pick: the person picks one debit.
    picked = client.post(f"{API}/debts/{debt['id']}/payments", headers=OWNER, json={"canonical_id": canonical[9]})
    assert picked.status_code == 201, picked.text
    assert client.get(f"{API}/debts/{debt['id']}/loan", headers=OWNER).json()["totals"]["paid_count"] == 23
    assert _loan_suggestion(debt["id"]) is None  # nothing new to find
    # Undo: every link of the batch is voided; the pick stays; the suggestion comes back.
    assert client.post(f"{API}/loan-batches/{batch_id}/undo", headers=OTHER).status_code == 404
    undone = client.post(f"{API}/loan-batches/{batch_id}/undo", headers=OWNER)
    assert undone.status_code == 200 and undone.json()["status"] == "undone"
    assert client.get(f"{API}/debt-payments", headers=OWNER).json()["total"] == 1
    assert client.get(f"{API}/debts/{debt['id']}/loan", headers=OWNER).json()["totals"]["paid_count"] == 1
    back = _loan_suggestion(debt["id"])
    assert back is not None and back["params"]["found"] == "22"


def test_the_schedule_balance_replaces_the_typed_one_everywhere_and_is_counted_once() -> None:
    current = _account(opening_balance="50000")
    debt, view = _loan(current["id"], rate="6", rate_type="reducing", start_on=_months_ago(4).isoformat())
    paid_rows = [r for r in view["rows"] if date.fromisoformat(r["due_on"]) <= utc_now().date()]
    for row in paid_rows:
        made = client.post(f"{API}/debts/{debt['id']}/payments", headers=OWNER,
                           json={"canonical_id": _pay(current["id"], f"-{row['payment']}", date.fromisoformat(row["due_on"]), "Example Bank instalment")})
        assert made.status_code == 201, made.text
    loan = client.get(f"{API}/debts/{debt['id']}/loan", headers=OWNER).json()
    owed = Decimal(loan["totals"]["balance"])
    assert owed == Decimal(paid_rows[-1]["balance_after"])
    summary = client.get(f"{API}/summary", headers=OWNER).json()
    part = next(p for p in summary["net_worth_parts"] if p["currency"] == "SAR")
    assert Decimal(part["debts"]) == owed  # never the typed 99,999, never twice
    payoff = client.get(f"{API}/planner/payoff?currency=SAR&minimums_only=true", headers=OWNER).json()["currencies"][0]
    mine = next(d for d in payoff["debts"] if d["id"] == debt["id"])
    assert Decimal(mine["balance"]) == owed and Decimal(mine["minimum"]) == Decimal(loan["totals"]["installment"])
    listed = client.get(f"{API}/loans", headers=OWNER).json()["items"]
    assert [(i["debt_id"], i["totals"]["balance"]) for i in listed] == [(debt["id"], loan["totals"]["balance"])]
    # Paid off early: it owes nothing, stays in the list with its record, and leaves the plan.
    closed = client.put(f"{API}/debts/{debt['id']}/loan", headers=OWNER, json={**loan["terms"], "status": "paid_off", "closed_on": utc_now().date().isoformat(),
                                                                               "amount": "24000", "rate": "6"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["totals"]["balance"] == "0.00" and closed.json()["totals"]["status"] == "paid_off"
    assert client.get(f"{API}/loans", headers=OWNER).json()["items"][0]["totals"]["status"] == "paid_off"
    after = next(p for p in client.get(f"{API}/summary", headers=OWNER).json()["net_worth_parts"] if p["currency"] == "SAR")
    assert Decimal(after["debts"]) == 0
    # Removing the terms makes the typed balance count again.
    assert client.delete(f"{API}/debts/{debt['id']}/loan", headers=OWNER).status_code == 204
    again = next(p for p in client.get(f"{API}/summary", headers=OWNER).json()["net_worth_parts"] if p["currency"] == "SAR")
    assert Decimal(again["debts"]) > 90000


def test_salary_deductions_are_found_from_payslips_and_the_profile_and_never_counted_twice() -> None:
    current = _account()
    start = _months_ago(4)
    debt, view = _loan(None, repayment="salary_deduction", start_on=start.isoformat(), installment="1000", term_months=24, lender="Example Bank")
    first_month = view["rows"][0]["due_on"][:7]
    profile = client.put(f"{API}/salary/profile", headers=OWNER, json={
        "employer": "Example Employer", "joined_on": _months_ago(30).isoformat(), "currency": "SAR", "expected_gross": "15000", "pay_day": 27,
        "account_id": current["id"], "rules": [{"kind": "deduction", "reason": "loan", "label": "Example Bank", "amount": "1000", "starts": first_month}]})
    assert profile.status_code == 200, profile.text
    # The first month's payslip shows the deduction; the second month's payslip shows none (a missing month).
    second_month = view["rows"][1]["due_on"][:7]
    for month, deductions in ((first_month, [{"kind": "loan", "amount": "1000", "label": "Example Bank"}]), (second_month, [])):
        slip = client.post(f"{API}/payslips", headers=OWNER, json={"employer": "Example Employer", "month": month, "currency": "SAR", "gross": "15000",
                                                                    "deductions": deductions})
        assert slip.status_code == 201, slip.text
    item = _loan_suggestion(debt["id"])
    assert item is not None
    months = {m["number"]: m for m in item["months"]}
    assert months[1]["state"] == "found" and months[1]["source"] == "payslip" and months[2]["state"] == "missing"
    assert all(m["source"] == "salary_rule" for n, m in months.items() if n > 2 and m["state"] == "found")
    assert item["action"]["links"] == [] and len(item["action"]["loan_months"]) == int(item["params"]["found"])
    accepted = client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": item["key"], "payload_hash": item["payload_hash"]})
    assert accepted.status_code == 200, accepted.text
    loan = client.get(f"{API}/debts/{debt['id']}/loan", headers=OWNER).json()
    assert loan["totals"]["paid_count"] == int(item["params"]["found"]) and loan["totals"]["from_salary"] is True
    # The salary's expected net already takes the instalment: no commitment of its own, never a second minimum.
    fixed = client.get(f"{API}/fixed-commitments", headers=OWNER).json()
    assert all(c["ref_id"] != debt["id"] for c in fixed["items"])
    assert any(c["ref_id"] == debt["id"] and c["note"] == "from_salary" for c in fixed["not_counted"])
    safe = client.get(f"{API}/safe-to-spend", headers=OWNER).json()
    assert all(line.get("ref_id") != debt["id"] for row in safe["currencies"] for line in row.get("lines", []))
    forecast = client.get(f"{API}/forecast", headers=OWNER).json()
    assert all(e.get("ref_id") != debt["id"] for c in forecast["currencies"] for e in c["events"])


def test_terms_are_checked_and_other_people_and_viewers_are_refused() -> None:
    current = _account()
    debt, _view = _loan(current["id"])
    base = {"start_on": _months_ago(3).isoformat(), "amount": "5000", "term_months": 12, "repayment": "manual"}
    path = f"{API}/debts/{debt['id']}/loan"
    assert client.put(path, headers=OWNER, json={**base, "term_months": None}).status_code == 422  # no term and no instalment
    assert client.put(path, headers=OWNER, json={**base, "repayment": "bank_debit"}).status_code == 422  # no account
    assert client.put(path, headers=OWNER, json={**base, "rate": "0"}).status_code == 422  # a rate is more than 0 or empty
    assert client.put(path, headers=OWNER, json={**base, "status": "closed"}).status_code == 422  # closed needs its date
    usd = _account(name="Dollars", currency="USD")
    assert client.put(path, headers=OWNER, json={**base, "repayment": "bank_debit", "account_id": usd["id"]}).status_code == 422
    assert client.put(path, headers=VIEWER, json=base).status_code == 403
    assert client.put(path, headers=OTHER, json=base).status_code == 404
    assert client.get(path, headers=OTHER).status_code == 404
    assert client.get(f"{API}/loans", headers=OTHER).json()["items"] == []
    assert client.delete(path, headers=VIEWER).status_code == 403
    assert client.get(path, headers=VIEWER).status_code == 403  # personal finance is the owner's own


def test_accepting_the_found_payments_is_never_automated() -> None:
    from anum_api.finance.rules import AutomationRefused, require_automatable

    with pytest.raises(AutomationRefused):
        require_automatable("link_loan_payments")
    assert "link_loan_payments" in client.get(f"{API}/automation", headers=OWNER).json()["never_automated"]


def test_a_salary_loan_keeps_its_planner_payment() -> None:
    from anum_api.finance.obligations import LoanDebt

    debt = LoanDebt(**_debt(minimum_payment=Decimal(1000), balance=Decimal(5000)).model_dump(), from_salary=True)
    assert M.debt_minimum(debt) == 0 and M.plan_minimum(debt) == Decimal(1000)
