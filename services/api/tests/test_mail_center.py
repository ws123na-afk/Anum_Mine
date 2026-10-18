"""Email Center (docs/email.md#email-center) against a fake mail server.

The fake implements ``MailServer`` in memory; nothing leaves the process. Every address and
password here is a plainly fake fixture.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from anum_api import main
from anum_api.hardening import insecure_configuration_problems
from anum_api.mailbox import service
from anum_api.mailbox.config import MailServerSettings, mail_settings, mailserver_problems
from anum_api.mailbox.html_safe import sanitize_html
from anum_api.mailbox.mime import Outgoing, build_message, parse_message
from anum_api.mailbox.server import (
    Folder,
    MailServerError,
    Page,
    SendReport,
    SendUncertain,
    decode_mutf7,
    encode_mutf7,
    folder_role,
    parse_fetch,
    parse_list_line,
    quote_mailbox,
)
from anum_api.mailbox.store import mail_backend
from anum_api.mailbox.mime import parse_headers
from anum_api.settings import Settings
from anum_api.store import store

TENANT = "tenant_mailcenter"
WORKSPACE = "workspace_mailcenter"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_mc", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_mc", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_mc", "x-user-roles": "viewer"}
ADDRESS = "owner@example.test"
PASSWORD = "example-app-password"

client = TestClient(main.app)


def raw_message(subject: str, sender: str = "Sara <sara@client.example>", *, html: str | None = None, attach: bytes | None = None, message_id: str | None = None) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ADDRESS
    message["Cc"] = "team@client.example"
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc))
    message["Message-ID"] = message_id or f"<{abs(hash(subject))}@client.example>"
    message.set_content(f"Hello, this is {subject}.")
    if html is not None:
        message.add_alternative(html, subtype="html")
    if attach is not None:
        message.add_attachment(attach, maintype="application", subtype="pdf", filename="quote.pdf")
    return message.as_bytes()


@dataclass
class Stored:
    uid: int
    raw: bytes
    flags: set[str] = field(default_factory=set)


@dataclass
class FakeServer:
    folders_: dict[str, tuple[str | None, list[Stored]]]
    next_uid: int = 100
    sent: list[tuple[str, list[str], bytes]] = field(default_factory=list)
    password: str = PASSWORD
    offline: bool = False
    send_error: Exception | None = None

    def _gate(self, password: str) -> None:
        if self.offline:
            raise MailServerError("offline", "The mail server cannot be reached right now.")
        if password != self.password:
            raise MailServerError("auth", "The mail server refused this address and password.")

    def bind(self, cfg: MailServerSettings, username: str, password: str) -> "Bound":
        return Bound(self, password)


class Bound:
    def __init__(self, fake: FakeServer, password: str) -> None:
        self.fake = fake
        self.password = password

    def _folder(self, name: str) -> list[Stored]:
        self.fake._gate(self.password)
        if name not in self.fake.folders_:
            raise MailServerError("not_found", "That folder does not exist on the mail server.")
        return self.fake.folders_[name][1]

    def check_login(self) -> None:
        self.fake._gate(self.password)

    def folders(self) -> list[Folder]:
        self.fake._gate(self.password)
        return [
            Folder(id=name, name=name, role=role, total=len(items), unread=sum(1 for i in items if "\\Seen" not in i.flags))
            for name, (role, items) in self.fake.folders_.items()
        ]

    def messages(self, folder: str, *, before: int | None, limit: int, query: str | None, unread: bool, flagged: bool) -> Page:
        items = sorted(self._folder(folder), key=lambda i: i.uid, reverse=True)
        if before is not None:
            items = [i for i in items if i.uid < before]
        if unread:
            items = [i for i in items if "\\Seen" not in i.flags]
        if flagged:
            items = [i for i in items if "\\Flagged" in i.flags]
        if query:
            items = [i for i in items if query.lower().encode() in i.raw.lower()]
        page = items[:limit]
        fetch = [
            (f"1 (UID {i.uid} FLAGS ({' '.join(sorted(i.flags))}) RFC822.SIZE {len(i.raw)})".encode(), i.raw.split(b"\n\n", 1)[0] + b"\n\n")
            for i in page
        ]
        return Page(items=parse_fetch(fetch), total=len(items), next_before=page[-1].uid if len(items) > limit else None)

    def fetch(self, folder: str, uid: int, *, mark_seen: bool, max_bytes: int) -> bytes:
        found = next((i for i in self._folder(folder) if i.uid == uid), None)
        if found is None:
            raise MailServerError("not_found", "That message is no longer in this folder.")
        if mark_seen:
            found.flags.add("\\Seen")
        return found.raw

    def set_flag(self, folder: str, uids: list[int], flag: str, value: bool) -> None:
        name = {"seen": "\\Seen", "flagged": "\\Flagged", "answered": "\\Answered"}[flag]
        for item in self._folder(folder):
            if item.uid in uids:
                (item.flags.add if value else item.flags.discard)(name)

    def move(self, folder: str, uids: list[int], target: str) -> None:
        source, destination = self._folder(folder), self._folder(target)
        moving = [i for i in source if i.uid in uids]
        for item in moving:
            source.remove(item)
            self.fake.next_uid += 1
            destination.append(Stored(self.fake.next_uid, item.raw, item.flags))

    def delete(self, folder: str, uids: list[int]) -> None:
        items = self._folder(folder)
        items[:] = [i for i in items if i.uid not in uids]

    def append(self, folder: str, raw: bytes, *, seen: bool) -> None:
        self.fake.next_uid += 1
        self._folder(folder).append(Stored(self.fake.next_uid, raw, {"\\Seen"} if seen else set()))

    def find_message_id(self, folder: str, message_id: str) -> int | None:
        return next((i.uid for i in self._folder(folder) if message_id.encode() in i.raw), None)

    def send(self, sender: str, recipients: list[str], raw: bytes) -> SendReport:
        self.fake._gate(self.password)
        if self.fake.send_error is not None:
            raise self.fake.send_error
        self.fake.sent.append((sender, recipients, raw))
        return SendReport()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeServer]:
    server = FakeServer(
        folders_={
            "INBOX": ("inbox", [Stored(i, raw_message(f"Quote request {i}")) for i in range(1, 31)]),
            "Sent": ("sent", []),
            "Drafts": ("drafts", []),
            "Archive": ("archive", []),
            "Junk": ("junk", [Stored(40, raw_message("Win a prize"))]),
            "Trash": ("trash", []),
        }
    )
    monkeypatch.setattr(mail_settings, "enabled", True)
    monkeypatch.setattr(mail_settings, "imap_host", "mail.example.test")
    monkeypatch.setattr(mail_settings, "smtp_host", "mail.example.test")
    monkeypatch.setattr(mail_settings, "allowed_domains", "example.test")
    monkeypatch.setattr(service, "server_factory", server.bind)
    mail_backend.clear()
    service.reset_connect_attempts()
    yield server
    mail_backend.clear()
    service.reset_connect_attempts()


def connect(headers: dict[str, str] = OWNER) -> dict:
    response = client.post("/api/v1/mail/accounts", headers=headers, json={"address": ADDRESS, "password": PASSWORD, "display_name": "Owner"})
    assert response.status_code == 201, response.text
    return response.json()


# -- pure helpers ---------------------------------------------------------------------------


def test_sanitizer_removes_script_handlers_and_remote_images() -> None:
    safe = sanitize_html(
        '<p onclick="steal()">Hi <b>there</b></p><script>alert(1)</script><style>p{}</style>'
        '<a href="javascript:alert(1)">bad</a><a href="https://shop.example/x">good</a>'
        '<img src="https://tracker.example/pixel.gif" alt="logo"><img src="cid:part1">'
        '<div style="color: red; background: url(https://x.example/a.png)">t</div><iframe src="https://x"></iframe><form><input></form>'
    )
    assert "<script" not in safe.html and "alert(1)" not in safe.html and "onclick" not in safe.html
    assert "<style" not in safe.html and "<iframe" not in safe.html and "javascript:" not in safe.html
    assert 'href="https://shop.example/x"' in safe.html and 'rel="noopener noreferrer nofollow"' in safe.html
    assert "tracker.example" not in safe.html and "[logo]" in safe.html
    assert safe.remote_images_blocked == 1 and safe.inline_images_hidden == 1
    assert 'style="color: red"' in safe.html and "url(" not in safe.html


def test_mailbox_names_and_list_lines() -> None:
    assert encode_mutf7("Entwürfe") == "Entw&APw-rfe" and decode_mutf7("Entw&APw-rfe") == "Entwürfe"
    assert decode_mutf7(encode_mutf7("A & B")) == "A & B"
    assert quote_mailbox('Say "hi"') == '"Say \\"hi\\""'
    assert parse_list_line(b'(\\HasNoChildren \\Sent) "/" "Sent Items"') == (["\\hasnochildren", "\\sent"], "Sent Items")
    assert parse_list_line(b'(\\HasNoChildren) "/" INBOX') == (["\\hasnochildren"], "INBOX")
    assert parse_list_line((b'(\\HasNoChildren) "/" {5}', b"Notes")) == (["\\hasnochildren"], "Notes")
    assert folder_role("INBOX", []) == "inbox" and folder_role("Spam", []) == "junk" and folder_role("X", ["\\trash"]) == "trash"


def test_fetch_parsing_and_header_parsing() -> None:
    headers = b"From: Sara <sara@client.example>\r\nSubject: =?utf-8?q?Caf=C3=A9?=\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n"
    [item] = parse_fetch([(b"1 (UID 7 FLAGS (\\Seen \\Flagged) RFC822.SIZE 900 BODY[HEADER.FIELDS (FROM)] {10}", headers), b")"])
    assert (item.uid, item.seen, item.flagged, item.size, item.subject) == (7, True, True, 900, "Café")
    assert item.sender is not None and item.sender.address == "sara@client.example" and item.maybe_attachments
    assert parse_headers(b"Subject: x\r\nMessage-ID: not-an-id\r\n\r\n")["message_id"] is None


def test_outgoing_messages_refuse_header_injection() -> None:
    raw = build_message(Outgoing("Owner", ADDRESS, ["a@client.example"], [], ["hidden@client.example"], "Hello", "Body", "<x1@example.test>"))
    assert b"hidden@client.example" not in raw and b"Message-ID: <x1@example.test>" in raw
    with pytest.raises(ValueError):
        build_message(Outgoing("Owner", ADDRESS, ["a@client.example"], [], [], "Hi\r\nBcc: evil@x.example", "Body", "<x2@example.test>"))
    parsed = parse_message(raw_message("With file", html="<p>Hi <img src='https://t.example/p.png'></p>", attach=b"%PDF-1.4 fake"))
    assert parsed.html is not None and parsed.html.remote_images_blocked == 1
    assert [a.filename for a in parsed.attachments] == ["quote.pdf"]


def test_config_refusals_mirror_the_helm_chart() -> None:
    assert mailserver_problems(MailServerSettings(enabled=False)) == []
    good = MailServerSettings(enabled=True, imap_host="mail.example.test", smtp_host="mail.example.test", allowed_tenants="tenant_x")
    assert mailserver_problems(good) == []
    problems = " ".join(mailserver_problems(MailServerSettings(enabled=True, imap_host="10.0.0.5", smtp_host="smtp-relay.brevo.com", smtp_security="none", allowed_domains="", allowed_tenants="")))
    for expected in ("ANUM_MAILSERVER_IMAP_HOST must be", "never a relay service", "SMTP_SECURITY must be", "ALLOWED_DOMAINS", "ALLOWED_TENANTS"):
        assert expected in problems
    assert "IMAP_HOST is required" in " ".join(mailserver_problems(MailServerSettings(enabled=True, smtp_host="mail.example.test", allowed_tenants="t")))


def test_startup_policy_refuses_an_unsafe_mail_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    config = Settings(environment="staging", secrets_key=Fernet.generate_key().decode(), cors_origins=["https://app.example.test"])
    monkeypatch.setattr(mail_settings, "enabled", True)
    monkeypatch.setattr(mail_settings, "imap_host", "mail.example.test")
    monkeypatch.setattr(mail_settings, "smtp_host", "smtp-relay.brevo.com")
    monkeypatch.setattr(mail_settings, "allowed_tenants", "tenant_x")
    assert any("never a relay service" in p for p in insecure_configuration_problems(config))


# -- routes ---------------------------------------------------------------------------------


def test_off_by_default_and_viewers_have_no_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_settings, "enabled", False)
    setup = client.get("/api/v1/mail/setup", headers=OWNER).json()
    assert setup["enabled"] is False and setup["accounts"] == []
    assert client.post("/api/v1/mail/accounts", headers=OWNER, json={"address": ADDRESS, "password": PASSWORD}).status_code == 409
    assert client.get("/api/v1/mail/setup", headers=VIEWER).status_code == 403


def test_connect_checks_the_password_and_keeps_it_encrypted(fake: FakeServer) -> None:
    bad = client.post("/api/v1/mail/accounts", headers=OWNER, json={"address": ADDRESS, "password": "example-wrong"})
    assert bad.status_code == 422 and "app password" in bad.json()["error"]["message"]
    other = client.post("/api/v1/mail/accounts", headers=OWNER, json={"address": "someone@elsewhere.example", "password": PASSWORD})
    assert other.status_code == 422
    account = connect()
    assert account["status"] == "connected" and "password" not in str(account)
    stored = mail_backend.accounts[account["id"]]
    assert stored.password_enc != PASSWORD and PASSWORD not in stored.password_enc
    assert client.post("/api/v1/mail/accounts", headers=OWNER, json={"address": ADDRESS, "password": PASSWORD}).status_code == 409
    # Private to the person who connected it.
    assert client.get("/api/v1/mail/setup", headers=MEMBER).json()["accounts"] == []
    assert client.get(f"/api/v1/mail/accounts/{account['id']}/folders", headers=MEMBER).status_code == 404


def test_connection_attempts_are_limited(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_settings, "connect_attempts_per_hour", 2)
    for _ in range(2):
        assert client.post("/api/v1/mail/accounts", headers=OWNER, json={"address": ADDRESS, "password": "example-wrong"}).status_code == 422
    assert client.post("/api/v1/mail/accounts", headers=OWNER, json={"address": ADDRESS, "password": PASSWORD}).status_code == 429


def test_folders_paging_search_and_reading(fake: FakeServer) -> None:
    account = connect()
    base = f"/api/v1/mail/accounts/{account['id']}"
    folders = client.get(f"{base}/folders", headers=OWNER).json()
    assert [f["role"] for f in folders][:3] == ["inbox", "drafts", "sent"] and folders[0]["unread"] == 30
    first = client.get(f"{base}/messages", headers=OWNER, params={"folder": "INBOX", "limit": 25}).json()
    assert len(first["items"]) == 25 and first["total"] == 30 and first["items"][0]["uid"] == 30
    rest = client.get(f"{base}/messages", headers=OWNER, params={"folder": "INBOX", "limit": 25, "before": first["next_before"]}).json()
    assert [m["uid"] for m in rest["items"]] == [5, 4, 3, 2, 1] and rest["next_before"] is None
    found = client.get(f"{base}/messages", headers=OWNER, params={"folder": "INBOX", "q": "Quote request 7"}).json()
    assert [m["uid"] for m in found["items"]] == [7]
    fake.folders_["INBOX"][1].append(Stored(50, raw_message("Rich", html="<p>Hi<script>x()</script><img src='https://t.example/p.gif'></p>", attach=b"%PDF fake")))
    detail = client.get(f"{base}/message", headers=OWNER, params={"folder": "INBOX", "uid": 50}).json()
    assert "<script" not in detail["html"] and detail["remote_images_blocked"] == 1
    assert detail["attachments"][0]["filename"] == "quote.pdf" and detail["text"].startswith("Hello")
    assert "\\Seen" in fake.folders_["INBOX"][1][-1].flags
    download = client.get(f"{base}/attachment", headers=OWNER, params={"folder": "INBOX", "uid": 50, "index": 0})
    assert download.status_code == 200 and download.headers["content-disposition"].startswith("attachment")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"{base}/message", headers=OWNER, params={"folder": "INBOX", "uid": 999}).status_code == 404


def test_offline_server_and_wrong_saved_password_are_reported(fake: FakeServer) -> None:
    account = connect()
    base = f"/api/v1/mail/accounts/{account['id']}"
    fake.offline = True
    response = client.get(f"{base}/folders", headers=OWNER)
    assert response.status_code == 503 and "cannot be reached" in response.json()["error"]["message"]
    assert client.get("/api/v1/mail/setup", headers=OWNER).json()["accounts"][0]["status"] == "offline"
    fake.offline = False
    fake.password = "example-changed"
    assert client.get(f"{base}/folders", headers=OWNER).status_code == 409
    assert client.get("/api/v1/mail/setup", headers=OWNER).json()["accounts"][0]["status"] == "auth_failed"
    fixed = client.put(f"{base}/password", headers=OWNER, json={"password": "example-changed"})
    assert fixed.status_code == 200 and fixed.json()["status"] == "connected"


def test_reply_send_needs_approval_and_saves_to_sent_once(fake: FakeServer) -> None:
    account = connect()
    reply = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"], "kind": "reply_all", "folder": "INBOX", "uid": 3}).json()
    assert reply["subject"] == "Re: Quote request 3" and reply["to"] == ["sara@client.example"] and reply["cc"] == ["team@client.example"]
    assert reply["in_reply_to"] and "> Hello, this is Quote request 3." in reply["body"]
    edited = client.patch(f"/api/v1/mail/drafts/{reply['id']}", headers=OWNER, json={"body": "Thanks Sara, the quote is attached.\n" + reply["body"]}).json()
    asked = client.post(f"/api/v1/mail/drafts/{reply['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0001"})
    assert asked.status_code == 200, asked.text
    approval = asked.json()["approval"]
    assert asked.json()["draft"]["status"] == "pending_approval" and approval["action"] == "send_mail"
    assert approval["arguments"]["body"] == edited["body"] and not fake.sent
    # The same key answers with the same approval.
    again = client.post(f"/api/v1/mail/drafts/{reply['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0001"}).json()
    assert again["approval"]["id"] == approval["id"]
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=MEMBER, json={"payload_hash": approval["payload_hash"]}).status_code == 403
    approved = client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    assert approved.status_code == 200, approved.text
    assert len(fake.sent) == 1
    sender, recipients, raw = fake.sent[0]
    assert sender == ADDRESS and recipients == ["sara@client.example", "team@client.example"]
    assert f"Message-ID: {reply['message_id']}".encode() in raw and b"In-Reply-To:" in raw
    sent_folder = fake.folders_["Sent"][1]
    assert len(sent_folder) == 1 and reply["message_id"].encode() in sent_folder[0].raw
    assert "\\Answered" in next(i for i in fake.folders_["INBOX"][1] if i.uid == 3).flags
    view = client.get(f"/api/v1/mail/drafts/{reply['id']}", headers=OWNER).json()
    assert view["status"] == "sent" and view["saved_to_sent"] is True
    # Sending again never submits twice.
    assert client.post(f"/api/v1/mail/drafts/{reply['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0002"}).status_code == 409
    assert len(fake.sent) == 1


def test_editing_withdraws_the_approval_and_a_changed_call_is_refused(fake: FakeServer) -> None:
    account = connect()
    draft = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER, json={"to": ["buyer@client.example"], "subject": "Offer", "body": "First text"})
    first = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0101"}).json()["approval"]
    client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER, json={"body": "Second text"})
    stale = client.post(f"/api/v1/approvals/{first['id']}/approve", headers=OWNER, json={"payload_hash": first["payload_hash"]})
    assert stale.status_code >= 400 and not fake.sent
    second = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0102"}).json()["approval"]
    assert client.post(f"/api/v1/approvals/{second['id']}/approve", headers=OWNER, json={"payload_hash": second["payload_hash"]}).status_code == 200
    assert len(fake.sent) == 1 and b"Second text" in fake.sent[0][2]


def test_a_broken_connection_during_send_is_never_retried(fake: FakeServer) -> None:
    account = connect()
    draft = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER, json={"to": ["buyer@client.example"], "subject": "Offer", "body": "Text"})
    approval = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0201"}).json()["approval"]
    fake.send_error = SendUncertain("The connection broke while the message was being sent. Check Sent before trying again.")
    client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    view = client.get(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER).json()
    assert view["status"] == "unknown" and "Check Sent" in view["error"]
    assert client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER, json={"body": "x"}).status_code == 409
    assert client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0202"}).status_code == 409


def test_send_validation_and_rate_limit(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    account = connect()
    draft = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    assert client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0301"}).status_code == 422
    assert client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER, json={"to": ["not an address"]}).status_code == 422
    assert client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "short"}).status_code == 422
    client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER, json={"to": ["buyer@client.example"], "body": "Hi"})
    monkeypatch.setattr(mail_settings, "send_per_hour", 1)
    approval = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0302"}).json()["approval"]
    client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]})
    second = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    client.patch(f"/api/v1/mail/drafts/{second['id']}", headers=OWNER, json={"to": ["buyer@client.example"], "body": "Again"})
    assert client.post(f"/api/v1/mail/drafts/{second['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-0303"}).status_code == 429


def test_forward_flags_moves_and_delete_for_good(fake: FakeServer) -> None:
    account = connect()
    base = f"/api/v1/mail/accounts/{account['id']}"
    forward = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"], "kind": "forward", "folder": "INBOX", "uid": 2}).json()
    assert forward["subject"] == "Fwd: Quote request 2" and forward["to"] == [] and "Forwarded message" in forward["body"]
    assert client.post(f"{base}/flags", headers=OWNER, json={"folder": "INBOX", "uids": [2], "flag": "flagged", "value": True}).status_code == 200
    assert client.get(f"{base}/messages", headers=OWNER, params={"folder": "INBOX", "flagged": True}).json()["total"] == 1
    assert client.post(f"{base}/move", headers=OWNER, json={"folder": "INBOX", "uids": [2], "to": "trash"}).status_code == 200
    assert len(fake.folders_["Trash"][1]) == 1
    assert client.post(f"{base}/delete", headers=OWNER, json={"folder": "INBOX", "uids": [3]}).status_code == 409
    trash_uid = fake.folders_["Trash"][1][0].uid
    asked = client.post(f"{base}/delete", headers=OWNER, json={"folder": "Trash", "uids": [trash_uid]}).json()
    assert asked["status"] == "waiting_approval" and fake.folders_["Trash"][1]
    approval = asked["approval"]
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": approval["payload_hash"]}).status_code == 200
    assert fake.folders_["Trash"][1] == []


def test_every_state_changing_route_is_audited_without_content(fake: FakeServer) -> None:
    from audit_probe import AuditProbe

    probe = AuditProbe(client, "mail", secrets=[PASSWORD])
    account = probe.call("POST", "/api/v1/mail/accounts", OWNER, sent=[PASSWORD], json={"address": ADDRESS, "password": PASSWORD}).json()
    base = f"/api/v1/mail/accounts/{account['id']}"
    probe.call("PUT", f"{base}/password", OWNER, sent=[PASSWORD], json={"password": PASSWORD})
    probe.call("POST", f"{base}/check", OWNER)
    probe.call("POST", f"{base}/flags", OWNER, json={"folder": "INBOX", "uids": [1], "flag": "seen", "value": True})
    probe.call("POST", f"{base}/move", OWNER, json={"folder": "Junk", "uids": [40], "to": "trash"})
    trash_uid = fake.folders_["Trash"][1][0].uid
    probe.call("POST", f"{base}/delete", OWNER, json={"folder": "Trash", "uids": [trash_uid]})
    draft = probe.call("POST", "/api/v1/mail/drafts", OWNER, json={"account_id": account["id"]}).json()
    body = "A private message text"
    probe.call("PATCH", f"/api/v1/mail/drafts/{draft['id']}", OWNER, sent=[body, "buyer@client.example"], json={"to": ["buyer@client.example"], "body": body})
    attached = probe.call(
        "POST", f"/api/v1/mail/drafts/{draft['id']}/attachments", OWNER, params={"name": "notes.txt"}, content=b"plain example text",
    ).json()
    probe.call("DELETE", f"/api/v1/mail/drafts/{draft['id']}/attachments/{attached['id']}", OWNER)
    later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    probe.call("POST", f"/api/v1/mail/drafts/{draft['id']}/send", OWNER, json={"idempotency_key": "send-key-0401", "send_at": later})
    probe.call("POST", f"/api/v1/mail/drafts/{draft['id']}/cancel-schedule", OWNER)
    probe.call("DELETE", f"/api/v1/mail/drafts/{draft['id']}", OWNER)
    # Phase 3 part A: shares, sender identities and contacts.
    from anum_api.schemas import WorkspaceMembership, utc_now

    store.memberships[(TENANT, WORKSPACE, "member_mc")] = WorkspaceMembership(
        tenant_id=TENANT, workspace_id=WORKSPACE, user_id="member_mc", role="member", created_at=utc_now(), updated_at=utc_now()
    )
    try:
        grant = probe.call("POST", f"{base}/grants", OWNER, json={"grantee_user_id": "member_mc", "access": "read"}).json()
        probe.call("PATCH", f"/api/v1/mail/grants/{grant['id']}", OWNER, json={"access": "send"})
        probe.call("DELETE", f"/api/v1/mail/grants/{grant['id']}", OWNER)
    finally:
        store.memberships.pop((TENANT, WORKSPACE, "member_mc"), None)
    signature = "A private signature"
    identity = probe.call("POST", f"{base}/identities", OWNER, sent=[signature], json={"address": "sales@example.test", "signature": signature}).json()
    probe.call("PATCH", f"/api/v1/mail/identities/{identity['id']}", OWNER, sent=[signature], json={"signature": signature, "is_default": True})
    probe.call("DELETE", f"/api/v1/mail/identities/{identity['id']}", OWNER)
    contact = probe.call("POST", "/api/v1/mail/contacts", OWNER, sent=["Sara Private", "sara@client.example"], json={"name": "Sara Private", "email": "sara@client.example"}).json()
    probe.call("PATCH", f"/api/v1/mail/contacts/{contact['id']}", OWNER, sent=["Private note"], json={"notes": "Private note"})
    probe.call("DELETE", f"/api/v1/mail/contacts/{contact['id']}", OWNER)
    # Phase 6: settings changes and forwarding rules (addresses never in the audit).
    settings = {"sent_retention_days": 30, "draft_retention_days": None, "label_retention_days": None, "background_sync": True, "base_version": 0}
    probe.call("POST", f"{base}/settings", OWNER, json=settings)
    probe.call("DELETE", f"{base}/settings/pending", OWNER)
    partner = "partner@elsewhere.example"
    rule = probe.call("POST", f"{base}/rules", OWNER, sent=[partner], json={"name": "Orders", "from_contains": "client.example", "forward_to": [partner]}).json()["rule"]
    probe.call("PATCH", f"/api/v1/mail/rules/{rule['id']}", OWNER, sent=[partner], json={"subject_contains": "Order"})
    probe.call("POST", f"/api/v1/mail/rules/{rule['id']}/pause", OWNER)
    probe.call("POST", f"/api/v1/mail/rules/{rule['id']}/resume", OWNER)
    probe.call("DELETE", f"/api/v1/mail/rules/{rule['id']}", OWNER)
    probe.call("DELETE", base, OWNER)
    probe.assert_covered()
    assert mail_backend.accounts == {} and store is not None


def test_privacy_export_never_holds_the_password_and_delete_removes_mailboxes(fake: FakeServer) -> None:
    connect()
    summary = client.get("/api/v1/privacy", headers=OWNER).json()
    assert summary["private"]["mail_mailboxes"] == 1
    exported = client.get("/api/v1/privacy/export", headers=OWNER)
    assert exported.status_code == 200 and "password_enc" not in exported.text and PASSWORD not in exported.text
