"""WhatsApp inbox in PostgreSQL (migration 0031_whatsapp, docs/whatsapp.md#storage).

The API runs as the non-owner ``anum_test_app`` role against a fake Meta. Rows are
scoped by forced RLS; the webhook finds the owning workspace as ``anum_maintenance``,
which sees only the routing columns. "Restart" clears the in-memory store so everything
is read back from PostgreSQL.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_whatsapp import APP_SECRET, PHONE_ID, TOKEN, VERIFY, WABA_ID, FakeMeta, inbound, sign

from anum_api import whatsapp
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings
from anum_api.whatsapp import whatsapp_settings
from anum_api.whatsapp_store import route_phone_numbers, verify_token_known, whatsapp_store

pytestmark = pytest.mark.database

BASE = "/api/v1/whatsapp"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, role: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": f"{role}_test", "x-user-roles": role}


A = headers()


@pytest.fixture
def meta(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMeta]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    fake = FakeMeta()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(whatsapp, "graph_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(whatsapp_settings, "graph_api_version", "v23.0")
    whatsapp.reset_limits()
    whatsapp_store.clear()
    yield fake
    whatsapp_store.clear()
    whatsapp.reset_limits()


@pytest.fixture
def client(meta: FakeMeta) -> TestClient:
    return TestClient(app)


def connect(client: TestClient, scope: dict[str, str] = A, phone_id: str = PHONE_ID) -> None:
    response = client.put(f"{BASE}/account", headers=scope, json={
        "phone_number_id": phone_id, "business_account_id": WABA_ID, "access_token": TOKEN,
        "app_secret": APP_SECRET, "verify_token": VERIFY,
    })
    assert response.status_code == 200, response.text


def deliver(client: TestClient, payload: dict) -> httpx.Response:  # type: ignore[type-arg]
    body = json.dumps(payload).encode()
    return client.post(f"{BASE}/webhook", content=body, headers={"x-hub-signature-256": sign(body)})


def test_secrets_are_encrypted_at_rest_and_messages_persist(client: TestClient, database_engine: Engine) -> None:
    connect(client)
    with database_engine.connect() as connection:
        row = connection.execute(text("select access_token_ciphertext, app_secret_ciphertext, verify_token_hash from whatsapp_accounts")).one()
    assert TOKEN not in row[0] and APP_SECRET not in row[1] and VERIFY not in row[2] and len(row[2]) == 64

    assert deliver(client, inbound()).json()["messages"] == 1
    assert deliver(client, inbound()).json()["duplicates"] == 1  # unique wamid per workspace
    whatsapp_store.clear()  # restart: read back from PostgreSQL
    conversations = client.get(f"{BASE}/conversations", headers=A).json()
    assert len(conversations) == 1 and conversations[0]["unread_count"] == 1
    thread = client.get(f"{BASE}/conversations/{conversations[0]['id']}", headers=A).json()
    assert [m["body"] for m in thread["messages"]] == ["Hello, is 205/55R16 in stock?"]

    approval = client.post(f"{BASE}/conversations/{conversations[0]['id']}/send", headers=A, json={"text": "Yes"}).json()["approval"]
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    assert decided.status_code == 200, decided.text
    assert client.get(f"{BASE}/conversations/{conversations[0]['id']}", headers=A).json()["messages"][-1]["status"] == "sent"


def test_rows_are_isolated_by_tenant_and_workspace(client: TestClient) -> None:
    connect(client)
    deliver(client, inbound())
    conversation_id = client.get(f"{BASE}/conversations", headers=A).json()[0]["id"]
    for other in (headers(workspace_id=WORKSPACE_A2), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)):
        assert client.get(f"{BASE}/account", headers=other).json()["configured"] is False
        assert client.get(f"{BASE}/conversations", headers=other).json() == []
        assert client.get(f"{BASE}/conversations/{conversation_id}", headers=other).status_code == 404


def test_one_phone_number_one_workspace_across_tenants(client: TestClient) -> None:
    connect(client)
    other = headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)
    taken = client.put(f"{BASE}/account", headers=other, json={
        "phone_number_id": PHONE_ID, "business_account_id": WABA_ID, "access_token": TOKEN,
        "app_secret": APP_SECRET, "verify_token": VERIFY,
    })
    assert taken.status_code == 409
    assert client.get(f"{BASE}/account", headers=other).json()["configured"] is False


def test_webhook_routes_each_number_to_its_own_tenant(client: TestClient) -> None:
    connect(client)
    connect(client, headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B), phone_id="222222222222")
    assert route_phone_numbers({PHONE_ID, "222222222222", "333333333333"}) == {
        PHONE_ID: (TENANT_A, WORKSPACE_A),
        "222222222222": (TENANT_B, WORKSPACE_B),
    }
    assert verify_token_known(VERIFY) and not verify_token_known("not-the-verify-token")
    assert deliver(client, inbound(phone_id="222222222222", wamid="wamid.b")).status_code == 200
    assert client.get(f"{BASE}/conversations", headers=A).json() == []
    assert len(client.get(f"{BASE}/conversations", headers=headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)).json()) == 1


def test_maintenance_role_sees_routing_columns_only(meta: FakeMeta, client: TestClient, database_engine: Engine) -> None:
    connect(client)
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("set local role anum_maintenance"))
        assert connection.execute(text("select phone_number_id, tenant_id from whatsapp_accounts")).all() == [(PHONE_ID, TENANT_A)]
        with pytest.raises(DBAPIError):
            connection.execute(text("select access_token_ciphertext from whatsapp_accounts"))
        transaction.rollback()
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("set local role anum_maintenance"))
        with pytest.raises(DBAPIError):
            connection.execute(text("select body from whatsapp_messages"))
        transaction.rollback()
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("set local role anum_maintenance"))
        with pytest.raises(DBAPIError):
            connection.execute(text("update whatsapp_accounts set triage_enabled = false"))
        transaction.rollback()


def test_app_role_without_scope_sees_nothing_and_cannot_write_elsewhere(meta: FakeMeta, client: TestClient, database_engine: Engine) -> None:
    connect(client)
    deliver(client, inbound())
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        for table in ("whatsapp_accounts", "whatsapp_contacts", "whatsapp_conversations", "whatsapp_messages"):
            assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0  # nosec B608 - fixed names
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_A})
        with pytest.raises(DBAPIError):
            connection.execute(
                text("insert into whatsapp_contacts (id, tenant_id, workspace_id, wa_id) values ('wac_x', :t, :w, '966500000009')"),
                {"t": TENANT_B, "w": WORKSPACE_B},
            )
        transaction.rollback()
