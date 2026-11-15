"""Matching across currencies (docs/finance-matching.md#across-currencies). Every message, order and amount is SYNTHETIC.

A USD installment paid from a SAR card: the bank's printed original amount is compared with what is due, the
installment is settled in USD, the link's own amount stays the SAR purchase, an FX fee stays apart, and nothing is
converted at a rate ANUM chose. Without the original amount the match waits (``fx_evidence_missing``).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.finance import links as K
from anum_api.finance import matching as E
from anum_api.finance import sms_service as svc
from anum_api.finance.links_store import LinkStatus, links_memory_store
from anum_api.finance.match_policy import MatchingPolicy
from anum_api.finance.models import FinanceSettings
from anum_api.finance.rules_store import rules_memory_store
from anum_api.finance.sms_store import sms_memory_store
from anum_api.finance.store import finance_store
from anum_api.finance.summary import today_in

TODAY = date(2026, 11, 15)
BANK = "fin_acct_bank"
API = "/api/v1/finance"
OWNER = {"x-tenant-id": "tenant_fxm", "x-workspace-id": "ws_fxm", "x-user-id": "owner_fxm", "x-user-roles": "owner"}
OTHER = {**OWNER, "x-user-id": "someone_else_fxm"}
OTHER_TENANT = {**OWNER, "x-tenant-id": "tenant_fxm_b", "x-workspace-id": "ws_fxm_b"}
SENDER = "TestBank"  # SYNTHETIC
client = TestClient(main.app)


# -- the engine, pure ---------------------------------------------------------------------------------------------


def policy(**over: object) -> MatchingPolicy:
    values: dict = {"match_transactions": True, "reconcile_installments": True, "link_recurring_bills": True, "link_bank_loan_repayments": True,
                    "source_account_ids": [BANK], "approved_at": datetime(2026, 11, 1, tzinfo=timezone.utc)}
    values.update(over)
    return MatchingPolicy(**values)


def fx(original: str | None = "100", billed: str = "375", purchase: str | None = None, *, currency: str = "USD", fee: str | None = None,
       fee_source: str | None = None) -> E.FxEvidence:
    return E.FxEvidence(original_currency=currency, original_amount=Decimal(original) if original else None, billed_amount=Decimal(billed),
                        purchase_amount=Decimal(purchase or billed), rate=Decimal("3.75") if original else None,
                        rate_kind="implied" if original else "not_stated", rate_source="sms", rate_date=TODAY,
                        fee_amount=Decimal(fee) if fee else None, fee_source=fee_source)


def pay(amount: str = "375", text: str = "POS PURCHASE TAMARA", *, evidence: E.FxEvidence | None = None, canonical: str = "fin_je_1") -> E.Payment:
    value = Decimal(amount)
    return E.Payment(canonical_id=canonical, posted_on=TODAY, account_id=BANK, amount=value, currency="SAR", direction="out", text=text,
                     sources=frozenset({"sms"}), left={"bill": value, "debt": value}, fx=evidence)


def usd_installment(amount: str = "100", *, order: str = "ORD10001", bill: str = "fin_bill_a") -> E.Obligation:
    return E.Obligation(kind="installment", dimension="bill", target_type="finance_bill_due", target_id=f"{bill}:{TODAY.isoformat()}",
                        label="Shop 2/4", due_on=TODAY, expected=Decimal(amount), currency="USD", provider="tamara", order_ref=order,
                        installment_no=2, group=f"tamara:{order.casefold()}")


def only(result: E.Result) -> E.Decision:
    assert len(result.decisions) == 1, result.decisions
    return result.decisions[0]


def test_the_banks_original_amount_pays_a_usd_installment_from_a_sar_card_automatically_when_unique() -> None:
    d = only(E.decide([pay(evidence=fx())], [usd_installment()], policy()))
    assert d.outcome == "auto" and d.reason == "fx_original_amount" and d.state == "verified"
    part = d.parts[0]
    # Settled in the obligation's currency; the bank side stays the purchase in the account's currency.
    assert (part.amount, part.obligation.currency, part.bank_amount, part.payment.currency) == (Decimal("100"), "USD", Decimal("375"), "SAR")
    assert {"fx_original", "amount_exact", "provider"} <= {e.code for e in part.evidence}
    # Two USD installments it fits equally: review, never a pick.
    two = E.decide([pay(evidence=fx())], [usd_installment(), usd_installment(order="ORD20002", bill="fin_bill_b")], policy())
    assert only(two).outcome == "review" and only(two).reason == "ambiguous_obligation"
    # Another original currency, or another amount, is not this installment.
    assert E.decide([pay(evidence=fx(currency="EUR"))], [usd_installment()], policy()).decisions == []
    assert E.decide([pay(evidence=fx(original="120"))], [usd_installment()], policy()).decisions == []


def test_without_the_original_amount_the_match_waits_with_fx_evidence_missing() -> None:
    for evidence in (None, fx(original=None)):
        d = only(E.decide([pay(evidence=evidence)], [usd_installment()], policy()))
        assert d.outcome == "review" and d.reason == "fx_evidence_missing" and d.parts[0].bank_amount is None
        assert "fx_evidence_missing" in {e.code for e in d.parts[0].evidence}
    # A same-currency installment the payment fits beats one in another currency that it only names.
    sar = E.Obligation(kind="installment", dimension="bill", target_type="finance_bill_due", target_id=f"fin_bill_s:{TODAY.isoformat()}",
                       label="Shop 1/4", due_on=TODAY, expected=Decimal("375"), currency="SAR", provider="tamara", order_ref="ORD30003",
                       installment_no=1, group="tamara:ord30003")
    d = only(E.decide([pay()], [usd_installment(), sar], policy()))
    assert d.outcome == "auto" and d.parts[0].obligation.currency == "SAR"


def test_an_fx_fee_the_bank_included_is_kept_apart_from_what_the_installment_was_paid() -> None:
    evidence = fx(billed="384.38", purchase="375", fee="9.38", fee_source="markup_stated")
    d = only(E.decide([pay("384.38", evidence=evidence)], [usd_installment()], policy()))
    assert d.outcome == "auto" and d.parts[0].bank_amount == Decimal("375") and d.parts[0].amount == Decimal("100")
    assert "fx_fee" in {e.code for e in d.parts[0].evidence}
    stored = evidence.as_dict("SAR", d.parts[0].bank_amount)
    assert (stored["bank_amount"], stored["fee_amount"], stored["fee_source"], stored["rate_kind"]) == ("375", "9.38", "markup_stated", "implied")


def test_a_part_payment_across_currencies_waits_and_a_card_is_repaid_across_currencies_only_on_the_banks_figures() -> None:
    d = only(E.decide([pay("225", evidence=fx(original="60", billed="225"))], [usd_installment()], policy()))
    assert d.outcome == "review" and d.reason == "partial_amount" and d.parts[0].amount == Decimal("60") and d.parts[0].bank_amount == Decimal("225")
    card = E.Obligation(kind="card_repayment", dimension="debt", target_type="finance_debt", target_id="fin_debt_card", label="USD card",
                        due_on=date(9999, 12, 31), expected=Decimal("1e12"), currency="USD", liability_account_id="fin_acct_usd_card")
    repay = E.Payment(canonical_id="fin_je_9", posted_on=TODAY, account_id=BANK, amount=Decimal("375"), currency="SAR", direction="out", text="CARD PAYMENT",
                      sources=frozenset({"sms"}), kind="card_repayment", counter_account_id="fin_acct_usd_card",
                      left={"bill": Decimal("375"), "debt": Decimal("375")}, fx=fx())
    # The bank printed USD 100 for the SAR 375: the card is repaid USD 100 (the gap/finance batch; before it, never across currencies).
    d = only(E.decide([repay], [card], policy(link_card_repayments=True)))
    assert d.parts[0].amount == Decimal("100") and d.parts[0].bank_amount == Decimal("375") and d.reason == "fx_original_amount"
    # Without the original amount it waits for the owner with the reason, never on a guessed rate.
    unknown = E.Payment(**{**repay.__dict__, "fx": fx(original=None)})
    d = only(E.decide([unknown], [card], policy(link_card_repayments=True)))
    assert (d.outcome, d.reason, d.parts[0].fx_missing) == ("review", "fx_evidence_missing", True)


def test_a_link_settles_the_obligation_in_its_own_currency_and_two_currencies_are_never_added() -> None:
    link = type("L", (), {"amount": Decimal("375"), "currency": "SAR", "settled_amount": Decimal("100"), "settled_currency": "USD"})()
    plain = type("L", (), {"amount": Decimal("50"), "currency": "USD", "settled_amount": None, "settled_currency": None})()
    assert (K.settled(link, "USD"), K.settled(link, "SAR"), K.settled(plain, "USD"), K.settled(plain, "SAR")) == (Decimal("100"), 0, Decimal("50"), 0)
    assert K.settled_currency(link) == "USD" and K.settled_currency(plain) == "USD"
    for bad in ({"settled_amount": Decimal("100"), "settled_currency": "SAR", "fx": {"rate": "1"}},  # the same currency
                {"settled_amount": Decimal("100"), "settled_currency": "USD"},  # no evidence
                {"settled_amount": Decimal("0"), "settled_currency": "USD", "fx": {"rate": "1"}}):
        with pytest.raises(K.LinkError):
            K._validate(K.LinkRequest(canonical_id="fin_je_1", dimension="bill", role="payment", target_type="finance_bill_due",
                                      target_id="fin_bill_a:2026-11-15", amount=Decimal("375"), currency="SAR", **bad))  # type: ignore[arg-type]
    with pytest.raises(K.LinkError) as refused:
        K._validate(K.LinkRequest(canonical_id="fin_je_1", dimension="vehicle", role="payment", target_type="vehicle", target_id="v1", amount=Decimal("1"),
                                  currency="SAR", settled_amount=Decimal("1"), settled_currency="USD", fx={"rate": "1"}))
    assert refused.value.code == "settled_not_allowed"


# -- through the API, in-memory stores ----------------------------------------------------------------------------


@pytest.fixture
def clean() -> Iterator[None]:
    for store in (finance_store, sms_memory_store, links_memory_store, rules_memory_store):
        store.clear()
    svc._last_notified.clear()
    yield
    for store in (finance_store, sms_memory_store, links_memory_store, rules_memory_store):
        store.clear()


def today() -> date:
    return today_in(FinanceSettings())


def setup(headers: dict = OWNER) -> dict[str, Any]:
    bank = client.post(f"{API}/accounts", headers=headers, json={"name": "Current", "kind": "bank", "currency": "SAR", "last4": "5678"})
    assert bank.status_code == 201, bank.text
    assert client.post(f"{API}/sms/self-test", headers=headers).json()["passed"]
    version = client.get(f"{API}/sms/settings", headers=headers).json()["version"]
    put = client.put(f"{API}/sms/settings", headers=headers, json={
        "version": version, "enabled": True, "extra_senders": [{"sender": SENDER, "bank": "test_bank"}], "verified_senders": [SENDER],
        "account_map": [{"last4": "5678", "account_id": bank.json()["id"]}]})
    assert put.status_code == 200, put.text
    device = client.post(f"{API}/sms/devices", headers=headers, json={"name": "My iPhone"})
    assert device.status_code == 201
    return {"bank": bank.json(), "token": device.json()["token"]}


def send(token: str, message: str) -> dict:
    response = client.post(f"{API}/sms/ingest", headers={"Authorization": f"Bearer {token}"}, json={"message": message, "sender": SENDER})
    assert response.status_code == 200, response.text
    return response.json()


def usd_order(amount: str = "100", headers: dict = OWNER) -> dict:
    due = today()
    entry = {"provider": "Tamara", "order_id": "ORD10001", "merchant": "Synthetic Shop", "purchase_on": (due - timedelta(days=30)).isoformat(),
             "due_on": due.isoformat(), "installment": 2, "total_installments": 4, "amount": amount, "currency": "USD", "source_status": "scheduled"}
    assert client.post(f"{API}/installment-statement/sync", headers=headers, json={"entries": [entry]}).status_code == 200
    bill = client.post(f"{API}/bills", headers=headers, json={
        "name": "Tamara: Synthetic Shop 2/4", "amount": amount, "currency": "USD", "cadence": "once", "next_due_on": due.isoformat(),
        "notes": "Order: ORD10001 | Installment: 2 | Provider-reported scheduled; no bank match confirmed."})
    assert bill.status_code == 201, bill.text
    return bill.json()


def approve(bank_id: str, headers: dict = OWNER) -> None:
    response = client.put(f"{API}/automation/matching", headers=headers, json={
        "match_transactions": True, "reconcile_installments": True, "link_recurring_bills": True, "source_account_ids": [bank_id], "approve": True})
    assert response.status_code == 200, response.text


def records(headers: dict = OWNER, **params: str) -> list[dict]:
    response = client.get(f"{API}/matching/records", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def occurrence(bill_id: str) -> dict:
    day = today()
    rows = client.get(f"{API}/bill-schedule?from={(day - timedelta(days=40)).isoformat()}&to={(day + timedelta(days=40)).isoformat()}",
                      headers=OWNER).json()["items"]
    return next(r for r in rows if r["bill_id"] == bill_id)


def live_links() -> list[Any]:
    return [l for l in links_memory_store.links.values() if l.status != LinkStatus.VOID]


def expenses() -> dict[str, Decimal]:
    board = client.get(f"{API}/ledger/dashboard", headers=OWNER, params={"month": today().strftime("%Y-%m")}).json()
    return {r["currency"]: Decimal(str(r["amount"])) for r in board["expenses"]}


def test_a_usd_purchase_from_a_sar_card_pays_the_usd_installment_and_is_counted_once(clean: None) -> None:
    s = setup()
    bill = usd_order()
    approve(s["bank"]["id"])
    posted = send(s["token"], "SYNTHETIC. Purchase USD 100.00 (SAR 375.00) at TAMARA card **5678")
    assert posted["status"] == "posted", posted
    made = records(outcome="auto_linked")
    assert len(made) == 1 and made[0]["reason_code"] == "fx_original_amount" and made[0]["evidence_state"] == "verified"
    assert (Decimal(made[0]["amount"]), made[0]["currency"]) == (Decimal("100"), "USD")
    assert made[0]["fx"]["original_amount"] == "100.00" and made[0]["fx"]["bank_currency"] == "SAR"
    assert Decimal(made[0]["fx"]["bank_amount"]) == Decimal("375") and made[0]["fx"]["rate_source"] == "sms"
    [link] = live_links()
    assert (link.amount, link.currency, link.settled_amount, link.settled_currency) == (Decimal("375.00"), "SAR", Decimal("100.00"), "USD")
    row = occurrence(bill["id"])
    assert row["status"] == "paid" and Decimal(row["paid_amount"]) == Decimal("100") and row["currency"] == "USD"
    status = client.get(f"{API}/matching/installments", headers=OWNER).json()["orders"][0]
    assert status["installments"][0]["status"] == "verified_paid" and Decimal(status["still_due"]) == 0
    # The payment is spending once, in the account's currency; paying the installment adds nothing on top.
    assert expenses() == {"SAR": Decimal("375")}
    shown = client.get(f"{API}/links", headers=OWNER, params={"canonical_id": link.canonical_id}).json()["items"][0]
    assert shown["settled_currency"] == "USD" and shown["fx"]["original_currency"] == "USD"
    # The transaction's FX details show what it paid in the installment's currency and the bank side it used.
    txn = next(iter(finance_store.transactions.values()))
    detail = client.get(f"{API}/transactions/{txn.id}/fx", headers=OWNER).json()
    assert detail["original_amount"] == "100.00" and detail["original_currency"] == "USD"
    [settles] = detail["settles"]
    assert (settles["settled_amount"], settles["settled_currency"], settles["bank_currency"], settles["basis"]) == ("100.00", "USD", "SAR", "bank_figures")
    assert settles["label"] == "Tamara: Synthetic Shop 2/4" and settles["dimension"] == "bill"
    # A rerun links nothing again.
    assert client.post(f"{API}/matching/run", headers=OWNER).json()["counts"]["auto_linked"] == 0 and len(live_links()) == 1


def test_without_the_original_amount_it_waits_and_the_owner_confirms_it(clean: None) -> None:
    s = setup()
    bill = usd_order()
    approve(s["bank"]["id"])
    send(s["token"], "SYNTHETIC. Purchase of SAR 375.00 at TAMARA with card **5678")
    waiting = records(outcome="review")
    assert len(waiting) == 1 and waiting[0]["reason_code"] == "fx_evidence_missing" and waiting[0]["fx"]["basis"] == "missing"
    assert not live_links() and occurrence(bill["id"])["status"] != "paid"
    accepted = client.post(f"{API}/matching/records/{waiting[0]['id']}/accept", headers=OWNER, json={"version": waiting[0]["version"]})
    assert accepted.status_code == 200, accepted.text
    [link] = live_links()
    assert (link.amount, link.currency, link.settled_amount, link.settled_currency) == (Decimal("375.00"), "SAR", Decimal("100.00"), "USD")
    assert link.fx is not None and link.fx["basis"] == "owner_confirmed" and link.fx["rate"] is None  # no rate recorded or guessed
    assert Decimal(occurrence(bill["id"])["paid_amount"]) == Decimal("100")


def test_a_part_payment_across_currencies_shows_partly_paid_once_accepted(clean: None) -> None:
    s = setup()
    usd_order()
    approve(s["bank"]["id"])
    send(s["token"], "SYNTHETIC. Purchase USD 60.00 (SAR 225.00) at TAMARA card **5678")
    waiting = records(outcome="review")
    assert len(waiting) == 1 and waiting[0]["reason_code"] == "partial_amount" and Decimal(waiting[0]["amount"]) == Decimal("60")
    assert client.post(f"{API}/matching/records/{waiting[0]['id']}/accept", headers=OWNER, json={}).status_code == 200
    row = client.get(f"{API}/matching/installments", headers=OWNER).json()["orders"][0]["installments"][0]
    assert row["status"] == "partly_paid" and Decimal(row["still_due"]) == Decimal("40")
    # The whole SAR purchase is used: nothing of it can pay another bill in SAR on top.
    assert live_links()[0].amount == Decimal("225.00")


def test_records_and_links_across_currencies_stay_the_owners(clean: None) -> None:
    s = setup()
    usd_order()
    approve(s["bank"]["id"])
    send(s["token"], "SYNTHETIC. Purchase USD 100.00 (SAR 375.00) at TAMARA card **5678")
    [made] = records(outcome="auto_linked")
    for headers in (OTHER, OTHER_TENANT):
        assert records(headers) == []
        assert client.get(f"{API}/links", headers=headers).json()["total"] == 0
        assert client.post(f"{API}/matching/records/{made['id']}/undo", headers=headers, json={}).status_code == 404
    # The other tenant's own run never sees this payment.
    other = setup(OTHER_TENANT)
    approve(other["bank"]["id"], OTHER_TENANT)
    assert client.post(f"{API}/matching/run", headers=OTHER_TENANT).json()["counts"]["transactions"] == 0
    undone = client.post(f"{API}/matching/records/{made['id']}/undo", headers=OWNER, json={"version": made["version"]})
    assert undone.status_code == 200 and not live_links() and len(sms_memory_store.entries) >= 1


def test_a_statement_row_with_an_included_markup_links_the_purchase_and_leaves_the_fee_apart(clean: None) -> None:
    s = setup()
    bill = usd_order()
    approve(s["bank"]["id"])
    content = f"Date,Description,Amount\n{today().isoformat()},TAMARA PAYMENT USD 100.00 markup 2.5%,-384.38\n"
    imported = client.post(f"{API}/imports", headers=OWNER, json={"account_id": s["bank"]["id"], "content": content, "filename": "nov.csv"})
    assert imported.status_code == 201, imported.text
    client.post(f"{API}/matching/run", headers=OWNER)
    [made] = records(outcome="auto_linked")
    assert made["fx"]["fee_source"] == "markup_stated" and "fx_fee" in {e["code"] for e in made["evidence"]}
    fee = Decimal(made["fx"]["fee_amount"])
    assert fee > 0 and Decimal(made["fx"]["purchase_amount"]) + fee == Decimal("384.38")
    [link] = live_links()
    # The installment is paid with the purchase; the bank's markup is not part of it and stays on the payment, unlinked.
    assert (link.amount, link.currency, link.settled_amount) == (Decimal("384.38") - fee, "SAR", Decimal("100.00"))
    assert Decimal(occurrence(bill["id"])["paid_amount"]) == Decimal("100")
    remaining = client.get(f"{API}/links/transactions/{link.canonical_id}", headers=OWNER)
    assert remaining.status_code == 200, remaining.text
    # Linking wrote no transaction: the account still holds the one row the bank billed.
    rows = client.get(f"{API}/transactions", headers=OWNER, params={"account_id": s["bank"]["id"], "limit": 100}).json()["items"]
    assert [Decimal(r["amount"]) for r in rows] == [Decimal("-384.38")]
