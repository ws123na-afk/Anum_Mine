"""The statement matching engine (docs/finance.md#statement-matching), in-memory stores.

Every statement row, message and merchant here is SYNTHETIC: plainly fake ("EXAMPLE MERCHANT"), written for these
tests, and not the wording of any real bank. The engine is checked twice: as pure functions (``statement_match``,
``fx_text``) and end to end through the CSV and PDF import routes, the Bills schedule, the summary and the accounts
overview.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import fx_text
from anum_api.finance import sms_service as svc
from anum_api.finance import statement_match as M
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store

TENANT, WORKSPACE = "tenant_stmt", "workspace_stmt"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_stmt", "x-user-roles": "owner"}
OTHER_PERSON = {**OWNER, "x-user-id": "member_stmt", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_stmt", "x-user-roles": "viewer"}
API = "/api/v1/finance"
SENDER = "TestBank"  # SYNTHETIC sender

PURCHASE = "SYNTHETIC. Purchase of SAR 45.50 at EXAMPLE MARKET with card **1234 on 03/11/2026 14:22. Ref 88AB12"
FX_PURCHASE = "SYNTHETIC. Purchase USD 20.00 (SAR 75.40) at EXAMPLE STREAMING card **1234 on 08/11/2026"

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()
    svc._last_notified.clear()
    yield
    finance_store.clear()
    sms_memory_store.clear()
    links_memory_store.clear()


# -- helpers ---------------------------------------------------------------------------------------------


def account(headers: dict = OWNER, **body: object) -> dict:
    response = client.post(f"{API}/accounts", headers=headers, json={"name": "Current", "kind": "bank", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def sms_setup() -> dict[str, Any]:
    bank = account(name="Salary account", last4="5678")
    card = account(name="Card", kind="card", last4="1234")
    assert client.post(f"{API}/sms/self-test", headers=OWNER).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=OWNER).json()["version"]
    put = client.put(f"{API}/sms/settings", headers=OWNER, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "1234", "account_id": card["id"], "credit_limit": "5000"}]})
    assert put.status_code == 200, put.text
    device = client.post(f"{API}/sms/devices", headers=OWNER, json={"name": "My iPhone"})
    return {"bank": bank, "card": card, "token": device.json()["token"]}


def send_sms(token: str, message: str) -> dict:
    response = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": SENDER})
    assert response.status_code == 200, response.text
    return response.json()


def preview(account_id: str, content: str, headers: dict = OWNER, **extra: Any) -> dict:
    response = client.post(f"{API}/imports/preview", headers=headers, json={"account_id": account_id, "content": content, "filename": "nov.csv", **extra})
    assert response.status_code == 200, response.text
    return response.json()


def do_import(account_id: str, content: str, headers: dict = OWNER, **extra: Any) -> dict:
    response = client.post(f"{API}/imports", headers=headers, json={"account_id": account_id, "content": content, "filename": "nov.csv", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def transactions(account_id: str | None = None) -> list[dict]:
    params = {"account_id": account_id} if account_id else {}
    return client.get(f"{API}/transactions", headers=OWNER, params={**params, "limit": 200}).json()["items"]


def statuses(view: dict) -> list[str]:
    return [row["match"]["status"] for row in view["rows"]]


def record(record_id: str, amount: str, day: date, text: str, *, primary: str = "sms", account_id: str = "acc_1", last4: str | None = None,
           original: tuple[str, str] | None = None, currency: str = "SAR", reference: str | None = None) -> M.Recorded:
    return M.Recorded(
        record_id=record_id, entry_id=record_id, canonical_id=record_id, transaction_id=f"txn_{record_id}", account_id=account_id, posted_on=day,
        amount=Decimal(amount), currency=currency, description=text, category=None, sources=frozenset({primary}), primary=primary,
        reference=reference, last4=last4, original_amount=Decimal(original[0]) if original else None, original_currency=original[1] if original else None,
        plain=primary in ("manual", "statement"),
    )


def line(key: int, amount: str, day: date, text: str, digest: str | None = None, **extra: Any) -> M.Line:
    return M.Line(key=key, posted_on=day, amount=Decimal(amount), description=text, dedupe_hash=digest or f"hash_{key}", **extra)


NOV = date(2026, 11, 3)
ACCOUNT = M.AccountInfo("acc_1", "Card", "card", "SAR", "1234")


# -- the engine, pure ------------------------------------------------------------------------------------


def test_exact_duplicate_by_hash_and_by_the_same_statement_row() -> None:
    imported = M.Recorded(record_id="r1", entry_id="e1", canonical_id="e1", transaction_id="t1", account_id="acc_1", posted_on=NOV,
                          amount=Decimal("-10.00"), currency="SAR", description="EXAMPLE MERCHANT", category="shopping",
                          sources=frozenset({"statement"}), primary="statement", statement_hashes=frozenset({"h1"}), reconciled=True, plain=True)
    out = M.match_lines([line(1, "-10.00", NOV, "EXAMPLE MERCHANT", "h1"), line(2, "-10.00", NOV, "POS EXAMPLE MERCHANT 03/11")], [imported])
    assert [m.status for m in out] == ["exact_duplicate", "new"]  # one record absorbs one row
    assert out[0].code == "imported_before" and out[0].record is imported
    # Another export of the same statement (different words, same day, amount and merchant) is the same row.
    out = M.match_lines([line(1, "-10.00", NOV, "POS VIS 4567 EXAMPLE MERCHANT RIYADH")], [imported])
    assert (out[0].status, out[0].code) == ("exact_duplicate", "same_statement_row")


def test_sms_and_apple_pay_records_are_matched_with_a_reason() -> None:
    sms = record("sms1", "-45.50", NOV, "EXAMPLE MARKET", last4="1234")
    wallet = record("w1", "-12.00", NOV, "EXAMPLE CAFE", primary="wallet")
    out = M.match_lines([line(1, "-45.50", date(2026, 11, 4), "POS EXAMPLE MARKET RIYADH"), line(2, "-12.00", NOV, "EXAMPLE CAFE 03/11")], [sms, wallet])
    assert [(m.status, m.code, m.record.record_id if m.record else None) for m in out] == [
        ("matched_existing", "same_amount_merchant", "sms1"), ("matched_existing", "same_amount_merchant", "w1")]
    assert out[0].lag_days == 1 and "SMS from card ending 1234" in M.explain(out[0]) and "1 day later" in M.explain(out[0])
    assert "Apple Pay" in M.explain(out[1])


def test_two_identical_purchases_on_one_day_stay_two_rows() -> None:
    one, two = record("a", "-25.00", NOV, "EXAMPLE KIOSK"), record("b", "-25.00", NOV, "EXAMPLE KIOSK")
    lines = [line(1, "-25.00", NOV, "EXAMPLE KIOSK"), line(2, "-25.00", NOV, "EXAMPLE KIOSK")]
    out = M.match_lines(lines, [one, two])
    assert [m.status for m in out] == ["matched_existing", "matched_existing"]
    assert {m.record.record_id for m in out if m.record} == {"a", "b"}
    out = M.match_lines(lines, [one])
    assert [m.status for m in out] == ["matched_existing", "new"]


def test_posting_lag_is_zero_to_three_days() -> None:
    sms = record("s", "-30.00", NOV, "EXAMPLE PHARMACY")
    assert M.match_lines([line(1, "-30.00", date(2026, 11, 6), "EXAMPLE PHARMACY")], [sms])[0].status == "matched_existing"
    assert M.match_lines([line(1, "-30.00", date(2026, 11, 7), "EXAMPLE PHARMACY")], [sms])[0].status == "new"
    assert M.match_lines([line(1, "-30.00", date(2026, 11, 2), "EXAMPLE PHARMACY")], [sms])[0].status == "new"  # before the purchase


def test_weak_and_ambiguous_matches_need_a_decision() -> None:
    sms = record("s", "-45.50", NOV, "EXAMPLE MARKET")
    out = M.match_lines([line(1, "-45.50", NOV, "CARD TXN 9912")], [sms])
    assert (out[0].status, out[0].code) == ("possible_duplicate", "amount_date_only") and not out[0].default_import
    # A different merchant on the same day and amount is a second purchase: never attached, never held (I1).
    assert M.match_lines([line(1, "-45.50", NOV, "OTHER SHOP")], [sms])[0].status == "new"
    # An agreeing bank reference is enough on its own; a conflicting one rules the record out.
    ref = record("r", "-45.50", NOV, "EXAMPLE MARKET", reference="88AB12")
    assert M.match_lines([line(1, "-45.50", NOV, "CARD TXN 9912", reference="88AB12")], [ref])[0].code == "same_reference"
    assert M.match_lines([line(1, "-45.50", NOV, "EXAMPLE MARKET", reference="99ZZ00")], [ref])[0].status == "new"
    # Two different records fit: the person decides.
    early, late = record("e", "-45.50", date(2026, 11, 1), "EXAMPLE MARKET"), record("l", "-45.50", NOV, "EXAMPLE MARKET")
    ambiguous = M.match_lines([line(1, "-45.50", NOV, "EXAMPLE MARKET")], [early, late])[0]
    assert (ambiguous.status, ambiguous.code, len(ambiguous.candidates)) == ("possible_duplicate", "two_candidates", 2)


def test_same_foreign_amount_billed_differently_is_matched_with_the_difference() -> None:
    sms = record("fx", "-75.40", NOV, "EXAMPLE STREAMING", original=("20.00", "USD"))
    out = M.match_lines([line(1, "-76.20", date(2026, 11, 5), "EXAMPLE STREAMING USD 20.00 @ 3.81",
                              original_amount=Decimal("20.00"), original_currency="USD")], [sms])
    assert (out[0].status, out[0].code, out[0].amount_difference) == ("matched_existing", "same_original_amount", Decimal("-0.80"))


def test_bill_payment_strong_and_weak() -> None:
    bill = M.BillDue("bill_1", "FAKETEL", Decimal("290.00"), "SAR", date(2026, 11, 15), None)
    strong = M.bill_hit(Decimal("-290.00"), date(2026, 11, 14), "FAKETEL BILL PAYMENT", "SAR", "acc_1", bill)
    assert strong is not None and strong.strength == "strong" and strong.reasons == ("payee", "amount", "due_window")
    within = M.bill_hit(Decimal("-305.00"), date(2026, 11, 20), "FAKETEL", "SAR", "acc_1", bill)
    assert within is not None and within.strength == "strong"  # within 10 % and 5 days late
    weak = M.bill_hit(Decimal("-450.00"), date(2026, 11, 15), "FAKETEL", "SAR", "acc_1", bill)
    assert weak is not None and weak.strength == "weak"
    assert M.bill_hit(Decimal("-290.00"), date(2026, 11, 15), "FAKEGROCER", "SAR", "acc_1", bill) is None  # no payee: never a bill
    assert M.bill_hit(Decimal("290.00"), date(2026, 11, 15), "FAKETEL REFUND", "SAR", "acc_1", bill) is None  # money in
    # One due date absorbs one row.
    out = M.match_lines([line(1, "-290.00", date(2026, 11, 14), "FAKETEL"), line(2, "-290.00", date(2026, 11, 15), "FAKETEL")], [],
                        bills=[bill], account=ACCOUNT)
    assert [m.bill is not None for m in out] == [True, False]


def test_fx_details_are_read_from_the_row_text_in_english_and_arabic() -> None:
    read = fx_text.read_fx("POS EXAMPLE STORE USD 20.00 @ 3.84 markup 2.5%", "SAR")
    assert (read.original_amount, read.original_currency, read.rate, read.rate_source, read.markup_percent) == (
        Decimal("20.00"), "USD", Decimal("3.84"), "printed", Decimal("2.5"))
    read = fx_text.read_fx("EXAMPLE SHOP 20.00 USD FX rate 3.8400 intl txn fee SAR 1.20", "SAR")
    assert (read.rate, read.fee, read.fee_currency) == (Decimal("3.8400"), Decimal("1.20"), "SAR")
    read = fx_text.read_fx("شراء 20.00 دولار سعر الصرف ٣٫٧٥ رسوم 1.20 ر.س", "SAR")
    assert (read.original_amount, read.original_currency, read.rate, read.fee) == (Decimal("20.00"), "USD", Decimal("3.75"), Decimal("1.20"))
    read = fx_text.read_fx("EXAMPLE PAY EUR 10.00 (SAR 41.50)", "SAR")
    assert (read.rate, read.rate_source, read.billed_amount) == (Decimal("4.150000"), "implied", Decimal("41.50"))
    # Nothing foreign: nothing read, and a fee-only row is its own expense, not an FX detail.
    assert fx_text.read_fx("EXAMPLE MERCHANT 45.50", "SAR").empty() and fx_text.read_fx("SERVICE CHARGE SAR 15.00", "SAR").empty()
    check = fx_text.check(Decimal("-78.72"), Decimal("20.00"), Decimal("3.84"), "SAR", markup_percent=Decimal("2.5"))
    assert check is not None and (check.status, check.includes, check.difference) == ("ties", "rate_markup", Decimal("0.00"))
    check = fx_text.check(Decimal("80.00"), Decimal("20.00"), Decimal("3.84"), "SAR")
    assert check is not None and (check.status, check.difference) == ("differs", Decimal("3.20"))
    assert fx_text.check(Decimal("80.00"), Decimal("20.00"), None, "SAR") is None  # no rate is ever invented


def test_kwd_amounts_keep_three_decimals_and_the_fx_check_uses_its_minor_unit() -> None:
    check = fx_text.check(Decimal("6.154"), Decimal("20.00"), Decimal("0.3075"), "KWD")
    assert check is not None and check.expected == Decimal("6.150") and check.status == "differs"  # 4 fils over: more than two minor units
    assert fx_text.check(Decimal("6.151"), Decimal("20.00"), Decimal("0.3075"), "KWD").status == "ties"  # type: ignore[union-attr]
    kwd = account(name="Kuwait account", currency="KWD")
    view = preview(kwd["id"], "Date,Description,Amount,Balance\n2026-11-01,EXAMPLE SALARY,1250.500,1350.500\n2026-11-02,EXAMPLE GROCER,-12.125,1338.375\n")
    assert [row["amount"] for row in view["rows"]] == ["1250.500", "-12.125"]
    assert view["reconciliation"]["status"] == "tied" and view["reconciliation"]["closing"] == "1338.375"


def test_transfer_pairs_are_strong_with_a_transfer_word_or_the_account_named_and_never_on_one_account() -> None:
    bank = M.AccountInfo("bank", "Salary account", "bank", "SAR", "5678")
    card = M.AccountInfo("card", "Card", "card", "SAR", "5377")
    out_side = record("o", "-1000.00", NOV, "TRANSFER TO CARD 5377", account_id="bank", primary="statement")
    in_side = record("i", "1000.00", date(2026, 11, 5), "PAYMENT RECEIVED THANK YOU", account_id="card", primary="statement")
    pairs = M.find_transfers([out_side, in_side], [bank, card])
    assert len(pairs) == 1 and pairs[0].strength == "strong" and set(pairs[0].reasons) >= {"same_amount", "transfer_words", "account_named"}
    # Same amount, no transfer word and no account named: weak.
    quiet_out = record("o2", "-700.00", NOV, "EXAMPLE PAYEE", account_id="bank", primary="statement")
    quiet_in = record("i2", "700.00", NOV, "EXAMPLE CREDIT", account_id="card", primary="statement")
    assert [p.strength for p in M.find_transfers([quiet_out, quiet_in], [bank, card])] == ["weak"]
    # Six days apart or on the same account: no pair. One to one: two ins for one out pair once.
    late = record("i3", "1000.00", date(2026, 11, 9), "TRANSFER", account_id="card", primary="statement")
    assert M.find_transfers([out_side, late], [bank, card]) == []
    same = record("i4", "1000.00", NOV, "TRANSFER", account_id="bank", primary="statement")
    assert M.find_transfers([out_side, same], [bank, card]) == []
    twin = record("i5", "1000.00", date(2026, 11, 4), "TRANSFER RECEIVED", account_id="card", primary="statement")
    pairs = M.find_transfers([out_side, in_side, twin], [bank, card])
    assert len(pairs) == 1 and pairs[0].into.record_id == "i5"  # the nearer day
    # Different currencies pair only when one side states the other's amount.
    usd = M.AccountInfo("usd", "Dollar account", "bank", "USD", "4444")
    fx_out = record("fo", "-375.00", NOV, "TRANSFER TO DOLLAR ACCOUNT", account_id="bank", primary="statement", original=("100.00", "USD"))
    fx_in = record("fi", "100.00", NOV, "TRANSFER IN", account_id="usd", currency="USD", primary="statement")
    assert [p.reasons[0] for p in M.find_transfers([fx_out, fx_in], [bank, usd])] == ["fx_amount"]


def test_descriptors_are_normalized_before_words_are_compared() -> None:
    assert M.merchant_words("POS VIS 4567 EXAMPLE MERCHANT RIYADH SA 03/11 14:22") == {"example", "merchant"}
    assert M.merchants_agree("EXAMPLE MERCHANT", "CARD TXN 9912 REF 88AB12") is None


# -- end to end ------------------------------------------------------------------------------------------


def test_preview_groups_rows_and_the_import_attaches_instead_of_doubling() -> None:
    s = sms_setup()
    assert send_sms(s["token"], PURCHASE)["status"] == "posted"
    content = "Date,Description,Amount\n2026-11-04,POS EXAMPLE MARKET RIYADH,-45.50\n2026-11-04,EXAMPLE BAKERY,-9.00\n"
    view = preview(s["card"]["id"], content)
    assert statuses(view) == ["matched_existing", "new"]
    assert view["counts"] == {"new": 1, "exact_duplicate": 0, "matched_existing": 1, "possible_duplicate": 0, "bills": 0, "transfers": 0}
    matched = view["rows"][0]["match"]
    assert matched["record"]["source"] == "sms" and matched["record"]["last4"] == "1234" and matched["lag_days"] == 1
    done = do_import(s["card"]["id"], content)
    assert (done["imported_count"], done["reconciled"]) == (1, 1)
    assert len(transactions(s["card"]["id"])) == 2
    # The same statement again: both rows are already recorded, including the one that only confirmed the SMS.
    again = preview(s["card"]["id"], content)
    assert statuses(again) == ["exact_duplicate", "exact_duplicate"]
    assert [r["match"]["code"] for r in again["rows"]] == ["reconciled_before", "imported_before"]
    repeat = do_import(s["card"]["id"], content)
    assert (repeat["imported_count"], repeat["reconciled"], repeat["duplicate_count"]) == (0, 0, 2)
    assert len(transactions(s["card"]["id"])) == 2


def test_a_possible_duplicate_waits_unless_ticked_and_a_new_row_can_be_left_out() -> None:
    s = sms_setup()
    send_sms(s["token"], PURCHASE.replace(" Ref 88AB12", ""))
    content = "Date,Description,Amount\n2026-11-03,CARD TXN 9912,-45.50\n2026-11-03,EXAMPLE BAKERY,-9.00\n"
    assert statuses(preview(s["card"]["id"], content)) == ["possible_duplicate", "new"]
    held = do_import(s["card"]["id"], content, decisions=[{"line": 3, "action": "skip"}])
    assert (held["held_for_review"], held["imported_count"], held["skipped"]) == (1, 0, 1)
    # Re-importing: the held row is already waiting in Review; the skipped one is still new.
    view = preview(s["card"]["id"], content)
    assert [r["match"]["code"] for r in view["rows"]] == ["waiting_review", "no_match"]
    sms_memory_store.clear()
    finance_store.clear()
    s = sms_setup()
    send_sms(s["token"], PURCHASE.replace(" Ref 88AB12", ""))
    ticked = do_import(s["card"]["id"], content, decisions=[{"line": 2, "action": "import"}])
    assert (ticked["imported_count"], ticked["held_for_review"]) == (2, 0)


def test_an_fx_purchase_billed_differently_is_corrected_through_the_audited_path() -> None:
    s = sms_setup()
    assert send_sms(s["token"], FX_PURCHASE)["status"] == "posted"
    content = "Date,Description,Amount\n2026-11-09,EXAMPLE STREAMING USD 20.00 @ 3.81,-76.20\n"
    view = preview(s["card"]["id"], content)
    row = view["rows"][0]
    assert row["match"]["status"] == "matched_existing" and row["match"]["code"] == "same_original_amount"
    assert row["match"]["amount_difference"] == "-0.80"
    assert row["fx"]["original_amount"] == "20.00" and row["fx"]["rate"] == "3.81" and row["fx"]["rate_source"] == "printed"
    assert row["fx"]["expected"] == "76.20" and row["fx"]["check"] == "ties"
    done = do_import(s["card"]["id"], content)
    assert (done["reconciled"], done["corrected"], done["imported_count"]) == (1, 1, 0)
    items = transactions(s["card"]["id"])
    assert [i["amount"] for i in items] == ["-76.20"]
    live = [e for e in sms_memory_store.entries.values() if e.status.value == "posted" and e.kind != "reversal" and e.reverses_id is None
            and e.source.value != "reversal"]
    assert len(live) == 1 and live[0].original_amount == Decimal("20.00") and live[0].reconciled_at is not None
    reversed_ = [e for e in sms_memory_store.entries.values() if e.status.value != "posted"]
    assert reversed_, "the correction keeps the old entry as a reversal"
    # Unticked: the recorded amount stays.
    finance_store.clear()
    sms_memory_store.clear()
    s = sms_setup()
    send_sms(s["token"], FX_PURCHASE)
    kept = do_import(s["card"]["id"], content, decisions=[{"line": 2, "correct": False}])
    assert kept["corrected"] == 0 and [i["amount"] for i in transactions(s["card"]["id"])] == ["-75.40"]


def test_a_statement_row_pays_a_saved_bill_and_a_weak_one_waits_for_review() -> None:
    bank = account(name="Salary account", last4="5678")
    strong = client.post(f"{API}/bills", headers=OWNER, json={"name": "FAKETEL", "amount": "290", "currency": "SAR", "cadence": "monthly",
                                                             "next_due_on": "2026-11-15"}).json()
    weak = client.post(f"{API}/bills", headers=OWNER, json={"name": "FAKEWATER", "amount": "90", "currency": "SAR", "cadence": "monthly",
                                                           "next_due_on": "2026-11-20"}).json()
    content = "Date,Description,Amount\n2026-11-14,FAKETEL BILL,-290.00\n2026-11-19,FAKEWATER,-140.00\n2026-11-19,FAKEGROCER,-90.00\n"
    view = preview(bank["id"], content)
    bills = [r["match"]["bill"] for r in view["rows"]]
    assert bills[0]["bill_id"] == strong["id"] and bills[0]["strength"] == "strong"
    assert bills[1]["bill_id"] == weak["id"] and bills[1]["strength"] == "weak" and bills[2] is None
    done = do_import(bank["id"], content)
    assert (done["bills_paid"], done["bills_review"]) == (1, 1)
    schedule = {i["bill_id"]: i for i in client.get(f"{API}/bill-schedule?from=2026-11-01&to=2026-11-30", headers=OWNER).json()["items"]}
    assert schedule[strong["id"]]["status"] == "paid" and schedule[weak["id"]]["status"] == "review"


def test_own_transfer_from_the_salary_account_to_the_card_is_paired_on_import() -> None:
    bank = account(name="Salary account", last4="5678")
    card = account(name="Card", kind="card", last4="5377")
    # A purchase a rule recognises (an unrecognised one would be held as uncategorized and counted in neither figure).
    do_import(bank["id"], "Date,Description,Amount\n2026-11-03,TRANSFER TO CARD 5377,-1000.00\n2026-11-03,EXAMPLE GROCERY,-50.00\n")
    view = preview(card["id"], "Date,Description,Amount\n2026-11-05,PAYMENT RECEIVED THANK YOU,1000.00\n")
    transfer = view["rows"][0]["match"]["transfer"]
    assert transfer["direction"] == "from" and transfer["account_id"] == bank["id"] and transfer["strength"] == "strong"
    assert view["counts"]["transfers"] == 1
    done = do_import(card["id"], "Date,Description,Amount\n2026-11-05,PAYMENT RECEIVED THANK YOU,1000.00\n")
    assert done["transfers"] == 1
    every = transactions()
    assert sorted(t["category"] for t in every if abs(Decimal(t["amount"])) == 1000) == ["transfer", "transfer"]
    summary = client.get(f"{API}/summary", headers=OWNER, params={"month": "2026-11"}).json()
    assert all(Decimal(i["amount"]) != Decimal("1000.00") for i in summary["income"])
    assert sum(Decimal(s["amount"]) for s in summary["spend"]) == Decimal("50.00")
    links = [l for l in links_memory_store.links.values() if l.dimension == "transfer"]
    assert len(links) == 1 and links[0].amount == Decimal("1000.00")
    # Paired once: the transfer is not offered again.
    assert client.get(f"{API}/transfers/candidates", headers=OWNER, params={"start": "2026-11-01", "end": "2026-11-30"}).json()["pairs"] == []


def test_find_transfers_over_existing_rows_previews_then_pairs_only_what_is_ticked() -> None:
    bank = account(name="Salary account", last4="5678")
    card = account(name="Card", kind="card", last4="5377")
    savings = account(name="Savings", last4="9999")
    for body in ({"account_id": bank["id"], "amount": "-1000.00", "description": "EXAMPLE CARD PAYMENT 5377", "posted_on": "2026-11-03"},
                 {"account_id": card["id"], "amount": "1000.00", "description": "EXAMPLE PAYMENT", "posted_on": "2026-11-06"},
                 {"account_id": bank["id"], "amount": "-300.00", "description": "EXAMPLE PAYEE", "posted_on": "2026-11-10"},
                 {"account_id": savings["id"], "amount": "300.00", "description": "EXAMPLE DEPOSIT", "posted_on": "2026-11-10"}):
        assert client.post(f"{API}/transactions", headers=OWNER, json=body).status_code == 201
    found = client.get(f"{API}/transfers/candidates", headers=OWNER, params={"start": "2026-11-01", "end": "2026-11-30"}).json()
    assert (found["strong"], found["weak"]) == (1, 1)
    strong = next(p for p in found["pairs"] if p["strength"] == "strong")
    assert strong["into_last4"] == "5377" and strong["lag_days"] == 3
    # A preview changes nothing. (A credit on a card, and card payment wording, may already be filed as a
    # transfer when recorded: docs/finance.md#how-figures-are-counted.)
    before = {t["id"]: t["category"] for t in transactions()}
    client.get(f"{API}/transfers/candidates", headers=OWNER, params={"start": "2026-11-01", "end": "2026-11-30"})
    assert {t["id"]: t["category"] for t in transactions()} == before
    assert client.post(f"{API}/transfers", headers=VIEWER, json={"start": "2026-11-01", "end": "2026-11-30", "pairs": [
        {"out_record_id": strong["out"]["record_id"], "into_record_id": strong["into"]["record_id"]}]}).status_code == 403
    made = client.post(f"{API}/transfers", headers=OWNER, json={"start": "2026-11-01", "end": "2026-11-30", "pairs": [
        {"out_record_id": strong["out"]["record_id"], "into_record_id": strong["into"]["record_id"]}]})
    assert made.status_code == 201, made.text
    # Both rows are filed by the pair (their category source becomes the pairing), even where a rule had
    # already put one of them under transfers.
    assert made.json()["paired"] == 1 and made.json()["recategorized"] == 2
    assert sorted(t["category"] for t in transactions() if abs(Decimal(t["amount"])) == 1000) == ["transfer", "transfer"]
    left = client.get(f"{API}/transfers/candidates", headers=OWNER, params={"start": "2026-11-01", "end": "2026-11-30"}).json()
    assert (left["strong"], left["weak"]) == (0, 1)
    # Another person sees none of it.
    other = client.get(f"{API}/transfers/candidates", headers=OTHER_PERSON, params={"start": "2026-11-01", "end": "2026-11-30"}).json()
    assert other["pairs"] == []
    # A pair that no longer fits is refused, not forced.
    weak = left["pairs"][0]
    stale = client.post(f"{API}/transfers", headers=OWNER, json={"start": "2026-11-01", "end": "2026-11-30", "pairs": [
        {"out_record_id": weak["out"]["record_id"], "into_record_id": strong["into"]["record_id"]}]}).json()
    assert stale["paired"] == 0 and stale["failed"] == ["not_available"]


def test_reconciliation_ties_or_shows_the_difference_and_reaches_the_accounts_overview() -> None:
    bank = account(name="Salary account", last4="5678")
    content = "Date,Description,Amount,Balance\n2026-11-02,EXAMPLE GROCER,-50.00,950.00\n2026-11-01,EXAMPLE SALARY,1000.00,1000.00\n"
    view = preview(bank["id"], content, opening_balance="0.00", closing_balance="950.00")
    assert view["mapping"]["balance"] == "Balance" and view["reconciliation"]["status"] == "tied"  # newest first is put in order
    off = preview(bank["id"], content, opening_balance="0.00", closing_balance="900.00")["reconciliation"]
    assert off["status"] == "differs" and off["difference"] == "-50.00"
    done = do_import(bank["id"], content, opening_balance="0.00", closing_balance="950.00")
    assert done["imported_count"] == 2
    overview = client.get(f"{API}/accounts/overview", headers=OWNER).json()
    mine = next(a for a in overview["accounts"] if a["account_id"] == bank["id"])
    assert mine["last_statement"]["status"] == "tied" and mine["last_reconciled_at"] is not None
    do_import(bank["id"], "Date,Description,Amount\n2026-11-03,EXAMPLE CAFE,-5.00\n", opening_balance="950.00", closing_balance="900.00")
    mine = next(a for a in client.get(f"{API}/accounts/overview", headers=OWNER).json()["accounts"] if a["account_id"] == bank["id"])
    assert mine["last_statement"]["status"] == "differs" and mine["last_statement"]["difference"] == "-45.00"
    assert "statement_differs" in mine["needs_attention"]


def test_preview_needs_manage_and_reads_only_the_callers_ledger() -> None:
    s = sms_setup()
    send_sms(s["token"], PURCHASE)
    content = "Date,Description,Amount\n2026-11-03,EXAMPLE MARKET,-45.50\n"
    denied = client.post(f"{API}/imports/preview", headers=VIEWER, json={"account_id": s["card"]["id"], "content": content})
    assert denied.status_code == 403
    theirs = account(OTHER_PERSON, name="Their card", kind="card")
    view = preview(theirs["id"], content, headers=OTHER_PERSON)
    assert statuses(view) == ["new"]  # the owner's SMS is not someone else's match


def test_a_timed_sms_and_an_untimed_entry_on_one_day_can_be_compared() -> None:
    # Review finding: a timezone-aware SMS time was compared with a naive placeholder and raised TypeError.
    s = sms_setup()
    assert send_sms(s["token"], PURCHASE)["status"] == "posted"  # has a time (14:22)
    hand = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": s["card"]["id"], "amount": "-45.50",
                                                                   "description": "EXAMPLE MARKET", "posted_on": "2026-11-03"})
    assert hand.status_code == 201
    other = client.post(f"{API}/transactions", headers=OWNER, json={"account_id": s["bank"]["id"], "amount": "45.50",
                                                                    "description": "EXAMPLE REFUND", "posted_on": "2026-11-03"})
    assert other.status_code == 201
    content = "Date,Description,Amount\n2026-11-03,EXAMPLE MARKET,-45.50\n2026-11-03,EXAMPLE MARKET,-45.50\n"
    assert statuses(preview(s["card"]["id"], content)) == ["matched_existing", "matched_existing"]
    done = do_import(s["card"]["id"], content)
    assert done["reconciled"] == 2 and done["imported_count"] == 0
    found = client.get(f"{API}/transfers/candidates", headers=OWNER, params={"start": "2026-11-01", "end": "2026-11-30"})
    assert found.status_code == 200, found.text


@pytest.mark.parametrize("card_first", [False, True])
def test_a_cross_currency_transfer_pairs_whichever_side_is_imported_first(card_first: bool) -> None:
    # Review finding: the statement row's original amount was not kept on its journal entry, so a later import
    # of the other side could not see it and the transfer never paired.
    sar = account(name="Salary account", last4="5678")
    usd = account(name="Dollar account", currency="USD", last4="4444")
    sar_csv = "Date,Description,Amount\n2026-11-03,TRANSFER TO DOLLAR ACCOUNT USD 20.00,-75.40\n"
    usd_csv = "Date,Description,Amount\n2026-11-04,TRANSFER RECEIVED,20.00\n"
    first, second = ((usd["id"], usd_csv), (sar["id"], sar_csv)) if card_first else ((sar["id"], sar_csv), (usd["id"], usd_csv))
    do_import(*first)
    view = preview(*second)
    assert view["rows"][0]["match"]["transfer"] is not None and view["rows"][0]["match"]["transfer"]["reasons"][0] == "fx_amount"
    assert do_import(*second)["transfers"] == 1


def test_find_transfers_sees_the_stated_foreign_amount_of_earlier_imports() -> None:
    sar = account(name="Salary account", last4="5678")
    usd = account(name="Dollar account", currency="USD", last4="4444")
    do_import(usd["id"], "Date,Description,Amount\n2026-11-04,EXAMPLE CREDIT,20.00\n")
    do_import(sar["id"], "Date,Description,Amount\n2026-11-03,EXAMPLE OUTGOING USD 20.00,-75.40\n",
              decisions=[{"line": 2, "transfer": False}])
    found = client.get(f"{API}/transfers/candidates", headers=OWNER, params={"start": "2026-11-01", "end": "2026-11-30"}).json()
    assert [p["reasons"][0] for p in found["pairs"]] == ["fx_amount"]


def _pdf(rows: list[tuple[str, str, str, str, str]]) -> bytes:
    from anum_api.pdf_writer import PdfDocument

    document = PdfDocument("SYNTHETIC statement")
    y = 790.0
    for cells in [("Date", "Description", "Debit", "Credit", "Balance"), *rows]:
        for (x, width), value in zip(((40, 0), (110, 0), (300, 60), (380, 60), (460, 70)), cells, strict=True):
            if value:
                if width:
                    document.page.text(x, y, value, 9, align="right", width=width)
                else:
                    document.page.text(x, y, value, 9)
        y -= 14
    return document.render()


def test_pdf_preview_shows_each_rows_match_and_the_import_follows_the_ticks() -> None:
    s = sms_setup()
    send_sms(s["token"], PURCHASE.replace(" Ref 88AB12", ""))
    data = _pdf([("", "Opening balance", "", "", "1,000.00"), ("03/11/2026", "CARD TXN 9912", "45.50", "", "954.50"),
                 ("04/11/2026", "EXAMPLE BAKERY", "9.00", "", "945.50"), ("", "Closing balance", "", "", "945.50")])
    read = client.post(f"{API}/imports/pdf/read", headers={**OWNER, "content-type": "application/pdf"},
                       params={"account_id": s["card"]["id"], "filename": "nov.pdf"}, content=data)
    assert read.status_code == 200, read.text
    view = read.json()
    assert [r["match"]["status"] for r in view["rows"]] == ["possible_duplicate", "new"]
    assert view["counts"]["possible_duplicate"] == 1 and view["counts"]["new"] == 1
    rows = [{"posted_on": r["posted_on"], "description": r["description"], "amount": r["amount"], "balance": r["balance"]} for r in view["rows"]]
    rows[0]["action"] = "import"  # the person says it is a separate purchase
    body = {"account_id": view["account_id"], "filename": view["filename"], "file_sha256": view["file_sha256"], "source": view["source"],
            "rows": rows, "opening_balance": view["check"]["opening"], "closing_balance": view["check"]["closing"]}
    done = client.post(f"{API}/imports/pdf", headers=OWNER, json=body)
    assert done.status_code == 201, done.text
    assert (done.json()["imported_count"], done.json()["held_for_review"]) == (2, 0)
    overview = client.get(f"{API}/accounts/overview", headers=OWNER).json()
    card = next(a for a in overview["accounts"] if a["account_id"] == s["card"]["id"])
    assert card["last_statement"]["status"] == "tied"
