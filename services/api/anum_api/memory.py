from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .schemas import TenantContext, new_id


class RetentionKind(StrEnum):
    TASK = "task"
    EXPIRES_AT = "expires_at"
    INDEFINITE = "indefinite"


class MemoryProvenance(BaseModel):
    source_type: str = Field(min_length=1, max_length=80)
    source_id: str | None = Field(default=None, max_length=200)
    created_by_user_id: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetentionPolicy(BaseModel):
    kind: RetentionKind = RetentionKind.TASK
    expires_at: datetime | None = None


class MemoryNote(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    task_id: str
    content: str
    provenance: MemoryProvenance
    retention: RetentionPolicy
    created_at: datetime

    def is_expired(self, now: datetime) -> bool:
        expires_at = self.retention.expires_at
        return (
            self.retention.kind == RetentionKind.EXPIRES_AT
            and expires_at is not None
            and expires_at <= now
        )


class MemoryCreate(BaseModel):
    task_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=16_000)
    source_type: str = Field(min_length=1, max_length=80)
    source_id: str | None = Field(default=None, max_length=200)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)


class MemoryListFilters(BaseModel):
    task_id: str | None = None
    query: str | None = None
    source_types: set[str] = Field(default_factory=set)
    include_expired: bool = False


class MemoryAccess(BaseModel):
    readable_task_ids: set[str] = Field(default_factory=set)
    can_read_all_workspace_tasks: bool = False
    can_delete_any: bool = False

    def can_read(self, task_id: str) -> bool:
        return self.can_read_all_workspace_tasks or task_id in self.readable_task_ids


class MemoryRepository(Protocol):
    def create(self, note: MemoryNote) -> MemoryNote: ...

    def get(self, note_id: str, context: TenantContext) -> MemoryNote | None: ...

    def list(
        self, context: TenantContext, filters: MemoryListFilters, now: datetime
    ) -> list[MemoryNote]: ...

    def delete(self, note_id: str, context: TenantContext) -> bool: ...


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self._notes: dict[str, MemoryNote] = {}

    def create(self, note: MemoryNote) -> MemoryNote:
        stored = note.model_copy(deep=True)
        self._notes[stored.id] = stored
        return stored.model_copy(deep=True)

    def get(self, note_id: str, context: TenantContext) -> MemoryNote | None:
        note = self._notes.get(note_id)
        if note is None or not _in_scope(note, context):
            return None
        return note.model_copy(deep=True)

    def list(
        self, context: TenantContext, filters: MemoryListFilters, now: datetime
    ) -> list[MemoryNote]:
        query_terms = tuple((filters.query or "").casefold().split())
        notes = []
        for note in self._notes.values():
            if not _in_scope(note, context):
                continue
            if filters.task_id is not None and note.task_id != filters.task_id:
                continue
            if filters.source_types and note.provenance.source_type not in filters.source_types:
                continue
            if not filters.include_expired and note.is_expired(now):
                continue
            content = note.content.casefold()
            if query_terms and not all(term in content for term in query_terms):
                continue
            notes.append(note.model_copy(deep=True))
        return sorted(notes, key=lambda note: (note.created_at, note.id))

    def delete(self, note_id: str, context: TenantContext) -> bool:
        note = self._notes.get(note_id)
        if note is None or not _in_scope(note, context):
            return False
        del self._notes[note_id]
        return True


class MemoryService:
    def __init__(
        self,
        repository: MemoryRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def create(self, context: TenantContext, request: MemoryCreate) -> MemoryNote:
        now = self.clock()
        _validate_retention(request.retention, now)
        note = MemoryNote(
            id=new_id("memory"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            task_id=request.task_id,
            content=request.content,
            provenance=MemoryProvenance(
                source_type=request.source_type,
                source_id=request.source_id,
                created_by_user_id=context.user_id,
                created_at=now,
                metadata=request.source_metadata,
            ),
            retention=request.retention,
            created_at=now,
        )
        return self.repository.create(note)

    def list(
        self,
        context: TenantContext,
        access: MemoryAccess,
        filters: MemoryListFilters | None = None,
    ) -> list[MemoryNote]:
        effective_filters = filters or MemoryListFilters()
        return [
            note
            for note in self.repository.list(context, effective_filters, self.clock())
            if access.can_read(note.task_id)
        ]

    def delete(
        self, context: TenantContext, note_id: str, access: MemoryAccess
    ) -> bool:
        note = self.repository.get(note_id, context)
        if note is None or not access.can_read(note.task_id):
            return False
        if note.provenance.created_by_user_id != context.user_id and not access.can_delete_any:
            return False
        return self.repository.delete(note_id, context)


def _in_scope(note: MemoryNote, context: TenantContext) -> bool:
    return (
        note.tenant_id == context.tenant_id
        and note.workspace_id == context.workspace_id
    )


def _validate_retention(policy: RetentionPolicy, now: datetime) -> None:
    if policy.kind == RetentionKind.EXPIRES_AT:
        if policy.expires_at is None:
            raise ValueError("expires_at retention requires an expiry timestamp")
        if policy.expires_at <= now:
            raise ValueError("memory expiry must be in the future")
    elif policy.expires_at is not None:
        raise ValueError("expires_at is only valid for expires_at retention")
