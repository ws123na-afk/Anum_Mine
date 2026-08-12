from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from anum_api.audit import (
    REDACTED,
    AuditRecord,
    DuplicateAuditRecordError,
    ImmutableAuditError,
    InMemoryAuditRecorder,
)
from anum_api.schemas import TenantContext


BASE_TIME = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)


def context(tenant_id: str = "tenant_a", workspace_id: str = "workspace_a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id="user_a",
        roles=["owner"],
    )


def audit_record(
    record_id: str,
    *,
    tenant_id: str = "tenant_a",
    workspace_id: str = "workspace_a",
    created_at: datetime = BASE_TIME,
    metadata: dict | None = None,
) -> AuditRecord:
    return AuditRecord(
        id=record_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor="user_a",
        action="approval.decided",
        target="approval:approval_1",
        outcome="approved",
        correlation_id="correlation_1",
        created_at=created_at,
        metadata=metadata or {},
    )


def test_queries_are_tenant_and_workspace_isolated() -> None:
    recorder = InMemoryAuditRecorder()
    recorder.record(audit_record("audit_a"))
    recorder.record(audit_record("audit_other_workspace", workspace_id="workspace_b"))
    recorder.record(audit_record("audit_other_tenant", tenant_id="tenant_b"))

    assert [record.id for record in recorder.query(context())] == ["audit_a"]
    assert [record.id for record in recorder.query(context(workspace_id="workspace_b"))] == [
        "audit_other_workspace"
    ]
    assert [record.id for record in recorder.query(context(tenant_id="tenant_b"))] == [
        "audit_other_tenant"
    ]


def test_records_and_nested_metadata_are_immutable() -> None:
    source_metadata = {"request": {"method": "POST"}, "roles": ["owner"]}
    record = audit_record("audit_1", metadata=source_metadata)
    recorder = InMemoryAuditRecorder()
    stored = recorder.record(record)

    source_metadata["request"]["method"] = "DELETE"
    source_metadata["roles"].append("admin")

    assert stored.metadata["request"]["method"] == "POST"
    assert stored.metadata["roles"] == ("owner",)
    with pytest.raises(TypeError):
        stored.metadata["request"]["method"] = "PATCH"
    with pytest.raises(FrozenInstanceError):
        stored.outcome = "rejected"
    with pytest.raises(ImmutableAuditError):
        recorder.replace(stored)
    with pytest.raises(ImmutableAuditError):
        recorder.delete(stored.id)
    with pytest.raises(DuplicateAuditRecordError):
        recorder.record(audit_record("audit_1"))


def test_queries_have_deterministic_ordering_and_filters() -> None:
    recorder = InMemoryAuditRecorder()
    recorder.record(audit_record("audit_z", created_at=BASE_TIME))
    recorder.record(audit_record("audit_b", created_at=BASE_TIME + timedelta(seconds=1)))
    recorder.record(audit_record("audit_a", created_at=BASE_TIME))

    assert [record.id for record in recorder.query(context())] == [
        "audit_a",
        "audit_z",
        "audit_b",
    ]
    assert [
        record.id
        for record in recorder.query(
            context(),
            action="approval.decided",
            target="approval:approval_1",
            outcome="approved",
            correlation_id="correlation_1",
        )
    ] == ["audit_a", "audit_z", "audit_b"]
    assert recorder.query(context(), outcome="rejected") == ()


def test_secret_metadata_is_recursively_redacted() -> None:
    record = audit_record(
        "audit_secret",
        metadata={
            "request_id": "request_1",
            "password": "do-not-store",
            "headers": {
                "Authorization": "Bearer private-token",
                "x-api-key": "private-key",
                "content-type": "application/json",
            },
            "integration": {"credentials": {"access_token": "nested-secret"}},
        },
    )

    assert record.metadata["request_id"] == "request_1"
    assert record.metadata["password"] == REDACTED
    assert record.metadata["headers"]["Authorization"] == REDACTED
    assert record.metadata["headers"]["x-api-key"] == REDACTED
    assert record.metadata["headers"]["content-type"] == "application/json"
    assert record.metadata["integration"]["credentials"] == REDACTED
    assert "do-not-store" not in repr(record.metadata)
    assert "private-token" not in repr(record.metadata)
    assert "nested-secret" not in repr(record.metadata)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        audit_record("audit_naive", created_at=datetime(2026, 8, 12, 8, 0))
