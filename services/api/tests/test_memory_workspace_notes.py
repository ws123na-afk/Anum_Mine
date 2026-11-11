"""Workspace memories, editing, paging and governance over the API (docs/memory.md#workspace-memories)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from anum_api.dependencies import memory_note_repository
from anum_api.governance import governance_store
from anum_api.main import app, store
from anum_api.memory import MemoryCreate, MemoryGovernanceRules, RetentionKind, RetentionPolicy, apply_memory_governance
from anum_api.retrieval import memory_retrieval_store
from anum_api.schemas import utc_now

client = TestClient(app)
TENANT = "tenant_memnotes"
WORKSPACE = "workspace_memnotes"
OWNER = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "owner_a", "x-user-roles": "owner"}
MEMBER = {**OWNER, "x-user-id": "member_a", "x-user-roles": "member"}
OTHER_MEMBER = {**OWNER, "x-user-id": "member_b", "x-user-roles": "member"}
VIEWER = {**OWNER, "x-user-id": "viewer_a", "x-user-roles": "viewer"}
ORG_ADMIN = {**OWNER, "x-user-roles": "owner,org_admin"}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    memory_note_repository._notes.clear()
    memory_retrieval_store.clear()
    governance_store.clear()
    yield
    memory_note_repository._notes.clear()
    memory_retrieval_store.clear()
    governance_store.clear()


def _note(headers: dict[str, str], content: str, **extra: object) -> dict:
    response = client.post("/api/v1/memories", headers=headers, json={"content": content, "source_type": "user", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_member_adds_a_workspace_memory_without_a_task() -> None:
    note = _note(MEMBER, "Office closes at 16:00 on Thursdays")
    assert note["task_id"] is None and note["owner_user_id"] is None
    # "task" retention means nothing without a task: the note is kept until someone deletes it.
    assert note["retention"] == {"kind": "indefinite", "expires_at": None}
    assert note["provenance"]["created_by_user_id"] == "member_a"
    # Shared with the workspace: another member and the owner list it; a viewer may read it.
    assert [n["id"] for n in client.get("/api/v1/memories", headers=OTHER_MEMBER).json()] == [note["id"]]
    assert [n["id"] for n in client.get("/api/v1/memories", headers=VIEWER).json()] == [note["id"]]
    # A viewer may not create one, and a task that does not exist is still refused.
    assert client.post("/api/v1/memories", headers=VIEWER, json={"content": "x", "source_type": "user"}).status_code == 403
    assert client.post("/api/v1/memories", headers=MEMBER, json={"task_id": "task_missing", "content": "x", "source_type": "user"}).status_code == 404
    # An explicit expiry is kept.
    later = (utc_now() + timedelta(days=3)).isoformat()
    dated = _note(MEMBER, "Visitor badge code 4411", retention={"kind": "expires_at", "expires_at": later})
    assert dated["retention"]["kind"] == "expires_at"


def test_memory_governance_binds_workspace_memories() -> None:
    saved = client.put(
        "/api/v1/organization/memory-governance", headers=ORG_ADMIN,
        json={"default_retention_days": 30, "allow_permanent_retention": False, "require_provenance": True, "allowed_source_types": ["user", "meeting"]},
    )
    assert saved.status_code == 200, saved.text
    refused = client.post("/api/v1/memories", headers=MEMBER, json={"content": "x", "source_type": "scraper"})
    assert refused.status_code == 422 and "memory governance does not allow" in refused.json()["error"]["message"]
    note = _note(MEMBER, "Kept for a month")
    assert note["retention"]["kind"] == "expires_at"
    expires = note["retention"]["expires_at"]
    assert expires is not None and expires[:10] == (utc_now() + timedelta(days=30)).date().isoformat()
    # Task notes keep their own retention (the task's): governance of shared notes does not touch them.
    task = client.post("/api/v1/tasks", headers=MEMBER, json={"title": "T", "prompt": "p"}).json()
    on_task = _note(MEMBER, "On the task", task_id=task["id"])
    assert on_task["retention"]["kind"] == "task"


def test_the_pure_governance_rule() -> None:
    now = utc_now()
    request = MemoryCreate(content="x", source_type="user")
    assert apply_memory_governance(request, None, now).retention.kind == RetentionKind.INDEFINITE
    rules = MemoryGovernanceRules(default_retention_days=7, allow_permanent_retention=False, allowed_source_types=["User"])
    kept = apply_memory_governance(request, rules, now)
    assert kept.retention.kind == RetentionKind.EXPIRES_AT and kept.retention.expires_at == now + timedelta(days=7)
    with pytest.raises(ValueError, match="does not allow"):
        apply_memory_governance(MemoryCreate(content="x", source_type="other"), rules, now)
    chosen = MemoryCreate(content="x", source_type="user", retention=RetentionPolicy(kind=RetentionKind.EXPIRES_AT, expires_at=now + timedelta(days=1)))
    assert apply_memory_governance(chosen, rules, now).retention.expires_at == now + timedelta(days=1)
    on_task = MemoryCreate(task_id="t", content="x", source_type="other")
    assert apply_memory_governance(on_task, rules, now) is on_task


def test_the_author_or_an_owner_edits_a_memory_and_the_change_is_audited_and_reindexed() -> None:
    note = _note(MEMBER, "Printer code is 1234")
    before = len(store.audit_records)
    edited = client.patch(f"/api/v1/memories/{note['id']}", headers=MEMBER, json={"content": "Printer code is 5678"})
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["content"] == "Printer code is 5678" and body["updated_at"] is not None
    assert body["provenance"]["created_by_user_id"] == "member_a"
    assert body["provenance"]["metadata"]["last_edited_by"] == "member_a"
    # Another member may not edit it; a workspace owner may; a viewer may not (no memory:create).
    assert client.patch(f"/api/v1/memories/{note['id']}", headers=OTHER_MEMBER, json={"content": "no"}).status_code == 403
    assert client.patch(f"/api/v1/memories/{note['id']}", headers=VIEWER, json={"content": "no"}).status_code == 403
    by_owner = client.patch(f"/api/v1/memories/{note['id']}", headers=OWNER, json={"content": "Printer code is 9999"})
    assert by_owner.status_code == 200 and by_owner.json()["provenance"]["metadata"]["last_edited_by"] == "owner_a"
    assert client.get("/api/v1/memories", headers=MEMBER).json()[0]["content"] == "Printer code is 9999"
    # Unknown, another workspace's, and empty text.
    assert client.patch("/api/v1/memories/memory_missing", headers=OWNER, json={"content": "x"}).status_code == 404
    elsewhere = {**OWNER, "x-workspace-id": "workspace_memnotes_2"}
    assert client.patch(f"/api/v1/memories/{note['id']}", headers=elsewhere, json={"content": "x"}).status_code == 404
    assert client.patch(f"/api/v1/memories/{note['id']}", headers=MEMBER, json={"content": ""}).status_code == 422
    audits = [r for r in store.audit_records[before:] if r.action == "memory.update"]
    assert [a.actor for a in audits] == ["member_a", "owner_a"]
    assert audits[0].target == note["id"] and audits[0].metadata["author"] == "member_a" and "content" not in audits[0].metadata
    # The index follows the text.
    source = memory_retrieval_store.get_source(
        __import__("anum_api.schemas", fromlist=["TenantContext"]).TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="owner_a", roles=["owner"]),
        __import__("anum_api.retrieval", fromlist=["SourceType"]).SourceType.MEMORY,
        note["id"],
    )
    assert source is not None and source.chunk_count >= 1


def test_memories_page_with_offset_and_say_how_many_there_are() -> None:
    ids = [_note(MEMBER, f"note {index}")["id"] for index in range(5)]
    first = client.get("/api/v1/memories", headers=MEMBER, params={"limit": 2})
    assert first.status_code == 200 and first.headers["X-Total-Count"] == "5"
    assert [n["id"] for n in first.json()] == ids[3:]
    second = client.get("/api/v1/memories", headers=MEMBER, params={"limit": 2, "offset": 2})
    assert [n["id"] for n in second.json()] == ids[1:3] and second.headers["X-Total-Count"] == "5"
    last = client.get("/api/v1/memories", headers=MEMBER, params={"limit": 2, "offset": 4})
    assert [n["id"] for n in last.json()] == ids[:1]
    assert client.get("/api/v1/memories", headers=MEMBER, params={"limit": 2, "offset": 9}).json() == []
    assert client.get("/api/v1/memories", headers=MEMBER, params={"offset": -1}).status_code == 422
    found = client.get("/api/v1/memories", headers=MEMBER, params={"query": "note 4"})
    assert [n["id"] for n in found.json()] == [ids[4]] and found.headers["X-Total-Count"] == "1"
