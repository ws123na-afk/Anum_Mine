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

import redis


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


# How long a Valkey-backed record survives with no further activity. Nothing
# upstream specifies a retention window; this just keeps the keyspace from
# growing unbounded from abandoned/replayed requests. Every write refreshes
# the TTL, so an actively-replayed key never expires out from under it.
_VALKEY_RECORD_TTL_SECONDS = 7 * 24 * 60 * 60


# begin() must be atomic: two replicas racing to begin() a brand-new key must
# not both observe "missing" and both return STARTED (that's exactly the
# double-execution idempotency exists to prevent). A plain GET-then-SET from
# Python has that race; a Lua script runs as a single atomic step on the
# Valkey server, so it doesn't.
_BEGIN_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
local fingerprint = ARGV[1]
local retry_failed = ARGV[2] == '1'
local now = ARGV[3]
local ttl = tonumber(ARGV[4])

if not raw then
    local record = {
        fingerprint = fingerprint,
        state = 'processing',
        created_at = now,
        updated_at = now,
        attempts = 1,
        response = '',
        failure_reason = '',
    }
    local encoded = cjson.encode(record)
    redis.call('SET', KEYS[1], encoded, 'EX', ttl)
    return {'started', encoded}
end

local record = cjson.decode(raw)
if record.fingerprint ~= fingerprint then
    return {'conflict', raw}
end
if record.state == 'processing' then
    return {'in_progress', raw}
end
if record.state == 'completed' then
    return {'replayed', raw}
end
if not retry_failed then
    return {'previously_failed', raw}
end

record.state = 'processing'
record.updated_at = now
record.attempts = record.attempts + 1
record.response = ''
record.failure_reason = ''
local encoded = cjson.encode(record)
redis.call('SET', KEYS[1], encoded, 'EX', ttl)
return {'started', encoded}
"""

# complete()/fail() share the same "load, validate, transition, store" shape
# as begin() and need the same atomicity for the same reason: a concurrent
# get()/begin() must never observe a half-written record. `response` is kept
# as an opaque pre-encoded JSON *string* field (not decoded into a Lua table)
# so an empty `headers: {}` never round-trips through cjson's ambiguous
# empty-table-is-`{}`-or-`[]` encoding.
_FINISH_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then
    return {'not_started', ''}
end

local record = cjson.decode(raw)
if record.fingerprint ~= ARGV[1] then
    return {'conflict', raw}
end
if record.state ~= 'processing' then
    return {'bad_transition', raw}
end

record.state = ARGV[2]
record.updated_at = ARGV[3]
record.response = ARGV[4]
record.failure_reason = ARGV[5]

local encoded = cjson.encode(record)
redis.call('SET', KEYS[1], encoded, 'EX', tonumber(ARGV[6]))
return {'ok', encoded}
"""


class ValkeyIdempotencyRepository:
    """Valkey-backed `IdempotencyRepository` with the same semantics as
    `InMemoryIdempotencyRepository`, so it can be swapped in transparently
    (see anum_api/settings.py `valkey_url`). Records are JSON blobs stored
    under one string key per (scope, key), mutated atomically via Lua
    scripts (see `_BEGIN_SCRIPT`/`_FINISH_SCRIPT` above for why).
    """

    def __init__(
        self,
        client: redis.Redis,
        *,
        clock: Callable[[], datetime] = _utc_now,
        ttl_seconds: int = _VALKEY_RECORD_TTL_SECONDS,
    ) -> None:
        self._client = client
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._begin_script = client.register_script(_BEGIN_SCRIPT)
        self._finish_script = client.register_script(_FINISH_SCRIPT)

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
        redis_key = self._redis_key(scope, key)

        status, raw = self._begin_script(
            keys=[redis_key],
            args=[
                fingerprint,
                "1" if retry_failed else "0",
                self._now_iso(),
                self._ttl_seconds,
            ],
        )

        if status == "conflict":
            raise IdempotencyConflict(
                "Idempotency key is already associated with a different request"
            )
        if status == "in_progress":
            raise IdempotencyInProgress("An equivalent request is already processing")

        record = self._decode(scope, key, raw)
        outcome = {
            "started": BeginOutcome.STARTED,
            "replayed": BeginOutcome.REPLAYED,
            "previously_failed": BeginOutcome.PREVIOUSLY_FAILED,
        }[status]
        return BeginResult(outcome, record)

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
        raw = self._client.get(self._redis_key(scope, key))
        if raw is None:
            return None
        return self._decode(scope, key, raw)

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
        redis_key = self._redis_key(scope, key)

        response_json = (
            json.dumps(
                {
                    "status_code": response.status_code,
                    "body": response.body,
                    "headers": dict(response.headers),
                }
            )
            if response is not None
            else ""
        )

        status, raw = self._finish_script(
            keys=[redis_key],
            args=[
                fingerprint,
                state.value,
                self._now_iso(),
                response_json,
                failure_reason or "",
                self._ttl_seconds,
            ],
        )

        if status == "not_started":
            raise IdempotencyTransitionError("Idempotency key has not been started")
        if status == "conflict":
            raise IdempotencyConflict(
                "Idempotency key is already associated with a different request"
            )
        if status == "bad_transition":
            existing = self._decode(scope, key, raw)
            raise IdempotencyTransitionError(
                f"Cannot transition idempotency record from {existing.state} to {state}"
            )

        return self._decode(scope, key, raw)

    @staticmethod
    def _redis_key(scope: IdempotencyScope, key: str) -> str:
        return f"anum:idempotency:{scope.tenant_id}:{scope.workspace_id}:{scope.action}:{key}"

    def _now_iso(self) -> str:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.isoformat()

    @staticmethod
    def _validate_fingerprint(fingerprint: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("fingerprint must be a lowercase SHA-256 hexadecimal digest")

    @staticmethod
    def _decode(scope: IdempotencyScope, key: str, raw: str) -> IdempotencyRecord:
        payload = json.loads(raw)
        # response/failure_reason use "" as the "not set" sentinel (see the
        # scripts above) rather than JSON null, so an empty string collapses
        # back to None here.
        response_raw = payload.get("response") or ""
        response_payload = json.loads(response_raw) if response_raw else None
        return IdempotencyRecord(
            scope=scope,
            key=key,
            fingerprint=payload["fingerprint"],
            state=IdempotencyState(payload["state"]),
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
            attempts=payload["attempts"],
            response=(
                StoredResponse(
                    response_payload["status_code"],
                    response_payload["body"],
                    response_payload["headers"],
                )
                if response_payload is not None
                else None
            ),
            failure_reason=payload.get("failure_reason") or None,
        )
