"""Brevo email campaigns in PostgreSQL (migration ``0074_brevo_campaigns``), as the non-owner app role.

Both tables are under forced RLS: tenant A's connection and drafts are invisible to tenant B
and to A's other workspace, guessed ids read as missing, writes into another scope are
refused by the row policy, one Brevo campaign belongs to at most one draft in a workspace,
the API key is stored only as a ciphertext that ``anum_maintenance`` cannot read, and key
rotation re-encrypts it inside each workspace's scope. The main path (connect, draft, ask,
approve, sent) runs end to end against a fake Brevo.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from anum_api.brevo import client as brevo_client
from anum_api.brevo.store import brevo_store
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.rotate_secrets import rotate_brevo_keys
from anum_api.secret_box import SecretCipher
from anum_api.settings import settings

pytestmark = pytest.mark.database
BASE = "/api/v1/brevo"
TABLES = ("brevo_connections", "brevo_campaign_drafts")
# Plainly fake, low-entropy fixture (gitleaks).
API_KEY = "xkeysib-example-example-example"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user: str = "owner_test", roles: str = "owner") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": roles}


A, A2, B = headers(), headers(workspace_id=WORKSPACE_A2), headers(TENANT_B, WORKSPACE_B)
client = TestClient(app)


def fake_brevo(sent: list[int]) -> httpx.MockTransport:
    campaigns: dict[int, dict[str, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.brevo.com" and request.headers["api-key"] == API_KEY
        path = request.url.path[3:]
        if path == "/account":
            return httpx.Response(200, json={"email": "owner@shop.example", "companyName": "Tyre Shop", "plan": [{"type": "free"}]})
        if path.startswith("/contacts/lists/"):
            return httpx.Response(200, json={"id": int(path.rsplit("/", 1)[1]), "name": "Newsletter", "uniqueSubscribers": 10})
        if path == "/senders":
            return httpx.Response(200, json={"senders": [{"id": 1, "name": "Tyre Shop", "email": "news@shop.example", "active": True}]})
        if path == "/emailCampaigns" and request.method == "POST":
            campaign_id = 700 + len(campaigns)
            campaigns[campaign_id] = json.loads(request.content)
            return httpx.Response(201, json={"id": campaign_id})
        if path.endswith("/sendNow"):
            sent.append(int(path.split("/")[2]))
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "unknown"})

    return httpx.MockTransport(handler)


@pytest.fixture
def db(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    sent: list[int] = []
    monkeypatch.setattr(brevo_client, "transport", fake_brevo(sent))
    brevo_store.clear()
    yield sent


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def _scoped(connection: Any, tenant_id: str, workspace_id: str) -> None:
    connection.execute(text(f"set local role {APP_ROLE}"))
    connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": tenant_id})
    connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": workspace_id})


def main_path(h: dict[str, str]) -> dict[str, Any]:
    ok(client.put(f"{BASE}/connection", headers=h, json={"api_key": API_KEY}))
    draft = ok(client.post(f"{BASE}/drafts", headers=h, json={
        "name": "Winter tyres", "subject": "Fit winter tyres", "sender_id": 1, "html": "<p>Book now</p>", "list_ids": [7]}), 201)
    pending = ok(client.post(f"{BASE}/drafts/{draft['id']}/send", headers=h, json={}))
    approval = pending["approval"]
    ok(client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=h, json={"payload_hash": approval["payload_hash"]}))
    return ok(client.get(f"{BASE}/drafts/{draft['id']}", headers=h))


def test_main_path_end_to_end_under_rls(db: list[int], database_engine: Engine) -> None:
    sent = main_path(A)
    assert sent["status"] == "sent" and db == [sent["brevo_campaign_id"]]
    with database_engine.connect() as connection:
        stored = connection.execute(text("select api_key_ciphertext, key_last4 from brevo_connections")).one()
    assert API_KEY not in stored.api_key_ciphertext and stored.key_last4 == API_KEY[-4:]


def test_other_tenants_and_workspaces_cannot_read_or_guess(db: list[int], database_engine: Engine) -> None:
    sent = main_path(A)
    for other in (A2, B):
        assert ok(client.get(f"{BASE}/status", headers=other))["connected"] is False
        assert ok(client.get(f"{BASE}/drafts", headers=other))["total"] == 0
        assert client.get(f"{BASE}/drafts/{sent['id']}", headers=other).status_code == 404
        assert client.delete(f"{BASE}/drafts/{sent['id']}", headers=other).status_code == 404
        assert client.post(f"{BASE}/drafts/{sent['id']}/cancel", headers=other).status_code == 404
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            for table in TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            for table in TABLES:
                assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table
            assert connection.execute(text("update brevo_campaign_drafts set html = 'x' where id = :id"), {"id": sent["id"]}).rowcount == 0


def test_rls_refuses_writes_into_another_scope_and_a_second_draft_per_campaign(db: list[int], database_engine: Engine) -> None:
    sent = main_path(A)
    insert = text(
        "insert into brevo_campaign_drafts (id, tenant_id, workspace_id, name, subject, sender_id, sender_name, sender_email, html, "
        "content_hash, list_ids, brevo_campaign_id, created_by) values (:id, :t, :w, 'n', 's', 1, 'n', 'a@b.example', '<p>x</p>', "
        "'h', '[7]'::jsonb, :c, 'u')"
    )
    with database_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(insert, {"id": "brevodraft_x", "t": TENANT_B, "w": WORKSPACE_B, "c": None})
        with pytest.raises(DBAPIError):
            with connection.begin():
                _scoped(connection, TENANT_A, WORKSPACE_A)
                connection.execute(insert, {"id": "brevodraft_dup", "t": TENANT_A, "w": WORKSPACE_A, "c": sent["brevo_campaign_id"]})
        # The same Brevo campaign number in another workspace is another account's campaign: allowed.
        with connection.begin():
            _scoped(connection, TENANT_B, WORKSPACE_B)
            connection.execute(insert, {"id": "brevodraft_b", "t": TENANT_B, "w": WORKSPACE_B, "c": sent["brevo_campaign_id"]})


def test_maintenance_sees_scopes_only_and_rotation_re_encrypts_the_key(db: list[int], database_engine: Engine) -> None:
    old_key, new_key = Fernet.generate_key(), Fernet.generate_key()
    with database_engine.connect() as connection:
        for tenant_id, workspace_id in ((TENANT_A, WORKSPACE_A), (TENANT_B, WORKSPACE_B)):
            with connection.begin():
                _scoped(connection, tenant_id, workspace_id)
                connection.execute(text(
                    "insert into brevo_connections (tenant_id, workspace_id, api_key_ciphertext, key_last4, connected_by) "
                    "values (:t, :w, :c, 'mple', 'u')"), {"t": tenant_id, "w": workspace_id, "c": SecretCipher([old_key]).encrypt(API_KEY)})
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert len(connection.execute(text("select tenant_id, workspace_id from brevo_connections")).all()) == 2
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("select api_key_ciphertext from brevo_connections")).all()
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session: Session, transaction: Any, connection: Any) -> None:
        connection.execute(text(f"set local role {APP_ROLE}"))

    cipher = SecretCipher([new_key, old_key])
    report = rotate_brevo_keys(cipher, session_factory=factory)
    assert (report.scanned, report.rotated, report.failed) == (2, 2, 0)
    with database_engine.connect() as connection:
        rows = connection.execute(text("select api_key_ciphertext from brevo_connections")).scalars().all()
        audited = connection.execute(text("select count(*) from audit_records where action = 'secrets.rotated' and metadata->>'store' = 'brevo_connections'")).scalar_one()
    assert [SecretCipher([new_key]).decrypt(value) for value in rows] == [API_KEY, API_KEY] and audited == 2
    assert rotate_brevo_keys(cipher, session_factory=factory).already_current == 2
