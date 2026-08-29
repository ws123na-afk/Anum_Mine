from __future__ import annotations

import json
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, Field


class ModelUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    provider: str
    model: str
    estimated_cost_usd: float | None = Field(default=None, ge=0)


class ModelCallMetadata(BaseModel):
    latency_ms: int = Field(ge=0)
    request_id: str | None = None
    finish_reason: str | None = None


class ModelResponse(BaseModel):
    text: str
    usage: ModelUsage
    metadata: ModelCallMetadata | None = None


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class ModelGateway(Protocol):
    async def generate_text(self, prompt: str) -> ModelResponse: ...

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> tuple[StructuredModel, ModelResponse]: ...

    def stream_text(self, prompt: str) -> AsyncIterator[str]: ...


class MockModelGateway:
    provider = "mock"
    model = "anum-mock-planner"

    async def generate_text(self, prompt: str) -> ModelResponse:
        words = prompt.split()
        summary = " ".join(words[:18]) if words else "empty task"
        return ModelResponse(
            text=f"Prepared ANUM plan for: {summary}",
            usage=ModelUsage(
                input_tokens=max(1, len(words)),
                output_tokens=12,
                provider=self.provider,
                model=self.model,
                estimated_cost_usd=0,
            ),
            metadata=ModelCallMetadata(latency_ms=0, finish_reason="stop"),
        )

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> tuple[StructuredModel, ModelResponse]:
        response = await self.generate_text(prompt)
        return response_model.model_validate_json(prompt), response

    async def stream_text(self, prompt: str) -> AsyncIterator[str]:
        response = await self.generate_text(prompt)
        for word in response.text.split():
            yield f"{word} "


class OpenAICompatibleGateway:
    """Provider adapter for OpenAI-compatible chat-completions APIs."""

    provider = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("model provider API key is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def generate_text(self, prompt: str) -> ModelResponse:
        started = perf_counter()
        response = await self._post(
            {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        )
        payload = response.json()
        choice = payload["choices"][0]
        usage = payload.get("usage", {})
        return ModelResponse(
            text=choice["message"]["content"] or "",
            usage=ModelUsage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                provider=self.provider,
                model=payload.get("model", self.model),
            ),
            metadata=ModelCallMetadata(
                latency_ms=round((perf_counter() - started) * 1000),
                request_id=response.headers.get("x-request-id"),
                finish_reason=choice.get("finish_reason"),
            ),
        )

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> tuple[StructuredModel, ModelResponse]:
        schema = response_model.model_json_schema()
        started = perf_counter()
        response = await self._post(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_model.__name__,
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
        )
        payload = response.json()
        choice = payload["choices"][0]
        text = choice["message"]["content"] or "{}"
        usage = payload.get("usage", {})
        normalized = ModelResponse(
            text=text,
            usage=ModelUsage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                provider=self.provider,
                model=payload.get("model", self.model),
            ),
            metadata=ModelCallMetadata(
                latency_ms=round((perf_counter() - started) * 1000),
                request_id=response.headers.get("x-request-id"),
                finish_reason=choice.get("finish_reason"),
            ),
        )
        return response_model.model_validate_json(text), normalized

    async def stream_text(self, prompt: str) -> AsyncIterator[str]:
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        owns_client = self._client is None
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    data = json.loads(line[6:])
                    content = data["choices"][0].get("delta", {}).get("content")
                    if content:
                        yield content
        finally:
            if owns_client:
                await client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}", "content-type": "application/json"}

    async def _post(self, payload: dict[str, object]) -> httpx.Response:
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        owns_client = self._client is None
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
            return response
        finally:
            if owns_client:
                await client.aclose()


def build_model_gateway(
    provider: str,
    *,
    api_key: str | None = None,
    model: str = "gpt-4.1-mini",
    base_url: str = "https://api.openai.com/v1",
) -> ModelGateway:
    if provider == "mock":
        return MockModelGateway()
    if provider == "openai-compatible":
        return OpenAICompatibleGateway(api_key=api_key or "", model=model, base_url=base_url)
    raise ValueError(f"Unsupported model provider: {provider}")
