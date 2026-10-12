"""PostgreSQL store of commands and quick commands (migration ``0044_commands``).

The store works in the request's unit of work (the transaction that also creates the
command's tasks). It sets ``anum.user_id`` on that transaction, so the tables' forced RLS
policies (tenant, workspace and user) admit only the caller's own rows; every statement
also names the scope explicitly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, delete, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from anum_api.commands import (
    CommandAttachment,
    QuickCommand,
    StoredCommand,
    StoredStep,
    UnknownCommandCursor,
)
from anum_api.schemas import TenantContext

from .models import Base, WorkspaceScopedMixin, _workspace_fk
from .scope import require_workspace


class CommandRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "commands"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_key: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    quick_command_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("commands"),)


class QuickCommandRecord(Base, WorkspaceScopedMixin):
    __tablename__ = "quick_commands"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (_workspace_fk("quick_commands"),)


def _command(record: CommandRecord) -> StoredCommand:
    return StoredCommand(
        id=record.id,
        tenant_id=record.tenant_id,
        workspace_id=record.workspace_id,
        created_by=record.created_by,
        text=record.text,
        text_key=record.text_key,
        source=record.source,  # type: ignore[arg-type]
        locale=record.locale,  # type: ignore[arg-type]
        steps=[StoredStep.model_validate(item) for item in record.steps],
        attachments=[CommandAttachment.model_validate(item) for item in record.attachments],
        quick_command_id=record.quick_command_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _quick(record: QuickCommandRecord) -> QuickCommand:
    return QuickCommand(
        id=record.id,
        title=record.title,
        text=record.text,
        category=record.category,  # type: ignore[arg-type]
        position=record.position,
        use_count=record.use_count,
        last_used_at=record.last_used_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class SqlAlchemyCommandStore:
    def __init__(self, session: Session, context: TenantContext) -> None:
        self.session = session
        self.context = context
        # Transaction-local, like the tenant and workspace the unit of work already set.
        session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": context.user_id})

    def _commands(self):  # type: ignore[no-untyped-def]
        return select(CommandRecord).where(
            CommandRecord.tenant_id == self.context.tenant_id,
            CommandRecord.workspace_id == self.context.workspace_id,
            CommandRecord.created_by == self.context.user_id,
        )

    def _quicks(self):  # type: ignore[no-untyped-def]
        return select(QuickCommandRecord).where(
            QuickCommandRecord.tenant_id == self.context.tenant_id,
            QuickCommandRecord.workspace_id == self.context.workspace_id,
            QuickCommandRecord.user_id == self.context.user_id,
        )

    def add_command(self, command: StoredCommand) -> StoredCommand:
        require_workspace(self.session, command.tenant_id, command.workspace_id)
        self.session.add(
            CommandRecord(
                id=command.id,
                tenant_id=command.tenant_id,
                workspace_id=command.workspace_id,
                created_by=command.created_by,
                text=command.text,
                text_key=command.text_key,
                source=command.source.value,
                locale=command.locale,
                steps=[step.model_dump(mode="json") for step in command.steps],
                attachments=[item.model_dump(mode="json") for item in command.attachments],
                quick_command_id=command.quick_command_id,
                created_at=command.created_at,
                updated_at=command.updated_at,
            )
        )
        self.session.flush()
        return command

    def _record(self, command_id: str) -> CommandRecord | None:
        return self.session.scalar(self._commands().where(CommandRecord.id == command_id))

    def get_command(self, command_id: str) -> StoredCommand | None:
        record = self._record(command_id)
        return _command(record) if record is not None else None

    def list_commands(self, limit: int, after: str | None) -> list[StoredCommand]:
        query = self._commands()
        if after:
            cursor = self._record(after)
            if cursor is None:
                raise UnknownCommandCursor(after)
            query = query.where(
                (CommandRecord.created_at < cursor.created_at)
                | ((CommandRecord.created_at == cursor.created_at) & (CommandRecord.id < cursor.id))
            )
        rows = self.session.scalars(
            query.order_by(CommandRecord.created_at.desc(), CommandRecord.id.desc()).limit(limit)
        ).all()
        return [_command(row) for row in rows]

    def recent_texts(self, limit: int) -> list[tuple[str, str, datetime]]:
        rows = self.session.execute(
            select(CommandRecord.text, CommandRecord.text_key, CommandRecord.created_at)
            .where(
                CommandRecord.tenant_id == self.context.tenant_id,
                CommandRecord.workspace_id == self.context.workspace_id,
                CommandRecord.created_by == self.context.user_id,
            )
            .order_by(CommandRecord.created_at.desc(), CommandRecord.id.desc())
            .limit(limit)
        ).all()
        return [(row[0], row[1], row[2]) for row in rows]

    def delete_command(self, command_id: str) -> bool:
        result = self.session.execute(
            delete(CommandRecord).where(
                CommandRecord.id == command_id,
                CommandRecord.tenant_id == self.context.tenant_id,
                CommandRecord.workspace_id == self.context.workspace_id,
                CommandRecord.created_by == self.context.user_id,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    def list_quick(self) -> list[QuickCommand]:
        rows = self.session.scalars(
            self._quicks().order_by(
                QuickCommandRecord.category,
                QuickCommandRecord.position,
                QuickCommandRecord.created_at,
                QuickCommandRecord.id,
            )
        ).all()
        return [_quick(row) for row in rows]

    def get_quick(self, quick_id: str) -> QuickCommand | None:
        record = self.session.scalar(self._quicks().where(QuickCommandRecord.id == quick_id))
        return _quick(record) if record is not None else None

    def save_quick(self, quick: QuickCommand) -> QuickCommand:
        record = self.session.scalar(self._quicks().where(QuickCommandRecord.id == quick.id))
        if record is None:
            require_workspace(self.session, self.context.tenant_id, self.context.workspace_id)
            record = QuickCommandRecord(
                id=quick.id,
                tenant_id=self.context.tenant_id,
                workspace_id=self.context.workspace_id,
                user_id=self.context.user_id,
                created_at=quick.created_at,
            )
            self.session.add(record)
        record.title = quick.title
        record.text = quick.text
        record.category = quick.category.value
        record.position = quick.position
        record.use_count = quick.use_count
        record.last_used_at = quick.last_used_at
        record.updated_at = quick.updated_at
        self.session.flush()
        return _quick(record)

    def delete_all(self) -> None:
        self.session.execute(
            delete(CommandRecord).where(
                CommandRecord.tenant_id == self.context.tenant_id,
                CommandRecord.workspace_id == self.context.workspace_id,
                CommandRecord.created_by == self.context.user_id,
            )
        )
        self.session.execute(
            delete(QuickCommandRecord).where(
                QuickCommandRecord.tenant_id == self.context.tenant_id,
                QuickCommandRecord.workspace_id == self.context.workspace_id,
                QuickCommandRecord.user_id == self.context.user_id,
            )
        )

    def delete_quick(self, quick_id: str) -> bool:
        result = self.session.execute(
            delete(QuickCommandRecord).where(
                QuickCommandRecord.id == quick_id,
                QuickCommandRecord.tenant_id == self.context.tenant_id,
                QuickCommandRecord.workspace_id == self.context.workspace_id,
                QuickCommandRecord.user_id == self.context.user_id,
            )
        )
        return bool(getattr(result, "rowcount", 0))
