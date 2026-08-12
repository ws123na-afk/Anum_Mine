from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
import re
from threading import RLock
from typing import Any, Callable, Mapping, Protocol


_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")


class IdempotencyError(Exception):
    """Base error for idempotency operations."""


class InvalidIdempotencyKey(IdempotencyError, ValueError):
    pass


class IdempotencyConflict(IdempotencyError):
    """The key is already associated with a different request."""


class IdempotencyInProgress(IdempotencyError):
    """An equivalent request currently owns the key."""


class IdempotencyTransitionError(IdempotencyError):
    """The requested record state transition is not allowed."""


class IdempotencyState(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class BeginOutcome(StrEnum):
    STARTED = "started"
    REPLAYED = "replayed"
    PREVIOUSLY_FAILED = "previously_failed"


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    tenant_id: str
    workspace_id: str
    action: str

    def __post_init__(self) -> None:
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("workspace_id", self.workspace_id),
            ("action", self.action),
        ):
            if not value or not value.strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: Any
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    scope: IdempotencyScope
    key: str
    fingerprint: str
    state: IdempotencyState
    created_at: datetime
    updated_at: datetime
    attempts: int = 1
    response: StoredResponse | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BeginResult:
    outcome: BeginOutcome
    record: IdempotencyRecord

    @property
    def response(self) -> StoredResponse | None:
        return self.record.response


class IdempotencyRepository(Protocol):
    """Atomic persistence contract for mutating request idempotency."""

    def begin(
        self,
        scope: IdempotencyScope,
        key: str,
        fingerprint: str,
        *,
        retry_failed: bool = False,
    ) -> BeginResult: ...

    def complete(
        self,
        scope: IdempotencyScope,
        key: str,
        fingerprint: str,
        response: StoredResponse,
    ) -> IdempotencyRecord: ...

    def fail(
        self,
        scope: IdempotencyScope,
        key: str,
        fingerprint: str,
        reason: str,
    ) -> IdempotencyRecord: ...

    def get(self, scope: IdempotencyScope, key: str) -> IdempotencyRecord | None: ...


def validate_idempotency_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
        raise InvalidIdempotencyKey(
            "Idempotency key must be 1-255 ASCII letters, digits, '.', '_', ':', or '-' "
            "and start with a letter or digit"
        )
    return key


def canonical_request_fingerprint(action: str, payload: Any = None) -> str:
    if not action or not action.strip():
        raise ValueError("action must not be empty")
    canonical = json.dumps(
        {"action": action, "payload": _canonical_value(payload)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("request payload must not contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("request payload object keys must be strings")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise TypeError(f"request payload contains unsupported value: {type(value).__name__}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryIdempotencyRepository:
    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._records: dict[tuple[IdempotencyScope, str], IdempotencyRecord] = {}
        self._lock = RLock()

    def begin(
        self,
        scope: IdempotencyScope,
        key: str,
        fingerprint: str,
        *,
        retry_failed: bool = False,
    ) -> BeginResult:
        key = validate_idempotency_key(key)
        self._validate_fingerprint(fingerprint)
        identity = (scope, key)
        with self._lock:
            existing = self._records.get(identity)
            if existing is None:
                now = self._now()
                record = IdempotencyRecord(
                    scope=scope,
                    key=key,
                    fingerprint=fingerprint,
                    state=IdempotencyState.PROCESSING,
                    created_at=now,
                    updated_at=now,
                )
                self._records[identity] = record
                return BeginResult(BeginOutcome.STARTED, self._copy(record))

            self._assert_same_request(existing, fingerprint)
            if existing.state is IdempotencyState.PROCESSING:
                raise IdempotencyInProgress("An equivalent request is already processing")
            if existing.state is IdempotencyState.COMPLETED:
                return BeginResult(BeginOutcome.REPLAYED, self._copy(existing))
            if not retry_failed:
                return BeginResult(BeginOutcome.PREVIOUSLY_FAILED, self._copy(existing))

            record = replace(
                existing,
                state=IdempotencyState.PROCESSING,
                updated_at=self._now(),
                attempts=existing.attempts + 1,
                response=None,
                failure_reason=None,
            )
            self._records[identity] = record
            return BeginResult(BeginOutcome.STARTED, self._copy(record))

    def complete(
        self,
        scope: IdempotencyScope,
        key: str,
        fingerprint: str,
        response: StoredResponse,
    ) -> IdempotencyRecord:
        if not 100 <= response.status_code <= 599:
            raise ValueError("response status_code must be between 100 and 599")
        return self._finish(
            scope,
            key,
            fingerprint,
            state=IdempotencyState.COMPLETED,
            response=response,
        )

    def fail(
        self,
        scope: IdempotencyScope,
        key: str,
        fingerprint: str,
        reason: str,
    ) -> IdempotencyRecord:
        if not reason or not reason.strip():
            raise ValueError("failure reason must not be empty")
        return self._finish(
            scope,
            key,
            fingerprint,
            state=IdempotencyState.FAILED,
            failure_reason=reason,
        )

    def get(self, scope: IdempotencyScope, key: str) -> IdempotencyRecord | None:
        key = validate_idempotency_key(key)
        with self._lock:
            record = self._records.get((scope, key))
            return self._copy(record) if record is not None else None

    def _finish(
        self,
        scope: IdempotencyScope,
        key: str,
        fingerprint: str,
        *,
        state: IdempotencyState,
        response: StoredResponse | None = None,
        failure_reason: str | None = None,
    ) -> IdempotencyRecord:
        key = validate_idempotency_key(key)
        self._validate_fingerprint(fingerprint)
        with self._lock:
            identity = (scope, key)
            existing = self._records.get(identity)
            if existing is None:
                raise IdempotencyTransitionError("Idempotency key has not been started")
            self._assert_same_request(existing, fingerprint)
            if existing.state is not IdempotencyState.PROCESSING:
                raise IdempotencyTransitionError(
                    f"Cannot transition idempotency record from {existing.state} to {state}"
                )
            record = replace(
                existing,
                state=state,
                updated_at=self._now(),
                response=deepcopy(response),
                failure_reason=failure_reason,
            )
            self._records[identity] = record
            return self._copy(record)

    @staticmethod
    def _validate_fingerprint(fingerprint: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("fingerprint must be a lowercase SHA-256 hexadecimal digest")

    @staticmethod
    def _assert_same_request(record: IdempotencyRecord, fingerprint: str) -> None:
        if record.fingerprint != fingerprint:
            raise IdempotencyConflict(
                "Idempotency key is already associated with a different request"
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value

    @staticmethod
    def _copy(record: IdempotencyRecord) -> IdempotencyRecord:
        return deepcopy(record)
