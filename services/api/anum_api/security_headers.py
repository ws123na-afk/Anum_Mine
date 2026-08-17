"""Standard security response headers, applied to every response.

This is a browser-facing defense-in-depth layer (clickjacking, MIME
sniffing, referrer leakage, unwanted permissions) - it does not replace
authentication, authorization, or input validation, all of which live
elsewhere.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, hsts: bool = False) -> None:  # noqa: ANN001
        super().__init__(app)
        # HSTS only makes sense once the deployment is actually served over
        # HTTPS end-to-end - enabling it behind plain HTTP (e.g. local dev)
        # would tell browsers to *require* HTTPS for this host, which breaks
        # local http://localhost access. Off by default; production
        # deployments behind TLS should enable it explicitly.
        self._hsts = hsts

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if self._hsts:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
