"""Email Center phase 6 (docs/email.md#mailbox-settings): retention settings and forwarding rules
behind payload-hash approvals, the background sync of unread counts, and the retention purge,
against the fake mail server of ``test_mail_center``. Every address here is a plainly fake fixture.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from email import policy
from email.parser import BytesParser

import pytest
from test_mail_center import MEMBER, OWNER, VIEWER, FakeServer, Stored, client, connect, fake, raw_message  # noqa: F401
from test_mail_sharing import members, share  # noqa: F401

from anum_api import mail_retention
from anum_api.mailbox import sync
from anum_api.mailbox.automation import automation_backend, open_automation_store
from anum_api.mailbox.automation_tools import AUTOMATION_TOOL_DEFINITIONS, RULE_TOOL, SETTINGS_TOOL
from anum_api.mailbox.config import mail_settings
from anum_api.mailbox.labels import MailLabel, label_backend, open_label_store
from anum_api.mailbox.server import MailServerError
from anum_api.mailbox.store import DraftStatus, mail_backend
from anum_api.schemas import TenantContext, utc_now

MAIL = "/api/v1/mail"
FRIEND = "partner@elsewhere.example"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    automation_backend.clear()
    label_backend.clear()
    yield
    automation_backend.clear()
    label_backend.clear()


def approve(approval: dict, headers: dict[str, str] = OWNER):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=headers, json={"payload_hash": approval["payload_hash"]})


def settings_of(account: dict, headers: dict[str, str] = OWNER) -> dict:
    response = client.get(f"{MAIL}/accounts/{account['id']}/settings", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def ask(account: dict, version: int, **values: object):  # type: ignore[no-untyped-def]
    body = {"sent_retention_days": None, "draft_retention_days": None, "label_retention_days": None, "background_sync": True, "base_version": version, **values}
    return client.post(f"{MAIL}/accounts/{account['id']}/settings", headers=OWNER, json=body)


def run_sync(now=None) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return asyncio.run(sync.run_once(now or utc_now()))


def due_again() -> None:
    """Make every mailbox due now (the interval is minutes)."""
    with automation_backend.lock:
        for key, state in automation_backend.sync.items():
            automation_backend.sync[key] = state.model_copy(update={"next_sync_at": utc_now() - timedelta(seconds=1)})


def new_rule(account: dict, **values: object):  # type: ignore[no-untyped-def]
    body = {"name": "Orders to partner", "from_contains": "sara@client.example", "forward_to": [FRIEND], **values}
    return client.post(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER, json=body)


def arrive(fake: FakeServer, subject: str, sender: str = "Sara <sara@client.example>", raw: bytes | None = None) -> int:
    fake.next_uid += 1
    fake.folders_["INBOX"][1].append(Stored(fake.next_uid, raw or raw_message(subject, sender)))
    return fake.next_uid


# -- the tools never run without approval ----------------------------------------------------------


def test_settings_and_rule_tools_always_wait_for_an_approval() -> None:
    names = {definition.name: definition for definition, _ in AUTOMATION_TOOL_DEFINITIONS}
    assert set(names) == {SETTINGS_TOOL, RULE_TOOL}
    assert all(d.always_ask and d.risk_level.value == "high" for d in names.values())


# -- settings -------------------------------------------------------------------------------------


def test_settings_change_only_after_an_approval_of_exactly_that_change(fake: FakeServer) -> None:
    account = connect()
    shown = settings_of(account)
    assert shown["sent_retention_days"] is None and shown["background_sync"] is True and shown["version"] == 0 and shown["can_change"] is True
    assert shown["retention_choices"] == [7, 30, 90, 180, 365, 730] and shown["approval"] is None
    assert ask(account, 0).status_code == 422  # nothing changed
    waiting = ask(account, 0, sent_retention_days=30, label_retention_days=90)
    assert waiting.status_code == 200, waiting.text
    approval = waiting.json()["approval"]
    assert approval["action"] == SETTINGS_TOOL and approval["payload_hash"]
    assert approval["arguments"]["settings"] == {"sent_retention_days": 30, "draft_retention_days": None, "label_retention_days": 90, "background_sync": True}
    # Not applied yet; the page shows the change waiting.
    pending = settings_of(account)
    assert pending["sent_retention_days"] is None and pending["approval"]["id"] == approval["id"] and pending["pending"]["sent_retention_days"] == 30
    # A wrong payload hash is refused; the right one applies it.
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=OWNER, json={"payload_hash": "0" * 64}).status_code in {409, 422}
    assert approve(approval).status_code == 200
    applied = settings_of(account)
    assert applied["sent_retention_days"] == 30 and applied["label_retention_days"] == 90 and applied["version"] == 1 and applied["approval"] is None
    # Asked against an old version: refused before any approval exists.
    assert ask(account, 0, draft_retention_days=7).status_code == 409


def test_a_change_approved_after_another_one_is_stale(fake: FakeServer) -> None:
    account = connect()
    first = ask(account, 0, sent_retention_days=30).json()["approval"]
    # The same base version again replaces the waiting change (the first is withdrawn).
    second = ask(account, 0, sent_retention_days=7).json()["approval"]
    assert approve(first).status_code != 200 or settings_of(account)["sent_retention_days"] is None
    assert approve(second).status_code == 200 and settings_of(account)["sent_retention_days"] == 7
    # A call approved against version 0 after the settings moved to 1 changes nothing.
    from anum_api.mailbox.automation_tools import change_mail_settings
    from anum_api.agent_tools import ToolCall

    context = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id="owner_mc", roles=["owner"])
    stale = asyncio.run(change_mail_settings(ToolCall(name=SETTINGS_TOOL, arguments={
        "account_id": account["id"], "owner_user_id": "owner_mc", "base_version": 0,
        "settings": {"sent_retention_days": 365, "draft_retention_days": None, "label_retention_days": None, "background_sync": True},
    }), context))
    assert stale.status == "failed" and stale.output["kind"] == "stale" and settings_of(account)["sent_retention_days"] == 7


def test_withdrawing_a_waiting_change(fake: FakeServer) -> None:
    account = connect()
    approval = ask(account, 0, background_sync=False).json()["approval"]
    assert client.delete(f"{MAIL}/accounts/{account['id']}/settings/pending", headers=OWNER).status_code == 204
    assert settings_of(account)["approval"] is None
    assert approve(approval).status_code != 200
    assert settings_of(account)["background_sync"] is True
    assert client.delete(f"{MAIL}/accounts/{account['id']}/settings/pending", headers=OWNER).status_code == 404


def test_people_a_mailbox_is_shared_with_read_settings_but_never_change_them(fake: FakeServer, members: None) -> None:
    account = connect()
    share(account["id"], "send")
    shown = settings_of(account, MEMBER)
    assert shown["access"] == "send" and shown["can_change"] is False
    body = {"sent_retention_days": 7, "base_version": 0}
    assert client.post(f"{MAIL}/accounts/{account['id']}/settings", headers=MEMBER, json=body).status_code == 403
    assert client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=MEMBER).status_code == 403
    assert client.get(f"{MAIL}/accounts/{account['id']}/settings", headers=VIEWER).status_code == 403
    assert client.post(f"{MAIL}/accounts/{account['id']}/settings", headers=OWNER, json={**body, "sent_retention_days": 5000}).status_code == 422


# -- forwarding rules -------------------------------------------------------------------------------


def test_a_rule_forwards_nothing_until_approved_then_only_new_matching_mail_once(fake: FakeServer) -> None:
    account = connect()
    assert new_rule(account, from_contains="", subject_contains="").status_code == 422
    assert new_rule(account, forward_to=["owner@example.test"]).status_code == 422  # never to itself
    assert new_rule(account, forward_to=["not an address"]).status_code == 422
    created = new_rule(account)
    assert created.status_code == 201, created.text
    rule, approval = created.json()["rule"], created.json()["approval"]
    assert rule["status"] == "pending_approval" and approval["action"] == RULE_TOOL and approval["arguments"]["forward_to"] == [FRIEND]
    arrive(fake, "Order 1")
    run_sync()
    assert fake.sent == []  # waiting for approval: nothing forwarded
    assert approve(approval).status_code == 200
    rules = client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json()
    assert rules[0]["status"] == "active" and rules[0]["approval"] is None
    due_again()
    run_sync()
    assert fake.sent == []  # the first pass after approval only marks where it starts
    arrive(fake, "Order 2")
    arrive(fake, "Hello from someone else", sender="Omar <omar@other.example>")
    due_again()
    run_sync()
    assert len(fake.sent) == 1
    sender, recipients, raw = fake.sent[0]
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert sender == "owner@example.test" and recipients == [FRIEND]
    assert message["Auto-Submitted"] == "auto-forwarded" and message["X-ANUM-Forward-Rule"] == rule["id"] and message["Subject"] == "Fwd: Order 2"
    assert any(b"Order 2" in item.raw for item in fake.folders_["Sent"][1])  # a copy in Sent
    due_again()
    run_sync()
    assert len(fake.sent) == 1  # never twice
    shown = client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json()[0]
    assert shown["forwarded_count"] == 1 and shown["last_run_at"]


def test_editing_pausing_and_tampering_stop_forwarding(fake: FakeServer) -> None:
    account = connect()
    created = new_rule(account).json()
    approve(created["approval"])
    run_sync()  # baseline
    rule_id = created["rule"]["id"]
    edited = client.patch(f"{MAIL}/rules/{rule_id}", headers=OWNER, json={"forward_to": ["other@elsewhere.example"]})
    assert edited.status_code == 200 and edited.json()["rule"]["status"] == "pending_approval"
    # The first approval (of the old rule) no longer turns anything on.
    assert approve(created["approval"]).status_code != 200
    arrive(fake, "Order 3")
    due_again()
    run_sync()
    assert fake.sent == []
    assert approve(edited.json()["approval"]).status_code == 200
    paused = client.post(f"{MAIL}/rules/{rule_id}/pause", headers=OWNER)
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    arrive(fake, "Order 4")
    due_again()
    run_sync()
    assert fake.sent == []
    resumed = client.post(f"{MAIL}/rules/{rule_id}/resume", headers=OWNER).json()
    assert resumed["rule"]["status"] == "pending_approval"
    assert approve(resumed["approval"]).status_code == 200
    # Changed behind the API (the stored addresses no longer match the approved hash): refused at run time.
    with automation_backend.lock:
        stored = automation_backend.rules[rule_id]
        automation_backend.rules[rule_id] = stored.model_copy(update={"forward_to": ["attacker@evil.example"]})
    arrive(fake, "Order 5")
    due_again()
    run_sync()
    assert fake.sent == []
    shown = client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json()[0]
    assert shown["status"] == "paused" and "changed after it was approved" in shown["last_error"]
    assert client.delete(f"{MAIL}/rules/{rule_id}", headers=OWNER).status_code == 204
    assert client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json() == []


def test_loop_guard_and_hourly_limit(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    account = connect()
    approve(new_rule(account, from_contains="", subject_contains="Order").json()["approval"])
    run_sync()
    automatic = raw_message("Order robot").replace(b"Subject:", b"Auto-Submitted: auto-replied\nSubject:", 1)
    arrive(fake, "Order robot", raw=automatic)
    monkeypatch.setattr(mail_settings, "send_per_hour", 2)
    for n in range(4):
        arrive(fake, f"Order {n}")
    due_again()
    run_sync()
    assert len(fake.sent) == 2 and all(b"Order robot" not in raw for _, _, raw in fake.sent)
    shown = client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json()[0]
    assert "limit" in shown["last_error"]


def test_only_the_mailbox_owner_manages_rules(fake: FakeServer, members: None) -> None:
    account = connect()
    share(account["id"], "send")
    body = {"name": "x", "from_contains": "a", "forward_to": [FRIEND]}
    assert client.post(f"{MAIL}/accounts/{account['id']}/rules", headers=MEMBER, json=body).status_code == 403
    rule = new_rule(account).json()
    # A member cannot approve: the high-risk approval needs a workspace owner.
    assert approve(rule["approval"], MEMBER).status_code == 403
    assert client.post(f"{MAIL}/rules/{rule['rule']['id']}/pause", headers=MEMBER).status_code == 404
    assert client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=VIEWER).status_code == 403


# -- background sync and the unread badge -----------------------------------------------------------


def test_background_sync_keeps_the_unread_badge_current(fake: FakeServer, members: None) -> None:
    account = connect()
    assert client.get(f"{MAIL}/unread", headers=OWNER).json()["total"] == 0
    assert run_sync() == {"checked": 1}
    unread = client.get(f"{MAIL}/unread", headers=OWNER).json()
    assert unread["enabled"] is True and unread["total"] == 30 and unread["mailboxes"][0]["unread"] == 30 and unread["checked_at"]
    # Not due again yet: nothing is checked twice.
    assert run_sync() == {}
    fake.folders_["INBOX"][1][0].flags.add("\\Seen")
    due_again()
    run_sync()
    assert client.get(f"{MAIL}/unread", headers=OWNER).json()["total"] == 29
    # People it is shared with see its count too; viewers have no mail.
    share(account["id"], "read")
    assert client.get(f"{MAIL}/unread", headers=MEMBER).json()["total"] == 29
    assert client.get(f"{MAIL}/unread", headers=VIEWER).status_code == 403
    # Reading for the count never marks anything read.
    assert sum(1 for item in fake.folders_["INBOX"][1] if "\\Seen" in item.flags) == 1


def test_a_held_lease_and_a_refused_password(fake: FakeServer) -> None:
    account = connect()
    context = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id="owner_mc", roles=["member"])
    from anum_api.mailbox.automation import SyncState

    state = SyncState(account_id=account["id"], tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id="owner_mc")
    now = utc_now()
    with open_automation_store(context) as store:
        assert store.claim_sync(state, now, now + timedelta(minutes=5)) is True
        assert store.claim_sync(state, now, now + timedelta(minutes=5)) is False  # another replica holds it
    assert sync.due_mailboxes(now) == []
    automation_backend.clear()
    fake.password = "changed-on-the-server"
    assert run_sync() == {"auth_failed": 1}
    assert client.get(f"{MAIL}/unread", headers=OWNER).json()["mailboxes"][0]["status"] == "auth_failed"
    # A refused password is left alone until the owner enters it again.
    due_again()
    assert run_sync() == {}


def test_background_sync_can_be_turned_off_with_an_approved_change(fake: FakeServer) -> None:
    account = connect()
    approve(ask(account, 0, background_sync=False).json()["approval"])
    assert run_sync() == {}
    fake.offline = True
    approve(ask(account, 1, background_sync=True).json()["approval"])
    assert run_sync() == {"offline": 1}


def test_sync_survives_a_mail_server_error(fake: FakeServer) -> None:
    connect()
    fake.offline = True
    assert run_sync() == {"offline": 1}
    state = next(iter(automation_backend.sync.values()))
    assert state.lease_until is None and state.next_sync_at > utc_now() and state.status == "offline"


# -- retention purge ---------------------------------------------------------------------------------


def test_the_purge_deletes_only_what_is_past_the_mailbox_retention(fake: FakeServer) -> None:
    account = connect()
    old = utc_now() - timedelta(days=40)
    drafts = {}
    for name in ("sent_old", "sent_new", "draft_old", "pending_old"):
        drafts[name] = client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": account["id"]}).json()["id"]
    with mail_backend.lock:
        for name, status in (("sent_old", DraftStatus.SENT), ("sent_new", DraftStatus.SENT), ("draft_old", DraftStatus.DRAFT), ("pending_old", DraftStatus.PENDING_APPROVAL)):
            draft = mail_backend.drafts[drafts[name]]
            when = utc_now() if name == "sent_new" else old
            mail_backend.drafts[draft.id] = draft.model_copy(update={"status": status, "sent_at": when if status == DraftStatus.SENT else None, "updated_at": when})
    context = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id="owner_mc", roles=["owner"])
    with open_label_store(context) as labels:
        for uid, when in ((1, old), (2, utc_now())):
            labels.save_label(MailLabel(
                id=f"mlabel_{uid}", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id="owner_mc", account_id=account["id"],
                folder="INBOX", uid=uid, category="order", priority="normal", source="rules", updated_at=when,
            ))
    assert mail_retention.purge_memory().mailboxes_scanned == 0  # no retention chosen: everything stays
    approve(ask(account, 0, sent_retention_days=30, draft_retention_days=30, label_retention_days=30).json()["approval"])
    counted = mail_retention.purge_memory(dry_run=True)
    assert (counted.sent, counted.drafts, counted.labels) == (1, 1, 1) and drafts["sent_old"] in mail_backend.drafts
    done = mail_retention.purge_memory()
    assert (done.sent, done.drafts, done.labels) == (1, 1, 1)
    left = set(mail_backend.drafts)
    assert drafts["sent_new"] in left and drafts["pending_old"] in left and drafts["sent_old"] not in left and drafts["draft_old"] not in left
    with open_label_store(context) as labels:
        assert [label.uid for label in labels.list_all()] == [2]


def test_state_changes_are_audited_without_addresses(fake: FakeServer) -> None:
    from anum_api.store import store

    account = connect()
    new_rule(account)
    ask(account, 0, sent_retention_days=7)
    records = [r for r in store.audit_records if r.action in {"mail.rule.created", "mail.settings.requested"}]
    assert {r.action for r in records} == {"mail.rule.created", "mail.settings.requested"}
    assert all(FRIEND not in str(r.metadata) for r in records)


def test_mail_server_errors_in_rules_do_not_lose_the_counts(fake: FakeServer) -> None:
    account = connect()
    approve(new_rule(account).json()["approval"])
    run_sync()
    arrive(fake, "Order 9")
    fake.send_error = MailServerError("rejected", "Recipient refused by policy.")
    due_again()
    assert run_sync() == {"checked": 1}
    shown = client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json()[0]
    assert shown["forwarded_count"] == 0 and "not sent" in shown["last_error"]
    assert client.get(f"{MAIL}/unread", headers=OWNER).json()["total"] == 31


# -- review fixes ---------------------------------------------------------------------------------


def test_the_hourly_limit_counts_every_writer_of_a_shared_mailbox(fake: FakeServer, members: None, monkeypatch: pytest.MonkeyPatch) -> None:
    account = connect()
    share(account["id"], "send")
    approve(new_rule(account).json()["approval"])
    run_sync()
    monkeypatch.setattr(mail_settings, "send_per_hour", 3)
    # The person it is shared with sent three messages from it in the last hour.
    for _ in range(3):
        draft = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).json()
        with mail_backend.lock:
            mail_backend.drafts[draft["id"]] = mail_backend.drafts[draft["id"]].model_copy(update={"status": DraftStatus.SENT, "sent_at": utc_now(), "updated_at": utc_now()})
    arrive(fake, "Order 11")
    due_again()
    run_sync()
    assert fake.sent == []
    assert "limit" in client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json()[0]["last_error"]


def test_more_new_mail_than_one_page_is_all_forwarded(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    account = connect()
    approve(new_rule(account, from_contains="", subject_contains="Order").json()["approval"])
    run_sync()  # starts after uid 30
    monkeypatch.setattr(mail_settings, "forward_per_pass", 200)
    monkeypatch.setattr(mail_settings, "send_per_hour", 500)
    uids = [arrive(fake, f"Order {n}") for n in range(70)]
    due_again()
    run_sync()
    forwarded = [BytesParser(policy=policy.default).parsebytes(raw)["Subject"] for _, _, raw in fake.sent]
    assert len(forwarded) == 70 and forwarded[0] == "Fwd: Order 0" and forwarded[-1] == "Fwd: Order 69"
    assert client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json()[0]["forwarded_count"] == 70
    assert uids[0] < uids[-1]


def test_only_sent_or_maybe_sent_forwards_count_toward_the_limit(fake: FakeServer) -> None:
    from anum_api.mailbox.automation import ForwardLog

    account = connect()
    rule_id = new_rule(account).json()["rule"]["id"]
    context = TenantContext(tenant_id=OWNER["x-tenant-id"], workspace_id=OWNER["x-workspace-id"], user_id="owner_mc", roles=["owner"])
    with open_automation_store(context) as store:
        for uid, status in ((1, "forwarded"), (2, "unknown"), (3, "failed"), (4, "failed"), (5, "forwarding")):
            store.claim_forward(ForwardLog(id=f"mfwd_{uid}", tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id="owner_mc", rule_id=rule_id, uid=uid, status=status))
        assert store.count_forwards_since([rule_id], utc_now() - timedelta(hours=1)) == 3


def test_disconnecting_removes_every_writers_stored_files(fake: FakeServer, members: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.mailbox import api as mail_api

    account = connect()
    share(account["id"], "send")
    mine = client.post(f"{MAIL}/drafts", headers=OWNER, json={"account_id": account["id"]}).json()
    theirs = client.post(f"{MAIL}/drafts", headers=MEMBER, json={"account_id": account["id"]}).json()
    for draft, headers in ((mine, OWNER), (theirs, MEMBER)):
        uploaded = client.post(f"{MAIL}/drafts/{draft['id']}/attachments", headers=headers, params={"name": "notes.txt"}, content=b"plain example text")
        assert uploaded.status_code == 201, uploaded.text
    forgotten: list[str] = []
    monkeypatch.setattr(mail_api, "forget_objects", lambda files: forgotten.extend(f.owner_user_id for f in files))
    assert client.delete(f"{MAIL}/accounts/{account['id']}", headers=OWNER).status_code == 204
    assert sorted(forgotten) == ["member_mc", "owner_mc"]
    assert mail_backend.attachments == {}


def test_a_new_uidvalidity_restarts_the_rule_instead_of_skipping_mail(fake: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    from test_mail_center import Bound

    validity = {"value": 1}
    plain = Bound.folders
    monkeypatch.setattr(Bound, "folders", lambda self: [dataclasses.replace(f, uidvalidity=validity["value"]) for f in plain(self)])
    account = connect()
    approve(new_rule(account).json()["approval"])
    run_sync()  # starts after uid 30 in UIDVALIDITY 1
    arrive(fake, "Order before")  # uid 101
    due_again()
    run_sync()
    assert len(fake.sent) == 1
    # The server renumbers the inbox (UIDVALIDITY 2): UIDs start again from 1.
    validity["value"] = 2
    fake.folders_["INBOX"] = ("inbox", [Stored(i, raw_message(f"Renumbered {i}")) for i in range(1, 6)])
    due_again()
    run_sync()
    assert len(fake.sent) == 1  # the restart forwards nothing old
    rule = client.get(f"{MAIL}/accounts/{account['id']}/rules", headers=OWNER).json()[0]
    with automation_backend.lock:
        stored = automation_backend.rules[rule["id"]]
        assert stored.last_uid == 5 and stored.last_uidvalidity == 2
    fake.folders_["INBOX"][1].append(Stored(6, raw_message("Order after", "Sara <sara@client.example>")))
    due_again()
    run_sync()
    assert len(fake.sent) == 2 and b"Order after" in fake.sent[1][2]
