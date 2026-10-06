"""Unit tests for anum_api.valkey with an in-process fake (no server needed).

The fake implements the handful of commands the module uses and runs the module's
Lua scripts as equivalent Python, keyed by the script text. The real scripts are
exercised against a server in ``test_valkey_integration.py``.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from anum_api import main
from anum_api.hardening import InMemoryTokenBucket, RateLimitMiddleware
from anum_api.schemas import TenantContext
from anum_api.settings import Settings
from anum_api.valkey import (
    EXTEND_SCRIPT,
    RELEASE_SCRIPT,
    RATE_LIMIT_SCRIPT,
    CoordinationUnavailable,
    DistributedLock,
    LockNotAcquired,
    RunLockManager,
    ValkeyTokenBucket,
    build_rate_limit_backend,
    build_run_lock_manager,
    run_lock_key,
)
from anum_api.store import store


class FakeValkey:
    """Synchronous core shared by the async and sync fakes. ``now`` is in seconds."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, float] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise RedisConnectionError("valkey is down")

    def _expire(self) -> None:
        for key, at in list(self.expires.items()):
            if at <= self.now:
                self.values.pop(key, None)
                self.hashes.pop(key, None)
                self.expires.pop(key, None)

    def set(self, key: str, value: str, *, nx: bool = False, px: int | None = None) -> bool | None:
        self._check()
        self._expire()
        if nx and key in self.values:
            return None
        self.values[key] = value
        if px is not None:
            self.expires[key] = self.now + px / 1000
        return True

    def get(self, key: str) -> str | None:
        self._check()
        self._expire()
        return self.values.get(key)

    def eval(self, script: str, numkeys: int, *args: object) -> object:
        self._check()
        self._expire()
        keys, argv = [str(a) for a in args[:numkeys]], [str(a) for a in args[numkeys:]]
        if script == RELEASE_SCRIPT:
            if self.values.get(keys[0]) == argv[0]:
                self.values.pop(keys[0])
                self.expires.pop(keys[0], None)
                return 1
            return 0
        if script == EXTEND_SCRIPT:
            if self.values.get(keys[0]) == argv[0]:
                self.expires[keys[0]] = self.now + int(argv[1]) / 1000
                return 1
            return 0
        if script == RATE_LIMIT_SCRIPT:
            rate, burst = float(argv[0]), float(argv[1])
            now = float(argv[2]) if argv[2] else self.now * 1000
            state = self.hashes.get(keys[0], {})
            tokens = float(state.get("tokens", burst))
            updated = float(state.get("ts", now))
            tokens = min(burst, tokens + max(0.0, now - updated) * rate / 1000)
            allowed, wait = 0, 0
            if tokens >= 1:
                tokens -= 1
                allowed = 1
            else:
                wait = math.ceil((1 - tokens) * 1000 / rate)
            self.hashes[keys[0]] = {"tokens": repr(tokens), "ts": repr(now)}
            return [allowed, math.floor(tokens), wait]
        raise AssertionError("unexpected script")

    def scan_iter(self, match: str, count: int = 10) -> Iterator[str]:
        prefix = match.rstrip("*")
        return iter([key for key in [*self.values, *self.hashes] if key.startswith(prefix)])

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self.values.pop(key, None) is not None or self.hashes.pop(key, None) is not None)
        return removed


class FakeAsyncValkey:
    def __init__(self, core: FakeValkey) -> None:
        self.core = core

    async def set(self, key: str, value: str, *, nx: bool = False, px: int | None = None) -> bool | None:
        return self.core.set(key, value, nx=nx, px=px)

    async def eval(self, script: str, numkeys: int, *args: object) -> object:
        return self.core.eval(script, numkeys, *args)


CONTEXT = TenantContext(tenant_id="tenant_a", workspace_id="workspace_a", user_id="user_a", roles=["owner"])


def test_run_lock_key_is_namespaced_by_tenant_and_workspace() -> None:
    assert run_lock_key(CONTEXT, "task_1") == "anum:lock:tenants:tenant_a:workspaces:workspace_a:tasks:task_1"
    with pytest.raises(ValueError):
        run_lock_key(CONTEXT, "task:1")
    with pytest.raises(ValueError):
        run_lock_key(CONTEXT.model_copy(update={"tenant_id": "a*"}), "task_1")


def test_lock_is_exclusive_until_released() -> None:
    core = FakeValkey()
    client = FakeAsyncValkey(core)

    async def scenario() -> None:
        first = DistributedLock(client, "anum:lock:x", ttl_seconds=30)
        second = DistributedLock(client, "anum:lock:x", ttl_seconds=30)
        assert await first.acquire()
        assert not await second.acquire()
        assert await first.release()
        assert await second.acquire()
        assert core.values["anum:lock:x"] == second.token

    asyncio.run(scenario())


def test_release_after_ttl_never_deletes_the_next_holders_lock() -> None:
    core = FakeValkey()
    client = FakeAsyncValkey(core)

    async def scenario() -> None:
        stale = DistributedLock(client, "anum:lock:x", ttl_seconds=1)
        assert await stale.acquire()
        core.now += 2  # TTL lapses: the holder crashed or stalled
        fresh = DistributedLock(client, "anum:lock:x", ttl_seconds=30)
        assert await fresh.acquire()
        assert not await stale.extend()
        assert not await stale.release()
        assert core.values["anum:lock:x"] == fresh.token

    asyncio.run(scenario())


def test_extend_pushes_the_expiry_forward() -> None:
    core = FakeValkey()
    client = FakeAsyncValkey(core)

    async def scenario() -> None:
        lock = DistributedLock(client, "anum:lock:x", ttl_seconds=1)
        assert await lock.acquire()
        assert await lock.extend(10)
        core.now += 5
        assert not await DistributedLock(client, "anum:lock:x", ttl_seconds=1).acquire()

    asyncio.run(scenario())


def test_acquire_waits_for_release_within_wait_time() -> None:
    core = FakeValkey()
    client = FakeAsyncValkey(core)

    async def scenario() -> None:
        holder = DistributedLock(client, "anum:lock:x", ttl_seconds=30)
        await holder.acquire()
        waiter = DistributedLock(client, "anum:lock:x", ttl_seconds=30)

        async def release_soon() -> None:
            await asyncio.sleep(0.05)
            await holder.release()

        release = asyncio.create_task(release_soon())
        assert await waiter.acquire(wait_seconds=2, retry_interval=0.01)
        await release

    asyncio.run(scenario())


def test_lock_context_manager_raises_when_held() -> None:
    core = FakeValkey()
    client = FakeAsyncValkey(core)

    async def scenario() -> None:
        async with DistributedLock(client, "anum:lock:x", ttl_seconds=30):
            with pytest.raises(LockNotAcquired):
                async with DistributedLock(client, "anum:lock:x", ttl_seconds=30):
                    pass
        assert "anum:lock:x" not in core.values

    asyncio.run(scenario())


def test_run_lock_manager_without_backend_is_a_no_op() -> None:
    manager = build_run_lock_manager(Settings(run_lock_backend="none"))
    assert not manager.enabled

    async def scenario() -> None:
        async with manager.hold(CONTEXT, "task_1") as lock:
            assert lock is None

    asyncio.run(scenario())


def test_run_lock_manager_maps_outages_to_coordination_unavailable() -> None:
    core = FakeValkey()
    core.fail = True
    manager = RunLockManager(FakeAsyncValkey(core))

    async def scenario() -> None:
        with pytest.raises(CoordinationUnavailable):
            await manager.acquire(CONTEXT, "task_1")

    asyncio.run(scenario())


def test_valkey_backend_selected_by_settings() -> None:
    manager = build_run_lock_manager(Settings(run_lock_backend="valkey", valkey_url="redis://127.0.0.1:1/0"))
    assert manager.enabled
    limiter = build_rate_limit_backend(Settings(rate_limit_backend="valkey", valkey_url="redis://127.0.0.1:1/0"))
    assert isinstance(limiter, ValkeyTokenBucket)
    assert isinstance(build_rate_limit_backend(Settings()), InMemoryTokenBucket)
    with pytest.raises(ValueError):
        Settings(rate_limit_backend="sqlite")


def test_valkey_token_bucket_allows_burst_then_limits() -> None:
    core = FakeValkey()
    bucket = ValkeyTokenBucket(core, rate_per_second=1.0, burst=2)
    assert bucket.acquire("ip:1", now=10).allowed
    assert bucket.acquire("ip:1", now=10).allowed
    denied = bucket.acquire("ip:1", now=10)
    assert not denied.allowed and denied.retry_after_seconds == 1
    assert bucket.acquire("ip:2", now=10).allowed  # separate client key
    assert bucket.acquire("ip:1", now=11).allowed  # refilled
    assert set(core.hashes) == {"anum:ratelimit:ip:1", "anum:ratelimit:ip:2"}
    bucket.reset()
    assert core.hashes == {}


def test_valkey_token_bucket_falls_back_to_process_limit_when_down() -> None:
    core = FakeValkey()
    core.fail = True
    bucket = ValkeyTokenBucket(core, rate_per_second=1.0, burst=1)
    assert bucket.acquire("ip:1", now=10).allowed
    assert not bucket.acquire("ip:1", now=10).allowed


def test_rate_limit_middleware_runs_blocking_backend_in_a_thread() -> None:
    core = FakeValkey()
    bucket = ValkeyTokenBucket(core, rate_per_second=0.001, burst=2)
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_middleware(RateLimitMiddleware, backend=bucket)
    client = TestClient(app)
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    limited = client.get("/ping")
    assert limited.status_code == 429
    assert limited.headers["retry-after"]


HEADERS = {
    "x-tenant-id": "tenant_lock",
    "x-workspace-id": "workspace_lock",
    "x-user-id": "user_lock",
    "x-user-roles": "owner",
}


@pytest.fixture
def locked_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, FakeValkey]]:
    core = FakeValkey()
    monkeypatch.setattr(main, "run_locks", RunLockManager(FakeAsyncValkey(core), ttl_seconds=30))
    for collection in (store.tasks, store.runs, store.approvals, store.events):
        collection.clear()
    yield TestClient(main.app), core


def test_run_endpoint_rejects_a_task_another_replica_is_running(locked_api) -> None:
    client, core = locked_api
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": "Lock", "prompt": "Summarize notes"}).json()
    key = run_lock_key(
        TenantContext(tenant_id="tenant_lock", workspace_id="workspace_lock", user_id="u", roles=[]), task["id"]
    )
    core.set(key, "other-replica", nx=True, px=30_000)

    busy = client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS)
    assert busy.status_code == 409
    assert store.tasks[task["id"]].status == "created"

    core.delete(key)
    done = client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS)
    assert done.status_code == 200
    assert done.json()["run"]["status"] == "completed"
    assert key not in core.values  # released after the request


def test_approval_decision_takes_the_task_lock(locked_api) -> None:
    client, core = locked_api
    task = client.post(
        "/api/v1/tasks", headers=HEADERS, json={"title": "Publish", "prompt": "Publish the final update"}
    ).json()
    approval = client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS).json()["approval"]
    key = run_lock_key(
        TenantContext(tenant_id="tenant_lock", workspace_id="workspace_lock", user_id="u", roles=[]), task["id"]
    )
    core.set(key, "other-replica", nx=True, px=30_000)
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=HEADERS).status_code == 409
    assert store.approvals[approval["id"]].status == "pending"
    core.delete(key)
    assert client.post(f"/api/v1/approvals/{approval['id']}/approve", headers=HEADERS).status_code == 200


def test_run_endpoint_reports_unavailable_coordination(locked_api) -> None:
    client, core = locked_api
    task = client.post("/api/v1/tasks", headers=HEADERS, json={"title": "Lock", "prompt": "Summarize notes"}).json()
    core.fail = True
    response = client.post(f"/api/v1/tasks/{task['id']}/run", headers=HEADERS)
    assert response.status_code == 503
