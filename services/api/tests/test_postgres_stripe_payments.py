"""Stripe for a UAE company in PostgreSQL, as the non-owner app role (docs/billing.md#stripe-refunds).

The main path against a fake Stripe: Stripe on and Moyasar off, an AED invoice paid on Checkout
(amounts in fils), a partial refund through Stripe's Refunds API with its credit note, the same
request replayed, a refund made in the Stripe dashboard recorded once by the webhook, and the
checklist naming Stripe as the active provider. Forced RLS keeps the refunds in the customer's
tenant, and migration ``0127_stripe_payment_links`` widens the Business provider checks to Stripe
(validated) without allowing anything else.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from anum_api.billing import company, enforce, providers, signup
from anum_api.billing.rows import open_billing
from anum_api.main import app
from anum_api.settings import settings
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)
from test_stripe_payments import STRIPE_KEY, WEBHOOK_SECRET, FakeStripe, signed

pytestmark = pytest.mark.database
OPERATOR = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "operator_a", "x-user-roles": "owner,platform_operator"}
ADMIN = {"x-tenant-id": "acme-co", "x-workspace-id": "acme-co_main", "x-user-id": "admin", "x-user-roles": "owner"}
WEBHOOK = f"/api/v1/billing/webhooks/stripe/{TENANT_A}"


@pytest.fixture
def client(postgres_backend: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:  # noqa: F811
    monkeypatch.setattr(settings, "billing_issuer_tenant_id", TENANT_A)
    monkeypatch.setattr(settings, "web_app_url", "https://app.example")
    enforce.clear_cache()
    company.clear_cache()
    signup.reset_limits()
    return TestClient(app)


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def deliver(client: TestClient, event: dict[str, Any]) -> Any:
    payload, headers = signed(event, WEBHOOK_SECRET)
    return ok(client.post(WEBHOOK, content=payload, headers=headers))


def test_stripe_payment_and_refunds_under_rls(client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeStripe()
    monkeypatch.setattr(providers, "transport", httpx.MockTransport(fake.handler))
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={
        "issuer_name": "ANUM FZ-LLC", "tax_rules": {"AE": 500}, "stripe_enabled": True, "stripe_secret_key": STRIPE_KEY, "stripe_webhook_secret": WEBHOOK_SECRET}))
    plan = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json={
        "code": "basic-aed", "name": "Basic", "price_minor": 36_700, "currency": "AED", "trial_days": 0, "published": True, "limits": {"seats": 5}}), 201)
    ok(client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": "acme-co", "display_name": "Acme", "first_admin_email": "admin@acme.example"}), 201)
    invoice = ok(client.post("/api/v1/platform/billing/organizations/acme-co/subscription", headers=OPERATOR,
                             json={"plan_id": plan["id"], "trial_days": 0}), 201)["invoice"]
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=ADMIN))
    assert checkout["provider"] == "stripe"
    metadata = {"anum_tenant_id": "acme-co", "anum_invoice_id": invoice["id"]}
    fake.intent("pi_pg_1", invoice["total_minor"], "AED", metadata)
    assert deliver(client, {"id": "evt_pg_paid", "type": "checkout.session.completed", "data": {"object": {
        "id": checkout["session_id"], "payment_status": "paid", "amount_total": invoice["total_minor"], "currency": "aed",
        "payment_intent": "pi_pg_1", "metadata": metadata}}})["outcome"] == "paid"
    detail = ok(client.get(f"/api/v1/billing/invoices/{invoice['id']}", headers=ADMIN))
    assert detail["status"] == "paid" and detail["currency"] == "AED"
    payment_id = detail["payments"][0]["id"]

    url = f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/payments/{payment_id}/refunds"
    body = {"amount_minor": 5_050, "reason": "Partial refund", "mode": "provider", "idempotency_key": "pg-stripe-refund-1"}
    refunded = ok(client.post(url, headers=OPERATOR, json=body), 201)
    assert refunded["refund"]["status"] == "succeeded" and refunded["invoice"]["amount_credited_minor"] == 5_050
    assert ok(client.post(url, headers=OPERATOR, json=body))["refund"]["id"] == refunded["refund"]["id"]
    assert len(fake.calls("POST", "/v1/refunds")) == 1
    fake.dashboard_refund("pi_pg_1", 1_000)
    assert deliver(client, {"id": "evt_pg_dash", "type": "charge.refunded", "data": {"object": {"id": "ch_pg", "payment_intent": "pi_pg_1"}}})["outcome"] == "external_refund_recorded"
    assert deliver(client, {"id": "evt_pg_dash", "type": "charge.refunded", "data": {"object": {"id": "ch_pg", "payment_intent": "pi_pg_1"}}})["duplicate"] is True
    with open_billing("acme-co") as rows:
        refunds = rows.select("billing_refunds", order=[("created_at", False)])
        assert [(r["provider"], r["status"], int(r["amount_minor"])) for r in refunds] == [("stripe", "succeeded", 5_050), ("stripe", "succeeded", 1_000)]
        assert rows.count("billing_credit_notes") == 2
    item = next(i for i in ok(client.get("/api/v1/platform/readiness", headers=OPERATOR))["items"] if i["key"] == "payment_provider")
    assert item["detail"]["active"] == "stripe" and item["detail"]["moyasar_optional"] is True
    assert ok(client.get("/api/v1/public/legal"))["values"]["IN_USE_STRIPE"] == "Yes"

    with database_engine.connect() as connection:
        for scope in (None, TENANT_B):
            with connection.begin():
                connection.execute(text(f"set local role {APP_ROLE}"))
                if scope:
                    connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": scope})
                assert connection.execute(text("select count(*) from billing_refunds")).scalar_one() == 0
                assert connection.execute(text("update billing_refunds set status = 'failed'")).rowcount == 0
        stored = connection.execute(text("select stripe_secret_ciphertext, stripe_webhook_secret_ciphertext from billing_settings")).one()
        assert STRIPE_KEY not in str(stored) and WEBHOOK_SECRET not in str(stored)


def test_migration_0127_allows_stripe_and_nothing_else(client: TestClient, database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        rows = connection.execute(text(
            "select conname, convalidated, pg_get_constraintdef(oid) from pg_constraint "
            "where conname in ('ck_business_invoice_payment_links_provider', 'ck_business_invoice_payments_provider') order by conname")).all()
    assert [row[0] for row in rows] == ["ck_business_invoice_payment_links_provider", "ck_business_invoice_payments_provider"]
    for _name, validated, definition in rows:
        assert validated and "stripe" in definition and "moyasar" in definition and "paypal" not in definition
