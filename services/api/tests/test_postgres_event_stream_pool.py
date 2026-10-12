"""Open event streams must not exhaust the PostgreSQL pool (the reported web freeze).

A real uvicorn server runs the API against PostgreSQL with a deliberately small pool
(2 connections, no overflow). More event streams than the pool holds stay open, the
way browser tabs keep ``/api/v1/events/stream`` open, and the API must still answer
other requests quickly. Before the fix each stream held its request session "idle in
transaction" for its whole life and every poll waited for a connection on the event
loop, so ``GET /api/v1/tasks`` and even ``/health`` hung.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.settings import settings

pytestmark = pytest.mark.database

HEADERS = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
POOL_SIZE = 2


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def server(
    test_database_url: str, database_engine: Engine, seed_scopes: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "insert into workspace_memberships (tenant_id, workspace_id, user_id, role) "
                "values (:tenant_id, :workspace_id, 'user_test', 'owner')"
            ),
            {"tenant_id": TENANT_A, "workspace_id": WORKSPACE_A},
        )
    # The production pool shape, only smaller: a bounded QueuePool with a short wait.
    engine = create_engine(test_database_url, pool_size=POOL_SIZE, max_overflow=0, pool_timeout=5, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield base
    uv.should_exit = True
    thread.join(timeout=15)
    engine.dispose()


def _hold_stream(base: str, opened: threading.Event, stop: threading.Event) -> None:
    try:
        with httpx.stream("GET", f"{base}/api/v1/events/stream", headers=HEADERS, timeout=httpx.Timeout(5, read=None)) as response:
            opened.set()
            for _ in response.iter_raw():
                if stop.is_set():
                    return
    except httpx.HTTPError:
        opened.set()


def test_api_keeps_answering_with_more_open_streams_than_pool_connections(server: str) -> None:
    stop = threading.Event()
    streams = []
    for _ in range(POOL_SIZE * 3):
        opened = threading.Event()
        worker = threading.Thread(target=_hold_stream, args=(server, opened, stop), daemon=True)
        worker.start()
        assert opened.wait(10), "an event stream did not open"
        streams.append(worker)
    try:
        time.sleep(1.5)  # several poll cycles with every stream open
        started = time.monotonic()
        health = httpx.get(f"{server}/health", timeout=10)
        health_seconds = time.monotonic() - started
        started = time.monotonic()
        tasks = httpx.get(f"{server}/api/v1/tasks", headers=HEADERS, timeout=10)
        tasks_seconds = time.monotonic() - started
        created = httpx.post(f"{server}/api/v1/tasks", headers=HEADERS, json={"title": "Still here", "prompt": "Answer while streams are open"}, timeout=10)
    finally:
        stop.set()
    assert health.status_code == 200 and health_seconds < 2, f"/health took {health_seconds:.1f}s"
    assert tasks.status_code == 200 and tasks_seconds < 2, f"GET /api/v1/tasks: {tasks.status_code} in {tasks_seconds:.1f}s"
    assert created.status_code == 201, created.text
