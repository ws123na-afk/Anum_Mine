"""All inboxes in PostgreSQL (docs/email.md#all-inboxes): which mailboxes the route lists and reads
is decided by the row policies of ``mail_accounts`` and ``mail_account_grants``, not only by the query.

The API runs as the non-owner ``anum_test_app`` role against the fake mail server of
``test_mail_center``. Every address and password is a plainly fake fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import sessionmaker
from test_mail_center import PASSWORD, FakeServer, fake  # noqa: F401

from anum_api.db import session as db_session
from anum_api.mailbox import all_inboxes as inboxes_module
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
VIEWER = headers("viewer_pg", "viewer")
# The same person id in another organisation: nothing of tenant A may reach them, or the reverse.
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
            (TENANT_A, WORKSPACE_A, "other_pg", "member"), (TENANT_A, WORKSPACE_A, "viewer_pg", "viewer"),
            (TENANT_B, WORKSPACE_B, "owner_pg", "owner"),
        ):
            connection.execute(
                text("insert into workspace_memberships (tenant_id, workspace_id, user_id, role) values (:t, :w, :u, :r)"),
                {"t": tenant, "w": workspace, "u": user, "r": role},
            )
    yield TestClient(app)
    mail_backend.clear()


def connect(client: TestClient, who: dict[str, str], address: str) -> dict:
    response = client.post(f"{MAIL}/accounts", headers=who, json={"address": address, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def listed(client: TestClient, who: dict[str, str], **params: object) -> set[str]:
    response = client.get(f"{MAIL}/all-inboxes", headers=who, params={"limit": 50, **params})
    assert response.status_code == 200, response.text
    return {m["account_address"] for m in response.json()["items"]}


def test_shared_mailboxes_follow_the_row_policies(client: TestClient) -> None:
    own = connect(client, OWNER, "owner@example.test")
    member = connect(client, MEMBER, "member@example.test")
    other = connect(client, OTHER, "other@example.test")
    # Before any share each person sees only their own inbox, also a workspace owner.
    assert listed(client, OWNER) == {"owner@example.test"}
    assert listed(client, MEMBER) == {"member@example.test"}
    shared = client.post(f"{MAIL}/accounts/{member['id']}/grants", headers=MEMBER, json={"grantee_user_id": "owner_pg", "access": "read"})
    assert shared.status_code == 201, shared.text
    page = client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"limit": 50}).json()
    assert {m["account_address"] for m in page["items"]} == {"owner@example.test", "member@example.test"}
    assert {m["address"]: m["access"] for m in page["mailboxes"]} == {"owner@example.test": "owner", "member@example.test": "read"}
    # A mailbox in the workspace that is not shared answers like one that does not exist.
    assert client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"account_id": other["id"]}).status_code == 404
    assert listed(client, OWNER, account_id=member["id"]) == {"member@example.test"}
    # The share is one way: the member still sees only their own.
    assert listed(client, MEMBER) == {"member@example.test"}
    # Leaving the share removes it from the list at once.
    assert client.delete(f"{MAIL}/grants/{shared.json()['id']}", headers=OWNER).status_code == 204
    assert listed(client, OWNER) == {"owner@example.test"}
    assert client.get(f"{MAIL}/all-inboxes", headers=VIEWER).status_code == 403
    assert own["id"]


def test_another_organisation_is_never_listed_or_read(client: TestClient, fake: FakeServer) -> None:  # noqa: F811
    own = connect(client, OWNER, "owner@example.test")
    elsewhere = connect(client, STRANGER, "owner@example.test")  # same address and person id, other tenant
    assert own["id"] != elsewhere["id"]
    page = client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"limit": 50}).json()
    assert {m["account_id"] for m in page["items"]} == {own["id"]}
    assert [m["account_id"] for m in page["mailboxes"]] == [own["id"]]
    # Naming the other organisation's mailbox is refused, by filter and by a forged cursor.
    assert client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"account_id": elsewhere["id"]}).status_code == 404
    forged = inboxes_module.encode_cursor({elsewhere["id"]: 99}, inboxes_module._fingerprint([], None, False, False, None, None))
    again = client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"cursor": forged, "limit": 50}).json()
    assert {m["account_id"] for m in again["items"]} == {own["id"]}
    assert client.get(f"{MAIL}/all-inboxes", headers=STRANGER, params={"account_id": own["id"]}).status_code == 404
    assert {m["account_id"] for m in client.get(f"{MAIL}/all-inboxes", headers=STRANGER, params={"limit": 50}).json()["items"]} == {elsewhere["id"]}


def test_the_policies_alone_hide_unshared_and_foreign_mailboxes(database_engine: Engine, client: TestClient) -> None:
    connect(client, OWNER, "owner@example.test")
    connect(client, OTHER, "other@example.test")
    connect(client, STRANGER, "owner@example.test")

    def visible(user: str, tenant: str, workspace: str) -> set[str]:
        with database_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(f"set local role {APP_ROLE}"))
                for key, value in (("anum.tenant_id", tenant), ("anum.workspace_id", workspace), ("anum.user_id", user)):
                    connection.execute(text("select set_config(:k, :v, true)"), {"k": key, "v": value})
                # No owner or tenant condition here: only the row policies decide.
                return set(connection.execute(text("select address from mail_accounts")).scalars())
            finally:
                transaction.rollback()

    assert visible("owner_pg", TENANT_A, WORKSPACE_A) == {"owner@example.test"}
    assert visible("other_pg", TENANT_A, WORKSPACE_A) == {"other@example.test"}
    assert visible("owner_pg", TENANT_B, WORKSPACE_B) == {"owner@example.test"}
    with database_engine.connect() as connection:
        tenants = dict(connection.execute(text("select address, count(distinct tenant_id) from mail_accounts group by address")).all())
    assert tenants["owner@example.test"] == 2  # the same address in two tenants, each seeing one
