"""Email Center scheduled send and outgoing attachments (docs/email.md#scheduled-send,
docs/email.md#attachments) against the fake mail server of ``test_mail_center`` and a fake
malware scanner. Every address, password and file here is a plainly fake fixture.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

import pytest

from anum_api import file_policy
from anum_api.files import file_store
from anum_api.mailbox import outgoing, service
from anum_api.mailbox.config import mail_settings
from anum_api.mailbox.server import SendUncertain
from anum_api.mailbox.store import SEND_LEASE, DraftStatus, mail_backend
from anum_api.store import store
from test_mail_center import MEMBER, OWNER, VIEWER, FakeServer, Stored, client, connect, fake, raw_message  # noqa: F401

PDF = b"%PDF-1.4 example pdf body"
PNG = b"\x89PNG\r\n\x1a\n example image"


class FakeScanner:
    def __init__(self) -> None:
        self.seen: list[str] = []
        self.refuse: set[str] = set()
        self.broken = False

    def scan(self, name: str, content_type: str, content: bytes) -> file_policy.ScanVerdict:
        if self.broken:
            raise RuntimeError("scanner down")
        self.seen.append(name)
        if name in self.refuse:
            return file_policy.ScanVerdict(clean=False, reason="Example.Test.Signature")
        return file_policy.ScanVerdict(clean=True)


@pytest.fixture
def scanner() -> Iterator[FakeScanner]:
    found = FakeScanner()
    file_policy.set_file_scanner(found)
    yield found
    file_policy.set_file_scanner(None)


def new_draft(account: dict, to: str = "buyer@client.example", body: str = "Hello") -> dict:
    draft = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER, json={"to": [to], "subject": "Offer", "body": body})
    return draft


def attach(draft_id: str, name: str, content: bytes, headers: dict[str, str] = OWNER):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/mail/drafts/{draft_id}/attachments", headers=headers, params={"name": name}, content=content)


def approve(approval: dict, headers: dict[str, str] = OWNER):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=headers, json={"payload_hash": approval["payload_hash"]})


def view(draft_id: str) -> dict:
    return client.get(f"/api/v1/mail/drafts/{draft_id}", headers=OWNER).json()


def soon(minutes: int = 30) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0)


def run(now: datetime) -> dict[str, int]:
    return asyncio.run(outgoing.run_once(now))


# -- attachments ----------------------------------------------------------------------------


def test_attachments_pass_the_upload_checks_and_the_scanner(fake: FakeServer, scanner: FakeScanner) -> None:
    draft = new_draft(connect())
    ok = attach(draft["id"], "quote.pdf", PDF)
    assert ok.status_code == 201, ok.text
    assert ok.json()["scanned"] is True and ok.json()["sha256"] == hashlib.sha256(PDF).hexdigest() and scanner.seen == ["quote.pdf"]
    assert attach(draft["id"], "tool.exe", b"MZ example").status_code == 415
    assert attach(draft["id"], "fake.pdf", b"<html>not a pdf</html>").status_code == 415
    assert attach(draft["id"], "empty.txt", b"").status_code == 422
    scanner.refuse.add("bad.pdf")
    refused = attach(draft["id"], "bad.pdf", PDF)
    assert refused.status_code == 422 and "Example.Test.Signature" in refused.json()["error"]["message"]
    scanner.broken = True
    assert attach(draft["id"], "later.pdf", PDF).status_code == 503
    files = view(draft["id"])["attachments"]
    assert [f["filename"] for f in files] == ["quote.pdf"]


def test_without_a_scanner_files_are_marked_not_scanned(fake: FakeServer) -> None:
    draft = new_draft(connect())
    assert attach(draft["id"], "notes.txt", b"plain example text").json()["scanned"] is False


def test_size_and_count_caps(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    draft = new_draft(connect())
    monkeypatch.setattr(mail_settings, "outbound_attachment_max_bytes", 2048)
    monkeypatch.setattr(mail_settings, "outbound_max_attachments", 2)
    monkeypatch.setattr(mail_settings, "outbound_total_max_bytes", 3000)
    too_big = attach(draft["id"], "big.txt", b"x" * 3000)
    assert too_big.status_code == 413
    assert attach(draft["id"], "a.txt", b"a" * 1500).status_code == 201
    total = attach(draft["id"], "b.txt", b"b" * 1600)
    assert total.status_code == 413 and "add up to" in total.json()["error"]["message"]
    assert attach(draft["id"], "c.txt", b"c" * 100).status_code == 201
    count = attach(draft["id"], "d.txt", b"d")
    assert count.status_code == 413 and "at most 2 files" in count.json()["error"]["message"]


def test_attachments_are_private_and_viewers_cannot_attach(fake: FakeServer) -> None:
    draft = new_draft(connect())
    file_id = attach(draft["id"], "quote.pdf", PDF).json()["id"]
    assert attach(draft["id"], "x.txt", b"x", headers=VIEWER).status_code == 403
    assert attach(draft["id"], "x.txt", b"x", headers=MEMBER).status_code == 404
    assert client.get(f"/api/v1/mail/drafts/{draft['id']}/attachments/{file_id}", headers=MEMBER).status_code == 404
    own = client.get(f"/api/v1/mail/drafts/{draft['id']}/attachments/{file_id}", headers=OWNER)
    assert own.status_code == 200 and own.content == PDF and own.headers["content-disposition"].startswith("attachment")
    assert own.headers["x-content-type-options"] == "nosniff"


def test_attachments_are_in_the_approval_and_sent_exactly(fake: FakeServer, scanner: FakeScanner) -> None:
    draft = new_draft(connect())
    file_id = attach(draft["id"], "quote.pdf", PDF).json()["id"]
    asked = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-a001"}).json()
    approval = asked["approval"]
    assert approval["arguments"]["attachments"] == [
        {"id": file_id, "filename": "quote.pdf", "content_type": "application/pdf", "size": len(PDF), "sha256": hashlib.sha256(PDF).hexdigest()}
    ]
    assert approve(approval).status_code == 200
    [(_, _, raw)] = fake.sent
    message = BytesParser(policy=policy.default).parsebytes(raw)
    parts = [part for part in message.iter_attachments()]
    assert [(p.get_filename(), p.get_content_type(), p.get_payload(decode=True)) for p in parts] == [("quote.pdf", "application/pdf", PDF)]
    assert view(draft["id"])["status"] == "sent"


def test_adding_or_removing_a_file_withdraws_the_approval(fake: FakeServer) -> None:
    draft = new_draft(connect())
    first = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-a101"}).json()["approval"]
    file_id = attach(draft["id"], "quote.pdf", PDF).json()["id"]
    assert view(draft["id"])["status"] == "draft"
    assert approve(first).status_code >= 400 and not fake.sent
    second = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-a102"}).json()["approval"]
    assert client.delete(f"/api/v1/mail/drafts/{draft['id']}/attachments/{file_id}", headers=OWNER).status_code == 204
    assert approve(second).status_code >= 400 and not fake.sent


def test_a_file_changed_in_storage_is_never_sent(fake: FakeServer) -> None:
    draft = new_draft(connect())
    attach(draft["id"], "quote.pdf", PDF)
    approval = client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-a201"}).json()["approval"]
    [stored] = mail_backend.attachments.values()
    file_store.storage.put(stored.storage_key, b"%PDF-1.4 swapped", "application/pdf")
    approve(approval)
    assert not fake.sent
    after = view(draft["id"])
    assert after["status"] == "failed" and "changed after it was attached" in after["error"]


def test_forward_carries_the_original_files_through_the_checks(fake: FakeServer, scanner: FakeScanner) -> None:
    account = connect()
    fake.folders_["INBOX"][1].append(Stored(60, raw_message("With file", attach=PDF)))
    forward = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"], "kind": "forward", "folder": "INBOX", "uid": 60}).json()
    assert [f["filename"] for f in forward["attachments"]] == ["quote.pdf"] and forward["attachment_note"] is None
    scanner.refuse.add("quote.pdf")
    refused = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"], "kind": "forward", "folder": "INBOX", "uid": 60}).json()
    assert refused["attachments"] == [] and "quote.pdf" in refused["attachment_note"]
    bare = client.post(
        "/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"], "kind": "forward", "folder": "INBOX", "uid": 60, "include_attachments": False}
    ).json()
    assert bare["attachments"] == []
    reply = client.post("/api/v1/mail/drafts", headers=OWNER, json={"account_id": account["id"], "kind": "reply", "folder": "INBOX", "uid": 60}).json()
    assert reply["attachments"] == []
    assert attach(reply["id"], "answer.png", PNG).status_code == 201


def test_discarding_and_disconnecting_delete_the_stored_bytes(fake: FakeServer) -> None:
    account = connect()
    draft = new_draft(account)
    attach(draft["id"], "quote.pdf", PDF)
    [stored] = mail_backend.attachments.values()
    assert file_store.storage.get(stored.storage_key) == PDF
    client.delete(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER)
    with pytest.raises((FileNotFoundError, KeyError)):
        file_store.storage.get(stored.storage_key)
    second = new_draft(account)
    attach(second["id"], "quote.pdf", PDF)
    [kept] = mail_backend.attachments.values()
    client.delete(f"/api/v1/mail/accounts/{account['id']}", headers=OWNER)
    assert mail_backend.attachments == {}
    with pytest.raises((FileNotFoundError, KeyError)):
        file_store.storage.get(kept.storage_key)


# -- scheduled send --------------------------------------------------------------------------


def schedule(draft_id: str, when: datetime, key: str) -> dict:
    response = client.post(f"/api/v1/mail/drafts/{draft_id}/send", headers=OWNER, json={"idempotency_key": key, "send_at": when.isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_scheduled_message_is_approved_first_and_sent_once_at_its_time(fake: FakeServer) -> None:
    draft = new_draft(connect())
    attach(draft["id"], "quote.pdf", PDF)
    when = soon(45)
    asked = schedule(draft["id"], when, "send-key-s001")
    approval = asked["approval"]
    assert approval["arguments"]["send_at"] == when.isoformat() and asked["draft"]["send_at"] == when.isoformat()
    assert schedule(draft["id"], when, "send-key-s001")["approval"]["id"] == approval["id"]
    listed = client.get("/api/v1/mail/scheduled", headers=OWNER).json()
    assert [d["id"] for d in listed] == [draft["id"]] and listed[0]["status"] == "pending_approval"
    assert approve(approval).status_code == 200
    assert not fake.sent and view(draft["id"])["status"] == "scheduled"
    assert run(when - timedelta(seconds=5)) == {}
    assert not fake.sent
    assert run(when + timedelta(seconds=1)) == {"sent": 1}
    assert len(fake.sent) == 1 and b"quote.pdf" in fake.sent[0][2]
    assert run(when + timedelta(minutes=1)) == {} and len(fake.sent) == 1
    after = view(draft["id"])
    assert after["status"] == "sent" and after["saved_to_sent"] is True
    assert client.get("/api/v1/mail/scheduled", headers=OWNER).json() == []
    actions = [r.action for r in store.audit_records if r.target == draft["id"]]
    assert "mail.send.scheduled" in actions


def test_the_send_time_is_checked(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    draft = new_draft(connect())
    url = f"/api/v1/mail/drafts/{draft['id']}/send"
    naive = client.post(url, headers=OWNER, json={"idempotency_key": "send-key-s101", "send_at": "2030-01-01T09:00:00"})
    assert naive.status_code == 422 and "time zone" in naive.json()["error"]["message"]
    past = client.post(url, headers=OWNER, json={"idempotency_key": "send-key-s102", "send_at": soon(0).isoformat()})
    assert past.status_code == 422 and "at least a minute" in past.json()["error"]["message"]
    monkeypatch.setattr(mail_settings, "schedule_max_days", 2)
    far = client.post(url, headers=OWNER, json={"idempotency_key": "send-key-s103", "send_at": soon(60 * 24 * 3).isoformat()})
    assert far.status_code == 422 and "2 days" in far.json()["error"]["message"]


def test_editing_or_cancelling_a_scheduled_message_stops_it(fake: FakeServer) -> None:
    draft = new_draft(connect())
    when = soon(30)
    approve(schedule(draft["id"], when, "send-key-s201")["approval"])
    edited = client.patch(f"/api/v1/mail/drafts/{draft['id']}", headers=OWNER, json={"body": "Changed text"}).json()
    assert edited["status"] == "draft" and edited["send_at"] == when.isoformat()
    assert run(when + timedelta(seconds=1)) == {} and not fake.sent
    later = soon(90)
    approve(schedule(draft["id"], later, "send-key-s202")["approval"])
    cancelled = client.post(f"/api/v1/mail/drafts/{draft['id']}/cancel-schedule", headers=OWNER)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "draft" and cancelled.json()["send_at"] is None
    assert client.post(f"/api/v1/mail/drafts/{draft['id']}/cancel-schedule", headers=OWNER).status_code == 409
    assert run(later + timedelta(seconds=1)) == {} and not fake.sent


def test_rescheduling_waits_for_the_new_approval(fake: FakeServer) -> None:
    draft = new_draft(connect())
    first = soon(30)
    approve(schedule(draft["id"], first, "send-key-s301")["approval"])
    second = soon(120)
    again = schedule(draft["id"], second, "send-key-s302")
    assert again["draft"]["status"] == "pending_approval" and again["approval"]["arguments"]["send_at"] == second.isoformat()
    assert run(first + timedelta(seconds=1)) == {} and not fake.sent
    approve(again["approval"])
    assert run(second + timedelta(seconds=1)) == {"sent": 1} and len(fake.sent) == 1


def test_a_send_not_approved_before_it_is_due_does_not_go(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    draft = new_draft(connect())
    when = soon(10)
    approval = schedule(draft["id"], when, "send-key-s401")["approval"]
    # The scheduler withdraws the approval once the time (plus a short grace) has passed.
    assert run(when + service.DUE_GRACE + timedelta(seconds=1)) == {"not_approved": 1}
    after = view(draft["id"])
    assert after["status"] == "draft" and "not approved before its scheduled time" in after["error"]
    assert approve(approval).status_code >= 400 and not fake.sent
    # An approval that comes late, before the scheduler noticed, does not send either.
    second = new_draft(client.get("/api/v1/mail/setup", headers=OWNER).json()["accounts"][0])
    late = schedule(second["id"], when, "send-key-s402")["approval"]
    monkeypatch.setattr(service, "utc_now", lambda: when + service.DUE_GRACE + timedelta(minutes=1))
    approve(late)
    assert not fake.sent and view(second["id"])["status"] == "draft"


def test_an_interrupted_send_becomes_unknown_and_is_never_retried(fake: FakeServer) -> None:
    draft = new_draft(connect())
    when = soon(20)
    approve(schedule(draft["id"], when, "send-key-s501")["approval"])
    stored = mail_backend.drafts[draft["id"]]
    # A replica claimed it and stopped mid-send: the lease runs out.
    stored.status, stored.send_lease_until = DraftStatus.SENDING, when
    assert run(when + SEND_LEASE) == {"interrupted": 1}
    after = view(draft["id"])
    assert after["status"] == "unknown" and "Check Sent" in after["error"]
    assert run(when + SEND_LEASE * 2) == {} and not fake.sent
    assert client.post(f"/api/v1/mail/drafts/{draft['id']}/send", headers=OWNER, json={"idempotency_key": "send-key-s502"}).status_code == 409


def test_a_broken_connection_at_the_scheduled_time_is_unknown(fake: FakeServer) -> None:
    draft = new_draft(connect())
    when = soon(20)
    approve(schedule(draft["id"], when, "send-key-s601")["approval"])
    fake.send_error = SendUncertain("The connection broke while the message was being sent. Check Sent before trying again.")
    assert run(when + timedelta(seconds=1)) == {"failed": 1}
    assert view(draft["id"])["status"] == "unknown"
    fake.send_error = None
    assert run(when + timedelta(minutes=5)) == {} and not fake.sent


def test_a_scheduled_message_changed_behind_the_api_is_not_sent(fake: FakeServer) -> None:
    draft = new_draft(connect())
    when = soon(20)
    approve(schedule(draft["id"], when, "send-key-s701")["approval"])
    mail_backend.drafts[draft["id"]].body = "Text nobody approved"
    assert run(when + timedelta(seconds=1)) == {"failed": 1} and not fake.sent
    assert "changed after it was approved" in view(draft["id"])["error"]


def test_scheduler_respects_the_hourly_limit_and_the_mail_server_switch(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    account = connect()
    when = soon(20)
    drafts = [new_draft(account, body=f"Message {i}") for i in range(2)]
    for i, draft in enumerate(drafts):
        approve(schedule(draft["id"], when, f"send-key-s80{i}")["approval"])
    monkeypatch.setattr(mail_settings, "send_per_hour", 1)
    assert run(when + timedelta(seconds=1)) == {"sent": 1, "failed": 1} and len(fake.sent) == 1
    third = new_draft(account)
    approve(schedule(third["id"], when, "send-key-s809")["approval"])
    monkeypatch.setattr(mail_settings, "send_per_hour", 30)
    monkeypatch.setattr(mail_settings, "enabled", False)
    assert run(when + timedelta(seconds=2)) == {"failed": 1} and "turned off" in view(third["id"])["error"]


def test_the_runner_is_built_only_when_the_mail_server_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_settings, "enabled", False)
    assert outgoing.build_schedule_runner() is None
    monkeypatch.setattr(mail_settings, "enabled", True)
    monkeypatch.setattr(mail_settings, "schedule_poll_seconds", 0)
    assert outgoing.build_schedule_runner() is None
    monkeypatch.setattr(mail_settings, "schedule_poll_seconds", 30)

    async def cycle() -> None:
        runner = outgoing.build_schedule_runner()
        assert runner is not None
        runner.start()
        await asyncio.sleep(0)
        await runner.stop()

    asyncio.run(cycle())


def test_privacy_export_lists_files_without_storage_keys(fake: FakeServer) -> None:
    draft = new_draft(connect())
    attach(draft["id"], "quote.pdf", PDF)
    exported = client.get("/api/v1/privacy/export", headers=OWNER)
    assert exported.status_code == 200 and "quote.pdf" in exported.text and "storage_key" not in exported.text
