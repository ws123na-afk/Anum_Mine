"""Assistant conversations: streamed asks with bounded history (memory backend)."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anum_api import conversations as conversations_module
from anum_api.conversations import (
    ConversationCreate,
    ConversationTurn,
    TurnRole,
    TurnStatus,
    build_messages,
    conversation_store,
    router,
    stream_reply,
)
from anum_api.model_budget import BudgetKind, BudgetScope, ModelBudgetExceededError
from anum_api.schemas import TenantContext, utc_now
from anum_api.voice import voice_model_gateway

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def headers(user: str = "user_chat", tenant: str = "tenant_chat", workspace: str = "workspace_chat") -> dict[str, str]:
    return {"x-tenant-id": tenant, "x-workspace-id": workspace, "x-user-id": user, "x-user-roles": "member"}


H = headers()


def setup_function() -> None:
    conversation_store.clear()
    app.dependency_overrides.clear()


def events(response) -> list[tuple[str, dict]]:  # type: ignore[no-untyped-def]
    parsed = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


def _new(hdrs: dict[str, str] = H) -> str:
    response = client.post("/api/v1/conversations", headers=hdrs, json={})
    assert response.status_code == 201
    return response.json()["id"]


def _ask(conversation_id: str, body: dict, hdrs: dict[str, str] = H):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/conversations/{conversation_id}/ask", headers=hdrs, json=body)


def test_ask_streams_the_reply_and_stores_both_turns() -> None:
    conversation_id = _new()
    response = _ask(conversation_id, {"content": "What can you do?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    stream = events(response)
    kinds = [kind for kind, _ in stream]
    assert kinds[0] == "turn" and kinds[-1] == "done" and kinds.count("delta") > 3
    reply = "".join(data["text"] for kind, data in stream if kind == "delta")
    assert reply.startswith("Mock reply to: What can you do?")
    assert stream[-1][1]["assistant_turn"]["content"] == reply

    detail = client.get(f"/api/v1/conversations/{conversation_id}", headers=H).json()
    assert detail["title"] == "What can you do?"
    assert [(t["role"], t["status"]) for t in detail["turns"]] == [("user", "complete"), ("assistant", "complete")]


def test_follow_up_questions_carry_the_earlier_turns() -> None:
    conversation_id = _new()
    _ask(conversation_id, {"content": "First question"})
    reply = "".join(d["text"] for k, d in events(_ask(conversation_id, {"content": "And a follow-up?"})) if k == "delta")
    assert "1 earlier question" in reply


def test_conversations_are_private_to_their_user_workspace_and_tenant() -> None:
    conversation_id = _new()
    _ask(conversation_id, {"content": "secret plans"})
    for other in (headers(user="user_other"), headers(tenant="tenant_other"), headers(workspace="workspace_other")):
        assert client.get(f"/api/v1/conversations/{conversation_id}", headers=other).status_code == 404
        assert client.get("/api/v1/conversations", headers=other).json() == []
        assert _ask(conversation_id, {"content": "leak?"}, other).status_code == 404
        assert client.delete(f"/api/v1/conversations/{conversation_id}", headers=other).status_code == 404
    assert len(client.get("/api/v1/conversations", headers=H).json()) == 1


def test_list_is_most_recent_first_and_delete_removes_turns() -> None:
    first, second = _new(), _new()
    _ask(first, {"content": "bump"})
    listed = [item["id"] for item in client.get("/api/v1/conversations", headers=H).json()]
    assert listed == [first, second]
    assert client.delete(f"/api/v1/conversations/{first}", headers=H).status_code == 204
    assert client.get(f"/api/v1/conversations/{first}", headers=H).status_code == 404


class FailingGateway:
    provider = "test"

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
        yield "Half an"
        raise TimeoutError("provider stalled")


def test_a_failed_reply_is_reported_and_can_be_retried_without_a_duplicate_question() -> None:
    conversation_id = _new()
    app.dependency_overrides[voice_model_gateway] = lambda: FailingGateway()
    stream = events(_ask(conversation_id, {"content": "Long question"}))
    assert stream[-1][0] == "error"
    assert stream[-1][1]["code"] == "model_unavailable"
    assert "provider stalled" not in json.dumps(stream)
    user_turn_id = stream[0][1]["user_turn"]["id"]

    app.dependency_overrides.clear()
    retried = events(_ask(conversation_id, {"retry_turn_id": user_turn_id}))
    assert retried[0][1]["user_turn"]["id"] == user_turn_id
    assert retried[-1][0] == "done"
    turns = client.get(f"/api/v1/conversations/{conversation_id}", headers=H).json()["turns"]
    # Retry replaces the failed reply instead of leaving it above the new one.
    assert [(t["role"], t["status"]) for t in turns] == [("user", "complete"), ("assistant", "complete")]
    assert turns[1]["content"] != "Half an"


def test_only_the_latest_question_can_be_retried() -> None:
    conversation_id = _new()
    first = events(_ask(conversation_id, {"content": "one"}))[0][1]["user_turn"]["id"]
    _ask(conversation_id, {"content": "two"})
    assert _ask(conversation_id, {"retry_turn_id": first}).status_code == 409
    assert _ask(conversation_id, {}).status_code == 422
    assert _ask(conversation_id, {"content": "x", "retry_turn_id": first}).status_code == 422


def test_budget_refusal_happens_before_anything_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(context, now=None):  # type: ignore[no-untyped-def]
        raise ModelBudgetExceededError(BudgetScope.WORKSPACE, BudgetKind.TOKENS, date(2026, 11, 1))

    monkeypatch.setattr(conversations_module, "check_model_budget", refuse)
    conversation_id = _new()
    with pytest.raises(ModelBudgetExceededError):  # main.py turns this into a 402
        _ask(conversation_id, {"content": "anything"})
    assert client.get(f"/api/v1/conversations/{conversation_id}", headers=H).json()["turns"] == []


def _turn(role: TurnRole, content: str, turn_status: TurnStatus = TurnStatus.COMPLETE) -> ConversationTurn:
    return ConversationTurn(id=f"t{content}", conversation_id="c", role=role, content=content, status=turn_status, created_at=utc_now())


def test_history_is_bounded_by_turns_and_characters_and_alternates() -> None:
    history = []
    for index in range(10):
        history += [_turn(TurnRole.USER, f"q{index}"), _turn(TurnRole.ASSISTANT, f"a{index}")]
    messages = build_messages(history, "now", max_turns=4, max_chars=10_000)
    assert [m["content"] for m in messages[1:]] == ["q8", "a8", "q9", "a9", "now"]
    assert messages[0]["role"] == "system"

    long = [_turn(TurnRole.USER, "x" * 600), _turn(TurnRole.ASSISTANT, "y" * 600), _turn(TurnRole.USER, "z" * 300)]
    trimmed = build_messages(long + [_turn(TurnRole.ASSISTANT, "ok")], "now", max_turns=10, max_chars=1000)
    # Kept newest first until the budget runs out, starting at a question.
    assert [m["content"][:1] for m in trimmed[1:]] == ["z", "o", "n"]

    # A failed (empty) reply leaves two questions in a row: they are joined.
    failed = [_turn(TurnRole.USER, "first"), _turn(TurnRole.ASSISTANT, "", TurnStatus.FAILED)]
    joined = build_messages(failed, "second", max_turns=10, max_chars=1000)
    assert [m["role"] for m in joined] == ["system", "user"]
    assert joined[1]["content"] == "first\n\nsecond"


class SlowGateway:
    provider = "test"

    def __init__(self) -> None:
        self.closed = False

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
        try:
            yield "Thinking"
            await asyncio.sleep(10)
            yield " never"
        finally:
            self.closed = True


def test_a_disconnected_client_stops_generation_and_keeps_the_partial_reply() -> None:
    context = TenantContext(tenant_id="tenant_chat", workspace_id="workspace_chat", user_id="user_chat", roles=["member"])
    conversation = conversation_store.create(ConversationCreate(), context)
    user_turn = conversation_store.add_turn(conversation, TurnRole.USER, "Explain", TurnStatus.COMPLETE)
    gateway = SlowGateway()

    async def run() -> list[str]:
        body = stream_reply(
            context=context,
            conversation=conversation,
            user_turn=user_turn,
            messages=build_messages([], "Explain"),
            gateway=gateway,  # type: ignore[arg-type]
        )
        frames = [await anext(body), await anext(body)]
        await body.aclose()  # Starlette closes the body when the client goes away
        return frames

    frames = asyncio.run(run())
    assert frames[1].startswith("event: delta")
    assert gateway.closed
    turns = conversation_store.turns(conversation)
    assert (turns[-1].role, turns[-1].status, turns[-1].content) == (TurnRole.ASSISTANT, TurnStatus.CANCELLED, "Thinking")


def test_the_main_app_serves_conversations() -> None:
    from anum_api.main import app as main_app

    paths = main_app.openapi()["paths"]
    assert "/api/v1/conversations/{conversation_id}/ask" in paths


def test_conversation_retention_defaults_to_30_days_and_zero_keeps_forever() -> None:
    from pydantic import ValidationError

    from anum_api.settings import Settings

    assert Settings().conversation_retention_days == 30
    assert Settings(conversation_retention_days=0).conversation_retention_days == 0
    for invalid in (-1, 3651):
        with pytest.raises(ValidationError):
            Settings(conversation_retention_days=invalid)


def test_conversation_retention_command_refuses_the_memory_backend(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from anum_api.conversation_retention import main
    from anum_api.settings import settings

    monkeypatch.setattr(settings, "repository_backend", "memory")
    assert main([]) == 2
    assert "needs ANUM_REPOSITORY_BACKEND=postgresql" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--batch-size", "0"])
