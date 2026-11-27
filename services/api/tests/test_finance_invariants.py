"""Personal Finance invariants (docs/finance.md#how-figures-are-counted, #privacy), in-memory stores.

The safety net every Personal Finance change is checked against: each test states one accounting or
isolation rule end to end through the API, whatever the channel (typed, CSV statement, bank SMS, Apple
Wallet). ``test_postgres_finance_isolation.py`` repeats the isolation rules under row-level security.
Every message, merchant, name and amount here is SYNTHETIC and low-entropy.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from test_finance_sms import (
    API,
    NO_AMOUNT,
    OTHER_PERSON,
    OTHER_WORKSPACE,
    OWNER,
    clean,  # noqa: F401  (the autouse fixture that empties the in-memory stores)
    client,
    dashboard,
    money,
    send,
    wallet,
    wallet_setup,
)

from anum_api import main
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store

MONTH = "2026-11"
TODAY = datetime.now(UTC).date().isoformat()
MARK = "SYNTHPRIVATE"  # in every name the owner types, so a leak is easy to find


@pytest.fixture(autouse=True)
def _links() -> Iterator[None]:
    links_memory_store.clear()
    yield
    links_memory_store.clear()


def call(method: str, path: str, headers: dict[str, str] = OWNER, **kwargs: Any) -> Any:
    main.app.state.rate_limiter.reset()  # the sweeps send more requests than one person may in a minute
    return client.request(method, f"{API}{path}", headers=headers, **kwargs)


def made(path: str, body: dict[str, Any], headers: dict[str, str] = OWNER) -> dict[str, Any]:
    response = call("POST", path, headers, json=body)
    assert response.status_code in (200, 201), (path, response.text)
    return response.json()


def account(kind: str = "bank", currency: str = "SAR", **body: Any) -> dict[str, Any]:
    return made("/accounts", {"name": f"{MARK} {kind}", "kind": kind, "currency": currency, **body})


def typed(account_id: str, value: str, description: str, on: str = "2026-11-10", **extra: Any) -> dict[str, Any]:
    return made("/transactions", {"account_id": account_id, "posted_on": on, "amount": value, "description": description, **extra})


def canonical(transaction_id: str) -> str:
    return next(e["canonical_id"] for e in call("GET", "/ledger/entries").json()["items"] if e["source_ref"] == transaction_id)


def imported(account_id: str, content: str, name: str = "synthetic.csv") -> dict[str, Any]:
    return made("/imports", {"account_id": account_id, "content": content, "filename": name})


def summary(month: str = MONTH) -> dict[str, Any]:
    response = call("GET", "/summary", params={"month": month})
    assert response.status_code == 200, response.text
    return response.json()


def figures() -> dict[str, Any]:
    """What the summary and the ledger dashboard say: the two must agree on every invariant here."""
    s, board = summary(), dashboard()
    assert s["spend"] == board["expenses"] and s["income"] == board["income"], (s["spend"], board["expenses"], s["income"], board["income"])
    return {"spend": money(s["spend"]), "income": money(s["income"]), "debts": money(s["debts"]), "spend_rows": s["spend"], "income_rows": s["income"]}


def owed(currency: str = "SAR") -> Decimal:
    """Debt balances count payments made on or after the day the debt was saved (obligations.debt_balance), so today."""
    return money(summary(TODAY[:7])["debts"], currency)


def _ledger() -> set[str]:
    """Ids of what the owner keeps, from every collection that names one."""
    ids: set[str] = set()
    for path in ("/accounts", "/transactions", "/debts", "/bills", "/goals", "/people-loans", "/tags", "/payslips", "/remittances",
                 "/expected-income", "/links", "/ledger/entries", "/merchants/aliases", "/subcategories", "/rules"):
        body = call("GET", path, params={"month": MONTH}).json()
        rows = body if isinstance(body, list) else body.get("items", [])
        ids |= {r.get("id") or r.get("rule_id") for r in rows if isinstance(r, dict)}
    ids.discard(None)
    return ids


def _populate() -> set[str]:
    bank, card = account(last4="5678"), account("card", last4="1234")
    paid = typed(bank["id"], "-300", f"{MARK} LOAN PAYMENT", category="transfer")
    imported(bank["id"], f"Date,Description,Amount\n2026-11-04,{MARK} BAKERY,-12.00\n")
    debt = made("/debts", {"name": f"{MARK} Loan", "kind": "other", "balance": "1000", "currency": "SAR"})
    made(f"/debts/{debt['id']}/payments", {"canonical_id": canonical(paid["id"])})
    made("/bills", {"name": f"{MARK} Rent", "amount": "50", "currency": "SAR", "cadence": "monthly", "next_due_on": "2026-11-20"})
    made("/goals", {"name": f"{MARK} Goal", "target_amount": "500", "currency": "SAR"})
    made("/people-loans", {"person": f"{MARK} Friend", "direction": "lent", "amount": "300", "currency": "SAR", "lent_on": "2026-11-01"})
    made("/tags", {"name": f"{MARK} Trip", "kind": "trip"})
    made("/payslips", {"employer": f"{MARK} Employer", "month": MONTH, "gross": "1000", "currency": "SAR"})
    made("/remittances", {"sent_on": "2026-11-02", "amount": "100", "currency": "SAR", "recipient": f"{MARK} Family", "provider": "example"})
    made("/expected-income", {"label": f"{MARK} Bonus", "amount": "100", "currency": "SAR", "expected_on": "2026-11-25"})
    made("/merchants/aliases", {"alias": f"{MARK} alias", "merchant": f"{MARK} merchant"})
    made("/subcategories", {"parent": "groceries", "name": f"{MARK} sub"})
    made("/rules", {"content": {"match": {"words": [MARK.lower()]}, "category": "groceries"}})
    ids = _ledger()
    assert card["id"] in ids and debt["id"] in ids and len(ids) >= 15
    return ids


# Investing and trading routes keep no personal ledger rows; their own suites check their isolation.
NOT_LEDGER = ("/intel", "/broker", "/ibkr", "/research", "/trading", "/paper", "/alpaca", "/execution")


def _finance_routes() -> dict[str, set[str]]:
    """Personal finance path (without the prefix) to its methods."""
    return {p.removeprefix(API): set(ops) for p, ops in main.app.openapi()["paths"].items()
            if p.startswith(API) and not p.removeprefix(API).startswith(NOT_LEDGER)}


# -- isolation -------------------------------------------------------------------------------------------------


def test_no_read_shows_the_owners_ledger_to_a_member_or_another_workspace() -> None:
    ids = _populate()
    leaks = []
    for local, methods in sorted(_finance_routes().items()):
        if "get" not in methods:
            continue
        urls = [local] if "{" not in local else sorted({re.sub(r"{\w+}", i, local) for i in ids})
        for url in urls:
            for who, headers in (("member", OTHER_PERSON), ("other workspace", OTHER_WORKSPACE)):
                body = call("GET", url, headers, params={"month": MONTH}).text
                if MARK in body or MARK.lower() in body or any(i in body for i in ids):
                    leaks.append((who, url))
    assert leaks == [], leaks


def test_no_change_by_a_member_or_another_workspace_reaches_the_owners_ledger() -> None:
    ids = _populate()
    before = {p: call("GET", p, params={"month": MONTH}).json() for p in ("/accounts", "/transactions", "/debts", "/links", "/ledger/entries",
                                                                          "/people-loans", "/rules", "/summary")}
    allowed = []
    for local, methods in sorted(_finance_routes().items()):
        if "{" not in local:
            continue
        for method in sorted(m.upper() for m in methods - {"get"}):
            for i in ids:
                for headers in (OTHER_PERSON, OTHER_WORKSPACE):
                    response = call(method, re.sub(r"{\w+}", i, local), headers, json={"version": 1, "payload_hash": "0" * 64})
                    if response.status_code < 300 and i in response.text:
                        allowed.append((method, local, i))
    assert allowed == [], allowed
    after = {p: call("GET", p, params={"month": MONTH}).json() for p in before}
    assert after == before


def test_a_member_cannot_link_the_owners_transactions_to_their_own_records() -> None:
    bank = account()
    canonical_id = canonical(typed(bank["id"], "-500", f"{MARK} PAYMENT", category="transfer")["id"])
    debt = made("/debts", {"name": "Mine", "kind": "other", "balance": "1000", "currency": "SAR"}, OTHER_PERSON)
    loan = made("/people-loans", {"person": "Someone", "direction": "lent", "amount": "500", "currency": "SAR", "lent_on": "2026-11-01"}, OTHER_PERSON)
    tag = made("/tags", {"name": "Mine", "kind": "trip"}, OTHER_PERSON)
    for path, body in ((f"/debts/{debt['id']}/payments", {"canonical_id": canonical_id}),
                       (f"/people-loans/{loan['id']}/links", {"canonical_id": canonical_id, "kind": "handed"}),
                       (f"/tags/{tag['id']}/links", {"canonical_id": canonical_id})):
        assert call("POST", path, OTHER_PERSON, json=body).status_code == 404, path
    assert call("GET", "/links", OTHER_PERSON).json()["total"] == 0
    assert call("GET", "/links").json()["total"] == 0


# -- one economic event is counted once -------------------------------------------------------------------------


def test_rerunning_the_same_statement_never_adds_a_row_or_changes_a_figure() -> None:
    bank = account()
    first = imported(bank["id"], "Date,Description,Amount\n2026-11-02,SALARY NOVEMBER,5000.00\n"
                                 "2026-11-03,TALABAT ORDER,-35.00\n2026-11-03,TALABAT ORDER,-35.00\n")
    assert (first["imported_count"], first["duplicate_count"]) == (3, 0)
    once = figures()
    again = imported(bank["id"], "Date,Description,Amount\n2026-11-02,SALARY NOVEMBER,5000.00\n"
                                 "2026-11-03,TALABAT ORDER,-35.00\n2026-11-03,TALABAT ORDER,-35.00\n", "renamed.csv")
    assert (again["imported_count"], again["duplicate_count"]) == (0, 3)
    # A later statement that overlaps: the one identical row it repeats is the same row, the new one is new.
    overlap = imported(bank["id"], "Date,Description,Amount\n2026-11-03,TALABAT ORDER,-35.00\n2026-11-04,SPINNEYS,-10.00\n")
    assert (overlap["imported_count"], overlap["duplicate_count"]) == (1, 1)
    assert call("GET", "/transactions").json()["total"] == 4
    assert figures()["spend"] == once["spend"] + Decimal("10.00") and figures()["income"] == once["income"] == Decimal("5000.00")


def test_one_purchase_from_sms_wallet_and_the_statement_is_spent_once() -> None:
    s = wallet_setup()
    assert send(s["token"], "SYNTHETIC. Purchase of SAR 45.50 at CARREFOUR RIYADH with card **1234").json()["status"] == "posted"
    assert wallet(s["token"], amount="SAR 45.50", merchant="Carrefour Riyadh").json()["status"] == "attached"
    today = sms_memory_store.entries[next(iter(sms_memory_store.entries))].posted_on.isoformat()
    statement = f"Date,Description,Amount\n{today},CARREFOUR RIYADH,-45.50\n"
    assert imported(s["card"]["id"], statement)["imported_count"] == 0
    assert imported(s["card"]["id"], statement)["imported_count"] == 0
    assert len(sms_memory_store.entries) == 1
    assert figures()["spend"] == Decimal("45.50")


def test_a_card_repayment_and_an_own_transfer_from_both_statements_are_neither_spend_nor_income() -> None:
    bank, card, savings = account(last4="5678"), account("card", last4="1234"), account(last4="9999")
    imported(bank["id"], "Date,Description,Amount\n2026-11-02,SALARY NOVEMBER,5000.00\n2026-11-06,TRANSFER TO 9999,-300.00\n"
                         "2026-11-10,CREDIT CARD PAYMENT 1234,-200.00\n")
    imported(card["id"], "Date,Description,Amount\n2026-11-04,CARREFOUR CITY,-200.00\n2026-11-10,PAYMENT RECEIVED THANK YOU,200.00\n")
    imported(savings["id"], "Date,Description,Amount\n2026-11-06,TRANSFER FROM 5678,300.00\n")
    # The purchase is the only spending; the repayment is not a second expense; the transfer is neither.
    assert (figures()["spend"], figures()["income"]) == (Decimal("200.00"), Decimal("5000.00"))


def test_debt_payments_count_once_in_one_currency_and_principal_never_goes_negative() -> None:
    bank = account()
    first = made("/debts", {"name": "Loan one", "kind": "other", "balance": "1000", "currency": "SAR"})
    second = made("/debts", {"name": "Loan two", "kind": "other", "balance": "1000", "currency": "SAR"})
    dollars = made("/debts", {"name": "Loan usd", "kind": "other", "balance": "1000", "currency": "USD"})
    payment = canonical(typed(bank["id"], "-400", "SAMPLE LOAN PAYMENT", on=TODAY, category="transfer")["id"])
    link = made(f"/debts/{first['id']}/payments", {"canonical_id": payment})
    # The same payment matched again is the same link, never a second one.
    assert made(f"/debts/{first['id']}/payments", {"canonical_id": payment})["link"]["id"] == link["link"]["id"]
    assert call("POST", f"/debts/{second['id']}/payments", json={"canonical_id": payment}).json()["code"] == "over_allocated"
    assert call("POST", f"/debts/{dollars['id']}/payments", json={"canonical_id": payment}).json()["code"] == "currency_mismatch"
    assert owed() == Decimal("1600.00")  # 600 + 1000 SAR; the USD debt is never added to it
    assert owed("USD") == Decimal("1000.00")
    money_in = canonical(typed(bank["id"], "400", "SAMPLE DEPOSIT", on=TODAY, category="income")["id"])
    assert call("POST", f"/debts/{second['id']}/payments", json={"canonical_id": money_in}).json()["code"] == "not_a_payment"
    big = canonical(typed(bank["id"], "-900", "SAMPLE LOAN PAYMENT TWO", on=TODAY, category="transfer")["id"])
    assert call("POST", f"/debts/{first['id']}/payments", json={"canonical_id": big, "amount": "901"}).json()["code"] == "over_allocated"
    # Never more than the debt owes: 900 asked against 600 left is refused; without an amount the match takes the 600.
    assert call("POST", f"/debts/{first['id']}/payments", json={"canonical_id": big, "amount": "900"}).json()["code"] == "over_debt"
    assert made(f"/debts/{first['id']}/payments", {"canonical_id": big})["link"]["amount"] == "600.00"
    assert owed() == Decimal("1000.00")  # the first debt is paid off and shows zero, not -300
    assert all(Decimal(d["amount"]) >= 0 for d in summary(TODAY[:7])["debts"])
    # Undoing the payment row voids its link and the debt owes again.
    rows = {t["description"]: t["id"] for t in call("GET", "/transactions").json()["items"]}
    assert call("DELETE", f"/transactions/{rows['SAMPLE LOAN PAYMENT']}").status_code == 204
    assert owed() == Decimal("1400.00")  # the first debt now carries only the 600 matched: 400 left, plus the second 1000
    after = summary(TODAY[:7])
    assert (money(after["spend"]), money(after["income"])) == (0, Decimal("400.00"))


def test_borrowed_principal_is_not_income_and_lending_is_not_spend() -> None:
    bank = account()
    borrowed = made("/people-loans", {"person": "Friend A", "direction": "borrowed", "amount": "800", "currency": "SAR", "lent_on": "2026-11-01"})
    lent = made("/people-loans", {"person": "Friend B", "direction": "lent", "amount": "300", "currency": "SAR", "lent_on": "2026-11-01"})
    received = typed(bank["id"], "800", "SAMPLE DEPOSIT FRIEND A", category="income")
    handed = typed(bank["id"], "-300", "SAMPLE TRANSFER FRIEND B", category="shopping")
    repaid = typed(bank["id"], "300", "SAMPLE TRANSFER FROM FRIEND B", category="income")
    made(f"/people-loans/{borrowed['id']}/links", {"canonical_id": canonical(received["id"]), "kind": "handed"})
    made(f"/people-loans/{lent['id']}/links", {"canonical_id": canonical(handed["id"]), "kind": "handed"})
    made(f"/people-loans/{lent['id']}/links", {"canonical_id": canonical(repaid["id"])})
    assert (figures()["spend"], figures()["income"]) == (0, 0)
    loans = {r["person"]: r for r in call("GET", "/people-loans").json()["items"]}
    assert (loans["Friend B"]["outstanding"], loans["Friend B"]["status"]) == ("0.00", "repaid")


def test_figures_stay_per_currency_and_nothing_is_converted() -> None:
    riyal, dollar = account(currency="SAR"), account(currency="USD")
    typed(riyal["id"], "-100", "CARREFOUR CITY", category="groceries")
    typed(dollar["id"], "-10", "CARREFOUR CITY", category="groceries")
    typed(dollar["id"], "50", "SALARY NOVEMBER", category="income")
    out = figures()
    assert out["spend_rows"] == [{"currency": "SAR", "amount": "100.00"}, {"currency": "USD", "amount": "10.00"}]
    assert out["income_rows"] == [{"currency": "USD", "amount": "50.00"}]
    assert {r["currency"] for r in summary()["net_worth"]} == {"SAR", "USD"}
    # A row cannot be given a currency other than its account's.
    assert call("POST", "/transactions", json={"account_id": dollar["id"], "posted_on": "2026-11-10", "amount": "-1", "description": "x",
                                               "currency": "SAR"}).status_code == 422


def test_the_owners_category_survives_a_reimport_and_a_rule_applied_to_earlier_rows() -> None:
    bank = account()
    imported(bank["id"], "Date,Description,Amount\n2026-11-03,ZEBRAMART OUTLET,-60.00\n2026-11-04,ZEBRAMART OUTLET,-40.00\n")
    rows = call("GET", "/transactions").json()["items"]
    chosen = next(r for r in rows if r["amount"] == "-60.00")
    assert call("PATCH", f"/transactions/{chosen['id']}", json={"category": "health"}).status_code == 200
    imported(bank["id"], "Date,Description,Amount\n2026-11-03,ZEBRAMART OUTLET,-60.00\n2026-11-04,ZEBRAMART OUTLET,-40.00\n", "again.csv")
    rule = made("/rules", {"content": {"match": {"words": ["zebramart"]}, "category": "shopping"}})
    # The rule only runs once approved with the hash the owner was shown.
    assert call("POST", f"/rules/{rule['rule_id']}/approve", json={"version": rule["version"]}).status_code == 409
    assert call("POST", f"/rules/{rule['rule_id']}/approve", json={"version": rule["version"], "payload_hash": "0" * 64}).status_code == 409
    assert call("POST", f"/rules/{rule['rule_id']}/approve", json={"version": rule["version"], "payload_hash": rule["payload_hash"]}).status_code == 200
    preview = call("POST", f"/rules/{rule['rule_id']}/preview").json()
    assert call("POST", f"/rules/{rule['rule_id']}/apply", json={"preview_hash": preview["preview_hash"]}).status_code == 201
    after = {r["amount"]: r["category"] for r in call("GET", "/transactions").json()["items"]}
    assert after == {"-60.00": "health", "-40.00": "shopping"}


# -- privacy of bank data -------------------------------------------------------------------------------------


def test_logs_carry_no_device_token_message_text_or_bank_details(caplog: pytest.LogCaptureFixture) -> None:
    s = wallet_setup()
    caplog.set_level(logging.DEBUG)
    send(s["token"], f"SYNTHETIC. Purchase of SAR 45.50 at {MARK} SHOP with card **1234")
    send(s["token"], NO_AMOUNT)
    send("anum_sms_example", f"SYNTHETIC. Purchase of SAR 45.50 at {MARK} SHOP with card **1234")
    wallet(s["token"], amount="SAR 99.00", merchant=f"{MARK} WALLET")
    imported(s["bank"]["id"], f"Date,Description,Amount\n2026-11-04,{MARK} CSV,-12.00\n")
    # The message and every extra field; the record's own timing floats are left out, since a timestamp
    # such as 1764265299.0012 contains "99.00" by chance.
    standard = set(logging.LogRecord("x", logging.INFO, "f", 1, "m", None, None).__dict__)
    texts = [f"{r.getMessage()} {({k: v for k, v in r.__dict__.items() if k not in standard})}" for r in caplog.records]
    for needle in (s["token"], MARK, "45.50", "99.00", "**1234", "anum_sms_example"):
        assert not any(needle in t for t in texts), needle


def test_lending_part_of_a_purchase_keeps_the_rest_as_spending() -> None:
    bank = account()
    loan = made("/people-loans", {"person": "Friend", "direction": "lent", "amount": "300", "currency": "SAR", "lent_on": "2026-11-10"})
    paid = typed(bank["id"], "-500", "JARIR BOOKSTORE", category="shopping")
    made(f"/people-loans/{loan['id']}/links", {"canonical_id": canonical(paid["id"]), "kind": "handed", "amount": "300"})
    assert figures()["spend"] == Decimal("200.00")


def test_a_repayment_cannot_be_linked_beyond_what_the_loan_expects() -> None:
    bank = account()
    loan = made("/people-loans", {"person": "Friend", "direction": "lent", "amount": "300", "currency": "SAR", "lent_on": "2026-11-01"})
    back = canonical(typed(bank["id"], "500", "FROM FRIEND", category="income")["id"])
    response = call("POST", f"/people-loans/{loan['id']}/links", json={"canonical_id": back, "kind": "repayment", "amount": "500"})
    assert response.status_code == 409 and response.json()["code"] == "over_loan", response.text
    # Linking what the loan expects leaves the excess counted as income, never hidden.
    made(f"/people-loans/{loan['id']}/links", {"canonical_id": back, "kind": "repayment", "amount": "300"})
    assert figures()["income"] == Decimal("200.00")


def test_a_debt_payment_cannot_pay_more_than_the_debt_owes() -> None:
    bank = account()
    debt = made("/debts", {"name": "Loan", "kind": "other", "balance": "1000", "currency": "SAR"})
    paid = canonical(typed(bank["id"], "-1500", "SAMPLE LOAN PAYMENT", on=TODAY, category="transfer")["id"])
    response = call("POST", f"/debts/{debt['id']}/payments", json={"canonical_id": paid, "amount": "1500"})
    assert response.status_code in (409, 422), response.text
    assert call("POST", f"/debts/{debt['id']}/payments", json={"canonical_id": paid, "amount": "1000"}).status_code == 201
