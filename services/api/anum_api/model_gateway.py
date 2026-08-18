"""Model gateway adapters (docs/model-gateway.md).

`MockModelGateway` is the Phase 1 deterministic adapter used everywhere by
default - AgentRuntime's callers (main.py, workflows/activities.py) never
construct it directly; they go through `build_model_gateway()`, which
returns Mock unless `settings.model_provider` opts into a real one. This
keeps every existing test's behavior (which all run with the default
"mock" provider) completely unchanged.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any, Protocol

from pydantic import BaseModel

from .settings import settings


class ModelUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    provider: str
    model: str


class ModelResponse(BaseModel):
    text: str
    usage: ModelUsage
    # Call-level audit metadata (docs/model-gateway.md: "record provider,
    # model, prompt metadata, token counts, latency, error class, and
    # estimated cost for each call"). Optional/defaulted so MockModelGateway
    # doesn't need every field to be meaningful.
    latency_ms: float = 0.0


class ModelGateway(Protocol):
    """The interface AgentRuntime actually depends on - see runtime.py."""

    async def generate_text(self, prompt: str) -> ModelResponse: ...


class MockModelGateway:
    provider = "mock"
    model = "anum-mock-planner"

    async def generate_text(self, prompt: str) -> ModelResponse:
        started = time.monotonic()
        words = prompt.split()
        summary = " ".join(words[:18]) if words else "empty task"
        return ModelResponse(
            text=f"Prepared ANUM plan for: {summary}",
            usage=ModelUsage(
                input_tokens=max(1, len(words)),
                output_tokens=12,
                provider=self.provider,
                model=self.model,
            ),
            latency_ms=(time.monotonic() - started) * 1000,
        )


class ModelGatewayError(RuntimeError):
    """A provider call failed in a way the caller should treat as a real error
    (as opposed to MockModelGateway, which cannot fail)."""


class StructuredGenerationError(ModelGatewayError):
    """The provider's response could not be parsed into the requested shape."""


# Anthropic's SDK (and its `anthropic` dependency) is only imported inside
# this class, not at module import time - so `model_gateway.py` stays
# importable (and MockModelGateway usable) even in an environment that
# hasn't installed the `anthropic` extra, matching how every other Phase 2
# integration in this codebase (redis, nats-py, temporalio, boto3) is a
# hard dependency but its *use* is still gated behind an opt-in setting.
class AnthropicModelGateway:
    """The one production provider adapter docs/model-gateway.md's "Now"
    scope asks for. Opt-in via `settings.model_provider = "anthropic"` +
    `settings.anthropic_api_key` - never falls back to an ambient
    `ANTHROPIC_API_KEY` environment variable implicitly, so this can't
    silently pick up credentials meant for something else running in the
    same environment.
    """

    provider = "anthropic"

    def __init__(self, *, api_key: str, model: str, max_tokens: int = 1024) -> None:
        import anthropic

        self.model = model
        self._max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def generate_text(self, prompt: str) -> ModelResponse:
        import anthropic

        started = time.monotonic()
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            raise ModelGatewayError(f"Anthropic API call failed: {exc}") from exc

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        return ModelResponse(
            text=text,
            usage=ModelUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                provider=self.provider,
                model=response.model,
            ),
            latency_ms=(time.monotonic() - started) * 1000,
        )

    async def generate_text_stream(self, prompt: str) -> AsyncIterator[str]:
        """Streaming text generation (docs/model-gateway.md's "Now" scope).

        Not consumed by AgentRuntime yet - the run loop is a single blocking
        step today (see runtime.py), so there's nowhere to stream *to*.
        This exists so a real-time-capable caller (e.g. a future streaming
        endpoint alongside realtime.py's SSE stream) has a working provider
        method to call rather than needing to add one from scratch.
        """

        import anthropic

        try:
            async with self._client.messages.stream(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIError as exc:
            raise ModelGatewayError(f"Anthropic API call failed: {exc}") from exc

    async def generate_structured(self, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        """Structured-output generation (docs/model-gateway.md's "Now" scope).

        Uses Anthropic's tool-calling with a forced tool choice: the model
        is given exactly one "tool" shaped like `json_schema` and required
        to call it, so the response is the validated tool-call input rather
        than free text that needs separate JSON-extraction/repair.
        """

        import anthropic

        tool_name = "emit_structured_output"
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
                tools=[
                    {
                        "name": tool_name,
                        "description": "Emit the structured result for this request.",
                        "input_schema": json_schema,
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
            )
        except anthropic.APIError as exc:
            raise ModelGatewayError(f"Anthropic API call failed: {exc}") from exc

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                return dict(block.input)
        raise StructuredGenerationError(
            "Anthropic response did not include the requested tool call"
        )


def build_model_gateway() -> ModelGateway:
    """The factory every AgentRuntime call site should use instead of
    constructing MockModelGateway/AnthropicModelGateway directly (see
    main.py and workflows/activities.py)."""

    if settings.model_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ModelGatewayError(
                "ANUM_MODEL_PROVIDER=anthropic requires ANUM_ANTHROPIC_API_KEY to be set"
            )
        return AnthropicModelGateway(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    return MockModelGateway()
