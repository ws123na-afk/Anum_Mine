"""Native Anthropic Messages API adapter: requests, streaming, prompt caching, usage
accounting, error paths and model routing (docs/model-gateway.md)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from anum_api.agent_planning import AgentPlanner
from anum_api.anthropic_gateway import (
    ANTHROPIC_CAPABLE_MODEL,
    ANTHROPIC_FAST_MODEL,
    MAX_CACHE_BREAKPOINTS,
    STRUCTURED_MAX_TOKENS,
    AnthropicGateway,
    anthropic_base_url,
    build_messages_request,
    count_cache_breakpoints,
)
from anum_api.model_budget import (
    BudgetedModelGateway,
    BudgetScope,
    memory_model_budget_store,
    month_start,
)
from anum_api.model_gateway import (
    LOCAL_EMBEDDING_MODEL,
    MockModelGateway,
    ModelRoute,
    ModelStreamError,
    ModelUsage,
    OpenAICompatibleGateway,
    RetryPolicy,
    build_default_model_gateway,
    build_model_gateway,
    configured_route_models,
    estimate_cost_usd,
    route_gateway,
)
from anum_api.schemas import TenantContext
from anum_api.settings import ModelPrice, Settings, settings

API_KEY = "sk-ant-example"
PROMPT = "Summarize the quarterly plan for the example workspace"
SYSTEM = "You are ANUM, a careful assistant."
MESSAGES = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "Hi"},
    {"role": "assistant", "content": "Hello!"},
    {"role": "user", "content": "What is 2+2?"},
]


# --------------------------------------------------------------------------- helpers


class ScriptedStream(httpx.AsyncByteStream):
    def __init__(self, steps: list[bytes | Exception]) -> None:
        self.steps = steps
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for step in self.steps:
            if isinstance(step, Exception):
                raise step
            yield step

    async def aclose(self) -> None:
        self.closed = True


def _sse(kind: str, **payload: Any) -> bytes:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **payload})}\n\n".encode()


def _message_start(model: str = ANTHROPIC_FAST_MODEL, **usage: int) -> bytes:
    return _sse(
        "message_start",
        message={"id": "msg_example", "model": model, "usage": {"output_tokens": 1, **usage}},
    )


def _text(text: str) -> bytes:
    return _sse("content_block_delta", index=0, delta={"type": "text_delta", "text": text})


def _message_delta(output_tokens: int, stop_reason: str = "end_turn", **usage: int) -> bytes:
    return _sse(
        "message_delta",
        delta={"stop_reason": stop_reason},
        usage={"output_tokens": output_tokens, **usage},
    )


STOP = _sse("message_stop")


def _message(text: str = "Four.", **usage: int) -> dict[str, Any]:
    return {
        "id": "msg_example",
        "type": "message",
        "role": "assistant",
        "model": ANTHROPIC_CAPABLE_MODEL,
        "content": [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": text},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5, **usage},
    }


def _gateway(
    responses: list[Callable[[httpx.Request], httpx.Response]],
    *,
    attempts: int = 3,
    sleeps: list[float] | None = None,
    **kwargs: Any,
) -> tuple[AnthropicGateway, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen), len(responses)) - 1](request)

    async def fake_sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    kwargs.setdefault("model", ANTHROPIC_CAPABLE_MODEL)
    gateway = AnthropicGateway(
        api_key=API_KEY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_policy=RetryPolicy(max_attempts=attempts, base_delay_seconds=0.01, max_delay_seconds=0.01),
        sleep=fake_sleep,
        jitter=lambda: 1.0,
        **kwargs,
    )
    return gateway, seen


def _json(payload: dict[str, Any], status: int = 200, **headers: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(status, json=payload, headers=headers)


def _status(status: int, **headers: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(
        status, json={"type": "error", "error": {"type": "api_error", "message": PROMPT}}, headers=headers
    )


def _streaming(stream: ScriptedStream) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})


def _collect(gateway: Any, messages: list[dict[str, str]] = MESSAGES, **kwargs: Any) -> list[str]:
    async def run() -> list[str]:
        return [chunk async for chunk in gateway.stream_chat(messages, **kwargs)]

    return asyncio.run(run())


# --------------------------------------------------------------------------- complete


def test_generate_text_posts_to_the_messages_api_with_anthropic_headers() -> None:
    gateway, seen = _gateway(
        [_json(_message(), **{"request-id": "req_example"})], effort="medium"
    )

    response = asyncio.run(gateway.generate_text(PROMPT))

    request = seen[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == API_KEY
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body == {
        "model": ANTHROPIC_CAPABLE_MODEL,
        "max_tokens": settings.model_max_output_tokens,
        "messages": [{"role": "user", "content": PROMPT}],
        "output_config": {"effort": "medium"},
    }
    # Only text blocks are the answer; thinking blocks are not.
    assert response.text == "Four."
    assert response.metadata is not None
    assert response.metadata.request_id == "req_example"
    assert response.metadata.finish_reason == "end_turn"
    assert response.metadata.attempts == 1
    assert response.usage.provider == "anthropic"
    assert response.usage.model == ANTHROPIC_CAPABLE_MODEL
    assert (response.usage.input_tokens, response.usage.output_tokens) == (10, 5)
    # claude-sonnet-5-5 at $2 / $10 per million tokens.
    assert response.usage.estimated_cost_usd == pytest.approx((10 * 2 + 5 * 10) / 1_000_000)


def test_generate_text_counts_cached_input_and_prices_it_at_cache_rates() -> None:
    gateway, _ = _gateway(
        [_json(_message(cache_creation_input_tokens=2000, cache_read_input_tokens=10000, input_tokens=1000, output_tokens=500))]
    )

    usage = asyncio.run(gateway.generate_text(PROMPT)).usage

    assert usage.input_tokens == 13000  # uncached + written + read
    assert usage.cache_creation_input_tokens == 2000
    assert usage.cache_read_input_tokens == 10000
    # 1000 x $2 + 2000 x $2.50 (1.25x write) + 10000 x $0.20 (0.1x read) + 500 x $10
    assert usage.estimated_cost_usd == pytest.approx(14000 / 1_000_000)


class Plan(BaseModel):
    objective: str
    steps: list[str]


def test_complete_chat_posts_messages_without_cache_breakpoints() -> None:
    # Conversation memory's background call: the Messages API, not the OpenAI form the
    # parent class would send, capped at the caller's max_tokens and never cached.
    gateway, seen = _gateway([_json(_message("- Prefers tea"))])

    response = asyncio.run(gateway.complete_chat(MESSAGES, max_tokens=256))

    request = seen[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    body = json.loads(request.content)
    assert body["max_tokens"] == 256
    assert "choices" not in body and "cache_control" not in request.content.decode()
    assert response.text == "- Prefers tea"
    assert response.usage.provider == "anthropic"


def test_generate_structured_asks_for_the_json_schema_and_validates_the_reply() -> None:
    reply = json.dumps({"objective": "ship", "steps": ["build", "test"]})
    gateway, seen = _gateway([_json(_message(reply))])

    parsed, response = asyncio.run(gateway.generate_structured(PROMPT, Plan))

    assert parsed == Plan(objective="ship", steps=["build", "test"])
    assert response.text == reply
    body = json.loads(seen[0].content)
    assert body["max_tokens"] == STRUCTURED_MAX_TOKENS
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": Plan.model_json_schema()}


def test_base_url_accepts_the_openai_compatible_v1_form() -> None:
    assert anthropic_base_url("https://api.anthropic.com/v1/") == "https://api.anthropic.com"
    assert anthropic_base_url(None) == "https://api.anthropic.com"
    gateway, seen = _gateway([_json(_message())], base_url="https://gateway.example/v1")
    asyncio.run(gateway.generate_text(PROMPT))
    assert str(seen[0].url) == "https://gateway.example/v1/messages"


# --------------------------------------------------------------------------- caching


def test_chat_requests_cache_the_system_prompt_and_the_conversation_so_far() -> None:
    body = build_messages_request(MESSAGES, model=ANTHROPIC_FAST_MODEL, max_tokens=400)

    assert body["system"] == [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][0] == {"role": "user", "content": "Hi"}
    assert body["messages"][-1]["content"] == [
        {"type": "text", "text": "What is 2+2?", "cache_control": {"type": "ephemeral"}}
    ]
    assert count_cache_breakpoints(body) == 2


def test_stable_tool_definitions_are_the_first_cached_prefix() -> None:
    tools = [
        {"name": "lookup", "description": "Look up a record", "input_schema": {"type": "object"}},
        {"name": "notify", "description": "Notify a member", "input_schema": {"type": "object"}},
    ]
    body = build_messages_request(MESSAGES, model=ANTHROPIC_CAPABLE_MODEL, max_tokens=64, tools=tools)

    assert "cache_control" not in body["tools"][0]
    assert body["tools"][1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[1]  # the caller's definitions are not mutated
    assert count_cache_breakpoints(body) == 3 <= MAX_CACHE_BREAKPOINTS


def test_several_system_messages_keep_one_breakpoint_and_caching_can_be_off() -> None:
    messages = [{"role": "system", "content": "Rules."}, {"role": "system", "content": "Facts."}, {"role": "user", "content": "Q"}]
    body = build_messages_request(messages, model="m", max_tokens=16)
    assert [b.get("cache_control") for b in body["system"]] == [None, {"type": "ephemeral"}]
    assert count_cache_breakpoints(body) == 2

    plain = build_messages_request(messages, model="m", max_tokens=16, cache=False)
    assert count_cache_breakpoints(plain) == 0
    assert plain["messages"] == [{"role": "user", "content": "Q"}]


def test_a_request_needs_a_conversation_turn_and_a_known_effort() -> None:
    with pytest.raises(ValueError):
        build_messages_request([{"role": "system", "content": "Rules."}], model="m", max_tokens=16)
    with pytest.raises(ValueError):
        build_messages_request(MESSAGES, model="m", max_tokens=16, effort="extreme")


def test_gateway_tools_make_one_off_prompts_cacheable() -> None:
    tools = [{"name": "lookup", "description": "Look up", "input_schema": {"type": "object"}}]
    gateway, seen = _gateway([_json(_message())], tools=tools)
    asyncio.run(gateway.generate_text(PROMPT))
    body = json.loads(seen[0].content)
    assert body["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert count_cache_breakpoints(body) == 2


def test_cost_estimate_uses_model_specific_cache_prices() -> None:
    # claude-opus-5-5 reads its cache at $0.20 (0.05x), not the default 0.1x.
    cost = estimate_cost_usd(
        "anthropic", "claude-opus-5-5", 2000, 0, cache_read_input_tokens=1000, cache_creation_input_tokens=0
    )
    assert cost == pytest.approx((1000 * 4 + 1000 * 0.2) / 1_000_000)
    prices = {"custom": ModelPrice(input_per_million=1.0, output_per_million=2.0, cache_write_per_million=3.0)}
    assert estimate_cost_usd(
        "anthropic", "custom", 300, 0, prices, cache_creation_input_tokens=100, cache_read_input_tokens=100
    ) == pytest.approx((100 * 1.0 + 100 * 3.0 + 100 * 0.1) / 1_000_000)
    # Without cache figures the estimate is unchanged for every provider.
    assert estimate_cost_usd("openai-compatible", "gpt-4.1-mini", 1000, 1000) == pytest.approx(0.002)


# --------------------------------------------------------------------------- streaming


def test_stream_chat_yields_text_deltas_and_reports_cache_usage() -> None:
    stream = ScriptedStream(
        [
            _message_start(input_tokens=40, cache_creation_input_tokens=600, cache_read_input_tokens=0),
            _sse("content_block_start", index=0, content_block={"type": "thinking", "thinking": ""}),
            _sse("content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": "hidden"}),
            _sse("ping"),
            _sse("content_block_start", index=1, content_block={"type": "text", "text": ""}),
            _text("Fo"),
            _text("ur."),
            _sse("content_block_stop", index=1),
            _message_delta(7),
            STOP,
        ]
    )
    gateway, seen = _gateway([_streaming(stream)], model=ANTHROPIC_FAST_MODEL)
    usages: list[ModelUsage] = []

    assert _collect(gateway, max_tokens=400, on_usage=usages.append) == ["Fo", "ur."]

    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://api.anthropic.com/v1/messages"
    assert body["stream"] is True
    assert body["max_tokens"] == 400
    assert body["model"] == ANTHROPIC_FAST_MODEL
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert stream.closed
    assert len(usages) == 1
    usage = usages[0]
    assert (usage.input_tokens, usage.output_tokens) == (640, 7)
    assert (usage.cache_creation_input_tokens, usage.cache_read_input_tokens) == (600, 0)
    # claude-haiku-5-5: 40 x $0.10 + 600 x $0.125 + 7 x $0.50
    assert usage.estimated_cost_usd == pytest.approx((40 * 0.1 + 600 * 0.125 + 7 * 0.5) / 1_000_000)


def test_a_cache_hit_on_the_next_turn_is_metered_at_the_read_price() -> None:
    stream = ScriptedStream(
        [_message_start(input_tokens=12, cache_read_input_tokens=640), _text("Yes."), _message_delta(3), STOP]
    )
    gateway, _ = _gateway([_streaming(stream)], model=ANTHROPIC_FAST_MODEL)
    usages: list[ModelUsage] = []
    _collect(gateway, on_usage=usages.append)
    assert usages[0].cache_read_input_tokens == 640
    assert usages[0].estimated_cost_usd == pytest.approx((12 * 0.1 + 640 * 0.01 + 3 * 0.5) / 1_000_000)


def test_an_error_event_before_any_text_fails_the_stream() -> None:
    stream = ScriptedStream(
        [_message_start(input_tokens=5), _sse("error", error={"type": "overloaded_error", "message": PROMPT})]
    )
    gateway, seen = _gateway([_streaming(stream)])
    usages: list[ModelUsage] = []
    with pytest.raises(ModelStreamError):
        _collect(gateway, on_usage=usages.append)
    assert len(seen) == 1
    assert usages == []  # nothing was said, nothing is metered


def test_a_stream_that_fails_after_the_first_token_is_not_retried_and_is_metered() -> None:
    stream = ScriptedStream([_message_start(input_tokens=5), _text("Partial answer"), httpx.ReadError("reset")])
    gateway, seen = _gateway([_streaming(stream)], attempts=3)
    usages: list[ModelUsage] = []
    produced: list[str] = []

    async def run() -> None:
        async for chunk in gateway.stream_chat(MESSAGES, on_usage=usages.append):
            produced.append(chunk)

    with pytest.raises(httpx.ReadError):
        asyncio.run(run())
    assert produced == ["Partial answer"]
    assert len(seen) == 1
    assert len(usages) == 1 and usages[0].output_tokens > 0


def test_overloaded_and_rate_limited_streams_are_retried_before_the_first_token() -> None:
    sleeps: list[float] = []
    stream = ScriptedStream([_message_start(input_tokens=5), _text("ok"), _message_delta(1), STOP])
    gateway, seen = _gateway(
        [_status(529), _status(429, **{"retry-after": "0"}), _streaming(stream)], sleeps=sleeps
    )
    assert _collect(gateway) == ["ok"]
    assert len(seen) == 3
    assert len(sleeps) == 2


# --------------------------------------------------------------------------- errors


def test_client_errors_are_raised_without_retry() -> None:
    for status in (400, 401, 404):
        gateway, seen = _gateway([_status(status)])
        with pytest.raises(httpx.HTTPStatusError) as raised:
            asyncio.run(gateway.generate_text(PROMPT))
        assert raised.value.response.status_code == status
        assert len(seen) == 1


def test_server_errors_are_retried_then_raised() -> None:
    sleeps: list[float] = []
    gateway, seen = _gateway([_status(529), _status(500), _status(503)], sleeps=sleeps)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(gateway.generate_text(PROMPT))
    assert len(seen) == 3
    assert len(sleeps) == 2


def test_a_reply_without_message_content_is_an_error() -> None:
    gateway, _ = _gateway([_json({"type": "error", "error": {"type": "api_error"}})])
    with pytest.raises(ValueError):
        asyncio.run(gateway.generate_text(PROMPT))


def test_the_api_key_is_required() -> None:
    with pytest.raises(ValueError):
        AnthropicGateway(api_key="  ")
    with pytest.raises(ValueError):
        build_model_gateway("anthropic")


def test_logs_carry_cache_metadata_but_never_the_prompt_reply_or_key(caplog: pytest.LogCaptureFixture) -> None:
    gateway, _ = _gateway([_json(_message("SECRET-REPLY", cache_read_input_tokens=900))])
    with caplog.at_level(logging.DEBUG, logger="anum.model_gateway"):
        asyncio.run(gateway.generate_text(PROMPT))
    record = next(r for r in caplog.records if r.getMessage().startswith("model_call "))
    fields = record.anum_model_call  # type: ignore[attr-defined]
    assert fields["provider"] == "anthropic"
    assert fields["cache_read_input_tokens"] == 900
    assert fields["cache_creation_input_tokens"] == 0
    for text in (PROMPT, "SECRET-REPLY", API_KEY):
        assert all(text not in r.getMessage() and text not in str(r.__dict__) for r in caplog.records)


def test_embeddings_use_the_local_embedder() -> None:
    gateway, seen = _gateway([_json(_message())])
    assert gateway.default_embedding_model == LOCAL_EMBEDDING_MODEL
    response = asyncio.run(gateway.embed(["alpha beta", "gamma"]))
    assert response.model == LOCAL_EMBEDDING_MODEL and len(response.vectors) == 2
    assert response.usage.estimated_cost_usd == 0
    assert seen == []
    with pytest.raises(ValueError):
        asyncio.run(gateway.embed(["alpha"], model="text-embedding-3-small"))


# --------------------------------------------------------------------------- budgets


@pytest.fixture()
def _budgets() -> Iterator[None]:
    memory_model_budget_store.clear()
    yield
    memory_model_budget_store.clear()


def test_budgets_add_up_cache_tokens_and_their_cost(_budgets: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "repository_backend", "memory")
    context = TenantContext(tenant_id="tenant_cache", workspace_id="workspace_cache", user_id="owner", roles=["owner"])
    stream = ScriptedStream(
        [_message_start(input_tokens=40, cache_read_input_tokens=600), _text("ok"), _message_delta(10), STOP]
    )
    gateway, _ = _gateway([_streaming(stream)], model=ANTHROPIC_FAST_MODEL)
    budgeted = BudgetedModelGateway(gateway, context)

    assert _collect(budgeted) == ["ok"]

    totals = memory_model_budget_store.usage(context, BudgetScope.WORKSPACE, month_start(datetime.now(timezone.utc)))
    assert (totals.input_tokens, totals.output_tokens, totals.calls, totals.unpriced_calls) == (640, 10, 1, 0)
    assert totals.estimated_cost_usd == pytest.approx((40 * 0.1 + 600 * 0.01 + 10 * 0.5) / 1_000_000)


# --------------------------------------------------------------------------- routing


def test_anthropic_defaults_route_quick_replies_to_haiku_and_agent_work_to_sonnet() -> None:
    config = Settings(model_provider="anthropic", model_api_key=API_KEY)
    assert configured_route_models(config) == {
        ModelRoute.FAST: ANTHROPIC_FAST_MODEL,
        ModelRoute.CAPABLE: ANTHROPIC_CAPABLE_MODEL,
    }
    gateway = build_default_model_gateway(config)
    assert isinstance(gateway, AnthropicGateway)
    assert gateway.base_url == "https://api.anthropic.com"
    assert gateway.model == ANTHROPIC_CAPABLE_MODEL
    assert gateway.effort is None
    fast = route_gateway(gateway, ModelRoute.FAST)
    assert (fast.model, fast.effort) == (ANTHROPIC_FAST_MODEL, "low")
    assert fast.api_key == gateway.api_key and fast is not gateway
    assert gateway.model == ANTHROPIC_CAPABLE_MODEL  # routing never changes the original
    assert route_gateway(gateway, ModelRoute.CAPABLE) is gateway


def test_routes_are_configurable_and_a_named_model_serves_both() -> None:
    named = Settings(model_provider="anthropic", model_api_key=API_KEY, model_name="claude-opus-5-5")
    assert set(configured_route_models(named).values()) == {"claude-opus-5-5"}
    custom = Settings(
        model_provider="anthropic",
        model_api_key=API_KEY,
        model_fast_name="claude-haiku-4-5",
        model_fast_effort="",
        model_capable_name="claude-opus-5-5",
        model_capable_effort="high",
    )
    gateway = build_default_model_gateway(custom)
    assert (gateway.model, gateway.effort) == ("claude-opus-5-5", "high")
    fast = route_gateway(gateway, ModelRoute.FAST)
    assert (fast.model, fast.effort) == ("claude-haiku-4-5", None)


def test_effort_settings_are_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(model_fast_effort="extreme")


def test_single_model_configuration_is_unchanged_for_other_providers() -> None:
    config = Settings(model_provider="openai-compatible", model_api_key="sk-example", model_name="gpt-4.1-mini")
    assert configured_route_models(config) == {}
    gateway = build_default_model_gateway(config)
    assert isinstance(gateway, OpenAICompatibleGateway) and not isinstance(gateway, AnthropicGateway)
    assert gateway.model == "gpt-4.1-mini"
    assert route_gateway(gateway, ModelRoute.FAST) is gateway
    assert route_gateway(gateway, ModelRoute.CAPABLE) is gateway

    routed = build_default_model_gateway(
        Settings(model_provider="openai-compatible", model_api_key="sk-example", model_fast_name="gpt-4.1-nano")
    )
    assert routed.model == "gpt-4.1-mini"
    assert route_gateway(routed, ModelRoute.FAST).model == "gpt-4.1-nano"
    assert route_gateway(routed, ModelRoute.CAPABLE) is routed
    assert route_gateway(MockModelGateway(), ModelRoute.FAST).provider == "mock"


def test_a_routed_budgeted_gateway_stays_behind_the_budget() -> None:
    gateway = build_default_model_gateway(Settings(model_provider="anthropic", model_api_key=API_KEY))
    context = TenantContext(tenant_id="tenant_route", workspace_id="workspace_route", user_id="owner", roles=["owner"])
    budgeted = BudgetedModelGateway(gateway, context)
    fast = route_gateway(budgeted, ModelRoute.FAST)
    assert isinstance(fast, BudgetedModelGateway)
    assert fast.inner.model == ANTHROPIC_FAST_MODEL
    assert fast.context == context
    assert route_gateway(budgeted, ModelRoute.CAPABLE) is budgeted


def test_planning_uses_the_capable_route(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.agent_skills import default_skill_registry
    from anum_api.agent_tools import default_tool_registry
    from anum_api.schemas import Task

    gateway, seen = _gateway([_json(_message("Plan ready."))], model="claude-opus-5-5")
    gateway.route_models = {ModelRoute.FAST: ANTHROPIC_FAST_MODEL, ModelRoute.CAPABLE: ANTHROPIC_CAPABLE_MODEL}
    planner = AgentPlanner(gateway, default_skill_registry(), default_tool_registry())
    task = Task.model_validate(
        {
            "id": "task_example",
            "tenant_id": "tenant_route",
            "workspace_id": "workspace_route",
            "created_by": "owner",
            "title": "Plan",
            "prompt": PROMPT,
            "status": "queued",
            "created_at": "2026-10-11T00:00:00Z",
            "updated_at": "2026-10-11T00:00:00Z",
        }
    )
    result = asyncio.run(planner.plan(task))
    assert json.loads(seen[0].content)["model"] == ANTHROPIC_CAPABLE_MODEL
    assert result.model_response.text == "Plan ready."
