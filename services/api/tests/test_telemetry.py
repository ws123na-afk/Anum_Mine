"""OpenTelemetry instrumentation with in-memory exporters (docs/observability.md).

Every test binds ANUM's tracer and instruments to fresh SDK providers, so nothing
depends on (or changes) the process-global providers, and asserts what is recorded
as well as what must never be: prompts, replies, query strings, keys, client IPs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess  # nosec B404
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from temporalio.exceptions import ApplicationError

from anum_api import telemetry as telemetry_module
from anum_api.dependencies import tenant_context
from anum_api.durable_runs import AgentRunActivities
from anum_api.event_bus import EventOutbox, InMemoryEventBus, NatsJetStreamBus
from anum_api.events import CanonicalEventName, create_event
from anum_api.hardening import InMemoryTokenBucket, RateLimitMiddleware
from anum_api.model_gateway import MockModelGateway, OpenAICompatibleGateway
from anum_api.request_context import CORRELATION_ID_HEADER, CorrelationIdMiddleware
from anum_api.schemas import TenantContext, utc_now
from anum_api.settings import ModelPrice, Settings
from anum_api.telemetry import (
    REDACTED,
    UNMATCHED_ROUTE,
    HttpMetricsMiddleware,
    OtlpLogHandler,
    OutboxSnapshot,
    RedactingSpanExporter,
    install_log_correlation,
    instrument_fastapi,
    otlp_endpoint,
    redacted_attributes,
    register_outbox_source,
    setup_telemetry,
    strip_query,
    telemetry,
    temporal_interceptors,
)
from anum_api.temporal_workflow import AgentRunInput, AgentRunState
from anum_api.valkey import CoordinationUnavailable, LockNotAcquired, RunLockManager

PROMPT = "PRIVATE-PROMPT summarise the board minutes for Acme"
REPLY = "PRIVATE-REPLY the minutes say the merger is off"
CONTEXT = TenantContext(
    tenant_id="tenant_obs", workspace_id="workspace_obs", user_id="user_obs", roles=["owner"]
)


@dataclass
class Signals:
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
    tracer_provider: TracerProvider
    meter_provider: MeterProvider

    def finished(self) -> list[Any]:
        return list(self.spans.get_finished_spans())

    def points(self, name: str) -> list[tuple[dict[str, Any], Any]]:
        data = self.metrics.get_metrics_data()
        found: list[tuple[dict[str, Any], Any]] = []
        for resource_metrics in data.resource_metrics if data else []:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name != name:
                        continue
                    for point in metric.data.data_points:
                        found.append((dict(point.attributes), point))
        return found

    def total(self, name: str, **attributes: Any) -> float:
        total = 0.0
        for point_attributes, point in self.points(name):
            if all(point_attributes.get(key) == value for key, value in attributes.items()):
                total += getattr(point, "value", None) or getattr(point, "count", 0)
        return total


@pytest.fixture
def signals() -> Iterator[Signals]:
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    # Same redaction wrapper as production (setup_telemetry), around an in-memory exporter.
    tracer_provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(spans)))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    telemetry.bind(tracer_provider, meter_provider)
    try:
        yield Signals(spans, reader, tracer_provider, meter_provider)
    finally:
        telemetry.bind()
        tracer_provider.shutdown()
        meter_provider.shutdown()


def _everything_exported(signals: Signals) -> str:
    """Every span name/attribute/event and every metric attribute, as one string."""
    parts: list[str] = []
    for span in signals.finished():
        parts.append(span.name)
        parts.append(json.dumps(dict(span.attributes or {}), default=str))
        parts.extend(json.dumps(dict(event.attributes or {}), default=str) for event in span.events)
        parts.append(str(span.status.description))
    data = signals.metrics.get_metrics_data()
    for resource_metrics in data.resource_metrics if data else []:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                for point in metric.data.data_points:
                    parts.append(json.dumps(dict(point.attributes), default=str))
    return "\n".join(parts)


# --------------------------------------------------------------------------- HTTP


def test_http_metrics_use_route_templates_and_status_codes(signals: Signals) -> None:
    from anum_api.main import app

    client = TestClient(app)
    headers = {
        "x-tenant-id": "tenant_obs",
        "x-workspace-id": "workspace_obs",
        "x-user-id": "user_obs",
        "x-user-roles": "owner",
    }
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/tasks/task_does_not_exist", headers=headers).status_code == 404
    assert client.get("/definitely/not/a/route").status_code == 404

    name = "anum.http.server.request.duration"
    assert signals.total(name, **{"http.route": "/health", "http.response.status_code": 200}) == 1
    assert (
        signals.total(
            name,
            **{
                "http.route": "/api/v1/tasks/{task_id}",
                "http.request.method": "GET",
                "http.response.status_code": 404,
            },
        )
        == 1
    )
    assert signals.total(name, **{"http.route": UNMATCHED_ROUTE}) == 1
    # Raw paths (with ids) never become metric labels.
    assert "task_does_not_exist" not in _everything_exported(signals)


def test_unhandled_errors_are_counted_as_500(signals: Signals) -> None:
    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        detail = "PRIVATE " + "detail"  # built at runtime: the source line is not data
        raise RuntimeError(detail)

    app.add_middleware(HttpMetricsMiddleware)
    instrument_fastapi(app, signals.tracer_provider)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/boom").status_code == 500
    assert signals.total(
        "anum.http.server.request.duration",
        **{"http.route": "/boom", "http.response.status_code": 500},
    ) == 1
    (server,) = [span for span in signals.finished() if span.kind == SpanKind.SERVER]
    assert server.status.status_code == StatusCode.ERROR
    exception_events = [event for event in server.events if event.name == "exception"]
    assert exception_events and exception_events[0].attributes["exception.type"] == "RuntimeError"
    assert "PRIVATE detail" not in _everything_exported(signals)


def test_rate_limit_rejections_are_counted(signals: Signals) -> None:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_middleware(
        RateLimitMiddleware, backend=InMemoryTokenBucket(rate_per_second=0.001, burst=1)
    )
    client = TestClient(app)
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 429
    assert client.get("/ping").status_code == 429
    assert signals.total("anum.rate_limit.rejections", **{"anum.rate_limit.backend": "memory"}) == 2
    assert "testclient" not in _everything_exported(signals)  # no client address labels


def test_request_spans_carry_tenant_ids_but_no_query_strings_or_client_address(
    signals: Signals,
) -> None:
    app = FastAPI()

    @app.get("/api/v1/things/{thing_id}")
    async def thing(
        thing_id: str,
        query: str | None = None,
        context: TenantContext = Depends(tenant_context),
    ) -> dict[str, str]:
        return {"id": thing_id}

    app.add_middleware(CorrelationIdMiddleware)
    instrument_fastapi(app, signals.tracer_provider)
    client = TestClient(app)
    response = client.get(
        "/api/v1/things/thing_1?query=PRIVATE-SEARCH-TEXT",
        headers={
            "x-tenant-id": "tenant_obs",
            "x-workspace-id": "workspace_obs",
            "x-user-id": "user_obs",
            "x-user-roles": "owner",
            "authorization": "Bearer PRIVATE-TOKEN",
        },
    )
    assert response.status_code == 200

    (server,) = [span for span in signals.finished() if span.kind == SpanKind.SERVER]
    assert server.attributes["http.route"] == "/api/v1/things/{thing_id}"
    assert server.attributes["anum.tenant_id"] == "tenant_obs"
    assert server.attributes["anum.workspace_id"] == "workspace_obs"
    exported = _everything_exported(signals)
    assert "PRIVATE-SEARCH-TEXT" not in exported
    assert "PRIVATE-TOKEN" not in exported
    assert "user_obs" not in exported  # user ids are not span data
    for key in ("net.peer.ip", "client.address"):
        if key in server.attributes:
            assert server.attributes[key] == REDACTED


def test_redaction_rules() -> None:
    assert strip_query("https://user:pw@hooks.example:8443/a/b?api_key=k#frag") == (
        "https://hooks.example:8443/a/b"
    )
    changes = redacted_attributes(
        {
            "http.url": "http://api/x?q=secret",
            "url.full": "https://api/x",
            "http.target": "/x?q=secret",
            "url.query": "q=secret",
            "client.address": "203.0.113.9",
            "http.request.header.authorization": ("Bearer x",),
            "http.request.header.accept": ("json",),
            "http.route": "/x",
        }
    )
    assert changes == {
        "http.url": "http://api/x",
        "http.target": "/x",
        "url.query": REDACTED,
        "client.address": REDACTED,
        "http.request.header.authorization": REDACTED,
    }


def test_outbound_httpx_spans_drop_query_strings(signals: Signals) -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    async def call() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(204))
        async with httpx.AsyncClient(transport=transport) as client:
            HTTPXClientInstrumentor.instrument_client(client, tracer_provider=signals.tracer_provider)
            await client.post("https://hooks.example/notify?api_key=PRIVATE-KEY", json={"a": 1})

    asyncio.run(call())
    (span,) = signals.finished()
    assert span.kind == SpanKind.CLIENT
    assert "PRIVATE-KEY" not in _everything_exported(signals)


# --------------------------------------------------------------------------- model gateway


def _gateway(handler) -> OpenAICompatibleGateway:
    return OpenAICompatibleGateway(
        api_key="provider-secret",
        model="gpt-4.1-mini",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        prices={"gpt-4.1-mini": ModelPrice(input_per_million=0.40, output_per_million=1.60)},
        sleep=lambda _: asyncio.sleep(0),
        jitter=lambda: 0.0,
    )


def test_model_calls_record_span_latency_tokens_and_cost_without_text(signals: Signals) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "gpt-4.1-mini-2025-04-14",
                "choices": [{"message": {"content": REPLY}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
            },
        )

    response = asyncio.run(_gateway(handler).generate_text(PROMPT))
    assert response.text == REPLY

    (span,) = signals.finished()
    assert span.name == "generate_text gpt-4.1-mini"
    assert span.kind == SpanKind.CLIENT
    assert span.attributes["gen_ai.provider.name"] == "openai-compatible"
    assert span.attributes["gen_ai.response.model"] == "gpt-4.1-mini-2025-04-14"
    assert span.attributes["gen_ai.usage.input_tokens"] == 1000
    assert span.attributes["gen_ai.usage.output_tokens"] == 500
    assert span.attributes["anum.model.attempts"] == 1

    labels = {"gen_ai.provider.name": "openai-compatible", "gen_ai.request.model": "gpt-4.1-mini"}
    assert signals.total("anum.model.call.duration", **labels, **{"anum.model.status": "ok"}) == 1
    assert signals.total("anum.model.tokens", **labels, **{"gen_ai.token.type": "input"}) == 1000
    assert signals.total("anum.model.tokens", **labels, **{"gen_ai.token.type": "output"}) == 500
    assert signals.total("anum.model.estimated_cost_usd", **labels) == pytest.approx(0.0012)

    exported = _everything_exported(signals)
    assert "PRIVATE-PROMPT" not in exported
    assert "PRIVATE-REPLY" not in exported
    assert "provider-secret" not in exported


def test_failed_model_calls_mark_the_span_and_count_errors(signals: Signals) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": f"bad request: {PROMPT}"}})

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_gateway(handler).generate_text(PROMPT))

    (span,) = signals.finished()
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error.type"] == "HTTPStatusError"
    assert span.attributes["http.response.status_code"] == 400
    assert not span.events  # no exception event: its message could echo the prompt
    assert signals.total(
        "anum.model.call.duration",
        **{"anum.model.status": "error", "error.type": "HTTPStatusError"},
    ) == 1
    assert "PRIVATE-PROMPT" not in _everything_exported(signals)


def test_streamed_model_calls_end_their_span(signals: Signals) -> None:
    body = "".join(
        f"data: {json.dumps({'choices': [{'delta': {'content': word}}]})}\n\n"
        for word in ("PRIVATE-REPLY ", "streamed")
    ) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    async def consume() -> str:
        return "".join([chunk async for chunk in _gateway(handler).stream_text(PROMPT)])

    assert asyncio.run(consume()) == "PRIVATE-REPLY streamed"
    (span,) = signals.finished()
    assert span.name == "stream_text gpt-4.1-mini"
    assert span.attributes["gen_ai.operation.name"] == "stream_text"
    assert "PRIVATE" not in _everything_exported(signals)


def test_mock_provider_reports_usage_at_zero_cost(signals: Signals) -> None:
    asyncio.run(MockModelGateway().generate_text("three word prompt"))
    assert signals.total("anum.model.tokens", **{"gen_ai.provider.name": "mock"}) == 3 + 12
    assert signals.total("anum.model.estimated_cost_usd") == 0


# --------------------------------------------------------------------------- events


class _FakeNats:
    is_connected = True
    is_closed = False


@dataclass
class _FakeJetStream:
    published: list[dict[str, Any]] = field(default_factory=list)

    async def publish(self, subject: str, data: bytes, **kwargs: Any) -> None:
        self.published.append({"subject": subject, "data": data, **kwargs})


def test_nats_publish_is_a_producer_span_that_propagates_trace_context(signals: Signals) -> None:
    bus = NatsJetStreamBus("nats://unused:4222", stream_name="ANUM_EVENTS")
    bus._nc = _FakeNats()
    bus._js = _FakeJetStream()

    async def publish() -> None:
        with telemetry.tracer.start_as_current_span("request"):
            await bus.publish(
                "anum.tenant_obs.workspace_obs.task.created", b"{}", msg_id="evt_1"
            )

    asyncio.run(publish())
    producer = next(span for span in signals.finished() if span.kind == SpanKind.PRODUCER)
    assert producer.name == "publish ANUM_EVENTS"
    assert producer.attributes["messaging.system"] == "nats"
    assert producer.attributes["messaging.message.id"] == "evt_1"
    (message,) = bus._js.published
    assert message["headers"]["Nats-Msg-Id"] == "evt_1"
    traceparent = message["headers"]["traceparent"]
    assert format(producer.context.trace_id, "032x") in traceparent
    assert "tenant_obs" not in json.dumps(dict(producer.attributes))


def test_memory_outbox_reports_backlog_age_and_publications(signals: Signals) -> None:
    now = utc_now()
    clock = {"now": now}
    bus = InMemoryEventBus()
    outbox = EventOutbox(bus, clock=lambda: clock["now"])
    events = [
        create_event(
            CanonicalEventName.TASK_CREATED,
            CONTEXT,
            f"task_{index}",
            {},
            created_at=now - timedelta(seconds=90 - index),
        ).event
        for index in range(3)
    ]

    async def scenario() -> tuple[OutboxSnapshot, OutboxSnapshot]:
        outbox.start()
        try:
            bus.fail_publishes = 1  # bus not connected yet: first pass fails
            outbox.enqueue(events)
            before = outbox.snapshot()
            assert signals.total("anum.outbox.backlog", **{"anum.outbox": "memory"}) == 3
            age = signals.total("anum.outbox.oldest_unpublished_age", **{"anum.outbox": "memory"})
            assert age == pytest.approx(90)
            await bus.connect()
            await outbox.publish_due()
            outbox.retry_now()
            await outbox.publish_due()
            return before, outbox.snapshot()
        finally:
            await outbox.stop()

    before, after = asyncio.run(scenario())
    assert before.backlog == 3 and before.oldest_age_seconds == pytest.approx(90)
    assert after == OutboxSnapshot(backlog=0, oldest_age_seconds=0.0)
    assert signals.total("anum.outbox.published", **{"anum.outbox": "memory"}) == 3
    assert signals.total("anum.outbox.publish_failures", **{"anum.outbox": "memory"}) >= 1
    # Unregistered on stop: the gauge no longer reports this outbox.
    assert signals.total("anum.outbox.backlog", **{"anum.outbox": "memory"}) == 0


def test_a_failing_outbox_source_does_not_break_other_gauges(signals: Signals) -> None:
    def broken() -> OutboxSnapshot:
        raise RuntimeError("database down")

    remove_broken = register_outbox_source("broken", broken)
    remove_ok = register_outbox_source("ok", lambda: OutboxSnapshot(7, 12.5, parked=2))
    try:
        assert signals.total("anum.outbox.backlog", **{"anum.outbox": "ok"}) == 7
        assert signals.total("anum.outbox.oldest_unpublished_age", **{"anum.outbox": "ok"}) == 12.5
        assert signals.total("anum.outbox.parked", **{"anum.outbox": "ok"}) == 2
    finally:
        remove_broken()
        remove_ok()


# --------------------------------------------------------------------------- coordination


class _BusyValkey:
    async def set(self, *args: Any, **kwargs: Any) -> None:
        return None


class _DownValkey:
    async def set(self, *args: Any, **kwargs: Any) -> None:
        from redis.exceptions import ConnectionError as RedisConnectionError

        raise RedisConnectionError("valkey is down")


def test_run_lock_contention_is_counted(signals: Signals) -> None:
    async def scenario() -> None:
        with pytest.raises(LockNotAcquired):
            await RunLockManager(_BusyValkey()).acquire(CONTEXT, "task_1")
        with pytest.raises(CoordinationUnavailable):
            await RunLockManager(_DownValkey(), metric_source="worker").acquire(CONTEXT, "task_1")

    asyncio.run(scenario())
    name = "anum.run_lock.contention"
    assert signals.total(name, **{"anum.lock.outcome": "busy", "anum.lock.source": "api"}) == 1
    assert (
        signals.total(name, **{"anum.lock.outcome": "unavailable", "anum.lock.source": "worker"})
        == 1
    )
    assert "task_1" not in _everything_exported(signals)


class _ScriptedActivities(AgentRunActivities):
    def __init__(self, outcome: BaseException | None, locks: RunLockManager | None = None) -> None:
        super().__init__(lambda context, repository: None, locks=locks)  # type: ignore[arg-type]
        self.outcome = outcome

    async def advance(self, context: TenantContext, request: AgentRunInput) -> AgentRunState:
        if self.outcome is not None:
            raise self.outcome
        return AgentRunState(run_id=request.run_id, phase="completed", status="completed")


def _input() -> AgentRunInput:
    return AgentRunInput(
        tenant_id="tenant_obs",
        workspace_id="workspace_obs",
        user_id="user_obs",
        roles=["owner"],
        task_id="task_1",
        run_id="run_1",
    )


def test_temporal_activity_outcomes_are_counted(signals: Signals) -> None:
    async def scenario() -> None:
        with telemetry.tracer.start_as_current_span("RunActivity:anum.advance_run"):
            await _ScriptedActivities(None).advance_run(_input())
        with pytest.raises(ApplicationError):
            await _ScriptedActivities(None, RunLockManager(_BusyValkey())).advance_run(_input())
        with pytest.raises(ApplicationError):
            await _ScriptedActivities(
                ApplicationError("gone", type="RunNotFound", non_retryable=True)
            ).advance_run(_input())
        with pytest.raises(RuntimeError):
            await _ScriptedActivities(RuntimeError("PRIVATE failure")).advance_run(_input())

    asyncio.run(scenario())
    name = "anum.temporal.activity.outcomes"
    for outcome in ("advanced", "locked", "not_found", "error"):
        assert signals.total(name, **{"anum.activity.outcome": outcome}) == 1, outcome
    assert signals.total("anum.temporal.activity.duration", **{"anum.activity.outcome": "advanced"}) == 1
    (activity_span,) = signals.finished()
    assert activity_span.attributes["anum.tenant_id"] == "tenant_obs"
    assert activity_span.attributes["anum.workspace_id"] == "workspace_obs"
    assert "PRIVATE" not in _everything_exported(signals)


# --------------------------------------------------------------------------- logs


def test_log_records_carry_trace_span_and_correlation_ids(signals: Signals) -> None:
    install_log_correlation()
    app = FastAPI()
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    log = logging.getLogger("anum_api.tests.telemetry")
    handler = Capture()
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    @app.get("/log")
    async def write_log() -> dict[str, str]:
        with telemetry.tracer.start_as_current_span("work") as span:
            log.info("inside a span")
            context = span.get_span_context()
            return {"trace_id": format(context.trace_id, "032x"), "span_id": format(context.span_id, "016x")}

    app.add_middleware(CorrelationIdMiddleware)
    try:
        response = TestClient(app).get("/log", headers={CORRELATION_ID_HEADER: "corr-obs-1"})
        log.info("outside any request")
    finally:
        log.removeHandler(handler)

    inside, outside = captured
    assert inside.anum_trace_id == response.json()["trace_id"]
    assert inside.anum_span_id == response.json()["span_id"]
    assert inside.anum_correlation_id == "corr-obs-1"
    assert (outside.anum_trace_id, outside.anum_span_id, outside.anum_correlation_id) == ("-", "-", "-")
    # An explicit extra={"correlation_id": ...} (errors.py does this) still works.
    log.info("explicit extra", extra={"correlation_id": "x"})


def test_otlp_log_handler_exports_trace_context_but_not_exception_messages(
    signals: Signals,
) -> None:
    install_log_correlation()
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    log = logging.getLogger("anum_api.tests.otlp")
    log.propagate = False
    handler = OtlpLogHandler(provider)
    log.addHandler(handler)
    try:
        with telemetry.tracer.start_as_current_span("work") as span:
            try:
                raise ValueError(f"provider said: {PROMPT}")
            except ValueError:
                log.exception("model call failed", extra={"anum_model_call": {"prompt": PROMPT}})
    finally:
        log.removeHandler(handler)
        log.propagate = True
        provider.shutdown()

    (exported,) = exporter.get_finished_logs()
    record = exported.log_record
    assert record.body == "model call failed"
    assert record.trace_id == span.get_span_context().trace_id
    assert record.attributes["exception.type"] == "ValueError"
    assert "PRIVATE-PROMPT" not in json.dumps(dict(record.attributes), default=str)


# --------------------------------------------------------------------------- setup


def test_export_is_off_without_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    config = Settings(environment="test")
    assert otlp_endpoint(config) is None
    assert setup_telemetry(config, service_name="anum-api") is False
    assert not telemetry_module.is_configured()
    assert temporal_interceptors() == []


def test_endpoint_comes_from_settings_or_the_standard_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318/")
    assert otlp_endpoint(Settings(environment="test")) == "http://otel-collector:4318"
    configured = Settings(environment="test", otel_exporter_otlp_endpoint="http://collector:4318")
    assert otlp_endpoint(configured) == "http://collector:4318"
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert otlp_endpoint(configured) is None


def test_resource_names_the_service_and_environment_only() -> None:
    resource = telemetry_module.build_resource(Settings(environment="test"), "anum-worker")
    attributes = dict(resource.attributes)
    assert attributes["service.name"] == "anum-worker"
    assert attributes["service.namespace"] == "anum"
    assert attributes["deployment.environment.name"] == "test"
    assert not any(key.startswith("anum.") for key in attributes)  # no tenant data on resources


_EXPORT_SCRIPT = """
import logging
from fastapi.testclient import TestClient
from anum_api.main import app
from anum_api.telemetry import is_configured, shutdown_telemetry

assert is_configured()
headers = {"x-tenant-id": "tenant_obs", "x-workspace-id": "workspace_obs",
           "x-user-id": "user_obs", "x-user-roles": "owner"}
with TestClient(app) as client:
    client.get("/api/v1/memories?query=PRIVATE-SEARCH-TEXT", headers=headers)
    logging.getLogger("anum_api.export_check").warning("export check")
shutdown_telemetry()
"""


def test_setup_exports_traces_metrics_and_logs_over_otlp_http(tmp_path: Path) -> None:
    """End to end in a subprocess (global providers are process-wide): the API exports
    all three signals to an OTLP/HTTP endpoint, and redaction holds on the wire."""
    received: dict[str, list[bytes]] = {}

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            received.setdefault(self.path, []).append(body)
            self.send_response(200)
            self.send_header("content-type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "ANUM_ENVIRONMENT": "local",
            "ANUM_OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
            "ANUM_AUTOMATION_DATABASE_PATH": str(tmp_path / "automation.db"),
        }
        env.pop("OTEL_SDK_DISABLED", None)
        result = subprocess.run(  # nosec B603
            [sys.executable, "-c", _EXPORT_SCRIPT],
            cwd=Path(__file__).parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr[-2000:]
    finally:
        server.shutdown()
        server.server_close()

    assert set(received) >= {"/v1/traces", "/v1/metrics", "/v1/logs"}
    traces = b"".join(received["/v1/traces"])
    assert b"/api/v1/memories" in traces
    assert b"tenant_obs" in traces  # anum.tenant_id on the request span
    assert b"PRIVATE-SEARCH-TEXT" not in traces
    assert b"anum.http.server.request.duration" in b"".join(received["/v1/metrics"])
    logs = b"".join(received["/v1/logs"])
    assert b"export check" in logs
    assert b"PRIVATE-SEARCH-TEXT" not in logs
