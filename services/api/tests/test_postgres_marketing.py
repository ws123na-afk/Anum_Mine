"""Marketing in PostgreSQL (migration ``0040_marketing``).

The API runs as the non-owner ``anum_test_app`` role. Accounts, campaigns and posts are
isolated by tenant and workspace under forced RLS; tokens are stored only as
ciphertexts; due posts are discovered as ``anum_maintenance`` (ids, scope and times only)
and published inside each tenant's context; key rotation re-encrypts account tokens.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_A2, WORKSPACE_B
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from marketing_fakes import PAGE_TOKEN, FakePlatforms
from pydantic import SecretStr
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_marketing import SETTINGS

from anum_api import rotate_secrets
from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.marketing import clients, config as marketing_config, publisher
from anum_api.marketing.config import marketing_settings
from anum_api.marketing.store import marketing_store
from anum_api.secret_box import reset_secret_cipher
from anum_api.settings import settings

pytestmark = pytest.mark.database
BASE = "/api/v1/marketing"


def headers(tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A, user: str = "owner_test") -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user, "x-user-roles": "owner"}


A, A2, B = headers(), headers(workspace_id=WORKSPACE_A2), headers(TENANT_B, WORKSPACE_B)
client = TestClient(app)


@pytest.fixture
def fake(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakePlatforms]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    platforms = FakePlatforms()
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(marketing_config, "platform_transport", httpx.MockTransport(platforms))
    for name, value in SETTINGS.items():
        monkeypatch.setattr(marketing_settings, name, value)
    monkeypatch.setattr(clients, "POLL_SECONDS", 0.0)
    marketing_store.clear()
    yield platforms
    marketing_store.clear()


def connect_meta(h: dict[str, str]) -> list[dict]:
    started = client.post(f"{BASE}/oauth/start", headers=h, json={"provider": "meta"}).json()["authorize_url"]
    state = parse_qs(urlsplit(started).query)["state"][0]
    response = client.post(f"{BASE}/oauth/callback", headers=h, json={"code": "meta-code", "state": state})
    assert response.status_code == 200, response.text
    return response.json()


def page(h: dict[str, str]) -> dict:
    return next(item for item in client.get(f"{BASE}/accounts", headers=h).json() if item["platform"] == "facebook")


def test_rows_are_isolated_and_tokens_are_ciphertexts(fake: FakePlatforms, database_engine: Engine) -> None:
    connect_meta(A)
    post = client.post(f"{BASE}/posts", headers=A, json={"variants": [{"account_id": page(A)["id"], "text": "Tenant A only"}]}).json()[0]
    campaign = client.post(f"{BASE}/campaigns", headers=A, json={"name": "A campaign"}).json()
    client.put(f"{BASE}/preferences", headers=A, json={"brand_voice": "Calm"})
    for other in (A2, B):
        assert client.get(f"{BASE}/accounts", headers=other).json() == []
        assert client.get(f"{BASE}/posts", headers=other).json() == []
        assert client.get(f"{BASE}/posts/{post['id']}", headers=other).status_code == 404
        assert client.get(f"{BASE}/campaigns/{campaign['id']}", headers=other).status_code == 404
        assert client.get(f"{BASE}/preferences", headers=other).json()["brand_voice"] == ""
        # Another workspace cannot post to this workspace's account.
        refused = client.post(f"{BASE}/posts", headers=other, json={"variants": [{"account_id": page(A)["id"], "text": "x"}]})
        assert refused.status_code == 422
    with database_engine.connect() as connection:
        tokens = connection.execute(text("select access_token_ciphertext from marketing_accounts")).scalars().all()
        assert tokens and all(PAGE_TOKEN not in value for value in tokens)
    # As the application role without a scope, nothing is visible.
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text(f"set local role {APP_ROLE}"))
            assert connection.execute(text("select count(*) from marketing_posts")).scalar_one() == 0
            assert connection.execute(text("select count(*) from marketing_accounts")).scalar_one() == 0


def test_writes_into_another_tenant_are_refused_by_rls(fake: FakePlatforms, database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text(f"set local role {APP_ROLE}"))
                connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
                connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_A})
                connection.execute(text(
                    "insert into marketing_campaigns (id, tenant_id, workspace_id, name, status, created_by) "
                    "values ('mkcamp_x', :t, :w, 'x', 'draft', 'u')"), {"t": TENANT_B, "w": WORKSPACE_B})


def test_publisher_discovers_due_posts_as_maintenance_and_publishes_in_scope(fake: FakePlatforms, database_engine: Engine) -> None:
    connect_meta(A)
    connect_meta(B)
    ids = {}
    for h in (A, B):
        post = client.post(f"{BASE}/posts", headers=h, json={"variants": [{"account_id": page(h)["id"], "text": "Due now"}]}).json()[0]
        pending = client.post(f"{BASE}/posts/{post['id']}/schedule", headers=h, json={}).json()
        approval = pending["approval"]
        decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=h, json={"payload_hash": approval["payload_hash"]})
        assert decided.status_code == 200, decided.text
        ids[h["x-tenant-id"]] = post["id"]
    # The maintenance role sees ids and times of scheduled posts, never the copy.
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            rows = connection.execute(text("select id, tenant_id from marketing_posts")).all()
            assert {row.id for row in rows} == set(ids.values())
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("select text from marketing_posts")).all()
        with pytest.raises(DBAPIError):
            with connection.begin():
                connection.execute(text("set local role anum_maintenance"))
                connection.execute(text("select access_token_ciphertext from marketing_accounts")).all()
    assert asyncio.run(publisher.publish_due()) == 2
    for h in (A, B):
        published = client.get(f"{BASE}/posts/{ids[h['x-tenant-id']]}", headers=h).json()
        assert published["status"] == "published" and published["external_id"]
    # Published posts drop out of the maintenance role's view.
    with database_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("set local role anum_maintenance"))
            assert connection.execute(text("select count(*) from marketing_posts")).scalar_one() == 0


def test_key_rotation_re_encrypts_account_tokens(fake: FakePlatforms, database_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setattr(settings, "secrets_key", SecretStr(old.decode()))
    reset_secret_cipher()
    try:
        connect_meta(A)
        monkeypatch.setattr(settings, "secrets_key", SecretStr(f"{new.decode()},{old.decode()}"))
        reset_secret_cipher()
        from anum_api.secret_box import secret_cipher

        report = rotate_secrets.rotate_marketing_tokens(secret_cipher(), session_factory=db_session.SessionLocal)
        assert (report.scanned, report.rotated, report.failed) == (2, 2, 0)
        with database_engine.connect() as connection:
            values = connection.execute(text("select access_token_ciphertext from marketing_accounts")).scalars().all()
        assert all(Fernet(new).decrypt(value.encode()).decode() == PAGE_TOKEN for value in values)
        again = rotate_secrets.rotate_marketing_tokens(secret_cipher(), session_factory=db_session.SessionLocal)
        assert again.already_current == 2
        assert client.post(f"{BASE}/accounts/{page(A)['id']}/check", headers=A).json()["status"] == "connected"
    finally:
        reset_secret_cipher()
