"""Email Center scheduled send and attachments in PostgreSQL (migration ``0062_mail_outgoing``).

The API runs as the non-owner ``anum_test_app`` role against the fake mail server of
``test_mail_center``. Attachments are private like drafts (forced RLS plus the restrictive
owner policy); the scheduler finds due messages as ``anum_maintenance``, which sees only ids,
scope, status and times, then claims and sends each in its owner's scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_mail_center import ADDRESS, PASSWORD, FakeServer, Stored, raw_message

from anum_api.db import session as db_session
from anum_api.mailbox import outgoing, service
from anum_api.mailbox.config import mail_settings
from anum_api.mailbox.store import SEND_LEASE, DraftStatus, open_mail_store
from anum_api.main import app
from anum_api.schemas import TenantContext
from anum_api.settings import settings

pytestmark = pytest.mark.database

PDF = b"%PDF-1.4 example pdf body"


def headers(user_id: str = "owner_test", tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant_id, "x-workspace-id": workspace_id, "x-user-id": user_id, "x-user-roles": "owner"}


A = headers()
CONTEXT = TenantContext(tenant_id=TENANT_A, workspace_id=WORKSPACE_A, user_id="owner_test", roles=["owner"])


@pytest.fixture
def fake(database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeServer]:
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    with database_engine.begin() as connection:
        connection.execute(text("grant anum_maintenance to current_user"))
    server = FakeServer(folders_={"INBOX": ("inbox", [Stored(1, raw_message("Quote request 1", attach=PDF))]), "Sent": ("sent", [])})
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    monkeypatch.setattr(mail_settings, "enabled", True)
    monkeypatch.setattr(mail_settings, "imap_host", "mail.example.test")
    monkeypatch.setattr(mail_settings, "smtp_host", "mail.example.test")
    monkeypatch.setattr(mail_settings, "allowed_domains", "example.test")
    monkeypatch.setattr(service, "server_factory", server.bind)
    service.reset_connect_attempts()
    yield server
    service.reset_connect_attempts()


@pytest.fixture
def client(fake: FakeServer) -> TestClient:
    return TestClient(app)


def _scheduled_draft(client: TestClient, when: datetime) -> tuple[dict, dict]:
    account = client.post("/api/v1/mail/accounts", headers=A, json={"address": ADDRESS, "password": PASSWORD})
    assert account.status_code == 201, account.text
    draft = client.post("/api/v1/mail/drafts", headers=A, json={"account_id": account.json()["id"]}).json()
    client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=A, json={"to": ["buyer@client.example"], "subject": "Offer", "body": "Hello"})
    uploaded = client.post(f"/api/v1/mail/drafts/{draft['id']}/attachments", headers=A, params={"name": "quote.pdf"}, content=PDF)
    assert uploaded.status_code == 201, uploaded.text
    asked = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=A, json={"idempotency_key": "send-key-p001", "send_at": when.isoformat()})
    assert asked.status_code == 200, asked.text
    return draft, asked.json()["approval"]


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(f"select count(*) from {table}")).scalar_one())  # nosec B608 - fixed table names


def test_scheduled_send_end_to_end_in_postgres(client: TestClient, fake: FakeServer, database_engine: Engine) -> None:
    when = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0)
    draft, approval = _scheduled_draft(client, when)
    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    assert approved.status_code == 200, approved.text
    with database_engine.connect() as connection:
        row = connection.execute(text("select status, send_at, approved_hash from mail_drafts")).one()
    assert row.status == "scheduled" and row.send_at == when and len(row.approved_hash) == 64
    assert not fake.sent
    assert asyncio.run(outgoing.run_once(when - timedelta(seconds=1))) == {}
    assert asyncio.run(outgoing.run_once(when + timedelta(seconds=1))) == {"sent": 1}
    assert len(fake.sent) == 1 and b"quote.pdf" in fake.sent[0][2]
    assert asyncio.run(outgoing.run_once(when + timedelta(minutes=2))) == {}
    with database_engine.connect() as connection:
        assert connection.execute(text("select status from mail_drafts")).scalar_one() == "sent"
        actions = set(connection.execute(text("select action from audit_records")).scalars())
    assert {"mail.attachment.add", "mail.send.requested", "mail.send.scheduled"} <= actions
    assert client.get("/api/v1/mail/scheduled", headers=A).json() == []
    client.delete(f"/api/v1/mail/drafts/{draft['id']}", headers=A)
    assert _count(database_engine, "mail_attachments") == 0


def test_maintenance_sees_only_due_rows_and_their_times(client: TestClient, database_engine: Engine) -> None:
    when = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0)
    _scheduled_draft(client, when)
    plain = client.post("/api/v1/mail/drafts", headers=A, json={"account_id": client.get("/api/v1/mail/setup", headers=A).json()["accounts"][0]["id"]}).json()
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("set local role anum_maintenance"))
        visible = connection.execute(text("select id, status, send_at from mail_drafts")).all()
        assert [row.status for row in visible] == ["pending_approval"] and plain["id"] not in {row.id for row in visible}
        transaction.rollback()
    for column in ("subject", "body", "to_addresses", "approved_hash"):
        with database_engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("set local role anum_maintenance"))
            with pytest.raises(DBAPIError):
                connection.execute(text(f"select {column} from mail_drafts"))  # nosec B608 - fixed column names
            transaction.rollback()
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("set local role anum_maintenance"))
        with pytest.raises(DBAPIError):
            connection.execute(text("select filename from mail_attachments"))
        transaction.rollback()


def test_attachments_are_private_to_their_owner(client: TestClient, database_engine: Engine) -> None:
    when = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0)
    draft, _ = _scheduled_draft(client, when)
    file_id = client.get(f"/api/v1/mail/drafts/{draft['id']}", headers=A).json()["attachments"][0]["id"]
    for other in (headers(user_id="second_owner"), headers(tenant_id=TENANT_B, workspace_id=WORKSPACE_B)):
        assert client.get(f"/api/v1/mail/drafts/{draft['id']}/attachments/{file_id}", headers=other).status_code == 404
        assert client.get("/api/v1/mail/scheduled", headers=other).json() == []
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        connection.execute(text("select set_config('anum.tenant_id', :t, true)"), {"t": TENANT_A})
        connection.execute(text("select set_config('anum.workspace_id', :w, true)"), {"w": WORKSPACE_A})
        connection.execute(text("select set_config('anum.user_id', 'second_owner', true)"))
        assert connection.execute(text("select count(*) from mail_attachments")).scalar_one() == 0
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "insert into mail_attachments (id, tenant_id, workspace_id, owner_user_id, draft_id, filename, content_type, size_bytes, sha256, storage_key) "
                    "values ('mailfile_x', :t, :w, 'owner_test', :d, 'x.txt', 'text/plain', 1, 'aa', 'k')"
                ),
                {"t": TENANT_A, "w": WORKSPACE_A, "d": draft["id"]},
            )
        transaction.rollback()


def test_claims_are_compare_and_set_and_a_lapsed_lease_is_unknown(client: TestClient, fake: FakeServer, database_engine: Engine) -> None:
    when = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0)
    draft, approval = _scheduled_draft(client, when)
    client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=A, json={"payload_hash": approval["payload_hash"]})
    due = when + timedelta(seconds=1)
    with open_mail_store(CONTEXT) as store:
        assert store.claim_scheduled(draft["id"], due, due + SEND_LEASE) is True
    with open_mail_store(CONTEXT) as store:
        assert store.claim_scheduled(draft["id"], due, due + SEND_LEASE) is False
        assert store.reset_to_draft(draft["id"], DraftStatus.SCHEDULED) is False
    assert client.delete(f"/api/v1/mail/drafts/{draft['id']}", headers=A).status_code == 409
    with open_mail_store(CONTEXT) as store:
        assert store.release_interrupted(draft["id"], due) is False
    assert asyncio.run(outgoing.run_once(due + SEND_LEASE + timedelta(seconds=1))) == {"interrupted": 1}
    with database_engine.connect() as connection:
        row = connection.execute(text("select status, send_lease_until from mail_drafts")).one()
    assert row.status == "unknown" and row.send_lease_until is None and not fake.sent
