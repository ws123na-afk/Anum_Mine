from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


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


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        correlation_id = correlation_id_from_request(request)
        request.state.correlation_id = correlation_id
        token: Token[str | None] = _correlation_id.set(correlation_id)
        try:
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = correlation_id
            return response
        finally:
            _correlation_id.reset(token)
