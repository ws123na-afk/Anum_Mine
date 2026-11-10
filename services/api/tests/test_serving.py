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


def test_the_default_executor_is_explicit_and_sized_by_the_setting() -> None:
    """``asyncio.to_thread`` used Python's default pool: min(32, CPUs + 4), 6 on a 2-CPU server."""
    import threading

    async def scenario() -> tuple[object, int, str]:
        executor = serving.use_worker_threads(9)
        loop = asyncio.get_running_loop()
        gate = threading.Barrier(9, timeout=5)

        def wait_for_the_others() -> str:
            gate.wait()  # passes only when nine calls run at the same time
            return threading.current_thread().name

        names = await asyncio.gather(*(asyncio.to_thread(wait_for_the_others) for _ in range(9)))
        return loop._default_executor is executor, executor._max_workers, names[0]  # type: ignore[attr-defined]

    is_default, size, thread_name = asyncio.run(scenario())
    assert is_default and size == 9 and thread_name.startswith("anum-worker")


def test_the_lifespan_sets_the_worker_threads_before_anything_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from anum_api.settings import Settings

    assert Settings().worker_threads == 32
    for refused in (0, 3, 257):
        with pytest.raises(ValidationError):
            Settings(worker_threads=refused)
    order: list[str] = []
    real_start = main.event_runtime.start

    async def started() -> None:
        order.append("event_runtime")
        await real_start()

    monkeypatch.setattr(main, "use_worker_threads", lambda size: order.append(f"threads:{size}"))
    monkeypatch.setattr(main.event_runtime, "start", started)
    monkeypatch.setattr(main.settings, "worker_threads", 12)
    with TestClient(main.app):
        pass
    assert order[:2] == ["threads:12", "event_runtime"]


def test_a_scheduler_stop_waits_for_a_running_pass_only_so_long(caplog: pytest.LogCaptureFixture) -> None:
    """stop() waited for the pass thread with no limit (automation, team reminders, lifecycle)."""
    import logging
    import threading
    import time

    from anum_api.automation import AutomationScheduler
    from anum_api.team_work import TeamReminderScheduler
    from anum_api.tenant_lifecycle import LifecycleRunner

    async def scenario() -> tuple[list[float], bool, bool]:
        release = threading.Event()
        waits: list[float] = []
        for scheduler in (AutomationScheduler.__new__(AutomationScheduler), TeamReminderScheduler.__new__(TeamReminderScheduler),
                          LifecycleRunner.__new__(LifecycleRunner)):
            scheduler._task, scheduler._stop = None, None  # type: ignore[attr-defined]
            scheduler._pass = asyncio.ensure_future(asyncio.to_thread(release.wait, 5))  # type: ignore[attr-defined]
            started = time.monotonic()
            await scheduler.stop(pass_timeout=0.05)
            waits.append(time.monotonic() - started)
        stuck = asyncio.ensure_future(asyncio.to_thread(release.wait, 5))
        still_running = await serving.wait_for_pass(stuck, name="example", timeout=0.05)
        release.set()  # the pass was not cancelled: it finishes by itself
        finished = await serving.wait_for_pass(stuck, name="example", timeout=5)
        return waits, still_running, finished

    with caplog.at_level(logging.WARNING, logger="anum_api.serving"):
        waits, still_running, finished = asyncio.run(scenario())
    assert max(waits) < 2, waits
    assert still_running is False and finished is True
    named = [record.anum_serving["scheduler"] for record in caplog.records if record.message == "scheduler_pass_still_running_at_stop"]
    assert named == ["automation scheduler", "team reminders", "organisation lifecycle", "example"]
    assert asyncio.run(serving.wait_for_pass(None, name="none")) is True
    assert serving.PASS_STOP_TIMEOUT_SECONDS == 30.0
