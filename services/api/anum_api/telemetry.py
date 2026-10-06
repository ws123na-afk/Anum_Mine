"""OpenTelemetry for the API and the Temporal worker (docs/observability.md).

Traces, metrics and logs leave the process over OTLP/HTTP only when an endpoint is
configured (``ANUM_OTEL_EXPORTER_OTLP_ENDPOINT`` or the standard
``OTEL_EXPORTER_OTLP_ENDPOINT``); otherwise every instrument below is a cheap no-op.
``OTEL_SDK_DISABLED=true`` turns export off regardless.

What is never recorded, in any signal: prompts, model replies, memory or file
contents, request or response bodies, query strings, credentials, tokens, keys and
client addresses. Tenant scope appears only as ``anum.tenant_id`` /
``anum.workspace_id`` on spans, never as metric attributes (cardinality). The
:class:`RedactingSpanExporter` enforces the URL, header, client-address and
exception-message part for every instrumentation library, not just ANUM's own spans.

Log correlation is always on: every ``logging.LogRecord`` carries ``anum_trace_id``,
``anum_span_id`` and ``anum_correlation_id`` (the ``X-Correlation-ID`` of the active
request), whether or not export is configured. The ``anum_`` prefix keeps them clear
of ``extra=`` keys callers already pass (``extra`` may not overwrite a record field).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import metrics, trace
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .request_context import current_correlation_id

logger = logging.getLogger(__name__)

INSTRUMENTATION_NAME = "anum_api"
SERVICE_NAMESPACE = "anum"
UNMATCHED_ROUTE = "unmatched"
REDACTED = "[redacted]"

# Seconds. Requests: fast CRUD to slow model-backed runs. Model calls: up to minutes.
HTTP_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
MODEL_DURATION_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300)
ACTIVITY_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)

# Span attributes rewritten before export. URL keys keep scheme, host and path only.
_URL_KEYS = frozenset({"http.url", "url.full"})
_TARGET_KEYS = frozenset({"http.target"})
_REDACTED_KEYS = frozenset(
    {
        "url.query",
        "net.peer.ip",
        "net.sock.peer.addr",
        "client.address",
        "network.peer.address",
        "http.client_ip",
    }
)
_SECRET_HEADER_FRAGMENTS = ("authorization", "cookie", "api-key", "api_key", "token", "secret")


# --------------------------------------------------------------------------- outbox gauges


@dataclass(frozen=True)
class OutboxSnapshot:
    """Point-in-time outbox depth. ``oldest_age_seconds`` is 0 when nothing is due."""

    backlog: int
    oldest_age_seconds: float
    parked: int = 0


OutboxSource = Callable[[], OutboxSnapshot | None]
_outbox_sources: dict[str, OutboxSource] = {}
_outbox_lock = threading.Lock()


def register_outbox_source(name: str, source: OutboxSource) -> Callable[[], None]:
    """Report an outbox's depth on the ``anum.outbox.*`` gauges as ``anum.outbox=<name>``.

    ``source`` runs on the metric export thread, so it must not do I/O; return a cached
    snapshot (the PostgreSQL relay refreshes its own from its loop).
    """
    with _outbox_lock:
        _outbox_sources[name] = source

    def unregister() -> None:
        with _outbox_lock:
            if _outbox_sources.get(name) is source:
                del _outbox_sources[name]

    return unregister


def _outbox_snapshots() -> list[tuple[str, OutboxSnapshot]]:
    with _outbox_lock:
        sources = list(_outbox_sources.items())
    snapshots: list[tuple[str, OutboxSnapshot]] = []
    for name, source in sources:
        try:
            snapshot = source()
        except Exception:  # a broken source must not break the export of other metrics
            logger.debug("Outbox source %s failed", name, exc_info=True)
            continue
        if snapshot is not None:
            snapshots.append((name, snapshot))
    return snapshots


def _observe_backlog(_: CallbackOptions) -> Iterator[Observation]:
    for name, snapshot in _outbox_snapshots():
        yield Observation(snapshot.backlog, {"anum.outbox": name})


def _observe_oldest_age(_: CallbackOptions) -> Iterator[Observation]:
    for name, snapshot in _outbox_snapshots():
        yield Observation(max(0.0, snapshot.oldest_age_seconds), {"anum.outbox": name})


def _observe_parked(_: CallbackOptions) -> Iterator[Observation]:
    for name, snapshot in _outbox_snapshots():
        yield Observation(snapshot.parked, {"anum.outbox": name})


# --------------------------------------------------------------------------- instruments


class Telemetry:
    """ANUM's tracer and instruments, bound to the global providers by default.

    Call sites read the attributes at call time, so :meth:`bind` (used by
    :func:`setup_telemetry` and by tests with in-memory exporters) re-targets them.
    """

    def __init__(self) -> None:
        self.bind()

    def bind(self, tracer_provider: Any | None = None, meter_provider: Any | None = None) -> None:
        self.tracer = trace.get_tracer(INSTRUMENTATION_NAME, tracer_provider=tracer_provider)
        meter = metrics.get_meter(INSTRUMENTATION_NAME, meter_provider=meter_provider)
        self.meter = meter
        self.http_duration = meter.create_histogram(
            "anum.http.server.request.duration",
            unit="s",
            description="API request duration by route template, method and status code.",
            explicit_bucket_boundaries_advisory=HTTP_DURATION_BUCKETS,
        )
        self.rate_limit_rejections = meter.create_counter(
            "anum.rate_limit.rejections",
            unit="{request}",
            description="Requests answered 429 by the API rate limiter.",
        )
        self.model_duration = meter.create_histogram(
            "anum.model.call.duration",
            unit="s",
            description="Model gateway call duration, including retries.",
            explicit_bucket_boundaries_advisory=MODEL_DURATION_BUCKETS,
        )
        self.model_tokens = meter.create_counter(
            "anum.model.tokens",
            unit="{token}",
            description="Model tokens by provider, model and gen_ai.token.type.",
        )
        self.model_cost = meter.create_counter(
            "anum.model.estimated_cost_usd",
            unit="{USD}",
            description="Estimated model spend in US dollars (price table estimate).",
        )
        self.outbox_published = meter.create_counter(
            "anum.outbox.published", unit="{event}", description="Events acknowledged by the bus."
        )
        self.outbox_publish_failures = meter.create_counter(
            "anum.outbox.publish_failures",
            unit="{event}",
            description="Publish attempts that failed and were rescheduled.",
        )
        self.outbox_rejected = meter.create_counter(
            "anum.outbox.rejected",
            unit="{event}",
            description="Events that can never be published (parked or dropped).",
        )
        meter.create_observable_gauge(
            "anum.outbox.backlog",
            callbacks=[_observe_backlog],
            unit="{event}",
            description="Committed events not yet published (parked events excluded).",
        )
        meter.create_observable_gauge(
            "anum.outbox.oldest_unpublished_age",
            callbacks=[_observe_oldest_age],
            unit="s",
            description="Age of the oldest committed, unpublished, not parked event.",
        )
        meter.create_observable_gauge(
            "anum.outbox.parked",
            callbacks=[_observe_parked],
            unit="{event}",
            description="Events parked as unpublishable; need an operator.",
        )
        self.run_lock_contention = meter.create_counter(
            "anum.run_lock.contention",
            unit="{attempt}",
            description="Run lock acquisitions that failed (anum.lock.outcome=busy|unavailable).",
        )
        self.activity_outcomes = meter.create_counter(
            "anum.temporal.activity.outcomes",
            unit="{activity}",
            description="Temporal activity results by activity name and outcome.",
        )
        self.activity_duration = meter.create_histogram(
            "anum.temporal.activity.duration",
            unit="s",
            description="Temporal activity duration by activity name and outcome.",
            explicit_bucket_boundaries_advisory=ACTIVITY_DURATION_BUCKETS,
        )

    # -- recording helpers (metadata only, by construction) ---------------------

    def record_model_call(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        status: str,
        duration_seconds: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        error_type: str | None = None,
    ) -> None:
        attributes: dict[str, str] = {
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "gen_ai.operation.name": operation,
            "anum.model.status": status,
        }
        if error_type:
            attributes["error.type"] = error_type
        self.model_duration.record(max(0.0, duration_seconds), attributes)
        usage_attributes = {"gen_ai.provider.name": provider, "gen_ai.request.model": model}
        if input_tokens:
            self.model_tokens.add(input_tokens, {**usage_attributes, "gen_ai.token.type": "input"})
        if output_tokens:
            self.model_tokens.add(output_tokens, {**usage_attributes, "gen_ai.token.type": "output"})
        if estimated_cost_usd:
            self.model_cost.add(estimated_cost_usd, usage_attributes)

    def record_lock_contention(self, outcome: str, *, source: str = "api") -> None:
        self.run_lock_contention.add(1, {"anum.lock.outcome": outcome, "anum.lock.source": source})

    def record_activity(self, activity: str, outcome: str, duration_seconds: float) -> None:
        attributes = {"temporal.activity.type": activity, "anum.activity.outcome": outcome}
        self.activity_outcomes.add(1, attributes)
        self.activity_duration.record(max(0.0, duration_seconds), attributes)


telemetry = Telemetry()


def set_tenant_attributes(span: Span, tenant_id: str | None, workspace_id: str | None) -> None:
    """The only tenant data allowed on spans: the opaque tenant and workspace ids."""
    if tenant_id:
        span.set_attribute("anum.tenant_id", tenant_id)
    if workspace_id:
        span.set_attribute("anum.workspace_id", workspace_id)


@contextmanager
def model_call_span(provider: str, model: str, operation: str) -> Iterator[Span]:
    """A CLIENT span around one model gateway call. Never put prompt or reply text on it."""
    with telemetry.tracer.start_as_current_span(
        f"{operation} {model}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "gen_ai.operation.name": operation,
        },
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        yield span


def annotate_model_span(
    span: Span,
    *,
    status: str,
    attempts: int,
    http_status: int | None = None,
    response_model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
    error_type: str | None = None,
) -> None:
    if not span.is_recording():
        return
    span.set_attribute("anum.model.attempts", attempts)
    if http_status is not None:
        span.set_attribute("http.response.status_code", http_status)
    if response_model:
        span.set_attribute("gen_ai.response.model", response_model)
    if input_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    if output_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    if estimated_cost_usd is not None:
        span.set_attribute("anum.model.estimated_cost_usd", estimated_cost_usd)
    if status == "ok":
        return
    # The error is reduced to its class: provider error messages can echo request text.
    if error_type:
        span.set_attribute("error.type", error_type)
    span.set_status(Status(StatusCode.ERROR, error_type or "error"))


# --------------------------------------------------------------------------- HTTP metrics


def route_template(scope: Scope) -> str:
    """The matched route's template (``/api/v1/tasks/{task_id}``), never the raw path."""
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) and template else UNMATCHED_ROUTE


class HttpMetricsMiddleware:
    """Records ``anum.http.server.request.duration`` per route template and status.

    Pure ASGI (it never buffers streams). The duration of a streaming response is
    the life of the stream; dashboards and alerts exclude ``/stream`` routes from
    latency percentiles.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status_code = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            telemetry.http_duration.record(
                time.perf_counter() - started,
                {
                    "http.request.method": str(scope.get("method", "UNKNOWN")),
                    "http.route": route_template(scope),
                    "http.response.status_code": status_code,
                },
            )


# --------------------------------------------------------------------------- redaction


def strip_query(url: str) -> str:
    """Scheme, host and path only: no userinfo, query string or fragment."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return REDACTED
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _is_secret_header_attribute(key: str) -> bool:
    if not key.startswith(("http.request.header.", "http.response.header.")):
        return False
    return any(fragment in key for fragment in _SECRET_HEADER_FRAGMENTS)


def redacted_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """The attribute rewrites :class:`RedactingSpanExporter` applies, as a pure function."""
    changes: dict[str, Any] = {}
    for key, value in attributes.items():
        if key in _URL_KEYS and isinstance(value, str):
            stripped = strip_query(value)
            if stripped != value:
                changes[key] = stripped
        elif key in _TARGET_KEYS and isinstance(value, str) and ("?" in value or "#" in value):
            changes[key] = value.split("?", 1)[0].split("#", 1)[0]
        elif key in _REDACTED_KEYS or _is_secret_header_attribute(key):
            if value != REDACTED:
                changes[key] = REDACTED
    return changes


def _frames_only(stacktrace: str) -> str:
    """A formatted traceback without its final ``Type: message`` line(s)."""
    return "\n".join(
        line for line in stacktrace.splitlines() if line.startswith(("Traceback", "  "))
    )


def redacted_event_attributes(attributes: Mapping[str, Any]) -> dict[str, Any] | None:
    """Exception events keep the type and frames; messages can echo request text."""
    if "exception.message" not in attributes and "exception.stacktrace" not in attributes:
        return None
    cleaned = dict(attributes)
    if "exception.message" in cleaned:
        cleaned["exception.message"] = REDACTED
    stacktrace = cleaned.get("exception.stacktrace")
    if isinstance(stacktrace, str):
        cleaned["exception.stacktrace"] = _frames_only(stacktrace)
    return cleaned


try:  # the SDK is a runtime dependency, but keep the API importable without it
    from opentelemetry.sdk.trace.export import SpanExporter as _SpanExporterBase
except ImportError:  # pragma: no cover
    _SpanExporterBase = object  # type: ignore[assignment,misc]


def redact_span(span: Any) -> Any:
    """A copy of a finished span with :func:`redacted_attributes` applied."""
    from opentelemetry.sdk.trace import Event, ReadableSpan

    attributes = dict(span.attributes or {})
    attribute_changes = redacted_attributes(attributes)
    events = list(span.events or ())
    cleaned_events = []
    events_changed = False
    for event in events:
        cleaned = redacted_event_attributes(event.attributes or {})
        if cleaned is None:
            cleaned_events.append(event)
        else:
            events_changed = True
            cleaned_events.append(Event(event.name, cleaned, event.timestamp))
    status = span.status
    # Instrumentations put str(exception) in the status description; keep the code only.
    status_changed = status.status_code == StatusCode.ERROR and bool(status.description)
    if not (attribute_changes or events_changed or status_changed):
        return span
    attributes.update(attribute_changes)
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=cleaned_events,
        links=span.links,
        kind=span.kind,
        status=Status(StatusCode.ERROR) if status_changed else status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class RedactingSpanExporter(_SpanExporterBase):  # type: ignore[misc,valid-type]
    """Wraps the real exporter and redacts every span on its way out.

    Applies to every instrumentation library, not just ANUM's own spans: FastAPI's
    ``http.url`` includes the query string (memory search text), httpx's ``url.full``
    can carry webhook keys, servers record the client address, and exception events
    and error statuses carry exception messages. Instrumentations set most of these
    after the span starts, so redaction happens at export, on immutable copies.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def export(self, spans: Sequence[Any]) -> Any:
        return self._inner.export([redact_span(span) for span in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return bool(self._inner.force_flush(timeout_millis))


# --------------------------------------------------------------------------- logs


_record_factory_installed = False
_record_factory_lock = threading.Lock()


def install_log_correlation() -> None:
    """Give every log record ``anum_trace_id``, ``anum_span_id`` and ``anum_correlation_id``.

    Uses the record factory (not a handler filter) so it covers every logger and
    handler, including uvicorn's and the OTLP handler. Values are ``-`` when absent.
    """
    global _record_factory_installed
    with _record_factory_lock:
        if _record_factory_installed:
            return
        previous = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous(*args, **kwargs)
            context = trace.get_current_span().get_span_context()
            if context.is_valid:
                record.anum_trace_id = format(context.trace_id, "032x")
                record.anum_span_id = format(context.span_id, "016x")
            else:
                record.anum_trace_id = "-"
                record.anum_span_id = "-"
            record.anum_correlation_id = current_correlation_id() or "-"
            return record

        logging.setLogRecordFactory(factory)
        _record_factory_installed = True


LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "trace_id=%(anum_trace_id)s span_id=%(anum_span_id)s "
    "correlation_id=%(anum_correlation_id)s %(message)s"
)


def _severity(levelno: int) -> Any:
    from opentelemetry._logs import SeverityNumber

    if levelno >= logging.CRITICAL:
        return SeverityNumber.FATAL
    if levelno >= logging.ERROR:
        return SeverityNumber.ERROR
    if levelno >= logging.WARNING:
        return SeverityNumber.WARN
    if levelno >= logging.INFO:
        return SeverityNumber.INFO
    return SeverityNumber.DEBUG


class OtlpLogHandler(logging.Handler):
    """Ships log records over OTLP with a fixed, minimal attribute set.

    Unlike the SDK's generic handler it never copies ``extra`` fields or exception
    messages (they can embed request text); exceptions travel as their type and the
    stack frames only. Trace context comes from the active span.
    """

    def __init__(self, logger_provider: Any, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._provider = logger_provider

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from opentelemetry.context import get_current

            attributes: dict[str, Any] = {
                "logger.name": record.name,
                "code.function.name": record.funcName,
                "code.line.number": record.lineno,
            }
            correlation_id = getattr(record, "anum_correlation_id", None)
            if correlation_id and correlation_id != "-":
                attributes["anum.correlation_id"] = correlation_id
            if record.exc_info and record.exc_info[0] is not None:
                attributes["exception.type"] = record.exc_info[0].__name__
                if record.exc_info[2] is not None:
                    attributes["exception.stacktrace"] = "".join(
                        traceback.format_tb(record.exc_info[2])
                    )
            otel_logger = self._provider.get_logger(record.name)
            otel_logger.emit(
                timestamp=int(record.created * 1e9),
                observed_timestamp=time.time_ns(),
                context=get_current(),
                severity_number=_severity(record.levelno),
                severity_text=record.levelname,
                body=record.getMessage(),
                attributes=attributes,
            )
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)


# --------------------------------------------------------------------------- setup


@dataclass
class _Providers:
    tracer_provider: Any
    meter_provider: Any
    logger_provider: Any | None
    log_handler: logging.Handler | None


_providers: _Providers | None = None
_setup_lock = threading.Lock()


def otlp_endpoint(config: Any) -> str | None:
    """The OTLP/HTTP base URL to export to, or ``None`` when export is off."""
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return None
    value = (
        getattr(config, "otel_exporter_otlp_endpoint", None)
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or ""
    ).strip()
    return value.rstrip("/") or None


def is_configured() -> bool:
    return _providers is not None


def build_resource(config: Any, service_name: str) -> Any:
    from opentelemetry.sdk.resources import Resource

    return Resource.create(
        {
            "service.name": getattr(config, "otel_service_name", None) or service_name,
            "service.namespace": SERVICE_NAMESPACE,
            "service.version": "0.1.0",
            "deployment.environment.name": str(getattr(config, "environment", "local")),
        }
    )


def sqlalchemy_engines(event_runtime: Any | None = None) -> list[Any]:
    """Engines created before telemetry was set up (API session, outbox relay)."""
    engines: list[Any] = []
    session_module = sys.modules.get("anum_api.db.session")
    if session_module is not None and getattr(session_module, "engine", None) is not None:
        engines.append(session_module.engine)
    relay = getattr(event_runtime, "relay", None)
    factory = getattr(relay, "session_factory", None)
    bind = getattr(factory, "kw", {}).get("bind") if factory is not None else None
    if bind is not None and all(bind is not engine for engine in engines):
        engines.append(bind)
    return engines


def instrument_fastapi(app: Any, tracer_provider: Any | None = None) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.metrics import NoOpMeterProvider

    # HTTP metrics come from HttpMetricsMiddleware; the instrumentor only traces.
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=NoOpMeterProvider(),
        excluded_urls="/health",
        exclude_spans=["receive", "send"],
    )


def setup_telemetry(
    config: Any,
    *,
    service_name: str,
    app: Any | None = None,
    engines: Sequence[Any] = (),
) -> bool:
    """Configure OTLP export for this process. Returns whether export is on.

    Idempotent. Without an endpoint only log correlation is installed.
    """
    global _providers
    install_log_correlation()
    endpoint = otlp_endpoint(config)
    if endpoint is None:
        return False
    with _setup_lock:
        if _providers is None:
            _providers = _configure_providers(config, service_name, endpoint)
            _instrument_libraries(_providers, engines)
    if app is not None:
        instrument_fastapi(app, _providers.tracer_provider)
    logger.info("OpenTelemetry export enabled for %s", service_name)
    return True


def _configure_providers(config: Any, service_name: str, endpoint: str) -> _Providers:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    resource = build_resource(config, service_name)
    ratio = float(getattr(config, "otel_traces_sampler_ratio", 1.0))
    tracer_provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(ratio)))
    tracer_provider.add_span_processor(
        BatchSpanProcessor(RedactingSpanExporter(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
    )
    interval_ms = int(float(getattr(config, "otel_metric_export_interval_seconds", 15)) * 1000)
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
                export_interval_millis=interval_ms,
            )
        ],
    )
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    telemetry.bind(tracer_provider, meter_provider)

    logger_provider = None
    handler: logging.Handler | None = None
    if getattr(config, "otel_logs_enabled", True):
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs"))
        )
        handler = OtlpLogHandler(logger_provider)
        # Never export the exporters' own logs (a failing export would loop).
        handler.addFilter(lambda record: not record.name.startswith("opentelemetry"))
        logging.getLogger().addHandler(handler)
    return _Providers(tracer_provider, meter_provider, logger_provider, handler)


def _instrument_libraries(providers: _Providers, engines: Sequence[Any]) -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    HTTPXClientInstrumentor().instrument(
        tracer_provider=providers.tracer_provider,
        meter_provider=providers.meter_provider,
    )
    SQLAlchemyInstrumentor().instrument(
        tracer_provider=providers.tracer_provider,
        meter_provider=providers.meter_provider,
        engines=list(engines),
    )


def temporal_interceptors() -> list[Any]:
    """Temporal client interceptors: trace propagation API -> workflow -> activity."""
    if _providers is None:
        return []
    from temporalio.contrib.opentelemetry import TracingInterceptor

    return [TracingInterceptor(telemetry.tracer)]


def shutdown_telemetry() -> None:
    """Flush and stop exporters (process shutdown)."""
    global _providers
    with _setup_lock:
        providers, _providers = _providers, None
    if providers is None:
        return
    if providers.log_handler is not None:
        logging.getLogger().removeHandler(providers.log_handler)
    for provider in (providers.tracer_provider, providers.meter_provider, providers.logger_provider):
        if provider is None:
            continue
        try:
            provider.shutdown()
        except Exception:  # pragma: no cover - best effort on shutdown
            logger.debug("Telemetry provider shutdown failed", exc_info=True)


__all__ = [
    "HttpMetricsMiddleware",
    "LOG_FORMAT",
    "OtlpLogHandler",
    "OutboxSnapshot",
    "RedactingSpanExporter",
    "Telemetry",
    "annotate_model_span",
    "install_log_correlation",
    "is_configured",
    "model_call_span",
    "otlp_endpoint",
    "redacted_attributes",
    "register_outbox_source",
    "route_template",
    "set_tenant_attributes",
    "setup_telemetry",
    "shutdown_telemetry",
    "sqlalchemy_engines",
    "strip_query",
    "telemetry",
    "temporal_interceptors",
]
