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
from anum_api.main import app, store
from anum_api.memory import MemoryNote, MemoryProvenance, RetentionKind, RetentionPolicy
from anum_api.onboarding import _notifications
from anum_api.retrieval import IndexStatus, RetrievalSource, SourceType, memory_retrieval_store
from anum_api.schemas import TenantContext, new_id, utc_now
from anum_api.settings import settings
from anum_api.voice import voice_store

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
    file_store.clear()


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


def test_summary_shows_the_configured_policy_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "privacy_policy_url", "https://example.com/privacy")
    assert client.get("/api/v1/privacy", headers=ME).json()["privacy_policy_url"] == "https://example.com/privacy"


def test_export_contains_my_data_and_nobody_elses() -> None:
    mine = _seed(ME, "mine")
    _seed(COLLEAGUE, "colleague")
    _seed(OTHER_WORKSPACE, "elsewhere")
    _seed(OTHER_TENANT, "foreign")
    upload = client.post("/api/v1/files", headers={**ME, "x-file-name": "notes.bin", "content-type": "application/octet-stream"}, content=b"\x00\x01")
    assert upload.status_code == 201

    response = client.get("/api/v1/privacy/export", headers=ME)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"].startswith('attachment; filename="anum-my-data-')
    assert response.headers["cache-control"] == "no-store"
    export = json.loads(response.content)
    assert export["format"] == "anum.privacy-export/v1"
    assert export["identity"]["user_id"] == "user_me"
    assert [c["id"] for c in export["conversations"]] == [mine["conversation"]]
    assert [t["role"] for t in export["conversations"][0]["turns"]] == ["user", "assistant"]
    assert export["conversations"][0]["turns"][0]["content"] == "mine question"
    assert [m["id"] for m in export["memories"]["private"]] == [mine["memory"]]
    assert export["voice_sessions"][0]["transcript"][0]["text"] == "mine spoken words"
    assert [t["id"] for t in export["tasks_created"]] == [mine["task"]]
    assert export["notification_preferences"]["email_enabled"] is True
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
    }
    assert body["kept"]["tasks_created"] == 1
    assert "identity provider" in body["account_note"]

    after = client.get("/api/v1/privacy", headers=ME).json()
    assert set(after["private"].values()) == {0}
    # Shared records stay: the task I created is still in the workspace.
    assert client.get(f"/api/v1/tasks/{mine['task']}", headers=ME).status_code == 200
    assert client.get(f"/api/v1/voice/sessions/{mine['voice']}", headers=ME).status_code == 404
    assert client.get(f"/api/v1/conversations/{mine['conversation']}", headers=ME).status_code == 404

    # Nobody else lost anything.
    for hdrs, seeded in ((COLLEAGUE, colleague), (OTHER_WORKSPACE, elsewhere), (OTHER_TENANT, foreign)):
        counts = client.get("/api/v1/privacy", headers=hdrs).json()["private"]
        assert counts["conversations"] == counts["private_memories"] == counts["voice_sessions"] == 1
        assert counts["retrieval_index_entries"] == counts["notification_preferences"] == 1
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
        "approvals_decided", "notification_preferences", "files_uploaded",
    }
    assert parsed["notification_preferences"] is None
    assert parsed["not_included"]


def test_the_export_file_name_is_readable_by_the_web_client() -> None:
    response = client.get("/api/v1/privacy/export", headers={**ME, "origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert "content-disposition" in response.headers["access-control-expose-headers"].lower()
