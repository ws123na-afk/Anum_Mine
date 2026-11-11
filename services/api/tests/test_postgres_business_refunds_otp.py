"""Refunds synced from the card provider and the portal's emailed code sign-in, in PostgreSQL (migration
``0176_business_refunds_otp``, docs/business.md#refunds-made-in-the-provider-dashboard,
docs/business-portal.md#signing-in-with-an-emailed-code).

The API runs as the non-owner ``anum_test_app`` role under forced RLS. Two deliveries of one refund at once record it
once (the invoice row lock and the unique provider refund id); another workspace and tenant see nothing; the sign-in
rows hold only hashes. Every name is SYNTHETIC.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from anum_api import notifications
from anum_api.business.invoice_refunds import ProviderRefund, apply_card_refunds
from anum_api.business.invoice_send import apply_card_payment
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.notifications import MailOutcome
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database
API = "/api/v1/business"
PORTAL = "/api/v1/portal"
ME = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A2, "x-user-id": "user_test", "x-user-roles": "owner"}
COLLEAGUE = {**ME, "x-user-id": "colleague", "x-user-roles": "member"}
TABLES = ("business_invoice_refunds", "business_portal_sign_ins")


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        for user_id, role, workspace in (("user_test", "owner", WORKSPACE_A), ("user_test", "owner", WORKSPACE_A2), ("colleague", "member", WORKSPACE_A2)):
            connection.execute(text("insert into workspace_memberships (user_id, tenant_id, workspace_id, role, active) values "
                                    "(:u, :t, :w, :r, true) on conflict do nothing"), {"u": user_id, "r": role, "t": TENANT_A, "w": workspace})
    yield TestClient(app)


def _rows(app_session: Callable[..., Iterator[Session]], sql: str, params: dict | None = None, *, workspace: str = WORKSPACE_A2,
          tenant: str = TENANT_A) -> list:
    with app_session(TenantContext(tenant_id=tenant, workspace_id=workspace, user_id="user_test")) as session:
        return list(session.execute(text(sql), params or {}).all())


def _ok(response, code: int = 200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json()


def _account(api: TestClient) -> dict:
    account = _ok(api.post(f"{API}/accounts", headers=ME, json={"name": f"Synthetic Client {uuid4().hex[:6]}"}), 201)
    with_contact = _ok(api.post(f"{API}/accounts/{account['id']}/contacts", headers=ME,
                                json={"name": "Sara Example", "email": "sara@client.example", "consent_basis": "inbound_inquiry"}), 201)
    return {**account, "contact_id": with_contact["contacts"][0]["id"]}


def _sent_invoice(api: TestClient, account_id: str) -> dict:
    _ok(api.put(f"{API}/billing-profile", headers=ME, json={"legal_name": "Synthetic Seller LLC", "invoice_prefix": "INV"}))
    draft = _ok(api.post(f"{API}/invoices", headers=COLLEAGUE, json={"account_id": account_id, "currency": "SAR",
                                                                       "lines": [{"description": "Synthetic work", "unit_price": 1000}]}), 201)
    waiting = _ok(api.post(f"{API}/invoices/{draft['id']}/request-approval", headers=COLLEAGUE))
    _ok(api.post(f"/api/v1/approvals/{waiting['approval']['id']}/approve", headers=ME, json={"payload_hash": waiting["approval"]["payload_hash"]}))
    sent = _ok(api.get(f"{API}/invoices/{draft['id']}", headers=ME))
    assert sent["status"] == "sent"
    return sent


def test_new_tables_have_forced_rls_and_purge_policies(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        for table in TABLES:
            forced = connection.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where relname = :t"), {"t": table}).scalar_one()
            policies = set(connection.execute(text("select policyname from pg_policies where tablename = :t"), {"t": table}).scalars())
            assert forced and {f"tenant_isolation_{table}", f"lifecycle_purge_{table}"} <= policies, (table, policies)


def test_a_dashboard_refund_is_recorded_once_under_rls_and_stays_in_its_workspace(client: TestClient,
                                                                                app_session: Callable[..., Iterator[Session]]) -> None:
    account = _account(client)
    sent = _sent_invoice(client, account["id"])
    metadata = {"anum_kind": "business_invoice", "anum_tenant_id": TENANT_A, "anum_workspace_id": WORKSPACE_A2, "anum_business_invoice_id": sent["id"]}
    assert apply_card_payment("stripe", TENANT_A, metadata, "pi_pg_1", "paid", 100000, "SAR") == "paid"
    refund = [ProviderRefund("re_pg_1", 40000, "SAR", "succeeded")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: apply_card_refunds("stripe", TENANT_A, metadata, "pi_pg_1", refund), range(2)))
    assert outcomes == ["refund_recorded", "unchanged"], outcomes
    assert _rows(app_session, "select provider_amount, amount, status from business_invoice_refunds") == [(400, 400, "recorded")]
    view = _ok(client.get(f"{API}/invoices/{sent['id']}", headers=ME))
    assert view["status"] == "sent" and view["amount_paid"] == 600 and view["refunds"][0]["provider_refund_id"] == "re_pg_1"
    # The payment with refunds stays: the route refuses and the database restricts.
    assert client.delete(f"{API}/invoices/{sent['id']}/payments/{view['payments'][0]['id']}", headers=ME).status_code == 409
    # Another workspace and tenant see nothing.
    assert _rows(app_session, "select count(*) from business_invoice_refunds", workspace=WORKSPACE_A) == [(0,)]
    assert _rows(app_session, "select count(*) from business_invoice_refunds", workspace=WORKSPACE_B, tenant=TENANT_B) == [(0,)]


def test_code_sign_in_under_rls_stores_only_hashes(client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                                   app_session: Callable[..., Iterator[Session]]) -> None:
    mailed: list[str] = []
    monkeypatch.setattr(notifications, "email_configured", lambda: True)
    monkeypatch.setattr(notifications, "send_with_retries", lambda to, subject, body, **_: mailed.append(body) or MailOutcome(status="sent", attempts=1, error=None))
    account = _account(client)
    _ok(client.post(f"{API}/portal/access", headers=ME, json={"account_id": account["id"], "contact_id": account["contact_id"]}), 201)
    _ok(client.put(f"{API}/portal/sign-in", headers=ME, json={"enabled": True}))
    scope = f"{TENANT_A}.{WORKSPACE_A2}"
    challenge = _ok(client.post(f"{PORTAL}/sign-in/code", json={"workspace": scope, "email": "Sara@Client.example"}), 202)["challenge_id"]
    unknown = _ok(client.post(f"{PORTAL}/sign-in/code", json={"workspace": scope, "email": "nobody@client.example"}), 202)
    assert len(mailed) == 1 and set(unknown) == {"challenge_id", "expires_in_seconds", "message"}
    code = re.search(r"\b(\d{6})\b", mailed[0]).group(1)  # type: ignore[union-attr]
    token = _ok(client.post(f"{PORTAL}/sign-in/verify", json={"workspace": scope, "challenge_id": challenge, "code": code}))["token"]
    assert _ok(client.get(f"{PORTAL}/me", headers={"authorization": f"Bearer {token}"}))["signed_in_with"] == "code"
    rows = _rows(app_session, "select status, email_hash, code_hash, session_hash, access_id is not null from business_portal_sign_ins order by created_at")
    assert [(row[0], row[4]) for row in rows] == [("signed_in", True), ("unmatched", False)]
    assert all("@" not in str(row[1]) and code not in str(row[2] or "") and token.rsplit(".", 1)[1] != row[3] for row in rows)
    # Another workspace's address finds nothing (the switch is per workspace, and the rows stay in theirs).
    assert client.post(f"{PORTAL}/sign-in/code", json={"workspace": f"{TENANT_A}.{WORKSPACE_A}", "email": "sara@client.example"}).status_code == 403
    assert _rows(app_session, "select count(*) from business_portal_sign_ins", workspace=WORKSPACE_A) == [(0,)]
    assert _rows(app_session, "select count(*) from business_portal_sign_ins", workspace=WORKSPACE_B, tenant=TENANT_B) == [(0,)]
