from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any, Protocol, TypeVar

import httpx
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind
from pydantic import BaseModel, Field

from .model_egress import model_http_client
from .settings import ModelPrice, settings
from .telemetry import annotate_model_span, model_call_span, telemetry

# Model calls are logged with metadata only (provider, model, latency, attempts, token
# counts, cost, status, error class). Prompts, responses, URLs with credentials, API keys
# and exception messages are never logged: they can carry private tenant data.
logger = logging.getLogger("anum.model_gateway")

FREE_PROVIDERS = frozenset({"mock", "ollama"})
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


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
    attempts: int = Field(default=1, ge=1)


class ModelResponse(BaseModel):
    text: str
    usage: ModelUsage
    metadata: ModelCallMetadata | None = None


class EmbeddingResponse(BaseModel):
    """Vectors for a batch of texts, in input order, plus the call's usage."""

    vectors: list[list[float]]
    model: str
    usage: ModelUsage


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)

# Deterministic local embedder (docs/memory.md#embeddings). Feature hashing of word
# tokens into a fixed number of signed buckets, L2-normalised: no model server, no
# network, same vector for the same text in every process. It finds lexical overlap,
# not meaning, which is what tests and the `local` environment need.
LOCAL_EMBEDDING_MODEL = "anum-local-hash-v1"
LOCAL_EMBEDDING_DIMENSIONS = 256
_TOKEN = re.compile(r"\w+", re.UNICODE)


def local_embedding(text: str, dimensions: int = LOCAL_EMBEDDING_DIMENSIONS) -> list[float]:
    """A unit vector for ``text`` (all zeros when it has no word tokens)."""
    vector = [0.0] * dimensions
    for token in _TOKEN.findall(text.casefold()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        vector[value % dimensions] += 1.0 if (value >> 63) & 1 else -1.0
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector] if norm else vector


def local_embedding_response(texts: list[str]) -> EmbeddingResponse:
    tokens = sum(len(_TOKEN.findall(text)) for text in texts)
    return EmbeddingResponse(
        vectors=[local_embedding(text) for text in texts],
        model=LOCAL_EMBEDDING_MODEL,
        usage=ModelUsage(
            input_tokens=tokens,
            output_tokens=0,
            provider="local",
            model=LOCAL_EMBEDDING_MODEL,
            estimated_cost_usd=0,
        ),
    )


OLLAMA_DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
OPENAI_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
MAX_EMBEDDING_BATCH = 64


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
        response = ModelResponse(
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
        telemetry.record_model_call(
            provider=self.provider,
            model=self.model,
            operation="generate_text",
            status="ok",
            duration_seconds=0.0,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            estimated_cost_usd=0.0,
        )
        return response

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

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResponse:
        """The mock provider embeds with the deterministic local embedder."""
        return local_embedding_response(list(texts))


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retries with exponential backoff and full jitter.

    Retries only timeouts, connection errors, HTTP 429 and HTTP 5xx. A ``Retry-After``
    header sets the minimum wait; when it asks for longer than
    ``max_retry_after_seconds`` the gateway gives up instead of waiting.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    max_retry_after_seconds: float = 30.0

    @classmethod
    def from_settings(cls) -> RetryPolicy:
        return cls(
            max_attempts=settings.model_max_attempts,
            base_delay_seconds=settings.model_retry_base_seconds,
            max_delay_seconds=settings.model_retry_max_seconds,
            max_retry_after_seconds=settings.model_retry_after_max_seconds,
        )

    def backoff(self, attempt: int, jitter: Callable[[], float]) -> float:
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1)))
        return jitter() * ceiling

    def delay_before_retry(
        self, attempt: int, retry_after: float | None, jitter: Callable[[], float]
    ) -> float | None:
        """Seconds to wait before the next attempt, or ``None`` to stop retrying."""
        if attempt >= self.max_attempts:
            return None
        if retry_after is not None and retry_after > self.max_retry_after_seconds:
            return None
        return max(self.backoff(attempt, jitter), retry_after or 0.0)


def is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """``Retry-After`` as seconds (delta-seconds or HTTP-date form)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


def find_model_price(model: str, prices: Mapping[str, ModelPrice]) -> ModelPrice | None:
    """Exact match, else the longest configured name that prefixes ``model`` at a ``-``
    boundary, so dated snapshots (``gpt-4.1-mini-2025-04-14``) use their family price."""
    if model in prices:
        return prices[model]
    matches = [name for name in prices if model.startswith(f"{name}-")]
    return prices[max(matches, key=len)] if matches else None


def estimate_cost_usd(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    prices: Mapping[str, ModelPrice] | None = None,
) -> float | None:
    if provider in FREE_PROVIDERS:
        return 0.0
    price = find_model_price(model, settings.model_prices if prices is None else prices)
    if price is None:
        return None
    cost = (input_tokens * price.input_per_million + output_tokens * price.output_per_million) / 1_000_000
    return round(cost, 8)


@dataclass
class _CallState:
    attempts: int = 0
    http_status: int | None = None


class OpenAICompatibleGateway:
    """Provider adapter for OpenAI-compatible chat-completions APIs."""

    provider = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
        require_api_key: bool = True,
        provider: str | None = None,
        retry_policy: RetryPolicy | None = None,
        prices: Mapping[str, ModelPrice] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        egress_guard: bool = False,
    ) -> None:
        key = (api_key or "").strip()
        if require_api_key and not key:
            raise ValueError("model provider API key is required")
        self.api_key = key or None
        if provider:
            self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.model_timeout_seconds
        self.retry_policy = retry_policy or RetryPolicy.from_settings()
        self.prices = prices
        self._client = client
        self._sleep = sleep
        self._jitter = jitter
        # Workspace-configured endpoints go through the SSRF guard (model_egress.py);
        # the operator's ANUM_MODEL_BASE_URL default is trusted configuration.
        self.egress_guard = egress_guard

    def _new_client(self) -> httpx.AsyncClient:
        if self.egress_guard:
            return model_http_client(self.timeout_seconds)
        return httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False)

    async def generate_text(self, prompt: str) -> ModelResponse:
        with model_call_span(self.provider, self.model, "generate_text"):
            payload, response, state, started = await self._complete(
                "generate_text",
                {"model": self.model, "messages": [{"role": "user", "content": prompt}]},
            )
            choice = payload["choices"][0]
            normalized = self._normalize(
                choice["message"]["content"] or "", payload, response, state, started
            )
            self._log_call("generate_text", started, state, "ok", usage=normalized.usage)
            return normalized

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> tuple[StructuredModel, ModelResponse]:
        schema = response_model.model_json_schema()
        with model_call_span(self.provider, self.model, "generate_structured"):
            payload, response, state, started = await self._complete(
                "generate_structured",
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
                },
            )
            choice = payload["choices"][0]
            text = choice["message"]["content"] or "{}"
            normalized = self._normalize(text, payload, response, state, started)
            self._log_call("generate_structured", started, state, "ok", usage=normalized.usage)
        return response_model.model_validate_json(text), normalized

    async def stream_text(self, prompt: str) -> AsyncIterator[str]:
        client = self._client or self._new_client()
        owns_client = self._client is None
        state = _CallState()
        started = perf_counter()
        yielded = False
        # Not made current: an async generator resumes in its consumer's context, so a
        # current span would leak into (or be detached from) unrelated code.
        span = telemetry.tracer.start_span(
            f"stream_text {self.model}",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.provider.name": self.provider,
                "gen_ai.request.model": self.model,
                "gen_ai.operation.name": "stream_text",
            },
            record_exception=False,
            set_status_on_exception=False,
        )
        try:
            while True:
                state.attempts += 1
                delay: float | None = None
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
                        state.http_status = response.status_code
                        if is_retryable_status(response.status_code):
                            delay = self.retry_policy.delay_before_retry(
                                state.attempts,
                                parse_retry_after(response.headers.get("retry-after")),
                                self._jitter,
                            )
                        if delay is None:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if not line.startswith("data: ") or line == "data: [DONE]":
                                    continue
                                data = json.loads(line[6:])
                                content = data["choices"][0].get("delta", {}).get("content")
                                if content:
                                    yielded = True
                                    yield content
                            self._log_call("stream_text", started, state, "ok", span=span)
                            return
                except _RETRYABLE_EXCEPTIONS as exc:
                    # Once text reached the caller a retry would duplicate it.
                    delay = (
                        None
                        if yielded
                        else self.retry_policy.delay_before_retry(state.attempts, None, self._jitter)
                    )
                    if delay is None:
                        self._log_call(
                            "stream_text", started, state, "error", error=exc, span=span
                        )
                        raise
                    self._log_retry("stream_text", state, type(exc).__name__, delay)
                    await self._sleep(delay)
                    continue
                except Exception as exc:
                    self._log_call("stream_text", started, state, "error", error=exc, span=span)
                    raise
                self._log_retry("stream_text", state, f"http_{state.http_status}", delay)
                await self._sleep(delay)
        finally:
            span.end()
            if owns_client:
                await client.aclose()

    @property
    def default_embedding_model(self) -> str:
        if self.provider == "ollama":
            return OLLAMA_DEFAULT_EMBEDDING_MODEL
        return OPENAI_DEFAULT_EMBEDDING_MODEL

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResponse:
        """``POST /embeddings`` (OpenAI-compatible; Ollama serves it under ``/v1``).

        Same client, retries, SSRF guard, logging and metrics as text generation. The
        caller decides which texts go in one call; every text must belong to the one
        tenant and workspace this gateway was built for.
        """
        texts = list(texts)
        if not texts:
            raise ValueError("embed needs at least one text")
        if len(texts) > MAX_EMBEDDING_BATCH:
            raise ValueError(f"embed takes at most {MAX_EMBEDDING_BATCH} texts per call")
        embedding_model = model or self.default_embedding_model
        state = _CallState()
        started = perf_counter()
        with model_call_span(self.provider, embedding_model, "embed"):
            try:
                response = await self._post(
                    {"model": embedding_model, "input": texts}, state, "embed", "/embeddings"
                )
                payload = response.json()
                items = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(items, list) or len(items) != len(texts):
                    raise ValueError("model provider returned the wrong number of embeddings")
                ordered = sorted(items, key=lambda item: int(item.get("index", 0)))
                vectors = [[float(value) for value in item["embedding"]] for item in ordered]
                if not vectors[0] or any(len(vector) != len(vectors[0]) for vector in vectors):
                    raise ValueError("model provider returned inconsistent embeddings")
            except Exception as exc:
                self._log_call("embed", started, state, "error", error=exc)
                raise
            usage_payload = payload.get("usage") or {}
            input_tokens = int(usage_payload.get("prompt_tokens") or usage_payload.get("total_tokens") or 0)
            reported_model = payload.get("model") or embedding_model
            usage = ModelUsage(
                input_tokens=input_tokens,
                output_tokens=0,
                provider=self.provider,
                model=reported_model,
                estimated_cost_usd=estimate_cost_usd(
                    self.provider, reported_model, input_tokens, 0, self.prices
                ),
            )
            self._log_call("embed", started, state, "ok", usage=usage)
        # The requested name, not the reported one: stored chunks are matched on it.
        return EmbeddingResponse(vectors=vectors, model=embedding_model, usage=usage)

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _complete(
        self, operation: str, request: dict[str, object]
    ) -> tuple[dict[str, Any], httpx.Response, _CallState, float]:
        state = _CallState()
        started = perf_counter()
        try:
            response = await self._post(request, state, operation)
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("choices"):
                raise ValueError("model provider returned no choices")
        except Exception as exc:
            self._log_call(operation, started, state, "error", error=exc)
            raise
        return payload, response, state, started

    async def _post(
        self,
        payload: dict[str, object],
        state: _CallState,
        operation: str,
        path: str = "/chat/completions",
    ) -> httpx.Response:
        client = self._client or self._new_client()
        owns_client = self._client is None
        try:
            while True:
                state.attempts += 1
                try:
                    response = await client.post(
                        f"{self.base_url}{path}",
                        headers=self._headers,
                        json=payload,
                    )
                except _RETRYABLE_EXCEPTIONS as exc:
                    delay = self.retry_policy.delay_before_retry(state.attempts, None, self._jitter)
                    if delay is None:
                        raise
                    self._log_retry(operation, state, type(exc).__name__, delay)
                    await self._sleep(delay)
                    continue
                state.http_status = response.status_code
                if is_retryable_status(response.status_code):
                    delay = self.retry_policy.delay_before_retry(
                        state.attempts,
                        parse_retry_after(response.headers.get("retry-after")),
                        self._jitter,
                    )
                    if delay is not None:
                        await response.aclose()
                        self._log_retry(operation, state, f"http_{response.status_code}", delay)
                        await self._sleep(delay)
                        continue
                response.raise_for_status()
                return response
        finally:
            if owns_client:
                await client.aclose()

    def _normalize(
        self,
        text: str,
        payload: dict[str, Any],
        response: httpx.Response,
        state: _CallState,
        started: float,
    ) -> ModelResponse:
        usage = payload.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        model = payload.get("model") or self.model
        return ModelResponse(
            text=text,
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider=self.provider,
                model=model,
                estimated_cost_usd=estimate_cost_usd(
                    self.provider, model, input_tokens, output_tokens, self.prices
                ),
            ),
            metadata=ModelCallMetadata(
                latency_ms=round((perf_counter() - started) * 1000),
                request_id=response.headers.get("x-request-id"),
                finish_reason=payload["choices"][0].get("finish_reason"),
                attempts=max(1, state.attempts),
            ),
        )

    def _log_retry(self, operation: str, state: _CallState, reason: str, delay: float) -> None:
        logger.warning(
            "model_call_retry provider=%s model=%s operation=%s attempt=%d reason=%s delay_ms=%d",
            self.provider,
            self.model,
            operation,
            state.attempts,
            reason,
            round(delay * 1000),
            extra={
                "anum_model_retry": {
                    "provider": self.provider,
                    "model": self.model,
                    "operation": operation,
                    "attempt": state.attempts,
                    "reason": reason,
                    "delay_ms": round(delay * 1000),
                }
            },
        )

    def _log_call(
        self,
        operation: str,
        started: float,
        state: _CallState,
        status: str,
        *,
        usage: ModelUsage | None = None,
        error: BaseException | None = None,
        span: Span | None = None,
    ) -> None:
        # Only metadata: the error is reduced to its class name because httpx and JSON
        # error messages can embed URLs, headers or response text. The same fields feed
        # the model metrics and the call's span (docs/observability.md).
        fields = {
            "provider": self.provider,
            "model": usage.model if usage else self.model,
            "operation": operation,
            "status": status,
            "attempts": state.attempts,
            "latency_ms": round((perf_counter() - started) * 1000),
            "http_status": state.http_status,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "estimated_cost_usd": usage.estimated_cost_usd if usage else None,
            "error_class": type(error).__name__ if error else None,
        }
        logger.log(
            logging.INFO if status == "ok" else logging.WARNING,
            "model_call " + " ".join(f"{name}=%s" for name in fields),
            *fields.values(),
            extra={"anum_model_call": fields},
        )
        error_type = type(error).__name__ if error else None
        telemetry.record_model_call(
            provider=self.provider,
            model=self.model,
            operation=operation,
            status=status,
            duration_seconds=perf_counter() - started,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            estimated_cost_usd=usage.estimated_cost_usd if usage else None,
            error_type=error_type,
        )
        annotate_model_span(
            span if span is not None else trace.get_current_span(),
            status=status,
            attempts=state.attempts,
            http_status=state.http_status,
            response_model=usage.model if usage else None,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            estimated_cost_usd=usage.estimated_cost_usd if usage else None,
            error_type=error_type,
        )


OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"
OLLAMA_DEFAULT_MODEL = "llama3.2"
OLLAMA_MIN_TIMEOUT_SECONDS = 120.0
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-4.1-mini"


def normalize_provider(provider: str) -> str:
    """Accept both spellings used across clients (``openai_compatible`` / ``openai-compatible``)."""
    value = provider.strip().lower()
    return "openai-compatible" if value == "openai_compatible" else value


def build_model_gateway(
    provider: str,
    *,
    api_key: str | None = None,
    model: str = OPENAI_DEFAULT_MODEL,
    base_url: str = OPENAI_DEFAULT_BASE_URL,
    client: httpx.AsyncClient | None = None,
    egress_guard: bool = False,
) -> ModelGateway:
    """``egress_guard=True`` for endpoints a workspace chose (SSRF guard, model_egress.py)."""
    provider = normalize_provider(provider)
    if provider == "mock":
        return MockModelGateway()
    if provider == "openai-compatible":
        return OpenAICompatibleGateway(
            api_key=api_key or "",
            model=model,
            base_url=base_url,
            client=client,
            egress_guard=egress_guard,
        )
    if provider == "ollama":
        # Ollama serves an OpenAI-compatible API locally and needs no key. Fall back
        # to Ollama defaults when the caller passed the OpenAI ones (e.g. env defaults).
        # Local models are slow, so the timeout is never below 120 seconds.
        return OpenAICompatibleGateway(
            api_key=api_key,
            model=OLLAMA_DEFAULT_MODEL if not model or model == OPENAI_DEFAULT_MODEL else model,
            base_url=(
                OLLAMA_DEFAULT_BASE_URL
                if not base_url or base_url.rstrip("/") == OPENAI_DEFAULT_BASE_URL
                else base_url
            ),
            timeout_seconds=max(settings.model_timeout_seconds, OLLAMA_MIN_TIMEOUT_SECONDS),
            client=client,
            require_api_key=False,
            provider="ollama",
            egress_guard=egress_guard,
        )
    raise ValueError(f"Unsupported model provider: {provider}")
