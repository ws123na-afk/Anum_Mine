"""One Email module (docs/email.md#one-email-module): the unified read layer over the OAuth provider
inbox and the Email Center, with its permissions, tenant scoping and routing to each back end.

Provider mailboxes are seeded straight into the in-memory email store; own-server mailboxes use
the fake mail server of the All inboxes tests. Every address here is a plainly fake fixture.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from test_mail_all_inboxes import MEMBER, START, connect, servers, share  # noqa: F401  (fixture)
from test_mail_center import OWNER, TENANT, VIEWER, WORKSPACE, FakeServer, client

from anum_api.email_inbox import EmailAccount, EmailMessage, Visibility, email_backend, open_email_store
from anum_api.email_providers import EmailProvider
from anum_api.email_unified import DONE, decode_cursor, encode_cursor
from anum_api.mailbox.config import mail_settings
from anum_api.schemas import TenantContext, utc_now

UNIFIED = "/api/v1/email/unified"
STRANGER = {"x-tenant-id": "tenant_elsewhere", "x-workspace-id": "workspace_elsewhere", "x-user-id": "stranger_mc", "x-user-roles": "owner"}


def provider_mailbox(account_id: str, address: str, *, provider: EmailProvider = EmailProvider.GMAIL, tenant: str = TENANT,
                     workspace: str = WORKSPACE, user: str = "owner_mc", visibility: Visibility = Visibility.WORKSPACE,
                     hours: list[int] | None = None) -> None:
    context = TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=["owner"])
    now = utc_now()
    with open_email_store(context) as store:
        store.add_account(EmailAccount(id=account_id, tenant_id=tenant, workspace_id=workspace, provider=provider, address=address,
                                       connected_by=user, visibility=visibility, created_at=now, updated_at=now))
        store.upsert_messages([
            EmailMessage(id=f"{account_id}_m{n}", tenant_id=tenant, workspace_id=workspace, account_id=account_id, provider_message_id=f"p{n}",
                         thread_id=f"t{n}", subject=f"{address} {n}", from_address="client@client.example", snippet="Hello",
                         received_at=START + timedelta(hours=h), is_read=n == 1, created_at=now, updated_at=now)
            for n, h in enumerate(hours or [], start=1)
        ])


@pytest.fixture(autouse=True)
def clean_provider() -> Iterator[None]:
    email_backend.clear()
    yield
    email_backend.clear()


def get(path: str, headers: dict[str, str] = OWNER, **params: object) -> dict:
    response = client.get(f"{UNIFIED}{path}", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_one_mailbox_list_shows_every_source_with_its_badge(servers: dict[str, FakeServer]) -> None:  # noqa: F811
    provider_mailbox("acct_gmail", "owner@gmail.example", hours=[1, 3])
    provider_mailbox("acct_zoho", "sales@zoho.example", provider=EmailProvider.ZOHO)
    own = connect(OWNER, "owner@example.test")
    member = connect(MEMBER, "member@example.test")
    share(member["id"], MEMBER, "owner_mc", "read")
    found = get("/mailboxes")
    rows = {item["ref"]: item for item in found["items"]}
    assert list(rows) == ["provider:acct_gmail", "provider:acct_zoho", f"server:{own['id']}", f"server:{member['id']}"]
    assert rows["provider:acct_gmail"]["source"] == "gmail" and rows["provider:acct_gmail"]["unread"] == 1
    assert rows["provider:acct_zoho"]["source"] == "zoho" and rows["provider:acct_gmail"]["can_send"] is True
    assert rows[f"server:{own['id']}"]["source"] == "mailserver" and rows[f"server:{own['id']}"]["can_send"] is True
    # Shared to read only: listed, but not a sender.
    assert rows[f"server:{member['id']}"]["access"] == "read" and rows[f"server:{member['id']}"]["can_send"] is False
    assert found["provider"]["state"] == "ok" and found["server"]["state"] == "ok"
    assert found["can_act"] is True and found["can_manage"] is True


def test_a_private_provider_mailbox_and_another_organisation_stay_hidden(servers: dict[str, FakeServer]) -> None:  # noqa: F811
    provider_mailbox("acct_private", "member@gmail.example", user="member_mc", visibility=Visibility.PRIVATE, hours=[1])
    provider_mailbox("acct_far", "far@gmail.example", tenant="tenant_elsewhere", workspace="workspace_elsewhere", user="stranger_mc", hours=[2])
    connect(STRANGER, "stranger@example.test")
    assert get("/mailboxes")["items"] == []
    assert get("/messages")["items"] == []
    assert [item["ref"] for item in get("/mailboxes", MEMBER)["items"]] == ["provider:acct_private"]
    stranger = get("/mailboxes", STRANGER)
    assert [item["address"] for item in stranger["items"]] == ["far@gmail.example", "stranger@example.test"]


def test_viewers_are_refused_on_both_routes() -> None:
    for path in ("/mailboxes", "/messages"):
        response = client.get(f"{UNIFIED}{path}", headers=VIEWER)
        assert response.status_code == 403 and "owner or member" in response.json()["error"]["message"]


def test_all_inboxes_merges_both_back_ends_newest_first_and_routes_each_message(servers: dict[str, FakeServer]) -> None:  # noqa: F811
    provider_mailbox("acct_gmail", "owner@gmail.example", hours=[5, 60])
    own = connect(OWNER, "owner@example.test")  # fake inbox: hours 0, 2, 4, 6, 8, 50
    page = get("/messages", limit=3)
    subjects = [item["subject"] for item in page["items"]]
    assert subjects == ["owner@gmail.example 2", "Owner 6", "Owner 5", "Owner 4", "owner@gmail.example 1"]
    gmail = page["items"][0]
    assert gmail["backend"] == "provider" and gmail["source"] == "gmail" and gmail["message_id"] == "acct_gmail_m2" and gmail["uid"] is None
    server = page["items"][1]
    assert server["backend"] == "server" and server["source"] == "mailserver" and server["mailbox_id"] == own["id"]
    assert server["folder"] == "INBOX" and server["uid"] == 6 and server["ref"] == f"server:{own['id']}:INBOX:6"
    # Paging walks every message of both back ends exactly once.
    seen = subjects[:]
    cursor = page["next_cursor"]
    while cursor:
        page = get("/messages", limit=3, cursor=cursor)
        seen += [item["subject"] for item in page["items"]]
        cursor = page["next_cursor"]
    assert len(seen) == 8 and len(set(seen)) == 8


def test_search_and_unread_reach_both_back_ends(servers: dict[str, FakeServer]) -> None:  # noqa: F811
    provider_mailbox("acct_gmail", "owner@gmail.example", hours=[5, 60])
    connect(OWNER, "owner@example.test")
    unread = get("/messages", unread="true")
    assert "owner@gmail.example 1" not in [item["subject"] for item in unread["items"]]
    found = get("/messages", q="Owner 5")
    assert [item["subject"] for item in found["items"]] == ["Owner 5"]


def test_with_the_email_center_off_the_provider_inbox_still_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_settings, "enabled", False)
    provider_mailbox("acct_gmail", "owner@gmail.example", hours=[5])
    mailboxes = get("/mailboxes")
    assert mailboxes["server"]["state"] == "off" and "ANUM_MAILSERVER_ENABLED" in mailboxes["server"]["detail"]
    page = get("/messages")
    assert [item["backend"] for item in page["items"]] == ["provider"] and page["server"]["state"] == "off" and page["next_cursor"] is None


def test_an_unreadable_server_mailbox_is_reported_and_the_rest_still_lists(servers: dict[str, FakeServer]) -> None:  # noqa: F811
    provider_mailbox("acct_gmail", "owner@gmail.example", hours=[5])
    own = connect(OWNER, "owner@example.test")
    servers["owner@example.test"].offline = True  # type: ignore[attr-defined]
    page = get("/messages")
    assert page["unreadable"] == [{"ref": f"server:{own['id']}", "address": "owner@example.test", "state": "offline",
                                   "reason": "The mail server cannot be reached right now."}]
    assert any(item["backend"] == "provider" for item in page["items"])


def test_cursors_round_trip_and_bad_ones_are_refused() -> None:
    assert decode_cursor(encode_cursor("abc", DONE)) == ("abc", DONE)
    for bad in ("not-base64!", encode_cursor(None, None).replace("e", "x")):
        response = client.get(f"{UNIFIED}/messages", headers=OWNER, params={"cursor": bad})
        assert response.status_code == 422
