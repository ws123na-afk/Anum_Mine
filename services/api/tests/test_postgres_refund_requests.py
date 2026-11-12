"""Refunds started from ANUM and provider refunds ANUM could not match, in PostgreSQL (migration
``0178_refund_requests_vault``, docs/business.md#refunds-from-anum, docs/business.md#refunds-anum-could-not-match).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. The provider is a stand-in object (no HTTP);
the approval goes through the ordinary approvals flow with its payload hash. Every name is SYNTHETIC.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest
from conftest import TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from test_postgres_business_refunds_otp import API, ME, _account, _ok, _rows, _sent_invoice, client  # noqa: F401 - pytest fixture

from anum_api.billing.providers import StripeRefund
from anum_api.business import invoice_refund_requests
from anum_api.business.invoice_refunds import ProviderRefund, apply_card_refunds
from anum_api.business.invoice_send import apply_card_payment

pytestmark = pytest.mark.database
TABLES = ("business_refund_requests", "business_refund_reviews", "vault_match_dismissals")


@dataclass
class FakeStripeAdapter:
    made: list[dict] = field(default_factory=list)

    def refund(self, *, payment_intent: str, amount_minor: int, idempotency_key: str, metadata: dict[str, str]) -> StripeRefund:
        self.made.append({"payment_intent": payment_intent, "amount": amount_minor, "key": idempotency_key})
        return StripeRefund(id=f"re_pg_anum_{len(self.made)}", payment_intent=payment_intent, amount_minor=amount_minor, currency="sar",
                            status="succeeded", metadata=metadata)

    def refunds_of(self, payment_intent: str) -> list[StripeRefund]:
        return []


def test_new_tables_have_forced_rls_and_purge_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, (table, policies)


def test_a_refund_from_anum_is_approved_refunded_and_recorded_once_under_rls(client: TestClient, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
                                                                            app_session: Callable[..., Iterator[Session]]) -> None:
    fake = FakeStripeAdapter()
    monkeypatch.setattr(invoice_refund_requests, "_provider_or_refusal", lambda tenant_id, provider: fake)
    account = _account(client)
    sent = _sent_invoice(client, account["id"])
    metadata = {"anum_kind": "business_invoice", "anum_tenant_id": TENANT_A, "anum_workspace_id": WORKSPACE_A2, "anum_business_invoice_id": sent["id"]}
    assert apply_card_payment("stripe", TENANT_A, metadata, "pi_pg_anum", "paid", 100000, "SAR") == "paid"
    payment = _ok(client.get(f"{API}/invoices/{sent['id']}", headers=ME))["payments"][0]
    url = f"{API}/invoices/{sent['id']}/payments/{payment['id']}/refund-requests"
    asked = _ok(client.post(url, headers={**ME, "Idempotency-Key": "pg-refund-0001"}, json={"amount": 400, "reason": "Synthetic return"}), 201)["request"]
    assert _ok(client.post(url, headers={**ME, "Idempotency-Key": "pg-refund-0001"}, json={"amount": 400, "reason": "Synthetic return"}))["request"]["id"] == asked["id"]
    # The held amount: 1000 charged, 400 held, so 700 more is refused.
    assert client.post(url, headers={**ME, "Idempotency-Key": "pg-refund-0002"}, json={"amount": 700, "reason": "Too much"}).status_code == 422
    approval = _ok(client.get(f"/api/v1/approvals/{asked['approval_id']}", headers=ME))
    _ok(client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=ME, json={"payload_hash": approval["payload_hash"]}))
    assert fake.made == [{"payment_intent": "pi_pg_anum", "amount": 40000, "key": f"anum-binref-{asked['id']}"}]
    assert _rows(app_session, "select status, refund_id is not null, provider_refund_id from business_refund_requests") == [("succeeded", True, "re_pg_anum_1")]
    assert _rows(app_session, "select provider_amount, amount, status from business_invoice_refunds") == [(400, 400, "recorded")]
    # The provider's webhook for the same refund afterwards records nothing.
    assert apply_card_refunds("stripe", TENANT_A, metadata, "pi_pg_anum", [ProviderRefund("re_pg_anum_1", 40000, "SAR", "succeeded")]) == "unchanged"
    assert _rows(app_session, "select count(*) from business_invoice_refunds") == [(1,)]
    # A payment with a refund request stays (the route refuses before the database's restrict would).
    assert client.delete(f"{API}/invoices/{sent['id']}/payments/{payment['id']}", headers=ME).status_code == 409
    for workspace, tenant in ((WORKSPACE_A, TENANT_A), (WORKSPACE_B, TENANT_B)):
        assert _rows(app_session, "select count(*) from business_refund_requests", workspace=workspace, tenant=tenant) == [(0,)]


def test_a_refund_of_an_unknown_payment_is_kept_once_for_review(client: TestClient, app_session: Callable[..., Iterator[Session]]) -> None:  # noqa: F811
    account = _account(client)
    sent = _sent_invoice(client, account["id"])
    metadata = {"anum_kind": "business_invoice", "anum_tenant_id": TENANT_A, "anum_workspace_id": WORKSPACE_A2, "anum_business_invoice_id": sent["id"]}
    unknown = [ProviderRefund("re_pg_unknown", 2500, "SAR", "succeeded")]
    assert apply_card_refunds("stripe", TENANT_A, metadata, "pi_pg_unknown", unknown) == "refund_needs_review"
    assert apply_card_refunds("stripe", TENANT_A, metadata, "pi_pg_unknown", unknown) == "ignored"
    assert _rows(app_session, "select amount, status, invoice_id from business_refund_reviews") == [(25, "open", sent["id"])]
    review = _ok(client.get(f"{API}/refund-reviews", headers=ME))["items"][0]
    assert _ok(client.post(f"{API}/refund-reviews/{review['id']}/check", headers=ME, json={"note": "Synthetic test payment"}))["status"] == "checked"
    assert _rows(app_session, "select status, checked_by from business_refund_reviews") == [("checked", "user_test")]
    assert _rows(app_session, "select count(*) from business_refund_reviews", workspace=WORKSPACE_A) == [(0,)]
    assert _rows(app_session, "select count(*) from business_refund_reviews", workspace=WORKSPACE_B, tenant=TENANT_B) == [(0,)]
