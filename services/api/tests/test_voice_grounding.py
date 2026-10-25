"""Grounded spoken answers (docs/voice.md#grounded-answers): read-only lookups through
ToolPolicy, tenant scope, bounded rounds, the snapshot fallback, the "don't invent" rule
and the turn timings in the stream."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import anum_api.main  # noqa: F401  (configures the runtime registry, as in the app)
from anum_api import voice_actions, voice_grounding
from anum_api.agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from anum_api.anthropic_gateway import AnthropicGateway, ToolRound
from anum_api.dependencies import memory_repository
from anum_api.model_budget import BudgetedModelGateway
from anum_api.model_gateway import MockModelGateway, RetryPolicy
from anum_api.schemas import RiskLevel, Task, TaskStatus, TenantContext, new_id, utc_now
from anum_api.voice import VOICE_MAX_TOKENS, VoiceTurnTiming, router, voice_model_gateway, voice_store
from anum_api.voice_assistant import WorkspaceSnapshot
from anum_api.voice_grounding import (
    GROUNDING_RULES,
    MAX_TOOL_ROUNDS,
    TurnTiming,
    VoiceToolbox,
    grounded_answer,
    is_voice_readable,
    run_read_tool,
)

app = FastAPI()
app.include_router(router)
client = TestClient(app)
TENANT, WORKSPACE, OTHER = "tenant_ground", "workspace_ground", "workspace_other"
headers = {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": "user_ground", "x-user-roles": "member"}
FACTS = WorkspaceSnapshot(tasks_total=0, running=0, waiting_approval=0, pending_approvals=0)
member = TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id="user_ground", roles=["member"])


def setup_function() -> None:
    voice_store.clear()
    memory_repository.store.tasks.clear()
    memory_repository.store.audit_records.clear()
    app.dependency_overrides.clear()


def _task(title: str, workspace: str = WORKSPACE, status: TaskStatus = TaskStatus.RUNNING) -> Task:
    now = utc_now()
    task = Task(id=new_id("task"), title=title, prompt=title, status=status, tenant_id=TENANT,
                workspace_id=workspace, created_at=now, updated_at=now, created_by="user_ground")
    memory_repository.store.tasks[task.id] = task
    return task


# --------------------------------------------------------------------------- a scripted model


class ScriptedToolModel:
    """An "anthropic" gateway whose rounds follow a script: each step is a list of text
    pieces and tool calls, and the round's stop reason."""

    provider = "anthropic"

    def __init__(self, steps: list[tuple[list[Any], str]]) -> None:
        self.steps = steps
        self.rounds: list[ToolRound] = []
        self.messages: list[list[dict[str, str]]] = []
        self._round: ToolRound | None = None

    def with_tool_round(self, tool_round: ToolRound) -> ScriptedToolModel:
        variant = copy.copy(self)
        variant._round = tool_round
        return variant

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None):  # type: ignore[no-untyped-def]
        assert max_tokens == VOICE_MAX_TOKENS
        tool_round = self._round
        assert tool_round is not None
        self.rounds.append(tool_round)
        self.messages.append(messages)
        pieces, stop = self.steps[min(len(self.rounds), len(self.steps)) - 1]
        for index, piece in enumerate(pieces):
            if isinstance(piece, str):
                tool_round.start(index, {"type": "text", "text": ""})
                tool_round.delta(index, {"type": "text_delta", "text": piece})
                tool_round.stop(index)
                yield piece
            else:
                name, arguments = piece
                tool_round.start(index, {"type": "tool_use", "id": f"toolu_{len(self.rounds)}_{index}", "name": name, "input": {}})
                raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
                tool_round.delta(index, {"type": "input_json_delta", "partial_json": raw})
                tool_round.stop(index)
        tool_round.stop_reason = stop


def _answer(gateway: Any, question: str, timing: TurnTiming | None = None) -> str:
    async def run() -> str:
        parts = []
        async for chunk in grounded_answer(
            gateway, question, FACTS, "Anum", [], member, timing or TurnTiming(), max_tokens=VOICE_MAX_TOKENS,
        ):
            if isinstance(chunk, str):
                parts.append(chunk)
        return "".join(parts)

    return asyncio.run(run())


def _results(gateway: ScriptedToolModel, round_index: int) -> list[dict[str, Any]]:
    """The tool_result blocks sent back in round ``round_index`` (1 = after the first)."""
    return gateway.rounds[round_index].prior[-1]["content"]


# --------------------------------------------------------------------------- which tools


def test_only_registered_read_only_tools_are_offered_and_new_read_tools_are_picked_up() -> None:
    toolbox = VoiceToolbox.current()
    names = set(toolbox.names)
    # The voice's own workspace reads and the runtime's read tools.
    assert {"workspace_tasks", "workspace_approvals", "workspace_agents", "knowledge_search"} <= names
    assert {"tyre_fleet_summary", "tyre_alerts", "commerce.sales_summary"} <= names
    # Writes, outward actions, delivery tools and non-lookups never are.
    runtime = voice_actions.configured().tools  # type: ignore[union-attr]
    for name in runtime.names:
        definition = runtime.definition(name)
        assert (name in names) == is_voice_readable(definition), name
    assert not names & {"anum.respond", "web_search", "external.action", "team.assign_task"}
    assert all(runtime.definition(name).action_kind == "read" for name in names if runtime.definition(name))  # type: ignore[union-attr]
    # Model-facing names are API-safe and map back to the real tool.
    for tool in toolbox.model_tools():
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool["name"])
        assert tool["eager_input_streaming"] is True and tool["input_schema"]["type"] == "object"
    assert toolbox.resolve("commerce_sales_summary").definition.name == "commerce.sales_summary"  # type: ignore[union-attr]

    # A read tool another module registers later (category and kind declared) is picked up
    # with no change here; a write in the same registry is not.
    later = ToolRegistry()

    async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
        return ToolResult(status="succeeded", summary="ok")

    later.register(ToolDefinition(name="fleet.later_read", description="A later read.", risk_level=RiskLevel.LOW,
                                  category="general", action_kind="read"), handler)
    later.register(ToolDefinition(name="fleet.later_write", description="A write.", risk_level=RiskLevel.LOW,
                                  category="general", action_kind="update"), handler)
    later.register(ToolDefinition(name="fleet.medium_read", description="Medium.", risk_level=RiskLevel.MEDIUM,
                                  category="general", action_kind="read"), handler)
    assert VoiceToolbox([later]).names == ["fleet.later_read"]


def test_a_write_tool_asked_for_by_the_model_is_refused_and_never_runs() -> None:
    before = len(memory_repository.store.tasks)
    model = ScriptedToolModel([
        ([("team_assign_task", {"text": "ask Ahmed to pay"}), ("external_action", {"action": "pay"})], "tool_use"),
        (["I can't do that by voice."], "end_turn"),
    ])
    _answer(model, "pay the supplier")
    results = _results(model, 1)
    assert [result["is_error"] for result in results] == [True, True]
    assert all("not available" in result["content"] for result in results)
    assert len(memory_repository.store.tasks) == before
    # Called directly with a write tool's entry, the mediation refuses it too.
    runtime = voice_actions.configured().tools  # type: ignore[union-attr]
    entry = voice_grounding._Entry(runtime, runtime.definition("external.action"))  # type: ignore[arg-type]
    refused = asyncio.run(run_read_tool(entry, ToolCall(name="external.action"), member))
    assert refused.status == "blocked"


# --------------------------------------------------------------------------- lookups


def test_the_model_reads_tasks_through_policy_in_the_callers_workspace_only() -> None:
    _task("Quarterly VAT filing")
    _task("Secret merger plan", workspace=OTHER)
    model = ScriptedToolModel([
        ([("workspace_tasks", {"query": "VAT"})], "tool_use"),
        (["According to Tasks, the VAT filing is running."], "end_turn"),
    ])
    timing = TurnTiming()
    reply = _answer(model, "Where is the VAT filing?", timing)
    assert reply == "According to Tasks, the VAT filing is running."
    content = _results(model, 1)[0]["content"]
    assert "Quarterly VAT filing" in content and "Secret merger plan" not in content
    assert "untrusted" in content.lower()
    # The snapshot of the other workspace is not in the prompt either.
    system = model.messages[0][0]["content"]
    assert "Secret merger plan" not in system and "Quarterly VAT filing" in system
    # The assistant turn is sent back unchanged with the tool_use, then all results in one user turn.
    assert model.rounds[1].prior[0]["role"] == "assistant"
    assert model.rounds[1].prior[0]["content"][0]["type"] == "tool_use"
    assert model.rounds[1].prior[1]["role"] == "user"
    audit = [record for record in memory_repository.list_audit_records(member) if record.action == "tool.call"]
    assert any(record.target == "workspace_tasks" and record.metadata.get("surface") == "voice" for record in audit)
    assert all(str(record.metadata).find("VAT") < 0 for record in audit), "no content in the audit"
    assert timing.tool_rounds == 1 and timing.tool_calls == 1 and timing.grounding == "tools"
    assert "workspace_tasks" in timing.sources


def test_lookup_rounds_are_bounded_and_the_last_round_must_answer() -> None:
    model = ScriptedToolModel([([("workspace_agents", {})], "tool_use")])
    timing = TurnTiming()
    _answer(model, "Tell me everything", timing)
    assert len(model.rounds) == MAX_TOOL_ROUNDS + 1
    assert [tool_round.final for tool_round in model.rounds] == [False] * MAX_TOOL_ROUNDS + [True]
    assert timing.tool_rounds == MAX_TOOL_ROUNDS


def test_too_many_calls_in_one_round_and_broken_arguments_are_not_run() -> None:
    model = ScriptedToolModel([
        ([("workspace_tasks", "{\"status\": "), *[("workspace_agents", {})] * 5], "tool_use"),
        (["Done."], "end_turn"),
    ])
    timing = TurnTiming()
    _answer(model, "Lots", timing)
    results = _results(model, 1)
    assert results[0]["is_error"] and "INVALID_JSON" in results[0]["content"]
    assert [result["is_error"] for result in results[1:4]] == [False, False, False]
    assert all(result["is_error"] for result in results[4:])
    assert timing.tool_calls == 3


def test_a_tool_that_policy_blocks_is_not_run() -> None:
    ran: list[str] = []
    registry = ToolRegistry()

    async def handler(call: ToolCall, context: TenantContext) -> ToolResult:
        ran.append(call.name)
        return ToolResult(status="succeeded", summary="owners only")

    registry.register(ToolDefinition(name="owner_read", description="Owners only.", risk_level=RiskLevel.LOW,
                                     required_roles=frozenset({"owner"}), category="general", action_kind="read"), handler)
    toolbox = VoiceToolbox([registry])
    result = asyncio.run(run_read_tool(toolbox.entry("owner_read"), ToolCall(name="owner_read"), member))
    assert result.status == "blocked" and ran == []


def test_a_slow_tool_times_out() -> None:
    registry = ToolRegistry()

    async def slow(call: ToolCall, context: TenantContext) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(status="succeeded", summary="late")

    registry.register(ToolDefinition(name="slow_read", description="Slow.", risk_level=RiskLevel.LOW,
                                     category="general", action_kind="read"), slow)
    toolbox = VoiceToolbox([registry])
    result = asyncio.run(run_read_tool(toolbox.entry("slow_read"), ToolCall(name="slow_read"), member, timeout=0.05))
    assert result.status == "failed" and "timed out" in result.summary and result.audit_metadata["outcome"] == "timeout"


# --------------------------------------------------------------------------- the fallback


class PlainModel:
    provider = "openai-compatible"

    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    async def stream_chat(self, messages, *, max_tokens=None, on_usage=None):  # type: ignore[no-untyped-def]
        self.messages.append(messages)
        yield "I couldn't find that."


def test_without_tool_use_the_snapshot_is_prefetched_with_the_dont_invent_rule() -> None:
    _task("Renew the trade licence", status=TaskStatus.WAITING_APPROVAL)
    _task("Other workspace job", workspace=OTHER)
    model = PlainModel()
    timing = TurnTiming()
    assert _answer(model, "What about the licence?", timing) == "I couldn't find that."
    system = model.messages[0][0]["content"]
    assert GROUNDING_RULES in system
    assert "Never invent names, counts, amounts or dates" in system and "couldn't find it" in system
    assert "Renew the trade licence" in system and "Other workspace job" not in system
    assert voice_grounding.TOOL_RULES not in system  # no tools to call
    assert timing.grounding == "snapshot"
    assert {"workspace_tasks", "workspace_approvals", "workspace_agents", "knowledge_search"} <= set(timing.sources)


def test_the_mock_provider_still_answers_without_a_model() -> None:
    session = client.post("/api/v1/voice/sessions", headers=headers, json={}).json()["id"]
    segment = client.post(f"/api/v1/voice/sessions/{session}/transcript", headers=headers,
                          json={"text": "Why is the sky blue?", "client_sequence": 0}).json()["id"]
    app.dependency_overrides[voice_model_gateway] = lambda: MockModelGateway()
    answer = client.post(f"/api/v1/voice/sessions/{session}/ask", headers=headers, json={"transcript_segment_id": segment}).json()
    assert "connect a free local model" in answer["reply"]
    assert answer["timing"]["grounding"] == "none"


# --------------------------------------------------------------------------- the stream


def _frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    frames = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


def test_the_stream_sends_timings_before_done_and_in_it() -> None:
    _task("Weekly report")
    model = ScriptedToolModel([
        ([("workspace_tasks", {})], "tool_use"),
        (["You have one task running, ", "according to Tasks."], "end_turn"),
    ])
    app.dependency_overrides[voice_model_gateway] = lambda: model
    session = client.post("/api/v1/voice/sessions", headers=headers, json={}).json()["id"]
    segment = client.post(f"/api/v1/voice/sessions/{session}/transcript", headers=headers,
                          json={"text": "How is my weekly report doing?", "client_sequence": 0}).json()["id"]
    response = client.post(f"/api/v1/voice/sessions/{session}/ask/stream", headers=headers,
                           json={"transcript_segment_id": segment})
    frames = _frames(response.text)
    kinds = [kind for kind, _ in frames]
    # Progress markers (what is being checked) come between the meta event and the reply text.
    assert kinds[0] == "meta" and kinds[1] == "progress"
    assert [kind for kind in kinds if kind != "progress"] == ["meta", "delta", "timing", "done"]
    assert {data["phase"] for kind, data in frames if kind == "progress"} == {"checking"}
    assert kinds.index("progress") < kinds.index("delta")
    timing = frames[-2][1]
    assert set(timing) == set(VoiceTurnTiming.model_fields)
    assert timing == frames[-1][1]["timing"]
    assert timing["grounding"] == "tools" and timing["tool_rounds"] == 1 and timing["tool_calls"] == 1
    assert timing["first_token_ms"] is not None and 0 <= timing["first_token_ms"] <= timing["total_ms"]
    assert timing["tool_ms"] <= timing["total_ms"]
    # Measured in this test environment (in-memory store, scripted model): printed for the docs.
    print(f"voice turn timing: {timing}")
    assert frames[-1][1]["assistant_segment"]["text"] == "You have one task running, according to Tasks."


def test_the_typescript_contract_matches_the_timing() -> None:
    source = (Path(__file__).resolve().parents[3] / "packages" / "contracts" / "src" / "voice.ts").read_text()
    body = re.search(r"export interface VoiceTurnTiming \{(.*?)\n\}", source, re.S)
    assert body
    assert set(re.findall(r"^\s+(\w+)\??:", body.group(1), re.M)) == set(VoiceTurnTiming.model_fields)


# --------------------------------------------------------------------------- the adapter


def _sse(kind: str, **payload: Any) -> bytes:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **payload})}\n\n".encode()


def test_the_anthropic_adapter_streams_a_tool_round_and_keeps_thinking_blocks() -> None:
    seen: list[dict[str, Any]] = []
    events = b"".join([
        _sse("message_start", message={"id": "msg_x", "model": "fast-model", "usage": {"input_tokens": 5, "output_tokens": 1}}),
        _sse("content_block_start", index=0, content_block={"type": "thinking", "thinking": "", "signature": ""}),
        _sse("content_block_delta", index=0, delta={"type": "signature_delta", "signature": "sig"}),
        _sse("content_block_stop", index=0),
        _sse("content_block_start", index=1, content_block={"type": "text", "text": ""}),
        _sse("content_block_delta", index=1, delta={"type": "text_delta", "text": "One moment."}),
        _sse("content_block_stop", index=1),
        _sse("content_block_start", index=2, content_block={"type": "tool_use", "id": "toolu_1", "name": "workspace_tasks", "input": {}}),
        _sse("content_block_delta", index=2, delta={"type": "input_json_delta", "partial_json": "{\"status\": "}),
        _sse("content_block_delta", index=2, delta={"type": "input_json_delta", "partial_json": "\"open\"}"}),
        _sse("content_block_stop", index=2),
        _sse("message_delta", delta={"stop_reason": "tool_use"}, usage={"output_tokens": 20}),
        _sse("message_stop"),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=events, headers={"content-type": "text/event-stream"})

    gateway = AnthropicGateway(api_key="example", model="fast-model", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                               retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0.01, max_delay_seconds=0.01))
    tools = VoiceToolbox.current().model_tools()
    prior = [{"role": "assistant", "content": [{"type": "text", "text": "earlier"}]}]
    tool_round = ToolRound(tools=tools, prior=prior)

    async def run() -> list[str]:
        return [chunk async for chunk in gateway.with_tool_round(tool_round).stream_chat(
            [{"role": "system", "content": "rules"}, {"role": "user", "content": "question"}], max_tokens=400)]

    assert asyncio.run(run()) == ["One moment."]
    body = seen[0]
    assert body["tool_choice"] == {"type": "auto"} and body["max_tokens"] == 400 and body["stream"] is True
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][-1] == prior[0]
    assert tool_round.stop_reason == "tool_use"
    content = tool_round.content()
    assert content[0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert tool_round.tool_uses() == [{"type": "tool_use", "id": "toolu_1", "name": "workspace_tasks", "input": {"status": "open"}}]
    assert not tool_round.invalid
    # The plain gateway is untouched: no tools, no tool_choice.
    assert gateway._tool_round is None

    final = ToolRound(tools=tools, final=True)
    asyncio.run(_drain(gateway.with_tool_round(final)))
    assert seen[1]["tool_choice"] == {"type": "none"}


async def _drain(gateway: Any) -> None:
    async for _ in gateway.stream_chat([{"role": "user", "content": "q"}], max_tokens=400):
        pass


def test_a_tool_round_stays_behind_the_budget() -> None:
    gateway = AnthropicGateway(api_key="example", model="fast-model")
    budgeted = BudgetedModelGateway(gateway, member)
    variant = budgeted.with_tool_round(ToolRound(tools=[]))
    assert isinstance(variant, BudgetedModelGateway) and variant.inner._tool_round is not None  # type: ignore[attr-defined]
    assert BudgetedModelGateway(MockModelGateway(), member).with_tool_round(ToolRound(tools=[])) is None
    assert voice_grounding.tool_gateway(budgeted) is budgeted
    assert voice_grounding.tool_gateway(BudgetedModelGateway(MockModelGateway(), member)) is None
