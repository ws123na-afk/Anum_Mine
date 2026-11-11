"""PostgreSQL memory store.

The session carries the tenant and workspace RLS context; for private memories (those
with an ``owner_user_id``, remembered from a user's conversations) it must also carry
``anum.user_id`` (migration 0021's restrictive policy). Every statement also names the
scope and the owner rule explicitly, so a missing context finds nothing.
"""

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from anum_api.memory import (
    KNOWLEDGE_BASE_SOURCE_TYPE,
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
# A shared note attached to no task (docs/memory.md#workspace-memories).
WORKSPACE_NOTE_KIND = "workspace_note"
LISTED_KINDS = (TASK_NOTE_KIND, CONVERSATION_FACT_KIND, WORKSPACE_NOTE_KIND)


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
        if self.session.get(MemoryRecord, note.id) is not None:
            raise ValueError(f"Memory already exists: {note.id}")

        if note.task_id is not None:
            kind = TASK_NOTE_KIND
        elif note.owner_user_id is not None or note.provenance.source_type == KNOWLEDGE_BASE_SOURCE_TYPE:
            # A private chat fact, or a knowledge base note (docs/knowledge-base.md): stored as before.
            kind = CONVERSATION_FACT_KIND
        else:
            kind = WORKSPACE_NOTE_KIND
        record = MemoryRecord(
            id=note.id,
            tenant_id=note.tenant_id,
            workspace_id=note.workspace_id,
            source_task_id=note.task_id,
            kind=kind,
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
        statement = self._matching(select(MemoryRecord), context, filters, now)
        if filters.limit is not None or filters.offset > 0:
            newest_first = statement.order_by(MemoryRecord.created_at.desc(), MemoryRecord.id.desc()).offset(filters.offset)
            if filters.limit is not None:
                newest_first = newest_first.limit(filters.limit)
            records = list(self.session.scalars(newest_first).all())
            records.reverse()
        else:
            records = list(
                self.session.scalars(
                    statement.order_by(MemoryRecord.created_at, MemoryRecord.id)
                ).all()
            )
        return [self._from_record(record) for record in records]

    def count(self, context: TenantContext, filters: MemoryListFilters, now: datetime) -> int:
        statement = self._matching(select(func.count()).select_from(MemoryRecord), context, filters, now)
        return int(self.session.scalar(statement) or 0)

    def _matching(self, statement, context: TenantContext, filters: MemoryListFilters, now: datetime):  # type: ignore[no-untyped-def]
        statement = statement.where(
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
        return statement

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
