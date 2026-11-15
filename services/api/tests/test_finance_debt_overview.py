"""The personal debt overview, credit cards, promises to pay, financial alerts and the Money Advisor's answers
(docs/finance.md#debt-overview, #credit-cards, #financial-alerts, #advisor-answers). In-memory stores; every record is SYNTHETIC
and low entropy. ``test_postgres_finance_debts.py`` repeats the storage checks under RLS.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

from test_finance_personal_money import ME, OTHER, OWNER, VIEWER, account, clean, client, record  # noqa: F401  (``clean`` resets the stores)

from anum_api.agent_tools import ToolCall, ToolRegistry
from anum_api.finance import alerts as A
from anum_api.finance import bank_loans as BL
from anum_api.finance import debt_overview as DO
from anum_api.finance import personal as P
from anum_api.finance.models import Debt, DebtKind, InstallmentStatementEntry
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.tools import plan_call, register_finance_tools
from anum_api.schemas import TenantContext, utc_now
from anum_api.team_store import memory_team_store

API = "/api/v1/finance"
STAMP = datetime(2026, 11, 1, tzinfo=UTC)


def today() -> date:
    return utc_now().date()


def months_ago(months: int, day: int = 15) -> date:
    t = today()
    year, month = divmod(t.year * 12 + t.month - 1 - months, 12)
    return date(year, month + 1, day)


def overview(headers: dict = OWNER) -> dict:
    found = client.get(f"{API}/debt-overview", headers=headers)
    assert found.status_code == 200, found.text
    return found.json()


def item(view: dict, ref_id: str) -> dict:
    return next(i for i in view["items"] if i["ref_id"] == ref_id)


def queue(kind: str) -> list[dict]:
    response = client.get(f"{API}/suggestions", headers=OWNER, params={"kind": kind, "limit": 100})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def bank_loan(account_id: str | None, **terms: object) -> dict:
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Example car loan", "kind": "loan", "currency": "AED", "balance": "99999"}).json()
    body = {"lender": "Example Bank", "start_on": months_ago(3).isoformat(), "amount": "12000", "term_months": 12, "payment_day": 27,
            "repayment": "bank_debit", "account_id": account_id, **terms}
    made = client.put(f"{API}/debts/{debt['id']}/loan", headers=OWNER, json=body)
    assert made.status_code == 200, made.text
    return made.json()


# -- bank loans: a bank deduction and a salary deduction update what is owed ---------------------------------------


def test_a_bank_deduction_matched_to_a_loan_lowers_its_balance_in_the_overview() -> None:
    bank = account(balance="20000")
    loan = bank_loan(bank["id"])
    debt_id = loan["debt_id"] if "debt_id" in loan else loan["terms"].get("debt_id") or client.get(f"{API}/loans", headers=OWNER).json()["items"][0]["debt_id"]
    first = loan["rows"][0]
    canonical = record(bank["id"], "-1000", first["due_on"], "Example Bank instalment", category="transfer")
    assert client.post(f"{API}/debts/{debt_id}/payments", headers=OWNER, json={"canonical_id": canonical}).status_code == 201
    view = overview()
    mine = item(view, debt_id)
    assert mine["type"] == "personal_loan" and mine["direction"] == "owe" and mine["currency"] == "AED"
    assert D(mine["paid"]) == D(1000) and D(mine["remaining"]) == D(11000) and D(mine["original_amount"]) == D(12000)
    assert mine["history"][0]["evidence"] == "verified" and mine["history"][0]["ref_id"] == canonical
    assert {"figure": "remaining", "source": "schedule_and_matched_payments"} in mine["sources"]
    assert "rate" in mine["missing"] and mine["interest"] == "unknown"  # no rate entered: never invented
    # Instalments not paid since the start are missed: overdue, first in the plan.
    assert D(mine["overdue_amount"]) > 0 and view["plan"][0]["obligation_id"] == mine["id"] and view["plan"][0]["reason"] == "overdue"
    assert any(c["obligation_id"] == mine["id"] and c["status"] == "overdue" for c in view["calendar"])
    # The typed 99,999 is never what is owed.
    assert next(t for t in view["totals"] if t["currency"] == "AED")["owe"] == mine["remaining"]


def test_a_salary_deduction_accepted_for_a_loan_lowers_its_balance_with_payslip_evidence() -> None:
    bank = account()
    loan = bank_loan(None, repayment="salary_deduction", installment="1000")
    debt_id = client.get(f"{API}/loans", headers=OWNER).json()["items"][0]["debt_id"]
    first_month = loan["rows"][0]["due_on"][:7]
    assert client.put(f"{API}/salary/profile", headers=OWNER, json={
        "employer": "Example Employer", "joined_on": months_ago(30).isoformat(), "currency": "AED", "expected_gross": "15000", "pay_day": 27,
        "account_id": bank["id"], "rules": [{"kind": "deduction", "reason": "loan", "label": "Example Bank", "amount": "1000", "starts": first_month}]}
    ).status_code == 200
    item_ = next(s for s in queue("loan_payments") if s["params"]["debt_id"] == debt_id)
    assert client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": item_["key"], "payload_hash": item_["payload_hash"]}).status_code == 200
    mine = item(overview(), debt_id)
    found = int(item_["params"]["found"])
    assert found >= 2 and D(mine["paid"]) == D(1000 * found) and D(mine["remaining"]) == D(12000 - 1000 * found)
    assert mine["schedule"] == "salary" and {h["evidence"] for h in mine["history"]} == {"payslip"}
    # The salary rule is carried by the loan: it is not listed again as an employer loan.
    assert not [i for i in overview()["items"] if i["type"] == "employer_loan"]


def test_an_employer_loan_and_a_salary_advance_come_from_the_salary_profile() -> None:
    bank = account()
    month = today().strftime("%Y-%m")
    assert client.put(f"{API}/salary/profile", headers=OWNER, json={
        "employer": "Example Employer", "joined_on": months_ago(30).isoformat(), "currency": "AED", "expected_gross": "9000", "pay_day": 28,
        "account_id": bank["id"], "rules": [
            {"kind": "deduction", "reason": "loan", "label": "Staff loan", "amount": "500", "starts": month, "ends": month},
            {"kind": "advance", "reason": "advance", "label": "Advance", "amount": "1000", "starts": month, "total": "3000",
             "received_on": (today() - timedelta(days=1)).isoformat()}]}).status_code == 200
    items = {i["type"]: i for i in overview()["items"]}
    assert items["employer_loan"]["expected_installment"] == "500.00" and items["employer_loan"]["counterparty"] == "Example Employer"
    advance = items["salary_advance"]
    assert D(advance["original_amount"]) == D(3000) and D(advance["remaining"]) in (D(3000), D(2000)) and advance["interest"] == "interest_free"


def test_an_extra_and_a_partial_repayment_follow_the_schedule() -> None:
    debt = Debt(tenant_id="t", workspace_id="w", owner_user_id="u", id="d", name="Example", kind=DebtKind.LOAN, balance=D(1), currency="AED",
                created_at=STAMP, updated_at=STAMP, loan_start_on=date(2026, 1, 15), loan_amount=D(12000), loan_term_months=12, loan_payment_day=27)
    schedule = BL.build(debt)
    assert schedule is not None
    # An extra payment (1,500 on a 1,000 instalment) covers part of the next one; a partial payment adds to it (500 + 300 of 1,000).
    view = BL.figures(debt, schedule, [BL.Paid(on=date(2026, 2, 27), amount=D(1500), kind="link", ref="c1"),
                                       BL.Paid(on=date(2026, 3, 27), amount=D(300), kind="link", ref="c2")], date(2026, 3, 30))
    assert [r.status for r in view.rows[:3]] == ["paid", "part_paid", "upcoming"] and view.rows[1].paid == D(800) and view.balance == D("10200.00")
    loan = P.PersonLoan(tenant_id="t", workspace_id="w", owner_user_id="u", id="p", direction=P.LoanDirection.LENT, person="Synthetic Friend",
                        amount=D(900), currency="AED", lent_on=date(2026, 1, 1), installments=3, first_due_on=date(2026, 2, 1), created_at=STAMP, updated_at=STAMP)

    class Link:
        def __init__(self, amount: str) -> None:
            self.amount, self.currency, self.role, self.status, self.target_id = D(amount), "AED", "deposit", "active", "p"

    f = P.loan_figures(loan, [Link("200")], date(2026, 2, 10))
    assert f.outstanding == D(700) and f.overdue_amount == D(100) and f.status == "overdue"


# -- loans with people ------------------------------------------------------------------------------------------------


def test_a_repayment_from_a_person_is_linked_on_accept_and_shown_with_its_history_and_promise() -> None:
    bank = account(balance="5000")
    # A real person in the owner's Contacts: an unknown contact id is refused (docs/finance.md#people-from-contacts).
    neighbour = client.post("/api/v1/contacts", headers=OWNER, json={"name": "Synthetic Neighbour"})
    assert neighbour.status_code == 201, neighbour.text
    contact_id = neighbour.json()["person"]["id"]
    made = client.post(f"{API}/people-loans", headers=OWNER, json={
        "direction": "lent", "person": "Synthetic Neighbour", "amount": "800", "currency": "AED", "lent_on": (today() - timedelta(days=30)).isoformat(),
        "due_on": (today() + timedelta(days=20)).isoformat(), "contact_id": contact_id}).json()
    promise = client.post(f"{API}/people-loans/{made['id']}/promises", headers=OWNER, json={
        "amount": "300", "due_by": (today() + timedelta(days=1)).isoformat(), "made_on": (today() - timedelta(days=3)).isoformat()})
    assert promise.status_code == 201, promise.text
    assert client.post(f"{API}/people-loans/{made['id']}/promises", headers=VIEWER, json={"amount": "1", "due_by": today().isoformat()}).status_code == 403
    record(bank["id"], "300", (today() - timedelta(days=1)).isoformat(), "Transfer from SYNTHETIC NEIGHBOUR")
    found = queue("loan_repayment")
    assert len(found) == 1 and found[0]["action"]["kind"] == "link"
    assert client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": found[0]["key"], "payload_hash": found[0]["payload_hash"]}).status_code == 200
    detail = client.get(f"{API}/people-loans/{made['id']}", headers=OWNER).json()
    assert detail["contact_id"] == contact_id and detail["contact_name"] == "Synthetic Neighbour" and detail["promise_states"][0]["status"] == "kept"
    mine = item(overview(), made["id"])
    assert mine["direction"] == "owed_to_me" and D(mine["remaining"]) == D(500) and mine["history"][0]["evidence"] == "verified"
    assert mine["promises"][0]["status"] == "kept" and D(mine["promises"][0]["paid"]) == D(300)
    # The agreed balance differs from what the links leave: flagged, never taken over.
    assert client.put(f"{API}/people-loans/{made['id']}/agreed-balance", headers=OWNER, json={"balance": "400"}).status_code == 200
    mine = item(overview(), made["id"])
    assert mine["discrepancy"] is True and D(mine["difference"]) == D(-100) and D(mine["remaining"]) == D(500)
    answer = client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "owed_to_me"}).json()
    assert "Synthetic Neighbour" in answer["answer"] and answer["records"][0]["ref_id"] == mine["id"] and answer["complete"] is False


def test_two_people_sending_the_same_amount_wait_for_review_never_a_link() -> None:
    bank = account(balance="5000")
    lent_on = (today() - timedelta(days=40)).isoformat()
    for person in ("Synthetic Alpha", "Synthetic Beta"):
        assert client.post(f"{API}/people-loans", headers=OWNER, json={
            "direction": "lent", "person": person, "amount": "500", "currency": "AED", "lent_on": lent_on,
            "due_on": (today() + timedelta(days=5)).isoformat()}).status_code == 201
    record(bank["id"], "500", today().isoformat(), "Incoming transfer", category="income")
    found = queue("loan_repayment")
    assert len(found) == 1 and found[0]["action"]["kind"] == "open" and found[0]["params"]["review"] == "1" and found[0]["confidence"] is None
    assert found[0]["params"]["candidates"] == "2"


def test_two_unnamed_transfers_of_one_loans_amount_both_wait_for_review() -> None:
    bank = account(balance="5000")
    assert client.post(f"{API}/people-loans", headers=OWNER, json={
        "direction": "lent", "person": "Synthetic Gamma", "amount": "500", "currency": "AED", "lent_on": (today() - timedelta(days=40)).isoformat(),
        "due_on": (today() + timedelta(days=5)).isoformat()}).status_code == 201
    record(bank["id"], "500", today().isoformat(), "Incoming transfer", category="income")
    record(bank["id"], "500", (today() - timedelta(days=1)).isoformat(), "Incoming transfer", category="income")
    found = queue("loan_repayment")
    assert len(found) == 2 and {f["action"]["kind"] for f in found} == {"open"} and all(f["params"]["candidates"] == "2" for f in found)
    # A third transfer naming the person is still proposed: the name tells it apart.
    record(bank["id"], "500", today().isoformat(), "Transfer from Synthetic Gamma", category="income")
    assert [f["action"]["kind"] for f in queue("loan_repayment")].count("link") == 1


# -- credit cards ---------------------------------------------------------------------------------------------------


def test_a_card_payment_from_the_bank_links_both_sides_and_is_not_spending() -> None:
    bank = account(balance="5000", name="Example Current")
    card = account(kind="card", name="Example Card")
    day = today() - timedelta(days=3)
    record(card["id"], "-300", (day - timedelta(days=5)).isoformat(), "Example Grocer", category="groceries")
    record(bank["id"], "-300", day.isoformat(), "Credit card payment", category="other")
    record(card["id"], "300", day.isoformat(), "Payment received thank you", category="other")
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Example Card", "kind": "card", "currency": "AED", "balance": "0",
                                                             "account_id": card["id"], "due_day": 25, "minimum_payment": "100"}).json()
    candidates = client.get(f"{API}/transfers/candidates", headers=OWNER).json()["pairs"]
    assert len(candidates) == 1
    pair = candidates[0]
    paired = client.post(f"{API}/transfers", headers=OWNER, json={"start": (today() - timedelta(days=90)).isoformat(), "end": today().isoformat(),
                                                                   "pairs": [{"out_record_id": pair["out"]["record_id"], "into_record_id": pair["into"]["record_id"]}]})
    assert paired.status_code == 201 and paired.json()["paired"] == 1, paired.text
    cards = client.get(f"{API}/cards", headers=OWNER).json()["items"]
    view = cards[0]
    assert view["outstanding"] == "0.00" and view["debt_id"] == debt["id"]
    payment = view["payments"][0]
    assert payment["linked"] is True and payment["from_account_name"] == "Example Current" and payment["kind"] == "payment"
    assert "rate" in view["missing"] and "statement" in view["missing"]  # no rate or statement entered: nothing invented
    # Spend counts the purchase once: the card payment is a transfer on both sides.
    spend = client.get(f"{API}/summary", headers=OWNER).json()["spend"]
    assert [(s["currency"], s["amount"]) for s in spend] == [("AED", "300.00")]


def test_card_statement_minimum_and_full_with_the_owners_rate_and_a_statement_check() -> None:
    card = account(kind="card", name="Example Card")
    statement_on = today() - timedelta(days=10)
    record(card["id"], "-1000", (statement_on - timedelta(days=3)).isoformat(), "Example Shop", category="shopping")
    record(card["id"], "-25", (statement_on - timedelta(days=2)).isoformat(), "Late fee", category="fees")
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Example Card", "kind": "card", "currency": "AED", "balance": "0",
                                                             "account_id": card["id"], "apr_percent": "36", "due_day": (statement_on + timedelta(days=20)).day}).json()
    put = client.put(f"{API}/debts/{debt['id']}/profile", headers=OWNER, json={
        "debt_type": "credit_card", "card_limit": "5000", "card_statement_day": statement_on.day, "card_statement_balance": "1100",
        "card_statement_minimum": "55", "card_statement_on": statement_on.isoformat()})
    assert put.status_code == 200, put.text
    assert client.put(f"{API}/debts/{debt['id']}/profile", headers=OWNER, json={"card_statement_balance": "1"}).status_code == 422  # needs its date
    view = client.get(f"{API}/cards", headers=OWNER).json()["items"][0]
    assert view["limit"] == "5000.00" and view["available"] == "3975.00" and view["apr_percent"] == "36.0000"
    assert view["check"]["status"] == "differs" and view["check"]["difference"] == "75.00"  # the statement says 1,100, the ledger 1,025
    full, minimum = view["pay_options"]
    assert full["code"] == "full" and full["amount"] == "1100.00" and minimum["code"] == "minimum" and minimum["amount"] == "55.00"
    assert D(minimum["interest_estimate"]) > 0 and minimum["months_to_clear"] is not None
    assert view["payment_state"] in ("due", "not_paid") and view["cycle"]["fees"] == "0.00"
    assert {r["evidence"] for r in view["records"]} == {"unverified"}  # hand entries: no statement confirmed them
    mine = item(overview(), card["id"])
    assert mine["type"] == "credit_card" and mine["discrepancy"] is True and mine["remaining"] == "1025.00"
    # A debt's edit keeps the profile.
    assert client.patch(f"{API}/debts/{debt['id']}", headers=OWNER, json={"name": "Example Card 2", "kind": "card", "currency": "AED", "balance": "0",
                                                                         "account_id": card["id"], "apr_percent": "36"}).status_code == 200
    assert client.get(f"{API}/cards", headers=OWNER).json()["items"][0]["statement_balance"] == "1100.00"


# -- a lender's balance that differs --------------------------------------------------------------------------------


def test_a_lenders_balance_is_compared_and_flagged_only_beyond_the_tolerance() -> None:
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Example Informal", "kind": "other", "currency": "AED", "balance": "2000"}).json()
    ok = client.put(f"{API}/debts/{debt['id']}/profile", headers=OWNER, json={"debt_type": "informal", "verified_balance": "2005",
                                                                               "verified_on": today().isoformat()})
    assert ok.status_code == 200, ok.text
    mine = item(overview(), debt["id"])
    assert mine["type"] == "informal" and mine["discrepancy"] is False  # within half a percent
    client.put(f"{API}/debts/{debt['id']}/profile", headers=OWNER, json={"verified_balance": "2500", "verified_on": today().isoformat(),
                                                                          "disputed_amount": "500", "dispute_note": "Example"})
    mine = item(overview(), debt["id"])
    assert mine["discrepancy"] is True and mine["difference"] == "500.00" and mine["remaining"] == "2000.00" and mine["status"] == "disputed"
    assert overview()["discrepancies"] == 1
    assert client.put(f"{API}/debts/{debt['id']}/profile", headers=OWNER, json={"verified_balance": "1", "verified_on": (today() + timedelta(days=2)).isoformat()}
                      ).status_code == 422


# -- alerts --------------------------------------------------------------------------------------------------------


def _bnpl(status: str, due_on: date, installment: int = 1) -> InstallmentStatementEntry:
    return InstallmentStatementEntry(provider="Tamara", order_id="order-example", merchant="Example Store", due_on=due_on, installment=installment,
                                     total_installments=3, amount=D(125), currency="SAR", source_status=status)


def test_alerts_never_call_a_provider_report_paid_and_tell_each_item_once() -> None:
    planning = finance_store.get_planning(ME)
    planning.installment_entries = [_bnpl("paid", today() - timedelta(days=1), 1), _bnpl("scheduled", today() + timedelta(days=1), 2),
                                    _bnpl("scheduled", today() + timedelta(days=31), 3)]
    finance_store.save_planning(ME, planning)
    bnpl = next(i for i in overview()["items"] if i["type"] == "bnpl")
    assert bnpl["paid"] == "0.00" and bnpl["unverified_amount"] == "125.00" and bnpl["history"][0]["evidence"] == "reported"
    preview = client.get(f"{API}/alerts/preview", headers=OWNER).json()["items"]
    kinds = {a["kind"]: a for a in preview}
    assert "installment_due_tomorrow" in kinds and "not paid yet" in kinds["installment_due_tomorrow"]["body"]
    assert "installment_paid" not in kinds  # no bank payment is matched
    assert "reported paid by the provider" in kinds["unverified_payment"]["body"] and "not counted as paid" in kinds["unverified_payment"]["body"]
    for alert in preview:
        assert " is paid" not in alert["body"] or alert["evidence"] == "verified"
    # The daily run tells each item once; a kind turned off tells nothing.
    rules_memory_store.clear()
    before = len(memory_team_store.notifications) if hasattr(memory_team_store, "notifications") else None
    sent, failed = A.run(ME, now=utc_now())
    assert failed == 0 and sent >= 2
    again, _ = A.run(ME, now=utc_now())
    assert again == 0
    settings = client.put(f"{API}/alerts/settings", headers=OWNER, json={"kinds": {"installment_due_tomorrow": False}})
    assert settings.status_code == 200
    assert next(k for k in settings.json()["kinds"] if k["kind"] == "installment_due_tomorrow")["on"] is False
    assert client.put(f"{API}/alerts/settings", headers=OWNER, json={"kinds": {"made_up": True}}).status_code == 422
    assert client.put(f"{API}/alerts/settings", headers=VIEWER, json={"kinds": {}}).status_code == 403
    assert before is None or len(memory_team_store.notifications) >= before


def test_compose_says_paid_only_for_a_verified_installment() -> None:
    hist = DO.HistoryItem(on=today(), amount=D(125), currency="SAR", kind="installment", evidence="verified", ref_type="installment", ref_id="o:1")
    reported = hist.model_copy(update={"evidence": "reported", "ref_id": "o:2"})
    ob = DO.Obligation(id="installment_order:Tamara:o", ref_type="installment_order", ref_id="Tamara:o", type="bnpl", direction="owe", name="Tamara: Example",
                       counterparty="Tamara", currency="SAR", original_amount=D(375), start_on=None, schedule="monthly", interest="interest_free", rate=None,
                       expected_installment=D(125), paid=D(125), remaining=D(125), next_due_on=None, next_due_amount=D(0), overdue_amount=D(0),
                       status="open", history=[hist, reported])
    view = DO.Overview(today=today(), items=[ob], totals=[], calendar=[], plan=[], discrepancies=0)
    found = A.compose(A.AlertData(today=today(), overview=view, cards=[], accounts=[], rows=[]))
    paid = [a for a in found if a.kind == "installment_paid"]
    assert len(paid) == 1 and paid[0].key.endswith("o:1") and paid[0].evidence == "verified"


# -- the plan --------------------------------------------------------------------------------------------------------


def test_the_plan_puts_overdue_first_then_the_highest_known_rate_then_unknown_then_interest_free() -> None:
    def ob(name: str, rate: str | None, overdue: str = "0", interest: str = "interest") -> DO.Obligation:
        return DO.Obligation(id=f"debt:{name}", ref_type="debt", ref_id=name, type="other", direction="owe", name=name, counterparty=None, currency="AED",
                             original_amount=None, start_on=None, schedule="none", interest=interest, rate=D(rate) if rate else None,  # type: ignore[arg-type]
                             expected_installment=None, paid=D(0), remaining=D(1000), next_due_on=None, next_due_amount=D(0), overdue_amount=D(overdue),
                             status="open")

    rows = DO.plan([ob("free", None, interest="interest_free"), ob("low", "5"), ob("unknown", None, interest="unknown"), ob("high", "30"),
                    ob("late", "2", overdue="100")])
    assert [(r.name, r.reason) for r in rows] == [("late", "overdue"), ("high", "highest_rate"), ("low", "highest_rate"), ("unknown", "rate_unknown"),
                                                   ("free", "interest_free")]


# -- the advisor's answers -------------------------------------------------------------------------------------------


def test_advisor_answers_carry_their_records_and_admit_what_is_missing() -> None:
    bank = account(balance="4000", name="Example Current")
    stale = account(balance="100", name="Example Old")
    record(bank["id"], "-50", today().isoformat(), "Example Laundry", category="other")
    record(bank["id"], "-70", today().isoformat(), "Example Cafe", category="dining")
    record(stale["id"], "-5", (today() - timedelta(days=40)).isoformat(), "Example", category="other")
    cash = client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "cash"}).json()
    assert cash["figures"] == [{"label": "bank and cash", "amount": "3975.00", "currency": "AED"}]
    assert {r["label"] for r in cash["records"]} == {"Example Current", "Example Old"} and cash["complete"] is False
    assert any("Example Old" in m for m in cash["missing"]) and cash["calculation"][0].startswith("AED: ")
    food = client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "category_spend", "category": "food"}).json()
    assert food["figures"][0]["amount"] == "70.00" and food["records"][0]["label"] == "Example Cafe"
    laundry = client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "words_spend", "words": "laundry"}).json()
    assert laundry["figures"][0]["amount"] == "50.00" and laundry["complete"] is False  # found by words only: said so
    salary = client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "after_salary"}).json()
    assert salary["complete"] is False and "Salary profile." in salary["missing"]
    today_installment = client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "installment_today"}).json()
    assert today_installment["complete"] is False
    assert client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "nonsense"}).status_code == 422
    assert client.get(f"{API}/advisor/answer", headers=VIEWER, params={"question": "cash"}).status_code in (200, 403)


def test_todays_tamara_installment_is_confirmed_only_by_a_matched_payment() -> None:
    planning = finance_store.get_planning(ME)
    planning.installment_entries = [_bnpl("paid", today(), 1), _bnpl("scheduled", today() + timedelta(days=30), 2)]
    finance_store.save_planning(ME, planning)
    found = client.get(f"{API}/advisor/answer", headers=OWNER, params={"question": "installment_today"}).json()
    assert found["answer"].startswith("Not confirmed.") and "reported paid by the provider" in found["answer"]
    assert found["records"][0]["evidence"] == "reported" and found["complete"] is False


def test_the_assistant_routes_money_questions_to_the_answer_tool_and_it_returns_records() -> None:
    assert plan_call("how much do I owe?") == ToolCall(name="finance.answer", arguments={"question": "owe"})
    assert plan_call("did I pay today's Tamara installment?") == ToolCall(name="finance.answer", arguments={"question": "installment_today"})
    assert plan_call("money sent to Pakistan this year") == ToolCall(name="finance.answer", arguments={"question": "sent_home", "country": "pakistan"})
    assert plan_call("bills due this week").name == "finance.bills.upcoming"  # existing routes keep working
    assert plan_call("send 500 to Ali") is None  # moving money is never answered here
    client.post(f"{API}/debts", headers=OWNER, json={"name": "Example Informal", "kind": "other", "currency": "AED", "balance": "700"})
    registry = register_finance_tools(ToolRegistry())
    result = asyncio.run(registry.execute(ToolCall(name="finance.answer", arguments={"question": "owe"}), ME))
    assert result.status == "succeeded" and "AED 700.00" in result.summary and result.output["records"][0]["label"].startswith("Example Informal")


# -- privacy ----------------------------------------------------------------------------------------------------------


def test_another_person_sees_none_of_it_and_cannot_change_it() -> None:
    debt = client.post(f"{API}/debts", headers=OWNER, json={"name": "Example Informal", "kind": "other", "currency": "AED", "balance": "700"}).json()
    loan = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "borrowed", "person": "Synthetic Uncle", "amount": "100", "currency": "AED",
                                                                    "lent_on": today().isoformat()}).json()
    assert {i["ref_id"] for i in overview()["items"]} == {debt["id"], loan["id"]}
    assert overview(OTHER)["items"] == []
    assert client.put(f"{API}/debts/{debt['id']}/profile", headers=OTHER, json={"debt_type": "other"}).status_code == 404
    assert client.put(f"{API}/debts/{debt['id']}/profile", headers=VIEWER, json={"debt_type": "other"}).status_code == 403
    assert client.post(f"{API}/people-loans/{loan['id']}/promises", headers=OTHER, json={"amount": "1", "due_by": today().isoformat()}).status_code == 404
    assert client.put(f"{API}/people-loans/{loan['id']}/agreed-balance", headers=OTHER, json={"balance": "1"}).status_code == 404
    other = TenantContext(tenant_id=ME.tenant_id, workspace_id=ME.workspace_id, user_id="other_pm", roles=["owner"])
    assert A.settings(other) == A.DEFAULTS
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"kinds": {"refund_received": False}})
    assert A.settings(other)["refund_received"] is True and A.settings(ME)["refund_received"] is False
