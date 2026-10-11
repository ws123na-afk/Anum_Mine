"""Optional error reporting to a Sentry-compatible server (docs/observability.md#error-reporting).

Off unless ``ANUM_SENTRY_DSN`` is set. On the one-VM deployment the server is the
self-hosted GlitchTip of the ``monitoring`` profile (docs/monitoring-oracle.md); any
Sentry-compatible endpoint works. Tracing stays with OpenTelemetry (telemetry.py): only
errors are sent here, never performance data.

The same rules as telemetry.py apply to what leaves the process. An event keeps the
exception type and stack frames, the route, the request method and path, log message
templates, the environment, the service name, and two tags: ``anum.tenant_id`` (the
opaque tenant id, set per request in dependencies.py) and ``anum.correlation_id``. It
never carries exception messages (they can echo request text), local variables, request
bodies, query strings, cookies, source lines, headers other than content type and length, client
addresses, user identity, log arguments or ``extra=`` fields. :func:`scrub_event` and
:func:`scrub_breadcrumb` enforce that for every integration the SDK enables, on top of
``send_default_pii=False``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .request_context import current_correlation_id

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"
TENANT_TAG = "anum.tenant_id"
CORRELATION_TAG = "anum.correlation_id"
DSN_FORMAT = "ANUM_SENTRY_DSN must look like https://<public key>@<host>/<project id>"
DSN_TRANSPORT = (
    "ANUM_SENTRY_DSN must use https outside local, or http to a private service name "
    "without dots such as http://<key>@glitchtip:8000/1"
)

_KEPT_REQUEST_HEADERS = frozenset({"content-type", "content-length"})
_KEPT_CONTEXTS = frozenset({"runtime", "os", "trace"})
_KEPT_BREADCRUMB_DATA = frozenset({"method", "status_code", "url", "reason", "http.method"})
_URL_BREADCRUMB_KEYS = frozenset({"url"})

_enabled = False


# --------------------------------------------------------------------------- settings checks


def dsn_problem(dsn: str) -> str | None:
    """Why ``dsn`` is not a usable DSN, or ``None``. Never echoes the value (it holds a key)."""
    if not dsn.strip():
        return None
    try:
        parts = urlsplit(dsn.strip())
        hostname = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return DSN_FORMAT
    project = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if (
        parts.scheme not in {"http", "https"}
        or not hostname
        or not parts.username
        or not project.isdigit()
        or parts.query
        or parts.fragment
    ):
        return DSN_FORMAT
    return None


def insecure_dsn_problem(dsn: str) -> str | None:
    """Outside local, events go over https, or over http to a private service name only.

    A single-label host (``glitchtip``) is a compose or Kubernetes service on the same
    private network; anything else over plain http would cross a network in clear text.
    """
    dsn = dsn.strip()
    if not dsn or dsn_problem(dsn):
        return None
    parts = urlsplit(dsn)
    if parts.scheme == "https":
        return None
    hostname = parts.hostname or ""
    if "." not in hostname and ":" not in hostname and hostname != "localhost":
        return None
    return DSN_TRANSPORT


def configured_dsn(config: Any) -> str:
    secret = getattr(config, "sentry_dsn", None)
    if secret is None:
        return ""
    value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
    return value.strip()


# --------------------------------------------------------------------------- scrubbing


def _strip_query(url: Any) -> Any:
    if not isinstance(url, str) or not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return REDACTED
    netloc = parts.hostname or ""
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _scrub_request(request: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    if isinstance(request.get("method"), str):
        cleaned["method"] = request["method"]
    if "url" in request:
        cleaned["url"] = _strip_query(request["url"])
    headers = request.get("headers")
    if isinstance(headers, Mapping):
        kept = {
            name: value
            for name, value in headers.items()
            if isinstance(name, str) and name.lower() in _KEPT_REQUEST_HEADERS
        }
        if kept:
            cleaned["headers"] = kept
    return cleaned


def _scrub_frames(container: Any) -> None:
    if not isinstance(container, Mapping):
        return
    stacktrace = container.get("stacktrace")
    if isinstance(stacktrace, Mapping):
        for frame in stacktrace.get("frames") or ():
            if isinstance(frame, dict):
                frame.pop("vars", None)


def scrub_breadcrumb(crumb: dict[str, Any], hint: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Breadcrumbs keep their category, level and time, URLs without query strings, and
    log message templates (never the formatted message or its arguments)."""
    record = (hint or {}).get("log_record")
    if isinstance(record, logging.LogRecord):
        crumb["message"] = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
    data = crumb.get("data")
    if isinstance(data, Mapping):
        kept = {
            key: (_strip_query(value) if key in _URL_BREADCRUMB_KEYS else value)
            for key, value in data.items()
            if key in _KEPT_BREADCRUMB_DATA
        }
        if kept:
            crumb["data"] = kept
        else:
            crumb.pop("data", None)
    if crumb.get("category") in {"http", "httplib", "httpx"} and isinstance(crumb.get("message"), str):
        crumb["message"] = _strip_query(crumb["message"])
    return crumb


def scrub_event(event: dict[str, Any], hint: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Reduce an event to what the module docstring allows. Runs on every event."""
    event.pop("user", None)
    event.pop("extra", None)
    request = event.get("request")
    if isinstance(request, Mapping):
        event["request"] = _scrub_request(request)
    exceptions = event.get("exception")
    if isinstance(exceptions, Mapping):
        for value in exceptions.get("values") or ():
            if isinstance(value, dict):
                if value.get("value"):
                    value["value"] = REDACTED
                _scrub_frames(value)
    threads = event.get("threads")
    if isinstance(threads, Mapping):
        for thread in threads.get("values") or ():
            _scrub_frames(thread)
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        # The template is code; its arguments and the formatted text can carry data.
        logentry.pop("params", None)
        logentry.pop("formatted", None)
    contexts = event.get("contexts")
    if isinstance(contexts, Mapping):
        event["contexts"] = {key: value for key, value in contexts.items() if key in _KEPT_CONTEXTS}
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, Mapping):
        values = [
            scrub_breadcrumb(dict(crumb)) for crumb in breadcrumbs.get("values") or () if isinstance(crumb, Mapping)
        ]
        event["breadcrumbs"] = {"values": values}
    tags = event.get("tags")
    tags = dict(tags) if isinstance(tags, Mapping) else {}
    correlation_id = current_correlation_id()
    if correlation_id:
        tags[CORRELATION_TAG] = correlation_id
    if tags:
        event["tags"] = tags
    return event


# --------------------------------------------------------------------------- setup


def is_enabled() -> bool:
    return _enabled


def setup_error_reporting(config: Any, *, service_name: str, transport: Any = None) -> bool:
    """Start the Sentry SDK when ``ANUM_SENTRY_DSN`` is set. Returns whether it started.

    ``transport`` is for tests only (a ``sentry_sdk.transport.Transport``).
    """
    global _enabled
    dsn = configured_dsn(config)
    if not dsn:
        return False
    problem = dsn_problem(dsn)
    if problem:  # Settings already refuses this; keep the SDK from raising BadDsn.
        raise ValueError(problem)

    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    options: dict[str, Any] = {
        "dsn": dsn,
        "environment": str(config.environment),
        "server_name": service_name,
        "send_default_pii": False,
        "include_local_variables": False,
        # File, function and line only, as in telemetry.py: no source lines either.
        "include_source_context": False,
        "max_request_body_size": "never",
        # Errors only: traces stay with OpenTelemetry, and no session or log stream.
        "traces_sample_rate": None,
        "auto_session_tracking": False,
        "enable_logs": False,
        "max_breadcrumbs": 30,
        "before_send": scrub_event,
        "before_breadcrumb": scrub_breadcrumb,
        "integrations": [LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    }
    if transport is not None:
        options["transport"] = transport
    sentry_sdk.init(**options)
    _enabled = True
    logger.info("error reporting on", extra={"anum_error_reporting_service": service_name})
    return True


def tag_tenant(tenant_id: str | None) -> None:
    """Tag the current request's events with its opaque tenant id and correlation id.

    Set here, while the request context is active: the SDK reports unhandled errors from
    the outermost middleware, after CorrelationIdMiddleware has reset its context.
    """
    if not _enabled or not tenant_id:
        return
    import sentry_sdk

    sentry_sdk.set_tag(TENANT_TAG, tenant_id)
    correlation_id = current_correlation_id()
    if correlation_id:
        sentry_sdk.set_tag(CORRELATION_TAG, correlation_id)


def shutdown_error_reporting(timeout_seconds: float = 2.0) -> None:
    """Send what is queued before the process exits."""
    global _enabled
    if not _enabled:
        return
    import sentry_sdk

    sentry_sdk.flush(timeout=timeout_seconds)
    _enabled = False


__all__ = [
    "CORRELATION_TAG",
    "TENANT_TAG",
    "configured_dsn",
    "dsn_problem",
    "insecure_dsn_problem",
    "is_enabled",
    "scrub_breadcrumb",
    "scrub_event",
    "setup_error_reporting",
    "shutdown_error_reporting",
    "tag_tenant",
]
