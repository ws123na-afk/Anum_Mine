"""Ready to sell (docs/selling-readiness.md) against the in-memory backend and a fake Moyasar.

Covers the Moyasar adapter (write-only keys, the hosted payment page request, the webhook's
secret token compared in constant time, the payment read back from Moyasar before money is
recorded, idempotency per event and per payment, another issuer's invoice ignored), refunds
(through Moyasar and by hand, idempotency keys, limits, credit notes, failures, a refund made in
the Moyasar dashboard), receipts, the company details and legal facts (permissions, validation,
the public read leaking nothing, generated subprocessors, the lawyer confirmation, every legal
placeholder known), invoices printing the company details, the readiness checklist, the SPF
analysis, and tenant isolation of every new route. Moyasar is never called for real: every
request goes to an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.billing import company, enforce, moyasar, selling, signup
from anum_api.billing import service as svc
from anum_api.billing.rows import memory_db, open_billing
from anum_api.dependencies import memory_repository
from anum_api.organisations import memory_branding
from anum_api.settings import settings

client = TestClient(main.app)
ISSUER = "tenant_issuer"
OPERATOR = {"x-tenant-id": ISSUER, "x-workspace-id": "tenant_issuer_ws", "x-user-id": "operator_1", "x-user-roles": "owner,platform_operator"}
OTHER_OPERATOR = {"x-tenant-id": "tenant_issuer_two", "x-workspace-id": "ws_two", "x-user-id": "operator_2", "x-user-roles": "owner,platform_operator"}
# Plainly fake, low-entropy fixtures (gitleaks).
MOYASAR_KEY = "sk_test_example"
WEBHOOK_TOKEN = "example-webhook-token-value"
LEGAL_DIR = Path(__file__).resolve().parents[3] / "docs" / "legal"
DOC_FILES = {"terms": "terms-of-service.md", "privacy": "privacy-policy.md", "dpa": "data-processing-addendum.md",
             "aup": "acceptable-use-policy.md", "subprocessors": "subprocessors.md"}


def owner(tenant_id: str, user: str = "owner_c", roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": f"{tenant_id}_main", "x-user-id": user, "x-user-roles": roles}


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json() if response.content else None


class FakeMoyasar:
    """A stand-in for api.moyasar.com: invoices, payments and refunds held in memory."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.invoices: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, dict[str, Any]] = {}
        self.fail_refund = False

    def pay(self, invoice_id: str, payment_id: str = "pay_1", amount: int | None = None, currency: str | None = None) -> None:
        invoice = self.invoices[invoice_id]
        self.payments[payment_id] = {
            "id": payment_id, "status": "paid", "amount": invoice["amount"] if amount is None else amount,
            "currency": currency or invoice["currency"], "refunded": 0, "invoice_id": invoice_id, "metadata": None,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != httpx.BasicAuth(MOYASAR_KEY, "")._auth_header:
            return httpx.Response(401, json={"type": "authentication_error"})
        path = request.url.path
        if request.method == "POST" and path == "/v1/invoices":
            body = json.loads(request.content)
            invoice_id = f"inv_{len(self.invoices) + 1}"
            self.invoices[invoice_id] = {**body, "id": invoice_id, "status": "initiated", "url": f"https://checkout.moyasar.example/invoices/{invoice_id}"}
            return httpx.Response(201, json=self.invoices[invoice_id])
        match = re.fullmatch(r"/v1/invoices/([\w-]+)", path)
        if request.method == "GET" and match and match.group(1) in self.invoices:
            return httpx.Response(200, json=self.invoices[match.group(1)])
        match = re.fullmatch(r"/v1/payments/([\w-]+)", path)
        if request.method == "GET" and match and match.group(1) in self.payments:
            return httpx.Response(200, json=self.payments[match.group(1)])
        match = re.fullmatch(r"/v1/payments/([\w-]+)/refund", path)
        if request.method == "POST" and match and match.group(1) in self.payments:
            if self.fail_refund:
                return httpx.Response(400, json={"type": "invalid_request_error"})
            payment = self.payments[match.group(1)]
            amount = int(json.loads(request.content)["amount"])
            if payment["refunded"] + amount > payment["amount"]:
                return httpx.Response(400, json={"type": "invalid_request_error"})
            payment["refunded"] += amount
            payment["status"] = "refunded"
            return httpx.Response(200, json=payment)
        return httpx.Response(404, json={"type": "not_found"})


@pytest.fixture(autouse=True)
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMoyasar]:
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
    fake = FakeMoyasar()
    monkeypatch.setattr(moyasar, "transport", httpx.MockTransport(fake.handler))
    yield fake
    memory_db.clear()
    company.clear_cache()


@pytest.fixture
def fake(clean: FakeMoyasar) -> FakeMoyasar:
    return clean


def setup_issuer(currency: str = "SAR") -> dict[str, Any]:
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={
        "issuer_name": "ANUM Co", "bank_name": "Riyad Bank", "bank_account_name": "ANUM Co", "bank_iban": "SA0000000000000000000000"}))
    return ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json={
        "code": "basic", "name": "Basic", "price_minor": 10_000, "currency": currency, "trial_days": 0, "published": True,
        "limits": {"seats": 5}, "features": []}), 201)


def bill(plan_id: str, tenant_id: str = "acme-co", headers: dict[str, str] = OPERATOR) -> dict[str, Any]:
    ok(client.post("/api/v1/platform/organizations", headers=headers, json={
        "tenant_id": tenant_id, "display_name": f"{tenant_id} Ltd", "first_admin_email": f"admin@{tenant_id}.example"}), 201)
    return ok(client.post(f"/api/v1/platform/billing/organizations/{tenant_id}/subscription", headers=headers,
                          json={"plan_id": plan_id, "trial_days": 0}), 201)["invoice"]


def turn_on_moyasar() -> dict[str, Any]:
    return ok(client.put("/api/v1/platform/billing/providers/moyasar", headers=OPERATOR,
                         json={"enabled": True, "secret_key": MOYASAR_KEY, "webhook_secret": WEBHOOK_TOKEN}))


def event(payment_id: str = "pay_1", event_id: str = "evt_1", kind: str = "payment_paid", token: str = WEBHOOK_TOKEN,
          amount: int = 1) -> bytes:
    # The delivered body is never trusted for amounts: ANUM reads the payment back from Moyasar.
    return json.dumps({"id": event_id, "type": kind, "secret_token": token, "live": False,
                       "data": {"id": payment_id, "status": "paid", "amount": amount, "currency": "SAR"}}).encode()


def paid_by_card(fake: FakeMoyasar) -> dict[str, Any]:
    plan = setup_issuer()
    turn_on_moyasar()
    invoice = bill(plan["id"])
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co")))
    fake.pay(checkout["session_id"])
    assert ok(client.post(f"/api/v1/billing/webhooks/moyasar/{ISSUER}", content=event()))["outcome"] == "paid"
    return invoice


# ----------------------------------------------------------------------------- Moyasar settings and checkout


def test_moyasar_keys_are_write_only_and_need_both_secrets(fake: FakeMoyasar) -> None:
    setup_issuer()
    refused = client.put("/api/v1/platform/billing/providers/moyasar", headers=OPERATOR, json={"enabled": True, "secret_key": MOYASAR_KEY})
    assert refused.status_code == 422 and "webhook secret" in refused.json()["error"]["message"]
    assert client.put("/api/v1/platform/billing/providers/moyasar", headers=OPERATOR,
                      json={"secret_key": "pk_test_example"}).status_code == 422
    assert client.put("/api/v1/platform/billing/providers/moyasar", headers=OPERATOR,
                      json={"webhook_secret": "short"}).status_code == 422
    view = turn_on_moyasar()
    assert view["enabled"] and view["has_secret_key"] and view["has_webhook_secret"] and view["test_mode"] is True
    assert view["webhook_url"] == f"/api/v1/billing/webhooks/moyasar/{ISSUER}"
    dumped = json.dumps(memory_db.all_rows("billing_settings"), default=str)
    assert MOYASAR_KEY not in dumped and WEBHOOK_TOKEN not in dumped and MOYASAR_KEY not in json.dumps(view)
    # Saving the other billing settings keeps Moyasar on.
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={"issuer_name": "ANUM Co"}))
    assert ok(client.get("/api/v1/platform/billing/providers/moyasar", headers=OPERATOR))["enabled"] is True
    audit = [r for r in memory_repository.store.audit_records if r.action == "billing.moyasar.update"]
    assert audit and MOYASAR_KEY not in json.dumps(dict(audit[-1].metadata))


def test_moyasar_settings_are_platform_operator_only() -> None:
    for headers in (owner("acme-co"), owner("acme-co", roles="org_admin,owner")):
        assert client.get("/api/v1/platform/billing/providers/moyasar", headers=headers).status_code == 403
        assert client.put("/api/v1/platform/billing/providers/moyasar", headers=headers, json={"enabled": False}).status_code == 403


def test_checkout_opens_a_moyasar_payment_page(fake: FakeMoyasar) -> None:
    plan = setup_issuer()
    turn_on_moyasar()
    invoice = bill(plan["id"])
    overview = ok(client.get("/api/v1/billing/overview", headers=owner("acme-co")))
    assert overview["payment"]["card_enabled"] and overview["payment"]["card_provider"] == "moyasar"
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co")))
    assert checkout["provider"] == "moyasar" and checkout["url"].startswith("https://checkout.moyasar.example/")
    sent = json.loads(fake.requests[0].content)
    assert sent["amount"] == invoice["total_minor"] and sent["currency"] == "SAR"
    assert sent["metadata"] == {"anum_tenant_id": "acme-co", "anum_invoice_id": invoice["id"]}
    assert sent["success_url"].startswith("https://app.example/") and "card" not in json.dumps(sent).lower()
    # A member who is not a billing admin cannot open it.
    assert client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co", "member_1", "member")).status_code == 403


def test_checkout_refuses_a_currency_moyasar_is_not_set_up_for(fake: FakeMoyasar) -> None:
    plan = setup_issuer(currency="EUR")
    turn_on_moyasar()
    invoice = bill(plan["id"])
    refused = client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co"))
    assert refused.status_code == 502 and "EUR" in refused.json()["error"]["message"] and not fake.requests


def test_checkout_in_aed_is_accepted(fake: FakeMoyasar) -> None:
    plan = setup_issuer(currency="AED")
    turn_on_moyasar()
    invoice = bill(plan["id"])
    ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co")))
    assert json.loads(fake.requests[0].content)["currency"] == "AED"


# ----------------------------------------------------------------------------- Moyasar webhook


def test_webhook_checks_the_token_in_constant_time_and_reads_the_payment_back(fake: FakeMoyasar, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = setup_issuer()
    turn_on_moyasar()
    invoice = bill(plan["id"])
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co")))
    fake.pay(checkout["session_id"])
    url = f"/api/v1/billing/webhooks/moyasar/{ISSUER}"
    compared: list[tuple[bytes, bytes]] = []
    real = moyasar.hmac.compare_digest
    monkeypatch.setattr(moyasar.hmac, "compare_digest", lambda a, b: compared.append((a, b)) or real(a, b))
    assert client.post(url, content=event(token="wrong-token-value")).status_code == 400
    assert compared, "the token must go through hmac.compare_digest"
    no_token = json.loads(event())
    no_token.pop("secret_token")
    assert client.post(url, content=json.dumps(no_token).encode()).status_code == 400
    assert client.post(url, content=b"not json").status_code == 400
    with open_billing("acme-co") as rows:
        assert rows.count("billing_payments") == 0
    first = ok(client.post(url, content=event(amount=1)))
    assert first == {"received": True, "duplicate": False, "outcome": "paid"}
    with open_billing("acme-co") as rows:
        payment = rows.select("billing_payments")[0]
        # The amount is Moyasar's, not the 1 halala in the delivered body.
        assert payment["amount_minor"] == invoice["total_minor"] and payment["provider"] == "moyasar" and payment["method"] == "card"
        assert svc.get_invoice(rows, invoice["id"])["status"] == "paid"
    assert ok(client.post(url, content=event()))["duplicate"] is True
    assert ok(client.post(url, content=event(event_id="evt_again")))["outcome"] == "duplicate_payment"
    with open_billing("acme-co") as rows:
        assert rows.count("billing_payments") == 1
    assert client.post("/api/v1/billing/webhooks/moyasar/tenant_unknown", content=event()).status_code == 404
    assert client.post("/api/v1/billing/webhooks/moyasar/BAD!", content=event()).status_code == 404


def test_webhook_ignores_an_unpaid_payment_another_issuers_invoice_and_a_wrong_currency(fake: FakeMoyasar) -> None:
    plan = setup_issuer()
    turn_on_moyasar()
    invoice = bill(plan["id"])
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co")))
    url = f"/api/v1/billing/webhooks/moyasar/{ISSUER}"
    fake.pay(checkout["session_id"], "pay_failed")
    fake.payments["pay_failed"]["status"] = "failed"
    assert ok(client.post(url, content=event("pay_failed", "evt_f")))["outcome"] == "ignored"
    fake.pay(checkout["session_id"], "pay_usd", currency="USD")
    assert ok(client.post(url, content=event("pay_usd", "evt_u")))["outcome"] == "ignored"
    with open_billing("acme-co") as rows:
        rows.update("billing_accounts", {"tenant_id": "acme-co"}, {"issuer_tenant_id": "tenant_issuer_two"})
    fake.pay(checkout["session_id"], "pay_foreign")
    assert ok(client.post(url, content=event("pay_foreign", "evt_x")))["outcome"] == "ignored"
    with open_billing("acme-co") as rows:
        assert rows.count("billing_payments") == 0


def test_webhook_that_cannot_reach_moyasar_is_retried_not_dropped(fake: FakeMoyasar, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = setup_issuer()
    turn_on_moyasar()
    invoice = bill(plan["id"])
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=owner("acme-co")))
    fake.pay(checkout["session_id"])

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(moyasar, "transport", httpx.MockTransport(down))
    assert client.post(f"/api/v1/billing/webhooks/moyasar/{ISSUER}", content=event()).status_code == 502
    monkeypatch.setattr(moyasar, "transport", httpx.MockTransport(fake.handler))
    assert ok(client.post(f"/api/v1/billing/webhooks/moyasar/{ISSUER}", content=event()))["outcome"] == "paid"


# ----------------------------------------------------------------------------- refunds


def refund_url(invoice: dict[str, Any], payment_id: str, tenant: str = "acme-co") -> str:
    return f"/api/v1/platform/billing/organizations/{tenant}/invoices/{invoice['id']}/payments/{payment_id}/refunds"


def payment_id_of(tenant: str = "acme-co") -> str:
    with open_billing(tenant) as rows:
        return rows.select("billing_payments")[0]["id"]


def test_refund_through_moyasar_issues_a_credit_note_and_is_idempotent(fake: FakeMoyasar) -> None:
    invoice = paid_by_card(fake)
    pid = payment_id_of()
    body = {"amount_minor": 5000, "reason": "Customer cancelled", "idempotency_key": "refund-key-0001"}
    first = ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json=body), 201)
    assert first["refund"]["status"] == "succeeded" and first["refund"]["provider"] == "moyasar"
    detail = first["invoice"]
    assert detail["amount_paid_minor"] == invoice["total_minor"] - 5000 and detail["amount_credited_minor"] == 5000
    assert detail["amount_due_minor"] == 0 and detail["status"] == "paid"
    assert detail["payments"][0]["refunded_minor"] == 5000 and len(detail["credit_notes"]) == 1
    assert fake.payments["pay_1"]["refunded"] == 5000
    refunds_sent = [r for r in fake.requests if r.url.path.endswith("/refund")]
    assert len(refunds_sent) == 1
    again = ok(client.post(refund_url(invoice, pid), headers=OPERATOR, json=body), 200)
    assert again["refund"]["id"] == first["refund"]["id"]
    assert len([r for r in fake.requests if r.url.path.endswith("/refund")]) == 1
    conflict = client.post(refund_url(invoice, pid), headers=OPERATOR, json={**body, "amount_minor": 10})
    assert conflict.status_code == 409
    with open_billing("acme-co") as rows:
        account = svc.account_of(rows)
        assert account["credit_balance_minor"] == 0
    audit = [r for r in memory_repository.store.audit_records if r.action == "billing.refund.create"]
    assert audit and set(audit[-1].metadata) == {"tenant", "refund_id", "payment_id", "amount_minor", "mode", "status"}


def test_refund_cannot_exceed_the_payment(fake: FakeMoyasar) -> None:
    invoice = paid_by_card(fake)
    pid = payment_id_of()
    too_much = client.post(refund_url(invoice, pid), headers=OPERATOR,
                           json={"amount_minor": invoice["total_minor"] + 1, "reason": "Too much", "idempotency_key": "refund-key-0002"})
    assert too_much.status_code == 422
    ok(client.post(refund_url(invoice, pid), headers=OPERATOR,
                   json={"amount_minor": invoice["total_minor"], "reason": "All of it", "idempotency_key": "refund-key-0003"}), 201)
    more = client.post(refund_url(invoice, pid), headers=OPERATOR, json={"amount_minor": 1, "reason": "More", "idempotency_key": "refund-key-0004"})
    assert more.status_code == 422


def test_failed_provider_refund_books_nothing(fake: FakeMoyasar) -> None:
    invoice = paid_by_card(fake)
    pid = payment_id_of()
    fake.fail_refund = True
    failed = client.post(refund_url(invoice, pid), headers=OPERATOR, json={"amount_minor": 100, "reason": "Try", "idempotency_key": "refund-key-0005"})
    assert failed.status_code == 502
    with open_billing("acme-co") as rows:
        refund = rows.select("billing_refunds")[0]
        assert refund["status"] == "failed" and rows.count("billing_credit_notes") == 0
        assert svc.get_invoice(rows, invoice["id"])["amount_paid_minor"] == invoice["total_minor"]
    # A failed refund frees the amount for a new attempt with a new key.
    fake.fail_refund = False
    ok(client.post(refund_url(invoice, pid), headers=OPERATOR,
                   json={"amount_minor": invoice["total_minor"], "reason": "Retry", "idempotency_key": "refund-key-0006"}), 201)


def test_manual_refund_of_a_bank_payment(fake: FakeMoyasar) -> None:
    plan = setup_issuer()
    invoice = bill(plan["id"])
    ok(client.post(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/payments", headers=OPERATOR,
                   json={"amount_minor": invoice["total_minor"], "method": "bank_transfer", "reference": "TRX1"}), 201)
    pid = payment_id_of()
    provider = client.post(refund_url(invoice, pid), headers=OPERATOR, json={"amount_minor": 100, "reason": "Back", "idempotency_key": "refund-key-0007"})
    assert provider.status_code == 409 and "manual" in provider.json()["error"]["message"]
    manual = ok(client.post(refund_url(invoice, pid), headers=OPERATOR,
                            json={"amount_minor": 100, "reason": "Returned by transfer", "mode": "manual", "idempotency_key": "refund-key-0008"}), 201)
    assert manual["refund"]["status"] == "succeeded" and manual["refund"]["provider"] is None and not fake.requests


def test_refund_made_in_the_moyasar_dashboard_is_recorded_once(fake: FakeMoyasar) -> None:
    invoice = paid_by_card(fake)
    fake.payments["pay_1"]["refunded"] = 2500
    url = f"/api/v1/billing/webhooks/moyasar/{ISSUER}"
    assert ok(client.post(url, content=event(kind="payment_refunded", event_id="evt_r1")))["outcome"] == "external_refund_recorded"
    assert ok(client.post(url, content=event(kind="payment_refunded", event_id="evt_r2")))["outcome"] == "unchanged"
    with open_billing("acme-co") as rows:
        refunds = rows.select("billing_refunds")
        assert len(refunds) == 1 and refunds[0]["amount_minor"] == 2500 and refunds[0]["status"] == "succeeded"
        assert svc.get_invoice(rows, invoice["id"])["amount_credited_minor"] == 2500


def test_refunds_are_platform_operator_only_and_tenant_isolated(fake: FakeMoyasar) -> None:
    invoice = paid_by_card(fake)
    pid = payment_id_of()
    body = {"amount_minor": 100, "reason": "Not needed", "idempotency_key": "refund-key-0009"}
    assert client.post(refund_url(invoice, pid), headers=owner("acme-co"), json=body).status_code == 403
    assert client.post(refund_url(invoice, pid), headers=OTHER_OPERATOR, json=body).status_code == 404
    assert client.post(refund_url(invoice, "payment_other"), headers=OPERATOR, json=body).status_code == 404


# ----------------------------------------------------------------------------- receipts


def test_receipts_for_the_organisation_and_the_console(fake: FakeMoyasar) -> None:
    invoice = paid_by_card(fake)
    pid = payment_id_of()
    own = client.get(f"/api/v1/billing/invoices/{invoice['id']}/payments/{pid}/receipt", headers=owner("acme-co"))
    assert own.status_code == 200 and own.content.startswith(b"%PDF") and own.headers["cache-control"] == "private, no-store"
    console = client.get(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/payments/{pid}/receipt", headers=OPERATOR)
    assert console.status_code == 200 and b"Payment receipt" in console.content
    assert client.get(f"/api/v1/billing/invoices/{invoice['id']}/payments/{pid}/receipt", headers=owner("acme-co", "m", "member")).status_code == 403
    assert client.get(f"/api/v1/billing/invoices/{invoice['id']}/payments/{pid}/receipt", headers=owner("other-co")).status_code == 404
    assert client.get(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/payments/{pid}/receipt",
                      headers=OTHER_OPERATOR).status_code == 404


# ----------------------------------------------------------------------------- company details and legal pages


COMPANY = {
    "legal_name": "Shahzeb Business Group LLC", "registration_number": "1010000000", "registration_country": "sa",
    "registration_authority": "Ministry of Commerce", "address": "Riyadh, Saudi Arabia", "contact_email": "hello@example.com",
    "dpo_name": "Privacy Lead", "dpo_email": "privacy@example.com", "vat_number": "300000000000003",
    "server_location": "Oracle Cloud, Riyadh (Saudi Arabia)", "hosting_provider": "oracle", "governing_law": "The laws of Saudi Arabia",
}


def test_every_legal_placeholder_is_known() -> None:
    for doc, name in DOC_FILES.items():
        text = (LEGAL_DIR / name).read_text("utf-8")
        body = "\n".join(line for line in text.splitlines() if not line.startswith(">"))
        found = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", body))
        assert found == set(company.DOC_KEYS[doc]), (doc, sorted(found ^ set(company.DOC_KEYS[doc])))
    for key in company.ALL_KEYS:
        known = key in company.TERM_KEYS or key in company.company_values(company.EMPTY_PROFILE) or key in company.generated_values({})
        assert known, key


def test_company_details_fill_the_public_legal_facts_and_invoices(fake: FakeMoyasar) -> None:
    plan = setup_issuer()
    before = ok(client.get("/api/v1/public/legal"))
    assert before["values"]["LEGAL_ENTITY_NAME"] is None and before["missing"]["terms"]
    saved = ok(client.put("/api/v1/platform/legal", headers=OPERATOR, json={**COMPANY, "legal_terms": {"EFFECTIVE_DATE": "1 December 2026"}}))
    assert saved["company"]["registration_country"] == "SA" and saved["legal_terms"] == {"EFFECTIVE_DATE": "1 December 2026"}
    company.clear_cache()
    public = ok(client.get("/api/v1/public/legal"))
    values = public["values"]
    assert values["LEGAL_ENTITY_NAME"] == COMPANY["legal_name"] and values["LICENSING_AUTHORITY"] == "Ministry of Commerce, Saudi Arabia"
    assert values["DPO_OR_PRIVACY_LEAD"] == "Privacy Lead (privacy@example.com)" and values["PRIVACY_CONTACT_EMAIL"] == "privacy@example.com"
    assert values["SERVICE_URL"] == "https://app.example" and values["WEB_HOST"] == "app.example"
    assert values["IN_USE_ORACLE"] == "Yes" and values["IN_USE_RENDER"] == "No" and values["IN_USE_MOYASAR"] == "No"
    assert values["MOYASAR_REGION"] == "Not used" and values["EFFECTIVE_DATE"] == "1 December 2026"
    # Nothing that identifies who edited it, and no tenant id.
    dumped = json.dumps(public)
    assert "operator_1" not in dumped and ISSUER not in dumped and "updated_by" not in dumped
    # A new invoice prints the legal name, registration and VAT number.
    invoice = bill(plan["id"])
    pdf = client.get(f"/api/v1/billing/invoices/{invoice['id']}/pdf", headers=owner("acme-co"))
    assert b"Shahzeb Business Group LLC" in pdf.content and b"1010000000" in pdf.content and b"300000000000003" in pdf.content


def test_turning_moyasar_on_lists_it_as_a_subprocessor() -> None:
    setup_issuer()
    turn_on_moyasar()
    values = ok(client.get("/api/v1/public/legal"))["values"]
    assert values["IN_USE_MOYASAR"] == "Yes" and values["MOYASAR_REGION"] is None and values["IN_USE_STRIPE"] == "No"


def test_publishing_needs_every_fact_and_the_lawyer_confirmation() -> None:
    no_lawyer = client.put("/api/v1/platform/legal", headers=OPERATOR, json={**COMPANY, "published_docs": ["aup"]})
    assert no_lawyer.status_code == 422 and "lawyer" in no_lawyer.json()["error"]["message"]
    gaps = client.put("/api/v1/platform/legal", headers=OPERATOR, json={**COMPANY, "published_docs": ["aup"], "lawyer_reviewed": True})
    assert gaps.status_code == 422 and "aup" in gaps.json()["error"]["message"]
    done = ok(client.put("/api/v1/platform/legal", headers=OPERATOR, json={
        **COMPANY, "security_email": "security@example.com", "published_docs": ["aup"], "lawyer_reviewed": True,
        "legal_terms": {"EFFECTIVE_DATE": "1 December 2026", "ENFORCEMENT_DETAILS": "We tell you first where we can."}}))
    assert done["published_docs"] == ["aup"] and done["missing"]["aup"] == []


def test_legal_settings_validate_and_are_platform_operator_only() -> None:
    assert client.put("/api/v1/platform/legal", headers=OPERATOR, json={"contact_email": "not an email"}).status_code == 422
    assert client.put("/api/v1/platform/legal", headers=OPERATOR, json={"legal_terms": {"NOT_A_KEY": "x"}}).status_code == 422
    assert client.put("/api/v1/platform/legal", headers=OPERATOR, json={"legal_terms": {"MINIMUM_AGE": "x" * 2001}}).status_code == 422
    assert client.put("/api/v1/platform/legal", headers=OPERATOR, json={"unknown": 1}).status_code == 422
    for headers in (owner("acme-co"), owner("acme-co", roles="org_admin,owner")):
        assert client.get("/api/v1/platform/legal", headers=headers).status_code == 403
        assert client.put("/api/v1/platform/legal", headers=headers, json=COMPANY).status_code == 403
        assert client.get("/api/v1/platform/readiness", headers=headers).status_code == 403
        assert client.post("/api/v1/platform/readiness/dns", headers=headers, json={"domain": "example.com"}).status_code == 403


def test_each_operator_sees_only_their_own_company_details() -> None:
    ok(client.put("/api/v1/platform/legal", headers=OPERATOR, json=COMPANY))
    other = ok(client.get("/api/v1/platform/legal", headers=OTHER_OPERATOR))
    assert other["company"]["legal_name"] == "" and other["updated_by"] is None
    # The public page shows only the configured issuer.
    ok(client.put("/api/v1/platform/legal", headers=OTHER_OPERATOR, json={**COMPANY, "legal_name": "Someone Else"}))
    company.clear_cache()
    assert ok(client.get("/api/v1/public/legal"))["company"]["legal_name"] == COMPANY["legal_name"]


def test_public_legal_without_an_issuer_says_nothing_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "billing_issuer_tenant_id", None)
    answer = ok(client.get("/api/v1/public/legal"))
    assert answer["configured"] is False and answer["values"]["LEGAL_ENTITY_NAME"] is None


# ----------------------------------------------------------------------------- readiness


def test_readiness_reads_real_state(fake: FakeMoyasar, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = ok(client.get("/api/v1/platform/readiness", headers=OPERATOR))
    states = {item["key"]: item["state"] for item in first["items"]}
    assert states["payment_provider"] == "missing" and states["company_details"] == "missing" and states["plans"] == "missing"
    assert states["backups_local"] == "unknown" and first["ready"] is False
    setup_issuer()
    turn_on_moyasar()
    ok(client.put("/api/v1/platform/legal", headers=OPERATOR, json=COMPANY))
    (tmp_path / "last-local.json").write_text(json.dumps({"ok": True, "finished_at": "2026-11-02T02:00:00Z", "last_success_at": "2026-11-02T02:00:00Z", "keep_days": 7}))
    (tmp_path / "last-offsite.json").write_text(json.dumps({"ok": False, "finished_at": "2026-11-02T02:05:00Z", "last_success_at": None}))
    monkeypatch.setattr(settings, "backup_status_dir", str(tmp_path))
    after = ok(client.get("/api/v1/platform/readiness", headers=OPERATOR))
    items = {item["key"]: item for item in after["items"]}
    # Test keys work but are not live, so the provider is a warning, not done.
    assert items["payment_provider"]["state"] == "warning" and items["payment_provider"]["detail"]["moyasar"] == "ready"
    assert items["company_details"]["state"] == "done" and items["plans"]["state"] == "done"
    assert items["backups_local"]["state"] == "done" and items["backups_local"]["detail"]["keep_days"] == 7
    assert items["backups_offsite"]["state"] == "missing"
    assert items["legal_pages"]["state"] == "missing" and items["legal_pages"]["detail"]["missing"]["privacy"] > 0
    assert items["public_issuer"]["state"] == "done" and after["mail_domain"] == "example.com"
    assert {item["fix"] for item in after["items"]} <= {"payments", "company", "mail", "backups", "legal", "plans", "env"}


def test_spf_analysis() -> None:
    assert selling.analyse_spf(["v=spf1 ~all"])["status"] == "no_senders"
    assert selling.analyse_spf(["v=spf1 ip4:84.13.132.198 include:spf.brevo.com ~all"])["status"] == "ok"
    assert selling.analyse_spf(["v=spf1 +all"])["status"] == "too_open"
    assert selling.analyse_spf(["google-site-verification=x"])["status"] == "missing"
    assert selling.analyse_spf(["v=spf1 mx ~all", "v=spf1 a ~all"])["status"] == "multiple"


def test_dns_check_resolves_on_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"example.com": ["v=spf1 ~all"], "_dmarc.example.com": ["v=DMARC1; p=none; rua=mailto:d@example.com"]}
    monkeypatch.setattr(selling, "_txt", lambda name: (answers.get(name, []), None))
    result = ok(client.post("/api/v1/platform/readiness/dns", headers=OPERATOR, json={"domain": "Example.com"}))
    assert result["domain"] == "example.com" and result["spf"]["status"] == "no_senders" and result["dmarc"]["policy"] == "none"
    assert client.post("/api/v1/platform/readiness/dns", headers=OPERATOR, json={"domain": "not a domain"}).status_code == 422
    assert client.post("/api/v1/platform/readiness/dns", headers=OPERATOR, json={"domain": "127.0.0.1"}).status_code == 422


def test_readiness_lists_the_ai_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The placeholder model, or a hosted one without its key, is not ready to sell; a real one is."""
    def model_item() -> dict[str, Any]:
        items = {item["key"]: item for item in ok(client.get("/api/v1/platform/readiness", headers=OPERATOR))["items"]}
        return items["ai_model"]

    monkeypatch.setattr(settings, "model_provider", "mock")
    item = model_item()
    assert item["state"] == "missing" and item["fix"] == "env"
    assert item["detail"] == {"provider": "mock", "placeholder": True, "key_missing": False}
    monkeypatch.setattr(settings, "model_provider", "anthropic")
    monkeypatch.setattr(settings, "model_api_key", None)
    item = model_item()
    assert item["state"] == "missing" and item["detail"]["key_missing"] is True and item["detail"]["placeholder"] is False
    monkeypatch.setattr(settings, "model_api_key", "sk-ant-example")
    assert model_item()["state"] == "done"
    monkeypatch.setattr(settings, "model_provider", "ollama")
    monkeypatch.setattr(settings, "model_api_key", None)
    assert model_item()["state"] == "done"
