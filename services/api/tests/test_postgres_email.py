"""Unified inbox in PostgreSQL (migration 0031_email_inbox, docs/email.md).

The API runs as the non-owner ``anum_test_app`` role against fake mail providers.
Mailboxes, messages, drafts and pending connections are scoped to their tenant and
workspace by forced RLS; "restart" clears the in-memory store so everything is read back
from PostgreSQL.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import timedelta

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_email import FakeGateway, FakeMail, gmail_message

from anum_api import email_inbox
from anum_api.db import session as db_session
from anum_api.email_inbox import EmailSyncScheduler, email_backend, email_settings
from anum_api.main import app
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user_id: str = "owner_test") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "owner"}


A = headers()


@pytest.fixture
def fake(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMail]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    mail = FakeMail()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(email_inbox, "email_transport", httpx.MockTransport(mail))
    for name, value in {
        "google_client_id": "example-google-client",
        "microsoft_client_id": "example-azure-client",
        "zoho_client_id": "example-zoho-client",
        "zoho_data_centre": "sa",
        "public_api_url": "https://api.shop.example",
        "web_url": "https://app.shop.example",
    }.items():
        monkeypatch.setattr(email_settings, name, value)
    for name in ("google_client_secret", "microsoft_client_secret", "zoho_client_secret"):
        monkeypatch.setattr(email_settings, name, SecretStr("example"))
    gateway = FakeGateway()
    email_inbox.set_model_gateway(gateway)
    email_backend.clear()
    yield mail
    email_inbox.set_model_gateway(None)
    email_backend.clear()


@pytest.fixture
def client(fake: FakeMail) -> TestClient:
    return TestClient(app)


def _connect(client: TestClient, provider: str = "gmail", who: dict[str, str] = A, visibility: str = "workspace") -> dict:
    from urllib.parse import parse_qs, urlsplit

    started = client.post(f"/api/v1/email/oauth/{provider}/start", headers=who, json={"visibility": visibility})
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    back = client.get("/api/v1/email/oauth/callback", params={"state": state, "code": "example-code"}, follow_redirects=False)
    nonce = parse_qs(urlsplit(back.headers["location"]).query)["email_connect"][0]
    done = client.post("/api/v1/email/oauth/complete", headers=who, json={"connection": nonce})
    assert done.status_code == 201, done.text
    return done.json()


def _count(database_engine: Engine, table: str) -> int:
    with database_engine.connect() as connection:
        return int(connection.execute(text(f"select count(*) from {table}")).scalar_one())


def test_connect_sync_read_and_send_persist_in_postgresql(client: TestClient, fake: FakeMail, database_engine: Engine) -> None:
    account = _connect(client)
    assert account["message_count"] == 3
    assert _count(database_engine, "email_accounts") == 1 and _count(database_engine, "email_messages") == 3
    assert _count(database_engine, "email_oauth_requests") == 0  # consumed
    with database_engine.connect() as connection:
        stored = connection.execute(text("select refresh_token_enc, access_token_enc from email_accounts")).one()
    assert "gmail-refresh-1" not in stored.refresh_token_enc and "gmail-access-1" not in stored.access_token_enc

    page = client.get("/api/v1/email/messages", headers=A, params={"limit": 2}).json()
    rest = client.get("/api/v1/email/messages", headers=A, params={"limit": 2, "before": page["next_cursor"]}).json()
    assert len(page["items"]) == 2 and len(rest["items"]) == 1
    found = client.get("/api/v1/email/messages", headers=A, params={"q": "bridge_stone%"}).json()["items"]
    assert found == []  # LIKE wildcards in the query are literal
    first = client.get("/api/v1/email/messages", headers=A, params={"q": "tyres"}).json()["items"][0]

    # Incremental sync merges new metadata without losing the stored triage.
    fake.gmail["g5"] = gmail_message("g5", subject="Order 9 cancelled?", sender="x@gmail.com", labels=["INBOX", "UNREAD"], internal=1760000500000)
    fake.history = [{"messagesAdded": [{"message": {"id": "g5", "labelIds": ["INBOX", "UNREAD"]}}]}]
    fake.history_id = 130
    assert client.post(f"/api/v1/email/messages/{first['id']}/analyze", headers=A).status_code == 200
    synced = client.post(f"/api/v1/email/accounts/{account['id']}/sync", headers=A).json()
    assert synced["message_count"] == 4
    again = client.get(f"/api/v1/email/messages/{first['id']}", headers=A, params={"body": "false"}).json()["message"]
    assert again["triage_source"] == "model" and again["summary"].startswith("Sara asks")

    draft = client.post(f"/api/v1/email/messages/{first['id']}/drafts", headers=A, json={}).json()["draft"]
    approval = client.post(f"/api/v1/email/drafts/{draft['id']}/send-approval", headers=A).json()["approval"]
    email_backend.clear()  # restart: read back from PostgreSQL
    sent = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    assert sent.status_code == 200, sent.text
    assert len(fake.gmail_sent) == 1
    raw = base64.urlsafe_b64decode(fake.gmail_sent[0]["raw"] + "==").decode()
    assert "205/55R16 is in stock" in raw
    with database_engine.connect() as connection:
        assert connection.execute(text("select status from email_drafts")).scalar_one() == "sent"
        actions = set(connection.execute(text("select action from audit_records")).scalars())
    assert {"email.account.connected", "email.send.requested"} <= actions

    result = client.delete(f"/api/v1/email/accounts/{account['id']}", headers=A).json()
    assert result["revoked_at_provider"] is True
    for table in ("email_accounts", "email_messages", "email_drafts"):
        assert _count(database_engine, table) == 0


def test_mailboxes_are_isolated_by_tenant_workspace_and_privacy(client: TestClient, fake: FakeMail) -> None:
    _connect(client, visibility="private")
    message_id = client.get("/api/v1/email/messages", headers=A).json()["items"][0]["id"]
    for other in (headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B), headers(user_id="second_owner")):
        assert client.get("/api/v1/email/accounts", headers=other).json() == []
        assert client.get("/api/v1/email/messages", headers=other).json()["items"] == []
        assert client.get(f"/api/v1/email/messages/{message_id}", headers=other).status_code == 404


def test_forced_rls_refuses_rows_outside_the_session_scope(fake: FakeMail, client: TestClient, database_engine: Engine) -> None:
    _connect(client)
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_B})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_B})
        assert connection.execute(text("select count(*) from email_messages")).scalar_one() == 0
        assert connection.execute(text("select count(*) from email_accounts")).scalar_one() == 0
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "insert into email_accounts (id, tenant_id, workspace_id, provider, address, connected_by) "
                    "values ('emb_x', :t, :w, 'gmail', 'a@b.example', 'u')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A},
            )
        transaction.rollback()


def test_sync_lease_lets_only_one_pass_run(fake: FakeMail, client: TestClient) -> None:
    from anum_api.email_inbox import open_email_store

    account = _connect(client)
    context = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="owner_test", roles=["owner"])
    now = utc_now()
    with open_email_store(context) as store:
        assert store.claim_sync(account["id"], now, now + timedelta(minutes=3)) is True
    with open_email_store(context) as store:
        assert store.claim_sync(account["id"], now, now + timedelta(minutes=3)) is False
    with open_email_store(context) as store:
        assert store.claim_sync(account["id"], now + timedelta(minutes=4), now + timedelta(minutes=7)) is True


def test_background_sync_discovers_accounts_as_anum_maintenance_only(fake: FakeMail, client: TestClient, database_engine: Engine) -> None:
    account = _connect(client)
    with database_engine.begin() as connection:
        connection.execute(text("grant anum_maintenance to current_user"))
        connection.execute(text("update email_accounts set last_sync_at = now() - interval '1 hour'"))
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("set local role anum_maintenance"))
        visible = connection.execute(text("select id, tenant_id, workspace_id from email_accounts")).all()
        assert [row.id for row in visible] == [account["id"]]
        with pytest.raises(DBAPIError):
            connection.execute(text("select refresh_token_enc from email_accounts"))
        transaction.rollback()
    fake.history_id = 140
    import asyncio

    done = asyncio.run(EmailSyncScheduler(60).run_once())
    assert done == 1
    with database_engine.connect() as connection:
        assert connection.execute(text("select last_sync_at > now() - interval '1 minute' from email_accounts")).scalar_one() is True
