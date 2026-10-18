"""Email Center phase 3 part A (docs/email.md#email-center): shared mailboxes, sender
identities and contacts, against the fake mail server of ``test_mail_center``.

Every address and password here is a plainly fake fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from test_mail_center import (  # noqa: F401
    MEMBER,
    OWNER,
    TENANT,
    VIEWER,
    WORKSPACE,
    FakeServer,
    client,
    connect,
    fake,
)

from anum_api.mailbox.store import mail_backend
from anum_api.schemas import WorkspaceMembership, utc_now
from anum_api.store import store

OTHER = {**OWNER, "x-user-id": "other_mc", "x-user-roles": "member"}
MAIL = "/api/v1/mail"


@pytest.fixture
def members() -> Iterator[None]:
    now = utc_now()
    for user, role in (("owner_mc", "owner"), ("member_mc", "member"), ("other_mc", "member"), ("viewer_mc", "viewer")):
        store.memberships[(TENANT, WORKSPACE, user)] = WorkspaceMembership(
            tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, created_at=now, updated_at=now
        )
    yield
    for user in ("owner_mc", "member_mc", "other_mc", "viewer_mc"):
        store.memberships.pop((TENANT, WORKSPACE, user), None)


def share(account_id: str, access: str = "read", grantee: str = "member_mc") -> dict:
    response = client.post(f"{MAIL}/accounts/{account_id}/grants", headers=OWNER, json={"grantee_user_id": grantee, "access": access})
    assert response.status_code == 201, response.text
    return response.json()


def approve(approval: dict, headers: dict[str, str] = OWNER) -> int:
    return client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=headers, json={"payload_hash": approval["payload_hash"]}).status_code


# -- shares -----------------------------------------------------------------------------------


def test_only_active_owners_and_members_can_be_given_a_share(fake: FakeServer, members: None) -> None:
    account = connect()
    base = f"{MAIL}/accounts/{account['id']}"
    candidates = client.get(f"{base}/share-candidates", headers=OWNER).json()
    assert {c["user_id"] for c in candidates} == {"member_mc", "other_mc"}  # never the caller or a viewer
    assert client.post(f"{base}/grants", headers=OWNER, json={"grantee_user_id": "viewer_mc"}).status_code == 422
    assert client.post(f"{base}/grants", headers=OWNER, json={"grantee_user_id": "stranger_mc"}).status_code == 422
    assert client.post(f"{base}/grants", headers=OWNER, json={"grantee_user_id": "owner_mc"}).status_code == 422
    assert client.post(f"{base}/grants", headers=OWNER, json={"grantee_user_id": "member_mc", "access": "admin"}).status_code == 422
    # Someone else's mailbox cannot be shared, not even by a workspace owner.
    assert client.post(f"{base}/grants", headers=OTHER, json={"grantee_user_id": "other_mc"}).status_code == 404
    first = share(account["id"])
    again = share(account["id"], "send")  # sharing again changes the access, never duplicates
    assert again["id"] == first["id"] and again["access"] == "send"
    assert [g["grantee_user_id"] for g in client.get(f"{base}/grants", headers=OWNER).json()] == ["member_mc"]
    setup = client.get(f"{MAIL}/setup", headers=OWNER).json()
    assert setup["accounts"][0]["access"] == "owner" and setup["accounts"][0]["shared_with"] == 1


def test_a_read_share_reads_but_changes_nothing(fake: FakeServer, members: None) -> None:
    account = connect()
    grant = share(account["id"], "read")
    base = f"{MAIL}/accounts/{account['id']}"
    mine = client.get(f"{MAIL}/setup", headers=MEMBER).json()["accounts"]
    assert [(a["id"], a["access"], a["owner_user_id"], a["shared_with"], a["grant_id"]) for a in mine] == [(account["id"], "read", "owner_mc", 0, grant["id"])]
    assert client.get(f"{base}/folders", headers=MEMBER).status_code == 200
    assert client.get(f"{base}/messages", headers=MEMBER, params={"folder": "INBOX"}).json()["total"] == 30
    opened = client.get(f"{base}/message", headers=MEMBER, params={"folder": "INBOX", "uid": 5})
    assert opened.status_code == 200
    assert "\\Seen" not in next(i for i in fake.folders_["INBOX"][1] if i.uid == 5).flags  # reading did not mark it read
    denied = client.post(f"{base}/flags", headers=MEMBER, json={"folder": "INBOX", "uids": [5], "flag": "seen", "value": True})
    assert denied.status_code == 403 and "read only" in denied.json()["error"]["message"]
    assert client.post(f"{base}/move", headers=MEMBER, json={"folder": "INBOX", "uids": [5], "to": "archive"}).status_code == 403
    assert client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).status_code == 403
    assert client.get(f"{base}/identities", headers=MEMBER).json() == []
    # The mailbox's settings stay with its owner.
    assert client.put(f"{base}/password", headers=MEMBER, json={"password": "example-pass"}).status_code == 403
    assert client.delete(base, headers=MEMBER).status_code == 403
    assert client.get(f"{base}/grants", headers=MEMBER).status_code == 403
    assert client.post(f"{base}/delete", headers=MEMBER, json={"folder": "Junk", "uids": [40]}).status_code == 403
    assert client.post(f"{base}/check", headers=MEMBER).json()["access"] == "read"
    # Not shared with this member at all.
    assert client.get(f"{base}/folders", headers=OTHER).status_code == 404
    assert client.get(f"{MAIL}/setup", headers=OTHER).json()["accounts"] == []


def test_a_send_share_writes_its_own_drafts_and_the_owner_approves(fake: FakeServer, members: None) -> None:
    account = connect()
    grant = share(account["id"], "send")
    reply = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"], "kind": "reply", "folder": "INBOX", "uid": 3})
    assert reply.status_code == 201, reply.text
    draft = reply.json()
    assert draft["from_address"] == "owner@example.test" and draft["to"] == ["sara@client.example"]
    # The writer's drafts are theirs: the mailbox owner does not see them in their list.
    assert client.get(f"{MAIL}/drafts", headers=OWNER).json() == []
    assert client.get(f"{MAIL}/drafts/{draft['id']}", headers=OWNER).status_code == 404
    asked = client.post(f"{MAIL}/drafts/{draft['id']}/send", headers=MEMBER, json={"idempotency_key": "send-key-share-01"}).json()
    approval = asked["approval"]
    assert approval["arguments"]["owner_user_id"] == "member_mc" and approval["arguments"]["mailbox"] == "owner@example.test"
    assert approve(approval, MEMBER) == 403 and not fake.sent  # members never approve
    assert approve(approval) == 200
    assert len(fake.sent) == 1 and fake.sent[0][0] == "owner@example.test"
    assert client.get(f"{MAIL}/drafts/{draft['id']}", headers=MEMBER).json()["status"] == "sent"

    # Read only again: the member can no longer write or send.
    assert client.patch(f"{MAIL}/grants/{grant['id']}", headers=MEMBER, json={"access": "send"}).status_code == 403
    assert client.patch(f"{MAIL}/grants/{grant['id']}", headers=OWNER, json={"access": "read"}).json()["access"] == "read"
    second = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]})
    assert second.status_code == 403


def test_a_send_approved_after_the_share_is_withdrawn_never_goes_out(fake: FakeServer, members: None) -> None:
    account = connect()
    grant = share(account["id"], "send")
    draft = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).json()
    client.patch(f"{MAIL}/drafts/{draft['id']}", headers=MEMBER, json={"to": ["buyer@client.example"], "subject": "Offer", "body": "Text"})
    approval = client.post(f"{MAIL}/drafts/{draft['id']}/send", headers=MEMBER, json={"idempotency_key": "send-key-share-02"}).json()["approval"]
    assert client.delete(f"{MAIL}/grants/{grant['id']}", headers=OWNER).status_code == 204
    approve(approval)
    assert fake.sent == []
    # The mailbox and the message written in it are out of the member's sight.
    assert client.get(f"{MAIL}/accounts/{account['id']}/folders", headers=MEMBER).status_code == 404
    assert client.get(f"{MAIL}/drafts", headers=MEMBER).json() == []
    assert client.get(f"{MAIL}/drafts/{draft['id']}", headers=MEMBER).status_code == 404


def test_the_person_a_mailbox_is_shared_with_can_leave_it(fake: FakeServer, members: None) -> None:
    account = connect()
    grant = share(account["id"])
    assert client.delete(f"{MAIL}/grants/{grant['id']}", headers=OTHER).status_code == 404
    assert client.delete(f"{MAIL}/grants/{grant['id']}", headers=MEMBER).status_code == 204
    assert client.get(f"{MAIL}/setup", headers=MEMBER).json()["accounts"] == []
    # Disconnecting removes every share of the mailbox.
    share(account["id"])
    assert client.delete(f"{MAIL}/accounts/{account['id']}", headers=OWNER).status_code == 204
    assert mail_backend.grants == {}


# -- identities -------------------------------------------------------------------------------


def test_sender_identities_choose_the_from_address_and_signature(fake: FakeServer, members: None) -> None:
    account = connect()
    base = f"{MAIL}/accounts/{account['id']}/identities"
    assert client.post(base, headers=OWNER, json={"address": "sales@elsewhere.example"}).status_code == 422
    sales = client.post(base, headers=OWNER, json={"address": "Sales@example.test", "display_name": "Sales team", "signature": "Sales team\nExample Ltd", "is_default": True})
    assert sales.status_code == 201, sales.text
    sales = sales.json()
    assert sales["address"] == "sales@example.test" and sales["is_default"] is True
    assert client.post(base, headers=OWNER, json={"address": "sales@example.test"}).status_code == 409
    support = client.post(base, headers=OWNER, json={"address": "support@example.test", "signature": "Support desk", "is_default": True}).json()
    listed = client.get(base, headers=OWNER).json()
    assert [(i["address"], i["is_default"]) for i in listed] == [("support@example.test", True), ("sales@example.test", False)]
    client.patch(f"{MAIL}/identities/{sales['id']}", headers=OWNER, json={"is_default": True})

    draft = client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    assert draft["identity_id"] == sales["id"] and draft["from_address"] == "sales@example.test"
    assert draft["body"].endswith("\n\n-- \nSales team\nExample Ltd")
    switched = client.patch(f"{MAIL}/drafts/{draft['id']}", headers=OWNER, json={"identity_id": support["id"], "to": ["buyer@client.example"], "subject": "Hello"}).json()
    assert switched["from_address"] == "support@example.test" and "Support desk" in switched["body"] and "Example Ltd" not in switched["body"]
    reply = client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": account["id"], "kind": "reply", "folder": "INBOX", "uid": 4}).json()
    assert reply["body"].startswith("\n\n-- \nSales team") and "> Hello, this is Quote request 4." in reply["body"]

    approval = client.post(f"{MAIL}/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-ident-01"}).json()["approval"]
    assert approval["arguments"]["from"] == "support@example.test"
    assert approve(approval) == 200
    sender, _, raw = fake.sent[0]
    assert sender == "support@example.test" and b"From: Owner <support@example.test>" in raw

    # A send share sees and uses the identities; it cannot change them.
    share(account["id"], "send")
    assert len(client.get(base, headers=MEMBER).json()) == 2
    assert client.post(base, headers=MEMBER, json={"address": "member@example.test"}).status_code == 403
    assert client.patch(f"{MAIL}/identities/{sales['id']}", headers=MEMBER, json={"signature": "x"}).status_code == 403
    assert client.delete(f"{MAIL}/identities/{sales['id']}", headers=MEMBER).status_code == 403
    # Removing an identity sends its drafts from the mailbox address again.
    assert client.delete(f"{MAIL}/identities/{sales['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{MAIL}/drafts/{reply['id']}", headers=OWNER).json()["from_address"] == "owner@example.test"


def test_an_identity_of_another_mailbox_is_refused(fake: FakeServer, members: None) -> None:
    account = connect()
    other = client.post(f"{MAIL}/accounts", headers=OTHER, json={"address": "other@example.test", "password": fake.password}).json()
    theirs = client.post(f"{MAIL}/accounts/{other['id']}/identities", headers=OTHER, json={"address": "theirs@example.test"}).json()
    assert client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": account["id"], "identity_id": theirs["id"]}).status_code == 422
    draft = client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    assert client.patch(f"{MAIL}/drafts/{draft['id']}", headers=OWNER, json={"identity_id": theirs["id"]}).status_code == 422
    # Several mailboxes per person: both listed, own first.
    second = client.post(f"{MAIL}/accounts", headers=OWNER, json={"address": "second@example.test", "password": fake.password})
    assert second.status_code == 201
    assert [a["address"] for a in client.get(f"{MAIL}/setup", headers=OWNER).json()["accounts"]] == ["owner@example.test", "second@example.test"]


# -- contacts ---------------------------------------------------------------------------------


def test_contacts_create_search_page_update_and_delete(fake: FakeServer) -> None:
    created = client.post(f"{MAIL}/contacts", headers=OWNER, json={"name": "Sara Example", "email": "Sara@Client.example", "company": "Client Co", "phone": "+971 50 000 0000"})
    assert created.status_code == 201, created.text
    sara = created.json()
    assert sara["email"] == "sara@client.example"
    assert client.post(f"{MAIL}/contacts", headers=OWNER, json={"email": "sara@client.example"}).status_code == 409
    assert client.post(f"{MAIL}/contacts", headers=OWNER, json={"email": "not an address"}).status_code == 422
    assert client.post(f"{MAIL}/contacts", headers=OWNER, json={"email": "x@client.example", "phone": "call me"}).status_code == 422
    for index in range(30):
        assert client.post(f"{MAIL}/contacts", headers=OWNER, json={"name": f"Buyer {index:02d}", "email": f"buyer{index:02d}@shop.example"}).status_code == 201
    first = client.get(f"{MAIL}/contacts", headers=OWNER, params={"limit": 20}).json()
    assert first["total"] == 31 and len(first["items"]) == 20 and first["next_cursor"]
    rest = client.get(f"{MAIL}/contacts", headers=OWNER, params={"limit": 20, "cursor": first["next_cursor"]}).json()
    names = [c["name"] for c in first["items"] + rest["items"]]
    assert len(names) == 31 and len(set(names)) == 31 and names == sorted(names, key=str.lower) and rest["next_cursor"] is None
    found = client.get(f"{MAIL}/contacts", headers=OWNER, params={"q": "client co"}).json()
    assert [c["id"] for c in found["items"]] == [sara["id"]] and found["total"] == 1
    assert client.get(f"{MAIL}/contacts", headers=OWNER, params={"q": "100%_"}).json()["total"] == 0
    assert client.get(f"{MAIL}/contacts", headers=OWNER, params={"cursor": "not-a-cursor"}).status_code == 422
    updated = client.patch(f"{MAIL}/contacts/{sara['id']}", headers=OWNER, json={"notes": "Prefers mornings", "email": "sara@client.example"}).json()
    assert updated["notes"] == "Prefers mornings"
    assert client.patch(f"{MAIL}/contacts/{sara['id']}", headers=OWNER, json={"email": "buyer01@shop.example"}).status_code == 409
    # Private to their owner.
    assert client.get(f"{MAIL}/contacts", headers=MEMBER).json()["total"] == 0
    assert client.get(f"{MAIL}/contacts/{sara['id']}", headers=MEMBER).status_code == 404
    assert client.delete(f"{MAIL}/contacts/{sara['id']}", headers=MEMBER).status_code == 404
    assert client.get(f"{MAIL}/contacts", headers=VIEWER).status_code == 403
    assert client.delete(f"{MAIL}/contacts/{sara['id']}", headers=OWNER).status_code == 204
    assert client.get(f"{MAIL}/contacts/{sara['id']}", headers=OWNER).status_code == 404


def test_privacy_covers_contacts_identities_and_shares(fake: FakeServer, members: None) -> None:
    account = connect()
    client.post(f"{MAIL}/contacts", headers=OWNER, json={"name": "Sara", "email": "sara@client.example"})
    client.post(f"{MAIL}/accounts/{account['id']}/identities", headers=OWNER, json={"address": "sales@example.test"})
    share(account["id"], "send")
    client.post(f"{MAIL}/contacts", headers=MEMBER, json={"email": "kept@client.example"})
    assert client.get("/api/v1/privacy", headers=OWNER).json()["private"]["mail_contacts"] == 1
    exported = client.get("/api/v1/privacy/export", headers=OWNER)
    assert exported.status_code == 200 and "sara@client.example" in exported.text and "sales@example.test" in exported.text
    assert "member_mc" in exported.text and "password_enc" not in exported.text
    # The member's own data: their contact and the share they received.
    client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]})
    deleted = client.post("/api/v1/privacy/delete", headers=MEMBER, json={"confirm": "DELETE MY DATA"})
    assert deleted.status_code == 200, deleted.text
    assert [c.email for c in mail_backend.contacts.values()] == ["sara@client.example"]
    assert mail_backend.grants == {} and all(d.owner_user_id != "member_mc" for d in mail_backend.drafts.values())
    assert len(mail_backend.identities) == 1 and account["id"] in mail_backend.accounts


def test_a_scheduled_send_from_a_shared_mailbox_needs_the_share_when_it_goes(fake: FakeServer, members: None) -> None:
    import asyncio
    from datetime import datetime, timedelta, timezone

    from anum_api.mailbox import outgoing

    account = connect()
    grant = share(account["id"], "send")
    sales = client.post(f"{MAIL}/accounts/{account['id']}/identities", headers=OWNER, json={"address": "sales@example.test", "is_default": True}).json()
    when = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0)
    drafts = []
    for index in range(2):
        draft = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).json()
        client.patch(f"{MAIL}/drafts/{draft['id']}", headers=MEMBER, json={"to": ["buyer@client.example"], "subject": f"Later {index}", "body": "Text"})
        asked = client.post(f"{MAIL}/drafts/{draft['id']}/send", headers=MEMBER, json={"idempotency_key": f"send-key-later-{index}", "send_at": when.isoformat()})
        assert asked.status_code == 200, asked.text
        assert asked.json()["approval"]["arguments"]["from"] == "sales@example.test"
        assert approve(asked.json()["approval"]) == 200
        drafts.append(draft["id"])
    assert all(client.get(f"{MAIL}/drafts/{d}", headers=MEMBER).json()["status"] == "scheduled" for d in drafts)
    # The first goes from the identity while the share holds.
    assert asyncio.run(outgoing.handle_due(TENANT, WORKSPACE, "member_mc", drafts[0], when + timedelta(seconds=1))) == "sent"
    assert fake.sent[0][0] == sales["address"]
    # Read only before the second's time: it is not sent.
    client.patch(f"{MAIL}/grants/{grant['id']}", headers=OWNER, json={"access": "read"})
    assert asyncio.run(outgoing.handle_due(TENANT, WORKSPACE, "member_mc", drafts[1], when + timedelta(seconds=1))) == "failed"
    assert len(fake.sent) == 1
    failed = client.get(f"{MAIL}/drafts/{drafts[1]}", headers=MEMBER).json()
    assert failed["status"] == "failed" and "no longer lets the writer send" in failed["error"]
