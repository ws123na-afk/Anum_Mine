"""Refunds of a Business invoice's card payment started from inside ANUM (docs/business.md#refunds-from-anum) and
refunds the provider reported for a payment ANUM never recorded (docs/business.md#refunds-anum-could-not-match).

The provider's HTTP is faked (``FakeStripe``, ``FakeMoyasar``); approvals go through the ordinary approvals flow with
the payload hash the approver was shown.
"""

from __future__ import annotations

from typing import Any

import pytest

from anum_api.business.store import Query, business_store

from test_business import BASE, CONTEXT as CONTEXT_OF_TEST, MEMBER, OTHER_TENANT, OWNER, VIEWER, approve, ok
from test_business_invoice_refunds import invoice, moyasar_refund_event, paid_by_stripe, stripe_refund_event
from test_business_invoice_send import (  # noqa: F401 - fixtures
    arabic_invoice,
    clean,
    client,
    fake_moyasar,
    fake_stripe,
    turn_on_moyasar,
    webhook,
)
from test_selling_readiness import FakeMoyasar
from test_stripe_payments import FakeStripe


def ask(invoice_id: str, payment_id: str, amount: float, key: str = "refund-key-0001", headers: dict[str, str] = MEMBER, reason: str = "Returned item") -> Any:
    return client.post(f"{BASE}/invoices/{invoice_id}/payments/{payment_id}/refund-requests", headers={**headers, "Idempotency-Key": key},
                       json={"amount": amount, "reason": reason})


def approval_of(approval_id: str) -> dict[str, Any]:
    return ok(client.get(f"/api/v1/approvals/{approval_id}", headers=OWNER))


def test_a_refund_waits_for_an_owner_then_refunds_through_stripe_once(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    view = invoice(sent["id"])
    payment = view["payments"][0]
    assert view["can"]["refund"] is True and view["refundable"] == {payment["id"]: 1050}
    answer = ok(ask(sent["id"], payment["id"], 300), 201)
    request = answer["request"]
    assert request["status"] == "waiting_approval" and request["payload_hash"] and request["amount"] == 300
    # Nothing moved yet; the amount is held.
    assert not [r for r in fake_stripe.refunds if r["id"].startswith("re_") and not r["id"].startswith("re_dash")]
    assert answer["invoice"]["refundable"] == {payment["id"]: 750} and answer["invoice"]["amount_paid"] == 1050
    # The same form again is the same request; another amount with the same key is refused.
    assert ok(ask(sent["id"], payment["id"], 300), 200)["request"]["id"] == request["id"]
    assert ask(sent["id"], payment["id"], 200).status_code == 409
    # Above what is left (1050 charged, 300 held): refused with the figure.
    above = ask(sent["id"], payment["id"], 800, key="refund-key-0002")
    assert above.status_code == 422 and "750" in above.text
    approval = approval_of(request["approval_id"])
    assert approval["action"] == "business.refund_card_payment" and approval["arguments"]["amount"] == "300.00"
    assert approve(approval, MEMBER).status_code == 403  # members ask, owners decide
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": "0" * 64}).status_code >= 400
    assert approve(approval).status_code == 200
    made = [r for r in fake_stripe.refunds if not r["id"].startswith("re_dash")]
    assert len(made) == 1 and made[0]["amount"] == 30000
    posted = fake_stripe.calls("POST", "/v1/refunds")
    assert posted[-1].headers["Idempotency-Key"] == f"anum-binref-{request['id']}"
    view = invoice(sent["id"])
    [refund] = view["refunds"]
    assert refund["provider_refund_id"] == made[0]["id"] and refund["amount"] == 300 and "from ANUM" in refund["note"]
    assert refund["recorded_by"] == "owner_biz"
    assert view["status"] == "sent" and view["amount_paid"] == 750 and view["refund_requests"][0]["status"] == "succeeded"
    assert view["refund_requests"][0]["refund_id"] == refund["id"]
    # Stripe's webhook for the same refund afterwards counts nothing twice.
    assert ok(stripe_refund_event("evt_after_anum", "pi_ref_1", kind="refund.created"))["outcome"] == "unchanged"
    assert invoice(sent["id"])["amount_paid"] == 750 and len(invoice(sent["id"])["refunds"]) == 1


def test_a_pending_stripe_refund_is_recorded_by_its_webhook_and_a_failed_one_frees_the_amount(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment = invoice(sent["id"])["payments"][0]
    fake_stripe.refund_status = "pending"
    request = ok(ask(sent["id"], payment["id"], 1050), 201)["request"]
    assert approve(approval_of(request["approval_id"])).status_code == 200
    view = invoice(sent["id"])
    assert view["refund_requests"][0]["status"] == "pending_provider" and view["refunds"] == [] and view["refundable"] == {}
    made = next(r for r in fake_stripe.refunds if not r["id"].startswith("re_dash"))
    made["status"] = "succeeded"
    assert ok(stripe_refund_event("evt_done", "pi_ref_1", kind="refund.updated"))["outcome"] == "refund_recorded"
    view = invoice(sent["id"])
    assert view["refund_requests"][0]["status"] == "succeeded" and view["amount_paid"] == 0 and "from ANUM" in view["refunds"][0]["note"]


def test_a_pending_stripe_refund_that_fails_records_nothing_and_frees_the_amount(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent2, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment2 = invoice(sent2["id"])["payments"][0]
    fake_stripe.refund_status = "pending"
    request2 = ok(ask(sent2["id"], payment2["id"], 100, key="refund-key-0003"), 201)["request"]
    assert approve(approval_of(request2["approval_id"])).status_code == 200
    second = next(r for r in fake_stripe.refunds if not r["id"].startswith("re_dash"))
    second["status"] = "failed"
    ok(stripe_refund_event("evt_fail", "pi_ref_1", kind="refund.failed"))
    view2 = invoice(sent2["id"])
    assert view2["refund_requests"][0]["status"] == "failed" and view2["refunds"] == [] and view2["refundable"] == {payment2["id"]: 1050}


def test_a_moyasar_refund_from_anum_is_recorded_by_its_running_total_and_the_webhook_adds_nothing(fake_moyasar: FakeMoyasar, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_on_moyasar(monkeypatch)
    sent = arabic_invoice()
    link = ok(client.post(f"{BASE}/invoices/{sent['id']}/payment-link", headers=MEMBER), 201)["payment_link"]
    fake_moyasar.pay(link["provider_invoice_id"], payment_id="pay_anum_1")
    assert ok(webhook("pay_anum_1", "evt_paid"))["outcome"] == "paid"
    view = invoice(sent["id"])
    payment = view["payments"][0]
    total = payment["provider_amount"] or payment["amount"]
    request = ok(ask(sent["id"], payment["id"], 150), 201)["request"]
    assert approve(approval_of(request["approval_id"])).status_code == 200
    assert fake_moyasar.payments["pay_anum_1"]["refunded"] == 15000
    view = invoice(sent["id"])
    [refund] = view["refunds"]
    assert refund["provider_refund_id"] == "pay_anum_1:15000" and refund["provider_amount"] == 150
    assert view["refund_requests"][0]["status"] == "succeeded" and view["refundable"] == {payment["id"]: total - 150}
    # Moyasar's webhook for the same total records nothing more.
    assert ok(moyasar_refund_event("pay_anum_1", "evt_refunded"))["outcome"] == "unchanged"
    assert len(invoice(sent["id"])["refunds"]) == 1
    # Moyasar refusing a refund marks the request failed and records nothing.
    fake_moyasar.fail_refund = True
    request2 = ok(ask(sent["id"], payment["id"], 10, key="refund-key-0004"), 201)["request"]
    assert approve(approval_of(request2["approval_id"])).status_code == 200
    view = invoice(sent["id"])
    assert view["refund_requests"][0]["status"] == "failed" and "Moyasar" in view["refund_requests"][0]["detail"] and len(view["refunds"]) == 1


def test_rules_permissions_withdrawal_and_rejection(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment = invoice(sent["id"])["payments"][0]
    assert ask(sent["id"], payment["id"], 10, headers=VIEWER).status_code == 403
    assert ask(sent["id"], payment["id"], 10, headers=OTHER_TENANT).status_code in (403, 404)
    assert ask(sent["id"], "binpay_missing", 10).status_code == 404
    assert client.post(f"{BASE}/invoices/{sent['id']}/payments/{payment['id']}/refund-requests", headers=MEMBER, json={"amount": 10, "reason": "x y"}).status_code == 422
    # Withdraw: frees the amount and lapses the approval.
    request = ok(ask(sent["id"], payment["id"], 500), 201)["request"]
    assert client.post(f"{BASE}/invoices/{sent['id']}/refund-requests/{request['id']}/cancel", headers=VIEWER).status_code == 403
    withdrawn = ok(client.post(f"{BASE}/invoices/{sent['id']}/refund-requests/{request['id']}/cancel", headers=MEMBER))
    assert withdrawn["refund_requests"][0]["status"] == "cancelled" and withdrawn["refundable"] == {payment["id"]: 1050}
    assert approve(approval_of(request["approval_id"])).status_code >= 400
    # Rejected: the request closes and holds nothing.
    request2 = ok(ask(sent["id"], payment["id"], 500, key="refund-key-0005"), 201)["request"]
    assert client.post(f"/api/v1/approvals/{request2['approval_id']}/reject", headers=OWNER, json={"reason": "Not agreed"}).status_code == 200
    view = invoice(sent["id"])
    assert view["refund_requests"][0]["status"] == "rejected" and "Not agreed" in view["refund_requests"][0]["detail"]
    assert view["refundable"] == {payment["id"]: 1050}
    assert all(r["id"].startswith("re_dash") for r in fake_stripe.refunds)  # nothing reached Stripe


def test_a_dashboard_refund_between_request_and_approval_stops_an_over_refund(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment = invoice(sent["id"])["payments"][0]
    request = ok(ask(sent["id"], payment["id"], 1000), 201)["request"]
    fake_stripe.dashboard_refund("pi_ref_1", 50000)
    ok(stripe_refund_event("evt_between", "pi_ref_1"))
    assert approve(approval_of(request["approval_id"])).status_code == 200
    view = invoice(sent["id"])
    assert view["refund_requests"][0]["status"] == "failed" and "550" in view["refund_requests"][0]["detail"]
    assert len(view["refunds"]) == 1 and not [r for r in fake_stripe.refunds if not r["id"].startswith("re_dash")]


def test_a_refund_of_a_payment_anum_never_recorded_waits_for_an_owner_to_check(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, metadata = paid_by_stripe(fake_stripe, monkeypatch)
    fake_stripe.intent("pi_never_paid", 105000, "aed", metadata)
    fake_stripe.dashboard_refund("pi_never_paid", 2500)
    assert ok(stripe_refund_event("evt_unknown", "pi_never_paid"))["outcome"] == "refund_needs_review"
    assert ok(stripe_refund_event("evt_unknown_2", "pi_never_paid", kind="refund.updated"))["outcome"] == "ignored"  # kept once
    assert invoice(sent["id"])["refunds"] == [] and invoice(sent["id"])["amount_paid"] == 1050
    page = ok(client.get(f"{BASE}/refund-reviews", headers=MEMBER))
    [review] = page["items"]
    assert page["total"] == 1 and review["provider"] == "stripe" and review["amount"] == 25 and review["currency"] == "AED"
    assert review["invoice_id"] == sent["id"] and review["provider_payment_id"] == "pi_never_paid" and review["status"] == "open"
    assert client.get(f"{BASE}/refund-reviews", headers=VIEWER).status_code == 403
    assert ok(client.get(f"{BASE}/refund-reviews", headers=OTHER_TENANT))["items"] == []
    assert client.post(f"{BASE}/refund-reviews/{review['id']}/check", headers=MEMBER, json={"note": "Checked"}).status_code == 403
    checked = ok(client.post(f"{BASE}/refund-reviews/{review['id']}/check", headers=OWNER, json={"note": "A test payment — fine"}))
    assert checked["status"] == "checked" and checked["checked_by"] == "owner_biz" and "—" not in checked["note"]
    assert ok(client.get(f"{BASE}/refund-reviews", headers=MEMBER))["total"] == 0
    assert ok(client.get(f"{BASE}/refund-reviews?state=checked", headers=MEMBER))["total"] == 1
    reopened = ok(client.post(f"{BASE}/refund-reviews/{review['id']}/reopen", headers=OWNER))
    assert reopened["status"] == "open" and reopened["checked_at"] is None
    assert client.post(f"{BASE}/refund-reviews/binrev_missing/check", headers=OWNER, json={}).status_code == 404


def _race(monkeypatch: pytest.MonkeyPatch, payment_id: str, invoice_id: str, amount: str) -> None:
    """Another submit with the same key is written between this request's key check and its insert."""
    from datetime import datetime, timezone
    from decimal import Decimal

    from anum_api.business import invoice_refund_requests as module
    from anum_api.business.invoice_models import Currency, RefundRequest
    from test_business import CONTEXT

    real = module._provider_or_refusal

    def winner_first(tenant_id: str, provider: str) -> Any:
        adapter = real(tenant_id, provider)
        now = datetime.now(timezone.utc)
        business_store.scoped(CONTEXT).add("refund_requests", RefundRequest(
            id="binrfq_winner", tenant_id=CONTEXT.tenant_id, workspace_id=CONTEXT.workspace_id, invoice_id=invoice_id, payment_id=payment_id,
            provider="stripe", currency=Currency.AED, amount=Decimal(amount), reason="Returned item", idempotency_key="refund-race-0001",
            requested_by="member_biz", created_at=now, updated_at=now))
        return adapter

    monkeypatch.setattr(module, "_provider_or_refusal", winner_first)


def test_two_submits_with_one_key_at_once_answer_the_first_request_not_a_500(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment = invoice(sent["id"])["payments"][0]
    _race(monkeypatch, payment["id"], sent["id"], "300")
    answer = ok(ask(sent["id"], payment["id"], 300, key="refund-race-0001"), 200)
    assert answer["request"]["id"] == "binrfq_winner"
    # The loser's planned approval lapsed; only the winner's request exists.
    rows, _ = business_store.scoped(CONTEXT_OF_TEST).find("refund_requests", Query(where={"idempotency_key": "refund-race-0001"}, limit=10))
    assert [row.id for row in rows] == ["binrfq_winner"]
    planned = [a["status"] for a in ok(client.get("/api/v1/approvals", headers=OWNER)) if a.get("action") == "business.refund_card_payment"]
    assert planned and "pending" not in planned


def test_two_submits_with_one_key_for_different_refunds_answer_409(fake_stripe: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> None:
    sent, _ = paid_by_stripe(fake_stripe, monkeypatch)
    payment = invoice(sent["id"])["payments"][0]
    _race(monkeypatch, payment["id"], sent["id"], "500")
    refused = ask(sent["id"], payment["id"], 300, key="refund-race-0001")
    assert refused.status_code == 409 and "different refund" in refused.text
