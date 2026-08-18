"""Real tests for AnthropicModelGateway against a mocked HTTP transport -
same legitimate pattern as rest_integration.py's tests: no live API key is
available in this environment, so httpx.MockTransport gives full-fidelity
request/response serialization without a network dependency. This proves
the adapter's request shape and response parsing are correct, not just
that the code compiles.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from anum_api.model_gateway import (
    AnthropicModelGateway,
    ModelGatewayError,
    StructuredGenerationError,
    build_model_gateway,
)
from anum_api.settings import settings


def _gateway_with_transport(handler) -> AnthropicModelGateway:
    gateway = AnthropicModelGateway(api_key="test-key", model="claude-sonnet-5")
    # Swap the SDK client's underlying httpx transport for a mock one -
    # everything above the transport (request building, retries, response
    # parsing) is the real anthropic SDK, exercised for real.
    gateway._client = gateway._client.with_options(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return gateway


def _text_response(text: str, *, input_tokens: int = 10, output_tokens: int = 5) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )


@pytest.mark.asyncio
async def test_generate_text_sends_expected_request_and_parses_response() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return _text_response("Here is the plan.", input_tokens=12, output_tokens=6)

    gateway = _gateway_with_transport(handler)
    response = await gateway.generate_text("Plan the migration")

    assert response.text == "Here is the plan."
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 6
    assert response.usage.provider == "anthropic"
    assert response.usage.model == "claude-sonnet-5"
    assert response.latency_ms >= 0

    assert len(captured_requests) == 1
    import json

    body = json.loads(captured_requests[0].content)
    assert body["model"] == "claude-sonnet-5"
    assert body["messages"] == [{"role": "user", "content": "Plan the migration"}]


@pytest.mark.asyncio
async def test_generate_text_wraps_api_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"type": "api_error", "message": "boom"}})

    gateway = _gateway_with_transport(handler)
    with pytest.raises(ModelGatewayError):
        await gateway.generate_text("anything")


@pytest.mark.asyncio
async def test_generate_structured_uses_forced_tool_choice_and_returns_input() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "emit_structured_output",
                        "input": {"steps": ["one", "two"]},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 8, "output_tokens": 4},
            },
        )

    gateway = _gateway_with_transport(handler)
    schema = {
        "type": "object",
        "properties": {"steps": {"type": "array", "items": {"type": "string"}}},
        "required": ["steps"],
    }
    result = await gateway.generate_structured("Break this into steps", schema)

    assert result == {"steps": ["one", "two"]}

    import json

    body = json.loads(captured_requests[0].content)
    assert body["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}
    assert body["tools"][0]["input_schema"] == schema


@pytest.mark.asyncio
async def test_generate_structured_raises_when_model_does_not_call_the_tool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response("I refuse to use tools.")

    gateway = _gateway_with_transport(handler)
    with pytest.raises(StructuredGenerationError):
        await gateway.generate_structured("anything", {"type": "object"})


@pytest.mark.asyncio
async def test_generate_text_stream_yields_text_deltas() -> None:
    gateway = AnthropicModelGateway(api_key="test-key", model="claude-sonnet-5")

    class _FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        @property
        def text_stream(self):
            return self._iter_chunks()

        async def _iter_chunks(self):
            for chunk in ["Hello", ", ", "world"]:
                yield chunk

    gateway._client = MagicMock()
    gateway._client.messages.stream = MagicMock(return_value=_FakeStream())

    chunks = [chunk async for chunk in gateway.generate_text_stream("hi")]
    assert chunks == ["Hello", ", ", "world"]


def test_build_model_gateway_defaults_to_mock() -> None:
    assert settings.model_provider == "mock"
    gateway = build_model_gateway()
    assert gateway.__class__.__name__ == "MockModelGateway"


def test_build_model_gateway_requires_api_key_for_anthropic() -> None:
    original_provider = settings.model_provider
    original_key = settings.anthropic_api_key
    settings.model_provider = "anthropic"
    settings.anthropic_api_key = None
    try:
        with pytest.raises(ModelGatewayError):
            build_model_gateway()
    finally:
        settings.model_provider = original_provider
        settings.anthropic_api_key = original_key


def test_build_model_gateway_returns_anthropic_gateway_when_configured() -> None:
    original_provider = settings.model_provider
    original_key = settings.anthropic_api_key
    settings.model_provider = "anthropic"
    settings.anthropic_api_key = "test-key"
    try:
        gateway = build_model_gateway()
        assert isinstance(gateway, AnthropicModelGateway)
    finally:
        settings.model_provider = original_provider
        settings.anthropic_api_key = original_key
