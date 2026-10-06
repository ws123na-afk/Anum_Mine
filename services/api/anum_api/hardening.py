"""HTTP hardening for the ANUM API.

This module holds the edge protections described in ``docs/security.md``:

* request body size limits (413),
* per-client rate limiting with a pluggable backend (429 + ``Retry-After``),
* security response headers (CSP, nosniff, referrer policy, HSTS outside local),
* fail-fast startup checks that refuse development defaults outside ``local``.

The middlewares are pure ASGI so they never buffer streaming (SSE) responses.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Protocol
from urllib.parse import urlsplit

from .errors import ErrorCode
from .request_context import CORRELATION_ID_HEADER, is_valid_correlation_id, new_correlation_id

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

LOCAL_ENVIRONMENT = "local"
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})

# Credentials that only exist in infra/docker/compose.yaml and the .env.example files.
DEFAULT_DEV_DATABASE_CREDENTIALS = frozenset({("anum", "anum")})
DEFAULT_DEV_SECRETS = frozenset({"anum-local-secret", "anum", "admin"})
# 0.0.0.0 and :: are caught by the ip_address() check below.
LOCAL_HOSTNAMES = frozenset({"localhost", "host.docker.internal"})

API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
HSTS_VALUE = "max-age=63072000; includeSubDomains"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


async def _send_error(
    scope: Scope,
    send: Send,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    extra_headers: Iterable[tuple[str, str]] = (),
) -> None:
    """Send the standard ANUM error envelope without going through FastAPI."""
    incoming = _header(scope, CORRELATION_ID_HEADER.lower().encode("latin-1"))
    correlation_id = incoming if incoming and is_valid_correlation_id(incoming) else new_correlation_id()
    body = json.dumps(
        {
            "error": {
                "code": code,
                "message": message,
                "correlation_id": correlation_id,
                "details": [],
            }
        }
    ).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("latin-1")),
        (CORRELATION_ID_HEADER.lower().encode("latin-1"), correlation_id.encode("latin-1")),
    ]
    headers.extend((name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in extra_headers)
    await send({"type": "http.response.start", "status": status_code, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


# ---------------------------------------------------------------------------
# Request body size limit
# ---------------------------------------------------------------------------


class _BodyTooLarge(Exception):
    pass


@dataclass(frozen=True)
class BodyLimitRule:
    """A larger body allowance for one method and path prefix (for example uploads)."""

    method: str
    path_prefix: str
    max_bytes: int


class BodySizeLimitMiddleware:
    """Reject requests whose body exceeds the configured limit with 413.

    ``Content-Length`` is checked up front; chunked bodies are counted as they stream
    in, so a missing or dishonest ``Content-Length`` cannot bypass the limit.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        rules: Iterable[BodyLimitRule] = (),
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes
        self.rules = tuple(rules)

    def limit_for(self, method: str, path: str) -> int:
        for rule in self.rules:
            if method == rule.method and (
                path == rule.path_prefix or path.startswith(rule.path_prefix.rstrip("/") + "/")
            ):
                return rule.max_bytes
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.limit_for(scope.get("method", "GET"), scope.get("path", ""))
        declared = _header(scope, b"content-length")
        if declared is not None:
            try:
                declared_length = int(declared)
            except ValueError:
                await _send_error(
                    scope, send, status_code=400, code=ErrorCode.BAD_REQUEST, message="Invalid Content-Length"
                )
                return
            if declared_length > limit:
                await self._reject(scope, send, limit)
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if response_started:
                raise
            await self._reject(scope, send, limit)

    @staticmethod
    async def _reject(scope: Scope, send: Send, limit: int) -> None:
        await _send_error(
            scope,
            send,
            status_code=413,
            code=ErrorCode.PAYLOAD_TOO_LARGE,
            message=f"Request body exceeds the {limit} byte limit",
        )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    remaining: int = 0


class RateLimitBackend(Protocol):
    """Storage for rate-limit state.

    The in-memory backend is per process. A Valkey backend (Stage 3) implements the
    same method with an atomic script so limits hold across API replicas.
    """

    def acquire(self, key: str, *, now: float | None = None) -> RateLimitDecision: ...


class InMemoryTokenBucket:
    """Token bucket per key, bounded so unique client keys cannot exhaust memory."""

    def __init__(
        self,
        *,
        rate_per_second: float,
        burst: int,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_second <= 0 or burst <= 0 or max_keys <= 0:
            raise ValueError("rate_per_second, burst and max_keys must be positive")
        self.rate_per_second = rate_per_second
        self.burst = burst
        self.max_keys = max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def acquire(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        current = self._clock() if now is None else now
        with self._lock:
            tokens, updated = self._buckets.pop(key, (float(self.burst), current))
            tokens = min(float(self.burst), tokens + max(0.0, current - updated) * self.rate_per_second)
            if tokens >= 1.0:
                tokens -= 1.0
                decision = RateLimitDecision(allowed=True, remaining=int(tokens))
            else:
                wait = (1.0 - tokens) / self.rate_per_second
                decision = RateLimitDecision(allowed=False, retry_after_seconds=max(1, math.ceil(wait)))
            self._buckets[key] = (tokens, current)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return decision


def client_key(scope: Scope) -> str:
    """Identify the caller by network address.

    Behind a load balancer, uvicorn's ``--proxy-headers`` with ``FORWARDED_ALLOW_IPS``
    set to the proxy range rewrites ``scope["client"]`` to the real client address, so
    spoofed ``X-Forwarded-For`` values from untrusted peers are ignored.
    """
    client = scope.get("client")
    if client and client[0]:
        return f"ip:{client[0]}"
    return "ip:unknown"


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        backend: RateLimitBackend,
        exempt_paths: Iterable[str] = ("/health",),
        key_func: Callable[[Scope], str] = client_key,
    ) -> None:
        self.app = app
        self.backend = backend
        self.exempt_paths = frozenset(exempt_paths)
        self.key_func = key_func

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        decision = self.backend.acquire(self.key_func(scope))
        if not decision.allowed:
            await _send_error(
                scope,
                send,
                status_code=429,
                code=ErrorCode.RATE_LIMITED,
                message="Too many requests",
                extra_headers=[("Retry-After", str(decision.retry_after_seconds))],
            )
            return
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def security_headers(*, environment: str) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"DENY"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    ]
    if environment != LOCAL_ENVIRONMENT:
        headers.append((b"strict-transport-security", HSTS_VALUE.encode("latin-1")))
    return headers


class SecurityHeadersMiddleware:
    """Add security headers to every HTTP response, including errors and streams.

    The API serves JSON and SSE only, so its CSP denies everything. The interactive
    docs pages (local only) load Swagger UI assets and keep FastAPI's defaults.
    """

    def __init__(self, app: ASGIApp, *, environment: str) -> None:
        self.app = app
        self.environment = environment
        self.headers = security_headers(environment=environment)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        apply_csp = scope.get("path") not in DOCS_PATHS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {key.lower() for key, _ in message.get("headers", [])}
                extra = [(key, value) for key, value in self.headers if key not in existing]
                if apply_csp and b"content-security-policy" not in existing:
                    extra.append((b"content-security-policy", API_CONTENT_SECURITY_POLICY.encode("latin-1")))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# Startup policy
# ---------------------------------------------------------------------------


class InsecureConfigurationError(RuntimeError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            "Refusing to start with insecure configuration: " + "; ".join(problems)
        )


def _is_local_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip("[]").lower()
    if host in LOCAL_HOSTNAMES or host.endswith(".localhost"):
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def insecure_configuration_problems(config: Any) -> list[str]:
    """Return every reason ``config`` must not run outside ``environment=local``."""
    if config.environment == LOCAL_ENVIRONMENT:
        return []
    problems: list[str] = []

    for origin in config.cors_origins:
        if "*" in origin:
            problems.append(f"ANUM_CORS_ORIGINS contains a wildcard origin {origin!r}")
            continue
        parts = urlsplit(origin)
        if _is_local_host(parts.hostname):
            problems.append(f"ANUM_CORS_ORIGINS contains a local origin {origin!r}")
        elif parts.scheme != "https":
            problems.append(f"ANUM_CORS_ORIGINS origin {origin!r} must use https")

    database = urlsplit(config.database_url)
    if (database.username, database.password) in DEFAULT_DEV_DATABASE_CREDENTIALS:
        problems.append("ANUM_DATABASE_URL uses the default development credentials from compose")

    for name in ("s3_secret_key", "external_webhook_api_key", "model_api_key"):
        value = getattr(config, name, None)
        if value and value in DEFAULT_DEV_SECRETS:
            problems.append(f"ANUM_{name.upper()} uses a default development credential")

    if config.max_request_body_bytes <= 0 or config.rate_limit_requests_per_minute <= 0:
        problems.append("Request size and rate limits must be positive outside local")
    if not config.rate_limit_enabled:
        problems.append("ANUM_RATE_LIMIT_ENABLED must stay true outside local")
    return problems


def enforce_startup_policy(config: Any) -> None:
    problems = insecure_configuration_problems(config)
    if problems:
        raise InsecureConfigurationError(problems)


def docs_routes(config: Any) -> dict[str, str | None]:
    """Interactive API docs are a development aid; they are not served outside local."""
    if config.environment == LOCAL_ENVIRONMENT:
        return {}
    return {"docs_url": None, "redoc_url": None}


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def install_hardening(app: Any, config: Any, *, upload_path_prefix: str, upload_max_bytes: int) -> None:
    """Add the hardening middlewares to ``app``.

    Starlette runs the most recently added middleware first, so the resulting order
    for a request is: security headers -> rate limit -> body size -> inner app.
    ``main.py`` calls this before adding CORS, so CORS stays outermost: preflights are
    answered before rate limiting, and 413/429 responses still carry CORS headers so
    browser clients can read them.
    """
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=config.max_request_body_bytes,
        rules=[BodyLimitRule("POST", upload_path_prefix, max(upload_max_bytes, config.max_request_body_bytes))],
    )
    app.state.rate_limiter = None
    if config.rate_limit_enabled:
        limiter = InMemoryTokenBucket(
            rate_per_second=config.rate_limit_requests_per_minute / 60.0,
            burst=config.rate_limit_burst,
        )
        app.state.rate_limiter = limiter
        app.add_middleware(RateLimitMiddleware, backend=limiter)
    app.add_middleware(SecurityHeadersMiddleware, environment=config.environment)


__all__ = [
    "API_CONTENT_SECURITY_POLICY",
    "BodyLimitRule",
    "BodySizeLimitMiddleware",
    "InMemoryTokenBucket",
    "InsecureConfigurationError",
    "RateLimitBackend",
    "RateLimitDecision",
    "RateLimitMiddleware",
    "SecurityHeadersMiddleware",
    "client_key",
    "docs_routes",
    "enforce_startup_policy",
    "insecure_configuration_problems",
    "install_hardening",
    "security_headers",
]
