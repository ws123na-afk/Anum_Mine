"""Personal finance: the category ontology, learned merchants, budgets, safe to spend and recurring payments
(docs/finance.md#category-ontology, #learned-merchants, #budgets-and-planning, #safe-to-spend, #recurring-payments).

In-memory stores. Every merchant, description and amount here is SYNTHETIC and low-entropy.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from test_finance_sms import API, OTHER_PERSON, OWNER, VIEWER, account, clean, client  # noqa: F401

from anum_api.finance import categories as C
from anum_api.finance import recurring as R
from anum_api.finance import safe_to_spend as STS
from anum_api.finance.commitments import bill_target_id, plan_budget_report, salary_allocation
from anum_api.finance.flows import Flow, flow_of
from anum_api.finance.links_store import FinanceLink, LinkStatus
from anum_api.finance.models import (
    Account,
    AccountKind,
    Bill,
    Budget,
    BudgetKind,
    BudgetPeriod,
    Cadence,
    Category,
    CategorySource,
    InstallmentStatementEntry,
    PlanSettings,
    Transaction,
    TransactionType,
    TypeSource,
)
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.summary import detect_recurring

D = Decimal
TODAY = date(2026, 11, 15)
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)
OWNED = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u"}


@pytest.fixture(autouse=True)
def _rules() -> Iterator[None]:
    rules_memory_store.clear()
    yield
    rules_memory_store.clear()


def row(tid: str, amount: str, description: str, on: date, *, category: Category = Category.OTHER, account_id: str = "bank",
        kind: TransactionType | None = None, currency: str = "AED", sub: str | None = None,
        source: CategorySource = CategorySource.RULE) -> Transaction:
    return Transaction(**OWNED, id=tid, account_id=account_id, posted_on=on, amount=D(amount), currency=currency, description=description,
                       category=category, category_source=source, dedupe_hash=tid, created_at=NOW, updated_at=NOW, transaction_type=kind,
                       type_source=TypeSource.RULE if kind else None, subcategory_id=sub)


def budget(bid: str, amount: str, categories: list[Category], **extra: object) -> Budget:
    return Budget(id=bid, name=bid, currency="AED", amount=D(amount), categories=categories, created_at=NOW, updated_at=NOW, **extra)


def bill(bid: str, amount: str, due: date, category: Category = Category.HOUSING, **extra: object) -> Bill:
    return Bill(**OWNED, id=bid, name=bid, amount=D(amount), currency="AED", cadence=Cadence.MONTHLY, next_due_on=due, category=category,
                created_at=NOW, updated_at=NOW, **extra)


def link(bill_id: str, due: date, amount: str) -> FinanceLink:
    return FinanceLink(id=f"lnk_{bill_id}", tenant_id="t", workspace_id="w", owner_user_id="u", canonical_id=f"can_{bill_id}", dimension="bill",
                       role="payment", target_type="finance_bill_due", target_id=bill_target_id(bill_id, due), amount=D(amount), currency="AED",
                       status=LinkStatus.ACTIVE, idem_key="k", created_by="u", created_at=NOW, updated_at=NOW)


# -- the ontology -------------------------------------------------------------------------------------------


def test_every_category_sits_in_one_group_and_every_built_in_subcategory_under_a_real_category() -> None:
    placed = [c for g in C.GROUPS for c in g.categories]
    assert sorted(placed, key=lambda c: c.value) == sorted(Category, key=lambda c: c.value)
    assert len(placed) == len(set(placed))
    assert set(C.CATEGORY_NAMES) == set(Category)
    ids = [s.id for s in C.BUILT_IN_SUBCATEGORIES]
    assert len(ids) == len(set(ids)) and all(i.startswith("sys:") and len(i) <= 80 for i in ids)
    names = {s.parent: [] for s in C.BUILT_IN_SUBCATEGORIES}
    for s in C.BUILT_IN_SUBCATEGORIES:
        names[s.parent].append(s.en)
        assert s.ar and s.en and "—" not in s.en + s.ar and "–" not in s.en + s.ar
    # The owner's list: a sample from every group.
    for wanted in ("sys:vegetables", "sys:work_meals", "sys:deposit", "sys:laundry", "sys:home_wifi", "sys:tolls", "sys:fines", "sys:diapers",
                   "sys:dental", "sys:barber", "sys:installment_purchase", "sys:visa", "sys:parents", "sys:zakat", "sys:umrah", "sys:fx_markup",
                   "sys:cash_advance_fee", "sys:loan_repayment", "sys:money_lent", "sys:overtime", "sys:wallet_transfer", "sys:pets",
                   "sys:government"):
        assert wanted in C.BUILT_IN_SUB_BY_ID


def test_built_in_rules_file_new_categories_and_their_subcategories() -> None:
    cases = {
        "POS 1234 AL ANSARI EXCHANGE": (Category.FAMILY_SUPPORT, "sys:remittance"),
        "ZAKAT AL MAL DONATION": (Category.CHARITY, "sys:zakat"),
        "GENTS SALON BARBER": (Category.PERSONAL_CARE, "sys:barber"),
        "SPEEDY LAUNDRY": (Category.CLOTHING, "sys:laundry"),
        "BABYSHOP PAMPERS": (Category.FAMILY, "sys:diapers"),
        "SALIK TOLL": (Category.TRANSPORT, "sys:tolls"),
        "NETFLIX.COM": (Category.SUBSCRIPTIONS, "sys:streaming"),
        "POS 99 CARREFOUR CITY": (Category.GROCERIES, "sys:supermarket"),
    }
    for text, (category, sub) in cases.items():
        found = C.classify_text(text, D("-20"))
        assert (found.category, found.subcategory_id, found.type) == (category, sub, TransactionType.EXPENSE), text
    # Money in never becomes zakat spending, and a plain transfer to a person still waits for the owner.
    assert C.classify_text("TRANSFER TO SAMPLE PERSON", D("-50")).type == TransactionType.UNCATEGORIZED
    assert C.group_of(Category.TRANSFER, TransactionType.LOAN_PRINCIPAL) == "debt"
    assert C.group_of(Category.TRANSFER, TransactionType.INTERNAL_TRANSFER) == "cash_transfers"


def test_accounting_rules_hold_for_the_new_categories() -> None:
    kinds = {"bank": AccountKind.BANK, "card": AccountKind.CARD}
    own = row("own", "-500", "OWN ACCOUNT TRANSFER", TODAY, category=Category.TRANSFER, kind=TransactionType.INTERNAL_TRANSFER)
    pay_card = row("pay", "-900", "CARD PAYMENT", TODAY, category=Category.TRANSFER, kind=TransactionType.CARD_PAYMENT)
    card_in = row("cin", "900", "PAYMENT RECEIVED THANK YOU", TODAY, category=Category.TRANSFER, kind=TransactionType.CARD_PAYMENT,
                  account_id="card")
    borrowed = row("bor", "1000", "LOAN FROM SAMPLE", TODAY, category=Category.TRANSFER, kind=TransactionType.LOAN_RECEIVED)
    cashback = row("cb", "12", "CASHBACK", TODAY, category=Category.CASHBACK, kind=TransactionType.CASHBACK)
    fee = row("fee", "-25", "ANNUAL FEE", TODAY, category=Category.FEES, kind=TransactionType.BANK_FEE)
    assert {flow_of(t, kinds) for t in (own, pay_card, card_in, borrowed)} == {Flow.TRANSFER}
    assert flow_of(cashback, kinds) == Flow.CASHBACK and flow_of(fee, kinds) == Flow.SPEND
    lent = row("lent", "-300", "TO SAMPLE FRIEND", TODAY, category=Category.FAMILY_SUPPORT, kind=TransactionType.EXPENSE)
    assert flow_of(lent, kinds, frozenset({"lent"})) == Flow.TRANSFER  # money lent to a person is never consumption


# -- budgets ----------------------------------------------------------------------------------------------


def test_budget_actuals_come_from_linked_rows_and_a_paid_bill_counts_once() -> None:
    rent_bill = bill("rent", "3000", date(2026, 11, 1))
    phone_bill = bill("phone", "200", date(2026, 11, 25), Category.TELECOM)
    rows = [
        row("r1", "-3000", "LANDLORD RENT", date(2026, 11, 1), category=Category.HOUSING, kind=TransactionType.EXPENSE),
        row("own", "-1000", "OWN ACCOUNT", date(2026, 11, 2), category=Category.TRANSFER, kind=TransactionType.INTERNAL_TRANSFER),
        row("g1", "-300", "CARREFOUR", date(2026, 11, 3), category=Category.GROCERIES, kind=TransactionType.EXPENSE),
        row("g2", "50", "CARREFOUR REFUND", date(2026, 11, 4), category=Category.GROCERIES, kind=TransactionType.REFUND),
        row("card", "-700", "CARD PAYMENT", date(2026, 11, 5), category=Category.TRANSFER, kind=TransactionType.CARD_PAYMENT),
    ]
    budgets = [
        budget("housing", "3500", [Category.HOUSING, Category.TELECOM], fixed=True),
        budget("food", "1000", [Category.GROCERIES]),
    ]
    report = plan_budget_report(budgets, rows, on=TODAY, today=TODAY, bills=[rent_bill, phone_bill], bill_links=[link("rent", date(2026, 11, 1), "3000")])
    lines = {line.id: line for line in report.budgets}
    housing = lines["housing"]
    # Rent is paid (matched): in spent once, not coming. The phone bill is still coming. A fixed budget adds no pace.
    assert housing.spent == D("3000.00") and housing.coming == D("200.00") and [c.bill_id for c in housing.coming_items] == ["phone"]
    assert housing.forecast == D("3200.00") and housing.risk == "watch" and housing.remaining == D("500.00") and housing.percent == 85
    food = lines["food"]
    # The refund lowers its category; the own-account transfer and the card repayment are never spending.
    assert food.spent == D("250.00") and food.rows == 1
    assert food.forecast is not None and food.forecast > food.spent  # a variable budget runs at its pace to the month end
    assert {t.currency for t in report.totals} == {"AED"} and report.totals[0].spent == D("3250.00")


def test_budget_risk_weekly_period_subcategory_narrowing_and_savings_pace() -> None:
    rows = [
        row("a", "-120", "TALABAT", date(2026, 11, 10), category=Category.DINING, kind=TransactionType.EXPENSE, sub="sys:delivery"),
        row("b", "-90", "STARBUCKS", date(2026, 11, 11), category=Category.DINING, kind=TransactionType.EXPENSE, sub="sys:coffee"),
        row("s", "400", "TRANSFER IN", date(2026, 11, 2), category=Category.TRANSFER, kind=TransactionType.INTERNAL_TRANSFER, account_id="save"),
    ]
    from anum_api.finance.models import Goal

    goal = Goal(**OWNED, id="g", name="Reserve", target_amount=D("6000"), currency="AED", account_id="save", created_at=NOW, updated_at=NOW)
    budgets = [
        budget("delivery", "100", [], subcategory_ids=["sys:delivery"], period=BudgetPeriod.WEEKLY),
        budget("save", "1500", [], kind=BudgetKind.EMERGENCY, goal_id="g"),
    ]
    report = plan_budget_report(budgets, rows, on=TODAY, today=TODAY, bills=[], bill_links=[], goals=[goal])
    lines = {line.id: line for line in report.budgets}
    weekly = lines["delivery"]
    assert (weekly.period_start, weekly.period_end) == (date(2026, 11, 9), date(2026, 11, 15))
    assert weekly.spent == D("120.00") and weekly.risk == "over" and weekly.over == D("20.00")  # coffee is not delivery
    reserve = lines["save"]
    assert reserve.spent == D("400.00") and reserve.basis == "goal_account" and reserve.risk == "behind"


def test_salary_allocation_splits_the_income_and_says_what_is_left() -> None:
    budgets = [
        budget("rent", "3000", [Category.HOUSING]),
        budget("food", "200", [Category.GROCERIES], period=BudgetPeriod.WEEKLY),
        budget("home", "1000", [], kind=BudgetKind.REMITTANCE),
        budget("trip", "900", [Category.TRAVEL], period=BudgetPeriod.CUSTOM, starts_on=date(2026, 12, 1), ends_on=date(2026, 12, 20)),
    ]
    view = salary_allocation(budgets, "AED", D("8000"), "salary_profile")
    assert view.buckets["housing"] == D("3000.00") and view.buckets["food"] == D("866.67") and view.buckets["remittances"] == D("1000.00")
    assert view.allocated == D("4866.67") and view.unallocated == D("3133.33") and [l.budget_id for l in view.left_out] == ["trip"]


# -- safe to spend ------------------------------------------------------------------------------------------


def _accounts() -> list[Account]:
    bank = Account(**OWNED, id="bank", name="Current", kind=AccountKind.BANK, currency="AED", created_at=NOW, updated_at=NOW)
    card = Account(**OWNED, id="card", name="Visa", kind=AccountKind.CARD, currency="AED", created_at=NOW, updated_at=NOW)
    return [bank, card]


def test_safe_to_spend_counts_cash_only_and_takes_off_installments_and_a_kept_budget_without_double_counting() -> None:
    accounts = _accounts()
    # The card shows a credit balance: card credit is not cash and is never counted.
    balances = {"bank": D("5000"), "card": D("800")}
    rent_bill = bill("rent", "1500", date(2026, 11, 28))
    entry = InstallmentStatementEntry(provider="Tamara", order_id="ORDER1", merchant="SAMPLE STORE", due_on=date(2026, 11, 20), installment=2,
                                      total_installments=4, amount=D("125"), currency="AED", source_status="scheduled")
    paid = entry.model_copy(update={"order_id": "ORDER2", "source_status": "paid"})
    housing = budget("housing", "2000", [Category.HOUSING], reserve_in_safe_to_spend=True)
    rows = [row("h", "-100", "HOME REPAIR", date(2026, 11, 3), category=Category.HOUSING, kind=TransactionType.EXPENSE)]
    view = STS.compute(today=TODAY, base_currency="AED", timezone="Asia/Dubai", accounts=accounts, balances=balances, bills=[rent_bill], debts=[],
                       goals=[], plan=PlanSettings(), rates=[], installments=[entry, paid], budgets=[housing], month_rows=rows)
    aed = view.currencies[0]
    assert aed.available == D("5000") and any(line.note == "account_card" for line in view.excluded)
    # Bills 1500; the unpaid installment 125 (the paid one is not counted); the housing budget keeps back
    # 2000 - 100 spent - 1500 rent still coming = 400 (the rent is counted once, as the bill).
    assert aed.bills == D("1500") and aed.planned == D("525.00")
    assert aed.safe_to_spend == D("5000") - D("1500") - D("525.00")
    assert "Card credit is not cash" in aed.explanation


# -- recurring ----------------------------------------------------------------------------------------------


def _monthly(prefix: str, amount: str, description: str, category: Category, count: int = 4, last: str | None = None) -> list[Transaction]:
    out = []
    for i in range(count):
        on = date(2026, 7 + i, 5)
        value = last if last is not None and i == count - 1 else amount
        out.append(row(f"{prefix}{i}", f"-{value}", description, on, category=category, kind=TransactionType.EXPENSE))
    return out


def test_recurring_finds_bills_and_subscriptions_but_not_ordinary_purchases() -> None:
    rows = (_monthly("n", "55", "NETFLIX.COM", Category.SUBSCRIPTIONS, last="60")
            + _monthly("r", "4000", "LANDLORD RENT", Category.HOUSING)
            + _monthly("h", "1000", "AL ANSARI EXCHANGE", Category.FAMILY_SUPPORT))
    # The same supermarket every week for a similar amount: an ordinary purchase, never a subscription.
    for i in range(8):
        rows.append(row(f"g{i}", "-150", "CARREFOUR CITY", date(2026, 9, 1) + timedelta(days=7 * i), category=Category.GROCERIES,
                        kind=TransactionType.EXPENSE))
    found = {s.name: s for s in detect_recurring(rows, TODAY, [])}
    assert set(found) == {"NETFLIX.COM", "LANDLORD RENT", "AL ANSARI EXCHANGE"}
    assert (found["NETFLIX.COM"].kind, found["LANDLORD RENT"].kind, found["AL ANSARI EXCHANGE"].kind) == ("streaming", "rent", "remittance")
    assert found["NETFLIX.COM"].price_change == D("5.00") and found["NETFLIX.COM"].last_amount == D("60.00")
    assert found["LANDLORD RENT"].price_change is None and len(found["LANDLORD RENT"].transaction_ids) == 4


def test_kept_bill_reports_price_change_and_still_billing_while_rarely_used() -> None:
    rows = _monthly("s", "30", "SAMPLEFLIX SUBSCRIPTION", Category.SUBSCRIPTIONS, last="36")
    kept = bill("sub", "30", date(2026, 11, 5), Category.SUBSCRIPTIONS, merchant="sampleflix subscription", usage="rarely")
    health = R.bill_health(kept, rows, TODAY)
    assert health.price_change == D("6.00") and health.still_billing_rarely_used and health.last_paid_on == date(2026, 10, 5)


class _Candidate:
    def __init__(self, canonical_id: str, posted_on: date, amount: str, merchant: str, account_id: str = "bank") -> None:
        self.canonical_id, self.posted_on, self.amount, self.remaining = canonical_id, posted_on, D(amount), D(amount)
        self.merchant, self.account_id, self.currency = merchant, account_id, "AED"


def test_a_confirmed_bill_links_only_its_one_strong_payment() -> None:
    kept = bill("net", "55", date(2026, 11, 5), Category.SUBSCRIPTIONS, merchant="netflix", auto_link=True)
    due = date(2026, 11, 5)
    strong = _Candidate("c1", date(2026, 11, 6), "55.00", "NETFLIX.COM")
    other_merchant = _Candidate("c2", date(2026, 11, 6), "55.00", "SAMPLE CINEMA")
    wrong_amount = _Candidate("c3", date(2026, 11, 5), "80.00", "NETFLIX.COM")
    links, skipped = R.plan_auto_links([(kept, due, [strong, other_merchant, wrong_amount])])
    assert [(l.bill_id, l.canonical_id, l.reason) for l in links] == [("net", "c1", "merchant_amount_window")] and not skipped
    assert "amount" not in links[0].evidence or links[0].evidence["amount_agrees"] == "yes"
    # Two identical payments: never pick one.
    twin = _Candidate("c4", date(2026, 11, 4), "55.00", "NETFLIX.COM")
    links, skipped = R.plan_auto_links([(kept, due, [strong, twin])])
    assert not links and skipped == [("net", due, "ambiguous")]
    # Two bills wanting the same payment: neither takes it.
    second = bill("net2", "55", date(2026, 11, 5), Category.SUBSCRIPTIONS, merchant="netflix", auto_link=True)
    links, skipped = R.plan_auto_links([(kept, due, [strong]), (second, due, [strong])])
    assert not links and {s[2] for s in skipped} == {"contested"}


# -- the API: learning, budgets, the tree, recurring, privacy ----------------------------------------------


def txn(account_id: str, amount: str, description: str, *, days: int = 3, headers: dict = OWNER) -> dict:
    on = (date.today() - timedelta(days=days)).isoformat()
    made = client.post(f"{API}/transactions", headers=headers, json={"account_id": account_id, "posted_on": on, "amount": amount,
                                                                     "description": description})
    assert made.status_code == 201, made.text
    return made.json()


def test_owner_correction_with_permission_files_the_next_row_of_that_merchant() -> None:
    bank = account()
    first = txn(bank["id"], "-40.00", "POS 1 QUOKKA KITCHEN TESTCITY")
    assert first["transaction_type"] == "uncategorized"
    # Without the owner's permission the correction teaches nothing: the next row still waits.
    client.patch(f"{API}/transactions/{first['id']}", headers=OWNER, json={"category": "dining"})
    assert txn(bank["id"], "-41.00", "POS 2 QUOKKA KITCHEN TESTCITY")["transaction_type"] == "uncategorized"
    fixed = client.patch(f"{API}/transactions/{first['id']}", headers=OWNER,
                         json={"category": "dining", "subcategory_id": "sys:restaurants", "learn_merchant": True})
    assert fixed.status_code == 200, fixed.text
    nxt = txn(bank["id"], "-42.00", "POS 3 QUOKKA KITCHEN TESTCITY")
    assert (nxt["category"], nxt["subcategory_id"], nxt["category_source"], nxt["type_reason"]) == ("dining", "sys:restaurants", "owner_rule",
                                                                                                       "learned_merchant")
    mappings = client.get(f"{API}/category-mappings", headers=OWNER).json()
    assert mappings["total"] == 1 and mappings["items"][0]["merchant"] == "quokka kitchen testcity"
    # Money in from the same name is another side: not filed by it.
    assert txn(bank["id"], "15.00", "POS 4 QUOKKA KITCHEN TESTCITY")["category"] != "dining"
    # Switched off: later rows go back to the rules. Rows already filed keep their category.
    off = client.patch(f"{API}/category-mappings", headers=OWNER, json={"merchant": "quokka kitchen testcity", "direction": "out", "enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert txn(bank["id"], "-43.00", "POS 5 QUOKKA KITCHEN TESTCITY")["transaction_type"] == "uncategorized"
    assert client.get(f"{API}/transactions/{nxt['id']}", headers=OWNER).json()["category"] == "dining"
    assert client.delete(f"{API}/category-mappings", headers=OWNER, params={"merchant": "quokka kitchen testcity", "direction": "out"}).status_code == 204
    # Another person never sees it.
    assert client.get(f"{API}/category-mappings", headers=OTHER_PERSON).status_code in (200, 403)
    if client.get(f"{API}/category-mappings", headers=OTHER_PERSON).status_code == 200:
        assert client.get(f"{API}/category-mappings", headers=OTHER_PERSON).json()["total"] == 0


def test_apply_similar_previews_earlier_rows_and_leaves_owner_rows() -> None:
    bank = account()
    rows = [txn(bank["id"], f"-{20 + i}.00", f"POS {i} WOMBAT GROCER TESTCITY", days=5 + i) for i in range(3)]
    client.patch(f"{API}/transactions/{rows[2]['id']}", headers=OWNER, json={"category": "shopping"})
    client.patch(f"{API}/transactions/{rows[0]['id']}", headers=OWNER, json={"category": "groceries", "learn_merchant": True})
    similar = client.post(f"{API}/transactions/{rows[0]['id']}/similar", headers=OWNER, json={"scope": "all"})
    assert similar.status_code == 200, similar.text
    preview = similar.json()["preview"]
    assert {r["transaction_id"] for r in preview["rows"]} == {rows[1]["id"]} and preview["manual_kept"] == 2
    assert client.get(f"{API}/transactions/{rows[1]['id']}", headers=OWNER).json()["transaction_type"] == "uncategorized"


def test_built_in_subcategory_must_sit_under_the_row_category() -> None:
    bank = account()
    made = txn(bank["id"], "-9.00", "SAMPLE PLACE")
    wrong = client.patch(f"{API}/transactions/{made['id']}", headers=OWNER, json={"category": "dining", "subcategory_id": "sys:zakat"})
    assert wrong.status_code == 422
    ok = client.patch(f"{API}/transactions/{made['id']}", headers=OWNER, json={"category": "charity", "subcategory_id": "sys:zakat"})
    assert ok.status_code == 200 and ok.json()["subcategory_id"] == "sys:zakat"


def test_budget_routes_create_report_validate_and_stay_private() -> None:
    bank = account(currency="AED")
    txn(bank["id"], "-80.00", "POS CARREFOUR CITY", days=0)
    made = client.post(f"{API}/budgets/plans", headers=OWNER, json={"name": "Food", "currency": "AED", "amount": "1000", "categories": ["groceries"]})
    assert made.status_code == 201, made.text
    line = made.json()
    assert line["spent"] == "80.00" and line["essential"] is True and line["allocation"] == "food"
    assert client.post(f"{API}/budgets/plans", headers=OWNER, json={"name": "X", "currency": "AED", "amount": "10"}).status_code == 422
    assert client.post(f"{API}/budgets/plans", headers=OWNER,
                       json={"name": "X", "currency": "AED", "amount": "10", "categories": ["transfer"]}).status_code == 422
    assert client.post(f"{API}/budgets/plans", headers=OWNER,
                       json={"name": "X", "currency": "AED", "amount": "10", "kind": "savings", "goal_id": "nope"}).status_code == 422
    assert client.post(f"{API}/budgets/plans", headers=OWNER, json={"name": "X", "currency": "AED", "amount": "10", "categories": ["travel"],
                                                                    "period": "custom", "starts_on": "2026-12-10", "ends_on": "2026-12-01"}).status_code == 422
    changed = client.patch(f"{API}/budgets/plans/{line['id']}", headers=OWNER,
                           json={"name": "Food", "currency": "AED", "amount": "50", "categories": ["groceries"], "alert_percent": 90})
    assert changed.status_code == 200 and changed.json()["risk"] == "over"
    report = client.get(f"{API}/budgets/plans", headers=OWNER).json()
    assert [b["name"] for b in report["budgets"]] == ["Food"]
    assert client.get(f"{API}/budgets/allocation", headers=OWNER).status_code == 200
    # Another person, a viewer and another workspace see none of it.
    assert client.get(f"{API}/budgets/plans", headers=OTHER_PERSON).json()["budgets"] == []
    assert client.patch(f"{API}/budgets/plans/{line['id']}", headers=OTHER_PERSON,
                        json={"name": "Mine", "currency": "AED", "amount": "1", "categories": ["groceries"]}).status_code in (403, 404)
    assert client.get(f"{API}/budgets/plans", headers=VIEWER).status_code == 403
    assert client.delete(f"{API}/budgets/plans/{line['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/budgets/plans", headers=OWNER).json()["budgets"] == []


def test_category_tree_lists_groups_with_spend_that_matches_the_shared_rule() -> None:
    bank = account(currency="AED")
    txn(bank["id"], "-30.00", "POS CARREFOUR CITY", days=0)
    txn(bank["id"], "-500.00", "OWN ACCOUNT TRANSFER", days=0)
    month = date.today().strftime("%Y-%m")
    tree = client.get(f"{API}/categories", headers=OWNER, params={"month": month})
    assert tree.status_code == 200, tree.text
    body = tree.json()
    assert [g["id"] for g in body["groups"]][:2] == ["food_drinks", "housing"]
    food = next(g for g in body["groups"] if g["id"] == "food_drinks")
    groceries = next(c for c in food["categories"] if c["id"] == "groceries")
    assert any(s["id"] == "sys:supermarket" and s["name_ar"] for s in groceries["subcategories"])
    assert body["group_totals"] == {"food_drinks": {"AED": "30.00"}}


def test_recurring_routes_confirm_reject_and_settings() -> None:
    bank = account(currency="AED")
    for i in range(4):
        txn(bank["id"], "-55.00", "NETFLIX.COM", days=5 + 30 * i)
    for i in range(4):
        txn(bank["id"], "-77.00", "SAMPLE GYM MEMBERSHIP", days=6 + 30 * i)
    view = client.get(f"{API}/recurring", headers=OWNER).json()
    found = {f["name"]: f for f in view["found"]}
    assert set(found) == {"NETFLIX.COM", "SAMPLE GYM MEMBERSHIP"}
    kept = client.post(f"{API}/recurring/confirm", headers=OWNER, json={
        "key": found["NETFLIX.COM"]["key"], "name": "Netflix", "amount": "55", "currency": "AED", "cadence": "monthly",
        "next_due_on": found["NETFLIX.COM"]["next_due_on"], "category": "subscriptions", "account_id": bank["id"],
        "merchant": found["NETFLIX.COM"]["merchant"], "recurring_kind": "streaming", "auto_link": True})
    assert kept.status_code == 201, kept.text and kept.json()["auto_link"] is True
    assert client.post(f"{API}/recurring/reject", headers=OWNER, json={"key": found["SAMPLE GYM MEMBERSHIP"]["key"]}).status_code == 200
    after = client.get(f"{API}/recurring", headers=OWNER).json()
    assert after["found"] == [] and after["dismissed"] == 1 and [k["name"] for k in after["kept"]] == ["Netflix"]
    assert after["kept"][0]["last_amount"] == "55.00" and after["kept"][0]["price_change"] is None
    settings = client.patch(f"{API}/recurring/bills/{kept.json()['id']}", headers=OWNER, json={"usage": "rarely", "auto_link": False})
    assert settings.status_code == 200 and settings.json()["usage"] == "rarely" and settings.json()["still_billing_rarely_used"] is True
    # Editing the bill on the Bills page keeps its recurring settings.
    edit = client.put(f"{API}/bills/{kept.json()['id']}", headers=OWNER, json={"name": "Netflix", "amount": "55", "currency": "AED",
                                                                               "cadence": "monthly", "next_due_on": kept.json()["next_due_on"],
                                                                               "category": "subscriptions", "account_id": bank["id"]})
    if edit.status_code == 405:
        edit = client.patch(f"{API}/bills/{kept.json()['id']}", headers=OWNER, json={"name": "Netflix", "amount": "55", "currency": "AED",
                                                                                     "cadence": "monthly", "next_due_on": kept.json()["next_due_on"],
                                                                                     "category": "subscriptions", "account_id": bank["id"]})
    assert edit.status_code == 200, edit.text
    assert client.get(f"{API}/recurring", headers=OWNER).json()["kept"][0]["usage"] == "rarely"
    assert client.get(f"{API}/recurring", headers=OTHER_PERSON).json()["kept"] == []
