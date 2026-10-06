from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


CORRELATION_ID_HEADER = "X-Correlation-ID"
_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def is_valid_correlation_id(value: str | None) -> bool:
    """Return whether a value is safe to use in headers, logs, and persistence."""
    return value is not None and _CORRELATION_ID_PATTERN.fullmatch(value) is not None


def new_correlation_id() -> str:
    return f"corr_{uuid4().hex}"


def correlation_id_from_request(request: Request) -> str:
    incoming = request.headers.get(CORRELATION_ID_HEADER)
    return incoming if is_valid_correlation_id(incoming) else new_correlation_id()


def current_correlation_id() -> str | None:
    """The active request's correlation ID, or ``None`` outside a request (no error)."""
    return _correlation_id.get()


def get_correlation_id(request: Request | None = None) -> str:
    """Get the correlation ID for the active request without shared mutable state."""
    if request is not None:
        request_id = getattr(request.state, "correlation_id", None)
        if request_id is not None:
            return request_id

    correlation_id = _correlation_id.get()
    if correlation_id is None:
        raise RuntimeError("No correlation ID is active outside a request context")
    return correlation_id


class CorrelationIdMiddleware:
    """Attach a correlation ID to the request context and the response headers.

    A plain ASGI middleware, not ``BaseHTTPMiddleware``: that wrapper hides client
    disconnects from ``request.is_disconnected()``, so long-lived streams such as
    ``/api/v1/events/stream`` would never notice their client left and would run
    (and block server shutdown) forever.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(CORRELATION_ID_HEADER)
        correlation_id = incoming if is_valid_correlation_id(incoming) else new_correlation_id()
        scope.setdefault("state", {})["correlation_id"] = correlation_id

        async def send_with_correlation_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[CORRELATION_ID_HEADER] = correlation_id
            await send(message)

        token: Token[str | None] = _correlation_id.set(correlation_id)
        try:
            await self.app(scope, receive, send_with_correlation_id)
        finally:
            _correlation_id.reset(token)
