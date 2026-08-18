from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from anum_api.events import is_secret_key
from anum_api.schemas import TenantContext


REDACTED = "[REDACTED]"


class DuplicateAuditRecordError(ValueError):
    """Raised when an audit record ID has already been recorded."""


class ImmutableAuditError(RuntimeError):
    """Raised when code attempts to alter append-only audit history."""


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    if key is not None and is_secret_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(item_key): _sanitize(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_sanitize(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_sanitize(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def redact_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a detached, deeply immutable copy with secret fields removed."""

    return _sanitize(metadata)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    id: str
    tenant_id: str
    workspace_id: str
    actor: str
    action: str
    target: str
    outcome: str
    correlation_id: str
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        required = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "outcome": self.outcome,
            "correlation_id": self.correlation_id,
        }
        for name, value in required.items():
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(timezone.utc))
        object.__setattr__(self, "metadata", redact_metadata(self.metadata))


class AuditQuery(Protocol):
    def query(
        self,
        context: TenantContext,
        *,
        action: str | None = None,
        target: str | None = None,
        outcome: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[AuditRecord, ...]: ...


class AuditRecorder(AuditQuery, Protocol):
    def record(self, record: AuditRecord) -> AuditRecord: ...


class InMemoryAuditRecorder:
    """Append-only audit storage intended for local use and contract tests."""

    def __init__(self) -> None:
        self._records: dict[str, AuditRecord] = {}

    def record(self, record: AuditRecord) -> AuditRecord:
        if record.id in self._records:
            raise DuplicateAuditRecordError(f"audit record already exists: {record.id}")

        stored = replace(record, metadata=record.metadata)
        self._records[stored.id] = stored
        return stored

    def query(
        self,
        context: TenantContext,
        *,
        action: str | None = None,
        target: str | None = None,
        outcome: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[AuditRecord, ...]:
        records = (
            record
            for record in self._records.values()
            if record.tenant_id == context.tenant_id
            and record.workspace_id == context.workspace_id
            and (action is None or record.action == action)
            and (target is None or record.target == target)
            and (outcome is None or record.outcome == outcome)
            and (correlation_id is None or record.correlation_id == correlation_id)
        )
        return tuple(sorted(records, key=lambda record: (record.created_at, record.id)))

    def replace(self, record: AuditRecord) -> None:
        raise ImmutableAuditError(f"audit record cannot be replaced: {record.id}")

    def delete(self, record_id: str) -> None:
        raise ImmutableAuditError(f"audit record cannot be deleted: {record_id}")
