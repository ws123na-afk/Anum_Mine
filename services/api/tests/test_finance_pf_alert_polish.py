"""Financial alert polish (docs/finance.md#financial-alerts, #alert-channels, #budget-rollover).

Budgets judged against what is available with rollover (the Guardian caps, their gate, review and alerts, and the
planned budgets), a borrowed person loan with its own reminder told once, alert emails in the person's email language
(English, Arabic, or both) with no figures, and the failed data capture alert told once per message or import. Memory
backend; SMTP faked; every record is SYNTHETIC and low entropy.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_finance_alert_channels import ADDRESS, _reset, mailbox  # noqa: F401  (fixtures)
from test_finance_personal_money import ME, OWNER, clean, client  # noqa: F401
from test_finance_pf_budgets import budget, row

from anum_api.finance import alert_channels as C
from anum_api.finance import alerts as A
from anum_api.finance import budget_rollover as RO
from anum_api.finance.commitments import budget_report, plan_budget_report
from anum_api.finance.flows import spend_rows
from anum_api.finance.guardian import ActionKind, CategoryCap, GuardianAction, GuardianRules, LedgerState, SpendRow, evaluate
from anum_api.finance.guardian_review import flag
from anum_api.finance.models import BudgetTerms, Category, OverspendMode, RolloverMode, TransactionType
from anum_api.onboarding import NotificationPreferences, _notifications
from anum_api.schemas import utc_now

D = Decimal
TODAY = date(2026, 11, 15)
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)
G = Category.GROCERIES
API = "/api/v1/finance"


def _terms(rollover: RolloverMode = RolloverMode.SURPLUS, overspend: OverspendMode = OverspendMode.ABSORB, budget_id: str = "food") -> BudgetTerms:
    return BudgetTerms(budget_id=budget_id, effective_from=date(2026, 8, 1), currency="AED", amount=D(1000), categories=[G], rollover=rollover,
                       overspend=overspend, set_at=NOW)


def _spend(month: int, amount: str, day: int = 10) -> object:
    return row(f"g{month}_{day}_{amount}", f"-{amount}", "SAMPLE GROCER", date(2026, month, day), category=G, kind=TransactionType.EXPENSE)


# Aug 800, Sep 900, Oct 1100 of a 1000 budget that carries what is left: 200, then 300, then 200 carried into November.
HISTORY = [_spend(8, "800"), _spend(9, "900"), _spend(10, "1100")]
CAP = CategoryCap(category=G, currency="AED", amount=D(1000))


def _carry(rows: list, budgets: list, rollovers: list) -> dict:
    return RO.cap_carry([CAP], budgets, rollovers, spend_rows(rows, None, None), on=TODAY, today=TODAY)


# -- budgets against what is available ----------------------------------------------------------------------------------


def test_a_cap_pairs_with_one_matching_rollover_budget_only() -> None:
    food = budget("food", "1000", [G])
    found = _carry(HISTORY, [food], [_terms()])
    assert found[(G, "AED")] == RO.CapCarry("food", D("200.00"))
    assert _carry(HISTORY, [food], []) == {}  # no rollover terms: the cap stays the cap
    twin = budget("food2", "1000", [G])
    assert _carry(HISTORY, [food, twin], [_terms(), _terms(budget_id="food2")]) == {}  # two budgets cannot both decide
    wider = budget("food", "1000", [G, Category.DINING])
    assert _carry(HISTORY, [wider], [_terms()]) == {}  # another category in it: not the cap's month
    assert _carry(HISTORY, [budget("food", "1000", [G], active=False)], [_terms()]) == {}


def test_the_budgets_page_and_the_alert_judge_a_cap_against_the_cap_plus_what_carried_in() -> None:
    november = [*HISTORY, _spend(11, "1100", day=12)]
    carried = _carry(november, [budget("food", "1000", [G])], [_terms()])
    report = budget_report(GuardianRules(category_caps=[CAP]), 1, [], [r for r in november if r.posted_on.month == 11], month="2026-11",
                           start=date(2026, 11, 1), end=date(2026, 11, 30), today=TODAY, carried=carried)
    line = report.budgets[0]
    # 1,100 spent: over the plain cap, but within the 1,200 available, past its 80 percent alert line.
    assert (line.cap, line.carried_in, line.available, line.state, line.over, line.remaining) == (
        D("1000.00"), D("200.00"), D("1200.00"), "near", D("0.00"), D("100.00"))
    assert line.percent == 91 and line.rollover_budget_id == "food"
    plain = budget_report(GuardianRules(category_caps=[CAP]), 1, [], [r for r in november if r.posted_on.month == 11], month="2026-11",
                          start=date(2026, 11, 1), end=date(2026, 11, 30), today=TODAY)
    assert plain.budgets[0].state == "over" and plain.budgets[0].available == D("1000.00")
    found = [a for a in A.compose(A.AlertData(today=TODAY, overview=SimpleNamespace(items=[], calendar=[]), cards=[], accounts=[], rows=[],
                                              budgets=[line]))]
    assert [a.kind for a in found] == ["budget_nearly_exhausted"]
    assert "AED 1,200.00 available" in found[0].body and ":near:" in found[0].key


def test_a_carried_overspend_tightens_the_gate_and_carried_money_left_never_loosens_it() -> None:
    rules = GuardianRules(category_caps=[CAP])

    def decide(spent: str, carried: str, amount: str) -> str:
        state = LedgerState(month_spend=[SpendRow(category=G, currency="AED", amount=D(spent))],
                            carried=[SpendRow(category=G, currency="AED", amount=D(carried))])
        return evaluate(GuardianAction(kind=ActionKind.RECORD, amount=D(amount), currency="AED", category=G, merchant="SAMPLE", at=NOW), rules, state).outcome

    assert decide("600", "0", "200") == "allow"
    assert decide("600", "-300", "200") == "deny"  # 1,000 less last month's 300 overspend leaves 700
    assert decide("1000", "200", "50") == "deny"  # the confirmed cap still holds; only the owner loosens it
    # The review queue judges each payment the same way.
    rows = [_spend(11, "600", day=2), _spend(11, "200", day=3)]
    assert flag(rows, rules, "Asia/Dubai", NOW) == []
    flagged = flag(rows, rules, "Asia/Dubai", NOW, carried=[SpendRow(category=G, currency="AED", amount=D("-300"))])
    assert [t.id for t, _ in flagged] == [rows[1].id]


def test_a_planned_budget_alerts_against_its_available_and_a_paired_one_is_told_once() -> None:
    food = budget("food", "1000", [G])
    rows = [*HISTORY, _spend(11, "1100", day=12)]
    plan = plan_budget_report([food], rows, on=TODAY, today=TODAY, bills=[], bill_links=[], rollovers=[_terms()]).budgets[0]
    assert (plan.available, plan.percent) == (D("1200.00"), 91)
    empty = SimpleNamespace(items=[], calendar=[])
    found = A.compose(A.AlertData(today=TODAY, overview=empty, cards=[], accounts=[], rows=[], plans=[plan]))
    assert [a.key for a in found] == ["plan:food:near:2026-11-01"]
    assert "AED 1,200.00 available (the amount plus AED 200.00 carried in)" in found[0].body
    # Without the carried 200 the same 1,100 would be over; with only 900 spent it is under the line and quiet.
    calm = plan_budget_report([food], [*HISTORY, _spend(11, "900", day=12)], on=TODAY, today=TODAY, bills=[], bill_links=[],
                              rollovers=[_terms()]).budgets[0]
    assert A.compose(A.AlertData(today=TODAY, overview=empty, cards=[], accounts=[], rows=[], plans=[calm])) == []
    # A budget that stands for a Guardian cap is told through the cap's line only.
    cap_line = SimpleNamespace(category=G, currency="AED", cap=D(1000), spent=D(1100), percent=91, state="near", alert_near=True, alert_over=True,
                               available=D(1200), carried_in=D(200), rollover_budget_id="food")
    both = A.compose(A.AlertData(today=TODAY, overview=empty, cards=[], accounts=[], rows=[], budgets=[cap_line], plans=[plan]))
    assert [a.key.split(":")[0] for a in both] == ["budget"]


def test_the_budgets_route_reads_the_carry_from_the_owners_own_plan() -> None:
    from anum_api.finance.store import finance_store

    planning = finance_store.get_planning(ME)
    planning.budgets = [budget("food", "1000", [G])]
    first = date.today().replace(day=1)
    planning.budget_rollovers = [_terms().model_copy(update={"effective_from": first})]
    finance_store.save_planning(ME, planning)
    view = client.get(f"{API}/budgets", headers=OWNER)
    assert view.status_code == 200, view.text
    assert view.json()["budgets"] == []  # no Guardian cap: nothing to judge, and nothing breaks


# -- a person loan told once ------------------------------------------------------------------------------------------


def test_a_borrowed_person_loan_with_its_own_reminder_is_told_by_that_reminder_only() -> None:
    soon = TODAY + timedelta(days=2)
    loan = SimpleNamespace(id="person_loan:p1", direction="owe", type="borrowed_person", history=[], unverified_amount=0, discrepancy=False,
                           verified_balance=None, overdue_amount=D(50), name="SAMPLE PERSON", currency="AED")
    bank = SimpleNamespace(id="debt:d1", direction="owe", type="personal_loan", history=[], unverified_amount=0, discrepancy=False,
                           verified_balance=None, overdue_amount=D(0), name="SAMPLE BANK", currency="AED")
    calendar = [SimpleNamespace(obligation_id=i.id, on=soon, status="due", direction="owe", amount=D(100), currency="AED", name=i.name)
                for i in (loan, bank)]
    overview = SimpleNamespace(items=[loan, bank], calendar=calendar)
    told = A.compose(A.AlertData(today=TODAY, overview=overview, cards=[], accounts=[], rows=[]))
    assert sorted(a.kind for a in told) == ["loan_due_soon", "loan_due_soon", "loan_installment_missed"]
    once = A.compose(A.AlertData(today=TODAY, overview=overview, cards=[], accounts=[], rows=[], own_reminders=frozenset({"person_loan:p1"})))
    assert [(a.kind, a.key.split(":")[0]) for a in once] == [("loan_due_soon", "debt")]


# -- failed data capture --------------------------------------------------------------------------------------------------


def _message(mid: str, status: str, reason: str | None, days: int = 1, source: str = "device") -> SimpleNamespace:
    return SimpleNamespace(id=mid, status=status, reason=reason, source=source, received_at=NOW - timedelta(days=days))


def test_a_failed_or_unread_bank_message_and_an_import_with_unreadable_rows_are_told_once_each() -> None:
    messages = [_message("m1", "failed", "processing_error"), _message("m2", "review", "missing_amount", source="wallet"),
                _message("m3", "review", "unknown_sender"), _message("m4", "failed", "processing_error", days=9),
                _message("m5", "failed", "processing_error", source="test")]
    imports = [SimpleNamespace(id="imp1", created_at=NOW, error_count=3), SimpleNamespace(id="imp2", created_at=NOW, error_count=0)]
    found = A.compose(A.AlertData(today=TODAY, overview=SimpleNamespace(items=[], calendar=[]), cards=[], accounts=[], rows=[], messages=messages,
                                  imports=imports))
    assert sorted(a.key for a in found if a.kind == "capture_failed") == ["import:imp1", "sms:m1", "sms:m2"]
    assert any("Apple Pay" in a.body for a in found) and any("3 rows" in a.body for a in found)


def test_the_capture_alert_has_its_own_channels_and_runs_once(monkeypatch: pytest.MonkeyPatch, mailbox: list) -> None:  # noqa: F811
    data = A.AlertData(today=utc_now().date(), overview=SimpleNamespace(items=[], calendar=[]), cards=[], accounts=[], rows=[], user_id=ME.user_id,
                       messages=[SimpleNamespace(id="m1", status="failed", reason="processing_error", source="device", received_at=utc_now())])
    monkeypatch.setattr(A, "gather", lambda context, now: data)
    view = client.get(f"{API}/alerts/settings", headers=OWNER).json()
    mine = next(k for k in view["kinds"] if k["kind"] == "capture_failed")
    assert (mine["in_app"], mine["email"]) == (True, False)
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"capture_failed": {"email": True}}})
    sent, failed = A.run(ME, now=utc_now())
    assert failed == 0 and sent == 2 and len(mailbox) == 1  # in the app and by email
    assert A.run(ME, now=utc_now()) == (0, 0) and len(mailbox) == 1  # the same message is never told again
    inbox = [n for n in client.get("/api/v1/notifications", headers=OWNER).json()["items"] if n["kind"] == "finance_alerts"]
    assert len(inbox) == 1 and inbox[0]["title"] == A.TITLES["capture_failed"]


# -- alert emails in the person's language -------------------------------------------------------------------------------


def test_alert_emails_follow_the_persons_email_language_and_carry_no_figures() -> None:
    en_subject, en = C.email_text("card_payment_due", 3, "en")
    ar_subject, ar = C.email_text("card_payment_due", 3, "ar")
    both_subject, both = C.email_text("card_payment_due", 3)
    assert en_subject == both_subject == "ANUM: Card payment due (3)" and ar_subject == "ANUM: دفعة بطاقة مستحقة (3)"
    assert "no amounts, names or account numbers" in en and not re.search(r"[؀-ۿ]", en)
    assert "لا تحتوي هذه الرسالة على مبالغ" in ar and "privacy" not in ar
    assert both.startswith(en) and both.endswith(ar)
    for text in (en, ar, both):
        assert not re.search(r"\d", text)


def test_a_run_emails_in_arabic_when_the_person_chose_arabic(monkeypatch: pytest.MonkeyPatch, mailbox: list) -> None:  # noqa: F811
    _notifications.save(ME, NotificationPreferences(email_enabled=True, calendar_email=False, calendar_email_language="ar"))
    assert C.reach(ME).email_language == "ar"
    data = A.AlertData(today=utc_now().date(), overview=SimpleNamespace(items=[], calendar=[]), cards=[], accounts=[], rows=[], user_id=ME.user_id,
                       imports=[SimpleNamespace(id="imp1", created_at=utc_now(), error_count=2)])
    monkeypatch.setattr(A, "gather", lambda context, now: data)
    client.put(f"{API}/alerts/settings", headers=OWNER, json={"channels": {"capture_failed": {"email": True, "in_app": False}}})
    assert A.run(ME, now=utc_now()) == (1, 0)
    (to, subject, text), = mailbox
    assert to == ADDRESS and subject == "ANUM: " + C.GENERIC_AR["capture_failed"][0] and not re.search(r"\d", subject + text)
