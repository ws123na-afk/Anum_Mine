"""Real (not mocked) integration tests for the optional Valkey-backed
idempotency repository and rate limiter, run against an actual
redis-server process. These are opt-in via the `redis` marker (see
pyproject.toml) since they need a real binary on PATH; the default
`-m "not database"` test run never touches this file's fixtures.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
import redis as redis_lib
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anum_api.idempotency import (
    BeginOutcome,
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyScope,
    IdempotencyState,
    InMemoryIdempotencyRepository,
    StoredResponse,
    ValkeyIdempotencyRepository,
    canonical_request_fingerprint,
)
from anum_api.rate_limit import RateLimitMiddleware

REDIS_PORT = 16399
SCOPE = IdempotencyScope("tenant_a", "workspace_a", "task.create")


class Clock:
    """Deterministic, strictly-increasing clock - mirrors test_idempotency.py."""

    def __init__(self) -> None:
        self.value = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(seconds=1)
        return result


def fingerprint(payload: object = None) -> str:
    return canonical_request_fingerprint(SCOPE.action, payload)


@pytest.fixture(scope="module")
def redis_server() -> Iterator[None]:
    if not shutil.which("redis-server"):
        pytest.skip("redis-server is not on PATH")
    process = subprocess.Popen(
        [
            "redis-server",
            "--port",
            str(REDIS_PORT),
            "--daemonize",
            "no",
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5.0
        client = redis_lib.Redis(port=REDIS_PORT)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if client.ping():
                    break
            except Exception as exc:  # noqa: BLE001 - retry until deadline
                last_error = exc
                time.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError(f"redis-server did not become ready in time: {last_error}")
        yield
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def redis_client(redis_server: None) -> Iterator[redis_lib.Redis]:
    client = redis_lib.Redis(port=REDIS_PORT, decode_responses=True)
    client.flushall()
    yield client
    client.flushall()
    client.close()


# --- ValkeyIdempotencyRepository ---------------------------------------


@pytest.mark.redis
def test_valkey_begin_creates_processing_record(redis_client: redis_lib.Redis) -> None:
    repository = ValkeyIdempotencyRepository(redis_client, clock=Clock())

    result = repository.begin(SCOPE, "request-1", fingerprint({"title": "Report"}))

    assert result.outcome is BeginOutcome.STARTED
    assert result.record.state is IdempotencyState.PROCESSING
    assert result.record.attempts == 1
    assert result.record.created_at == datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    assert result.record.updated_at == result.record.created_at


@pytest.mark.redis
def test_valkey_completed_response_is_replayed(redis_client: redis_lib.Redis) -> None:
    repository = ValkeyIdempotencyRepository(redis_client, clock=Clock())
    request_fingerprint = fingerprint({"title": "Report"})
    repository.begin(SCOPE, "request-1", request_fingerprint)
    body = {"id": "task_1", "labels": ["new"]}

    completed = repository.complete(
        SCOPE,
        "request-1",
        request_fingerprint,
        StoredResponse(201, body, {"location": "/api/v1/tasks/task_1"}),
    )
    replay = repository.begin(SCOPE, "request-1", request_fingerprint)

    assert completed.state is IdempotencyState.COMPLETED
    assert replay.outcome is BeginOutcome.REPLAYED
    assert replay.response == StoredResponse(
        201,
        {"id": "task_1", "labels": ["new"]},
        {"location": "/api/v1/tasks/task_1"},
    )


@pytest.mark.redis
def test_valkey_same_key_with_different_request_conflicts(redis_client: redis_lib.Redis) -> None:
    repository = ValkeyIdempotencyRepository(redis_client, clock=Clock())
    repository.begin(SCOPE, "request-1", fingerprint({"title": "First"}))

    with pytest.raises(IdempotencyConflict):
        repository.begin(SCOPE, "request-1", fingerprint({"title": "Second"}))


@pytest.mark.redis
def test_valkey_duplicate_processing_request_is_rejected(redis_client: redis_lib.Redis) -> None:
    repository = ValkeyIdempotencyRepository(redis_client, clock=Clock())
    request_fingerprint = fingerprint({"title": "Report"})
    repository.begin(SCOPE, "request-1", request_fingerprint)

    with pytest.raises(IdempotencyInProgress):
        repository.begin(SCOPE, "request-1", request_fingerprint)


@pytest.mark.redis
def test_valkey_failed_request_can_be_observed_then_explicitly_retried(
    redis_client: redis_lib.Redis,
) -> None:
    repository = ValkeyIdempotencyRepository(redis_client, clock=Clock())
    request_fingerprint = fingerprint({"title": "Report"})
    repository.begin(SCOPE, "request-1", request_fingerprint)

    failed = repository.fail(SCOPE, "request-1", request_fingerprint, "database unavailable")
    observed = repository.begin(SCOPE, "request-1", request_fingerprint)
    retried = repository.begin(SCOPE, "request-1", request_fingerprint, retry_failed=True)

    assert failed.state is IdempotencyState.FAILED
    assert failed.failure_reason == "database unavailable"
    assert observed.outcome is BeginOutcome.PREVIOUSLY_FAILED
    assert retried.outcome is BeginOutcome.STARTED
    assert retried.record.state is IdempotencyState.PROCESSING
    assert retried.record.failure_reason is None
    assert retried.record.attempts == 2
    assert retried.record.created_at == failed.created_at


@pytest.mark.redis
def test_valkey_begin_is_atomic_under_concurrent_new_key_race(
    redis_client: redis_lib.Redis,
) -> None:
    """The whole point of moving this to Valkey: two *separate* repository
    instances (standing in for two API replicas sharing one Valkey) racing
    to begin() a brand-new key must not both win STARTED."""

    from concurrent.futures import ThreadPoolExecutor

    request_fingerprint = fingerprint({"title": "Race"})
    repos = [ValkeyIdempotencyRepository(redis_client) for _ in range(8)]

    def attempt(repository: ValkeyIdempotencyRepository) -> BeginOutcome | Exception:
        try:
            return repository.begin(SCOPE, "race-key", request_fingerprint).outcome
        except IdempotencyInProgress as exc:
            return exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, repos))

    started = [r for r in results if r is BeginOutcome.STARTED]
    in_progress = [r for r in results if isinstance(r, IdempotencyInProgress)]

    assert len(started) == 1
    assert len(in_progress) == 7


@pytest.mark.redis
def test_valkey_stores_a_ttl_on_the_record_key(redis_client: redis_lib.Redis) -> None:
    repository = ValkeyIdempotencyRepository(redis_client, clock=Clock())
    repository.begin(SCOPE, "request-1", fingerprint({}))

    ttl = redis_client.ttl("anum:idempotency:tenant_a:workspace_a:task.create:request-1")

    assert 0 < ttl <= 7 * 24 * 60 * 60


# --- default in-memory path is unaffected --------------------------------


@pytest.mark.redis
def test_in_memory_repository_still_behaves_the_same_without_redis() -> None:
    """Sanity check that InMemoryIdempotencyRepository (the default, used
    when ANUM_VALKEY_URL is unset) is untouched by the Valkey addition.
    Full regression protection is the existing suite in test_idempotency.py.
    """

    repository = InMemoryIdempotencyRepository(clock=Clock())
    request_fingerprint = fingerprint({"title": "Report"})

    started = repository.begin(SCOPE, "request-1", request_fingerprint)
    completed = repository.complete(
        SCOPE, "request-1", request_fingerprint, StoredResponse(200, {"ok": True}, {})
    )
    replayed = repository.begin(SCOPE, "request-1", request_fingerprint)

    assert started.outcome is BeginOutcome.STARTED
    assert completed.state is IdempotencyState.COMPLETED
    assert replayed.outcome is BeginOutcome.REPLAYED


# --- _ValkeyFixedWindowCounter / RateLimitMiddleware ---------------------


def _app_with_rate_limit(
    *, limit: int, window_seconds: int, redis_client: redis_lib.Redis
) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(
        RateLimitMiddleware,
        limit=limit,
        window_seconds=window_seconds,
        redis_client=redis_client,
    )
    return app


@pytest.mark.redis
def test_valkey_backed_rate_limit_allows_within_limit_and_blocks_over(
    redis_client: redis_lib.Redis,
) -> None:
    client = TestClient(
        _app_with_rate_limit(limit=3, window_seconds=60, redis_client=redis_client)
    )

    for _ in range(3):
        assert client.get("/ping").status_code == 200

    blocked = client.get("/ping")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]


@pytest.mark.redis
def test_valkey_backed_rate_limit_state_is_shared_across_middleware_instances(
    redis_client: redis_lib.Redis,
) -> None:
    """Proves the counter isn't process/instance-local: two independently
    constructed middleware instances sharing one Valkey client must share
    the same count for the same client key."""

    app_one = _app_with_rate_limit(limit=2, window_seconds=60, redis_client=redis_client)
    app_two = _app_with_rate_limit(limit=2, window_seconds=60, redis_client=redis_client)
    client_one = TestClient(app_one)
    client_two = TestClient(app_two)

    # TestClient's default host/client IP is the same for both apps, so both
    # middleware instances key on the same client identity and therefore
    # the same Valkey window key.
    first = client_one.get("/ping")
    second = client_two.get("/ping")
    third = client_one.get("/ping")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


@pytest.mark.redis
def test_valkey_rate_limit_key_has_a_ttl(redis_client: redis_lib.Redis) -> None:
    client = TestClient(
        _app_with_rate_limit(limit=5, window_seconds=60, redis_client=redis_client)
    )
    client.get("/ping")

    keys = redis_client.keys("anum:ratelimit:*")
    assert len(keys) == 1
    assert 0 < redis_client.ttl(keys[0]) <= 60
