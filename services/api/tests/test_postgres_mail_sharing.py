"""Email Center shares, identities and contacts in PostgreSQL (migration 0061_mail_sharing_contacts).

The API runs as the non-owner ``anum_test_app`` role against the fake mail server of
``test_mail_center``. Direct SQL checks run as that role with the tenant, workspace and user
settings, so the row policies themselves are tested, not only the queries. Every address and
password is a plainly fake fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_mail_center import PASSWORD, FakeServer, fake  # noqa: F401

from anum_api.db import session as db_session
from anum_api.mailbox.store import mail_backend
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database

MAIL = "/api/v1/mail"


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


def test_new_tables_have_forced_rls(database_engine: Engine, client: TestClient) -> None:
    with database_engine.connect() as connection:
        rows = connection.execute(
            text("select relname from pg_class where relname = any(:t) and relrowsecurity and relforcerowsecurity"),
            {"t": ["mail_account_grants", "mail_identities", "mail_contacts", "mail_accounts"]},
        ).scalars().all()
    assert sorted(rows) == ["mail_account_grants", "mail_accounts", "mail_contacts", "mail_identities"]


def test_shares_identities_and_contacts_through_the_api(database_engine: Engine, client: TestClient, fake: FakeServer) -> None:  # noqa: F811
    account = client.post(f"{MAIL}/accounts", headers=OWNER, json={"address": "owner@example.test", "password": PASSWORD}).json()
    base = f"{MAIL}/accounts/{account['id']}"
    assert client.get(f"{base}/folders", headers=MEMBER).status_code == 404
    assert [c["user_id"] for c in client.get(f"{base}/share-candidates", headers=OWNER).json()] == ["member_pg", "other_pg"]
    grant = client.post(f"{base}/grants", headers=OWNER, json={"grantee_user_id": "member_pg", "access": "send"})
    assert grant.status_code == 201, grant.text
    identity = client.post(f"{base}/identities", headers=OWNER, json={"address": "sales@example.test", "signature": "Sales", "is_default": True}).json()

    shared = client.get(f"{MAIL}/setup", headers=MEMBER).json()["accounts"]
    assert [(a["id"], a["access"]) for a in shared] == [(account["id"], "send")]
    assert client.get(f"{base}/folders", headers=MEMBER).status_code == 200
    assert [i["id"] for i in client.get(f"{base}/identities", headers=MEMBER).json()] == [identity["id"]]
    draft = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).json()
    assert draft["from_address"] == "sales@example.test"
    client.patch(f"{MAIL}/drafts/{draft['id']}", headers=MEMBER, json={"to": ["buyer@client.example"], "subject": "Offer"})
    approval = client.post(f"{MAIL}/drafts/{draft['id']}/send", headers=MEMBER, json={"idempotency_key": "send-key-pg-share-1"}).json()["approval"]
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}).status_code == 200
    assert len(fake.sent) == 1 and fake.sent[0][0] == "sales@example.test"
    with database_engine.connect() as connection:
        assert connection.execute(text("select status, owner_user_id from mail_drafts")).one() == ("sent", "member_pg")
    # The member's health check of a shared mailbox never writes the owner's row.
    assert client.post(f"{base}/check", headers=MEMBER).status_code == 200

    # Read only again: no drafts; withdrawn: nothing at all.
    client.patch(f"{MAIL}/grants/{grant.json()['id']}", headers=OWNER, json={"access": "read"})
    assert client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).status_code == 403
    assert client.get(f"{base}/identities", headers=MEMBER).json() == []
    assert client.delete(f"{MAIL}/grants/{grant.json()['id']}", headers=MEMBER).status_code == 204
    assert client.get(f"{base}/folders", headers=MEMBER).status_code == 404

    # Contacts: private, searchable, paged.
    for index in range(12):
        assert client.post(f"{MAIL}/contacts", headers=OWNER, json={"name": f"Buyer {index:02d}", "email": f"buyer{index:02d}@shop.example"}).status_code == 201
    assert client.post(f"{MAIL}/contacts", headers=OWNER, json={"email": "BUYER01@shop.example"}).status_code == 409
    first = client.get(f"{MAIL}/contacts", headers=OWNER, params={"limit": 5}).json()
    seen = [c["name"] for c in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = client.get(f"{MAIL}/contacts", headers=OWNER, params={"limit": 5, "cursor": cursor}).json()
        seen += [c["name"] for c in page["items"]]
        cursor = page["next_cursor"]
    assert seen == [f"Buyer {index:02d}" for index in range(12)] and first["total"] == 12
    assert client.get(f"{MAIL}/contacts", headers=OWNER, params={"q": "buyer1_"}).json()["total"] == 0
    assert client.get(f"{MAIL}/contacts", headers=OWNER, params={"q": "buyer1"}).json()["total"] == 2
    assert client.get(f"{MAIL}/contacts", headers=MEMBER).json()["total"] == 0
    assert client.get(f"{MAIL}/contacts", headers=STRANGER).json()["total"] == 0
    # Privacy: delete my data removes the contacts, mailboxes, shares and identities.
    assert client.post("/api/v1/privacy/delete", headers=OWNER, json={"confirm": "DELETE MY DATA"}).status_code == 200
    with database_engine.connect() as connection:
        for table in ("mail_accounts", "mail_account_grants", "mail_identities", "mail_contacts"):
            assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table


def test_row_policies_keep_shares_owner_written(database_engine: Engine, client: TestClient) -> None:
    account = client.post(f"{MAIL}/accounts", headers=OWNER, json={"address": "owner@example.test", "password": PASSWORD}).json()
    aid = account["id"]
    # Before any share the member's role sees nothing.
    assert as_user(database_engine, "member_pg", "select id from mail_accounts") == []
    # A member cannot write a share for someone else's mailbox: the check and the foreign key refuse it.
    forged = (
        "insert into mail_account_grants (id, tenant_id, workspace_id, owner_user_id, account_id, grantee_user_id, access) "
        "values ('g_forged', :t, :w, :owner, :a, 'member_pg', 'send')"
    )
    for owner in ("member_pg", "owner_pg"):
        with pytest.raises(DBAPIError):
            as_user(database_engine, "member_pg", forged, {"t": TENANT_A, "w": WORKSPACE_A, "owner": owner, "a": aid})
    # The owner can; then the member reads the mailbox but cannot change it or the share.
    grant = client.post(f"{MAIL}/accounts/{aid}/grants", headers=OWNER, json={"grantee_user_id": "member_pg", "access": "read"}).json()
    assert [row.id for row in as_user(database_engine, "member_pg", "select id from mail_accounts")] == [aid]
    assert as_user(database_engine, "other_pg", "select id from mail_accounts") == []
    assert as_user(database_engine, "member_pg", "update mail_accounts set display_name = 'x'") == 0
    assert as_user(database_engine, "member_pg", "delete from mail_accounts") == 0
    with pytest.raises(DBAPIError):
        as_user(database_engine, "member_pg", "update mail_account_grants set access = 'send'")
    assert as_user(database_engine, "other_pg", "select id from mail_account_grants") == []
    # Identities: only senders see them; nobody but the owner writes them.
    client.post(f"{MAIL}/accounts/{aid}/identities", headers=OWNER, json={"address": "sales@example.test"})
    assert as_user(database_engine, "member_pg", "select id from mail_identities") == []
    client.patch(f"{MAIL}/grants/{grant['id']}", headers=OWNER, json={"access": "send"})
    assert len(as_user(database_engine, "member_pg", "select id from mail_identities")) == 1
    assert as_user(database_engine, "member_pg", "update mail_identities set signature = 'x'") == 0
    with pytest.raises(DBAPIError):
        as_user(
            database_engine, "member_pg",
            "insert into mail_identities (id, tenant_id, workspace_id, owner_user_id, account_id, address) values ('i_forged', :t, :w, 'member_pg', :a, 'x@example.test')",
            {"t": TENANT_A, "w": WORKSPACE_A, "a": aid},
        )
    # Another tenant sees none of it, even with the same user id.
    assert as_user(database_engine, "owner_pg", "select id from mail_accounts", tenant=TENANT_B, workspace=WORKSPACE_B) == []
    # Contacts are the owner's alone.
    client.post(f"{MAIL}/contacts", headers=OWNER, json={"email": "sara@client.example"})
    assert as_user(database_engine, "member_pg", "select id from mail_contacts") == []
    assert len(as_user(database_engine, "owner_pg", "select id from mail_contacts")) == 1
    # Key rotation still finds mailboxes as anum_maintenance (the reading policy looks at shares).
    with database_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("set local role anum_maintenance"))
        assert connection.execute(text("select id from mail_accounts")).scalars().all() == [aid]
        transaction.rollback()
