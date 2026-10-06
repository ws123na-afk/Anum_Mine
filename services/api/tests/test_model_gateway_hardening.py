"""Retries, cost accounting and redacted logging for the model gateway."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

import httpx
import pytest

from anum_api.model_gateway import (
    OpenAICompatibleGateway,
    RetryPolicy,
    build_model_gateway,
    estimate_cost_usd,
    find_model_price,
    parse_retry_after,
)
from anum_api.settings import ModelPrice, settings

API_KEY = "sk-test-SUPERSECRET-9876"
PROMPT = "Confidential: Q3 acquisition of Globex for 42 million"
ANSWER = "Private answer about the Globex deal"


def _reply(content: str = ANSWER, *, model: str = "gpt-4.1-mini", usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "req-1"},
        json={
            "model": model,
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage if usage is not None else {"prompt_tokens": 1000, "completion_tokens": 500},
        },
    )


def _gateway(
    responses: list[Callable[[httpx.Request], httpx.Response]],
    *,
    policy: RetryPolicy | None = None,
    sleeps: list[float] | None = None,
    **kwargs,
) -> tuple[OpenAICompatibleGateway, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        step = responses[min(len(seen), len(responses)) - 1]
        return step(request)

    async def fake_sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    gateway = OpenAICompatibleGateway(
        api_key=API_KEY,
        model="gpt-4.1-mini",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_policy=policy or RetryPolicy(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=8),
        sleep=fake_sleep,
        jitter=lambda: 1.0,
        **kwargs,
    )
    return gateway, seen


def _status(code: int, headers: dict[str, str] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(code, headers=headers or {}, json={"error": "nope"})


def _raise(exc_type: type[Exception]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("transport failure", request=request)

    return handler


# --- retries -----------------------------------------------------------------


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_retryable_statuses_are_retried_with_exponential_backoff(code: int) -> None:
    sleeps: list[float] = []
    gateway, seen = _gateway([_status(code), _status(code), lambda _: _reply()], sleeps=sleeps)

    response = asyncio.run(gateway.generate_text(PROMPT))

    assert response.text == ANSWER
    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]
    assert response.metadata is not None and response.metadata.attempts == 3


@pytest.mark.parametrize("exc_type", [httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError])
def test_timeouts_and_connection_errors_are_retried(exc_type: type[Exception]) -> None:
    sleeps: list[float] = []
    gateway, seen = _gateway([_raise(exc_type), lambda _: _reply()], sleeps=sleeps)

    response = asyncio.run(gateway.generate_text(PROMPT))

    assert response.text == ANSWER
    assert len(seen) == 2
    assert sleeps == [0.5]


def test_retries_are_bounded_and_the_last_error_is_raised() -> None:
    sleeps: list[float] = []
    gateway, seen = _gateway([_raise(httpx.ConnectError)], sleeps=sleeps)

    with pytest.raises(httpx.ConnectError):
        asyncio.run(gateway.generate_text(PROMPT))

    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def test_exhausted_5xx_raises_http_status_error() -> None:
    gateway, seen = _gateway([_status(503)], sleeps=[])

    with pytest.raises(httpx.HTTPStatusError) as raised:
        asyncio.run(gateway.generate_text(PROMPT))

    assert raised.value.response.status_code == 503
    assert len(seen) == 3


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422])
def test_other_4xx_are_not_retried(code: int) -> None:
    sleeps: list[float] = []
    gateway, seen = _gateway([_status(code), lambda _: _reply()], sleeps=sleeps)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(gateway.generate_text(PROMPT))

    assert len(seen) == 1
    assert sleeps == []


def test_retry_after_seconds_sets_the_minimum_wait() -> None:
    sleeps: list[float] = []
    gateway, seen = _gateway([_status(429, {"retry-after": "7"}), lambda _: _reply()], sleeps=sleeps)

    asyncio.run(gateway.generate_text(PROMPT))

    assert len(seen) == 2
    assert sleeps == [7.0]


def test_retry_after_longer_than_the_cap_gives_up_instead_of_waiting() -> None:
    sleeps: list[float] = []
    gateway, seen = _gateway(
        [_status(429, {"retry-after": "120"}), lambda _: _reply()],
        sleeps=sleeps,
        policy=RetryPolicy(max_attempts=3, max_retry_after_seconds=30),
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(gateway.generate_text(PROMPT))

    assert len(seen) == 1
    assert sleeps == []


def test_backoff_uses_full_jitter_and_is_capped() -> None:
    policy = RetryPolicy(max_attempts=10, base_delay_seconds=1, max_delay_seconds=4)

    assert policy.backoff(1, lambda: 1.0) == 1
    assert policy.backoff(3, lambda: 1.0) == 4
    assert policy.backoff(8, lambda: 1.0) == 4
    assert policy.backoff(3, lambda: 0.25) == 1
    assert policy.delay_before_retry(10, None, lambda: 1.0) is None


def test_parse_retry_after_accepts_seconds_and_http_dates() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("1.5") == 1.5
    assert parse_retry_after("Thu, 01 Jan 2026 12:00:10 GMT", now=now) == 10.0
    assert parse_retry_after("Thu, 01 Jan 2026 11:00:00 GMT", now=now) == 0.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_max_attempts_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_max_attempts", 5)
    monkeypatch.setattr(settings, "model_retry_base_seconds", 0)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503)

    gateway = build_model_gateway(
        "openai-compatible",
        api_key=API_KEY,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(gateway.generate_text(PROMPT))

    assert len(seen) == 5


def test_stream_is_retried_before_the_first_chunk() -> None:
    sleeps: list[float] = []
    stream_body = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    gateway, seen = _gateway(
        [_status(503), lambda _: httpx.Response(200, text=stream_body)],
        sleeps=sleeps,
    )

    async def collect() -> list[str]:
        return [chunk async for chunk in gateway.stream_text(PROMPT)]

    assert asyncio.run(collect()) == ["Hel", "lo"]
    assert len(seen) == 2
    assert sleeps == [0.5]


def test_stream_does_not_retry_non_retryable_status() -> None:
    gateway, seen = _gateway([_status(401)], sleeps=[])

    async def collect() -> list[str]:
        return [chunk async for chunk in gateway.stream_text(PROMPT)]

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(collect())
    assert len(seen) == 1


# --- cost accounting ---------------------------------------------------------


def test_usage_and_estimated_cost_are_read_from_the_response() -> None:
    gateway, _ = _gateway([lambda _: _reply()])

    response = asyncio.run(gateway.generate_text(PROMPT))

    assert response.usage.input_tokens == 1000
    assert response.usage.output_tokens == 500
    # gpt-4.1-mini default price: $0.40 / 1M input, $1.60 / 1M output.
    assert response.usage.estimated_cost_usd == pytest.approx(0.0012)


def test_dated_model_snapshots_use_their_family_price() -> None:
    gateway, _ = _gateway([lambda _: _reply(model="gpt-4.1-mini-2025-04-14")])

    response = asyncio.run(gateway.generate_text(PROMPT))

    assert response.usage.model == "gpt-4.1-mini-2025-04-14"
    assert response.usage.estimated_cost_usd == pytest.approx(0.0012)


def test_price_lookup_prefers_the_longest_family_and_needs_a_boundary() -> None:
    prices = {
        "gpt-4o": ModelPrice(input_per_million=2.5, output_per_million=10),
        "gpt-4o-mini": ModelPrice(input_per_million=0.15, output_per_million=0.6),
    }

    assert find_model_price("gpt-4o-mini-2024-07-18", prices) == prices["gpt-4o-mini"]
    assert find_model_price("gpt-4o-2024-08-06", prices) == prices["gpt-4o"]
    assert find_model_price("gpt-4omega", prices) is None


def test_unknown_hosted_models_report_no_cost_estimate() -> None:
    gateway, _ = _gateway([lambda _: _reply(model="some-private-model")])

    response = asyncio.run(gateway.generate_text(PROMPT))

    assert response.usage.estimated_cost_usd is None


def test_custom_price_table_is_used() -> None:
    prices = {"house-model": ModelPrice(input_per_million=10, output_per_million=20)}
    gateway, _ = _gateway([lambda _: _reply(model="house-model")], prices=prices)

    response = asyncio.run(gateway.generate_text(PROMPT))

    assert response.usage.estimated_cost_usd == pytest.approx(0.02)


def test_price_table_setting_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from anum_api.settings import Settings

    monkeypatch.setenv("ANUM_MODEL_PRICES", '{"house-model": {"input_per_million": 1, "output_per_million": 2}}')

    configured = Settings()

    assert configured.model_prices == {"house-model": ModelPrice(input_per_million=1, output_per_million=2)}


def test_ollama_and_mock_calls_cost_nothing() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _reply(model="llama3.2")

    gateway = build_model_gateway(
        "ollama",
        model="llama3.2",
        base_url="http://localhost:11434/v1",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert asyncio.run(gateway.generate_text(PROMPT)).usage.estimated_cost_usd == 0
    assert asyncio.run(build_model_gateway("mock").generate_text(PROMPT)).usage.estimated_cost_usd == 0
    assert estimate_cost_usd("ollama", "gpt-4.1", 10**6, 10**6) == 0


def test_missing_usage_counts_as_zero_tokens() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"model": "gpt-4.1-mini", "choices": [{"message": {"content": "ok"}}], "usage": None},
        )

    gateway, _ = _gateway([handler])

    response = asyncio.run(gateway.generate_text(PROMPT))

    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.usage.estimated_cost_usd == 0


# --- redacted logging --------------------------------------------------------


@pytest.fixture
def gateway_logs(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> pytest.LogCaptureFixture:
    # Alembic's fileConfig can disable existing loggers when migrations ran earlier
    # in the same session; make sure the gateway logger is live for these tests.
    monkeypatch.setattr(logging.getLogger("anum.model_gateway"), "disabled", False)
    caplog.set_level(logging.DEBUG)
    return caplog


def _all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(repr(record.args))
        parts.append(repr(record.__dict__))
    return "\n".join(parts)


def test_successful_calls_log_metadata_but_never_prompt_response_or_key(gateway_logs: pytest.LogCaptureFixture) -> None:
    sleeps: list[float] = []
    gateway, _ = _gateway([_status(503), lambda _: _reply()], sleeps=sleeps)

    asyncio.run(gateway.generate_text(PROMPT))

    text = _all_log_text(gateway_logs)
    assert PROMPT not in text and "Globex" not in text
    assert ANSWER not in text
    assert API_KEY not in text and "SUPERSECRET" not in text

    calls = [r for r in gateway_logs.records if hasattr(r, "anum_model_call")]
    assert len(calls) == 1
    fields = calls[0].anum_model_call
    assert fields["provider"] == "openai-compatible"
    assert fields["model"] == "gpt-4.1-mini"
    assert fields["status"] == "ok"
    assert fields["attempts"] == 2
    assert fields["input_tokens"] == 1000
    assert fields["output_tokens"] == 500
    assert fields["estimated_cost_usd"] == pytest.approx(0.0012)
    assert fields["latency_ms"] >= 0
    retries = [r for r in gateway_logs.records if hasattr(r, "anum_model_retry")]
    assert [r.anum_model_retry["reason"] for r in retries] == ["http_503"]


def test_failed_calls_log_error_class_without_prompt_or_key(gateway_logs: pytest.LogCaptureFixture) -> None:
    def leaky(request: httpx.Request) -> httpx.Response:
        # Provider errors can echo the request; none of it may reach the logs.
        return httpx.Response(400, json={"error": f"bad prompt {PROMPT} with key {API_KEY}"})

    gateway, _ = _gateway([leaky], sleeps=[])

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(gateway.generate_text(PROMPT))

    text = _all_log_text(gateway_logs)
    assert "Globex" not in text
    assert "SUPERSECRET" not in text
    calls = [r for r in gateway_logs.records if hasattr(r, "anum_model_call")]
    assert calls[0].anum_model_call["status"] == "error"
    assert calls[0].anum_model_call["http_status"] == 400
    assert calls[0].anum_model_call["error_class"] == "HTTPStatusError"


def test_stream_logs_are_redacted(gateway_logs: pytest.LogCaptureFixture) -> None:
    body = 'data: {"choices":[{"delta":{"content":"Globex secret reply"}}]}\n\ndata: [DONE]\n\n'
    gateway, _ = _gateway([lambda _: httpx.Response(200, text=body)], sleeps=[])

    async def collect() -> list[str]:
        return [chunk async for chunk in gateway.stream_text(PROMPT)]

    assert asyncio.run(collect()) == ["Globex secret reply"]
    text = _all_log_text(gateway_logs)
    assert "Globex" not in text
    assert "SUPERSECRET" not in text
    assert any(getattr(r, "anum_model_call", {}).get("status") == "ok" for r in gateway_logs.records)
