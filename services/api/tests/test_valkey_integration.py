"""Integration tests against a real Valkey (or Redis) server.

Start one (``docker compose -f infra/docker/compose.yaml up valkey`` or
``valkey-server --port 6379``) and point ``ANUM_TEST_VALKEY_URL`` at it (default
``redis://127.0.0.1:6379/15``). Tests marked ``valkey`` are skipped when the server
is not reachable. Every test uses its own key prefix and cleans up after itself.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from collections.abc import Iterator
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from anum_api.schemas import TenantContext
from anum_api.valkey import (
    DistributedLock,
    LockNotAcquired,
    RunLockManager,
    ValkeyTokenBucket,
    create_async_client,
    create_sync_client,
)

VALKEY_URL = os.getenv("ANUM_TEST_VALKEY_URL", "redis://127.0.0.1:6379/15")


def _reachable(url: str) -> bool:
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 6379), 0.5):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.valkey,
    pytest.mark.skipif(not _reachable(VALKEY_URL), reason=f"Valkey is not reachable at {VALKEY_URL}"),
]


@pytest.fixture
def prefix() -> Iterator[str]:
    value = f"anum-test:{uuid4().hex}"
    yield value
    client = create_sync_client(VALKEY_URL, timeout_seconds=2)
    keys = list(client.scan_iter(match=f"{value}*"))
    if keys:
        client.delete(*keys)
    client.close()


def test_distributed_lock_contention_across_threads_and_connections(prefix: str) -> None:
    """Workers on separate event loops and connections never overlap in the critical section.

    Each worker does a deliberately non-atomic read-sleep-write on a shared counter while
    holding the lock. Without mutual exclusion increments are lost; with it the total is
    exact and at most one worker is ever inside.
    """
    workers, rounds = 6, 8
    lock_key, counter_key = f"{prefix}:lock", f"{prefix}:counter"
    inside = 0
    max_inside = 0
    guard = threading.Lock()
    errors: list[BaseException] = []

    async def work() -> None:
        nonlocal inside, max_inside
        client = create_async_client(VALKEY_URL, timeout_seconds=2)
        try:
            for _ in range(rounds):
                lock = DistributedLock(client, lock_key, ttl_seconds=10)
                assert await lock.acquire(wait_seconds=20, retry_interval=0.005)
                try:
                    with guard:
                        inside += 1
                        max_inside = max(max_inside, inside)
                    value = int(await client.get(counter_key) or 0)
                    await asyncio.sleep(0.002)
                    await client.set(counter_key, value + 1)
                    with guard:
                        inside -= 1
                finally:
                    assert await lock.release()
        finally:
            await client.aclose()

    def thread_main() -> None:
        try:
            asyncio.run(work())
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=thread_main) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, errors
    assert max_inside == 1
    client = create_sync_client(VALKEY_URL, timeout_seconds=2)
    try:
        assert int(client.get(counter_key)) == workers * rounds
        assert client.exists(lock_key) == 0
    finally:
        client.close()


def test_only_one_of_many_concurrent_acquirers_wins(prefix: str) -> None:
    async def scenario() -> list[bool]:
        clients = [create_async_client(VALKEY_URL, timeout_seconds=2) for _ in range(10)]
        try:
            locks = [DistributedLock(client, f"{prefix}:lock", ttl_seconds=10) for client in clients]
            results = await asyncio.gather(*(lock.acquire() for lock in locks))
            for lock in locks:
                if lock.held:
                    await lock.release()
            return list(results)
        finally:
            for client in clients:
                await client.aclose()

    results = asyncio.run(scenario())
    assert results.count(True) == 1


def test_expired_lock_is_taken_over_and_stale_release_is_refused(prefix: str) -> None:
    async def scenario() -> None:
        client = create_async_client(VALKEY_URL, timeout_seconds=2)
        try:
            stale = DistributedLock(client, f"{prefix}:lock", ttl_seconds=0.15)
            assert await stale.acquire()
            await asyncio.sleep(0.3)
            fresh = DistributedLock(client, f"{prefix}:lock", ttl_seconds=10)
            assert await fresh.acquire()
            assert not await stale.extend()
            assert not await stale.release()
            assert await client.get(f"{prefix}:lock") == fresh.token
            assert await fresh.extend(20)
            assert 10_000 < await client.pttl(f"{prefix}:lock") <= 20_000
            assert await fresh.release()
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_run_lock_manager_serialises_one_task_but_not_others(prefix: str) -> None:
    context = TenantContext(tenant_id=f"t{uuid4().hex[:8]}", workspace_id="w_lock", user_id="u", roles=[])

    async def scenario() -> None:
        client = create_async_client(VALKEY_URL, timeout_seconds=2)
        manager = RunLockManager(client, ttl_seconds=10)
        try:
            async with manager.hold(context, "task_1") as lock:
                assert lock is not None and lock.key.startswith(
                    f"anum:lock:tenants:{context.tenant_id}:workspaces:w_lock:tasks:"
                )
                with pytest.raises(LockNotAcquired):
                    await manager.acquire(context, "task_1")
                other = await manager.acquire(context, "task_2")
                await manager.release(other)
            again = await manager.acquire(context, "task_1")
            await manager.release(again)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_token_bucket_script_is_shared_across_replicas(prefix: str) -> None:
    first = ValkeyTokenBucket(
        create_sync_client(VALKEY_URL, timeout_seconds=2), rate_per_second=1.0, burst=3, key_prefix=prefix
    )
    second = ValkeyTokenBucket(
        create_sync_client(VALKEY_URL, timeout_seconds=2), rate_per_second=1.0, burst=3, key_prefix=prefix
    )
    now = 1_700_000_000.0
    assert first.acquire("ip:1", now=now).allowed
    assert second.acquire("ip:1", now=now).allowed
    assert first.acquire("ip:1", now=now).remaining == 0
    denied = second.acquire("ip:1", now=now)
    assert not denied.allowed and denied.retry_after_seconds == 1
    assert first.acquire("ip:1", now=now + 1).allowed
    assert second.acquire("ip:other", now=now).allowed
    ttl = first.client.pttl(f"{prefix}:ip:1")
    assert 0 < ttl <= 4_000
    first.reset()
    assert first.acquire("ip:1", now=now).remaining == 2


def test_token_bucket_uses_server_time_when_no_clock_is_given(prefix: str) -> None:
    bucket = ValkeyTokenBucket(
        create_sync_client(VALKEY_URL, timeout_seconds=2), rate_per_second=50.0, burst=1, key_prefix=prefix
    )
    assert bucket.acquire("ip:1").allowed
    assert not bucket.acquire("ip:1").allowed
    time.sleep(0.05)
    assert bucket.acquire("ip:1").allowed
