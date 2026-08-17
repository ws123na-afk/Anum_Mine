"""A minimal, single-process rate limiter, with an optional Valkey backend.

This is a fixed-window counter, keyed by client IP. By default it is held
entirely in this process's memory: real protection for a single-instance
deployment, but it does NOT coordinate across multiple API replicas — each
instance enforces its own independent limit. Passing `redis_client` (see
anum_api/settings.py `valkey_url`) switches the counter to Valkey, so the
limit is shared across replicas and survives restarts; leaving it unset (the
default) keeps today's in-memory-only behavior exactly as it is.

Disabled by default (`ANUM_RATE_LIMIT_ENABLED=false`) so it never changes
behavior for existing local/dev/test usage unless explicitly turned on.
"""

from __future__ import annotations

import time
from threading import Lock

import redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .errors import ErrorCode
from .request_context import CORRELATION_ID_HEADER, correlation_id_from_request, get_correlation_id


class _FixedWindowCounter:
    """Tracks a request count per key within the current fixed time window."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._lock = Lock()
        self._windows: dict[str, tuple[int, int]] = {}  # key -> (window_index, count)

    def hit(self, key: str) -> tuple[bool, int]:
        """Record one request for `key`. Returns (allowed, seconds_until_reset)."""

        now = time.time()
        window_index = int(now // self._window_seconds)
        window_ends_at = (window_index + 1) * self._window_seconds
        seconds_until_reset = max(0, int(window_ends_at - now))

        with self._lock:
            stored_window, count = self._windows.get(key, (window_index, 0))
            if stored_window != window_index:
                count = 0
                stored_window = window_index
            count += 1
            self._windows[key] = (stored_window, count)

        return count <= self._limit, seconds_until_reset


# INCR then EXPIRE as two separate round trips would leave a window key with
# no TTL forever if the process died between them; a Lua script runs the
# increment-and-maybe-expire as one atomic step on the server instead.
_HIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return count
"""


class _ValkeyFixedWindowCounter:
    """Same `.hit()` contract as `_FixedWindowCounter`, backed by Valkey so
    the count is shared across every process/replica using this client."""

    def __init__(self, *, limit: int, window_seconds: int, client: redis.Redis) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._client = client
        self._hit_script = client.register_script(_HIT_SCRIPT)

    def hit(self, key: str) -> tuple[bool, int]:
        """Record one request for `key`. Returns (allowed, seconds_until_reset)."""

        now = time.time()
        window_index = int(now // self._window_seconds)
        window_ends_at = (window_index + 1) * self._window_seconds
        seconds_until_reset = max(0, int(window_ends_at - now))

        redis_key = f"anum:ratelimit:{key}:{window_index}"
        count = self._hit_script(keys=[redis_key], args=[self._window_seconds])

        return count <= self._limit, seconds_until_reset


def _client_key(request: Request, *, trust_forwarded_for: bool) -> str:
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Only meaningful behind a reverse proxy that itself sets/overwrites
            # this header (never trust it directly from the public internet -
            # it is trivially spoofable otherwise). The first entry is the
            # original client as seen by the nearest trusted proxy.
            return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,  # noqa: ANN001 - Starlette's BaseHTTPMiddleware base signature
        *,
        limit: int,
        window_seconds: int,
        trust_forwarded_for: bool = False,
        redis_client: redis.Redis | None = None,
    ) -> None:
        super().__init__(app)
        self._counter: _FixedWindowCounter | _ValkeyFixedWindowCounter
        if redis_client is not None:
            self._counter = _ValkeyFixedWindowCounter(
                limit=limit, window_seconds=window_seconds, client=redis_client
            )
        else:
            self._counter = _FixedWindowCounter(limit=limit, window_seconds=window_seconds)
        self._trust_forwarded_for = trust_forwarded_for

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        key = _client_key(request, trust_forwarded_for=self._trust_forwarded_for)
        allowed, retry_after = self._counter.hit(key)

        if not allowed:
            # Reuse the correlation ID CorrelationIdMiddleware already put on
            # this request (so the 429 body and the X-Correlation-ID header
            # agree - main.py always registers CorrelationIdMiddleware
            # around this one). Fall back to minting a fresh one when this
            # middleware is used standalone, e.g. in its own unit tests,
            # where no CorrelationIdMiddleware ran first.
            try:
                correlation_id = get_correlation_id(request)
            except RuntimeError:
                correlation_id = correlation_id_from_request(request)
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": ErrorCode.RATE_LIMITED.value,
                        "message": "Too many requests. Please slow down and retry shortly.",
                        "correlation_id": correlation_id,
                        "details": [],
                    }
                },
                headers={
                    "Retry-After": str(retry_after),
                    CORRELATION_ID_HEADER: correlation_id,
                },
            )

        return await call_next(request)
