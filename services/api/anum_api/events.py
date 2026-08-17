from __future__ import annotations

import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Callable, Mapping, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from .schemas import DomainEvent, TenantContext, new_id, utc_now


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

REDACTED = "[REDACTED]"
MAX_PAYLOAD_BYTES = 16_384
MAX_PAYLOAD_DEPTH = 8


class CanonicalEventName(StrEnum):
    TASK_CREATED = "task.created"
    TASK_QUEUED = "task.queued"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    AGENT_RUN_STARTED = "agent_run.started"
    AGENT_RUN_WAITING_APPROVAL = "agent_run.waiting_approval"
    AGENT_RUN_COMPLETED = "agent_run.completed"
    AGENT_RUN_FAILED = "agent_run.failed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    APPROVAL_EXPIRED = "approval.expired"


class PublicationStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class EventEnvelope(BaseModel):
    """Transport metadata wrapped around the durable domain event."""

    model_config = ConfigDict(frozen=True)

    event: DomainEvent
    actor_id: str = Field(min_length=1, max_length=160)
    causation_id: str | None = Field(default=None, min_length=1, max_length=160)

    def child_context(self) -> EventContext:
        return EventContext(
            actor_id=self.actor_id,
            correlation_id=self.event.correlation_id,
            causation_id=self.event.id,
        )


class EventContext(BaseModel):
    """Identity propagated across one logical event chain."""

    model_config = ConfigDict(frozen=True)

    actor_id: str = Field(min_length=1, max_length=160)
    correlation_id: str = Field(min_length=1, max_length=160)
    causation_id: str | None = Field(default=None, min_length=1, max_length=160)

    @classmethod
    def from_parent(cls, parent: EventEnvelope, *, actor_id: str | None = None) -> EventContext:
        return cls(
            actor_id=actor_id or parent.actor_id,
            correlation_id=parent.event.correlation_id,
            causation_id=parent.event.id,
        )


class PublishedEvent(BaseModel):
    """Serializable publication state suitable for a durable outbox row."""

    model_config = ConfigDict(frozen=True)

    envelope: EventEnvelope
    status: PublicationStatus = PublicationStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    available_at: datetime
    published_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=1000)

    @classmethod
    def pending(
        cls, envelope: EventEnvelope, *, available_at: datetime | None = None
    ) -> PublishedEvent:
        return cls(envelope=envelope, available_at=available_at or envelope.event.created_at)

    def mark_published(self, *, published_at: datetime | None = None) -> PublishedEvent:
        if self.status == PublicationStatus.PUBLISHED:
            return self
        return self.model_copy(
            update={
                "status": PublicationStatus.PUBLISHED,
                "attempts": self.attempts + 1,
                "published_at": published_at or utc_now(),
                "last_error": None,
            }
        )

    def mark_failed(self, error: str, *, retry_at: datetime | None = None) -> PublishedEvent:
        if self.status == PublicationStatus.PUBLISHED:
            raise ValueError("A published event cannot return to the outbox")
        message = error.strip()
        if not message:
            raise ValueError("Publication error must not be empty")
        return self.model_copy(
            update={
                "status": PublicationStatus.FAILED,
                "attempts": self.attempts + 1,
                "available_at": retry_at or self.available_at,
                "published_at": None,
                "last_error": message[:1000],
            }
        )

    def retry(self, *, available_at: datetime | None = None) -> PublishedEvent:
        if self.status != PublicationStatus.FAILED:
            raise ValueError("Only failed events can be retried")
        return self.model_copy(
            update={
                "status": PublicationStatus.PENDING,
                "available_at": available_at or self.available_at,
                "last_error": None,
            }
        )


class EventPublisher(Protocol):
    def publish(self, event: PublishedEvent) -> PublishedEvent: ...


class InMemoryEventPublisher:
    """Deterministic publisher used by tests and local adapters."""

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock
        self._published: dict[str, PublishedEvent] = {}

    @property
    def published(self) -> tuple[PublishedEvent, ...]:
        return tuple(self._published.values())

    def publish(self, event: PublishedEvent) -> PublishedEvent:
        event_id = event.envelope.event.id
        existing = self._published.get(event_id)
        if existing is not None:
            if existing.envelope != event.envelope:
                raise ValueError(f"Event id {event_id!r} was reused with different content")
            return existing
        published = event.mark_published(published_at=self._clock())
        self._published[event_id] = published
        return published


def create_event(
    event_type: CanonicalEventName,
    context: TenantContext,
    subject: str,
    payload: Mapping[str, Any] | None = None,
    *,
    propagation: EventContext | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    event_id: str | None = None,
    created_at: datetime | None = None,
) -> EventEnvelope:
    """Create a canonical, tenant-scoped event with safe payload data."""

    clean_subject = subject.strip()
    if not clean_subject:
        raise ValueError("Event subject must not be empty")
    if propagation is not None and any(
        value is not None for value in (actor_id, correlation_id, causation_id)
    ):
        raise ValueError("Use propagation or explicit event metadata, not both")

    resolved_actor = propagation.actor_id if propagation else actor_id or context.user_id
    resolved_correlation = (
        propagation.correlation_id if propagation else correlation_id or clean_subject
    )
    resolved_causation = propagation.causation_id if propagation else causation_id
    domain_event = DomainEvent(
        id=event_id or new_id("event"),
        type=event_type.value,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        subject=clean_subject,
        correlation_id=resolved_correlation,
        created_at=created_at or utc_now(),
        payload=sanitize_payload(payload or {}),
    )
    return EventEnvelope(
        event=domain_event,
        actor_id=resolved_actor,
        causation_id=resolved_causation,
    )


def sanitize_payload(payload: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Return JSON-safe event data while redacting likely credentials."""

    clean = _sanitize_mapping(payload, depth=0)
    encoded = json.dumps(clean, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"Event payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    return clean


def _sanitize_mapping(payload: Mapping[str, Any], *, depth: int) -> dict[str, JsonValue]:
    if depth > MAX_PAYLOAD_DEPTH:
        raise ValueError(f"Event payload exceeds maximum depth {MAX_PAYLOAD_DEPTH}")
    clean: dict[str, JsonValue] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Event payload keys must be non-empty strings")
        clean[key] = REDACTED if is_secret_key(key) else _sanitize_value(value, depth=depth + 1)
    return clean


def _sanitize_value(value: Any, *, depth: int) -> JsonValue:
    if depth > MAX_PAYLOAD_DEPTH:
        raise ValueError(f"Event payload exceeds maximum depth {MAX_PAYLOAD_DEPTH}")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Event payload numbers must be finite")
        return value
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth=depth)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, depth=depth + 1) for item in value]
    raise ValueError(f"Event payload contains unsupported value type: {type(value).__name__}")


SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "privatekey",
)


def is_secret_key(key: str) -> bool:
    """Return whether a field name looks like it holds a credential.

    Shared by every subsystem that redacts payloads (events, audit) so the
    allow-list of secret markers has a single source of truth.
    """

    normalized = "".join(character for character in key.casefold() if character.isalnum())
    return any(marker in normalized for marker in SECRET_KEY_MARKERS)
