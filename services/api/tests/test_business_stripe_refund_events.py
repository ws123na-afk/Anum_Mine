"""Stripe refund webhooks against Stripe's documented event shapes and semantics
(docs/business.md#refunds-made-in-the-provider-dashboard).

Covers what the audit of 15 Nov 2026 pinned or fixed: the ``Stripe-Signature`` rules (several ``v1`` values while a
secret is rolled, ``v0`` ignored, the 5 minute tolerance both ways), delivery out of order (the refund before the
payment it gives back), a refund from ANUM whose provider answer was lost, and a refund that succeeded and then
failed (the request, the invoice and the books). Ids are plainly fake and low entropy (gitleaks).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from anum_api.billing import providers
from anum_api.business.books_posting import Chart, propose
from anum_api.business.books_service import gather
from anum_api.schemas import TenantContext, utc_now

from test_business import BASE, CONTEXT, MEMBER, OWNER, TENANT, approve, ok
from test_business_invoice_refunds import invoice, paid_by_stripe
from test_business_invoice_send import (  # noqa: F401 - fixtures
    aed_invoice,
    clean,
    client,
    fake_stripe,
    stripe_session,
    turn_on_stripe,
)
from test_business_refund_requests import approval_of, ask
from test_stripe_payments import WEBHOOK_SECRET as STRIPE_WEBHOOK_SECRET
from test_stripe_payments import FakeStripe

SECRET = "whsec_example"


def _sign(secret: str, timestamp: int, payload: bytes) -> str:
    return hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()


# ----------------------------------------------------------------------------- Stripe-Signature


def test_a_signature_with_several_v1_values_verifies_with_any_current_secret() -> None:
    """While a webhook secret is rolled Stripe signs each delivery with every active secret, one ``v1`` per secret."""
    payload = b'{"id":"evt_example","type":"refund.updated"}'
    now = 1_800_000_000
    header = f"t={now},v1={_sign('whsec_old_example', now, payload)},v1={_sign(SECRET, now, payload)},v0={'0' * 64}"
    providers.verify_stripe_signature(payload, header, SECRET, now=now)
    providers.verify_stripe_signature(payload, header, "whsec_old_example", now=now)
    with pytest.raises(providers.SignatureError):
        providers.verify_stripe_signature(payload, header, "whsec_other_example", now=now)
    # A v0 (Stripe's test scheme) that matches is never enough on its own.
    only_v0 = f"t={now},v0={_sign(SECRET, now, payload)}"
    with pytest.raises(providers.SignatureError):
        providers.verify_stripe_signature(payload, only_v0, SECRET, now=now)


def test_the_signature_tolerance_holds_both_ways_and_the_body_is_signed_byte_for_byte() -> None:
    payload = b'{"id":"evt_example","type":"charge.refunded"}'
    now = 1_800_000_000
    header = f"t={now},v1={_sign(SECRET, now, payload)}"
    providers.verify_stripe_signature(payload, header, SECRET, now=now + 300)
    providers.verify_stripe_signature(payload, header, SECRET, now=now - 300)
    for moment in (now + 301, now - 301):  # a replay after five minutes, a clock far ahead
        with pytest.raises(providers.SignatureError):
            providers.verify_stripe_signature(payload, header, SECRET, now=moment)
    with pytest.raises(providers.SignatureError):  # re-serialised JSON is another body
        providers.verify_stripe_signature(b'{"id": "evt_example", "type": "charge.refunded"}', header, SECRET, now=now)
    for bad in ("", "t=abc,v1=00", f"v1={_sign(SECRET, now, payload)}", f"t={now}", f"t=-{now},v1=00"):
        with pytest.raises(providers.SignatureError):
            providers.verify_stripe_signature(payload, bad, SECRET, now=now)


# ----------------------------------------------------------------------------- events shaped like Stripe's


def _deliver(event: dict[str, Any]) -> Any:
    payload = json.dumps(event).encode()
    header = providers.sign_stripe_payload(payload, STRIPE_WEBHOOK_SECRET, int(utc_now().timestamp()))
    return client.post(f"/api/v1/billing/webhooks/stripe/{TENANT}", content=payload, headers={"stripe-signature": header})


def refund_object_event(event_id: str, kind: str, refund: dict[str, Any]) -> dict[str, Any]:
    """``refund.created`` / ``refund.updated`` / ``refund.failed`` / ``charge.refund.updated``: ``data.object`` is a Refund."""
    return {"id": event_id, "object": "event", "api_version": "2024-06-20", "created": int(utc_now().timestamp()), "livemode": False,
            "pending_webhooks": 1, "request": {"id": None, "idempotency_key": None}, "type": kind,
            "data": {"object": {"object": "refund", "balance_transaction": "txn_example", "charge": "ch_example", "created": int(utc_now().timestamp()),
                                "destination_details": {"card": {"type": "refund"}, "type": "card"}, "reason": None, "receipt_number": None,
                                "source_transfer_reversal": None, "transfer_reversal": None, **refund},
                     "previous_attributes": {"status": "pending"}}}


def charge_refunded_event(event_id: str, payment_intent: str, amount: int, refunded: int, refunds: list[dict[str, Any]]) -> dict[str, Any]:
    """``charge.refunded``: ``data.object`` is the Charge, which no longer embeds its refunds unless expanded."""
    return {"id": event_id, "object": "event", "api_version": "2024-06-20", "created": int(utc_now().timestamp()), "livemode": False,
            "pending_webhooks": 1, "type": "charge.refunded",
            "data": {"object": {"id": "ch_example", "object": "charge", "amount": amount, "amount_captured": amount, "amount_refunded": refunded,
                                "currency": "aed", "captured": True, "paid": True, "refunded": refunded >= amount, "payment_intent": payment_intent,
                                "metadata": {}, "status": "succeeded",
                                "refunds": {"object": "list", "data": refunds, "has_more": False, "total_count": len(refunds)}}}}


def test_a_refund_delivered_before_its_payment_is_recorded_once_the_payment_arrives(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stripe does not promise order: the checkout event can be retried for hours while the owner already refunded."""
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    metadata = dict(fake_stripe.links[link["provider_invoice_id"]]["metadata"])
    fake_stripe.intent("pi_late_example", 105000, "aed", metadata)
    refund = fake_stripe.dashboard_refund("pi_late_example", 40000)
    first = _deliver(charge_refunded_event("evt_late_refund", "pi_late_example", 105000, 40000, [refund]))
    assert ok(first)["outcome"] == "refund_needs_review"
    assert ok(client.get(f"{BASE}/refund-reviews", headers=MEMBER))["total"] == 1
    # The payment arrives: recorded, then the refund Stripe holds for it is recorded too and the review closes itself.
    assert ok(stripe_session(link, "evt_late_paid", "pi_late_example", 105000, metadata))["outcome"] == "paid_then_refunded"
    view = invoice(sent["id"])
    assert view["amount_paid"] == 650 and view["status"] == "sent" and len(view["refunds"]) == 1
    assert view["refunds"][0]["provider_refund_id"] == refund["id"] and view["refunds"][0]["amount"] == 400
    assert ok(client.get(f"{BASE}/refund-reviews", headers=MEMBER))["total"] == 0
    [closed] = ok(client.get(f"{BASE}/refund-reviews?state=checked", headers=MEMBER))["items"]
    assert closed["checked_by"] == "system:stripe" and "once the payment arrived" in closed["note"]
    # A repeat of either delivery records nothing more.
    assert ok(stripe_session(link, "evt_late_paid", "pi_late_example", 105000, metadata))["duplicate"] is True
    assert ok(_deliver(refund_object_event("evt_late_again", "refund.updated", refund)))["outcome"] == "unchanged"
    assert invoice(sent["id"])["amount_paid"] == 650 and len(invoice(sent["id"])["refunds"]) == 1


def test_charge_refunded_with_several_refunds_and_reordered_refund_events(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch, payment_intent="pi_many_example")
    first = fake_stripe.dashboard_refund("pi_many_example", 10000)
    second = fake_stripe.dashboard_refund("pi_many_example", 20000)
    pending = fake_stripe.dashboard_refund("pi_many_example", 5000)
    pending["status"] = "pending"
    # refund.updated for the newest refund arrives before anything else: every finished refund is recorded from Stripe's list.
    assert ok(_deliver(refund_object_event("evt_many_upd", "refund.updated", second)))["outcome"] == "refund_recorded"
    assert ok(_deliver(refund_object_event("evt_many_created", "refund.created", first)))["outcome"] == "unchanged"
    assert ok(_deliver(charge_refunded_event("evt_many_charge", "pi_many_example", 105000, 35000, [pending, second, first])))["outcome"] == "unchanged"
    view = invoice(sent["id"])
    assert sorted(item["provider_refund_id"] for item in view["refunds"]) == sorted([first["id"], second["id"]]) and view["amount_paid"] == 750
    # The pending one is canceled: nothing was ever recorded for it, so nothing changes.
    pending["status"] = "canceled"
    assert ok(_deliver(refund_object_event("evt_many_cancel", "charge.refund.updated", pending)))["outcome"] == "unchanged"
    assert invoice(sent["id"])["amount_paid"] == 750 and len(invoice(sent["id"])["refunds"]) == 2


def test_a_refund_from_anum_whose_answer_was_lost_is_settled_by_the_webhook(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    """The refund reached Stripe but its answer did not: Stripe's webhook finds the request by the id in the refund's metadata."""
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment = invoice(sent["id"])["payments"][0]
    request = ok(ask(sent["id"], payment["id"], 300), 201)["request"]
    fake_stripe.drop_refund_answer = True
    approve(approval_of(request["approval_id"]))
    fake_stripe.drop_refund_answer = False
    held = invoice(sent["id"])
    assert held["refund_requests"][0]["status"] == "processing" and held["refundable"] == {payment["id"]: 750}
    made = next(r for r in fake_stripe.refunds if r["metadata"].get("anum_refund_request_id") == request["id"])
    assert ok(_deliver(refund_object_event("evt_lost_answer", "refund.created", made)))["outcome"] == "refund_recorded"
    view = invoice(sent["id"])
    [refund] = view["refunds"]
    assert refund["provider_refund_id"] == made["id"] and "from ANUM" in refund["note"]
    assert view["refund_requests"][0]["status"] == "succeeded" and view["refund_requests"][0]["refund_id"] == refund["id"]
    # The 300 is counted once: refunded, no longer also held by the request.
    assert view["amount_paid"] == 750 and view["refundable"] == {payment["id"]: 750}


def test_a_lost_answer_refund_that_fails_frees_the_request(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment = invoice(sent["id"])["payments"][0]
    request = ok(ask(sent["id"], payment["id"], 200), 201)["request"]
    fake_stripe.refund_status = "pending"
    fake_stripe.drop_refund_answer = True
    approve(approval_of(request["approval_id"]))
    fake_stripe.drop_refund_answer = False
    made = next(r for r in fake_stripe.refunds if r["metadata"].get("anum_refund_request_id") == request["id"])
    made["status"] = "failed"
    ok(_deliver(refund_object_event("evt_lost_failed", "refund.failed", made)))
    view = invoice(sent["id"])
    assert view["refund_requests"][0]["status"] == "failed" and view["refunds"] == [] and view["refundable"] == {payment["id"]: 1050}


def test_a_refund_that_succeeded_then_failed_is_reversed_everywhere(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment = invoice(sent["id"])["payments"][0]
    request = ok(ask(sent["id"], payment["id"], 300), 201)["request"]
    assert approve(approval_of(request["approval_id"])).status_code == 200
    assert invoice(sent["id"])["refund_requests"][0]["status"] == "succeeded"
    made = next(r for r in fake_stripe.refunds if r["metadata"].get("anum_refund_request_id") == request["id"])
    made["status"] = "failed"  # Stripe: a refund can fail after it succeeded (the card issuer refused the credit)
    assert ok(_deliver(refund_object_event("evt_after_success", "refund.failed", made)))["outcome"] == "refund_reversed"
    view = invoice(sent["id"])
    assert view["status"] == "paid" and view["amount_paid"] == 1050 and view["refunds"][0]["status"] == "reversed"
    assert view["refund_requests"][0]["status"] == "failed" and "after it had succeeded" in view["refund_requests"][0]["detail"]
    assert view["refundable"] == {payment["id"]: 1050}
    # A second failure event reverses nothing twice.
    assert ok(_deliver(refund_object_event("evt_after_success_2", "charge.refund.updated", made)))["outcome"] == "unchanged"
    assert invoice(sent["id"])["amount_paid"] == 1050
    # The books no longer propose the refund, so the posting run reverses an entry already posted for it.
    current = ok(client.get(f"{BASE}/books/setup", headers=OWNER))["settings"]
    ok(client.put(f"{BASE}/books/settings", headers=OWNER, json={"base_currency": "AED", "country": "AE", "fiscal_year_start_month": 1, "basis": "accrual",
                                                               "tax_label": "VAT", "version": current["version"] if current else None}))
    context = TenantContext(tenant_id=CONTEXT.tenant_id, workspace_id=CONTEXT.workspace_id, user_id="owner_biz", roles=["owner"])
    assert not [item for item in propose(gather(context), Chart()) if item.source_kind == "invoice_refund"]


def test_a_reviewed_refund_that_then_fails_at_stripe_closes_its_review(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refund kept for review (its payment had not arrived) that Stripe then reports failed gave nothing back."""
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    metadata = dict(fake_stripe.links[link["provider_invoice_id"]]["metadata"])
    fake_stripe.intent("pi_review_fail_example", 105000, "aed", metadata)
    refund = fake_stripe.dashboard_refund("pi_review_fail_example", 40000)
    assert ok(_deliver(refund_object_event("evt_review_made", "refund.created", refund)))["outcome"] == "refund_needs_review"
    assert ok(client.get(f"{BASE}/refund-reviews", headers=MEMBER))["total"] == 1
    refund["status"] = "failed"
    assert ok(_deliver(refund_object_event("evt_review_failed", "refund.failed", refund)))["outcome"] == "refund_review_closed"
    assert ok(client.get(f"{BASE}/refund-reviews", headers=MEMBER))["total"] == 0
    [closed] = ok(client.get(f"{BASE}/refund-reviews?state=checked", headers=MEMBER))["items"]
    assert closed["checked_by"] == "system:stripe" and "failed" in closed["note"] and "no money was given back" in closed["note"]
    # A repeat changes nothing, and the payment that then arrives is recorded whole: nothing was refunded.
    assert ok(_deliver(refund_object_event("evt_review_failed_2", "charge.refund.updated", refund)))["outcome"] == "ignored"
    assert ok(stripe_session(link, "evt_review_paid", "pi_review_fail_example", 105000, metadata))["outcome"] == "paid"
    view = invoice(sent["id"])
    assert view["status"] == "paid" and view["amount_paid"] == 1050 and view["refunds"] == []


def test_a_reviewed_refund_that_fails_after_the_payment_arrived_closes_too(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    """The payment arrives while the refund is pending again at Stripe (nothing recorded), then the refund is canceled."""
    turn_on_stripe(monkeypatch)
    sent = aed_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    metadata = dict(fake_stripe.links[link["provider_invoice_id"]]["metadata"])
    fake_stripe.intent("pi_review_late_example", 105000, "aed", metadata)
    refund = fake_stripe.dashboard_refund("pi_review_late_example", 40000)
    assert ok(_deliver(refund_object_event("evt_rl_made", "refund.created", refund)))["outcome"] == "refund_needs_review"
    refund["status"] = "pending"
    assert ok(stripe_session(link, "evt_rl_paid", "pi_review_late_example", 105000, metadata))["outcome"] == "paid"
    assert ok(client.get(f"{BASE}/refund-reviews", headers=MEMBER))["total"] == 1
    refund["status"] = "canceled"
    assert ok(_deliver(refund_object_event("evt_rl_cancel", "charge.refund.updated", refund)))["outcome"] == "refund_review_closed"
    assert ok(client.get(f"{BASE}/refund-reviews", headers=MEMBER))["total"] == 0
    assert invoice(sent["id"])["amount_paid"] == 1050
