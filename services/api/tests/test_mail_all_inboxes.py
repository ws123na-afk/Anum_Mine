"""All inboxes (docs/email.md#all-inboxes) against one fake mail server per mailbox.

Every address and password here is a plainly fake fixture.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

import pytest
from fastapi import HTTPException
from test_mail_center import OWNER, PASSWORD, TENANT, VIEWER, WORKSPACE, Bound, FakeServer, Stored, client

from anum_api.mailbox import all_inboxes as inboxes_module
from anum_api.mailbox import service
from anum_api.mailbox.config import MailServerSettings, mail_settings
from anum_api.mailbox.server import Page, imap_date
from anum_api.mailbox.store import mail_backend
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.store import store

MAIL = "/api/v1/mail"
MEMBER = {**OWNER, "x-user-id": "member_mc", "x-user-roles": "member"}
OTHER = {**OWNER, "x-user-id": "other_mc", "x-user-roles": "member"}
STRANGER = {"x-tenant-id": "tenant_elsewhere", "x-workspace-id": "workspace_elsewhere", "x-user-id": "stranger_mc", "x-user-roles": "owner"}
START = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


def message(subject: str, to: str, when: datetime) -> bytes:
    item = EmailMessage()
    item["From"] = "Sara <sara@client.example>"
    item["To"] = to
    item["Subject"] = subject
    item["Date"] = format_datetime(when)
    item["Message-ID"] = f"<{abs(hash((subject, to)))}@client.example>"
    item.set_content(f"Hello, this is {subject}.")
    return item.as_bytes()


class DatedBound(Bound):
    """The shared fake plus the received-date filter the real server runs as IMAP SINCE and BEFORE."""

    def messages(self, folder: str, *, before: int | None, limit: int, query: str | None, unread: bool, flagged: bool, since: date | None = None, until: date | None = None) -> Page:
        self.fake.calls += 1  # type: ignore[attr-defined]
        hold = getattr(self.fake, "hold", None)
        if hold is not None:
            hold.wait(5)  # a slow mail server: answers only when the test lets it
        if since is None and until is None:
            return super().messages(folder, before=before, limit=limit, query=query, unread=unread, flagged=flagged)
        kept = [i for i in self._folder(folder) if (since is None or self.fake.dates[i.uid] >= since) and (until is None or self.fake.dates[i.uid] <= until)]  # type: ignore[attr-defined]
        original = self.fake.folders_[folder]
        self.fake.folders_[folder] = (original[0], kept)
        try:
            return super().messages(folder, before=before, limit=limit, query=query, unread=unread, flagged=flagged)
        finally:
            self.fake.folders_[folder] = original


def inbox(address: str, offsets_hours: list[int], prefix: str) -> FakeServer:
    """An inbox whose UID n arrived ``offsets_hours[n-1]`` hours after START (UIDs follow arrival)."""
    items = [Stored(n, message(f"{prefix} {n}", address, START + timedelta(hours=h))) for n, h in enumerate(offsets_hours, start=1)]
    fake = FakeServer(folders_={"INBOX": ("inbox", items), "Sent": ("sent", []), "Drafts": ("drafts", []), "Trash": ("trash", [])})
    fake.dates = {n: (START + timedelta(hours=h)).date() for n, h in enumerate(offsets_hours, start=1)}  # type: ignore[attr-defined]
    fake.calls = 0  # type: ignore[attr-defined]
    return fake


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, FakeServer]]:
    found = {
        # Owner's mailbox: even hours; member's: odd hours, so the merged list interleaves them.
        "owner@example.test": inbox("owner@example.test", [0, 2, 4, 6, 8, 50], "Owner"),
        "member@example.test": inbox("member@example.test", [1, 3, 5, 7, 9], "Member"),
        "other@example.test": inbox("other@example.test", [10], "Other"),
        "stranger@example.test": inbox("stranger@example.test", [11], "Stranger"),
    }

    def bind(cfg: MailServerSettings, username: str, password: str) -> DatedBound:
        return DatedBound(found[username], password)

    monkeypatch.setattr(mail_settings, "enabled", True)
    monkeypatch.setattr(mail_settings, "imap_host", "mail.example.test")
    monkeypatch.setattr(mail_settings, "smtp_host", "mail.example.test")
    monkeypatch.setattr(mail_settings, "allowed_domains", "example.test")
    monkeypatch.setattr(service, "server_factory", bind)
    now = utc_now()
    for user, role in (("owner_mc", "owner"), ("member_mc", "member"), ("other_mc", "member"), ("viewer_mc", "viewer")):
        store.memberships[(TENANT, WORKSPACE, user)] = WorkspaceMembership(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role=role, created_at=now, updated_at=now)
    mail_backend.clear()
    service.reset_connect_attempts()
    yield found
    mail_backend.clear()
    service.reset_connect_attempts()
    for user in ("owner_mc", "member_mc", "other_mc", "viewer_mc"):
        store.memberships.pop((TENANT, WORKSPACE, user), None)


def connect(headers: dict[str, str], address: str) -> dict:
    response = client.post(f"{MAIL}/accounts", headers=headers, json={"address": address, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def share(account_id: str, owner: dict[str, str], grantee: str, access: str = "read") -> None:
    response = client.post(f"{MAIL}/accounts/{account_id}/grants", headers=owner, json={"grantee_user_id": grantee, "access": access})
    assert response.status_code == 201, response.text


def all_inboxes(headers: dict[str, str] = OWNER, **params: object) -> dict:
    response = client.get(f"{MAIL}/all-inboxes", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def mailboxes(servers: dict[str, FakeServer]) -> dict[str, dict]:
    """The owner's own mailbox, the member's mailbox shared with the owner, and two the owner may not read."""
    own = connect(OWNER, "owner@example.test")
    member = connect(MEMBER, "member@example.test")
    other = connect(OTHER, "other@example.test")  # in the workspace, never shared with the owner
    stranger = connect(STRANGER, "stranger@example.test")  # another organisation
    share(member["id"], MEMBER, "owner_mc", "read")
    return {"own": own, "member": member, "other": other, "stranger": stranger}


def test_one_list_newest_first_with_a_mailbox_badge_per_message(mailboxes: dict[str, dict]) -> None:
    page = all_inboxes(limit=4)
    assert [m["subject"] for m in page["items"]] == ["Owner 6", "Member 5", "Owner 5", "Member 4"]
    first = page["items"][0]
    assert first["account_id"] == mailboxes["own"]["id"] and first["account_address"] == "owner@example.test" and first["access"] == "owner"
    assert page["items"][1]["account_address"] == "member@example.test" and page["items"][1]["access"] == "read"
    assert first["folder"] == "INBOX" and page["total"] == 11 and page["next_cursor"]
    assert {m["address"]: m["state"] for m in page["mailboxes"]} == {"owner@example.test": "ok", "member@example.test": "ok"}


def test_paging_walks_every_message_once_and_ends(mailboxes: dict[str, dict]) -> None:
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = all_inboxes(limit=3, **({"cursor": cursor} if cursor else {}))
        seen += [m["subject"] for m in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 11 and len(set(seen)) == 11
    assert seen[:2] == ["Owner 6", "Member 5"] and seen[-1] == "Owner 1"


def test_each_page_reads_each_inbox_once(servers: dict[str, FakeServer], mailboxes: dict[str, dict]) -> None:
    all_inboxes(limit=5)
    assert servers["owner@example.test"].calls == 1 and servers["member@example.test"].calls == 1  # type: ignore[attr-defined]
    assert servers["other@example.test"].calls == 0 and servers["stranger@example.test"].calls == 0  # type: ignore[attr-defined]


def test_only_readable_mailboxes_and_never_another_organisation(mailboxes: dict[str, dict]) -> None:
    addresses = {m["account_address"] for m in all_inboxes(limit=50)["items"]}
    assert addresses == {"owner@example.test", "member@example.test"}
    # A mailbox in the workspace that is not shared, and one in another organisation, answer alike.
    for hidden in ("other", "stranger"):
        response = client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"account_id": mailboxes[hidden]["id"]})
        assert response.status_code == 404
    # The other organisation's owner sees only their own.
    assert {m["account_address"] for m in all_inboxes(STRANGER)["items"]} == {"stranger@example.test"}
    # Removing the share removes the mailbox from the list at once.
    grants = client.get(f"{MAIL}/accounts/{mailboxes['member']['id']}/grants", headers=MEMBER).json()
    assert client.delete(f"{MAIL}/grants/{grants[0]['id']}", headers=MEMBER).status_code == 204
    assert {m["account_address"] for m in all_inboxes(limit=50)["items"]} == {"owner@example.test"}


def test_viewers_are_refused(mailboxes: dict[str, dict]) -> None:
    response = client.get(f"{MAIL}/all-inboxes", headers=VIEWER)
    assert response.status_code == 403 and "owner or member" in response.json()["error"]["message"]


def test_filters_mailbox_unread_flagged_search_and_dates(servers: dict[str, FakeServer], mailboxes: dict[str, dict]) -> None:
    only = all_inboxes(account_id=mailboxes["member"]["id"], limit=50)
    assert {m["account_address"] for m in only["items"]} == {"member@example.test"} and len(only["items"]) == 5
    owner_items = servers["owner@example.test"].folders_["INBOX"][1]
    owner_items[0].flags.add("\\Seen")
    owner_items[1].flags.add("\\Flagged")
    unread = all_inboxes(unread="true", limit=50)
    assert "Owner 1" not in [m["subject"] for m in unread["items"]] and len(unread["items"]) == 10
    assert [m["subject"] for m in all_inboxes(flagged="true", limit=50)["items"]] == ["Owner 2"]
    assert [m["subject"] for m in all_inboxes(q="Member 3", limit=50)["items"]] == ["Member 3"]
    # Owner 6 arrived two days later than everything else.
    later = (START + timedelta(hours=50)).date().isoformat()
    assert [m["subject"] for m in all_inboxes(since=later, limit=50)["items"]] == ["Owner 6"]
    assert len(all_inboxes(until=START.date().isoformat(), limit=50)["items"]) == 10
    bad = client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"since": later, "until": START.date().isoformat()})
    assert bad.status_code == 422


def test_a_cursor_is_bound_to_its_filters(mailboxes: dict[str, dict]) -> None:
    cursor = all_inboxes(limit=2)["next_cursor"]
    changed = client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"cursor": cursor, "unread": "true"})
    assert changed.status_code == 422 and "filters changed" in changed.json()["error"]["message"]
    assert client.get(f"{MAIL}/all-inboxes", headers=OWNER, params={"cursor": "not-a-cursor"}).status_code == 422


def test_a_cursor_naming_someone_elses_mailbox_reads_nothing_of_it(servers: dict[str, FakeServer], mailboxes: dict[str, dict]) -> None:
    fingerprint = inboxes_module._fingerprint([], None, False, False, None, None)
    forged = inboxes_module.encode_cursor({mailboxes["other"]["id"]: 99, mailboxes["stranger"]["id"]: 99}, fingerprint)
    page = all_inboxes(cursor=forged, limit=50)
    assert {m["account_address"] for m in page["items"]} == {"owner@example.test", "member@example.test"}
    assert servers["other@example.test"].calls == 0 and servers["stranger@example.test"].calls == 0  # type: ignore[attr-defined]


def test_a_mailbox_that_fails_is_reported_and_the_rest_still_list(servers: dict[str, FakeServer], mailboxes: dict[str, dict]) -> None:
    servers["member@example.test"].offline = True
    page = all_inboxes(limit=50)
    assert {m["account_address"] for m in page["items"]} == {"owner@example.test"}
    states = {m["address"]: m for m in page["mailboxes"]}
    assert states["member@example.test"]["state"] == "offline" and states["member@example.test"]["total"] is None
    assert states["owner@example.test"]["state"] == "ok"


def test_a_message_opens_and_replies_in_its_own_mailbox(servers: dict[str, FakeServer], mailboxes: dict[str, dict]) -> None:
    share(mailboxes["member"]["id"], MEMBER, "owner_mc", "send")
    item = next(m for m in all_inboxes(limit=50)["items"] if m["account_address"] == "member@example.test")
    opened = client.get(f"{MAIL}/accounts/{item['account_id']}/message", headers=OWNER, params={"folder": item["folder"], "uid": item["uid"]})
    assert opened.status_code == 200 and opened.json()["message"]["subject"] == item["subject"]
    draft = client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": item["account_id"], "kind": "reply", "folder": item["folder"], "uid": item["uid"]})
    assert draft.status_code == 201 and draft.json()["from_address"] == "member@example.test"
    asked = client.post(f"{MAIL}/drafts/{draft.json()['id']}/send", headers=OWNER, json={"idempotency_key": "all-inboxes-reply-1"})
    assert asked.status_code == 200 and asked.json()["approval"]["arguments"]["from"] == "member@example.test"
    assert not servers["member@example.test"].sent  # nothing goes before an owner approves


def test_merge_keeps_each_inbox_in_its_own_order_and_imap_dates() -> None:
    assert imap_date(date(2026, 2, 1)) == "1-Feb-2026" and imap_date(date(2026, 12, 31)) == "31-Dec-2026"
    assert inboxes_module.decode_cursor(inboxes_module.encode_cursor({"a": 3}, "f1"), "f1") == {"a": 3}
    with pytest.raises(HTTPException):
        inboxes_module.decode_cursor(inboxes_module.encode_cursor({"a": -1}, "f1"), "f1")


def test_a_slow_inbox_does_not_hold_the_page(servers: dict[str, FakeServer], mailboxes: dict[str, dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inboxes_module, "INBOX_TIMEOUT_SECONDS", 0.3)
    hold = threading.Event()
    servers["member@example.test"].hold = hold  # type: ignore[attr-defined]
    context = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_mc", roles=["owner"])
    # The route on a loop that stays open, as in the server (a TestClient request closes its loop,
    # which waits for the slow server's thread before answering).
    loop = asyncio.new_event_loop()
    try:
        started = time.monotonic()
        result = loop.run_until_complete(inboxes_module.list_all_inboxes(
            account_id=[], cursor=None, limit=50, q=None, unread=False, flagged=False, since=None, until=None, context=context,
        ))
        elapsed = time.monotonic() - started
    finally:
        hold.set()
        loop.close()
    assert elapsed < 3, elapsed  # the page went out after the timeout, not after the slow server
    page = result.model_dump()
    assert page["partial"] is True
    assert {m["account_address"] for m in page["items"]} == {"owner@example.test"}
    states = {m["address"]: m for m in page["mailboxes"]}
    assert states["member@example.test"]["state"] == "slow" and states["member@example.test"]["total"] is None
    assert "did not answer in time" in states["member@example.test"]["error"]
    # Once it answers again, the next load includes it and the page is whole.
    del servers["member@example.test"].hold  # type: ignore[attr-defined]
    again = all_inboxes(limit=50)
    assert again["partial"] is False and "member@example.test" in {m["account_address"] for m in again["items"]}
