"""Download my data and Delete my data (memory backend, docs/privacy.md)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from anum_api import privacy
from anum_api.conversations import conversation_store
from anum_api.dependencies import memory_note_repository
from anum_api.files import file_store
from anum_api.governance import MemoryGovernance, governance_store
from anum_api.learning_store import learning_data
from anum_api.main import app, store
from anum_api.memory import MemoryNote, MemoryProvenance, RetentionKind, RetentionPolicy
from anum_api.onboarding import _notifications
from anum_api.push import PushPlatform, StoredPushDevice, push_device_store
from anum_api.user_preferences import user_preference_store
from anum_api.retrieval import IndexStatus, RetrievalSource, SourceType, memory_retrieval_store
from anum_api.schemas import TenantContext, new_id, utc_now
from anum_api.settings import settings
from anum_api.voice import voice_store
from anum_api.autonomy import autonomy_memory_store
from anum_api.email_inbox import email_backend
from anum_api.whatsapp_store import whatsapp_store

client = TestClient(app)


def headers(user: str = "user_me", tenant: str = "tenant_p", workspace: str = "workspace_p", roles: str = "member") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": roles}


ME = headers()
COLLEAGUE = headers("user_colleague")
OTHER_WORKSPACE = headers(workspace="workspace_other")
OTHER_TENANT = headers(tenant="tenant_other")
OWNER = headers("user_owner", roles="owner")


def setup_function() -> None:
    for collection in (store.tasks, store.runs, store.approvals, store.approval_approvers):
        collection.clear()
    store.events.clear()
    store.audit_records.clear()
    conversation_store.clear()
    memory_note_repository._notes.clear()
    memory_retrieval_store.clear()
    voice_store.clear()
    _notifications.clear()
    user_preference_store.clear()
    governance_store.clear()
    push_device_store.clear()
    file_store.clear()
    learning_data.clear()
    email_backend.clear()
    whatsapp_store.clear()
    autonomy_memory_store.clear()


def _context(hdrs: dict[str, str]) -> TenantContext:
    return TenantContext(tenant_id=hdrs["x-tenant-id"], workspace_id=hdrs["x-workspace-id"], user_id=hdrs["x-user-id"], roles=["member"])


def _private_memory(hdrs: dict[str, str], content: str) -> str:
    context = _context(hdrs)
    now = utc_now()
    note = memory_note_repository.create(
        MemoryNote(
            id=new_id("memory"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            task_id=None,
            content=content,
            provenance=MemoryProvenance(source_type="conversation", source_id="conv_x", created_by_user_id=context.user_id, created_at=now),
            retention=RetentionPolicy(kind=RetentionKind.INDEFINITE),
            created_at=now,
            owner_user_id=context.user_id,
        )
    )
    memory_retrieval_store.save_source(
        context,
        RetrievalSource(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            source_type=SourceType.MEMORY,
            source_id=note.id,
            content_sha256="0" * 64,
            status=IndexStatus.INDEXED,
            indexed_at=now,
            owner_user_id=context.user_id,
        ),
        [],
    )
    return note.id


def _seed(hdrs: dict[str, str], label: str) -> dict[str, str]:
    """Give one user a conversation, a private memory, a voice transcript, preferences and a task."""
    conversation_id = client.post("/api/v1/conversations", headers=hdrs, json={}).json()["id"]
    response = client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=hdrs, json={"content": f"{label} question"})
    assert response.status_code == 200
    memory_id = _private_memory(hdrs, f"{label} prefers tea")
    session_id = client.post("/api/v1/voice/sessions", headers=hdrs, json={"retention": "permanent"}).json()["id"]
    assert client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        headers=hdrs,
        json={"text": f"{label} spoken words", "client_sequence": 1},
    ).status_code == 201
    assert client.put(
        "/api/v1/notification-preferences", headers=hdrs, json={"email_enabled": True}
    ).status_code == 200
    # What ANUM learned (docs/learning.md): one edited draft (a signal and the derived
    # profile) and one learned preference, recorded while learning is on (the chat memory
    # switch is per tenant, so an earlier seed of the same user turned it off).
    assert client.put("/api/v1/me/preferences", headers=hdrs, json={"remember_conversations": True}).status_code == 200
    learned = client.post(
        "/api/v1/learning/feedback",
        headers=hdrs,
        json={"surface": "email", "original": f"Hello, {label} draft", "final": f"Hi, {label} draft as sent"},
    )
    assert learned.status_code == 200 and learned.json()["recorded"] is True, learned.text
    assert client.post("/api/v1/learning/preferences", headers=hdrs, json={"text": f"{label} keeps it short"}).status_code == 201
    assert client.put("/api/v1/me/preferences", headers=hdrs, json={"remember_conversations": False}).status_code == 200
    context, now = _context(hdrs), utc_now()
    push_device_store.save(
        StoredPushDevice(
            id=new_id("device"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            platform=PushPlatform.ANDROID,
            label=f"{label} phone",
            token_ciphertext="ciphertext-placeholder",
            token_sha256="1" * 64,
            token_hint="abcd",
            created_at=now,
            updated_at=now,
        )
    )
    task = client.post("/api/v1/tasks", headers=hdrs, json={"title": f"{label} task", "prompt": f"{label} summarize notes"})
    assert task.status_code == 201
    return {"conversation": conversation_id, "memory": memory_id, "voice": session_id, "task": task.json()["id"]}


def test_summary_counts_only_the_callers_data_and_shows_retention() -> None:
    _seed(ME, "mine")
    _seed(COLLEAGUE, "colleague")
    response = client.get("/api/v1/privacy", headers=ME)
    assert response.status_code == 200
    body = response.json()
    assert body["private"] == {
        "conversations": 1,
        "conversation_turns": 2,
        "private_memories": 1,
        "retrieval_index_entries": 1,
        "voice_sessions": 1,
        "voice_transcript_segments": 1,
        "notification_preferences": 1,
        "user_preferences": 1,
        "push_devices": 1,
        "learning_signals": 1,
        "learned_items": 2,
        "meetings": 0,
        "meeting_transcript_segments": 0,
        "meeting_recordings": 0,
        "meeting_connections": 0,
        "commands": 0,
        "quick_commands": 0,
        "favorites": 0,
        "private_collections": 0,
        "private_reports": 0,
        "email_mailboxes": 0,
        "email_messages": 0,
        "email_drafts": 0,
    }
    assert body["shared"]["tasks_created"] == 1
    assert body["retention"]["conversation_days"] == settings.conversation_retention_days
    assert body["retention"]["voice_transcript_days"] == 30
    assert body["delete_confirmation"] == "DELETE MY DATA"
    assert body["model_provider"]["deployment"] == settings.model_provider
    assert body["privacy_policy_url"] is None
    assert "identity provider" in body["account_note"]
    # Counts only: the summary never carries content.
    assert "mine question" not in response.text


def test_summary_reports_the_callers_effective_chat_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same state GET /api/v1/me/preferences returns, not just the deployment flag."""

    def memory(hdrs: dict[str, str] = ME) -> tuple[bool, str]:
        provider = client.get("/api/v1/privacy", headers=hdrs).json()["model_provider"]
        mine = client.get("/api/v1/me/preferences", headers=hdrs).json()["conversation_memory"]
        assert provider["conversation_memory"] == mine
        return provider["conversation_memory_enabled"], provider["conversation_memory"]

    assert memory() == (True, "on")
    assert memory(headers("user_viewer", roles="viewer")) == (True, "not_for_role")

    assert client.put("/api/v1/me/preferences", headers=ME, json={"remember_conversations": False}).status_code == 200
    assert memory() == (True, "off_by_user")
    # Only the caller's own switch: a colleague in the same workspace is unaffected.
    assert memory(COLLEAGUE) == (True, "on")
    assert client.put("/api/v1/me/preferences", headers=ME, json={"remember_conversations": True}).status_code == 200

    governance_store.save_memory_governance(
        _context(ME),
        MemoryGovernance(
            tenant_id=ME["x-tenant-id"], default_retention_days=30, allow_permanent_retention=True,
            require_provenance=True, allowed_source_types=["task_message"], updated_by="user_owner", updated_at=utc_now(),
        ),
    )
    assert memory() == (True, "off_for_organization")
    # Another tenant's policy does not apply.
    assert memory(OTHER_TENANT) == (True, "on")

    monkeypatch.setattr(settings, "conversation_memory_enabled", False)
    assert memory() == (False, "off_on_server")


def test_summary_treats_an_unreadable_memory_policy_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import conversation_memory

    def broken(_context: TenantContext) -> bool:
        raise RuntimeError("governance store unavailable")

    monkeypatch.setattr(conversation_memory, "governance_allows_conversation_memory", broken)
    response = client.get("/api/v1/privacy", headers=ME)
    assert response.status_code == 200
    assert response.json()["model_provider"]["conversation_memory"] == "off_for_organization"


def test_summary_shows_the_configured_policy_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "privacy_policy_url", "https://example.com/privacy")
    assert client.get("/api/v1/privacy", headers=ME).json()["privacy_policy_url"] == "https://example.com/privacy"


def test_export_contains_my_data_and_nobody_elses() -> None:
    mine = _seed(ME, "mine")
    _seed(COLLEAGUE, "colleague")
    _seed(OTHER_WORKSPACE, "elsewhere")
    _seed(OTHER_TENANT, "foreign")
    upload = client.post("/api/v1/files", headers={**ME, "x-file-name": "notes.txt", "content-type": "text/plain"}, content=b"my notes")
    assert upload.status_code == 201

    response = client.get("/api/v1/privacy/export", headers=ME)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"].startswith('attachment; filename="anum-my-data-')
    assert response.headers["cache-control"] == "no-store"
    export = json.loads(response.content)
    assert export["format"] == "anum.privacy-export/v1"
    assert export["identity"]["user_id"] == "user_me"
    # What ANUM learned from me, in full: my edited draft, the style it derived, my preference.
    assert [signal["final_text"] for signal in export["learning"]["signals"]] == ["Hi, mine draft as sent"]
    assert export["learning"]["profile"]["derived"]["samples"] == 1
    assert [item["text"] for item in export["learning"]["preferences"]] == ["mine keeps it short"]
    assert [c["id"] for c in export["conversations"]] == [mine["conversation"]]
    assert [t["role"] for t in export["conversations"][0]["turns"]] == ["user", "assistant"]
    assert export["conversations"][0]["turns"][0]["content"] == "mine question"
    assert [m["id"] for m in export["memories"]["private"]] == [mine["memory"]]
    assert export["voice_sessions"][0]["transcript"][0]["text"] == "mine spoken words"
    assert [t["id"] for t in export["tasks_created"]] == [mine["task"]]
    assert export["notification_preferences"]["email_enabled"] is True
    assert export["user_preferences"]["remember_conversations"] is False
    (exported_device,) = export["push_devices"]
    assert exported_device["label"] == "mine phone" and exported_device["token_hint"] == "abcd"
    assert not {"token", "token_ciphertext", "token_sha256"} & set(exported_device)
    (exported_file,) = export["files_uploaded"]
    assert exported_file["download_path"] == f"/api/v1/files/{upload.json()['id']}/content"
    assert "storage_key" not in exported_file
    text = response.content.decode()
    for foreign in ("colleague", "elsewhere", "foreign"):
        assert foreign not in text


def test_export_lists_approvals_i_decided_without_tool_arguments() -> None:
    owner = headers("user_me", roles="owner,member")
    task_id = client.post("/api/v1/tasks", headers=owner, json={"title": "Publish", "prompt": "Send and publish the final update"}).json()["id"]
    run = client.post(f"/api/v1/tasks/{task_id}/run", headers=owner).json()
    approval = run["approval"]
    assert client.post(
        f"/api/v1/approvals/{approval['id']}/approve", headers=owner, json={"payload_hash": approval["payload_hash"]}
    ).status_code == 200

    export = client.get("/api/v1/privacy/export", headers=owner).json()
    (decided,) = export["approvals_decided"]
    assert decided["id"] == approval["id"]
    assert decided["decided_by"] == "user_me"
    assert "arguments" not in decided
    assert "checkpoint" not in export["tasks_created"][0]["runs"][0]
    assert client.get("/api/v1/privacy", headers=owner).json()["shared"]["approvals_decided"] == 1


def test_export_is_audited_without_content() -> None:
    _seed(ME, "mine")
    assert client.get("/api/v1/privacy/export", headers=ME).status_code == 200
    (record,) = [r for r in store.audit_records if r.action == "privacy.export"]
    assert (record.actor, record.target, record.outcome) == ("user_me", "user_me", "success")
    assert record.metadata["sections"]["conversations"] == 1
    assert "mine" not in json.dumps(dict(record.metadata), default=str)


def test_an_export_over_the_cap_is_refused_and_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(ME, "mine")
    _private_memory(ME, "x" * 5000)
    monkeypatch.setattr(settings, "privacy_export_max_bytes", 2048)
    response = client.get("/api/v1/privacy/export", headers=ME)
    assert response.status_code == 413
    assert "ANUM_PRIVACY_EXPORT_MAX_BYTES" in response.text
    assert [r.outcome for r in store.audit_records if r.action == "privacy.export"] == ["refused_too_large"]


def test_delete_needs_the_typed_phrase() -> None:
    _seed(ME, "mine")
    for phrase in ("", "delete my data", "DELETE"):
        response = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": phrase})
        assert response.status_code == 422
        assert "Nothing was deleted" in response.text
    assert client.get("/api/v1/privacy", headers=ME).json()["private"]["conversations"] == 1


def test_delete_removes_only_my_private_data_and_keeps_shared_records() -> None:
    mine = _seed(ME, "mine")
    colleague = _seed(COLLEAGUE, "colleague")
    elsewhere = _seed(OTHER_WORKSPACE, "elsewhere")
    foreign = _seed(OTHER_TENANT, "foreign")

    response = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"})
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == {
        "conversations": 1,
        "conversation_turns": 2,
        "private_memories": 1,
        "retrieval_index_entries": 1,
        "voice_sessions": 1,
        "voice_transcript_segments": 1,
        "notification_preferences": 1,
        "user_preferences": 1,
        "push_devices": 1,
        "learning_signals": 1,
        "learned_items": 2,
        "meetings": 0,
        "meeting_transcript_segments": 0,
        "meeting_recordings": 0,
        "meeting_connections": 0,
        "commands": 0,
        "quick_commands": 0,
        "favorites": 0,
        "private_collections": 0,
        "private_reports": 0,
        "email_mailboxes": 0,
        "email_messages": 0,
        "email_drafts": 0,
    }
    assert body["kept"]["tasks_created"] == 1
    assert "identity provider" in body["account_note"]

    after = client.get("/api/v1/privacy", headers=ME).json()
    assert set(after["private"].values()) == {0}
    # Shared records stay: the task I created is still in the workspace.
    assert client.get(f"/api/v1/tasks/{mine['task']}", headers=ME).status_code == 200
    # The preference row is gone, so the defaults apply again.
    assert client.get("/api/v1/me/preferences", headers=ME).json()["remember_conversations"] is True
    assert client.get(f"/api/v1/voice/sessions/{mine['voice']}", headers=ME).status_code == 404
    assert client.get(f"/api/v1/conversations/{mine['conversation']}", headers=ME).status_code == 404

    # Nobody else lost anything.
    for hdrs, seeded in ((COLLEAGUE, colleague), (OTHER_WORKSPACE, elsewhere), (OTHER_TENANT, foreign)):
        counts = client.get("/api/v1/privacy", headers=hdrs).json()["private"]
        assert counts["conversations"] == counts["private_memories"] == counts["voice_sessions"] == 1
        assert counts["retrieval_index_entries"] == counts["notification_preferences"] == 1
        # user_preferences is one row per user and tenant (every workspace): the same
        # user in another workspace of this tenant shares the row that was deleted.
        assert counts["user_preferences"] == (0 if hdrs is OTHER_WORKSPACE else 1)
        assert client.get(f"/api/v1/conversations/{seeded['conversation']}", headers=hdrs).status_code == 200
        assert memory_note_repository.get(seeded["memory"], _context(hdrs)) is not None

    (record,) = [r for r in store.audit_records if r.action == "privacy.delete"]
    assert record.metadata["deleted"]["conversations"] == 1
    assert "mine" not in json.dumps(dict(record.metadata), default=str)


def test_owners_use_the_same_actions_for_their_own_data() -> None:
    _seed(OWNER, "owner")
    _seed(ME, "mine")
    assert client.post("/api/v1/privacy/delete", headers=OWNER, json={"confirm": "DELETE MY DATA"}).json()["deleted"]["conversations"] == 1
    # An owner's deletion is still their own: the member's data stays.
    assert client.get("/api/v1/privacy", headers=ME).json()["private"]["conversations"] == 1


def test_callers_without_a_workspace_role_are_refused() -> None:
    no_role = headers(roles="guest")
    for method, path, body in (("get", "/api/v1/privacy", None), ("get", "/api/v1/privacy/export", None), ("post", "/api/v1/privacy/delete", {"confirm": "DELETE MY DATA"})):
        response = getattr(client, method)(path, headers=no_role, **({"json": body} if body else {}))
        assert response.status_code == 403
    assert client.get("/api/v1/privacy", headers={"x-tenant-id": "tenant_p"}).status_code == 401


def test_an_expired_transcript_is_not_exported() -> None:
    session_id = client.post("/api/v1/voice/sessions", headers=ME, json={"retention": "30_days"}).json()["id"]
    client.post(f"/api/v1/voice/sessions/{session_id}/transcript", headers=ME, json={"text": "old words", "client_sequence": 1})
    voice_store.sessions[session_id].expires_at = utc_now() - timedelta(days=1)
    export = client.get("/api/v1/privacy/export", headers=ME).json()
    assert export["voice_sessions"][0]["transcript"] == []
    assert "old words" not in json.dumps(export)


def test_build_export_is_valid_json_with_every_section() -> None:
    body, sections = privacy.build_export(privacy.InMemoryPrivacyStore(_context(ME)), _context(ME), 1_000_000)
    parsed = json.loads(body)
    assert set(sections) == {
        "identity", "conversations", "memories", "voice_sessions", "tasks_created",
        "approvals_decided", "notification_preferences", "user_preferences", "push_devices", "learning", "commands", "library", "files_uploaded",
        "meetings_imported", "meeting_connections",
        "email", "whatsapp_messages_sent", "autonomy_actions_requested",
    }
    assert parsed["notification_preferences"] is None
    assert parsed["user_preferences"] is None
    assert parsed["push_devices"] == []
    assert parsed["library"] == {"favorites": [], "private_collections": [], "private_reports": []}
    assert parsed["not_included"]


def test_the_export_file_name_is_readable_by_the_web_client() -> None:
    response = client.get("/api/v1/privacy/export", headers={**ME, "origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert "content-disposition" in response.headers["access-control-expose-headers"].lower()


# -- email, WhatsApp and autonomy (docs/privacy.md#what-is-included) -----------------------


def _seed_modules(hdrs: dict[str, str], label: str) -> None:
    """Mailboxes (one private, one shared), drafts, synced metadata, a WhatsApp send, an autonomous action."""
    from anum_api.autonomy import AutonomousAction
    from anum_api.email_inbox import EmailAccount, EmailDraft, EmailMessage
    from anum_api.secret_box import secret_cipher
    from anum_api.whatsapp_store import Message

    context, now = _context(hdrs), utc_now()
    scope = {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id}
    user = context.user_id
    with email_backend.lock:
        for suffix, visibility, provider in (("private", "private", "gmail"), ("shared", "workspace", "outlook"), ("other", "private", "zoho")):
            account_id = f"mbox_{label}_{suffix}"
            email_backend.accounts[account_id] = EmailAccount(
                id=account_id, **scope, provider=provider, address=f"{label}.{suffix}@example.com",
                connected_by=user, visibility=visibility, access_token_enc="ciphertext-placeholder",
                refresh_token_enc=secret_cipher().encrypt("example-refresh") if suffix != "other" else None,
                sync_cursor="cursor-placeholder",
                created_at=now, updated_at=now,
            )
        email_backend.messages[f"msg_{label}"] = EmailMessage(
            id=f"msg_{label}", **scope, account_id=f"mbox_{label}_private", provider_message_id="p1", thread_id="t1",
            subject=f"{label} private subject", received_at=now, created_at=now, updated_at=now,
        )
        email_backend.messages[f"msg_{label}_shared"] = EmailMessage(
            id=f"msg_{label}_shared", **scope, account_id=f"mbox_{label}_shared", provider_message_id="p2", thread_id="t2",
            subject=f"{label} shared subject", received_at=now, created_at=now, updated_at=now,
        )
        for draft_id, account, status in (
            ("unsent", "shared", "draft"),
            ("sent", "shared", "sent"),
            ("waiting", "shared", "pending_approval"),
            ("private", "private", "sent"),
        ):
            email_backend.drafts[f"draft_{label}_{draft_id}"] = EmailDraft(
                id=f"draft_{label}_{draft_id}", **scope, account_id=f"mbox_{label}_{account}", body=f"{label} {draft_id} body",
                status=status, created_by=user, created_at=now, updated_at=now,
            )
    with whatsapp_store._lock:
        whatsapp_store._messages[f"wam_{label}"] = Message(
            id=f"wam_{label}", **scope, conversation_id="wcv_x", direction="outbound", kind="text",
            body=f"{label} whatsapp reply", status="sent", sent_by=user, timestamp=now, created_at=now, updated_at=now,
        )
        whatsapp_store._messages[f"wam_{label}_in"] = Message(
            id=f"wam_{label}_in", **scope, conversation_id="wcv_x", direction="inbound", kind="text",
            body="customer text", status="received", timestamp=now, created_at=now, updated_at=now,
        )
    with autonomy_memory_store._lock:
        autonomy_memory_store.actions[f"act_{label}"] = AutonomousAction(
            id=f"act_{label}", **scope, task_id="task_x", run_id="run_x", tool="email.send", category="email",
            action_kind="send", level="act_and_notify", pattern_key="email.send:x", summary=f"{label} sent a reply",
            requested_by=user, undo_token="undo-token-placeholder", undo_arguments={"draft_id": "x"}, created_at=now,
        )


class _FakeProvider:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    async def revoke(self, token: str) -> bool:
        return self.provider == "gmail"


def test_email_whatsapp_and_autonomy_are_counted_exported_and_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import email_inbox

    revoked: list[str] = []

    def fake_client(provider: str) -> _FakeProvider:
        revoked.append(str(provider))
        return _FakeProvider(str(provider))

    monkeypatch.setattr(email_inbox, "provider_client", fake_client)
    _seed_modules(ME, "mine")
    _seed_modules(COLLEAGUE, "colleague")
    _seed_modules(OTHER_TENANT, "foreign")

    summary = client.get("/api/v1/privacy", headers=ME).json()
    assert {key: summary["private"][key] for key in ("email_mailboxes", "email_messages", "email_drafts")} == {
        "email_mailboxes": 2, "email_messages": 1, "email_drafts": 2,
    }
    assert {key: summary["shared"][key] for key in (
        "email_mailboxes_connected", "email_drafts_sent", "whatsapp_messages_sent", "autonomy_actions_requested"
    )} == {"email_mailboxes_connected": 1, "email_drafts_sent": 2, "whatsapp_messages_sent": 1, "autonomy_actions_requested": 1}

    response = client.get("/api/v1/privacy/export", headers=ME)
    export = json.loads(response.content)
    assert len(export["email"]["mailboxes_connected"]) == 3
    assert not {"access_token_enc", "refresh_token_enc", "sync_cursor"} & set(export["email"]["mailboxes_connected"][0])
    assert sorted(d["id"] for d in export["email"]["drafts_written"]) == [
        "draft_mine_private", "draft_mine_sent", "draft_mine_unsent", "draft_mine_waiting",
    ]
    assert [m["subject"] for m in export["email"]["private_mailbox_messages"]] == ["mine private subject"]
    assert [m["body"] for m in export["whatsapp_messages_sent"]] == ["mine whatsapp reply"]
    (action,) = export["autonomy_actions_requested"]
    assert action["summary"] == "mine sent a reply" and "undo_token" not in action and "undo_arguments" not in action
    text = response.content.decode()
    assert "example-refresh" not in text and "cursor-placeholder" not in text
    assert "ciphertext-placeholder" not in text and "undo-token-placeholder" not in text and "customer text" not in text
    assert "colleague" not in text and "foreign" not in text

    deleted = client.post("/api/v1/privacy/delete", headers=ME, json={"confirm": "DELETE MY DATA"}).json()
    assert (deleted["deleted"]["email_mailboxes"], deleted["deleted"]["email_messages"], deleted["deleted"]["email_drafts"]) == (2, 1, 2)
    assert deleted["kept"]["email_drafts_sent"] == 2 and deleted["kept"]["whatsapp_messages_sent"] == 1
    # Gmail revoked; the Zoho mailbox had no refresh token, so the caller is told what to do.
    assert revoked == ["gmail"]
    (note,) = deleted["notes"]
    assert "mine.other@example.com" in note and "Zoho" in note
    assert sorted(email_backend.accounts) == [
        "mbox_colleague_other", "mbox_colleague_private", "mbox_colleague_shared",
        "mbox_foreign_other", "mbox_foreign_private", "mbox_foreign_shared", "mbox_mine_shared",
    ]
    assert "msg_mine" not in email_backend.messages and "msg_mine_shared" in email_backend.messages
    assert {d for d in email_backend.drafts if d.startswith("draft_mine")} == {"draft_mine_sent", "draft_mine_waiting"}
    assert "wam_mine" in whatsapp_store._messages and "act_mine" in autonomy_memory_store.actions
    after = client.get("/api/v1/privacy", headers=ME).json()
    assert set(after["private"].values()) == {0}
    assert client.get("/api/v1/privacy", headers=COLLEAGUE).json()["private"]["email_mailboxes"] == 2
    (record,) = [r for r in store.audit_records if r.action == "privacy.delete"]
    assert record.metadata["mailboxes_revoked_at_provider"] == 1
    assert "mine" not in json.dumps(dict(record.metadata), default=str)
