from datetime import datetime, timedelta, timezone

import pytest

from anum_api.events import (
    REDACTED,
    CanonicalEventName,
    EventContext,
    InMemoryEventPublisher,
    PublicationStatus,
    PublishedEvent,
    create_event,
    sanitize_payload,
)
from anum_api.schemas import TenantContext


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)


def tenant_context() -> TenantContext:
    return TenantContext(
        tenant_id="tenant_a",
        workspace_id="workspace_a",
        user_id="user_a",
        roles=["owner"],
    )


def test_create_event_uses_canonical_name_actor_and_safe_payload() -> None:
    envelope = create_event(
        CanonicalEventName.TASK_CREATED,
        tenant_context(),
        "task_1",
        {
            "title": "Prepare report",
            "access_token": "do-not-publish",
            "integration": {"apiKey": "also-secret", "name": "mail"},
        },
        event_id="event_1",
        created_at=NOW,
    )

    assert envelope.event.type == "task.created"
    assert envelope.event.tenant_id == "tenant_a"
    assert envelope.event.workspace_id == "workspace_a"
    assert envelope.event.correlation_id == "task_1"
    assert envelope.actor_id == "user_a"
    assert envelope.causation_id is None
    assert envelope.event.payload == {
        "title": "Prepare report",
        "access_token": REDACTED,
        "integration": {"apiKey": REDACTED, "name": "mail"},
    }


def test_child_event_propagates_correlation_actor_and_causation() -> None:
    parent = create_event(
        CanonicalEventName.TASK_CREATED,
        tenant_context(),
        "task_1",
        event_id="event_parent",
        correlation_id="request_1",
        created_at=NOW,
    )

    child = create_event(
        CanonicalEventName.APPROVAL_REQUESTED,
        tenant_context(),
        "approval_1",
        {"task_id": "task_1"},
        propagation=EventContext.from_parent(parent),
        event_id="event_child",
        created_at=NOW,
    )

    assert child.actor_id == "user_a"
    assert child.event.correlation_id == "request_1"
    assert child.causation_id == "event_parent"


def test_explicit_metadata_cannot_be_mixed_with_propagation() -> None:
    propagation = EventContext(
        actor_id="user_a",
        correlation_id="request_1",
        causation_id="event_parent",
    )

    with pytest.raises(ValueError, match="propagation or explicit"):
        create_event(
            CanonicalEventName.TASK_FAILED,
            tenant_context(),
            "task_1",
            propagation=propagation,
            actor_id="user_b",
        )


@pytest.mark.parametrize(
    "payload, error",
    [
        ({"unsupported": object()}, "unsupported value type"),
        ({"not_a_number": float("nan")}, "must be finite"),
        ({1: "not a string"}, "non-empty strings"),
    ],
)
def test_payload_rejects_values_that_are_not_safe_json(payload: dict, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        sanitize_payload(payload)


def test_payload_rejects_oversized_content() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        sanitize_payload({"summary": "x" * 20_000})


def test_publication_state_supports_failure_retry_and_success() -> None:
    envelope = create_event(
        CanonicalEventName.AGENT_RUN_COMPLETED,
        tenant_context(),
        "run_1",
        event_id="event_1",
        created_at=NOW,
    )
    retry_at = NOW + timedelta(minutes=1)
    pending = PublishedEvent.pending(envelope)

    failed = pending.mark_failed("broker unavailable", retry_at=retry_at)
    retrying = failed.retry()
    published = retrying.mark_published(published_at=retry_at)

    assert pending.status == PublicationStatus.PENDING
    assert failed.status == PublicationStatus.FAILED
    assert failed.attempts == 1
    assert failed.last_error == "broker unavailable"
    assert retrying.status == PublicationStatus.PENDING
    assert retrying.attempts == 1
    assert published.status == PublicationStatus.PUBLISHED
    assert published.attempts == 2
    assert published.published_at == retry_at


def test_in_memory_publisher_is_idempotent_by_event_id() -> None:
    envelope = create_event(
        CanonicalEventName.APPROVAL_APPROVED,
        tenant_context(),
        "approval_1",
        event_id="event_1",
        created_at=NOW,
    )
    publisher = InMemoryEventPublisher(clock=lambda: NOW)
    pending = PublishedEvent.pending(envelope)

    first = publisher.publish(pending)
    second = publisher.publish(pending)

    assert first is second
    assert first.status == PublicationStatus.PUBLISHED
    assert first.attempts == 1
    assert publisher.published == (first,)
