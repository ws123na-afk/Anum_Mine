"""The in-app call stream never opens a database unit of work on the event loop.

Like test_ask_off_event_loop.py: the API runs one event loop per process, and a store or
repository opened on it blocks every request while it waits for a connection, a row lock
or a slow query. The call stream route checks the membership in a dependency (a worker
thread, scope="function") and its body reads, settles and re-reads in worker threads,
whether it was woken by a call event or by its fallback poll. This test wraps every store
opener and records any call made from a thread that is running an event loop.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterator
from contextlib import aclosing, contextmanager
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from anum_api import dependencies, main, scoped_store
from anum_api.app_call_wake import call_wake_hub
from anum_api.app_calls import call_event_source, memory_call_store
from anum_api.dependencies import memory_repository
from anum_api.onboarding import _notifications
from anum_api.schemas import TenantContext, WorkspaceMembership, utc_now
from anum_api.settings import settings
from anum_api.store import store

TENANT, WORKSPACE = "tenant_calls_loop", "workspace_calls_loop"
DEVICE_A = "device-alice-loop-0001"
DEVICE_B = "device-bob-loop-000001"
OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"
client = TestClient(main.app)


def headers(user: str) -> dict[str, str]:
    return {"x-tenant-id": TENANT, "x-workspace-id": WORKSPACE, "x-user-id": user, "x-user-roles": "member"}


def ctx(user: str) -> TenantContext:
    return TenantContext(tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, roles=["member"])


@pytest.fixture
def on_loop(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    calls: list[str] = []
    state: dict[str, Any] = {"on": False, "calls": calls}

    def guard(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        @contextmanager
        def wrapped(*args: Any, **kwargs: Any) -> Iterator[Any]:
            if state["on"]:
                try:
                    asyncio.get_running_loop()
                    calls.append(name)
                except RuntimeError:
                    pass  # a worker thread: allowed
            with original(*args, **kwargs) as opened:
                yield opened

        return wrapped

    original_scoped = scoped_store.open_scoped_store
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("anum_api") and getattr(module, "open_scoped_store", None) is original_scoped:
            monkeypatch.setattr(module, "open_scoped_store", guard("open_scoped_store", original_scoped))
    monkeypatch.setattr(dependencies, "_unit_of_work", guard("_unit_of_work", dependencies._unit_of_work))

    monkeypatch.setattr(settings, "calls_turn_urls", "turn:turn.example.test:3478?transport=udp")
    monkeypatch.setattr(settings, "calls_turn_secret", SecretStr("turn-secret-example-turn-secret-example"))
    monkeypatch.setattr(settings, "calls_stun_urls", "")
    monkeypatch.setattr(settings, "calls_allow_stun_only", False)
    memory_call_store.clear()
    _notifications.clear()
    now = utc_now()
    for user in ("alice", "bob"):
        memory_repository.save_membership(
            WorkspaceMembership(
                tenant_id=TENANT, workspace_id=WORKSPACE, user_id=user, role="member", active=True, created_at=now, updated_at=now
            )
        )
    try:
        yield state
    finally:
        memory_call_store.clear()
        _notifications.clear()
        store.memberships.clear()
        store.events.clear()
        store.audit_records.clear()


def _place_and_accept() -> str:
    placed = client.post("/api/v1/app-calls", headers=headers("alice"), json={"callee_user_id": "bob", "client_id": DEVICE_A})
    assert placed.status_code == 201, placed.text
    call_id = placed.json()["id"]
    accepted = client.post(f"/api/v1/app-calls/{call_id}/accept", headers=headers("bob"), json={"client_id": DEVICE_B})
    assert accepted.status_code == 200, accepted.text
    return call_id


def test_the_stream_route_checks_membership_and_reads_off_the_event_loop(on_loop: dict[str, Any]) -> None:
    _place_and_accept()
    on_loop["on"] = True
    response = client.get("/api/v1/app-calls/stream", params={"client_id": DEVICE_B, "follow": "false"}, headers=headers("bob"))
    on_loop["on"] = False
    assert response.status_code == 200, response.text
    assert "event: call" in response.text
    assert on_loop["calls"] == [], f"opened on the event loop: {on_loop['calls']}"


def test_woken_reads_polls_and_settles_all_run_off_the_event_loop(
    on_loop: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "calls_poll_seconds", 0.1)
    monkeypatch.setattr(settings, "calls_active_poll_seconds", 0.05)
    call_id = _place_and_accept()

    async def run() -> list[str]:
        frames: list[str] = []
        stop = asyncio.Event()

        async def disconnected() -> bool:
            return stop.is_set()

        async def consume() -> None:
            async with aclosing(call_event_source(ctx("bob"), DEVICE_B, disconnected)) as source:
                async for frame in source:
                    frames.append(frame)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)  # several fallback polls
        for _ in range(3):  # woken reads
            response = await asyncio.to_thread(
                client.post, f"/api/v1/app-calls/{call_id}/signals", headers=headers("alice"), json={"kind": "offer", "sdp": OFFER}
            )
            assert response.status_code == 202, response.text
        await asyncio.sleep(0.2)
        # The connected call goes silent: the stream settles it as dropped in a thread.
        stale = memory_call_store.get(ctx("bob"), call_id)
        assert stale is not None
        stale.heartbeat_at = utc_now() - timedelta(seconds=90)
        memory_call_store.save(ctx("bob"), stale)
        await asyncio.sleep(0.4)
        stop.set()
        await asyncio.wait_for(task, 3)
        return frames

    on_loop["on"] = True
    try:
        frames = asyncio.run(run())
    finally:
        on_loop["on"] = False
    assert sum("event: signal" in frame for frame in frames) == 3
    assert any('"outcome":"dropped"' in frame for frame in frames), frames
    assert on_loop["calls"] == [], f"opened on the event loop: {on_loop['calls']}"
    assert call_wake_hub.listener_count == 0
