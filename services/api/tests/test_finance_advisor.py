"""The weekly Money advisor (docs/finance.md#money-advisor): pure arithmetic, routes and the weekly job.

The pure functions are tested on hand-built ledgers with figures worked out by hand. Routes and the job
run on the in-memory stores; ``test_postgres_finance_advisor.py`` repeats storage, RLS and the job's
once-a-week claim in PostgreSQL. All data is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main, push
from anum_api.dependencies import memory_repository
from anum_api.events import CanonicalEventName, create_event
from anum_api.finance import advisor as A
from anum_api.finance import advisor_jobs as J
from anum_api.finance import advisor_text as T
from anum_api.finance import commitment_math as M
from anum_api.finance.advisor_store import advisor_memory_store
from anum_api.finance.models import (
    Account,
    AccountKind,
    Bill,
    Cadence,
    Category,
    CategorySource,
    Debt,
    DebtKind,
    FinanceSettings,
    Transaction,
)
from anum_api.finance.sms_store import AccountMapping, SmsSettings, sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.punctuation import LONG_DASHES
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.team_store import memory_team_store

TENANT, WORKSPACE = "tenant_adv", "workspace_adv"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_adv", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_adv", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_adv", "x-user-roles": "viewer"}
API = "/api/v1/finance/advisor"
ME = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_adv", roles=["owner"])
STAMP = datetime(2026, 11, 1, tzinfo=UTC)
SCOPE = {"tenant_id": TENANT, "workspace_id": WORKSPACE, "owner_user_id": "owner_adv"}
TODAY = date(2026, 11, 5)  # a Thursday
SUNDAY = datetime(2026, 11, 8, 6, 0, tzinfo=UTC)  # 10:00 on Sunday in Asia/Dubai

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    advisor_memory_store.clear()
    memberships = dict(memory_repository.store.memberships)
    for user, role in (("owner_adv", "owner"), ("member_adv", "member"), ("viewer_adv", "viewer")):
        memory_repository.store.memberships[(TENANT, WORKSPACE, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, active=True, created_at=STAMP, updated_at=STAMP)
    yield
    memory_repository.store.memberships.clear()
    memory_repository.store.memberships.update(memberships)
    finance_store.clear()
    advisor_memory_store.clear()


# -- builders ----------------------------------------------------------------------------------

_seq = iter(range(1, 10_000_000))


def account(id_: str, kind: AccountKind = AccountKind.BANK, currency: str = "AED", opening: str = "0", **extra: Any) -> Account:
    return Account(id=id_, name=extra.pop("name", id_.title()), kind=kind, currency=currency, opening_balance=D(opening), created_at=STAMP,
                   updated_at=STAMP, **SCOPE, **extra)


def txn(day: date, amount: str, description: str, category: Category, account_id: str = "bank", currency: str = "AED") -> Transaction:
    n = next(_seq)
    return Transaction(id=f"t{n}", account_id=account_id, posted_on=day, amount=D(amount), currency=currency, description=description,
                       category=category, category_source=CategorySource.RULE, dedupe_hash=f"h{n}", created_at=STAMP, updated_at=STAMP, **SCOPE)


def bill(name: str, amount: str, due: date, cadence: Cadence = Cadence.MONTHLY, category: Category = Category.UTILITIES, currency: str = "AED") -> Bill:
    return Bill(id=f"bill_{name.lower()}", name=name, amount=D(amount), currency=currency, cadence=cadence, next_due_on=due, category=category,
                created_at=STAMP, updated_at=STAMP, **SCOPE)


def debt(name: str, balance: str, minimum: str, *, apr: str = "0", due_day: int | None = 10, kind: DebtKind = DebtKind.CARD,
         account_id: str | None = None, currency: str = "AED") -> Debt:
    return Debt(id=f"debt_{name.lower()}", name=name, kind=kind, balance=D(balance), currency=currency, apr_percent=D(apr), minimum_payment=D(minimum),
                due_day=due_day, account_id=account_id, created_at=STAMP, updated_at=STAMP, **SCOPE)


def monthly(day_of_month: int, amount: str, description: str, category: Category, start: date, end: date, **kw: Any) -> list[Transaction]:
    rows, cursor = [], start
    while cursor <= end:
        try:
            when = cursor.replace(day=day_of_month)
        except ValueError:
            when = A.month_end(cursor)
        if start <= when <= end:
            rows.append(txn(when, amount, description, category, **kw))
        cursor = A.month_start(A.month_end(cursor) + timedelta(days=1))
    return rows


def inputs(*, today: date = TODAY, accounts: list[Account] | None = None, rows: list[Transaction] | None = None, bills: list[Bill] | None = None,
           debts: list[Debt] | None = None, limits: dict[str, D] | None = None) -> A.AdvisorInputs:
    accounts = accounts if accounts is not None else [account("bank", opening="10000")]
    rows = rows or []
    sums: dict[str, D] = {}
    for row in rows:
        sums[row.account_id] = sums.get(row.account_id, D(0)) + row.amount
    return A.AdvisorInputs(today=today, timezone="Asia/Dubai", base_currency="AED", accounts=accounts, balances=sums, transactions=rows,
                           bills=bills or [], debts=debts or [], card_limits=limits or {})


def steady_ledger() -> list[Transaction]:
    """July to today: salary 12,000 on the 1st, rent 4,000 on the 2nd, groceries 1,500 and dining 600 a month."""
    start = date(2026, 7, 1)
    return (monthly(1, "12000", "SALARY ACME TRADING", Category.INCOME, start, TODAY)
            + monthly(2, "-4000", "RENT EJARI", Category.HOUSING, start, TODAY)
            + monthly(10, "-1500", "CARREFOUR CITY CENTRE", Category.GROCERIES, start, TODAY)
            + monthly(15, "-600", "TALABAT ORDER", Category.DINING, start, TODAY))


def codes(note: A.AdvisorNote) -> list[str]:
    return [a.code for a in note.actions]


# -- pure: building blocks -----------------------------------------------------------------------


def test_small_helpers() -> None:
    # The advisor's runway takes a bill's monthly share from the shared rule (commitment_math.py): a one-off bill adds nothing.
    assert M.bill_monthly(bill("Gym", "120", TODAY, Cadence.WEEKLY)) == D(120) * 52 / 12
    assert M.bill_monthly(bill("Car", "1200", TODAY, Cadence.YEARLY)) == D(100)
    assert M.bill_monthly(bill("Once", "500", TODAY, Cadence.ONCE)) == 0
    assert M.monthly_equivalent(D(500), Cadence.ONCE) is None
    assert A.next_due_day(31, date(2027, 2, 3)) == date(2027, 2, 28)
    assert A.next_due_day(5, date(2026, 11, 5)) == date(2026, 11, 5) and A.next_due_day(4, date(2026, 11, 5)) == date(2026, 12, 4)
    assert A.next_due_day(31, date(2026, 12, 31)) == date(2026, 12, 31) and A.next_due_day(30, date(2027, 1, 31)) == date(2027, 2, 28)
    assert [s for s, _ in A.previous_months(TODAY, 3)] == [date(2026, 10, 1), date(2026, 9, 1), date(2026, 8, 1)]
    # Days round down; no money means zero days; nothing going out means no end date at all.
    assert A.scenario("bare", D(1000), D("304.375"), TODAY, "AED") == A.Scenario(kind="bare", monthly_outflow=D("304.38"), days=99,
                                                                                    runs_out_on=TODAY + timedelta(days=99))
    assert A.scenario("bare", D(-5), D(100), TODAY, "AED").days == 0
    assert A.scenario("bare", D(5000), D(0), TODAY, "AED").days is None
    assert J.week_start_for(date(2026, 11, 8), 6) == date(2026, 11, 8) and J.week_start_for(date(2026, 11, 10), 6) == date(2026, 11, 8)
    assert J.week_start_for(date(2026, 11, 7), 6) == date(2026, 11, 1) and J.week_start_for(date(2026, 11, 5), 3) == date(2026, 11, 5)


def test_interest_of_paying_only_the_minimum() -> None:
    first, months, total, never = A.interest_at_minimum(D(3000), D(36), D(150), "AED")
    assert first == D("85.50")  # (3,000 - 150) x 3% a month
    assert months is not None and months > 12 and total is not None and total > first and not never
    # A minimum below the monthly interest never clears the balance.
    assert A.interest_at_minimum(D(10000), D(36), D(250), "AED")[3] is True



def test_payment_first_then_interest_in_both_figures() -> None:
    # Review finding: the payoff used to charge interest before the payment while the next-month figure paid first.
    assert A.interest_at_minimum(D(100), D(36), D(100), "AED") == (D("0.00"), 1, D("0.00"), False)
    first, months, total, never = A.interest_at_minimum(D(300), D(12), D(100), "AED")
    # Pay 100, 2.00 interest on 200; pay 100, 1.02 on 102; pay 100, 0.0302 on 3.02; month 4 clears. 3.0502 rounds up to 3.06.
    assert first == D("2.00") and months == 4 and total == D("3.06") and not never


def test_a_huge_runway_keeps_its_days_and_has_no_end_date() -> None:
    # Review finding: 100,000 against a yearly bill of 12 is about 3 million days, past the last date Python can hold.
    huge = A.scenario("bare", D(100000), D(1), TODAY, "AED")
    assert huge.days == 3043750 and huge.runs_out_on is None
    note = A.build_note(inputs(accounts=[account("bank", opening="100000")], bills=[bill("Domain", "12", date(2027, 1, 5), Cadence.YEARLY)]))
    bare = note.currencies[0].runway.scenarios[0]
    assert bare.days is not None and bare.days > 3_000_000 and bare.runs_out_on is None
    assert A.scenario("bare", D(1000), D(100), TODAY, "AED").runs_out_on == TODAY + timedelta(days=304)


# -- pure: the note -------------------------------------------------------------------------------


def test_no_data_says_what_to_add_instead_of_guessing() -> None:
    note = A.build_note(inputs(accounts=[]))
    assert note.has_accounts is False and note.currencies == [] and note.cards == []
    assert codes(note) == ["no_accounts"] and "Add your bank account" in note.actions[0].text_en
    empty = A.build_note(inputs())
    assert codes(empty) == ["import_statements"]
    block = empty.currencies[0]
    assert block.runway.status == "no_records" and [s.kind for s in block.runway.scenarios] == ["bare"]
    assert "never moves money" in note.notice_en


def test_one_currency_runway_three_ways_with_the_assumptions_written_out() -> None:
    rows = steady_ledger()
    note = A.build_note(inputs(rows=rows, bills=[bill("DEWA", "500", date(2026, 11, 20))], debts=[debt("Loan", "20000", "1000", kind=DebtKind.LOAN)]))
    block = note.currencies[0]
    runway = block.runway
    assert runway.status == "ok" and runway.basis == "months" and runway.months == ["2026-08", "2026-09", "2026-10"]
    balance = D(10000) + sum((r.amount for r in rows), D(0))
    assert runway.liquid == balance
    assert (runway.bills_monthly, runway.minimums_monthly) == (D("500.00"), D("1000.00"))
    # Essentials: housing 4,000 and groceries 1,500 a month; dining is not essential.
    assert runway.essential_spending_monthly == D("5500.00") and runway.total_spending_monthly == D("6100.00")
    bare, essential, full = runway.scenarios
    assert (bare.monthly_outflow, essential.monthly_outflow, full.monthly_outflow) == (D("1500.00"), D("7000.00"), D("7100.00"))
    assert essential.days == int(balance * D("30.4375") / D(7000))
    assert essential.runs_out_on == TODAY + timedelta(days=essential.days)
    assert bare.days > essential.days > full.days
    assert note.income.salary_found is True and "no_salary" not in codes(note)


def test_a_bill_in_an_essential_category_is_not_counted_twice() -> None:
    rows = steady_ledger() + monthly(20, "-500", "DEWA BILL", Category.UTILITIES, date(2026, 7, 1), TODAY)
    note = A.build_note(inputs(rows=rows, bills=[bill("DEWA", "500", date(2026, 11, 20))]))
    utilities = next(c for c in note.currencies[0].runway.essential_categories if c.category == Category.UTILITIES)
    assert (utilities.monthly_average, utilities.bills_monthly, utilities.counted) == (D("500.00"), D("500.00"), D("0.00"))
    assert note.currencies[0].runway.scenarios[1].monthly_outflow == D("6000.00")  # 500 bill + 4,000 + 1,500


def test_two_currencies_are_never_mixed_and_a_card_limit_is_never_counted() -> None:
    accounts = [account("bank", opening="10000"), account("usd", currency="USD", opening="2000"), account("card", AccountKind.CARD)]
    rows = steady_ledger() + monthly(5, "-300", "AMAZON.COM", Category.SHOPPING, date(2026, 7, 1), TODAY, account_id="usd", currency="USD")
    note = A.build_note(inputs(accounts=accounts, rows=rows, limits={"card": D(20000)}))
    assert [b.currency for b in note.currencies] == ["AED", "USD"]
    aed, usd = note.currencies
    assert usd.runway.liquid == D(2000) + sum((r.amount for r in rows if r.currency == "USD"), D(0))
    assert usd.runway.total_spending_monthly == D("300.00") and usd.runway.essential_spending_monthly == D("0.00")
    assert aed.runway.liquid == D(10000) + sum((r.amount for r in rows if r.currency == "AED"), D(0))
    assert aed.card_available == D("20000.00") and usd.card_available is None
    assert aed.runway.accounts_counted == 1  # the card is not liquid money


def test_fewer_than_30_days_of_records_gives_only_the_bare_minimum() -> None:
    rows = [txn(TODAY - timedelta(days=d), "-100", "CARREFOUR", Category.GROCERIES) for d in range(0, 20, 2)]
    note = A.build_note(inputs(rows=rows, bills=[bill("DEWA", "400", date(2026, 11, 25))]))
    runway = note.currencies[0].runway
    assert runway.status == "not_enough_history" and runway.history_days == 19 and runway.basis == "none"
    assert [s.kind for s in runway.scenarios] == ["bare"] and runway.essential_spending_monthly is None
    action = next(a for a in note.actions if a.code == "history_needed")
    assert "19 days" in action.text_en and "30 days" in action.text_en
    assert note.currencies[0].spending.month.average is None and "no_salary" not in codes(note)


def test_thirty_days_but_no_full_month_uses_the_last_30_days() -> None:
    later = date(2026, 11, 20)  # records from 11 October: October is not covered from its first week
    rows = [txn(later - timedelta(days=d), "-100", "CARREFOUR", Category.GROCERIES) for d in range(1, 41)]
    runway = A.build_note(inputs(rows=rows, today=later)).currencies[0].runway
    assert runway.basis == "last_30_days" and runway.months == []
    assert runway.essential_spending_monthly == (D(3000) * D("30.4375") / 30).quantize(D("0.01"))


def test_no_salary_detected_is_said_plainly() -> None:
    rows = [r for r in steady_ledger() if r.category != Category.INCOME]
    note = A.build_note(inputs(rows=rows))
    assert note.income.salary_found is False
    action = next(a for a in note.actions if a.code == "no_salary")
    assert "already assume no income" in action.text_en and action.severity == "info"
    # A steady monthly credit counts as salary even without the word.
    paid = rows + monthly(1, "9000", "ACME LLC MONTHLY", Category.INCOME, date(2026, 9, 1), TODAY)
    assert A.build_note(inputs(rows=paid)).income.salary_found is True


def test_card_advice_without_a_rate_never_invents_one() -> None:
    accounts = [account("bank", opening="10000"), account("card", AccountKind.CARD, name="Visa", last4="1234")]
    rows = steady_ledger() + [txn(date(2026, 11, 3), "-2400", "IKEA", Category.SHOPPING, account_id="card")]
    note = A.build_note(inputs(accounts=accounts, rows=rows, debts=[debt("Visa", "2400", "120", due_day=12, account_id="card")]))
    card = note.cards[0]
    assert (card.owed, card.owed_source, card.apr_percent, card.interest_if_minimum) == (D("2400.00"), "ledger", None, None)
    assert card.due_on == date(2026, 11, 12) and card.minimum_payment == D("120.00") and "rate" in card.missing
    assert {"card_pay_full", "card_add_rate"} <= set(codes(note)) and "card_pay_full_rate" not in codes(note)
    pay = next(a for a in note.actions if a.code == "card_pay_full")
    assert "Visa (1234)" in pay.text_en and "AED 2,400.00" in pay.text_en and "2026-11-12" in pay.text_en and pay.severity == "urgent"


def test_card_advice_with_a_rate_shows_the_cost_of_the_minimum() -> None:
    accounts = [account("bank", opening="10000"), account("card", AccountKind.CARD, name="Visa", last4="1234")]
    rows = steady_ledger() + [txn(date(2026, 11, 3), "-3000", "IKEA", Category.SHOPPING, account_id="card")]
    note = A.build_note(inputs(accounts=accounts, rows=rows, limits={"card": D(5000)},
                               debts=[debt("Visa", "3000", "150", apr="36", due_day=25, account_id="card")]))
    card = note.cards[0]
    assert card.interest_if_minimum == D("85.50") and card.utilization_percent == 60 and card.available == D("2000.00")
    action = next(a for a in note.actions if a.code == "card_pay_full_rate")
    assert action.severity == "high" and "AED 85.50" in action.text_en and "card_high_use" in codes(note)
    assert all(code not in codes(note) for code in ("card_add_rate", "card_add_debt"))


def test_a_card_without_a_debt_record_asks_for_one_and_a_card_debt_alone_still_counts() -> None:
    accounts = [account("bank", opening="10000"), account("card", AccountKind.CARD, name="Mastercard")]
    rows = steady_ledger() + [txn(date(2026, 11, 3), "-800", "NOON", Category.SHOPPING, account_id="card")]
    note = A.build_note(inputs(accounts=accounts, rows=rows, debts=[debt("Store card", "900", "90", apr="30", due_day=None)]))
    master = next(c for c in note.cards if c.name == "Mastercard")
    assert master.missing[0] == "debt_record" and "limit" in master.missing
    assert any(a.code == "card_add_debt" and "Mastercard" in a.text_en for a in note.actions)
    store = next(c for c in note.cards if c.name == "Store card")
    assert store.owed_source == "debt" and store.owed == D("900.00") and "due_day" in store.missing and store.due_on is None
    # A minimum below the interest: the card never clears.
    never = A.build_note(inputs(accounts=[account("bank", opening="100")], debts=[debt("Big card", "10000", "200", apr="36", due_day=20)]))
    assert never.cards[0].never_clears_at_minimum and never.actions[0].code == "card_minimum_too_low"


def test_coming_payments_in_the_next_30_days() -> None:
    netflix = monthly(3, "-55", "NETFLIX.COM", Category.SUBSCRIPTIONS, date(2026, 6, 1), TODAY)
    gym = monthly(8, "-250", "FITNESS FIRST GYM", Category.OTHER, date(2026, 6, 1), TODAY)
    rows = steady_ledger() + netflix + gym
    note = A.build_note(inputs(rows=rows, bills=[bill("Maid", "100", date(2026, 11, 6), Cadence.WEEKLY, Category.HOUSING)],
                               debts=[debt("Car loan", "30000", "1500", kind=DebtKind.LOAN, due_day=None),
                                      debt("Visa", "1000", "100", due_day=7)]))
    coming = note.currencies[0].coming
    maid = [i for i in coming.items if i.name == "Maid"]
    assert [i.due_on for i in maid] == [date(2026, 11, 6), date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 27), date(2026, 12, 4)]
    kinds = {i.name: i.kind for i in coming.items}
    assert kinds["NETFLIX.COM"] == "subscription" and kinds["FITNESS FIRST GYM"] == "recurring" and kinds["Visa"] == "card_minimum"
    loan = next(i for i in coming.items if i.name == "Car loan")
    assert loan.due_on is None and loan.kind == "minimum" and coming.items[-1] is not None
    assert coming.saved_total == D("500.00") + D("1500.00") + D("100.00")
    # Found in the records, not saved as bills: Netflix and the gym, and the steady rent, groceries and dining of the ledger.
    assert coming.detected_total == D("55.00") + D("250.00") + D("4000.00") + D("1500.00") + D("600.00")
    assert coming.total == coming.saved_total + coming.detected_total
    assert {"subscriptions_review", "save_recurring", "debt_add_due_day", "bill_due"} <= set(codes(note))
    assert next(a for a in note.actions if a.code == "bill_due").text_en == "Maid (AED 100.00) is due on 2026-11-06, in 1 days."


def test_a_weekly_payment_found_in_the_records_counts_every_week_of_the_window() -> None:
    # Review finding: detected recurring payments used to add only their next date.
    rows = steady_ledger() + [txn(TODAY - timedelta(days=7 * k), "-80", "HOME CLEANERS LLC", Category.OTHER) for k in range(1, 9)]
    coming = A.build_note(inputs(rows=rows)).currencies[0].coming
    cleaners = [i for i in coming.items if i.name == "HOME CLEANERS LLC"]
    assert [i.due_on for i in cleaners] == [TODAY + timedelta(days=7 * k) for k in range(5) if 7 * k <= 30]
    assert all(i.detected and i.kind == "recurring" for i in cleaners)
    assert coming.detected_total >= D(80) * len(cleaners)


def test_where_money_went_compares_week_month_and_average_and_flags_spikes() -> None:
    rows = steady_ledger() + [txn(date(2026, 11, 2), "-1400", "NOBU RESTAURANT", Category.DINING),
                              txn(date(2026, 11, 3), "-900", "EMIRATES AIRLINE", Category.TRAVEL)]
    note = A.build_note(inputs(rows=rows, today=date(2026, 11, 9)))
    spending = note.currencies[0].spending
    assert (spending.week.start, spending.week.end) == (date(2026, 11, 2), date(2026, 11, 8))
    # Rent (2 Nov), the restaurant and the flight; nothing in the 7 days before.
    assert spending.week.amount == D("6300.00") and spending.week.previous == D("0.00") and spending.week.change_percent is None
    assert spending.month.amount == D("4000.00") + D("2300.00")  # groceries come on the 10th
    assert spending.month.previous_start == date(2026, 10, 1) and spending.month.previous_end == date(2026, 10, 9)
    assert spending.month.average == D("6100.00")
    assert [p.name for p in spending.payees_week] == ["RENT EJARI", "NOBU RESTAURANT", "EMIRATES AIRLINE"]
    assert spending.categories[0].category == Category.HOUSING
    spikes = {s.category: s for s in spending.spikes}
    assert spikes[Category.DINING].period == "month" and spikes[Category.TRAVEL].period == "new" and spikes[Category.TRAVEL].percent is None
    assert {"spending_up", "spending_new"} <= set(codes(note))


def test_actions_are_ranked_capped_bilingual_and_plain() -> None:
    accounts = [account("bank", opening="-28000"), account("card", AccountKind.CARD, name="Visa")]
    rows = steady_ledger() + [txn(date(2026, 11, 3), "-3000", "IKEA", Category.SHOPPING, account_id="card")]
    note = A.build_note(inputs(accounts=accounts, rows=rows, bills=[bill("DEWA", "600", TODAY)],
                               debts=[debt("Visa", "3000", "150", apr="36", due_day=6, account_id="card")]))
    ranks = [T.SEVERITY_RANK[a.severity] for a in note.actions]
    assert ranks == sorted(ranks) and len(note.actions) <= A.MAX_ACTIONS and note.actions[0].severity == "urgent"
    assert "runway_low" in codes(note) and "bill_due_today" in codes(note)
    for action in note.actions:
        assert action.text_en and action.text_ar and not any(ch in action.text_en + action.text_ar for ch in LONG_DASHES)
        assert "{" not in action.text_en and "{" not in action.text_ar
    arabic = next(a for a in note.actions if a.code == "card_pay_full_rate").text_ar
    assert f"{T.FSI}AED 3,000.00{T.PDI}" in arabic
    for english, arabic_template in T.TEMPLATES.values():
        assert not any(ch in english + arabic_template for ch in LONG_DASHES)
    title, body = T.notice_text(note)
    assert "AED 3" not in body and "Visa" not in body and "about 17 days" in body and "never moves money" in body and title


def test_records_older_than_a_week_ask_for_the_latest_statement() -> None:
    rows = [r for r in steady_ledger() if r.posted_on <= date(2026, 10, 20)]
    note = A.build_note(inputs(rows=rows))
    assert note.actions[0].code == "stale_records" or "stale_records" in codes(note)
    assert next(a for a in note.actions if a.code == "stale_records").params["date"] == "2026-10-15"


# -- routes ----------------------------------------------------------------------------------------


def seed(ctx: TenantContext = ME) -> None:
    scope = {"tenant_id": ctx.tenant_id, "workspace_id": ctx.workspace_id, "owner_user_id": ctx.user_id}
    finance_store.save_account(account("bank", opening="10000").model_copy(update=scope))
    finance_store.add_transactions([r.model_copy(update=scope) for r in steady_ledger()])


def test_overview_generate_history_and_settings() -> None:
    first = client.get(API, headers=OWNER)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["latest"] is None and body["settings"] == {"enabled": True, "weekday": 6, "updated_at": None}
    assert body["timezone"] == "Asia/Dubai" and body["next_note_on"] is not None
    seed()
    made = client.post(f"{API}/generate", headers=OWNER)
    assert made.status_code == 201, made.text
    note = made.json()
    assert note["trigger"] == "manual" and note["delivered_at"] is None and note["note"]["currencies"][0]["currency"] == "AED"
    again = client.post(f"{API}/generate", headers=OWNER).json()
    assert again["id"] == note["id"]  # one note per week: Generate now refreshes it
    assert client.get(API, headers=OWNER).json()["latest"]["id"] == note["id"]
    page = client.get(f"{API}/notes", headers=OWNER, params={"limit": 5}).json()
    assert page["total"] == 1 and page["items"][0]["survival"][0]["currency"] == "AED" and page["items"][0]["actions"] == len(note["note"]["actions"])
    assert client.get(f"{API}/notes/{note['id']}", headers=OWNER).json()["id"] == note["id"]
    # Someone else's note does not exist for you, a workspace owner's either.
    assert client.get(f"{API}/notes/{note['id']}", headers=MEMBER).status_code == 404
    assert client.get(f"{API}/notes", headers=MEMBER).json()["total"] == 0
    saved = client.put(f"{API}/settings", headers=OWNER, json={"enabled": False, "weekday": 4})
    assert saved.status_code == 200 and saved.json()["weekday"] == 4
    assert client.get(API, headers=OWNER).json()["next_note_on"] is None
    assert client.put(f"{API}/settings", headers=OWNER, json={"enabled": True, "weekday": 7}).status_code == 422
    assert client.put(f"{API}/settings", headers=OWNER, json={"enabled": True, "weekday": 1, "email": True}).status_code == 422
    audit = [r.action for r in memory_repository.store.audit_records if r.actor == "owner_adv"]
    assert "finance.advisor.generate" in audit and "finance.advisor.settings" in audit


def test_viewers_cannot_read_or_generate_and_members_can() -> None:
    assert client.get(API, headers=VIEWER).status_code == 403
    assert "viewer to member" in client.get(f"{API}/notes", headers=VIEWER).json()["error"]["message"]
    assert client.post(f"{API}/generate", headers=VIEWER).status_code == 403
    assert client.put(f"{API}/settings", headers=VIEWER, json={"enabled": False, "weekday": 0}).status_code == 403
    assert client.post(f"{API}/generate", headers=MEMBER).status_code == 201
    assert client.get(f"{API}/notes", params={"limit": 0}, headers=MEMBER).status_code == 422


# -- the weekly job --------------------------------------------------------------------------------


def notices(user: str = "owner_adv") -> list[Any]:
    ctx = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user)
    return [n for n in memory_team_store.list_notifications(ctx, limit=50, before=None, unread_only=False) if n.kind == J.NOTICE_KIND]


@pytest.fixture
def clean_notices() -> Iterator[None]:
    ws = memory_team_store._ws(ME)
    before = dict(ws.notifications)
    ws.notifications.clear()
    yield
    ws.notifications.clear()
    ws.notifications.update(before)


def test_the_job_writes_one_note_a_week_on_the_chosen_day_and_never_emails(clean_notices: None) -> None:
    seed()
    finance_store.save_settings(ME, FinanceSettings(timezone="Asia/Dubai"))
    saturday = SUNDAY - timedelta(days=1)
    assert J.run(now=saturday)["notes"] == 0 and notices() == []
    dry = J.run(now=SUNDAY, dry_run=True)
    assert dry["notes"] == 1 and advisor_memory_store.reports == {}
    result = J.run(now=SUNDAY)
    assert result["notes"] == 1 and result["failed"] == 0 and result["people"] == 1
    sent = notices()
    assert len(sent) == 1 and sent[0].email_status.value == "not_requested" and "never moves money" in sent[0].body
    assert "AED 1" not in sent[0].body and "AED: about" in sent[0].body  # days and counts only, never an amount
    # Idempotent: later runs in the same week send nothing, even after "Generate now".
    assert client.post(f"{API}/generate", headers=OWNER).status_code == 201
    assert J.run(now=SUNDAY + timedelta(hours=5))["notes"] == 0
    assert J.run(now=SUNDAY + timedelta(days=1))["notes"] == 0 and len(notices()) == 1
    report = next(iter(advisor_memory_store.reports.values()))
    assert report.week_start == date(2026, 11, 8) and report.delivered_at is not None
    # The next week: one more.
    assert J.run(now=SUNDAY + timedelta(days=7))["notes"] == 1 and len(notices()) == 2


def test_the_job_catches_up_two_days_late_respects_the_switch_and_retries_a_failed_notice(clean_notices: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seed()
    assert J.run(now=SUNDAY + timedelta(days=3))["notes"] == 0  # Wednesday: too late, waits for next Sunday
    assert J.run(now=SUNDAY + timedelta(days=2))["notes"] == 1  # Tuesday: still this week
    advisor_memory_store.clear()
    memory_team_store._ws(ME).notifications.clear()
    assert client.put(f"{API}/settings", headers=OWNER, json={"enabled": False, "weekday": 6}).status_code == 200
    assert J.run(now=SUNDAY) == {"dry_run": False, "people": 1, "notes": 0, "skipped": 1, "failed": 0}
    assert client.put(f"{API}/settings", headers=OWNER, json={"enabled": True, "weekday": 2}).status_code == 200
    wednesday = SUNDAY + timedelta(days=3)
    monkeypatch.setattr(J, "notify", lambda *_a, **_k: False)
    assert J.run(now=wednesday)["failed"] == 1
    assert next(iter(advisor_memory_store.reports.values())).delivered_at is None
    monkeypatch.undo()
    assert J.run(now=wednesday)["notes"] == 1 and len(notices()) == 1


def test_a_viewer_with_finance_records_gets_no_note(clean_notices: None) -> None:
    viewer = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="viewer_adv")
    seed(viewer)
    assert J.run(now=SUNDAY)["skipped"] == 1 and notices("viewer_adv") == []


def test_the_notification_event_becomes_the_finance_advice_push() -> None:
    event = create_event(CanonicalEventName.NOTIFICATION_CREATED, ME, "ntf_1", {"recipient": "owner_adv", "kind": J.NOTICE_KIND, "work_item_id": None}).event
    intent = push.intent_for(event)
    assert intent is not None and intent.kind == push.PushKind.FINANCE_ADVICE and intent.recipient == "owner_adv"
    assert intent.data() == {"anum_kind": "finance_advice", "tenant_id": TENANT, "workspace_id": WORKSPACE}
    assert J.NOTICE_KIND not in push.IN_APP_ONLY_KINDS
    assert "never moves money" in push.push_text(push.PushKind.FINANCE_ADVICE, "en")[1]


def test_card_limits_come_from_the_sms_settings_and_privacy_counts_export_and_deletes_notes() -> None:
    seed()
    finance_store.save_account(account("card", AccountKind.CARD, name="Visa").model_copy(update=SCOPE))
    assert sms_memory_store.save_settings(ME, SmsSettings(account_map=[AccountMapping(last4="1234", account_id="card", credit_limit=D(8000))]),
                                          expected_version=0)
    gathered = J.gather(ME, now=SUNDAY)
    assert gathered.card_limits == {"card": D(8000)} and gathered.today == date(2026, 11, 8)
    assert client.post(f"{API}/generate", headers=OWNER).status_code == 201
    assert client.put(f"{API}/settings", headers=OWNER, json={"enabled": True, "weekday": 0}).status_code == 200
    from anum_api.privacy import finance_export

    exported = finance_export(finance_store, ME)
    assert len(exported["advisor_notes"]) == 1 and exported["advisor_settings"]["weekday"] == 0
    assert "owner_user_id" not in exported["advisor_notes"][0]
    before = finance_store.count_everything(ME)
    assert before >= 2 + advisor_memory_store.count_everything(ME) - 2
    finance_store.delete_everything(ME)
    assert advisor_memory_store.count_everything(ME) == 0
