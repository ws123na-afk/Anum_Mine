"""The API answers other requests while an assistant answer streams (the reported freeze).

A real uvicorn server runs the API against PostgreSQL with the production pool shape
(SQLAlchemy's defaults: 5 connections plus 10 overflow, 30 s wait). Several browser tabs
keep ``/api/v1/events/stream`` open, one asks the assistant (chat ``ask``, voice
``ask/stream`` or voice ``ask``) and the model answers slowly, the way a remote provider
does. Every database round trip is made a little slow, like a busy small VM. While the
answer streams, ``/health`` and a cheap authenticated read must keep answering quickly:
any blocking database call on the event loop shows up as a stall here.
"""

from __future__ import annotations

import socket
import statistics
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from conftest import APP_ROLE, TENANT_A, WORKSPACE_A
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import sessionmaker

from anum_api.db import session as db_session
from anum_api.main import app
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.settings import settings
from anum_api.voice import voice_model_gateway

pytestmark = pytest.mark.database

HEADERS = {"x-tenant-id": TENANT_A, "x-workspace-id": WORKSPACE_A, "x-user-id": "user_test", "x-user-roles": "owner"}
TABS = 6
# Each statement waits this long before it runs: a database a few milliseconds away.
STATEMENT_DELAY_SECONDS = 0.03
# The slowest a probe may answer while the assistant streams.
MAX_PROBE_SECONDS = 0.5


class SlowGateway:
    """A remote model that takes a moment before and between its words."""

    provider = "slow-test"
    model = "slow-test"
    base_url = "http://slow.invalid"

    async def stream_chat(self, messages: list[Any], *, max_tokens: int | None = None, on_usage: Any = None) -> AsyncIterator[str]:
        import asyncio

        await asyncio.sleep(0.5)
        for word in ["My ", "name ", "is ", "Anum", ". "] * 4:
            await asyncio.sleep(0.1)
            yield word
        if on_usage is not None:
            on_usage(ModelUsage(input_tokens=10, output_tokens=20, provider=self.provider, model=self.model))

    async def complete_chat(self, messages: list[Any], *, max_tokens: int | None = None) -> ModelResponse:
        import asyncio

        await asyncio.sleep(2.5)
        return ModelResponse(
            text="My name is Anum.",
            usage=ModelUsage(input_tokens=10, output_tokens=5, provider=self.provider, model=self.model),
        )

    async def generate_text(self, prompt: str) -> ModelResponse:
        return await self.complete_chat([{"role": "user", "content": prompt}])


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
    # The production engine (anum_api/db/session.py) with its pool settings.
    engine = create_engine(
        test_database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        connect_args=db_session._connect_args(),
    )

    @event.listens_for(engine, "before_cursor_execute")
    def _slow_round_trip(*_: object) -> None:
        time.sleep(STATEMENT_DELAY_SECONDS)

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    @event.listens_for(factory, "after_begin")
    def _use_app_role(session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute(text(f"set local role {APP_ROLE}"))

    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(settings, "repository_backend", "postgresql")
    app.dependency_overrides[voice_model_gateway] = lambda: SlowGateway()
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
    try:
        yield base
    finally:
        uv.should_exit = True
        thread.join(timeout=15)
        app.dependency_overrides.pop(voice_model_gateway, None)
        engine.dispose()


def _hold_stream(base: str, opened: threading.Event, stop: threading.Event) -> None:
    try:
        with httpx.stream("GET", f"{base}/api/v1/events/stream", headers=HEADERS, timeout=httpx.Timeout(10, read=None)) as response:
            opened.set()
            for _ in response.iter_raw():
                if stop.is_set():
                    return
    except httpx.HTTPError:
        opened.set()


IDLE_IN_TRANSACTION = text(
    "select count(*) from pg_stat_activity where datname = current_database() "
    "and state = 'idle in transaction' and now() - state_change > interval '1 second'"
)


def _probe(base: str, stop: threading.Event, health: list[float], reads: list[float], engine: Engine, idle: list[int]) -> None:
    with httpx.Client(timeout=60) as client:
        while not stop.is_set():
            with engine.connect() as connection:
                idle.append(int(connection.execute(IDLE_IN_TRANSACTION).scalar_one()))
            started = time.monotonic()
            client.get(f"{base}/health").raise_for_status()
            health.append(time.monotonic() - started)
            started = time.monotonic()
            client.get(f"{base}/api/v1/conversations?limit=1", headers=HEADERS).raise_for_status()
            reads.append(time.monotonic() - started)
            time.sleep(0.05)


def _chat_ask(base: str) -> str:
    conversation = httpx.post(f"{base}/api/v1/conversations", headers=HEADERS, json={}, timeout=30)
    conversation.raise_for_status()
    with httpx.stream(
        "POST",
        f"{base}/api/v1/conversations/{conversation.json()['id']}/ask",
        headers=HEADERS,
        json={"content": "What's your name?"},
        timeout=60,
    ) as response:
        assert response.status_code == 200, response.read()
        return "".join(response.iter_text())


def _voice_turn(base: str) -> tuple[str, str]:
    session = httpx.post(f"{base}/api/v1/voice/sessions", headers=HEADERS, json={}, timeout=30)
    session.raise_for_status()
    session_id = session.json()["id"]
    segment = httpx.post(
        f"{base}/api/v1/voice/sessions/{session_id}/transcript",
        headers=HEADERS,
        json={"text": "Explain in one line why the sky is blue", "client_sequence": 1},
        timeout=30,
    )
    segment.raise_for_status()
    return session_id, segment.json()["id"]


def _voice_ask_stream(base: str) -> str:
    session_id, segment_id = _voice_turn(base)
    with httpx.stream(
        "POST",
        f"{base}/api/v1/voice/sessions/{session_id}/ask/stream",
        headers=HEADERS,
        json={"transcript_segment_id": segment_id},
        timeout=60,
    ) as response:
        assert response.status_code == 200, response.read()
        return "".join(response.iter_text())


def _voice_ask(base: str) -> str:
    session_id, segment_id = _voice_turn(base)
    response = httpx.post(
        f"{base}/api/v1/voice/sessions/{session_id}/ask",
        headers=HEADERS,
        json={"transcript_segment_id": segment_id},
        timeout=60,
    )
    assert response.status_code == 200, response.text
    return response.text


@pytest.mark.parametrize("ask", [_chat_ask, _voice_ask_stream, _voice_ask], ids=["chat-ask", "voice-ask-stream", "voice-ask"])
def test_api_stays_responsive_while_an_answer_streams_with_open_tabs(
    server: str, ask: Any, database_engine: Engine
) -> None:
    stop = threading.Event()
    for _ in range(TABS):
        opened = threading.Event()
        threading.Thread(target=_hold_stream, args=(server, opened, stop), daemon=True).start()
        assert opened.wait(15), "an event stream did not open"
    health: list[float] = []
    reads: list[float] = []
    idle: list[int] = []
    try:
        time.sleep(1.2)  # every tab polls at least once
        prober = threading.Thread(target=_probe, args=(server, stop, health, reads, database_engine, idle), daemon=True)
        prober.start()
        body = ask(server)
        time.sleep(0.5)
    finally:
        stop.set()
    prober.join(timeout=60)
    assert "Anum" in body or "sky" in body or "delta" in body, body[:400]
    assert health and reads, "the probe never ran"
    summary = (
        f"/health max {max(health):.2f}s median {statistics.median(health):.3f}s; "
        f"GET /conversations max {max(reads):.2f}s median {statistics.median(reads):.3f}s over {len(reads)} probes"
    )
    # No connection waits "idle in transaction" while the model answers: a session held
    # across the model call is a pooled connection lost for the whole answer.
    assert max(idle) == 0, f"{max(idle)} connection(s) idle in transaction for over 1 s during the answer"
    assert max(health) < MAX_PROBE_SECONDS, summary
    assert max(reads) < MAX_PROBE_SECONDS * 2, summary


# ----------------------------------------------------------------------------- task runs


def _new_task(base: str) -> str:
    created = httpx.post(
        f"{base}/api/v1/tasks",
        headers=HEADERS,
        json={"title": "A small task", "prompt": "What's your name?"},
        timeout=30,
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _run_in_background(base: str, task_id: str, out: dict[str, Any]) -> threading.Thread:
    def run() -> None:
        started = time.monotonic()
        response = httpx.post(f"{base}/api/v1/tasks/{task_id}/run", headers=HEADERS, timeout=90)
        out["status"], out["body"], out["seconds"] = response.status_code, response.text, time.monotonic() - started

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker


@pytest.fixture
def slow_task_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inline task runs plan with the slow model (the server default, no workspace model)."""
    from anum_api import main

    monkeypatch.setattr(main, "model_gateway", SlowGateway())
    monkeypatch.setattr(main, "run_dispatcher", None)


def test_api_stays_responsive_while_an_inline_task_runs(
    server: str, slow_task_model: None, database_engine: Engine
) -> None:
    stop = threading.Event()
    for _ in range(TABS):
        opened = threading.Event()
        threading.Thread(target=_hold_stream, args=(server, opened, stop), daemon=True).start()
        assert opened.wait(15), "an event stream did not open"
    task_id = _new_task(server)
    health: list[float] = []
    reads: list[float] = []
    idle: list[int] = []
    outcome: dict[str, Any] = {}
    try:
        time.sleep(1.2)
        prober = threading.Thread(target=_probe, args=(server, stop, health, reads, database_engine, idle), daemon=True)
        prober.start()
        _run_in_background(server, task_id, outcome).join(90)
        time.sleep(0.5)
    finally:
        stop.set()
    prober.join(timeout=60)
    assert outcome.get("status") == 200, outcome
    summary = (
        f"/health max {max(health):.2f}s; GET /conversations max {max(reads):.2f}s over {len(reads)} probes; "
        f"run took {outcome['seconds']:.1f}s"
    )
    # The model call runs with no transaction open: no row lock, no pooled connection held.
    assert max(idle) == 0, f"{max(idle)} connection(s) idle in transaction for over 1 s during the run; {summary}"
    assert max(health) < MAX_PROBE_SECONDS, summary
    assert max(reads) < MAX_PROBE_SECONDS * 2, summary


def test_cancel_during_an_inline_run_answers_and_never_stalls_the_api(server: str, slow_task_model: None) -> None:
    task_id = _new_task(server)
    outcome: dict[str, Any] = {}
    worker = _run_in_background(server, task_id, outcome)
    time.sleep(1.0)  # the run has started and the model is answering
    health: list[float] = []
    stop = threading.Event()

    def probe_health() -> None:
        with httpx.Client(timeout=60) as client:
            while not stop.is_set():
                started = time.monotonic()
                client.get(f"{server}/health").raise_for_status()
                health.append(time.monotonic() - started)
                time.sleep(0.05)

    prober = threading.Thread(target=probe_health, daemon=True)
    prober.start()
    try:
        started = time.monotonic()
        cancelled = httpx.post(f"{server}/api/v1/tasks/{task_id}/cancel", headers=HEADERS, timeout=60)
        cancel_seconds = time.monotonic() - started
        worker.join(90)
    finally:
        stop.set()
    prober.join(timeout=60)
    assert cancelled.status_code == 200, cancelled.text
    assert cancel_seconds < 2, f"cancel waited {cancel_seconds:.1f}s for the running task's row lock"
    assert health and max(health) < MAX_PROBE_SECONDS, f"/health max {max(health):.2f}s while cancelling"
    # The run notices the cancel instead of overwriting it.
    assert outcome.get("status") == 409, outcome
    task = httpx.get(f"{server}/api/v1/tasks/{task_id}", headers=HEADERS, timeout=30).json()
    assert task["status"] == "cancelled", task
