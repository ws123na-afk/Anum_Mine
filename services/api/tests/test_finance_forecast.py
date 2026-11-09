"""The cash-flow forecast and the emergency reserve (docs/finance.md#forecast, #emergency-reserve).

Pure checks of ``finance/forecast.py`` on a fixed day (9 November 2026), then the routes and the Suggestions
producer with the in-memory stores. Every record is SYNTHETIC: fake accounts, employers and amounts.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import forecast as F
from anum_api.finance import obligations as O
from anum_api.finance import personal as P
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.models import (
    Account, AccountKind, Bill, Cadence, Category, CategorySource, Debt, DebtKind, Goal, GoalPurpose, InstallmentStatementEntry, Transaction,
)
from anum_api.finance.personal_store import personal_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store

SCOPE = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u"}
STAMP = datetime(2026, 1, 1, tzinfo=UTC)
TODAY = date(2026, 11, 9)
_seq = itertools.count(1)


def account(id: str = "bank", currency: str = "SAR", kind: AccountKind = AccountKind.BANK, opening: str = "0") -> Account:
    return Account(id=id, name=id.title(), kind=kind, currency=currency, opening_balance=D(opening), created_at=STAMP, updated_at=STAMP, **SCOPE)


def txn(day: date, amount: str, description: str = "Example grocer", category: Category = Category.GROCERIES, account_id: str = "bank",
        currency: str = "SAR") -> Transaction:
    n = next(_seq)
    return Transaction(id=f"tx{n}", account_id=account_id, posted_on=day, amount=D(amount), currency=currency, description=description,
                       category=category, category_source=CategorySource.RULE, dedupe_hash=f"h{n}", created_at=STAMP, updated_at=STAMP, **SCOPE)


def bill(name: str = "Rent", amount: str = "2500", due: date = date(2026, 11, 20), currency: str = "SAR", category: Category = Category.HOUSING) -> Bill:
    return Bill(id=f"bill_{name}", name=name, amount=D(amount), currency=currency, cadence=Cadence.MONTHLY, next_due_on=due, category=category,
                created_at=STAMP, updated_at=STAMP, **SCOPE)


def debt(name: str = "Car loan", minimum: str = "900", due_day: int | None = 25, balance: str = "20000", kind: DebtKind = DebtKind.LOAN) -> Debt:
    return Debt(id=f"debt_{name}", name=name, kind=kind, balance=D(balance), currency="SAR", minimum_payment=D(minimum), due_day=due_day,
                created_at=STAMP, updated_at=STAMP, **SCOPE)


def profile(**over: object) -> P.SalaryProfile:
    values: dict = {"id": "sp", "employer": "Example Trading Co", "joined_on": date(2025, 1, 10), "currency": "SAR", "expected_gross": D("10000"),
                    "pay_day": 27, "account_id": "bank", "created_at": STAMP, "updated_at": STAMP, **SCOPE}
    values.update(over)
    return P.SalaryProfile(**values)


def inputs(**over: object) -> F.ForecastInputs:
    values: dict = {"today": TODAY, "timezone": "Asia/Riyadh", "base_currency": "SAR", "accounts": [account()], "balances": {"bank": D("5000")},
                    "transactions": [], "bills": [], "debts": [], "goals": []}
    values.update(over)
    return F.ForecastInputs(**values)


def sar(result: F.Forecast) -> F.CurrencyForecast:
    return next(c for c in result.currencies if c.currency == "SAR")


def point(row: F.CurrencyForecast, day: date) -> F.ForecastPoint:
    return next(p for p in row.points if p.on == day)


# -- the forecast ------------------------------------------------------------------------------------------


def test_the_forecast_starts_from_today_s_cash_and_runs_to_the_month_end_and_three_months() -> None:
    row = sar(F.build(inputs(accounts=[account(), account("card", kind=AccountKind.CARD)], balances={"bank": D("5000"), "card": D("-800")})))
    assert row.actual == D("5000.00") and row.accounts_counted == 1  # the card is not cash
    assert row.points[0].on == TODAY and row.points[-1].on == date(2027, 1, 31)
    assert [m.month for m in row.months] == ["2026-11", "2026-12", "2027-01"] and row.month_end.on == date(2026, 11, 30)
    # Nothing coming and no history: the line stays flat and says spending is unknown.
    assert row.month_end.expected == D("5000.00") and any(a.code == "spend_none" for a in row.assumptions)


def test_the_salary_arrives_on_pay_day_as_the_expected_net() -> None:
    p = profile(rules=[P.SalaryRule(id="r1", kind=P.SalaryRuleKind.DEDUCTION, amount=D("450"), starts="2025-01", reason=P.SalaryDeductionReason.SOCIAL_INSURANCE),
                       P.SalaryRule(id="r2", kind=P.SalaryRuleKind.ADVANCE, amount=D("500"), starts="2026-11", total=D("1000"), received_on=date(2026, 10, 1))])
    row = sar(F.build(inputs(salary_profile=p)))
    salary = [e for e in row.events if e.kind == "salary"]
    # 10,000 less 450 GOSI less 500 advance repayment in November and December; January has no advance left.
    assert [(e.on, e.amount) for e in salary] == [(date(2026, 11, 27), D("9050.00")), (date(2026, 12, 27), D("9050.00")), (date(2027, 1, 27), D("9550.00"))]
    assert point(row, date(2026, 11, 26)).expected == D("5000.00") and point(row, date(2026, 11, 27)).expected == D("14050.00")


def test_a_salary_already_received_this_month_is_not_counted_twice() -> None:
    p = profile(pay_day=5)
    received = txn(date(2026, 11, 5), "10000", "Example Trading Co salary", Category.INCOME)
    row = sar(F.build(inputs(salary_profile=p, transactions=[received])))
    assert [e.on.month for e in row.events if e.kind == "salary"] == [12, 1]
    assert any(a.code == "salary_received" for a in row.assumptions)


def test_a_missing_salary_is_named_and_not_counted() -> None:
    row = sar(F.build(inputs(salary_profile=profile(pay_day=1))))
    assert [e.on for e in row.events if e.kind == "salary"] == [date(2026, 12, 1), date(2027, 1, 1)]
    assert [a.code for a in row.left_out] == ["salary_missing"]


def test_bills_overdue_minimums_and_installments_come_off_on_their_days() -> None:
    entries = [
        InstallmentStatementEntry(provider="Tamara", order_id="o1", merchant="Example Shop", due_on=date(2026, 11, 15), installment=2, total_installments=4,
                                  amount=D("250"), currency="SAR", source_status="scheduled"),
        InstallmentStatementEntry(provider="Tamara", order_id="o1", merchant="Example Shop", due_on=date(2026, 10, 15), installment=1, total_installments=4,
                                  amount=D("250"), currency="SAR", source_status="paid"),
    ]
    row = sar(F.build(inputs(bills=[bill()], debts=[debt(due_day=3), debt("Card", "300", 20, "1000", DebtKind.CARD)], installments=entries)))
    events = {(e.kind, e.label, e.on): (e.amount, e.status) for e in row.events if e.on.month == 11}
    # The car loan's day (3rd) passed with no matched payment: overdue, taken off today.
    assert events[("minimum_payment", "Car loan", TODAY)] == (D("-900"), "overdue")
    assert events[("minimum_payment", "Card", date(2026, 11, 20))] == (D("-300"), "expected")
    assert events[("bill", "Rent", date(2026, 11, 20))] == (D("-2500"), "expected")
    assert events[("installment", "Tamara: Example Shop", date(2026, 11, 15))] == (D("-250"), "expected")
    assert point(row, TODAY).expected == D("4100.00")
    assert row.month_end.expected == D("5000.00") - 900 - 250 - 2500 - 300
    # The card's minimum stops once what it owes is paid: 1,000 at 300 a month is 300, 300 and 300 in the horizon.
    assert sum(e.amount for e in row.events if e.label == "Card") == D("-900")


def test_a_minimum_paid_this_month_is_not_taken_off_again() -> None:
    paid = [O.DebtPayment(debt_id="debt_Car loan", amount=D("900"), currency="SAR", posted_on=date(2026, 11, 3), matched_at=STAMP)]
    row = sar(F.build(inputs(debts=[debt(due_day=3)], debt_payments=paid)))
    assert [e.on for e in row.events if e.kind == "minimum_payment"] == [date(2026, 12, 3), date(2027, 1, 3)]


def test_regular_remittances_and_recurring_income_with_confidence() -> None:
    remit = P.Remittance(id="rm1", sent_on=date(2026, 10, 12), amount=D("1000"), currency="SAR", fee=D("15"), recipient="Example Family", provider="Example Exchange",
                         regular=True, created_at=STAMP, updated_at=STAMP, **SCOPE)
    rent_in = [txn(date(2026, m, 2), "1500", "Example tenant rent", Category.INCOME) for m in (8, 9, 10, 11)]
    once = [txn(date(2026, 10, 20), "700", "Example one-off refund deposit", Category.INCOME)]
    row = sar(F.build(inputs(remittances=[remit], transactions=rent_in + once)))
    remits = [(e.on, e.amount) for e in row.events if e.kind == "remittance"]
    assert remits[0] == (date(2026, 11, 12), D("-1015"))
    income = [(e.on, e.amount) for e in row.events if e.kind == "income"]
    assert income == [(date(2026, 12, 2), D("1500.00")), (date(2027, 1, 2), D("1500.00"))]
    # The low line leaves the other income out; the expected line has it.
    december = row.months[1]
    assert december.low < december.expected


def test_spending_outside_bills_is_taken_off_daily_with_a_band() -> None:
    rows = []
    for month, spent in ((8, "3043.75"), (9, "6087.50"), (10, "4565.63")):
        rows.append(txn(date(2026, month, 1), "10", "Example opening", Category.INCOME))
        rows.append(txn(date(2026, month, 10), f"-{spent}"))
    rows.append(txn(date(2026, 7, 28), "1", "Example older", Category.INCOME))
    row = sar(F.build(inputs(transactions=rows, balances={"bank": D("20000")})))
    assert row.spend.basis == "months" and row.spend.months == ["2026-08", "2026-09", "2026-10"]
    assert row.spend.daily_low == D("100.00") and row.spend.daily_high == D("200.00")
    tomorrow = point(row, TODAY + timedelta(days=1))
    assert point(row, TODAY).expected == D("20000.00") and tomorrow.expected == D("20000.00") - row.spend.daily
    assert tomorrow.low == D("19800.00") and tomorrow.high == D("19900.00")


def test_never_mixes_currencies() -> None:
    accounts = [account(), account("usd", "USD")]
    row_inputs = inputs(accounts=accounts, balances={"bank": D("1000"), "usd": D("50")}, bills=[bill("Hosting", "80", date(2026, 11, 20), "USD")])
    result = F.build(row_inputs)
    by = {c.currency: c for c in result.currencies}
    assert set(by) == {"SAR", "USD"}
    assert by["SAR"].month_end.expected == D("1000.00") and by["USD"].month_end.expected == D("-30.00")
    assert [w.kind for w in by["USD"].warnings] == ["below_zero"] and by["SAR"].warnings == []


def test_below_reserve_warning_with_the_date() -> None:
    reserve = Goal(id="g1", name="Emergency", target_amount=D("3000"), saved_amount=D("0"), currency="SAR", purpose=GoalPurpose.EMERGENCY,
                   created_at=STAMP, updated_at=STAMP, **SCOPE)
    row = sar(F.build(inputs(bills=[bill()], goals=[reserve])))
    warning = next(w for w in row.warnings if w.kind == "below_reserve")
    assert (warning.on, warning.balance, warning.threshold) == (date(2026, 11, 20), D("2500.00"), D("3000.00"))
    # The rent comes again each month: zero is reached on 20 December and passed on 20 January.
    assert row.reserve_target == D("3000.00") and [(w.kind, w.on) for w in row.warnings if w.kind == "below_zero"] == [("below_zero", date(2027, 1, 20))]


# -- the reserve ---------------------------------------------------------------------------------------------


def test_reserve_progress_and_months_covered() -> None:
    goal = Goal(id="g1", name="Emergency", target_amount=D("18000"), saved_amount=D("7500"), currency="SAR", purpose=GoalPurpose.EMERGENCY,
                reserve_months=3, account_id="savings", created_at=STAMP, updated_at=STAMP, **SCOPE)
    other = Goal(id="g2", name="Travel", target_amount=D("5000"), currency="SAR", created_at=STAMP, updated_at=STAMP, **SCOPE)
    result = F.reserves([goal, other], [account("savings")], {"SAR": (D("6000"), "essential"), "USD": (D("900"), "bare")}, 3)
    view = result.reserves[0]
    assert len(result.reserves) == 1 and view.mode == "months" and view.held_in == "account" and view.account_name == "Savings"
    assert (view.progress_percent, view.months_covered, view.recalculated_target, view.status) == (41, D("1.2"), D("18000.00"), "building")
    # A currency with essentials and no reserve gets a suggested target of three months.
    assert [(s.currency, s.target) for s in result.suggestions] == [("USD", D("2700.00"))]
    assert F.months_covered(D("100"), None) is None


# -- the routes and the Suggestions queue -----------------------------------------------------------------------


API = "/api/v1/finance"
OWNER = {"x-tenant-id": "tenant_fc", "x-workspace-id": "ws_fc", "x-user-id": "owner_fc", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-user-id": "other_fc"}
VIEWER = {**OWNER, "x-user-id": "viewer_fc", "x-user-roles": "viewer"}
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    for store in (finance_store, sms_memory_store, links_memory_store, personal_memory_store):
        store.clear()
    yield
    for store in (finance_store, sms_memory_store, links_memory_store, personal_memory_store):
        store.clear()


def test_the_forecast_route_reads_your_own_records_and_feeds_the_suggestions_queue() -> None:
    bank = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", "opening_balance": "1000"}).json()
    soon = (date.today() + timedelta(days=1)).isoformat()
    assert client.post(f"{API}/bills", headers=OWNER, json={"name": "Rent", "amount": "4000", "currency": "SAR", "cadence": "monthly",
                                                            "next_due_on": soon, "category": "housing"}).status_code == 201
    goal = client.post(f"{API}/goals", headers=OWNER, json={"name": "Emergency", "target_amount": "500", "currency": "SAR", "purpose": "emergency",
                                                            "reserve_months": 3, "account_id": bank["id"]})
    assert goal.status_code == 201 and goal.json()["reserve_months"] == 3
    result = client.get(f"{API}/forecast", headers=OWNER).json()
    row = result["currencies"][0]
    assert row["currency"] == "SAR" and row["actual"] in ("1000", "1000.00") and {w["kind"] for w in row["warnings"]} >= {"below_zero", "below_reserve"}
    queue = client.get(f"{API}/suggestions", headers=OWNER, params={"kind": "cash_shortage", "limit": 50}).json()
    reasons = {item["params"].get("reason") for item in queue["items"] if item["producer"] == "forecast"}
    assert reasons == {"below_zero", "below_reserve"}
    # Someone else's forecast and reserve hold none of it; a viewer may not read Finance.
    assert client.get(f"{API}/forecast", headers=OTHER).json()["currencies"] == []
    assert client.get(f"{API}/reserve", headers=OTHER).json()["reserves"] == []
    assert client.get(f"{API}/forecast", headers=VIEWER).status_code == 403


def test_the_reserve_is_a_goal_and_months_are_only_for_an_emergency_goal() -> None:
    client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", "opening_balance": "2000"})
    goal = client.post(f"{API}/goals", headers=OWNER, json={"name": "Emergency", "target_amount": "6000", "saved_amount": "1500", "currency": "SAR",
                                                            "purpose": "emergency", "reserve_months": 3}).json()
    reserve = client.get(f"{API}/reserve", headers=OWNER).json()["reserves"][0]
    assert (reserve["goal_id"], reserve["mode"], reserve["months"], reserve["progress_percent"], reserve["held_in"]) == (goal["id"], "months", 3, 25, "goal")
    # Back to a fixed amount with 0; left out, the months stay.
    kept = client.patch(f"{API}/goals/{goal['id']}", headers=OWNER, json={"name": "Emergency", "target_amount": "6000", "purpose": "emergency"}).json()
    assert kept["reserve_months"] == 3
    fixed = client.patch(f"{API}/goals/{goal['id']}", headers=OWNER, json={"name": "Emergency", "target_amount": "6000", "reserve_months": 0}).json()
    assert fixed["reserve_months"] is None
    refused = client.post(f"{API}/goals", headers=OWNER, json={"name": "Trip", "target_amount": "100", "reserve_months": 2})
    assert refused.status_code == 422
