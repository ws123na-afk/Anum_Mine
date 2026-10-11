from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any, Protocol, TypeVar

import httpx
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind
from pydantic import BaseModel, Field

from .model_egress import model_http_client
from .settings import ModelPrice, Settings, settings
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
    """One call's usage. ``input_tokens`` is every prompt token the call processed,
    cached ones included; the two cache fields say how many of them were written to or
    read from the provider's prompt cache (Anthropic), which ``estimated_cost_usd``
    prices at the cache rates."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    provider: str
    model: str
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)


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
T = TypeVar("T")

# One chat message as the OpenAI-compatible API takes it: {"role": ..., "content": ...}
# with role "system", "user" or "assistant".
ChatMessage = dict[str, str]
UsageCallback = Callable[[ModelUsage], None]
CHAT_ROLES = frozenset({"system", "user", "assistant"})


class ModelRoute(StrEnum):
    """What a call is for, so a gateway can pick a model (docs/model-gateway.md)."""

    FAST = "fast"  # chat and voice quick replies
    CAPABLE = "capable"  # task planning and agent runs


def route_gateway(gateway: ModelGateway, route: ModelRoute) -> ModelGateway:
    """``gateway`` set to the model configured for ``route``; itself when it has none."""
    select = getattr(gateway, "for_route", None)
    return select(route) if callable(select) else gateway


class ModelStreamTimeout(httpx.TimeoutException):
    """A stream produced no first token, or did not finish, within its time budget."""


class ModelStreamError(RuntimeError):
    """The provider reported an error inside an already-open stream."""


def estimate_tokens(text: str) -> int:
    """Rough token count (about four characters per token) for providers that do not
    report stream usage; budgets are metered with it rather than not at all."""
    return max(1, math.ceil(len(text) / 4)) if text else 0


def validate_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    if not messages:
        raise ValueError("a chat needs at least one message")
    clean: list[ChatMessage] = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role not in CHAT_ROLES or not isinstance(content, str):
            raise ValueError("chat messages need a system, user or assistant role and text content")
        clean.append({"role": role, "content": content})
    return clean

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

    def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        on_usage: UsageCallback | None = None,
    ) -> AsyncIterator[str]: ...


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

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        on_usage: UsageCallback | None = None,
    ) -> AsyncIterator[str]:
        """A deterministic streamed reply that names the last user message."""
        messages = validate_messages(messages)
        question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        words = question.split()
        summary = " ".join(words[:18]) if words else "your message"
        earlier = sum(1 for m in messages if m["role"] == "user") - 1
        reply = f"Mock reply to: {summary}."
        if earlier > 0:
            reply += f" I can see {earlier} earlier question{'s' if earlier != 1 else ''} in this conversation."
        reply += " Connect a model in Settings for real answers."
        for index, word in enumerate(reply.split()):
            yield word if index == 0 else f" {word}"
        if on_usage is not None:
            on_usage(
                ModelUsage(
                    input_tokens=sum(estimate_tokens(m["content"]) for m in messages),
                    output_tokens=estimate_tokens(reply),
                    provider=self.provider,
                    model=self.model,
                    estimated_cost_usd=0,
                )
            )

    async def complete_chat(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None
    ) -> ModelResponse:
        """The streamed mock reply, returned in one piece."""
        parts: list[str] = []
        usage: list[ModelUsage] = []
        async for chunk in self.stream_chat(messages, max_tokens=max_tokens, on_usage=usage.append):
            parts.append(chunk)
        return ModelResponse(
            text="".join(parts),
            usage=usage[-1],
            metadata=ModelCallMetadata(latency_ms=0, finish_reason="stop"),
        )

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
    *,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float | None:
    """``input_tokens`` includes the cached ones; those are priced at the cache write
    (default 1.25x input) and cache read (default 0.1x input) rates instead."""
    if provider in FREE_PROVIDERS:
        return 0.0
    price = find_model_price(model, settings.model_prices if prices is None else prices)
    if price is None:
        return None
    uncached = max(0, input_tokens - cache_creation_input_tokens - cache_read_input_tokens)
    cost = (
        uncached * price.input_per_million
        + cache_creation_input_tokens * price.cache_write_price
        + cache_read_input_tokens * price.cache_read_price
        + output_tokens * price.output_per_million
    ) / 1_000_000
    return round(cost, 8)


@dataclass
class _CallState:
    attempts: int = 0
    http_status: int | None = None


@dataclass
class _StreamState:
    """What a stream reported so far: the model name, usage and stop reason."""

    model: str
    usage: ModelUsage | None = None
    raw_usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None


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
        max_output_tokens: int | None = None,
        first_token_timeout_seconds: float | None = None,
        stream_timeout_seconds: float | None = None,
        clock: Callable[[], float] = perf_counter,
        route_models: Mapping[ModelRoute, str] | None = None,
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
        self.max_output_tokens = max_output_tokens or settings.model_max_output_tokens
        self.first_token_timeout_seconds = (
            first_token_timeout_seconds or settings.model_first_token_timeout_seconds
        )
        self.stream_timeout_seconds = stream_timeout_seconds or settings.model_stream_timeout_seconds
        self._clock = clock
        self.route_models = {ModelRoute(k): v for k, v in (route_models or {}).items() if v}

    def for_route(self, route: ModelRoute) -> OpenAICompatibleGateway:
        """This gateway using ``route``'s model (shares the client and settings)."""
        model = self.route_models.get(ModelRoute(route))
        if not model or model == self.model:
            return self
        routed = copy.copy(self)
        routed.model = model
        return routed

    def _new_client(self) -> httpx.AsyncClient:
        if self.egress_guard:
            return model_http_client(self.timeout_seconds)
        return httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False)

    async def generate_text(self, prompt: str) -> ModelResponse:
        with model_call_span(self.provider, self.model, "generate_text"):
            payload, response, state, started = await self._complete(
                "generate_text",
                {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self.max_output_tokens,
                },
            )
            choice = payload["choices"][0]
            normalized = self._normalize(
                choice["message"]["content"] or "", payload, response, state, started
            )
            self._log_call("generate_text", started, state, "ok", usage=normalized.usage)
            return normalized

    async def complete_chat(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None
    ) -> ModelResponse:
        """A non-streaming chat completion of at most ``max_tokens`` (background work
        such as conversation memory extraction). Same retries, logging and pricing as
        :meth:`generate_text`."""
        messages = validate_messages(messages)
        with model_call_span(self.provider, self.model, "complete_chat"):
            payload, response, state, started = await self._complete(
                "complete_chat",
                {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens or self.max_output_tokens,
                },
            )
            choice = payload["choices"][0]
            normalized = self._normalize(
                (choice.get("message") or {}).get("content") or "", payload, response, state, started
            )
            self._log_call("complete_chat", started, state, "ok", usage=normalized.usage)
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
        async for chunk in self.stream_chat([{"role": "user", "content": prompt}]):
            yield chunk

    async def _within(self, awaitable: Awaitable[T], until: float) -> T:
        remaining = until - self._clock()
        if remaining <= 0:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise ModelStreamTimeout("model stream timed out")
        try:
            return await asyncio.wait_for(awaitable, remaining)
        except TimeoutError as exc:
            raise ModelStreamTimeout("model stream timed out") from exc

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        on_usage: UsageCallback | None = None,
    ) -> AsyncIterator[str]:
        """Stream a chat completion (``stream: true`` server-sent events).

        Each attempt must deliver its first token within ``first_token_timeout_seconds``
        and the whole call (retries included) ends after ``stream_timeout_seconds``.
        Failures before the first token follow the retry policy; once text reached the
        caller nothing is retried, because a retry would repeat it. Closing the iterator
        (the client went away) closes the provider connection, which stops generation.

        ``on_usage`` receives the call's usage once: the provider's own figures when it
        reports them (``stream_options.include_usage``), else an estimate from the text,
        also when the stream fails or is closed after text was produced.
        """
        messages = validate_messages(messages)
        url, body = self._stream_request(messages, max_tokens or self.max_output_tokens)
        client = self._client or self._new_client()
        owns_client = self._client is None
        state = _CallState()
        log_started = perf_counter()
        deadline = self._clock() + self.stream_timeout_seconds
        produced: list[str] = []
        stream = _StreamState(model=self.model)
        finished = False
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
                first_deadline = min(self._clock() + self.first_token_timeout_seconds, deadline)
                response: httpx.Response | None = None
                try:
                    request = client.build_request("POST", url, headers=self._headers, json=body)
                    response = await self._within(client.send(request, stream=True), first_deadline)
                    state.http_status = response.status_code
                    if is_retryable_status(response.status_code):
                        delay = self.retry_policy.delay_before_retry(
                            state.attempts,
                            parse_retry_after(response.headers.get("retry-after")),
                            self._jitter,
                        )
                    if delay is None:
                        response.raise_for_status()
                        lines = response.aiter_lines()
                        while True:
                            line = await self._within(
                                anext(lines, None), deadline if produced else first_deadline
                            )
                            if line is None:
                                break
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            data_text = line[5:].strip()
                            if data_text == "[DONE]":
                                break
                            data = json.loads(data_text)
                            if not isinstance(data, dict):
                                continue
                            chunks, done = self._stream_event(data, stream)
                            for content in chunks:
                                produced.append(content)
                                yield content
                            if done:
                                break
                        finished = True
                        stream.usage = stream.usage or self._estimated_usage(
                            messages, produced, stream.model
                        )
                        self._log_call(
                            "stream_text", log_started, state, "ok", usage=stream.usage, span=span
                        )
                        return
                except _RETRYABLE_EXCEPTIONS as exc:
                    # Once text reached the caller a retry would duplicate it.
                    delay = (
                        None
                        if produced
                        else self.retry_policy.delay_before_retry(state.attempts, None, self._jitter)
                    )
                    if delay is not None and self._clock() + delay >= deadline:
                        delay = None
                    if delay is None:
                        self._log_call("stream_text", log_started, state, "error", error=exc, span=span)
                        raise
                    self._log_retry("stream_text", state, type(exc).__name__, delay)
                    await self._sleep(delay)
                    continue
                except Exception as exc:
                    self._log_call("stream_text", log_started, state, "error", error=exc, span=span)
                    raise
                finally:
                    if response is not None:
                        await response.aclose()
                if self._clock() + delay >= deadline:
                    timeout = ModelStreamTimeout("model stream timed out")
                    self._log_call("stream_text", log_started, state, "error", error=timeout, span=span)
                    raise timeout
                self._log_retry("stream_text", state, f"http_{state.http_status}", delay)
                await self._sleep(delay)
        finally:
            span.end()
            if on_usage is not None and (finished or produced):
                try:
                    on_usage(stream.usage or self._estimated_usage(messages, produced, stream.model))
                except Exception as exc:  # metering never breaks the caller's stream
                    logger.warning("model_stream_usage_failed error_class=%s", type(exc).__name__)
            if owns_client:
                await client.aclose()

    def _stream_request(
        self, messages: list[ChatMessage], max_tokens: int
    ) -> tuple[str, dict[str, object]]:
        """The URL and body of one streaming chat request."""
        return f"{self.base_url}/chat/completions", {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": max_tokens,
        }

    def _stream_event(self, data: dict[str, Any], stream: _StreamState) -> tuple[list[str], bool]:
        """Text chunks in one server-sent event, and whether the stream is complete."""
        if data.get("error"):
            raise ModelStreamError("model provider reported a stream error")
        stream.model = data.get("model") or stream.model
        if data.get("usage"):
            stream.usage = self._stream_usage(data["usage"], stream.model)
        chunks: list[str] = []
        for choice in data.get("choices") or []:
            content = (choice.get("delta") or {}).get("content")
            if content:
                chunks.append(content)
        return chunks, False

    def _stream_usage(self, payload: Mapping[str, Any], model: str) -> ModelUsage:
        input_tokens = int(payload.get("prompt_tokens") or payload.get("input_tokens") or 0)
        output_tokens = int(payload.get("completion_tokens") or payload.get("output_tokens") or 0)
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=self.provider,
            model=model,
            estimated_cost_usd=estimate_cost_usd(
                self.provider, model, input_tokens, output_tokens, self.prices
            ),
        )

    def _estimated_usage(self, messages: list[ChatMessage], produced: list[str], model: str) -> ModelUsage:
        input_tokens = sum(estimate_tokens(m["content"]) for m in messages)
        output_tokens = estimate_tokens("".join(produced))
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=self.provider,
            model=model,
            estimated_cost_usd=estimate_cost_usd(
                self.provider, model, input_tokens, output_tokens, self.prices
            ),
        )

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
            "cache_creation_input_tokens": usage.cache_creation_input_tokens if usage else None,
            "cache_read_input_tokens": usage.cache_read_input_tokens if usage else None,
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
            cache_creation_input_tokens=usage.cache_creation_input_tokens if usage else None,
            cache_read_input_tokens=usage.cache_read_input_tokens if usage else None,
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
            cache_creation_input_tokens=usage.cache_creation_input_tokens if usage else None,
            cache_read_input_tokens=usage.cache_read_input_tokens if usage else None,
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
    route_models: Mapping[ModelRoute, str] | None = None,
    route_efforts: Mapping[ModelRoute, str] | None = None,
) -> ModelGateway:
    """``egress_guard=True`` for endpoints a workspace chose (SSRF guard, model_egress.py).

    ``route_models`` maps :class:`ModelRoute` to a model name (``route_gateway``);
    ``route_efforts`` sets the Anthropic ``output_config.effort`` per route.
    """
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
            route_models=route_models,
        )
    if provider == "anthropic":
        from .anthropic_gateway import (
            ANTHROPIC_CAPABLE_MODEL,
            ANTHROPIC_DEFAULT_BASE_URL,
            AnthropicGateway,
        )

        # The OpenAI defaults (e.g. untouched env defaults) mean "not set" here.
        return AnthropicGateway(
            api_key=api_key or "",
            model=ANTHROPIC_CAPABLE_MODEL if not model or model == OPENAI_DEFAULT_MODEL else model,
            base_url=(
                ANTHROPIC_DEFAULT_BASE_URL
                if not base_url or base_url.rstrip("/") == OPENAI_DEFAULT_BASE_URL
                else base_url
            ),
            client=client,
            egress_guard=egress_guard,
            route_models=route_models,
            effort=(route_efforts or {}).get(ModelRoute.CAPABLE) or None,
            route_efforts=route_efforts,
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
            route_models=route_models,
        )
    raise ValueError(f"Unsupported model provider: {provider}")


def configured_route_models(config: Settings) -> dict[ModelRoute, str]:
    """The fast and capable models the operator configured (docs/model-gateway.md).

    Unset routes use ``ANUM_MODEL_NAME`` as before. With the ``anthropic`` provider and
    ``ANUM_MODEL_NAME`` left at its default, they are claude-haiku-5-5 (fast) and
    claude-sonnet-5-5 (capable).
    """
    fast = config.model_fast_name.strip()
    capable = config.model_capable_name.strip()
    if normalize_provider(config.model_provider) == "anthropic":
        from .anthropic_gateway import ANTHROPIC_CAPABLE_MODEL, ANTHROPIC_FAST_MODEL

        named = config.model_name.strip()
        named = "" if named == OPENAI_DEFAULT_MODEL else named
        fast = fast or named or ANTHROPIC_FAST_MODEL
        capable = capable or named or ANTHROPIC_CAPABLE_MODEL
    return {route: name for route, name in ((ModelRoute.FAST, fast), (ModelRoute.CAPABLE, capable)) if name}


def build_default_model_gateway(config: Settings | None = None) -> ModelGateway:
    """The server's default gateway from ``ANUM_MODEL_*`` settings, with model routing."""
    config = config or settings
    routes = configured_route_models(config)
    return build_model_gateway(
        config.model_provider,
        api_key=config.model_api_key,
        model=routes.get(ModelRoute.CAPABLE) or config.model_name,
        base_url=config.model_base_url,
        route_models=routes,
        route_efforts={
            ModelRoute.FAST: config.model_fast_effort,
            ModelRoute.CAPABLE: config.model_capable_effort,
        },
    )
