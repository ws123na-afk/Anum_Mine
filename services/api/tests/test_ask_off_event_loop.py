"""Assistant answers never open a database unit of work on the event loop (memory backend).

The API runs one event loop per process. A store or repository opened on it blocks
every other request while it waits for a pooled connection, a row lock or a slow query
(the reported freeze: one tab asked "what's your name" and every tab stopped
updating). The chat ``ask`` and the voice ``ask`` and ``ask/stream`` routes must do
their reads and writes in worker threads. This test wraps every store opener and
records any call made from a thread that is running an event loop.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anum_api import dependencies, scoped_store
from anum_api.conversations import conversation_store
from anum_api.conversations import router as conversations_router
from anum_api.model_gateway import ModelResponse, ModelUsage
from anum_api.voice import router as voice_router
from anum_api.voice import voice_model_gateway, voice_store

app = FastAPI()
app.include_router(conversations_router)
app.include_router(voice_router)
client = TestClient(app)
HEADERS = {"x-tenant-id": "tenant_loop", "x-workspace-id": "workspace_loop", "x-user-id": "user_loop", "x-user-roles": "owner"}


class QuickGateway:
    provider = "loop-test"
    model = "loop-test"

    async def stream_chat(self, messages: list[Any], *, max_tokens: int | None = None, on_usage: Any = None) -> AsyncIterator[str]:
        for word in ["My ", "name ", "is ", "Anum."]:
            await asyncio.sleep(0)
            yield word
        if on_usage is not None:
            on_usage(ModelUsage(input_tokens=5, output_tokens=4, provider=self.provider, model=self.model))

    async def complete_chat(self, messages: list[Any], *, max_tokens: int | None = None) -> ModelResponse:
        return ModelResponse(
            text="My name is Anum.",
            usage=ModelUsage(input_tokens=5, output_tokens=4, provider=self.provider, model=self.model),
        )

    async def generate_text(self, prompt: str) -> ModelResponse:
        return await self.complete_chat([{"role": "user", "content": prompt}])


@pytest.fixture
def on_loop(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Names of store openers called on the event loop while ``recording`` is set."""
    calls: list[str] = []
    recording = {"on": False}

    def guard(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        @contextmanager
        def wrapped(*args: Any, **kwargs: Any) -> Iterator[Any]:
            if recording["on"]:
                try:
                    asyncio.get_running_loop()
                    calls.append(name)
                except RuntimeError:
                    pass  # a worker thread: allowed
            with original(*args, **kwargs) as store:
                yield store

        return wrapped

    original_scoped = scoped_store.open_scoped_store
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("anum_api") and getattr(module, "open_scoped_store", None) is original_scoped:
            monkeypatch.setattr(module, "open_scoped_store", guard("open_scoped_store", original_scoped))
    monkeypatch.setattr(dependencies, "_unit_of_work", guard("_unit_of_work", dependencies._unit_of_work))
    app.dependency_overrides[voice_model_gateway] = lambda: QuickGateway()
    conversation_store.clear()
    voice_store.clear()
    calls.append  # noqa: B018 - keeps the list referenced for the closure
    try:
        yield _Recorder(calls, recording)  # type: ignore[misc]
    finally:
        app.dependency_overrides.clear()


class _Recorder(list):  # type: ignore[type-arg]
    def __init__(self, calls: list[str], recording: dict[str, bool]) -> None:
        super().__init__()
        self.calls = calls
        self.recording = recording

    @contextmanager
    def watching(self) -> Iterator[list[str]]:
        self.calls.clear()
        self.recording["on"] = True
        try:
            yield self.calls
        finally:
            self.recording["on"] = False


def _voice_segment() -> tuple[str, str]:
    session = client.post("/api/v1/voice/sessions", headers=HEADERS, json={})
    assert session.status_code == 201, session.text
    segment = client.post(
        f"/api/v1/voice/sessions/{session.json()['id']}/transcript",
        headers=HEADERS,
        json={"text": "Explain in one line why the sky is blue", "client_sequence": 1},
    )
    assert segment.status_code == 201, segment.text
    return session.json()["id"], segment.json()["id"]


def test_chat_ask_does_its_database_work_off_the_event_loop(on_loop: Any) -> None:
    conversation = client.post("/api/v1/conversations", headers=HEADERS, json={})
    assert conversation.status_code == 201, conversation.text
    with on_loop.watching() as calls:
        response = client.post(
            f"/api/v1/conversations/{conversation.json()['id']}/ask",
            headers=HEADERS,
            json={"content": "What's your name?"},
        )
    assert response.status_code == 200, response.text
    assert "event: done" in response.text and "Anum" in response.text, response.text
    assert calls == [], f"opened on the event loop during the ask: {calls}"


def test_voice_ask_stream_does_its_database_work_off_the_event_loop(on_loop: Any) -> None:
    session_id, segment_id = _voice_segment()
    with on_loop.watching() as calls:
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/ask/stream",
            headers=HEADERS,
            json={"transcript_segment_id": segment_id},
        )
    assert response.status_code == 200, response.text
    assert "event: done" in response.text, response.text
    assert calls == [], f"opened on the event loop during the ask: {calls}"


def test_voice_ask_does_its_database_work_off_the_event_loop(on_loop: Any) -> None:
    session_id, segment_id = _voice_segment()
    with on_loop.watching() as calls:
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/ask",
            headers=HEADERS,
            json={"transcript_segment_id": segment_id},
        )
    assert response.status_code == 200, response.text
    assert response.json()["reply"], response.text
    assert calls == [], f"opened on the event loop during the ask: {calls}"
