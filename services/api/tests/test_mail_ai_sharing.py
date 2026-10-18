"""AI on shared mailboxes and with sender identities (docs/email.md#ai-on-email).

Read access is enough to summarise and label; drafting and forwarding need send access; bulk
archive and export stay with the mailbox's owner. An AI draft goes out as the person's default
identity (or the one they pick) and carries its signature above the quoted original.
Plainly fake fixtures only.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from test_mail_ai import ScriptedModel
from test_mail_center import MEMBER, OWNER, FakeServer, client, connect, fake  # noqa: F401 (fake is a fixture)
from test_mail_sharing import MAIL, members, share  # noqa: F401 (members is a fixture)

from anum_api import email_inbox
from anum_api.mailbox.labels import label_backend

AI = "/api/v1/mail/ai"
SIGNATURE = "Sales team\nExample Ltd"


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    gateway = ScriptedModel()
    email_inbox.set_model_gateway(gateway)
    label_backend.clear()
    yield gateway
    email_inbox.set_model_gateway(None)
    label_backend.clear()


def ref(account: dict, uid: int = 3) -> dict:
    return {"account_id": account["id"], "folder": "INBOX", "uid": uid}


def identities(account: dict) -> tuple[dict, dict]:
    base = f"{MAIL}/accounts/{account['id']}/identities"
    sales = client.post(base, headers=OWNER, json={"address": "sales@example.test", "display_name": "Sales team", "signature": SIGNATURE, "is_default": True})
    support = client.post(base, headers=OWNER, json={"address": "support@example.test", "signature": "Support desk"})
    assert sales.status_code == 201 and support.status_code == 201, (sales.text, support.text)
    return sales.json(), support.json()


def test_an_ai_draft_uses_the_default_identity_and_its_signature(fake: FakeServer, model: ScriptedModel, members: None) -> None:
    account = connect()
    sales, support = identities(account)
    draft = client.post(f"{AI}/draft-reply", headers=OWNER, json=ref(account)).json()["draft"]
    assert draft["identity_id"] == sales["id"] and draft["from_address"] == "sales@example.test"
    body = draft["body"]
    # The AI's text, then the signature, then the quoted original.
    assert body.index("Hello Sara") < body.index("-- \nSales team\nExample Ltd") < body.index("> Hello, this is Quote request 3.")
    picked = client.post(f"{AI}/draft-reply", headers=OWNER, json={**ref(account), "identity_id": support["id"]}).json()["draft"]
    assert picked["identity_id"] == support["id"] and "Support desk" in picked["body"] and "Example Ltd" not in picked["body"]
    plain = client.post(f"{AI}/draft-reply", headers=OWNER, json={**ref(account), "identity_id": ""}).json()["draft"]
    assert plain["identity_id"] is None and "-- \n" not in plain["body"]
    wrong = client.post(f"{AI}/draft-reply", headers=OWNER, json={**ref(account), "identity_id": "mailidentity_example"})
    assert wrong.status_code == 422 and "identities" in wrong.json()["error"]["message"]
    # Sending binds the identity: the approval shows the From address.
    asked = client.post(f"{MAIL}/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "ai-identity-01"}).json()
    assert asked["approval"]["arguments"]["from"] == "sales@example.test" and fake.sent == []


def test_an_ai_forward_uses_the_identity_too(fake: FakeServer, model: ScriptedModel, members: None) -> None:
    account = connect()
    sales, _ = identities(account)
    result = client.post(f"{AI}/forward", headers=OWNER, json={**ref(account), "to": ["colleague@example.test"], "note": "See below.", "idempotency_key": "fwd-identity-01"}).json()
    assert result["draft"]["identity_id"] == sales["id"] and result["approval"]["arguments"]["from"] == "sales@example.test"
    assert result["draft"]["body"].startswith("See below.\n\n-- \nSales team")


def test_a_read_share_summarises_and_labels_but_cannot_draft_or_forward(fake: FakeServer, model: ScriptedModel, members: None) -> None:
    account = connect()
    share(account["id"], "read")
    assert client.post(f"{AI}/summarize", headers=MEMBER, json=ref(account)).status_code == 200
    assert client.post(f"{AI}/classify", headers=MEMBER, json=ref(account)).status_code == 200
    page = client.post(f"{AI}/classify-page", headers=MEMBER, json={"account_id": account["id"], "folder": "INBOX", "uids": [4]})
    assert page.status_code == 200 and page.json()["labelled"] == 1
    # The member's labels are the member's: the owner sees none of them.
    params = {"account_id": account["id"], "folder": "INBOX", "uids": "3,4"}
    assert len(client.get(f"{AI}/labels", headers=MEMBER, params=params).json()) == 2
    assert client.get(f"{AI}/labels", headers=OWNER, params=params).json() == []
    drafted = client.post(f"{AI}/draft-reply", headers=MEMBER, json=ref(account))
    assert drafted.status_code == 403 and "send access" in drafted.json()["error"]["message"]
    forwarded = client.post(f"{AI}/forward", headers=MEMBER, json={**ref(account), "to": ["colleague@example.test"], "idempotency_key": "fwd-share-0001"})
    assert forwarded.status_code == 403
    for path in ("archive", "export"):
        refused = client.post(f"{AI}/{path}", headers=MEMBER, json={"account_id": account["id"], "folder": "INBOX", "uids": [3]})
        assert refused.status_code == 403 and "owner" in refused.json()["error"]["message"]
    assert fake.sent == []


def test_a_send_share_drafts_with_ai_as_the_shared_identity_and_the_owner_approves(fake: FakeServer, model: ScriptedModel, members: None) -> None:
    account = connect()
    sales, _ = identities(account)
    share(account["id"], "send")
    drafted = client.post(f"{AI}/draft-reply", headers=MEMBER, json=ref(account))
    assert drafted.status_code == 201, drafted.text
    draft = drafted.json()["draft"]
    assert draft["identity_id"] == sales["id"] and "Sales team" in draft["body"]
    # The draft is the member's own: the owner does not see it in their list.
    assert all(d["id"] != draft["id"] for d in client.get(f"{MAIL}/drafts", headers=OWNER).json())
    forward = client.post(f"{AI}/forward", headers=MEMBER, json={**ref(account), "to": ["colleague@example.test"], "idempotency_key": "fwd-share-0002"})
    assert forward.status_code == 200, forward.text
    approval = forward.json()["approval"]
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}).status_code == 200
    assert len(fake.sent) == 1 and fake.sent[0][0] == "sales@example.test"


def test_after_the_share_is_withdrawn_ai_cannot_read_the_mailbox(fake: FakeServer, model: ScriptedModel, members: None) -> None:
    account = connect()
    grant = share(account["id"], "read")
    assert client.post(f"{AI}/summarize", headers=MEMBER, json=ref(account)).status_code == 200
    assert client.delete(f"{MAIL}/grants/{grant['id']}", headers=OWNER).status_code == 204
    assert client.post(f"{AI}/summarize", headers=MEMBER, json=ref(account)).status_code == 404
