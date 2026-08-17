"""A minimal, single-process rate limiter.

This is a fixed-window counter, keyed by client IP, held entirely in this
process's memory. That makes it real protection for a single-instance
deployment, but it does NOT coordinate across multiple API replicas — each
instance enforces its own independent limit. A multi-instance production
deployment needs a shared store (the `valkey` service already defined in
infra/docker/compose.yaml, unused by the app today, is the natural fit) for
the limit to hold across instances; that is not implemented here.

Disabled by default (`ANUM_RATE_LIMIT_ENABLED=false`) so it never changes
behavior for existing local/dev/test usage unless explicitly turned on.
"""

from __future__ import annotations

import time
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .errors import ErrorCode
from .request_context import CORRELATION_ID_HEADER, correlation_id_from_request


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
    ) -> None:
        super().__init__(app)
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
