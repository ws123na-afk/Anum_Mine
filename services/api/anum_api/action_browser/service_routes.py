"""Session routes of the browser service in action mode (docs/action-browser.md#the-browser-service-in-action-mode).

Mounted beside ``/v1/browse`` only when the service runs with ``ANUM_BROWSER_ACTION_MODE=1``,
behind the same bearer token. No ANUM settings and no secrets here: the one value a saved
handle stands for arrives in the ``act`` request that types it and is never stored, logged or
returned. Log lines carry outcomes and counts only, never addresses or page content.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from collections.abc import Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .handles import normalize_site, parse_handle
from .kernel import ActionError, ActionSession, SessionLimits

logger = logging.getLogger("anum.browser_service.action")

MAX_BODY_BYTES = 16_384
SessionFactory = Callable[[str, list[str]], ActionSession]


class Busy(Exception):
    pass


class ActionSessions:
    """The live sessions of this service; at most ``slots`` at once, each killed when idle or old."""

    def __init__(
        self,
        *,
        slots: int = 1,
        limits: SessionLimits | None = None,
        executable_path: str | None = None,
        factory: SessionFactory | None = None,
    ) -> None:
        self.slots = max(1, slots)
        self.limits = limits or SessionLimits()
        self.executable_path = executable_path
        self._factory = factory or (lambda session_id, sites: ActionSession(session_id, sites, limits=self.limits))
        self._sessions: dict[str, ActionSession] = {}
        self._lock = asyncio.Lock()

    def __len__(self) -> int:
        return len(self._sessions)

    async def create(self, sites: list[str]) -> ActionSession:
        await self.reap()
        async with self._lock:
            if len(self._sessions) >= self.slots:
                raise Busy
            session = self._factory(secrets.token_urlsafe(18), sites)
            self._sessions[session.id] = session
        try:
            await session.start(self.executable_path)
        except Exception:
            await self.close(session.id)
            raise
        return session

    async def get(self, session_id: str) -> ActionSession | None:
        await self.reap()
        session = self._sessions.get(session_id)
        return None if session is None or session.closed else session

    async def close(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.close()
        return True

    async def reap(self) -> int:
        expired = [sid for sid, session in list(self._sessions.items()) if session.expired() or session.closed]
        for session_id in expired:
            await self.close(session_id)
        if expired:
            logger.info("action_sessions_reaped count=%s", len(expired))
        return len(expired)

    async def close_all(self) -> None:
        for session_id in list(self._sessions):
            await self.close(session_id)

    async def reaper(self, every_seconds: float = 15.0) -> None:  # pragma: no cover - background loop
        while True:
            await asyncio.sleep(every_seconds)
            with contextlib.suppress(Exception):
                await self.reap()


def action_routes(authorised: Callable[[Request], bool], sessions: ActionSessions) -> list[Route]:
    async def body_of(request: Request) -> dict[str, Any] | Response:
        if not authorised(request):
            return JSONResponse({"detail": "Not authorised."}, status_code=401)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request too large."}, status_code=413)
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            return JSONResponse({"detail": "Send a JSON object."}, status_code=422)
        return value if isinstance(value, dict) else JSONResponse({"detail": "Send a JSON object."}, status_code=422)

    async def session_of(request: Request) -> ActionSession | Response:
        if not authorised(request):
            return JSONResponse({"detail": "Not authorised."}, status_code=401)
        session = await sessions.get(request.path_params["session_id"])
        if session is None:
            return JSONResponse({"outcome": "session_gone", "message": "The browser session has ended."}, status_code=404)
        return session

    async def create(request: Request) -> Response:
        body = await body_of(request)
        if isinstance(body, Response):
            return body
        raw_sites = body.get("sites")
        sites = [site for site in (normalize_site(str(item)) for item in (raw_sites if isinstance(raw_sites, list) else [])[:20]) if site]
        if not sites:
            return JSONResponse({"detail": "Send at least one approved site."}, status_code=422)
        try:
            session = await sessions.create(sites)
        except Busy:
            return JSONResponse({"outcome": "busy", "message": "The browser is busy with another task; try again when it ends."}, status_code=429)
        except Exception as exc:  # a browser that does not start is an outcome, never a stack trace
            logger.warning("action_session_start_failed error_class=%s", type(exc).__name__)
            return JSONResponse({"outcome": "browser_error", "message": "The browser could not start."}, status_code=503)
        logger.info("action_session_started sites=%s", len(sites))
        return JSONResponse({"session_id": session.id, "sites": session.sites})

    async def snapshot(request: Request) -> Response:
        session = await session_of(request)
        if isinstance(session, Response):
            return session
        async with session.lock:
            return JSONResponse(await _guarded(session.snapshot))

    async def inspect(request: Request) -> Response:
        session = await session_of(request)
        if isinstance(session, Response):
            return session
        body = await body_of(request)
        if isinstance(body, Response):
            return body
        action = body.get("action") if isinstance(body.get("action"), dict) else {}
        async with session.lock:
            try:
                return JSONResponse(await session.inspect(action))
            except ActionError as exc:
                return JSONResponse({"outcome": exc.code, "message": str(exc)}, status_code=409)

    async def act(request: Request) -> Response:
        session = await session_of(request)
        if isinstance(session, Response):
            return session
        body = await body_of(request)
        if isinstance(body, Response):
            return body
        action = body.get("action") if isinstance(body.get("action"), dict) else {}
        expect = body.get("expect") if isinstance(body.get("expect"), dict) else None
        secret = body.get("secret") if isinstance(body.get("secret"), str) else None
        handle = parse_handle(str(action.get("value") or "")) if action.get("type") == "type" else None
        if secret is not None and handle is None:
            return JSONResponse({"detail": "A secret is only sent with the handle it stands for."}, status_code=422)
        async with session.lock:
            try:
                result = await session.act(action, expect, secret)
            except ActionError as exc:
                result = {"outcome": exc.code, "message": str(exc)}
                status = 409
            else:
                status = 200
        del secret, body
        logger.info("action_step type=%s outcome=%s", str(action.get("type") or "")[:12], result.get("outcome"))
        return JSONResponse(result, status_code=status)

    async def screenshot(request: Request) -> Response:
        session = await session_of(request)
        if isinstance(session, Response):
            return session
        async with session.lock:
            try:
                image = await session.screenshot()
            except Exception:
                return JSONResponse({"outcome": "no_picture", "message": "The browser could not take a picture of the page."}, status_code=409)
        return Response(image, media_type="image/jpeg", headers={"cache-control": "no-store"})

    async def owner_input(request: Request) -> Response:
        session = await session_of(request)
        if isinstance(session, Response):
            return session
        body = await body_of(request)
        if isinstance(body, Response):
            return body
        async with session.lock:
            try:
                await session.owner_input(body)
            except ActionError as exc:
                return JSONResponse({"outcome": exc.code, "message": str(exc)}, status_code=409)
            except Exception as exc:
                return JSONResponse({"outcome": "input_failed", "message": f"The page did not take that input ({type(exc).__name__})."}, status_code=409)
        return JSONResponse({"outcome": "done"})

    async def add_domains(request: Request) -> Response:
        session = await session_of(request)
        if isinstance(session, Response):
            return session
        body = await body_of(request)
        if isinstance(body, Response):
            return body
        sites = [site for site in (normalize_site(str(item)) for item in (body.get("sites") if isinstance(body.get("sites"), list) else [])[:5]) if site]
        session.add_sites(sites)
        return JSONResponse({"sites": session.sites})

    async def close(request: Request) -> Response:
        if not authorised(request):
            return JSONResponse({"detail": "Not authorised."}, status_code=401)
        closed = await sessions.close(request.path_params["session_id"])
        return JSONResponse({"closed": closed})

    return [
        Route("/v1/sessions", create, methods=["POST"]),
        Route("/v1/sessions/{session_id}/snapshot", snapshot, methods=["GET"]),
        Route("/v1/sessions/{session_id}/inspect", inspect, methods=["POST"]),
        Route("/v1/sessions/{session_id}/act", act, methods=["POST"]),
        Route("/v1/sessions/{session_id}/screenshot", screenshot, methods=["GET"]),
        Route("/v1/sessions/{session_id}/input", owner_input, methods=["POST"]),
        Route("/v1/sessions/{session_id}/domains", add_domains, methods=["POST"]),
        Route("/v1/sessions/{session_id}", close, methods=["DELETE"]),
    ]


async def _guarded(read: Callable[[], Any]) -> dict[str, Any]:
    try:
        return await read()
    except Exception as exc:
        return {"outcome": "read_failed", "message": f"The page could not be read ({type(exc).__name__}).", "elements": []}


__all__ = ["ActionSessions", "Busy", "action_routes"]
