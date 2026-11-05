"""The browser service: runs the kernel in its own container (docs/browser-research.md#the-browser-service).

``python -m anum_api.browser_research.service`` serves, on port 8090 of an internal network:

* ``GET /healthz``: 200 when Chromium can start here, 503 with the reason otherwise.
* ``POST /v1/browse`` with ``Authorization: Bearer <ANUM_BROWSER_SERVICE_TOKEN>`` and
  ``{"url": "https://..."}``: reads that one page (``kernel.browse``) and answers its outcome.
* with ``ANUM_BROWSER_ACTION_MODE=1`` only, the action browser's session routes
  (``anum_api.action_browser.service_routes``, docs/action-browser.md), behind the same token.

Why a separate container: a page's JavaScript runs in Chromium, and a browser exploit must
not land next to the API's database login, secrets key or provider keys. This process reads
no ANUM settings; its only secret is the token the API sends. Calls run one at a time
(``ANUM_BROWSER_CONCURRENCY``, default 1); a call that waits longer than 20 seconds for a
free slot is answered 429.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .kernel import Limits, browse, chromium_available

logger = logging.getLogger("anum.browser_service")

MAX_BODY_BYTES = 4096
QUEUE_WAIT_SECONDS = 20.0
MIN_TOKEN_CHARS = 32


class BrowserService:
    def __init__(
        self, token: str, *, concurrency: int = 1, executable_path: str | None = None, browse_fn: Any = None, action_mode: bool = False,
    ) -> None:
        if len(token) < MIN_TOKEN_CHARS:
            raise ValueError(f"ANUM_BROWSER_SERVICE_TOKEN must be at least {MIN_TOKEN_CHARS} characters")
        self._token = token.encode()
        self._slots = asyncio.Semaphore(max(1, concurrency))
        self.executable_path = executable_path or None
        self._browse = browse_fn or browse
        self.action_mode = action_mode

    def authorised(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), self._token)

    async def healthz(self, _: Request) -> JSONResponse:
        ok, detail = chromium_available(self.executable_path)
        return JSONResponse(
            {"status": "ok" if ok else "unavailable", "detail": detail, "action_mode": self.action_mode}, status_code=200 if ok else 503,
        )

    async def browse(self, request: Request) -> JSONResponse:
        if not self.authorised(request):
            return JSONResponse({"detail": "Not authorised."}, status_code=401)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request too large."}, status_code=413)
        try:
            payload = json.loads(body)
            url = payload["url"]
            if not isinstance(url, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"detail": "Send {\"url\": \"https://...\"}."}, status_code=422)
        ok, detail = chromium_available(self.executable_path)
        if not ok:
            return JSONResponse({"outcome": "unavailable", "message": detail, "url": url[:2048], "final_url": "", "stats": {}}, status_code=503)
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=QUEUE_WAIT_SECONDS)
        except TimeoutError:
            return JSONResponse({"detail": "The browser is busy; try again shortly."}, status_code=429)
        try:
            result = await self._browse(url, limits=Limits(), executable_path=self.executable_path)
        except Exception as exc:  # a browser crash is an outcome, never a stack trace
            logger.warning("browse_failed error_class=%s", type(exc).__name__)
            result = {"outcome": "browser_error", "message": "The browser stopped while reading the page.", "url": url[:2048], "final_url": "", "stats": {}}
        finally:
            self._slots.release()
        # Logged without the address or any content: outcome and counts only.
        logger.info("browse outcome=%s requests=%s blocked=%s", result.get("outcome"), (result.get("stats") or {}).get("requests"), (result.get("stats") or {}).get("blocked"))
        return JSONResponse(result)


def create_app(service: BrowserService, action_sessions: Any = None) -> Starlette:
    routes = [
        Route("/healthz", service.healthz, methods=["GET"]),
        Route("/v1/browse", service.browse, methods=["POST"]),
    ]
    lifespan = None
    if action_sessions is not None:
        from contextlib import asynccontextmanager

        from ..action_browser.service_routes import action_routes

        routes.extend(action_routes(service.authorised, action_sessions))

        @asynccontextmanager
        async def lifespan(_: Starlette) -> Any:
            reaper = asyncio.ensure_future(action_sessions.reaper())
            try:
                yield
            finally:
                reaper.cancel()
                await action_sessions.close_all()

    return Starlette(routes=routes, lifespan=lifespan)


def app_from_environment() -> Starlette:
    executable_path = os.environ.get("ANUM_BROWSER_CHROMIUM_PATH") or None
    action_mode = os.environ.get("ANUM_BROWSER_ACTION_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    service = BrowserService(
        os.environ.get("ANUM_BROWSER_SERVICE_TOKEN", ""),
        concurrency=int(os.environ.get("ANUM_BROWSER_CONCURRENCY", "1") or 1),
        executable_path=executable_path,
        action_mode=action_mode,
    )
    sessions = None
    if action_mode:
        from ..action_browser.kernel import SessionLimits
        from ..action_browser.service_routes import ActionSessions

        sessions = ActionSessions(
            slots=int(os.environ.get("ANUM_BROWSER_ACTION_SESSIONS", "1") or 1),
            executable_path=executable_path,
            limits=SessionLimits(
                idle_seconds=float(os.environ.get("ANUM_BROWSER_ACTION_IDLE_SECONDS", "900") or 900),
                max_seconds=float(os.environ.get("ANUM_BROWSER_ACTION_MAX_SECONDS", "1800") or 1800),
            ),
        )
    return create_app(service, sessions)


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app_from_environment(), host="0.0.0.0", port=int(os.environ.get("ANUM_BROWSER_PORT", "8090")), server_header=False)  # nosec B104: the internal network only


if __name__ == "__main__":  # pragma: no cover
    main()
