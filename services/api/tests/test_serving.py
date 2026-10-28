"""Startup work that would otherwise pause the event loop (anum_api/serving.py).

FastAPI builds a route's dependency graph the first time a request walks past its router,
and a full garbage collection over the startup heap stops every thread for half a second
or more. The lifespan does both before serving (docs/realtime.md#garbage-collection).
"""

from __future__ import annotations

import asyncio
import gc
from collections.abc import Iterator

import pytest
from fastapi import APIRouter, FastAPI, routing
from fastapi.testclient import TestClient

from anum_api import main, serving


@pytest.fixture
def unfrozen() -> Iterator[None]:
    yield
    gc.unfreeze()


def _small_app() -> FastAPI:
    app = FastAPI()
    inner = APIRouter(prefix="/inner")

    @inner.get("/item/{item_id}")
    def item(item_id: str, limit: int = 1) -> dict[str, object]:
        return {"id": item_id, "limit": limit}

    outer = APIRouter(prefix="/outer")
    outer.include_router(inner)
    app.include_router(outer)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_the_warm_up_builds_every_route_before_the_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _small_app()
    built: list[str] = []
    real = routing.get_dependant

    def counting(*, path: str, **kwargs):  # type: ignore[no-untyped-def]
        built.append(path)
        return real(path=path, **kwargs)

    monkeypatch.setattr(routing, "get_dependant", counting)
    asyncio.run(serving.warm_routes(app.router))
    # Routes declared on the app itself are built when declared; included ones lazily.
    assert built == ["/outer/inner/item/{item_id}"]

    built.clear()
    client = TestClient(app)
    assert client.get("/outer/inner/item/a?limit=2").json() == {"id": "a", "limit": 2}
    assert client.get("/health").status_code == 200
    assert built == [], f"routes built on a request: {built}"


def test_the_warm_up_runs_no_endpoint_even_for_a_catch_all_route(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    calls: list[str] = []
    pages = APIRouter()

    @pages.get("/{anything:path}")
    def catch_all(anything: str) -> dict[str, str]:
        calls.append(anything)
        return {}

    app.include_router(pages)

    built: list[str] = []
    real = routing.get_dependant

    def counting(*, path: str, **kwargs):  # type: ignore[no-untyped-def]
        built.append(path)
        return real(path=path, **kwargs)

    monkeypatch.setattr(routing, "get_dependant", counting)
    asyncio.run(serving.warm_routes(app.router))
    assert built == ["/{anything}"]  # the catch-all was built (FastAPI keeps the path without ":path")
    assert calls == []


def test_prepare_for_serving_freezes_the_startup_heap(unfrozen: None) -> None:
    asyncio.run(serving.prepare_for_serving(_small_app().router))
    assert gc.get_freeze_count() > 0


def test_the_lifespan_prepares_serving_once_on_start(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    async def record(router: object) -> None:
        calls.append(router)

    monkeypatch.setattr(main, "prepare_for_serving", record)
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
    assert calls == [main.app.router]
