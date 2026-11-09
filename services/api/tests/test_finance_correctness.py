"""How figures are counted (docs/finance.md#how-figures-are-counted), in-memory stores.

One test per finding of the November 2026 finance audit: each failed on the code before the fix.
Every message, merchant, name and amount here is SYNTHETIC and low-entropy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from test_finance_sms import (
    API,
    OWNER,
    PURCHASE_EN,
    REFUND,
    account,
    clean,  # noqa: F401  (the autouse fixture that empties the in-memory stores)
    client,
    dashboard,
    money,
    send,
    setup,
    wallet,
    wallet_setup,
)

from anum_api.finance import flows
from anum_api.finance import ledger as L
from anum_api.finance import obligations as O
from anum_api.finance import sms_parser as P
from anum_api.finance.advisor import AdvisorInputs, build_note
from anum_api.finance.categories import categorize
from anum_api.finance.links_store import FinanceLink, links_memory_store
from anum_api.finance.models import (
    Account,
    AccountKind,
    Bill,
    Cadence,
    Category,
    CategoryRule,
    CategorySource,
    Debt,
    DebtKind,
    FxRate,
    Goal,
    PlanSettings,
    Transaction,
)
from anum_api.finance.sms_store import sms_memory_store

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
OWNED = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u", "created_at": NOW, "updated_at": NOW}


@pytest.fixture(autouse=True)
def _links() -> Iterator[None]:
    links_memory_store.clear()
    yield
    links_memory_store.clear()


def summary(month: str = "2026-11") -> dict:
    response = client.get(f"{API}/summary", headers=OWNER, params={"month": month})
    assert response.status_code == 200, response.text
    return response.json()


def amount(rows: list[dict], currency: str = "SAR") -> Decimal:
    return next((Decimal(r["amount"]) for r in rows if r["currency"] == currency), Decimal(0))


def manual(account_id: str, value: str, description: str, on: str = "2026-11-03", **extra: object) -> dict:
    response = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": account_id, "posted_on": on, "amount": value,
                                                                        "description": description, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _account(account_id: str, kind: AccountKind, currency: str = "SAR", opening: str = "0") -> Account:
    return Account(id=account_id, name=account_id, kind=kind, currency=currency, opening_balance=Decimal(opening), **OWNED)


def _txn(n: int, account_id: str, value: str, category: Category, *, day: date = date(2026, 11, 3), currency: str = "SAR",
         source: CategorySource = CategorySource.RULE, description: str = "x") -> Transaction:
    return Transaction(id=f"x{n}", account_id=account_id, posted_on=day, amount=Decimal(value), currency=currency, description=description,
                       category=category, category_source=source, dedupe_hash=str(n), **OWNED)


def _bill_link(bill_id: str, due: date, value: str = "400", currency: str = "AED") -> FinanceLink:
    return FinanceLink(id=f"lnk-{bill_id}-{due}", canonical_id="je-x", dimension="bill", role="payment", target_type="finance_bill_due",
                       target_id=f"{bill_id}:{due.isoformat()}", amount=Decimal(value), currency=currency, idem_key=f"k{bill_id}{due}",
                       created_by="u", **OWNED)


# -- 1. the ledger dashboard with a debt ------------------------------------------------------------------


def test_ledger_dashboard_works_with_a_debt_and_plans_its_payoff() -> None:
    setup()
    made = client.post(f"{API}/debts", headers=OWNER, json={"name": "Loan", "kind": "loan", "balance": "1000", "currency": "SAR",
                                                            "apr_percent": "5", "minimum_payment": "100"})
    assert made.status_code == 201, made.text
    board = dashboard()
    plan = next(d for d in board["debts"] if d["name"] == "Loan")
    assert Decimal(plan["balance"]) == Decimal("1000") and plan["months_to_clear"] == 11


# -- 2. categories ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "value", "kind", "expected"), [
    ("CREDIT CARD PAYMENT 1234", "-500", None, Category.TRANSFER),
    ("ADCB CC PAYMENT", "-500", None, Category.TRANSFER),
    ("سداد بطاقة الائتمان", "-200", None, Category.TRANSFER),
    ("PAYMENT RECEIVED - THANK YOU", "500", None, Category.TRANSFER),
    ("PAYMENT RECEIVED - THANK YOU", "500", AccountKind.CARD, Category.TRANSFER),
    ("SOME CREDIT", "500", AccountKind.LOAN, Category.TRANSFER),
    ("SALARY", "100", AccountKind.CARD, Category.TRANSFER),
    ("CARREFOUR", "45", AccountKind.CARD, Category.GROCERIES),  # a merchant's credit on a card is a refund, not a card payment
    ("CASHBACK CREDIT", "5", AccountKind.CARD, Category.CASHBACK),
    ("WPS SALARY TRANSFER ACME", "10000", None, Category.INCOME),
    ("TRANSFER TO SAMPLE PERSON RENT", "-3000", None, Category.HOUSING),
    ("INTEREST EARNED", "5", None, Category.INCOME),
    ("LATE PAYMENT INTEREST", "-50", None, Category.FEES),
    ("SOMETHING IN", "20", AccountKind.BANK, Category.INCOME),
])
def test_built_in_rules(text: str, value: str, kind: AccountKind | None, expected: Category) -> None:
    assert categorize(text, Decimal(value), None, kind)[0] == expected


def test_owner_rules_and_manual_categories_still_win() -> None:
    rules = [CategoryRule(match="payment received", category=Category.INCOME)]
    assert categorize("PAYMENT RECEIVED - THANK YOU", Decimal(500), rules, AccountKind.CARD) == (Category.INCOME, CategorySource.OWNER_RULE)
    # Chosen by hand or by the owner's rule, money in on a card stays income in the totals.
    for source in (CategorySource.MANUAL, CategorySource.OWNER_RULE):
        assert flows.classify(Category.INCOME, Decimal(500), AccountKind.CARD, source) == flows.Flow.INCOME
    assert flows.classify(Category.INCOME, Decimal(500), AccountKind.CARD, CategorySource.NONE) == flows.Flow.TRANSFER


def test_a_card_payment_from_statements_or_typed_is_neither_spend_nor_income() -> None:
    bank = account(name="Bank")
    card = account(name="Card", kind="card", opening_balance="0")
    manual(card["id"], "-500", "CARREFOUR RIYADH")
    manual(bank["id"], "-500", "CREDIT CARD PAYMENT 1234")
    manual(card["id"], "500", "PAYMENT RECEIVED - THANK YOU")
    s = summary()
    assert amount(s["spend"]) == Decimal("500.00") and amount(s["income"]) == Decimal(0)
    board = dashboard()
    assert money(board["expenses"]) == Decimal("500.00") and money(board["income"]) == Decimal(0)


def test_a_card_payment_stored_as_income_by_older_rules_is_read_as_a_transfer() -> None:
    from anum_api.finance.store import finance_store

    card = account(name="Card", kind="card")
    # A row categorised before the fix: money in on a card filed as income by no rule.
    row = manual(card["id"], "500", "SAMPLE CREDIT", category="income")
    stored = finance_store.transactions[row["id"]]
    stored.category_source = CategorySource.NONE
    assert amount(summary()["income"]) == Decimal(0)
    assert money(dashboard()["income"]) == Decimal(0)


# -- 3. refunds -----------------------------------------------------------------------------------------------


def test_an_sms_refund_lowers_its_category_and_is_never_income() -> None:
    s = setup()
    assert send(s["token"], PURCHASE_EN).json()["status"] == "posted"
    assert send(s["token"], REFUND).json()["status"] == "posted"
    sm = summary()
    assert amount(sm["income"]) == Decimal(0) and amount(sm["spend"]) == Decimal(0)
    assert not [r for r in sm["spend_by_category"] if r["category"] == "groceries"]
    board = dashboard()
    assert money(board["income"]) == Decimal(0) and money(board["expenses"]) == Decimal(0)


def test_a_statement_merchant_credit_is_a_refund_and_an_unmatched_refund_is_its_own_line() -> None:
    bank = account(name="Bank")
    manual(bank["id"], "-300", "AMAZON.AE ORDER")
    manual(bank["id"], "120", "AMAZON.AE")
    manual(bank["id"], "40", "REFUND SAMPLE SHOP")
    manual(bank["id"], "15", "CASHBACK CREDIT")
    sm = summary()
    shopping = next(r for r in sm["spend_by_category"] if r["category"] == "shopping")
    assert Decimal(shopping["amount"]) == Decimal("180.00") and shopping["count"] == 1
    assert amount(sm["income"]) == Decimal(0) and amount(sm["refunds"]) == Decimal("40.00") and amount(sm["cashback"]) == Decimal("15.00")
    assert amount(sm["net"]) == Decimal("-125.00")  # 40 + 15 received, 180 spent
    board = dashboard()
    assert money(board["income"]) == Decimal(0) and money(board["expenses"]) == Decimal("180.00")
    assert money(board["refunds"]) == Decimal("40.00") and money(board["cashback"]) == Decimal("15.00")
    assert money(board["surplus"]) == amount(sm["net"])


def test_an_sms_refund_without_its_purchase_is_filed_as_refunds() -> None:
    s = setup()
    assert send(s["token"], REFUND).json()["status"] == "posted"
    sm = summary()
    assert amount(sm["income"]) == Decimal(0) and amount(sm["refunds"]) == Decimal("45.50")


# -- 4. own transfer told by two messages ---------------------------------------------------------------------

OUT = "SYNTHETIC. SAR 300.00 transferred to SAMPLE OWNER from account ***5678 on 06/11/2026"
IN = "SYNTHETIC. SAR 300.00 received from SAMPLE OWNER to account ***9999 on 06/11/2026"


@pytest.mark.parametrize("order", [(OUT, IN), (IN, OUT)])
def test_two_messages_for_one_own_transfer_become_one_own_transfer(order: tuple[str, str]) -> None:
    s = setup()
    first = send(s["token"], order[0]).json()
    second = send(s["token"], order[1]).json()
    assert first["status"] == "posted" and second["status"] == "attached"
    live = [e for e in sms_memory_store.entries.values() if e.status == L.EntryStatus.POSTED and e.source != L.EntrySource.REVERSAL]
    assert [e.kind for e in live] == [P.Kind.OWN_TRANSFER.value]
    legs = {p.account_id: p.amount for p in live[0].postings}
    assert legs == {s["bank"]["id"]: Decimal("-300.00"), s["savings"]["id"]: Decimal("300.00")}
    first_message = sms_memory_store.messages[first["message_id"]]
    assert first_message.entry_id == live[0].id
    sm = summary()
    assert amount(sm["income"]) == Decimal(0) and amount(sm["spend"]) == Decimal(0)
    board = dashboard()
    assert money(board["income"]) == Decimal(0) and money(board["expenses"]) == Decimal(0)
    balances = {a["id"]: Decimal(a["balance"]) for a in sm["accounts"]}
    assert balances[s["bank"]["id"]] == Decimal("-300.00") and balances[s["savings"]["id"]] == Decimal("300.00")


def test_a_transfer_to_someone_else_is_not_paired() -> None:
    s = setup()
    send(s["token"], OUT)
    send(s["token"], IN.replace("300.00", "299.00"))
    kinds = sorted(e.kind for e in sms_memory_store.entries.values() if e.status == L.EntryStatus.POSTED)
    assert kinds == ["transfer_in", "transfer_out"]
    # Transfers are neither income nor spend: the same everywhere.
    assert amount(summary()["income"]) == Decimal(0) and money(dashboard()["expenses"]) == Decimal(0)


def test_own_transfer_partner_is_pure_and_needs_exactly_one_candidate() -> None:
    from anum_api.finance import sms_service as svc

    accounts = {a.id: a for a in (_account("a", AccountKind.BANK), _account("b", AccountKind.BANK), _account("c", AccountKind.BANK))}

    def entry(entry_id: str, kind: str, account_id: str, value: str, day: int = 6) -> L.JournalEntry:
        other = "income:transfer_in" if kind == "transfer_in" else "expense:transfer"
        return L.JournalEntry(id=entry_id, posted_on=date(2026, 11, day), kind=kind, description="x", source=L.EntrySource.SMS, created_at=NOW,
                              postings=[L.Posting(id=f"{entry_id}1", entry_id=entry_id, ledger_account=f"asset:{account_id}", account_id=account_id,
                                                  amount=Decimal(value), currency="SAR", created_at=NOW, **{k: v for k, v in OWNED.items() if k != "created_at" and k != "updated_at"}),
                                        L.Posting(id=f"{entry_id}2", entry_id=entry_id, ledger_account=other, amount=-Decimal(value), currency="SAR",
                                                  created_at=NOW, **{k: v for k, v in OWNED.items() if k != "created_at" and k != "updated_at"})],
                              **{k: v for k, v in OWNED.items() if k != "created_at" and k != "updated_at"})

    out = entry("o", "transfer_out", "a", "-300")
    assert svc.own_transfer_partner([out, entry("i", "transfer_in", "b", "300")], out, accounts).id == "i"
    assert svc.own_transfer_partner([out, entry("i", "transfer_in", "b", "300", day=9)], out, accounts) is None  # more than 2 days apart
    assert svc.own_transfer_partner([out, entry("i", "transfer_in", "a", "300")], out, accounts) is None  # same account
    two = [out, entry("i", "transfer_in", "b", "300"), entry("j", "transfer_in", "c", "300")]
    assert svc.own_transfer_partner(two, out, accounts) is None  # ambiguous: left as it is


# -- 5. net worth, debts and goals ------------------------------------------------------------------------------


def test_net_worth_counts_a_card_kept_as_an_account_and_a_debt_once() -> None:
    card = account(name="Card", kind="card", opening_balance="-5000")
    made = client.post(f"{API}/debts", headers=OWNER, json={"name": "Card", "kind": "card", "balance": "5000", "currency": "SAR",
                                                            "apr_percent": "30", "minimum_payment": "250", "account_id": card["id"]})
    assert made.status_code == 201, made.text
    loose = client.post(f"{API}/debts", headers=OWNER, json={"name": "Family loan", "kind": "other", "balance": "1000", "currency": "SAR"})
    assert loose.status_code == 201
    sm = summary()
    assert amount(sm["balances"]) == Decimal("-5000.00") and amount(sm["unlinked_debts"]) == Decimal("1000.00")
    assert amount(sm["net_worth"]) == Decimal("-6000.00") and amount(sm["debts"]) == Decimal("6000.00")


def test_a_card_or_loan_opening_balance_is_what_it_owes() -> None:
    refused = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Card", "kind": "card", "currency": "SAR", "opening_balance": "3000"})
    assert refused.status_code == 422 and "negative" in refused.text
    credit = account(name="Card", kind="card", opening_balance="40", credit_balance=True)
    assert Decimal(credit["opening_balance"]) == Decimal("40.00")
    owed = account(name="Loan", kind="loan", opening_balance="-3000")
    patched = client.patch(f"{API}/accounts/{owed['id']}", headers=OWNER, json={"opening_balance": "3000"})
    assert patched.status_code == 422


def test_a_debt_balance_follows_matched_payments() -> None:
    debt = Debt(id="d", name="Loan", kind=DebtKind.LOAN, balance=Decimal("5000"), currency="SAR", apr_percent=Decimal("12"),
                minimum_payment=Decimal("500"), **OWNED)
    later = datetime(2026, 11, 20, tzinfo=timezone.utc)
    payments = [O.DebtPayment("d", Decimal("2000"), "SAR", date(2026, 11, 15), later),
                O.DebtPayment("d", Decimal("100"), "SAR", date(2026, 10, 1), later),  # before the debt was saved: history only
                O.DebtPayment("other", Decimal("50"), "SAR", date(2026, 11, 15), later)]
    # 1% a month on 5000 is 50 of interest; the other 1950 is principal.
    assert O.debt_balance(debt, {}, {}, payments) == Decimal("3050")
    # A debt kept on a loan account owes what that account's ledger says.
    loan = _account("loan", AccountKind.LOAN, opening="-4000")
    linked = debt.model_copy(update={"account_id": "loan"})
    assert O.debt_balance(linked, {"loan": loan}, {"loan": Decimal("1700")}, payments) == Decimal("2300")


def test_a_goal_kept_in_an_account_follows_its_balance() -> None:
    savings = _account("s", AccountKind.BANK, opening="1000")
    goal = Goal(id="g", name="Trip", target_amount=Decimal("5000"), saved_amount=Decimal("10"), currency="SAR", account_id="s", **OWNED)
    assert O.current_goals([goal], [savings], {"s": Decimal("250")})[0].saved_amount == Decimal("1250")
    other = goal.model_copy(update={"account_id": None})
    assert O.current_goals([other], [savings], {})[0].saved_amount == Decimal("10")


# -- 6. bills honour matched payments -----------------------------------------------------------------------------


def _dewa(**over: object) -> Bill:
    values = {"id": "dewa", "name": "DEWA", "amount": Decimal("400"), "currency": "AED", "cadence": Cadence.MONTHLY, "next_due_on": date(2026, 11, 20),
              "category": Category.UTILITIES, **OWNED}
    values.update(over)
    return Bill(**values)


def _safe(bills: list[Bill], links: list, debts: list[Debt] | None = None, today: date = date(2026, 11, 10)):  # type: ignore[no-untyped-def]
    from anum_api.finance import safe_to_spend as S

    return S.compute(today=today, base_currency="AED", timezone="Asia/Dubai", accounts=[_account("b", AccountKind.BANK, "AED", "5000")], balances={},
                     bills=bills, debts=debts or [], goals=[], plan=PlanSettings(), rates=[], bill_links=links)


def test_a_bill_paid_early_is_not_taken_off_again() -> None:
    from anum_api.finance.summary import upcoming_bills

    bill = _dewa()
    paid = [_bill_link("dewa", date(2026, 11, 20))]
    assert [b.due_on for b in upcoming_bills([bill], date(2026, 11, 10), links=paid)] == []
    assert _safe([bill], paid).currencies[0].bills == Decimal(0)
    assert _safe([bill], []).currencies[0].bills == Decimal("400")


def test_an_unpaid_bill_that_passed_this_month_stays_as_overdue() -> None:
    from anum_api.finance.summary import upcoming_bills

    bill = _dewa(next_due_on=date(2026, 11, 5))
    rows = upcoming_bills([bill], date(2026, 11, 10))
    assert [(r.due_on, r.overdue, r.days_until) for r in rows] == [(date(2026, 11, 5), True, -5), (date(2026, 12, 5), False, 25)]
    result = _safe([bill], [])
    line = next(line for line in result.currencies[0].lines if line.kind == "bill")
    assert line.note == "bill_overdue" and result.currencies[0].bills == Decimal("400")
    assert _safe([bill], [_bill_link("dewa", date(2026, 11, 5))]).currencies[0].bills == Decimal(0)


def test_a_card_minimum_saved_as_a_bill_is_counted_once() -> None:
    bill = _dewa(id="cardbill", name="Visa card", amount=Decimal("250"), next_due_on=date(2026, 11, 25), category=Category.FEES)
    debt = Debt(id="visa", name="Visa card", kind=DebtKind.CARD, balance=Decimal("5000"), currency="AED", minimum_payment=Decimal("250"), due_day=25,
                **OWNED)
    result = _safe([bill], [], [debt])
    row = result.currencies[0]
    assert row.bills == Decimal("250") and row.minimum_payments == Decimal(0)
    assert any(line.note == "minimum_in_bill" for line in result.excluded)
    note = build_note(AdvisorInputs(today=date(2026, 11, 10), timezone="Asia/Dubai", base_currency="AED", accounts=[], balances={}, transactions=[],
                                    bills=[bill], debts=[debt]))
    coming = note.currencies[0].coming
    assert [(i.kind, i.amount) for i in coming.items] == [("bill", Decimal("250.00"))]


def test_summary_and_api_read_bill_links() -> None:
    bank = account(name="Bank", currency="AED")
    made = client.post(f"{API}/bills", headers=OWNER, json={"name": "DEWA", "amount": "400", "currency": "AED", "cadence": "monthly",
                                                            "next_due_on": "2026-11-28", "category": "utilities", "account_id": bank["id"]})
    assert made.status_code == 201, made.text
    pay = manual(bank["id"], "-400", "DEWA BILL", on="2026-11-02", category="utilities")
    entries = client.get(f"{API}/ledger/entries", headers=OWNER).json()["items"]
    canonical = next(e["canonical_id"] for e in entries if e["source_ref"] == pay["id"])
    matched = client.post(f"{API}/bills/{made.json()['id']}/matches", headers=OWNER, json={"canonical_id": canonical, "due_on": "2026-11-28"})
    assert matched.status_code == 201, matched.text
    upcoming = client.get(f"{API}/bills/upcoming", headers=OWNER, params={"days": 20}).json()
    assert all(b["due_on"] != "2026-11-28" for b in upcoming)
    safe = client.get(f"{API}/safe-to-spend", headers=OWNER).json()
    assert all(line["ref_id"] != made.json()["id"] for c in safe["currencies"] for line in c["lines"] if line["params"].get("date") == "2026-11-28")


# -- 7. loan repayments ---------------------------------------------------------------------------------------


def test_a_loan_repayment_is_a_transfer_and_only_its_interest_is_spending() -> None:
    bank, loan = _account("b", AccountKind.BANK), _account("l", AccountKind.LOAN)
    legs = L.plan(P.Kind.LOAN_REPAYMENT, Decimal("2000"), account=bank, counter=loan, interest=Decimal("300"))
    assert L.balanced(legs)
    rows = [_txn(i, leg.account_id or "", str(leg.amount), L.transaction_category(legs, leg)) for i, leg in enumerate(legs) if leg.account_id]
    assert sorted((t.account_id, t.amount, t.category) for t in rows) == [
        ("b", Decimal("-2000"), Category.TRANSFER), ("l", Decimal("-300"), Category.FEES), ("l", Decimal("2000"), Category.TRANSFER)]
    found = flows.figures(rows, {"b": AccountKind.BANK, "l": AccountKind.LOAN})
    assert found.spend == {"SAR": Decimal("300")} and found.income == {}
    ledger_spend = sum((leg.amount for leg in legs if flows.ledger_flow(leg.ledger_account) == flows.Flow.SPEND), Decimal(0))
    assert ledger_spend == Decimal("300")  # the dashboard counts the same 300


# -- 8. Apple Pay without a currency ----------------------------------------------------------------------------


def test_apple_pay_without_a_currency_waits_so_the_fx_message_is_the_one_purchase() -> None:
    s = wallet_setup()
    first = wallet(s["token"], amount="20", merchant="NETFLIX.COM").json()
    assert first["status"] == "review" and first["reason"] == "wallet_currency_missing"
    second = send(s["token"], "SYNTHETIC. Purchase USD 20.00 (SAR 75.40) at NETFLIX.COM card **1234 on 09/11/2026").json()
    assert second["status"] == "posted"
    assert money(dashboard()["expenses"]) == Decimal("75.40")


# -- 9. one definition everywhere --------------------------------------------------------------------------------


def test_advisor_income_matches_the_summary_rule() -> None:
    bank = _account("b", AccountKind.BANK, "AED")
    rows = [_txn(1, "b", "100", Category.REFUNDS, currency="AED"), _txn(2, "b", "50", Category.CASHBACK, currency="AED"),
            _txn(3, "b", "900", Category.INCOME, currency="AED"), _txn(4, "b", "-200", Category.TRANSFER, currency="AED")]
    note = build_note(AdvisorInputs(today=date(2026, 11, 9), timezone="Asia/Dubai", base_currency="AED", accounts=[bank], balances={},
                                    transactions=rows, bills=[], debts=[]))
    block = note.currencies[0]
    assert block.income_month == Decimal("900.00") and block.spend_month == Decimal("0.00")
    assert flows.figures(rows).income == {"AED": Decimal("900")}


def test_shared_figures_net_refunds_against_their_category() -> None:
    rows = [_txn(1, "b", "-100", Category.DINING), _txn(2, "b", "30", Category.DINING), _txn(3, "c", "500", Category.INCOME),
            _txn(4, "b", "-20", Category.TRANSFER)]
    found = flows.figures(rows, {"b": AccountKind.BANK, "c": AccountKind.CARD})
    assert found.spend == {"SAR": Decimal("70")} and found.by_category == {(Category.DINING, "SAR"): (Decimal("70"), 1)}
    assert found.income == {}  # money in on the card is the card payment received


# -- 10. remaining findings ----------------------------------------------------------------------------------------


def test_recurring_payments_match_saved_bills_by_amount_and_cadence_and_span_accounts() -> None:
    from anum_api.finance.summary import detect_recurring

    rows = [_txn(i, "a" if i % 2 else "b", "-400", Category.UTILITIES, day=day, currency="AED", description="DEWA BILL PAYMENT")
            for i, day in enumerate([date(2026, 7, 3), date(2026, 8, 3), date(2026, 10, 3), date(2026, 11, 3)])]
    found = detect_recurring(rows, date(2026, 11, 9), [])
    assert [(s.cadence, s.amount, s.occurrences) for s in found] == [(Cadence.MONTHLY, Decimal("400.00"), 4)]  # two accounts, one skipped month
    saved = _dewa(name="Electricity")
    assert detect_recurring(rows, date(2026, 11, 9), ["Electricity"], [saved]) == []


def test_advisor_full_runway_does_not_add_card_minimums_on_top_of_card_spending() -> None:
    from anum_api.finance.advisor import _runway

    bank, card = _account("b", AccountKind.BANK, "AED", "10000"), _account("c", AccountKind.CARD, "AED")
    debts = [Debt(id="cc", name="Card", kind=DebtKind.CARD, balance=Decimal("3000"), currency="AED", minimum_payment=Decimal("300"), account_id="c", **OWNED),
             Debt(id="ln", name="Loan", kind=DebtKind.LOAN, balance=Decimal("9000"), currency="AED", minimum_payment=Decimal("500"), **OWNED)]
    runway = _runway("AED", date(2026, 11, 9), [bank, card], {}, [], debts, {Category.DINING: Decimal("1000")}, "months", ["2026-10"],
                     date(2026, 7, 1), 130)
    full = next(s for s in runway.scenarios if s.kind == "full")
    assert full.monthly_outflow == Decimal("1500.00")  # 1000 spending + the loan's 500; the card's 300 pays card spending
    assert runway.minimums_monthly == Decimal("800.00")


@pytest.mark.parametrize(("raw", "expected"), [
    ("KWD 1.250", ("1.250", "KWD")),
    ("SAR 1.250", ("1250", "SAR")),
    ("SAR 1,250", ("1250", "SAR")),
    ("KWD 1,250", ("1.250", "KWD")),
    ("SAR 12.5", ("12.5", "SAR")),
    ("1,234,567 AED", ("1234567", "AED")),
])
def test_wallet_decimal_mark_follows_the_currency(raw: str, expected: tuple[str, str]) -> None:
    from anum_api.finance.wallet import parse_amount

    found = parse_amount(raw)
    assert (found.amount, found.currency) == (Decimal(expected[0]), expected[1])


@pytest.mark.parametrize("raw", ["1.250", "1,250", "SAR 1.2345"])
def test_wallet_ambiguous_amounts_go_to_review(raw: str) -> None:
    from anum_api.finance.wallet import WalletError, parse_amount

    with pytest.raises(WalletError) as caught:
        parse_amount(raw)
    assert caught.value.code == "ambiguous_amount"


def test_a_rate_dated_in_the_future_never_wins() -> None:
    from anum_api.finance import fx

    def rate(day: date, value: str) -> FxRate:
        return FxRate(id=f"r{day}", base="USD", quote="AED", rate=Decimal(value), as_of=day, source="manual", fetched_at=NOW,
                      **{k: v for k, v in OWNED.items() if k not in ("created_at", "updated_at")})

    found = fx.convert(Decimal(10), "USD", "AED", [rate(date(2026, 11, 8), "3.6725"), rate(date(2026, 11, 20), "9.0")], date(2026, 11, 9), 7)
    assert found is not None and found.rate == Decimal("3.6725")


def test_deleting_a_transaction_takes_the_owner_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.finance import sms_service as svc

    bank = account(name="Bank")
    row = manual(bank["id"], "-10", "SAMPLE SHOP")
    taken: list[str] = []
    real = svc.owner_lock

    @contextmanager
    def watched(context):  # type: ignore[no-untyped-def]
        taken.append(context.user_id)
        with real(context):
            yield

    monkeypatch.setattr(svc, "owner_lock", watched)
    assert client.delete(f"{API}/transactions/{row['id']}", headers=OWNER).status_code == 204
    assert taken == [OWNER["x-user-id"]]
