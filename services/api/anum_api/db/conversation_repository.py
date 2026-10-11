"""PostgreSQL assistant conversations (migration 0018).

The session must carry the tenant, workspace *and user* RLS context
(``set_tenant_context(..., user_id=...)``): conversations are private to the user who
started them. Queries also filter on the scope explicitly.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from anum_api.conversations import (
    Conversation,
    ConversationCreate,
    ConversationTurn,
    TurnRole,
    TurnStatus,
)
from anum_api.schemas import TenantContext, new_id, utc_now

from .models import ConversationRecord, ConversationTurnRecord
from .scope import require_workspace


def _conversation(record: ConversationRecord) -> Conversation:
    return Conversation(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        user_id=record.user_id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _turn(record: ConversationTurnRecord) -> ConversationTurn:
    return ConversationTurn(
        id=record.id,
        conversation_id=record.conversation_id,
        role=TurnRole(record.role),
        content=record.content,
        status=TurnStatus(record.status),
        created_at=record.created_at,
    )


class SqlAlchemyConversationStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context

    def _scope(self):  # type: ignore[no-untyped-def]
        return (
            ConversationRecord.tenant_id == self.context.tenant_id,
            ConversationRecord.workspace_id == self.context.workspace_id,
            ConversationRecord.user_id == self.context.user_id,
        )

    def _turn_scope(self, conversation_id: str):  # type: ignore[no-untyped-def]
        return (
            ConversationTurnRecord.tenant_id == self.context.tenant_id,
            ConversationTurnRecord.workspace_id == self.context.workspace_id,
            ConversationTurnRecord.user_id == self.context.user_id,
            ConversationTurnRecord.conversation_id == conversation_id,
        )

    def create(self, payload: ConversationCreate, context: TenantContext) -> Conversation:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        now = utc_now()
        record = ConversationRecord(
            id=new_id("conv"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            title=(payload.title or "").strip(),
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        self.session.flush()
        return _conversation(record)

    def list(self, context: TenantContext, limit: int) -> list[Conversation]:
        records = self.session.scalars(
            select(ConversationRecord)
            .where(*self._scope())
            .order_by(ConversationRecord.updated_at.desc(), ConversationRecord.id.desc())
            .limit(limit)
        ).all()
        return [_conversation(record) for record in records]

    def get(self, conversation_id: str, context: TenantContext) -> Conversation | None:
        record = self.session.scalar(
            select(ConversationRecord).where(*self._scope(), ConversationRecord.id == conversation_id)
        )
        return _conversation(record) if record is not None else None

    def turns(self, conversation: Conversation, *, last: int | None = None) -> list[ConversationTurn]:
        statement = select(ConversationTurnRecord).where(*self._turn_scope(conversation.id))
        if last:
            records = list(
                self.session.scalars(statement.order_by(ConversationTurnRecord.seq.desc()).limit(last)).all()
            )
            records.reverse()
        else:
            records = list(self.session.scalars(statement.order_by(ConversationTurnRecord.seq)).all())
        return [_turn(record) for record in records]

    def count_turns(self, conversation: Conversation) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(ConversationTurnRecord).where(*self._turn_scope(conversation.id))
            )
            or 0
        )

    def add_turn(
        self, conversation: Conversation, role: TurnRole, content: str, turn_status: TurnStatus
    ) -> ConversationTurn:
        now = utc_now()
        record = ConversationTurnRecord(
            id=new_id("turn"),
            tenant_id=self.context.tenant_id,
            workspace_id=self.context.workspace_id,
            user_id=self.context.user_id,
            conversation_id=conversation.id,
            role=role.value,
            content=content,
            status=turn_status.value,
            created_at=now,
        )
        self.session.add(record)
        self.session.flush()
        self.session.execute(
            update(ConversationRecord)
            .where(*self._scope(), ConversationRecord.id == conversation.id)
            .values(updated_at=now)
        )
        return _turn(record)

    def set_title(self, conversation: Conversation, title: str) -> None:
        self.session.execute(
            update(ConversationRecord)
            .where(*self._scope(), ConversationRecord.id == conversation.id)
            .values(title=title)
        )

    def delete(self, conversation: Conversation) -> None:
        # Turns go with it (ON DELETE CASCADE), under the same RLS context.
        self.session.execute(
            delete(ConversationRecord).where(*self._scope(), ConversationRecord.id == conversation.id)
        )
