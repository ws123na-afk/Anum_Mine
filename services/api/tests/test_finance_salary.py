"""The salary profile, the month by month salary check and the one income figure (docs/finance.md#salary).

Pure checks of ``finance/salary.py`` with a fixed day, then the routes with the in-memory stores (dates are
worked out from the real day the server uses, so they hold on any date). Every record is SYNTHETIC: a fake
employer, fake amounts and a fake SAR salary account.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.dependencies import memory_repository
from anum_api.finance import personal as P
from anum_api.finance import salary as SAL
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.models import Account, AccountKind, Category, CategorySource, Transaction
from anum_api.finance.personal_store import personal_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import WorkspaceMembership

TENANT, WORKSPACE = "tenant_sal", "workspace_sal"
SCOPE = {"tenant_id": TENANT, "workspace_id": WORKSPACE, "owner_user_id": "owner_sal"}
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_sal", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-user-id": "other_sal"}
VIEWER = {**OWNER, "x-user-id": "viewer_sal", "x-user-roles": "viewer"}
API = "/api/v1/finance"
STAMP = datetime(2026, 1, 1, tzinfo=UTC)
TODAY = date(2026, 11, 9)
client = TestClient(main.app)
_seq = itertools.count(1)

BANK = Account(id="sal_bank", name="Salary account", kind=AccountKind.BANK, currency="SAR", created_at=STAMP, updated_at=STAMP, **SCOPE)
OTHER_BANK = Account(id="spare", name="Spare", kind=AccountKind.BANK, currency="SAR", created_at=STAMP, updated_at=STAMP, **SCOPE)


def txn(day: date, amount: str, description: str = "Example Trading Co salary", account: str = "sal_bank", currency: str = "SAR") -> Transaction:
    n = next(_seq)
    return Transaction(id=f"tx{n}", account_id=account, posted_on=day, amount=D(amount), currency=currency, description=description,
                       category=Category.INCOME, category_source=CategorySource.RULE, dedupe_hash=f"h{n}", created_at=STAMP, updated_at=STAMP, **SCOPE)


def profile(**over: object) -> P.SalaryProfile:
    values: dict = {"id": "sp", "employer": "Example Trading Co", "joined_on": date(2026, 7, 10), "currency": "SAR", "expected_gross": D("10000"),
                    "pay_day": 27, "account_id": "sal_bank", "created_at": STAMP, "updated_at": STAMP, **SCOPE}
    values.update(over)
    return P.SalaryProfile(**values)


def rule(kind: str, amount: str, starts: str, **over: object) -> P.SalaryRule:
    return P.SalaryRule(id=f"r{next(_seq)}", kind=P.SalaryRuleKind(kind), amount=D(amount), starts=starts, **over)


# -- what is expected ------------------------------------------------------------------------------------


def test_expected_net_is_gross_less_deductions_and_the_advance_repayment() -> None:
    p = profile(rules=[
        rule("deduction", "450", "2026-07", reason=P.SalaryDeductionReason.SOCIAL_INSURANCE, label="GOSI"),
        rule("deduction", "300", "2026-09", ends="2026-09", reason=P.SalaryDeductionReason.ABSENCE),
        rule("advance", "1000", "2026-09", total=D("2500"), received_on=date(2026, 8, 15)),
    ])
    assert SAL.expected_for(p, "2026-08", []).net == D("9550.00")
    september = SAL.expected_for(p, "2026-09", [])
    assert (september.net, september.advance_repayment, len(september.deductions)) == (D("8250.00"), D("1000.00"), 2)
    # The advance of 2,500 is repaid 1,000, 1,000 and the last 500; then nothing.
    assert [SAL.expected_for(p, m, []).advance_repayment for m in ("2026-10", "2026-11", "2026-12")] == [D("1000.00"), D("500.00"), D("0.00")]
    assert SAL.advance_left(p.rules[2], "2026-10") == D("500")


def test_a_payslip_overrides_its_month() -> None:
    slip = P.Payslip(id="ps", employer="Example Trading Co", month="2026-10", currency="SAR", gross=D("10500"),
                     deductions=[P.Deduction(kind=P.DeductionKind.SOCIAL_INSURANCE, amount=D("472.50"))], created_at=STAMP, updated_at=STAMP, **SCOPE)
    expected = SAL.expected_for(profile(), "2026-10", [slip])
    assert (expected.payslip, expected.gross, expected.net) == (True, D("10500.00"), D("10027.50"))
    assert SAL.expected_for(profile(), "2026-09", [slip]).payslip is False


# -- detection ---------------------------------------------------------------------------------------------


def test_each_month_from_joining_is_found_flagged_or_missing() -> None:
    rows = [
        txn(date(2026, 7, 27), "10000"),  # July: on the pay day
        txn(date(2026, 8, 26), "10000", "TRF FROM EXAMPLE TRADING CO"),  # August: the employer's name
        txn(date(2026, 9, 3), "10000"),  # not September's: before the window
        txn(date(2026, 10, 3), "10000"),  # September, paid a week late (inside the window)
        txn(date(2026, 8, 27), "10000", "Example Trading Co salary", account="spare"),  # another account: never
        txn(date(2026, 7, 26), "10000", currency="AED"),  # another currency: never
    ]
    months = SAL.salary_months(profile(), rows, [BANK, OTHER_BANK], TODAY, [])
    by = {m.month: m for m in months}
    assert list(by) == ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]
    assert (by["2026-07"].status, by["2026-07"].transaction.confidence, by["2026-07"].basis) == ("received", "high", "suggested")
    assert by["2026-08"].status == "received" and by["2026-08"].transaction.posted_on == date(2026, 8, 26)
    assert (by["2026-09"].status, by["2026-09"].flags) == ("late", ["late"])
    # October: nothing near the 27th and the pay day has passed (today is 9 November).
    assert (by["2026-10"].status, by["2026-10"].flags, by["2026-10"].received) == ("missing", ["missing"], D("0.00"))
    assert by["2026-11"].status == "upcoming"


def test_partial_salary_and_the_usual_payer() -> None:
    rows = [txn(date(2026, 8, 27), "10000", "PAYROLL EXAMPLE TRADING"), txn(date(2026, 9, 27), "6000", "PAYROLL EXAMPLE TRADING")]
    p = profile(decisions=[P.SalaryDecision(month="2026-08", transaction_id=rows[0].id, decision="confirmed", decided_at=STAMP)],
                joined_on=date(2026, 8, 1))
    months = {m.month: m for m in SAL.salary_months(p, rows, [BANK], date(2026, 10, 1), [])}
    assert months["2026-08"].basis == "confirmed"
    september = months["2026-09"]
    assert (september.status, september.flags, september.received) == ("partial", ["partial"], D("6000.00"))


def test_a_rejected_transaction_is_never_suggested_again_and_a_confirmed_one_wins() -> None:
    right, wrong = txn(date(2026, 8, 25), "9900", "Transfer in"), txn(date(2026, 8, 27), "10000", "Example Trading Co salary")
    p = profile(joined_on=date(2026, 8, 1), decisions=[P.SalaryDecision(month="2026-08", transaction_id=wrong.id, decision="rejected", decided_at=STAMP)])
    month = SAL.salary_months(p, [right, wrong], [BANK], date(2026, 8, 31), [])[0]
    assert month.transaction.transaction_id == right.id and month.rejected == [wrong.id]
    confirmed = p.model_copy(update={"decisions": [*p.decisions, P.SalaryDecision(month="2026-08", transaction_id=right.id, decision="confirmed",
                                                                                   decided_at=STAMP)]})
    assert SAL.salary_months(confirmed, [right, wrong], [BANK], date(2026, 8, 31), [])[0].basis == "confirmed"


def test_an_advance_or_money_from_a_person_is_never_the_salary() -> None:
    advance, lent_back = txn(date(2026, 8, 25), "2500", "Example Trading Co advance"), txn(date(2026, 8, 27), "10000", "From a cousin")
    p = profile(joined_on=date(2026, 8, 1), expected_gross=D("10000"),
                rules=[rule("advance", "2500", "2026-09", total=D("2500"), received_on=date(2026, 8, 25))])
    month = SAL.salary_months(p, [advance, lent_back], [BANK], date(2026, 8, 31), [], lending_ids=frozenset({lent_back.id}))[0]
    assert month.status == "missing" and month.transaction is None


def test_a_credit_linked_to_the_payslip_is_that_months_salary() -> None:
    credit = txn(date(2026, 8, 20), "4000", "Bonus transfer")
    month = SAL.salary_months(profile(joined_on=date(2026, 8, 1)), [credit], [BANK], date(2026, 8, 31), [],
                              payslip_credits={"2026-08": {credit.id}})[0]
    assert (month.basis, month.received) == ("payslip", D("4000.00"))


def test_the_one_income_figure_prefers_the_salary_profile() -> None:
    rows = [txn(date(2026, m, 27), "8000", "Other income") for m in (8, 9, 10)]
    p = profile(rules=[rule("deduction", "450", "2026-07", reason=P.SalaryDeductionReason.SOCIAL_INSURANCE)])
    with_profile = SAL.monthly_income(currency="SAR", today=TODAY, transactions=rows, accounts=[BANK], lending_ids=frozenset(), profile=p, payslips=[])
    assert (with_profile.amount, with_profile.source, with_profile.next_pay_on) == (D("9550.00"), "salary_profile", date(2026, 11, 27))
    records = SAL.monthly_income(currency="SAR", today=TODAY, transactions=rows, accounts=[BANK], lending_ids=frozenset(), profile=None, payslips=[])
    assert (records.amount, records.source) == (D("8000.00"), "records")
    other = SAL.monthly_income(currency="AED", today=TODAY, transactions=rows, accounts=[BANK], lending_ids=frozenset(), profile=p, payslips=[])
    assert (other.amount, other.source) == (None, None)


# -- the routes ----------------------------------------------------------------------------------------


@pytest.fixture()
def owner() -> Iterator[None]:
    for store in (finance_store, sms_memory_store, links_memory_store, personal_memory_store):
        store.clear()
    saved = dict(memory_repository.store.memberships)
    for user, role in (("owner_sal", "owner"), ("other_sal", "owner"), ("viewer_sal", "viewer")):
        memory_repository.store.memberships[(TENANT, WORKSPACE, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, active=True, created_at=STAMP, updated_at=STAMP)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(saved)
    for store in (finance_store, sms_memory_store, links_memory_store, personal_memory_store):
        store.clear()


def _server_today() -> date:
    settings = client.get(f"{API}/summary", headers=OWNER).json()
    return date.fromisoformat(settings["today"])


def _month_start(day: date, back: int) -> date:
    index = day.year * 12 + day.month - 1 - back
    return date(index // 12, index % 12 + 1, 1)


def test_the_salary_routes_find_past_months_and_remember_decisions(owner: None) -> None:
    bank = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Salary account", "kind": "bank", "currency": "SAR"}).json()
    today = _server_today()
    joined = _month_start(today, 3)
    body = {"employer": "Example Trading Co", "joined_on": joined.isoformat(), "currency": "SAR", "expected_gross": "10000", "pay_day": 15,
            "account_id": bank["id"], "rules": [{"kind": "deduction", "reason": "social_insurance", "label": "GOSI", "amount": "450",
                                                  "starts": joined.isoformat()[:7]}]}
    assert client.put(f"{API}/salary/profile", headers=VIEWER, json=body).status_code == 403
    made = client.put(f"{API}/salary/profile", headers=OWNER, json=body)
    assert made.status_code == 200, made.text
    assert (made.json()["expected_net"], made.json()["currency"]) == ("9550.00", "SAR")
    # The salary of each of the three full months before this one, on the 15th, recorded by hand.
    ids = []
    for back in (3, 2, 1):
        day = _month_start(today, back).replace(day=15)
        made_tx = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": bank["id"], "posted_on": day.isoformat(), "amount": "9550",
                                                                          "description": "Example Trading Co salary", "category": "income"})
        assert made_tx.status_code == 201, made_tx.text
        ids.append(made_tx.json()["id"])
    page = client.get(f"{API}/salary/months", headers=OWNER).json()
    past = {m["month"]: m for m in page["months"]}
    first = joined.isoformat()[:7]
    assert past[first]["status"] == "received" and past[first]["found"]["transaction_id"] == ids[0] and past[first]["basis"] == "suggested"
    assert past[first]["expected"]["net"] == "9550.00" and past[first]["found"]["confidence"] == "high"
    assert page["waiting"] >= 3 and page["currency"] == "SAR"
    # Reject the first month's: it is remembered and the month goes missing; confirming the second makes it the salary.
    rejected = client.post(f"{API}/salary/months/{first}/decision", headers=OWNER, json={"transaction_id": ids[0], "decision": "reject"})
    assert rejected.status_code == 200, rejected.text
    second = _month_start(today, 2).isoformat()[:7]
    assert client.post(f"{API}/salary/months/{second}/decision", headers=OWNER, json={"transaction_id": ids[1], "decision": "confirm"}).status_code == 200
    past = {m["month"]: m for m in client.get(f"{API}/salary/months", headers=OWNER).json()["months"]}
    assert (past[first]["status"], past[first]["rejected"]) == ("missing", 1) and past[second]["basis"] == "confirmed"
    # Undo brings the suggestion back.
    assert client.delete(f"{API}/salary/months/{first}/decision", headers=OWNER, params={"transaction_id": ids[0]}).status_code == 200
    assert {m["month"]: m for m in client.get(f"{API}/salary/months", headers=OWNER).json()["months"]}[first]["status"] == "received"
    # The profile is the owner's own; a viewer reads nothing they may not.
    assert client.get(f"{API}/salary/profile", headers=OTHER).json() == {"profile": None}
    assert client.get(f"{API}/salary/months", headers=VIEWER).status_code == 403
    # One income figure: Fixed costs' share and Safe to spend's payday read the profile.
    client.post(f"{API}/bills", headers=OWNER, json={"name": "Rent", "amount": "2865", "currency": "SAR", "cadence": "monthly",
                                                     "next_due_on": (today + timedelta(days=3)).isoformat(), "category": "housing"})
    fixed = client.get(f"{API}/fixed-commitments", headers=OWNER).json()
    assert fixed["income"] == [{"currency": "SAR", "monthly": "9550.00", "source": "salary_profile", "share_percent": 30}]
    safe = next(c for c in client.get(f"{API}/safe-to-spend", headers=OWNER).json()["currencies"] if c["currency"] == "SAR")
    assert safe["expected_pay"] == "9550.00" and safe["next_pay_on"] >= today.isoformat()
    assert client.delete(f"{API}/salary/profile", headers=OWNER).status_code == 204
    assert client.get(f"{API}/salary/profile", headers=OWNER).json() == {"profile": None}


def test_a_profile_is_refused_for_a_future_joining_date_or_an_account_in_another_currency(owner: None) -> None:
    aed = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Current", "kind": "bank", "currency": "AED"}).json()
    base = {"employer": "Example Trading Co", "currency": "SAR", "expected_gross": "10000", "pay_day": 27}
    future = (_server_today() + timedelta(days=40)).isoformat()
    assert client.put(f"{API}/salary/profile", headers=OWNER, json={**base, "joined_on": future}).status_code == 422
    wrong = client.put(f"{API}/salary/profile", headers=OWNER, json={**base, "joined_on": "2026-01-05", "account_id": aed["id"]})
    assert wrong.status_code == 422 and "SAR" in wrong.text
    over = client.put(f"{API}/salary/profile", headers=OWNER, json={**base, "joined_on": "2026-01-05", "rules": [
        {"kind": "advance", "amount": "3000", "total": "2000", "starts": "2026-02"}]})
    assert over.status_code == 422


def test_the_advisor_reads_the_salary_profile_and_names_a_missing_month() -> None:
    from anum_api.finance import advisor as A

    rows = [txn(date(2026, 8, 27), "10000"), txn(date(2026, 9, 27), "10000")]
    inputs = A.AdvisorInputs(today=TODAY, timezone="Asia/Riyadh", base_currency="SAR", accounts=[BANK], balances={}, transactions=rows, bills=[],
                             debts=[], salary_profile=profile(joined_on=date(2026, 8, 1)))
    note = A.build_note(inputs)
    assert (note.income.source, note.income.monthly, note.income.next_pay_on) == ("salary_profile", D("10000.00"), date(2026, 11, 27))
    assert note.income.missing_months == ["2026-10"] and note.income.salary_found
    action = next(a for a in note.actions if a.code == "salary_missing")
    assert action.text_en.startswith("No salary found for October 2026, expected around 27 October.")
