from datetime import datetime, timedelta, timezone

import pytest

from anum_api.idempotency import (
    BeginOutcome,
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyScope,
    IdempotencyState,
    IdempotencyTransitionError,
    InMemoryIdempotencyRepository,
    InvalidIdempotencyKey,
    StoredResponse,
    canonical_request_fingerprint,
    validate_idempotency_key,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(seconds=1)
        return result


SCOPE = IdempotencyScope("tenant_a", "workspace_a", "task.create")


def fingerprint(payload: object = None) -> str:
    return canonical_request_fingerprint(SCOPE.action, payload)


@pytest.mark.parametrize(
    "key",
    ["request-123", "A", "client.v2:task_42", "x" * 255],
)
def test_validate_idempotency_key_accepts_safe_keys(key: str) -> None:
    assert validate_idempotency_key(key) == key


@pytest.mark.parametrize(
    "key",
    ["", " leading", "trailing ", "contains space", "../slash", "x" * 256],
)
def test_validate_idempotency_key_rejects_invalid_keys(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey):
        validate_idempotency_key(key)


def test_fingerprint_is_canonical_and_action_sensitive() -> None:
    first = canonical_request_fingerprint(
        "task.create", {"title": "Report", "options": {"b": 2, "a": [True, None]}}
    )
    reordered = canonical_request_fingerprint(
        "task.create", {"options": {"a": [True, None], "b": 2}, "title": "Report"}
    )

    assert first == reordered
    assert first != canonical_request_fingerprint("task.update", {"title": "Report"})
    assert first != canonical_request_fingerprint("task.create", {"title": "Other"})
    assert len(first) == 64


def test_begin_creates_processing_record_with_deterministic_time() -> None:
    repository = InMemoryIdempotencyRepository(clock=Clock())

    result = repository.begin(SCOPE, "request-1", fingerprint({"title": "Report"}))

    assert result.outcome is BeginOutcome.STARTED
    assert result.record.state is IdempotencyState.PROCESSING
    assert result.record.attempts == 1
    assert result.record.created_at == datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    assert result.record.updated_at == result.record.created_at


def test_completed_response_is_replayed_and_defensively_copied() -> None:
    repository = InMemoryIdempotencyRepository(clock=Clock())
    request_fingerprint = fingerprint({"title": "Report"})
    repository.begin(SCOPE, "request-1", request_fingerprint)
    body = {"id": "task_1", "labels": ["new"]}

    completed = repository.complete(
        SCOPE,
        "request-1",
        request_fingerprint,
        StoredResponse(201, body, {"location": "/api/v1/tasks/task_1"}),
    )
    body["labels"].append("mutated")
    replay = repository.begin(SCOPE, "request-1", request_fingerprint)

    assert completed.state is IdempotencyState.COMPLETED
    assert replay.outcome is BeginOutcome.REPLAYED
    assert replay.response == StoredResponse(
        201,
        {"id": "task_1", "labels": ["new"]},
        {"location": "/api/v1/tasks/task_1"},
    )


def test_same_key_with_different_request_conflicts() -> None:
    repository = InMemoryIdempotencyRepository(clock=Clock())
    repository.begin(SCOPE, "request-1", fingerprint({"title": "First"}))

    with pytest.raises(IdempotencyConflict):
        repository.begin(SCOPE, "request-1", fingerprint({"title": "Second"}))


def test_duplicate_processing_request_is_rejected() -> None:
    repository = InMemoryIdempotencyRepository(clock=Clock())
    request_fingerprint = fingerprint({"title": "Report"})
    repository.begin(SCOPE, "request-1", request_fingerprint)

    with pytest.raises(IdempotencyInProgress):
        repository.begin(SCOPE, "request-1", request_fingerprint)


def test_failed_request_can_be_observed_then_explicitly_retried() -> None:
    repository = InMemoryIdempotencyRepository(clock=Clock())
    request_fingerprint = fingerprint({"title": "Report"})
    repository.begin(SCOPE, "request-1", request_fingerprint)

    failed = repository.fail(SCOPE, "request-1", request_fingerprint, "database unavailable")
    observed = repository.begin(SCOPE, "request-1", request_fingerprint)
    retried = repository.begin(
        SCOPE, "request-1", request_fingerprint, retry_failed=True
    )

    assert failed.state is IdempotencyState.FAILED
    assert failed.failure_reason == "database unavailable"
    assert observed.outcome is BeginOutcome.PREVIOUSLY_FAILED
    assert retried.outcome is BeginOutcome.STARTED
    assert retried.record.state is IdempotencyState.PROCESSING
    assert retried.record.failure_reason is None
    assert retried.record.attempts == 2
    assert retried.record.created_at == failed.created_at


def test_keys_are_isolated_by_tenant_workspace_and_action() -> None:
    repository = InMemoryIdempotencyRepository(clock=Clock())
    scopes = [
        SCOPE,
        IdempotencyScope("tenant_b", "workspace_a", "task.create"),
        IdempotencyScope("tenant_a", "workspace_b", "task.create"),
        IdempotencyScope("tenant_a", "workspace_a", "task.cancel"),
    ]

    results = [
        repository.begin(scope, "shared-key", canonical_request_fingerprint(scope.action, {}))
        for scope in scopes
    ]

    assert all(result.outcome is BeginOutcome.STARTED for result in results)


def test_terminal_record_cannot_be_changed_again() -> None:
    repository = InMemoryIdempotencyRepository(clock=Clock())
    request_fingerprint = fingerprint({})
    repository.begin(SCOPE, "request-1", request_fingerprint)
    repository.complete(
        SCOPE, "request-1", request_fingerprint, StoredResponse(204, None, {})
    )

    with pytest.raises(IdempotencyTransitionError):
        repository.fail(SCOPE, "request-1", request_fingerprint, "too late")


def test_complete_requires_a_started_key() -> None:
    repository = InMemoryIdempotencyRepository(clock=Clock())

    with pytest.raises(IdempotencyTransitionError):
        repository.complete(
            SCOPE,
            "request-1",
            fingerprint({}),
            StoredResponse(200, {"ok": True}, {}),
        )
