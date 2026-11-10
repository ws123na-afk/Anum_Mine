"""A repayment split across loans, the revaluation of foreign balances and personal trips and projects
(docs/finance.md#lent-and-borrowed, #foreign-currency-balances, #trips-and-projects). In-memory stores; every record is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from test_finance_personal_money import OTHER, OWNER, VIEWER, account, clean, client, record  # noqa: F401  (``clean`` resets the stores)

from anum_api.finance import revaluation as R
from anum_api.finance.models import Account, AccountKind, FxRate
from anum_api.finance.rules_store import rules_memory_store
from anum_api.schemas import utc_now

API = "/api/v1/finance"
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def decisions() -> Iterator[None]:
    rules_memory_store.clear()
    yield
    rules_memory_store.clear()


def today() -> date:
    return utc_now().date()


def queue(kind: str) -> list[dict]:
    response = client.get(f"{API}/suggestions", headers=OWNER, params={"kind": kind, "limit": 100})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def loan(person: str, amount: str, due: date, currency: str = "AED", direction: str = "lent", headers: dict = OWNER) -> dict:
    made = client.post(f"{API}/people-loans", headers=headers, json={
        "direction": direction, "person": person, "amount": amount, "currency": currency, "lent_on": (today() - timedelta(days=60)).isoformat(),
        "due_on": due.isoformat()})
    assert made.status_code == 201, made.text
    return made.json()


# -- 1. a repayment split across loans ------------------------------------------------------------------------------------


def test_one_transaction_covering_two_loans_is_proposed_as_a_split_and_accepted_through_the_links_api() -> None:
    bank = account(balance="5000")
    first = loan("Synthetic Cousin", "300", today() - timedelta(days=3))
    second = loan("Synthetic Cousin", "200", today() + timedelta(days=10))
    record(bank["id"], "500", (today() - timedelta(days=1)).isoformat(), "Transfer from Synthetic Cousin")
    found = [i for i in queue("loan_repayment") if i["params"].get("split")]
    assert len(found) == 1, queue("loan_repayment")
    item = found[0]
    parts = item["action"]["parts"]
    assert item["action"]["link"] is None and item["params"]["split"] == "2" and item["confidence"] == "0.85"
    assert [(p["target_id"], p["amount"], p["currency"], p["role"]) for p in parts] == [
        (first["id"], "300.00", "AED", "deposit"), (second["id"], "200.00", "AED", "deposit")]
    # Nothing is linked before the person accepts; a viewer cannot accept.
    assert client.get(f"{API}/people-loans/{first['id']}", headers=OWNER).json()["repaid"] == "0.00"
    assert client.post(f"{API}/suggestions/accept", headers=VIEWER, json={"key": item["key"], "payload_hash": item["payload_hash"]}).status_code == 403
    accepted = client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": item["key"], "payload_hash": item["payload_hash"]})
    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()["link_ids"]) == 2 and accepted.json()["link_id"] == accepted.json()["link_ids"][0]
    one = client.get(f"{API}/people-loans/{first['id']}", headers=OWNER).json()
    two = client.get(f"{API}/people-loans/{second['id']}", headers=OWNER).json()
    assert (one["repaid"], one["status"], two["repaid"], two["status"]) == ("300.00", "repaid", "200.00", "repaid")
    # Unlinking removes one part only.
    gone = client.delete(f"{API}/people-loans/{second['id']}/links/{two['movements'][0]['link']['id']}", headers=OWNER)
    assert gone.status_code == 204
    assert client.get(f"{API}/people-loans/{second['id']}", headers=OWNER).json()["repaid"] == "0.00"
    assert client.get(f"{API}/people-loans/{first['id']}", headers=OWNER).json()["repaid"] == "300.00"
    assert not [i for i in queue("loan_repayment") if i["params"].get("split")]


def test_the_split_route_checks_the_total_the_currency_each_loan_and_who_asks() -> None:
    bank = account(balance="5000")
    first = loan("Synthetic Aunt", "300", today() + timedelta(days=5))
    second = loan("Synthetic Aunt", "300", today() + timedelta(days=9))
    usd = loan("Synthetic Aunt", "300", today() + timedelta(days=9), currency="USD")
    canonical = record(bank["id"], "400", today().isoformat(), "Transfer from Synthetic Aunt")
    body = {"canonical_id": canonical, "parts": [{"loan_id": first["id"], "amount": "250"}, {"loan_id": second["id"], "amount": "250"}]}
    # More than the transaction: refused, and no part is made.
    over = client.post(f"{API}/people-loans/split", headers=OWNER, json=body)
    assert over.status_code == 409 and over.json()["code"] == "over_allocated" and over.json()["remaining"] == "400.00"
    assert client.get(f"{API}/people-loans/{first['id']}", headers=OWNER).json()["repaid"] == "0.00"
    # A loan in another currency is never mixed in.
    mixed = client.post(f"{API}/people-loans/split", headers=OWNER, json={**body, "parts": [body["parts"][0], {"loan_id": usd["id"], "amount": "100"}]})
    assert mixed.status_code == 422 and mixed.json()["code"] == "currency_mismatch"
    # A part above what that loan still expects.
    big = client.post(f"{API}/people-loans/split", headers=OWNER, json={**body, "parts": [{"loan_id": first["id"], "amount": "350"},
                                                                                           {"loan_id": second["id"], "amount": "50"}]})
    assert big.status_code == 409 and big.json()["code"] == "over_loan"
    # The same loan twice, or one part only.
    twice = client.post(f"{API}/people-loans/split", headers=OWNER, json={**body, "parts": [body["parts"][0], body["parts"][0]]})
    assert twice.status_code == 422 and twice.json()["code"] == "duplicate_part"
    assert client.post(f"{API}/people-loans/split", headers=OWNER, json={**body, "parts": body["parts"][:1]}).status_code == 422
    # A viewer cannot link; another person cannot use these loans.
    fine = {**body, "parts": [{"loan_id": first["id"], "amount": "250"}, {"loan_id": second["id"], "amount": "150"}]}
    assert client.post(f"{API}/people-loans/split", headers=VIEWER, json=fine).status_code == 403
    assert client.post(f"{API}/people-loans/split", headers=OTHER, json=fine).status_code == 404
    made = client.post(f"{API}/people-loans/split", headers={**OWNER, "Idempotency-Key": "split-example"}, json=fine)
    assert made.status_code == 201, made.text
    assert [l["amount"] for l in made.json()["links"]] == ["250.00", "150.00"] and made.json()["replayed"] is False
    again = client.post(f"{API}/people-loans/split", headers={**OWNER, "Idempotency-Key": "split-example"}, json=fine)
    assert again.json()["replayed"] is True and [l["id"] for l in again.json()["links"]] == [l["id"] for l in made.json()["links"]]
    assert client.get(f"{API}/people-loans/{second['id']}", headers=OWNER).json()["repaid"] == "150.00"


def test_no_split_is_proposed_when_the_amount_is_not_exactly_what_is_due() -> None:
    bank = account(balance="5000")
    loan("Synthetic Uncle", "300", today() + timedelta(days=3))
    loan("Synthetic Uncle", "200", today() + timedelta(days=10))
    record(bank["id"], "450", today().isoformat(), "Transfer from Synthetic Uncle")
    assert not [i for i in queue("loan_repayment") if i["params"].get("split")]


# -- 2. foreign balances revalued at stored rates ---------------------------------------------------------------------------


def acct(currency: str = "USD", opening: str = "0", kind: AccountKind = AccountKind.BANK) -> Account:
    return Account(tenant_id="t", workspace_id="w", owner_user_id="u", id="acc_usd", name="Dollars", kind=kind, currency=currency,
                   opening_balance=D(opening), created_at=STAMP, updated_at=STAMP)


def rate(on: date, value: str, base: str = "USD", quote: str = "AED") -> FxRate:
    return FxRate(tenant_id="t", workspace_id="w", owner_user_id="u", id=f"r_{on}_{value}", base=base, quote=quote, rate=D(value), as_of=on,
                  source="manual", fetched_at=STAMP)


class Row:
    def __init__(self, on: date, amount: str, currency: str = "USD") -> None:
        self.account_id, self.posted_on, self.amount, self.currency = "acc_usd", on, D(amount), currency


def test_average_cost_values_money_in_at_its_day_and_money_out_at_the_running_average() -> None:
    rows = [Row(date(2026, 2, 1), "1000"), Row(date(2026, 3, 1), "1000"), Row(date(2026, 3, 10), "-500")]
    rates = [rate(date(2026, 2, 1), "3.60"), rate(date(2026, 3, 1), "3.70"), rate(date(2026, 4, 1), "3.80")]
    found = R.revalue(acct(), rows, "AED", rates, date(2026, 4, 2), 7)  # type: ignore[arg-type]
    # In: 1000 x 3.60 + 1000 x 3.70 = 7300 for 2000; out 500 at the average 3.65 leaves 1500 costing 5475.
    assert (found.status, found.balance, found.value, found.cost, found.unrealised) == ("ok", D("1500.00"), D("5700.00"), D("5475.00"), D("225.00"))
    assert (found.rate, found.rate_as_of) == (D("3.80"), date(2026, 4, 1))


def test_a_missing_rate_is_said_and_never_guessed() -> None:
    rows = [Row(date(2026, 2, 1), "1000")]
    no_rate_now = R.revalue(acct(), rows, "AED", [rate(date(2026, 2, 1), "3.60")], date(2026, 4, 2), 7)  # type: ignore[arg-type]
    assert (no_rate_now.status, no_rate_now.value, no_rate_now.unrealised, no_rate_now.cost) == ("rate_not_available", None, None, D("3600.00"))
    no_cost = R.revalue(acct(), rows, "AED", [rate(date(2026, 4, 1), "3.80")], date(2026, 4, 2), 7)  # type: ignore[arg-type]
    assert (no_cost.status, no_cost.value, no_cost.cost, no_cost.missing_rate_dates) == ("cost_rate_not_available", D("3800.00"), None, [date(2026, 2, 1)])
    # Cards and accounts in the base currency are not revalued; an empty balance has nothing to revalue.
    assert R.revalue_all([acct(kind=AccountKind.CARD), acct(currency="AED")], [], "AED", [], date(2026, 4, 2), 7) == []
    assert R.revalue(acct(), [], "AED", [], date(2026, 4, 2), 7).status == "nothing_to_revalue"


def test_the_api_revalues_the_owners_own_foreign_accounts_and_posts_nothing() -> None:
    usd = account(currency="USD", name="Dollars")
    account(currency="AED", name="Dirhams", balance="100")
    record(usd["id"], "1000", (today() - timedelta(days=20)).isoformat(), "Salary abroad", category="income")
    for on, value in ((today() - timedelta(days=20), "3.60"), (today() - timedelta(days=1), "3.70")):
        assert client.post(f"{API}/fx-rates", headers=OWNER, json={"base": "USD", "quote": "AED", "rate": value, "as_of": on.isoformat()}).status_code == 201
    entries = client.get(f"{API}/ledger/entries", headers=OWNER).json()["total"]
    page = client.get(f"{API}/fx-revaluation", headers=OWNER).json()
    assert page["base_currency"] == "AED" and len(page["items"]) == 1
    row = page["items"][0]
    assert (row["currency"], row["balance"], row["value"], row["cost"], row["unrealised"], row["status"]) == (
        "USD", "1000.00", "3700.00", "3600.00", "100.00", "ok")
    assert page["unrealised_total"] == "100.00" and page["incomplete"] == 0
    assert client.get(f"{API}/fx-revaluation", headers=OWNER, params={"account_id": usd["id"]}).json()["items"][0]["account_id"] == usd["id"]
    # Never posted: the ledger has the same entries after reading it.
    assert client.get(f"{API}/ledger/entries", headers=OWNER).json()["total"] == entries
    # Another person sees none of it; the owner's account is not found for them.
    assert client.get(f"{API}/fx-revaluation", headers=OTHER).json()["items"] == []
    assert client.get(f"{API}/fx-revaluation", headers=OTHER, params={"account_id": usd["id"]}).status_code == 404


# -- 3. personal trips and projects --------------------------------------------------------------------------------------


def tag(kind: str, name: str, **body: object) -> dict:
    made = client.post(f"{API}/tags", headers=OWNER, json={"kind": kind, "name": name, **body})
    assert made.status_code == 201, made.text
    return made.json()


def test_a_trip_is_proposed_for_spending_within_its_dates_and_tagged_only_on_accept() -> None:
    bank = account(balance="9000")
    trip = tag("trip", "Synthetic Istanbul trip", starts_on=(today() - timedelta(days=10)).isoformat(), ends_on=(today() - timedelta(days=3)).isoformat(),
               keywords="Istanbul")
    record(bank["id"], "-420", (today() - timedelta(days=6)).isoformat(), "Hotel Istanbul Synthetic", category="travel")
    record(bank["id"], "-50", (today() - timedelta(days=30)).isoformat(), "Hotel Istanbul Synthetic", category="travel")  # before the trip
    found = [i for i in queue("attribution") if i["params"]["dimension"] == "trip"]
    assert len(found) == 1 and found[0]["action"]["link"]["target_id"] == trip["id"] and found[0]["confidence"] == "0.8"
    assert client.get(f"{API}/tags/{trip['id']}", headers=OWNER).json()["links"] == 0
    assert client.post(f"{API}/suggestions/accept", headers=VIEWER, json={"key": found[0]["key"], "payload_hash": found[0]["payload_hash"]}).status_code == 403
    accepted = client.post(f"{API}/suggestions/accept", headers=OWNER, json={"key": found[0]["key"], "payload_hash": found[0]["payload_hash"]})
    assert accepted.status_code == 200, accepted.text
    detail = client.get(f"{API}/tags/{trip['id']}", headers=OWNER).json()
    assert detail["totals"] == {"AED": "420.00"} and len(detail["movements"]) == 1
    # A tag is a view: the transaction keeps its own category.
    txns = client.get(f"{API}/transactions", headers=OWNER, params={"q": "Istanbul"}).json()["items"]
    assert {t["category"] for t in txns} == {"travel"}
    # Untag removes the link only.
    link_id = detail["movements"][0]["link"]["id"]
    assert client.delete(f"{API}/tags/{trip['id']}/links/{link_id}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/tags/{trip['id']}", headers=OWNER).json()["totals"] == {}


def test_a_project_is_tagged_by_hand_learns_the_merchant_and_is_the_owners_own() -> None:
    bank = account(balance="9000")
    project = tag("project", "Synthetic kitchen")
    first = record(bank["id"], "-300", (today() - timedelta(days=9)).isoformat(), "Synthetic Tiles Trading")
    assert client.post(f"{API}/tags/{project['id']}/links", headers=VIEWER, json={"canonical_id": first}).status_code == 403
    assert client.post(f"{API}/tags/{project['id']}/links", headers=OTHER, json={"canonical_id": first}).status_code == 404
    made = client.post(f"{API}/tags/{project['id']}/links", headers=OWNER, json={"canonical_id": first, "amount": "200"})
    assert made.status_code == 201, made.text
    assert (made.json()["link"]["dimension"], made.json()["link"]["amount"], made.json()["link"]["role"]) == ("project", "200.00", "payment")
    # Never more than the payment, across the projects it is split over.
    other = tag("project", "Synthetic garden")
    over = client.post(f"{API}/tags/{other['id']}/links", headers=OWNER, json={"canonical_id": first, "amount": "150"})
    assert over.status_code == 409 and over.json()["code"] == "over_allocated"
    record(bank["id"], "-80", (today() - timedelta(days=2)).isoformat(), "Synthetic Tiles Trading")
    found = [i for i in queue("attribution") if i["params"]["dimension"] == "project"]
    assert len(found) == 1 and found[0]["params"]["reason"] == "history" and found[0]["action"]["link"]["amount"] == "80.00"
    # Others see nothing; a viewer cannot make one; dates the wrong way round are refused.
    assert client.get(f"{API}/tags", headers=OTHER).json()["total"] == 0
    assert client.get(f"{API}/tags/{project['id']}", headers=OTHER).status_code == 404
    assert client.post(f"{API}/tags", headers=VIEWER, json={"kind": "trip", "name": "Synthetic"}).status_code == 403
    assert client.post(f"{API}/tags", headers=OWNER, json={"kind": "trip", "name": "Synthetic", "starts_on": "2026-05-02", "ends_on": "2026-05-01"}).status_code == 422
    listed = client.get(f"{API}/tags", headers=OWNER, params={"kind": "project", "q": "kitchen"}).json()
    assert listed["total"] == 1 and listed["items"][0]["totals"] == {"AED": "200.00"}
    # Deleting the project removes its links; the payment stays.
    assert client.delete(f"{API}/tags/{project['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{API}/links", headers=OWNER, params={"dimension": "project", "status": "active"}).json()["total"] == 0
