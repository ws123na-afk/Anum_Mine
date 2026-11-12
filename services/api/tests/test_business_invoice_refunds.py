"""Refunds made in the Stripe or Moyasar dashboard on a Business invoice's card payment
(docs/business.md#refunds-made-in-the-provider-dashboard).

The provider's HTTP is faked (``FakeStripe``, ``FakeMoyasar``); the webhooks are signed (Stripe) or carry the
secret token (Moyasar) exactly as the providers send them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from anum_api.business.books_posting import Chart, propose
from anum_api.business.books_report import Book
from anum_api.business.books_service import gather
from anum_api.business.store import Query, business_store
from anum_api.billing import providers
from anum_api.schemas import TenantContext, utc_now

from test_business import BASE, CONTEXT, MEMBER, OTHER_TENANT, OWNER, TENANT, ok
from test_business_invoice_send import (  # noqa: F401 - fixtures
    aed_invoice,
    arabic_invoice,
    clean,
    client,
    fake_moyasar,
    fake_stripe,
    pay,
    stripe_session,
    turn_on_moyasar,
    turn_on_stripe,
    webhook,
)
from test_selling_readiness import WEBHOOK_TOKEN, FakeMoyasar
from test_stripe_payments import WEBHOOK_SECRET as STRIPE_WEBHOOK_SECRET
from test_stripe_payments import FakeStripe


def stripe_refund_event(event_id: str, payment_intent: str, kind: str = "charge.refunded") -> Any:
    """A signed refund delivery: only the payment id is read from it; the refunds come from Stripe's API."""
    payload = json.dumps({"id": event_id, "type": kind, "data": {"object": {"id": f"ch_{payment_intent}", "payment_intent": payment_intent,
                                                                             "amount_refunded": 999999}}}).encode()
    header = providers.sign_stripe_payload(payload, STRIPE_WEBHOOK_SECRET, int(utc_now().timestamp()))
    return client.post(f"/api/v1/billing/webhooks/stripe/{TENANT}", content=payload, headers={"stripe-signature": header})


def moyasar_refund_event(payment_id: str, event_id: str) -> Any:
    return client.post(f"/api/v1/billing/webhooks/moyasar/{TENANT}", content=json.dumps({
        "id": event_id, "type": "payment_refunded", "secret_token": WEBHOOK_TOKEN,
        "data": {"id": payment_id, "status": "refunded", "refunded": 999999}}).encode())


def paid_by_stripe(fake: FakeStripe, monkeypatch: pytest.MonkeyPatch, payment_intent: str = "pi_ref_1", amount: int = 105000) -> tuple[dict[str, Any], dict[str, str]]:
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    metadata = dict(fake.links[link["provider_invoice_id"]]["metadata"])
    fake.intent(payment_intent, amount, "aed", metadata)
    assert ok(stripe_session(link, f"evt_paid_{payment_intent}", payment_intent, amount, metadata))["outcome"] == "paid"
    return sent, metadata


def invoice(invoice_id: str) -> dict[str, Any]:
    return ok(client.get(f"{BASE}/invoices/{invoice_id}", headers=MEMBER))


def test_a_stripe_dashboard_refund_is_recorded_once_against_the_invoice_and_its_payment(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    assert invoice(sent["id"])["status"] == "paid"
    fake_stripe.dashboard_refund("pi_ref_1", 30000)
    assert ok(stripe_refund_event("evt_r1", "pi_ref_1"))["outcome"] == "refund_recorded"
    # The same delivery again, and another event about the same refund, record nothing more.
    assert ok(stripe_refund_event("evt_r1", "pi_ref_1"))["duplicate"] is True
    assert ok(stripe_refund_event("evt_r1b", "pi_ref_1", kind="refund.updated"))["outcome"] == "unchanged"
    view = invoice(sent["id"])
    assert view["status"] == "sent" and view["amount_paid"] == 750 and view["balance"] == 300 and view["paid_at"] is None
    [refund] = view["refunds"]
    payment = view["payments"][0]
    assert refund["payment_id"] == payment["id"] and refund["provider"] == "stripe" and refund["provider_refund_id"] == "re_dash_1"
    assert refund["provider_amount"] == 300 and refund["amount"] == 300 and refund["status"] == "recorded" and refund["currency"] == "AED"
    # A second, partial refund later: both count, never more than was paid.
    fake_stripe.dashboard_refund("pi_ref_1", 75000)
    assert ok(stripe_refund_event("evt_r2", "pi_ref_1"))["outcome"] == "refund_recorded"
    assert invoice(sent["id"])["amount_paid"] == 0
    activities, _ = business_store.scoped(CONTEXT).find("activities", Query(where={"kind": "invoice_refund_recorded"}, order_by="created_at", limit=10))
    texts = [item.summary for item in activities]
    assert any("Stripe refunded AED 300.00" in text and "credit note" in text for text in texts)


def test_the_payment_with_refunds_cannot_be_removed_and_a_credit_note_settles_the_balance(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anum_api.business.api_invoice_money.settings.business_credit_notes_enabled", True)
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    fake_stripe.dashboard_refund("pi_ref_1", 105000)
    ok(stripe_refund_event("evt_full", "pi_ref_1"))
    view = invoice(sent["id"])
    refused = client.delete(f"{BASE}/invoices/{sent['id']}/payments/{view['payments'][0]['id']}", headers=OWNER)
    assert refused.status_code == 409 and "refunds" in refused.text
    settled = ok(client.post(f"{BASE}/invoices/{sent['id']}/credit-notes", headers=OWNER, json={"amount": 1050, "reason": "Refunded in full"}), 201)
    assert settled["status"] == "paid" and settled["balance"] == 0


def test_a_failed_stripe_refund_is_reversed_and_pending_ones_wait(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    refund = fake_stripe.dashboard_refund("pi_ref_1", 20000)
    refund["status"] = "pending"
    assert ok(stripe_refund_event("evt_p", "pi_ref_1", kind="refund.created"))["outcome"] == "unchanged"
    assert invoice(sent["id"])["refunds"] == []
    refund["status"] = "succeeded"
    assert ok(stripe_refund_event("evt_s", "pi_ref_1", kind="refund.updated"))["outcome"] == "refund_recorded"
    assert invoice(sent["id"])["amount_paid"] == 850
    refund["status"] = "failed"
    assert ok(stripe_refund_event("evt_f", "pi_ref_1", kind="refund.failed"))["outcome"] == "refund_reversed"
    view = invoice(sent["id"])
    assert view["status"] == "paid" and view["amount_paid"] == 1050 and view["refunds"][0]["status"] == "reversed"


def test_refunding_what_the_card_took_above_the_balance_takes_nothing_off(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    metadata = dict(fake_stripe.links[link["provider_invoice_id"]]["metadata"])
    ok(pay(sent["id"], 50), 201)  # a bank transfer arrives first; the old link still takes 1050 by card
    fake_stripe.intent("pi_over", 105000, "aed", metadata)
    assert ok(stripe_session(link, "evt_over", "pi_over", 105000, metadata))["outcome"] == "paid"
    card = next(item for item in invoice(sent["id"])["payments"] if item["provider"] == "stripe")
    assert card["amount"] == 1000 and card["provider_amount"] == 1050
    fake_stripe.dashboard_refund("pi_over", 3000)  # part of the 50 surplus
    ok(stripe_refund_event("evt_o1", "pi_over"))
    view = invoice(sent["id"])
    assert view["status"] == "paid" and view["amount_paid"] == 1050 and view["refunds"][0]["amount"] == 0
    fake_stripe.dashboard_refund("pi_over", 10000)  # 20 more of the surplus, then 80 of the payment
    ok(stripe_refund_event("evt_o2", "pi_over"))
    view = invoice(sent["id"])
    assert view["amount_paid"] == 970 and [item["amount"] for item in view["refunds"]] == [0, 80]
    assert "above the balance" in view["refunds"][1]["note"]


def test_a_refund_for_another_tenant_or_an_unknown_payment_changes_nothing(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, metadata = paid_by_stripe(fake_stripe, monkeypatch)
    fake_stripe.intent("pi_other", 105000, "aed", {**metadata, "anum_tenant_id": "tenant_someone_else"})
    fake_stripe.dashboard_refund("pi_other", 1000)
    assert ok(stripe_refund_event("evt_x", "pi_other"))["outcome"] == "ignored"
    fake_stripe.intent("pi_never_paid", 105000, "aed", metadata)
    fake_stripe.dashboard_refund("pi_never_paid", 1000)
    assert ok(stripe_refund_event("evt_y", "pi_never_paid"))["outcome"] == "refund_needs_review"  # kept for an owner to check, nothing taken off
    assert invoice(sent["id"])["refunds"] == [] and invoice(sent["id"])["amount_paid"] == 1050
    # A delivery that does not verify is refused before anything is read.
    payload = json.dumps({"id": "evt_forged", "type": "charge.refunded", "data": {"object": {"payment_intent": "pi_ref_1"}}}).encode()
    forged = providers.sign_stripe_payload(payload, "whsec_wrong", int(utc_now().timestamp()))
    assert client.post(f"/api/v1/billing/webhooks/stripe/{TENANT}", content=payload, headers={"stripe-signature": forged}).status_code == 400
    assert client.get(f"{BASE}/invoices/{sent['id']}", headers=OTHER_TENANT).status_code == 404


def test_a_moyasar_dashboard_refund_is_recorded_from_its_running_total(fake_moyasar: FakeMoyasar, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_moyasar(monkeypatch)
    sent = arabic_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    fake_moyasar.pay(link["provider_invoice_id"], payment_id="pay_ref_1")
    assert ok(webhook("pay_ref_1", "evt_paid"))["outcome"] == "paid"
    fake_moyasar.payments["pay_ref_1"]["refunded"] = 15000
    assert ok(moyasar_refund_event("pay_ref_1", "evt_r1"))["outcome"] == "refund_recorded"
    assert ok(moyasar_refund_event("pay_ref_1", "evt_r1"))["duplicate"] is True
    assert ok(moyasar_refund_event("pay_ref_1", "evt_r1_again"))["outcome"] == "unchanged"  # the same total, another event id
    fake_moyasar.payments["pay_ref_1"]["refunded"] = 115000
    assert ok(moyasar_refund_event("pay_ref_1", "evt_r2"))["outcome"] == "refund_recorded"
    view = invoice(sent["id"])
    assert [item["provider_amount"] for item in view["refunds"]] == [150, 1000] and view["amount_paid"] == 0 and view["status"] == "sent"
    assert {item["provider_refund_id"] for item in view["refunds"]} == {"pay_ref_1:15000", "pay_ref_1:115000"}
    # The customer's portal shows what was given back.
    contact = ok(client.get(f"{BASE}/accounts/{sent['account_id']}", headers=OWNER))["contacts"][0]
    portal = ok(client.post(f"{BASE}/portal/access", headers=OWNER, json={"account_id": sent["account_id"], "contact_id": contact["id"]}), 201)
    shown = ok(client.get(f"/api/v1/portal/invoices/{sent['id']}", headers={"authorization": f"Bearer {portal['token']}"}))
    assert [item["amount"] for item in shown["refunds"]] == [150, 1000]


def test_the_books_post_a_refund_and_the_cash_figures_count_it(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    fake_stripe.dashboard_refund("pi_ref_1", 30000)
    ok(stripe_refund_event("evt_books", "pi_ref_1"))
    current = ok(client.get(f"{BASE}/books/setup", headers=OWNER))["settings"]
    ok(client.put(f"{BASE}/books/settings", headers=OWNER, json={"base_currency": "AED", "country": "AE", "fiscal_year_start_month": 1, "basis": "accrual",
                                                               "tax_label": "VAT", "version": current["version"] if current else None}))
    context = TenantContext(tenant_id=CONTEXT.tenant_id, workspace_id=CONTEXT.workspace_id, user_id="owner_biz", roles=["owner"])
    inputs = gather(context)
    [refund] = inputs.refunds
    proposals = {item.event_key: item for item in propose(inputs, Chart())}
    posted = proposals[f"invoice_refund:{refund.id}"]
    assert posted.source_kind == "invoice_refund"
    legs = {(leg.account, leg.debit): leg.amount for leg in posted.legs}
    assert legs == {("key:ar", True): 300, ("key:bank", False): 300}
    # The cash basis sees it as money given back on its own day.
    book = Book(inputs)
    assert sorted(float(item.amount) for item in book.payments if item.invoice_id == sent["id"]) == [-300.0, 1050.0]
