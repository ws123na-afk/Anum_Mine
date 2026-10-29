from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from anum_api.schemas import TenantContext


REDACTED = "[REDACTED]"
_SECRET_KEY_PARTS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
)


class DuplicateAuditRecordError(ValueError):
    """Raised when an audit record ID has already been recorded."""


class ImmutableAuditError(RuntimeError):
    """Raised when code attempts to alter append-only audit history."""


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_secret_key(key):
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


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """Filters for the audit log (docs/governance-and-scale.md#audit-log).

    ``action`` matches exactly, or as a prefix when it ends with ``*``
    (``workspace_member.*``). ``since`` is inclusive and ``until`` exclusive. ``text``
    matches actor, action or target case-insensitively as a substring.
    """

    actor: str | None = None
    action: str | None = None
    target: str | None = None
    outcome: str | None = None
    correlation_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    text: str | None = None
    # Ids of one room with its desks, or of one agent (the History panel's room and agent
    # views): a record matches when its actor or target is one of them, or any value it
    # recorded (at any depth) equals one of them. Empty means no such filter.
    mentions: tuple[str, ...] = ()

    def matches(self, record: "AuditRecord") -> bool:
        if self.actor is not None and record.actor != self.actor:
            return False
        if self.action is not None:
            if self.action.endswith("*"):
                if not record.action.startswith(self.action[:-1]):
                    return False
            elif record.action != self.action:
                return False
        if self.target is not None and record.target != self.target:
            return False
        if self.outcome is not None and record.outcome != self.outcome:
            return False
        if self.correlation_id is not None and record.correlation_id != self.correlation_id:
            return False
        if self.since is not None and record.created_at < self.since:
            return False
        if self.until is not None and record.created_at >= self.until:
            return False
        if self.text:
            needle = self.text.casefold()
            if not any(needle in value.casefold() for value in (record.actor, record.action, record.target)):
                return False
        if self.mentions and not names_any(record, frozenset(self.mentions)):
            return False
        return True

    def as_metadata(self) -> dict[str, str]:
        """The filters that are set, as strings (for the export's own audit record)."""
        values = {
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "outcome": self.outcome,
            "correlation_id": self.correlation_id,
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "text": self.text,
            "mentions": ",".join(self.mentions) if self.mentions else None,
        }
        return {key: value for key, value in values.items() if value is not None}


def _mentions(value: Any, ids: frozenset[str]) -> bool:
    if isinstance(value, str):
        return value in ids
    if isinstance(value, Mapping):
        return any(_mentions(item, ids) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_mentions(item, ids) for item in value)
    return False


def names_any(record: "AuditRecord", ids: frozenset[str]) -> bool:
    """True when the record names one of ``ids`` as actor, target or any recorded value."""
    return record.actor in ids or record.target in ids or _mentions(record.metadata, ids)


# Newest first: (created_at, id) descending. A cursor is the last row's key.
AuditCursor = tuple[datetime, str]


def newest_first_page(
    records: "list[AuditRecord] | tuple[AuditRecord, ...]",
    audit_filter: AuditFilter,
    *,
    limit: int,
    after: AuditCursor | None,
) -> "list[AuditRecord]":
    """Filter, order newest first and cut one keyset page (the in-memory backends)."""
    ordered = sorted(
        (record for record in records if audit_filter.matches(record)),
        key=lambda record: (record.created_at, record.id),
        reverse=True,
    )
    if after is not None:
        ordered = [record for record in ordered if (record.created_at, record.id) < after]
    return ordered[:limit]


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
