"""Native Anthropic Messages API adapter (docs/model-gateway.md#anthropic-messages-api).

Same contract as :class:`~anum_api.model_gateway.OpenAICompatibleGateway` (timeouts,
bounded retries, the output cap, stream time budgets, no retry after the first token,
redacted logging, metrics and budget metering) but speaks ``POST /v1/messages`` with
plain httpx, so it can use prompt caching:

* the tool definitions (when a gateway is given any), the system prompt and the
  conversation so far are marked with ``cache_control: {"type": "ephemeral"}``
  breakpoints (at most three of the four the API allows), so repeated prefixes are
  read from the provider's cache instead of being billed as new input;
* ``cache_creation_input_tokens`` and ``cache_read_input_tokens`` are recorded on
  :class:`~anum_api.model_gateway.ModelUsage` and priced at the cache write and read
  rates, which is what the monthly budgets add up.

Anthropic has no embeddings endpoint: ``embed`` uses the deterministic local embedder.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from time import perf_counter
from typing import Any

import httpx

from .model_gateway import (
    LOCAL_EMBEDDING_MODEL,
    ChatMessage,
    EmbeddingResponse,
    ModelCallMetadata,
    ModelResponse,
    ModelRoute,
    ModelStreamError,
    ModelUsage,
    OpenAICompatibleGateway,
    StructuredModel,
    _CallState,
    _StreamState,
    estimate_cost_usd,
    local_embedding_response,
    validate_messages,
)
from .telemetry import model_call_span

ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_FAST_MODEL = "claude-haiku-5-5"
ANTHROPIC_CAPABLE_MODEL = "claude-sonnet-5-5"
# The Messages API accepts at most four cache breakpoints per request.
MAX_CACHE_BREAKPOINTS = 4
# Structured (JSON) calls are not capped by ANUM_MODEL_MAX_OUTPUT_TOKENS (a cut-off plan
# is invalid JSON), but the Messages API needs a max_tokens: this generous ceiling.
STRUCTURED_MAX_TOKENS = 16000
EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_CACHE = {"type": "ephemeral"}
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def anthropic_base_url(base_url: str | None) -> str:
    """The API root without ``/v1`` (accepts the OpenAI-compatible ``.../v1`` form)."""
    url = (base_url or ANTHROPIC_DEFAULT_BASE_URL).rstrip("/")
    return url[: -len("/v1")] if url.endswith("/v1") else url


def _text_block(text: str, *, cache: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = dict(_CACHE)
    return block


def build_messages_request(
    messages: list[ChatMessage],
    *,
    model: str,
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    effort: str | None = None,
    cache: bool = True,
    cache_messages: bool = True,
) -> dict[str, Any]:
    """A Messages API body for an OpenAI-style message list.

    System messages become the top-level ``system`` blocks; the rest keep their order.
    With ``cache`` the stable prefix gets breakpoints in render order (tools, then
    system, then messages): the last tool definition, the last system block and the
    last message. Each request therefore reads the previous turn's cached prefix and
    writes its own for the next turn. A prefix below the model's minimum cacheable
    length is simply not cached (no error, no write charge). ``cache_messages=False``
    keeps the tools and system breakpoints only: a one-off message after a stable
    system prompt (planning) would be written to the cache and never read.
    """
    messages = validate_messages(messages)
    system = [m["content"] for m in messages if m["role"] == "system" and m["content"]]
    turns = [m for m in messages if m["role"] != "system"]
    if not turns:
        raise ValueError("a chat needs at least one user or assistant message")
    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
    breakpoints = 0
    if tools:
        body["tools"] = [dict(tool) for tool in tools]
        if cache:
            body["tools"][-1]["cache_control"] = dict(_CACHE)
            breakpoints += 1
    if system:
        body["system"] = [
            _text_block(text, cache=cache and index == len(system) - 1)
            for index, text in enumerate(system)
        ]
        breakpoints += 1 if cache else 0
    body["messages"] = [{"role": m["role"], "content": m["content"]} for m in turns]
    last = body["messages"][-1]
    if cache and cache_messages and last["content"] and breakpoints < MAX_CACHE_BREAKPOINTS:
        last["content"] = [_text_block(last["content"], cache=True)]
    if effort:
        if effort not in EFFORTS:
            raise ValueError(f"unsupported effort: {effort}")
        body["output_config"] = {"effort": effort}
    return body


def count_cache_breakpoints(body: Mapping[str, Any]) -> int:
    """How many ``cache_control`` markers a Messages API body carries."""
    count = sum(1 for tool in body.get("tools") or [] if "cache_control" in tool)
    count += sum(1 for block in body.get("system") or [] if "cache_control" in block)
    for message in body.get("messages") or []:
        if isinstance(message.get("content"), list):
            count += sum(1 for block in message["content"] if "cache_control" in block)
    return count


class AnthropicGateway(OpenAICompatibleGateway):
    """Provider adapter for the native Anthropic Messages API (``provider="anthropic"``)."""

    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = ANTHROPIC_CAPABLE_MODEL,
        base_url: str = ANTHROPIC_DEFAULT_BASE_URL,
        tools: list[dict[str, Any]] | None = None,
        effort: str | None = None,
        route_efforts: Mapping[ModelRoute, str] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.pop("provider", None)
        kwargs.pop("require_api_key", None)
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=anthropic_base_url(base_url),
            provider="anthropic",
            require_api_key=True,
            **kwargs,
        )
        # Stable tool definitions sent with every call and cached as the first prefix.
        self.tools = [dict(tool) for tool in tools] if tools else None
        self.effort = effort or None
        self.route_efforts = {ModelRoute(k): v for k, v in (route_efforts or {}).items()}

    def for_route(self, route: ModelRoute) -> AnthropicGateway:
        route = ModelRoute(route)
        model = self.route_models.get(route) or self.model
        effort = self.route_efforts.get(route, self.effort) or None
        if model == self.model and effort == self.effort:
            return self
        routed = copy.copy(self)
        routed.model = model
        routed.effort = effort
        return routed

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    @property
    def default_embedding_model(self) -> str:
        return LOCAL_EMBEDDING_MODEL

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResponse:
        """Anthropic serves no embeddings: the deterministic local embedder, in process."""
        if model and model != LOCAL_EMBEDDING_MODEL:
            raise ValueError(
                "the anthropic provider has no embeddings endpoint; leave ANUM_EMBEDDING_MODEL empty"
            )
        return local_embedding_response(list(texts))

    # -- requests ------------------------------------------------------------------

    def _request(
        self,
        messages: list[ChatMessage],
        max_tokens: int,
        *,
        cache: bool,
        cache_messages: bool = True,
        output_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = build_messages_request(
            messages,
            model=self.model,
            max_tokens=max_tokens,
            tools=self.tools,
            effort=self.effort,
            cache=cache,
            cache_messages=cache_messages,
        )
        if output_format is not None:
            body.setdefault("output_config", {})["format"] = output_format
        return body

    def _stream_request(
        self, messages: list[ChatMessage], max_tokens: int
    ) -> tuple[str, dict[str, object]]:
        body = self._request(messages, max_tokens, cache=True)
        body["stream"] = True
        return f"{self.base_url}/v1/messages", body

    async def generate_text(self, prompt: str) -> ModelResponse:
        # A one-off prompt is not cached unless tools form a stable prefix: a cache write
        # costs 1.25x and nothing would read it.
        body = self._request(
            [{"role": "user", "content": prompt}], self.max_output_tokens, cache=bool(self.tools)
        )
        with model_call_span(self.provider, self.model, "generate_text"):
            return await self._call("generate_text", body)

    # The planner sends its stable instructions through generate_with_instructions.
    caches_instructions = True

    async def generate_with_instructions(self, instructions: str, prompt: str) -> ModelResponse:
        """A one-off prompt after stable instructions (planning with retrieved context).

        The instructions become the system prompt with a cache breakpoint (after the
        tools, when there are any); the prompt itself is not cached, since nothing would
        read it again. A prefix below the model's minimum cacheable length is simply not
        cached.
        """
        body = self._request(
            [{"role": "system", "content": instructions}, {"role": "user", "content": prompt}],
            self.max_output_tokens,
            cache=True,
            cache_messages=False,
        )
        with model_call_span(self.provider, self.model, "generate_text"):
            return await self._call("generate_text", body)

    async def complete_chat(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None
    ) -> ModelResponse:
        """A non-streaming Messages API call (background work such as conversation memory
        extraction). Not cached: it runs once per reply and nothing would read the cache."""
        body = self._request(
            validate_messages(messages), max_tokens or self.max_output_tokens, cache=False
        )
        with model_call_span(self.provider, self.model, "complete_chat"):
            return await self._call("complete_chat", body)

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
    ) -> tuple[StructuredModel, ModelResponse]:
        body = self._request(
            [{"role": "user", "content": prompt}],
            STRUCTURED_MAX_TOKENS,
            cache=bool(self.tools),
            output_format={"type": "json_schema", "schema": response_model.model_json_schema()},
        )
        with model_call_span(self.provider, self.model, "generate_structured"):
            response = await self._call("generate_structured", body)
        return response_model.model_validate_json(response.text or "{}"), response

    async def _call(self, operation: str, body: dict[str, Any]) -> ModelResponse:
        state = _CallState()
        started = perf_counter()
        try:
            response = await self._post(body, state, operation, "/v1/messages")
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
                raise ValueError("model provider returned no message content")
        except Exception as exc:
            self._log_call(operation, started, state, "error", error=exc)
            raise
        normalized = self._normalize_message(payload, response, state, started)
        self._log_call(operation, started, state, "ok", usage=normalized.usage)
        return normalized

    # -- responses -----------------------------------------------------------------

    def _usage(self, raw: Mapping[str, Any], model: str) -> ModelUsage:
        """``ModelUsage`` from Anthropic's figures, whose ``input_tokens`` excludes the
        cached tokens: ANUM's ``input_tokens`` is all three input counts together."""
        values = {name: max(0, int(raw.get(name) or 0)) for name in _USAGE_FIELDS}
        created = values["cache_creation_input_tokens"]
        read = values["cache_read_input_tokens"]
        input_tokens = values["input_tokens"] + created + read
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=values["output_tokens"],
            provider=self.provider,
            model=model,
            cache_creation_input_tokens=created,
            cache_read_input_tokens=read,
            estimated_cost_usd=estimate_cost_usd(
                self.provider,
                model,
                input_tokens,
                values["output_tokens"],
                self.prices,
                cache_creation_input_tokens=created,
                cache_read_input_tokens=read,
            ),
        )

    def _normalize_message(
        self,
        payload: dict[str, Any],
        response: httpx.Response,
        state: _CallState,
        started: float,
    ) -> ModelResponse:
        text = "".join(
            block.get("text") or ""
            for block in payload["content"]
            if isinstance(block, dict) and block.get("type") == "text"
        )
        model = payload.get("model") or self.model
        return ModelResponse(
            text=text,
            usage=self._usage(payload.get("usage") or {}, model),
            metadata=ModelCallMetadata(
                latency_ms=round((perf_counter() - started) * 1000),
                request_id=response.headers.get("request-id") or payload.get("id"),
                finish_reason=payload.get("stop_reason"),
                attempts=max(1, state.attempts),
            ),
        )

    def _stream_event(self, data: dict[str, Any], stream: _StreamState) -> tuple[list[str], bool]:
        """One Messages API stream event (``message_start``, ``content_block_delta``,
        ``message_delta``, ``message_stop``, ``ping``, ``error``)."""
        kind = data.get("type")
        if kind == "error" or data.get("error"):
            raise ModelStreamError("model provider reported a stream error")
        if kind == "message_start":
            message = data.get("message") or {}
            stream.model = message.get("model") or stream.model
            self._merge_usage(stream, message.get("usage"))
            return [], False
        if kind == "content_block_delta":
            delta = data.get("delta") or {}
            text = delta.get("text") if delta.get("type") == "text_delta" else None
            return ([text] if text else []), False
        if kind == "message_delta":
            stream.finish_reason = (data.get("delta") or {}).get("stop_reason") or stream.finish_reason
            self._merge_usage(stream, data.get("usage"))
            # The final usage: message_delta's counts are cumulative for the message.
            stream.usage = self._usage(stream.raw_usage, stream.model)
            return [], False
        if kind == "message_stop":
            return [], True
        return [], False  # ping, content_block_start/stop, thinking deltas

    @staticmethod
    def _merge_usage(stream: _StreamState, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for name in _USAGE_FIELDS:
            value = usage.get(name)
            if isinstance(value, int):
                stream.raw_usage[name] = value
