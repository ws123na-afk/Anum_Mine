"""Customer billing in PostgreSQL (migration ``0083_customer_billing``), as the non-owner app role.

Every billing table is under forced RLS on ``anum.tenant_id``: another organisation reads
nothing and cannot write into this one. The narrow roles see only what they must:
``anum_platform_reader`` the amounts, statuses and dates of the issuer's own customers and
published plans (never contacts, bill-to snapshots or proof files), ``anum_maintenance``
live subscriptions, open invoices and usage counters of the one organisation named.
Invoice numbers are gapless under rollback, and self-serve sign-up provisions each
organisation inside its own tenant. See docs/billing.md.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

from anum_api.billing import enforce, providers, signup
from anum_api.billing import service as svc
from anum_api.billing.rows import open_billing
from anum_api.main import app
from anum_api.settings import settings
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)

pytestmark = pytest.mark.database
OPERATOR = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "operator_a", "x-user-roles": "owner,platform_operator"}
OTHER = {"x-tenant-id": TENANT_B, "x-workspace-id": WORKSPACE_B, "x-user-id": "owner_b", "x-user-roles": "owner"}
TABLES = (
    "billing_plans", "billing_settings", "billing_accounts", "billing_subscriptions", "billing_seats", "billing_usage",
    "billing_invoices", "billing_invoice_lines", "billing_credit_notes", "billing_payments", "billing_number_sequences",
    "billing_webhook_events", "billing_signups",
)
WEBHOOK_SECRET = "whsec_example"


@pytest.fixture
def client(postgres_backend: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:  # noqa: F811
    monkeypatch.setattr(settings, "billing_issuer_tenant_id", TENANT_A)
    monkeypatch.setattr(settings, "signup_enabled", True)
    enforce.clear_cache()
    signup.reset_limits()
    return TestClient(app)


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def _scoped(connection: Connection, **values: str) -> None:
    for name, value in values.items():
        connection.execute(text("select set_config(:name, :value, true)"), {"name": f"anum.{name}", "value": value})


def _rows(engine: Engine, role: str, statement: str, **settings_values: str) -> list[Any]:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {role}"))
            _scoped(connection, **settings_values)
            return list(connection.execute(text(statement)).all())
        finally:
            transaction.rollback()


def _bill_acme(client: TestClient) -> dict[str, Any]:
    ok(client.put("/api/v1/platform/billing/settings", headers=OPERATOR, json={
        "issuer_name": "ANUM Co", "bank_iban": "SA0000000000000000000000", "stripe_enabled": True,
        "stripe_secret_key": "sk_test_example", "stripe_webhook_secret": WEBHOOK_SECRET}))
    basic = ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json={
        "code": "basic", "name": "Basic", "price_minor": 10_000, "trial_days": 0, "published": True, "limits": {"seats": 5}}), 201)
    ok(client.post("/api/v1/platform/billing/plans", headers=OPERATOR, json={
        "code": "secret", "name": "Secret", "price_minor": 1, "published": False}), 201)
    ok(client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": "acme-co", "display_name": "Acme", "first_admin_email": "admin@acme.example"}), 201)
    started = ok(client.post("/api/v1/platform/billing/organizations/acme-co/subscription", headers=OPERATOR,
                             json={"plan_id": basic["id"], "trial_days": 0}), 201)
    invoice = started["invoice"]
    ok(client.put("/api/v1/billing/account", headers={"x-tenant-id": "acme-co", "x-workspace-id": "acme-co_main", "x-user-id": "admin",
                                                       "x-user-roles": "owner"},
                  json={"contact_email": "ap@acme.example", "address_line1": "King Fahd Road", "city": "Riyadh"}))
    ok(client.post(f"/api/v1/platform/billing/organizations/acme-co/invoices/{invoice['id']}/payments", headers=OPERATOR, json={
        "amount_minor": 5000, "reference": "TRX-1",
        "proof": {"name": "p.png", "content_type": "image/png", "data_base64": "iVBORw0KGgoAAAA="}}), 201)
    return {"basic": basic, "invoice": invoice}


def test_main_path_under_rls_and_other_tenants_see_nothing(client: TestClient, database_engine: Engine) -> None:
    billed = _bill_acme(client)
    admin = {"x-tenant-id": "acme-co", "x-workspace-id": "acme-co_main", "x-user-id": "admin", "x-user-roles": "owner"}
    overview = ok(client.get("/api/v1/billing/overview", headers=admin))
    assert overview["subscription"]["plan_code"] == "basic" and overview["open_balance_minor"] == 6500
    pdf = client.get(f"/api/v1/billing/invoices/{billed['invoice']['id']}/pdf", headers=admin)
    assert pdf.content.startswith(b"%PDF")
    assert ok(client.get("/api/v1/billing/invoices", headers=OTHER))["total"] == 0
    assert client.get(f"/api/v1/billing/invoices/{billed['invoice']['id']}", headers=OTHER).status_code == 404
    assert ok(client.get("/api/v1/platform/billing/summary", headers=OPERATOR))["counts"]["active"] == 1
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            for table in TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table  # nosec B608
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            _scoped(connection, tenant_id=TENANT_B)
            for table in TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table  # nosec B608
            assert connection.execute(text("update billing_accounts set state = 'read_only'")).rowcount == 0
            assert connection.execute(text("delete from billing_invoices")).rowcount == 0
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            _scoped(connection, tenant_id=TENANT_B)
            with pytest.raises(DBAPIError):
                connection.execute(text(
                    "insert into billing_usage (tenant_id, period, meter, quantity) values ('acme-co', '2026-10', 'seats', 1)"))


def test_platform_reader_sees_only_its_customers_amounts_and_published_plans(client: TestClient, database_engine: Engine) -> None:
    _bill_acme(client)
    reader = "anum_platform_reader"
    subs = _rows(database_engine, reader, "select tenant_id, status, price_minor from billing_subscriptions", billing_issuer=TENANT_A)
    assert [(r.tenant_id, r.status, r.price_minor) for r in subs] == [("acme-co", "active", 10_000)]
    assert _rows(database_engine, reader, "select tenant_id from billing_subscriptions", billing_issuer=TENANT_B) == []
    assert _rows(database_engine, reader, "select tenant_id from billing_subscriptions") == []
    plans = _rows(database_engine, reader, "select code from billing_plans", billing_issuer=TENANT_A)
    assert [r.code for r in plans] == ["basic"]
    for statement in ("select contact_email from billing_accounts", "select bill_to from billing_invoices",
                      "select proof_content from billing_payments", "select reference from billing_payments",
                      "select stripe_secret_ciphertext from billing_settings", "select created_by from billing_plans"):
        with pytest.raises(DBAPIError):
            _rows(database_engine, reader, statement, billing_issuer=TENANT_A)
    with pytest.raises(DBAPIError):
        _rows(database_engine, reader, "update billing_accounts set state = 'active'", billing_issuer=TENANT_A)
    # INHERIT FALSE: the app role's own query in tenant B never gains the reader's rows.
    assert _rows(database_engine, APP_ROLE, "select tenant_id from billing_subscriptions", tenant_id=TENANT_B, billing_issuer=TENANT_A) == []


def test_maintenance_discovers_live_work_and_one_organisations_usage(client: TestClient, database_engine: Engine) -> None:
    _bill_acme(client)
    found = _rows(database_engine, "anum_maintenance", "select tenant_id, status from billing_subscriptions")
    assert [(r.tenant_id, r.status) for r in found] == [("acme-co", "active")]
    open_invoices = _rows(database_engine, "anum_maintenance", "select tenant_id, due_at from billing_invoices")
    assert len(open_invoices) == 1
    with pytest.raises(DBAPIError):
        _rows(database_engine, "anum_maintenance", "select total_minor, bill_to from billing_invoices")
    with pytest.raises(DBAPIError):
        _rows(database_engine, "anum_maintenance", "select prompt from tasks", billing_tenant="acme-co")
    workspaces = _rows(database_engine, "anum_maintenance", "select id from workspaces", billing_tenant="acme-co")
    assert [r.id for r in workspaces] == ["acme-co_main"]
    assert _rows(database_engine, "anum_maintenance", "select id from workspaces", billing_tenant=TENANT_B) != workspaces
    usage = ok(client.get("/api/v1/platform/billing/organizations/acme-co", headers=OPERATOR))["usage"]["meters"]
    assert next(m for m in usage if m["key"] == "workspaces")["used"] == 1
    assert next(m for m in usage if m["key"] == "seats")["used"] == 1  # the owner invitation


def test_invoice_numbers_stay_gapless_when_a_transaction_rolls_back(client: TestClient) -> None:
    _bill_acme(client)
    with pytest.raises(RuntimeError):
        with open_billing("acme-co") as rows:
            with rows.as_tenant(TENANT_A):
                assert rows.next_number("invoice") == 2
            raise RuntimeError("roll back")
    with open_billing("acme-co") as rows:
        with rows.as_tenant(TENANT_A):
            assert rows.next_number("invoice") == 2
        assert rows.tenant_id == "acme-co" and rows.count("billing_invoices") == 1
        account = svc.account_of(rows)
        issued = svc.issue_invoice(rows, account, [svc.Line("Setup", 1000, kind="adjustment")], kind="manual",
                                   now=datetime.now(UTC), actor="t")
        assert issued.invoice["number"] == "INV-000003"


def test_dunning_and_read_only_under_rls(client: TestClient) -> None:
    billed = _bill_acme(client)
    admin = {"x-tenant-id": "acme-co", "x-workspace-id": "acme-co_main", "x-user-id": "admin", "x-user-roles": "owner"}
    now = datetime.now(UTC) + timedelta(days=60)
    with open_billing("acme-co") as rows:
        result = svc.run_tenant(rows, now=now)
    assert "read_only" in result.transitions and result.reminders
    enforce.clear_cache()
    refused = client.post("/api/v1/tasks", headers=admin, json={"title": "x", "prompt": "x"})
    assert refused.status_code == 402 and refused.json()["error"]["code"] == "billing_read_only"
    with open_billing("acme-co") as rows:
        for invoice in rows.select("billing_invoices", {"status": "open"}):
            svc.record_payment(rows, invoice["id"], amount_minor=int(invoice["amount_due_minor"]), method="bank_transfer",
                               reference="x", received_at=now, now=now, actor="t")
        assert svc.account_of(rows)["state"] == "active"
    assert billed


def test_stripe_webhook_is_idempotent_in_the_database(client: TestClient, database_engine: Engine) -> None:
    billed = _bill_acme(client)
    invoice = billed["invoice"]
    payload = json.dumps({"id": "evt_db", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "payment_status": "paid", "amount_total": 6500, "payment_intent": "pi_db",
        "metadata": {"anum_tenant_id": "acme-co", "anum_invoice_id": invoice["id"]}}}}).encode()
    header = providers.sign_stripe_payload(payload, WEBHOOK_SECRET, int(datetime.now(UTC).timestamp()))
    url = f"/api/v1/billing/webhooks/stripe/{TENANT_A}"
    assert ok(client.post(url, content=payload, headers={"stripe-signature": header}))["outcome"] == "paid"
    second = ok(client.post(url, content=payload, headers={"stripe-signature": header}))
    assert second["duplicate"] is True
    with open_billing("acme-co") as rows:
        assert rows.count("billing_payments", {"provider": "stripe"}) == 1
        assert svc.get_invoice(rows, invoice["id"])["status"] == "paid"


def test_sign_up_provisions_each_organisation_in_its_own_tenant(client: TestClient, database_engine: Engine) -> None:
    billed = _bill_acme(client)
    body = {"organization_name": "Startup", "plan_id": billed["basic"]["id"], "accept_terms": True}
    first = ok(client.post("/api/v1/signup/organization", headers={"x-user-id": "kc-1", "x-user-email": "a@startup.example"}, json=body), 201)
    second = ok(client.post("/api/v1/signup/organization", headers={"x-user-id": "kc-2", "x-user-email": "b@other.example"}, json=body), 201)
    assert first["tenant_id"] != second["tenant_id"]
    for mine, theirs in ((first, second), (second, first)):
        rows = _rows(database_engine, APP_ROLE, "select tenant_id, status from billing_subscriptions", tenant_id=mine["tenant_id"])
        assert [(r.tenant_id, r.status) for r in rows] == [(mine["tenant_id"], "trialing")]
        members = _rows(database_engine, APP_ROLE, "select user_id, role from workspace_memberships",
                        tenant_id=mine["tenant_id"], workspace_id=mine["workspace_id"])
        assert len(members) == 1 and members[0].role == "owner"
        assert _rows(database_engine, APP_ROLE, f"select id from billing_signups where tenant_id = '{theirs['tenant_id']}'",  # nosec B608
                     tenant_id=mine["tenant_id"]) == []
    again = client.post("/api/v1/signup/organization", headers={"x-user-id": "kc-1", "x-user-email": "a@startup.example"}, json=body)
    assert again.status_code == 409
    assert ok(client.get("/api/v1/platform/billing/subscriptions", headers=OPERATOR))["total"] == 3


def test_key_rotation_re_encrypts_stripe_secrets_inside_the_issuer(client: TestClient, database_engine: Engine) -> None:
    from cryptography.fernet import Fernet

    from anum_api.db import session as db_session
    from anum_api.rotate_secrets import rotate_billing_stripe_keys
    from anum_api.secret_box import SecretCipher, secret_cipher

    _bill_acme(client)
    old_cipher = secret_cipher()
    new_key = Fernet.generate_key()
    with database_engine.connect() as connection:
        before = connection.execute(text("select stripe_secret_ciphertext from billing_settings")).scalar_one()
    from cryptography.fernet import MultiFernet

    combined = SecretCipher.__new__(SecretCipher)
    combined._primary = Fernet(new_key)
    combined._fernet = MultiFernet([Fernet(new_key), old_cipher._primary])
    report = rotate_billing_stripe_keys(combined, session_factory=db_session.SessionLocal)
    assert report.scanned == 1 and report.rotated == 1 and report.failed == 0
    with database_engine.connect() as connection:
        after = connection.execute(text("select stripe_secret_ciphertext from billing_settings")).scalar_one()
    assert after != before and Fernet(new_key).decrypt(after.encode()) == b"sk_test_example"
    assert rotate_billing_stripe_keys(combined, session_factory=db_session.SessionLocal).already_current == 1
