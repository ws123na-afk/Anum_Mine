"""Transaction types (docs/finance.md#transaction-types): the scenarios the owner asked for, in-memory stores.

Each scenario checks the stored type and how the row counts in the summary (and, where an SMS is involved, on the
ledger dashboard too). Every message, merchant, name and amount here is SYNTHETIC and low-entropy.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from test_finance_sms import (
    API,
    FX,
    OWNER,
    SALARY_EN,
    account,
    clean,  # noqa: F401  (the autouse fixture that empties the in-memory stores)
    client,
    dashboard,
    money,
    send,
    setup,
)

from anum_api.finance import flows
from anum_api.finance import txn_types as TT
from anum_api.finance.categories import classify_text
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.models import (
    AccountKind,
    Category,
    CategoryRule,
    CategorySource,
    Transaction,
    TransactionType,
    TypeSource,
)
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store

T = TransactionType
NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
OWNED = {"tenant_id": "t", "workspace_id": "w", "owner_user_id": "u", "created_at": NOW, "updated_at": NOW}
CSV = "Date,Description,Amount\n"


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


def do_import(account_id: str, rows: str) -> dict:
    response = client.post(f"{API}/imports", headers=OWNER, json={"account_id": account_id, "content": CSV + rows, "filename": "nov.csv"})
    assert response.status_code == 201, response.text
    return response.json()


def stored(**where: object) -> list[Transaction]:
    return [t for t in finance_store.transactions.values() if all(getattr(t, k) == v for k, v in where.items())]


def register(**params: str) -> dict:
    response = client.get(f"{API}/register", headers=OWNER, params={"from": "2026-11-01", "to": "2026-11-30", **params})
    assert response.status_code == 200, response.text
    return response.json()


# -- the owner's scenarios ------------------------------------------------------------------------------------


def test_room_rent_paid_out_is_housing_spending_from_a_statement_and_from_an_sms() -> None:
    s = setup()
    do_import(s["bank"]["id"], "2026-11-02,TRANSFER TO SAMPLE PERSON ROOM RENT,-2600.00\n")
    row = stored(account_id=s["bank"]["id"])[0]
    assert (row.category, row.transaction_type) == (Category.ROOM_RENT, T.EXPENSE)
    # The same payment told by the bank's SMS is filed the same way (audit C3).
    assert send(s["token"], "SYNTHETIC. SAR 2,600.00 transferred to ROOM RENT SAMPLE from account ***9999 on 04/11/2026").json()["status"] == "posted"
    sms_row = stored(account_id=s["savings"]["id"])[0]
    assert (sms_row.category, sms_row.transaction_type, sms_row.type_source) == (Category.ROOM_RENT, T.EXPENSE, TypeSource.SMS)
    sm = summary()
    rent = next(r for r in sm["spend_by_category"] if r["category"] == "room_rent")
    assert Decimal(rent["amount"]) == Decimal("5200.00") and rent["count"] == 2
    assert amount(sm["income"]) == Decimal(0)
    assert money(dashboard()["expenses"]) == Decimal("5200.00")
    # Plain rent is housing.
    assert classify_text("TRANSFER TO SAMPLE LANDLORD RENT", Decimal("-2600"), None, AccountKind.BANK).category == Category.HOUSING


def test_a_plain_transfer_to_someone_else_is_spending_in_other_from_both_sources() -> None:
    s = setup()
    do_import(s["bank"]["id"], "2026-11-02,TRANSFER TO SAMPLE PERSON,-150.00\n")
    send(s["token"], "SYNTHETIC. SAR 150.00 transferred to SAMPLE PERSON from account ***9999 on 04/11/2026")
    rows = sorted((t.category, t.transaction_type, t.type_reason) for t in finance_store.transactions.values())
    assert rows == [(Category.OTHER, T.EXPENSE, "transfer_out_third_party")] * 2
    assert amount(summary()["spend"]) == Decimal("300.00") and money(dashboard()["expenses"]) == Decimal("300.00")


def test_salary_received_is_income_from_a_statement_and_from_an_sms() -> None:
    s = setup()
    do_import(s["savings"]["id"], "2026-11-01,WPS SALARY ACME,9000.00\n")
    assert send(s["token"], SALARY_EN).json()["status"] == "posted"
    assert {t.transaction_type for t in finance_store.transactions.values()} == {T.INCOME}
    assert amount(summary()["income"]) == Decimal("21000.00")
    assert money(dashboard()["income"]) == Decimal("21000.00")


def test_cashback_is_its_own_line() -> None:
    card = account(name="Card", kind="card")
    row = manual(card["id"], "15", "CASHBACK CREDIT")
    assert row["transaction_type"] == "cashback" and row["type_source"] == "rule"
    sm = summary()
    assert amount(sm["cashback"]) == Decimal("15.00") and amount(sm["income"]) == Decimal(0) and amount(sm["spend"]) == Decimal(0)


def test_a_statement_refund_finds_its_purchase_and_lowers_that_category() -> None:
    bank = account(name="Bank")
    do_import(bank["id"], "2026-11-02,PANDA HYPERMARKET,-80.00\n")
    do_import(bank["id"], "2026-11-06,REFUND PANDA HYPERMARKET,80.00\n")
    purchase = next(t for t in finance_store.transactions.values() if t.amount < 0)
    refund = next(t for t in finance_store.transactions.values() if t.amount > 0)
    assert (refund.transaction_type, refund.type_source, refund.type_reason) == (T.REFUND, TypeSource.MATCH, "refund_matched")
    assert refund.related_transaction_id == purchase.id and refund.category == Category.GROCERIES
    assert refund.category_source == CategorySource.MATCH
    sm = summary()
    assert amount(sm["spend"]) == Decimal(0) and amount(sm["income"]) == Decimal(0) and amount(sm["refunds"]) == Decimal(0)


def test_a_partial_refund_is_only_a_suggestion_and_stays_its_own_line() -> None:
    bank = account(name="Bank")
    do_import(bank["id"], "2026-11-02,PANDA HYPERMARKET,-80.00\n2026-11-06,REFUND PANDA HYPERMARKET,30.00\n")
    purchase = next(t for t in finance_store.transactions.values() if t.amount < 0)
    refund = next(t for t in finance_store.transactions.values() if t.amount > 0)
    assert refund.category == Category.REFUNDS and refund.type_reason == "refund_suggested" and refund.related_transaction_id == purchase.id
    assert refund.type_confidence is not None and refund.type_confidence < TT.REVIEW_BELOW
    sm = summary()
    assert amount(sm["spend"]) == Decimal("80.00") and amount(sm["refunds"]) == Decimal("30.00") and amount(sm["income"]) == Decimal(0)


def test_a_refund_from_another_merchant_finds_nothing() -> None:
    bank = account(name="Bank")
    manual(bank["id"], "-80", "PANDA HYPERMARKET", on="2026-11-02")
    refund = manual(bank["id"], "80", "REFUND SAMPLE SHOP", on="2026-11-04")
    assert refund["related_transaction_id"] is None and refund["category"] == "refunds"


def test_a_usd_card_purchase_billed_in_sar_keeps_its_original_amount() -> None:
    s = setup()
    assert send(s["token"], FX).json()["status"] == "posted"
    entry = next(iter(sms_memory_store.entries.values()))
    assert (entry.original_amount, entry.original_currency) == (Decimal("20.00"), "USD")
    row = stored(account_id=s["card"]["id"])[0]
    assert (row.amount, row.currency, row.transaction_type) == (Decimal("-75.40"), "SAR", T.EXPENSE)
    # The same from a statement that prints both amounts.
    header = "Date,Description,Amount,Currency,Billed Amount,Billed Currency\n"
    imported = client.post(f"{API}/imports", headers=OWNER, json={"account_id": s["bank"]["id"], "filename": "fx.csv",
                                                                   "content": header + "2026-11-08,NETFLIX.COM,20.00,USD,-76.80,SAR\n"})
    assert imported.status_code == 201, imported.text
    statement = stored(account_id=s["bank"]["id"])[0]
    assert (statement.amount, statement.currency, statement.transaction_type) == (Decimal("-76.80"), "SAR", T.EXPENSE)
    plain = next(e for e in sms_memory_store.entries.values() if e.source_ref == statement.id)
    assert (plain.original_amount, plain.original_currency) == (Decimal("20.00"), "USD")
    assert amount(summary()["spend"]) == Decimal("152.20")


def test_an_internal_transfer_between_owned_accounts_is_neither_income_nor_spend() -> None:
    bank, savings = account(name="Bank"), account(name="Savings")
    manual(bank["id"], "-500", "OWN ACCOUNT TRANSFER")
    manual(savings["id"], "500", "OWN ACCOUNT TRANSFER")
    assert {t.transaction_type for t in finance_store.transactions.values()} == {T.INTERNAL_TRANSFER}
    sm = summary()
    assert amount(sm["income"]) == Decimal(0) and amount(sm["spend"]) == Decimal(0) and sm["uncategorized_count"] == 0


def test_a_card_payment_is_counted_once() -> None:
    bank = account(name="Bank")
    card = account(name="Card", kind="card")
    manual(card["id"], "-500", "CARREFOUR RIYADH")
    manual(bank["id"], "-500", "CREDIT CARD PAYMENT 1234")
    manual(card["id"], "500", "PAYMENT RECEIVED - THANK YOU")
    types = sorted(t.transaction_type for t in finance_store.transactions.values())
    assert types == [T.CARD_PAYMENT, T.CARD_PAYMENT, T.EXPENSE]
    sm = summary()
    assert amount(sm["spend"]) == Decimal("500.00") and amount(sm["income"]) == Decimal(0)


def test_loan_principal_is_a_transfer_and_only_interest_is_spending() -> None:
    bank, loan = account(name="Bank"), account(name="Loan", kind="loan", opening_balance="-10000")
    manual(bank["id"], "-1700", "LOAN INSTALMENT", transaction_type="loan_principal")
    manual(loan["id"], "1700", "LOAN INSTALMENT", transaction_type="loan_principal")
    manual(loan["id"], "-300", "LOAN INTEREST")
    assert sorted(t.transaction_type for t in finance_store.transactions.values()) == [T.INTEREST, T.LOAN_PRINCIPAL, T.LOAN_PRINCIPAL]
    sm = summary()
    assert amount(sm["spend"]) == Decimal("300.00") and amount(sm["income"]) == Decimal(0)
    interest = next(r for r in sm["spend_by_category"] if r["category"] == "interest")
    assert Decimal(interest["amount"]) == Decimal("300.00")


def test_an_unknown_credit_is_not_income_and_waits_for_review() -> None:
    bank = account(name="Bank")
    manual(bank["id"], "750", "SAMPLE DEPOSIT 001")
    manual(bank["id"], "-40", "SAMPLE MERCHANT 002")
    sm = summary()
    assert amount(sm["income"]) == Decimal(0) and amount(sm["spend"]) == Decimal(0) and amount(sm["net"]) == Decimal(0)
    assert sm["uncategorized_count"] == 2
    assert sm["uncategorized_in"] == [{"currency": "SAR", "amount": "750.00"}]
    assert sm["uncategorized_out"] == [{"currency": "SAR", "amount": "40.00"}]
    page = register()
    assert {r["status"] for r in page["items"]} == {"uncategorized"} and page["totals"]["uncategorized"] == 2
    assert {r["type_reason"] for r in page["items"]} == {"no_rule_matched"}
    assert register(type="uncategorized")["total"] == 2 and register(type="income")["total"] == 0
    board = dashboard()
    assert money(board["income"]) == Decimal(0) and money(board["expenses"]) == Decimal(0) and board["uncategorized_count"] == 2


def test_an_sms_deposit_is_not_income_until_the_owner_files_it() -> None:
    s = setup()
    assert send(s["token"], "SYNTHETIC. SAR 400.00 deposited to your account ***5678 on 03/11/2026").json()["status"] == "posted"
    row = stored(account_id=s["bank"]["id"])[0]
    assert row.transaction_type == T.UNCATEGORIZED
    assert amount(summary()["income"]) == Decimal(0) and money(dashboard()["income"]) == Decimal(0)
    assert summary()["uncategorized_count"] == 1 and dashboard()["uncategorized_count"] == 1


def test_the_owner_files_a_held_row_and_it_counts() -> None:
    bank = account(name="Bank")
    row = manual(bank["id"], "750", "SAMPLE DEPOSIT 001")
    edited = client.patch(f"{API}/transactions/{row['id']}", headers=OWNER, json={"transaction_type": "income"})
    assert edited.status_code == 200, edited.text
    view = edited.json()
    assert (view["transaction_type"], view["type_source"], view["category"], view["category_source"]) == ("income", "owner", "income", "manual")
    sm = summary()
    assert amount(sm["income"]) == Decimal("750.00") and sm["uncategorized_count"] == 0


def test_a_manual_correction_is_never_overwritten_by_pairing() -> None:
    bank = account(name="Salary account", last4="5678")
    card = account(name="Card", kind="card", last4="5377")
    do_import(bank["id"], "2026-11-03,TRANSFER TO CARD 5377,-1000.00\n")
    out = stored(account_id=bank["id"])[0]
    client.patch(f"{API}/transactions/{out.id}", headers=OWNER, json={"category": "housing"})
    done = do_import(card["id"], "2026-11-05,PAYMENT RECEIVED THANK YOU,1000.00\n")
    assert done["transfers"] == 1  # the pair is still linked
    kept = finance_store.transactions[out.id]
    assert (kept.category, kept.category_source, kept.transaction_type, kept.type_source) == (
        Category.HOUSING, CategorySource.MANUAL, T.EXPENSE, TypeSource.OWNER)
    into = stored(account_id=card["id"])[0]
    # The machine-paired side records the match, never "manual" (audit C4).
    assert into.category == Category.TRANSFER and into.category_source in (CategorySource.RULE, CategorySource.MATCH)
    assert into.category_source != CategorySource.MANUAL and into.type_source != TypeSource.OWNER


def test_a_strong_pair_on_import_is_recorded_as_a_match() -> None:
    bank = account(name="Salary account", last4="5678")
    card = account(name="Card", kind="card", last4="5377")
    do_import(card["id"], "2026-11-05,SAMPLE CREDIT 5678,1000.00\n")
    do_import(bank["id"], "2026-11-03,TRANSFER TO CARD 5377,-1000.00\n")
    out = stored(account_id=bank["id"])[0]
    assert out.category == Category.TRANSFER and out.category_source == CategorySource.MATCH
    assert (out.transaction_type, out.type_source, out.type_reason) == (T.INTERNAL_TRANSFER, TypeSource.MATCH, "paired_own_transfer")
    assert amount(summary()["spend"]) == Decimal(0)


def test_classification_is_idempotent() -> None:
    rules = [CategoryRule(match="sample gym", category=Category.ENTERTAINMENT)]
    for text, value in (("SAMPLE GYM DUBAI", "-90"), ("SOMETHING IN", "20"), ("TRANSFER TO SAMPLE PERSON", "-5"), ("INTEREST", "3")):
        first = classify_text(text, Decimal(value), rules, AccountKind.BANK)
        assert classify_text(text, Decimal(value), rules, AccountKind.BANK) == first
    row = Transaction(id="x", account_id="a", posted_on=date(2026, 11, 3), amount=Decimal("20"), currency="SAR", description="x",
                      category=Category.INCOME, category_source=CategorySource.NONE, dedupe_hash="h", **OWNED)
    once = TT.fill(row.model_copy(), AccountKind.BANK)
    twice = TT.fill(once.model_copy(), AccountKind.BANK)
    assert once == twice and once.transaction_type == T.UNCATEGORIZED
    # Editing only the notes of a typed row changes neither its category nor its type.
    bank = account(name="Bank")
    made = manual(bank["id"], "-45", "CARREFOUR CITY")
    again = client.patch(f"{API}/transactions/{made['id']}", headers=OWNER, json={"notes": "weekly shop"}).json()
    assert {k: again[k] for k in ("category", "transaction_type", "type_source", "type_reason")} == {
        k: made[k] for k in ("category", "transaction_type", "type_source", "type_reason")}


# -- the rules behind them ------------------------------------------------------------------------------------


def test_owner_rules_match_whole_words_and_respect_the_sign() -> None:
    rules = [CategoryRule(match="gym", category=Category.ENTERTAINMENT), CategoryRule(match="sample pay", category=Category.INCOME)]
    assert classify_text("GYMNASTICS SHOP", Decimal("-20"), rules).source != CategorySource.OWNER_RULE  # not inside a word
    assert classify_text("SAMPLE GYM", Decimal("-20"), rules).category == Category.ENTERTAINMENT
    assert classify_text("SAMPLE PAY", Decimal("500"), rules).type == T.INCOME
    # An owner's income rule never files money out.
    assert classify_text("SAMPLE PAY", Decimal("-500"), rules).source != CategorySource.OWNER_RULE


def test_the_register_suggestion_reads_the_account_kind() -> None:
    card = account(name="Card", kind="card")
    row = manual(card["id"], "500", "SAMPLE CREDIT", category="other")
    detail = client.get(f"{API}/register/{row['id']}", headers=OWNER).json()
    assert detail["suggested_category"] == "transfer" and detail["suggested_type"] == "card_payment"


def test_rows_stored_before_types_read_their_type_from_the_category() -> None:
    def old(category: Category, value: str, source: CategorySource = CategorySource.RULE) -> TT.Typed:
        return TT.derive(category, Decimal(value), AccountKind.BANK, source)

    assert old(Category.TRANSFER, "-100").type == T.INTERNAL_TRANSFER
    assert TT.derive(Category.TRANSFER, Decimal("100"), AccountKind.CARD, CategorySource.RULE).type == T.CARD_PAYMENT
    assert old(Category.INCOME, "100").type == T.INCOME
    assert old(Category.INCOME, "100", CategorySource.NONE).type == T.UNCATEGORIZED
    assert old(Category.OTHER, "-100", CategorySource.NONE).type == T.UNCATEGORIZED
    assert old(Category.OTHER, "-100", CategorySource.MANUAL).type == T.EXPENSE
    assert old(Category.GROCERIES, "20").type == T.REFUND and old(Category.FEES, "-5").type == T.BANK_FEE
    assert old(Category.CASHBACK, "5").type == T.CASHBACK and old(Category.REFUNDS, "5").type == T.REFUND
    # Each type maps to one flow; transfers of every kind are neither income nor spend.
    for kind in TT.TRANSFER_TYPES:
        assert flows.flow_for_type(kind, Category.TRANSFER) == flows.Flow.TRANSFER
    for kind in TT.SPEND_TYPES:
        assert flows.flow_for_type(kind, Category.OTHER) == flows.Flow.SPEND
    assert flows.flow_for_type(T.UNCATEGORIZED, Category.OTHER) == flows.Flow.UNCATEGORIZED
