"""Token streaming, output caps and stream time budgets in the model gateway."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from anum_api.model_budget import BudgetedModelGateway
from anum_api.model_gateway import (
    MockModelGateway,
    ModelStreamTimeout,
    ModelUsage,
    OpenAICompatibleGateway,
    RetryPolicy,
    build_model_gateway,
    validate_messages,
)
from anum_api.schemas import TenantContext
from anum_api.settings import settings

MESSAGES = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "Hi"},
    {"role": "assistant", "content": "Hello!"},
    {"role": "user", "content": "What is 2+2?"},
]


def _event(content: str | None = None, **extra: object) -> bytes:
    payload: dict[str, object] = {"choices": [{"delta": {"content": content}}] if content is not None else []}
    payload.update(extra)
    return f"data: {json.dumps(payload)}\n\n".encode()


class ScriptedStream(httpx.AsyncByteStream):
    """A response body that yields events with optional pauses or a failure."""

    def __init__(self, steps: list[bytes | float | Exception]) -> None:
        self.steps = steps
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for step in self.steps:
            if isinstance(step, Exception):
                raise step
            if isinstance(step, float):
                await asyncio.sleep(step)
                continue
            yield step

    async def aclose(self) -> None:
        self.closed = True


def _gateway(
    responses: list[Callable[[httpx.Request], httpx.Response]],
    *,
    attempts: int = 3,
    sleeps: list[float] | None = None,
    **kwargs: object,
) -> tuple[OpenAICompatibleGateway, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen), len(responses)) - 1](request)

    async def fake_sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    gateway = OpenAICompatibleGateway(
        api_key="sk-test-example",
        model="claude-sonnet-5-5",
        base_url="https://api.anthropic.com/v1",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_policy=RetryPolicy(max_attempts=attempts, base_delay_seconds=0.01, max_delay_seconds=0.01),
        sleep=fake_sleep,
        jitter=lambda: 1.0,
        **kwargs,
    )
    return gateway, seen


def _streaming(stream: ScriptedStream) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})


def _collect(gateway, messages=MESSAGES, **kwargs) -> list[str]:  # type: ignore[no-untyped-def]
    async def run() -> list[str]:
        return [chunk async for chunk in gateway.stream_chat(messages, **kwargs)]

    return asyncio.run(run())


def test_stream_chat_sends_the_conversation_with_a_token_cap_and_streams_deltas() -> None:
    stream = ScriptedStream([_event("Fo"), _event("ur"), _event("."), b"data: [DONE]\n\n"])
    gateway, seen = _gateway([_streaming(stream)])

    assert _collect(gateway) == ["Fo", "ur", "."]
    body = json.loads(seen[0].content)
    assert body["messages"] == MESSAGES
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == settings.model_max_output_tokens == 1024
    assert seen[0].url == "https://api.anthropic.com/v1/chat/completions"


def test_max_tokens_is_configurable_per_gateway_and_per_call() -> None:
    gateway, seen = _gateway(
        [_streaming(ScriptedStream([_event("a")])), _streaming(ScriptedStream([_event("b")]))],
        max_output_tokens=256,
    )
    _collect(gateway)
    _collect(gateway, max_tokens=64)
    assert [json.loads(request.content)["max_tokens"] for request in seen] == [256, 64]


def test_generate_text_is_capped_but_structured_output_is_not() -> None:
    from pydantic import BaseModel

    class Plan(BaseModel):
        step: str

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"step": "x"}'}}]})

    gateway, seen = _gateway([reply])
    asyncio.run(gateway.generate_text("hello"))
    asyncio.run(gateway.generate_structured("hello", Plan))
    assert json.loads(seen[0].content)["max_tokens"] == 1024
    assert "max_tokens" not in json.loads(seen[1].content)


def test_a_stream_that_breaks_after_the_first_token_is_not_retried() -> None:
    stream = ScriptedStream([_event("Partial"), httpx.ReadError("connection reset")])
    gateway, seen = _gateway([_streaming(stream)], sleeps=[])
    received: list[str] = []

    async def run() -> None:
        async for chunk in gateway.stream_chat(MESSAGES):
            received.append(chunk)

    with pytest.raises(httpx.ReadError):
        asyncio.run(run())
    assert received == ["Partial"]
    assert len(seen) == 1


def test_no_first_token_in_time_is_retried_then_raised() -> None:
    sleeps: list[float] = []
    gateway, seen = _gateway(
        [_streaming(ScriptedStream([0.5, _event("late")]))],
        attempts=2,
        sleeps=sleeps,
        first_token_timeout_seconds=0.05,
        stream_timeout_seconds=10,
    )
    with pytest.raises(ModelStreamTimeout):
        _collect(gateway)
    assert len(seen) == 2
    assert sleeps == [0.01]


def test_first_token_timeout_recovers_on_the_next_attempt() -> None:
    gateway, seen = _gateway(
        [_streaming(ScriptedStream([0.5, _event("late")])), _streaming(ScriptedStream([_event("fast")]))],
        first_token_timeout_seconds=0.05,
    )
    assert _collect(gateway) == ["fast"]
    assert len(seen) == 2


def test_the_total_stream_budget_ends_a_slow_stream_without_retrying() -> None:
    gateway, seen = _gateway(
        [_streaming(ScriptedStream([_event("Start"), 0.5, _event("never")]))],
        first_token_timeout_seconds=5,
        stream_timeout_seconds=0.1,
    )
    received: list[str] = []

    async def run() -> None:
        async for chunk in gateway.stream_chat(MESSAGES):
            received.append(chunk)

    with pytest.raises(ModelStreamTimeout):
        asyncio.run(run())
    assert received == ["Start"]
    assert len(seen) == 1


def test_reported_stream_usage_is_passed_on() -> None:
    stream = ScriptedStream(
        [
            _event("Hi", model="claude-sonnet-5-5"),
            _event(None, usage={"prompt_tokens": 40, "completion_tokens": 3}),
            b"data: [DONE]\n\n",
        ]
    )
    gateway, _ = _gateway([_streaming(stream)])
    usage: list[ModelUsage] = []
    assert _collect(gateway, on_usage=usage.append) == ["Hi"]
    assert [(u.input_tokens, u.output_tokens) for u in usage] == [(40, 3)]


def test_unreported_usage_is_estimated() -> None:
    gateway, _ = _gateway([_streaming(ScriptedStream([_event("x" * 40)]))])
    usage: list[ModelUsage] = []
    _collect(gateway, on_usage=usage.append)
    assert usage[0].output_tokens == 10
    assert usage[0].input_tokens > 0


def test_closing_the_stream_closes_the_provider_response_and_meters_what_was_said() -> None:
    stream = ScriptedStream([_event("One"), 0.05, _event(" two"), 0.05, _event(" three")])
    gateway, _ = _gateway([_streaming(stream)])
    usage: list[ModelUsage] = []

    async def run() -> list[str]:
        received: list[str] = []
        iterator = gateway.stream_chat(MESSAGES, on_usage=usage.append)
        received.append(await anext(iterator))
        await iterator.aclose()  # the client disconnected or pressed Stop
        return received

    assert asyncio.run(run()) == ["One"]
    assert stream.closed
    assert len(usage) == 1 and usage[0].output_tokens == 1


def test_a_provider_error_event_inside_the_stream_fails_the_call() -> None:
    gateway, _ = _gateway([_streaming(ScriptedStream([b'data: {"error": {"message": "overloaded"}}\n\n']))])
    with pytest.raises(RuntimeError):
        _collect(gateway)


def test_messages_are_validated() -> None:
    with pytest.raises(ValueError):
        validate_messages([])
    with pytest.raises(ValueError):
        validate_messages([{"role": "tool", "content": "x"}])


def test_ollama_streams_through_its_openai_compatible_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=ScriptedStream([_event("local")]))

    gateway = build_model_gateway(
        "ollama", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert _collect(gateway) == ["local"]
    assert seen[0].url == "http://localhost:11434/v1/chat/completions"
    assert json.loads(seen[0].content)["stream"] is True


def test_mock_gateway_streams_a_reply_that_sees_earlier_turns() -> None:
    usage: list[ModelUsage] = []
    text = "".join(_collect(MockModelGateway(), on_usage=usage.append))
    assert text.startswith("Mock reply to: What is 2+2?")
    assert "1 earlier question" in text
    assert usage and usage[0].estimated_cost_usd == 0


def test_budgeted_stream_is_checked_and_metered(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api import model_budget

    checked: list[str] = []
    recorded: list[ModelUsage] = []
    monkeypatch.setattr(model_budget, "check_model_budget", lambda context, now=None: checked.append(context.tenant_id))
    monkeypatch.setattr(model_budget, "record_model_usage", lambda context, usage, now=None: recorded.append(usage))
    context = TenantContext(tenant_id="t", workspace_id="w", user_id="u", roles=["member"])
    passed_on: list[ModelUsage] = []
    gateway = BudgetedModelGateway(MockModelGateway(), context)

    _collect(gateway, on_usage=passed_on.append)
    assert checked == ["t"]
    assert len(recorded) == 1 and recorded == passed_on
