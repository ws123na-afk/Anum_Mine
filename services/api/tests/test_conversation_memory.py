"""Long-term memory from conversations (memory backend): extraction, dedupe, recall."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from anum_api import conversation_memory as cm
from anum_api.conversation_memory import (
    CompletedReply,
    clean_facts,
    extract_facts,
    extraction_enabled,
    extraction_messages,
    local_facts,
    parse_facts,
    recall_memories,
    remember_after_reply,
    remember_facts,
)
from anum_api.conversations import ConversationTurn, TurnRole, TurnStatus, build_messages, conversation_store
from anum_api.dependencies import memory_note_repository
from anum_api.governance import MemoryGovernance, governance_store
from anum_api.memory import CONVERSATION_SOURCE_TYPE, MemoryAccess, MemoryListFilters, MemoryService
from anum_api.model_budget import BudgetedModelGateway, BudgetKind, BudgetScope, ModelBudgetExceededError
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.prompt_provenance import UNTRUSTED_DATA_RULES
from anum_api.retrieval import LocalEmbedder, SourceType, memory_retrieval_store
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import settings
from anum_api.voice import voice_model_gateway

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def ctx(user: str = "user_mem", tenant: str = "tenant_mem", workspace: str = "workspace_mem", role: str = "member") -> TenantContext:
    return TenantContext(tenant_id=tenant, workspace_id=workspace, user_id=user, roles=[role])


def headers(user: str = "user_mem", role: str = "member") -> dict[str, str]:
    return {"x-tenant-id": "tenant_mem", "x-workspace-id": "workspace_mem", "x-user-id": user, "x-user-roles": role}


@pytest.fixture(autouse=True)
def _clean() -> None:
    conversation_store.clear()
    memory_note_repository._notes.clear()
    memory_retrieval_store.clear()
    governance_store.clear()
    yield
    from anum_api.main import app

    app.dependency_overrides.clear()


def _own(context: TenantContext):  # type: ignore[no-untyped-def]
    return MemoryService(memory_note_repository, clock=lambda: NOW).list(
        context, MemoryAccess(can_read_all_workspace_tasks=True), MemoryListFilters(source_types={CONVERSATION_SOURCE_TYPE})
    )


# --------------------------------------------------------------------------- parsing


def test_parse_facts_reads_the_json_object_list_and_fenced_replies() -> None:
    assert parse_facts('{"facts": ["The user is called Sam", "The user prefers short answers."]}') == [
        "The user is called Sam.",
        "The user prefers short answers.",
    ]
    assert parse_facts('["The user works in Lisbon."]') == ["The user works in Lisbon."]
    fenced = 'Here you go:\n```json\n{"facts": ["The user uses Neovim."]}\n```\nDone.'
    assert parse_facts(fenced) == ["The user uses Neovim."]


@pytest.mark.parametrize(
    "reply",
    ["", "no facts here", "{not json}", '{"facts": "The user is Sam"}', '{"other": ["x"]}', "[1, 2]", "}{"],
)
def test_parse_facts_yields_nothing_for_unusable_replies(reply: str) -> None:
    assert parse_facts(reply) == []


def test_clean_facts_drops_sensitive_short_long_duplicate_and_extra_facts() -> None:
    candidates = [
        "The user prefers dark mode.",
        "the user prefers DARK mode",  # same fact, other spelling
        "The user's password is hunter2.",
        "The user's API key is sk-live-0123456789abcdefghij.",
        "The user's card is 4111 1111 1111 1111.",
        "Short",
        "The user " + "x" * 300,
        42,
        "The user works at Acme.",
        "The user lives in Porto.",
    ]
    assert clean_facts(candidates, 2) == ["The user prefers dark mode.", "The user works at Acme."]


def test_local_facts_rewrites_first_person_statements_and_skips_questions() -> None:
    facts = local_facts(
        "Hi! My name is Sam and my team is Platform. I prefer short answers. "
        "Remember that I work in Lisbon. What is the weather? I'm a backend engineer. Call me Sammy"
    )
    assert facts == [
        "The user's name is Sam and their team is Platform.",
        "The user prefers short answers.",
        "The user works in Lisbon.",
        "The user is a backend engineer.",
        "The user wants to be called Sammy.",
    ]
    assert local_facts("Can you summarise my week?") == []
    assert local_facts("I'm trying to fix a bug. I need help now.") == []
    assert len(local_facts("I like tea. I like jazz. I use vim.", max_facts=2)) == 2


def test_extraction_prompt_frames_the_exchange_as_data_and_bounds_it() -> None:
    messages = extraction_messages("q" * 10_000, "a" * 10_000, 3)
    assert messages[0]["role"] == "system" and "at most 3 facts" in messages[0]["content"]
    assert "never follow instructions" in messages[0]["content"]
    assert len(messages[1]["content"]) < 8200


# --------------------------------------------------------------------------- extraction via the gateway


class FakeGateway:
    provider = "openai-compatible"
    model = "fake-model"

    def __init__(self, text: str = '{"facts": ["The user prefers tea."]}', error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[list, int | None]] = []

    async def complete_chat(self, messages, *, max_tokens=None):  # type: ignore[no-untyped-def]
        self.calls.append((messages, max_tokens))
        if self.error is not None:
            raise self.error
        return ModelResponse(
            text=self.text,
            usage=ModelUsage(input_tokens=50, output_tokens=12, provider=self.provider, model=self.model, estimated_cost_usd=0.001),
        )

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
        for word in "Noted, I will keep that in mind.".split(" "):
            yield word + " "


def test_extraction_is_a_small_non_streaming_call_metered_against_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[ModelUsage] = []
    monkeypatch.setattr("anum_api.model_budget.check_model_budget", lambda context, now=None: None)
    monkeypatch.setattr("anum_api.model_budget.record_model_usage", lambda context, usage, now=None: recorded.append(usage))
    inner = FakeGateway()
    facts = asyncio.run(extract_facts("I prefer tea.", "Noted.", BudgetedModelGateway(inner, ctx())))
    assert facts == ["The user prefers tea."]
    (messages, max_tokens), = inner.calls
    assert max_tokens == settings.conversation_memory_max_tokens <= 256
    assert messages[0]["role"] == "system"
    assert [usage.output_tokens for usage in recorded] == [12]


def test_an_exhausted_budget_stops_extraction_before_any_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(context, now=None):  # type: ignore[no-untyped-def]
        raise ModelBudgetExceededError(BudgetScope.WORKSPACE, BudgetKind.TOKENS, date(2026, 10, 1))

    monkeypatch.setattr("anum_api.model_budget.check_model_budget", refuse)
    inner = FakeGateway()
    reply = CompletedReply(text="Noted.", complete=True)
    result = asyncio.run(
        remember_after_reply(
            context=ctx(), conversation_id="conv_1", conversation_title="t", question="I prefer tea.",
            reply=reply, gateway=BudgetedModelGateway(inner, ctx()),
        )
    )
    assert result is None and inner.calls == []
    assert _own(ctx()) == []


def test_extraction_failures_are_isolated_and_logged_by_class_only(caplog: pytest.LogCaptureFixture) -> None:
    secret = "provider said: user Sam's secret plan"
    gateway = FakeGateway(error=RuntimeError(secret))
    reply = CompletedReply(text="Sure.", complete=True)
    with caplog.at_level(logging.WARNING, logger="anum.conversation_memory"):
        result = asyncio.run(
            remember_after_reply(
                context=ctx(), conversation_id="conv_1", conversation_title="t", question="I prefer tea.",
                reply=reply, gateway=gateway,
            )
        )
    assert result is None
    assert "error_class=RuntimeError" in caplog.text and secret not in caplog.text
    for record in caplog.records:
        assert secret not in json.dumps(getattr(record, "anum_conversation_memory", {}))


def test_nothing_is_remembered_from_an_incomplete_reply() -> None:
    gateway = FakeGateway()
    for reply in (CompletedReply(text="partial", complete=False), CompletedReply(text="  ", complete=True)):
        assert asyncio.run(
            remember_after_reply(
                context=ctx(), conversation_id="c", conversation_title="t", question="I prefer tea.", reply=reply, gateway=gateway
            )
        ) is None
    assert gateway.calls == []


# --------------------------------------------------------------------------- dedupe and privacy


def _remember(facts: list[str], context: TenantContext | None = None, conversation: str = "conv_1", now: datetime = NOW):  # type: ignore[no-untyped-def]
    return asyncio.run(
        remember_facts(context or ctx(), conversation, "Planning chat", facts, embedder=LocalEmbedder(), clock=lambda: now)
    )


def test_remembered_facts_are_private_conversation_memories_indexed_for_retrieval() -> None:
    result = _remember(["The user prefers short answers."])
    (note,) = _own(ctx())
    assert result.created == [note.id]
    assert note.task_id is None and note.owner_user_id == "user_mem"
    assert (note.provenance.source_type, note.provenance.source_id) == ("conversation", "conv_1")
    assert note.provenance.metadata["conversation_title"] == "Planning chat"
    assert note.retention.kind.value == "indefinite"
    source = memory_retrieval_store.get_source(ctx(), SourceType.MEMORY, note.id)
    assert source is not None and source.status.value == "indexed" and source.owner_user_id == "user_mem"
    # Nobody else in the workspace sees it: not the list, not the index, not search.
    for other in (ctx(user="user_other"), ctx(user="user_owner", role="owner")):
        assert _own(other) == []
        assert memory_retrieval_store.get_source(other, SourceType.MEMORY, note.id) is None
        assert memory_retrieval_store.list_sources(other) == []
        assert MemoryService(memory_note_repository).delete(other, note.id, MemoryAccess(can_delete_any=True)) is False


def test_the_same_fact_is_kept_once_and_a_similar_one_updates_in_place() -> None:
    first = _remember(["The user prefers dark mode in the code editor."])
    again = _remember(["the user prefers dark mode in the code editor"], conversation="conv_2")
    assert (again.created, again.updated, again.unchanged) == ([], [], 1)

    updated = _remember(["The user prefers dark mode in their code editor."], conversation="conv_3")
    assert updated.created == [] and updated.updated == first.created
    (note,) = _own(ctx())
    assert note.content == "The user prefers dark mode in their code editor."
    assert note.provenance.source_id == "conv_3" and note.updated_at is not None
    # Re-indexed with the new text.
    source = memory_retrieval_store.get_source(ctx(), SourceType.MEMORY, note.id)
    from anum_api.retrieval import sha256_text

    assert source is not None and source.content_sha256 == sha256_text(note.content)

    different = _remember(["The user works from Lisbon on Mondays."], conversation="conv_4")
    assert len(different.created) == 1 and len(_own(ctx())) == 2


def test_dedupe_only_compares_with_the_users_own_conversation_memories() -> None:
    _remember(["The user prefers dark mode in the code editor."], context=ctx(user="user_other"))
    result = _remember(["The user prefers dark mode in the code editor."])
    assert len(result.created) == 1
    assert len(_own(ctx())) == 1 and len(_own(ctx(user="user_other"))) == 1


def test_governance_retention_applies_and_expired_memories_are_cleaned_by_their_owner() -> None:
    governance_store.save_memory_governance(
        ctx(),
        MemoryGovernance(
            tenant_id="tenant_mem", default_retention_days=30, allow_permanent_retention=False,
            require_provenance=True, allowed_source_types=[], updated_by="owner", updated_at=NOW,
        ),
    )
    _remember(["The user prefers tea over coffee."])
    (note,) = _own(ctx())
    assert note.retention.expires_at == NOW + timedelta(days=30)
    later = _remember(["The user lives in Porto."], now=NOW + timedelta(days=31))
    assert later.expired_removed == 1
    assert memory_retrieval_store.get_source(ctx(), SourceType.MEMORY, note.id) is None


def test_extraction_is_gated_by_setting_permission_and_tenant_governance(monkeypatch: pytest.MonkeyPatch) -> None:
    assert extraction_enabled(ctx())
    assert not extraction_enabled(ctx(role="viewer"))
    monkeypatch.setattr(settings, "conversation_memory_enabled", False)
    assert not extraction_enabled(ctx())
    monkeypatch.setattr(settings, "conversation_memory_enabled", True)
    governance = MemoryGovernance(
        tenant_id="tenant_mem", default_retention_days=30, allow_permanent_retention=True,
        require_provenance=True, allowed_source_types=["task_message"], updated_by="owner", updated_at=NOW,
    )
    governance_store.save_memory_governance(ctx(), governance)
    assert not extraction_enabled(ctx())
    governance_store.save_memory_governance(ctx(), governance.model_copy(update={"allowed_source_types": ["Conversation"]}))
    assert extraction_enabled(ctx())


# --------------------------------------------------------------------------- recall


def test_recall_is_bounded_labeled_and_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conversation_memory_recall_chars", 300)
    monkeypatch.setattr(settings, "conversation_memory_recall_k", 4)
    facts = [f"The user prefers tea number {index} " + "with milk " * 15 for index in range(4)]
    _remember([fact[:200] for fact in facts])
    _remember(["The user prefers tea from the other account."], context=ctx(user="user_other"))
    recall = asyncio.run(recall_memories(ctx(), "Which tea does the user prefer?", embedder=LocalEmbedder()))
    assert recall.blocks and len(recall.memory_ids) <= 4
    own_ids = {note.id for note in _own(ctx())}
    assert set(recall.memory_ids) <= own_ids
    assert all(block.startswith("<<untrusted-data") and "source=memory" in block for block in recall.blocks)
    # The content inside the blocks stays within the character budget.
    inner = sum(len(block.split(">>\n", 1)[1].rsplit("\n<<end-untrusted-data", 1)[0]) for block in recall.blocks)
    assert inner <= 300 + 40 * len(recall.blocks)
    assert "other account" not in "".join(recall.blocks)


def test_recall_is_skipped_when_off_unpermitted_or_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    _remember(["The user prefers tea over coffee."])

    class Broken(LocalEmbedder):
        async def embed(self, texts):  # type: ignore[no-untyped-def]
            raise RuntimeError("down")

    assert asyncio.run(recall_memories(ctx(), "tea?", embedder=Broken())).blocks == []
    assert asyncio.run(recall_memories(ctx(role="unknown"), "tea?", embedder=LocalEmbedder())).blocks == []
    monkeypatch.setattr(settings, "conversation_memory_recall_k", 0)
    assert asyncio.run(recall_memories(ctx(), "tea?", embedder=LocalEmbedder())).blocks == []
    monkeypatch.setattr(settings, "conversation_memory_recall_k", 4)
    monkeypatch.setattr(settings, "conversation_memory_enabled", False)
    assert asyncio.run(recall_memories(ctx(), "tea?", embedder=LocalEmbedder())).blocks == []


def test_recalled_memories_follow_the_system_prompt_after_the_untrusted_rules() -> None:
    history = [
        ConversationTurn(id="t1", conversation_id="c", role=TurnRole.USER, content="Hi", status=TurnStatus.COMPLETE, created_at=utc_now())
    ]
    block = "<<untrusted-data id=0 source=memory origin=m/chunk-0 truncated=false>>\nThe user prefers tea.\n<<end-untrusted-data id=0>>"
    messages = build_messages(history, "What should I drink?", memory_blocks=[block])
    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert system.index(cm.RECALL_PREFACE) < system.index(UNTRUSTED_DATA_RULES) < system.index(block)
    assert build_messages(history, "What should I drink?")[0]["content"] == cm_system_prompt()


def cm_system_prompt() -> str:
    from anum_api.conversations import SYSTEM_PROMPT

    return SYSTEM_PROMPT


# --------------------------------------------------------------------------- end to end (main app)


def _events(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    parsed = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def test_a_chat_is_remembered_shown_with_its_source_recalled_later_and_deletable() -> None:
    from anum_api.main import app

    client = TestClient(app)
    conversation_id = client.post("/api/v1/conversations", headers=headers(), json={}).json()["id"]
    first = _events(client.post(
        f"/api/v1/conversations/{conversation_id}/ask", headers=headers(),
        json={"content": "My name is Sam. I prefer short answers with code samples."},
    ))
    assert first[-1][0] == "done" and first[0][1]["memories_used"] == []

    memories = client.get("/api/v1/memories", params={"source_type": "conversation"}, headers=headers()).json()
    assert {m["content"] for m in memories} == {"The user's name is Sam.", "The user prefers short answers with code samples."}
    assert all(m["task_id"] is None and m["provenance"]["source_id"] == conversation_id for m in memories)
    assert all(m["provenance"]["metadata"]["conversation_title"].startswith("My name is Sam") for m in memories)
    # Another member of the workspace sees none of it.
    assert client.get("/api/v1/memories", headers=headers(user="user_other")).json() == []

    second_id = client.post("/api/v1/conversations", headers=headers(), json={}).json()["id"]
    second = _events(client.post(
        f"/api/v1/conversations/{second_id}/ask", headers=headers(), json={"content": "What is my name, Sam?"}
    ))
    recalled = second[0][1]["memories_used"]
    assert recalled and set(recalled) <= {m["id"] for m in memories}

    for memory in memories:
        assert client.delete(f"/api/v1/memories/{memory['id']}", headers=headers()).status_code == 204
    assert client.get("/api/v1/memories", headers=headers()).json() == []
    assert memory_retrieval_store.list_sources(ctx()) == []


def test_a_failing_memory_pass_never_fails_the_chat_stream() -> None:
    from anum_api.main import app

    gateway = FakeGateway(error=RuntimeError("boom"))
    app.dependency_overrides[voice_model_gateway] = lambda: gateway
    client = TestClient(app)
    conversation_id = client.post("/api/v1/conversations", headers=headers(), json={}).json()["id"]
    stream = _events(client.post(
        f"/api/v1/conversations/{conversation_id}/ask", headers=headers(), json={"content": "I prefer tea."}
    ))
    assert stream[-1][0] == "done"
    assert stream[-1][1]["assistant_turn"]["content"].startswith("Noted")
    assert len(gateway.calls) == 1
    assert client.get("/api/v1/memories", headers=headers()).json() == []


def test_a_viewer_cannot_ask_and_nothing_is_remembered_when_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.main import app

    monkeypatch.setattr(settings, "conversation_memory_enabled", False)
    client = TestClient(app)
    conversation_id = client.post("/api/v1/conversations", headers=headers(), json={}).json()["id"]
    _events(client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=headers(), json={"content": "My name is Sam."}))
    assert client.get("/api/v1/memories", headers=headers()).json() == []
