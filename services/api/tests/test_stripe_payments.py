"""Stripe for a UAE company (docs/selling-readiness.md#stripe-uae) against a fake Stripe.

Covers refunds of recorded Stripe payments through Stripe's Refunds API (full and partial, the
Idempotency-Key, a replay after Stripe gave no answer, Stripe refusing, a refund Stripe holds as
pending), the refund webhooks (signature checked, duplicates, a pending refund confirmed or failed,
a refund made in the Stripe dashboard recorded once), AED in fils (and SAR and USD) on the
Checkout request, unsupported currencies and amounts under Stripe's minimum refused before Stripe
is called, permissions and tenant isolation, and the Ready to sell checklist and subprocessor list
with Stripe on and Moyasar off. Stripe is never called for real: every request goes to an
``httpx.MockTransport`` that stands in for api.stripe.com.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.billing import company, enforce, providers, signup
from anum_api.billing.rows import memory_db, open_billing
from anum_api.dependencies import memory_repository
from anum_api.organisations import memory_branding
from anum_api.settings import settings

client = TestClient(main.app)
ISSUER = "tenant_issuer"
OPERATOR = {"x-tenant-id": ISSUER, "x-workspace-id": "tenant_issuer_ws", "x-user-id": "operator_1", "x-user-roles": "owner,platform_operator"}
OTHER_OPERATOR = {"x-tenant-id": "tenant_issuer_two", "x-workspace-id": "ws_two", "x-user-id": "operator_2", "x-user-roles": "owner,platform_operator"}
# Plainly fake, low-entropy fixtures (gitleaks).
STRIPE_KEY = "sk_test_example"
WEBHOOK_SECRET = "whsec_example"
WEBHOOK_URL = f"/api/v1/billing/webhooks/stripe/{ISSUER}"


def owner(tenant_id: str, user: str = "owner_c", roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": f"{tenant_id}_main", "x-user-id": user, "x-user-roles": roles}


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json() if response.content else None


def form_of(request: httpx.Request) -> dict[str, str]:
    return {key: values[-1] for key, values in parse_qs(request.content.decode()).items()}


class FakeStripe:
    """A stand-in for api.stripe.com: Checkout sessions, prices, payment links, payment intents and refunds."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.intents: dict[str, dict[str, Any]] = {}
        self.refunds: list[dict[str, Any]] = []
        self.links: dict[str, dict[str, Any]] = {}
        self.by_key: dict[str, dict[str, Any]] = {}
        self.refund_status = "succeeded"
        self.refuse_refund = False
        self.drop_refund_answer = False  # the refund happens, but the answer never arrives

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def intent(self, payment_intent: str, amount: int, currency: str, metadata: dict[str, str]) -> None:
        self.intents[payment_intent] = {"id": payment_intent, "amount": amount, "currency": currency.lower(), "metadata": metadata}

    def dashboard_refund(self, payment_intent: str, amount: int) -> dict[str, Any]:
        refund = {"id": f"re_dash_{len(self.refunds) + 1}", "object": "refund", "payment_intent": payment_intent, "amount": amount,
                  "currency": self.intents[payment_intent]["currency"], "status": "succeeded", "metadata": {}}
        self.refunds.insert(0, refund)
        return refund

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {STRIPE_KEY}":
            return httpx.Response(401, json={"error": {"type": "invalid_request_error"}})
        path, key = request.url.path, request.headers.get("idempotency-key")
        if request.method == "POST" and key and key in self.by_key:
            return httpx.Response(200, json=self.by_key[key])  # Stripe answers a repeated key with the first result
        if request.method == "POST" and path == "/v1/checkout/sessions":
            body = {"id": f"cs_test_{len(self.requests)}", "url": f"https://checkout.stripe.example/cs_{len(self.requests)}"}
        elif request.method == "POST" and path == "/v1/prices":
            form = form_of(request)
            body = {"id": f"price_{len(self.requests)}", "unit_amount": int(form["unit_amount"]), "currency": form["currency"]}
        elif request.method == "POST" and path == "/v1/payment_links":
            form = form_of(request)
            link_id = f"plink_{len(self.links) + 1}"
            metadata = {k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")}
            body = {"id": link_id, "url": f"https://buy.stripe.example/{link_id}", "active": True, "metadata": metadata, "form": form}
            self.links[link_id] = body
        elif request.method == "POST" and (match := re.fullmatch(r"/v1/payment_links/(\w+)", path)):
            self.links[match.group(1)]["active"] = form_of(request).get("active") != "false"
            body = self.links[match.group(1)]
        elif request.method == "GET" and (match := re.fullmatch(r"/v1/payment_links/(\w+)", path)) and match.group(1) in self.links:
            body = self.links[match.group(1)]
        elif request.method == "GET" and (match := re.fullmatch(r"/v1/payment_intents/(\w+)", path)) and match.group(1) in self.intents:
            body = self.intents[match.group(1)]
        elif request.method == "GET" and path == "/v1/refunds":
            wanted = request.url.params.get("payment_intent")
            return httpx.Response(200, json={"object": "list", "has_more": False, "data": [r for r in self.refunds if r["payment_intent"] == wanted]})
        elif request.method == "POST" and path == "/v1/refunds":
            form = form_of(request)
            intent = self.intents.get(form["payment_intent"])
            if intent is None or self.refuse_refund:
                return httpx.Response(400, json={"error": {"type": "invalid_request_error"}})
            taken = sum(r["amount"] for r in self.refunds if r["payment_intent"] == intent["id"] and r["status"] != "failed")
            if taken + int(form["amount"]) > intent["amount"]:
                return httpx.Response(400, json={"error": {"type": "invalid_request_error", "code": "charge_already_refunded"}})
            body = {"id": f"re_{len(self.refunds) + 1}", "object": "refund", "payment_intent": intent["id"], "amount": int(form["amount"]),
                    "currency": intent["currency"], "status": self.refund_status,
                    "metadata": {k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")}}
            self.refunds.insert(0, body)
            if key:
                self.by_key[key] = body
            if self.drop_refund_answer:
                raise httpx.ReadTimeout("no answer", request=request)
            return httpx.Response(200, json=body)
        else:
            return httpx.Response(404, json={"error": {"type": "invalid_request_error"}})
        if key:
            self.by_key[key] = body
        return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeStripe]:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    monkeypatch.setattr(settings, "auth_mode", "headers")
    monkeypatch.setattr(settings, "billing_issuer_tenant_id", ISSUER)
    monkeypatch.setattr(settings, "web_app_url", "https://app.example")
    store = memory_repository.store
    for name in ("tasks", "runs", "approvals", "events", "tenants", "workspaces", "memberships", "invitations", "audit_records"):
        getattr(store, name).clear()
    memory_db.clear()
    memory_branding.clear()
    enforce.clear_cache()
    company.clear_cache()
    signup.reset_limits()
    stand_in = FakeStripe()
    monkeypatch.setattr(providers, "transport", httpx.MockTransport(stand_in.handler))
    yield stand_in
    memory_db.clear()
    company.clear_cache()


def setup_issuer(currency: str = "AED", price_minor: int = 10_000, stripe: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {"issuer_name": "ANUM FZ-LLC", "bank_name": "Emirates NBD", "bank_account_name": "ANUM FZ-LLC",
                            "bank_iban": "AE000000000000000000000", "tax_rules": {"AE": 500, "SA": 1500}}
    if stripe:
        body.update(stripe_enabled=True, stripe_secret_key=STRIPE_KEY, stripe_webhook_secret=WEBHOOK_SECRET)
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json=body))
    return ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json={
        "code": f"basic-{currency.lower()}", "name": "Basic", "price_minor": price_minor, "currency": currency, "trial_days": 0,
        "published": True, "limits": {"seats": 5}, "features": []}), 201)


def bill(plan_id: str, tenant_id: str = "acme-co", headers: dict[str, str] = OPERATOR) -> dict[str, Any]:
    ok(client.post("/api/v1/platform/organizations", headers=headers, json={
        "tenant_id": tenant_id, "display_name": f"{tenant_id} Ltd", "first_admin_email": f"admin@{tenant_id}.example"}), 201)
    return ok(client.post(f"/api/v1/platform/billing/organizations/{tenant_id}/subscription", headers=headers,
                          json={"plan_id": plan_id, "trial_days": 0}), 201)["invoice"]


def signed(event: dict[str, Any], secret: str = WEBHOOK_SECRET) -> tuple[bytes, dict[str, str]]:
    payload = json.dumps(event).encode()
    return payload, {"stripe-signature": providers.sign_stripe_payload(payload, secret, int(datetime.now(UTC).timestamp()))}


def deliver(event: dict[str, Any], secret: str = WEBHOOK_SECRET) -> Any:
    payload, headers = signed(event, secret)
    return client.post(WEBHOOK_URL, content=payload, headers=headers)


def paid_by_stripe(fake: FakeStripe, currency: str = "AED", tenant: str = "acme-co") -> dict[str, Any]:
    """An invoice paid on Stripe Checkout, confirmed by Stripe's signed checkout.session.completed."""
    plan = setup_issuer(currency)
    invoice = bill(plan["id"], tenant)
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner(tenant)))
    assert checkout["provider"] == "stripe"
    metadata = {"anum_tenant_id": tenant, "anum_invoice_id": invoice["id"]}
    fake.intent("pi_1", invoice["total_minor"], currency, metadata)
    assert ok(deliver({"id": "evt_paid", "type": "checkout.session.completed", "data": {"object": {
        "id": checkout["session_id"], "payment_status": "paid", "amount_total": invoice["total_minor"], "currency": currency.lower(),
        "payment_intent": "pi_1", "metadata": metadata}}}))["outcome"] == "paid"
    return invoice


def payment_id_of(tenant: str = "acme-co") -> str:
    with open_billing(tenant) as rows:
        return rows.select("billing_payments")[0]["id"]


def refund_url(invoice: dict[str, Any], payment_id: str, tenant: str = "acme-co") -> str:
    return f"/api/v1/platform/billing/organizations/{tenant}/invoices/{invoice['id']}/payments/{payment_id}/refunds"


def detail(invoice: dict[str, Any], tenant: str = "acme-co") -> dict[str, Any]:
    return ok(client.get(f"/api/v1/platform/billing/organizations/{tenant}/invoices/{invoice['id']}", headers=OPERATOR))


def refund_event(kind: str, refund: dict[str, Any], event_id: str) -> dict[str, Any]:
    return {"id": event_id, "type": kind, "data": {"object": refund}}


# ----------------------------------------------------------------------------- currencies


@pytest.mark.parametrize(("currency", "unit"), [("AED", "fils"), ("SAR", "halalas"), ("USD", "cents")])
def test_checkout_sends_minor_units_in_the_invoice_currency(fake: FakeStripe, currency: str, unit: str) -> None:
    plan = setup_issuer(currency, price_minor=12_345)
    invoice = bill(plan["id"])
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co")))
    assert checkout["provider"] == "stripe" and checkout["url"].startswith("https://checkout.stripe.example/")
    form = form_of(fake.calls("POST", "/v1/checkout/sessions")[0])
    # ANUM stores minor units (fils for AED) and Stripe expects the same integers for these currencies.
    assert form["line_items[0][price_data][currency]"] == currency.lower(), unit
    assert int(form["line_items[0][price_data][unit_amount]"]) == invoice["amount_due_minor"]
    assert form["metadata[anum_invoice_id]"] == invoice["id"] and form["payment_intent_data[metadata][anum_tenant_id]"] == "acme-co"


def test_an_aed_payment_is_recorded_in_fils(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake, "AED")
    paid = detail(invoice)
    assert paid["status"] == "paid" and paid["currency"] == "AED" and paid["amount_paid_minor"] == invoice["total_minor"]
    assert paid["payments"][0]["provider"] == "stripe" and paid["payments"][0]["refundable_minor"] == invoice["total_minor"]


def test_unsupported_currencies_and_tiny_amounts_never_reach_stripe(fake: FakeStripe) -> None:
    plan = setup_issuer("KWD")
    invoice = bill(plan["id"])
    refused = client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co"))
    assert refused.status_code == 502 and "KWD" in refused.json()["error"]["message"]
    tiny = setup_issuer("AED", price_minor=100)
    small = bill(tiny["id"], "tiny-co")
    refused = client.post(f"/api/v1/billing/invoices/{small['id']}/checkout", headers=owner("tiny-co"))
    assert refused.status_code == 502 and "2.00 AED" in refused.json()["error"]["message"]
    assert fake.requests == []


def test_a_session_in_another_currency_is_not_recorded(fake: FakeStripe) -> None:
    plan = setup_issuer("AED")
    invoice = bill(plan["id"])
    outcome = ok(deliver({"id": "evt_usd", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_x", "payment_status": "paid", "amount_total": invoice["total_minor"], "currency": "usd", "payment_intent": "pi_x",
        "metadata": {"anum_tenant_id": "acme-co", "anum_invoice_id": invoice["id"]}}}}))
    assert outcome["outcome"] == "ignored" and detail(invoice)["amount_paid_minor"] == 0


# ----------------------------------------------------------------------------- refunds through Stripe


def test_a_full_refund_goes_through_stripe_once_with_a_credit_note(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    pid = payment_id_of()
    body = {"amount_minor": invoice["total_minor"], "reason": "Customer cancelled", "mode": "provider", "idempotency_key": "refund-key-full-1"}
    first = ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json=body), 201)
    refund = first["refund"]
    assert refund["status"] == "succeeded" and refund["provider"] == "stripe" and refund["credit_note_id"]
    sent = fake.calls("POST", "/v1/refunds")
    assert len(sent) == 1
    form = form_of(sent[0])
    assert form["payment_intent"] == "pi_1" and int(form["amount"]) == invoice["total_minor"]
    assert form["metadata[anum_refund_id]"] == refund["id"] and form["metadata[anum_tenant_id]"] == "acme-co"
    assert sent[0].headers["idempotency-key"] == f"anum-refund-{refund['id']}"
    after = first["invoice"]
    assert after["amount_paid_minor"] == 0 and after["amount_credited_minor"] == invoice["total_minor"] and after["amount_due_minor"] == 0
    assert after["credit_notes"][0]["total_minor"] == invoice["total_minor"]
    with open_billing("acme-co") as rows:
        stored = rows.get("billing_refunds", id=refund["id"])
        assert stored["provider_refund_id"] == fake.refunds[0]["id"]
        assert rows.select("billing_accounts")[0]["credit_balance_minor"] == 0
    # The same key again: the first refund, never a second call to Stripe.
    again = ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json=body), 200)
    assert again["refund"]["id"] == refund["id"] and len(fake.calls("POST", "/v1/refunds")) == 1
    # A different refund with the same key is refused; nothing is left to refund anyway.
    assert client.post(refund_url(invoice, pid), headers=OPERATOR, json={**body, "amount_minor": 10}).status_code == 409
    more = client.post(refund_url(invoice, pid), headers=OPERATOR, json={**body, "idempotency_key": "refund-key-full-2", "amount_minor": 1})
    assert more.status_code == 422 and len(fake.calls("POST", "/v1/refunds")) == 1


def test_partial_refunds_add_up_and_never_exceed_the_payment(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    pid, total = payment_id_of(), invoice["total_minor"]
    base = {"reason": "Partial service", "mode": "provider"}
    ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json={**base, "amount_minor": 2_550, "idempotency_key": "partial-one"}), 201)
    second = ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json={**base, "amount_minor": total - 2_550, "idempotency_key": "partial-two"}), 201)
    assert second["invoice"]["amount_paid_minor"] == 0 and len(second["invoice"]["credit_notes"]) == 2
    assert [int(form_of(r)["amount"]) for r in fake.calls("POST", "/v1/refunds")] == [2_550, total - 2_550]
    over = client.post(refund_url(invoice, pid), headers=OPERATOR, json={**base, "amount_minor": 1, "idempotency_key": "partial-three"})
    assert over.status_code == 422 and len(fake.calls("POST", "/v1/refunds")) == 2


def test_no_answer_from_stripe_keeps_the_refund_pending_and_a_retry_never_refunds_twice(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    pid = payment_id_of()
    body = {"amount_minor": 5_000, "reason": "Goodwill", "mode": "provider", "idempotency_key": "refund-timeout-1"}
    fake.drop_refund_answer = True
    lost = client.post(refund_url(invoice, pid), headers=OPERATOR, json=body)
    assert lost.status_code == 502 and "pending" in lost.json()["error"]["message"]
    with open_billing("acme-co") as rows:
        claim = rows.select("billing_refunds")[0]
        assert claim["status"] == "pending"
    # The amount stays reserved while it is pending.
    assert detail(invoice)["payments"][0]["refundable_minor"] == invoice["total_minor"] - 5_000
    fake.drop_refund_answer = False
    settled = ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json=body), 200)
    assert settled["refund"]["status"] == "succeeded" and settled["refund"]["id"] == claim["id"]
    # Stripe was asked to refund once; the replay found that refund by its ANUM id.
    assert len(fake.calls("POST", "/v1/refunds")) == 1 and len(fake.refunds) == 1
    assert settled["invoice"]["amount_credited_minor"] == 5_000


def test_a_refund_stripe_refuses_books_nothing_and_frees_the_amount(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    pid = payment_id_of()
    fake.refuse_refund = True
    refused = client.post(refund_url(invoice, pid), headers=OPERATOR, json={
        "amount_minor": 1_000, "reason": "Try", "mode": "provider", "idempotency_key": "refund-refused-1"})
    assert refused.status_code == 502
    after = detail(invoice)
    assert after["refunds"][0]["status"] == "failed" and after["credit_notes"] == [] and after["amount_paid_minor"] == invoice["total_minor"]
    assert after["payments"][0]["refundable_minor"] == invoice["total_minor"]


def test_a_pending_stripe_refund_is_booked_when_the_webhook_says_it_succeeded(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    pid = payment_id_of()
    fake.refund_status = "pending"
    waiting = ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json={
        "amount_minor": 4_000, "reason": "Late delivery", "mode": "provider", "idempotency_key": "refund-pending-1"}), 201)
    assert waiting["refund"]["status"] == "pending" and waiting["invoice"]["credit_notes"] == []
    stripe_refund = fake.refunds[0]
    stripe_refund["status"] = "succeeded"
    # The delivered refund object is not what counts: ANUM lists the payment's refunds from Stripe's API.
    forged = {**stripe_refund, "amount": 999_999}
    result = ok(deliver(refund_event("refund.updated", forged, "evt_ref_1")))
    assert result["outcome"] == "refund_confirmed"
    assert ok(deliver(refund_event("refund.updated", forged, "evt_ref_1")))["duplicate"] is True
    assert ok(deliver(refund_event("charge.refunded", {"id": "ch_1", "object": "charge", "payment_intent": "pi_1"}, "evt_ref_2")))["outcome"] == "unchanged"
    booked = detail(invoice)
    assert booked["refunds"][0]["status"] == "succeeded" and booked["amount_credited_minor"] == 4_000 and len(booked["credit_notes"]) == 1


def test_a_pending_stripe_refund_that_fails_is_marked_failed(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    pid = payment_id_of()
    fake.refund_status = "pending"
    ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json={
        "amount_minor": 4_000, "reason": "Late delivery", "mode": "provider", "idempotency_key": "refund-pending-2"}), 201)
    fake.refunds[0]["status"] = "failed"
    assert ok(deliver(refund_event("refund.failed", fake.refunds[0], "evt_fail_1")))["outcome"] == "refund_failed"
    after = detail(invoice)
    assert after["refunds"][0]["status"] == "failed" and after["credit_notes"] == []
    assert after["payments"][0]["refundable_minor"] == invoice["total_minor"]


def test_a_refund_made_in_the_stripe_dashboard_is_recorded_once(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    fake.dashboard_refund("pi_1", 3_000)
    charge = {"id": "ch_1", "object": "charge", "payment_intent": "pi_1", "amount_refunded": 3_000}
    assert ok(deliver(refund_event("charge.refunded", charge, "evt_dash_1")))["outcome"] == "external_refund_recorded"
    assert ok(deliver(refund_event("refund.created", fake.refunds[0], "evt_dash_2")))["outcome"] == "unchanged"
    after = detail(invoice)
    assert len(after["refunds"]) == 1 and after["refunds"][0]["reason"] == "Refunded in the Stripe dashboard"
    assert after["amount_credited_minor"] == 3_000 and len(after["credit_notes"]) == 1


def test_refund_webhooks_need_a_valid_signature(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    fake.dashboard_refund("pi_1", 3_000)
    before = len(fake.requests)
    payload, _ = signed(refund_event("charge.refunded", {"id": "ch_1", "payment_intent": "pi_1"}, "evt_bad"))
    for header in ("t=1,v1=00", providers.sign_stripe_payload(payload, "whsec_wrong", int(datetime.now(UTC).timestamp())), None):
        response = client.post(WEBHOOK_URL, content=payload, headers={"stripe-signature": header} if header else {})
        assert response.status_code == 400
    assert len(fake.requests) == before and detail(invoice)["refunds"] == []


def test_a_refund_webhook_for_another_issuers_customer_changes_nothing(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    with open_billing("acme-co") as rows:
        rows.update("billing_accounts", {"tenant_id": "acme-co"}, {"issuer_tenant_id": "tenant_issuer_two"})
    fake.dashboard_refund("pi_1", 3_000)
    assert ok(deliver(refund_event("charge.refunded", {"id": "ch_1", "payment_intent": "pi_1"}, "evt_foreign")))["outcome"] == "ignored"
    with open_billing("acme-co") as rows:
        assert rows.select("billing_refunds") == [] and rows.get("billing_invoices", id=invoice["id"])["amount_credited_minor"] == 0


def test_stripe_refunds_are_platform_operator_only_and_tenant_isolated(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    pid = payment_id_of()
    body = {"amount_minor": 1_000, "reason": "Not allowed", "mode": "provider", "idempotency_key": "refund-perm-1"}
    for headers in (owner("acme-co"), owner("acme-co", roles="org_admin")):
        assert client.post(refund_url(invoice, pid), headers=headers, json=body).status_code == 403
    assert client.post(refund_url(invoice, pid), headers=OTHER_OPERATOR, json=body).status_code == 404
    assert fake.calls("POST", "/v1/refunds") == []
    assert all(r.action != "billing.refund.create" for r in memory_repository.store.audit_records)


def test_turning_stripe_off_refuses_a_provider_refund_but_keeps_manual(fake: FakeStripe) -> None:
    invoice = paid_by_stripe(fake)
    pid = payment_id_of()
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={"issuer_name": "ANUM FZ-LLC", "stripe_enabled": False}))
    off = client.post(refund_url(invoice, pid), headers=OPERATOR, json={
        "amount_minor": 1_000, "reason": "Stripe off", "mode": "provider", "idempotency_key": "refund-off-1"})
    assert off.status_code == 409 and fake.calls("POST", "/v1/refunds") == []
    manual = ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json={
        "amount_minor": 1_000, "reason": "Bank transfer back", "mode": "manual", "idempotency_key": "refund-off-2"}), 201)
    assert manual["refund"]["status"] == "succeeded" and manual["refund"]["provider"] is None


# ----------------------------------------------------------------------------- Ready to sell and subprocessors


def test_stripe_on_and_moyasar_off_is_the_active_provider(fake: FakeStripe) -> None:
    setup_issuer()
    item = next(i for i in ok(client.get("/api/v1/platform/readiness", headers=OPERATOR))["items"] if i["key"] == "payment_provider")
    assert item["state"] == "warning"  # test keys only
    assert item["detail"]["active"] == "stripe" and item["detail"]["stripe"] == "ready" and item["detail"]["moyasar"] == "off"
    assert item["detail"]["moyasar_optional"] is True and "AED" in item["detail"]["currencies"]
    values = ok(client.get("/api/v1/public/legal"))["values"]
    assert values["IN_USE_STRIPE"] == "Yes" and values["STRIPE_REGION"] is None and values["IN_USE_MOYASAR"] == "No"


def test_stripe_without_the_webhook_secret_is_not_ready(fake: FakeStripe) -> None:
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={
        "issuer_name": "ANUM FZ-LLC", "stripe_enabled": True, "stripe_secret_key": STRIPE_KEY}))
    item = next(i for i in ok(client.get("/api/v1/platform/readiness", headers=OPERATOR))["items"] if i["key"] == "payment_provider")
    assert item["state"] == "missing" and item["detail"]["stripe"] == "incomplete" and item["detail"]["active"] is None
