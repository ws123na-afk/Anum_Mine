"""Card billing differences, FX fees and own transfers in two currencies (docs/finance.md#card-fx-and-fees). In-memory stores.

Statement rows and merchants are SYNTHETIC. The purchase is never inflated by the bank's FX fee, no rate is invented, and a
transfer's realised FX difference is shown, never posted.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import card_fx as C
from anum_api.finance import links as K
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import links_memory_store
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.schemas import TenantContext, utc_now

client = TestClient(main.app)
TENANT, WORKSPACE = "tenant_cardfx", "ws_cardfx"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_cardfx", "x-user-roles": "owner"}
VIEWER = {**OWNER, "x-user-id": "viewer_cardfx", "x-user-roles": "viewer"}
API = "/api/v1/finance"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    for store in (finance_store, sms_memory_store, links_memory_store, rules_memory_store):
        store.clear()
    svc._last_notified.clear()
    yield
    for store in (finance_store, sms_memory_store, links_memory_store, rules_memory_store):
        store.clear()


def day(back: int = 3) -> str:
    return (utc_now().date() - timedelta(days=back)).isoformat()


def account(**body: Any) -> dict:
    response = client.post(f"{API}/accounts", headers=OWNER, json={"name": "Card", "kind": "card", "currency": "SAR", **body})
    assert response.status_code == 201, response.text
    return response.json()


def do_import(account_id: str, content: str) -> dict:
    response = client.post(f"{API}/imports", headers=OWNER, json={"account_id": account_id, "content": content, "filename": "oct.csv"})
    assert response.status_code == 201, response.text
    return response.json()


def rows(account_id: str) -> list[dict]:
    return client.get(f"{API}/transactions", headers=OWNER, params={"account_id": account_id, "limit": 100}).json()["items"]


def by_text(account_id: str, words: str) -> dict:
    return next(r for r in rows(account_id) if words in r["description"])


def fx(transaction_id: str) -> dict:
    response = client.get(f"{API}/transactions/{transaction_id}/fx", headers=OWNER)
    assert response.status_code == 200, response.text
    return response.json()


def total(account_id: str) -> Decimal:
    return sum((Decimal(r["amount"]) for r in rows(account_id)), Decimal(0))


def balanced() -> bool:
    for entry in sms_memory_store.entries.values():
        sums: dict[str, Decimal] = {}
        for posting in entry.postings:
            sums[posting.currency] = sums.get(posting.currency, Decimal(0)) + posting.amount
        if any(v != 0 for v in sums.values()):
            return False
    return True


def kinds() -> list[str]:
    return [i["kind"] for i in client.get(f"{API}/suggestions", headers=OWNER, params={"limit": 100}).json()["items"]]


def test_a_usd_purchase_billed_in_sar_with_a_separate_markup_row() -> None:
    card = account()
    do_import(card["id"], f"Date,Description,Amount\n{day()},EXAMPLE BOOKSHOP USD 20.00,-75.00\n{day()},FX MARKUP 2.5% INTL TXN FEE,-1.88\n")
    purchase = by_text(card["id"], "BOOKSHOP")
    view = fx(purchase["id"])
    assert (view["original_amount"], view["original_currency"], view["billed_amount"], view["billed_currency"]) == ("20.00", "USD", "75.00", "SAR")
    # The rate comes from the two printed amounts and says so; the purchase is not inflated by the fee row.
    assert (view["rate"], view["rate_kind"], view["rate_date"], view["rate_source"]) == ("3.750000", "implied", day(), "statement")
    assert view["purchase_amount"] == "75.00" and view["fee"]["source"] == "fee_row" and view["fee"]["amount"] == "1.88"
    assert view["fee"]["markup_percent"] == "2.5" and view["split"] == "link"
    assert "fx_fee" in kinds()
    before = total(card["id"])
    refused = client.post(f"{API}/transactions/{purchase['id']}/fx/separate", headers=VIEWER, json={"plan_hash": view["plan_hash"]})
    assert refused.status_code == 403
    done = client.post(f"{API}/transactions/{purchase['id']}/fx/separate", headers=OWNER, json={"plan_hash": view["plan_hash"]})
    assert done.status_code == 200, done.text
    assert done.json()["mode"] == "link" and done.json()["purchase"]["split"] == "done"
    fee = by_text(card["id"], "MARKUP")
    assert fee["transaction_type"] == "bank_fee" and fee["category"] == "fees" and fee["related_transaction_id"] == purchase["id"]
    assert total(card["id"]) == before and Decimal(by_text(card["id"], "BOOKSHOP")["amount"]) == Decimal("-75.00") and balanced()
    assert "fx_fee" not in kinds()


def test_a_statement_that_states_the_markup_inline_separates_the_fee_as_its_own_line() -> None:
    card = account()
    do_import(card["id"], f"Date,Description,Amount\n{day()},EXAMPLE BOOKSHOP USD 20.00 markup 2.5%,-76.88\n")
    purchase = by_text(card["id"], "BOOKSHOP")
    view = fx(purchase["id"])
    assert view["billed_amount"] == "76.88" and view["purchase_amount"] == "75.00" and view["rate"] == "3.750000"
    assert view["fee"] == {"amount": "1.88", "currency": "SAR", "source": "markup_stated", "transaction_id": None, "markup_percent": "2.5",
                           "included_in_billed": True, "linked": False}
    assert view["split"] == "available" and view["can_correct"] is True
    stale = client.post(f"{API}/transactions/{purchase['id']}/fx/separate", headers=OWNER, json={"plan_hash": "0" * 64})
    assert stale.status_code == 409
    done = client.post(f"{API}/transactions/{purchase['id']}/fx/separate", headers=OWNER, json={"plan_hash": view["plan_hash"]})
    assert done.status_code == 200, done.text
    assert done.json()["mode"] == "split"
    after = rows(card["id"])
    assert sorted(Decimal(r["amount"]) for r in after) == [Decimal("-75.00"), Decimal("-1.88")]
    fee = next(r for r in after if r["id"] == done.json()["fee_transaction_id"])
    assert fee["transaction_type"] == "bank_fee" and fee["related_transaction_id"] == purchase["id"]
    # The account still moved by what the bank billed, the journal balances, and the view remembers the split.
    assert total(card["id"]) == Decimal("-76.88") and balanced()
    again = fx(purchase["id"])
    assert again["split"] == "done" and again["billed_amount"] == "76.88" and again["fee"]["transaction_id"] == fee["id"]
    assert client.post(f"{API}/transactions/{purchase['id']}/fx/separate", headers=OWNER, json={"plan_hash": again["plan_hash"]}).status_code == 409
    # Re-importing the same statement adds nothing.
    do_import(card["id"], f"Date,Description,Amount\n{day()},EXAMPLE BOOKSHOP USD 20.00 markup 2.5%,-76.88\n")
    assert len(rows(card["id"])) == 2


def test_only_the_billed_amount_means_rate_not_stated() -> None:
    card = account()
    do_import(card["id"], f"Date,Description,Amount\n{day()},EXAMPLE CAFE,-76.80\n")
    view = fx(by_text(card["id"], "CAFE")["id"])
    assert view["foreign"] is False and view["rate"] is None and view["rate_kind"] == "not_stated" and "rate_not_stated" in view["notes"]
    assert view["split"] == "none"
    found = C.details(C.Row("t", "a", date(2026, 10, 1), Decimal("-76.80"), "SAR", "EXAMPLE SHOP", notes=None, original_currency="USD"))
    assert found.foreign and found.rate is None and found.notes == ["rate_not_stated"]


def test_a_fee_printed_without_a_rate_that_proves_it_is_included_is_never_split() -> None:
    found = C.details(C.Row("t", "a", date(2026, 10, 1), Decimal("-76.00"), "SAR", "EXAMPLE SHOP USD 20.00 intl txn fee SAR 1.00"))
    assert found.fee is None and found.split == "none" and "fee_not_provably_included" in found.notes
    printed = C.details(C.Row("t", "a", date(2026, 10, 1), Decimal("-76.00"), "SAR", "EXAMPLE SHOP USD 20.00 @ 3.75 intl txn fee SAR 1.00"))
    assert printed.fee is not None and printed.fee.source == "fee_stated" and printed.purchase_amount == Decimal("75.00")
    assert printed.rate == Decimal("3.75") and printed.rate_kind == "printed"


def test_an_own_transfer_aed_to_sar_records_the_realised_rate_and_the_fx_difference() -> None:
    aed = account(name="Dirham account", kind="bank", currency="AED", last4="1111")
    sar = account(name="Riyal account", kind="bank", currency="SAR", last4="2222")
    when = day(2)
    for body in ({"account_id": aed["id"], "amount": "-1000.00", "description": "EXAMPLE TRANSFER TO OWN ACCOUNT 2222", "posted_on": when},
                 {"account_id": sar["id"], "amount": "1020.00", "description": "EXAMPLE TRANSFER IN AED 1000.00", "posted_on": when}):
        assert client.post(f"{API}/transactions", headers=OWNER, json=body).status_code == 201
    out_row, in_row = by_text(aed["id"], "TRANSFER"), by_text(sar["id"], "TRANSFER")
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_cardfx")
    out_c = sms_memory_store.entry_for_transaction(context, out_row["id"]).canonical_id
    in_c = sms_memory_store.entry_for_transaction(context, in_row["id"]).canonical_id
    # Paired the way Finance, Import pairs own transfers (``statement_ledger.make_links``, through ``links.create``).
    paired = K.create(context, K.LinkRequest(canonical_id=out_c, dimension="transfer", role="payment", target_type="finance_transfer",
                                             target_id=in_c, amount=Decimal("1000.00"), currency="AED"))
    assert not paired.replayed
    # No earlier rate on record: the realised rate only.
    plain = fx(out_row["id"])["transfer"]
    assert plain["role"] == "out" and plain["realised_rate"] == "1.020000" and plain["difference"] is None
    rate = client.post(f"{API}/fx-rates", headers=OWNER, json={"base": "AED", "quote": "SAR", "rate": "1.0211", "as_of": day(3)})
    assert rate.status_code == 201, rate.text
    view = fx(out_row["id"])["transfer"]
    assert (view["out_amount"], view["out_currency"], view["in_amount"], view["in_currency"]) == ("1000.00", "AED", "1020.00", "SAR")
    assert Decimal(view["valuation_rate"]) == Decimal("1.0211") and view["valued_at"] == "1021.10" and view["difference"] == "-1.10"
    assert fx(in_row["id"])["transfer"]["role"] == "in"
    # The difference is a line of the view, never posted: the journal and both rows are unchanged, and it is not spending.
    assert sorted(Decimal(r["amount"]) for r in rows(aed["id"]) + rows(sar["id"])) == [Decimal("-1000.00"), Decimal("1020.00")] and balanced()
