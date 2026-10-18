"""PostgreSQL: the Email Center operator view (migration 0064_mail_operator_view).

The API runs as the non-owner app role. The platform operator's mail health is read through
``anum_platform_reader``: only the operator's organisations, only ``tenant_id, status,
last_checked_at`` of ``mail_accounts`` and the counters, never an address, password or
message. The mailbox stays private to its owner for everyone else.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError

from anum_api.main import app
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from test_mail_center import PASSWORD, FakeServer, fake  # noqa: F401 (fixture)
from test_postgres_control_plane import postgres_backend  # noqa: F401 (fixture)
from test_postgres_team_work import _member, _rows, h

pytestmark = pytest.mark.database

OPERATOR = h("owner_a", "owner,platform_operator")
ORG, ORG_WS = "acme_mail", "acme_mail_main"
CUSTOMER = {**h("acme_admin", "owner", ORG, ORG_WS), "x-user-email": "admin@acme.example"}


@pytest.fixture
def client(postgres_backend: None, database_engine: Engine, fake: FakeServer) -> TestClient:  # noqa: F811
    _member(database_engine, TENANT_A, WORKSPACE_A, "owner_a", "owner")
    test_client = TestClient(app)
    created = test_client.post("/api/v1/platform/organizations", headers=OPERATOR, json={
        "tenant_id": ORG, "display_name": "Acme Mail", "first_admin_email": "admin@acme.example"})
    assert created.status_code == 201, created.text
    accepted = test_client.post("/api/v1/workspace-invitations/accept", headers={**CUSTOMER, "x-user-roles": "member"},
                                json={"token": created.json()["invitation_token"]})
    assert accepted.status_code == 200, accepted.text
    return test_client


def test_operator_reads_counts_through_the_platform_reader_only(client: TestClient, database_engine: Engine, fake: FakeServer) -> None:  # noqa: F811
    account = client.post("/api/v1/mail/accounts", headers=CUSTOMER, json={"address": "owner@example.test", "password": PASSWORD})
    assert account.status_code == 201, account.text
    draft = client.post("/api/v1/mail/drafts", headers=CUSTOMER, json={"account_id": account.json()["id"]}).json()
    client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=CUSTOMER, json={"to": ["buyer@client.example"], "subject": "Offer", "body": "Text"})
    approval = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=CUSTOMER, json={"idempotency_key": "send-key-pg-01"}).json()["approval"]
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=CUSTOMER, json={"payload_hash": approval["payload_hash"]}).status_code == 200
    assert len(fake.sent) == 1

    view = client.get("/api/v1/platform/mail/health", headers=OPERATOR)
    assert view.status_code == 200, view.text
    row = next(o for o in view.json()["organisations"] if o["tenant_id"] == ORG)
    assert row["mailboxes"] == 1 and row["connected"] == 1 and row["counts"]["sent"] == 1 and row["health"] == "ok"
    assert "owner@example.test" not in view.text and "buyer@client.example" not in view.text

    # The reader: the operator's organisation, counted columns only, no writes.
    seen = _rows(database_engine, "anum_platform_reader", "select tenant_id, status from mail_accounts", user="owner_a")
    assert [(r.tenant_id, r.status) for r in seen] == [(ORG, "connected")]
    assert _rows(database_engine, "anum_platform_reader", "select tenant_id from mail_accounts", user="someone_else") == []
    counters = _rows(database_engine, "anum_platform_reader", "select tenant_id, metric, count from mail_ops_counters", user="owner_a")
    assert [(r.tenant_id, r.metric, r.count) for r in counters] == [(ORG, "sent", 1)]
    for statement in (
        "select address from mail_accounts",
        "select password_enc from mail_accounts",
        "select display_name from mail_accounts",
        "select last_error from mail_accounts",
        "select owner_user_id from mail_accounts",
        "select body from mail_drafts",
        "select workspace_id from mail_ops_counters",
        "update mail_accounts set status = 'error'",
        "delete from mail_ops_counters",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            _rows(database_engine, "anum_platform_reader", statement, user="owner_a")

    # Still private for the app role: another person in the same workspace sees nothing.
    assert _rows(database_engine, APP_ROLE, "select id from mail_accounts", ORG, ORG_WS, "someone_else") == []
    assert len(_rows(database_engine, APP_ROLE, "select id from mail_accounts", ORG, ORG_WS, "acme_admin")) == 1
    # Counters follow the tenant and workspace policy.
    assert _rows(database_engine, APP_ROLE, "select metric from mail_ops_counters", TENANT_A, WORKSPACE_A) == []
    assert len(_rows(database_engine, APP_ROLE, "select metric from mail_ops_counters", ORG, ORG_WS)) == 1


def test_a_plain_owner_cannot_read_mail_health(client: TestClient) -> None:
    assert client.get("/api/v1/platform/mail/health", headers=h("owner_a", "owner")).status_code == 403
