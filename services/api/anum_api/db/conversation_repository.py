"""PostgreSQL assistant conversations (migration 0018).

The session must carry the tenant, workspace *and user* RLS context
(``set_tenant_context(..., user_id=...)``): conversations are private to the user who
started them. Queries also filter on the scope explicitly.

``purge_idle_conversations`` is the retention purge (migration 0019): idle conversations
are discovered as ``anum_maintenance`` (ids and scope only) and deleted as the
application role inside each user's tenant, workspace and user RLS context.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from anum_api.chat_tools import ChatCard
from anum_api.conversations import (
    Conversation,
    ConversationCreate,
    ConversationTurn,
    TurnRole,
    TurnStatus,
)
from anum_api.maintenance import SessionFactory, discover, scoped_unit
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
        agent_id=record.agent_id,
    )


def _turn(record: ConversationTurnRecord) -> ConversationTurn:
    return ConversationTurn(
        id=record.id,
        conversation_id=record.conversation_id,
        role=TurnRole(record.role),
        content=record.content,
        status=TurnStatus(record.status),
        created_at=record.created_at,
        card=_card(record.card),
    )


def _card(value: object) -> ChatCard | None:
    if not isinstance(value, dict):
        return None
    try:
        return ChatCard.model_validate(value)
    except ValueError:
        # A card this release cannot read (a newer one wrote it): the text still shows.
        return None


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
            agent_id=payload.agent_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        self.session.flush()
        return _conversation(record)

    def list(self, context: TenantContext, limit: int, agent_id: str | None = None) -> list[Conversation]:
        agent_filter = (
            ConversationRecord.agent_id.is_(None) if agent_id is None else ConversationRecord.agent_id == agent_id
        )
        records = self.session.scalars(
            select(ConversationRecord)
            .where(*self._scope(), agent_filter)
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
        self,
        conversation: Conversation,
        role: TurnRole,
        content: str,
        turn_status: TurnStatus,
        card: ChatCard | None = None,
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
            card=card.model_dump(mode="json") if card is not None else None,
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


# Retention purge (migration 0019_conversation_retention) -------------------------------

# Runs as anum_maintenance: its policy admits only conversations idle for at least a day
# and its grants cover these columns only. Keyset order, so a dry run (which deletes
# nothing) still walks every batch.
_IDLE_CONVERSATIONS = text(
    """
    select id, tenant_id, workspace_id, user_id, updated_at
    from conversations
    where updated_at <= now() - make_interval(days => :days)
      and (cast(:after_updated_at as timestamptz) is null
           or (updated_at, id) > (cast(:after_updated_at as timestamptz), :after_id))
    order by updated_at, id
    limit :limit
    """
)
# Both re-check the idle predicate: a turn added since discovery keeps the conversation.
_COUNT_IDLE = text(
    """
    select count(*) as conversations,
           coalesce(sum((select count(*) from conversation_turns t
                         where t.tenant_id = c.tenant_id and t.workspace_id = c.workspace_id
                           and t.user_id = c.user_id and t.conversation_id = c.id)), 0) as turns
    from conversations c
    where c.tenant_id = :tenant_id and c.workspace_id = :workspace_id and c.user_id = :user_id
      and c.id = any(:ids) and c.updated_at <= now() - make_interval(days => :days)
    """
)
# Turns go with their conversation (ON DELETE CASCADE); the count reads the statement's
# snapshot, taken before the delete, so it is the number of turns removed.
_DELETE_IDLE = text(
    """
    with doomed as (
      delete from conversations c
      where c.tenant_id = :tenant_id and c.workspace_id = :workspace_id and c.user_id = :user_id
        and c.id = any(:ids) and c.updated_at <= now() - make_interval(days => :days)
      returning c.id
    )
    select (select count(*) from doomed) as conversations,
           (select count(*) from conversation_turns t
            where t.tenant_id = :tenant_id and t.workspace_id = :workspace_id
              and t.user_id = :user_id and t.conversation_id in (select id from doomed)) as turns
    """
)


@dataclass
class ConversationPurgeResult:
    retention_days: int = 0
    conversations: int = 0
    turns: int = 0
    dry_run: bool = False


def purge_idle_conversations(
    session_factory: SessionFactory,
    *,
    retention_days: int,
    maintenance_session_factory: SessionFactory | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
) -> ConversationPurgeResult:
    """Delete conversations (and their turns) idle for ``retention_days`` or more.

    ``retention_days`` 0 keeps every conversation. Discovery runs as ``anum_maintenance``
    (ids, scope and ``updated_at`` only); each delete runs as the application role
    inside that user's tenant, workspace and user RLS context.
    """
    result = ConversationPurgeResult(retention_days=retention_days, dry_run=dry_run)
    if retention_days == 0:
        return result
    if retention_days < 1:
        raise ValueError("retention_days must be 0 (keep) or a positive number of days")
    after_updated_at: datetime | None = None
    after_id = ""
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _IDLE_CONVERSATIONS,
            {
                "days": retention_days,
                "after_updated_at": after_updated_at,
                "after_id": after_id,
                "limit": batch_size,
            },
        )
        if not rows:
            return result
        after_updated_at, after_id = rows[-1].updated_at, rows[-1].id
        scopes: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for row in rows:
            scopes[(row.tenant_id, row.workspace_id, row.user_id)].append(row.id)
        for (tenant_id, workspace_id, user_id), ids in scopes.items():
            params = {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "ids": ids,
                "days": retention_days,
            }
            with scoped_unit(session_factory, tenant_id, workspace_id, user_id=user_id) as session:
                counted = session.execute(_COUNT_IDLE if dry_run else _DELETE_IDLE, params).one()
                if dry_run:
                    session.rollback()
            result.conversations += int(counted.conversations)
            result.turns += int(counted.turns)
        if len(rows) < batch_size:
            return result
