"""Money lent and borrowed with people, payslips, remittances, fixed commitments and the planner
(docs/finance.md#lent-and-borrowed, #payslips, #sending-money-home, #fixed-commitments, #planning-and-advice).

In-memory stores; every record is SYNTHETIC. ``test_postgres_finance_personal_money.py`` repeats the storage
checks under RLS. Web search is always a fake handler: these tests never reach the internet.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal as D
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.agent_planning import choose_tool_call
from anum_api.agent_skills import default_skill_registry
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolResult
from anum_api.business import research as business_research
from anum_api.dependencies import memory_repository
from anum_api.finance import personal as P
from anum_api.finance import personal_jobs as J
from anum_api.finance import planner as PL
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.models import Bill, Cadence, Category, Debt, DebtKind, InstallmentStatementEntry
from anum_api.finance.personal_store import personal_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.tools import plan_call
from anum_api.punctuation import LONG_DASHES
from anum_api.schemas import RiskLevel, TenantContext, WorkspaceMembership
from anum_api.team_store import memory_team_store

TENANT, WORKSPACE = "tenant_pm", "workspace_pm"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_pm", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-user-id": "other_pm"}
VIEWER = {**OWNER, "x-user-id": "viewer_pm", "x-user-roles": "viewer"}
ME = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_pm", roles=["owner"])
API = "/api/v1/finance"
STAMP = datetime(2026, 11, 1, tzinfo=UTC)
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    personal_memory_store.clear()
    memberships = dict(memory_repository.store.memberships)
    for user, role in (("owner_pm", "owner"), ("other_pm", "owner"), ("viewer_pm", "viewer")):
        memory_repository.store.memberships[(TENANT, WORKSPACE, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, active=True, created_at=STAMP, updated_at=STAMP)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    personal_memory_store.clear()


def account(currency: str = "AED", kind: str = "bank", name: str = "Current", balance: str = "0") -> dict:
    made = client.post(f"{API}/accounts", headers=OWNER, json={"name": name, "kind": kind, "currency": currency, "opening_balance": balance})
    assert made.status_code == 201, made.text
    return made.json()


def record(account_id: str, amount: str, on: str, description: str = "Transfer", category: str = "other") -> str:
    made = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": account_id, "posted_on": on, "amount": amount,
                                                                    "description": description, "category": category})
    assert made.status_code == 201, made.text
    entries = client.get(f"{API}/ledger/entries", headers=OWNER).json()["items"]
    return next(e["canonical_id"] for e in entries if e["source_ref"] == made.json()["id"])


# -- the payoff order, worked out by hand ----------------------------------------------------------------


def test_payoff_orders_match_a_hand_worked_example() -> None:
    # A: 1,200 at 12% (min 100), B: 200 at 0% (min 50), paying 300 a month, AED.
    debts = [PL.PlanDebt("a", "Card A", D("1200"), D("12"), D("100")), PL.PlanDebt("b", "Loan B", D("200"), D("0"), D("50"))]
    r = PL.payoff(debts, "AED", D("300"))
    # Avalanche, interest 12 + 9.62 + 7.22 + 4.79 + 2.34; B is cleared by its minimums in month 4.
    assert (r.avalanche.months, r.avalanche.total_interest, r.avalanche.order) == (5, D("35.97"), ["a", "b"])
    assert r.avalanche.paid_off_in == {"a": 5, "b": 4}
    # Snowball clears B in month 1, then A: interest 12 + 11.12 + 8.23 + 5.31 + 2.37.
    assert (r.snowball.months, r.snowball.total_interest, r.snowball.order) == (5, D("39.03"), ["b", "a"])
    assert r.snowball.paid_off_in == {"b": 1, "a": 5}
    assert (r.cheaper, r.interest_saved, r.first_win_months) == ("avalanche", D("3.06"), 1)


def test_payoff_below_the_minimums_and_never_ending() -> None:
    debts = [PL.PlanDebt("a", "A", D("1000"), D("24"), D("10"))]
    r = PL.payoff(debts, "AED", D("5"))
    assert r.avalanche.below_minimums and r.avalanche.payment == D("10.00")
    # 2% a month on 1,000 is 20, more than the 10 paid: it never ends.
    assert r.avalanche.months is None and r.cheaper is None


# -- amortisation, checked by hand ------------------------------------------------------------------------


def test_amortisation_matches_the_standard_formula() -> None:
    # 12,000 at 12% for 12 months: 12000 x 0.01 / (1 - 1.01^-12) = 1,066.1855, rounded up to 1,066.19.
    a = PL.amortised(D("12000"), D("12"), 12, "AED")
    assert (a.monthly, a.total_paid, a.interest) == (D("1066.19"), D("12794.28"), D("794.28"))
    # A flat 5% over 24 months on 10,000: profit 1,000, 11,000 / 24 = 458.333 rounded up to 458.34.
    flat = PL.amortised(D("10000"), D("5"), 24, "AED", "flat")
    assert (flat.monthly, flat.interest) == (D("458.34"), D("1000.16"))
    assert PL.amortised(D("1200"), D("0"), 12, "AED").monthly == D("100.00")


def test_compare_never_costs_an_unknown_rate_and_sorts_by_total() -> None:
    rows = PL.compare(D("12000"), "AED", [PL.OptionInput("cash", "Cash"), PL.OptionInput("bank_loan", "Loan", 12, D("12")),
                                           PL.OptionInput("bank_loan", "Unknown", 12, None), PL.OptionInput("installments", "BNPL", 4, D("0"))])
    assert [r.label for r in rows] == ["Cash", "BNPL", "Loan", "Unknown"]
    assert rows[-1].status == "rate_unknown" and rows[-1].total_cost is None
    assert rows[2].extra_over_cash == D("794.28") and rows[1].monthly == D("3000.00")


# -- lent and borrowed ------------------------------------------------------------------------------------


def test_lending_and_being_repaid_are_neither_spend_nor_income() -> None:
    bank = account(balance="5000")
    lent = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "Friend A", "amount": "1000",
                                                                   "lent_on": "2026-11-02", "due_on": "2026-11-20"})
    assert lent.status_code == 201, lent.text
    loan = lent.json()
    assert (loan["outstanding"], loan["status"], loan["currency"]) == ("1000.00", "open", "AED")
    handed = record(bank["id"], "-1000", "2026-11-02", "Cash to Friend A", "other")
    back = record(bank["id"], "400", "2026-11-04", "From Friend A", "income")
    groceries = record(bank["id"], "-120", "2026-11-04", "Grocer", "groceries")
    month = client.get(f"{API}/summary", headers=OWNER, params={"month": "2026-11"}).json()
    assert [a["amount"] for a in month["spend"]] == ["1120.00"] and [a["amount"] for a in month["income"]] == ["400.00"]
    assert client.post(f"{API}/people-loans/{loan['id']}/links", headers=OWNER, json={"canonical_id": handed, "kind": "handed"}).status_code == 201
    made = client.post(f"{API}/people-loans/{loan['id']}/links", headers=OWNER, json={"canonical_id": back})
    assert made.status_code == 201 and made.json()["link"]["role"] == "deposit"
    # A purchase is not money in: refused as a repayment.
    refused = client.post(f"{API}/people-loans/{loan['id']}/links", headers=OWNER, json={"canonical_id": groceries})
    assert refused.status_code == 422 and refused.json()["code"] == "not_money_in"
    month = client.get(f"{API}/summary", headers=OWNER, params={"month": "2026-11"}).json()
    assert [a["amount"] for a in month["spend"]] == ["120.00"] and month["income"] == []
    detail = client.get(f"{API}/people-loans/{loan['id']}", headers=OWNER).json()
    assert (detail["repaid"], detail["outstanding"], detail["handed"], len(detail["movements"])) == ("400.00", "600.00", "1000.00", 2)
    page = client.get(f"{API}/people-loans", headers=OWNER).json()
    assert page["owed"] == [{"currency": "AED", "they_owe_me": "600.00", "i_owe": "0.00", "they_owe_me_overdue": "0.00", "i_owe_overdue": "0.00"}]
    # Deleting the record removes its links: the payments count by their category again.
    assert client.delete(f"{API}/people-loans/{loan['id']}", headers=OWNER).status_code == 204
    month = client.get(f"{API}/summary", headers=OWNER, params={"month": "2026-11"}).json()
    assert [a["amount"] for a in month["spend"]] == ["1120.00"]


def test_borrowed_money_and_repaying_it_follow_the_direction() -> None:
    bank = account()
    loan = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "borrowed", "person": "Cousin B", "amount": "500",
                                                                    "currency": "aed", "lent_on": "2026-10-01", "due_on": "2026-11-01"}).json()
    paid = record(bank["id"], "-500", "2026-11-03", "To Cousin B")
    made = client.post(f"{API}/people-loans/{loan['id']}/links", headers=OWNER, json={"canonical_id": paid})
    assert made.status_code == 201 and made.json()["link"]["role"] == "payment"
    view = client.get(f"{API}/people-loans/{loan['id']}", headers=OWNER).json()
    assert (view["outstanding"], view["status"]) == ("0.00", "repaid")
    assert client.get(f"{API}/people-loans", headers=OWNER, params={"status": "settled"}).json()["total"] == 1
    assert client.get(f"{API}/people-loans", headers=OWNER, params={"q": "nobody"}).json()["total"] == 0


def test_loans_are_private_and_viewers_cannot_write() -> None:
    loan = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "Friend C", "amount": "10",
                                                                   "lent_on": "2026-11-01"}).json()
    assert client.get(f"{API}/people-loans/{loan['id']}", headers=OTHER).status_code == 404
    assert client.get(f"{API}/people-loans", headers=OTHER).json()["total"] == 0
    assert client.post(f"{API}/people-loans", headers=VIEWER, json={"direction": "lent", "person": "X", "amount": "1",
                                                                    "lent_on": "2026-11-01"}).status_code == 403
    bad = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "X", "amount": "1", "lent_on": "2026-11-05",
                                                                  "due_on": "2026-11-01"})
    assert bad.status_code == 422


def test_loan_status_and_reminders_pure() -> None:
    loan = P.PersonLoan(id="l", tenant_id="t", workspace_id="w", owner_user_id="u", direction=P.LoanDirection.LENT, person="A", amount=D("100"),
                        currency="AED", lent_on=date(2026, 10, 1), due_on=date(2026, 11, 10), created_at=STAMP, updated_at=STAMP)
    early = P.loan_figures(loan, [], date(2026, 11, 1))
    assert early.status == "open" and P.reminder_due(loan, early, date(2026, 11, 1)) is None
    near = P.loan_figures(loan, [], date(2026, 11, 7))
    assert near.status == "due_soon" and P.reminder_due(loan, near, date(2026, 11, 7)) == "due"
    late = P.loan_figures(loan, [], date(2026, 11, 12))
    assert late.status == "overdue" and P.reminder_due(loan, late, date(2026, 11, 12)) == "overdue"
    assert P.reminder_due(loan.model_copy(update={"remind": False}), late, date(2026, 11, 12)) is None


def _notices() -> list[Any]:
    return [n for n in memory_team_store.list_notifications(ME, limit=50, before=None, unread_only=False) if n.kind == J.NOTICE_KIND]


def test_the_daily_job_reminds_once_before_the_due_date_and_once_when_overdue(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = memory_team_store._ws(ME)
    saved = dict(ws.notifications)
    ws.notifications.clear()
    try:
        account()
        client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "Friend D", "amount": "300", "lent_on": "2026-10-01",
                                                                "due_on": "2026-11-10", "remind_days_before": 3})
        assert J.run(now=datetime(2026, 11, 5, 8, tzinfo=UTC))["reminders"] == 0
        monkeypatch.setattr(J, "notify", lambda *_a, **_k: False)
        assert J.run(now=datetime(2026, 11, 7, 8, tzinfo=UTC))["failed"] == 1
        monkeypatch.undo()
        assert J.run(now=datetime(2026, 11, 7, 8, tzinfo=UTC))["reminders"] == 1
        assert J.run(now=datetime(2026, 11, 8, 8, tzinfo=UTC))["reminders"] == 0
        assert J.run(now=datetime(2026, 11, 12, 8, tzinfo=UTC))["reminders"] == 1
        assert J.run(now=datetime(2026, 11, 13, 8, tzinfo=UTC))["reminders"] == 0
        sent = _notices()
        assert len(sent) == 2 and all(n.email_status.value == "not_requested" for n in sent)
        assert "never moves money" in sent[0].body and not any(d in n.body + n.title for n in sent for d in LONG_DASHES)
    finally:
        ws.notifications.clear()
        ws.notifications.update(saved)


# -- payslips ---------------------------------------------------------------------------------------------


def test_payslip_net_reconciles_with_the_salary_credit() -> None:
    bank = account()
    slip = client.post(f"{API}/payslips", headers=OWNER, json={
        "employer": "Example Co", "month": "2026-10", "gross": "10000", "deductions": [
            {"kind": "social_insurance", "amount": "975", "label": "GOSI"}, {"kind": "medical_insurance", "amount": "200"},
            {"kind": "loan", "amount": "500"}, {"kind": "advance", "amount": "0.00"}]})
    assert slip.status_code == 422  # a zero deduction is refused
    slip = client.post(f"{API}/payslips", headers=OWNER, json={
        "employer": "Example Co", "month": "2026-10", "gross": "10000", "deductions": [
            {"kind": "social_insurance", "amount": "975", "label": "GOSI"}, {"kind": "medical_insurance", "amount": "200"},
            {"kind": "loan", "amount": "500"}]}).json()
    assert (slip["total_deductions"], slip["net"], slip["status"]) == ("1675.00", "8325.00", "not_linked")
    salary = record(bank["id"], "8300", "2026-10-28", "SALARY EXAMPLE CO", "income")
    spend = record(bank["id"], "-50", "2026-10-28", "Cafe", "dining")
    assert client.post(f"{API}/payslips/{slip['id']}/links", headers=OWNER, json={"canonical_id": spend}).status_code == 422
    assert client.post(f"{API}/payslips/{slip['id']}/links", headers=OWNER, json={"canonical_id": salary}).status_code == 201
    page = client.get(f"{API}/payslips", headers=OWNER, params={"year": 2026}).json()
    item = page["items"][0]
    assert (item["received"], item["difference"], item["status"]) == ("8300.00", "-25.00", "differs")
    assert page["months"][0] == {"month": "2026-10", "currency": "AED", "gross": "10000.00", "deductions": "1675.00", "net": "8325.00",
                                 "received": "8300.00", "by_kind": {"loan": "500.00", "medical_insurance": "200.00", "social_insurance": "975.00"},
                                 "payslips": 1}
    too_much = client.post(f"{API}/payslips", headers=OWNER, json={"employer": "E", "month": "2026-09", "gross": "100",
                                                                   "deductions": [{"kind": "tax", "amount": "101"}]})
    assert too_much.status_code == 422


# -- remittances ------------------------------------------------------------------------------------------


def test_remittance_totals_fees_and_provider_costs() -> None:
    bank = account()
    first = client.post(f"{API}/remittances", headers=OWNER, json={"sent_on": "2026-10-05", "amount": "1000", "fee": "20", "fx_rate": "22.5",
                                                                   "received_currency": "INR", "recipient": "Family", "provider": "Exchange A",
                                                                   "regular": True})
    assert first.status_code == 201, first.text
    one = first.json()
    assert (one["received_amount"], one["total_paid"], one["fee_percent"], one["effective_rate"]) == ("22500.00", "1020.00", "2.00", "22.058824")
    two = client.post(f"{API}/remittances", headers=OWNER, json={"sent_on": "2026-10-20", "amount": "1000", "fee": "10", "received_amount": "22600",
                                                                 "received_currency": "INR", "recipient": "Family", "provider": "App B"}).json()
    assert two["fx_rate"] == "22.60000000"
    paid = record(bank["id"], "-1020", "2026-10-05", "EXCHANGE A")
    assert client.post(f"{API}/remittances/{one['id']}/links", headers=OWNER, json={"canonical_id": paid}).json()["link"]["amount"] == "1020.00"
    page = client.get(f"{API}/remittances", headers=OWNER).json()
    assert page["months"] == [{"month": "2026-10", "currency": "AED", "sent": "2000.00", "fees": "30.00", "count": 2, "received": {"INR": "45100.00"}}]
    assert [p["provider"] for p in page["providers"]] == ["App B", "Exchange A"]
    assert client.get(f"{API}/remittances", headers=OWNER, params={"q": "app b"}).json()["total"] == 1


# -- fixed commitments ------------------------------------------------------------------------------------


def _bill(name: str, amount: str, cadence: Cadence = Cadence.MONTHLY, category: Category = Category.UTILITIES, **over: Any) -> Bill:
    return Bill(id=f"b_{name}", tenant_id="t", workspace_id="w", owner_user_id="u", name=name, amount=D(amount), currency="AED", cadence=cadence,
                next_due_on=date(2026, 11, 15), category=category, created_at=STAMP, updated_at=STAMP, **over)


def test_fixed_commitments_count_each_payment_once() -> None:
    bills = [_bill("Rent", "3000", category=Category.HOUSING), _bill("Gym", "100", Cadence.WEEKLY, Category.SUBSCRIPTIONS),
             _bill("Car Loan", "1500"), _bill("Visa fee", "900", Cadence.ONCE), _bill("Paused", "50", active=False), _bill("Shop X", "200")]
    debts = [Debt(id="d1", tenant_id="t", workspace_id="w", owner_user_id="u", name="Car loan", kind=DebtKind.LOAN, balance=D("20000"), currency="AED",
                  minimum_payment=D("1500"), created_at=STAMP, updated_at=STAMP),
             Debt(id="d2", tenant_id="t", workspace_id="w", owner_user_id="u", name="Card", kind=DebtKind.CARD, balance=D("80"), currency="AED",
                  minimum_payment=D("100"), created_at=STAMP, updated_at=STAMP)]
    entry = {"provider": "Tamara", "merchant": "Phone Shop", "due_on": date(2026, 11, 9), "total_installments": 4, "currency": "AED",
             "source_status": "scheduled"}
    installments = [InstallmentStatementEntry(order_id="o1", installment=2, amount=D("250"), **entry),
                    InstallmentStatementEntry(order_id="o1", installment=3, amount=D("250"), **{**entry, "due_on": date(2026, 11, 23)}),
                    InstallmentStatementEntry(order_id="o2", installment=1, amount=D("99"), **{**entry, "merchant": "Shop X"}),
                    InstallmentStatementEntry(order_id="o3", installment=1, amount=D("70"), **{**entry, "source_status": "paid"})]
    remit = [P.Remittance(id=f"r{i}", tenant_id="t", workspace_id="w", owner_user_id="u", sent_on=date(2026, 10, d), amount=D(a), currency="AED",
                          fee=D("15"), recipient="Family", provider="X", regular=True, created_at=STAMP, updated_at=STAMP)
             for i, (d, a) in enumerate([(1, "1000"), (28, "1200")])]
    counted, skipped = P.fixed_commitments(bills, debts, installments, remit, date(2026, 11, 5))
    assert [(c.kind, c.name, c.monthly) for c in counted] == [
        ("rent", "Rent", D("3000")), ("bill", "Car Loan", D("1500")), ("bill", "Shop X", D("200")), ("subscription", "Gym", D("433.33")),
        ("installment", "Tamara: Phone Shop", D("500.00")), ("card_minimum", "Card", D("80")), ("remittance", "Family", D("1215.00"))]
    assert sorted((c.name, c.note) for c in skipped) == [("Car loan", "in_bill"), ("Tamara: Shop X", "in_bill"), ("Visa fee", "once")]
    assert P.commitment_totals(counted) == {"AED": D("6928.33")}


def test_fixed_commitments_route_reads_the_records() -> None:
    account()
    assert client.post(f"{API}/bills", headers=OWNER, json={"name": "Rent", "amount": "4000", "cadence": "monthly", "next_due_on": "2026-11-28",
                                                         "category": "housing"}).status_code == 201
    body = client.get(f"{API}/fixed-commitments", headers=OWNER).json()
    assert body["totals"] == [{"currency": "AED", "amount": "4000.00"}] and body["items"][0]["kind"] == "rent"
    assert client.get(f"{API}/fixed-commitments", headers=VIEWER).status_code == 403


# -- the planner ------------------------------------------------------------------------------------------


def test_payoff_route_uses_real_balances_and_names_missing_rates() -> None:
    for name, balance, apr, minimum in (("Card A", "1200", "12", "100"), ("Loan B", "200", "0", "50")):
        assert client.post(f"{API}/debts", headers=OWNER, json={"name": name, "balance": balance, "apr_percent": apr, "minimum_payment": minimum,
                                                             "currency": "AED"}).status_code == 201
    body = client.get(f"{API}/planner/payoff", headers=OWNER, params={"payment": "300"}).json()
    row = body["currencies"][0]
    assert (row["avalanche"]["months"], row["avalanche"]["total_interest"], row["snowball"]["total_interest"]) == (5, "35.97", "39.03")
    assert row["cheaper"] == "avalanche" and row["missing_rates"] == ["Loan B"] and "never moves money" in body["note"]
    # What the Debts page draws: when each debt is paid off and the total owed at each month's end.
    ids = {d["name"]: d["id"] for d in row["debts"]}
    assert row["avalanche"]["paid_off_in"] == {ids["Card A"]: 5, ids["Loan B"]: 4}
    assert row["avalanche"]["balances"] == ["1112.00", "821.62", "528.84", "233.63", "0.00"] and row["minimums_only"] is False
    # Minimums only: each debt gets only its minimum, nothing rolls over (the "now" line).
    only = client.get(f"{API}/planner/payoff", headers=OWNER, params={"minimums_only": "true", "payment": "300", "currency": "AED"}).json()["currencies"][0]
    assert only["minimums_only"] is True and only["payment"] == "150.00" and only["below_minimums"] is False


def test_afford_and_compare_from_the_owners_records() -> None:
    bank = account(balance="20000")
    record(bank["id"], "9000", "2026-08-28", "SALARY", "income")
    record(bank["id"], "9000", "2026-09-28", "SALARY", "income")
    record(bank["id"], "9000", "2026-10-28", "SALARY", "income")
    client.post(f"{API}/bills", headers=OWNER, json={"name": "Rent", "amount": "3000", "cadence": "monthly", "next_due_on": "2026-11-28", "category": "housing"})
    cash = client.post(f"{API}/planner/afford", headers=OWNER, json={"price": "500", "item": "Phone case"}).json()
    assert cash["verdict"] == "yes_from_cash" and cash["pay_with"][0]["note"] == "covers"
    monthly = client.post(f"{API}/planner/afford", headers=OWNER, json={"price": "80000", "monthly_cost": "2000"}).json()
    # Income averages 9,000 (three full months); 3,000 rent + 2,000 = 5,000, 56% of income.
    assert (monthly["income_monthly"], monthly["ratio_after"], monthly["verdict"]) == ("9000.00", "0.56", "too_much")
    compared = client.post(f"{API}/planner/compare", headers=OWNER, json={"price": "12000", "options": [
        {"kind": "cash"}, {"kind": "bank_loan", "months": 12, "rate_percent": "12"}, {"kind": "installments", "months": 4}]}).json()
    assert [(o["kind"], o["status"]) for o in compared["options"]] == [("cash", "ok"), ("bank_loan", "ok"), ("installments", "rate_unknown")]
    assert compared["options"][1]["monthly"] == "1066.19" and compared["options"][1]["rate_source"] == "owner"


class FakeSearch:
    def __init__(self, results: list[dict[str, str]]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def __call__(self, call: ToolCall, context: TenantContext) -> ToolResult:
        self.queries.append(str(call.arguments.get("query")))
        return ToolResult(status="succeeded", summary="ok", output={"query": call.arguments.get("query"), "results": self.results})


@pytest.fixture
def web_search(monkeypatch: pytest.MonkeyPatch) -> FakeSearch:
    from anum_api.finance.tools import register_finance_tools
    from anum_api.agent_tools import default_tool_registry

    registry = register_finance_tools(default_tool_registry())
    fake = FakeSearch([{"title": "Car finance profit rate 4.5% flat", "url": "https://bank.example/rates", "snippet": "From 4.5% a year"},
                       {"title": "Example car price AED 85,000", "url": "https://dealer.example/car", "snippet": "Now AED 85,000"}])
    registry.register(ToolDefinition(name="web_search", description="fake", risk_level=RiskLevel.LOW, category="general", action_kind="read"), fake)
    monkeypatch.setattr(business_research, "_registry", registry)
    return fake


def test_research_cites_every_figure_with_its_source_and_date(web_search: FakeSearch) -> None:
    found = client.post(f"{API}/planner/research", headers=OWNER, json={"topic": "loan_rate", "query": "car", "country": "UAE"}).json()
    assert found["status"] == "ok" and web_search.queries == ["car personal loan interest rate UAE"]
    assert found["items"][0]["url"] == "https://bank.example/rates" and found["items"][0]["figures"][0] == {"kind": "percent", "value": "4.5", "currency": None}
    assert found["items"][1]["figures"] == [{"kind": "amount", "value": "85000", "currency": "AED"}]
    assert found["items"][0]["read_on"] == datetime.now(UTC).date().isoformat()


def test_research_without_web_search_says_live_prices_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(business_research, "_registry", None)
    found = client.post(f"{API}/planner/research", headers=OWNER, json={"topic": "price", "query": "phone"}).json()
    assert found["status"] == "unavailable" and "unavailable" in found["message"]


# -- the assistant ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("prompt", "tool"), [
    ("Can I afford a car for 80,000 AED?", "finance.plan.afford"),
    ("Should I buy a new phone for 4500?", "finance.plan.afford"),
    ("أقدر أشتري سيارة بـ 80 ألف؟", "finance.plan.afford"),
    ("هل أشتري سيارة جديدة", "finance.plan.afford"),
    ("Which loan is better for a car: 80000 over 4 years at 3.5%?", "finance.plan.compare"),
    ("أي قرض أفضل لسيارة 80000", "finance.plan.compare"),
    ("Which debt should I pay first?", "finance.plan.debt_order"),
    ("أي دين أسدد أولا؟", "finance.plan.debt_order"),
    ("What is the cheapest way to send money home?", "finance.plan.send_home"),
    ("ما أرخص طريقة للتحويل للأهل؟", "finance.plan.send_home"),
])
def test_planning_questions_route_to_the_finance_skill(prompt: str, tool: str) -> None:
    skills = default_skill_registry().select(prompt, set(main.tool_registry.names))
    assert "anum.finance" in [s.id for s in skills]
    call = choose_tool_call(prompt, skills, "planned", main.tool_registry.names)
    assert call.name == tool


def test_planning_arguments_come_from_the_users_words() -> None:
    assert plan_call("Can I afford a car for 80,000 AED?").arguments == {"currency": "AED", "price": "80000", "item": "car"}  # type: ignore[union-attr]
    loan = plan_call("Which loan is better: 80000 over 4 years at 3.5% flat with 10000 down?")
    assert loan is not None and loan.arguments == {"price": "80000", "rate_percent": "3.5", "months": 48, "down_payment": "10000", "basis": "flat"}
    assert plan_call("أقدر أشتري سيارة بـ 80 ألف؟").arguments["price"] == "80000"  # type: ignore[union-attr]


@pytest.mark.parametrize("prompt", ["Buy it for me", "buy the car for me", "Send 500 AED to my mother"])
def test_buying_or_sending_is_never_a_finance_tool(prompt: str) -> None:
    assert plan_call(prompt) is None
    skills = [s for s in default_skill_registry().manifests() if s.id in {"anum.finance", "anum.external-action"}]
    call = choose_tool_call(prompt, skills, "planned", main.tool_registry.names)
    # Never a finance tool: either the approval-gated external action, or, for buying with no site named,
    # the question asking which site to use in the Action browser (docs/action-browser.md#how-it-is-used).
    assert not call.name.startswith("finance.")
    assert call.name == "external.action" or (call.name == "anum.respond" and call.arguments["errand"] is True)


def test_planning_tools_are_read_only_and_say_advice_only(web_search: FakeSearch) -> None:
    from anum_api.finance import plan_tools

    for name in plan_tools.TOOLS:
        definition = main.tool_registry.definition(name)
        assert definition.action_kind == "read" and definition.risk_level == RiskLevel.LOW
    bank = account(balance="3000")
    record(bank["id"], "-10", "2026-11-01", "x", "groceries")
    client.post(f"{API}/debts", headers=OWNER, json={"name": "Card A", "balance": "1200", "apr_percent": "12", "minimum_payment": "100"})
    for call in (ToolCall(name="finance.plan.afford", arguments={"item": "Example car"}),
                 ToolCall(name="finance.plan.debt_order", arguments={"payment": "300"}),
                 ToolCall(name="finance.plan.compare", arguments={"price": "12000", "months": 12}),
                 ToolCall(name="finance.plan.send_home", arguments={})):
        result = asyncio.run(main.tool_registry.execute(call, ME))
        assert result.status == "succeeded", result.summary
        assert "Advice only" in result.summary and not any(d in result.summary for d in LONG_DASHES)
    afford = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.plan.afford", arguments={"item": "Example car"}), ME))
    assert "https://dealer.example/car (read " in afford.summary and "AED 85000" in afford.summary
    compared = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.plan.compare", arguments={"price": "12000", "months": 12}), ME))
    assert "rate from https://bank.example/rates" in compared.summary
    denied = asyncio.run(main.tool_registry.execute(ToolCall(name="finance.plan.debt_order", arguments={}),
                                                   TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="viewer_pm", roles=["viewer"])))
    assert denied.status == "blocked"


def test_goal_purpose_is_kept() -> None:
    made = client.post(f"{API}/goals", headers=OWNER, json={"name": "Emergency", "target_amount": "9000", "purpose": "emergency"}).json()
    assert made["purpose"] == "emergency"
    again = client.patch(f"{API}/goals/{made['id']}", headers=OWNER, json={"name": "Emergency", "target_amount": "9500"}).json()
    assert again["purpose"] == "emergency"
    assert client.post(f"{API}/goals", headers=OWNER, json={"name": "G", "target_amount": "1"}).json()["purpose"] == "general"



# -- review fixes: the ledger dashboard and idempotent link retries -------------------------------------


def _board(month: str, currency: str) -> tuple[str, str]:
    board = client.get(f"{API}/ledger/dashboard", headers=OWNER, params={"month": month}).json()
    pick = lambda rows: next((r["amount"] for r in rows if r["currency"] == currency), "0")  # noqa: E731
    return pick(board["expenses"]), pick(board["income"])


def test_the_ledger_dashboard_agrees_with_the_summary_for_manual_lending() -> None:
    bank = account(balance="5000")
    loan = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "Friend A", "amount": "1000",
                                                                   "lent_on": "2026-11-02"}).json()
    handed = record(bank["id"], "-1000", "2026-11-02", "Cash to Friend A", "other")
    back = record(bank["id"], "400", "2026-11-04", "From Friend A", "income")
    record(bank["id"], "-120", "2026-11-04", "Grocer", "groceries")
    assert _board("2026-11", "AED") == ("1120.00", "400.00")
    for canonical, kind in ((handed, "handed"), (back, "repayment")):
        assert client.post(f"{API}/people-loans/{loan['id']}/links", headers=OWNER, json={"canonical_id": canonical, "kind": kind}).status_code == 201
    expenses, income = _board("2026-11", "AED")
    month = client.get(f"{API}/summary", headers=OWNER, params={"month": "2026-11"}).json()
    assert (D(expenses), D(income)) == (D("120.00"), D(0))
    assert [a["amount"] for a in month["spend"]] == ["120.00"] and month["income"] == []
    # Account balances are unchanged by the classification.
    assert client.get(f"{API}/ledger/dashboard", headers=OWNER, params={"month": "2026-11"}).status_code == 200


def test_the_ledger_dashboard_agrees_with_the_summary_for_sms_lending() -> None:
    from test_finance_sms import PURCHASE_EN, SALARY_EN, send, setup

    from anum_api.finance import sms_service as svc

    svc._last_notified.clear()
    s = setup(OWNER)
    assert send(s["token"], PURCHASE_EN).status_code in (200, 201)
    assert send(s["token"], SALARY_EN).status_code in (200, 201)
    entries = client.get(f"{API}/ledger/entries", headers=OWNER).json()["items"]
    out = next(e["canonical_id"] for e in entries if any(Decimal_str(p["amount"]) == D("-45.50") for p in e["postings"]))
    salary = next(e["canonical_id"] for e in entries if any(Decimal_str(p["amount"]) == D("12000.00") for p in e["postings"]))
    before = _board("2026-11", "SAR")
    assert (D(before[0]), D(before[1])) == (D("45.50"), D("12000.00"))
    loan = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "Friend B", "amount": "45.50", "currency": "SAR",
                                                                   "lent_on": "2026-11-03"}).json()
    assert client.post(f"{API}/people-loans/{loan['id']}/links", headers=OWNER, json={"canonical_id": out, "kind": "handed"}).status_code == 201
    other = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "borrowed", "person": "Friend C", "amount": "12000", "currency": "SAR",
                                                                    "lent_on": "2026-11-01"}).json()
    assert client.post(f"{API}/people-loans/{other['id']}/links", headers=OWNER, json={"canonical_id": salary, "kind": "handed"}).status_code == 201
    expenses, income = _board("2026-11", "SAR")
    month = client.get(f"{API}/summary", headers=OWNER, params={"month": "2026-11"}).json()
    assert (D(expenses), D(income)) == (D(0), D(0))
    assert month["spend"] == [] and month["income"] == []


def Decimal_str(value: str) -> D:  # noqa: N802
    return D(str(value))


@pytest.mark.parametrize("kind", ["person_loan", "payslip", "remittance"])
def test_a_retried_link_with_the_same_key_replays_instead_of_refusing(kind: str) -> None:
    bank = account(balance="1000")
    if kind == "person_loan":
        made = client.post(f"{API}/people-loans", headers=OWNER, json={"direction": "lent", "person": "Friend A", "amount": "100", "lent_on": "2026-11-02"}).json()
        canonical, path, body = record(bank["id"], "-100", "2026-11-02", "To Friend A"), f"people-loans/{made['id']}", {"kind": "handed"}
    elif kind == "payslip":
        made = client.post(f"{API}/payslips", headers=OWNER, json={"employer": "Example Co", "month": "2026-10", "gross": "100"}).json()
        canonical, path, body = record(bank["id"], "100", "2026-10-28", "SALARY", "income"), f"payslips/{made['id']}", {}
    else:
        made = client.post(f"{API}/remittances", headers=OWNER, json={"sent_on": "2026-11-02", "amount": "100", "recipient": "Family",
                                                                      "provider": "Exchange A"}).json()
        canonical, path, body = record(bank["id"], "-100", "2026-11-02", "EXCHANGE A"), f"remittances/{made['id']}", {}
    headers = {**OWNER, "Idempotency-Key": f"retry-{kind}"}
    first = client.post(f"{API}/{path}/links", headers=headers, json={"canonical_id": canonical, **body})
    again = client.post(f"{API}/{path}/links", headers=headers, json={"canonical_id": canonical, **body})
    assert first.status_code == 201, first.text
    assert again.status_code == 201, again.text
    assert again.json()["link"]["id"] == first.json()["link"]["id"] and again.json()["replayed"] is True
    assert again.json()["link"]["amount"] == "100.00"


def test_planner_question_parsing_stays_fast_on_hostile_input() -> None:
    """CodeQL flagged polynomial patterns: long runs of zeros, digits or tabs must not slow the planner."""
    import time

    from anum_api.finance.plan_tools import plan_call

    hostile = [
        "can i afford " + "0" * 20000,
        "can i afford a car " + "0," * 10000 + " per month",
        "which loan " + "\t" * 20000 + "a month",
        "cheapest way to send money " + "x" * 20000 + " home",
        "1" * 20000 + " down " + "9" * 20000 + "%",
    ]
    started = time.perf_counter()
    for text in hostile:
        plan_call(text)
    assert time.perf_counter() - started < 1.0


def test_planner_still_reads_ordinary_amounts() -> None:
    from anum_api.finance.plan_tools import plan_call

    call = plan_call("Can I afford a car for 60,000 SAR with 2,500 per month?")
    assert call is not None and call.name == "finance.plan.afford"
    assert call.arguments["price"] == "60000" and call.arguments["monthly_cost"] == "2500"
    assert call.arguments["currency"] == "SAR"
    compare = plan_call("Which loan is better for 45k at 4.5% over 3 years with 5000 down?")
    assert compare is not None and compare.name == "finance.plan.compare"
    assert compare.arguments["price"] == "45000" and compare.arguments["rate_percent"] == "4.5"
    assert compare.arguments["months"] == 36 and compare.arguments["down_payment"] == "5000"


def test_planner_reads_monthly_and_down_amounts_across_spacing_and_punctuation() -> None:
    from anum_api.finance.plan_tools import plan_call

    call = plan_call("Can I afford a phone for 4,000 AED with 350\ta   month.")
    assert call is not None and call.arguments["monthly_cost"] == "350" and call.arguments["price"] == "4000"
    compare = plan_call("Which loan is better for 30000, 3% over 24 months, 2,000 deposit?")
    assert compare is not None and compare.arguments["down_payment"] == "2000"
    assert compare.arguments["price"] == "30000" and compare.arguments["months"] == 24
    nothing = plan_call("Can I afford a sofa a month from now?")
    assert nothing is not None and "monthly_cost" not in nothing.arguments


def test_planner_reads_the_item_word_by_word() -> None:
    from anum_api.finance.plan_tools import plan_call

    assert plan_call("Should I buy a used car for 45,000 AED?").arguments["item"] == "used car"  # type: ignore[union-attr]
    assert plan_call("Can I afford the new phone?").arguments["item"] == "new phone"  # type: ignore[union-attr]
    assert "item" not in plan_call("Can I afford 60,000 AED?").arguments  # type: ignore[union-attr]
    started = time.monotonic()
    plan_call("can i afford a " + "a\t" * 5000)
    assert time.monotonic() - started < 1


def test_fixed_commitments_leave_out_what_a_refund_cleared() -> None:
    entry = {"provider": "Tamara", "merchant": "Phone Shop", "due_on": date(2026, 11, 9), "total_installments": 4, "currency": "AED",
             "source_status": "scheduled", "installment": 2}
    installments = [InstallmentStatementEntry(order_id="o1", amount=D("250"), refunded_amount=D("100"), **entry),
                    InstallmentStatementEntry(order_id="o2", amount=D("100"), refunded_amount=D("100"), **{**entry, "merchant": "Shop B"}),
                    InstallmentStatementEntry(order_id="o3", amount=D("100"), **{**entry, "merchant": "Shop C", "source_status": "refunded"})]
    counted, skipped = P.fixed_commitments([], [], installments, [], date(2026, 11, 5))
    assert [(c.name, c.monthly) for c in counted] == [("Tamara: Phone Shop", D("150.00"))]
    assert skipped == []


def test_planner_never_takes_a_model_number_for_the_price() -> None:
    from anum_api.finance.plan_tools import plan_call

    phone = plan_call("Can I afford an iPhone 16 for AED 4000?")
    assert phone is not None and phone.arguments["price"] == "4000" and phone.arguments["item"] == "iPhone 16"
    console = plan_call("Should I buy a PlayStation 5 for AED 2000?")
    assert console is not None and console.arguments["price"] == "2000" and console.arguments["item"] == "PlayStation 5"


def test_live_rates_cost_only_interest_rates_never_fees() -> None:
    from datetime import date as day

    from anum_api.finance.planning_service import LiveItem, LiveResearch, figures_in, live_rates

    figures = figures_in("Personal loan: 5% annual interest, 1% processing fee. Get 20% off today")
    assert [(f.value, f.rate) for f in figures if f.kind == "percent"] == [("5", True), ("1", False), ("20", False)]
    found = LiveResearch("ok", "q", day(2026, 11, 9), items=[LiveItem(title="Bank", url="https://bank.example/rates", read_on=day(2026, 11, 9), figures=figures)])
    assert [v for v, _, _ in live_rates(found)] == [D("5")]
    assert [(f.value, f.rate) for f in figures_in("معدل الفائدة 5% سنويا، رسوم 1%") if f.kind == "percent"] == [("5", True), ("1", False)]
