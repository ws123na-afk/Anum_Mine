"""Per-user "Remember things from my chats" (docs/memory.md#from-conversations), memory backend."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from anum_api import user_preferences
from anum_api.conversation_memory import extraction_enabled, recall_memories, remember_facts
from anum_api.conversations import conversation_store
from anum_api.dependencies import memory_note_repository
from anum_api.governance import MemoryGovernance, governance_store
from anum_api.retrieval import LocalEmbedder, memory_retrieval_store
from anum_api.schemas import TenantContext
from anum_api.settings import settings
from anum_api.user_preferences import UserPreferences, user_preference_store

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
TENANT, WORKSPACE = "tenant_prefs", "workspace_prefs"


def ctx(user: str = "user_prefs", role: str = "member", tenant: str = TENANT) -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=WORKSPACE, user_id=user, roles=[role])


def headers(user: str = "user_prefs", role: str = "member", tenant: str = TENANT) -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": WORKSPACE, "x-user-id": user, "x-user-roles": role}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    user_preference_store.clear()
    conversation_store.clear()
    memory_note_repository._notes.clear()
    memory_retrieval_store.clear()
    governance_store.clear()
    yield
    user_preference_store.clear()
    governance_store.clear()


@pytest.fixture
def client() -> TestClient:
    from anum_api.main import app

    return TestClient(app)


def _events(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    parsed = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def _remember(facts: list[str], context: TenantContext | None = None) -> None:
    asyncio.run(remember_facts(context or ctx(), "conv_prefs", "Prefs chat", facts, embedder=LocalEmbedder(), clock=lambda: NOW))


# --------------------------------------------------------------------------- API


def test_preferences_default_on_and_only_the_callers_own_change(client: TestClient) -> None:
    first = client.get("/api/v1/me/preferences", headers=headers())
    assert first.status_code == 200
    assert first.json() == {"remember_conversations": True, "updated_at": None, "conversation_memory": "on"}

    saved = client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": False})
    assert saved.status_code == 200
    assert saved.json()["remember_conversations"] is False
    assert saved.json()["conversation_memory"] == "off_by_user"
    assert saved.json()["updated_at"]
    assert client.get("/api/v1/me/preferences", headers=headers()).json()["remember_conversations"] is False

    # Another user of the same workspace, and the same user id in another tenant, keep theirs.
    assert client.get("/api/v1/me/preferences", headers=headers(user="user_other")).json()["remember_conversations"] is True
    assert client.get("/api/v1/me/preferences", headers=headers(tenant="tenant_elsewhere")).json()["remember_conversations"] is True

    again = client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": True})
    assert again.json()["conversation_memory"] == "on"


def test_nobody_can_name_another_user_or_send_a_bad_value(client: TestClient) -> None:
    # No user id is accepted: the body is closed and the store is keyed by the caller.
    other = client.put(
        "/api/v1/me/preferences", headers=headers(), json={"remember_conversations": False, "user_id": "user_other"}
    )
    assert other.status_code == 422
    assert client.put("/api/v1/me/preferences", headers=headers(), json={}).status_code == 422
    assert client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": "maybe"}).status_code == 422
    assert client.get("/api/v1/me/preferences", headers=headers(user="user_other")).json()["remember_conversations"] is True
    assert client.get("/api/v1/me/preferences").status_code in {400, 401}


def test_a_viewer_can_keep_their_own_switch(client: TestClient) -> None:
    saved = client.put("/api/v1/me/preferences", headers=headers(role="viewer"), json={"remember_conversations": False})
    assert saved.status_code == 200
    assert saved.json()["conversation_memory"] == "off_by_user"
    on = client.put("/api/v1/me/preferences", headers=headers(role="viewer"), json={"remember_conversations": True})
    assert on.json()["conversation_memory"] == "not_for_role"


def test_the_state_names_what_keeps_memory_off(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    governance_store.save_memory_governance(
        ctx(),
        MemoryGovernance(
            tenant_id=TENANT, default_retention_days=30, allow_permanent_retention=True, require_provenance=True,
            allowed_source_types=["task_message"], updated_by="owner", updated_at=NOW,
        ),
    )
    assert client.get("/api/v1/me/preferences", headers=headers()).json()["conversation_memory"] == "off_for_organization"
    monkeypatch.setattr(settings, "conversation_memory_enabled", False)
    assert client.get("/api/v1/me/preferences", headers=headers()).json()["conversation_memory"] == "off_on_server"


# --------------------------------------------------------------------------- extraction and recall


def test_turning_it_off_stops_extraction_and_recall_for_that_user_only() -> None:
    _remember(["The user prefers tea over coffee."])
    _remember(["The user prefers tea with lemon."], context=ctx(user="user_other"))
    assert extraction_enabled(ctx())
    assert asyncio.run(recall_memories(ctx(), "Which tea?", embedder=LocalEmbedder())).blocks

    user_preference_store.save(ctx(), UserPreferences(remember_conversations=False, updated_at=NOW))
    assert not extraction_enabled(ctx())
    assert asyncio.run(recall_memories(ctx(), "Which tea?", embedder=LocalEmbedder())).blocks == []
    # The other user is unaffected.
    assert extraction_enabled(ctx(user="user_other"))
    assert asyncio.run(recall_memories(ctx(user="user_other"), "Which tea?", embedder=LocalEmbedder())).blocks


def test_an_unreadable_preference_counts_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(context: TenantContext) -> bool:
        raise RuntimeError("database down")

    monkeypatch.setattr(user_preferences, "remembers_conversations", broken)
    _remember(["The user prefers tea over coffee."])
    assert not extraction_enabled(ctx())
    assert asyncio.run(recall_memories(ctx(), "Which tea?", embedder=LocalEmbedder())).blocks == []


def test_a_chat_with_memory_off_is_answered_but_nothing_is_remembered_or_recalled(client: TestClient) -> None:
    conversation_id = client.post("/api/v1/conversations", headers=headers(), json={}).json()["id"]
    _events(client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=headers(), json={"content": "My name is Sam."}))
    remembered = client.get("/api/v1/memories", params={"source_type": "conversation"}, headers=headers()).json()
    assert [m["content"] for m in remembered] == ["The user's name is Sam."]

    client.put("/api/v1/me/preferences", headers=headers(), json={"remember_conversations": False})
    events = _events(client.post(
        f"/api/v1/conversations/{conversation_id}/ask", headers=headers(), json={"content": "I prefer tea. What is my name, Sam?"}
    ))
    assert events[-1][0] == "done"
    assert events[0][1]["memories_used"] == []
    after = client.get("/api/v1/memories", params={"source_type": "conversation"}, headers=headers()).json()
    assert [m["id"] for m in after] == [m["id"] for m in remembered]


# --------------------------------------------------------------------------- delete my chat memories


def test_deleting_all_chat_memories_removes_only_the_callers_own(client: TestClient) -> None:
    _remember(["The user prefers tea over coffee.", "The user lives in Porto."])
    _remember(["The user prefers tea with lemon."], context=ctx(user="user_other"))

    response = client.delete("/api/v1/memories", params={"source_type": "conversation"}, headers=headers())
    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    assert client.get("/api/v1/memories", params={"source_type": "conversation"}, headers=headers()).json() == []
    assert [s for s in memory_retrieval_store.list_sources(ctx()) if s.owner_user_id == "user_prefs"] == []
    others = client.get("/api/v1/memories", params={"source_type": "conversation"}, headers=headers(user="user_other")).json()
    assert [m["content"] for m in others] == ["The user prefers tea with lemon."]
    # Nothing left: still a success, with nothing deleted.
    assert client.delete("/api/v1/memories", params={"source_type": "conversation"}, headers=headers()).json() == {"deleted": 0}


def test_deleting_together_needs_the_conversation_source_and_delete_permission(client: TestClient) -> None:
    assert client.delete("/api/v1/memories", params={"source_type": "user"}, headers=headers()).status_code == 422
    assert client.delete("/api/v1/memories", headers=headers()).status_code == 422
    assert client.delete("/api/v1/memories", params={"source_type": "conversation"}, headers=headers(role="viewer")).status_code == 403
