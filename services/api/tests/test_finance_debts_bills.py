"""Bills matched to recorded payments, debt payments, the debt plan, paused goals and budgets
(docs/finance.md#bills-and-budgets, #debts-and-goals), in-memory stores. Every record is SYNTHETIC.

``test_postgres_finance_debts_bills.py`` repeats the storage checks under RLS.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance.commitments import bill_schedule, budget_report, occurrence_status, occurrences, parse_bill_target
from anum_api.finance.guardian import CategoryCap, GuardianRules
from anum_api.finance.links_store import FinanceLink, LinkStatus, links_memory_store
from anum_api.finance.models import Bill, BudgetAlert, Cadence, Category, CategorySource, Transaction
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store

API = "/api/v1/finance"
OWNER = {"x-tenant-id": "tenant_plan", "x-workspace-id": "ws_plan", "x-user-id": "owner_plan", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-user-id": "someone_else"}
VIEWER = {**OWNER, "x-user-roles": "viewer"}
client = TestClient(main.app)
NOW = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    yield
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


def _bill(**over: object) -> Bill:
    values: dict = {"id": "fin_bill_1", "tenant_id": "t", "workspace_id": "w", "owner_user_id": "u", "name": "Rent", "amount": Decimal("2500"),
                    "currency": "SAR", "cadence": Cadence.MONTHLY, "next_due_on": date(2026, 11, 15), "created_at": NOW.replace(year=2026, month=1),
                    "updated_at": NOW}
    values.update(over)
    return Bill(**values)


def _link(target: str, status: LinkStatus = LinkStatus.ACTIVE, amount: str = "2500") -> FinanceLink:
    return FinanceLink(id=f"fin_link_{target}_{status.value}", tenant_id="t", workspace_id="w", owner_user_id="u", canonical_id="fin_je_1",
                       dimension="bill", role="payment", target_type="finance_bill_due", target_id=target, amount=Decimal(amount), currency="SAR",
                       status=status, idem_key="k", created_by="u", created_at=NOW, updated_at=NOW)


# -- pure figures -----------------------------------------------------------------------------------------------


def test_occurrences_run_forward_from_next_due_on() -> None:
    bill = _bill(next_due_on=date(2026, 12, 3))
    assert occurrences(bill, date(2026, 11, 1), date(2026, 12, 31)) == [date(2026, 12, 3)]
    weekly = _bill(cadence=Cadence.WEEKLY, next_due_on=date(2026, 11, 4))
    assert len(occurrences(weekly, date(2026, 11, 1), date(2026, 11, 30))) == 4
    once = _bill(cadence=Cadence.ONCE, next_due_on=date(2026, 11, 20))
    assert occurrences(once, date(2026, 11, 1), date(2026, 12, 31)) == [date(2026, 11, 20)]


def test_a_matched_due_date_stays_when_next_due_on_moves_on() -> None:
    # Paid on 31 March, then next_due_on moved to 30 April: the March date stays paid, and no
    # unmatched 30 March appears from the new anchor day.
    bill = _bill(next_due_on=date(2027, 4, 30), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    rows = bill_schedule([bill], [_link("fin_bill_1:2027-03-31")], date(2027, 4, 2), date(2027, 3, 1), date(2027, 4, 30))
    assert [(r.due_on, r.status) for r in rows] == [(date(2027, 3, 31), "paid"), (date(2027, 4, 30), "planned")]


def test_a_bill_is_paid_only_with_an_active_match() -> None:
    today = date(2026, 11, 5)
    assert occurrence_status(date(2026, 11, 1), today, []) == "overdue"
    assert occurrence_status(date(2026, 11, 12), today, []) == "due_soon"
    assert occurrence_status(date(2026, 11, 13), today, []) == "planned"
    assert occurrence_status(date(2026, 11, 1), today, [_link("x", LinkStatus.NEEDS_REVIEW)]) == "review"
    assert occurrence_status(date(2026, 11, 1), today, [_link("x")]) == "paid"
    rows = bill_schedule([_bill()], [_link("fin_bill_1:2026-11-15", amount="2400"), _link("fin_bill_1:2026-11-15", LinkStatus.VOID)], today,
                         date(2026, 11, 1), date(2026, 12, 31))
    assert [(r.due_on, r.status, r.paid_amount) for r in rows] == [(date(2026, 11, 15), "paid", Decimal("2400.00")),
                                                                   (date(2026, 12, 15), "planned", Decimal("0.00"))]
    assert parse_bill_target("fin_bill_1:2026-11-15") == ("fin_bill_1", date(2026, 11, 15))
    assert parse_bill_target("nonsense") is None


def test_a_paused_bill_hides_its_unmatched_dates() -> None:
    rows = bill_schedule([_bill(active=False)], [], date(2026, 11, 5), date(2026, 11, 1), date(2026, 11, 30))
    assert rows == []


def _spend(day: int, amount: str, category: Category = Category.FUEL, currency: str = "SAR") -> Transaction:
    return Transaction(id=f"t{day}{amount}{category.value}", tenant_id="t", workspace_id="w", owner_user_id="u", account_id="a", posted_on=date(2026, 11, day),
                       amount=Decimal(amount), currency=currency, description="x", category=category, category_source=CategorySource.MANUAL,
                       dedupe_hash="h", created_at=NOW, updated_at=NOW)


def test_budget_report_states_pace_and_daily_line() -> None:
    rules = GuardianRules(category_caps=[CategoryCap(category=Category.FUEL, currency="SAR", amount=Decimal("1000")),
                                         CategoryCap(category=Category.DINING, currency="SAR", amount=Decimal("500"))])
    rows = [_spend(1, "-300"), _spend(3, "-450"), _spend(2, "-100", Category.DINING), _spend(2, "500", Category.DINING), _spend(4, "-50", Category.GROCERIES)]
    report = budget_report(rules, 3, [BudgetAlert(category=Category.FUEL, currency="SAR", alert_percent=70)], rows, month="2026-11",
                           start=date(2026, 11, 1), end=date(2026, 11, 30), today=date(2026, 11, 5))
    fuel = next(b for b in report.budgets if b.category == Category.FUEL)
    assert (fuel.spent, fuel.remaining, fuel.percent, fuel.state, fuel.alert_percent) == (Decimal("750.00"), Decimal("250"), 75, "near", 70)
    # 750 over 5 days, 30 days in November: the pace ends at 4,500, 3,500 past the cap.
    assert (fuel.projected, fuel.projected_over) == (Decimal("4500.00"), Decimal("3500.00"))
    dining = next(b for b in report.budgets if b.category == Category.DINING)
    assert (dining.spent, dining.state) == (Decimal("100.00"), "ok")  # money in does not count, groceries has no cap
    series = report.series[0]
    assert (series.currency, series.budget, series.spent, len(series.points)) == ("SAR", Decimal("1500"), Decimal("850.00"), 5)
    assert [p.spent for p in series.points] == [Decimal("300.00"), Decimal("400.00"), Decimal("850.00"), Decimal("850.00"), Decimal("850.00")]
    past = budget_report(rules, 3, [], rows, month="2026-10", start=date(2026, 10, 1), end=date(2026, 10, 31), today=date(2026, 11, 5))
    assert all(b.projected is None for b in past.budgets) and len(past.series[0].points) == 31


# -- the API ---------------------------------------------------------------------------------------------------


def account(**body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def pay(account_id: str, amount: str, on: str = "2026-11-03", description: str = "STC broadband", category: str = "telecom") -> str:
    response = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": account_id, "posted_on": on, "amount": amount,
                                                                        "description": description, "category": category})
    assert response.status_code == 201, response.text
    entries = client.get(f"{API}/ledger/entries", headers=OWNER).json()["items"]
    return next(e["canonical_id"] for e in entries if e["source_ref"] == response.json()["id"])


def test_match_a_payment_to_a_bill_due_date_and_remove_it() -> None:
    current = account()
    bill = client.post(f"{API}/bills", headers=OWNER, json={"name": "STC broadband", "amount": "290", "cadence": "monthly",
                                                         "next_due_on": "2026-11-03", "category": "telecom", "account_id": current["id"]}).json()
    canonical = pay(current["id"], "-270")
    schedule = client.get(f"{API}/bill-schedule?from=2026-11-01&to=2026-11-30", headers=OWNER).json()
    assert [(i["due_on"], i["status"]) for i in schedule["items"]] != []
    assert schedule["items"][0]["status"] != "paid"  # a forecast until matched

    found = client.get(f"{API}/payment-candidates?for=bill&around=2026-11-03&amount=290&currency=SAR", headers=OWNER).json()["items"]
    assert [c["canonical_id"] for c in found] == [canonical] and found[0]["remaining"] == "270.00"

    wrong_day = client.post(f"{API}/bills/{bill['id']}/matches", headers=OWNER, json={"canonical_id": canonical, "due_on": "2026-11-04"})
    assert wrong_day.status_code == 422
    made = client.post(f"{API}/bills/{bill['id']}/matches", headers={**OWNER, "Idempotency-Key": "match-1"},
                       json={"canonical_id": canonical, "due_on": "2026-11-03"})
    assert made.status_code == 201, made.text
    link = made.json()["link"]
    assert (link["dimension"], link["target_id"], link["amount"]) == ("bill", f"{bill['id']}:2026-11-03", "270.00")
    again = client.post(f"{API}/bills/{bill['id']}/matches", headers={**OWNER, "Idempotency-Key": "match-1"},
                        json={"canonical_id": canonical, "due_on": "2026-11-03"})
    assert again.json()["replayed"] is True
    paid = client.get(f"{API}/bill-schedule?from=2026-11-01&to=2026-11-30", headers=OWNER).json()["items"][0]
    assert (paid["status"], paid["paid_amount"], paid["canonical_ids"]) == ("paid", "270.00", [canonical])
    # Nothing is left of the payment for another bill.
    other = client.post(f"{API}/bills", headers=OWNER, json={"name": "Water", "amount": "90", "currency": "SAR", "next_due_on": "2026-11-03"}).json()
    over = client.post(f"{API}/bills/{other['id']}/matches", headers=OWNER, json={"canonical_id": canonical, "due_on": "2026-11-03"})
    assert over.status_code == 409 and over.json()["code"] == "over_allocated"
    # The journal is untouched; removing the match makes the date a forecast again.
    assert client.delete(f"{API}/bills/{bill['id']}/matches/{link['id']}", headers=OWNER).status_code == 204
    again_schedule = client.get(f"{API}/bill-schedule?from=2026-11-01&to=2026-11-30", headers=OWNER).json()["items"]
    assert next(i for i in again_schedule if i["bill_id"] == bill["id"])["status"] != "paid"
    assert client.delete(f"{API}/bills/{other['id']}/matches/{link['id']}", headers=OWNER).status_code == 404


def test_matches_belong_to_the_owner_and_need_manage() -> None:
    current = account()
    bill = client.post(f"{API}/bills", headers=OWNER, json={"name": "Rent", "amount": "100", "currency": "SAR", "next_due_on": "2026-11-03"}).json()
    canonical = pay(current["id"], "-100")
    assert client.post(f"{API}/bills/{bill['id']}/matches", headers=OTHER, json={"canonical_id": canonical, "due_on": "2026-11-03"}).status_code == 404
    assert client.post(f"{API}/bills/{bill['id']}/matches", headers=VIEWER, json={"canonical_id": canonical, "due_on": "2026-11-03"}).status_code == 403
    assert client.get(f"{API}/bill-schedule", headers=OTHER).json()["items"] == []
    assert client.get(f"{API}/payment-candidates?for=bill", headers=OTHER).json()["items"] == []


def test_money_in_never_pays_a_bill() -> None:
    current = account()
    bill = client.post(f"{API}/bills", headers=OWNER, json={"name": "Rent", "amount": "100", "currency": "SAR", "next_due_on": "2026-11-03"}).json()
    salary = pay(current["id"], "5000", description="Salary", category="income")
    assert client.get(f"{API}/payment-candidates?for=bill&around=2026-11-03", headers=OWNER).json()["items"] == []
    refused = client.post(f"{API}/bills/{bill['id']}/matches", headers=OWNER, json={"canonical_id": salary, "due_on": "2026-11-03"})
    assert refused.status_code == 422


def test_debt_payments_history_and_starting_balance() -> None:
    current = account()
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Card 4812", "kind": "card", "currency": "SAR", "balance": "9300", "apr_percent": "28.8",
                                                         "minimum_payment": "620", "due_day": 21, "original_balance": "12000"}).json()
    assert debt["original_balance"] == "12000.00"
    # An edit without the field keeps it (the phone app sends the old body).
    kept = client.patch(f"{API}/debts/{debt['id']}", headers=OWNER, json={"name": "Card 4812", "kind": "card", "currency": "SAR", "balance": "9000"}).json()
    assert kept["original_balance"] == "12000.00"
    cleared = client.patch(f"{API}/debts/{debt['id']}", headers=OWNER, json={"name": "Card 4812", "currency": "SAR", "balance": "9000", "original_balance": ""}).json()
    assert cleared["original_balance"] is None
    canonical = pay(current["id"], "-620", description="Card payment", category="transfer")
    made = client.post(f"{API}/debts/{debt['id']}/payments", headers=OWNER, json={"canonical_id": canonical})
    assert made.status_code == 201, made.text
    history = client.get(f"{API}/debt-payments", headers=OWNER).json()
    assert history["total"] == 1
    row = history["items"][0]
    assert (row["debt_name"], row["posted_on"], row["amount"], row["account_id"], row["status"]) == ("Card 4812", "2026-11-03", "620.00", current["id"], "active")
    assert history["paid"] == [{"debt_id": debt["id"], "currency": "SAR", "amount": "620.00"}]
    assert client.get(f"{API}/debt-payments", headers=OTHER).json()["total"] == 0
    assert client.delete(f"{API}/debts/{debt['id']}/payments/{row['link_id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/debt-payments", headers=OWNER).json()["total"] == 0


def test_debt_plan_round_trip_and_validation() -> None:
    assert client.get(f"{API}/debt-plan", headers=OWNER).json() == {"plans": []}
    saved = client.put(f"{API}/debt-plan", headers=OWNER, json={"plans": [{"currency": "sar", "strategy": "snowball", "monthly_payment": "2450"}]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["plans"] == [{"currency": "SAR", "strategy": "snowball", "monthly_payment": "2450.00"}]
    assert client.get(f"{API}/debt-plan", headers=OTHER).json() == {"plans": []}
    twice = client.put(f"{API}/debt-plan", headers=OWNER, json={"plans": [{"currency": "SAR"}, {"currency": "SAR"}]})
    assert twice.status_code == 422
    assert client.put(f"{API}/debt-plan", headers=VIEWER, json={"plans": []}).status_code == 403
    # The plan lives next to the safe-to-spend settings without changing them.
    client.put(f"{API}/plan", headers=OWNER, json={"buffers": [{"currency": "SAR", "amount": "500"}]})
    assert client.get(f"{API}/debt-plan", headers=OWNER).json()["plans"][0]["strategy"] == "snowball"
    assert client.get(f"{API}/plan", headers=OWNER).json()["buffers"] == [{"currency": "SAR", "amount": "500.00"}]


def test_a_paused_goal_is_not_set_aside() -> None:
    account()
    goal = client.post(f"{API}/goals", headers=OWNER, json={"name": "Umrah", "target_amount": "12000", "saved_amount": "2000", "monthly_contribution": "500"}).json()
    assert goal["paused"] is False
    paused = client.patch(f"{API}/goals/{goal['id']}", headers=OWNER, json={"name": "Umrah", "target_amount": "12000", "saved_amount": "2000",
                                                                          "monthly_contribution": "500", "paused": True}).json()
    assert paused["paused"] is True
    # An edit that leaves ``paused`` out keeps it.
    kept = client.patch(f"{API}/goals/{goal['id']}", headers=OWNER, json={"name": "Umrah trip", "target_amount": "12000"}).json()
    assert kept["paused"] is True
    safe = client.get(f"{API}/safe-to-spend", headers=OWNER).json()
    lines = [line for c in safe["currencies"] for line in c["lines"] if line["kind"] == "goal"]
    excluded = [line for line in safe["excluded"] if line["kind"] == "goal"]
    assert lines == [] and [line["note"] for line in excluded] == ["goal_paused"]


def _guardian_caps(caps: list[dict]) -> None:
    change = client.post(f"{API}/guardian/changes", headers=OWNER, json={"rules": {"category_caps": caps}}).json()
    confirmed = client.post(f"{API}/guardian/changes/{change['id']}/confirm", headers=OWNER, json={"payload_hash": change["payload_hash"]})
    assert confirmed.status_code == 200, confirmed.text


def test_budgets_follow_guardian_caps_and_their_alert_settings() -> None:
    current = account()
    assert client.get(f"{API}/budgets", headers=OWNER).json()["budgets"] == []
    _guardian_caps([{"category": "fuel", "currency": "SAR", "amount": "1000"}])
    today = datetime.now(tz=ZoneInfo("Asia/Dubai")).date().isoformat()  # the finance default time zone
    pay(current["id"], "-850", on=today, description="Fuel", category="fuel")
    report = client.get(f"{API}/budgets", headers=OWNER).json()
    fuel = report["budgets"][0]
    assert (fuel["category"], fuel["spent"], fuel["remaining"], fuel["state"], fuel["alert_percent"]) == ("fuel", "850.00", "150.00", "near", 80)
    alerts = client.get(f"{API}/guardian/alerts", headers=OWNER).json()["alerts"]
    assert [a["code"] for a in alerts] == ["cap_near"]
    saved = client.put(f"{API}/budgets/alerts", headers=OWNER, json={"alerts": [{"category": "fuel", "currency": "SAR", "alert_percent": 90}]})
    assert saved.status_code == 200, saved.text
    assert client.get(f"{API}/budgets", headers=OWNER).json()["budgets"][0]["state"] == "ok"
    assert client.get(f"{API}/guardian/alerts", headers=OWNER).json()["alerts"] == []
    client.put(f"{API}/budgets/alerts", headers=OWNER, json={"alerts": [{"category": "fuel", "currency": "SAR", "alert_percent": 80, "alert_near": False}]})
    assert client.get(f"{API}/guardian/alerts", headers=OWNER).json()["alerts"] == []
    assert client.put(f"{API}/budgets/alerts", headers=OWNER, json={"alerts": [{"category": "fuel", "currency": "SAR", "alert_percent": 100}]}).status_code == 422
    assert client.put(f"{API}/budgets/alerts", headers=VIEWER, json={"alerts": []}).status_code == 403
    assert client.get(f"{API}/budgets", headers=OTHER).json()["budgets"] == []


def test_money_coming_in_never_counts_as_a_payment_whatever_its_category() -> None:
    current = account()
    bill = client.post(f"{API}/bills", headers=OWNER, json={"name": "Rent", "amount": "100", "currency": "SAR", "next_due_on": "2026-11-03"}).json()
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Card", "currency": "SAR", "balance": "900"}).json()
    refund = pay(current["id"], "80", description="Shop refund", category="shopping")
    incoming = pay(current["id"], "300", description="Transfer from savings", category="transfer")
    assert client.get(f"{API}/payment-candidates?for=bill&around=2026-11-03", headers=OWNER).json()["items"] == []
    assert client.get(f"{API}/payment-candidates?for=debt&around=2026-11-03", headers=OWNER).json()["items"] == []
    for canonical in (refund, incoming):
        assert client.post(f"{API}/bills/{bill['id']}/matches", headers=OWNER, json={"canonical_id": canonical, "due_on": "2026-11-03"}).status_code == 422
        assert client.post(f"{API}/debts/{debt['id']}/payments", headers=OWNER, json={"canonical_id": canonical}).status_code == 422


def test_deleting_my_data_removes_the_debt_plan_and_budget_alerts() -> None:
    from anum_api.schemas import TenantContext

    me = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id=OWNER["x-user-id"], roles=["owner"])
    assert finance_store.count_everything(me) == 0
    client.put(f"{API}/debt-plan", headers=OWNER, json={"plans": [{"currency": "SAR", "strategy": "snowball"}]})
    assert finance_store.count_everything(me) == 1
    assert finance_store.delete_everything(me) == 1
    assert finance_store.count_everything(me) == 0
    assert client.get(f"{API}/debt-plan", headers=OWNER).json() == {"plans": []}
