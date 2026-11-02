"""Ready to sell in PostgreSQL (migration ``0123_selling_readiness``), as the non-owner app role.

``billing_company_profiles`` and ``billing_refunds`` are under forced RLS on ``anum.tenant_id``;
``anum_platform_reader`` reads only the configured issuer's company row (never ``updated_by``)
and only the two provider switches of its billing settings, never a key ciphertext. The main
path (company details, Moyasar on, a card payment through the webhook, a refund through
Moyasar, the receipt and the public legal facts) runs end to end against a fake Moyasar.
See docs/selling-readiness.md.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from anum_api.billing import company, enforce, moyasar, signup
from anum_api.billing.rows import open_billing
from anum_api.main import app
from anum_api.settings import settings
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)
from test_selling_readiness import MOYASAR_KEY, WEBHOOK_TOKEN, FakeMoyasar

pytestmark = pytest.mark.database
OPERATOR = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "operator_a", "x-user-roles": "owner,platform_operator"}
ADMIN = {"x-tenant-id": "acme-co", "x-workspace-id": "acme-co_main", "x-user-id": "admin", "x-user-roles": "owner"}


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


def _reader(engine: Engine, statement: str, issuer: str) -> list[Any]:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("set local role anum_platform_reader"))
            connection.execute(text("select set_config('anum.billing_issuer', :v, true)"), {"v": issuer})
            return list(connection.execute(text(statement)).all())
        finally:
            transaction.rollback()


def test_main_path_and_isolation(client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMoyasar()
    monkeypatch.setattr(moyasar, "transport", httpx.MockTransport(fake.handler))
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={"issuer_name": "ANUM Co"}))
    ok(client.put("/api/v1/platform/legal", headers=OPERATOR, json={
        "legal_name": "Example Group LLC", "registration_number": "1010000000", "registration_country": "SA",
        "address": "Riyadh", "contact_email": "hello@example.com", "dpo_email": "privacy@example.com",
        "server_location": "Oracle Cloud, Riyadh", "hosting_provider": "oracle"}))
    ok(client.put("/api/v1/platform/billing/providers/moyasar", headers=OPERATOR,
                  json={"enabled": True, "secret_key": MOYASAR_KEY, "webhook_secret": WEBHOOK_TOKEN}))
    plan = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json={
        "code": "basic", "name": "Basic", "price_minor": 10_000, "trial_days": 0, "published": True, "limits": {"seats": 5}}), 201)
    ok(client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": "acme-co", "display_name": "Acme", "first_admin_email": "admin@acme.example"}), 201)
    invoice = ok(client.post("/api/v1/platform/billing/organizations/acme-co/subscription", headers=OPERATOR,
                             json={"plan_id": plan["id"], "trial_days": 0}), 201)["invoice"]
    checkout = ok(client.post(f"/api/v1/billing/invoices/{invoice['id']}/checkout", headers=ADMIN))
    fake.pay(checkout["session_id"])
    body = json.dumps({"id": "evt_pg", "type": "payment_paid", "secret_token": WEBHOOK_TOKEN, "data": {"id": "pay_1"}}).encode()
    assert ok(client.post(f"/api/v1/billing/webhooks/moyasar/{TENANT_A}", content=body))["outcome"] == "paid"
    detail = ok(client.get(f"/api/v1/billing/invoices/{invoice['id']}", headers=ADMIN))
    payment_id = detail["payments"][0]["id"]
    refunded = ok(client.post(
        f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/payments/{payment_id}/refunds", headers=OPERATOR,
        json={"amount_minor": 1000, "reason": "Goodwill", "idempotency_key": "pg-refund-0001"}), 201)
    assert refunded["refund"]["status"] == "succeeded" and refunded["invoice"]["amount_credited_minor"] == 1000
    receipt = client.get(f"/api/v1/billing/invoices/{invoice['id']}/payments/{payment_id}/receipt", headers=ADMIN)
    assert receipt.status_code == 200 and b"Example Group LLC" in receipt.content
    public = ok(client.get("/api/v1/public/legal"))
    assert public["values"]["LEGAL_ENTITY_NAME"] == "Example Group LLC" and public["values"]["IN_USE_MOYASAR"] == "Yes"
    readiness = ok(client.get("/api/v1/platform/readiness", headers=OPERATOR))
    assert {i["key"]: i["state"] for i in readiness["items"]}["company_details"] == "done"

    with database_engine.connect() as connection:
        # No tenant context: nothing. Another tenant: nothing, and no write into this one.
        for scope in (None, TENANT_B):
            with connection.begin():
                connection.execute(text(f"set local role {APP_ROLE}"))
                if scope:
                    connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": scope})
                for table in ("billing_company_profiles", "billing_refunds"):
                    assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table  # nosec B608
                assert connection.execute(text("update billing_refunds set status = 'failed'")).rowcount == 0
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_B})
            with pytest.raises(DBAPIError):
                connection.execute(text(
                    "insert into billing_company_profiles (tenant_id, updated_by) values (:t, 'x')"), {"t": TENANT_A})
    with open_billing("acme-co") as rows:
        assert rows.count("billing_refunds") == 1

    # The reader sees only the configured issuer's company row and its two switches.
    assert len(_reader(database_engine, "select legal_name from billing_company_profiles", TENANT_A)) == 1
    assert _reader(database_engine, "select legal_name from billing_company_profiles", TENANT_B) == []
    flags = _reader(database_engine, "select stripe_enabled, moyasar_enabled from billing_settings", TENANT_A)
    assert [tuple(row) for row in flags] == [(False, True)]
    for forbidden in ("select updated_by from billing_company_profiles", "select moyasar_secret_ciphertext from billing_settings",
                      "select moyasar_webhook_secret_ciphertext from billing_settings", "select bank_iban from billing_settings",
                      "select amount_minor from billing_refunds"):
        with pytest.raises(DBAPIError):
            _reader(database_engine, forbidden, TENANT_A)
    # The key is stored as a ciphertext only.
    with database_engine.connect() as connection:
        stored = connection.execute(text("select moyasar_secret_ciphertext, moyasar_webhook_secret_ciphertext from billing_settings")).one()
        assert MOYASAR_KEY not in str(stored) and WEBHOOK_TOKEN not in str(stored)


def test_refund_idempotency_key_is_unique_per_issuer(client: TestClient, database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        for name in ("uq_billing_refunds_key", "uq_billing_refunds_provider"):
            assert connection.execute(text("select count(*) from pg_indexes where indexname = :n"), {"n": name}).scalar_one() == 1
        forced = connection.execute(text(
            "select relname from pg_class where relname in ('billing_refunds', 'billing_company_profiles') "
            "and relrowsecurity and relforcerowsecurity order by relname")).scalars().all()
        assert forced == ["billing_company_profiles", "billing_refunds"]
