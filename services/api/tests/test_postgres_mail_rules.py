"""Mailbox settings, forwarding rules, background sync and the retention purge in PostgreSQL
(migration 0068_mail_rules_retention, docs/email.md#mailbox-settings).

The API runs as the non-owner ``anum_test_app`` role against the fake mail server of
``test_mail_center``; discovery runs as ``anum_maintenance``. Direct SQL checks run as the app
role inside one person's RLS context, so the row policies themselves are tested. Every address
and password is a plainly fake fixture.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_mail_center import PASSWORD, FakeServer, Stored, fake, raw_message  # noqa: F401

from anum_api import mail_retention
from anum_api.db import session as db_session
from anum_api.mailbox import sync
from anum_api.mailbox.store import mail_backend
from anum_api.main import app
from anum_api.schemas import utc_now
from anum_api.settings import settings

pytestmark = pytest.mark.database

MAIL = "/api/v1/mail"
FRIEND = "partner@elsewhere.example"
NEW_TABLES = ["mail_account_settings", "mail_forward_log", "mail_forward_rules", "mail_sync_state"]


def headers(user: str, role: str = "owner", tenant: str = TENANT_A, workspace: str = WORKSPACE_A) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": role}


OWNER = headers("owner_pg")
MEMBER = headers("member_pg", "member")
OTHER = headers("other_pg", "member")
STRANGER = headers("owner_pg", tenant=TENANT_B, workspace=WORKSPACE_B)


@pytest.fixture
def client(database_engine: Engine, seed_scopes: None, fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:  # noqa: F811
    factory = sessionmaker(bind=database_engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    with database_engine.begin() as connection:
        connection.execute(text("grant anum_maintenance to current_user"))
        for tenant, workspace, user, role in (
            (TENANT_A, WORKSPACE_A, "owner_pg", "owner"), (TENANT_A, WORKSPACE_A, "member_pg", "member"),
            (TENANT_A, WORKSPACE_A, "other_pg", "member"), (TENANT_B, WORKSPACE_B, "owner_pg", "owner"),
        ):
            connection.execute(
                text("insert into workspace_memberships (tenant_id, workspace_id, user_id, role) values (:t, :w, :u, :r)"),
                {"t": tenant, "w": workspace, "u": user, "r": role},
            )
    yield TestClient(app)
    mail_backend.clear()


def as_user(engine: Engine, user: str, sql: str, params: dict | None = None, *, tenant: str = TENANT_A, workspace: str = WORKSPACE_A):  # type: ignore[no-untyped-def]
    """Run ``sql`` as the application role inside one person's RLS context (rolled back)."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"set local role {APP_ROLE}"))
            for key, value in (("anum.tenant_id", tenant), ("anum.workspace_id", workspace), ("anum.user_id", user)):
                connection.execute(text("select set_config(:k, :v, true)"), {"k": key, "v": value})
            result = connection.execute(text(sql), params or {})
            return result.all() if result.returns_rows else result.rowcount
        finally:
            transaction.rollback()


def as_maintenance(engine: Engine, sql: str):  # type: ignore[no-untyped-def]
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("set local role anum_maintenance"))
            return connection.execute(text(sql)).all()
        finally:
            transaction.rollback()


def approve(client: TestClient, approval: dict) -> int:
    return client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}).status_code


def connect(client: TestClient) -> dict:
    response = client.post(f"{MAIL}/accounts", headers=OWNER, json={"address": "owner@example.test", "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def change(client: TestClient, account: dict, version: int, **values: object) -> dict:
    body = {"sent_retention_days": None, "draft_retention_days": None, "label_retention_days": None, "background_sync": True, "base_version": version, **values}
    response = client.post(f"{MAIL}/accounts/{account['id']}/settings", headers=OWNER, json=body)
    assert response.status_code == 200, response.text
    return response.json()["approval"]


def test_new_tables_have_forced_rls(database_engine: Engine, client: TestClient) -> None:
    with database_engine.connect() as connection:
        rows = connection.execute(
            text("select relname from pg_class where relname = any(:t) and relrowsecurity and relforcerowsecurity"), {"t": NEW_TABLES}
        ).scalars().all()
    assert sorted(rows) == NEW_TABLES


def test_settings_rules_and_sync_end_to_end(database_engine: Engine, client: TestClient, fake: FakeServer) -> None:  # noqa: F811
    account = connect(client)
    assert approve(client, change(client, account, 0, sent_retention_days=30)) == 200
    shown = client.get(f"{MAIL}/accounts/{account['id']}/settings", headers=OWNER).json()
    assert shown["sent_retention_days"] == 30 and shown["version"] == 1
    created = client.post(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER, json={"name": "Orders", "from_contains": "client.example", "forward_to": [FRIEND]})
    assert created.status_code == 201, created.text
    # The database refuses an active rule whose approved hash is not its own.
    with pytest.raises(DBAPIError):
        as_user(database_engine, "owner_pg", "update mail_forward_rules set status = 'active'")
    assert approve(client, created.json()["approval"]) == 200
    assert asyncio.run(sync.run_once()) == {"checked": 1}
    fake.next_uid += 1
    fake.folders_["INBOX"][1].append(Stored(fake.next_uid, raw_message("Order 7")))
    with database_engine.begin() as connection:
        connection.execute(text("update mail_sync_state set next_sync_at = now() - interval '1 second'"))
    assert asyncio.run(sync.run_once()) == {"forwarded": 1}
    assert [recipients for _, recipients, _ in fake.sent] == [[FRIEND]]
    with database_engine.connect() as connection:
        assert connection.execute(text("select status from mail_forward_log")).scalars().all() == ["forwarded"]
        assert connection.execute(text("select inbox_unread, status, lease_until from mail_sync_state")).one() == (31, "ok", None)
    assert client.get(f"{MAIL}/unread", headers=OWNER).json()["total"] == 31
    # Another replica's stale discovery result claims nothing (checked since it was found due).
    assert asyncio.run(sync.handle_mailbox(TENANT_A, WORKSPACE_A, "owner_pg", account["id"])) == "skipped"


def test_grantees_read_settings_and_counts_but_never_write_and_rules_stay_private(database_engine: Engine, client: TestClient, fake: FakeServer) -> None:  # noqa: F811
    account = connect(client)
    assert client.post(f"{MAIL}/accounts/{account['id']}/grants", headers=OWNER, json={"grantee_user_id": "member_pg", "access": "send"}).status_code == 201
    assert approve(client, change(client, account, 0, label_retention_days=90)) == 200
    client.post(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER, json={"name": "Orders", "subject_contains": "Order", "forward_to": [FRIEND]})
    asyncio.run(sync.run_once())
    assert client.get(f"{MAIL}/accounts/{account['id']}/settings", headers=MEMBER).json()["label_retention_days"] == 90
    assert client.get(f"{MAIL}/unread", headers=MEMBER).json()["total"] == 30
    assert as_user(database_engine, "member_pg", "select count(*) from mail_account_settings") == [(1,)]
    assert as_user(database_engine, "member_pg", "select count(*) from mail_sync_state") == [(1,)]
    assert as_user(database_engine, "member_pg", "select count(*) from mail_forward_rules") == [(0,)]
    assert as_user(database_engine, "member_pg", "update mail_account_settings set label_retention_days = 7") == 0
    assert as_user(database_engine, "member_pg", "update mail_sync_state set inbox_unread = 0") == 0
    for user in ("other_pg",):
        assert as_user(database_engine, user, "select count(*) from mail_account_settings") == [(0,)]
        assert as_user(database_engine, user, "select count(*) from mail_sync_state") == [(0,)]
    assert as_user(database_engine, "owner_pg", "select count(*) from mail_forward_rules", tenant=TENANT_B, workspace=WORKSPACE_B) == [(0,)]
    assert client.get(f"{MAIL}/accounts/{account['id']}/settings", headers=STRANGER).status_code == 404
    with pytest.raises(DBAPIError):
        as_user(
            database_engine, "member_pg",
            "insert into mail_forward_rules (id, tenant_id, workspace_id, owner_user_id, account_id, name, subject_contains, forward_to, status, rule_hash)"
            " values ('mrule_x', :t, :w, 'member_pg', :a, 'x', 'y', '[]', 'paused', 'h')",
            {"t": TENANT_A, "w": WORKSPACE_A, "a": account["id"]},
        )


def test_maintenance_sees_only_scheduling_and_retention_columns(database_engine: Engine, client: TestClient, fake: FakeServer) -> None:  # noqa: F811
    account = connect(client)
    assert approve(client, change(client, account, 0, draft_retention_days=7)) == 200
    client.post(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER, json={"name": "Orders", "subject_contains": "Order", "forward_to": [FRIEND]})
    asyncio.run(sync.run_once())
    client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": account["id"]})
    assert len(as_maintenance(database_engine, "select account_id, next_sync_at, lease_until from mail_sync_state")) == 1
    assert as_maintenance(database_engine, "select draft_retention_days from mail_account_settings") == [(7,)]
    assert len(as_maintenance(database_engine, "select account_id, owner_user_id from mail_drafts")) == 1
    for sql in (
        "select inbox_unread from mail_sync_state", "select last_error from mail_sync_state", "select version from mail_account_settings",
        "select forward_to from mail_forward_rules", "select id from mail_forward_rules", "select message_id from mail_forward_log",
        "select subject from mail_drafts", "select category from mail_labels",
    ):
        with pytest.raises(DBAPIError):
            as_maintenance(database_engine, sql)
    # Without a retention, the purge's policy shows the maintenance role no drafts.
    assert approve(client, change(client, account, 1)) == 200
    assert as_maintenance(database_engine, "select account_id from mail_drafts") == []


def test_retention_purge_as_maintenance_then_each_writer(database_engine: Engine, client: TestClient, fake: FakeServer) -> None:  # noqa: F811
    account = connect(client)
    assert client.post(f"{MAIL}/accounts/{account['id']}/grants", headers=OWNER, json={"grantee_user_id": "member_pg", "access": "send"}).status_code == 201
    mine = client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    theirs = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).json()
    fresh = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).json()
    old = utc_now() - timedelta(days=10)
    with database_engine.begin() as connection:
        connection.execute(text("update mail_drafts set updated_at = :old where id = any(:ids)"), {"old": old, "ids": [mine["id"], theirs["id"]]})
        connection.execute(text("update mail_drafts set status = 'sent', sent_at = :old where id = :id"), {"old": old, "id": mine["id"]})
    assert approve(client, change(client, account, 0, sent_retention_days=7, draft_retention_days=7)) == 200
    counted = mail_retention.purge_mail_retention(db_session.SessionLocal, dry_run=True)
    assert (counted.mailboxes_scanned, counted.sent, counted.drafts) == (1, 1, 1)
    done = mail_retention.purge_mail_retention(db_session.SessionLocal)
    assert (done.sent, done.drafts) == (1, 1)
    with database_engine.connect() as connection:
        assert connection.execute(text("select id from mail_drafts")).scalars().all() == [fresh["id"]]
