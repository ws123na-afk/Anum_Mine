"""Valkey coordination: distributed locks and a shared rate-limit backend.

Valkey speaks the Redis protocol, so the pinned ``redis`` client talks to it. Two
things live here (see ``docs/agent-runtime.md`` and ``docs/security.md``):

* :class:`DistributedLock` - ``SET NX PX`` with a random token; release and extend
  run a compare-and-act script, so a holder whose TTL lapsed can never delete or
  extend a lock someone else now holds.
* :class:`ValkeyTokenBucket` - the :class:`~anum_api.hardening.RateLimitBackend`
  token bucket as one atomic script using the server clock, so limits hold across
  API replicas.

Every key carries an ``anum:`` prefix; run locks are scoped by tenant and workspace.
"""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from .hardening import InMemoryTokenBucket, RateLimitDecision
from .schemas import TenantContext

logger = logging.getLogger(__name__)

KEY_PREFIX = "anum"

# Delete the lock only while it still holds our token.
RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# Extend the lock's TTL only while it still holds our token.
EXTEND_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

# Token bucket. ARGV: rate per second, burst, now in ms (empty = server TIME).
# Returns {allowed (0/1), whole tokens remaining, retry-after in ms}.
RATE_LIMIT_SCRIPT = """
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
if not now then
  local t = redis.call('TIME')
  now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or burst
local updated = tonumber(state[2]) or now
tokens = math.min(burst, tokens + math.max(0, now - updated) * rate / 1000)
local allowed = 0
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  wait = math.ceil((1 - tokens) * 1000 / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(burst * 1000 / rate) + 1000)
return {allowed, math.floor(tokens), wait}
"""


class LockNotAcquired(RuntimeError):
    """Another holder owns the lock and it was not released within the wait time."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"Lock is held by another worker: {key}")


class CoordinationUnavailable(RuntimeError):
    """Valkey could not be reached, so the lock state is unknown."""


def _is_unavailable(exc: Exception) -> bool:
    try:
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import TimeoutError as RedisTimeoutError
    except ImportError:  # pragma: no cover - redis is a pinned dependency
        return isinstance(exc, (OSError, TimeoutError))
    return isinstance(exc, (RedisConnectionError, RedisTimeoutError, OSError, TimeoutError))


def _scope_part(value: str) -> str:
    # Ids never contain ':' or whitespace in ANUM, but keep keys unambiguous anyway.
    if not value or any(char in value for char in ": \t\r\n*?[]"):
        raise ValueError(f"Invalid key component: {value!r}")
    return value


def run_lock_key(context: TenantContext, task_id: str) -> str:
    """Lock key for one task's run, namespaced by tenant and workspace."""
    return ":".join(
        (
            KEY_PREFIX,
            "lock",
            "tenants",
            _scope_part(context.tenant_id),
            "workspaces",
            _scope_part(context.workspace_id),
            "tasks",
            _scope_part(task_id),
        )
    )


def create_async_client(url: str, *, timeout_seconds: float = 0.5) -> Any:
    from redis import asyncio as redis_asyncio

    return redis_asyncio.Redis.from_url(
        url,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
        decode_responses=True,
    )


def create_sync_client(url: str, *, timeout_seconds: float = 0.5) -> Any:
    import redis

    return redis.Redis.from_url(
        url,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
        decode_responses=True,
    )


class DistributedLock:
    """A single-instance Valkey lock with a TTL and token-checked release.

    ``client`` is a ``redis.asyncio.Redis`` (or a compatible fake) created with
    ``decode_responses=True``. The TTL bounds how long a crashed holder blocks
    others; long work should call :meth:`extend` before the TTL runs out.
    """

    def __init__(
        self,
        client: Any,
        key: str,
        *,
        ttl_seconds: float,
        token_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.client = client
        self.key = key
        self.ttl_ms = max(1, int(ttl_seconds * 1000))
        self._token_factory = token_factory
        self.token: str | None = None

    @property
    def held(self) -> bool:
        return self.token is not None

    async def acquire(self, *, wait_seconds: float = 0, retry_interval: float = 0.05) -> bool:
        if self.token is not None:
            raise RuntimeError("Lock already held by this instance")
        token = self._token_factory()
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            if await self.client.set(self.key, token, nx=True, px=self.ttl_ms):
                self.token = token
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(retry_interval)

    async def release(self) -> bool:
        """Release if we still hold it. Returns False when the TTL had already lapsed."""
        token, self.token = self.token, None
        if token is None:
            return False
        return bool(await self.client.eval(RELEASE_SCRIPT, 1, self.key, token))

    async def extend(self, ttl_seconds: float | None = None) -> bool:
        if self.token is None:
            return False
        ttl_ms = self.ttl_ms if ttl_seconds is None else max(1, int(ttl_seconds * 1000))
        return bool(await self.client.eval(EXTEND_SCRIPT, 1, self.key, self.token, ttl_ms))

    async def __aenter__(self) -> DistributedLock:
        if not await self.acquire():
            raise LockNotAcquired(self.key)
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.release()


class RunLockManager:
    """Serialises run, resume and approval handling per task across processes.

    With no client (``ANUM_RUN_LOCK_BACKEND=none``) :meth:`hold` is a no-op and the
    database row lock is the only guard, as before.
    """

    def __init__(
        self,
        client: Any | None,
        *,
        ttl_seconds: float = 300,
        wait_seconds: float = 0,
    ) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.wait_seconds = wait_seconds

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def acquire(self, context: TenantContext, task_id: str) -> DistributedLock | None:
        """Take the task's lock. Raises :class:`LockNotAcquired` or :class:`CoordinationUnavailable`."""
        if self.client is None:
            return None
        lock = DistributedLock(self.client, run_lock_key(context, task_id), ttl_seconds=self.ttl_seconds)
        try:
            acquired = await lock.acquire(wait_seconds=self.wait_seconds)
        except Exception as exc:
            if _is_unavailable(exc):
                raise CoordinationUnavailable(str(exc)) from exc
            raise
        if not acquired:
            raise LockNotAcquired(lock.key)
        return lock

    async def release(self, lock: DistributedLock | None) -> None:
        if lock is None:
            return
        try:
            if not await lock.release():
                logger.warning("Run lock %s expired before release", lock.key)
        except Exception:  # the TTL frees it; never mask the caller's own outcome
            logger.exception("Could not release run lock %s", lock.key)

    @asynccontextmanager
    async def hold(self, context: TenantContext, task_id: str) -> AsyncIterator[DistributedLock | None]:
        lock = await self.acquire(context, task_id)
        try:
            yield lock
        finally:
            await self.release(lock)


def build_run_lock_manager(config: Any) -> RunLockManager:
    if config.run_lock_backend == "valkey":
        return RunLockManager(
            create_async_client(config.valkey_url, timeout_seconds=config.valkey_timeout_seconds),
            ttl_seconds=config.run_lock_ttl_seconds,
            wait_seconds=config.run_lock_wait_seconds,
        )
    if config.run_lock_backend != "none":
        raise RuntimeError(f"Unsupported run lock backend: {config.run_lock_backend}")
    return RunLockManager(None)


class ValkeyTokenBucket:
    """Rate-limit backend shared by every API replica.

    ``acquire`` is a blocking network call, so ``RateLimitMiddleware`` runs it in a
    worker thread (``blocking = True``). If Valkey is unreachable the request is
    limited by a per-process fallback bucket instead of failing or going unlimited.
    """

    blocking = True

    def __init__(
        self,
        client: Any,
        *,
        rate_per_second: float,
        burst: int,
        key_prefix: str = f"{KEY_PREFIX}:ratelimit",
        fallback: InMemoryTokenBucket | None = None,
    ) -> None:
        if rate_per_second <= 0 or burst <= 0:
            raise ValueError("rate_per_second and burst must be positive")
        self.client = client
        self.rate_per_second = rate_per_second
        self.burst = burst
        self.key_prefix = key_prefix
        self.fallback = fallback or InMemoryTokenBucket(rate_per_second=rate_per_second, burst=burst)

    def _key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"

    def acquire(self, key: str, *, now: float | None = None) -> RateLimitDecision:
        now_ms = "" if now is None else str(int(now * 1000))
        try:
            allowed, remaining, wait_ms = self.client.eval(
                RATE_LIMIT_SCRIPT, 1, self._key(key), repr(self.rate_per_second), self.burst, now_ms
            )
        except Exception:
            logger.warning("Valkey rate limiter unavailable; using the per-process limit", exc_info=True)
            return self.fallback.acquire(key, now=now)
        if int(allowed):
            return RateLimitDecision(allowed=True, remaining=int(remaining))
        return RateLimitDecision(
            allowed=False, retry_after_seconds=max(1, math.ceil(int(wait_ms) / 1000))
        )

    def reset(self) -> None:
        """Forget every bucket under this prefix (tests and operations only)."""
        self.fallback.reset()
        try:
            keys = list(self.client.scan_iter(match=f"{self.key_prefix}:*", count=500))
            if keys:
                self.client.delete(*keys)
        except Exception:
            logger.warning("Could not reset Valkey rate-limit buckets", exc_info=True)


def build_rate_limit_backend(config: Any) -> Any:
    rate = config.rate_limit_requests_per_minute / 60.0
    if config.rate_limit_backend == "valkey":
        return ValkeyTokenBucket(
            create_sync_client(config.valkey_url, timeout_seconds=config.valkey_timeout_seconds),
            rate_per_second=rate,
            burst=config.rate_limit_burst,
        )
    if config.rate_limit_backend != "memory":
        raise RuntimeError(f"Unsupported rate limit backend: {config.rate_limit_backend}")
    return InMemoryTokenBucket(rate_per_second=rate, burst=config.rate_limit_burst)


__all__ = [
    "CoordinationUnavailable",
    "DistributedLock",
    "LockNotAcquired",
    "RunLockManager",
    "ValkeyTokenBucket",
    "build_rate_limit_backend",
    "build_run_lock_manager",
    "create_async_client",
    "create_sync_client",
    "run_lock_key",
]
