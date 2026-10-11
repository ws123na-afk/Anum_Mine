"""PostgreSQL memory store.

The session carries the tenant and workspace RLS context; for private memories (those
with an ``owner_user_id``, remembered from a user's conversations) it must also carry
``anum.user_id`` (migration 0021's restrictive policy). Every statement also names the
scope and the owner rule explicitly, so a missing context finds nothing.
"""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from anum_api.memory import (
    MemoryListFilters,
    MemoryNote,
    MemoryProvenance,
    MemoryRepository,
    RetentionKind,
    RetentionPolicy,
)
from anum_api.schemas import TenantContext

from .models import MemoryRecord, TaskRecord

TASK_NOTE_KIND = "task_note"
# Facts remembered from a user's conversation (owner_user_id set, no task).
CONVERSATION_FACT_KIND = "conversation_fact"
LISTED_KINDS = (TASK_NOTE_KIND, CONVERSATION_FACT_KIND)


def _visible(context: TenantContext):  # type: ignore[no-untyped-def]
    return (
        MemoryRecord.tenant_id == context.tenant_id,
        MemoryRecord.workspace_id == context.workspace_id,
        or_(MemoryRecord.owner_user_id.is_(None), MemoryRecord.owner_user_id == context.user_id),
    )


class SqlAlchemyMemoryRepository(MemoryRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, note: MemoryNote) -> MemoryNote:
        if note.task_id is not None:
            task = self.session.scalar(
                select(TaskRecord).where(
                    TaskRecord.id == note.task_id,
                    TaskRecord.tenant_id == note.tenant_id,
                    TaskRecord.workspace_id == note.workspace_id,
                )
            )
            if task is None:
                raise ValueError("Cannot create memory for an unavailable task")
        elif note.owner_user_id is None:
            raise ValueError("A memory without a task must be private to its owner")
        if self.session.get(MemoryRecord, note.id) is not None:
            raise ValueError(f"Memory already exists: {note.id}")

        record = MemoryRecord(
            id=note.id,
            tenant_id=note.tenant_id,
            workspace_id=note.workspace_id,
            source_task_id=note.task_id,
            kind=TASK_NOTE_KIND if note.task_id is not None else CONVERSATION_FACT_KIND,
            owner_user_id=note.owner_user_id,
            content=note.content,
            provenance=note.provenance.model_dump(mode="json"),
            retention_policy=note.retention.kind.value,
            retention_expires_at=note.retention.expires_at,
            created_at=note.created_at,
            updated_at=note.created_at,
        )
        self.session.add(record)
        self.session.flush()
        return self._from_record(record)

    def get(self, note_id: str, context: TenantContext) -> MemoryNote | None:
        record = self.session.scalar(
            select(MemoryRecord).where(MemoryRecord.id == note_id, *_visible(context))
        )
        return self._from_record(record) if record is not None else None

    def list(
        self,
        context: TenantContext,
        filters: MemoryListFilters,
        now: datetime,
    ) -> list[MemoryNote]:
        statement = select(MemoryRecord).where(
            *_visible(context),
            MemoryRecord.kind.in_(LISTED_KINDS),
        )
        if filters.owner_only:
            statement = statement.where(MemoryRecord.owner_user_id == context.user_id)
        if filters.task_id is not None:
            statement = statement.where(MemoryRecord.source_task_id == filters.task_id)
        if filters.source_types:
            statement = statement.where(
                MemoryRecord.provenance["source_type"].astext.in_(filters.source_types)
            )
        if not filters.include_expired:
            statement = statement.where(
                (MemoryRecord.retention_expires_at.is_(None))
                | (MemoryRecord.retention_expires_at > now)
            )
        if filters.query:
            for term in filters.query.casefold().split():
                statement = statement.where(MemoryRecord.content.ilike(f"%{term}%"))
        if filters.limit is not None:
            records = list(
                self.session.scalars(
                    statement.order_by(MemoryRecord.created_at.desc(), MemoryRecord.id.desc())
                    .limit(filters.limit)
                ).all()
            )
            records.reverse()
        else:
            records = list(
                self.session.scalars(
                    statement.order_by(MemoryRecord.created_at, MemoryRecord.id)
                ).all()
            )
        return [self._from_record(record) for record in records]

    def delete(self, note_id: str, context: TenantContext) -> bool:
        record = self.session.scalar(
            select(MemoryRecord).where(MemoryRecord.id == note_id, *_visible(context)).with_for_update()
        )
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def update_content(
        self,
        note_id: str,
        context: TenantContext,
        content: str,
        provenance: MemoryProvenance,
        now: datetime,
    ) -> MemoryNote | None:
        record = self.session.scalar(
            select(MemoryRecord).where(MemoryRecord.id == note_id, *_visible(context)).with_for_update()
        )
        if record is None:
            return None
        record.content = content
        record.provenance = provenance.model_dump(mode="json")
        record.updated_at = now
        self.session.flush()
        return self._from_record(record)

    @staticmethod
    def _from_record(record: MemoryRecord) -> MemoryNote:
        provenance = MemoryProvenance.model_validate(record.provenance)
        return MemoryNote(
            id=record.id,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            task_id=record.source_task_id,
            content=record.content,
            provenance=provenance,
            retention=RetentionPolicy(
                kind=RetentionKind(record.retention_policy),
                expires_at=record.retention_expires_at,
            ),
            created_at=record.created_at,
            owner_user_id=record.owner_user_id,
            updated_at=record.updated_at if record.updated_at != record.created_at else None,
        )
