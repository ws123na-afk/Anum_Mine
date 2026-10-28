"""Startup steps that keep one-time and periodic work off the serving event loop.

Two pauses used to stop every request of the process (docs/realtime.md#garbage-collection):

* FastAPI builds each route's dependency graph and parameter fields lazily, the first
  time a request walks past its router, on the event loop. With every router of this app
  that is several seconds of CPU on the first request after a start.
* A full garbage collection walks every tracked object while holding the GIL, so it stops
  all threads, the event loop included. Imports and the route state above leave about
  900,000 long-lived objects, and each full collection over them took 0.5 to 0.9 s.

``prepare_for_serving`` builds the route state once, at startup, then moves everything
alive into the collector's permanent generation (``gc.freeze``): later full collections
only walk objects created after startup.
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message

logger = logging.getLogger(__name__)

# A path and a method no route serves: the router tries every route (building each one's
# state) and answers 404 or 405 itself. A route whose path matches (a catch-all) is only a
# partial match for this method, so no endpoint runs; no middleware runs either.
WARM_UP_PATH = "/__anum_warm_up_routes__"
WARM_UP_METHOD = "ANUM-WARM-UP"


async def warm_routes(router: ASGIApp) -> None:
    """Send one request no route serves straight to the router (not the middleware stack)."""
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": WARM_UP_METHOD,
        "scheme": "http",
        "path": WARM_UP_PATH,
        "raw_path": WARM_UP_PATH.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": None,
        "server": None,
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_: Message) -> None:
        return None

    await router(scope, receive, send)


def freeze_heap() -> int:
    """Collect once, then exclude every surviving object from later collections."""
    gc.collect()
    gc.freeze()
    return gc.get_freeze_count()


async def prepare_for_serving(router: ASGIApp) -> None:
    """Called by the lifespan right before the API starts serving (and by test servers that
    run without the lifespan, so they serve like production)."""
    started = time.monotonic()
    try:
        await warm_routes(router)
    except Exception:  # a route that cannot be prepared now fails on its first request instead
        logger.warning("serving_warm_up_failed", exc_info=True)
    frozen = freeze_heap()
    logger.info(
        "serving_prepared",
        extra={"anum_serving": {"frozen_objects": frozen, "seconds": round(time.monotonic() - started, 2)}},
    )
